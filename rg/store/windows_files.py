"""Windows 原生目录固定与不覆盖发布；只在原生 Windows 上实例化。"""

from __future__ import annotations

import ctypes
import os
import stat
import struct
from ctypes import wintypes
from pathlib import Path
from typing import Any


class Attributes(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]


class BasicInfo(ctypes.Structure):
    _fields_ = [
        ("created", ctypes.c_int64),
        ("accessed", ctypes.c_int64),
        ("written", ctypes.c_int64),
        ("changed", ctypes.c_int64),
        ("attributes", wintypes.DWORD),
    ]


class RenameInfo(ctypes.Structure):
    _fields_ = [
        ("replace", ctypes.c_bool),
        ("root", wintypes.HANDLE),
        ("length", wintypes.DWORD),
        ("name", wintypes.WCHAR * 1),
    ]


class UnicodeString(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.USHORT),
        ("maximum", wintypes.USHORT),
        ("buffer", ctypes.c_void_p),
    ]


class ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("root", wintypes.HANDLE),
        ("name", ctypes.POINTER(UnicodeString)),
        ("attributes", wintypes.DWORD),
        ("security", ctypes.c_void_p),
        ("quality", ctypes.c_void_p),
    ]


class IoStatus(ctypes.Structure):
    _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]


class FileIdInfo(ctypes.Structure):
    _fields_ = [("volume", ctypes.c_uint64), ("identifier", ctypes.c_ubyte * 16)]


