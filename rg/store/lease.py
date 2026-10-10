"""数据目录的存活占用锁；清除须排除仍在读取或写入的连接与钩子。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rg.store.locking import TaskBusy


@contextmanager
def _windows(root: Path, *, exclusive: bool, writable: bool) -> Iterator[None]:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    # Win32 FFI 的成员在 Linux 的类型存根中不存在。
    native: Any = ctypes
    descriptors: Any = msvcrt
    library = native.WinDLL("kernel32", use_last_error=True)
    library.LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(Overlapped),
    ]
    library.LockFileEx.restype = wintypes.BOOL
    library.UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(Overlapped),
    ]
    library.UnlockFileEx.restype = wintypes.BOOL
    path = root / ".store-use.lock"
    # 只读入口不创建目录或锁文件。先由写入口初始化旧 Windows 数据目录。
    flags = os.O_RDWR | os.O_CREAT if writable else os.O_RDONLY
    descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
    try:
        handle = wintypes.HANDLE(descriptors.get_osfhandle(descriptor))
        overlapped = Overlapped()
        if not library.LockFileEx(
            handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlapped)
        ):
            error = native.get_last_error()
            if error == 33:
                raise TaskBusy("数据目录仍有读取、写入或钩子；关闭后重试清除")
            raise native.WinError(error)
        try:
            yield
        finally:
            if not library.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
                raise native.WinError(native.get_last_error())
    finally:
        os.close(descriptor)


@contextmanager
def lease(root: Path, *, exclusive: bool = False, writable: bool = False) -> Iterator[None]:
    if writable:
        root.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        with _windows(root, exclusive=exclusive, writable=writable):
            yield
        return
    import fcntl

    descriptor = os.open(root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(
                descriptor,
                (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB,
            )
        except OSError:
            raise TaskBusy("数据目录仍有读取、写入或钩子；关闭后重试清除") from None
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
