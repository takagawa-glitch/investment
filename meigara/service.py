# -*- coding: utf-8 -*-
"""1銘柄ぶんの取得と計算をまとめる(画面からは独立させてテストしやすくする)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from . import codes, edinet, irbank, kabutan, net


@dataclass
class StockResult:
    query: str
    code: str = ""
    name: str = ""
    kabutan: kabutan.KabutanData = None
    debt: edinet.DebtResult = None
    psr_actual: Decimal = None
    psr_forecast: Decimal = None
    status: str = "成功"            # 成功 / 一部取得できず / 要確認 / 失敗
    notes: list = field(default_factory=list)
    candidates: list = field(default_factory=list)   # 名前で複数見つかったとき


def calc_psr(market_cap_oku, sales_million):
    """PSR = 時価総額 ÷ 売上高。
    株探の単位(時価総額: 億円、売上高: 百万円)をそろえるため、時価総額を100倍して百万円にする。
    表示用に小数第2位で四捨五入する(ROUND_HALF_UP)。
    売上高が0以下・不明のときは計算しない(None)。"""
    if market_cap_oku is None or sales_million is None or sales_million <= 0:
        return None
    return (market_cap_oku * 100 / sales_million).quantize(Decimal("0.01"), ROUND_HALF_UP)


def resolve(query, book):
    """入力1つを (Company or None, 銘柄コード or None, 候補一覧, 注意文) にする。"""
    q = codes.normalize_input(query)
    if codes.CODE_RE.match(q):
        return book.lookup_code(q), q, [], None
    found = book.search_name(q)
    if len(found) == 1:
        return found[0], found[0].code, [], None
    if not found:
        return None, None, [], "その名前の上場会社が見つかりませんでした。銘柄コードで入力してください"
    return None, None, found, f"候補が{len(found)}件あります。銘柄コードで入力し直してください"


def get_stock(query, book, api_key, progress=None, cancelled=None, source="kabutan") -> StockResult:
    """source: 株価・業績の取得元。"kabutan"(PC。株探)/"irbank"(GitHub。株探が使えないため)"""
    res = StockResult(query=query)
    company, code, cands, msg = resolve(query, book)
    if code is None:
        res.status = "要確認"
        res.candidates = cands
        res.notes.append(msg)
        return res
    res.code = code
    res.name = company.name if company else ""
    edinet_code = company.edinet_code if company else ""
    list_html = None   # IR BANK の財務状況ページ(取得済みなら有利子負債で使い回す)

    k = None
    if source == "irbank":
        try:
            k, list_html = irbank.fetch_market(
                code, edinet_code, res.name,
                progress=(lambda s: progress(f"{code}: {s}")) if progress else None)
            if k.price is None and k.sales_actual is None and k.equity_ratio is None:
                res.notes.extend(k.warnings)
                k = None
        except net.FetchError as e:
            res.notes.append(f"IR BANK: {e}")
    else:
        if progress:
            progress(f"{code}: 株探から取得しています")
        try:
            k = kabutan.fetch(code)
        except (kabutan.KabutanError, net.FetchError) as e:
            res.notes.append(f"株探: {e}")
    if k is not None:
        res.kabutan = k
        res.name = k.name or res.name
        res.notes.extend(k.warnings)
        if k.sales_actual:
            res.psr_actual = calc_psr(k.market_cap, k.sales_actual.value)
        if k.sales_forecast:
            res.psr_forecast = calc_psr(k.market_cap, k.sales_forecast.value)

    # 有利子負債: 分かる場合だけ出す。
    #   APIキーがあれば EDINET の正確な値、無ければ株探の倍率からの概算、どちらも無ければ「－」。
    #   「－」は正常な結果として扱う(結果の欄を「一部取得できず」にしない)。
    #   順番: EDINET(APIキーがあるとき)→ IR BANK の貸借対照表を足し算 → 株探の倍率から概算 → 「－」
    if cancelled and cancelled():
        raise edinet.Cancelled()
    if api_key and edinet_code:
        try:
            doc = edinet.find_latest_report(
                company.edinet_code, api_key,
                progress=(lambda s: progress(f"{code}: {s}")) if progress else None,
                cancelled=cancelled)
            if doc is None:
                res.notes.append("有利子負債: EDINET に最近の報告書が見つかりませんでした")
            else:
                if progress:
                    progress(f"{code}: EDINET から報告書を読み取っています")
                res.debt = edinet.fetch_debt(doc, api_key)
                res.notes.extend("有利子負債: " + n for n in res.debt.notes)
        except edinet.EdinetError as e:
            res.notes.append(f"有利子負債: {e}")
    if res.debt is None and edinet_code:
        if cancelled and cancelled():
            raise edinet.Cancelled()
        try:
            res.debt = irbank.fetch_debt(
                edinet_code, progress=(lambda s: progress(f"{code}: {s}")) if progress else None,
                list_html=list_html)
            res.notes.extend("有利子負債: " + n for n in res.debt.notes)
        except (irbank.IrbankError, net.FetchError) as e:
            res.notes.append(f"有利子負債: {e}")
    if res.debt is None:
        if res.kabutan and res.kabutan.debt_estimate:
            res.notes.append("有利子負債: 貸借対照表が取れなかったため、株探の倍率からの概算です")
        else:
            res.notes.append("有利子負債: 不明(貸借対照表が取れず、株探にも有利子負債倍率がありません)")

    k = res.kabutan
    if k is None and res.debt is None:
        res.status = "失敗"
    elif res.debt is not None and res.debt.needs_check:
        res.status = "要確認"
    elif k is None or k.warnings:
        res.status = "一部取得できず"
    return res
