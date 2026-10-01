# -*- coding: utf-8 -*-
"""市外局番 × 市区町村 の対応表を作る・引く。

ハローワークCSV(約79万件)の「TEL(ハイフン付き)」と「住所」から、
市外局番ごとにどの市区町村で使われているかを集計し cache/areacode.json に保存する。
元CSVのサイズ・更新日時が変わったら自動で作り直す。

市区町村の判定には日本郵便の utf_ken_all.csv の市区町村名を使う
(政令指定都市は区をまとめて市単位、郡名は省略された住所にも対応)。
"""
import csv
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict

csv.field_size_limit(10 ** 9)

PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]

# 地域に紐づかない番号(携帯・IP電話・フリーダイヤル等)は判定対象外
NON_GEO = ("070", "080", "090", "050", "060", "020", "0120", "0800", "0570", "0180", "0990")


# 旧市町村名 → 現在の名称(総務省一覧が旧名のまま・住所が旧名のことがあるもの)
OLD_NAMES = {"篠山市": "丹波篠山市"}


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "").replace("ヶ", "ケ").replace("ヵ", "カ")


def tel_digits(s):
    return re.sub(r"\D", "", nfkc(s))


def area_code(tel):
    """ハイフン区切りの先頭を市外局番とみなす。判定できなければ ''"""
    t = nfkc(tel).strip()
    t = re.sub(r"[‐−–—ー―()（）\s]", "-", t)
    parts = [p for p in t.split("-") if p]
    if len(parts) == 3 and all(p.isdigit() for p in parts) and parts[0].startswith("0"):
        return parts[0]
    return ""


def is_non_geo(tel):
    d = tel_digits(tel)
    return any(d.startswith(p) for p in NON_GEO)


class CityResolver:
    """住所 → (都道府県, 市区町村キー)。キーは政令市なら市、東京23区は区、郡は省く。"""

    def __init__(self, kenall_path):
        by_pref = defaultdict(dict)  # pref -> {表記: key}
        with open(kenall_path, encoding="utf-8", newline="") as f:
            for r in csv.reader(f):
                if len(r) < 9:
                    continue
                pref, city = r[6], nfkc(r[7]).replace(" ", "")
                m = re.match(r"^(.+?市)(.+区)$", city)
                key = m.group(1) if m else city
                isl = re.match(r"^(.+?島)(.+[町村])$", city) if pref == "東京都" else None
                if isl:
                    key = isl.group(2)
                key = re.sub(r"^.+?郡(?=.+[町村]$)", "", key)
                names = {city, key} | {o for o, n in OLD_NAMES.items() if n == key}
                if m:
                    names.add(m.group(1))
                g = re.match(r"^.+?郡(.+[町村])$", city)
                if g:
                    names.add(g.group(1))
                for n in names:
                    by_pref[pref].setdefault(n, key)
        self.by_pref = {p: sorted(d.items(), key=lambda x: -len(x[0])) for p, d in by_pref.items()}

    @staticmethod
    def guess_pref(addr):
        for p in PREFECTURES:
            if addr.startswith(p):
                return p
        return ""

    def resolve(self, addr, pref_hint=""):
        a = re.sub(r"\s+", "", nfkc(addr))
        pref = pref_hint if pref_hint in self.by_pref else self.guess_pref(a)
        if not pref:
            return "", ""
        if a.startswith(pref):
            a = a[len(pref):]
        for name, key in self.by_pref.get(pref, []):
            if a.startswith(name):
                return pref, key
        return pref, ""


def _signature(path):
    st = os.stat(path)
    return f"{os.path.basename(path)}:{st.st_size}:{int(st.st_mtime)}"


