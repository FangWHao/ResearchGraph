from __future__ import annotations

import pytest

from rg.api.static import web_directory


def test_default_assets_ignore_unrelated_working_directory(tmp_path, monkeypatch):
    import rg.api.static as module

    package = tmp_path / "public-package/rg"
    checkout = package.parent / "web/dist"
    checkout.mkdir(parents=True)
    (checkout / "index.html").write_text("source", encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(package / "api/static.py"))
    unrelated = tmp_path / "unrelated/web/dist"
    unrelated.mkdir(parents=True)
    (unrelated / "index.html").write_text("unrelated", encoding="utf-8")
    monkeypatch.chdir(unrelated.parents[1])
    assert web_directory() == checkout
    installed = package / "_web"
    installed.mkdir(parents=True)
    (installed / "index.html").write_text("installed", encoding="utf-8")
    assert web_directory() == installed
    assert web_directory(unrelated) == unrelated


def test_missing_desktop_assets_do_not_start_workers_or_create_data_directory(
    tmp_path, monkeypatch
):
    import rg.api.static as module
    from rg.cli.main import _start, parser

    monkeypatch.setattr(module, "__file__", str(tmp_path / "package/rg/api/static.py"))
    root = tmp_path / "not-created-data"
    args = parser().parse_args(["--data-dir", str(root), "start"])
    with pytest.raises(ValueError, match="桌面完整wheel"):
        _start(args)
    assert not root.exists()
    with pytest.raises(ValueError, match="index.html"):
        web_directory(tmp_path / "missing-explicit")
