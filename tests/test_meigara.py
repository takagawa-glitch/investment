# -*- coding: utf-8 -*-
"""銘柄情報取得ツールのテスト。架空の会社・架空の数値だけを使い、インターネットには接続しない。

実行: py -m unittest discover -s tests -v   (アプリのフォルダで)
"""

import datetime
import os
import re
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meigara import codes, edinet, kabutan, paths, records, report, service  # noqa: E402


# ------------------------------------------------------------ 架空の株探ページ

def kabutan_html(code="9999", name="テスト工業", price="2,345", cap="123.4<span>億円</span>", ratio="0.31"):
    return f"""<html><body>
<h2>ニュース</h2>
<div class="si_i1_1"><h2><span class="inline-block">{code}</span><span class="fs0">　</span>{name}</h2>
<div class="si_i1_1_rbox"><span class="market">東証Ｐ</span><time datetime="2026-10-27T15:00+09:00">15:00</time></div></div>
<div class="si_i1_2"><span class="kabuka">{price}円</span></div>
<table><tr><th class="v_zika1">時価総額</th><td class="v_zika2">{cap}</td></tr></table>
<table><thead><tr><th>決算期</th><th>売上高</th><th>営業益</th><th>経常益</th><th>最終益</th>
<th>修正<br>1株益</th><th>修正<br>1株配</th><th>発表日</th></tr></thead><tbody>
<tr><th scope="row"><span class="kubun1">　&nbsp;</span>2025.03</th><td>4,000</td><td>1</td><td>1</td><td>1</td><td>1</td><td>0</td><td>－</td></tr>
<tr><th scope="row"><span class="kubun1">　&nbsp;</span>2026.03</th><td>5,000</td><td>1</td><td>1</td><td>1</td><td>1</td><td>0</td><td>26/05/15</td></tr>
<tr><th scope="row"><span class="kubun1">予</span>2027.03</th><td>6,170</td><td>1</td><td>1</td><td>1</td><td>1</td><td>0</td><td>26/05/15</td></tr>
<tr><th scope="row">前期比</th><td>+23.4</td><td>1</td><td>1</td><td>1</td><td>1</td><td>0</td><td></td></tr>
</tbody></table>
<table><thead><tr><th>決算期</th><th>営業益</th><th>フリーCF</th><th>営業CF</th><th>投資CF</th><th>財務CF</th><th>現金等<br>残高</th><th>現金<br>比率</th></tr></thead><tbody>
<tr><th scope="row">2025.03</th><td>1</td><td>1</td><td>-120</td><td>1</td><td>1</td><td>1</td><td>1</td></tr>
<tr><th scope="row">2026.03</th><td>1</td><td>1</td><td>850</td><td>1</td><td>1</td><td>1</td><td>1</td></tr>
</tbody></table>
<table><thead><tr><th>決算期</th><th>１株<br />純資産</th><th>自己資本<br />比率</th><th>総資産</th><th>自己資本</th><th>剰余金</th><th>有利子<br />負債倍率</th><th>発表日</th></tr></thead><tbody>
<tr><td colspan="8"></td></tr>
<tr><th scope="row">2026.03</th><td>100</td><td>45.6</td><td>1</td><td>2,000</td><td>1</td><td>{ratio}</td><td>26/05/15</td></tr>
<tr><th scope="row">26.04-06&nbsp;</th><td>－</td><td>47.1</td><td>1</td><td>1</td><td>1</td><td>－</td><td>26/08/14</td></tr>
<tr><th scope="row">26.07-09&nbsp;</th><td>－</td><td>－</td><td>1</td><td>1</td><td>1</td><td>－</td><td>－</td></tr>
</tbody></table></body></html>"""


class TestKabutan(unittest.TestCase):
    def test_parse(self):
        d = kabutan.parse_finance_page(kabutan_html(), "9999")
        self.assertEqual(d.name, "テスト工業")
        self.assertEqual(d.market, "東証Ｐ")
        self.assertEqual(d.price, Decimal("2345"))
        self.assertEqual(d.price_time, "2026-10-27T15:00+09:00")
        self.assertEqual(d.market_cap, Decimal("123.4"))
        self.assertEqual((d.sales_actual.label, d.sales_actual.value), ("2026.03", Decimal("5000")))
        self.assertEqual((d.sales_forecast.label, d.sales_forecast.value), ("2027.03", Decimal("6170")))
        self.assertTrue(d.sales_forecast.forecast)
        self.assertEqual(d.operating_cf.value, Decimal("850"))
        # 値が「－」の行は飛ばし、値がある最も新しい行を使う
        self.assertEqual((d.equity_ratio.label, d.equity_ratio.value), ("26.04-06", Decimal("47.1")))
        self.assertEqual((d.equity_ratio_annual.label, d.equity_ratio_annual.value),
                         ("2026.03", Decimal("45.6")))
        self.assertEqual(d.warnings, [])

    def test_debt_estimate(self):
        # 有利子負債 ≒ 倍率 0.31 × 自己資本 2,000百万円 = 620百万円
        d = kabutan.parse_finance_page(kabutan_html(), "9999")
        self.assertEqual((d.debt_estimate.label, d.debt_estimate.value), ("2026.03", Decimal("620")))
        self.assertEqual(d.debt_ratio.value, Decimal("0.31"))

    def test_debt_unknown(self):
        # 倍率が「－」なら出さない(分かる場合だけ)
        d = kabutan.parse_finance_page(kabutan_html(ratio="－"), "9999")
        self.assertIsNone(d.debt_estimate)

    def test_negative_cf(self):
        html = kabutan_html().replace("<td>850</td>", "<td>-35</td>")
        self.assertEqual(kabutan.parse_finance_page(html, "9999").operating_cf.value, Decimal("-35"))

    def test_trillion_market_cap(self):
        d = kabutan.parse_finance_page(kabutan_html(cap="4.2<span>兆円</span>"), "9999")
        self.assertEqual(d.market_cap, Decimal("42000"))

    def test_not_found(self):
        with self.assertRaises(kabutan.KabutanError):
            kabutan.parse_finance_page("<html><body>該当なし</body></html>", "0000")

    def test_missing_tables_warn(self):
        html = kabutan_html().split("<table><thead>")[0] + "</body></html>"
        d = kabutan.parse_finance_page(html, "9999")
        self.assertIsNone(d.sales_actual)
        self.assertEqual(len(d.warnings), 3)


