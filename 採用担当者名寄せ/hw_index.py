# -*- coding: utf-8 -*-
"""ハローワークCSVから採用担当者インデックス(SQLite)を作成・参照する。

1.8GBのCSVを毎回読むと数分かかるため、初回だけインデックスを作り、
2回目以降はキャッシュを使う。元CSVの更新日時・サイズが変わったら自動で作り直す。
"""
import csv
import os
import re
import sqlite3
import sys
import unicodedata

csv.field_size_limit(10 ** 9)

# ---------------------------------------------------------------- 正規化

FORMS = ['株式会社', '有限会社', '合同会社', '合資会社', '合名会社',
         '一般社団法人', '公益社団法人', '一般財団法人', '公益財団法人',
         '医療法人社団', '医療法人財団', '医療法人', '学校法人', '社会福祉法人',
         '特定非営利活動法人', '協同組合', '事業協同組合', '企業組合',
         '宗教法人', '独立行政法人', '国立大学法人']

DEPT = re.compile(r'(.+?)(事業本部|事業部|本部|支社|支店|営業所|カンパニー|グループ本社)$')
PREF = re.compile(r'(北海道|東京都|京都府|大阪府|.{2,3}県)')


def split_form(s):
    """社名を (法人格, 法人格を除いた比較用の名前) に分解する。"""
    s = unicodedata.normalize('NFKC', s or '')
    s = re.sub(r'\s+', '', s)
    s = s.replace('(株)', '株式会社').replace('(有)', '有限会社')
    s = re.sub(r'[【\[].*?[】\]]', '', s)      # 【東証プライム上場】などの装飾を落とす
    form = ''
    for x in FORMS:
        if x in s:
            form = x
            break
    for x in FORMS:
        s = s.replace(x, '')
    return form, s.lower()


def name_keys(s):
    """社名から突合キーを作る。「○○ エンジニアリング事業本部」は本体名でも引けるようにする。"""
    form, cname = split_form(s)
    keys = []
    if cname:
        keys.append((form, cname))
        m = DEPT.match(cname)
        if m and m.group(1):
            keys.append((form, m.group(1)))
    return form, cname, keys


def norm_tel(s):
    return re.sub(r'\D', '', s or '')


def norm_hojin(s):
    d = re.sub(r'\D', '', s or '')
    return d if len(d) == 13 else ''


def pref_city(addr):
    """住所を (都道府県, 市区町村) に分解する。政令指定都市は区まで見る。"""
    a = unicodedata.normalize('NFKC', addr or '').replace(' ', '')
    m = PREF.match(a)
    if not m:
        return '', ''
    p = m.group(1)
    rest = a[len(p):]
    mc = re.match(r'(.+?市.+?区|.+?[市区町村])', rest)
    return p, (mc.group(1) if mc else '')


# ------------------------------------------------- 「担当者」欄のパース

POSTS = ['代表取締役社長', '代表取締役', '取締役', '執行役員', '本部長', '統括部長', '事業部長',
         '部長', '次長', '課長', '係長', '主任', '所長', '支店長', '店長', '工場長', '事務長',
         '局長', '室長', 'センター長', '管理者', '主査', '専務', '常務']

# 雇用形態が誤って担当者名に入っているもの
EMPTYPE = re.compile(r'^(契約社員|正社員|準社員|嘱託|派遣社員?|パートタイマー|アルバイト|時給制|日給制)')
# そもそも人名・部署名ではない注意書き
NOTANAME = re.compile(r'^[〇○※☆★●■◆・\-]|応募|連絡|詳細|参照|記載|補足|下記|別途|問い合わせ|問合せ'
                      r'|お断り|職業紹介|職業相談|就職支援|人材確保|勧誘')
# 人名ではないが担当窓口としては有効なもの（実名が無いときのみ採用する）
NONPERSON = re.compile(r'^(採用|人事|総務|管理|事務|求人|募集|担当|窓口|受付|本社|支社|支店|営業所|'
                       r'パート|アルバイト|ハローワーク|職員|社員|各|同上|上記|なし|未定|不明)')


def parse_tanto(s):
    """ハローワークの「担当者」欄から (氏名, カナ, 課係名・役職名) を取り出す。

    例: 課係名、役職名 事務長 担当者（カタカナ） ツルヤ 担当者 鶴谷 電話番号 011-688-7510
    """
    s = unicodedata.normalize('NFKC', s or '').replace('　', ' ')
    post = ''
    m = re.search(r'課係名、?役職名\s+(.+?)\s+担当者', s)
    if m:
        post = m.group(1).strip()
    kana = ''
    mk = re.search(r'担当者\s*\(カタカナ\)\s*(.+?)(?=\s+担当者\s|\s+電話番号|\s+FAX|$)', s)
    if mk:
        kana = mk.group(1).strip()
    mn = re.search(r'(?:^|\s)担当者\s+([^\s(].*?)\s*(?=電話番号|FAX|Eメール|$)', s)
    name = mn.group(1).strip() if mn else ''
    name = re.sub(r'[【\[(].*?[】\])]', '', name)
    name = re.sub(r'★.*$', '', name)
    for p in POSTS:
        if name.startswith(p):
            if not post:
                post = p
            name = name[len(p):].strip()
            break
    name = re.sub(r'\s+', ' ', name).strip(' 、,/')
    parts = name.split(' ')
    if len(parts) > 2 and all(len(x) == 1 for x in parts):   # 「矢 藤 ・ 橋 本」→「矢藤・橋本」
        name = ''.join(parts)
    if EMPTYPE.match(name) or NOTANAME.search(name):
        name = ''
    return name, kana, post


