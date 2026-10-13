"""Windows 原生钩子固定入口；路径不参与外层 shell 解析。"""

from __future__ import annotations

import base64
import ctypes
import re
from pathlib import Path
from typing import Any


def windows_command(arguments: list[str]) -> str:
    native: Any = ctypes
    library = native.WinDLL("kernel32", use_last_error=True)
    library.GetSystemDirectoryW.argtypes = [native.c_wchar_p, native.c_uint]
    library.GetSystemDirectoryW.restype = native.c_uint
    buffer = native.create_unicode_buffer(32768)
    size = library.GetSystemDirectoryW(buffer, len(buffer))
    if not size or size >= len(buffer):
        raise ValueError("无法取得原生 Windows 系统目录")
    program = Path(buffer.value) / "WindowsPowerShell/v1.0/powershell.exe"
    executable = program.as_posix()
    # 此绝对路径作为 cmd／PowerShell 的普通命令 token；不经 PATH／工作区搜索。
    if not program.is_file() or not re.fullmatch(r"[A-Za-z]:/[A-Za-z0-9_./-]+", executable):
        raise ValueError("系统 PowerShell 路径无法安全调用；请从 WSL 安装")
    literals = ["'" + argument.replace("'", "''") + "'" for argument in arguments]
    script = "try { & " + " ".join(literals) + " } catch {} ; exit 0"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"{executable} -NoProfile -NonInteractive -EncodedCommand {encoded}"
