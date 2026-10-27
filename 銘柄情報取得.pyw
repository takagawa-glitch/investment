# -*- coding: utf-8 -*-
"""銘柄情報取得ツール 起動用。ダブルクリックで起動(.pyw なので黒い画面は出ない)。

起動中にエラーが起きたときは、何も出ずに終わらないよう理由を画面に表示する
(meigara/startup_error.py)。外部ライブラリは使わない(標準ライブラリだけ)。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _run():
    try:
        from meigara.app import main
        main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as e:
        try:
            from meigara.startup_error import show_startup_error
            show_startup_error(e)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None, f"銘柄情報取得を起動できませんでした。\n\n{type(e).__name__}: {e}",
                    "銘柄情報取得 — 起動できません", 0x10 | 0x40000)
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    _run()