class TestPsr(unittest.TestCase):
    def test_calc(self):
        # 123.4億円 = 12,340百万円 ÷ 5,000百万円 = 2.468 → 2.47
        self.assertEqual(service.calc_psr(Decimal("123.4"), Decimal("5000")), Decimal("2.47"))

    def test_round_half_up(self):
        # 0.125 → 0.13(四捨五入)
        self.assertEqual(service.calc_psr(Decimal("1.25"), Decimal("1000")), Decimal("0.13"))

    def test_no_sales(self):
        self.assertIsNone(service.calc_psr(Decimal("10"), Decimal("0")))
        self.assertIsNone(service.calc_psr(Decimal("10"), Decimal("-5")))
        self.assertIsNone(service.calc_psr(None, Decimal("5")))


# ------------------------------------------------------------ 銘柄名・コード

CODELIST = (
    "ダウンロード実行日,2026年10月27日現在,件数,4件\n"
    "ＥＤＩＮＥＴコード,提出者種別,上場区分,連結の有無,資本金,決算日,提出者名,提出者名（英字）,提出者名（ヨミ）,所在地,提出者業種,証券コード,提出者法人番号\n"
    '"E99991","内国法人・組合","上場","有","100","3月31日","テスト工業株式会社","Test","テストコウギョウカブシキガイシャ","東京都","機械","99990","1"\n'
    '"E99992","内国法人・組合","上場","有","100","3月31日","株式会社テスト工業ホールディングス","Test","カブシキガイシャテストコウギョウホールディングス","東京都","機械","999A0","2"\n'
    '"E99993","内国法人・組合","上場","無","100","3月31日","株式会社サンプル商事","Sample","カブシキカイシャサンプルショウジ","東京都","卸売業","88880","3"\n'
    '"E99994","内国法人・組合","非上場","無","100","3月31日","非上場テスト株式会社","X","ヒジョウジョウテスト","東京都","機械","","4"\n'
).encode("cp932")


class TestCodes(unittest.TestCase):
    def setUp(self):
        self.book = codes.CodeBook(codes.parse_codelist(CODELIST))

    def test_listed_only(self):
        self.assertEqual(sorted(c.code for c in self.book.companies), ["8888", "9999", "999A"])

    def test_lookup_code(self):
        self.assertEqual(self.book.lookup_code("999a").edinet_code, "E99992")

    def test_name_exact(self):
        self.assertEqual([c.code for c in self.book.search_name("サンプル商事")], ["8888"])
        self.assertEqual([c.code for c in self.book.search_name("サンプルショウジ")], ["8888"])

    def test_name_ambiguous(self):
        # 「テスト」は2社に当たる → 自動で選ばない
        self.assertEqual(len(self.book.search_name("テスト")), 2)

    def test_split_queries(self):
        self.assertEqual(codes.split_queries("９９９９, 999a\nサンプル商事　8888、9999"),
                         ["9999", "999A", "サンプル商事", "8888"])

    def test_resolve(self):
        c, code, cands, msg = service.resolve("テスト", self.book)
        self.assertIsNone(code)
        self.assertEqual(len(cands), 2)
        self.assertIn("候補", msg)
        c, code, cands, msg = service.resolve("サンプル商事", self.book)
        self.assertEqual(code, "8888")
        c, code, cands, msg = service.resolve("存在しない会社", self.book)
        self.assertIsNone(code)
        self.assertIn("見つかりません", msg)
        # コードの形なら一覧に無くても株探で試す
        c, code, cands, msg = service.resolve("1234", self.book)
        self.assertEqual((c, code), (None, "1234"))


# ------------------------------------------------------------ EDINET の有利子負債

HEAD = ["要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別", "期間・時点",
        "ユニットID", "単位", "値"]


def row(el, label, value, ctx="CurrentYearInstant", rel="当期末", cons="連結", pt="時点"):
    return [el, label, ctx, rel, cons, pt, "JPY", "円", value]


