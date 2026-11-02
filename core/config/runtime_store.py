"""
运行时 LLM 配置持久化存储。

使用项目根目录 data/ama.db 中的 llm_runtime_config 表，
为 Web 管理台提供可编辑、可持久化的模型配置覆盖层。
"""
from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_DIR = PROJECT_ROOT / "data"
DB_PATH = DB_DIR / "ama.db"

_CREATE_LLM_RUNTIME_CONFIG_TABLE = """
CREATE TABLE IF NOT EXISTS llm_runtime_config (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    provider          TEXT,
    base_url          TEXT,
    api_key_encrypted TEXT,
    model_name        TEXT,
    timeout           INTEGER,
    is_vision_model   INTEGER,
    context_window    INTEGER,
    context_window_overridden INTEGER NOT NULL DEFAULT 0,
    updated_by        TEXT DEFAULT '',
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


_initialized_paths: set[str] = set()
_initialized_lock = threading.Lock()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create/upgrade the runtime table once per database file per process."""
    path_key = str(DB_PATH)
    with _initialized_lock:
        if path_key in _initialized_paths:
            return
        conn.executescript(_CREATE_LLM_RUNTIME_CONFIG_TABLE)
        # Serialize schema upgrades across the API and worker processes.
        conn.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_runtime_config)")}
        if "context_window" not in columns:
            conn.execute("ALTER TABLE llm_runtime_config ADD COLUMN context_window INTEGER")
        if "context_window_overridden" not in columns:
            conn.execute("ALTER TABLE llm_runtime_config ADD COLUMN context_window_overridden INTEGER NOT NULL DEFAULT 0")
            # Existing positive limits are explicit overrides; legacy NULLs inherit the environment.
            conn.execute("UPDATE llm_runtime_config SET context_window_overridden = 1 WHERE context_window IS NOT NULL")
        conn.commit()
        _initialized_paths.add(path_key)


def _connect() -> sqlite3.Connection:
    """创建运行时配置数据库连接。"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def _build_fernet(secret: str) -> Fernet:
    """根据配置密钥构造对称加密器。"""
    if not secret:
        raise ValueError("未配置 LLM_CONFIG_SECRET，无法加解密 LLM API Key")
    derived_key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(derived_key)


def encrypt_api_key(plain_text: str, secret: str) -> str:
    """加密 LLM API Key。"""
    return _build_fernet(secret).encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_api_key(cipher_text: str, secret: str) -> str:
    """解密 LLM API Key。"""
    try:
        return _build_fernet(secret).decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("LLM_CONFIG_SECRET 无法解密已保存的 LLM API Key") from exc


def get_runtime_llm_config(secret: str) -> dict[str, Any] | None:
    """读取运行时 LLM 配置，并在需要时解密 API Key。"""
    with closing(_connect()) as conn:
        row = conn.execute(
            """
            SELECT provider, base_url, api_key_encrypted, model_name, timeout,
                   is_vision_model, context_window, context_window_overridden, updated_by, updated_at
            FROM llm_runtime_config
            WHERE id = 1
            """
        ).fetchone()

    if row is None:
        return None

    config = dict(row)
    if not config.pop("context_window_overridden"):
        config.pop("context_window")
    api_key_encrypted = config.pop("api_key_encrypted", None)
    if api_key_encrypted is not None:
        try:
            config["api_key"] = decrypt_api_key(api_key_encrypted, secret)
        except ValueError as exc:
            logger.warning("读取运行时 LLM API Key 失败，已忽略保存的密文: %s", exc)
            config["api_key"] = None
    else:
        config["api_key"] = None

    if config.get("is_vision_model") is not None:
        config["is_vision_model"] = bool(config["is_vision_model"])
    return config


def upsert_runtime_llm_config(
    updates: dict[str, Any],
    *,
    secret: str,
    updated_by: str | None = None,
) -> None:
    """写入运行时 LLM 配置。"""
    window = updates.get("context_window")
    if window is not None and (type(window) is not int or window <= 0):
        raise ValueError("context_window must be a positive integer or null")
    with closing(_connect()) as conn:
        current = conn.execute(
            """
            SELECT provider, base_url, api_key_encrypted, model_name, timeout, is_vision_model,
                   context_window, context_window_overridden
            FROM llm_runtime_config
            WHERE id = 1
            """
        ).fetchone()

        merged: dict[str, Any] = dict(current) if current is not None else {
            "provider": None,
            "base_url": None,
            "api_key_encrypted": None,
            "model_name": None,
            "timeout": None,
            "is_vision_model": None,
            "context_window": None,
            "context_window_overridden": 0,
        }

        for key in ("provider", "base_url", "model_name", "timeout", "context_window"):
            if key in updates:
                merged[key] = updates[key]

        if "context_window" in updates:
            merged["context_window_overridden"] = 1

        if "is_vision_model" in updates:
            merged["is_vision_model"] = 1 if updates["is_vision_model"] else 0

        if "api_key" in updates:
            api_key = updates["api_key"]
            if api_key is None or (isinstance(api_key, str) and not api_key.strip()):
                merged["api_key_encrypted"] = None
            else:
                merged["api_key_encrypted"] = encrypt_api_key(str(api_key).strip(), secret)

        merged["updated_by"] = (updated_by or "").strip()

        conn.execute(
            """
            INSERT INTO llm_runtime_config (
                id, provider, base_url, api_key_encrypted, model_name,
                timeout, is_vision_model, context_window, context_window_overridden, updated_by, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                provider = excluded.provider,
                base_url = excluded.base_url,
                api_key_encrypted = excluded.api_key_encrypted,
                model_name = excluded.model_name,
                timeout = excluded.timeout,
                is_vision_model = excluded.is_vision_model,
                context_window = excluded.context_window,
                context_window_overridden = excluded.context_window_overridden,
                updated_by = excluded.updated_by,
                updated_at = datetime('now')
            """,
            (
                merged["provider"],
                merged["base_url"],
                merged["api_key_encrypted"],
                merged["model_name"],
                merged["timeout"],
                merged["is_vision_model"],
                merged["context_window"],
                merged["context_window_overridden"],
                merged["updated_by"],
            ),
        )
        conn.commit()


def clear_runtime_llm_config() -> None:
    """删除运行时 LLM 覆盖配置，恢复为 .env 中的配置。"""
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM llm_runtime_config WHERE id = 1")
        conn.commit()


def clear_runtime_llm_context_window() -> None:
    """删除管理台对上下文窗口的覆盖，使其回落到 .env 的 LLM_CONTEXT_WINDOW。"""
    with closing(_connect()) as conn:
        conn.execute(
            """
            UPDATE llm_runtime_config
            SET context_window = NULL, context_window_overridden = 0, updated_at = datetime('now')
            WHERE id = 1
            """
        )
        conn.commit()


def clear_runtime_llm_base_url() -> None:
    """删除管理台对 base_url 的覆盖，使其回落到 .env 的 LLM_BASE_URL。"""
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE llm_runtime_config SET base_url = NULL, updated_at = datetime('now') WHERE id = 1"
        )
        conn.commit()