def tanto_tels(s):
    s = unicodedata.normalize('NFKC', s or '')
    return {re.sub(r'\D', '', x) for x in re.findall(r'(?:電話番号|FAX)\s*([\d\-]{10,})', s)}


def parse_date(s):
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', (s or '').strip())
    if not m:
        return '0000-00-00'
    return '%04d-%02d-%02d' % tuple(int(x) for x in m.groups())


# ---------------------------------------------------------------- 索引作成

SCHEMA = """
CREATE TABLE hw (
    rid      INTEGER PRIMARY KEY,
    hojin    TEXT, tel TEXT, form TEXT, cname TEXT,
    pref     TEXT, city TEXT, name TEXT, addr TEXT,
    tname    TEXT, tkana TEXT, tpost TEXT, rdate TEXT,
    job      TEXT, url TEXT
);
CREATE TABLE hw_dtel (rid INTEGER, tel TEXT);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
"""
INDEXES = """
CREATE INDEX ix_hojin ON hw(hojin);
CREATE INDEX ix_tel   ON hw(tel);
CREATE INDEX ix_name  ON hw(form, cname);
CREATE INDEX ix_dtel  ON hw_dtel(tel);
"""

NEEDED = ['名称', 'TEL', '法人番号', '住所', '担当者', '受付年月日', '職種', '取得URL']


def _open_csv(path):
    for enc in ('utf-8-sig', 'cp932'):
        try:
            f = open(path, encoding=enc, newline='')
            f.readline()
            f.seek(0)
            return f
        except UnicodeDecodeError:
            try:
                f.close()
            except Exception:
                pass
    raise RuntimeError('文字コードを判別できません: ' + path)


def signature(paths):
    return '|'.join('%s:%d:%d' % (os.path.basename(p), os.path.getsize(p), int(os.path.getmtime(p)))
                    for p in paths)


def build(paths, db_path, log=print):
    tmp = db_path + '.tmp'
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.execute('PRAGMA journal_mode=OFF')
    con.execute('PRAGMA synchronous=OFF')

    rid = 0
    buf, dbuf = [], []
    for path in paths:
        log('  索引作成中: %s' % os.path.basename(path))
        with _open_csv(path) as f:
            rd = csv.reader(f)
            hdr = next(rd)
            ix = {c: i for i, c in enumerate(hdr)}
            missing = [c for c in ('名称', 'TEL', '担当者') if c not in ix]
            if missing:
                log('    ※ 必要な列がないため読み飛ばします: %s' % '/'.join(missing))
                continue
            get = {c: ix.get(c, -1) for c in NEEDED}
            n = 0
            for row in rd:
                n += 1
                if n % 100000 == 0:
                    log('    %s 行' % format(n, ','))

                def col(c):
                    i = get[c]
                    return row[i] if 0 <= i < len(row) else ''

                tname, tkana, tpost = parse_tanto(col('担当者'))
                form, cname, _ = name_keys(col('名称'))
                p, city = pref_city(col('住所'))
                rid += 1
                buf.append((rid, norm_hojin(col('法人番号')), norm_tel(col('TEL')), form, cname,
                            p, city, col('名称'), col('住所'),
                            tname, tkana, tpost, parse_date(col('受付年月日')),
                            col('職種') if tname else '', col('取得URL') if tname else ''))
                for t in tanto_tels(col('担当者')):
                    if len(t) >= 10:
                        dbuf.append((rid, t))
                if len(buf) >= 50000:
                    con.executemany('INSERT INTO hw VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', buf)
                    con.executemany('INSERT INTO hw_dtel VALUES (?,?)', dbuf)
                    buf, dbuf = [], []
            log('    %s 行 読み込み完了' % format(n, ','))
    if buf:
        con.executemany('INSERT INTO hw VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', buf)
    if dbuf:
        con.executemany('INSERT INTO hw_dtel VALUES (?,?)', dbuf)
    log('  索引を構築しています...')
    con.executescript(INDEXES)
    con.execute('INSERT INTO meta VALUES (?,?)', ('signature', signature(paths)))
    con.execute('INSERT INTO meta VALUES (?,?)', ('rows', str(rid)))
    con.commit()
    con.close()
    if os.path.exists(db_path):
        os.remove(db_path)
    os.rename(tmp, db_path)
    log('  完了: %s 件を索引化 (%s)' % (format(rid, ','), os.path.basename(db_path)))


def load(paths, db_path, log=print):
    """キャッシュがあれば使い、無い/古ければ作り直して接続を返す。"""
    sig = signature(paths)
    if os.path.exists(db_path):
        try:
            con = sqlite3.connect(db_path)
            cur = con.execute("SELECT v FROM meta WHERE k='signature'")
            row = cur.fetchone()
            if row and row[0] == sig:
                n = con.execute("SELECT v FROM meta WHERE k='rows'").fetchone()[0]
                log('  キャッシュを使用します（%s 件）' % format(int(n), ','))
                return con
            con.close()
            log('  ハローワークCSVが更新されているため索引を作り直します。')
        except sqlite3.DatabaseError:
            log('  キャッシュが壊れているため作り直します。')
    else:
        log('  初回のため索引を作成します（数分かかります）。')
    build(paths, db_path, log)
    return sqlite3.connect(db_path)
