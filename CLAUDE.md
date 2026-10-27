# 銘柄情報取得ツール(開発者・Claude 向け)

銘柄コードまたは銘柄名を入れると、WEB から株価・PSR・自己資本比率・営業CF・有利子負債を取得して画面に一覧表示する。
**外部と通信するツール**(共通ルール「個人情報の扱い」の例外。作成はユーザー確認済み 2026-10-27)。顧問先のデータは扱わない。

## フォルダ構成

```
investment/
├─ 銘柄情報取得.pyw      起動用(起動エラーを画面に出す)
├─ build_site.py        スマホ向けページを作る(GitHub Actions から実行)
├─ 銘柄リスト.txt        スマホ向けページに載せる銘柄
├─ .github/workflows/update.yml  自動更新と GitHub Pages への公開
├─ .gitignore           許可リスト方式
├─ meigara/
│  ├─ app.py            画面(tkinter)。取得は別スレッド、queue で画面に渡す
│  ├─ service.py        1銘柄の取得をまとめる(resolve / get_stock / calc_psr)
│  ├─ kabutan.py        株探「決算」ページの読み取り(parse_finance_page)
│  ├─ edinet.py         EDINET API: 最新報告書の検索と有利子負債の計算(compute_debt)。科目の判定 classify_item / check_items は IR BANK と共通
│  ├─ irbank.py         IR BANK の書類ごとの貸借対照表を読んで足し算(compute_debt_from_page)
│  ├─ codes.py          EDINETコード一覧で 銘柄名 ↔ 銘柄コード ↔ EDINETコード
│  ├─ report.py         表の1行・詳細欄の文字
│  ├─ web.py            スマホ向けページの HTML(外部ファイルなし・ライト/ダーク対応)
│  ├─ net.py            urllib での取得(サイトごとの間隔あけ・再試行)
│  ├─ records.py        処理記録(write_record / cleanup_old_logs)
│  ├─ startup_error.py  起動・操作中のエラー表示と error.log
│  └─ paths.py          保存先
└─ tests/test_meigara.py
```

## スマホ向けページ(GitHub Pages)

- リポジトリ: https://github.com/takagawa-glitch/investment (**公開**。ユーザーが公開でよいと判断 2026-10-27)
- `build_site.py` が `銘柄リスト.txt` を読み、`site/index.html`(`meigara/web.py`)を作る。
- **株探(405)・IR BANK(403)は GitHub のサーバーからの取得を拒否する(2026-10-27 確認)。** 拒否をすり抜ける工夫はしない(規約の「運営を妨げる行為」に当たるおそれ)。Google 検索結果の自動取得も規約違反なのでしない。
- GitHub では `--source edinet`(`service._from_edinet`): EDINET の最新の有報・半期報告書から、売上高・自己資本比率・営業CF(`edinet.compute_summary`: 「主要な経営指標等の推移」の項目)と有利子負債を出す。株価・時価総額・PSR は出さない(ユーザー判断: 検索すれば分かる)。
- `compute_summary` の読み方を変えたら `SUMMARY_VERSION` を上げる(保存した古い結果を使わない)。経営指標の「連結・個別」列は「その他」になるので、コンテキストIDの NonConsolidatedMember で見分ける。
- `--source irbank`(IR BANK から株価・業績)も残してあるが、GitHub からは使えない。PC で試すとき用。
- `.github/workflows/update.yml`: 平日 12:05・15:50 JST、`銘柄リスト.txt` 等の push、手動実行で動き、Pages に公開。
- スマホのブラウザから EDINET を直接見に行く作りはできない(EDINET が CORS を許可していない。キーをページに入れると誰でも見られる)。代わりに、ページの「＋ 銘柄を追加・削除」→ GitHub の Run workflow(入力欄 add/remove)→ `build_site.update_list` で `銘柄リスト.txt` を更新・コミット(github-actions[bot])→ ページを作り直す。入力は環境変数で渡し、コードの形か1社に決まる社名だけを受け付ける。
- ページの絞り込み欄は外部ファイルなしの小さな JavaScript(`web.JS`)。入力した文字は localStorage に記憶(使えなくても動く)。
- APIキーは GitHub の Secrets `EDINET_API_KEY`。キャッシュは `MEIGARA_DATA_DIR` を actions/cache で引き継ぐ。
- 1回の上限は50銘柄(`MAX_STOCKS`)。株探への負担を増やす変更(回数・銘柄数を増やす)はユーザーに確認する。
- ページには noindex を付けるが、URL を知っていれば誰でも見られる。公開リポジトリなので、処理記録・ログ・キー・キャッシュは `.gitignore`(許可リスト方式)で除外している。

## 取得元とルール

