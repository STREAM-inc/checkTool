#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
チェーン店・○○店 除外バッチ処理

【使い方】
  IN フォルダに CSV を置いて「実行.bat」をダブルクリックしてください。
  ・チェーン店マスタ（社内共有サーバー）と照合し、チェーン店の可能性が高い行を除外します。
  ・店名が「店」で終わる行（支店表記）を除外します。
  ・結果は OUT フォルダに 2 ファイル出力されます（除外後リスト／削除した行の一覧）。
  ・処理済みの元ファイルは「処理済み原本」フォルダに自動移動されます。
  ・OUT フォルダの _処理ログ.csv に毎回の処理結果が追記されます。

【フォルダ構成】
  自動処理_チェーン店除外\
    IN\              ← ここに元CSVを置く
    OUT\             ← 除外後リスト・削除分・処理ログが出力される
    処理済み原本\      ← 処理済みの元CSVがここに移動される
    process_folder.py（本体）
    実行.bat（ダブルクリックで起動）
    chain_master_cache.xlsx（社内サーバーに繋がらない時用のキャッシュ、自動更新）

【注意点・現在のルール】
  - 店名列は「名称/店舗名/顧客名/店名/名前/会社名/屋号」のいずれかの見出しを
    自動検出して使います。見つからない場合のみ1列目を使い、ログに警告が出ます。
  - チェーン店マスタとの照合は、ファイル名や業種列から「飲食」「温泉・スパ」の
    どちらかを自動判定できた場合のみ、その業種に該当するチェーンだけを対象に
    行います（他業種の同名チェーンとの誤爆を防ぐため）。判定できない場合は
    マスタ照合をスキップし、「店」で終わる行の除外のみ行います
    （処理ログに「業種判定：不明」と記録されるので、必要ならあとで見直してください）。
  - 完全一致判定なので、表記ゆれ（スペース・記号・全角半角・長音の有無等）は
    吸収しますが、チェーン店マスタに登録されていない支店網は除外されません。

【チェーン店マスタに載っていない“隠れチェーン”の検出】
  - 除外後も残った店名について、店名中のどこでもよいので5文字連続で
    一致する部分が5件以上の店にあれば「同じチェーンの支店が複数残って
    いる」可能性があるとして "{元ファイル名}_要確認チェーン候補_○件.csv"
    にまとめて出力します。
    ※あくまで人が見て判断するための候補リストです。「温泉センター」
    「健康ランド」「やすらぎの湯」のような業界の一般的な言い回しも混ざる
    ので、実際にチェーンだと確認できたものだけを次の手順で消してください
    （自動では削除しません）。
  - 確認の結果チェーンだと判断したら、「追加チェーン除外リスト.txt」に
    そのキーワード（店名の一部でよい）を1行ずつ追記してください。
    次回以降の処理で、店名にそのキーワードを含む行が自動的に除外されます。
