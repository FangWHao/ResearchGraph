"""已存Codex头自动补记：来源丢失、旧库积压与中断不能误判父身份。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import zstandard

from rg.api.server import LocalServer
from rg.ingest import parents
from rg.ingest.scanner import scan_file
from rg.ingest.watch import cycle
from rg.store import clear, migrations
from rg.store.backup import backup
from rg.store.database import Store
from rg.store.locking import exclusive
from rg.store.objects import digest
from tests.golden.test_ingestion import lines
from tests.golden.test_session_parent import A, B, C, add, header, projected, view


def context():
    return {"type": "turn_context", "payload": {"cwd": "/synthetic"}}


def legacy(tmp_path, monkeypatch, rows, version=20, name="old"):
    root = tmp_path / "data"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", version)
        with closing(Store(root)) as old:
            path, pk = add(old, tmp_path, name, rows)
            before = [
                tuple(r) for r in old.db.execute("SELECT * FROM raw_events ORDER BY event_id")
            ]
    return root, path, pk, before


def offsets(store):
    return [
        tuple(r)
        for r in store.db.execute(
            "SELECT parent_observed_offset,committed_offset FROM source_files "
            "ORDER BY file_instance_id"
        )
    ]


@pytest.mark.parametrize("version", [18, 20, 21, 22])
def test_source_loss_after_old_upgrade_restores_without_changing_raw(
    tmp_path, monkeypatch, version
):
    root, path, pk, before = legacy(tmp_path, monkeypatch, [header(parent_thread_id=B)], version)
    path.unlink()
    with closing(Store(root)) as store:
        assert view(store, pk)["state"] == "metadata_incomplete"
        assert not view(store, pk)["source_metadata_complete"]
        result = cycle(store)
        assert result["parent_backfilled"] == 1 and result["deleted"] == 1
        assert view(store, pk)["state"] == "missing_parent"
        assert view(store, pk)["source_metadata_complete"]
        assert view(store, pk)["identity_metadata_complete"]
        assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")] == before
        assert store.raw(1) == (json.dumps(header(parent_thread_id=B)) + "\n").encode()
        assert cycle(store).get("parent_backfilled", 0) == 0
        assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


def test_saved_multibatch_headers_cannot_publish_early_parent_or_hide_late_conflict(
    tmp_path, monkeypatch
):
    root, path, pk, _ = legacy(
        tmp_path,
        monkeypatch,
        [header(parent_thread_id=B)]
        + [context() for _ in range(258)]
        + [header(parent_thread_id=C)],
    )
    path.unlink()
    with closing(Store(root)) as store:
        _, parent = add(store, tmp_path, "parent", [header(B)])
        assert parents.backfill_saved(store) == {"parent_backfilled": 256}
        result = view(store, pk)
        assert result["state"] == "metadata_incomplete" and result["observations_total"] == 1
        assert result["parent_session_pk"] is None and projected(store, pk) is None
        assert parents.backfill_saved(store) == {"parent_backfilled": 4}
        assert view(store, pk)["state"] == "conflicting" and projected(store, pk) is None
        assert view(store, parent)["state"] == "no_parent_declared"


def test_live_scan_does_not_recheck_same_batch_in_cycle(tmp_path, monkeypatch):
    root, _, pk, _ = legacy(tmp_path, monkeypatch, [header()] + [context() for _ in range(519)])
    with closing(Store(root)) as store:
        first = cycle(store)
        assert first["files"] == 1 and first.get("parent_backfilled", 0) == 0
        assert 0 < offsets(store)[0][0] < offsets(store)[0][1]
        assert view(store, pk)["state"] == "metadata_incomplete"
        cycle(store)
        assert view(store, pk)["state"] == "metadata_incomplete"
        cycle(store)
        assert view(store, pk)["state"] == "no_parent_declared"


def test_new_tail_is_observed_but_cannot_jump_old_gap(tmp_path, monkeypatch):
    root, path, pk, _ = legacy(
        tmp_path, monkeypatch, [header(parent_thread_id=B)] + [context()] * 519
    )
    with closing(Store(root)) as store:
        with path.open("a") as stream:
            stream.write(json.dumps(header(parent_thread_id=C)) + "\n")
        scan_file(store, path, "codex")
        assert offsets(store)[0][0] < offsets(store)[0][1]
        assert view(store, pk)["observations_total"] == 2
        assert view(store, pk)["state"] == "conflicting"
        assert not view(store, pk)["source_metadata_complete"]
        path.unlink()
        assert cycle(store)["parent_backfilled"] == 256
        assert not view(store, pk)["source_metadata_complete"]
        assert cycle(store)["parent_backfilled"] == 9
        assert view(store, pk)["source_metadata_complete"]
        assert offsets(store)[0][0] == offsets(store)[0][1]


def test_late_duplicate_identity_in_other_source_prevents_unique_parent(tmp_path, monkeypatch):
    (tmp_path / "subagents").mkdir()
    root, path, copy, _ = legacy(
        tmp_path, monkeypatch, [context()] * 259 + [header(B)], name="subagents/" + B
    )
    path.unlink()
    with closing(Store(root)) as store:
        add(store, tmp_path, "parent", [header(B)])
        _, child = add(store, tmp_path, "child", [header(parent_thread_id=B)])
        assert view(store, child)["source_metadata_complete"]
        assert view(store, child)["state"] == "metadata_incomplete"
        parents.backfill_saved(store)
        assert view(store, child)["state"] == "metadata_incomplete"
        parents.backfill_saved(store)
        assert view(store, copy)["state"] == "no_parent_declared"
        assert view(store, child)["state"] == "ambiguous_parent"
        assert projected(store, child) is None


def test_source_lock_defers_only_owned_file_and_other_source_recovers(tmp_path, monkeypatch):
    root, path, pk, _ = legacy(tmp_path, monkeypatch, [header(parent_thread_id=B)])
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 20)
        with closing(Store(root)) as old:
            second, other = add(old, tmp_path, "second", [header(C)])
    path.unlink()
    second.unlink()
    with closing(Store(root)) as store:
        key = digest(str(path).encode())
        with exclusive(store.root / "locks" / "sources" / (key + ".lock"), "合成占用"):
            assert parents.backfill_saved(store) == {"parent_busy": 1, "parent_backfilled": 1}
        assert view(store, pk)["state"] == "metadata_incomplete"
        assert view(store, other)["state"] == "no_parent_declared"
        assert parents.backfill_saved(store) == {"parent_backfilled": 1}
        assert view(store, pk)["state"] == "missing_parent"


@pytest.mark.parametrize("damage", ["missing", "compressed", "digest"])
@pytest.mark.parametrize("live", [True, False])
def test_bad_saved_object_does_not_abort_cycle_or_publish_unique_identity(
    tmp_path, monkeypatch, damage, live
):
    root, path, pk, _ = legacy(tmp_path, monkeypatch, [header(parent_thread_id=B)])
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 20)
        with closing(Store(root)) as old:
            second, other = add(old, tmp_path, "second", [header(C)])
            obj = old.objects.path(
                old.db.execute("SELECT object_sha256 FROM raw_events WHERE event_id=1").fetchone()[
                    0
                ]
            )
            if damage == "missing":
                obj.unlink()
            elif damage == "digest":
                obj.write_bytes(zstandard.ZstdCompressor().compress(b"synthetic-wrong-digest"))
            else:
                obj.write_bytes(b"synthetic-corrupt-object")
    if not live:
        path.unlink()
        second.unlink()
    with closing(Store(root)) as store:
        result = cycle(store)
        assert result["parent_errors"] == 1
        if live:
            assert result["errors"] == 1
        assert view(store, pk)["state"] == "metadata_incomplete"
        assert view(store, other)["state"] == "no_parent_declared"
        assert not view(store, other)["identity_metadata_complete"]


def test_midbatch_failure_rolls_back_all_observations_and_cursor(tmp_path, monkeypatch):
    root, path, pk, before = legacy(
        tmp_path, monkeypatch, [header(parent_thread_id=B), header(parent_thread_id=C)]
    )
    path.unlink()
    original = parents.observe

    def fail(db, session, event, record):
        original(db, session, event, record)
        if event == 2:
            raise RuntimeError("合成批内失败")

    with closing(Store(root)) as store:
        with monkeypatch.context() as patch:
            patch.setattr(parents, "observe", fail)
            assert parents.backfill_saved(store) == {"parent_errors": 1}
        assert view(store, pk)["observations_total"] == 0 and offsets(store)[0][0] == 0
        assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")] == before
        assert parents.backfill_saved(store) == {"parent_backfilled": 2}
        assert view(store, pk)["state"] == "conflicting"


def test_cursor_compare_failure_rolls_back_batch_instead_of_reporting_completion(
    tmp_path, monkeypatch
):
    root, path, pk, _ = legacy(tmp_path, monkeypatch, [header(parent_thread_id=B)])
    path.unlink()
    with closing(Store(root)) as store:
        with monkeypatch.context() as patch:
            patch.setattr(parents, "checkpoint", lambda *a: False)
            assert parents.backfill_saved(store) == {"parent_errors": 1}
        assert view(store, pk)["observations_total"] == 0 and offsets(store)[0][0] == 0
        assert parents.backfill_saved(store) == {"parent_backfilled": 1}
        assert view(store, pk)["state"] == "missing_parent"


def test_already_observed_headers_do_not_need_reloaded_raw_to_checkpoint(tmp_path, monkeypatch):
    root, path, pk, before = legacy(tmp_path, monkeypatch, [header()], 22)
    path.unlink()
    with closing(Store(root)) as store:
        observation = dict(store.db.execute("SELECT * FROM session_parent_observations").fetchone())
        with monkeypatch.context() as patch:
            patch.setattr(Store, "raw", lambda *a: pytest.fail("既有头观测不应重读"))
            assert parents.backfill_saved(store) == {"parent_backfilled": 1}
        assert view(store, pk)["state"] == "no_parent_declared"
        assert (
            dict(store.db.execute("SELECT * FROM session_parent_observations").fetchone())
            == observation
        )
        assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")] == before


def test_actual_process_exit_during_saved_batch_is_atomic(tmp_path, monkeypatch):
    root, path, pk, _ = legacy(tmp_path, monkeypatch, [header(parent_thread_id=B)])
    path.unlink()
    script = (
        "import os,sys\nfrom pathlib import Path\nfrom rg.store.database import Store\n"
        "from rg.ingest import parents as p\ns=Store(Path(sys.argv[1]))\n"
        "original=p.checkpoint\ndef fail(*args):\n original(*args)\n os._exit(79)\n"
        "p.checkpoint=fail\np.backfill_saved(s)\n"
    )
    output = subprocess.run(
        [sys.executable, "-c", script, str(root)], capture_output=True, text=True
    )
    assert output.returncode == 79
    with closing(Store(root)) as store:
        assert view(store, pk)["observations_total"] == 0 and offsets(store)[0][0] == 0
        assert cycle(store)["parent_backfilled"] == 1
        assert view(store, pk)["state"] == "missing_parent"


def test_schema23_upgrade_is_atomic_and_does_no_object_io(tmp_path, monkeypatch):
    root, _, _, _ = legacy(tmp_path, monkeypatch, [header()], 22)
    with monkeypatch.context() as patch:
        patch.setitem(
            migrations.MIGRATIONS, 23, migrations.MIGRATIONS[23] + ("SELECT missing_column",)
        )
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 22
        assert "parent_observed_offset" not in [
            r[1] for r in db.execute("PRAGMA table_info(source_files)")
        ]
    with monkeypatch.context() as patch:
        patch.setattr(Store, "raw", lambda *a: pytest.fail("升级不应读原文"))
        with closing(Store(root)) as store:
            assert (
                store.db.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
            )
            assert offsets(store)[0][0] == 0


def test_legacy_backup_restored_without_sources_can_recover_parent(tmp_path, monkeypatch):
    root, path, child, _ = legacy(tmp_path, monkeypatch, [header(parent_thread_id=B)])
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 20)
        with closing(Store(root)) as old:
            parent_path, parent = add(old, tmp_path, "parent", [header(B)])
            backup(old, tmp_path / "backup")
    path.unlink()
    parent_path.unlink()
    with closing(Store(tmp_path / "backup")) as restored:
        assert view(restored, child)["state"] == "metadata_incomplete"
        assert cycle(restored)["parent_backfilled"] == 2
        assert projected(restored, child) == parent and view(restored, child)["state"] == "linked"
        assert (
            json.loads(restored.raw(view(restored, child)["parent_event_id"]))["payload"]["id"] == B
        )


def test_current_source_rotation_does_not_skip_old_saved_instance(tmp_path, monkeypatch):
    root, path, child, _ = legacy(tmp_path, monkeypatch, [header(parent_thread_id=B)])
    lines(path, [header(parent_thread_id=C)])
    with closing(Store(root)) as store:
        result = cycle(store)
        assert result["parent_backfilled"] == 1
        assert len(offsets(store)) == 2 and all(start == end for start, end in offsets(store))
        assert view(store, child)["state"] == "conflicting"
        assert view(store, child)["observations_total"] == 2


def test_midfile_identity_mismatch_remains_invalid_after_cursor_completes(tmp_path, monkeypatch):
    root, path, pk, _ = legacy(tmp_path, monkeypatch, [context(), header(A)])
    path.unlink()
    with closing(Store(root)) as store:
        assert cycle(store)["parent_backfilled"] == 2
        assert view(store, pk)["state"] == "invalid"  # 文件名身份与晚到头不一致。
        assert view(store, pk)["source_metadata_complete"]


@pytest.mark.parametrize("raw", [b"broken\n", b"[]\n", b"null\n"])
def test_bad_json_and_nonobject_record_remain_raw_and_do_not_claim_root(tmp_path, monkeypatch, raw):
    root, path = tmp_path / "data", tmp_path / (A + ".jsonl")
    path.write_bytes(raw)
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 20)
        with closing(Store(root)) as old:
            scan_file(old, path, "codex")
    path.unlink()
    with closing(Store(root)) as store:
        assert cycle(store)["parent_backfilled"] == 1
        assert view(store, 1)["state"] == "unobserved"
        assert view(store, 1)["source_metadata_complete"] and store.raw(1) == raw


def test_cross_project_clear_removes_gap_without_copying_parent_or_deleting_retained_source(
    tmp_path, monkeypatch
):
    root, path, pk, _ = legacy(tmp_path, monkeypatch, [header(B)])
    path.unlink()
    with closing(Store(root)) as store:
        target, retained = store.project("合成清除", []), store.project("合成保留", [])
        with store.transaction() as db:
            db.execute("UPDATE sessions SET project_id=? WHERE session_pk=?", (target, pk))
        child_path, child = add(store, tmp_path, "child", [header(parent_thread_id=B)], retained)
        assert view(store, child)["state"] == "metadata_incomplete"
    preview = clear.preview(root, target)
    assert preview["blockers"] == []
    clear.execute(root, target, str(uuid4()), preview["preview_sha256"])
    with closing(Store(root)) as store:
        assert view(store, child)["state"] == "missing_parent"
        assert view(store, child)["identity_metadata_complete"] and child_path.is_file()
        assert view(store, child)["observations_total"] == 1


def test_real_cli_authenticated_http_and_readonly_snapshot_do_not_backfill(tmp_path, monkeypatch):
    root, path, pk, _ = legacy(tmp_path, monkeypatch, [header(parent_thread_id=B)])
    path.unlink()
    with closing(Store(root)) as store:
        expected, before = view(store, pk), list(store.db.iterdump())
        output = subprocess.run(
            [
                str(Path(sys.executable).with_name("rg")),
                "--data-dir",
                str(root),
                "session-parent",
                "--session",
                str(pk),
            ],
            capture_output=True,
            text=True,
        )
        assert output.returncode == 0 and json.loads(output.stdout) == expected
        (tmp_path / "index.html").write_text("合成界面")
        server = LocalServer(root, tmp_path, port=0, token="synthetic-backfill")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=server.origin, trust_env=False) as client:
                assert client.get("/api/session-parent", params={"session": pk}).status_code == 401
                client.headers["Authorization"] = "Bearer " + server.token
                response = client.get("/api/session-parent", params={"session": pk})
                assert response.status_code == 200 and response.json() == expected
                assert response.headers["Cache-Control"] == "no-store"
                assert client.get("/api/evidence/1").json()["session_parent"] == expected
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert not thread.is_alive() and list(store.db.iterdump()) == before
        with closing(Store(root, readonly=True)) as reader:
            assert view(reader, pk) == expected
        assert cycle(store)["parent_backfilled"] == 1


def test_concurrent_backfill_cannot_mix_metadata_flags_and_parent_refs(tmp_path, monkeypatch):
    root, path, pk, _ = legacy(tmp_path, monkeypatch, [header(parent_thread_id=B)])
    path.unlink()
    with closing(Store(root)) as store:
        add(store, tmp_path, "parent", [header(B)])
        expected = view(store, pk)
        import rg.query.session_parent as module

        original = module.resolve

        def concurrent(db):
            with closing(Store(root)) as writer:
                cycle(writer)
            return original(db)

        with closing(Store(root, readonly=True)) as reader:
            with monkeypatch.context() as patch:
                patch.setattr(module, "resolve", concurrent)
                assert view(reader, pk) == expected
            assert view(reader, pk)["state"] == "linked"
