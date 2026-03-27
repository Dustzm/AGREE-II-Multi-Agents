"""
Unicode 安全清洗工具。

用于移除会导致 UTF-8 / JSON 序列化失败的非法代理字符（surrogate），
并递归清洗嵌套数据结构，降低 LLM 输入输出链路中的编码风险。
"""

from __future__ import annotations

from typing import Any


def strip_surrogates(value: str) -> str:
    """移除字符串中的 Unicode 代理字符。"""
    if not value:
        return value
    return "".join(ch for ch in value if not 0xD800 <= ord(ch) <= 0xDFFF)


def sanitize_unicode(value: Any) -> Any:
    """递归清洗嵌套结构中的字符串字段。"""
    if isinstance(value, str):
        return strip_surrogates(value)
    if isinstance(value, list):
        return [sanitize_unicode(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_unicode(item) for item in value)
    if isinstance(value, dict):
        return {
            sanitize_unicode(key): sanitize_unicode(item)
            for key, item in value.items()
        }
    return value