"""

import csv
import os
import re
import sys
import glob
import shutil
import unicodedata
import datetime

csv.field_size_limit(2**31 - 1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(BASE_DIR, "IN")
OUT_DIR = os.path.join(BASE_DIR, "OUT")
DONE_DIR = os.path.join(BASE_DIR, "処理済み原本")
LOG_PATH = os.path.join(OUT_DIR, "_処理ログ.csv")
CACHE_MASTER = os.path.join(BASE_DIR, "chain_master_cache.xlsx")
ADDITIONAL_EXCLUDE_PATH = os.path.join(BASE_DIR, "追加チェーン除外リスト.txt")

# 「隠れチェーン」候補検出: 何文字の一致を見るか（店名中のどこでもよい）／
# 何件以上あれば要注意とするか。
# 件数が多いファイルほど「イタリアン」「ダイニング」等の一般語が5件どころか
# 数百〜数千件ヒットしてしまうため、残り件数の0.1%を下限として自動的に
# 閾値を引き上げる（小規模ファイルでは最低 CANDIDATE_MIN_COUNT を使う）。
CANDIDATE_NGRAM_CHARS = 5
CANDIDATE_MIN_COUNT = 5
CANDIDATE_MIN_COUNT_RATIO = 0.001

# 社内共有サーバー上のチェーン店除外用マスタ（生きていればこちらを優先して読む）
NETWORK_MASTER = (
    r"\\Ra21\共有\DB\001_STREAM\05_スクレイピング\07_ 名寄せ元便利データ"
    r"\チェーン店除外用店名マスタ.xlsx"
)

OUTPUT_MARKERS = ("チェーン店・○○店除外後", "削除分（チェーン店・○○店）", "要確認チェーン候補")

# ---- 業種ジャンル（マスタの1列目「ジャンル」列の値）----------------------
GENRE_GROUPS = {
    "food": {
        "カフェ", "居酒屋/バー", "レストラン", "ラーメン/餃子", "弁当/惣菜/スタンド", "焼肉",
        "カレー", "回転寿司/すし", "ランチ/定食", "お好み/たこ焼き", "お茶/コーヒー豆", "うどん",
        "バーガー", "そば", "とんかつ", "からあげ", "食べ放題", "宅配ピザ", "牛丼/丼もの",
        "専門食品", "天ぷら", "しゃぶしゃぶ", "牛たん", "宅配専門", "ふぐ", "アジア/エスニック",
        "ベーカリー", "スイーツ/お菓子", "酒屋",
    },
    "spa": {
        "温泉/銭湯", "ホテル/宿泊", "リラクゼーション",
    },
}

# ドメイン自動判定用キーワード（ファイル名・業種列のどちらかに含まれていれば判定）
DOMAIN_KEYWORDS = {
    "food": ["飲食", "グルメ", "食べログ", "たべログ", "tabelog", "レストラン", "居酒屋"],
    "spa": ["温泉", "銭湯", "スパ", "サウナ", "sauna", "spa", "onsen"],
}

GENRE_COLUMN_NAMES = {"大業種", "中業種", "小業種", "業種名(大)", "業種名(小)", "ジャンル",
                      "業種大", "業種小", "業種"}

# 店名・会社名が入っている列の候補（見つかった最初のものを使う。
# 見つからない場合は1列目にフォールバックし、警告をログに出す）
NAME_COLUMN_CANDIDATES = ["名称", "店舗名", "顧客名", "店名", "名前", "会社名", "屋号"]

# 「店」で終わるが支店表記（チェーンの一部）ではなく、業種名がそのまま店名に
# なっている個人商店の語尾（例:「田中洋服店」「北野呉服店」）。小売業では
# こうした一人一店舗の商店が多く、これらまで「○○店表記」ルールで一律に
# 除外すると消しすぎになるため、これらの語尾で終わる店名は対象外とする。
INDEPENDENT_SHOP_SUFFIXES = [
    "呉服店", "洋品店", "洋服店", "時計店", "宝石店", "貴金属店", "眼鏡店", "メガネ店",
    "カメラ店", "自転車店", "家具店", "寝具店", "布団店", "ふとん店", "畳店", "建具店",
    "荒物金物店", "金物店", "荒物店", "雑貨店", "文具店", "書店", "生花店", "花店",
    "米穀店", "米店", "酒店", "酒販店", "薬店", "燃料店", "材木店", "木材店", "石材店",
    "古美術店", "骨董店", "古物店", "質店", "釣具店", "玩具店", "楽器店", "電器店",
    "電気店", "家電店", "履物店", "靴店", "鞄店", "傘店", "瀬戸物店", "陶器店",
    "乾物店", "精肉店", "鮮魚店", "青果店", "衣料品店",
]


def is_genre_column(header_cell):
    h = (header_cell or "").strip()
    return h in GENRE_COLUMN_NAMES or "業種" in h or h == "ジャンル"


def find_name_column(header):
    for i, h in enumerate(header):
        if (h or "").strip() in NAME_COLUMN_CANDIDATES:
            return i, True
    return 0, False


def norm(s):
    """表記ゆれ吸収: 全角半角統一・記号除去・小文字化。長音符ーは残す
    （「スパ」と「スーパー」のような別語の誤一致を防ぐため）。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(
        r'[\s\u3000・,．。\.\-‐-―~〜/\\_"\'’“”()（）\[\]【】&＆!！?？:：;；#＃*＊+＋]',
        "",
        s,
    )
    return s.lower()


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}")


