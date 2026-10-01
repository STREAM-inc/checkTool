# -*- coding: utf-8 -*-
"""目検ツールの処理本体。CSVを読み込み、自動修正と集計・要望チェックを行う。

自動修正:
  - 資本金 / 従業員数 : 「1,000万円」「1億2000万円」「50名(2024年)」→ 円・人の整数に統一
  - 代表者名 / 担当者名 : 役職・部署・読み仮名・経歴などを外して名前のみにする
                          (代表者名から外した役職は、役職列が空なら役職列へ移す)
  - 郵便番号          : 空欄を住所から補完、表記を 123-4567 に統一、都道府県が食い違う番号は住所から引き直す
                          (郵便番号列が無いリストには列を追加)
  - TEL               : 市外局番と住所の地域が明らかに違うものは消す
                          (リスト詳細に「電話番号あり」がある場合、TELが消えた行は除外行へ)
"""
import csv
import io
import os
import re
import sys
import unicodedata
from collections import Counter

csv.field_size_limit(10 ** 9)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "郵便番号補完ツール"))

from areacode import PREFECTURES  # noqa: E402

# ------------------------------------------------------------------ 列の自動判定

COLS = {
    "name": ["名称", "企業名", "会社名", "顧客名", "法人名", "事業所名", "店舗名"],
    "pref": ["都道府県"],
    "addr": ["住所", "本社住所", "所在地"],
    "tel": ["TEL", "電話番号", "代表電話", "代表TEL"],
    "zip": ["郵便番号", "本社郵便番号"],
    "capital": ["資本金"],
    "employees": ["従業員数"],
    "title": ["役職", "代表者役職"],
    "media": ["掲載媒体", "掲載サイト名", "媒体"],
    "premium": ["プレミアム掲載", "プレミアム"],
}
PERSON_COLS = ["代表者名", "代表者", "担当者名", "担当者", "採用担当者名", "採用担当者", "代表者氏名"]


LOOSE = {  # 完全一致が無いとき、列名にこの語を含む列を使う(左ほど優先)
    "zip": ["郵便番号", "郵便", "〒", "zip", "ZIP"],
    "addr": ["本社住所", "住所", "所在地"],
    "tel": ["電話番号", "TEL", "Tel", "tel", "電話"],
    "name": ["企業名", "会社名", "名称", "店舗名", "法人名"],
}


def find_col(header, kind):
    norm = [re.sub(r"\s+", "", unicodedata.normalize("NFKC", h or "")) for h in header]
    for c in COLS[kind]:
        if c in norm:
            return norm.index(c)
    for w in LOOSE.get(kind, []):
        for j, h in enumerate(norm):
            if w in h and not (kind == "addr" and re.search(r"勤務地|最寄|アクセス|URL|メール", h)) \
                    and not (kind == "tel" and re.search(r"FAX|ファックス|携帯", h, re.I)) \
                    and not (kind == "name" and re.search(r"カナ|かな|代表者|担当", h)):
                return j
    return None


# NFKCで直らない部首補助の字(⻑など)を通常の漢字に寄せる
RADICALS = str.maketrans({"⻑": "長", "⻄": "西", "⻝": "食", "⻤": "鬼", "⻩": "黄", "⻘": "青", "⻭": "歯", "⻯": "竜", "⻲": "亀", "⺠": "民", "⻆": "角", "⻅": "見", "⻣": "骨", "⻎": "辶"})


RADICAL_RE = re.compile("[" + "".join(chr(k) for k in RADICALS) + "]")


def nfkc(s):
    t = unicodedata.normalize("NFKC", s or "")
    return t.translate(RADICALS) if RADICAL_RE.search(t) else t


# ------------------------------------------------------------------ 資本金・従業員数

UNITS = {"兆": 10 ** 12, "億": 10 ** 8, "千万": 10 ** 7, "百万": 10 ** 6, "万": 10 ** 4, "千": 10 ** 3}
EMPTY_WORDS = {"", "-", "ー", "―", "‐", "なし", "無し", "非公開", "不明", "未定", "—", "*", "※", "該当なし", "-円"}


def parse_capital(raw):
    """(正規化後, 状態) 状態: same / fixed / blank / review"""
    s = nfkc(raw).strip()
    if not s:
        return raw, "same"
    t = re.sub(r"[,\s]", "", s)
    t = re.sub(r"^(資本金|資本)[:：]?", "", t)
    t = re.sub(r"^[（(][^）)]*[）)]", "", t)
    if t in EMPTY_WORDS or re.fullmatch(r"[※*\-ー]+", t):
        return "", "blank"
    if not re.search(r"\d", t) and re.search(r"非公開|なし|無し|ありません|ございません|不明|該当しない", t):
        return "", "blank"
    if re.search(r"ドル|\$|USD|ユーロ|EUR|元|ウォン", t, re.I):
        return raw, "review"
    m = re.match(r"^約?((?:\d+(?:\.\d+)?(?:兆|億|千万|百万|万|千)?)+)(円)?", t)
    if not m:
        return raw, "review"
    toks = re.findall(r"(\d+(?:\.\d+)?)(兆|億|千万|百万|万|千)?", m.group(1))
    total = 0
    for num, unit in toks:
        total += float(num) * UNITS.get(unit, 1)
    total = int(round(total))
    val = str(total)
    has_unit = any(u for _, u in toks)
    if not has_unit and not m.group(2) and total < 100000:
        # 単位の無い小さい数字は「万円」単位の可能性がある
        return raw, "review"
    return val, ("same" if val == raw else "fixed")


def parse_employees(raw):
    s = nfkc(raw).strip()
    if not s:
        return raw, "same"
    t = re.sub(r"[,\s]", "", s)
    if t in EMPTY_WORDS:
        return "", "blank"
    if re.match(r"^約?\d+[~〜～\-－]\d+", t):
        return raw, "review"
    m = re.match(r"^(?:従業員数?|社員数?)?[:：]?約?(\d+)(?:名|人)?", t) or re.search(r"約?(\d+)(?:名|人)", t)
    if not m:
        return raw, "review"
    val = str(int(m.group(1)))
    return val, ("same" if val == raw else "fixed")


# ------------------------------------------------------------------ 人名

TITLE_WORDS = sorted(set("""
代表取締役社長兼CEO 代表取締役社長兼最高経営責任者 代表取締役会長兼社長 代表取締役会長 代表取締役社長 代表取締役副社長
代表取締役専務 代表取締役 代表執行役社長 代表執行役 取締役代表執行役社長 取締役兼代表執行役社長 代表社員 代表理事長 代表理事
代表幹事 代表者 代表 取締役社長 取締役会長 取締役副社長 専務取締役 常務取締役 取締役 執行役員社長 執行役員 執行役 社長 会長 副社長
専務 常務 監査役 理事長 副理事長 理事 院長 副院長 医院長 総院長 園長 副園長 学園長 校長 学長 所長 副所長 施設長 店長 支店長 支配人
工場長 事務長 事務局長 局長 本部長 部長 次長 課長 係長 主任 室長 組合長 会頭 看護部長 管理者 オーナー 経営者 CEO COO CFO President
社主 頭取 総長 館長 塾長 教室長 税理士 弁護士 司法書士 行政書士 社会保険労務士 医師 歯科医師 獣医師 薬剤師 院長代理 マネージャー
マネジャー リーダー 担当者 担当 採用担当者 採用担当 人事担当 人事採用担当 採用責任者 責任者 兼 職務執行者 所有者 開設者 事業主
""".split()), key=len, reverse=True)
TITLE_RE = "|".join(map(re.escape, TITLE_WORDS))
DEPT_RE = (r"[^\s]{0,15}?(?:人事|総務|採用|営業|管理|経理|財務|企画|事業|業務|事務|技術|製造|開発|広報|経営|運営|店舗|本社|工事|施工|設計|"
           r"品質|生産|物流|購買|商品|販売|法務|秘書|看護|介護|支援|サービス|システム|マーケティング)(?:本部|部|課|室|グループ|チーム|センター|係)+")
