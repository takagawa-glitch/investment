# -*- coding: utf-8 -*-
"""EDINET(金融庁)から有利子負債を求める。

流れ:
1. 書類一覧API(日付ごと)を今日からさかのぼり、その会社の最新の
   有価証券報告書・半期報告書・四半期報告書を探す。
   日付ごとの一覧はこのPCに保存し、次からは取りにいかない(今日の分は保存しない)。
   EDINET は土日祝・年末年始は受け付けないため、土日は飛ばす。
2. 見つけた書類の CSV(type=5、XBRL を金融庁が CSV にしたもの)を取得し、
   貸借対照表の期末時点の「借入金」「社債」「コマーシャル・ペーパー」を合計する。
   リース債務は会社によって有利子負債に含める・含めないが分かれるため、合計とは別に表示する。

APIキー(Subscription-Key)は %LOCALAPPDATA%\\銘柄情報取得\\edinet_api_key.txt に置く。
キーが入る URL は、画面・処理記録・error.log に書かない。
"""

import csv
import datetime
import io
import json
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field, asdict
from decimal import Decimal, InvalidOperation

from . import net, paths

API = "https://api.edinet-fsa.go.jp/api/v2"
INTERVAL = 1.0          # EDINET へのアクセス間隔(秒)
MAX_DAYS_BACK = 400     # 最新の報告書を探す日数の上限

# 有報=120、四半期報告書=140、半期報告書=160(訂正報告書は対象外)
DOC_TYPES = {"120": "有価証券報告書", "140": "四半期報告書", "160": "半期報告書"}


class EdinetError(Exception):
    """EDINET から取れなかった。メッセージは画面に出せる日本語。"""


class Cancelled(Exception):
    """利用者が中止した。"""


# ------------------------------------------------------------ APIキー

def load_api_key():
    """環境変数 EDINET_API_KEY(GitHub Actions 用)→ このPCのファイル の順に探す。"""
    env = (os.environ.get("EDINET_API_KEY") or "").strip()
    if env:
        return env
    try:
        return paths.api_key_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def save_api_key(key):
    p = paths.api_key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(key.strip(), encoding="utf-8")


def _get(url_path, key, **params):
    from urllib.parse import urlencode
    params["Subscription-Key"] = key
    try:
        return net.fetch(f"{API}/{url_path}?{urlencode(params)}",
                         interval=INTERVAL, timeout=60, site_name="EDINET")
    except net.FetchError as e:
        if e.status == 401:
            raise EdinetError("EDINET の APIキーが正しくありません。"
                              "「EDINETのAPIキーを設定」から入れ直してください") from None
        raise EdinetError(str(e)) from None


# ------------------------------------------------------------ 書類を探す

def _list_cache_file(day):
    return paths.cache_dir() / "edinet_list" / f"{day:%Y-%m-%d}.json"


def list_documents(day, key, today=None):
    """その日に提出された書類の一覧(必要な項目だけ)。"""
    today = today or datetime.date.today()
    f = _list_cache_file(day)
    if day < today and f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    raw = _get("documents.json", key, date=f"{day:%Y-%m-%d}", type=2)
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise EdinetError("EDINET の応答を読み取れませんでした") from None
    status = str(data.get("metadata", {}).get("status", data.get("StatusCode", "")))
    if status == "401":
        raise EdinetError("EDINET の APIキーが正しくありません。"
                          "「EDINETのAPIキーを設定」から入れ直してください")
    if status not in ("200", ""):
        msg = data.get("metadata", {}).get("message") or data.get("message") or ""
        raise EdinetError(f"EDINET から書類一覧を取得できませんでした({status} {msg})")
    keep = ("docID", "edinetCode", "secCode", "docTypeCode", "periodStart", "periodEnd",
            "submitDateTime", "docDescription", "csvFlag", "withdrawalStatus")
    rows = [{k: r.get(k) for k in keep}
            for r in data.get("results") or []
            if r.get("docTypeCode") in DOC_TYPES]
    if day < today:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass   # 保存できなくても続ける(次回また取りにいくだけ)
    return rows


