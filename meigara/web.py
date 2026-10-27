# -*- coding: utf-8 -*-
"""取得結果から、スマホで見やすい1枚の HTML ページを作る(GitHub Pages で公開する)。

外部のファイル(CSS・JavaScript・フォント)は読み込まない。1ファイルで完結させる。
検索サイトに載らないよう noindex を付ける(URL を知っていれば見られることは変わらない)。
"""

import html
import re

from . import report


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _metric(label, value, unit, sub=""):
    sub_html = f'<span class="sub">{_e(sub)}</span>' if sub else ""
    if value == "－":
        unit = ""
    return (f'<div class="m"><div class="ml">{_e(label)}</div>'
            f'<div class="mv">{_e(value)}<small>{_e(unit)}</small></div>{sub_html}</div>')


_STATUS_CLASS = {"成功": "ok", "一部取得できず": "warn", "要確認": "warn", "失敗": "ng"}


def _card(r):
    k, d = r.kabutan, r.debt
    v = report.row_values(r)
    title = f"{r.code} {r.name}".strip() or r.query
    market = f'<span class="mk">{_e(k.market)}</span>' if k and k.market else ""
    price_sub = k.price_time[5:16].replace("T", " ").replace("-", "/") if k and k.price_time else ""
    if k and k.price_note == "終値":
        price_sub += " 終値"
    psr_f = f"予想 {v[5]}倍" if v[5] != "－" else ""
    eq_sub = k.equity_ratio.label if k and k.equity_ratio else ""
    cf_sub = f"{k.operating_cf.label}期" if k and k.operating_cf else ""
    debt_sub = ""
    if not d and k and k.debt_estimate:
        debt_sub = f"概算 {k.debt_estimate.label}"
    elif not d:
        debt_sub = "不明"
    if d:
        debt_sub = f"{d.period_end or d.period_label} {d.basis}"
        if d.lease is not None:
            debt_sub += f" / リース別 {report.fmt(report.yen_to_million(d.lease_yen))}"
    if k and k.source == "EDINET":
        # スマホ版(EDINET だけ): 株価・時価総額・PSR の代わりに売上高を出す
        sales = report.fmt(k.sales_actual.value) if k.sales_actual else "－"
        metrics = "".join([
            _metric("売上高", sales, "百万円", k.sales_actual.label if k.sales_actual else ""),
            _metric("自己資本比率", v[6], "%", eq_sub),
            _metric("営業CF", v[7], "百万円", k.operating_cf.label if k.operating_cf else ""),
            _metric("有利子負債", v[8], "百万円", debt_sub),
        ])
    else:
        metrics = "".join([
            _metric("株価", v[2], "円", price_sub),
            _metric("時価総額", v[3], "億円"),
            _metric("PSR(実績)", v[4], "倍", psr_f),
            _metric("自己資本比率", v[6], "%", eq_sub),
            _metric("営業CF", v[7], "百万円", cf_sub),
            _metric("有利子負債", v[8], "百万円", debt_sub),
        ])
    st = _STATUS_CLASS.get(r.status, "warn")
    # スマホで株探の銘柄ページを開くリンク(職員が自分で開くので、自動取得には当たらない)
    link = ""
    if r.code and re.fullmatch(r"[0-9][0-9A-Z]{3}", r.code):
        link = (f'<a class="lk" href="https://kabutan.jp/stock/?code={_e(r.code)}" '
                f'target="_blank" rel="noopener">株価・時価総額を見る(株探) ›</a>')
    key = f"{r.code} {r.name} {r.query}".upper()
    return (f'<section class="card" data-key="{_e(key)}"><header><h2>{_e(title)}{market}</h2>'
            f'<span class="st {st}">{_e(r.status)}</span></header>'
            f'{link}<div class="grid">{metrics}</div>'
            f'<details><summary>くわしく(出典・計算方法・注意)</summary>'
            f'<pre>{_e(report.detail_text(r))}</pre></details></section>')