CORP_RE = re.compile(r"カブシキガイシャ|ユウゲンガイシャ|コーポレーション|株式会社|有限会社|合同会社|合資会社|合名会社|法人|協同組合|組合|\(株\)|\(有\)|事務所|クリニック|病院|医院|ホテル|店$|会社")
NOT_NAME_WORDS = {"採用担当", "人事部", "人事課", "総務部", "総務課", "担当者", "ご担当者", "採用係", "人事", "総務", "代表",
                  "院長", "社長", "店長", "オーナー", "事務局", "受付", "採用窓口"}


def clean_person(raw):
    """(名前, 外した役職, 状態) 状態: same / fixed / blank / review"""
    s = nfkc(raw)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return raw, "", "same"
    orig = s
    if re.fullmatch(r"[※*\-ー―・.。 ]+", s) or s in EMPTY_WORDS:
        return "", "", "blank"
    # 経歴・補足
    s = re.split(r"【|\[|≪|《|<br|<|※|\n", s)[0]
    s = re.sub(r"[（(][^）)]*[）)]", " ", s)
    s = re.sub(r"[「」『』]", " ", s)
    s = re.sub(rf"((?:{TITLE_RE}))\s*[/／:：]\s*", r"\1 ", s)
    s = re.split(r"[、,，/／;；。]| and | & ", s)[0]
    s = re.sub(r"\s+", " ", s).strip()
    titles = []
    changed = True
    while changed and s:
        changed = False
        m = re.match(rf"^((?:{TITLE_RE})+)[\s:：・]*", s)
        if m and m.end() < len(s):
            titles.append(m.group(1))
            s = s[m.end():].strip()
            changed = True
            continue
        m = re.match(rf"^([^\s]{{0,12}}?(?:{TITLE_RE}))[\s:：・]+(.+)$", s) or re.match(rf"^({DEPT_RE})[\s:：・]+(.+)$", s)
        if m:
            titles.append(m.group(1))
            s = m.group(2).strip()
            changed = True
            continue
        m = re.search(rf"[\s:：・]*((?:{TITLE_RE})+)$", s)
        if m and m.start() > 0:
            titles.insert(0, m.group(1))
            s = s[:m.start()].strip()
            changed = True
            continue
    m = re.search(rf"\s(?:{TITLE_RE})(?:\s|$)", s)
    if m:  # 「山田 太郎 代表取締役社長 山田 次郎」のように複数人並んでいたら先頭の人だけ
        s = s[:m.start()].strip()
    s = re.sub(r"\s*(様|氏|さん|殿)$", "", s)
    s = re.sub(r"\s*(ほか|他)\s*\d*\s*名?$", "", s).strip()
    title = " ".join(titles).strip()
    if not s or s in NOT_NAME_WORDS or re.fullmatch(rf"(?:{TITLE_RE})+", s):
        return "", title, "blank"
    if CORP_RE.search(s) or re.search(r"\d|@|https?:|www\.|TEL|電話", s, re.I):
        return "", title, "blank"
    if len(s.replace(" ", "")) > 15:
        return raw, title, "review"
    if s == orig:
        return (raw if s == raw else s), "", ("same" if s == raw else "fixed")
    return s, title, "fixed"


# ------------------------------------------------------------------ 郵便番号

class ZipTools:
    def __init__(self, kenall_path):
        from fill_zip import ZipLookup
        self.lookup = ZipLookup(kenall_path)
        self.zip_pref = {}
        with open(kenall_path, encoding="utf-8", newline="") as f:
            for r in csv.reader(f):
                if len(r) >= 9:
                    self.zip_pref.setdefault(r[2], r[6])

    def fix(self, raw, addr, pref_hint):
        """(値, 状態) 状態: same / fixed / filled / refilled / review"""
        d = re.sub(r"\D", "", nfkc(raw))
        if d:
            if len(d) == 7:
                val = f"{d[:3]}-{d[3:]}"
                zp = self.zip_pref.get(d)
                apref = pref_hint or guess_pref(nfkc(addr))
                if zp and apref and zp != apref:
                    z, _ = self.lookup.find_zip(addr, pref_hint=apref)
                    if z:
                        return f"{z[:3]}-{z[3:]}", "refilled"
                    return val, "review"
                return val, ("same" if val == raw else "fixed")
            return raw, "review"
        if not nfkc(addr).strip():
            return raw, "same"
        z, _ = self.lookup.find_zip(addr, pref_hint=pref_hint or None)
        if z:
            return f"{z[:3]}-{z[3:]}", "filled"
        return raw, "unfilled"


def guess_pref(addr):
    a = re.sub(r"\s+", "", addr or "")
    for p in PREFECTURES:
        if a.startswith(p):
            return p
    return ""


# ------------------------------------------------------------------ エリア

REGIONS = {
    "北海道": ["北海道"],
    "東北": ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    "関東": ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"],
    "首都圏": ["埼玉県", "千葉県", "東京都", "神奈川県"],
    "1都3県": ["埼玉県", "千葉県", "東京都", "神奈川県"],
    "一都三県": ["埼玉県", "千葉県", "東京都", "神奈川県"],
    "甲信越": ["山梨県", "長野県", "新潟県"],
    "北陸": ["新潟県", "富山県", "石川県", "福井県"],
    "東海": ["岐阜県", "静岡県", "愛知県", "三重県"],
    "中部": ["新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県", "愛知県"],
    "近畿": ["三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    "関西": ["滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    "中国": ["鳥取県", "島根県", "岡山県", "広島県", "山口県"],
    "四国": ["徳島県", "香川県", "愛媛県", "高知県"],
    "九州": ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"],
    "沖縄": ["沖縄県"],
}


def parse_area(text):
    """文字列からエリア(都道府県の集合)を取り出す。全国・指定なしは None"""
    t = nfkc(text)
    if not t or "全国" in t:
        return None
    prefs = set()
    for k, v in REGIONS.items():
        if k in t:
            prefs.update(v)
    for p in PREFECTURES:
        short = p[:-1] if p != "北海道" else p
        if short == "京都":
            if re.search(r"(?<!東)京都", t):
                prefs.add(p)
        elif p in t or short in t:
            prefs.add(p)
    return prefs or None


# ------------------------------------------------------------------ CSV入出力

def read_csv_bytes(data):
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = data.decode(enc)
            return list(csv.reader(io.StringIO(text, newline=""))), enc
        except UnicodeDecodeError:
            continue
    raise ValueError("文字コードを判別できません(UTF-8 / Shift_JIS 以外)")


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)


# ------------------------------------------------------------------ 本体

def pct(a, b):
    return round(a * 100 / b, 1) if b else 0.0


