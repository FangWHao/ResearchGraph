"""Windows 错误日志只追加；固定目录句柄并拒绝重解析点。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any


def append(path: Path, raw: bytes) -> None:
    native: Any = ctypes
    library = native.WinDLL("kernel32", use_last_error=True)
    library.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    library.CreateFileW.restype = wintypes.HANDLE
    library.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    library.GetFileInformationByHandleEx.restype = wintypes.BOOL
    library.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    library.WriteFile.restype = wintypes.BOOL
    library.CloseHandle.argtypes = [wintypes.HANDLE]
    library.CloseHandle.restype = wintypes.BOOL

    class Attributes(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    handles = []

    def opened(value: Path, *, directory: bool) -> Any:
        # 所有目录不共享删除权限，直到写完日志；OPEN_REPARSE_POINT 不跟随末级链接。
        handle = library.CreateFileW(
            str(value),
            0x80 if directory else 0x80 | 4,
            1 | 2,
            None,
            3 if directory else 4,
            0x00200000 | (0x02000000 if directory else 0),
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise native.WinError(native.get_last_error())
        handles.append(handle)
        attributes = Attributes()
        if not library.GetFileInformationByHandleEx(
            handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)
        ):
            raise native.WinError(native.get_last_error())
        if attributes.attributes & 0x400 or bool(attributes.attributes & 0x10) != directory:
            raise ValueError("错误日志路径含重解析点或异常类型")
        return handle

    try:
        absolute = path.absolute()
        current = Path(absolute.anchor)
        opened(current, directory=True)
        for part in absolute.parent.parts[1:]:
            current /= part
            opened(current, directory=True)
        handle = opened(absolute, directory=False)
        written = wintypes.DWORD()
        if not library.WriteFile(handle, raw, len(raw), ctypes.byref(written), None):
            raise native.WinError(native.get_last_error())
        if written.value != len(raw):
            raise OSError("错误日志只完成部分追加")
    finally:
        for handle in reversed(handles):
            library.CloseHandle(handle)
