# -*- coding: utf-8 -*-
"""IR BANK(irbank.net)の貸借対照表から、借入金・社債などを足して有利子負債を求める。

APIキーは不要。流れ:
1. 会社の「財務状況」ページ https://irbank.net/<EDINETコード>/bs を取得し、
   書類ごとの貸借対照表ページへのリンクを、新しい順に並べる
   (先頭は最新の決算短信、続いて有価証券報告書などを新しい順)。
2. 書類ごとのページ https://irbank.net/<EDINETコード>/<書類ID>/bs を新しい順に試し、
   開けた最初のページの貸借対照表(表 id="c_bs1")の、いちばん右の列(最新の日付)を使う。
   IR BANK 側でページが壊れている(500 など)ことがあるため、最大 MAX_DOC_TRIES 件まで試す。
3. 「負債の部」から「純資産の部」までの行で、科目名が借入金・社債・CP のものを足す
   (判定は edinet.classify_item と共通)。リース債務は合計に入れず別に出す。
   表の単位(千円・百万円など)を見て円にそろえる。

アクセスは robots.txt(Allow: /)に従い、3秒以上あける。
書類ごとの計算結果は保存し、同じ書類は二度と取りにいかない。
"""

import json
import re
import time
import unicodedata
from dataclasses import asdict
from decimal import Decimal
from html.parser import HTMLParser

from . import net, paths
from .edinet import DebtItem, DebtResult, check_items, classify_item

BASE = "https://irbank.net"
INTERVAL = 3.0
MAX_DOC_TRIES = 3
BAD_DOC_SECONDS = 24 * 3600   # 開けなかった書類は1日は試さない(相手に負担をかけない)

UNITS = {"円": 1, "千円": 1000, "百万円": 1_000_000, "億円": 100_000_000}

# 金融庁の標準の科目(IR BANK のリンクに出る科目ID)。これ以外は「会社独自」として要確認にする
_STANDARD_ROOTS = (
    "ShortTermLoansPayable", "CurrentPortionOfLongTermLoansPayable", "LongTermLoansPayable",
    "BondsPayable", "CurrentPortionOfBonds", "CommercialPaper",
    "ConvertibleBond", "CurrentPortionOfConvertibleBond",
    "BondsWithSubscriptionRights", "CurrentPortionOfBondsWithSubscriptionRights",
    "LeaseObligations", "LeaseLiabilities", "Borrowings", "BondsAndBorrowings",
    "BondsPayableIFRS", "InterestBearing",
)


class IrbankError(Exception):
    """IR BANK から求められなかった。メッセージは画面に出せる日本語。"""


# ------------------------------------------------------------ 財務状況ページ

def doc_candidates(html_text):
    """[(書類ID, 説明)] を新しい順に返す。"""
    out = []
    # 最新(決算短信など): <p class="pad"> の最初のリンク
    m = re.search(r'<p class="pad"><a title="([^"]*)" href="([0-9A-Za-z]+)/bs"', html_text)
    if m:
        out.append((m.group(2), m.group(1)))
    # 財務履歴の表: 古い順に並んでいるので逆にする
    tbl = re.search(r'<table class="cs">.*?</table>', html_text, re.S)
    if tbl:
        rows = re.findall(r'<a title="([^"]*)" href="([0-9A-Za-z]+)/bs"', tbl.group(0))
        for title, doc in reversed(rows):
            out.append((doc, title))
    seen, uniq = set(), []
    for doc, title in out:
        if doc not in seen:
            seen.add(doc)
            uniq.append((doc, _doc_title(title)))
    return uniq


def _doc_title(title):
    # 「554A バトンズ | 貸借対照表 - 有価証券報告書-第8期(...) - 2026年6月30日 15:02提出」→ 書類名と提出日
    parts = [p.strip() for p in title.split(" - ")]
    if len(parts) >= 3:
        return f"{parts[1]}({parts[2]})"
    return title


# ------------------------------------------------------------ 書類ごとの貸借対照表

