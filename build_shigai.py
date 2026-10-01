# -*- coding: utf-8 -*-
"""総務省「市外局番の一覧」PDF → data/shigai_kyokuban.csv (市外局番,都道府県,市区町村)

市区町村は areacode.CityResolver と同じキー(政令市は市、郡は省いた町村名、東京23区は区)。
「〇〇市（△△を除く。）」のような一部区域の指定は市区町村単位では区別できないため、
その市区町村を含めて扱う(=判定は甘め。誤って消すことはない)。

更新方法: https://www.soumu.go.jp/main_sosiki/joho_tsusin/top/tel_number/shigai_list.html から
最新PDFを data/shigai_kyokuban_soumu.pdf に置き直して  py build_shigai.py  を実行。
"""
import csv
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from areacode import PREFECTURES, CityResolver, nfkc  # noqa: E402

PDF = os.path.join(HERE, "data", "shigai_kyokuban_soumu.pdf")
OUT = os.path.join(HERE, "data", "shigai_kyokuban.csv")
KENALL = os.path.join(HERE, "..", "郵便番号補完ツール", "data", "utf_ken_all.csv")

PREF_RE = "|".join(PREFECTURES)


def split_top(s, seps="、"):
    """括弧の外側の区切りでだけ分割する"""
    out, depth, buf = [], 0, ""
    for ch in s:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth -= 1
        if ch in seps and depth == 0:
            out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return [x.strip() for x in out if x.strip()]


def split_base_inner(item):
    i = item.find("（")
    if i < 0:
        return item, ""
    return item[:i], item[i + 1:item.rfind("）")]


def strip_parens(s):
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"（[^（）]*）", "", s)
    return s


def names_in(inner):
    """「浦臼町及び新十津川町に限る。」→ [(名前, 部分指定か)]"""
    body = re.sub(r"(に限る|を除く)。?$", "", inner)
    # 括弧の外側の「及び」「並びに」も区切りにする(「大洗町及び城里町（…）」)
    out, depth, i = "", 0, 0
    while i < len(body):
        ch = body[i]
        depth += ch in "（("
        depth -= ch in "）)"
        for w in ("並びに", "及び"):
            if depth == 0 and body.startswith(w, i):
                out += "、"
                i += len(w)
                break
        else:
            out += ch
            i += 1
    res = []
    for p in split_top(out, "、"):
        if p:
            res.append((strip_parens(p), "（" in p))
    return res


def main():
    import pypdf
    text = "".join(pg.extract_text() for pg in pypdf.PdfReader(PDF).pages)
    text = nfkc(text).replace("\n", "")
    text = re.sub(r"\s+", " ", text)
    # 全角括弧に戻す(NFKCで半角になるため)
    text = text.replace("(", "（").replace(")", "）")

    resolver = CityResolver(KENALL)
    # 郡 → その郡の町村キー
    gun_towns = defaultdict(set)
    with open(KENALL, encoding="utf-8", newline="") as f:
        for r in csv.reader(f):
            if len(r) >= 9:
                m = re.match(r"^(.+?郡)(.+)$", nfkc(r[7]))
                if m:
                    gun_towns[(r[6], m.group(1))].add(m.group(2))
    wards23 = {k for n, k in resolver.by_pref["東京都"] if k.endswith("区") and "市" not in k}

    entries = re.findall(rf"(\d+(?:-\d+)?) ((?:{PREF_RE}).+?) (\d{{1,4}}) (B?C?D?E)(?= |$)", text)
    rows, unresolved = set(), []
    for code_no, region, ac, local in entries:
        ac = "0" + ac
        pref = ""
        for item in split_top(region):
            m = re.match(rf"^({PREF_RE})(.*)$", item)
            if m:
                pref, item = m.group(1), m.group(2)
            base, inner = split_base_inner(item)
            base = base.strip()
            keys = set()
            if base in ("特別区", "23区") and pref == "東京都":
                keys = set(wards23)
            elif base.endswith("郡"):
                towns = gun_towns.get((pref, base), set())
                if "に限る" in inner:
                    keys = {next((t for t in towns if n.startswith(t)), n) for n, _ in names_in(inner)}
                elif "を除く" in inner:
                    whole = {n for n, partial in names_in(inner) if not partial and n in towns}
                    keys = towns - whole
                else:
                    keys = set(towns)
            else:
                p, k = resolver.resolve(pref + base, pref)
                if k:
                    keys = {k}
            if not keys:
                unresolved.append(f"{ac} {pref}{item}")
            for k in keys:
                rows.add((ac, pref, k))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["市外局番", "都道府県", "市区町村"])
        for r in sorted(rows):
            w.writerow(r)
    print(f"区画 {len(entries)}件 → {len(rows)}行 を {OUT} に出力")
    if unresolved:
        print(f"市区町村を特定できなかった区域 {len(unresolved)}件:")
        for u in unresolved:
            print("  ", u)


if __name__ == "__main__":
    main()