| 項目 | 取得元 | 備考 |
|---|---|---|
| 株価・時価総額 | 株探 `https://kabutan.jp/stock/finance?code=XXXX` | 15分遅れ。時価総額は億円 |
| 売上高(PSR用) | 同上(通期の業績推移) | 実績の最新期と「予」の期 |
| 自己資本比率 | 同上(財務【実績】) | 値がある最も新しい行(四半期のことあり)＋最新の通期 |
| 営業CF | 同上(キャッシュフロー推移) | 最新の通期 |
| 有利子負債 | ①EDINET API v2(APIキーがあるとき) ②IR BANK `https://irbank.net/<EDINETコード>/<書類ID>/bs` ③株探の有利子負債倍率×自己資本(概算) ④「－」 | ユーザーの希望(2026-10-27): 貸借対照表から自分で足し算。分かる場合だけ出し、「－」は正常扱い |
| 銘柄名→コード | EDINETコード一覧 Edinetcode.zip(キー不要) | 7日ごとに取り直し |

- 株探は 1銘柄1ページ。robots.txt の `Crawl-delay: 3` に合わせ3秒以上あける(`kabutan.CRAWL_DELAY`)。
- **Yahoo!ファイナンスは使わない**(ガイドラインで自動取得を禁止)。
- 株探の利用規約(https://info.kabutan.jp/terms-all/)はユーザーが確認済み(2026-10-27)。禁止は「本サイトのサーバーに不当な負担をかける行為や本サービスの運営を妨げる行為」で、負担をかけない程度の取得なら可。間隔を短くしたり、自動で大量に取りにいく作りにはしない。
- IR BANK: robots.txt は Allow: /。利用規約のページは見つからない。3秒以上あける。書類ページが 500/502 になる会社がある(例: 554A の最新2件)ため、新しい順に最大3件試し、開けなかった書類は1日試さない(`cache\irbank\開けなかった書類.json`)。計算結果は書類ごとに保存。
- EDINET へは1秒以上あける(`edinet.INTERVAL`)。書類一覧は日付ごとに `%LOCALAPPDATA%\銘柄情報取得\cache\edinet_list\` に保存(今日の分は保存しない)。土日は見ない。
- エラー文・処理記録・error.log に URL を書かない(EDINET の URL に APIキーが入る)。

## 計算のルール

- PSR = 時価総額(億円)×100 ÷ 売上高(百万円)。小数第2位で四捨五入(表示用)。売上高が0以下・不明なら計算しない。
- 有利子負債 = 貸借対照表の期末時点(相対年度が「当…末」)で、項目名に「借入金・社債・コマーシャル・ペーパー」を含む項目の合計。
  - 連結があれば連結、無ければ個別。Member 付きのコンテキスト(セグメント・担保などの内訳)は除く。`jpcrp_cor`(経営指標)は除く。
  - 「合計・利息・未払・保証・引当・発行費」などを含む項目は除く(`_EXCLUDE_RE`)。
  - **リース債務・リース負債は合計に含めず、別に表示**(会社によって扱いが分かれるため)。
  - 会社独自の項目を含めたとき、まとめた項目と個別の項目が並ぶときは「要確認」。
- 円→百万円は整数に四捨五入(表示用)。詳細欄には円の金額も出す。
- 名前で複数の会社が当たったときは自動で選ばず「要確認」にして候補を出す。

## 保存先

| もの | 場所 |
|---|---|
| EDINET APIキー | `%LOCALAPPDATA%\銘柄情報取得\edinet_api_key.txt`(Box に置かない) |
| キャッシュ | `%LOCALAPPDATA%\銘柄情報取得\cache\`(コード一覧・書類一覧・有利子負債の計算結果) |
| 処理記録 | `処理記録\処理記録_YYYY-MM_<PC名>.log`(記録: 1銘柄ごとの取得結果、全体の件数、APIキー設定。キーは書かない。顧問先は「-」、対象者は銘柄コード＋銘柄名) |
| error.log | `ログ\error_<PC名>.log`、書けなければ `%LOCALAPPDATA%\銘柄情報取得\error.log` |

## テスト

```
py -m unittest discover -s tests -v
```

架空の会社(テスト工業 9999 など)と架空の数値だけを使い、インターネットには接続しない。処理記録は一時フォルダに書く。
株探のページの作りが変わると読み取れなくなるので、取得できないと言われたら、まず実際のページの表の見出しを確認する。

## 未対応・今後

- 使い方PDF(docs/)はまだ作っていない。
- EDINET の実データでの確認は、APIキー登録後に行う。
- push の前に、`git ls-files` で許可したファイルだけか確認する(共通ルールの「公開前のセキュリティ最終チェック」)。
