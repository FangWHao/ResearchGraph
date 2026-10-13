from __future__ import annotations

import json
import posixpath
import re


def file_paths(text: str) -> set[str]:
    """只识别路径文字，不解释或执行其中的命令。"""
    structured: set[str] = set()
    try:
        arguments = json.loads(text)
    except ValueError:
        arguments = None
    if isinstance(arguments, dict):
        for key in ("file_path", "filePath", "path", "target_file", "destination"):
            value = arguments.get(key)
            if isinstance(value, str) and value and "\0" not in value:
                structured.add(value)
        text = "\n".join(value for value in arguments.values() if isinstance(value, str))
    # 引号和反引号中的路径可以含空格；不把周围的自然语言拼进路径。
    quoted = re.findall(r"[`\"']([^`\"'\n]+)[`\"']", text)
    structured.update(value for value in quoted if "/" in value or "\\" in value)
    for value in sorted(structured, key=len, reverse=True):
        text = text.replace(value, " ")
    matches = re.findall(
        r"(?<![\w:])(?:[A-Za-z]:[\\/]|\.?\.?/|/)?"
        r"(?:[\w.@+-]+[\\/])+[\w.@+-]+|"
        r"(?<![\w.])[\w@+-]+\.(?:py|ipynb|r|R|csv|tsv|json|parquet|h5ad|md)(?![\w.])",
        text,
    )
    return {posixpath.normpath(value.replace("\\", "/")) for value in [*matches, *structured]}
