#!/usr/bin/env python3
"""Run the CoT Direct LLM baseline for AGREE-II benchmark guides.

This script is intentionally independent from the multi-agent runtime. It reads
only the main PDF listed in the benchmark manifest, appraises each AGREE-II item
with a direct Chain-of-Thought style prompt, and writes outputs compatible with
the existing benchmark loader.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskID,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
except ImportError:
    Console = None  # type: ignore[assignment]
    Progress = None  # type: ignore[assignment]
    TaskID = int  # type: ignore[misc,assignment]


# =============================================================================
# Fixed experiment configuration. Edit these values directly for each run.
# The script intentionally does not read .env so each baseline run is explicit.
# =============================================================================

# Model id sent to /v1/chat/completions and recorded in output metadata.
MODEL_ID = os.environ.get("COT_MODEL_ID", "qwen3.5-122b-a10b")

# OpenAI-compatible API base URL. The script appends /chat/completions.
API_BASE_URL = ""

# Optional bearer token. Keep empty when the model gateway does not require auth.
API_KEY = ""

# Optional benchmark manifest containing guide ids and main PDF filenames.
MANIFEST_PATH = Path("input/bench_min_size/bench_annotation_manifest.csv")

# Directory containing the 100 human-annotated main PDFs and attachments.
GUIDES_DIR = Path("input/bench-annotation")

# Use the 100-guide human annotation exports as the run manifest.
USE_HUMAN_ANNOTATION_GUIDES = True
HUMAN_JSON_DIR = Path("bench_human_json")

# Wang et al. 2025 Supplement 1 eTable S-prompting transcription.
# The script dynamically injects only one item block for benchmark-compatible output.
PROMPTS_RAW_PATH = Path("prompts_raw.md")

# Keep each model's independent queries in separate directories.
OUTPUT_ROOT = Path("output") / MODEL_ID

# Wang et al. used the default GPT-4o web configuration, temperature=1.0.
TEMPERATURE = 1.0

# Maximum generated tokens for one prompt-part response.
MAX_OUTPUT_TOKENS = 20000

# HTTP timeout in seconds for one LLM request attempt.
REQUEST_TIMEOUT = 4000

# Number of request attempts before assigning fallback score=1.
RETRY_ATTEMPTS = 3

# Delay in seconds between retry attempts.
RETRY_DELAY_SECONDS = 3

# Skip existing item JSON files with valid scores when resuming a run.
RESUME = True

# When resuming, retry existing fallback/failed item JSON files.
RESUME_SUCCESS_ONLY = True

# Force re-evaluation even when an item JSON already exists.
OVERWRITE_EXISTING = False

# Ask compatible gateways to enforce JSON output. Automatically falls back if rejected.
USE_JSON_RESPONSE_FORMAT = True

# Qwen chat-template thinking switch for OpenAI-compatible gateways such as vLLM.
REQUEST_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}

# Maximum number of guides processed at the same time. Set to 1 for guide-level serial execution.
MAX_GUIDE_CONCURRENCY = 2

# Number of independent evaluations per guideline. Wang et al. ran four GPT-4o queries.
QUERY_COUNT = 2

# Deprecated for strict Wang-style CoT runs. Kept only to avoid breaking local config habits.
MAX_ITEM_CONCURRENCY_PER_GUIDE = 1

# Global upper bound for simultaneous LLM HTTP requests across all guides and items.
MAX_TOTAL_LLM_CONCURRENCY = 1

# Show a live progress display with one total guide bar plus active guide item bars.
SHOW_PROGRESS = True

# Print per-item success messages. Keep False for cleaner concurrent progress display.
LOG_ITEM_RESULTS = False

# Optional smoke-test controls. Keep both as None for the full 250-guide run.
GUIDE_LIMIT: int | None = None
ITEM_LIMIT: int | None = None

# Per-model character limits for the main PDF context injected into each item prompt.
MODEL_CHAR_LIMITS = {
    "Qwen3.6-35B-A3B": 100_000,
    "qwen2.5-72b": 80_000,
    "deepseek-v4-pro": 800_000,
    "gpt-5.5": 150_000,
    "qwen3.5-122b-a10b": 100_000,
}

# Fallback context limit when MODEL_ID does not match MODEL_CHAR_LIMITS.
DEFAULT_CHAR_LIMIT = 100_000

# Score used when an item request fails, returns invalid JSON, or returns an invalid score.
FALLBACK_SCORE = 1


DOMAIN_ITEMS = {
    1: ["item_1", "item_2", "item_3"],
    2: ["item_4", "item_5", "item_6"],
    3: ["item_7", "item_8", "item_9", "item_10", "item_11", "item_12", "item_13", "item_14"],
    4: ["item_15", "item_16", "item_17"],
    5: ["item_18", "item_19", "item_20", "item_21"],
    6: ["item_22", "item_23"],
}

DOMAIN_NAMES = {
    1: "Scope and Purpose",
    2: "Stakeholder Involvement",
    3: "Rigour of Development",
    4: "Clarity of Presentation",
    5: "Applicability",
    6: "Editorial Independence",
}

PROMPT_PARTS = [
    ("part_1", "First input", [f"item_{idx}" for idx in range(1, 7)]),
    ("part_2", "Second input", [f"item_{idx}" for idx in range(7, 15)]),
    ("part_3", "Third input", [f"item_{idx}" for idx in range(15, 24)]),
]


console = Console() if Console is not None else None


@dataclass(frozen=True)
class GuideEntry:
    """A benchmark guide listed in bench_annotation_manifest.csv."""

    guide_code: str
    guide_name: str
    main_pdf_name: str
    main_pdf_pages: int | None
    seq_no: int | None


@dataclass(frozen=True)
class TextChunk:
    """A simple page-level chunk extracted directly from the main PDF."""

    chunk_id: str
    page: int
    text: str


@dataclass(frozen=True)
class ContextBuildResult:
    """Guideline context and truncation metadata."""

    text: str
    included_chunks: list[TextChunk]
    metadata: dict[str, Any]


def build_output_dirs() -> list[Path]:
    """Return one independent batch directory per Wang-style query."""

    return [OUTPUT_ROOT / f"query{query_index}" for query_index in range(1, QUERY_COUNT + 1)]


def get_char_limit(model_id: str) -> int:
    """Resolve a model-specific guideline text character limit."""

    if model_id in MODEL_CHAR_LIMITS:
        return MODEL_CHAR_LIMITS[model_id]

    lowered = model_id.lower()
    for key, value in MODEL_CHAR_LIMITS.items():
        if key in lowered:
            return value

    return DEFAULT_CHAR_LIMIT


def validate_concurrency_config() -> None:
    """Fail fast when concurrency configuration would deadlock execution."""

    values = {
        "MAX_GUIDE_CONCURRENCY": MAX_GUIDE_CONCURRENCY,
        "MAX_TOTAL_LLM_CONCURRENCY": MAX_TOTAL_LLM_CONCURRENCY,
        "QUERY_COUNT": QUERY_COUNT,
    }
    invalid = [name for name, value in values.items() if value < 1]
    if invalid:
        raise ValueError(f"Concurrency values must be >= 1: {', '.join(invalid)}")


def create_progress() -> Any:
    """Create the live progress display used by concurrent runs."""

    if Progress is None or console is None:
        return None

    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def log_status(message: str) -> None:
    """Write status text without breaking the progress display."""

    if SHOW_PROGRESS and console is not None:
        console.log(message)
    else:
        print(message)


def load_manifest(path: Path) -> list[GuideEntry]:
    """Load benchmark guide entries from the annotation manifest."""

    entries: list[GuideEntry] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            guide_code = row.get("guide_code", "").strip()
            guide_name = row.get("guide_name", "").strip()
            main_pdf_name = row.get("main_pdf_name", "").strip()
            if not guide_code or not main_pdf_name:
                continue

            entries.append(
                GuideEntry(
                    guide_code=guide_code,
                    guide_name=guide_name,
                    main_pdf_name=main_pdf_name,
                    main_pdf_pages=parse_optional_int(row.get("main_pdf_pages")),
                    seq_no=parse_optional_int(row.get("seq_no")),
                )
            )

    return entries


def load_human_annotation_manifest(path: Path) -> list[GuideEntry]:
    """Load the 100 human-annotated guide entries from exported JSON files."""

    entries: list[GuideEntry] = []
    seen: set[str] = set()
    for seq_no, json_path in enumerate(sorted(path.glob("*.json")), start=1):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        guide_code = str(data.get("guideline_id", "") or "").strip()
        if not guide_code or guide_code in seen:
            continue

        main_doc = None
        for document in data.get("documents", []) or []:
            if str(document.get("role", "") or "") == "main":
                main_doc = document
                break
        if not main_doc:
            continue

        main_pdf_name = str(main_doc.get("filename", "") or "").strip()
        if not main_pdf_name:
            continue

        seen.add(guide_code)
        entries.append(
            GuideEntry(
                guide_code=guide_code,
                guide_name=guide_code,
                main_pdf_name=main_pdf_name,
                main_pdf_pages=None,
                seq_no=seq_no,
            )
        )

    return entries


def load_guide_entries() -> list[GuideEntry]:
    """Load the configured guide set for the CoT baseline run."""

    if USE_HUMAN_ANNOTATION_GUIDES:
        return load_human_annotation_manifest(HUMAN_JSON_DIR)
    return load_manifest(MANIFEST_PATH)


def parse_optional_int(value: str | None) -> int | None:
    """Parse an optional integer value from CSV text."""

    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def validate_main_pdfs(entries: list[GuideEntry]) -> None:
    """Raise if any manifest entry does not have its main PDF."""

    missing = [
        entry.main_pdf_name
        for entry in entries
        if not (GUIDES_DIR / entry.main_pdf_name).exists()
    ]
    if missing:
        sample = ", ".join(missing[:5])
        raise FileNotFoundError(f"Missing {len(missing)} main PDFs. Sample: {sample}")


def load_prompt_blocks(path: Path) -> dict[str, str]:
    """Parse item-level prompt blocks from prompts_raw.md."""

    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"((?:\*\*)?The description for Question (\d+)\):(?:\*\*)?.*?)"
        r"(?=\n(?:\*\*)?The description for Question \d+\):(?:\*\*)?|\n\*\*Questions [Pp]art|\n【|\Z)",
        flags=re.DOTALL,
    )
    blocks: dict[str, str] = {}
    for match in pattern.finditer(text):
        item_num = int(match.group(2))
        blocks[f"item_{item_num}"] = match.group(1).strip()

    missing = [f"item_{i}" for i in range(1, 24) if f"item_{i}" not in blocks]
    if missing:
        raise ValueError(f"prompts_raw.md is missing item blocks: {missing}")

    return blocks


def item_title(item_prompt: str) -> str:
    """Extract the AGREE-II item title from a prompts_raw item block."""

    lines = [line.strip() for line in item_prompt.splitlines() if line.strip()]
    if not lines:
        return ""

    first_line = lines[0]
    match = re.search(r"(?:\*\*)?The description for Question \d+\):(?:\*\*)?\s*(.+)", first_line)
    if match and match.group(1).strip():
        return match.group(1).strip()

    if first_line.startswith("The description for Question") and len(lines) > 1:
        return lines[1].strip("#* ")

    return first_line.strip("#* ")


def extract_pdf_chunks(pdf_path: Path) -> list[TextChunk]:
    """Extract page-level text chunks directly from the main PDF."""

    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Run inside the project environment: conda activate ama") from exc

    chunks: list[TextChunk] = []
    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            chunks.append(
                TextChunk(
                    chunk_id=f"chunk_{page_index:04d}",
                    page=page_index,
                    text=text,
                )
            )

    return chunks


def build_guideline_context(chunks: list[TextChunk], char_limit: int) -> ContextBuildResult:
    """Build model input context with deterministic head-tail truncation."""

    original_chars = sum(len(chunk.text) for chunk in chunks)
    rendered_chunks = [render_chunk(chunk) for chunk in chunks if chunk.text.strip()]
    full_text = "\n\n".join(rendered_chunks)

    if len(full_text) <= char_limit:
        return ContextBuildResult(
            text=full_text,
            included_chunks=[chunk for chunk in chunks if chunk.text.strip()],
            metadata={
                "main_pdf_only": True,
                "include_attachments": False,
                "char_limit": char_limit,
                "original_chars": original_chars,
                "included_chars": len(full_text),
                "truncated": False,
                "truncation": "none",
            },
        )

    head_limit = int(char_limit * 0.7)
    tail_limit = char_limit - head_limit
    head_chunks: list[TextChunk] = []
    tail_chunks: list[TextChunk] = []
    head_chars = 0
    tail_chars = 0

    non_empty_chunks = [chunk for chunk in chunks if chunk.text.strip()]

    for chunk in chunks:
        rendered = render_chunk(chunk)
        if chunk.text.strip() and head_chars + len(rendered) <= head_limit:
            head_chunks.append(chunk)
            head_chars += len(rendered) + 2
        elif head_chunks:
            break

    if not head_chunks and non_empty_chunks:
        first_chunk = non_empty_chunks[0]
        head_chunks.append(truncate_chunk_head(first_chunk, head_limit))

    for chunk in reversed(chunks):
        rendered = render_chunk(chunk)
        if chunk.text.strip() and tail_chars + len(rendered) <= tail_limit:
            tail_chunks.append(chunk)
            tail_chars += len(rendered) + 2
        elif tail_chunks:
            break

    if not tail_chunks and len(non_empty_chunks) > 1:
        last_chunk = non_empty_chunks[-1]
        tail_chunks.append(truncate_chunk_tail(last_chunk, tail_limit))

    tail_chunks.reverse()
    included_by_id: dict[str, TextChunk] = {chunk.chunk_id: chunk for chunk in head_chunks}
    for chunk in tail_chunks:
        included_by_id.setdefault(chunk.chunk_id, chunk)

    included_chunks = sorted(included_by_id.values(), key=lambda chunk: chunk.page)
    parts = [render_chunk(chunk) for chunk in included_chunks]
    context = "\n\n".join(parts)

    return ContextBuildResult(
        text=context,
        included_chunks=included_chunks,
        metadata={
            "main_pdf_only": True,
            "include_attachments": False,
            "char_limit": char_limit,
            "original_chars": original_chars,
            "included_chars": len(context),
            "truncated": True,
            "truncation": "head_tail_by_page_chunk",
        },
    )


def render_chunk(chunk: TextChunk) -> str:
    """Render a chunk with stable identifiers for model citations."""

    return f"[{chunk.chunk_id} | page {chunk.page}]\n{chunk.text.strip()}"


def truncate_chunk_head(chunk: TextChunk, char_limit: int) -> TextChunk:
    """Return the first part of an oversized chunk."""

    prefix = "[Page text truncated at the end]\n"
    text_limit = max(0, char_limit - len(prefix) - 64)
    return TextChunk(
        chunk_id=chunk.chunk_id,
        page=chunk.page,
        text=f"{chunk.text.strip()[:text_limit]}\n{prefix}".strip(),
    )


def truncate_chunk_tail(chunk: TextChunk, char_limit: int) -> TextChunk:
    """Return the last part of an oversized chunk."""

    prefix = "[Page text truncated at the beginning]\n"
    text_limit = max(0, char_limit - len(prefix) - 64)
    tail_text = chunk.text.strip()[-text_limit:] if text_limit > 0 else ""
    return TextChunk(
        chunk_id=chunk.chunk_id,
        page=chunk.page,
        text=f"{prefix}{tail_text}".strip(),
    )


def build_prompt(item_id: str, item_prompt: str, guideline_context: str) -> str:
    """Build the user prompt for one AGREE-II item."""

    question_number = int(item_id.split("_", maxsplit=1)[1])
    question_title = item_title(item_prompt)

    return f"""You are following the Wang et al. 2025 Supplement 1 eTable S-prompting rules for AGREE II appraisal.