def build(hw_csv, kenall_path, cache_path, log=print):
    resolver = CityResolver(kenall_path)
    counts = defaultdict(Counter)
    n = 0
    with open(hw_csv, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        h = next(rd)
        i_tel, i_addr, i_pref = h.index("TEL"), h.index("住所"), h.index("都道府県")
        for r in rd:
            n += 1
            if n % 100000 == 0:
                log(f"  市外局番辞書を作成中... {n:,}件")
            if len(r) <= max(i_tel, i_addr, i_pref):
                continue
            ac = area_code(r[i_tel])
            if not ac or is_non_geo(r[i_tel]):
                continue
            p, c = resolver.resolve(r[i_addr], r[i_pref])
            if p and c:
                counts[ac][f"{p}|{c}"] += 1
    data = {"signature": _signature(hw_csv), "codes": {k: dict(v) for k, v in counts.items()}}
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    log(f"  市外局番辞書を作成しました({len(counts):,}局番 / {n:,}件)")
    return data


# 地域に紐づかない番号の区切り方(先頭, 区切りの長さ)
NON_GEO_FORMAT = [("0120", (4, 3, 3)), ("0800", (4, 3, 4)), ("0570", (4, 3, 3)), ("0990", (4, 3, 3)), ("0180", (4, 3, 3)),
                  ("070", (3, 4, 4)), ("080", (3, 4, 4)), ("090", (3, 4, 4)), ("050", (3, 4, 4)), ("060", (3, 4, 4)),
                  ("020", (3, 4, 4))]


def build_prefix(hw_csv, cache_path, log=print):
    """ハローワークの正しくハイフンが入ったTELから「番号の先頭6桁/5桁 → 市外局番」を作る。
    固定電話は 0+市外局番+市内局番 が必ず6桁なので、先頭6桁で市外局番が決まる
    (04-7xxx(柏) と 047-xxx(船橋) のように数字だけでは紛らわしいものを見分けるため)。"""
    p6, p5 = defaultdict(Counter), defaultdict(Counter)
    with open(hw_csv, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        h = next(rd)
        i_tel = h.index("TEL")
        for r in rd:
            if len(r) <= i_tel:
                continue
            ac = area_code(r[i_tel])
            d = tel_digits(r[i_tel])
            if ac and len(d) == 10 and not is_non_geo(d):
                p6[d[:6]][ac] += 1
                p5[d[:5]][ac] += 1

    def major(m):
        return {k: c.most_common(1)[0][0] for k, c in m.items() if c.most_common(1)[0][1] >= max(1, sum(c.values()) * 0.8)}
    data = {"signature": _signature(hw_csv), "p6": major(p6), "p5": major(p5)}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    log(f"  TEL区切り辞書を作成しました(6桁 {len(data['p6']):,} / 5桁 {len(data['p5']):,})")
    return data


class TelFormatter:
    """TELの数字から市外局番を判定し、正しい位置にハイフンを入れる"""

    def __init__(self, hw_csv, cache_path, official_codes=(), log=print):
        data = None
        if os.path.isfile(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            if hw_csv and os.path.isfile(hw_csv) and data.get("signature") != _signature(hw_csv):
                data = None
        if data is None and hw_csv and os.path.isfile(hw_csv):
            data = build_prefix(hw_csv, cache_path, log)
        self.p6 = (data or {}).get("p6", {})
        self.p5 = (data or {}).get("p5", {})
        self.codes = sorted(set(official_codes) | set(self.p6.values()), key=len, reverse=True)

    def area_code_of(self, digits):
        d = digits
        if len(d) != 10 or not d.startswith("0") or is_non_geo(d):
            return ""
        ac = self.p6.get(d[:6]) or self.p5.get(d[:5])
        if ac and d.startswith(ac):
            return ac
        for c in self.codes:  # 実績の無い番号帯は公式の局番で最長一致
            if d.startswith(c):
                return c
        return ""

    def format(self, tel):
        """正しい区切りのTEL。判定できなければ None"""
        d = tel_digits(tel)
        for head, lens in NON_GEO_FORMAT:
            if d.startswith(head) and len(d) == sum(lens):
                a, b = lens[0], lens[0] + lens[1]
                return f"{d[:a]}-{d[a:b]}-{d[b:]}"
        ac = self.area_code_of(d)
        if not ac:
            return None
        return f"{ac}-{d[len(ac):6]}-{d[6:]}"


class AreaCodeChecker:
    """official_path(総務省の市外局番一覧をCSV化したもの: 市外局番,都道府県,市区町村)があればそれを正とし、
    無ければハローワーク実績で参考判定する(この場合は削除せず要確認止まり)。"""

    def __init__(self, hw_csv, kenall_path, cache_path, log=print, official_path=None, formatter=None):
        self.resolver = CityResolver(kenall_path)
        self.formatter = formatter
        self.official = None
        if official_path and os.path.isfile(official_path):
            self.official = defaultdict(set)
            with open(official_path, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    self.official[r["市外局番"]].add((r["都道府県"], r["市区町村"]))
            log(f"  総務省の市外局番一覧を使用({len(self.official)}局番)")
        data = None
        if os.path.isfile(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            if hw_csv and os.path.isfile(hw_csv) and data.get("signature") != _signature(hw_csv):
                data = None
        if data is None and self.official is None:
            if not hw_csv or not os.path.isfile(hw_csv):
                raise FileNotFoundError(f"ハローワークCSVが見つかりません: {hw_csv}")
            data = build(hw_csv, kenall_path, cache_path, log)
        self.codes = {k: Counter(v) for k, v in (data or {"codes": {}})["codes"].items()}
        self.pref_tot = {}
        self.city_tot = Counter()
        for ac, c in self.codes.items():
            pt = Counter()
            for pc, v in c.items():
                pt[pc.split("|")[0]] += v
                self.city_tot[pc] += v
            self.pref_tot[ac] = pt

    def check(self, tel, addr, pref_hint=""):
        """(判定, 詳細) を返す。判定: ok / skip / review / ng_pref / ng_city"""
        if not tel.strip():
            return "skip", "TELなし"
        if is_non_geo(tel):
            return "skip", "携帯・IP・フリーダイヤル等"
        # ハイフンの位置は間違っていることがあるので、数字の並びから市外局番を判定する
        ac = self.formatter.area_code_of(tel_digits(tel)) if self.formatter else area_code(tel)
        if not ac:
            return "skip", "市外局番を判別できない番号"
        pref, city = self.resolver.resolve(addr, pref_hint)
        if self.official is not None:
            return self._check_official(ac, pref, city)
        c = self.codes.get(ac)
        if not c or sum(c.values()) < 10:
            return "skip", f"{ac} の実績データ不足"
        where = self.top_areas(ac)
        if not pref:
            return "skip", "住所から都道府県を判別できない"
        pt = self.pref_tot[ac]
        total = sum(pt.values())
        if pt.get(pref, 0) / total < 0.01:
            return "ng_pref", f"{ac} は {where} の局番（住所は{pref}）"
        if not city:
            return "skip", "住所から市区町村を判別できない"
        cnt = c.get(f"{pref}|{city}", 0)
        if cnt >= 2:
            return "ok", ""
        if cnt == 0 and self.city_tot.get(f"{pref}|{city}", 0) >= 5:
            return "ng_city", f"{ac} は {where} の局番（住所は{pref}{city}）"
        return "review", f"{ac} で{pref}{city}の実績が少ない（{where}）"

    def _check_official(self, ac, pref, city):
        areas = self.official.get(ac)
        if not areas:
            return "review", f"{ac} は市外局番一覧に無い番号"
        where = "・".join(sorted({p + c for p, c in areas})[:4]) + ("ほか" if len(areas) > 4 else "")
        if not pref:
            return "skip", "住所から都道府県を判別できない"
        if pref not in {p for p, _ in areas}:
            return "ng_pref", f"{ac} は {where} の局番（住所は{pref}）"
        if not city:
            return "skip", "住所から市区町村を判別できない"
        if (pref, city) in areas:
            return "ok", ""
        return "ng_city", f"{ac} は {where} の局番（住所は{pref}{city}）"

    def top_areas(self, ac, n=3):
        c = self.codes.get(ac, Counter())
        return "・".join(pc.replace("|", "") for pc, _ in c.most_common(n))