def find_latest_report(edinet_code, key, progress=None, cancelled=None, today=None):
    """最新の有報・半期・四半期報告書を返す(見つからなければ None)。"""
    today = today or datetime.date.today()
    for i in range(MAX_DAYS_BACK):
        if cancelled and cancelled():
            raise Cancelled()
        day = today - datetime.timedelta(days=i)
        if day.weekday() >= 5:
            continue
        cached = day < today and _list_cache_file(day).exists()
        if progress and not cached:
            progress(f"EDINET の提出書類を確認しています({day:%Y-%m-%d})")
        hits = [r for r in list_documents(day, key, today)
                if r.get("edinetCode") == edinet_code
                and r.get("csvFlag") == "1"
                and str(r.get("withdrawalStatus") or "0") == "0"]
        if hits:
            return max(hits, key=lambda r: r.get("submitDateTime") or "")
    return None


# ------------------------------------------------------------ 有利子負債の計算

@dataclass
class DebtItem:
    label: str        # 項目名(例: 短期借入金)
    element: str      # 要素ID
    yen: str          # 金額(円)。JSON に保存するため文字で持つ
    standard: bool    # 金融庁の標準の項目か(False = 会社独自の項目)


@dataclass
class DebtResult:
    total_yen: str = "0"           # 借入金・社債・CP の合計(リース除く)
    lease_yen: str = None          # リース債務・リース負債の合計(無ければ None)
    basis: str = ""                # 連結 / 個別
    period_label: str = ""         # 当期末 / 当中間期末 など
    items: list = field(default_factory=list)
    lease_items: list = field(default_factory=list)
    needs_check: bool = False
    notes: list = field(default_factory=list)
    # 書類の情報
    doc_id: str = ""
    doc_type: str = ""
    doc_description: str = ""
    period_end: str = ""
    submitted: str = ""
    source: str = "EDINET"         # EDINET / IR BANK
    # 主要な経営指標など(EDINET のときだけ): {"sales": 円, "equity_ratio": %, "operating_cf": 円, "basis": 連結/個別}
    summary: dict = field(default_factory=dict)

    @property
    def total(self):
        return Decimal(self.total_yen)

    @property
    def lease(self):
        return None if self.lease_yen is None else Decimal(self.lease_yen)


_DEBT_RE = re.compile(r"借入金|社債|コマーシャル・?ペーパー|ＣＰ")
_LEASE_RE = re.compile(r"リース債務|リース負債")
# 借入金・社債を含むが有利子負債そのものではない項目、または合計の行
_EXCLUDE_RE = re.compile(r"合計|小計|利息|未払|保証|引当|担保|発行費|評価|差額|増減|収入|支出|"
                         r"借入金等|割合|比率|平均|利率|^.*計$")
_STANDARD_PREFIX = ("jppfs_cor:", "jpigp_cor:")


def classify_item(label):
    """科目名から 'debt'(有利子負債に入れる)/'lease'(リース。別に表示)/None を返す。"""
    label = unicodedata.normalize("NFKC", label or "").strip()
    if _EXCLUDE_RE.search(label):
        return None
    if _LEASE_RE.search(label):
        return "lease"
    if _DEBT_RE.search(label):
        return "debt"
    return None


def check_items(res):
    """合計のしかたに迷う点があれば res.needs_check と注意を付ける(EDINET・IR BANK 共通)。"""
    if not res.items:
        res.notes.append("貸借対照表に借入金・社債の項目がありません(無借金の可能性)")
    if any(not i.standard for i in res.items + res.lease_items):
        res.needs_check = True
        res.notes.append("会社独自の項目を含めて合計しています。内訳を確認してください")
    labels = [i.label for i in res.items]
    if any("及び" in l or "等" in l for l in labels) and len(labels) > 1:
        res.needs_check = True
        res.notes.append("「社債及び借入金」のようなまとめた項目と個別の項目が両方あります。"
                         "二重に数えていないか内訳を確認してください")
_SKIP_PREFIX = ("jpcrp_cor:", "jpdei_cor:")   # 経営指標・表紙などの項目(貸借対照表ではない)