# 処理ルール(画面のチェックボックスでON/OFF)。(キー, 表示名, 初期値, 種類)
RULES = [
    ("tel_format", "ハイフン追加・位置修正（0312345678→03-1234-5678、025-836-7606→0258-36-7606）。追加できない番号は削除", True, "TELチェック"),
    ("tel_nohyphen", "ハイフンの無いTELは追加せずに削除", False, "TELチェック"),
    ("tel_bad", "桁数違反を除外（10桁・11桁以外）", True, "TELチェック"),
    ("tel_area", "市外局番と住所の地域が違うTELを除外", True, "TELチェック"),
    ("tel_freedial", "フリーダイヤル除外（0120/0800）", False, "TELチェック"),
    ("tel_050", "050から始まる電話番号除外", False, "TELチェック"),
    ("tel_dup", "重複を除外（2件目以降）", True, "TELチェック"),
    ("need_tel", "空欄除去（TELが空欄の行を除外）", True, "TELチェック"),
    ("tel_deleted_row", "TELを消した行は行ごと除外（オフだとTELだけ空欄にして行は残す）", True, "TELチェック"),
    ("addr_digits", "全角→半角変換（数字・数字の間のハイフン）", True, "住所正規化"),
    ("addr_zip", "郵便番号除去（〒123-4567 / 123-4567）※郵便番号列が空なら移す", True, "住所正規化"),
    ("pref_fix", "都道府県列を住所の都道府県に合わせて修正", True, "住所正規化"),
    ("zip", "郵便番号の空欄補完・表記統一（郵便番号列があるときだけ）", True, "住所正規化"),
    ("add_zip", "郵便番号列が無いリストにも列を追加して埋める", False, "住所正規化"),
    ("name_eq", "名称の先頭の「=」を削除（Excelで数式扱いになるため）", True, "名称・人名・数値"),
    ("person", "代表者名・担当者名を名前のみにする", True, "名称・人名・数値"),
    ("capital", "資本金を円の整数に統一", True, "名称・人名・数値"),
    ("employees", "従業員数を整数に統一", True, "名称・人名・数値"),
    ("bad_delete", "直せない値（資本金・従業員数・人名・郵便番号の不正値）は削除", True, "名称・人名・数値"),
    ("area_out", "リスト詳細のエリア外の行を除外", True, "行の除外"),
    ("period_out", "リスト詳細の期間（開業・設立時期など）外の行を除外", True, "行の除外"),
    ("store_name", "名称が「〇〇店」の行を除外（〜店・〜支店・〜号店・〜営業所）", False, "行の除外"),
    ("chain", "チェーン店を除外（同じブランド名がリスト内に一定数以上ある）", False, "行の除外"),
    ("name_blank", "名称が空欄の行を除外", True, "行の除外"),
    ("mojibake", "文字化けを含む行を除外", True, "行の除外"),
    ("exclude", "除外リスト（既存顧客・NGリスト等）に一致する行を除外", True, "行の除外"),
]


# ------------------------------------------------------------------ 期間(開業・設立時期など)

ZEN_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
PERIOD_COL_RE = re.compile(r"設立|開業|創業|開設|オープン|登録日|設立日|掲載開始|掲載日|開始日")
# 期間系の列(日付が入っている列)。列名で候補を選び、値の6割以上が年月として読めるものだけ
DATE_COL_RE = re.compile(r"日$|日付|年月|設立|開業|創業|開始|開設|時期|登録|掲載日|更新日|オープン|\(推定\)|（推定）")
ERA = {"令和": 2018, "R": 2018, "平成": 1988, "H": 1988, "昭和": 1925, "S": 1925, "大正": 1911, "T": 1911, "明治": 1867, "M": 1867}


def _months_ago(n):
    import datetime
    d = datetime.date.today()
    y, m = d.year, d.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return y, m


def parse_period(text):
    """「2024年1月～2026年12月」→ ((2024,1),(2026,12))。「2024年以降」→ ((2024,1),None)。
    「2020年以前」→ (None,(2020,12))。「直近3ヶ月」「過去1年以内」→ (今からNヶ月前, None)。無ければ None"""
    t = nfkc(text)
    m = re.search(r"(\d{4})年\s*(?:(\d{1,2})月)?\s*(?:[~〜～\-－]|から)+\s*(\d{4})年\s*(?:(\d{1,2})月)?", t)
    if m:
        return (int(m.group(1)), int(m.group(2) or 1)), (int(m.group(3)), int(m.group(4) or 12))
    m = re.search(r"(\d{4})年\s*(?:(\d{1,2})月)?\s*(?:以降|以後|から)", t)
    if m:
        return (int(m.group(1)), int(m.group(2) or 1)), None
    m = re.search(r"(\d{4})年\s*(?:(\d{1,2})月)?\s*(?:以前|まで)", t)
    if m:
        return None, (int(m.group(1)), int(m.group(2) or 12))
    m = re.search(r"(?:直近|過去|ここ|最近)\s*(\d+)\s*(ヶ月|か月|カ月|ヵ月|年)", t)
    if m:
        n = int(m.group(1)) * (12 if m.group(2) == "年" else 1)
        return _months_ago(n), None
    return None


def parse_ym(v):
    """年月として読めれば (年, 月)。月が無ければ月=0。和暦(令和6年/R6.3/平成30年)も読む"""
    t = nfkc(v).strip()
    if not t:
        return None
    m = re.search(r"(令和|平成|昭和|大正|明治|[RHSTM])\s*(\d{1,2}|元)\s*[年.\-/]?\s*(\d{1,2})?", t)
    if m and (m.group(1) in ERA) and not re.match(r"\d{4}", t):
        y = ERA[m.group(1)] + (1 if m.group(2) == "元" else int(m.group(2)))
        return y, int(m.group(3) or 0) if m.group(3) and 1 <= int(m.group(3)) <= 12 else 0
    m = re.match(r"^(\d{4})(\d{2})(\d{2})?$", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})?", t) or re.match(r"^(\d{4})$", t)
    if not m or not 1800 <= int(m.group(1)) <= 2100:
        return None
    mo = int(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) else 0
    return int(m.group(1)), (mo if 1 <= mo <= 12 else 0)


def in_period(ym, period):
    """月が不明(0)の値は年だけで判定"""
    start, end = period
    y, mo = ym
    if start and (y, mo or 12) < start:
        return False
    if end and (y, mo or 1) > end:
        return False
    return True


def date_columns(header, body):
    res = []
    for j, h in enumerate(header):
        if not DATE_COL_RE.search(nfkc(h)) and not PERIOD_COL_RE.search(nfkc(h)):
            continue
        vals = [r[j] for r in body if r[j].strip()][:500]
        if not vals or sum(1 for v in vals if parse_ym(v)) >= len(vals) * 0.6:
            res.append(j)
    return res


def pick_period_col(line, header, date_idx):
    """リスト詳細の文から、どの期間系の列の話か選ぶ"""
    t = nfkc(line)
    for words, col in ((r"掲載", r"掲載"), (r"設立|会社設立|法人設立", r"設立"), (r"開業|オープン|開設|開店", r"開業|オープン|開設|開店"),
                       (r"創業", r"創業"), (r"登録", r"登録")):
        if re.search(words, t):
            for j in date_idx:
                if re.search(col, header[j]):
                    return j
    for j in date_idx:
        if PERIOD_COL_RE.search(header[j]):
            return j
    return date_idx[0] if date_idx else None


def ym_label(ym):
    return f"{ym[0]}年{ym[1]}月" if ym else ""
DEFAULT_OPTIONS = {k: v for k, _, v, _ in RULES}

