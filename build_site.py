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

MAX_STOCKS = 50   # 1回に取得する上限(相手のサイトに負担をかけないため。IR BANK は1銘柄4ページ・3秒間隔)
JST = datetime.timezone(datetime.timedelta(hours=9))


def read_list(path):
    """1行に1銘柄。# から後ろはメモとして無視する。"""
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = [line.split("#", 1)[0] for line in text.splitlines()]
    return codes.split_queries("\n".join(lines))


def update_list(path, add_text, remove_text, book):
    """銘柄リストに追加・削除する(スマホから GitHub の「Run workflow」で指定されたもの)。
    社名で指定されたときは、候補が1社だけならそのコードにする(複数なら追加しない)。
    戻り値: (変更したか, 画面に出す注意の一覧)"""
    notes = []
    p = Path(path)
    lines = p.read_text(encoding="utf-8-sig").splitlines() if p.exists() else []

    def code_of(line):
        q = codes.normalize_input(line.split("#", 1)[0])
        return q if codes.CODE_RE.match(q) else None

    def resolve(q):
        if codes.CODE_RE.match(q):
            return q, (book.lookup_code(q).name if book.lookup_code(q) else "")
        found = book.search_name(q)
        if len(found) == 1:
            return found[0].code, found[0].name
        notes.append(f"「{q}」は" + ("見つかりませんでした" if not found else
                     f"候補が{len(found)}件あるため追加・削除していません。銘柄コードで指定してください"))
        return None, ""

    changed = False
    have = {code_of(l) for l in lines} - {None}
    for q in codes.split_queries(remove_text or ""):
        code, _ = resolve(q)
        if code and code in have:
            lines = [l for l in lines if code_of(l) != code]
            have.discard(code)
            changed = True
            notes.append(f"{code} を一覧から外しました")
    for q in codes.split_queries(add_text or ""):
        code, name = resolve(q)
        if code and code not in have:
            lines.append(f"{code}  # {name}".rstrip(" #"))
            have.add(code)
            changed = True
            notes.append(f"{code} {name} を追加しました")
    if changed:
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description="銘柄情報のページを作る")
    ap.add_argument("--list", default="銘柄リスト.txt", help="銘柄リストのファイル")
    ap.add_argument("--out", default="site", help="ページを書き出すフォルダ")
    ap.add_argument("--source", default="kabutan", choices=["kabutan", "irbank", "edinet"],
                    help="株価・業績の取得元(GitHub では株探・IR BANK が使えないため edinet)")
    ap.add_argument("--add", default="", help="銘柄リストに追加する銘柄(コードか社名。空白・カンマ区切り)")
    ap.add_argument("--remove", default="", help="銘柄リストから外す銘柄")
    ap.add_argument("--manage-url", default="", help="ページの「銘柄を追加・削除」ボタンの行き先")
    args = ap.parse_args(argv)

    book, note = codes.load_codebook()
    notes = [note] if note else []
    if args.add.strip() or args.remove.strip():
        _, msgs = update_list(args.list, args.add, args.remove, book)
        notes.extend(msgs)
        for m in msgs:
            print(m, flush=True)

    queries = read_list(args.list)
    if len(queries) > MAX_STOCKS:
        notes.append(f"銘柄が{len(queries)}件あります。先頭の{MAX_STOCKS}件だけ取得しました")
        queries = queries[:MAX_STOCKS]

    key = edinet.load_api_key()   # 無くてよい(有利子負債は株探の倍率から概算する)

    results = []
    for q in queries:
        try:
            r = service.get_stock(q, book, key, source=args.source)
        except Exception as e:   # 1銘柄の失敗でページ全体を止めない
            r = service.StockResult(query=q, status="失敗",
                                    notes=[f"予期しないエラー({type(e).__name__})"])
        results.append(r)
        print(f"{r.code or '-'}\t{r.status}", flush=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    (out / "index.html").write_text(web.render_page(results, now, notes, manage_url=args.manage_url), encoding="utf-8")
    ok = sum(r.status == "成功" for r in results)
    print(f"完了: {len(results)}件(成功 {ok}件)", flush=True)
    # 1件も取れなかったときは失敗として知らせる(GitHub から通知メールが届く)
    return 1 if results and all(r.status == "失敗" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
