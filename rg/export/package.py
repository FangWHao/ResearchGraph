from __future__ import annotations

import io
import json
import os
import secrets
import stat
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

from rg.clients.record import unique_pairs
from rg.export.build import FORMAT, build
from rg.store.database import Store
from rg.store.objects import digest

MEMBERS = {
    "manifest.json",
    "claims.json",
    "graph.json",
    "state-events.json",
    "reviews.json",
    "evidence.json",
    "sources.json",
    "artifacts.json",
    "runs.json",
    "algorithms.json",
    "README.md",
}


def archive(store: Store, body: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    manifest, files = build(store, body)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in sorted(files.items()):
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100600 << 16
            package.writestr(member, data)
    return manifest, output.getvalue()


def verify(source: Path | BinaryIO) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(source) as package:
            names = package.namelist()
            if set(names) != MEMBERS or len(names) != len(MEMBERS):
                raise ValueError("导出成员缺失、重复或包含未声明的文件")
            for member in package.infolist():
                kind = stat.S_IFMT(member.external_attr >> 16)
                if kind not in {0, stat.S_IFREG} or member.flag_bits & 1:
                    raise ValueError("导出成员不能是链接、目录或加密内容")
            if package.getinfo("manifest.json").file_size > 65536:
                raise ValueError("导出清单过大")
            manifest = json.loads(package.read("manifest.json"), object_pairs_hook=unique_pairs)
            if (
                not isinstance(manifest, dict)
                or manifest.get("format") != FORMAT
                or not isinstance(manifest.get("files"), dict)
                or set(manifest["files"]) != MEMBERS - {"manifest.json"}
            ):
                raise ValueError("导出格式或成员清单无效")
            for name, expected in manifest["files"].items():
                if not isinstance(expected, dict) or set(expected) != {"bytes", "sha256"}:
                    raise ValueError("导出成员摘要无效")
                if type(expected["bytes"]) is not int or expected["bytes"] < 0:
                    raise ValueError("导出成员大小无效")
                if package.getinfo(name).file_size != expected["bytes"]:
                    raise ValueError("导出成员大小不符")
                import hashlib

                sha = hashlib.sha256()
                size = 0
                with package.open(name) as stream:
                    while block := stream.read(1024 * 1024):
                        sha.update(block)
                        size += len(block)
                if size != expected["bytes"] or sha.hexdigest() != expected["sha256"]:
                    raise ValueError("导出成员摘要不符")
            return {
                "format": FORMAT,
                "verified_files": len(manifest["files"]),
                "conditions": manifest.get("conditions"),
                "counts": manifest.get("counts"),
                "notice": "仅校验包内一致性，不证明研究结论或清单真实性",
            }
    except (zipfile.BadZipFile, RuntimeError, KeyError, UnicodeError) as error:
        raise ValueError("导出压缩包损坏或不支持") from error


def write(store: Store, body: dict[str, Any], output: Path) -> dict[str, Any]:
    output = Path(os.path.abspath(output))
    if output.is_relative_to(store.root.resolve()) or output.is_relative_to(store.root.absolute()):
        raise ValueError("导出文件不能位于证据库目录内")
    # 逐级 nofollow 打开已有父目录，避免祖先链接和检查后的替换。
    directory = os.open(output.anchor, os.O_RDONLY | os.O_DIRECTORY)
    temp = ".rg-export-" + secrets.token_hex(16)
    created = False
    try:
        for part in output.parent.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        try:
            os.stat(output.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("导出目标已存在，请使用新的文件名")
        manifest, data = archive(store, body)
        fd = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        created = True
        with os.fdopen(fd, "wb") as stream:
            mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # link 是独占发布；并发创建的目标不能被覆盖。
        os.link(
            temp, output.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False
        )
        os.unlink(temp, dir_fd=directory)
        created = False
        os.fsync(directory)
        return {
            "output": str(output),
            "sha256": digest(data),
            "bytes": len(data),
            "permissions": {
                "requested_mode": "0o600",
                "observed_mode": oct(mode),
                "posix_private": not bool(mode & 0o077),
                "notice": "权限由目标文件系统决定；Windows 挂载目录需按实际 ACL 核对",
            },
            "conditions": manifest["conditions"],
            "counts": manifest["counts"],
        }
    finally:
        if created:
            os.unlink(temp, dir_fd=directory)
        os.close(directory)
