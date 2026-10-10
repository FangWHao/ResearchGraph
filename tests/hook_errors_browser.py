"""钩子报告界面的独立合成服务；仅修改本进程的临时日志。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from rg.api.server import Handler, LocalServer
from rg.ingest.hook_errors import collect
from rg.store.database import Store


def seed(root: Path) -> tuple[str, str]:
    store = Store(root)
    try:
        first = store.project("验收钩子报告项目", [])
        second = store.project("验收钩子报告项目二", [])
        logs = root / "logs"
        logs.mkdir(exist_ok=True)
        path = logs / "hook-errors.log"
        records = [
            f"2026-10-10T00:00:{index:02}+00:00 hook "
            + ("JSONDecodeError" if index == 1 else "RuntimeError")
            + "\n"
            for index in range(25)
        ]
        records += [
            "2026-10-10T00:01:00+00:00 hook SyntheticPrivateError\n",
            "合成私密错误正文 <script>window.hookInjected=true</script>\n",
        ]
        path.write_text("".join(records), encoding="utf-8")
        collect(store)
        path.rename(logs / "synthetic-rotated.log")
        path.write_text(
            records[0]
            + "2026-10-10T00:02:00+00:00 hook PermissionError\n"
            + "2026-10-10T00:03:00+00:00 hook TimeoutError",
            encoding="utf-8",
        )
        collect(store)
        return first, second
    finally:
        store.close()


class FixtureHandler(Handler):
    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/synthetic-hook-fixture/append":
            super().do_POST()
            return
        self._unread_body = True
        if not self._authorized():
            return
        if self.headers.get("Content-Length") != "2":
            self._json(400, {"error": "仅允许合成控制器空对象"})
            return
        body = self.rfile.read(2)
        self._unread_body = False
        if body != b"{}":
            self._json(400, {"error": "合成控制器请求无效"})
            return
        store = Store(self.server.root)
        try:
            with (self.server.root / "logs/hook-errors.log").open("ab") as stream:
                stream.write(b"\n2026-10-10T00:04:00+00:00 hook ValueError\n")
            result = collect(store)
        finally:
            store.close()
        self._json(200, result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9797)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchgraph-hook-errors-") as directory:
        root = Path(directory) / "store"
        first, second = seed(root)
        server = LocalServer(root, args.web_dir, args.port, token="synthetic-hook-errors-token")
        server.RequestHandlerClass = FixtureHandler
        print(json.dumps({"project": first, "second": second}, ensure_ascii=False), flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