class TestDebt(unittest.TestCase):
    def test_consolidated(self):
        rows = [HEAD,
                row("jppfs_cor:Assets", "資産", "9000000000"),
                row("jppfs_cor:ShortTermLoansPayable", "短期借入金", "100000000"),
                row("jppfs_cor:CurrentPortionOfLongTermLoansPayable", "1年内返済予定の長期借入金", "50000000"),
                row("jppfs_cor:LongTermLoansPayable", "長期借入金", "300000000"),
                row("jppfs_cor:BondsPayable", "社債", "200000000"),
                row("jppfs_cor:LeaseObligationsCL", "リース債務", "10000000"),
                row("jppfs_cor:LeaseObligationsNCL", "リース債務", "20000000"),
                # 前期末・個別・期間・内訳(Member)・経営指標は数えない
                row("jppfs_cor:LongTermLoansPayable", "長期借入金", "999", ctx="Prior1YearInstant", rel="前期末"),
                row("jppfs_cor:LongTermLoansPayable", "長期借入金", "888",
                    ctx="CurrentYearInstant_NonConsolidatedMember", cons="個別"),
                row("jppfs_cor:ProceedsFromLongTermLoansPayable", "長期借入れによる収入", "777",
                    ctx="CurrentYearDuration", pt="期間"),
                row("jppfs_cor:ShortTermLoansPayable", "短期借入金", "666",
                    ctx="CurrentYearInstant_SecuredMember"),
                row("jpcrp_cor:InterestBearingDebt", "有利子負債", "555"),
                row("jppfs_cor:AccruedInterest", "未払利息", "444"),
                ]
        d = edinet.compute_debt(rows)
        self.assertEqual(d.basis, "連結")
        self.assertEqual(d.total, Decimal("650000000"))
        self.assertEqual(d.lease, Decimal("30000000"))
        self.assertFalse(d.needs_check)
        self.assertEqual(len(d.items), 4)

    def test_non_consolidated_only(self):
        rows = [HEAD,
                row("jppfs_cor:Assets", "資産", "100", ctx="CurrentYearInstant_NonConsolidatedMember", cons="個別"),
                row("jppfs_cor:ShortTermLoansPayable", "短期借入金", "40",
                    ctx="CurrentYearInstant_NonConsolidatedMember", cons="個別")]
        d = edinet.compute_debt(rows)
        self.assertEqual(d.basis, "個別")
        self.assertEqual(d.total, Decimal("40"))
        self.assertIsNone(d.lease)

    def test_no_debt(self):
        rows = [HEAD, row("jppfs_cor:Assets", "資産", "100")]
        d = edinet.compute_debt(rows)
        self.assertEqual(d.total, Decimal("0"))
        self.assertFalse(d.needs_check)
        self.assertIn("無借金", d.notes[0])

    def test_extension_item_needs_check(self):
        rows = [HEAD, row("jppfs_cor:Assets", "資産", "100"),
                row("jpcrp030000-asr_E99991-000:LoansFromOfficers", "役員借入金", "5")]
        d = edinet.compute_debt(rows)
        self.assertEqual(d.total, Decimal("5"))
        self.assertTrue(d.needs_check)

    def test_interim(self):
        rows = [HEAD, row("jppfs_cor:Assets", "資産", "100", ctx="InterimInstant", rel="当中間期末"),
                row("jppfs_cor:ShortTermLoansPayable", "短期借入金", "7", ctx="InterimInstant", rel="当中間期末")]
        d = edinet.compute_debt(rows)
        self.assertEqual((d.total, d.period_label), (Decimal("7"), "当中間期末"))

    def test_ifrs(self):
        rows = [HEAD, row("jpigp_cor:AssetsIFRS", "資産", "100"),
                row("jpigp_cor:BorrowingsCLIFRS", "借入金（流動）", "11"),
                row("jpigp_cor:BorrowingsNCLIFRS", "借入金（非流動）", "22"),
                row("jpigp_cor:LeaseLiabilitiesCLIFRS", "リース負債（流動）", "3")]
        d = edinet.compute_debt(rows)
        self.assertEqual((d.total, d.lease), (Decimal("33"), Decimal("3")))

    def test_combined_and_separate_needs_check(self):
        rows = [HEAD, row("jppfs_cor:Assets", "資産", "100"),
                row("jpigp_cor:BondsAndBorrowingsCLIFRS", "社債及び借入金", "10"),
                row("jppfs_cor:BondsPayable", "社債", "5")]
        self.assertTrue(edinet.compute_debt(rows).needs_check)

    def test_empty_needs_check(self):
        d = edinet.compute_debt([HEAD])
        self.assertTrue(d.needs_check)


