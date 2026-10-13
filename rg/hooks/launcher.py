"""固定解释器的离线钩子入口；导入或运行失败仍静默退出 0。"""

from __future__ import annotations

if __name__ == "__main__":
    try:
        from rg.hooks.entry import main

        main()
    except BaseException:
        pass
    finally:
        raise SystemExit(0)