**Your role**:

Suppose you are a clinical practice guideline developer and an evaluator of the methodological quality of clinical practice guidelines.

**Your task**:

Your task is to conduct a thorough critical appraisal of the submitted clinical practice guideline. Your insights will contribute to an in-depth understanding of the guideline's quality and whether its development methods are rigorous and transparent. Please utilize your expertise, respond to the question listed in the following **Questions part** based on the following **Response rules** and **Question descriptions**. **Question descriptions** add additional detailed descriptions to the question listed in **Questions part**. The **Question descriptions** consist of three parts: Description, Locating Information, and How to Rate.

The uploaded guideline file is represented below as extracted main PDF text. Evaluate based only on this provided text. Do not assume information from appendices, supplementary files, or external sources unless it appears in the provided text.

**Response rules**:

1. All questions are rated on the following 7-point scale:

Score of 1 (Strongly Disagree): A score of 1 should be given when there is no information that is relevant to the question, if the concept is very poorly reported, or if the authors state explicitly that criteria were not met.

Score of 7 (Strongly Agree): A score of 7 should be given if the quality of reporting is exceptional and meets all the criteria and conditions required.

Scores between 2 and 6: A score between 2 and 6 is assigned when the reporting of the question does not meet the full criteria or considerations. A score is assigned depending on the completeness and quality of reporting. Scores increase as more criteria are met and considerations addressed.

