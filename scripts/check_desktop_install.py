"""仓库外安装验收：加载完整界面、创建项目、管理实际后台进程。"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import time
import urllib.request
from contextlib import chdir, closing
from pathlib import Path


def main() -> None:
    import rg
    from rg.api.server import LocalServer
    from rg.api.static import web_directory
    from rg.runtime import Runtime, status
    from rg.store.database import Store

    checkout = Path(__file__).resolve().parents[1]
    assert rg.__file__ and not Path(rg.__file__).resolve().is_relative_to(checkout / "rg")
    with tempfile.TemporaryDirectory(prefix="rg-desktop-check-") as temporary, chdir(temporary):
        directory = Path(temporary)
        data = directory / "公开数据 空格"
        work = directory / "公开工作区"
        work.mkdir()
        web = web_directory()
        assert web.name == "_web" and web.is_relative_to(Path(rg.__file__).parent)
        with closing(Store(data)):
            pass
        server = LocalServer(data, web, 0)
        thread = threading.Thread(target=server.serve_forever)
        with Runtime(data, {"interval": 0.2, "limit": 20, "extract": False}) as runtime:
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def request(path: str, body: dict | None = None):
                req = urllib.request.Request(
                    server.origin + path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={
                        "Authorization": f"Bearer {server.token}",
                        "Origin": server.origin,
                        "Content-Type": "application/json",
                    },
                )
                with opener.open(req, timeout=10) as response:
                    return response.read()

            try:
                html = request("/").decode()
                assets = sorted(set(re.findall(r'(?:src|href)="(/assets/[^"<>]+)"', html)))
                assert assets
                assets = sorted(
                    set(assets)
                    | {
                        "/" + path.relative_to(web).as_posix()
                        for path in (web / "assets").rglob("*")
                        if path.is_file()
                    }
                )
                assert all(request(asset) for asset in assets)
                body = {"name": "安装包合成项目", "root": str(work), "collect_local": False}
                created = json.loads(request("/api/projects", body))
                repeated = json.loads(request("/api/projects", body))
                assert created["created"] and not repeated["created"]
                assert created["project"]["project_id"] == repeated["project"]["project_id"]
                assert created["project"]["remote_model_allowed"] == 0
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if all(
                        status(data).get(role, {}).get("state") == "idle"
                        for role in ("scan", "files")
                    ):
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("安装包后台未在原八秒预算内完成")
                handles = list(runtime.processes.values())
                assert all(process.poll() is None for process in handles)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
        assert all(process.poll() == 0 for process in handles)
        with closing(Store(data, readonly=True)) as store:
            assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0
        result = {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "outside_checkout": True,
            "assets_served": len(assets),
            "project_replay": True,
            "remote_allowed": False,
            "runtime_children_exit": [process.returncode for process in handles],
            "model_calls": 0,
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