class TestFindReport(unittest.TestCase):
    def test_latest_and_skip_weekend(self):
        today = datetime.date(2026, 10, 26)    # 月曜
        asked = []

        def fake_list(day, key, today=None):
            asked.append(day)
            if day == datetime.date(2026, 10, 22):
                return [
                    {"docID": "S1", "edinetCode": "E99991", "docTypeCode": "160", "csvFlag": "1",
                     "withdrawalStatus": "0", "submitDateTime": "2026-10-22 09:00"},
                    {"docID": "S2", "edinetCode": "E99991", "docTypeCode": "160", "csvFlag": "1",
                     "withdrawalStatus": "0", "submitDateTime": "2026-10-22 15:00"},
                    {"docID": "S3", "edinetCode": "E99991", "docTypeCode": "160", "csvFlag": "1",
                     "withdrawalStatus": "1", "submitDateTime": "2026-10-22 16:00"},   # 取下げ
                    {"docID": "S4", "edinetCode": "E00000", "docTypeCode": "120", "csvFlag": "1",
                     "withdrawalStatus": "0", "submitDateTime": "2026-10-22 17:00"},  # 別の会社
                ]
            return []

        with mock.patch.object(edinet, "list_documents", fake_list):
            doc = edinet.find_latest_report("E99991", "xxxx", today=today)
        self.assertEqual(doc["docID"], "S2")
        self.assertNotIn(datetime.date(2026, 10, 25), asked)   # 日曜は見ない
        self.assertNotIn(datetime.date(2026, 10, 24), asked)   # 土曜は見ない

    def test_cancel(self):
        with mock.patch.object(edinet, "list_documents", lambda *a, **k: []):
            with self.assertRaises(edinet.Cancelled):
                edinet.find_latest_report("E99991", "xxxx", cancelled=lambda: True)


# ------------------------------------------------------------ 表示

class TestReport(unittest.TestCase):
    def test_row_and_detail(self):
        k = kabutan.parse_finance_page(kabutan_html(), "9999")
        d = edinet.compute_debt([HEAD, row("jppfs_cor:Assets", "資産", "1"),
                                 row("jppfs_cor:ShortTermLoansPayable", "短期借入金", "1234500000"),
                                 row("jppfs_cor:LeaseObligationsCL", "リース債務", "500000")])
        r = service.StockResult(query="9999", code="9999", name="テスト工業", kabutan=k, debt=d,
                                psr_actual=service.calc_psr(k.market_cap, k.sales_actual.value))
        vals = report.row_values(r)
        self.assertEqual(vals[2], "2,345")
        self.assertEqual(vals[4], "2.47")
        self.assertEqual(vals[6], "47.1")
        self.assertEqual(vals[8], "1,235")     # 1,234.5百万円 → 四捨五入
        text = report.detail_text(r)
        self.assertIn("リース債務 1百万円", text)
        self.assertIn("26.04-06 時点", text)

    def test_debt_cells(self):
        k = kabutan.parse_finance_page(kabutan_html(), "9999")
        r = service.StockResult(query="9999", code="9999", kabutan=k)
        self.assertEqual(report.debt_cell(r), "約620")
        self.assertIn("倍率 0.31倍 × 自己資本 2,000百万円", report.detail_text(r))
        k2 = kabutan.parse_finance_page(kabutan_html(ratio="－"), "9999")
        r2 = service.StockResult(query="9999", code="9999", kabutan=k2)
        self.assertEqual(report.debt_cell(r2), "－")
        self.assertIn("不明", report.detail_text(r2))

    def test_failed_row(self):
        r = service.StockResult(query="テスト", status="要確認", notes=["候補が2件あります"])
        vals = report.row_values(r)
        self.assertEqual(vals[0], "テスト")
        self.assertIn("候補が2件", report.detail_text(r))


class TestStatus(unittest.TestCase):
    """有利子負債の取り方の順番と、結果の欄。インターネットには接続しない。"""

    def setUp(self):
        self.book = codes.CodeBook(codes.parse_codelist(CODELIST))
        from meigara import irbank
        self.irbank = irbank

    def _run(self, k, irbank_result):
        def fake_irbank(code, progress=None, list_html=None):
            if isinstance(irbank_result, Exception):
                raise irbank_result
            return irbank_result
        with mock.patch.object(kabutan, "fetch", lambda code: k),                 mock.patch.object(self.irbank, "fetch_debt", fake_irbank):
            return service.get_stock("9999", self.book, None)

    def test_irbank_used(self):
        k = kabutan.parse_finance_page(kabutan_html(), "9999")
        d = self.irbank.compute_debt_from_page(irbank_bs_html())
        r = self._run(k, d)
        self.assertEqual((r.status, report.debt_cell(r)), ("成功", "350"))

    def test_estimate_when_irbank_fails(self):
        k = kabutan.parse_finance_page(kabutan_html(), "9999")
        r = self._run(k, self.irbank.IrbankError("開けませんでした"))
        self.assertEqual((r.status, report.debt_cell(r)), ("成功", "約620"))
        self.assertTrue(any("概算" in n for n in r.notes))

    def test_unknown_is_success(self):
        # 分かる場合だけ: 有利子負債が「－」でも、ほかが取れていれば「成功」
        k = kabutan.parse_finance_page(kabutan_html(ratio="－"), "9999")
        r = self._run(k, self.irbank.IrbankError("開けませんでした"))
        self.assertEqual((r.status, report.debt_cell(r)), ("成功", "－"))
        self.assertTrue(any("不明" in n for n in r.notes))

    def test_needs_check(self):
        k = kabutan.parse_finance_page(kabutan_html(), "9999")
        extra = '<tr><td class="indent3"><a href="../LoansFromOfficersXYZ?t=Q4">役員借入金</a></td><td class="value">1</td><td class="value">2</td></tr>'
        r = self._run(k, self.irbank.compute_debt_from_page(irbank_bs_html(extra_rows=extra)))
        self.assertEqual(r.status, "要確認")

