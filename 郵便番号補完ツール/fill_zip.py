# -*- coding: utf-8 -*-
"""
郵便番号補完ツール

input フォルダに入れたCSV(住所・郵便番号の列を含むもの)を1件ずつ読み込み、
郵便番号が空欄の行を、日本郵便公式の郵便番号データ(data/utf_ken_all.csv)を
使って住所(町域名)から補完し、output フォルダに保存する。
処理済みの元ファイルは input_done フォルダに移動する。

前提とする列名(どちらも無ければそのファイルはスキップしてエラー表示):
  - 住所      : 郵便番号を調べる元になる住所の列
  - 郵便番号  : 埋める対象の列(既に値がある行は上書きしない)
  - 都道府県  : あれば優先的に使う(無ければ住所の先頭から都道府県名を推定)

使い方:
  1. input フォルダに対象CSVを置く
  2. 郵便番号補完.bat をダブルクリック
  3. output フォルダに「元のファイル名_郵便番号補完済.csv」が出力される
  4. 元のCSVは input_done フォルダに移動される

郵便番号データの更新方法:
  日本郵便のダウンロードページ https://www.post.japanpost.jp/zipcode/dl/utf-zip.html から
  「住所の郵便番号(1レコード1行、UTF-8形式)」の最新データ(zip)をダウンロードして展開し、
  data/utf_ken_all.csv を置き換える。
"""
import csv
import glob
import os
import re
import shutil
import sys
import unicodedata

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(SCRIPT_DIR, "input")
INPUT_DONE_DIR = os.path.join(SCRIPT_DIR, "input_done")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
KENALL_PATH = os.path.join(SCRIPT_DIR, "data", "utf_ken_all.csv")

KENALL_COLS = ["zenkoku_code", "old_zip", "zip", "pref_kana", "city_kana", "town_kana",
               "pref", "city", "town", "f1", "f2", "f3", "f4", "f5", "f6"]

PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]

NUM = r"[0-9０-９一二三四五六七八九十]+"
SEP = r"[〜~～、,・]"


def norm(s):
    if not isinstance(s, str) or not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", "", s)


def kanji_num(s):
    m = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if s.isdigit():
        return int(s)
    if "十" in s:
        parts = s.split("十")
        left = m.get(parts[0], 1) if parts[0] else 1
        right = m.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return left * 10 + right
    return m.get(s, None)


def parse_chome_list(bracket_text):
    """括弧内の文字列から丁目番号のリストを抽出する。無ければNone
    「１、２丁目」のような列挙と「１〜３丁目」「１～３丁目」のような範囲の両方に対応。
    """
    if not bracket_text:
        return None
    nums = set()
    for m in re.finditer(rf"((?:{NUM}{SEP})*{NUM})丁目", bracket_text):
        group = m.group(1)
        parts = re.split(SEP, group)
        seps = re.findall(SEP, group)
        if any(c in ("〜", "~", "～") for c in seps):
            vals = [kanji_num(unicodedata.normalize("NFKC", p)) for p in parts if p]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2:
                nums.update(range(min(vals), max(vals) + 1))
        else:
            for p in parts:
                if not p:
                    continue
                n = kanji_num(unicodedata.normalize("NFKC", p))
                if n is not None:
                    nums.add(n)
    return sorted(nums) or None


