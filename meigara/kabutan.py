# -*- coding: utf-8 -*-
"""株探(kabutan.jp)の「決算」ページから数値を読み取る。

1銘柄につき1ページ(https://kabutan.jp/stock/finance?code=XXXX)だけを取得する。
robots.txt の Crawl-delay: 3 に合わせて、3秒以上あけて取りにいく。

読み取るもの(株探の単位のまま):
- 株価(円・15分遅れ)と時刻、時価総額(億円)
- 通期の売上高(百万円): 最新の実績と、今期の予想 → PSR の計算に使う
- 自己資本比率(%): 「財務【実績】」の表の、値がある最も新しい行と、最新の通期の行
- 営業CF(百万円): 「キャッシュフロー推移」の表の、最新の通期の行
- 有利子負債(百万円・概算): 「財務【実績】」の表の 有利子負債倍率 × 自己資本。
  倍率が載っている最も新しい行を使う。倍率が「－」(株探で求められない会社)なら出さない。
"""

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html.parser import HTMLParser

from . import net

BASE_URL = "https://kabutan.jp/stock/finance?code={code}"
CRAWL_DELAY = 3.0   # 株探の robots.txt の Crawl-delay


class KabutanError(Exception):
    """株探から読み取れなかった。メッセージは画面に出せる日本語。"""


@dataclass
class Period:
    """表の1行分。label は「2026.03」「26.04-06」など株探の表記。"""
    label: str
    value: Decimal
    forecast: bool = False


@dataclass
class KabutanData:
    code: str
    name: str = ""
    market: str = ""
    price: Decimal = None          # 円
    price_time: str = ""           # 例: 2026-10-27T15:02+09:00
    market_cap: Decimal = None     # 億円
    sales_actual: Period = None    # 百万円(最新の通期実績)
    sales_forecast: Period = None  # 百万円(今期予想)
    equity_ratio: Period = None    # %(値がある最も新しい行。四半期のことがある)
    equity_ratio_annual: Period = None  # %(最新の通期)
    operating_cf: Period = None    # 百万円(最新の通期)
    # 有利子負債の概算 = 有利子負債倍率 × 自己資本(同じ行の値)。倍率が「－」の行は使わない
    debt_ratio: Period = None      # 倍
    debt_equity: Period = None     # 百万円(その行の自己資本)
    debt_estimate: Period = None   # 百万円(概算)
    warnings: list = field(default_factory=list)
    source: str = "株探"           # 取得元(GitHub 版は IR BANK)
    price_note: str = "15分遅れ"   # 株価の種類(IR BANK は「終値」)


# ------------------------------------------------------------ HTML の読み取り