# ------------------------------------------------------------ スマホ向けページ

class TestWeb(unittest.TestCase):
    def test_page(self):
        from meigara import web
        k = kabutan.parse_finance_page(kabutan_html(name="テスト<工業>"), "9999")
        r = service.StockResult(query="9999", code="9999", name=k.name, kabutan=k,
                                psr_actual=Decimal("2.47"))
        bad = service.StockResult(query="ダミー", status="失敗", notes=["株探: 接続できません"])
        page = web.render_page([r, bad], "2026-10-27 15:50", ["注意のテスト"])
        self.assertIn('name="robots" content="noindex', page)
        self.assertIn("テスト&lt;工業&gt;", page)     # 文字はエスケープする
        self.assertNotIn("テスト<工業>", page)
        self.assertIn("2.47", page)
        self.assertIn('class="st ng"', page)
        self.assertIn("注意のテスト", page)
        self.assertNotIn("<script", page)           # 外部・スクリプトを読み込まない

    def test_read_list(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import build_site
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, "list.txt")
            p.write_text("# メモ\n9999  # テスト工業\n\n８８８８,9999\n", encoding="utf-8")
            self.assertEqual(build_site.read_list(p), ["9999", "8888"])

    def test_api_key_env(self):
        with mock.patch.dict(os.environ, {"EDINET_API_KEY": " xxxx "}):
            self.assertEqual(edinet.load_api_key(), "xxxx")


# ------------------------------------------------------------ 処理記録

class TestRecords(unittest.TestCase):
    def test_write_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime.datetime(2026, 10, 27, 9, 0, 0)
            self.assertTrue(records.write_record("銘柄情報の取得", "9999 テスト|工業", "成功",
                                                 "a\nb", now=now, folder=tmp))
            path = records.log_file_path(now, tmp)
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], records.LOG_HEADER)
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[1].count(" | "), 6)
            # 古いファイルだけ消す。処理記録以外は消さない
            for n in ("処理記録_2025-09_PC1.log", "処理記録_2025-10_PC1.log", "メモ.txt"):
                Path(tmp, n).write_text("x", encoding="utf-8")
            removed = records.cleanup_old_logs(datetime.date(2026, 10, 27), tmp)
            self.assertEqual(removed, ["処理記録_2025-09_PC1.log"])
            self.assertTrue(Path(tmp, "メモ.txt").exists())


# ------------------------------------------------------------ IR BANK の貸借対照表(架空)

def irbank_bs_html(unit="百万円", extra_rows=""):
    def r(label, el, *vals, bold=False):
        cls = "indent3 bold" if bold else "indent3"
        a = f'<a href="../{el}?t=Q4">{label}</a>' if el else label + " "
        return f'<tr><td class="{cls}">{a}</td>' + "".join(f'<td class="value">{v}</td>' for v in vals) + "</tr>"
    rows = "".join([
        r("資産の部", None, "", "", bold=True),
        r("短期貸付金", "ShortTermLoansReceivable", "999", "999"),
        r("資産の部合計", "Assets", "5,000", "5,100"),
        r("負債の部", None, "", "", bold=True),
        r("短期借入金", "ShortTermLoansPayable", "100", "120"),
        r("１年内返済予定の長期借入金", "CurrentPortionOfLongTermLoansPayable", "30", "30"),
        r("１年内償還予定の社債", "CurrentPortionOfBonds", "10", "10"),
        r("リース債務", "LeaseObligationsCL", "5", "6"),
        r("未払利息", "AccruedInterest", "1", "1"),
        r("社債", "BondsPayable", "200", "190"),
        r("長期借入金", "LongTermLoansPayable", "300", "-"),
        extra_rows,
        r("負債の部合計", "Liabilities", "800", "800"),
        r("純資産の部", None, "", "", bold=True),
        r("新株予約権付社債", "BondsWithSubscriptionRightsToShares", "77", "77"),
    ])
    return (f'<h1>テスト工業</h1><a href="#">貸借（連結）</a>'
            f'<table id="c_bs1"><caption><ul><li>前期</li></ul>貸借対照表<span>（{unit}）</span></caption>'
            f'<tr><th>勘定科目</th><th>2026年3月31日</th><th>2026年6月30日</th></tr>{rows}</table>')