CORP_ABBR = {"(株)": "株式会社", "(有)": "有限会社", "(同)": "合同会社", "(医)": "医療法人", "(福)": "社会福祉法人",
             "(社)": "社団法人", "(財)": "財団法人", "(一社)": "一般社団法人", "(公社)": "公益社団法人"}


def name_key(s):
    """名称の比較用キー。法人格は残す(株式会社創建と有限会社創建は別会社)。空白・記号の違いだけ吸収"""
    t = nfkc(s).lower()
    for a, f in CORP_ABBR.items():
        t = t.replace(a, f)
    return re.sub(r"[\s・.,、。'\"()\[\]【】「」\-ー―_/]", "", t)


def tel_key(s):
    d = re.sub(r"\D", "", nfkc(s))
    return d if len(d) >= 10 else ""


def city_of(addr):
    """住所から「都道府県+市区町村」のざっくりしたキーを作る(政令市は区まで)"""
    a = re.sub(r"\s+", "", nfkc(addr))
    m = re.match(r"^(東京都|北海道|(?:京都|大阪)府|.{2,3}県)?(.+?郡.+?[町村]|.+?市.+?区|.+?[市区町村])", a)
    return ((m.group(1) or "") + m.group(2)) if m else ""


STORE_RE = re.compile(r"(店|支店|号店|営業所|出張所)\s*$")
BRANCH_SUFFIX = r"(?:店|支店|号店|営業所|校|教室|支部|院|クリニック|センター|スクール|ルーム|会館)"
GENERIC_BRANDS = {"個別指導", "個別指導塾", "学習塾", "進学塾", "塾", "英会話", "英会話教室", "そろばん", "書道", "ピアノ教室",
                  "美容室", "美容院", "ヘアサロン", "整骨院", "接骨院", "歯科", "歯科医院", "クリニック", "薬局", "居酒屋", "カフェ"}


def strip_name(s):
    t = nfkc(s)
    t = re.sub(r"[（(【\[<].*?[）)】\]>]", " ", t)
    for f in list(CORP_ABBR) + list(CORP_ABBR.values()) + ["株式会社", "有限会社", "合同会社"]:
        t = t.replace(f, " ")
    return re.sub(r"\s+", " ", t).strip()


def brand_of(name):
    """「公文式 西新井栄町教室」→「公文式」、「増田塾町田校」→「増田塾」。判定できなければ ''"""
    t = strip_name(name)
    toks = [x for x in re.split(r"[\s/／|｜・]+", t) if x]
    if len(toks) >= 2:
        b = toks[0]
        if b in GENERIC_BRANDS or len(b) < 2:
            b = toks[0] + toks[1]
        return b.lower()
    m = re.match(rf"^(.{{2,}})(\S{{2,6}}{BRANCH_SUFFIX})$", t)
    return m.group(1).lower() if m else ""


def chain_brands(names, min_count):
    c = Counter(b for b in map(brand_of, names) if b)
    return {b: n for b, n in c.items() if n >= min_count}


def load_exclude_list(rows, label):
    """除外リスト(ヘッダ付き2次元リスト)からTEL・名称・名称+都道府県のキー集合を作る"""
    header = rows[0]
    ci = {k: find_col(header, k) for k in ("name", "tel", "pref", "addr")}
    if ci["tel"] is None:  # 列名が無くても電話番号っぽい列を探す
        for j in range(len(header)):
            vals = [r[j] for r in rows[1:50] if j < len(r) and r[j].strip()]
            if vals and sum(1 for v in vals if re.fullmatch(r"0\d{1,4}-?\d{1,4}-?\d{3,4}", nfkc(v).strip())) >= len(vals) * 0.8:
                ci["tel"] = j
                break
    tels, names, name_pref, name_city = set(), set(), set(), set()
    for r in rows[1:]:
        r = r + [""] * (len(header) - len(r))
        if ci["tel"] is not None and tel_key(r[ci["tel"]]):
            tels.add(tel_key(r[ci["tel"]]))
        if ci["name"] is not None and name_key(r[ci["name"]]):
            nk = name_key(r[ci["name"]])
            names.add(nk)
            pref = r[ci["pref"]] if ci["pref"] is not None else (guess_pref(nfkc(r[ci["addr"]])) if ci["addr"] is not None else "")
            if pref:
                name_pref.add((nk, pref))
            if ci["addr"] is not None and city_of(r[ci["addr"]]):
                name_city.add((nk, city_of(r[ci["addr"]]).replace(pref, "", 1) if pref else city_of(r[ci["addr"]])))
    return {"label": label, "rows": len(rows) - 1, "tels": tels, "names": names, "name_pref": name_pref, "name_city": name_city,
            "cols": {k: (header[v] if v is not None else None) for k, v in ci.items()}}


