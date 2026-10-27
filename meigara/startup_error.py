# -*- coding: utf-8 -*-
"""起動に失敗したとき・画面操作中にエラーが起きたとき、理由を画面に出し error.log に書く。

本体の部品が読めなくても使えるよう、標準ライブラリだけを使う。
Windows では tkinter を使わずに MessageBoxW で表示する(tkinter が原因の場合に備える)。

error.log の場所: アプリのフォルダの ログ\\error_<PC名>.log
書けないときは %LOCALAPPDATA%\\銘柄情報取得\\error.log
"""

import datetime
import os
import sys
import tempfile
import traceback
from pathlib import Path

APP_NAME = "銘柄情報取得"
APP_DIR = Path(__file__).resolve().parent.parent
LOG_MAX_BYTES = 1_000_000


def _pc_name():
    name = os.environ.get("COMPUTERNAME") or ""
    return "".join(c for c in name if c.isalnum() or c in "-_") or "不明"


def log_candidates():
    local = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / APP_NAME / "error.log"
    return [APP_DIR / "ログ" / f"error_{_pc_name()}.log", local]


def write_error_log(exc, title="エラー"):
    """エラーの詳細を書き、書いた場所を返す(書けなければ None。例外は投げない)。"""
    for p in log_candidates():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size > LOG_MAX_BYTES:
                os.replace(p, p.with_name(p.stem + ".old.log"))
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S} {title} ---\n")
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
            return p
        except Exception:
            continue
    return None


def explain(exc):
    name = type(exc).__name__
    if isinstance(exc, ImportError):
        mod = getattr(exc, "name", None) or "(不明)"
        return (f"プログラムの部品「{mod}」が見つかりません。\n"
                "Box の同期が終わっているか確認し、もう一度起動してください。")
    if isinstance(exc, SyntaxError):
        return ("プログラムのファイルが壊れているか、更新の途中です。\n"
                "Box の同期が終わるまで少し待ってから、もう一度起動してください。")
    if isinstance(exc, PermissionError):
        return ("ファイルやフォルダに書き込めませんでした(ほかのアプリが使用中、または権限がありません)。\n"
                "パソコンを再起動してから、もう一度起動してください。")
    if isinstance(exc, OSError):
        return ("ファイルやフォルダを開けませんでした(Box の同期中の可能性があります)。\n"
                "少し待ってから、もう一度起動してください。")
    if name == "TclError":
        return "画面を表示できませんでした。\nパソコンを再起動してから、もう一度起動してください。"
    return "予期しないエラーが起きました。"


def build_message(exc, logged, first_line):
    detail = f"{type(exc).__name__}: {exc}".strip().replace("\n", " ")
    if len(detail) > 300:
        detail = detail[:300] + "…"
    lines = [first_line, "", "【原因】", explain(exc), "", "【詳細】", detail, "",
             "解決しない場合は、この画面を撮影して担当者に知らせてください。"]
    if logged:
        lines.append(f"記録: {logged}")
    return "\n".join(lines)


def show_message(title, text):
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 | 0x40000)
            return
        except Exception:
            pass
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(title, text, parent=r)
        r.destroy()
    except Exception:
        pass


def show_startup_error(exc):
    logged = write_error_log(exc, "起動に失敗")
    show_message(f"{APP_NAME} — 起動できません",
                 build_message(exc, logged, f"{APP_NAME}を起動できませんでした。"))


def show_runtime_error(exc):
    logged = write_error_log(exc, "操作中のエラー")
    show_message(f"{APP_NAME} — エラー",
                 build_message(exc, logged, "処理の途中でエラーが起きました。"))
