# -*- coding: utf-8 -*-
"""インターネットからの取得(標準ライブラリの urllib だけを使う)。

相手のサイトに負担をかけないよう、同じサイトへは決まった間隔をあけて取りにいく。
エラーの文章に URL を入れない(EDINET の URL には APIキーが入るため)。
"""

import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class FetchError(Exception):
    """取得に失敗した。メッセージは画面にそのまま出せる日本語。"""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


_lock = threading.Lock()
_last_access = {}   # サイト名 → 最後に取りにいった時刻


def _wait_turn(host, interval):
    while True:
        with _lock:
            now = time.monotonic()
            wait = _last_access.get(host, -1e9) + interval - now
            if wait <= 0:
                _last_access[host] = now
                return
        time.sleep(min(wait, 0.5))


def fetch(url, *, interval=0.0, timeout=30, retries=2, site_name=None) -> bytes:
    """URL の中身を返す。interval 秒以上あけて同じサイトへアクセスする。"""
    host = urllib.parse.urlsplit(url).hostname or ""
    site = site_name or host
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "ja,en;q=0.8",
    })
    for attempt in range(retries + 1):
        _wait_turn(host, interval)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(5)
                continue
            raise FetchError(f"{site}から取得できませんでした(応答コード {e.code})",
                             status=e.code) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError,
                ConnectionError) as e:
            if attempt < retries:
                time.sleep(5)
                continue
            reason = getattr(e, "reason", e)
            raise FetchError(f"{site}に接続できませんでした({reason})。"
                             "インターネットにつながっているか確認してください") from None
    raise FetchError(f"{site}から取得できませんでした")