def process(rows, filename, issue=None, zip_tools=None, area_checker=None, options=None, excludes=None):
    """rows: ヘッダ込みの2次元リスト。
    options: RULES のキー → True/False。exclude_keys: {"tel","name_pref","name"} のどれで照合するか
    excludes: load_exclude_list() の戻り値のリスト
    戻り値: (修正後rows, 除外rows, ログrows, report dict)"""
    opt = dict(DEFAULT_OPTIONS)
    opt.update({k: v for k, v in (options or {}).items() if k in DEFAULT_OPTIONS})
    for k, _, _, group in RULES:  # 「TELチェックを有効にする」等のグループ単位のOFF
        if group in ((options or {}).get("group_off") or []):
            opt[k] = False
    exclude_keys = set((options or {}).get("exclude_keys") or ["tel"])
    excludes = excludes or []
    header = list(rows[0])
    body = [list(r) + [""] * (len(header) - len(r)) for r in rows[1:] if any(c.strip() for c in r)]
    n_in = len(body)
    log = []  # [行番号, 列, 修正前, 修正後, 理由]
    counts = Counter()
    samples = {}

    def add_log(i, col, before, after, reason, key):
        log.append([i + 2, col, before, after, reason])
        counts[key] += 1
        samples.setdefault(key, [])
        if len(samples[key]) < 8:
            samples[key].append({"row": i + 2, "before": before, "after": after, "note": reason})

    details = (issue or {}).get("details", [])
    detail_text = " ".join(details)
    need_tel = bool(re.search(r"電話番号(あり|有り|有|必須)|TEL(あり|有り|有|必須)", nfkc(detail_text)))
    area = None
    for d in details + [(issue or {}).get("area", "")]:
        for part in re.split(r"[、,，]", nfkc(d)):
            pa = parse_area(part) if not re.search(r"掲載|求人|電話|TEL", part) else None
            if pa:
                area = (area or set()) | pa

    date_idx = date_columns(header, body)
    periods = {}  # 列番号 → (期間, 出どころ)
    for d in details:
        per = parse_period(d)
        j = pick_period_col(d, header, date_idx) if per else None
        if per and j is not None and j not in periods:
            periods[j] = (per, d)

    def ym_of(s):
        m = re.match(r"^(\d{4})-(\d{1,2})$", s or "")
        return (int(m.group(1)), int(m.group(2))) if m else None
    for p in (options or {}).get("periods") or []:  # 画面で入れた期間が優先
        if p.get("col") in header:
            j = header.index(p["col"])
            st, en = ym_of(p.get("start")), ym_of(p.get("end"))
            if st or en:
                periods[j] = ((st, en), "画面で指定")
            else:
                periods.pop(j, None)

    def detect():
        c = {k: find_col(header, k) for k in COLS}
        for k, key in (("tel", "tel_col"), ("addr", "addr_col")):  # 画面で列を指定したらそちらを優先
            name = (options or {}).get(key)
            if name and name in header:
                c[k] = header.index(name)
        return c

    ci = detect()
    # 郵便番号列が無ければ追加(住所列の直前)
    if ci["zip"] is None and ci["addr"] is not None and opt["add_zip"] and opt["zip"]:
        pos = ci["pref"] if ci["pref"] is not None and ci["pref"] < ci["addr"] else ci["addr"]
        header.insert(pos, "郵便番号")
        for r in body:
            r.insert(pos, "")
        counts["zip_col_added"] = 1
        ci = detect()
    person_idx = [header.index(c) for c in PERSON_COLS if c in header]
    orig_tel = [r[ci["tel"]] for r in body] if ci["tel"] is not None else None
    tel_why = {}  # 行番号 → TELを消した理由

    # --- 行ごとの修正
    for i, r in enumerate(body):
        if ci["addr"] is not None and (opt["addr_digits"] or opt["addr_zip"]):
            a0 = a = r[ci["addr"]]
            if opt["addr_zip"]:
                m = re.match(r"^\s*〒?\s*([0-9０-９]{3})\s*[-－−ー‐―]?\s*([0-9０-９]{4})(?![0-9０-９])\s*", a) or \
                    re.search(r"〒\s*([0-9０-９]{3})\s*[-－−ー‐―]?\s*([0-9０-９]{4})\s*", a)
                if m:
                    z = nfkc(m.group(1) + "-" + m.group(2))
                    a = (a[:m.start()] + " " + a[m.end():]).strip()
                    if ci["zip"] is not None and not r[ci["zip"]].strip():
                        r[ci["zip"]] = z
                        add_log(i, header[ci["zip"]], "", z, "住所に書かれていた郵便番号を郵便番号列へ移動", "zip_moved")
            if opt["addr_digits"]:
                a = a.translate(ZEN_DIGITS)
                a = re.sub(r"(?<=\d)[－−ー‐―─](?=\d)", "-", a)
            if a != a0:
                add_log(i, header[ci["addr"]], a0, a, "住所の正規化（全角数字→半角・郵便番号除去）", "addr_norm")
                r[ci["addr"]] = a
        if opt["pref_fix"] and ci["pref"] is not None and ci["addr"] is not None:
            ap = guess_pref(nfkc(r[ci["addr"]]))
            if ap and r[ci["pref"]] != ap:
                add_log(i, header[ci["pref"]], r[ci["pref"]], ap, "都道府県列を住所の都道府県に修正", "pref_fixed")
                r[ci["pref"]] = ap
        if opt["name_eq"] and ci["name"] is not None:
            v = r[ci["name"]]
            nv = re.sub(r"^[\s=＝]+", "", v)
            if nv != v:
                add_log(i, header[ci["name"]], v, nv, "名称の先頭の「=」を削除", "name_eq")
                r[ci["name"]] = nv
        if opt["capital"] and ci["capital"] is not None:
            v, st = parse_capital(r[ci["capital"]])
            if st in ("fixed", "blank") and v != r[ci["capital"]]:
                add_log(i, "資本金", r[ci["capital"]], v, "資本金表記を円の整数に統一" if st == "fixed" else "資本金が無効な値のため空欄", "capital_fixed")
                r[ci["capital"]] = v
            elif st == "review" and opt["bad_delete"]:
                add_log(i, "資本金", r[ci["capital"]], "", "資本金を自動変換できないため削除", "capital_deleted")
                r[ci["capital"]] = ""
            elif st == "review":
                add_log(i, "資本金", r[ci["capital"]], r[ci["capital"]], "資本金を自動変換できない(要確認)", "capital_review")
        if opt["employees"] and ci["employees"] is not None:
            v, st = parse_employees(r[ci["employees"]])
            if st in ("fixed", "blank") and v != r[ci["employees"]]:
                add_log(i, "従業員数", r[ci["employees"]], v, "従業員数を整数に統一", "emp_fixed")
                r[ci["employees"]] = v
            elif st == "review" and opt["bad_delete"]:
                add_log(i, "従業員数", r[ci["employees"]], "", "従業員数を自動変換できないため削除", "emp_deleted")
                r[ci["employees"]] = ""
            elif st == "review":
                add_log(i, "従業員数", r[ci["employees"]], r[ci["employees"]], "従業員数を自動変換できない(要確認)", "emp_review")
        for pi in person_idx if opt["person"] else []:
            col = header[pi]
            v, title, st = clean_person(r[pi])
            if st in ("fixed", "blank") and v != r[pi]:
                add_log(i, col, r[pi], v, "名前のみに整形" if st == "fixed" else "名前ではない値のため空欄", "person_fixed" if st == "fixed" else "person_blank")
                r[pi] = v
                if title and col.startswith("代表") and ci["title"] is not None and not r[ci["title"]].strip():
                    r[ci["title"]] = title
                    add_log(i, header[ci["title"]], "", title, f"{col}から外した役職を移動", "title_moved")
            elif st == "review" and opt["bad_delete"]:
                add_log(i, col, r[pi], "", "名前として長すぎるため削除", "person_deleted")
                r[pi] = ""
            elif st == "review":
                add_log(i, col, r[pi], r[pi], "名前が長すぎる(要確認)", "person_review")
        if opt["zip"] and ci["zip"] is not None and zip_tools is not None and ci["addr"] is not None:
            pref = r[ci["pref"]] if ci["pref"] is not None else ""
            before = r[ci["zip"]]
            v, st = zip_tools.fix(before, r[ci["addr"]], pref)
            if st == "filled":
                add_log(i, header[ci["zip"]], before, v, "郵便番号を住所から補完", "zip_filled")
            elif st == "refilled":
                add_log(i, header[ci["zip"]], before, v, "郵便番号の都道府県が住所と違うため引き直し", "zip_refilled")
            elif st == "fixed":
                add_log(i, header[ci["zip"]], before, v, "郵便番号の表記を統一", "zip_format")
            elif st == "review" and opt["bad_delete"]:
                add_log(i, header[ci["zip"]], before, "", "郵便番号が不正なため削除", "zip_deleted")
                v = ""
            elif st == "review":
                add_log(i, header[ci["zip"]], before, before, "郵便番号が不正(要確認)", "zip_review")
            elif st == "unfilled":
                counts["zip_unfilled"] += 1
            r[ci["zip"]] = v
        if opt["tel_nohyphen"] and ci["tel"] is not None:
            tel = r[ci["tel"]]
            if tel.strip() and not re.search(r"[-‐−–—ー―]", nfkc(tel)):
                add_log(i, header[ci["tel"]], tel, "", "ハイフンの無いTELを削除", "tel_nohyphen")
                r[ci["tel"]] = ""
                tel_why[i] = "ハイフンなし"
        if opt["tel_bad"] and ci["tel"] is not None and r[ci["tel"]].strip():
            tel = r[ci["tel"]]
            d = re.sub(r"\D", "", nfkc(tel))
            if len(d) not in (10, 11) or not d.startswith("0"):
                add_log(i, header[ci["tel"]], tel, "", "TELの桁数・形式が不正なため削除", "tel_bad")
                r[ci["tel"]] = ""
                tel_why[i] = "桁数不正"
        fmt = getattr(area_checker, "formatter", None)
        if opt["tel_format"] and fmt is not None and ci["tel"] is not None and r[ci["tel"]].strip():
            tel = r[ci["tel"]]
            nt = fmt.format(tel)
            if nt and nt != tel:
                had = bool(re.search(r"[-‐−–—ー―]", nfkc(tel)))
                add_log(i, header[ci["tel"]], tel, nt, "TELのハイフン位置を修正" if had else "TELにハイフンを追加", "tel_format")
                r[ci["tel"]] = nt
            elif not nt:
                add_log(i, header[ci["tel"]], tel, "", "市外局番を判定できずハイフンを追加できないため削除", "tel_unformattable")
                r[ci["tel"]] = ""
                tel_why[i] = "ハイフン追加不可"
        if opt["tel_area"] and ci["tel"] is not None and area_checker is not None and ci["addr"] is not None and r[ci["tel"]].strip():
            tel = r[ci["tel"]]
            pref = r[ci["pref"]] if ci["pref"] is not None else ""
            res, note = area_checker.check(tel, r[ci["addr"]], pref)
            if res.startswith("ng") and not getattr(area_checker, "official", False):
                res = "review"  # 公式の市外局番一覧が無いときは消さずに要確認止まり
            if res in ("ng_pref", "ng_city"):
                add_log(i, header[ci["tel"]], tel, "", f"市外局番と住所が不一致: {note}", "tel_deleted")
                r[ci["tel"]] = ""
                tel_why[i] = "市外局番と住所が不一致"
            elif res == "review":
                add_log(i, header[ci["tel"]], tel, tel, f"市外局番と住所の整合を要確認: {note}", "tel_review")

    # --- 除外行(理由の早いものから判定)
    # チェーン扱いの件数: 自動ならリスト件数の0.05%(最低3件)。画面で数字を入れたらそれを使う
    chain_min = int((options or {}).get("chain_min") or 0) or max(3, -(-len(body) * 5 // 10000))
    brands = [brand_of(r[ci["name"]]) for r in body] if ci["name"] is not None else []
    chains = {b: n for b, n in Counter(b for b in brands if b).items() if n >= chain_min}
    cand = Counter()  # ルールをONにしたら何件消えるか(OFFでも数える)
    if ci["name"] is not None:
        cand["store_name"] = sum(1 for r in body if STORE_RE.search(strip_name(r[ci["name"]])))
        cand["chain"] = sum(1 for b in brands if b in chains)
    if ci["tel"] is not None:
        cand["need_tel"] = sum(1 for t in orig_tel if not t.strip())  # 元から空欄のもの
        cand["tel_freedial"] = sum(1 for r in body if tel_key(r[ci["tel"]])[:4] in ("0120", "0800"))
        cand["tel_050"] = sum(1 for r in body if tel_key(r[ci["tel"]]).startswith("050"))
    removed, kept = [], []
    seen_tel = set()
    ex_hits = Counter()
    for i, r in enumerate(body):
        reason = ""
        tk = tel_key(r[ci["tel"]]) if ci["tel"] is not None else ""
        if opt["exclude"] and excludes:
            nk = name_key(r[ci["name"]]) if ci["name"] is not None else ""
            pref = r[ci["pref"]] if ci["pref"] is not None else (guess_pref(nfkc(r[ci["addr"]])) if ci["addr"] is not None else "")
            ctk = city_of(r[ci["addr"]]) if ci["addr"] is not None else ""
            if pref and ctk.startswith(pref):
                ctk = ctk[len(pref):]
            otk = tel_key(orig_tel[i]) if orig_tel is not None else ""  # 削除前のTELでも照合
            for ex in excludes:
                how = ""
                if "tel" in exclude_keys and ((tk and tk in ex["tels"]) or (otk and otk in ex["tels"])):
                    how = "TEL"
                elif "name_city" in exclude_keys and nk and ctk and (nk, ctk) in ex["name_city"]:
                    how = "名称+市区町村"
                elif "name_city" in exclude_keys and nk and not ex["name_city"] and (nk, pref) in ex["name_pref"]:
                    how = "名称+都道府県"
                elif "name" in exclude_keys and nk and nk in ex["names"]:
                    how = "名称"
                if how:
                    reason = f"除外リスト「{ex['label']}」と{how}が一致"
                    ex_hits[ex["label"]] += 1
                    break
        if not reason and opt["tel_deleted_row"] and orig_tel is not None and orig_tel[i].strip() and not r[ci["tel"]].strip():
            reason = f"TEL削除({tel_why.get(i, '不正な番号')})"
        if not reason and opt["need_tel"] and ci["tel"] is not None and not r[ci["tel"]].strip():
            reason = "TEL空欄"
        if not reason and opt["tel_freedial"] and tk[:4] in ("0120", "0800"):
            reason = "フリーダイヤル"
        if not reason and opt["tel_050"] and tk.startswith("050"):
            reason = "050番号"
        if not reason and opt["store_name"] and ci["name"] is not None and STORE_RE.search(strip_name(r[ci["name"]])):
            reason = "名称が〇〇店"
        if not reason and opt["chain"] and brands and brands[i] in chains:
            reason = f"チェーン店({brands[i]})"
        if not reason and opt["area_out"] and area and ci["pref"] is not None and r[ci["pref"]] not in area:
            reason = f"エリア外({r[ci['pref']]})"
        if not reason and opt["period_out"] and periods:
            for j, (per, _) in periods.items():
                ym = parse_ym(r[j])
                if ym and not in_period(ym, per):
                    reason = f"期間外({header[j]})"
                    break
        if not reason and opt["name_blank"] and ci["name"] is not None and not r[ci["name"]].strip():
            reason = "名称が空欄"
        if not reason and opt["mojibake"] and any("\ufffd" in c for c in r):
            reason = "文字化け"
        if not reason and opt["tel_dup"] and tk:
            if tk in seen_tel:
                reason = "TEL重複(2件目以降)"
            seen_tel.add(tk)
        if reason:
            if orig_tel is not None and not r[ci["tel"]].strip():
                r = r[:ci["tel"]] + [orig_tel[i]] + r[ci["tel"] + 1:]  # 除外行には確認できるよう元のTELを残す
            removed.append(r + [reason])
            counts["removed:" + reason.split("(")[0].split("「")[0]] += 1
            if reason.startswith("期間外("):
                counts["removed:" + reason] += 1  # 列ごとの件数も残す
        else:
            kept.append(r)
    body = kept

    report = build_report(header, body, ci, issue, filename, n_in, counts, samples, removed, need_tel, area_checker,
                          (periods, date_idx))
    report["chain_min_used"] = chain_min
    report["date_cols"] = []
    for j in date_idx:
        months = Counter()
        unparsed = 0
        for r in body:
            if not r[j].strip():
                continue
            ym = parse_ym(r[j])
            if ym:
                months[f"{ym[0]:04d}-{ym[1]:02d}"] += 1
            else:
                unparsed += 1
        per = periods.get(j)
        report["date_cols"].append({
            "name": header[j], "rows": len(body), "filled": sum(1 for r in body if r[j].strip()),
            "unparsed": unparsed, "months": dict(sorted(months.items())),
            "req": ({"start": f"{per[0][0][0]:04d}-{per[0][0][1]:02d}" if per[0][0] else "",
                     "end": f"{per[0][1][0]:04d}-{per[0][1][1]:02d}" if per[0][1] else "", "from": per[1]} if per else None),
        })
    report["options"] = {**opt, "exclude_keys": sorted(exclude_keys), "chain_min": (options or {}).get("chain_min") or 0,
                         "group_off": (options or {}).get("group_off") or [],
                         "tel_col": header[ci["tel"]] if ci["tel"] is not None else None,
                         "addr_col": header[ci["addr"]] if ci["addr"] is not None else None}
    report["candidates"] = dict(cand)
    report["chains"] = sorted(chains.items(), key=lambda x: -x[1])[:30]
    report["chain_brand_count"] = len(chains)
    if excludes:
        pos = next((k for k, c in enumerate(report["checks"]) if c["id"] == "sample"), len(report["checks"]))
        det = " / ".join(f"{e['label']}（{e['rows']:,}件）→ 一致 {ex_hits.get(e['label'], 0):,}件" for e in excludes)
        det += "" if opt["exclude"] else "（除外ルールOFFのため未適用）"
        report["checks"].insert(pos, {"id": "exclude", "group": "共通", "title": "除外リスト（現アナ等）の照合",
                                      "status": "info", "detail": det})
    report["excludes"] = [{"label": e["label"], "rows": e["rows"], "hits": ex_hits.get(e["label"], 0), "cols": e["cols"]} for e in excludes]
    report["removed_reasons"] = Counter(re.sub(r"^チェーン店\(.*\)$", "チェーン店", x[-1]) for x in removed).most_common()
    # 列の並び替え・出力しない列(options["columns"] = [{"name":列名,"on":bool}, ...] の順)
    conf = (options or {}).get("columns") or []
    order, seen = [], set()
    for c in conf:
        if c.get("name") in header and c["name"] not in seen:
            seen.add(c["name"])
            order.append((header.index(c["name"]), c.get("on", True) is not False))
    for j, h in enumerate(header):
        if h not in seen:
            order.append((j, True))
    idx = [j for j, on in order if on]
    rename = {c["name"]: c["rename"].strip() for c in conf if c.get("rename") and c["rename"].strip()}
    report["column_config"] = [{"name": header[j], "on": on, **({"rename": rename[header[j]]} if header[j] in rename else {})}
                               for j, on in order]
    stats = {c["name"]: c for c in report["columns"]}
    report["column_stats"] = stats
    report["columns"] = [stats[header[j]] for j in idx]
    out_header = [rename.get(header[j], header[j]) for j in idx]
    out_rows = [out_header] + [[r[j] for j in idx] for r in body]
    removed_rows = ([out_header + ["除外理由"]] + [[r[j] for j in idx] + [r[-1]] for r in removed]) if removed else []
    return out_rows, removed_rows, [["行番号(元ファイル)", "列", "修正前", "修正後", "理由"]] + log, report


def build_report(header, body, ci, issue, filename, n_in, counts, samples, removed, need_tel, area_checker, period_info=(None, None)):
    n = len(body)
    cols = []
    for j, h in enumerate(header):
        filled = sum(1 for r in body if r[j].strip())
        cols.append({"name": h, "filled": filled, "pct": pct(filled, n), "industry": "業種" in h})
    industry = []
    for j, h in enumerate(header):
        if "業種" in h:
            c = Counter(r[j].strip() for r in body if r[j].strip())
            industry.append({"name": h, "filled": sum(c.values()), "pct": pct(sum(c.values()), n),
                             "kinds": len(c), "top": c.most_common(12)})
    # 都道府県別の業種充足率(最初の業種列)
    pref_ind = []
    ind_idx = next((j for j, h in enumerate(header) if "業種" in h), None)
    if ci["pref"] is not None:
        pc = Counter(r[ci["pref"]] for r in body)
        for p, cnt in pc.most_common():
            f = sum(1 for r in body if r[ci["pref"]] == p and ind_idx is not None and r[ind_idx].strip())
            pref_ind.append({"pref": p, "rows": cnt, "industry_pct": pct(f, cnt) if ind_idx is not None else None})

    checks = auto_checks(header, body, ci, issue, filename, n, need_tel, counts, area_checker, n_in, period_info)
    return {
        "filename": filename,
        "out_filename": output_name(filename, issue, n),
        "rows_in": n_in, "rows_out": n, "removed": len(removed),
        "counts": dict(counts), "samples": samples,
        "columns": cols, "industry": industry, "pref_industry": pref_ind,
        "checks": checks,
        "area_official": bool(getattr(area_checker, "official", None)) if area_checker else None,
    }


def output_name(filename, issue, n):
    """納品ファイル名。課題があれば「20260925【STREAMREQ-15281】関東　遺品整理_1230件.csv」"""
    import datetime
    if issue and issue.get("key"):
        title = re.sub(r'[\\/:*?"<>|\r\n\t]', "", issue.get("summary") or "").strip()
        return f"{datetime.date.today():%Y%m%d}【{issue['key']}】{title}_{n}件.csv"
    base = os.path.splitext(filename)[0]
    base = re.sub(r"_(\d+)件", f"_{n}件", base) if re.search(r"_(\d+)件", base) else f"{base}_{n}件"
    return base + ".csv"


def _row_texts(body, idxs):
    return [nfkc(" ".join(r[j] for j in idxs)) for r in body]


def auto_checks(header, body, ci, issue, filename, n, need_tel, counts, area_checker, n_in, period_info=(None, None)):
    items = []

    def add(cid, group, title, status, detail):
        # 共通チェックは自動処理の結果(備考)。サンプル目視とリスト詳細は人がチェックする項目
        items.append({"id": cid, "group": group, "title": title, "status": status, "detail": detail,
                      "auto": group == "共通" and cid != "sample"})

    media_idx = [j for j, h in enumerate(header) if any(k in h for k in ("媒体", "掲載", "サイト", "URL"))]
    search_idx = media_idx or list(range(len(header)))
    row_texts = None
    area_prefs_all = None

    # --- リスト詳細の各行
    for k, line in enumerate((issue or {}).get("details", [])):
        parts = [p.strip() for p in re.split(r"[、,，]", nfkc(line)) if p.strip()]
        sub = []
        status = "manual"
        per = parse_period(line)
        if per:
            pidx = pick_period_col(line, header, period_info[1] or [])
            label = f"{ym_label(per[0])}～{ym_label(per[1])}"
            if pidx is None:
                sub.append(f"期間 {label}: 開業・設立時期の列なし（目視）")
                status = "ng"
            else:
                vals = [parse_ym(r[pidx]) for r in body]
                blank = sum(1 for r in body if not r[pidx].strip())
                inn = sum(1 for v in vals if v and in_period(v, per))
                out = sum(1 for v in vals if v and not in_period(v, per))
                sub.append(f"期間 {label}（{header[pidx]}）: 期間内 {inn:,} / 期間外 {out:,} / 空欄 {blank:,}件")
                if out or blank:
                    status = "ng"
            parts = [p for p in parts if not parse_period(p)]
        for p in parts:
            if re.search(r"電話番号|TEL", p):
                if ci["tel"] is None:
                    sub.append("TEL列なし"); status = "ng"
                else:
                    f = sum(1 for r in body if r[ci["tel"]].strip())
                    sub.append(f"TEL充足 {f:,}/{n:,}件 ({pct(f, n)}%)")
                    if f < n:
                        status = "ng"
            elif parse_area(p):
                prefs = parse_area(p)
                area_prefs_all = (area_prefs_all or set()) | prefs
                if ci["pref"] is not None:
                    out = Counter(r[ci["pref"]] for r in body if r[ci["pref"]] not in prefs)
                    sub.append(f"エリア外 {sum(out.values()):,}件" + (f"（{'、'.join(f'{a}{b}' for a, b in out.most_common(5))}）" if out else ""))
                    if out:
                        status = "ng"
                else:
                    sub.append("都道府県列なし(エリア目視)")
            else:
                kw = re.sub(r"[（(].*?[）)]", "", p)
                kw = re.sub(r"(求人)?(掲載|出稿|利用|あり|有り)$", "", kw).strip()
                # 媒体名などの短いキーワードだけ数える(「MEO対策している学習塾。」のような文章は目視)
                if media_idx and 2 <= len(kw) <= 12 and not re.search(r"[。．]|して|する|いる", kw):
                    if row_texts is None:
                        row_texts = _row_texts(body, search_idx)
                    f = sum(1 for t in row_texts if kw in t)
                    sub.append(f"「{kw}」該当 {f:,}件")
                if "プレミアム" in p:
                    if ci["premium"] is not None:
                        pf = sum(1 for r in body if r[ci["premium"]].strip())
                        sub.append(f"プレミアム {pf:,}件 ({pct(pf, n)}%)")
                    else:
                        sub.append("プレミアム列なし")
            if status == "manual" and sub:
                status = "info"
        if status == "manual" and sub:
            status = "info"
        if status == "info":
            status = "ng" if any("該当 0件" in s for s in sub) else "ok"
        pcol = pick_period_col(line, header, period_info[1] or []) if parse_period(line) else None
        rm = {"エリア外": counts.get("removed:エリア外", 0),
              "期間外": counts.get(f"removed:期間外({header[pcol]})", 0) if pcol is not None else 0,
              "TELなし": counts.get("removed:TELなし", 0)}
        done = [f"{k}で除外済み {v:,}件" for k, v in rm.items() if v and (k in " ".join(sub) or (k == "TELなし" and "TEL" in " ".join(sub)))]
        add(f"detail{k}", "リスト詳細", line, status, " / ".join(sub + done))

    # --- 共通チェック
    m = re.search(r"_(\d+)件", filename)
    if m:
        want = int(m.group(1))
        add("rows", "共通", "ファイル名の件数と実件数", "ok" if want == n_in else "ng",
            f"ファイル名 {want:,}件 / 元ファイル実件数 {n_in:,}件" + (f" → 修正後 {n:,}件（出力ファイル名を更新済み）" if n != n_in else ""))
    if ci["name"] is not None:
        blank = sum(1 for r in body if not r[ci["name"]].strip())
        add("name", "共通", "名称の空欄", "ok" if blank == 0 else "ng",
            f"空欄 残り {blank:,}件（除外 {counts.get('removed:名称が空欄', 0):,}件）")
        eq = sum(1 for r in body if re.match(r"^[\s=＝]", r[ci["name"]]))
        other = [r[ci["name"]] for r in body if re.match(r"^[+＋@＠\-]", r[ci["name"]])]
        add("name_eq", "共通", "名称の先頭の「=」（Excelで数式扱いになる）", "ok" if eq == 0 else "ng",
            f"残り {eq:,}件（自動削除 {counts.get('name_eq', 0):,}件）"
            + (f" ／ 先頭が「+」「@」「-」の名称 {len(other)}件（Excelで崩れる可能性・目視）: {'、'.join(other[:5])}" if other else ""))
    if ci["tel"] is not None:
        tels = [re.sub(r"\D", "", r[ci["tel"]]) for r in body if r[ci["tel"]].strip()]
        dup = sum(v - 1 for v in Counter(tels).values() if v > 1)
        bad = sum(1 for t in tels if len(t) not in (10, 11))
        add("tel_dup", "共通", "TELの重複・桁数", "ok" if dup == 0 and bad == 0 else "ng",
            f"重複 残り {dup:,}件（除外 {counts.get('removed:TEL重複', 0):,}件） / 桁数異常 残り {bad:,}件（削除 {counts.get('tel_bad', 0):,}件）")
        nohy = sum(1 for r in body if r[ci["tel"]].strip() and not re.search(r"[-‐−–—ー―]", nfkc(r[ci["tel"]])))
        add("tel_hyphen", "共通", "TELのハイフン", "ok" if nohy == 0 else "ng",
            f"ハイフンなし 残り {nohy:,}件（自動削除 {counts.get('tel_nohyphen', 0):,}件）")
        if area_checker is not None:
            d, rv = counts.get("tel_deleted", 0), counts.get("tel_review", 0)
            src = "総務省の市外局番一覧" if getattr(area_checker, "official", False) else "ハローワーク実績(参考判定)"
            add("tel_area", "共通", "市外局番と住所の整合", "ok" if rv == 0 else "info",
                f"不一致で削除 {d:,}件 / 要確認 {rv:,}件（判定元: {src}）")
    if ci["zip"] is not None:
        f = sum(1 for r in body if r[ci["zip"]].strip())
        add("zip", "共通", "郵便番号の充足", "ok" if counts.get("zip_review", 0) == 0 else "info",
            f"{f:,}/{n:,}件 ({pct(f, n)}%) うち自動補完 {counts.get('zip_filled', 0):,}件 / 引き直し {counts.get('zip_refilled', 0):,}件"
            f" / 不正で削除 {counts.get('zip_deleted', 0):,}件")
    else:
        add("zip", "共通", "郵便番号", "ok", "郵便番号列なし（追加・補完はしない設定）")
    if ci["capital"] is not None:
        add("capital", "共通", "資本金の表記統一", "ok" if counts.get("capital_review", 0) == 0 else "info",
            f"自動修正 {counts.get('capital_fixed', 0):,}件 / 直せず削除 {counts.get('capital_deleted', 0):,}件 / 要確認 {counts.get('capital_review', 0):,}件")
    if ci["employees"] is not None:
        add("employees", "共通", "従業員数の表記統一", "ok" if counts.get("emp_review", 0) == 0 else "info",
            f"自動修正 {counts.get('emp_fixed', 0):,}件 / 直せず削除 {counts.get('emp_deleted', 0):,}件 / 要確認 {counts.get('emp_review', 0):,}件")
    if any(c in header for c in PERSON_COLS):
        add("person", "共通", "代表者名・担当者名が名前のみ", "ok" if counts.get("person_review", 0) == 0 else "info",
            f"整形 {counts.get('person_fixed', 0):,}件 / 空欄化 {counts.get('person_blank', 0) + counts.get('person_deleted', 0):,}件 / 要確認 {counts.get('person_review', 0):,}件")
    ind = [j for j, h in enumerate(header) if "業種" in h]
    if ind:
        f = sum(1 for r in body if r[ind[0]].strip())
        add("industry", "共通", "業種の充足", "info", f"{header[ind[0]]} {f:,}/{n:,}件 ({pct(f, n)}%)")
    if ci["pref"] is not None and ci["addr"] is not None:
        mis = sum(1 for r in body if r[ci["pref"]] and guess_pref(nfkc(r[ci["addr"]])) not in ("", r[ci["pref"]]))
        add("pref_addr", "共通", "都道府県列と住所の一致", "ok" if mis == 0 else "ng",
            f"不一致 残り {mis:,}件（住所に合わせて修正 {counts.get('pref_fixed', 0):,}件）")
    moji = sum(1 for r in body if any("�" in c for c in r))
    add("mojibake", "共通", "文字化け", "ok" if moji == 0 else "ng",
        f"文字化けを含む行 残り {moji:,}件（除外 {counts.get('removed:文字化け', 0):,}件）")
    add("sample", "共通", "サンプル目視（ランダム20件程度をHP・掲載ページで確認）", "manual", "")
    return items
