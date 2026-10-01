# -*- coding: utf-8 -*-
"""Backlog API の最小クライアント(標準ライブラリのみ)"""
import gzip
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request

ISSUE_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]+-\d+")


class Backlog:
    def __init__(self, space_url, api_key):
        self.base = space_url.rstrip("/") + "/api/v2"
        self.key = api_key.strip()

    def _url(self, path, params=None):
        q = {"apiKey": self.key}
        q.update(params or {})
        return f"{self.base}{path}?{urllib.parse.urlencode(q, doseq=True)}"

    @staticmethod
    def _open(req, timeout):
        """環境のプロキシで失敗したら直接接続でもう一度試す"""
        try:
            return urllib.request.urlopen(req, timeout=timeout).read()
        except urllib.error.URLError as e:
            if isinstance(e, urllib.error.HTTPError) or "Proxy" not in str(e.reason) and "Tunnel" not in str(e.reason):
                raise
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            return opener.open(req, timeout=timeout).read()

    def get(self, path, params=None, raw=False):
        body = self._open(self._url(path, params), 120 if raw else 20)
        return body if raw else json.loads(body.decode("utf-8"))

    def post(self, path, data):
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(self._url(path), data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        return json.loads(self._open(req, 60).decode("utf-8"))

    def issue(self, key):
        return self.get(f"/issues/{key}")

    def comments(self, key):
        return self.get(f"/issues/{key}/comments", {"count": 100, "order": "asc"})

    def attachment(self, key, att_id):
        """添付をダウンロードして (bytes)。.gz は展開して返す"""
        data = self.get(f"/issues/{key}/attachments/{att_id}", raw=True)
        if data[:2] == b"\x1f\x8b":
            data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
        return data

    def add_comment(self, key, content):
        return self.post(f"/issues/{key}/comments", {"content": content})

    def patch(self, path, data):
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(self._url(path), data=body, method="PATCH",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        return json.loads(self._open(req, 60).decode("utf-8"))

    def myself(self):
        return self.get("/users/myself")

    def issue_types(self, project_id):
        return self.get(f"/projects/{project_id}/issueTypes")

    def children(self, project_id, parent_id):
        return self.get("/issues", {"projectId[]": project_id, "parentIssueId[]": parent_id, "count": 100, "sort": "created", "order": "asc"})

    def create_issue(self, data):
        return self.post("/issues", data)

    def update_issue(self, key, data):
        return self.patch(f"/issues/{key}", data)


def parse_sections(description):
    """「## 見出し」で区切られた課題本文を {見出し: 本文} にする"""
    sections, cur = {}, None
    for line in (description or "").splitlines():
        m = re.match(r"^#{1,6}\s*(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            sections[cur] = []
        elif cur:
            sections[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def detail_lines(sections):
    """リスト詳細の各行(・や-で始まる行)を取り出す"""
    text = sections.get("リスト詳細", "")
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"^\s*[・\-*●○■□◆◇]\s*", "", ln).strip()
        if ln:
            lines.append(ln)
    return lines


def summarize_issue(iss, comments=None):
    sec = parse_sections(iss.get("description"))
    cf = {c["name"]: c.get("value") for c in iss.get("customFields", [])}

    def val(v):
        if isinstance(v, dict):
            return v.get("name")
        if isinstance(v, list):
            return "、".join(x.get("name", "") for x in v)
        return v

    return {
        "key": iss["issueKey"],
        "summary": iss.get("summary"),
        "status": (iss.get("status") or {}).get("name"),
        "assignee": (iss.get("assignee") or {}).get("name"),
        "created_user": (iss.get("createdUser") or {}).get("name"),
        "due": (iss.get("dueDate") or "")[:10],
        "customer": val(cf.get("顧客名")),
        "sales": val(cf.get("営業担当者")),
        "sections": sec,
        "details": detail_lines(sec),
        "area": sec.get("エリア", ""),
        "attachments": [{"id": a["id"], "name": a["name"], "size": a["size"],
                         "created": a.get("created", "")[:16].replace("T", " ")}
                        for a in iss.get("attachments", [])],
        "comments": [{"user": (c.get("createdUser") or {}).get("name"),
                      "created": c.get("created", "")[:16].replace("T", " "),
                      "content": c.get("content") or ""}
                     for c in (comments or []) if (c.get("content") or "").strip()],
    }