def _read_tsv(raw):
    for enc in ("utf-16", "utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise EdinetError("EDINET の CSV を読み取れませんでした")
    return list(csv.reader(io.StringIO(text), delimiter="\t"))


def _col(header, word, default):
    for i, h in enumerate(header):
        if word in h:
            return i
    return default


def _dec(s):
    s = (s or "").replace(",", "").strip()
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def compute_debt(rows) -> DebtResult:
    """XBRL→CSV の行(見出し行を含む)から、期末時点の有利子負債を計算する。"""
    header, body = rows[0], rows[1:]
    c_el = _col(header, "要素ID", 0)
    c_label = _col(header, "項目名", 1)
    c_ctx = _col(header, "コンテキストID", 2)
    c_rel = _col(header, "相対年度", 3)
    c_cons = _col(header, "連結", 4)
    c_pt = _col(header, "期間", 5)
    c_val = _col(header, "値", 8)

    def usable(r):
        if len(r) <= max(c_el, c_label, c_ctx, c_rel, c_cons, c_pt, c_val):
            return False
        if r[c_pt].strip() != "時点" or not r[c_rel].strip().startswith("当"):
            return False
        # 「セグメント別」「担保に供している」などの内訳(Member 付き)は除く。
        # 個別を表す NonConsolidatedMember だけは許す。
        members = [p for p in r[c_ctx].split("_")[1:] if p]
        if any(m != "NonConsolidatedMember" for m in members):
            return False
        return not r[c_el].startswith(_SKIP_PREFIX)

    cand = [r for r in body if usable(r)]
    # 連結の数値があれば連結、無ければ個別(「連結・個別」列の値ごとに分けて選ぶ)
    groups = {}
    for r in cand:
        if r[c_el].startswith(_STANDARD_PREFIX):
            groups[r[c_cons].strip()] = groups.get(r[c_cons].strip(), 0) + 1
    if "連結" in groups:
        basis = "連結"
    elif groups:
        basis = max(groups, key=groups.get)
    else:
        basis = ""
    cand = [r for r in cand if r[c_cons].strip() == basis]

    res = DebtResult(basis=basis)
    res.period_label = cand[0][c_rel].strip() if cand else ""
    seen = set()
    total, lease = Decimal(0), None
    for r in cand:
        el, label = r[c_el].strip(), r[c_label].strip()
        if el in seen:
            continue
        val = _dec(r[c_val])
        if val is None:
            continue
        standard = el.startswith(_STANDARD_PREFIX)
        kind = classify_item(label)
        if kind == "lease":
            seen.add(el)
            lease = (lease or Decimal(0)) + val
            res.lease_items.append(DebtItem(label, el, str(val), standard))
        elif kind == "debt":
            seen.add(el)
            total += val
            res.items.append(DebtItem(label, el, str(val), standard))
    res.total_yen = str(total)
    res.lease_yen = None if lease is None else str(lease)

    if not cand:
        res.needs_check = True
        res.notes.append("貸借対照表の期末の数値が見つかりませんでした")
    else:
        check_items(res)
    return res


# 「主要な経営指標等の推移」の項目(jpcrp_cor:...SummaryOfBusinessResults)から、売上高・自己資本比率・営業CF を取る。
# 業種や会計基準で項目名が違うため、科目名で探す(上にあるものを優先)。
SUMMARY_VERSION = 2   # 読み取り方を変えたら上げる(保存した古い結果を使わず取り直す)
_SALES_LABELS = ("売上高", "売上収益", "営業収益", "営業収入", "経常収益", "収益", "純収益", "売上高及び営業収入")
_EQUITY_RE = re.compile(r"^(自己資本比率|親会社所有者帰属持分比率)")
_OPCF_RE = re.compile(r"^営業活動による.*キャッシュ・フロー")


def compute_summary(rows):
    """XBRL→CSV の行から {"sales": 円, "equity_ratio": %, "operating_cf": 円, "basis": 連結/個別} を作る。
    見つからない項目は入れない。値は JSON に保存するため文字で持つ。"""
    header, body = rows[0], rows[1:]
    c_el = _col(header, "要素ID", 0)
    c_label = _col(header, "項目名", 1)
    c_ctx = _col(header, "コンテキストID", 2)
    c_rel = _col(header, "相対年度", 3)
    c_pt = _col(header, "期間", 5)
    c_val = _col(header, "値", 8)
    need = max(c_el, c_label, c_ctx, c_rel, c_pt, c_val)

    cand = []
    for r in body:
        if len(r) <= need or not r[c_el].endswith("SummaryOfBusinessResults"):
            continue
        if not r[c_rel].strip().startswith("当"):
            continue
        members = [p for p in r[c_ctx].split("_")[1:] if p]
        if any(m != "NonConsolidatedMember" for m in members):
            continue
        val = _dec(r[c_val])
        if val is None:
            continue
        label = unicodedata.normalize("NFKC", r[c_label]).strip()
        label = re.sub(r"、経営指標等$", "", label).strip()       # 「売上高、経営指標等」→「売上高」
        label = re.sub(r"[((][^))]*[))]$", "", label).strip()   # 「売上高(千円)」などの末尾を除く
        # 経営指標の「連結・個別」列は「その他」になるため、コンテキストIDで見分ける
        cons = "個別" if "NonConsolidatedMember" in members else "連結"
        cand.append((label, cons, r[c_pt].strip(), val))
    if not cand:
        return {}
    basis = "連結" if any(c[1] == "連結" for c in cand) else "個別"
    cand = [c for c in cand if c[1] == basis]
    out = {"basis": basis, "v": SUMMARY_VERSION}
    for want in _SALES_LABELS:
        hit = [c for c in cand if c[0] == want and c[2] == "期間"]
        if hit:
            out["sales"] = str(hit[0][3])
            out["sales_label"] = want
            break
    hit = [c for c in cand if _EQUITY_RE.match(c[0]) and c[2] == "時点"]
    if hit:
        v = hit[0][3]
        out["equity_ratio"] = str(v * 100 if abs(v) <= Decimal("1.5") else v)   # 0.595 → 59.5%
    hit = [c for c in cand if _OPCF_RE.match(c[0]) and c[2] == "期間"]
    if hit:
        out["operating_cf"] = str(hit[0][3])
    return out


def _debt_cache_file(doc_id):
    return paths.cache_dir() / "edinet_debt" / f"{doc_id}.json"


def fetch_debt(doc, key) -> DebtResult:
    """書類(find_latest_report の戻り値)から有利子負債を求める。"""
    doc_id = doc["docID"]
    f = _debt_cache_file(doc_id)
    res = None
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            d["items"] = [DebtItem(**i) for i in d.get("items", [])]
            d["lease_items"] = [DebtItem(**i) for i in d.get("lease_items", [])]
            ok = (d.get("summary") or {}).get("v") == SUMMARY_VERSION
            res = DebtResult(**d) if ok else None   # 古い形式は取り直す
        except (OSError, ValueError, TypeError):
            res = None
    if res is None:
        raw = _get(f"documents/{doc_id}", key, type=5)
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            raise EdinetError("EDINET から書類の CSV を取得できませんでした") from None
        with z:
            names = [n for n in z.namelist()
                     if n.lower().endswith(".csv") and "XBRL_TO_CSV" in n.replace("\\", "/")]
            # 本体(jpcrp...)を優先。監査報告書(jpaud...)は使わない
            main = [n for n in names if os.path.basename(n).startswith("jpcrp")]
            if not main:
                raise EdinetError("書類の CSV に財務の数値が入っていませんでした")
            rows = _read_tsv(z.read(main[0]))
        res = compute_debt(rows)
        res.summary = compute_summary(rows)
        res.doc_id = doc_id
        res.doc_type = DOC_TYPES.get(doc.get("docTypeCode"), "")
        res.doc_description = doc.get("docDescription") or ""
        res.period_end = doc.get("periodEnd") or ""
        res.submitted = (doc.get("submitDateTime") or "")[:10]
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(asdict(res), ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return res