class NativeFiles:
    native: Any
    library: Any
    root: Path
    directories: dict[Path, int]
    observed: dict[Path, tuple[int, int]]
    nt: Any

    def __init__(self, root: Path):
        if os.name != "nt":
            raise RuntimeError("原生目录接口只用于 Windows")
        native: Any = ctypes
        self.native = native
        self.library = native.WinDLL("kernel32", use_last_error=True)
        self.library.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.library.CreateFileW.restype = wintypes.HANDLE
        self.library.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.library.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self.library.GetFileType.argtypes = [wintypes.HANDLE]
        self.library.GetFileType.restype = wintypes.DWORD
        self.library.CloseHandle.argtypes = [wintypes.HANDLE]
        self.library.CloseHandle.restype = wintypes.BOOL
        self.library.WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self.library.WriteFile.restype = wintypes.BOOL
        self.library.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        self.library.FlushFileBuffers.restype = wintypes.BOOL
        self.library.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.library.SetFileInformationByHandle.restype = wintypes.BOOL
        self.library.DeviceIoControl.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self.library.DeviceIoControl.restype = wintypes.BOOL
        self.nt = native.WinDLL("ntdll", use_last_error=True)
        self.nt.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(ObjectAttributes),
            ctypes.POINTER(IoStatus),
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.nt.NtCreateFile.restype = ctypes.c_int32
        self.nt.NtSetInformationFile.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(IoStatus),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_int,
        ]
        self.nt.NtSetInformationFile.restype = ctypes.c_int32
        self.nt.RtlNtStatusToDosError.argtypes = [ctypes.c_int32]
        self.nt.RtlNtStatusToDosError.restype = wintypes.DWORD
        self.root = root.absolute()
        self.directories: dict[Path, int] = {}
        self.observed: dict[Path, tuple[int, int]] = {}

    def _attributes(self, handle: int) -> int:
        return self._tag(handle).attributes

    def _tag(self, handle: int) -> Attributes:
        value = Attributes()
        if not self.library.GetFileInformationByHandleEx(
            handle, 9, ctypes.byref(value), ctypes.sizeof(value)
        ):
            raise self.native.WinError(self.native.get_last_error())
        return value

    def _open(self, path: Path, *, directory: bool, create: bool = False) -> int:
        # 子路径相对已打开父句柄解析；并发目录改名仍可进行，最后再核对路径身份。
        access = 0x80 | 1 if directory else 0x40000000 | 0x10000 if create else 0x80000000
        share = 0 if create else 1 | 2 | 4
        flags = 0x00200000 | (0x02000000 if directory else 0) | (0x80000000 if create else 0)
        if path.parent in self.directories:
            handle = self._relative(path, directory=directory, create=create)
        else:
            handle = self.library.CreateFileW(
                str(path), access, share, None, 1 if create else 3, flags, None
            )
        if handle == ctypes.c_void_p(-1).value:
            raise self.native.WinError(self.native.get_last_error())
        try:
            attributes = self._attributes(handle)
            if (
                attributes & 0x400
                or bool(attributes & 0x10) != directory
                or self.library.GetFileType(handle) != 1
            ):
                raise ValueError("路径含重解析点或不是期望的磁盘文件类型")
        except BaseException:
            try:
                if create:
                    self._delete(handle)
            finally:
                self.library.CloseHandle(handle)
            raise
        return handle

    def _relative(
        self, path: Path, *, directory: bool = False, create: bool = False, metadata: bool = False
    ) -> int:
        raw = path.name.encode("utf-16-le", errors="surrogatepass")
        buffer = ctypes.create_string_buffer(raw + b"\0\0")
        name = UnicodeString(len(raw), len(raw) + 2, ctypes.cast(buffer, ctypes.c_void_p))
        attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes),
            self.directories[path.parent],
            ctypes.pointer(name),
            0x40 | 0x1000,
            None,
            None,
        )
        handle, status = wintypes.HANDLE(), IoStatus()
        access = (
            0x100080 if metadata else 0x100081 if directory else 0x130196 if create else 0x120089
        )
        options = (
            0x00200000 | 0x20 | (0 if metadata else 1 if directory else 0x40) | (2 if create else 0)
        )
        result = self.nt.NtCreateFile(
            ctypes.byref(handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(status),
            None,
            0x80,
            0 if create else 7,
            2 if create else 1,
            options,
            None,
            0,
        )
        if result < 0:
            raise self.native.WinError(self.nt.RtlNtStatusToDosError(result))
        if handle.value is None:
            raise OSError("原生打开未返回文件句柄")
        return handle.value

    def enter(self) -> None:
        try:
            current = Path(self.root.anchor)
            self._directory(current)
            for part in self.root.parts[1:]:
                current /= part
                self._directory(current)
        except BaseException:
            self.close()
            raise

    def _directory(self, path: Path) -> int:
        if path not in self.directories:
            handle = self._open(path, directory=True)
            self.directories[path] = handle
            value = FileIdInfo()
            if not self.library.GetFileInformationByHandleEx(
                handle, 18, ctypes.byref(value), ctypes.sizeof(value)
            ):
                raise self.native.WinError(self.native.get_last_error())
            self.observed[path] = (value.volume, int.from_bytes(bytes(value.identifier), "little"))
        return self.directories[path]

    def path(self, relative: str) -> Path:
        value = Path(relative)
        if (
            not relative
            or value.anchor
            or ".." in value.parts
            or any(":" in p or "\x00" in p for p in value.parts)
        ):
            raise ValueError("文件路径必须位于已打开目录内，不能引用额外数据流")
        current = self.root
        for part in value.parts[:-1]:
            current /= part
            self._directory(current)
        return self.root / value

    def stat(self, relative: str) -> os.stat_result:
        from importlib import import_module

        runtime: Any = import_module("msvcrt")
        handle = self._relative(self.path(relative), metadata=True)
        try:
            descriptor = runtime.open_osfhandle(handle, os.O_RDONLY | 0x8000)
        except BaseException:
            self.library.CloseHandle(handle)
            raise
        try:
            value = os.fstat(descriptor)
            tag = self._tag(handle)
            extra = {
                k: getattr(value, k)
                for k in (
                    "st_atime",
                    "st_mtime",
                    "st_ctime",
                    "st_atime_ns",
                    "st_mtime_ns",
                    "st_ctime_ns",
                    "st_birthtime",
                    "st_birthtime_ns",
                )
                if hasattr(value, k)
            }
            extra.update(st_file_attributes=tag.attributes, st_reparse_tag=tag.tag)
            sequence = list(value)
            if tag.tag == 0xA000000C:
                sequence[0] = stat.S_IFLNK | 0o777
            return os.stat_result(sequence, extra)
        finally:
            os.close(descriptor)

    def open(self, relative: str) -> int:
        from importlib import import_module

        runtime: Any = import_module("msvcrt")
        handle = self._open(self.path(relative), directory=False)
        try:
            return runtime.open_osfhandle(handle, os.O_RDONLY | 0x8000)
        except BaseException:
            self.library.CloseHandle(handle)
            raise

    def link(self, relative: str) -> bytes:
        handle = self._relative(self.path(relative), metadata=True)
        try:
            output = ctypes.create_string_buffer(16 * 1024)
            received = wintypes.DWORD()
            if not self.library.DeviceIoControl(
                handle, 0x900A8, None, 0, output, len(output), ctypes.byref(received), None
            ):
                raise self.native.WinError(self.native.get_last_error())
            raw = output.raw[: received.value]
            if len(raw) < 20 or struct.unpack_from("<I", raw)[0] != 0xA000000C:
                raise ValueError("只保存原生符号链接目标元数据")
            offset, length = struct.unpack_from("<HH", raw, 8)
            if offset % 2 or length % 2 or 20 + offset + length > len(raw):
                raise ValueError("符号链接元数据范围无效")
            target = raw[20 + offset : 20 + offset + length].decode(
                "utf-16-le", errors="surrogatepass"
            )
            if target.startswith("\\??\\"):
                target = "\\\\?\\" + target[4:]
            return os.fsencode(target)
        finally:
            self.library.CloseHandle(handle)

    def change_time(self, descriptor: int) -> int:
        from importlib import import_module

        runtime: Any = import_module("msvcrt")
        return self._change_time(runtime.get_osfhandle(descriptor))

    def _change_time(self, handle: int) -> int:
        value = BasicInfo()
        if not self.library.GetFileInformationByHandleEx(
            handle, 0, ctypes.byref(value), ctypes.sizeof(value)
        ):
            raise self.native.WinError(self.native.get_last_error())
        return value.changed

    def changed(self, relative: str) -> int:
        # 只读末级元数据；链接取链接本身的 ChangeTime，不读取目标正文。
        handle = self._relative(self.path(relative), metadata=True)
        try:
            if self.library.GetFileType(handle) != 1:
                raise ValueError("元数据来源不是磁盘文件")
            return self._change_time(handle)
        finally:
            self.library.CloseHandle(handle)

    def verify(self) -> None:
        for path, handle in self.directories.items():
            value = path.lstat()
            if (
                self._attributes(handle) & 0x400
                or getattr(value, "st_file_attributes", 0) & 0x400
                or not stat.S_ISDIR(value.st_mode)
                or (value.st_dev, value.st_ino) != self.observed[path]
            ):
                raise ValueError("目录在读取期间变化")

    def rename(self, handle: int, name: str) -> None:
        # 在仍独占打开的文件句柄上发布；并发目标存在时不能覆盖。
        raw = name.encode("utf-16-le", errors="surrogatepass")
        buffer = ctypes.create_string_buffer(
            max(ctypes.sizeof(RenameInfo), RenameInfo.name.offset + len(raw) + 2)
        )
        value = ctypes.cast(buffer, ctypes.POINTER(RenameInfo)).contents
        value.replace = False
        value.root = self.directories[self.root]
        value.length = len(raw)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, raw, len(raw))
        status = IoStatus()
        result = self.nt.NtSetInformationFile(handle, ctypes.byref(status), buffer, len(buffer), 10)
        if result < 0:
            raise self.native.WinError(self.nt.RtlNtStatusToDosError(result))

    def publish(self, temporary: str, name: str, data: bytes) -> int:
        handle = self._open(self.path(temporary), directory=False, create=True)
        published = False
        try:
            for offset in range(0, len(data), 1024 * 1024):
                block = data[offset : offset + 1024 * 1024]
                written = wintypes.DWORD()
                if not self.library.WriteFile(
                    handle, block, len(block), ctypes.byref(written), None
                ):
                    raise self.native.WinError(self.native.get_last_error())
                if written.value != len(block):
                    raise OSError("导出仅写入部分字节")
            if not self.library.FlushFileBuffers(handle):
                raise self.native.WinError(self.native.get_last_error())
            mode = self.path(temporary).lstat().st_mode
            self.verify()
            self.rename(handle, name)
            self.verify()
            published = True
            return mode
        finally:
            try:
                if not published:
                    self._delete(handle)
            finally:
                self.library.CloseHandle(handle)

    def _delete(self, handle: int) -> None:
        deleted = ctypes.c_bool(True)
        if not self.library.SetFileInformationByHandle(
            handle, 4, ctypes.byref(deleted), ctypes.sizeof(deleted)
        ):
            raise self.native.WinError(self.native.get_last_error())

    def close(self) -> None:
        for handle in reversed(list(self.directories.values())):
            self.library.CloseHandle(handle)
        self.directories.clear()
        self.observed.clear()
