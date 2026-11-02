"""在目标目录中原子保存 JSON 文件。"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomically(path: str | Path, data: Any) -> None:
    """先写入并同步临时文件，再替换目标文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as output:
            temp_path = Path(output.name)
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