class TestIrbank(unittest.TestCase):
    def test_sum(self):
        from meigara import irbank
        d = irbank.compute_debt_from_page(irbank_bs_html())
        # 最新の列(2026年6月30日): 120 + 30 + 10 + 190 = 350百万円(長期借入金は「-」なので数えない)
        self.assertEqual(d.total, Decimal("350000000"))
        self.assertEqual(d.lease, Decimal("6000000"))
        self.assertEqual((d.period_end, d.basis, d.source), ("2026年6月30日", "連結", "IR BANK"))
        self.assertFalse(d.needs_check)
        self.assertEqual([i.label for i in d.items],
                         ["短期借入金", "1年内返済予定の長期借入金", "1年内償還予定の社債", "社債"])

    def test_thousand_yen(self):
        from meigara import irbank
        d = irbank.compute_debt_from_page(irbank_bs_html(unit="千円"))
        self.assertEqual(d.total, Decimal("350000"))

    def test_no_debt(self):
        from meigara import irbank
        html = re.sub(r'<tr><td class="indent3"><a href="\.\./(ShortTermLoansPayable|CurrentPortion\w+|BondsPayable|LongTermLoansPayable)\?.*?</tr>',
                      "", irbank_bs_html())
        d = irbank.compute_debt_from_page(html)
        self.assertEqual(d.total, Decimal("0"))
        self.assertIn("無借金", d.notes[0])

    def test_company_item_needs_check(self):
        from meigara import irbank
        extra = '<tr><td class="indent3"><a href="../LoansFromOfficersXYZ?t=Q4">役員借入金</a></td><td class="value">1</td><td class="value">2</td></tr>'
        d = irbank.compute_debt_from_page(irbank_bs_html(extra_rows=extra))
        self.assertTrue(d.needs_check)
        self.assertEqual(d.total, Decimal("352000000"))

    def test_no_table(self):
        from meigara import irbank
        with self.assertRaises(irbank.IrbankError):
            irbank.compute_debt_from_page("<html>エラー</html>")

    def test_doc_candidates(self):
        from meigara import irbank
        html = ('<p class="pad"><a title="9999 テスト工業 | 貸借対照表 - 第1四半期決算短信 - 2026年8月1日 15:00提出" href="0812AAA/bs">x</a></p>'
                '<table class="cs"><tr><td><a title="9999 テスト工業 | 貸借対照表 - 有価証券報告書-第1期 - 2025年6月1日提出" href="S100OLD/bs">2025/03</a></td></tr>'
                '<tr><td><a title="9999 テスト工業 | 貸借対照表 - 有価証券報告書-第2期 - 2026年6月1日提出" href="S100NEW/bs">2026/03</a></td></tr></table>')
        self.assertEqual([d for d, _ in irbank.doc_candidates(html)], ["0812AAA", "S100NEW", "S100OLD"])
        self.assertEqual(irbank.doc_candidates(html)[0][1], "第1四半期決算短信(2026年8月1日 15:00提出)")

    def test_fallback_to_older_doc(self):
        from meigara import irbank, net
        list_html = ('<p class="pad"><a title="a | b - 新しい短信 - 2026年8月1日" href="NEW1/bs">x</a></p>'
                     '<table class="cs"><tr><td><a title="a | b - 古い有報 - 2025年6月1日" href="OLD1/bs">y</a></td></tr></table>')

        def fake_get(path, retries=2):
            if path.endswith("E99991/bs"):
                return list_html
            if "NEW1" in path:
                raise net.FetchError("IR BANKから取得できませんでした(応答コード 500)", status=500)
            return irbank_bs_html()

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"MEIGARA_DATA_DIR": tmp}), \
                mock.patch.object(irbank, "_get", fake_get):
            d = irbank.fetch_debt("E99991")
            self.assertEqual(d.doc_id, "OLD1")
            self.assertTrue(any("新しい短信" in n for n in d.notes))
            # 2回目: 開けなかった書類は1日試さず、保存した結果を使う(取りにいくのは一覧だけ)
            calls = []
            with mock.patch.object(irbank, "_get", lambda p, retries=2: calls.append(p) or fake_get(p)):
                d2 = irbank.fetch_debt("E99991")
            self.assertEqual(calls, ["E99991/bs"])
            self.assertEqual(d2.total, d.total)



# ------------------------------------------------------------ IR BANK の株価・業績(GitHub 版。架空)

QUOTE_HTML = (
    '<div>9999 テスト工業 9999 2026/10/26 時価 123億円</div>'
    '<h2 id="c_Price">株価<span class="co_sm">（10/26）</span></h2><dl class="gdl">'
    '<dt>前日終値<span class="co_sm"> (10/23)</span></dt><dd><span class="text">2,300</span></dd>'
    '<dt>終値<span class="co_br">&thinsp;-1.24%</span></dt><dd><span class="dwn"></span><span class="text">2,345</span></dd></dl>'
    '<h2 id="c_Valuation">株式指標</h2><dl class="gdl"><dt>時価総額</dt><dd><span class="ratio"></span>'
    '<span class="text">123億4567万</span></dd></dl>')

LIST_HTML = (
    '<table class="cs"><caption>財務履歴（百万円）</caption><thead><tr><th>年度</th><th>総資産</th>'
    '<th class="lf">自己資本比率</th></tr></thead><tbody>'
    '<tr><td class="lf weaken">2025/03</td><td class="rt">1,000</td><td class="rt">40.0%</td></tr>'
    '<tr><td class="lf weaken"><a title="9999 テスト工業 | 貸借対照表 - 有価証券報告書-第2期 - 2026年6月1日提出" href="S100AAA/bs">2026/03</a></td><td class="rt">1,100</td><td class="rt">45.6%</td></tr>'
    '<tr><td class="lf weaken"><a title="9999 テスト工業 | 貸借対照表 - 第1四半期決算短信 - 2026年8月1日提出" href="0812BBB/bs">2026/06</a></td><td class="rt">1,200</td><td class="rt">47.1%</td></tr>'
    '</tbody></table>')

