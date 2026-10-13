"""桌面构建显式包含已编译界面；普通后端与editable安装保持轻量。"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel" or os.environ.get("RG_PACKAGE_WEB") != "1":
            return
        root = Path(self.root)
        dist = root / "web/dist"
        if not (dist / "index.html").is_file():
            raise ValueError("桌面构建前需在web目录运行pnpm build")
        allowed = {".html", ".js", ".css", ".svg", ".png", ".ico", ".woff", ".woff2", ".ttf"}
        for path in (dist, *dist.rglob("*")):
            value = path.lstat()
            if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
                raise ValueError("界面构建产物包含链接或重解析点")
            if stat.S_ISDIR(value.st_mode):
                continue
            if not stat.S_ISREG(value.st_mode) or path.suffix not in allowed:
                raise ValueError("界面构建产物包含非静态资源")
        build_data["force_include"][str(dist)] = "rg/_web"
