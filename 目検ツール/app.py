# -*- coding: utf-8 -*-
"""目検ツール ローカルWebアプリ

起動すると http://localhost:8765 をブラウザで開く。
  - Backlogの課題キーを入れると、リスト詳細・添付を取得
  - 添付CSV or 手元のCSV(ドラッグ&ドロップ / inputフォルダ)を自動修正し、チェックリストを作る
  - 担当者・オーナーそれぞれがチェックを付けられ、状態は checks フォルダに保存
  - チェック結果をBacklogにコメント投稿できる
"""
import datetime
import json
import os
import re
import shutil
import sys
import threading
import traceback
import unicodedata
import urllib.error
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import areacode  # noqa: E402
import backlog  # noqa: E402
import mokken_core as mc  # noqa: E402

KENALL = os.path.join(HERE, "..", "郵便番号補完ツール", "data", "utf_ken_all.csv")


ROOT = os.path.normpath(os.path.join(HERE, ".."))  # checkTool フォルダ
SETTING_JSON = os.path.join(ROOT, "setting.json")


def load_setting_json():
    """checkTool\\setting.json（無ければ None）。書き間違いは場所を出して止める"""
    if not os.path.isfile(SETTING_JSON):
        return None
    try:
        with open(SETTING_JSON, encoding="utf-8-sig") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"setting.json の書き方が間違っています（{e.lineno}行目 {e.colno}文字目）: {e.msg}")
        print("  Windows のパスは \\ を2つ重ねる（C:\\\\Users\\\\...）か / で書いてください")
        sys.exit(1)