RESULTS_HTML = (
    '<table class="bar bs"><thead><tr><th>年度</th><th><a>売上</a></th><th>営利</th></tr></thead><tbody>'
    '<tr><td>2025/03</td><td class="rt"><span class="text">40億</span></td><td>-</td></tr>'
    '<tr><td><a href="x">2026/03</a></td><td class="rt"><span class="text">50億</span></td><td>-</td></tr>'
    '<tr><td><a href="y">2027/03</a><span class="co_gr weaken">予</span></td><td class="rt"><span class="text">61.7億</span></td><td>-</td></tr>'
    '</tbody></table>')

CF_HTML = (
    '<table class="cs"><caption>キャッシュ・フロー計算書 (百万円)</caption><thead><tr><th>年度</th><th>四半期</th>'
    '<th class="lf">営業CF</th><th class="lf">投資CF</th></tr></thead><tbody>'
    '<tr><td class="lf weaken" rowspan="1">2025年<br>3月期<br>連結</td><td class="lf weaken">通期</td>'
    '<td class="rt odd">-120</td><td class="rt odd">-5</td></tr>'
    '<tr><td class="lf weaken" rowspan="2">2026年<br>3月期<br>連結</td><td class="lf weaken"><a>2Q</a></td>'
    '<td class="rt odd">400</td><td class="rt odd">-3</td></tr>'
    '<td class="lf weaken"><a>通期</a></td><td class="rt odd">850</td><td class="rt odd">-7</td></tr>'
    '</tbody></table>')


class TestIrbankMarket(unittest.TestCase):
    def setUp(self):
        from meigara import irbank
        self.irbank = irbank

    def test_amount(self):
        f = self.irbank.parse_amount_million
        self.assertEqual(f("56億6119万"), Decimal("5661.19"))
        self.assertEqual(f("20億"), Decimal("2000"))
        self.assertEqual(f("364百万"), Decimal("364"))
        self.assertEqual(f("-57百万"), Decimal("-57"))
        self.assertEqual(f("4兆2381億"), Decimal("4238100"))
        self.assertEqual(f("1,234"), Decimal("1234"))
        self.assertIsNone(f("-"))
        self.assertIsNone(f("赤字"))

    def test_quote(self):
        price, date, cap = self.irbank.parse_quote_page(QUOTE_HTML)
        self.assertEqual((price, date, cap), (Decimal("2345"), "2026-10-26", Decimal("123.5")))

    def test_tables(self):
        self.assertEqual(self.irbank.parse_equity_ratio(LIST_HTML)[-1], ("2026/06", Decimal("47.1")))
        self.assertEqual(self.irbank.parse_sales(RESULTS_HTML),
                         [("2025/03", Decimal("4000"), False), ("2026/03", Decimal("5000"), False),
                          ("2027/03", Decimal("6170"), True)])
        self.assertEqual(self.irbank.parse_operating_cf(CF_HTML),
                         [("2025/03", Decimal("-120")), ("2026/03", Decimal("850"))])

    def test_cf_oku_unit(self):
        # 大きな会社は単位が億円になる → 百万円にそろえる
        html = CF_HTML.replace('<caption>キャッシュ・フロー計算書 (百万円)</caption>',
                               '<caption class="em100">キャッシュ・フロー計算書 (億円)<ul><li>もっとみる</li></ul></caption>')
        self.assertEqual(self.irbank.parse_operating_cf(html)[-1], ("2026/03", Decimal("85000")))

    def test_fmt_no_extra_zeros(self):
        self.assertEqual(report.fmt(Decimal("3080000.00")), "3,080,000")
        self.assertEqual(report.fmt(Decimal("56.6")), "56.6")

    def test_fetch_market(self):
        pages = {"9999": QUOTE_HTML, "E99991/bs": LIST_HTML, "E99991/results": RESULTS_HTML,
                 "E99991/cf": CF_HTML}
        with mock.patch.object(self.irbank, "_get", lambda p, retries=2: pages[p]):
            d, list_html = self.irbank.fetch_market("9999", "E99991", "テスト工業株式会社")
        self.assertEqual(list_html, LIST_HTML)
        self.assertEqual((d.source, d.price_note, d.price), ("IR BANK", "終値", Decimal("2345")))
        self.assertEqual(d.sales_actual.value, Decimal("5000"))
        self.assertEqual(d.sales_forecast.value, Decimal("6170"))
        self.assertEqual((d.equity_ratio.label, d.equity_ratio_annual.label), ("2026/06", "2026/03"))
        self.assertEqual(d.operating_cf.value, Decimal("850"))
        self.assertEqual(d.warnings, [])
        # PSR は 123.5億円 ÷ 5,000百万円 = 2.47
        self.assertEqual(service.calc_psr(d.market_cap, d.sales_actual.value), Decimal("2.47"))
        text = report.detail_text(service.StockResult(query="9999", code="9999", kabutan=d))
        self.assertIn("2026-10-26 時点・終値/IR BANK", text)

    def test_service_irbank_reuses_list_page(self):
        book = codes.CodeBook(codes.parse_codelist(CODELIST))
        pages = {"9999": QUOTE_HTML, "E99991/bs": LIST_HTML, "E99991/results": RESULTS_HTML,
                 "E99991/cf": CF_HTML, "E99991/0812BBB/bs": irbank_bs_html()}
        calls = []
        with tempfile.TemporaryDirectory() as tmp,                 mock.patch.dict(os.environ, {"MEIGARA_DATA_DIR": tmp}),                 mock.patch.object(self.irbank, "_get", lambda p, retries=2: calls.append(p) or pages[p]):
            r = service.get_stock("9999", book, None, source="irbank")
        self.assertEqual(r.status, "成功")
        self.assertEqual(report.debt_cell(r), "350")
        self.assertEqual(calls.count("E99991/bs"), 1)   # 財務状況ページは1回だけ

    def test_service_irbank_all_failed(self):
        from meigara import net
        book = codes.CodeBook(codes.parse_codelist(CODELIST))

        def boom(p, retries=2):
            raise net.FetchError("IR BANKから取得できませんでした(応答コード 403)", status=403)
        with tempfile.TemporaryDirectory() as tmp,                 mock.patch.dict(os.environ, {"MEIGARA_DATA_DIR": tmp}),                 mock.patch.object(self.irbank, "_get", boom):
            r = service.get_stock("9999", book, None, source="irbank")
        self.assertEqual(r.status, "失敗")
        self.assertTrue(any("403" in n for n in r.notes))



