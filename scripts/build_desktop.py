"""构建包含桌面界面的通用wheel；用户安装后无需Node或pnpm。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    pnpm, uv = shutil.which("pnpm"), shutil.which("uv")
    if not pnpm or not uv:
        raise ValueError("桌面构建需已安装uv、固定版本Node和pnpm；版本见README")
    for command in ([pnpm, "exec", "tsc", "--noEmit"], [pnpm, "build"]):
        subprocess.run(command, cwd=root / "web", check=True)
    environment = dict(os.environ, RG_PACKAGE_WEB="1")
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(args.output.absolute())],
        cwd=root,
        env=environment,
        check=True,
    )


if __name__ == "__main__":
    main()
