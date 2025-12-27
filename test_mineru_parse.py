import asyncio
from pathlib import Path

from core.parsers.mineru_parser import MinerUParser


async def run_parse() -> int:
    pdf_path = Path("input/1-2020-026E.pdf")
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        return 1

    # 长文档配置：增加超时、降低并发、增加重试
    parser = MinerUParser(
        http_timeout=1800,      # 30分钟超时
        max_concurrency=5,      # 降低并发避免服务端压力（226页建议用5）
        max_retries=10,         # 10次重试，提高稳定性
    )
    result = await parser.parse_pdf(
        str(pdf_path),
        output_dir="output/test",
    )

    print("status:", result.get("status"))
    print("pages:", result.get("pages"))
    print("output_path:", result.get("output_path"))
    if result.get("error"):
        print("error:", result.get("error"))

    return 0 if result.get("status") == "success" else 2


def main() -> int:
    return asyncio.run(run_parse())


if __name__ == "__main__":
    raise SystemExit(main())