def find_repeat_candidates(rows, name_idx, ngram_chars=CANDIDATE_NGRAM_CHARS,
                            min_count=CANDIDATE_MIN_COUNT):
    """除外後も残っている行の店名について、店名中のどこでもよいので
    ngram_chars 文字連続で一致する部分が min_count 件以上の行にあれば
    「隠れチェーン候補」として返す。
    戻り値: [(表示用フレーズ, 出現件数, [対象行, ...]), ...]  件数の多い順。
    ※同じ支店網が微妙に違う位置の一致で複数グループに分かれるのを防ぐため、
    対象行の組み合わせが完全に同じグループは1つにまとめる（最長のフレーズを代表に）。
    「温泉センター」「健康ランド」のような業界の一般的な言い回しも混ざるので、
    あくまで人が確認するための候補リストとして扱うこと。"""
    row_ids = {}          # normalized ngram -> set(row index)
    for i, row in enumerate(rows):
        name = row[name_idx] if len(row) > name_idx else ""
        n = norm(name)
        if len(n) < ngram_chars:
            continue
        seen_here = set(n[j:j + ngram_chars] for j in range(len(n) - ngram_chars + 1))
        for gram in seen_here:
            row_ids.setdefault(gram, set()).add(i)

    hits = {gram: idxs for gram, idxs in row_ids.items() if len(idxs) >= min_count}

    # 対象行の組み合わせが同じグループは1つに統合(最長のフレーズを代表にする)
    by_rowset = {}
    for gram, idxs in hits.items():
        key = frozenset(idxs)
        if key not in by_rowset or len(gram) > len(by_rowset[key]):
            by_rowset[key] = gram

    candidates = [(gram, len(idxs), [rows[i] for i in idxs])
                  for idxs, gram in by_rowset.items()]
    candidates.sort(key=lambda x: -x[1])
    return candidates


def load_additional_excludes():
    """「追加チェーン除外リスト.txt」から、ユーザーが手動確認したチェーン名の
    断片を読み込む。1行1キーワード、# で始まる行と空行は無視。"""
    if not os.path.exists(ADDITIONAL_EXCLUDE_PATH):
        with open(ADDITIONAL_EXCLUDE_PATH, "w", encoding="utf-8-sig") as f:
            f.write(
                "# ここにチェーン店だと確認できた店名（の一部）を1行ずつ書いてください。\n"
                "# 例: 満天の湯\n"
                "# 「#」で始まる行と空行は無視されます。\n"
                "# 次回の処理から、店名にここに書いた文字列を含む行が自動的に除外されます。\n"
            )
        return {}
    with open(ADDITIONAL_EXCLUDE_PATH, encoding="utf-8-sig") as f:
        lines = [l.strip() for l in f]
    phrases = {}
    for l in lines:
        if not l or l.startswith("#"):
            continue
        key = norm(l)
        if key:
            phrases.setdefault(key, l)
    return phrases


def get_master_path():
    """社内サーバーが生きていればそちらを使い、ローカルキャッシュを更新する。
    繋がらなければ、以前作ったキャッシュを使う。"""
    try:
        if os.path.exists(NETWORK_MASTER):
            shutil.copyfile(NETWORK_MASTER, CACHE_MASTER)
            log("チェーン店マスタ: 社内サーバーから読み込み（キャッシュ更新済み）")
            return CACHE_MASTER, True
    except Exception as e:
        log(f"社内サーバーへのアクセスに失敗: {e}")
    if os.path.exists(CACHE_MASTER):
        log("チェーン店マスタ: サーバーに接続できないため、ローカルキャッシュを使用します"
            "（最新でない可能性があります）")
        return CACHE_MASTER, False
    return None, False