2. For questions to which you rated with 1, give your detailed explanation. For questions to which you rated with 2, 3, 4, 5, 6, or 7, list the original and unmodified content from the uploaded article in ascending order.

3. Each response should be informed and supported by the question description, ensuring a clear connection between the question asked and the description given.

**Question descriptions**:

{item_prompt}

**Questions part**:

Question {question_number}):
{question_title}

**Guideline text**:

{guideline_context}

For programmatic collection, return ONLY valid JSON with this exact shape:
{{
  "step1_locate": "Briefly describe where relevant information was found or not found according to the Locating Information section.",
  "step2_evidence": [
    {{
      "text": "Exact original and unmodified evidence text from the guideline. Leave the array empty when score is 1 and no relevant information is found.",
      "chunk_id": "chunk_0001",
      "page": 1
    }}
  ],
  "step3_reasoning": {{
    "summary": "Concise connection between the question, the question description, and the extracted evidence.",
    "score_justification": "For score 1, provide the detailed explanation required by the S-prompting rules. For scores 2-7, explain how the original evidence supports the selected score."
  }},
  "score": 5
}}
"""


def build_part_prompt(
    part_name: str,
    input_label: str,
    item_ids: list[str],
    prompt_blocks: dict[str, str],
    guideline_context: str,
    include_guideline_context: bool,
) -> str:
    """Build one Wang-style S-prompt input for a domain group."""

    descriptions = "\n\n".join(prompt_blocks[item_id] for item_id in item_ids)
    questions = "\n\n".join(
        f"Question {int(item_id.split('_', maxsplit=1)[1])}):\n{item_title(prompt_blocks[item_id])}"
        for item_id in item_ids
    )
    item_shape = """{
      "item_id": "item_1",
      "question_number": 1,
      "step1_locate": "Briefly describe where relevant information was found or not found according to the Locating Information section.",
      "step2_evidence": [
        {
          "text": "Exact original and unmodified evidence text from the guideline. Leave the array empty when score is 1 and no relevant information is found.",
          "chunk_id": "chunk_0001",
          "page": 1
        }
      ],
      "step3_reasoning": {
        "summary": "Concise connection between the question, the question description, and the extracted evidence.",
        "score_justification": "For score 1, provide the detailed explanation required by the S-prompting rules. For scores 2-7, explain how the original evidence supports the selected score."
      },
      "score": 5
    }"""

    json_instruction = f"""For programmatic collection, return ONLY valid JSON with this exact shape:
{{
  "prompt_part": "{part_name}",
  "items": [
    {item_shape}
  ]
}}

