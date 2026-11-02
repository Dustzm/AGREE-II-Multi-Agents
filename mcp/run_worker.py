"""从源码目录启动 AMA MCP Worker。"""
from __future__ import annotations

import sys
from pathlib import Path

MCP_SOURCE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_SOURCE_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(MCP_SOURCE_ROOT))

from worker import main  # noqa: E402


if __name__ == "__main__":
    main()