class _PageParser(HTMLParser):
    """表(table)を行・セルの文字に分け、株価などの決まった場所も拾う。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []        # [{"head": [...], "rows": [[(tag, text), ...], ...]}]
        self._stack = []        # 入れ子の表に備えて、作りかけの表を積む
        self._cell = None       # 読んでいるセルの (tag, [文字])
        self._in_thead = False
        self._capture = None    # 拾い中の項目名
        self._buf = []
        self._tagdepth = 0
        self.fields = {}        # kabuka / zika / h2 / market / time

    # 拾いたい場所: (タグ, class) → 項目名
    _TARGETS = {("span", "kabuka"): "kabuka", ("td", "v_zika2"): "zika",
                ("span", "market"): "market"}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if self._capture:
            self._tagdepth += 1
        else:
            for (t, c), name in self._TARGETS.items():
                if tag == t and c in cls and name not in self.fields:
                    self._capture, self._buf, self._tagdepth = name, [], 0
                    break
            # 銘柄名は <div class="si_i1_1"> の中の最初の <h2>
            if tag == "div" and "si_i1_1" in cls:
                self._want_h2 = True
            elif tag == "h2" and getattr(self, "_want_h2", False) and "h2" not in self.fields:
                self._want_h2 = False
                self._capture, self._buf, self._tagdepth = "h2", [], 0
        if tag == "time" and "time" not in self.fields and "market" in self.fields:
            self.fields["time"] = a.get("datetime") or ""

        if tag == "table":
            self._stack.append({"head": [], "rows": []})
        elif tag == "thead":
            self._in_thead = True
        elif tag == "tr" and self._stack:
            self._stack[-1]["rows"].append([])
        elif tag in ("td", "th") and self._stack:
            self._cell = (tag, [])
        elif tag == "br" and self._cell:
            self._cell[1].append("")

    def handle_endtag(self, tag):
        if self._capture:
            if self._tagdepth == 0:
                self.fields[self._capture] = "".join(self._buf)
                self._capture = None
            else:
                self._tagdepth -= 1
        if tag in ("td", "th") and self._cell and self._stack:
            t, parts = self._cell
            text = _clean("".join(parts))
            tbl = self._stack[-1]
            if self._in_thead:
                tbl["head"].append(text)
            elif tbl["rows"]:
                tbl["rows"][-1].append((t, text))
            self._cell = None
        elif tag == "thead":
            self._in_thead = False
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._capture:
            self._buf.append(data)
        if self._cell:
            self._cell[1].append(data)


def _clean(s):
    return re.sub(r"[\s　\xa0]+", "", s or "")


def to_decimal(text):
    """「1,205円」「60.5億円」「－」→ Decimal / None"""
    s = _clean(text).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return None


_PERIOD_RE = re.compile(r"(\d{4}\.\d{2}|\d{2}\.\d{2}-\d{2})")


def _period_rows(table, col):
    """表から (期の表記, 予想か, 値) を上から順に返す。値が無い行も返す(値 None)。"""
    out = []
    for row in table["rows"]:
        if not row or row[0][0] != "th":
            continue
        head = row[0][1]
        m = _PERIOD_RE.search(head)
        if not m:
            continue            # 「前期比」「過去最高」などの行
        cells = [text for _, text in row[1:]]
        if col - 1 >= len(cells):
            continue
        label = head[m.start():].rstrip("*")
        forecast = "予" in head[:m.start()]
        out.append((label, forecast, to_decimal(cells[col - 1])))
    return out


def _find_table(tables, must):
    """見出しに must の文字をすべて含む最初の表と、各見出しの列番号を返す。"""
    for t in tables:
        head = t["head"]
        if all(any(m == h for h in head) for m in must):
            return t, {h: i for i, h in enumerate(head)}
    return None, {}


def parse_finance_page(html_text, code) -> KabutanData:
    p = _PageParser()
    p.feed(html_text)
    d = KabutanData(code=code)

    h2 = _clean(p.fields.get("h2", ""))
    if not h2 or not p.fields.get("kabuka"):
        raise KabutanError("株探にこの銘柄のページが見つかりませんでした"
                           "(銘柄コードが正しいか確認してください)")
    d.name = h2[len(code):] if h2.upper().startswith(code.upper()) else h2
    d.market = _clean(p.fields.get("market", ""))
    d.price = to_decimal(p.fields.get("kabuka"))
    d.price_time = p.fields.get("time", "")
    d.market_cap = to_decimal(p.fields.get("zika"))
    if d.market_cap is not None and "兆" in p.fields.get("zika", ""):
        # 例:「4.2兆円」→ 42,000億円(「兆」表記の銘柄は億円にそろえる)
        d.market_cap = _parse_cap_oku(p.fields.get("zika"))

    # 通期の業績(売上高): 見出しに「修正1株配」がある表が通期の業績推移
    t, cols = _find_table(p.tables, ["決算期", "売上高", "修正1株配"])
    if t:
        rows = [r for r in _period_rows(t, cols["売上高"]) if r[2] is not None]
        actual = [r for r in rows if not r[1]]
        fc = [r for r in rows if r[1]]
        if actual:
            d.sales_actual = Period(actual[-1][0], actual[-1][2])
        if fc:
            d.sales_forecast = Period(fc[-1][0], fc[-1][2], forecast=True)
    else:
        d.warnings.append("株探: 通期の業績の表が見つかりません")

    # キャッシュフロー
    t, cols = _find_table(p.tables, ["決算期", "営業CF"])
    if t:
        rows = [r for r in _period_rows(t, cols["営業CF"]) if r[2] is not None and not r[1]]
        if rows:
            d.operating_cf = Period(rows[-1][0], rows[-1][2])
    else:
        d.warnings.append("株探: キャッシュフローの表が見つかりません")

    # 財務(自己資本比率)
    t, cols = _find_table(p.tables, ["決算期", "自己資本比率"])
    if t:
        rows = [r for r in _period_rows(t, cols["自己資本比率"]) if r[2] is not None]
        if rows:
            d.equity_ratio = Period(rows[-1][0], rows[-1][2])
        annual = [r for r in rows if re.fullmatch(r"\d{4}\.\d{2}", r[0])]
        if annual:
            d.equity_ratio_annual = Period(annual[-1][0], annual[-1][2])
        if "有利子負債倍率" in cols and "自己資本" in cols:
            ratios = _period_rows(t, cols["有利子負債倍率"])
            equities = _period_rows(t, cols["自己資本"])
            both = [(r[0], r[2], e[2]) for r, e in zip(ratios, equities)
                    if r[0] == e[0] and r[2] is not None and e[2] is not None]
            if both:
                label, ratio, equity = both[-1]
                d.debt_ratio = Period(label, ratio)
                d.debt_equity = Period(label, equity)
                # 表示は百万円の整数(四捨五入)。倍率が小数第2位までなので概算
                d.debt_estimate = Period(label, (ratio * equity).quantize(Decimal(1), ROUND_HALF_UP))
    else:
        d.warnings.append("株探: 財務の表が見つかりません")
    return d


def _parse_cap_oku(text):
    """「4兆2,000億円」「4.2兆円」→ 億円の Decimal"""
    s = _clean(text).replace(",", "")
    m = re.match(r"(\d+(?:\.\d+)?)兆(?:(\d+(?:\.\d+)?)億)?", s)
    if not m:
        return to_decimal(text)
    oku = Decimal(m.group(1)) * 10000
    if m.group(2):
        oku += Decimal(m.group(2))
    return oku


def fetch(code) -> KabutanData:
    raw = net.fetch(BASE_URL.format(code=code), interval=CRAWL_DELAY, site_name="株探")
    return parse_finance_page(raw.decode("utf-8", errors="replace"), code)