The "items" array must contain exactly these item ids in order: {", ".join(item_ids)}."""

    if include_guideline_context:
        return f"""【{input_label}】(Simultaneously uploaded the guideline file)

**Your role**:

Suppose you are a clinical practice guideline developer and an evaluator of the methodological quality of clinical practice guidelines.

**Your task**:

Your task is to conduct a thorough critical appraisal of the submitted clinical practice guideline. Your insights will contribute to an in-depth understanding of the guideline's quality and whether its development methods are rigorous and transparent. Please utilize your expertise, respond to the questions listed in the following **Questions part** based on the following **Response rules** and **Question descriptions**. **Question descriptions** add additional detailed descriptions to the questions listed in **Questions part**. The **Question descriptions** consist of three parts: Description, Locating Information, and How to Rate.

The uploaded guideline file is represented below as extracted main PDF text. Evaluate based only on this provided text. Do not assume information from appendices, supplementary files, or external sources unless it appears in the provided text.

**Response rules**:

1. All questions are rated on the following 7-point scale:

Score of 1 (Strongly Disagree): A score of 1 should be given when there is no information that is relevant to the question, if the concept is very poorly reported, or if the authors state explicitly that criteria were not met.

Score of 7 (Strongly Agree): A score of 7 should be given if the quality of reporting is exceptional and meets all the criteria and conditions required.

Scores between 2 and 6: A score between 2 and 6 is assigned when the reporting of the question does not meet the full criteria or considerations. A score is assigned depending on the completeness and quality of reporting. Scores increase as more criteria are met and considerations addressed.

2. For questions to which you rated with 1, give your detailed explanation. For questions to which you rated with 2, 3, 4, 5, 6, or 7, list the original and unmodified content from the uploaded article in ascending order, formatted as 1, 2, 3, etc.

3. Each response should be informed and supported by the question description, ensuring a clear connection between the question asked and the description given.

**Response Example**:

Question 1):

Score: 1/2/3/4/5/6/7

Original and unmodified content from the uploaded article:

1. xxxxxx.

2. xxxxxx.

Etc.

**Question descriptions**:

{descriptions}

**Questions part**:

{questions}

**Guideline text**:

{guideline_context}

{json_instruction}
"""

    return f"""【{input_label}】

Use the same uploaded guideline text provided in the first input of this conversation.

**Question descriptions**:

{descriptions}

**Questions part**:

{questions}

{json_instruction}
"""


async def chat_completion_messages(
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, Any]]:
    """Call an OpenAI-compatible /v1/chat/completions endpoint with a message history."""

    url = f"{API_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    payload: dict[str, Any] = {
        "model": MODEL_ID,
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_OUTPUT_TOKENS,
    }
    if REQUEST_EXTRA_BODY:
        payload["extra_body"] = REQUEST_EXTRA_BODY
    if USE_JSON_RESPONSE_FORMAT:
        payload["response_format"] = {"type": "json_object"}

    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code >= 400 and payload.get("response_format"):
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format", None)
                response = await client.post(url, headers=headers, json=fallback_payload)
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return str(content), data.get("usage", {})
        except Exception as exc:  # noqa: BLE001 - preserve original API/parse errors in metadata.
            last_error = exc
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY_SECONDS)

    raise RuntimeError(f"LLM request failed after {RETRY_ATTEMPTS} attempts: {last_error}")


async def chat_completion(client: httpx.AsyncClient, prompt: str) -> tuple[str, dict[str, Any]]:
    """Call a single-turn OpenAI-compatible /v1/chat/completions endpoint."""

    return await chat_completion_messages(
        client,
        [
            {
                "role": "system",
                "content": "You are a careful AGREE-II guideline evaluator following Wang et al. S-prompting rules. Return only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
    )


def parse_model_json(content: str) -> dict[str, Any]:
    """Parse JSON content, accepting optional markdown fences from weak endpoints."""

    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def normalize_score(value: Any) -> int:
    """Return a valid AGREE-II score or raise ValueError."""

    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if not match:
            raise ValueError(f"Invalid score: {value!r}")
        value = int(match.group(0))
    elif isinstance(value, float):
        value = int(round(value))
    elif not isinstance(value, int):
        raise ValueError(f"Invalid score type: {type(value).__name__}")

    if value < 1 or value > 7:
        raise ValueError(f"Score out of range: {value}")
    return value


def normalize_evidence_sources(evidence: Any) -> list[str]:
    """Extract chunk ids from model evidence output."""

    sources: list[str] = []
    if isinstance(evidence, list):
        for item in evidence:
            chunk_id = item.get("chunk_id") if isinstance(item, dict) else None
            if chunk_id and str(chunk_id) not in sources:
                sources.append(str(chunk_id))
    return sources


def parse_part_items(model_data: Any, item_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Extract per-item JSON objects from one Wang-style prompt part response."""

    if isinstance(model_data, list):
        raw_items = model_data
    elif isinstance(model_data, dict) and isinstance(model_data.get("items"), list):
        raw_items = model_data["items"]
    elif isinstance(model_data, dict):
        raw_items = []
        for item_id in item_ids:
            if isinstance(model_data.get(item_id), dict):
                raw_items.append({**model_data[item_id], "item_id": item_id})
        if not raw_items:
            raw_items = [value for value in model_data.values() if isinstance(value, dict)]
    else:
        raw_items = []

    parsed: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            continue
        item_id = normalize_item_id(raw_item.get("item_id") or raw_item.get("id") or "")
        if not item_id and index < len(item_ids):
            item_id = item_ids[index]
        if item_id not in item_ids:
            continue
        normalize_score(raw_item.get("score"))
        parsed[item_id] = raw_item
    return parsed


