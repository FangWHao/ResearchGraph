from __future__ import annotations

import hashlib
import os
from contextlib import ExitStack

import pytest

from rg.artifacts import files as physical_files
from rg.artifacts.files import current_file
from rg.export import package
from rg.snapshot.capture import capture
from rg.snapshot.files import WorkspaceFiles
from tests.directory_aliases import directory_alias
from tests.golden.test_exports import options
from tests.golden.test_snapshots_hooks import git, project, shadow


def test_snapshot_preserves_unicode_crlf_ignored_large_file_and_real_index(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    raw = "公开合成，保留原字节\r\n".encode()
    (work / "source.txt").write_bytes(b"staged\r\n")
    git(work, "add", "source.txt")
    (work / "source.txt").write_bytes(raw)
    (work / "中文.txt").write_bytes(raw)
    (work / ".rgignore").write_bytes(b"ignored.txt\n")
    (work / "ignored.txt").write_bytes(b"must remain outside snapshot")
    (work / "large.bin").write_bytes(b"L" * 5_000_001)
    before = (work / ".git" / "index").read_bytes()
    result = capture(store.root, identity, root, work)
    assert result["skipped"] is None and result["duration_ms"] <= 2000
    assert (work / ".git" / "index").read_bytes() == before
    commit = result["shadow_commit"]
    assert shadow(store, identity, "show", commit + ":source.txt") == raw
    assert shadow(store, identity, "show", commit + ":中文.txt") == raw
    paths = shadow(store, identity, "ls-tree", "-r", "--name-only", commit)
    assert b"ignored.txt" not in paths and b"large.bin" not in paths
    assert any(
        x["path"] == "large.bin" and x["reason"] == "large_file" for x in result["omitted_files"]
    )


def test_complete_digest_reads_registered_file_without_copying_body(store, tmp_path):
    work = tmp_path / "公开工作区"
    work.mkdir()
    raw = b"A" * 2_100_000 + b"complete-tail"
    (work / "完整.bin").write_bytes(raw)
    measured = current_file(store, work, "完整.bin", "registered-public-root")
    assert measured["sha256"] == hashlib.sha256(raw).hexdigest()
    assert measured["size"] == len(raw) and measured["content_sha256"] is None
    assert not list(store.root.rglob(measured["sha256"]))


def test_zip_publish_verifies_all_members_and_does_not_modify_database(store, tmp_path):
    identity = store.project("公开合成导出", [])
    before = "\n".join(store.db.iterdump())
    target = tmp_path / "公开导出.zip"
    result = package.write(store, options(identity), target)
    verified = package.verify(target)
    assert verified["verified_files"] == 10
    assert result["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert "\n".join(store.db.iterdump()) == before
    with pytest.raises(ValueError, match="已存在"):
        package.write(store, options(identity), target)
    assert not list(tmp_path.glob(".rg-export-*"))


def test_concurrent_zip_target_is_never_overwritten_and_temp_is_removed(
    store, tmp_path, monkeypatch
):
    identity = store.project("公开并发导出", [])
    target = tmp_path / "race.zip"
    original = package.archive

    def race(*args, **kwargs):
        data = original(*args, **kwargs)
        target.write_bytes(b"other owner")
        return data

    monkeypatch.setattr(package, "archive", race)
    with pytest.raises(FileExistsError):
        package.write(store, options(identity), target)
    assert target.read_bytes() == b"other owner"
    assert not list(tmp_path.glob(".rg-export-*"))


@pytest.mark.parametrize("operation", ["workspace", "hash", "export"])
def test_directory_alias_is_not_followed(operation, store, tmp_path):
    outer = tmp_path / "external"
    outer.mkdir()
    (outer / "private.bin").write_bytes(b"never read these external bytes")
    alias = tmp_path / "alias"
    with directory_alias(alias, outer):
        with pytest.raises((OSError, ValueError)):
            if operation == "workspace":
                with WorkspaceFiles(alias) as files:
                    files.open("private.bin")
            elif operation == "hash":
                current_file(store, alias, "private.bin", "public-root")
            else:
                identity = store.project("公开拒绝别名", [])
                package.write(store, options(identity), alias / "escape.zip")
    assert not (outer / "escape.zip").exists()


def test_opened_parent_cannot_redirect_read_and_handles_are_released(tmp_path):
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "value.txt").write_bytes(b"original bytes")
    external = tmp_path / "external"
    external.mkdir()
    (external / "value.txt").write_bytes(b"external bytes must not be read")
    previous = root / "previous"
    with WorkspaceFiles(root) as files:
        files.stat("nested/value.txt")
        nested.rename(previous)
        with directory_alias(nested, external):
            with os.fdopen(files.open("nested/value.txt"), "rb") as stream:
                assert stream.read() == b"original bytes"
            with pytest.raises((OSError, ValueError)):
                files.verify()
        previous.rename(nested)
    nested.rename(previous)
    assert (previous / "value.txt").read_bytes() == b"original bytes"


def test_failed_archive_leaves_no_target_or_temporary_file(store, tmp_path, monkeypatch):
    identity = store.project("公开导出失败", [])
    target = tmp_path / "failed.zip"

    def failed(*args, **kwargs):
        raise ValueError("public synthetic build failure")

    monkeypatch.setattr(package, "archive", failed)
    with pytest.raises(ValueError, match="public synthetic"):
        package.write(store, options(identity), target)
    assert not target.exists() and not list(tmp_path.glob(".rg-export-*"))


@pytest.mark.parametrize("when", ["before_publish", "after_publish"])
def test_moved_export_parent_discards_owned_output_without_touching_external_files(
    when, store, tmp_path, monkeypatch
):
    identity = store.project("公开目录变化导出", [])
    parent, previous, external = (tmp_path / x for x in ("output", "previous", "external"))
    parent.mkdir()
    external.mkdir()
    marker = external / "preserved.txt"
    marker.write_bytes(b"external preserved")
    target = parent / "result.zip"
    with ExitStack() as stack:

        def redirect():
            parent.rename(previous)
            stack.enter_context(directory_alias(parent, external))

        if when == "before_publish":
            original = package.archive

            def changed(*args, **kwargs):
                result = original(*args, **kwargs)
                redirect()
                return result

            monkeypatch.setattr(package, "archive", changed)
        elif os.name == "nt":
            from rg.store.windows_files import NativeFiles

            original = NativeFiles.rename

            def changed(*args, **kwargs):
                original(*args, **kwargs)
                redirect()

            monkeypatch.setattr(NativeFiles, "rename", changed)
        else:
            original = os.link

            def changed(*args, **kwargs):
                original(*args, **kwargs)
                redirect()

            monkeypatch.setattr(os, "link", changed)
        with pytest.raises((OSError, ValueError)):
            package.write(store, options(identity), target)
    assert marker.read_bytes() == b"external preserved"
    assert sorted(p.name for p in external.iterdir()) == ["preserved.txt"]
    # Windows 不允许移动仍含独占打开文件的目录；阻止替换也必须清掉本次输出。
    assert not list((previous if previous.exists() else parent).iterdir())


def test_changed_hash_parent_is_rejected_after_read_without_following_new_alias(
    store, tmp_path, monkeypatch
):
    root, nested, previous, external = (
        tmp_path / x for x in ("root", "root/nested", "root/previous", "external")
    )
    nested.mkdir(parents=True)
    external.mkdir()
    (nested / "data.bin").write_bytes(b"original public bytes")
    (external / "data.bin").write_bytes(b"external bytes")
    before = "\n".join(store.db.iterdump())
    original = physical_files.full_digest
    with ExitStack() as stack:

        def changed(stream):
            result = original(stream)
            nested.rename(previous)
            stack.enter_context(directory_alias(nested, external))
            return result

        monkeypatch.setattr(physical_files, "full_digest", changed)
        with pytest.raises((physical_files.ChangedDuringRead, OSError)):
            current_file(store, root, "nested/data.bin", "public-root")
    assert (external / "data.bin").read_bytes() == b"external bytes"
    assert "\n".join(store.db.iterdump()) == before


def test_filename_colon_does_not_open_windows_alternate_data_stream(store, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    if os.name == "nt":
        (root / "file.txt").write_bytes(b"public file")
        with pytest.raises(ValueError, match="数据流"):
            current_file(store, root, "file.txt:stream", "public-root")
    else:
        raw = b"ordinary POSIX filename"
        (root / "file.txt:stream").write_bytes(raw)
        assert (
            current_file(store, root, "file.txt:stream", "public-root")["sha256"]
            == hashlib.sha256(raw).hexdigest()
        )


def test_post_creation_publish_failure_removes_owned_temp(store, tmp_path, monkeypatch):
    identity = store.project("公开发布异常", [])
    if os.name == "nt":
        from rg.store.windows_files import NativeFiles

        original = NativeFiles._attributes

        def failed(self, handle):
            result = original(self, handle)
            if not result & 0x10:
                raise ValueError("public synthetic attribute failure")
            return result

        monkeypatch.setattr(NativeFiles, "_attributes", failed)
    else:

        def failed(*args, **kwargs):
            raise ValueError("public synthetic publish failure")

        monkeypatch.setattr(os, "link", failed)
    with pytest.raises(ValueError, match="public synthetic"):
        package.write(store, options(identity), tmp_path / "failed.zip")
    assert not (tmp_path / "failed.zip").exists() and not list(tmp_path.glob(".rg-export-*"))


def test_publish_cleanup_preserves_another_owners_replacement(store, tmp_path, monkeypatch):
    identity = store.project("公开并发归属清理", [])
    parent, previous, external = (tmp_path / x for x in ("output", "previous", "external"))
    parent.mkdir()
    external.mkdir()
    target = parent / "result.zip"
    with ExitStack() as stack:
        if os.name == "nt":
            original = package.archive

            def replaced(*args, **kwargs):
                result = original(*args, **kwargs)
                target.write_bytes(b"another owner")
                return result

            monkeypatch.setattr(package, "archive", replaced)
        else:
            original = os.link

            def replaced(*args, **kwargs):
                original(*args, **kwargs)
                target.unlink()
                target.write_bytes(b"another owner")
                parent.rename(previous)
                stack.enter_context(directory_alias(parent, external))

            monkeypatch.setattr(os, "link", replaced)
        with pytest.raises((OSError, ValueError)):
            package.write(store, options(identity), target)
    owner = previous if previous.exists() else parent
    assert (owner / "result.zip").read_bytes() == b"another owner"
    assert not list(owner.glob(".rg-export-*")) and not list(external.iterdir())


def test_opened_empty_directory_changed_to_reparse_point_cannot_supply_external_bytes(tmp_path):
    root = tmp_path / "root"
    empty = root / "empty"
    empty.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (external / "private.bin").write_bytes(b"external bytes must not be read")
    with WorkspaceFiles(root) as files:
        with pytest.raises(FileNotFoundError):
            files.stat("empty/missing.bin")
        if os.name == "nt":
            import ctypes
            import struct
            from ctypes import wintypes

            native = files.native
            assert native is not None
            library = native.library
            handle = library.CreateFileW(
                str(empty), 0x40000000, 7, None, 3, 0x02000000 | 0x00200000, None
            )
            assert handle != ctypes.c_void_p(-1).value
            substitute = ("\\??\\" + str(external)).encode("utf-16-le")
            printed = str(external).encode("utf-16-le")
            paths = substitute + b"\0\0" + printed + b"\0\0"
            data = (
                struct.pack(
                    "<IHHHHHH",
                    0xA0000003,
                    8 + len(paths),
                    0,
                    0,
                    len(substitute),
                    len(substitute) + 2,
                    len(printed),
                )
                + paths
            )
            changed = wintypes.DWORD()
            try:
                assert library.DeviceIoControl(
                    handle, 0x900A4, data, len(data), None, 0, ctypes.byref(changed), None
                )
            finally:
                library.CloseHandle(handle)
            try:
                with pytest.raises((OSError, ValueError)):
                    files.open("empty/private.bin")
                with pytest.raises((OSError, ValueError)):
                    files.verify()
            finally:
                empty.rmdir()
                empty.mkdir()
        else:
            previous = root / "previous"
            empty.rename(previous)
            with directory_alias(empty, external):
                with pytest.raises((OSError, ValueError)):
                    files.open("empty/private.bin")
                with pytest.raises((OSError, ValueError)):
                    files.verify()
            previous.rename(empty)


def test_root_replaced_during_open_is_compared_with_actual_directory_handle(tmp_path, monkeypatch):
    root, old = tmp_path / "root", tmp_path / "old-root"
    root.mkdir()
    (root / "value.txt").write_bytes(b"original public bytes")
    changed = False

    def replace():
        nonlocal changed
        root.rename(old)
        root.mkdir()
        (root / "value.txt").write_bytes(b"replacement public bytes")
        changed = True

    if os.name == "nt":
        from rg.store.windows_files import NativeFiles

        original = NativeFiles._open

        def opened(self, path, **kwargs):
            result = original(self, path, **kwargs)
            if path == root and kwargs.get("directory") and not changed:
                replace()
            return result

        monkeypatch.setattr(NativeFiles, "_open", opened)
    else:
        original = os.open

        def opened(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == root and not changed:
                replace()
            return result

        monkeypatch.setattr(os, "open", opened)
    with WorkspaceFiles(root) as files:
        with os.fdopen(files.open("value.txt"), "rb") as stream:
            assert stream.read() == b"original public bytes"
        with pytest.raises((OSError, ValueError)):
            files.verify()
    assert (root / "value.txt").read_bytes() == b"replacement public bytes"
