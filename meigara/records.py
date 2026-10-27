# -*- coding: utf-8 -*-
"""処理記録(いつ・誰が・どの銘柄を取得して・どうなったか)。画面には出さず、ファイルにだけ書く。

保存先: アプリのフォルダの 処理記録\\処理記録_YYYY-MM_<PC名>.log
書式: 日時 | 職員 | 顧問先 | 処理 | 対象者 | 結果 | 内容
  このツールは顧問先のデータを扱わないため、顧問先は「-」、対象者は銘柄コードと銘柄名。
APIキーは書かない。
"""

import calendar
import datetime
import os
import re
import threading

from . import paths

LOG_DIR = paths.APP_DIR / "処理記録"
LOG_HEADER = "日時 | 職員 | 顧問先 | 処理 | 対象者 | 結果 | 内容"
LOG_KEEP_YEARS = 1
_LOG_NAME_RE = re.compile(r"^処理記録_(\d{4})-(\d{2})_.*\.log$")
_lock = threading.Lock()


def _text(v):
    s = str(v if v is not None else "")
    return re.sub(r"[\r\n\t|]+", " ", s).strip()


def log_file_path(now=None, folder=None):
    now = now or datetime.datetime.now()
    return os.path.join(str(folder or LOG_DIR),
                        f"処理記録_{now:%Y-%m}_{paths.pc_name()}.log")


def write_record(action, target="", result="", detail="", now=None, folder=None):
    """1行追記する。書けなくても(Box の同期中など)本来の処理は止めない。"""
    now = now or datetime.datetime.now()
    staff = os.environ.get("USERNAME") or ""
    line = " | ".join(_text(v) for v in (
        now.strftime("%Y-%m-%d %H:%M:%S"), staff, "-", action, target, result, detail))
    path = log_file_path(now, folder)
    try:
        with _lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            new = not os.path.exists(path)
            with open(path, "a", encoding="utf-8") as f:
                if new:
                    f.write(LOG_HEADER + "\n")
                f.write(line + "\n")
        return True
    except OSError:
        return False


def cleanup_old_logs(today=None, folder=None):
    """その月の最終日から1年を過ぎた処理記録を消す(起動時に呼ぶ)。消したファイル名を返す。"""
    today = today or datetime.date.today()
    try:
        limit = today.replace(year=today.year - LOG_KEEP_YEARS)
    except ValueError:              # 2/29
        limit = today.replace(year=today.year - LOG_KEEP_YEARS, day=28)
    folder = str(folder or LOG_DIR)
    removed = []
    try:
        names = os.listdir(folder)
    except OSError:
        return removed
    for name in names:
        m = _LOG_NAME_RE.match(name)
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            continue
        if datetime.date(y, mo, calendar.monthrange(y, mo)[1]) < limit:
            try:
                os.remove(os.path.join(folder, name))
                removed.append(name)
            except OSError:
                pass
    return removed
