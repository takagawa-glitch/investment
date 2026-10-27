# -*- coding: utf-8 -*-
"""取得結果を画面に出す文字にする(表の1行と、下の詳細欄)。"""

from decimal import Decimal, ROUND_HALF_UP

COLUMNS = [
    # (列の名前, 見出し, 幅, 右寄せ)
    ("code", "コード", 60, False),
    ("name", "銘柄名", 150, False),
    ("price", "株価(円)", 80, True),
    ("cap", "時価総額(億円)", 100, True),
    ("psr", "PSR 実績(倍)", 90, True),
    ("psr_f", "PSR 予想(倍)", 90, True),
    ("eq", "自己資本比率(%)", 110, True),
    ("cf", "営業CF(百万円)", 105, True),
    ("debt", "有利子負債(百万円)", 125, True),
    ("status", "結果", 100, False),
]


def fmt(v, digits=None):
    """数値を3桁区切りで。None は「－」。"""
    if v is None:
        return "－"
    v = Decimal(v)
    if digits is not None:
        v = v.quantize(Decimal(1).scaleb(-digits), ROUND_HALF_UP)
    s = f"{v:,f}"
    return s


def yen_to_million(yen):
    """円 → 百万円(表示用に整数へ四捨五入。株探の百万円表示とそろえる)。"""
    if yen is None:
        return None
    return (Decimal(yen) / Decimal(1_000_000)).quantize(Decimal(1), ROUND_HALF_UP)


def row_values(r):
    k = r.kabutan
    d = r.debt
    return (
        r.code or r.query,
        r.name or "",
        fmt(k.price) if k else "－",
        fmt(k.market_cap) if k else "－",
        fmt(r.psr_actual),
        fmt(r.psr_forecast),
        fmt(k.equity_ratio.value) if k and k.equity_ratio else "－",
        fmt(k.operating_cf.value) if k and k.operating_cf else "－",
        debt_cell(r),
        r.status,
    )


def debt_cell(r):
    """有利子負債の表示。EDINET の値があればそれ、無ければ株探の概算に「約」を付ける。"""
    if r.debt:
        return fmt(yen_to_million(r.debt.total_yen))
    k = r.kabutan
    if k and k.debt_estimate:
        return "約" + fmt(k.debt_estimate.value)
    return "－"


def _time_text(iso):
    # 2026-10-27T15:02+09:00 → 2026-10-27 15:02
    return iso[:16].replace("T", " ") if iso else "時刻不明"


def detail_text(r):
    lines = []
    title = f"{r.code} {r.name}".strip() or r.query
    k = r.kabutan
    if k and k.market:
        title += f"({k.market})"
    lines.append(f"■ {title}  結果: {r.status}")

    if k:
        lines.append(f"株価: {fmt(k.price)}円({_time_text(k.price_time)} 時点・15分遅れ/株探)")
        lines.append(f"時価総額: {fmt(k.market_cap)}億円(株探)")
        for label, psr, sales in (("実績", r.psr_actual, k.sales_actual),
                                  ("予想", r.psr_forecast, k.sales_forecast)):
            if psr is not None:
                lines.append(f"PSR({label}): {fmt(psr)}倍 = 時価総額 {fmt(k.market_cap)}億円 ÷ "
                             f"売上高 {fmt(sales.value)}百万円({sales.label}期 {label})")
            else:
                lines.append(f"PSR({label}): 計算できません(売上高が取得できません)")
        if k.equity_ratio:
            s = f"自己資本比率: {fmt(k.equity_ratio.value)}%({k.equity_ratio.label} 時点/株探)"
            a = k.equity_ratio_annual
            if a and a.label != k.equity_ratio.label:
                s += f"  通期 {a.label}: {fmt(a.value)}%"
            lines.append(s)
        else:
            lines.append("自己資本比率: 取得できません")
        if k.operating_cf:
            lines.append(f"営業CF: {fmt(k.operating_cf.value)}百万円({k.operating_cf.label}期 通期/株探)")
        else:
            lines.append("営業CF: 取得できません")
        if not r.debt:
            if k.debt_estimate:
                lines.append(f"有利子負債: 約{fmt(k.debt_estimate.value)}百万円(概算 = 有利子負債倍率 "
                             f"{fmt(k.debt_ratio.value)}倍 × 自己資本 {fmt(k.debt_equity.value)}百万円、"
                             f"{k.debt_estimate.label} 時点/株探)")
            else:
                lines.append("有利子負債: 不明(株探に有利子負債倍率が載っていません)")

    d = r.debt
    if d:
        lines.append(f"有利子負債: {fmt(yen_to_million(d.total_yen))}百万円"
                     f"({fmt(d.total_yen)}円)  借入金・社債・CP の合計、リース債務は含まない")
        if d.source == "IR BANK":
            lines.append(f"  出典: IR BANK の貸借対照表 {d.period_end} 時点 {d.basis}"
                         f"(元の資料: {d.doc_description})")
        else:
            lines.append(f"  出典: EDINET {d.doc_description or d.doc_type}(提出日 {d.submitted})"
                         f" {d.basis}の貸借対照表 {d.period_label}"
                         + (f"({d.period_end})" if d.period_end else ""))
        for i in d.items:
            mark = "" if i.standard else " ※会社独自の項目"
            lines.append(f"    {i.label}: {fmt(i.yen)}円{mark}")
        if d.lease is not None:
            lines.append(f"  参考: リース債務 {fmt(yen_to_million(d.lease_yen))}百万円"
                         f"(含めると {fmt(yen_to_million(d.total + d.lease))}百万円)")
            for i in d.lease_items:
                lines.append(f"    {i.label}: {fmt(i.yen)}円")

    if r.candidates:
        lines.append("候補:")
        for c in r.candidates[:20]:
            lines.append(f"    {c.code}  {c.name}")
        if len(r.candidates) > 20:
            lines.append(f"    ほか {len(r.candidates) - 20}件")
    if r.notes:
        lines.append("注意:")
        lines.extend(f"    ・{n}" for n in r.notes)
    lines.append("※ 数値は確認の補助です。投資の判断は元の資料(決算短信・有価証券報告書)で確かめてください。")
    return "\n".join(lines)
