# -*- coding: utf-8 -*-
"""input フォルダのリストCSVに、ハローワークの採用担当者名を突合して埋める。

使い方:  input に CSV を入れて 採用担当者名寄せ.bat を実行するだけ。
出力:    output に 4 種類のCSV（本体 / 名寄せ明細 / 要確認候補 / 未取得一覧）
         処理済みの入力は input_done へ移動。
"""
import csv
import datetime
import difflib
import glob
import os
import shutil
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hw_index as HW

csv.field_size_limit(10 ** 9)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(SCRIPT_DIR, 'input')
DONE_DIR = os.path.join(SCRIPT_DIR, 'input_done')
OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'output')
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache')
CONFIG = os.path.join(SCRIPT_DIR, '設定.txt')
DB = os.path.join(CACHE_DIR, 'hellowork_index.sqlite')

# ------------------------------------------------------------ 列の自動判定
# 左が優先。完全一致で探し、見つからなければ部分一致で探す。
ALIAS = {
    '社名':       ['名称', '企業名', '会社名', '法人名', '事業所名', '社名', '企業'],
    '住所':       ['本社住所', '住所', '本社所在地', '所在地', '会社住所'],
    '都道府県':   ['都道府県', '県名'],
    'TEL':        ['TEL', '電話番号', '代表電話', '電話', 'TEL番号', '連絡先'],
    '法人番号':   ['法人番号', '法人番号13桁'],
    '担当者名':   ['採用担当者名', '担当者名', '採用担当者', '採用担当', '担当者'],
    '担当課係名': ['担当課係名', '担当部署', '部署名', '課係名'],
}
NEW_COL = '採用担当者名'   # 担当者名の列が無いファイルに追加する列名


def find_col(header, kind):
    for a in ALIAS[kind]:
        for h in header:
            if h.strip() == a:
                return h
    for a in ALIAS[kind]:
        for h in header:
            if a in h:
                return h
    return None


def read_csv(path):
    for enc in ('utf-8-sig', 'cp932'):
        try:
            with open(path, encoding=enc, newline='') as f:
                rows = list(csv.DictReader(f))
            if rows:
                return rows, list(rows[0].keys()), enc
            with open(path, encoding=enc, newline='') as f:
                hdr = next(csv.reader(f), [])
            return [], hdr, enc
        except UnicodeDecodeError:
            continue
    raise RuntimeError('文字コードを判別できません: ' + path)


