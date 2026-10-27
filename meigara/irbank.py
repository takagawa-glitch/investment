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
from decimal import ROUND_HALF_UP, Decimal
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


def fetch_debt(edinet_code, progress=None, list_html=None) -> DebtResult:
    """list_html: 財務状況ページを取得済みならそれを渡す(同じページを二度取りにいかない)。"""
    if not re.fullmatch(r"E\d{5}", edinet_code or ""):
        raise IrbankError("EDINETコードが分かりません")
    if progress:
        progress("IR BANK から貸借対照表を探しています")
    cands = doc_candidates(list_html or _get(f"{edinet_code}/bs"))
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


# ============================================================ 株価・業績(GitHub 版で株探の代わりに使う)
#
# 株探は GitHub のサーバーからの取得を拒否する(応答コード 405。2026-10-27 確認)ため、
# GitHub で動かすときは、次の IR BANK のページから取る(1銘柄 4ページ、3秒間隔)。
#   https://irbank.net/<銘柄コード>          株価(前営業日などの終値)・時価総額
#   https://irbank.net/<EDINETコード>/bs     自己資本比率(財務履歴の表の最新行)。有利子負債と共用
#   https://irbank.net/<EDINETコード>/results 売上高(実績・予想。「20億」のような概数)
#   https://irbank.net/<EDINETコード>/cf     営業CF(百万円。最新の通期)

def parse_amount_million(text):
    """「56億6119万」「20億」「364百万」「1.2兆」「-57百万」→ 百万円の Decimal(読めなければ None)。"""
    s = re.sub(r"[\s,]", "", unicodedata.normalize("NFKC", text or ""))
    if not s or s in ("-", "－"):
        return None
    neg = s.startswith("-")
    s = s.lstrip("-")
    units = {"兆": Decimal(1_000_000), "億": Decimal(100), "百万": Decimal(1), "万": Decimal("0.01")}
    total, pos, found = Decimal(0), 0, False
    for m in re.finditer(r"(\d+(?:\.\d+)?)(兆|億|百万|万)", s):
        if m.start() != pos:
            return None
        total += Decimal(m.group(1)) * units[m.group(2)]
        pos, found = m.end(), True
    if not found:
        if re.fullmatch(r"\d+(?:\.\d+)?", s):
            total, pos, found = Decimal(s), len(s), True   # 単位なし = 百万円の表(CF など)
        else:
            return None
    if pos != len(s):
        return None
    return -total if neg else total


def _text(fragment):
    return re.sub(r"\s+", "", html_unescape(re.sub(r"<[^>]+>", "", fragment or "")))


def html_unescape(s):
    import html
    return html.unescape(s)


def parse_quote_page(html_text):
    """(終値, 日付 'YYYY-MM-DD', 時価総額[億円]) を返す。取れない項目は None。"""
    price = None
    m = re.search(r"<dt>終値.*?</dt>\s*<dd>.*?<span class=\"text\">([^<]+)</span>", html_text, re.S)
    if m:
        price = to_decimal_plain(m.group(1))
    date = None
    # 株価の見出しは「株価（10/26）」と月日だけなので、ページ上部の「2026/10/26」から年を取る
    m = re.search(r'id="c_Price">株価<span class="co_sm">（(\d{1,2})/(\d{1,2})）', html_text)
    if m:
        mo, dd = int(m.group(1)), int(m.group(2))
        fulls = re.findall(r"(20\d{2})/(\d{2})/(\d{2})", _text(html_text[:40000]))
        same = [y for y, a, b in fulls if int(a) == mo and int(b) == dd]
        year = same[0] if same else (fulls[0][0] if fulls else None)
        if year:
            date = f"{year}-{mo:02d}-{dd:02d}"
    cap = None
    m = re.search(r"<dt>時価総額</dt>\s*<dd>.*?<span class=\"text\">([^<]+)</span>", html_text, re.S)
    if m:
        mil = parse_amount_million(m.group(1))
        if mil is not None:
            # 億円にそろえ、株探と同じく小数第1位で四捨五入(表示用)
            cap = (mil / 100).quantize(Decimal("0.1"), ROUND_HALF_UP)
    return price, date, cap


def to_decimal_plain(text):
    s = re.sub(r"[^\d.\-]", "", text or "")
    try:
        return Decimal(s) if s not in ("", "-", ".") else None
    except Exception:
        return None


def parse_equity_ratio(list_html):
    """財務状況ページの財務履歴の表から、自己資本比率の (年度, 値) の一覧を古い順に返す。"""
    tbl = re.search(r'<table class="cs">.*?</table>', list_html or "", re.S)
    if not tbl:
        return []
    t = tbl.group(0)
    heads = [_text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", t, re.S)]
    if "自己資本比率" not in heads:
        return []
    col = heads.index("自己資本比率")
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) > col and re.fullmatch(r"\d{4}/\d{2}", cells[0]):
            v = to_decimal_plain(cells[col].rstrip("%")) if cells[col] not in ("-", "") else None
            if v is not None:
                out.append((cells[0], v))
    return out


