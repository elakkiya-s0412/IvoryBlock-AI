out = []
for r in rows:
    sr, lab = seller_risk(r["seller_id"])
    out.append({"id": r["id"], "title": r["title"], "description": r["description"], "price": r["price"],
                    "category": r["category"], "seller": r["seller"], "seller_id": r["seller_id"],
                    "seller_risk": sr, "seller_label": lab, "status": r["status"], "score": r["score"],
                    "level": r["level"], "nb": r["nb"], "lr": r["lr"], "evidence": r["evidence"],
                    "reason": r["reason"], "open_reports": r["open_reports"], "created": r["created"],
                    "admin_action": r["admin_action"]})
return out


def sellers_overview():
    with closing(db()) as c:
        users = c.execute("SELECT * FROM users WHERE role='seller' ORDER BY id").fetchall()
        out = []
        for u in users:
            cnt = {r["status"]: r["n"] for r in c.execute(
                "SELECT status, COUNT(*) n FROM listings WHERE seller_id=? GROUP BY status", (u["id"],))}
            sr, lab = seller_risk(u["id"])
            out.append({"id": u["id"], "username": u["username"], "active": bool(u["active"]), "risk": sr,
                        "label": lab, "total": sum(cnt.values()), "published": cnt.get("published", 0),
                        "review": cnt.get("review", 0), "blocked": cnt.get("blocked", 0),
                        "rejected": cnt.get("rejected", 0), "created": u["created"]})
    return out


# ============================================================================
# 9. HTTP SERVER
# ============================================================================
ENGINE = None


def report_md(sid):
    with closing(db()) as c:
        r = c.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone()
    if not r:
        return None
    ents = json.loads(r["entities"] or "[]")
    lines = [
        "# IvoryBlock Intelligence Brief", "",
        "- Case ID: %s" % r["id"], "- Timestamp: %s" % r["ts"],
        "- Risk index: %s / 100 (%s)" % (r["score"], r["level"]),
        "- Naive Bayes: %s%%  |  Logistic Regression: %s%%" % (r["nb"], r["lr"]),
        "", "## Intercepted text", "", "> " + r["text"], "", "## Extracted indicators", "",
    ]
    lines += ["- [%s] %s" % (e["cat"], e["text"]) for e in ents] or ["- none"]
    lines += ["", "## Recommendation", "",
              "Escalate to a human analyst." if r["level"] != "CLEAN" else "No action required.", ""]
    return "\n".join(lines)


def analytics():
    with closing(db()) as c:
        rows = c.execute("SELECT id,score,level,entities FROM scans ORDER BY id").fetchall()
    levels = Counter(r["level"] for r in rows)
    buckets = [0] * 10
    cats, terms = Counter(), Counter()
    for r in rows:
        buckets[min(9, r["score"] // 10)] += 1
        for e in json.loads(r["entities"] or "[]"):
            cats[e["cat"]] += 1
            terms[e["text"].lower()] += 1
    return {
        "total": len(rows),
        "levels": {k: levels.get(k, 0) for k in ("CRITICAL", "SUSPICIOUS", "CLEAN")},
        "buckets": buckets,
        "cats": {k: cats.get(k, 0) for k in ("species", "payment", "evasion", "quantity")},
        "terms": terms.most_common(8),
        "timeline": [{"id": r["id"], "score": r["score"], "level": r["level"]} for r in rows[-30:]],
    }


def export_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "timestamp", "source", "risk_score", "level", "naive_bayes", "logistic_regression", "text"])
    with closing(db()) as c:
        for r in c.execute("SELECT * FROM scans ORDER BY id"):
            w.writerow([r["id"], r["ts"], r["source"], r["score"], r["level"], r["nb"], r["lr"], r["text"]])
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ---- helpers ----
    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _page(self, name):
        path = os.path.join(BASE, name)
        if not os.path.exists(path):
            return self._send(500, "<h2>%s is missing</h2><p>Put it in the same folder as app.py.</p>" % name, "text/html")
        with open(path, "rb") as fh:
            self._send(200, fh.read(), "text/html")

    def _token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = cookies.SimpleCookie(raw)
            return c["ib_session"].value if "ib_session" in c else None
        except Exception:
            return None

    def _user(self):
        tok = self._token()
        if not tok:
            return None
        with SESS_LOCK:
            uid = SESSIONS.get(tok)
        if not uid:
            return None
        with closing(db()) as c:
            return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    def _need(self, *roles):
        u = self._user()
        if not u:
            self._send(401, {"error": "Please log in."})
            return None
        if roles and u["role"] not in roles:
            self._send(403, {"error": "Your account type cannot do this."})
            return None
        return u

    def _login_cookie(self, uid):
        tok = secrets.token_urlsafe(32)
        with SESS_LOCK:
            SESSIONS[tok] = uid
        return {"Set-Cookie": "ib_session=%s; Path=/; HttpOnly; SameSite=Lax" % tok}

    # ---- GET ----
    def do_GET(self):
        try:
            self._get()
        except Exception as ex:
            self._send(500, {"error": str(ex)})

    def _get(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        if p in ("/", "/index.html"):
            return self._page("index.html")
        if p in ("/admin", "/admin.html"):