def write_csv(path, fields, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------ 突合

COLS = ('rid,hojin,tel,form,cname,pref,city,name,addr,'
        'tname,tkana,tpost,rdate,job,url')
IDX = {c: i for i, c in enumerate(COLS.split(','))}


def q(con, sql, args):
    return con.execute('SELECT %s FROM hw WHERE %s' % (COLS, sql), args).fetchall()


def candidates(con, hojin, tel, keys, pref, city):
    """優先度の高い突合方法から順に候補を探す。(候補リスト, 突合方法) を返す。"""
    def drop_other_corp(rs):
        """法人番号が判明していて、それと食い違う法人の求人は別会社なので捨てる。
        （同名・同市区町村の別法人を拾ってしまうのを防ぐ。支店は法人番号が同じなので残る）"""
        if not hojin:
            return rs
        return [r for r in rs if not r[IDX['hojin']] or r[IDX['hojin']] == hojin]

    if hojin:
        rs = q(con, 'hojin = ?', (hojin,))
        if rs:
            return rs, '①法人番号一致'
    if tel and len(tel) >= 10:
        rs = drop_other_corp(q(con, 'tel = ?', (tel,)))
        if rs:
            return rs, '②事業所TEL一致'
        rids = [r[0] for r in con.execute('SELECT DISTINCT rid FROM hw_dtel WHERE tel = ?', (tel,))]
        if rids:
            rs = drop_other_corp(q(con, 'rid IN (%s)' % ','.join('?' * len(rids)), rids))
            if rs:
                return rs, '③担当者電話番号一致'
    if pref:
        seen, ns = set(), []
        for form, cname in keys:
            for r in q(con, 'form = ? AND cname = ?', (form, cname)):
                if r[0] not in seen:
                    seen.add(r[0])
                    ns.append(r)
        ns = drop_other_corp(ns)
        if ns:
            same_city = [r for r in ns if (r[IDX['pref']], r[IDX['city']]) == (pref, city)]
            if same_city:
                return same_city, '④社名+市区町村一致'
            same_pref = [r for r in ns if r[IDX['pref']] == pref]
            if same_pref:
                return same_pref, '⑤社名+都道府県のみ一致'
    return [], ''


def choose(cands):
    """候補求人の中から採用する担当者を1件選ぶ。

    カナ読みがある＝実名らしいものを優先し、同点なら受付年月日が新しいものを採る。
    """
    picks = []
    for r in cands:
        nm = r[IDX['tname']]
        if not nm:
            continue
        score = (2 if r[IDX['tkana']] else 0) + (0 if HW.NONPERSON.match(nm) else 1)
        picks.append((score, r[IDX['rdate']], r))
    if not picks:
        return None, []
    picks.sort(key=lambda x: (x[0], x[1]), reverse=True)
    names = list(dict.fromkeys(p[2][IDX['tname']] for p in
                               sorted(picks, key=lambda x: x[1], reverse=True)))
    return picks[0][2], names


def process(path, con, log=print):
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0]
    log('')
    log('■ %s' % name)
    rows, fields, enc = read_csv(path)
    if not rows:
        log('  データ行がありません。スキップします。')
        return None

    c_name = find_col(fields, '社名')
    c_tel = find_col(fields, 'TEL')
    c_addr = find_col(fields, '住所')
    c_pref = find_col(fields, '都道府県')
    c_hojin = find_col(fields, '法人番号')
    c_out = find_col(fields, '担当者名')
    c_post = find_col(fields, '担当課係名')

    if not c_name and not c_tel and not c_hojin:
        log('  社名・電話番号・法人番号のいずれの列も見つかりません。スキップします。')
        return None
    if not c_out:
        c_out = NEW_COL
        fields = fields + [NEW_COL]
        for r in rows:
            r[NEW_COL] = ''
        log('  ※ 担当者名の列が無いため「%s」列を追加します。' % NEW_COL)

    log('  %s件 / 使用列: 社名=%s TEL=%s 住所=%s 法人番号=%s → 出力=%s'
        % (format(len(rows), ','), c_name, c_tel, c_addr or c_pref, c_hojin or 'なし', c_out))

    stat = {}
    detail, check, miss = [], [], []

    def bump(k):
        stat[k] = stat.get(k, 0) + 1

    for r in rows:
        raw_name = r.get(c_name, '') if c_name else ''
        raw_tel = r.get(c_tel, '') if c_tel else ''
        raw_addr = r.get(c_addr, '') if c_addr else ''
        hojin = HW.norm_hojin(r.get(c_hojin, '')) if c_hojin else ''
        tel = HW.norm_tel(raw_tel)
        form, cname, keys = HW.name_keys(raw_name)
        pref, city = HW.pref_city(raw_addr)
        if not pref and c_pref:
            pref = (r.get(c_pref, '') or '').strip()

        base = {'元_社名': raw_name, '元_住所': raw_addr, '元_TEL': raw_tel,
                '元_法人番号': (r.get(c_hojin, '') or '').strip() if c_hojin else ''}

        cands, how = candidates(con, hojin, tel, keys, pref, city)
        if not cands:
            bump('未マッチ（ハローワークに該当求人なし）')
            miss.append(dict(base, 理由='ハローワークに該当求人なし'))
            continue

        best, names = choose(cands)
        if best is None:
            bump('%s／担当者名の記載なし' % how)
            miss.append(dict(base, 理由='%sで求人%d件ヒットするが担当者名の記載なし' % (how, len(cands))))
            continue

        sim = difflib.SequenceMatcher(None, cname, HW.split_form(best[IDX['name']])[1]).ratio()
        hp, hc = best[IDX['pref']], best[IDX['city']]
        if pref and (pref, city) == (hp, hc):
            addr_ok = '○'
        elif pref and pref == hp:
            addr_ok = '都道府県のみ'
        else:
            addr_ok = '×'

        rec = dict(base,
                   突合方法=how, 社名類似度='%.2f' % sim, 住所一致=addr_ok,
                   HW求人件数=len(cands),
                   担当者名=best[IDX['tname']], カナ=best[IDX['tkana']],
                   担当課係名=best[IDX['tpost']], 受付年月日=best[IDX['rdate']],
                   HW名称=best[IDX['name']], HW住所=best[IDX['addr']],
                   HW法人番号=best[IDX['hojin']], HW事業所TEL=best[IDX['tel']],
                   HW職種=best[IDX['job']],
                   担当者候補=' / '.join(names), HW求人URL=best[IDX['url']])

        # 電話番号しか合っておらず、社名も所在地も乖離するものは別法人の疑い
        weak = how in ('②事業所TEL一致', '③担当者電話番号一致') and sim < 0.4 and addr_ok != '○'
        if weak:
            rec['判定'] = '要確認（電話番号は一致するが社名・所在地とも乖離）'
            check.append(rec)
            bump('要確認（TELのみ一致・社名/住所が乖離）')
            miss.append(dict(base, 理由='電話番号は一致するが社名・所在地が乖離（要確認候補を参照）'))
            continue
        if how == '⑤社名+都道府県のみ一致':
            rec['判定'] = '要確認（社名と都道府県のみ一致・別法人の可能性あり）'
            check.append(rec)
            bump('要確認（同名企業が同一都道府県の別市区町村）')
            miss.append(dict(base, 理由='同名企業が同一都道府県の別市区町村にヒット（要確認候補を参照）'))
            continue

        r[c_out] = best[IDX['tname']]
        if c_post and not (r.get(c_post) or '').strip():
            r[c_post] = best[IDX['tpost']]
        detail.append(rec)
        bump(how)

    filled = sum(1 for r in rows if (r.get(c_out) or '').strip())
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    write_csv(os.path.join(OUTPUT_DIR, '%s_担当者名寄せ%d件.csv' % (stem, filled)), fields, rows)
    if detail:
        write_csv(os.path.join(OUTPUT_DIR, '%s_名寄せ明細.csv' % stem), list(detail[0].keys()), detail)
    if check:
        write_csv(os.path.join(OUTPUT_DIR, '%s_要確認候補.csv' % stem), list(check[0].keys()), check)
    if miss:
        write_csv(os.path.join(OUTPUT_DIR, '%s_未取得一覧.csv' % stem), list(miss[0].keys()), miss)

    log('  --- 突合結果 ---')
    for k in sorted(stat, key=lambda x: -stat[x]):
        log('    %-40s %6d' % (k, stat[k]))
    log('  充足: %s / %s 件 (%.1f%%)' % (format(filled, ','), format(len(rows), ','),
                                        100.0 * filled / len(rows)))
    return {'file': name, 'rows': len(rows), 'filled': filled,
            'check': len(check), 'miss': len(miss)}


