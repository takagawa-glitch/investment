# -*- coding: utf-8 -*-
"""ファイルの置き場所をまとめる。

- アプリのフォルダ(Box の中): 処理記録\\ と ログ\\(error.log)
- このPCの %LOCALAPPDATA%\\銘柄情報取得\\ : EDINET の APIキー、一時保存(キャッシュ)
  APIキーは Box で共有しないよう、必ずこちらに置く。
"""

import os
import tempfile
from pathlib import Path

APP_NAME = "銘柄情報取得"

# アプリのフォルダ(このファイルの1つ上)
APP_DIR = Path(__file__).resolve().parent.parent


def local_dir() -> Path:
    """このPCだけの保存先。環境変数 MEIGARA_DATA_DIR があればそこ(GitHub Actions 用)。"""
    override = os.environ.get("MEIGARA_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / APP_NAME


def cache_dir() -> Path:
    return local_dir() / "cache"


def api_key_path() -> Path:
    return local_dir() / "edinet_api_key.txt"


def pc_name() -> str:
    name = os.environ.get("COMPUTERNAME") or ""
    if not name:
        try:
            import socket
            name = socket.gethostname()
        except Exception:
            name = ""
    return "".join(c for c in name if c.isalnum() or c in "-_") or "不明"