def load_master(path):
    """{genre: {normalized_name: original_name}} の形で全件返す。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb.worksheets[0]
    by_genre = {}
    for row in list(ws.iter_rows(values_only=True))[1:]:
        genre = (row[0] or "").strip()
        name = (row[1] or "").strip()
        if not name:
            continue
        by_genre.setdefault(genre, {}).setdefault(norm(name), name)
    return by_genre


def build_domain_master(by_genre, domain):
    allowed = GENRE_GROUPS.get(domain)
    if allowed is None:
        return {}
    merged = {}
    for genre, names in by_genre.items():
        if genre in allowed:
            merged.update(names)
    return merged


def detect_domain(filename, header, sample_rows):
    text_bits = [filename]
    genre_col_idx = [i for i, h in enumerate(header) if is_genre_column(h)]
    for row in sample_rows[:50]:
        for i in genre_col_idx:
            if i < len(row):
                text_bits.append(row[i] or "")
    haystack = "".join(text_bits).lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(kw.lower() in haystack for kw in keywords):
            return domain
    return None


def read_csv_flexible(path):
    """utf-8-sig を基本とし、ダメなら cp932(Shift-JIS) を試す。"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, encoding=enc, newline="") as f:
                rd = csv.reader(f)
                rows = list(rd)
            if not rows:
                continue
            return rows[0], rows[1:], enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise RuntimeError(f"文字コードを判定できませんでした: {path}")


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        w.writerow(header)
        w.writerows(rows)


def clean_stem(filename):
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"[_ ]?\(\d+\)$", "", stem)   # 末尾の " (1)" 等
    stem = re.sub(r"_\d+件$", "", stem)          # 末尾の "_1234件"
    return stem


def append_log(row):
    is_new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        if is_new:
            w.writerow(["処理日時", "元ファイル名", "文字コード", "店名列", "業種判定", "総件数",
                        "削除(○○店)", "削除(マスタ一致)", "削除(追加除外リスト)", "残り件数",
                        "隠れチェーン候補グループ数", "隠れチェーン候補件数", "出力ファイル(除外後)"])
        w.writerow(row)