# ------------------------------------------------------------ 設定・起動

DEFAULT_CONFIG = """\
# ハローワークCSVの場所を1行に1つ書いてください（CSVファイル or フォルダ）。
# 「#」で始まる行はコメントです。複数書いた場合はすべて索引に取り込みます。
# 必要な列: 名称 / TEL / 担当者 （あれば 法人番号・住所・受付年月日・職種・取得URL も使用）

C:\\Users\\1112376\\Desktop\\20260917\\7-タウンワーク\\20260908【0】ハローワーク_790897件.csv
"""


def master_paths(log=print):
    if not os.path.exists(CONFIG):
        with open(CONFIG, 'w', encoding='utf-8') as f:
            f.write(DEFAULT_CONFIG)
        log('設定.txt を作成しました。ハローワークCSVの場所を確認してください。')
    paths = []
    with open(CONFIG, encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if os.path.isdir(line):
                paths += sorted(glob.glob(os.path.join(line, '*.csv')))
            elif os.path.isfile(line):
                paths.append(line)
            else:
                log('※ 設定.txt のパスが見つかりません: %s' % line)
    return sorted(set(paths))


def main():
    print('=' * 70)
    print(' 採用担当者 名寄せツール   %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
    print('=' * 70)

    for d in (INPUT_DIR, DONE_DIR, OUTPUT_DIR, CACHE_DIR):
        os.makedirs(d, exist_ok=True)

    targets = sorted(glob.glob(os.path.join(INPUT_DIR, '*.csv')))
    if not targets:
        print('input フォルダに CSV がありません。')
        print('  %s' % INPUT_DIR)
        return 1

    masters = master_paths()
    if not masters:
        print('ハローワークCSVが設定されていません。設定.txt を確認してください。')
        return 1
    print('ハローワークデータ:')
    for m in masters:
        print('  - %s (%.1f GB)' % (os.path.basename(m), os.path.getsize(m) / 1024 ** 3))

    con = HW.load(masters, DB)

    print('')
    print('対象ファイル: %d件' % len(targets))
    for t in targets:
        print('  - %s' % os.path.basename(t))

    results = []
    for t in targets:
        try:
            res = process(t, con)
        except Exception:
            print('  エラーが発生したためこのファイルはスキップします:')
            traceback.print_exc()
            continue
        if res:
            results.append(res)
            shutil.move(t, os.path.join(DONE_DIR, os.path.basename(t)))
    con.close()

    print('')
    print('=' * 70)
    print(' 完了')
    print('=' * 70)
    for r in results:
        print('  %s' % r['file'])
        print('      %s件中 %s件を補完 / 要確認 %d件 / 未取得 %d件'
              % (format(r['rows'], ','), format(r['filled'], ','), r['check'], r['miss']))
    print('')
    print('  出力先: %s' % OUTPUT_DIR)
    print('  処理済: %s' % DONE_DIR)
    return 0


if __name__ == '__main__':
    sys.exit(main())