class ZipLookup:
    """都道府県ごとに町域名インデックスを持つ郵便番号逆引き"""

    def __init__(self, kenall_path):
        rows = []
        with open(kenall_path, encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            for r in reader:
                if len(r) < 9:
                    continue
                rows.append(dict(zip(KENALL_COLS, r)))

        # pref -> { base_town_n: [(city_n, zip, chome_list, bracket, is_default), ...] }
        self.town_index = {}
        # pref -> { city_n: zip }  ("以下に掲載がない場合"の代表郵便番号)
        self.city_fallback = {}

        for row in rows:
            pref = row["pref"]
            town = row["town"]
            city_n = norm(row["city"])
            base_town = re.split(r"[（(]", town)[0].strip()
            bracket_m = re.findall(r"[（(](.*)[）)]", town)
            bracket = bracket_m[0] if bracket_m else ""
            is_default = ("除く" in bracket) if bracket else True
            base_town_n = norm(base_town)
            chome_list = parse_chome_list(bracket)

            if town == "以下に掲載がない場合":
                self.city_fallback.setdefault(pref, {})[city_n] = row["zip"]

            if len(base_town_n) < 2:
                continue
            self.town_index.setdefault(pref, {}).setdefault(base_town_n, []).append(
                (city_n, row["zip"], chome_list, bracket, is_default)
            )

        self._sorted_towns = {
            pref: sorted(idx.keys(), key=len, reverse=True)
            for pref, idx in self.town_index.items()
        }

    def guess_pref(self, addr_n):
        for p in PREFECTURES:
            if addr_n.startswith(p):
                return p
        return None

    def find_zip(self, addr_raw, pref_hint=None):
        addr_n = norm(addr_raw)
        pref = pref_hint if pref_hint in self.town_index else None
        if pref is None:
            pref = self.guess_pref(addr_n)
        if pref is None:
            return None, "pref_unknown"
        addr_n = addr_n.replace(pref, "", 1)

        town_index = self.town_index.get(pref, {})
        sorted_towns = self._sorted_towns.get(pref, [])

        for tk in sorted_towns:
            pos = addr_n.find(tk)
            if pos == -1:
                continue
            candidates = town_index[tk]
            if len(candidates) == 1:
                return candidates[0][1], "town_unique"

            city_matched = [c for c in candidates if c[0] and c[0] in addr_n]
            pool = city_matched if city_matched else candidates

            rest = addr_n[pos + len(tk):]
            cm = re.match(r"([0-9０-９一二三四五六七八九十]+)", rest)
            chome_num = kanji_num(unicodedata.normalize("NFKC", cm.group(1))) if cm else None
            if chome_num is not None:
                for c, z, chome_list, bracket, is_default in pool:
                    if isinstance(chome_list, list) and chome_num in chome_list:
                        return z, "town_chome"

            for c, z, chome_list, bracket, is_default in pool:
                if bracket and len(bracket) >= 2 and bracket in addr_n:
                    return z, "town_building"

            defaults = [p for p in pool if p[4]]
            if defaults:
                return defaults[0][1], "town_ambiguous_default"
            return pool[0][1], "town_ambiguous"

        fb = self.city_fallback.get(pref, {})
        for city_n, z in fb.items():
            if city_n and city_n in addr_n:
                return z, "city_fallback"
        return None, "no_match"


def read_rows(path):
    """utf-8-sig / cp932 の順で読み込みを試す"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, encoding=enc, newline="") as f:
                reader = csv.reader(f)
                rows = list(reader)
            return rows, enc
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"{path} を読み込めませんでした(utf-8-sig / cp932 のいずれでもデコード不可)")


def find_address_zip_pairs(header):
    """
    「住所」列と「郵便番号」列のペアを、列名の接頭辞が一致するもの同士で自動検出する。
    例: 「住所」⇔「郵便番号」、「本社住所」⇔「本社郵便番号」、「勤務地住所」⇔「勤務地郵便番号」
    郵便番号側の列名だけが存在し、対応する住所列が無い場合はそのペアは対象外。
    """
    zip_cols = [(i, h) for i, h in enumerate(header) if h.endswith("郵便番号")]
    addr_cols = {h: i for i, h in enumerate(header) if h.endswith("住所")}

    pairs = []
    for zip_idx, zip_name in zip_cols:
        prefix = zip_name[: -len("郵便番号")]
        addr_name = f"{prefix}住所"
        if addr_name in addr_cols:
            pairs.append((addr_cols[addr_name], zip_idx, addr_name, zip_name))
    return pairs


def process_file(path, lookup):
    name = os.path.basename(path)
    rows, enc = read_rows(path)
    if not rows:
        print(f"  スキップ({name}): 空のファイルです")
        return False

    header = rows[0]
    pairs = find_address_zip_pairs(header)
    if not pairs:
        print(f"  スキップ({name}): 「住所」⇔「郵便番号」の組み合わせの列が見つかりません")
        return False

    pref_idx = header.index("都道府県") if "都道府県" in header else None

    print(f"  {name}: 対象列 " + ", ".join(f"{a}→{z}" for _, _, a, z in pairs))

    for addr_idx, zip_idx, addr_name, zip_name in pairs:
        total_missing = 0
        filled = 0
        for r in rows[1:]:
            if len(r) <= max(addr_idx, zip_idx):
                continue
            if r[zip_idx].strip():
                continue
            total_missing += 1
            addr = r[addr_idx]
            pref_hint = r[pref_idx] if pref_idx is not None and pref_idx < len(r) else None
            z, method = lookup.find_zip(addr, pref_hint=pref_hint)
            if z:
                r[zip_idx] = f"{z[:3]}-{z[3:]}"
                filled += 1
        print(f"    {zip_name}: 欠損{total_missing}件中 {filled}件を補完")

    base, ext = os.path.splitext(name)
    out_path = os.path.join(OUTPUT_DIR, f"{base}_郵便番号補完済{ext}")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(f"    -> {os.path.basename(out_path)}")
    return True


def main():
    if not os.path.isfile(KENALL_PATH):
        print(f"エラー: 郵便番号データが見つかりません: {KENALL_PATH}")
        print("日本郵便のダウンロードページから取得して data フォルダに置いてください。")
        print("https://www.post.japanpost.jp/zipcode/dl/utf-zip.html")
        sys.exit(1)

    csv_files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.csv")))
    if not csv_files:
        print(f"input フォルダに CSV がありません: {INPUT_DIR}")
        sys.exit(0)

    print("郵便番号データを読み込み中...")
    lookup = ZipLookup(KENALL_PATH)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(INPUT_DONE_DIR, exist_ok=True)

    print("対象ファイル:")
    for f in csv_files:
        print(f"  - {os.path.basename(f)}")
    print()

    for f in csv_files:
        try:
            ok = process_file(f, lookup)
        except Exception as e:
            ok = False
            print(f"  エラー({os.path.basename(f)}): {e}")
        if ok:
            shutil.move(f, os.path.join(INPUT_DONE_DIR, os.path.basename(f)))

    print()
    print("完了しました。")


if __name__ == "__main__":
    main()
