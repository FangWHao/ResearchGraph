from __future__ import annotations

import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path

import pytest

from rg.hooks.entry import _error
from rg.store.clear import _managed_path, preview, status
from rg.store.clear_denials import denied
from rg.store.database import Store
from rg.store.objects import ObjectStore, atomic_write, digest
from tests.directory_aliases import directory_alias


def test_packaged_schema_and_object_write_with_native_locale(tmp_path: Path):
    with closing(Store(tmp_path / "资料")) as store:
        project = store.project("公开合成项目", [])
        row = store.db.execute(
            "SELECT name FROM projects WHERE project_id=?", (project,)
        ).fetchone()
        assert row[0] == "公开合成项目"
        raw = "公开合成原件\n".encode()
        sha = store.objects.put(raw)
        assert store.objects.get(sha) == raw
        assert not list(store.root.rglob(".tmp-*"))


def test_failed_replacement_preserves_destination_and_removes_temp(tmp_path: Path):
    target = tmp_path / "existing-directory"
    target.mkdir()
    marker = target / "preserved.txt"
    marker.write_bytes(b"public synthetic")
    with pytest.raises(OSError):
        atomic_write(target, b"replacement")
    assert marker.read_bytes() == b"public synthetic"
    assert sorted(p.name for p in tmp_path.iterdir()) == [target.name]


@pytest.mark.parametrize("existing", [False, True])
def test_killed_writer_does_not_publish_partial_original(tmp_path: Path, existing: bool):
    target, ready = tmp_path / "original.bin", tmp_path / "ready"
    if existing:
        atomic_write(target, b"previous complete original")
    script = """
import sys
from pathlib import Path
from rg.store.objects import atomic_stream
class Paused:
    first = True
    def read(self, size):
        if self.first:
            self.first = False
            return b'incomplete public synthetic'
        Path(sys.argv[2]).write_bytes(b'ready')
        sys.stdin.buffer.read(1)
        return b''
atomic_stream(Path(sys.argv[1]), Paused())
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(target), str(ready)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.poll() is not None:
                pytest.fail(repr(process.communicate()[1]))
            assert time.monotonic() < deadline, "写入子进程未就绪"
            time.sleep(0.01)
        if existing:
            assert target.read_bytes() == b"previous complete original"
        else:
            assert not target.exists()
        process.kill()
        process.communicate(timeout=10)
        atomic_write(target, b"complete replacement")
        assert target.read_bytes() == b"complete replacement"
        # 强杀留下的临时文件不是可寻址原件，不自动删可能仍被其他写入使用的文件。
        objects = ObjectStore(tmp_path / "objects")
        sha = objects.put(b"complete retry")
        assert objects.get(sha) == b"complete retry"
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize("location", ["objects", "spool", "snapshots", "overviews"])
def test_clear_preview_rejects_managed_directory_aliases(tmp_path: Path, location: str):
    root, outside = tmp_path / "data", tmp_path / "outside"
    with closing(Store(root)) as store:
        project = store.project("公开合成清除项目", [])
    outside.mkdir()
    if location == "objects":
        (root / "objects").rmdir()
    (outside / "preserved.txt").write_bytes(b"public synthetic outside")
    with directory_alias(root / location, outside):
        with pytest.raises(ValueError, match="符号链接|重解析点"):
            preview(root, project)
        assert (outside / "preserved.txt").read_bytes() == b"public synthetic outside"


@pytest.mark.parametrize("operation", ["status", "denied"])
def test_clear_receipts_reject_directory_aliases(tmp_path: Path, operation: str):
    root, outside = tmp_path / "data", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    request = str(uuid.uuid4())
    (outside / (request + ".json")).write_bytes(b'{"state":"complete","denials":{}}')
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    with directory_alias(root / "clear-records", outside):
        with pytest.raises(ValueError, match="符号链接|重解析点"):
            status(root, request) if operation == "status" else denied(root, project="synthetic")
        assert {p.name: p.read_bytes() for p in outside.iterdir()} == before


def test_clear_resume_rejects_alias_in_object_parent(tmp_path: Path):
    root, outside = tmp_path / "data", tmp_path / "outside"
    (root / "objects").mkdir(parents=True)
    outside.mkdir()
    sha = digest(b"public synthetic")
    marker = outside / (sha + ".zst")
    marker.write_bytes(b"outside original")
    with directory_alias(root / "objects" / sha[:2], outside):
        with pytest.raises(ValueError, match="符号链接|重解析点"):
            _managed_path(root, "objects/" + sha[:2] + "/" + sha + ".zst")
        assert marker.read_bytes() == b"outside original"


def test_error_log_does_not_follow_managed_directory_alias(tmp_path: Path):
    root, outside = tmp_path / "data", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    with directory_alias(root / "logs", outside):
        _error(root, "hook", ValueError("private synthetic input"))
        assert list(outside.iterdir()) == []