def parse_sales(results_html):
    """業績ページの最初の表から、売上の (年度, 百万円, 予想か) の一覧を古い順に返す。"""
    tbl = re.search(r"<table class=\"bar bs\">.*?</table>", results_html or "", re.S)
    if not tbl:
        return []
    t = tbl.group(0)
    heads = [_text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", t, re.S)]
    col = next((i for i, h in enumerate(heads) if h in ("売上", "売上高", "営業収益", "売上収益")), None)
    if col is None:
        return []
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        raw = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(raw) <= col:
            continue
        label = _text(raw[0])
        m = re.match(r"(\d{4}/\d{2})", label)
        if not m:
            continue
        v = parse_amount_million(_text(raw[col]))
        if v is not None:
            out.append((m.group(1), v, "予" in label[7:]))
    return out


def parse_operating_cf(cf_html):
    """CF ページの表から、通期の営業CF の (年度, 百万円) の一覧を古い順に返す。"""
    tbl = re.search(r'<table class="cs"><caption[^>]*>キャッシュ・フロー計算書.*?</table>', cf_html or "", re.S)
    if not tbl:
        return []
    # 単位は会社の規模で変わる(百万円・億円など)。百万円にそろえる
    um = re.search(r"キャッシュ・フロー計算書\s*[(（]([^)）]+)[)）]", tbl.group(0))
    unit = um.group(1) if um else "百万円"
    to_million = {"百万円": Decimal(1), "億円": Decimal(100), "千円": Decimal("0.001"),
                  "兆円": Decimal(1_000_000)}.get(unit)
    if to_million is None:
        return []
    out, year, quarter, want = [], None, None, False
    for cls, body in re.findall(r'<td class="([^"]*)"[^>]*>(.*?)</td>', tbl.group(0), re.S):
        text = _text(body)
        if cls.startswith("lf"):
            m = re.match(r"(\d{4})年(\d{1,2})月期", text)
            if m:
                year = f"{m.group(1)}/{int(m.group(2)):02d}"
            else:
                quarter, want = text, True
        elif want:
            want = False
            if quarter == "通期" and year:
                v = to_decimal_plain(text) if text not in ("-", "") else None
                if v is not None:
                    out.append((year, v * to_million))
    return out


def fetch_market(code, edinet_code, name="", progress=None):
    """IR BANK から株探と同じ形(KabutanData)で返す。2つめは財務状況ページ(有利子負債で使い回す)。"""
    from .kabutan import KabutanData, Period
    d = KabutanData(code=code, name=name, source="IR BANK", price_note="終値")
    if progress:
        progress("IR BANK から株価を取得しています")
    try:
        price, date, cap = parse_quote_page(_get(code))
        d.price, d.market_cap = price, cap
        d.price_time = date or ""
        if price is None:
            d.warnings.append("IR BANK: 株価が見つかりません")
        if cap is None:
            d.warnings.append("IR BANK: 時価総額が見つかりません")
    except net.FetchError as e:
        d.warnings.append(f"IR BANK(株価): {e}")

    list_html = None
    eq_rows = []
    if not re.fullmatch(r"E\d{5}", edinet_code or ""):
        d.warnings.append("EDINETコードが分からないため、業績・財務を取得できません")
        return d, None
    for kind, path in (("財務", "bs"), ("業績", "results"), ("CF", "cf")):
        if progress:
            progress(f"IR BANK から{kind}を取得しています")
        try:
            page = _get(f"{edinet_code}/{path}")
        except net.FetchError as e:
            d.warnings.append(f"IR BANK({kind}): {e}")
            continue
        if path == "bs":
            list_html = page
            eq_rows = parse_equity_ratio(page)
            if eq_rows:
                d.equity_ratio = Period(*eq_rows[-1])
            else:
                d.warnings.append("IR BANK: 自己資本比率が見つかりません")
        elif path == "results":
            rows = parse_sales(page)
            actual = [r for r in rows if not r[2]]
            fc = [r for r in rows if r[2]]
            if actual:
                d.sales_actual = Period(actual[-1][0], actual[-1][1])
            else:
                d.warnings.append("IR BANK: 売上高が見つかりません")
            if fc:
                d.sales_forecast = Period(fc[-1][0], fc[-1][1], forecast=True)
        else:
            rows = parse_operating_cf(page)
            if rows:
                d.operating_cf = Period(*rows[-1])
            else:
                d.warnings.append("IR BANK: 営業CF が見つかりません")
    # 通期の自己資本比率: 決算月(売上高の年度の月)と同じ月の行のうち最新
    if d.equity_ratio and d.sales_actual:
        fy_month = d.sales_actual.label[5:]
        annual = [r for r in eq_rows if r[0][5:] == fy_month]
        if annual:
            d.equity_ratio_annual = Period(*annual[-1])
    return d, list_html
