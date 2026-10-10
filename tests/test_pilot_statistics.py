import json
from pathlib import Path

from scripts.measure_atlas import measure


def test_atlas_statistics_do_not_mix_prefix_collision_projects(tmp_path: Path):
    for name, cwd in [
        ("atlas", "/mnt/d/Documents/Atlas/analysis"),
        ("other", "/mnt/d/Documents/AtlasOther"),
    ]:
        (tmp_path / f"{name}.jsonl").write_text(
            json.dumps({"type": "session_meta", "payload": {"id": name, "cwd": cwd}}) + "\n"
        )
    result = measure([("codex", tmp_path)])
    assert result["sessions_files"] == 1
    assert result["records"] == 1
    assert "source_path" not in result