def process_one(path, by_genre, additional_excludes):
    filename = os.path.basename(path)
    log(f"--- 処理開始: {filename}")
    header, rows, enc = read_csv_flexible(path)
    total = len(rows)

    name_idx, name_found = find_name_column(header)
    if name_found:
        log(f"店名列: 「{header[name_idx]}」（{name_idx + 1}列目）")
    else:
        log(f"店名列: 見つからないため1列目「{header[0] if header else '?'}」を使用します"
            "（列名が名称/店舗名/顧客名/店名/名前/会社名/屋号のいずれでもない場合に発生。"
            "誤判定の可能性があるため出力を必ずご確認ください）")

    domain = detect_domain(filename, header, rows)
    if domain:
        master = build_domain_master(by_genre, domain)
        log(f"業種判定: {domain}（マスタ照合対象 {len(master)} 件）")
    else:
        master = {}
        log("業種判定: 不明 → チェーン店マスタ照合はスキップし、○○店ルールのみ適用します")

    keep, dele = [], []
    n_ten = n_master = n_add = 0
    for row in rows:
        if not row:
            continue
        name = row[name_idx] if len(row) > name_idx else ""
        name_norm = norm(name)
        reasons = []
        name_stripped = name.rstrip()
        if name_stripped.endswith("店") and not any(
            name_stripped.endswith(suf) for suf in INDEPENDENT_SHOP_SUFFIXES
        ):
            reasons.append("○○店表記")
        hit = master.get(name_norm)
        if hit:
            reasons.append("チェーン店マスタ一致:" + hit)
        add_hit = next((disp for key, disp in additional_excludes.items()
                         if key and key in name_norm), None)
        if add_hit:
            reasons.append("追加除外リスト一致:" + add_hit)
        if reasons:
            if "○○店表記" in reasons:
                n_ten += 1
            if any(r.startswith("チェーン店マスタ") for r in reasons):
                n_master += 1
            if any(r.startswith("追加除外リスト") for r in reasons):
                n_add += 1
            dele.append(row + [" / ".join(reasons)])
        else:
            keep.append(row)

    stem = clean_stem(filename)
    out_keep = os.path.join(OUT_DIR, f"{stem}_チェーン店・○○店除外後_{len(keep)}件.csv")
    out_dele = os.path.join(OUT_DIR, f"{stem}_削除分（チェーン店・○○店）_{len(dele)}件.csv")
    write_csv(out_keep, header, keep)
    write_csv(out_dele, header + ["削除理由"], dele)

    dyn_min_count = max(CANDIDATE_MIN_COUNT, int(len(keep) * CANDIDATE_MIN_COUNT_RATIO))
    if dyn_min_count > CANDIDATE_MIN_COUNT:
        log(f"隠れチェーン候補検知: 残り{len(keep)}件のため閾値を{dyn_min_count}件以上に引き上げます")
    candidates = find_repeat_candidates(keep, name_idx, min_count=dyn_min_count)
    n_candidates_rows = sum(c[1] for c in candidates)
    if candidates:
        cand_rows = []
        for display, count, crows in candidates:
            for r in crows:
                cand_rows.append(r + [display, count])
        out_cand = os.path.join(
            OUT_DIR, f"{stem}_要確認チェーン候補_{n_candidates_rows}件.csv"
        )
        write_csv(out_cand, header + [f"候補フレーズ（{CANDIDATE_NGRAM_CHARS}文字一致）", "出現件数"], cand_rows)
        log(f"  隠れチェーン候補: {len(candidates)}グループ・{n_candidates_rows}件"
            f" → {os.path.basename(out_cand)}")
        for display, count, _ in candidates[:10]:
            log(f"    「{display}」... {count}件")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    moved = os.path.join(DONE_DIR, f"{ts}_{filename}")
    shutil.move(path, moved)

    name_col_label = header[name_idx] if name_idx < len(header) else str(name_idx)
    if not name_found:
        name_col_label += "（推定・要確認）"
    append_log([
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        filename, enc, name_col_label, domain or "不明", total, n_ten, n_master, n_add,
        len(keep), len(candidates), n_candidates_rows, os.path.basename(out_keep),
    ])

    log(f"完了: 総数{total} → 残り{len(keep)} / 削除{len(dele)}"
        f"（○○店{n_ten}・マスタ{n_master}・追加除外{n_add}）")
    log(f"  出力: {os.path.basename(out_keep)}")
    log(f"  出力: {os.path.basename(out_dele)}")
    log(f"  元ファイルは 処理済み原本\\{os.path.basename(moved)} へ移動しました")


def main():
    for d in (IN_DIR, OUT_DIR, DONE_DIR):
        os.makedirs(d, exist_ok=True)

    targets = []
    for p in sorted(glob.glob(os.path.join(IN_DIR, "*.csv"))):
        name = os.path.basename(p)
        if any(marker in name for marker in OUTPUT_MARKERS):
            log(f"スキップ（出力ファイルらしきものが紛れ込んでいます）: {name}")
            continue
        targets.append(p)

    if not targets:
        log("IN フォルダに処理対象の CSV が見つかりませんでした。")
        return

    master_path, _ = get_master_path()
    if master_path is None:
        log("チェーン店マスタが取得できません（社内サーバー未接続・ローカルキャッシュも無し）。"
            "○○店ルールのみで処理を続けます。")
        by_genre = {}
    else:
        by_genre = load_master(master_path)

    additional_excludes = load_additional_excludes()
    if additional_excludes:
        log(f"追加チェーン除外リスト: {len(additional_excludes)}件のキーワードを適用します")

    for p in targets:
        try:
            process_one(p, by_genre, additional_excludes)
        except Exception as e:
            log(f"エラー: {os.path.basename(p)} の処理に失敗しました → {e}")
            log("  このファイルは IN フォルダに残しています。内容をご確認ください。")

    log("すべての処理が終了しました。")


if __name__ == "__main__":
    main()