def normalize_item_id(value: Any) -> str:
    """Normalize model item identifiers to item_N format."""

    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("item_"):
        return text
    if text.startswith("item"):
        suffix = text[4:].lstrip("_-")
        return f"item_{suffix}" if suffix else text
    match = re.search(r"\d+", text)
    if match:
        return f"item_{int(match.group(0))}"
    return text


def build_success_item_result(
    item_id: str,
    domain_id: int,
    title: str,
    model_data: dict[str, Any],
    usage: dict[str, Any],
    context_metadata: dict[str, Any],
    guide: GuideEntry,
    query_index: int | None = None,
    prompt_part: str | None = None,
    part_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert model output to the existing item evaluation shape."""

    score = normalize_score(model_data.get("score"))
    step3 = model_data.get("step3_reasoning", {})
    if not isinstance(step3, dict):
        step3 = {
            "summary": str(step3),
            "score_justification": str(step3),
        }

    evidence = model_data.get("step2_evidence", [])
    if not isinstance(evidence, list):
        evidence = [evidence]

    return {
        "item_id": item_id,
        "domain": domain_id,
        "title": title,
        "chain_of_thought": {
            "step1_locate": str(model_data.get("step1_locate", "")),
            "step2_evidence": evidence,
            "step3_reasoning": step3,
            "score": score,
        },
        "evidence_sources": normalize_evidence_sources(evidence),
        "metadata": {
            "method": "cot_direct_llm",
            "model": MODEL_ID,
            "status": "success",
            "guide_code": guide.guide_code,
            "main_pdf_name": guide.main_pdf_name,
            "context_policy": context_metadata,
            "usage": usage,
            "query_index": query_index,
            "prompt_part": prompt_part,
            "part_usage": part_usage or usage,
            "evaluated_at": datetime.now().isoformat(),
        },
    }


def build_failed_item_result(
    item_id: str,
    domain_id: int,
    title: str,
    error: str,
    context_metadata: dict[str, Any] | None,
    guide: GuideEntry,
    query_index: int | None = None,
    prompt_part: str | None = None,
) -> dict[str, Any]:
    """Build a fallback item result with score=1."""

    return {
        "item_id": item_id,
        "domain": domain_id,
        "title": title,
        "chain_of_thought": {
            "step1_locate": "",
            "step2_evidence": [],
            "step3_reasoning": {
                "summary": "Evaluation failed; fallback score assigned.",
                "score_justification": "Fallback score 1 was assigned because the model response could not be completed or parsed.",
            },
            "score": FALLBACK_SCORE,
        },
        "evidence_sources": [],
        "metadata": {
            "method": "cot_direct_llm",
            "model": MODEL_ID,
            "status": "failed",
            "fallback_score": FALLBACK_SCORE,
            "error": error,
            "guide_code": guide.guide_code,
            "main_pdf_name": guide.main_pdf_name,
            "context_policy": context_metadata or {},
            "query_index": query_index,
            "prompt_part": prompt_part,
            "evaluated_at": datetime.now().isoformat(),
        },
    }


def is_valid_existing_item(path: Path) -> bool:
    """Return True when an existing item JSON should be skipped during resume."""

    if OVERWRITE_EXISTING or not RESUME or not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        normalize_score(data.get("chain_of_thought", {}).get("score"))
        status = data.get("metadata", {}).get("status")
        if RESUME_SUCCESS_ONLY and status is not None:
            return status == "success"
        return True
    except Exception:
        return False


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON with stable UTF-8 formatting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_item_score(path: Path) -> int:
    """Read an item score from an evaluation JSON file."""

    data = json.loads(path.read_text(encoding="utf-8"))
    return normalize_score(data.get("chain_of_thought", {}).get("score"))


def item_domain(item_id: str) -> int:
    """Resolve the AGREE-II domain id for an item id."""

    for domain_id, item_ids in DOMAIN_ITEMS.items():
        if item_id in item_ids:
            return domain_id
    raise ValueError(f"Unknown item id: {item_id}")


def split_usage(usage: dict[str, Any], count: int) -> dict[str, Any]:
    """Allocate one multi-item prompt usage record across its items."""

    if count <= 0:
        return usage
    split: dict[str, Any] = {}
    for key, value in usage.items():
        if isinstance(value, int):
            split[key] = value // count
        else:
            split[key] = value
    return split


def generate_token_usage_from_items(task_dir: Path) -> None:
    """Generate token_usage.json from completed item metadata after concurrent work."""

    eval_dir = task_dir / ".ama" / "evaluations"
    usage_path = task_dir / ".ama" / "token_usage.json"
    data = {
        "method": "cot_direct_llm",
        "model": MODEL_ID,
        "items": [],
        "totals": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

    for item_path in sorted(eval_dir.glob("item_*.json"), key=lambda path: int(path.stem.split("_")[1])):
        try:
            item_data = json.loads(item_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        usage = item_data.get("metadata", {}).get("usage", {})
        if not usage:
            continue

        entry = {
            "item_id": item_data.get("item_id", item_path.stem),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "recorded_at": item_data.get("metadata", {}).get("evaluated_at", datetime.now().isoformat()),
        }
        data["items"].append(entry)
        for key in data["totals"]:
            data["totals"][key] += entry[key]

    write_json(usage_path, data)


def generate_report(
    task_dir: Path,
    guide: GuideEntry,
    prompt_blocks: dict[str, str],
    query_index: int | None = None,
) -> None:
    """Generate appraisal_report.json and appraisal_report.md from item outputs."""

    eval_dir = task_dir / ".ama" / "evaluations"
    domain_scores: dict[str, dict[str, Any]] = {}
    total_raw_score = 0
    total_items_evaluated = 0

    for domain_id, item_ids in DOMAIN_ITEMS.items():
        items = []
        domain_total = 0
        evaluated_count = 0
        for item_id in item_ids:
            path = eval_dir / f"{item_id}.json"
            title = item_title(prompt_blocks[item_id])
            score = None
            if path.exists():
                score = read_item_score(path)
                domain_total += score
                total_raw_score += score
                evaluated_count += 1
                total_items_evaluated += 1

            items.append(
                {
                    "item_id": item_id,
                    "score": score,
                    "title": title,
                }
            )

        if evaluated_count > 0:
            max_possible = 7 * evaluated_count
            min_possible = 1 * evaluated_count
            standardized_score = ((domain_total - min_possible) / (max_possible - min_possible)) * 100
        else:
            standardized_score = None

        domain_scores[str(domain_id)] = {
            "name": DOMAIN_NAMES[domain_id],
            "score": round(standardized_score, 1) if standardized_score is not None else None,
            "items": items,
            "item_count": len(item_ids),
            "evaluated_count": evaluated_count,
            "total_raw_score": domain_total,
        }

    report = {
        "guideline_title": guide.guide_name,
        "evaluator": "CoT Direct LLM",
        "evaluation_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task_id": guide.guide_code,
        "domain_scores": domain_scores,
        "overall_assessment": build_overall_assessment(domain_scores, total_raw_score),
        "total_raw_score": total_raw_score,
        "overall_quality_score": calculate_overall_quality_score(total_raw_score),
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "total_items_evaluated": total_items_evaluated,
            "experiment_method": "cot_direct_llm",
            "model": MODEL_ID,
            "main_pdf_name": guide.main_pdf_name,
            "main_pdf_pages": guide.main_pdf_pages,
            "seq_no": guide.seq_no,
            "query_index": query_index,
        },
    }

    write_json(task_dir / "appraisal_report.json", report)
    (task_dir / "appraisal_report.md").write_text(generate_markdown_report(report), encoding="utf-8")


def calculate_overall_quality_score(total_raw_score: int) -> int:
    """Calculate the existing 1-7 overall quality score from raw total."""

    if total_raw_score <= 23:
        return 1
    if total_raw_score <= 46:
        return 2
    if total_raw_score <= 69:
        return 3
    if total_raw_score <= 92:
        return 4
    if total_raw_score <= 115:
        return 5
    if total_raw_score <= 138:
        return 6
    return 7


def build_overall_assessment(domain_scores: dict[str, dict[str, Any]], total_raw_score: int) -> dict[str, Any]:
    """Build a concise report-level assessment from domain scores."""

    strengths = []
    weaknesses = []
    for domain_id in sorted(domain_scores, key=int):
        domain = domain_scores[domain_id]
        score = domain.get("score")
        name = domain.get("name", f"Domain {domain_id}")
        if isinstance(score, (int, float)) and score >= 70:
            strengths.append(f"{name}: {score:.1f}%")
        elif isinstance(score, (int, float)) and score < 50:
            weaknesses.append(f"{name}: {score:.1f}%")

    if not strengths:
        strengths.append("No high-scoring AGREE-II domain was identified by the direct LLM baseline.")
    if not weaknesses:
        weaknesses.append("No domain scored below 50% in the direct LLM baseline.")

    domain_3_score = domain_scores.get("3", {}).get("score")
    if isinstance(domain_3_score, (int, float)) and domain_3_score < 30:
        recommendation = "Do not recommend"
    elif total_raw_score > 96:
        recommendation = "Recommend"
    elif total_raw_score > 88:
        recommendation = "Recommend with modifications"
    else:
        recommendation = "Do not recommend"

    return {
        "strengths": strengths,
        "weaknesses": weaknesses,
        "recommendation": recommendation,
    }


def generate_markdown_report(report: dict[str, Any]) -> str:
    """Generate a compact markdown report."""

    lines = [
        "# AGREE-II Guideline Quality Appraisal Report",
        "",
        "## Guideline Information",
        f"- **Title**: {report['guideline_title']}",
        f"- **Appraisal Tool**: {report['evaluator']}",
        f"- **Appraisal Date**: {report['evaluation_date']}",
        "",
        "---",
        "",
        "## Domain Scores Overview",
        "",
        "| Domain | Score |",
        "|------|------|",
    ]

    for domain_id in sorted(report["domain_scores"], key=int):
        domain = report["domain_scores"][domain_id]
        score = domain.get("score")
        score_text = f"{score:.1f}%" if isinstance(score, (int, float)) else "Not evaluated"
        lines.append(f"| {domain.get('name', f'Domain {domain_id}')} | {score_text} |")

    lines.extend(
        [
            "",
            "---",
            "",
            "## Overall Assessment",
            "",
            "### Strengths",
        ]
    )
    lines.extend(f"- {value}" for value in report["overall_assessment"]["strengths"])
    lines.extend(["", "### Weaknesses"])
    lines.extend(f"- {value}" for value in report["overall_assessment"]["weaknesses"])
    lines.extend(["", "### Recommendation", f"**{report['overall_assessment']['recommendation']}**"])

    return "\n".join(lines)


async def evaluate_item(
    task_dir: Path,
    guide: GuideEntry,
    item_id: str,
    item_prompt_block: str,
    context: ContextBuildResult,
    client: httpx.AsyncClient,
    item_semaphore: asyncio.Semaphore,
    llm_semaphore: asyncio.Semaphore,
    progress: Progress | None,
    guide_task_id: TaskID | None,
) -> None:
    """Evaluate a single guide-item pair and write its result JSON."""

    eval_path = task_dir / ".ama" / "evaluations" / f"{item_id}.json"
    if is_valid_existing_item(eval_path):
        if LOG_ITEM_RESULTS:
            log_status(f"{guide.guide_code} {item_id}: skipped")
        if progress is not None and guide_task_id is not None:
            progress.advance(guide_task_id)
        return

    domain_id = item_domain(item_id)
    title = item_title(item_prompt_block)

    async with item_semaphore:
        try:
            prompt = build_prompt(item_id, item_prompt_block, context.text)
            async with llm_semaphore:
                content, usage = await chat_completion(client, prompt)
            model_data = parse_model_json(content)
            result = build_success_item_result(
                item_id=item_id,
                domain_id=domain_id,
                title=title,
                model_data=model_data,
                usage=usage,
                context_metadata=context.metadata,
                guide=guide,
            )
            write_json(eval_path, result)
            if LOG_ITEM_RESULTS:
                log_status(f"{guide.guide_code} {item_id}: score={result['chain_of_thought']['score']}")
        except Exception as exc:  # noqa: BLE001 - failure is intentionally converted to score=1.
            result = build_failed_item_result(
                item_id=item_id,
                domain_id=domain_id,
                title=title,
                error=str(exc),
                context_metadata=context.metadata,
                guide=guide,
            )
            write_json(eval_path, result)
            if LOG_ITEM_RESULTS:
                log_status(f"{guide.guide_code} {item_id}: failed -> score={FALLBACK_SCORE}: {exc}")
        finally:
            if progress is not None and guide_task_id is not None:
                progress.advance(guide_task_id)


async def evaluate_prompt_part(
    task_dir: Path,
    guide: GuideEntry,
    query_index: int,
    part_name: str,
    input_label: str,
    item_ids: list[str],
    prompt_blocks: dict[str, str],
    context: ContextBuildResult,
    messages: list[dict[str, str]],
    client: httpx.AsyncClient,
    llm_semaphore: asyncio.Semaphore,
    progress: Progress | None,
    guide_task_id: TaskID | None,
    include_guideline_context: bool,
) -> None:
    """Evaluate one Wang-style prompt part and write all item JSON files."""

    pending_item_ids = [
        item_id
        for item_id in item_ids
        if not is_valid_existing_item(task_dir / ".ama" / "evaluations" / f"{item_id}.json")
    ]
    if not pending_item_ids:
        if progress is not None and guide_task_id is not None:
            progress.advance(guide_task_id)
        return

    prompt = build_part_prompt(
        part_name=part_name,
        input_label=input_label,
        item_ids=item_ids,
        prompt_blocks=prompt_blocks,
        guideline_context=context.text,
        include_guideline_context=include_guideline_context,
    )
    messages.append({"role": "user", "content": prompt})

    try:
        async with llm_semaphore:
            content, usage = await chat_completion_messages(client, messages)
        messages.append({"role": "assistant", "content": content})
        model_data = parse_model_json(content)
        items_by_id = parse_part_items(model_data, item_ids)
        per_item_usage = split_usage(usage, len(item_ids))
        missing = [item_id for item_id in pending_item_ids if item_id not in items_by_id]
        if missing:
            raise ValueError(f"Prompt part response missing items: {missing}")

        for item_id in item_ids:
            eval_path = task_dir / ".ama" / "evaluations" / f"{item_id}.json"
            if is_valid_existing_item(eval_path):
                continue
            result = build_success_item_result(
                item_id=item_id,
                domain_id=item_domain(item_id),
                title=item_title(prompt_blocks[item_id]),
                model_data=items_by_id[item_id],
                usage=per_item_usage,
                context_metadata=context.metadata,
                guide=guide,
                query_index=query_index,
                prompt_part=part_name,
                part_usage=usage,
            )
            write_json(eval_path, result)
    except Exception as exc:  # noqa: BLE001 - match paper redo policy by keeping failure explicit in metadata.
        error = f"{part_name} failed: {exc}"
        messages.append({"role": "assistant", "content": json.dumps({"error": error}, ensure_ascii=False)})
        for item_id in item_ids:
            eval_path = task_dir / ".ama" / "evaluations" / f"{item_id}.json"
            if is_valid_existing_item(eval_path):
                continue
            result = build_failed_item_result(
                item_id=item_id,
                domain_id=item_domain(item_id),
                title=item_title(prompt_blocks[item_id]),
                error=error,
                context_metadata=context.metadata,
                guide=guide,
                query_index=query_index,
                prompt_part=part_name,
            )
            write_json(eval_path, result)
        log_status(f"{guide.guide_code} query{query_index} {part_name}: failed -> fallback scores assigned: {exc}")
    finally:
        if progress is not None and guide_task_id is not None:
            progress.advance(guide_task_id)


async def evaluate_guide(
    run_dir: Path,
    guide: GuideEntry,
    query_index: int,
    prompt_blocks: dict[str, str],
    char_limit: int,
    client: httpx.AsyncClient,
    guide_semaphore: asyncio.Semaphore,
    llm_semaphore: asyncio.Semaphore,
    progress: Progress | None,
    total_task_id: TaskID | None,
) -> None:
    """Evaluate one independent Wang-style query for one guide."""

    async with guide_semaphore:
        task_dir = run_dir / guide.guide_code
        task_dir.mkdir(parents=True, exist_ok=True)

        item_ids = [item_id for _part_name, _input_label, part_item_ids in PROMPT_PARTS for item_id in part_item_ids]
        if ITEM_LIMIT is not None:
            item_ids = item_ids[:ITEM_LIMIT]
        prompt_parts = [
            (part_name, input_label, [item_id for item_id in part_item_ids if item_id in item_ids])
            for part_name, input_label, part_item_ids in PROMPT_PARTS
        ]
        prompt_parts = [
            (part_name, input_label, part_item_ids)
            for part_name, input_label, part_item_ids in prompt_parts
            if part_item_ids
        ]

        guide_task_id = None
        if progress is not None:
            guide_task_id = progress.add_task(f"query{query_index} {guide.guide_code}", total=len(prompt_parts))

        if all(is_valid_existing_item(task_dir / ".ama" / "evaluations" / f"{item_id}.json") for item_id in item_ids):
            generate_token_usage_from_items(task_dir)
            generate_report(task_dir, guide, prompt_blocks, query_index=query_index)
            if progress is not None and guide_task_id is not None:
                progress.update(
                    guide_task_id,
                    completed=len(prompt_parts),
                    description=f"query{query_index} {guide.guide_code} done",
                )
                progress.remove_task(guide_task_id)
            if total_task_id is not None and progress is not None:
                progress.advance(total_task_id)
            log_status(f"{guide.guide_code} query{query_index}: all target items already valid; report regenerated")
            return

        pdf_path = GUIDES_DIR / guide.main_pdf_name
        log_status(f"{guide.guide_code} query{query_index}: extracting main PDF {guide.main_pdf_name}")
        try:
            chunks = await asyncio.to_thread(extract_pdf_chunks, pdf_path)
            context = build_guideline_context(chunks, char_limit)
        except Exception as exc:  # noqa: BLE001 - keep the full run alive when one PDF is bad.
            context_metadata = {
                "main_pdf_only": True,
                "include_attachments": False,
                "char_limit": char_limit,
                "original_chars": 0,
                "included_chars": 0,
                "truncated": False,
                "truncation": "pdf_extraction_failed",
            }
            for item_id in item_ids:
                eval_path = task_dir / ".ama" / "evaluations" / f"{item_id}.json"
                if is_valid_existing_item(eval_path):
                    if LOG_ITEM_RESULTS:
                        log_status(f"{guide.guide_code} {item_id}: skipped after PDF extraction failure")
                    continue
                result = build_failed_item_result(
                    item_id=item_id,
                    domain_id=item_domain(item_id),
                    title=item_title(prompt_blocks[item_id]),
                    error=f"PDF extraction failed: {exc}",
                    context_metadata=context_metadata,
                    guide=guide,
                    query_index=query_index,
                )
                write_json(eval_path, result)
            if progress is not None and guide_task_id is not None:
                progress.update(guide_task_id, completed=len(prompt_parts))
            generate_token_usage_from_items(task_dir)
            generate_report(task_dir, guide, prompt_blocks, query_index=query_index)
            if progress is not None and guide_task_id is not None:
                progress.update(guide_task_id, description=f"query{query_index} {guide.guide_code} failed")
                progress.remove_task(guide_task_id)
            if total_task_id is not None and progress is not None:
                progress.advance(total_task_id)
            log_status(f"{guide.guide_code} query{query_index}: PDF extraction failed -> fallback reports generated: {exc}")
            return

        messages = [
            {
                "role": "system",
                "content": "You are a careful AGREE-II guideline evaluator following Wang et al. S-prompting rules. Return only valid JSON.",
            }
        ]
        for part_index, (part_name, input_label, part_item_ids) in enumerate(prompt_parts):
            await evaluate_prompt_part(
                task_dir=task_dir,
                guide=guide,
                query_index=query_index,
                part_name=part_name,
                input_label=input_label,
                item_ids=part_item_ids,
                prompt_blocks=prompt_blocks,
                context=context,
                messages=messages,
                client=client,
                llm_semaphore=llm_semaphore,
                progress=progress,
                guide_task_id=guide_task_id,
                include_guideline_context=part_index == 0,
            )

        generate_token_usage_from_items(task_dir)
        generate_report(task_dir, guide, prompt_blocks, query_index=query_index)
        if progress is not None and guide_task_id is not None:
            progress.update(guide_task_id, description=f"query{query_index} {guide.guide_code} done")
            progress.remove_task(guide_task_id)
        if total_task_id is not None and progress is not None:
            progress.advance(total_task_id)


async def run_experiment() -> None:
    """Run the full CoT Direct LLM baseline experiment."""

    validate_concurrency_config()
    entries = load_guide_entries()
    if GUIDE_LIMIT is not None:
        entries = entries[:GUIDE_LIMIT]

    validate_main_pdfs(entries)
    prompt_blocks = load_prompt_blocks(PROMPTS_RAW_PATH)
    char_limit = get_char_limit(MODEL_ID)
    run_dirs = build_output_dirs()
    for run_dir in run_dirs:
        run_dir.mkdir(parents=True, exist_ok=True)

    log_status("Run outputs:")
    for query_index, run_dir in enumerate(run_dirs, start=1):
        log_status(f"  query{query_index}: {run_dir}")
    log_status(f"Model: {MODEL_ID}")
    log_status(f"Guides: {len(entries)}")
    log_status(f"Independent queries per guide: {QUERY_COUNT}")
    log_status(f"Character limit: {char_limit}")
    log_status(f"Guide concurrency: {MAX_GUIDE_CONCURRENCY}")
    log_status(f"Total LLM concurrency: {MAX_TOTAL_LLM_CONCURRENCY}")

    guide_semaphore = asyncio.Semaphore(MAX_GUIDE_CONCURRENCY)
    llm_semaphore = asyncio.Semaphore(MAX_TOTAL_LLM_CONCURRENCY)

    progress_context = create_progress() if SHOW_PROGRESS else None
    if progress_context is None:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            results = await run_guide_tasks(
                entries=entries,
                run_dirs=run_dirs,
                prompt_blocks=prompt_blocks,
                char_limit=char_limit,
                client=client,
                guide_semaphore=guide_semaphore,
                llm_semaphore=llm_semaphore,
                progress=None,
                total_task_id=None,
            )
    else:
        with progress_context as progress:
            total_task_id = progress.add_task("Total guide queries", total=len(entries) * len(run_dirs))
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                results = await run_guide_tasks(
                    entries=entries,
                    run_dirs=run_dirs,
                    prompt_blocks=prompt_blocks,
                    char_limit=char_limit,
                    client=client,
                    guide_semaphore=guide_semaphore,
                    llm_semaphore=llm_semaphore,
                    progress=progress,
                    total_task_id=total_task_id,
                )

    failures = [result for result in results if isinstance(result, Exception)]
    if failures:
        log_status(f"Completed with {len(failures)} guide-level unexpected failures:")
        for failure in failures[:10]:
            log_status(f"  - {failure}")

    log_status("Done. Outputs saved to:")
    for query_index, run_dir in enumerate(run_dirs, start=1):
        log_status(f"  query{query_index}: {run_dir}")


async def run_guide_tasks(
    entries: list[GuideEntry],
    run_dirs: list[Path],
    prompt_blocks: dict[str, str],
    char_limit: int,
    client: httpx.AsyncClient,
    guide_semaphore: asyncio.Semaphore,
    llm_semaphore: asyncio.Semaphore,
    progress: Progress | None,
    total_task_id: TaskID | None,
) -> list[Any]:
    """Schedule all guide tasks and return per-guide outcomes."""

    guide_tasks = []
    for query_index, run_dir in enumerate(run_dirs, start=1):
        for guide in entries:
            guide_tasks.append(
                asyncio.create_task(
                    evaluate_guide(
                        run_dir=run_dir,
                        guide=guide,
                        query_index=query_index,
                        prompt_blocks=prompt_blocks,
                        char_limit=char_limit,
                        client=client,
                        guide_semaphore=guide_semaphore,
                        llm_semaphore=llm_semaphore,
                        progress=progress,
                        total_task_id=total_task_id,
                    )
                )
            )
    return await asyncio.gather(*guide_tasks, return_exceptions=True)


def main() -> None:
    """Entrypoint wrapper for asyncio."""

    asyncio.run(run_experiment())


if __name__ == "__main__":
    main()
