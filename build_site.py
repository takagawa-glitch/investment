# -*- coding: utf-8 -*-
"""銘柄リスト.txt の銘柄を取得し、スマホ向けのページ(site/index.html)を作る。

GitHub Actions(.github/workflows/update.yml)から動かす。PC で試すこともできる:
    py build_site.py
    py build_site.py --list 銘柄リスト.txt --out site

APIキーは環境変数 EDINET_API_KEY(GitHub の Secrets)か、このPCの設定ファイルから読む。
画面(ログ)には銘柄コード・結果・件数だけを出し、APIキーや URL は出さない。
"""

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from meigara import codes, edinet, service, web  # noqa: E402

MAX_STOCKS = 50   # 1回に取得する上限(株探に負担をかけないため。3秒間隔で約2.5分)
JST = datetime.timezone(datetime.timedelta(hours=9))


def read_list(path):
    """1行に1銘柄。# から後ろはメモとして無視する。"""
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = [line.split("#", 1)[0] for line in text.splitlines()]
    return codes.split_queries("\n".join(lines))


def main(argv=None):
    ap = argparse.ArgumentParser(description="銘柄情報のページを作る")
    ap.add_argument("--list", default="銘柄リスト.txt", help="銘柄リストのファイル")
    ap.add_argument("--out", default="site", help="ページを書き出すフォルダ")
    args = ap.parse_args(argv)

    queries = read_list(args.list)
    notes = []
    if len(queries) > MAX_STOCKS:
        notes.append(f"銘柄が{len(queries)}件あります。先頭の{MAX_STOCKS}件だけ取得しました")
        queries = queries[:MAX_STOCKS]

    book, note = codes.load_codebook()
    if note:
        notes.append(note)
    key = edinet.load_api_key()   # 無くてよい(有利子負債は株探の倍率から概算する)

    results = []
    for q in queries:
        try:
            r = service.get_stock(q, book, key)
        except Exception as e:   # 1銘柄の失敗でページ全体を止めない
            r = service.StockResult(query=q, status="失敗",
                                    notes=[f"予期しないエラー({type(e).__name__})"])
        results.append(r)
        print(f"{r.code or '-'}\t{r.status}", flush=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    (out / "index.html").write_text(web.render_page(results, now, notes), encoding="utf-8")
    ok = sum(r.status == "成功" for r in results)
    print(f"完了: {len(results)}件(成功 {ok}件)", flush=True)
    # 1件も取れなかったときは失敗として知らせる(GitHub から通知メールが届く)
    return 1 if results and all(r.status == "失敗" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