# ------------------------------------------------------------ EDINET の経営指標(スマホ版。架空)

def srow(el, label, value, ctx="CurrentYearDuration", rel="当期", pt="期間"):
    return [f"jpcrp_cor:{el}", f"{label}、経営指標等", ctx, rel, "その他", pt, "JPY", "円", value]


class TestEdinetSummary(unittest.TestCase):
    def test_consolidated_preferred(self):
        rows = [HEAD,
                srow("NetSalesSummaryOfBusinessResults", "売上高", "5000000000"),
                srow("NetSalesSummaryOfBusinessResults", "売上高", "999", ctx="CurrentYearDuration_NonConsolidatedMember"),
                srow("NetSalesSummaryOfBusinessResults", "売上高", "888", ctx="Prior1YearDuration", rel="前期"),
                srow("EquityToAssetRatioSummaryOfBusinessResults", "自己資本比率", "0.456",
                     ctx="CurrentYearInstant", rel="当期末", pt="時点"),
                srow("NetCashProvidedByUsedInOperatingActivitiesSummaryOfBusinessResults",
                     "営業活動によるキャッシュ・フロー", "850000000")]
        s = edinet.compute_summary(rows)
        self.assertEqual((s["basis"], s["sales"], s["operating_cf"]), ("連結", "5000000000", "850000000"))
        self.assertEqual(Decimal(s["equity_ratio"]), Decimal("45.6"))

    def test_non_consolidated_and_operating_revenue(self):
        nc = "CurrentYearDuration_NonConsolidatedMember"
        rows = [HEAD, srow("OperatingRevenue1SummaryOfBusinessResults", "営業収益", "700", ctx=nc)]
        s = edinet.compute_summary(rows)
        self.assertEqual((s["basis"], s["sales"], s["sales_label"]), ("個別", "700", "営業収益"))
        self.assertNotIn("equity_ratio", s)

    def test_service_edinet_mode(self):
        book = codes.CodeBook(codes.parse_codelist(CODELIST))
        d = edinet.compute_debt([HEAD, row("jppfs_cor:Assets", "資産", "1"),
                                 row("jppfs_cor:ShortTermLoansPayable", "短期借入金", "120000000")])
        d.summary = {"basis": "連結", "v": edinet.SUMMARY_VERSION, "sales": "5000000000",
                     "equity_ratio": "45.6", "operating_cf": "850000000"}
        d.doc_type, d.period_end = "有価証券報告書", "2026-03-31"
        with mock.patch.object(edinet, "find_latest_report", lambda *a, **k: {"docID": "S1"}),                 mock.patch.object(edinet, "fetch_debt", lambda doc, key: d):
            r = service.get_stock("9999", book, "xxxx", source="edinet")
        self.assertEqual(r.status, "成功")
        k = r.kabutan
        self.assertEqual((k.source, k.sales_actual.value, k.sales_actual.label), ("EDINET", Decimal("5000"), "2026/03 通期"))
        self.assertEqual((k.equity_ratio.value, k.operating_cf.value), (Decimal("45.6"), Decimal("850")))
        self.assertIsNone(r.psr_actual)
        self.assertEqual(report.debt_cell(r), "120")
        from meigara import web
        page = web.render_page([r], "2026-10-27 15:50")
        self.assertIn("売上高", page)
        self.assertNotIn(">株価<", page)
        self.assertIn('href="https://kabutan.jp/stock/?code=9999"', page)
        self.assertIn("スマホ版では取得していません", report.detail_text(r))

    def test_service_edinet_no_key(self):
        book = codes.CodeBook(codes.parse_codelist(CODELIST))
        r = service.get_stock("9999", book, None, source="edinet")
        self.assertEqual(r.status, "失敗")
        self.assertTrue(any("APIキー" in n for n in r.notes))


if __name__ == "__main__":
    unittest.main()
