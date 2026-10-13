"""默认界面来自安装包或同一源码树，不依赖启动时的工作目录。"""

from __future__ import annotations

from pathlib import Path


def web_directory(explicit: Path | None = None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
    else:
        package = Path(__file__).resolve().parents[1]
        candidates = [package / "_web", package.parent / "web/dist"]
        candidate = next((path for path in candidates if (path / "index.html").is_file()), None)
        if candidate is None:
            raise ValueError("安装包没有界面；请安装桌面完整wheel，或在源码web目录运行pnpm build")
    if not (candidate / "index.html").is_file():
        raise ValueError("指定界面目录缺少index.html")
    return candidate
