# -*- coding: utf-8 -*-
"""銘柄名・銘柄コードの読み取り。

金融庁 EDINET の「EDINETコード一覧」(APIキー不要の公開ファイル)を使い、
銘柄コード(例: 554A)↔ 会社名 ↔ EDINETコード(例: E41514)を引く。
一覧はこのPCに保存し、7日たったら取り直す。取り直せないときは古い一覧を使う。
"""

import csv
import io
import os
import re
import time
import unicodedata
import zipfile
from dataclasses import dataclass

from . import net, paths

CODELIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"
REFRESH_SECONDS = 7 * 24 * 3600

CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")   # 例: 7203, 554A, 130A


@dataclass
class Company:
    code: str          # 4桁の銘柄コード
    name: str          # 提出者名(例: 株式会社バトンズ)
    kana: str
    edinet_code: str   # 例: E41514


def normalize_input(text):
    """全角→半角、英字は大文字に、前後の空白を除く。"""
    return unicodedata.normalize("NFKC", text or "").strip().upper()


def split_queries(text):
    """入力欄の文字を、改行・カンマ・読点・空白で区切った一覧にする(重複は1つに)。
    社名の途中に空白があると分かれてしまうので、社名は空白を入れずに入力してもらう。"""
    seen, out = set(), []
    for part in re.split(r"[\s,、，;；]+", text or ""):
        q = normalize_input(part)
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _name_key(s):
    s = normalize_input(s)
    s = re.sub(r"株式会社|\(株\)|有限会社|合同会社|ホールディングス|HOLDINGS|HD", "", s)
    return re.sub(r"[\s・.,'’\-ー]", "", s)


def _kana_key(s):
    s = normalize_input(s)
    s = re.sub("カブシキ[ガカ]イシャ", "", s)
    return re.sub(r"[\s・.\-ー]", "", s)


def parse_codelist(raw_csv_bytes):
    """EdinetcodeDlInfo.csv(Shift_JIS)から、上場会社(証券コードあり)の一覧を作る。"""
    text = raw_csv_bytes.decode("cp932", errors="replace")
    lines = text.splitlines()
    # 1行目はダウンロード日時などの説明、2行目が見出し
    reader = csv.DictReader(io.StringIO("\n".join(lines[1:])))
    out = []
    for row in reader:
        sec = (row.get("証券コード") or "").strip()
        if not sec:
            continue
        code = sec[:4] if len(sec) == 5 and sec.endswith("0") else sec
        out.append(Company(code=code.upper(),
                           name=(row.get("提出者名") or "").strip(),
                           kana=(row.get("提出者名（ヨミ）") or "").strip(),
                           edinet_code=(row.get("ＥＤＩＮＥＴコード") or "").strip()))
    return out


class CodeBook:
    def __init__(self, companies):
        self.companies = companies
        self.by_code = {c.code: c for c in companies}

    def lookup_code(self, code):
        return self.by_code.get(code.upper())

    def search_name(self, query):
        """名前で探す。完全一致があればそれだけ、無ければ部分一致の候補を返す。"""
        q = _name_key(query)
        qk = _kana_key(query)
        if not q:
            return []
        exact = [c for c in self.companies
                 if _name_key(c.name) == q or (qk and _kana_key(c.kana) == qk)]
        if exact:
            return exact
        return [c for c in self.companies
                if q in _name_key(c.name) or (qk and qk in _kana_key(c.kana))]


def _cache_file():
    return paths.cache_dir() / "EdinetcodeDlInfo.csv"


def load_codebook(force=False):
    """コード一覧を読み込む。(CodeBook, 注意文 or None) を返す。"""
    f = _cache_file()
    fresh = f.exists() and (time.time() - f.stat().st_mtime) < REFRESH_SECONDS
    note = None
    if force or not fresh:
        try:
            raw = net.fetch(CODELIST_URL, site_name="EDINET(コード一覧)", timeout=60)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
                data = z.read(name)
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, f)
        except Exception as e:   # 取れなくても、前回の一覧があれば使う
            if not f.exists():
                return CodeBook([]), f"銘柄の一覧を取得できませんでした({e})。銘柄名での検索と有利子負債の取得はできません"
            note = "銘柄の一覧を新しくできなかったため、前回の一覧を使っています"
    return CodeBook(parse_codelist(f.read_bytes())), note