def load_settings():
    """setting.json(checkTool直下)を読む。無ければ古い 設定.txt(このフォルダ)。
    相対パスは、setting.json なら checkTool フォルダから、設定.txt ならこのフォルダから"""
    cfg = {
        "BACKLOG_SPACE": "https://streeeeeam.backlog.com",
        "APIKEY_FILE": os.path.join("..", "APIキー.txt"),
        "CHECKS_DIR": "checks",
        "PORT": "8765",
        "HW_CSV": "",
        "TASKS_FILE": "tasks.json",
        "CUSTOMER_RULES": "顧客ルール.json",
    }
    base = dict.fromkeys(cfg, HERE)  # 項目ごとの相対パスの起点
    sj = load_setting_json()
    if sj is not None:
        for k, v in (sj.get("目検ツール") or {}).items():
            if not k.startswith("_"):  # 「_」で始まるのは説明
                cfg[k], base[k] = ("" if v is None else str(v)), ROOT
        if not cfg["HW_CSV"]:
            hw = (sj.get("採用担当者名寄せ") or {}).get("HW_CSV") or []
            hw = [hw] if isinstance(hw, str) else hw
            cfg["HW_CSV"], base["HW_CSV"] = next((x for x in hw if str(x).lower().endswith(".csv")), ""), ROOT
    else:
        p = os.path.join(HERE, "設定.txt")
        if os.path.isfile(p):
            for line in open(p, encoding="utf-8-sig"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip()
        if not cfg["HW_CSV"]:
            hp = os.path.join(HERE, "..", "採用担当者名寄せ", "設定.txt")
            if os.path.isfile(hp):
                for line in open(hp, encoding="utf-8-sig"):
                    line = line.strip()
                    if line and not line.startswith("#") and line.lower().endswith(".csv"):
                        cfg["HW_CSV"] = line
                        break

    def absp(k):
        x = cfg[k]
        return x if not x or os.path.isabs(x) else os.path.normpath(os.path.join(base.get(k, HERE), x))

    for k in ("APIKEY_FILE", "CHECKS_DIR", "TASKS_FILE", "CUSTOMER_RULES", "HW_CSV"):
        cfg[k] = absp(k)
    cfg["SOURCE"] = SETTING_JSON if sj is not None else os.path.join(HERE, "設定.txt")
    return cfg


CFG = load_settings()
# work: 処理するたびに作る作業用の納品用CSV・修正ログ・除外行(プレビュー・「ブラウザでダウンロード」・出力の元)
# output: 「納品用CSVを出力」したものだけ(output\{日付}\result と output\{日付}\log)
DIRS = {k: os.path.join(HERE, k) for k in ("input", "input_done", "output", "work", "cache", "exclude")}
for d in list(DIRS.values()) + [CFG["CHECKS_DIR"]]:
    os.makedirs(d, exist_ok=True)

DOWNLOAD_DIR = os.path.join(HERE, "download")  # 「この添付で目検」でBacklogから落とした添付の置き場所(目検ツール\download)

_tools = {}
_lock = threading.Lock()


def tools():
    with _lock:
        if not _tools:
            print("郵便番号データ・市外局番辞書を読み込み中...")
            _tools["zip"] = mc.ZipTools(KENALL)
            try:
                official = os.path.join(HERE, "data", "shigai_kyokuban.csv")
                codes = set()
                if os.path.isfile(official):
                    import csv as _csv
                    with open(official, encoding="utf-8-sig", newline="") as f:
                        codes = {r["市外局番"] for r in _csv.DictReader(f)}
                fmt = areacode.TelFormatter(CFG["HW_CSV"], os.path.join(DIRS["cache"], "tel_prefix.json"), codes)
                _tools["area"] = areacode.AreaCodeChecker(
                    CFG["HW_CSV"], KENALL, os.path.join(DIRS["cache"], "areacode.json"),
                    official_path=official, formatter=fmt)
            except Exception as e:  # noqa: BLE001
                print(f"  市外局番チェックは無効: {e}")
                _tools["area"] = None
            print("読み込み完了")
        return _tools


def bl():
    key = open(CFG["APIKEY_FILE"], encoding="utf-8-sig").read().strip()
    return backlog.Backlog(CFG["BACKLOG_SPACE"], key)


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def safe_id(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s)[:120]


def check_path(cid):
    return os.path.join(CFG["CHECKS_DIR"], safe_id(cid) + ".json")


def load_check(cid):
    p = check_path(cid)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_check(data):
    data["updated"] = now()
    p = check_path(data["id"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def fetch_issue(key):
    """課題とコメントを同時に取る(順番に待つより速い)。コメントが取れなくても課題は出す"""
    from concurrent.futures import ThreadPoolExecutor
    b = bl()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_iss, f_cms = ex.submit(b.issue, key), ex.submit(b.comments, key)
        iss = f_iss.result()
        try:
            cms = f_cms.result()
        except Exception:  # noqa: BLE001
            cms = []
    s = backlog.summarize_issue(iss, cms)
    s["url"] = f"{CFG['BACKLOG_SPACE'].rstrip('/')}/view/{s['key']}"
    return s


def read_table(path):
    """CSV / Excel を2次元リストで読む"""
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
    with open(path, "rb") as f:
        return mc.read_csv_bytes(f.read())[0]


def list_excludes():
    return [{"name": f, "size": os.path.getsize(os.path.join(DIRS["exclude"], f))}
            for f in sorted(os.listdir(DIRS["exclude"])) if f.lower().endswith((".csv", ".xlsx", ".xlsm"))]


def load_excludes(names):
    res = []
    for n in names or []:
        p = os.path.join(DIRS["exclude"], os.path.basename(n))
        if os.path.isfile(p):
            rows = read_table(p)
            if rows:
                res.append(mc.load_exclude_list(rows, os.path.splitext(os.path.basename(n))[0]))
    return res


# ---------------- 顧客ルール(課題の顧客名に合う 顧客ルール.json の内容を出す)
_CORP_RE = re.compile(r"株式会社|有限会社|合同会社|合資会社|合名会社|一般社団法人|一般財団法人|公益社団法人|公益財団法人|\(株\)|\(有\)|㈱|㈲")


def _cust_norm(s):
    """法人格・スペース・記号を無視して比べられる形にする"""
    s = unicodedata.normalize("NFKC", s or "")
    s = _CORP_RE.sub("", s)
    return re.sub(r"[\s・･.,、。'\"「」『』\[\]()/／\-‐_]", "", s).casefold()


def _cust_keys(*names):
    """名前と、カッコの中・外を別々にしたもの(例: ニュートン(保険見直し本舗グループ))"""
    keys = set()
    for n in names:
        n = unicodedata.normalize("NFKC", n or "")
        n = re.sub(r"\(\s*\d{6,}\s*\)", "", n)  # 「株式会社アクア(2012401016613)」の番号は名前に含めない
        for part in [n] + re.split(r"[()]", n):
            k = _cust_norm(part)
            if k:
                keys.add(k)
    return keys


def customer_rules(customer):
    p = CFG["CUSTOMER_RULES"]
    if not os.path.isfile(p):
        return {"matches": [], "file": p, "missing": True}
    with open(p, encoding="utf-8-sig") as f:
        rules = json.load(f).get("顧客ルール", [])
    want = _cust_keys(customer)
    ids = set(re.findall(r"\d{13}", unicodedata.normalize("NFKC", customer or "")))
    hits = []
    for r in rules:
        if r.get("状態") == "無効":
            continue
        rid = (r.get("顧客ID") or "").strip()
        if (want & _cust_keys(r.get("顧客名"), r.get("会社名"), *(r.get("別名") or []))) or (rid and rid in ids):
            hits.append(r)
    return {"matches": hits, "file": p}


def work_path(name):
    """作業ファイル(納品用CSV・修正ログ・除外行)の場所。前は output 直下に置いていたので、work に無ければそちらも見る"""
    name = os.path.basename(name or "")
    p = os.path.join(DIRS["work"], name)
    if not os.path.isfile(p) and os.path.isfile(os.path.join(DIRS["output"], name)):
        return os.path.join(DIRS["output"], name)
    return p


# ---------------- チェック履歴 = 処理したチェック(checks) ＋ 開いた課題(history.json。自分用なので tasks.json の隣)
_hist_lock = threading.Lock()


def history_path():
    return os.path.join(os.path.dirname(CFG["TASKS_FILE"]), "history.json")


def load_history():
    p = history_path()
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return {"items": {}}


def save_history(h):
    p = history_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def touch_history(key, summary=""):
    """課題を開いたら履歴に入れる(もう入っていれば開いた日時を今にする)"""
    with _hist_lock:
        h = load_history()
        h["items"][key] = {"key": key, "summary": summary or (h["items"].get(key) or {}).get("summary", ""), "opened": now()}
        save_history(h)


def set_history_done(key, done, summary=""):
    """左のタスクの完了チェックを履歴にも残す(タブを消しても「処理済」のまま)"""
    with _hist_lock:
        h = load_history()
        it = h["items"].get(key)
        if it is None:  # まだ開いていない課題を完了にしたときも履歴に入れる
            it = h["items"][key] = {"key": key, "summary": summary, "opened": now()}
        it["done"] = bool(done)
        save_history(h)


def history_items():
    """チェック履歴の一覧(新しい順)。処理したチェックと、開いただけの課題(kind=opened)をまとめる。
    日付は「最後に触った日」= チェックの更新日時と課題を開いた日時の新しいほう"""
    items = []
    for f in os.listdir(CFG["CHECKS_DIR"]):
        p = os.path.join(CFG["CHECKS_DIR"], f)
        if not f.endswith(".json") or f.startswith("_") or not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        chk = (d.get("report") or {}).get("checks", [])
        st = d.get("state", {})
        items.append({
            "id": d["id"], "key": (d.get("issue") or {}).get("key"), "kind": "check", "path": p,
            "updated": d.get("updated", ""),
            "summary": (d.get("issue") or {}).get("summary") or (d.get("report") or {}).get("filename"),
            "total": len(chk),
            "staff": sum(1 for c in chk if st.get(c["id"], {}).get("staff")),
            "owner": sum(1 for c in chk if st.get(c["id"], {}).get("owner")),
        })
    by_key = {it["key"]: it for it in items if it.get("key")}
    for k, h in load_history().get("items", {}).items():
        it = by_key.get(k)
        if it:
            if h.get("opened", "") > it["updated"]:
                it["updated"] = h["opened"]
            it["done"] = bool(h.get("done"))
        else:
            items.append({"id": k, "key": k, "kind": "opened", "updated": h.get("opened", ""), "done": bool(h.get("done")),
                          "summary": h.get("summary", ""), "total": 0, "staff": 0, "owner": 0})
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


def find_source(path):
    """チェックに保存した元ファイルの場所。フォルダの名前を変えた・別のPCで処理した などで
    そのパスに無ければ、このツールの input_done にある同じ名前のファイルを使う"""
    if path and os.path.isfile(path):
        return path
    if path:
        name = os.path.basename(path.replace("\\", "/"))
        alt = os.path.join(DIRS["input_done"], name)
        if os.path.isfile(alt):
            return alt
        # Backlogの添付は download\{日付}\{課題番号}\ にある。同じ名前があれば一番新しいもの
        hits = [os.path.join(r, name) for r, _, fs in os.walk(DOWNLOAD_DIR) if name in fs]
        if hits:
            return max(hits, key=os.path.getmtime)
    return None


def run_process(data_bytes, filename, issue_key=None, source="", options=None, saved_path=None, issue=None):
    t = tools()
    if not issue_key:
        m = backlog.ISSUE_KEY_RE.search(filename)
        issue_key = m.group(0) if m else None
    if issue_key and not issue:
        try:
            issue = fetch_issue(issue_key)
        except Exception as e:  # noqa: BLE001
            print(f"課題取得に失敗: {issue_key}: {e}（前回取得した課題情報を使います）")
            issue = (load_check(issue_key) or {}).get("issue")
    rows, enc = mc.read_csv_bytes(data_bytes)
    if not rows:
        raise ValueError("空のファイルです")
    options = options or {}
    excludes = load_excludes(options.get("exclude_files"))
    out_rows, removed, log, rep = mc.process(rows, filename, issue, t["zip"], t["area"], options, excludes)
    # 同じチェックの前回の作業ファイルは置き換える(再処理で件数違いのファイルが溜まらないように)
    prev = load_check(issue_key or os.path.splitext(filename)[0])
    for f in ((prev or {}).get("report") or {}).get("files", {}).values():
        p = work_path(f)
        if os.path.isfile(p):
            os.remove(p)
    base = os.path.splitext(rep["out_filename"])[0]
    out_name = rep["out_filename"] if rep["out_filename"].lower().endswith(".csv") else base + ".csv"
    mc.write_csv(os.path.join(DIRS["work"], out_name), out_rows)
    files = {"修正済みCSV": out_name}
    if len(log) > 1:
        mc.write_csv(os.path.join(DIRS["work"], base + "_修正ログ.csv"), log)
        files["修正ログ"] = base + "_修正ログ.csv"
    if removed:
        mc.write_csv(os.path.join(DIRS["work"], base + "_除外行.csv"), removed)
        files["除外行"] = base + "_除外行.csv"
    rep["files"] = files
    rep["encoding"] = enc
    rep["source"] = source

    cid = issue_key or os.path.splitext(filename)[0]
    data = load_check(cid) or {"id": cid, "state": {}, "created": now()}
    data["issue"] = issue
    data["report"] = rep
    data["options"] = options
    if saved_path:
        data["source_path"] = saved_path
    data["processed_at"] = now()
    save_check(data)
    if issue and issue.get("key"):
        add_task(issue["key"], issue.get("summary", ""), issue.get("url", ""), due=issue.get("due"))
    return data


# ---------------- タスクのタブ(左端の一覧)。自分用なので checks(共有の場合あり)ではなく TASKS_FILE に保存
_task_lock = threading.Lock()


def load_tasks():
    p = CFG["TASKS_FILE"]
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"version": 1, "tasks": []}


def save_tasks(d):
    p = CFG["TASKS_FILE"]
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def add_task(key=None, summary="", url="", name=None, due=None):
    """タブを追加する。同じ課題キーのタブが既にあれば件名・URL・期限だけ更新(名前・完了・メモはそのまま)"""
    with _task_lock:
        d = load_tasks()
        t = next((x for x in d["tasks"] if key and x.get("key") == key), None)
        if t:
            new = {"summary": summary, "url": url, **({"due": due} if due is not None else {})}
            if all(t.get(k) == v for k, v in new.items()):
                return d
            t.update(new)
        else:
            d["tasks"].insert(0, {
                "id": key or "m" + datetime.datetime.now().strftime("%Y%m%d%H%M%S%f"),
                "key": key, "name": name or summary or key or "新しいタスク", "summary": summary, "url": url,
                "due": due or "", "done": False, "notes": "", "created": now(), "updated": now()})
        save_tasks(d)
        return d


TASK_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+-\d+")


def parse_task_text(text):
    """貼り付けた文字から (課題キー, 件名) を取り出す。
    「STREAMREQ-18161 塗装　黒島リスト<TAB>10月1日…」の行 → 件名はキーの後ろ〜最初のタブまで
    「18161 18163」のような数字だけの行 → STREAMREQ-18161 など(件名は後でBacklogから取る)"""
    out, seen = [], set()

    def push(key, name=""):
        key = key.upper()
        if key not in seen:
            seen.add(key)
            out.append((key, name))

    for line in text.splitlines():
        keys = list(TASK_KEY_RE.finditer(line))
        if len(keys) == 1:
            push(keys[0].group(0), line[keys[0].end():].split("\t")[0].strip())
        elif keys:
            for m in keys:
                push(m.group(0))
        elif re.fullmatch(r"[\d\s,、，]+", line) and line.strip():
            for n in re.findall(r"\d+", line):
                push(f"STREAMREQ-{n}")
    return out


def add_tasks_bulk(text):
    """まとめて追加。全部の課題の期限(と件名が無いものは件名)をBacklogから取る。
    件名が分からない課題(番号だけで、Backlogで見つからない)は追加しない。追加済みの課題は期限だけ更新"""
    items = parse_task_text(text)
    info, errs = {}, {}  # 課題キー → (件名, 期限) / エラー
    if items:
        from concurrent.futures import ThreadPoolExecutor
        b = bl()

        def get(k):
            try:
                iss = b.issue(k)
                return k, (iss.get("summary") or "", (iss.get("dueDate") or "")[:10]), None
            except urllib.error.HTTPError as e:
                return k, None, "課題が見つかりません" if e.code == 404 else f"Backlog API エラー {e.code}"
            except Exception as e:  # noqa: BLE001
                return k, None, str(e)

        with ThreadPoolExecutor(max_workers=4) as ex:
            for k, v, err in ex.map(get, [k for k, _ in items]):
                if err:
                    errs[k] = err
                else:
                    info[k] = v
    base = CFG["BACKLOG_SPACE"].rstrip("/")
    added, existed, failed, nodue = [], [], [], []
    with _task_lock:
        d = load_tasks()
        by_key = {x.get("key"): x for x in d["tasks"] if x.get("key")}
        new = []
        for k, n in items:
            summary, due = info.get(k, ("", None))
            if k in by_key:
                existed.append(k)
                if due is not None:
                    by_key[k]["due"] = due
                continue
            n = n or summary
            if not n:
                failed.append({"key": k, "error": errs.get(k, "件名が分かりません")})
                continue
            if k in errs:
                nodue.append(k)
            new.append({"id": k, "key": k, "name": n, "summary": n, "url": f"{base}/view/{k}", "due": due or "",
                        "done": False, "notes": "", "created": now(), "updated": now()})
            added.append(k)
            by_key[k] = new[-1]
        d["tasks"][0:0] = new  # 貼り付けた順のまま一番上に
        save_tasks(d)
    return {"tasks": d["tasks"], "added": added, "existed": existed, "failed": failed, "nodue": nodue}


def clear_tasks():
    """タブを全部消す(直前の状態は .bak に残す)"""
    with _task_lock:
        p = CFG["TASKS_FILE"]
        if os.path.isfile(p):
            shutil.copyfile(p, p + ".bak")
        d = {"version": 1, "tasks": []}
        save_tasks(d)
        return d


def update_task(tid, fields):
    with _task_lock:
        d = load_tasks()
        t = next((x for x in d["tasks"] if x["id"] == tid), None)
        if not t:
            raise ValueError("タブが見つかりません")
        for k in ("name", "done", "notes"):
            if k in fields:
                t[k] = fields[k]
        if "rules_checked" in fields:  # 顧客ルールを確認したか(誰が・いつ)
            t["rules_checked"] = {"by": fields.get("by") or "名無し", "at": now()} if fields["rules_checked"] else None
        t["updated"] = now()
        save_tasks(d)
    if "done" in fields and t.get("key"):  # チェック履歴の「未処理 / 処理済」も合わせる
        set_history_done(t["key"], fields["done"], t.get("summary") or t.get("name") or "")
    return d


def delete_task(tid):
    with _task_lock:
        d = load_tasks()
        d["tasks"] = [x for x in d["tasks"] if x["id"] != tid]
        save_tasks(d)
        return d


# ---------------- 子課題(目検タスク・検品の担当)
_me = {}


def me():
    if not _me:
        _me.update(bl().myself())
    return _me


def child_tasks(key):
    """親課題の子課題一覧と、目検・検品の子課題"""
    b = bl()
    parent = b.issue(key)
    kids = b.children(parent["projectId"], parent["id"])
    base = CFG["BACKLOG_SPACE"].rstrip("/")
    items = [{"key": k["issueKey"], "type": k["issueType"]["name"], "summary": k["summary"],
              "assignee": (k.get("assignee") or {}).get("name"), "assignee_id": (k.get("assignee") or {}).get("id"),
              "status": k["status"]["name"], "url": f"{base}/view/{k['issueKey']}",
              "description": k.get("description") or "", "due": (k.get("dueDate") or "")[:10],
              "created": (k.get("created") or "")[:16].replace("T", " "),
              "created_user": (k.get("createdUser") or {}).get("name")} for k in kids]
    m = me()
    return {
        "parent": {"key": parent["issueKey"], "summary": parent["summary"], "id": parent["id"], "project_id": parent["projectId"],
                   "priority_id": (parent.get("priority") or {}).get("id")},
        "me": {"id": m["id"], "name": m["name"]},
        "children": items,
        "mokken": [c for c in items if c["summary"].startswith("目検")],
        "kenpin": [c for c in items if c["type"] == "検品"],
    }


def set_status(issue_key, project_id, name):
    sts = {s["name"]: s["id"] for s in bl().get(f"/projects/{project_id}/statuses")}
    if name not in sts:
        raise ValueError(f"状態「{name}」がプロジェクトにありません")
    bl().update_issue(issue_key, {"statusId": sts[name]})


def presets_path():
    return os.path.join(CFG["CHECKS_DIR"], "_列の並びプリセット.json")


def load_presets():
    p = presets_path()
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def comment_text(data):
    rep, st = data["report"], data.get("state", {})
    lines = [f"【目検チェック】{rep['filename']}",
             f"件数: {rep['rows_in']:,}件 → {rep['rows_out']:,}件" + (f"（除外 {rep['removed']:,}件）" if rep["removed"] else ""),
             ""]
    cur = None
    main = [it for it in rep["checks"] if not it.get("auto") or it["status"] == "ng"]
    notes = [it for it in rep["checks"] if it.get("auto") and it["status"] != "ng"]
    for it in main:
        if it["group"] != cur:
            cur = it["group"]
            lines.append(f"■ {cur}")
        s = st.get(it["id"], {})
        staff, owner = s.get("staff"), s.get("owner")
        mark = "✅" if staff else "⬜"
        who = []
        who.append(f"担当:{staff['by']}" if staff else "担当:未")
        who.append(f"オーナー:{owner['by']}" if owner else "オーナー:未")
        line = f"{mark} {it['title']}"
        if it.get("detail"):
            line += f" … {it['detail']}"
        line += f"（{' / '.join(who)}）"
        if s.get("memo"):
            line += f" メモ: {s['memo']}"
        lines.append(line)
    if notes:
        lines += ["", "■ 備考（自動処理の結果）"] + [f"・{it['title']} … {it['detail']}" for it in notes]
    c = rep["counts"]
    fixes = []
    for k, label in [("zip_filled", "郵便番号補完"), ("zip_refilled", "郵便番号引き直し"), ("capital_fixed", "資本金表記統一"),
                     ("emp_fixed", "従業員数表記統一"), ("person_fixed", "人名整形"), ("person_blank", "人名欄の無効値削除"),
                     ("addr_norm", "住所正規化"), ("zip_moved", "住所内の郵便番号を列へ移動"), ("tel_format", "TELハイフン追加・位置修正"), ("tel_unformattable", "ハイフン追加できないTEL削除"), ("tel_deleted", "市外局番不一致TEL削除"), ("tel_nohyphen", "ハイフンなしTEL削除"), ("tel_bad", "桁数不正TEL削除"),
                     ("name_eq", "名称先頭の=削除"), ("pref_fixed", "都道府県を住所に合わせて修正"),
                     ("capital_deleted", "資本金の不正値削除"), ("emp_deleted", "従業員数の不正値削除"),
                     ("person_deleted", "人名の不正値削除"), ("zip_deleted", "郵便番号の不正値削除")]:
        if c.get(k):
            fixes.append(f"{label} {c[k]:,}件")
    if fixes:
        lines += ["", "■ 自動修正", "、".join(fixes)]
    if rep.get("removed_reasons"):
        lines += ["", "■ 除外した行"] + [f"・{k} … {v:,}件" for k, v in rep["removed_reasons"]]
    off = [lbl for k, lbl, d, _ in mc.RULES if d and rep.get("options", {}).get(k) is False]
    if off:
        lines += ["", "■ OFFにしたルール", "、".join(off)]
    return "\n".join(lines)


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _err(self, e):
        if isinstance(e, urllib.error.HTTPError):
            try:
                msg = e.read().decode("utf-8")
            except Exception:  # noqa: BLE001
                msg = str(e)
            # 429/5xx(混雑)は 503 で返す → 画面側で再試行する。404 などは再試行しても同じなので 502
            return self._send(503 if e.code in backlog.RETRY_STATUS else 502, {"error": f"Backlog API エラー {e.code}: {msg[:300]}"})
        if isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError)):
            return self._send(504, {"error": f"Backlogに接続できませんでした（時間切れ・通信エラー）: {getattr(e, 'reason', e)}"})
        traceback.print_exc()
        return self._send(500, {"error": str(e)})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/api/preview":
                # 納品用CSVの先頭N行(大きいファイルでも先頭だけ読む)
                import csv
                import itertools
                d = load_check(q.get("id", ""))
                name = (((d or {}).get("report") or {}).get("files") or {}).get("修正済みCSV")
                p = work_path(name)
                if not name or not os.path.isfile(p):
                    return self._send(404, {"error": "納品用CSVが見つかりません"})
                n = max(1, min(int(q.get("n") or 10), 100))
                csv.field_size_limit(2 ** 31 - 1)
                with open(p, encoding="utf-8-sig", newline="") as f:
                    rows = list(itertools.islice(csv.reader(f), n + 1))
                return self._send(200, {"header": rows[0] if rows else [], "rows": rows[1:],
                                        "total": d["report"].get("rows_out")})
            if u.path == "/api/child_template":
                p = os.path.join(HERE, "子課題テンプレート.txt")
                text = open(p, encoding="utf-8-sig").read() if os.path.isfile(p) else ""
                return self._send(200, {"text": text, "file": p})
            if u.path == "/api/child_detail":
                # 編集ウィンドウの右側に出す、子課題の今の詳細(Backlogから最新を取る)
                k = bl().issue(q.get("key", ""))
                return self._send(200, {
                    "key": k["issueKey"], "summary": k["summary"], "description": k.get("description") or "",
                    "updated": (k.get("updated") or "")[:16].replace("T", " "),
                    "updated_user": (k.get("updatedUser") or {}).get("name")})
            if u.path == "/api/tasks":
                return self._send(200, child_tasks(q.get("key", "")))
            if u.path == "/api/issue":
                k = q.get("key", "").strip().upper()
                if k.isdigit():  # 「18016」だけ入れたら STREAMREQ-18016
                    k = f"STREAMREQ-{k}"
                m = backlog.ISSUE_KEY_RE.search(k)
                if not m:
                    return self._send(400, {"error": "課題キー(例: STREAMREQ-15096)を入れてください"})
                iss = fetch_issue(m.group(0))
                try:
                    add_task(iss["key"], iss.get("summary", ""), iss.get("url", ""), due=iss.get("due"))
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                return self._send(200, {"issue": iss, "check": load_check(m.group(0))})
            if u.path == "/api/customer_rules":
                return self._send(200, customer_rules(q.get("name", "")))
            if u.path == "/api/task_list":
                return self._send(200, load_tasks())
            if u.path == "/api/rules":
                return self._send(200, {"rules": [{"key": k, "label": l, "default": d, "kind": g} for k, l, d, g in mc.RULES]})
            if u.path == "/api/presets":
                return self._send(200, {"presets": load_presets()})
            if u.path == "/api/excludes":
                return self._send(200, {"files": list_excludes(), "dir": DIRS["exclude"]})
            if u.path == "/api/inputs":
                files = [{"name": f, "size": os.path.getsize(os.path.join(DIRS["input"], f))}
                         for f in sorted(os.listdir(DIRS["input"])) if f.lower().endswith(".csv")]
                return self._send(200, {"files": files, "dir": DIRS["input"]})
            if u.path == "/api/checks":
                items = [{k: v for k, v in it.items() if k != "path"} for it in history_items()]
                return self._send(200, {"items": items, "dir": CFG["CHECKS_DIR"]})
            if u.path == "/api/check":
                return self._send(200, {"check": load_check(q.get("id", ""))})
            if u.path == "/api/comment_preview":
                d = load_check(q.get("id", ""))
                return self._send(200, {"text": comment_text(d) if d else ""})
            if u.path.startswith("/files/"):
                name = os.path.basename(urllib.parse.unquote(u.path[len("/files/"):]))
                p = work_path(name)
                if not os.path.isfile(p):
                    return self._send(404, {"error": "not found"})
                with open(p, "rb") as f:
                    data = f.read()
                return self._send(200, data, "text/csv; charset=utf-8",
                                  {"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})
            if u.path == "/api/open_folder":
                which = q.get("which", "output")
                p = DOWNLOAD_DIR if which == "download" else DIRS.get(which, DIRS["output"])
                os.makedirs(p, exist_ok=True)
                sub = os.path.normpath(os.path.join(p, q.get("sub", "")))  # download\20261001\STREAMREQ-xxxxx など
                if os.path.commonpath([sub, p]) == p and os.path.isdir(sub):  # フォルダの外は開かない
                    p = sub
                os.startfile(p)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._err(e)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path == "/api/upload":
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n)
                name = os.path.basename(q.get("name", "upload.csv"))
                sp = os.path.join(DIRS["input_done"], name)
                with open(sp, "wb") as f:
                    f.write(data)
                d = run_process(data, name, q.get("key") or None, "アップロード", json.loads(q.get("opt") or "{}"), sp)
                return self._send(200, {"check": d})
            if u.path == "/api/process_input":
                b = self._json_body()
                name = os.path.basename(b["name"])
                p = os.path.join(DIRS["input"], name)
                with open(p, "rb") as f:
                    data = f.read()
                sp = os.path.join(DIRS["input_done"], name)
                d = run_process(data, name, b.get("key") or None, "inputフォルダ", b.get("options"), sp)
                shutil.move(p, sp)
                return self._send(200, {"check": d})
            if u.path == "/api/process_attachment":
                b = self._json_body()
                key, att = b["key"], b["id"]
                iss = fetch_issue(key)
                a = next(x for x in iss["attachments"] if str(x["id"]) == str(att))
                data = bl().attachment(key, att)
                name = re.sub(r"\.gz$", "", a["name"])
                # Backlogから落とした添付は 目検ツール\download\{今日の日付}\{課題番号}\ に置く
                dl_dir = os.path.join(DOWNLOAD_DIR, datetime.datetime.now().strftime("%Y%m%d"), safe_id(key))
                os.makedirs(dl_dir, exist_ok=True)
                sp = os.path.join(dl_dir, os.path.basename(name))
                with open(sp, "wb") as f:
                    f.write(data)
                d = run_process(data, name, key, "Backlog添付", b.get("options"), sp)
                return self._send(200, {"check": d})
            if u.path == "/api/reprocess":
                b = self._json_body()
                old = load_check(b["id"])
                sp = find_source((old or {}).get("source_path"))
                if not sp:
                    return self._send(400, {"error": "元ファイルが見つかりません。もう一度ファイルを入れてください"})
                with open(sp, "rb") as f:
                    data = f.read()
                key = (old.get("issue") or {}).get("key")
                d = run_process(data, os.path.basename(sp), key, re.sub(r"(・再処理)+$", "", old["report"].get("source", "")) + "・再処理",
                                b.get("options"), sp, issue=old.get("issue") if not b.get("refresh") else None)
                return self._send(200, {"check": d})
            if u.path == "/api/presets":
                b = self._json_body()
                ps = load_presets()
                if b.get("delete"):
                    ps.pop(b["name"], None)
                else:
                    ps[b["name"]] = b["columns"]
                with open(presets_path(), "w", encoding="utf-8") as f:
                    json.dump(ps, f, ensure_ascii=False, indent=1)
                return self._send(200, {"presets": ps})
            if u.path == "/api/upload_exclude":
                n = int(self.headers.get("Content-Length") or 0)
                name = os.path.basename(q.get("name", "exclude.csv"))
                with open(os.path.join(DIRS["exclude"], name), "wb") as f:
                    f.write(self.rfile.read(n))
                return self._send(200, {"files": list_excludes()})
            if u.path == "/api/remove_exclude":
                b = self._json_body()
                p = os.path.join(DIRS["exclude"], os.path.basename(b["name"]))
                done = os.path.join(DIRS["exclude"], "_外したファイル")
                os.makedirs(done, exist_ok=True)
                if os.path.isfile(p):
                    shutil.move(p, os.path.join(done, os.path.basename(p)))
                return self._send(200, {"files": list_excludes()})
            if u.path == "/api/check_item":
                b = self._json_body()
                d = load_check(b["id"])
                if not d:
                    return self._send(404, {"error": "チェックデータがありません"})
                s = d["state"].setdefault(b["item"], {})
                if "role" in b:
                    s[b["role"]] = {"by": b.get("by") or "名無し", "at": now()} if b.get("on") else None
                if "memo" in b:
                    s["memo"] = b["memo"]
                save_check(d)
                return self._send(200, {"check": d})
            if u.path == "/api/create_mokken":
                # 目検の子課題を作る: 種別「その他」、件名「目検　{親課題名}」、担当は自分(APIキーの持ち主)
                b = self._json_body()
                t = child_tasks(b["key"])
                if t["mokken"]:
                    return self._send(409, {"error": f"目検タスクはもうあります: {t['mokken'][0]['key']}"})
                p = t["parent"]
                types = {x["name"]: x["id"] for x in bl().issue_types(p["project_id"])}
                if "その他" not in types:
                    return self._send(400, {"error": "課題の種別「その他」が見つかりません"})
                res = bl().create_issue({
                    "projectId": p["project_id"], "summary": f"目検　{p['summary']}", "issueTypeId": types["その他"],
                    "priorityId": p["priority_id"] or 3, "parentIssueId": p["id"], "assigneeId": t["me"]["id"]})
                set_status(res["issueKey"], p["project_id"], "処理済み")  # 作ったらそのまま処理済みに
                return self._send(200, {"created": res["issueKey"], "tasks": child_tasks(b["key"])})
            if u.path == "/api/set_done":
                # 子課題(目検など)を処理済みにする
                b = self._json_body()
                t = child_tasks(b["key"])
                set_status(b["child"], t["parent"]["project_id"], "処理済み")
                return self._send(200, {"tasks": child_tasks(b["key"])})
            if u.path == "/api/update_child":
                # 子課題の詳細(本文)を、編集ウィンドウの左側の内容で上書きする
                b = self._json_body()
                t = child_tasks(b["key"])
                if b["child"] not in {c["key"] for c in t["children"]}:
                    return self._send(400, {"error": f"{b['child']} は {b['key']} の子課題ではありません"})
                bl().update_issue(b["child"], {"description": b.get("description") or ""})
                return self._send(200, {"tasks": child_tasks(b["key"])})
            if u.path == "/api/assign_me":
                # 検品などの子課題の担当者を自分にする
                b = self._json_body()
                bl().update_issue(b["child"], {"assigneeId": me()["id"]})
                return self._send(200, {"tasks": child_tasks(b["key"])})
            if u.path == "/api/task_add":
                b = self._json_body()
                return self._send(200, add_task(name=(b.get("name") or "").strip() or None))
            if u.path == "/api/deliver":
                # 「納品用CSVを出力」: 作業フォルダ(work)から
                #   納品用CSV        → output\{今日の日付}\result\
                #   修正ログ・除外行  → output\{今日の日付}\log\
                # にコピーして、どこに置いたかをチェックデータに残す(これ以外は output に出さない)
                b = self._json_body()
                d = load_check(b["id"])
                files = ((d or {}).get("report") or {}).get("files", {})
                name = files.get("修正済みCSV")
                src = work_path(name)
                if not name or not os.path.isfile(src):
                    return self._send(404, {"error": "納品用CSVが見つかりません。もう一度処理してください"})
                day = datetime.datetime.now().strftime("%Y%m%d")
                result_dir = os.path.join(DIRS["output"], day, "result")
                log_dir = os.path.join(DIRS["output"], day, "log")
                os.makedirs(result_dir, exist_ok=True)
                dest = os.path.join(result_dir, os.path.basename(name))
                shutil.copyfile(src, dest)
                logs = []
                for k in ("修正ログ", "除外行"):
                    lp = work_path(files.get(k)) if files.get(k) else None
                    if lp and os.path.isfile(lp):
                        os.makedirs(log_dir, exist_ok=True)
                        logs.append(os.path.join(log_dir, os.path.basename(lp)))
                        shutil.copyfile(lp, logs[-1])
                d["delivered"] = {"file": name, "path": dest, "logs": logs, "base": "output", "folder": day, "at": now(),
                                  "processed_at": d.get("processed_at")}
                save_check(d)
                return self._send(200, {"check": d, "path": dest})
            if u.path == "/api/clear_checks":
                # その日のチェック履歴を消す(日付は一覧と同じ「最後に触った日」)
                #   処理したチェック → 消さずに checks\_削除した履歴\{日付}\ に移す(戻せるように)
                #   開いただけの課題 → history.json から外す(外したものは history.json.bak に残す)
                b = self._json_body()
                day = str(b.get("date") or "")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    return self._send(400, {"error": "日付が正しくありません"})
                dest = os.path.join(CFG["CHECKS_DIR"], "_削除した履歴", day)
                moved, keys = [], set()
                for it in history_items():
                    if (it["updated"] or "")[:10] != day:
                        continue
                    if it.get("key"):
                        keys.add(it["key"])
                    if it["kind"] == "check":
                        f = os.path.basename(it["path"])
                        os.makedirs(dest, exist_ok=True)
                        to = os.path.join(dest, f)
                        if os.path.exists(to):  # 同じ日に同じ課題を2回消したときは上書きせず番号を付ける
                            n = 2
                            while os.path.exists(os.path.join(dest, f"{f[:-5]}_{n}.json")):
                                n += 1
                            to = os.path.join(dest, f"{f[:-5]}_{n}.json")
                        shutil.move(it["path"], to)
                    moved.append(it["id"])
                if keys:
                    with _hist_lock:
                        h = load_history()
                        gone = {k: v for k, v in h["items"].items() if k in keys}
                        if gone:
                            with open(history_path() + ".bak", "w", encoding="utf-8") as fb:
                                json.dump({"items": gone}, fb, ensure_ascii=False, indent=1)
                            h["items"] = {k: v for k, v in h["items"].items() if k not in keys}
                            save_history(h)
                return self._send(200, {"moved": moved, "dir": dest})
            if u.path == "/api/history_touch":
                b = self._json_body()
                if b.get("key"):
                    touch_history(b["key"], b.get("summary") or "")
                return self._send(200, {"ok": True})
            if u.path == "/api/task_add_bulk":
                return self._send(200, add_tasks_bulk(self._json_body().get("text") or ""))
            if u.path == "/api/task_clear":
                return self._send(200, clear_tasks())
            if u.path == "/api/task_update":
                b = self._json_body()
                return self._send(200, update_task(b["id"], b))
            if u.path == "/api/task_delete":
                b = self._json_body()
                return self._send(200, delete_task(b["id"]))
            if u.path == "/api/post_comment":
                b = self._json_body()
                d = load_check(b["id"])
                key = (d.get("issue") or {}).get("key")
                if not key:
                    return self._send(400, {"error": "Backlog課題に紐づいていません"})
                res = bl().add_comment(key, b.get("text") or comment_text(d))
                d.setdefault("posted", []).append({"at": now(), "comment_id": res.get("id")})
                save_check(d)
                return self._send(200, {"ok": True, "check": d})
            return self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._err(e)


class Server(ThreadingHTTPServer):
    # Windows では SO_REUSEADDR だと同じポートに2つ起動できてしまい、古い方が応答して新しい機能が動かない
    allow_reuse_address = False


def stop_old_instances(port):
    """前に起動したまま残っている目検ツールを終了させる
    (この app.py を起動しているPython、または同じポートを使っている app.py のPython)"""
    if os.name != "nt":
        return
    import subprocess
    skip = f"{os.getpid()},{os.getppid()}"  # 自分と、自分を起動した py.exe は除く
    ps = ("$skip = $env:MOKKEN_SKIP -split ','; "
          "$onPort = @(Get-NetTCPConnection -LocalPort $env:MOKKEN_PORT -State Listen -ErrorAction SilentlyContinue | "
          "ForEach-Object { [string]$_.OwningProcess }); "
          "Get-CimInstance Win32_Process -Filter \"Name like 'py%'\" | "
          "Where-Object { $_.CommandLine -and ($skip -notcontains [string]$_.ProcessId) -and "
          "($_.CommandLine.Contains($env:MOKKEN_APP) -or (($onPort -contains [string]$_.ProcessId) -and $_.CommandLine.Contains('app.py'))) } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, MOKKEN_APP=os.path.abspath(__file__), MOKKEN_SKIP=skip, MOKKEN_PORT=str(port)))
        pids = r.stdout.split()
        if pids:
            print(f"前に起動していた目検ツールを終了しました（PID {', '.join(pids)}）")
    except Exception as e:  # noqa: BLE001
        print(f"前の目検ツールの確認に失敗: {e}")


def main():
    port = int(CFG["PORT"])
    stop_old_instances(port)
    srv = None
    for _ in range(10):  # 終了させた古いプロセスがポートを離すまで少し待つ
        try:
            srv = Server(("127.0.0.1", port), H)
            break
        except OSError:
            threading.Event().wait(0.5)
    if srv is None:
        print(f"ポート {port} が他のプログラムに使われていて起動できません。")
        print("開いている目検ツールの黒いウィンドウを全部閉じてから、もう一度起動してください。")
        return
    url = f"http://localhost:{port}/"
    print(f"目検ツールを起動しました: {url}")
    print(f"設定ファイル: {CFG['SOURCE']}")
    print(f"チェック保存先: {CFG['CHECKS_DIR']}")
    print("このウィンドウを閉じると終了します。")
    threading.Thread(target=tools, daemon=True).start()
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    srv.serve_forever()


if __name__ == "__main__":
    main()
