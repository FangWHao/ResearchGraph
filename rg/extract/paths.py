from __future__ import annotations

import posixpath
import re


def file_paths(text: str) -> set[str]:
    """只识别路径文字，不解释或执行其中的命令。"""
    matches = re.findall(
        r"(?<![\w:])(?:[A-Za-z]:[\\/]|\.?\.?/|/)?"
        r"(?:[\w.@+-]+[\\/])+[\w.@+-]+|"
        r"(?<![\w.])[\w@+-]+\.(?:py|ipynb|r|R|csv|tsv|json|parquet|h5ad|md)(?![\w.])",
        text,
    )
    return {posixpath.normpath(value.replace("\\", "/")) for value in matches}