CSS = """
:root{--bg:#f4f5f7;--card:#fff;--fg:#1d1f23;--sub:#6b7280;--line:#e5e7eb;
--ok:#0f7b3f;--okbg:#e3f5ea;--warn:#8a5a00;--warnbg:#fff3d6;--ng:#a61b1b;--ngbg:#fde4e4;--acc:#1f5fbf}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--card:#1c1f24;--fg:#e8eaed;--sub:#9aa0a6;
--line:#2d3137;--okbg:#14321f;--ok:#7fd69f;--warnbg:#3a2e10;--warn:#f0c766;--ngbg:#3d1717;--ng:#f19999;--acc:#8ab4f8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Meiryo",sans-serif;line-height:1.5}
.wrap{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:1.25rem;margin:4px 0}
.upd{color:var(--sub);font-size:.85rem;margin-bottom:12px}
html,body{overflow-x:hidden}
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(min(300px,100%),1fr))}
.card,.grid>.m{min-width:0}
.note p{overflow-wrap:anywhere}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.card header{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.card h2{font-size:1.05rem;margin:0}
.mk{font-size:.75rem;color:var(--sub);font-weight:normal;margin-left:6px}
.st{font-size:.75rem;padding:2px 8px;border-radius:999px;white-space:nowrap}
.st.ok{background:var(--okbg);color:var(--ok)}.st.warn{background:var(--warnbg);color:var(--warn)}
.st.ng{background:var(--ngbg);color:var(--ng)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 12px;margin-top:10px}
.ml{font-size:.75rem;color:var(--sub)}
.mv{font-size:1.15rem;font-weight:600;font-variant-numeric:tabular-nums}
.mv small{font-size:.75rem;font-weight:normal;margin-left:2px;color:var(--sub)}
.sub{display:block;font-size:.72rem;color:var(--sub)}
details{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}
summary{cursor:pointer;color:var(--acc);font-size:.85rem}
.lk{display:inline-block;margin-top:6px;color:var(--acc);font-size:.85rem;text-decoration:none;padding:4px 0}
pre{white-space:pre-wrap;word-break:break-all;font-size:.78rem;margin:8px 0 0;font-family:inherit}
.note{color:var(--sub);font-size:.78rem;margin-top:16px}
.bar{display:flex;gap:8px;margin:4px 0 6px}
.bar input{flex:1;min-width:0;font-size:16px;padding:9px 12px;border:1px solid var(--line);border-radius:10px;
background:var(--card);color:var(--fg)}
.bar button{font-size:.9rem;padding:0 14px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg)}
.bar2{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-size:.8rem;color:var(--sub)}
.btn{background:var(--acc);color:#fff;text-decoration:none;padding:7px 12px;border-radius:999px;font-size:.85rem}
@media (prefers-color-scheme:dark){.btn{color:#111}}
.info{background:var(--warnbg);color:var(--warn);border-radius:10px;padding:4px 12px;margin:6px 0 10px;font-size:.85rem}
.info p{margin:6px 0}
"""


# 絞り込み(入力した文字をコード・社名に含む銘柄だけ表示)。外部のファイルは読み込まない。
# 最後に入れた文字はこのスマホにだけ覚えておく(使えない環境でも動くよう try で囲む)。
JS = r"""
(function(){
  var box=document.getElementById('q'), cards=[].slice.call(document.querySelectorAll('.card')),
      cnt=document.getElementById('cnt');
  function norm(s){return (s||'').normalize('NFKC').toUpperCase().trim();}
  function apply(){
    var words=norm(box.value).split(/[\s,、]+/).filter(Boolean), n=0;
    cards.forEach(function(c){
      var k=norm(c.getAttribute('data-key'));
      var show=!words.length||words.some(function(w){return k.indexOf(w)>=0;});
      c.style.display=show?'':'none'; if(show)n++;
    });
    cnt.textContent=n+' / '+cards.length+' 銘柄';
    try{localStorage.setItem('meigara_q',box.value);}catch(e){}
  }
  try{box.value=localStorage.getItem('meigara_q')||'';}catch(e){}
  box.addEventListener('input',apply);
  document.getElementById('clr').addEventListener('click',function(){box.value='';apply();box.focus();});
  apply();
})();
"""


def render_page(results, updated_at, notes=(), manage_url=""):
    """results: StockResult の一覧、updated_at: 表示用の更新日時(文字)
    manage_url: 「銘柄を追加・削除」ボタンの行き先(GitHub の Run workflow の画面)"""
    cards = "".join(_card(r) for r in results) or "<p>銘柄リストが空です。</p>"
    info = ('<div class="info">' + "".join(f"<p>{_e(n)}</p>" for n in notes) + "</div>") if notes else ""
    manage = ""
    if manage_url:
        manage = (f'<a class="btn" href="{_e(manage_url)}" target="_blank" rel="noopener">＋ 銘柄を追加・削除</a>')
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>銘柄情報</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>銘柄情報</h1>
<div class="upd">更新: {_e(updated_at)}(数字の時点は各銘柄に表示)</div>
{info}
<div class="bar">
<input id="q" type="search" placeholder="コード・社名で絞り込み" autocomplete="off">
<button id="clr" type="button">全部</button>
</div>
<div class="bar2"><span id="cnt"></span>{manage}</div>
<div class="cards">{cards}</div>
<div class="note">
<p>出典・時点は各銘柄の「くわしく」に表示しています。有利子負債は、貸借対照表の借入金・社債・CP を足したもの(リース債務は含まない)です。スマホ版は金融庁 EDINET の最新の有価証券報告書・半期報告書から作っているため、株価・時価総額・PSR はありません。</p>
<p>数値は確認の補助です。投資の判断は元の資料(決算短信・有価証券報告書)で確かめてください。</p>
</div></div><script>{JS}</script></body></html>
"""
