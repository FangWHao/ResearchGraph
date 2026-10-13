from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def directory_alias(link: Path, target: Path) -> Iterator[None]:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, repr(result.stderr)
        assert link.is_junction() and not link.is_symlink()
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        yield
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