class _BsParser(HTMLParser):
    """<table id="c_bs1"> の 単位・日付・各行(科目名, 科目ID, 値の一覧) を読む。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.depth = 0
        self.caption = []
        self.in_caption = False
        self.dates = []
        self.rows = []          # [(科目名, 科目ID, [値の文字...], 太字か)]
        self._cell = None
        self._row = None
        self._href = None
        self._bold = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            if a.get("id") == "c_bs1" and not self.rows:
                self.in_table = True
                self.depth = 0
            elif self.in_table:
                self.depth += 1
            return
        if not self.in_table:
            return
        if tag == "caption":
            self.in_caption = True
        elif tag == "tr":
            self._row = {"cells": [], "href": None, "bold": False, "th": []}
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            if tag == "td" and "bold" in (a.get("class") or "").split():
                self._row["bold"] = True
            self._is_th = tag == "th"
        elif tag == "a" and self._row is not None and self._row["href"] is None:
            href = a.get("href") or ""
            m = re.match(r"\.\./([A-Za-z0-9_]+)", href)
            if m:
                self._row["href"] = m.group(1)

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag == "table":
            if self.depth == 0:
                self.in_table = False
            else:
                self.depth -= 1
        elif tag == "caption":
            self.in_caption = False
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", "", "".join(self._cell))
            (self._row["th"] if self._is_th else self._row["cells"]).append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            r = self._row
            if r["th"] and not r["cells"]:
                self.dates = r["th"][1:]
            elif r["cells"]:
                self.rows.append((r["cells"][0], r["href"], r["cells"][1:], r["bold"]))
            self._row = None

    def handle_data(self, data):
        if self.in_caption:
            self.caption.append(data)
        if self._cell is not None:
            self._cell.append(data)


def compute_debt_from_page(html_text) -> DebtResult:
    p = _BsParser()
    p.feed(html_text)
    if not p.rows:
        raise IrbankError("IR BANK の貸借対照表を読み取れませんでした")
    cap = "".join(p.caption)
    unit = next((u for u in sorted(UNITS, key=len, reverse=True) if f"（{u}）" in cap or f"({u})" in cap), None)
    if unit is None:
        raise IrbankError("IR BANK の貸借対照表の単位が分かりませんでした")
    mult = Decimal(UNITS[unit])

    res = DebtResult(source="IR BANK")
    res.period_end = p.dates[-1] if p.dates else ""
    m = re.search(r"貸借（(連結|個別)）", html_text)
    res.basis = m.group(1) if m else ""

    in_liab = False
    total, lease = Decimal(0), None
    for label, element, values, bold in p.rows:
        name = unicodedata.normalize("NFKC", label).strip()
        if bold and name.startswith("負債の部"):
            in_liab = True
            continue
        if bold and name.startswith("純資産の部"):
            break
        if not in_liab or not values:
            continue
        raw = values[-1].replace(",", "")
        if not re.fullmatch(r"-?\d+(\.\d+)?", raw):
            continue
        yen = Decimal(raw) * mult
        kind = classify_item(name)
        if kind is None:
            continue
        el = element or ""
        standard = bool(el) and el.startswith(_STANDARD_ROOTS)
        item = DebtItem(name, el, str(yen), standard)
        if kind == "lease":
            lease = (lease or Decimal(0)) + yen
            res.lease_items.append(item)
        else:
            total += yen
            res.items.append(item)
    if not in_liab:
        raise IrbankError("IR BANK の貸借対照表に負債の部が見つかりませんでした")
    res.total_yen = str(total)
    res.lease_yen = None if lease is None else str(lease)
    check_items(res)
    return res


# ------------------------------------------------------------ 取得(保存つき)

def _cache(name):
    return paths.cache_dir() / "irbank" / name


def _load_json(f, default):
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save_json(f, data):
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass   # 保存できなくても続ける


def _get(path, retries=2):
    raw = net.fetch(f"{BASE}/{path}", interval=INTERVAL, timeout=40, retries=retries,
                    site_name="IR BANK")
    return raw.decode("utf-8", errors="replace")


def fetch_debt(edinet_code, progress=None) -> DebtResult:
    if not re.fullmatch(r"E\d{5}", edinet_code or ""):
        raise IrbankError("EDINETコードが分かりません")
    if progress:
        progress("IR BANK から貸借対照表を探しています")
    cands = doc_candidates(_get(f"{edinet_code}/bs"))
    if not cands:
        raise IrbankError("IR BANK に貸借対照表が見つかりませんでした")

    bad_file = _cache("開けなかった書類.json")
    bad = _load_json(bad_file, {})
    now = time.time()
    bad = {k: v for k, v in bad.items() if now - v < BAD_DOC_SECONDS}
    skipped = []
    tried = 0
    for doc, title in cands:
        f = _cache(f"{edinet_code}_{doc}.json")
        if f.exists():
            d = _load_json(f, None)
            if d:
                d["items"] = [DebtItem(**i) for i in d.get("items", [])]
                d["lease_items"] = [DebtItem(**i) for i in d.get("lease_items", [])]
                res = DebtResult(**d)
                _add_skip_note(res, skipped)
                return res
        if doc in bad:
            skipped.append(title)
            continue
        if tried >= MAX_DOC_TRIES:
            break
        tried += 1
        try:
            res = compute_debt_from_page(_get(f"{edinet_code}/{doc}/bs", retries=1))
        except (net.FetchError, IrbankError):
            bad[doc] = now
            skipped.append(title)
            continue
        res.doc_id = doc
        res.doc_description = title
        _save_json(f, asdict(res))
        _save_json(bad_file, bad)
        _add_skip_note(res, skipped)
        return res
    _save_json(bad_file, bad)
    raise IrbankError("IR BANK の貸借対照表のページを開けませんでした(IR BANK 側の不具合の可能性)")


def _add_skip_note(res, skipped):
    if skipped:
        res.notes = list(res.notes) + [
            "より新しい書類(" + "、".join(skipped) + ")のページが IR BANK で開けなかったため、"
            "古い書類の貸借対照表を使っています"]
