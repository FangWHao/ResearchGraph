"""RG_PACKAGE_WEB=1 时把已构建的 web/dist 打进 wheel（rg/_web）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel" or os.environ.get("RG_PACKAGE_WEB") != "1":
            return
        dist = Path(self.root) / "web" / "dist"
        if not (dist / "index.html").is_file():
            raise ValueError("先在 web 目录运行 pnpm build")
        build_data["force_include"][str(dist)] = "rg/_web"
