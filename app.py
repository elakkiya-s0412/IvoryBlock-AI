"""
IvoryBlock - AI wildlife-crime screening for an online marketplace.

  * GreenBazaar  (index.html)  : demo marketplace for sellers and buyers
  * IvoryBlock   (admin.html)  : admin console, AI lab, review queue

Zero-install: uses ONLY the Python standard library (Python 3.8+).
Run:  python app.py        then open http://127.0.0.1:8000
"""
import csv
import hashlib
import hmac
import io
import json
import math
import os
import random
import re
import secrets
import sqlite3
import sys
import threading
import time
import webbrowser
from collections import Counter, defaultdict
from contextlib import closing
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "ivoryblock.db")

# ============================================================================
# 1. TEXT FEATURES  (with simple obfuscation handling: "1v0ry" -> "ivory")
# ============================================================================
STOP = set(
    "a an the and or of to in on for with is are was be at by it this that from as "
    "we you i our your my me can will".split()
)
LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}


def normalize(text):
    """Lower-case and undo leetspeak. Length-preserving, so character offsets stay valid."""
    out = []
    for m in re.finditer(r"\S+|\s+", text.lower()):
        tok = m.group(0)
        if not tok.isspace() and sum(ch.isalpha() for ch in tok) >= 3:
            tok = "".join(LEET.get(ch, ch) for ch in tok)
        out.append(tok)
    return "".join(out)


def feats_raw(text):
    """Same features but WITHOUT undoing leetspeak (used only to show why normalization helps)."""
    toks = [t for t in re.findall(r"[a-z][a-z0-9]+", text.lower()) if t not in STOP]
    f = set(toks)
    for a, b in zip(toks, toks[1:]):
        f.add(a + "_" + b)
    return f


def feats(text):
    """Binary unigram + bigram feature set (computed on normalized text)."""
    norm = normalize(text)
    toks = [t for t in re.findall(r"[a-z][a-z0-9]+", norm) if t not in STOP]
    f = set(toks)
    for a, b in zip(toks, toks[1:]):
        f.add(a + "_" + b)
    if norm != text.lower():
        f.add("__obfuscated__")
    return f


# ============================================================================
# 2. SYNTHETIC TRAINING DATA (safe + offline; no real dark-web crawling)
# ============================================================================
SELL = ["Selling", "For sale:", "Available now:", "WTS:", "Vendor stock:", "Fresh stock of", "Offering"]
QTY = ["5kg", "bulk lot of", "wholesale", "2 pieces", "10 pcs", "genuine", "authentic", "raw", "carved", "20kg"]
SPECIES = [
    "ivory carvings", "raw ivory", "elephant tusks", "rhino horn", "rhino horn powder", "pangolin scales",
    "tiger skin", "tiger bone", "leopard pelt", "bear bile", "sea turtle shell", "hawksbill shell",
    "totoaba bladder", "shahtoosh shawl", "ivory bangles", "tusks", "pangolin scales dried",
    "star tortoises", "red sanders logs", "mongoose hair brushes", "tokay gecko", "leopard claws",
]
CODED = ["white gold carvings", "mountain medicine powder", "traditional horn pieces",
         "special carved pieces from old stock", "grey scale pieces", "rare bone pieces"]
EVADE = [
    "Shipping discreetly, stealth packaging", "Contact on Telegram only", "Message via encrypted chat",
    "Private channel only, no questions asked", "Ships undeclared", "PGP required for contact",
    "Customs safe packaging", "Message me on Wickr", "Find me on the onion market", "Ships quietly worldwide",
]
PAY = [
    "Payment in Monero only", "BTC or XMR via escrow", "Crypto only", "Bitcoin escrow accepted",
    "Pay with gift cards or crypto", "USDT or Western Union", "Wallet address after PGP check",
]
PLACES = ["Mombasa", "Nairobi", "Dubai", "Hong Kong", "Hanoi", "Guangzhou", "Laos", "Bangkok", "Lagos",
          "Maputo", "Singapore", "Kuala Lumpur", "Zanzibar", "Imphal", "Moreh", "Yangon"]

SELL_B = ["Selling", "For sale:", "Available:", "Brand new", "Listing:", "Buy"]
ITEMS = [
    "leather boots", "faux leather jacket", "ceramic coffee mugs", "oak dining table", "wool blanket",
    "smartphone 128GB", "cotton t-shirts", "running shoes", "laptop stand", "bamboo cutting board",
    "ivory white wedding dress", "ivory colored curtains", "Tiger Balm muscle rub", "rhino toy plush for kids",
    "elephant print cushion", "pangolin documentary DVD", "digital kitchen scale", "scales of justice desk statue",
    "car horn kit", "bull horn megaphone", "python programming book", "bear plush toy",
    "turtle shell pattern phone case", "leopard print scarf", "snake plant in pot", "stainless steel parrot cage",
    "wildlife conservation donation receipt", "safari photography tour booking", "tiger print yoga mat",
    "ivory keys cleaning cloth for piano", "horn rimmed glasses", "fish scale pattern wallpaper",
    "rhino beetle science kit", "elephant figurine in resin", "bluetooth speaker", "gaming mouse",
    "three seater sofa set", "acetate frame sunglasses", "mango wood figurine", "gold ring 18k hallmark",
    "handloom cotton kurta", "school stationery set", "herbal tea gift box", "mountain bike helmet",
    "ayurvedic hair oil with licence number", "resin earrings", "brass pooja lamp", "cotton bedsheet with animal print",
]
COND = ["Brand new", "Barely used", "Minor scratches", "Excellent condition", "Handmade", "Unopened"]
SHIP_B = [
    "Free shipping over $50", "Pickup available", "Pay by card or PayPal", "Returns accepted within 30 days",
    "Ships in 2 days with tracking", "Accepting UPI or bank transfer", "Cash on delivery available",
    "Crypto or card accepted, invoice provided", "Bitcoin and UPI both fine, bill included",
    "Escrow available for large orders, returns allowed", "Contact on Telegram or email, replies within a day",
    "Plain packaging, ships with tracking number", "Pay with PayPal, UPI or Ethereum, receipt provided",
]
BENIGN_CRYPTO = [
    "Freelance web design services. Payment accepted in Bitcoin or PayPal, invoice provided.",
    "Gift boxes with discreet packaging, ships worldwide, pay with card or crypto.",
    "Online course on cryptocurrency basics, includes wallet setup tutorial.",
    "Private tutoring for maths, contact on Telegram or email, pay by UPI.",
    "Used laptop for sale, escrow service accepted for large purchases, returns allowed.",
    "Encrypted USB drives for secure backups, bulk discount for offices.",
    "Wholesale cotton fabric rolls, bulk orders welcome, bank transfer or card.",
    "WhatsApp group for local cricket fans, join through the link, free entry.",
    "Donation drive for an animal shelter. UPI or crypto accepted, receipt provided.",
]


def make_dataset(seed=7):
    r = random.Random(seed)
    data = []
    for _ in range(240):
        head = "%s %s %s" % (r.choice(SELL), r.choice(QTY), r.choice(SPECIES))
        extra = []
        if r.random() < 0.8:
            extra.append(r.choice(EVADE))
        if r.random() < 0.7:
            extra.append(r.choice(PAY))
        if r.random() < 0.4:
            extra.append("From %s to %s" % (r.choice(PLACES), r.choice(PLACES)))
        data.append((head + ". " + " ".join(e + "." for e in extra), 1))
    for _ in range(40):  # coded language: no species word at all
        data.append(("%s %s. %s. %s." % (r.choice(SELL), r.choice(CODED), r.choice(EVADE), r.choice(PAY)), 1))
    for _ in range(300):
        t = "%s %s. %s. %s." % (r.choice(SELL_B), r.choice(ITEMS), r.choice(COND), r.choice(SHIP_B))
        if r.random() < 0.2:
            t += " Located in %s." % r.choice(PLACES + ["Chennai", "Mumbai", "Pune"])
        data.append((t, 0))
    for _ in range(40):
        data.append((r.choice(BENIGN_CRYPTO), 0))
    return data


# ============================================================================
# 3. MODELS (written from scratch so there is nothing to pip install)
# ============================================================================
class NB:
    def fit(self, X, y):
        self.cnt = [Counter(), Counter()]
        docs = [0, 0]
        for f, l in zip(X, y):
            self.cnt[l].update(f)
            docs[l] += 1
        self.vocab = set(self.cnt[0]) | set(self.cnt[1])
        self.V = len(self.vocab)
        self.tot = [sum(c.values()) for c in self.cnt]
        self.prior = [math.log(max(d, 1) / float(max(sum(docs), 1))) for d in docs]

    def llr(self, w):
        return (math.log((self.cnt[1][w] + 1.0) / (self.tot[1] + self.V))
                - math.log((self.cnt[0][w] + 1.0) / (self.tot[0] + self.V)))

    def prob(self, f):
        s = self.prior[1] - self.prior[0]
        for w in f:
            if w in self.vocab:
                s += self.llr(w)
        s = max(-12.0, min(12.0, s * 0.6))  # temper so long texts do not saturate
        return 1.0 / (1.0 + math.exp(-s))


class LR:
    def fit(self, X, y, epochs=30, lr=0.8, l2=1e-4):
        self.w = defaultdict(float)
        self.b = 0.0
        idx = list(range(len(X)))
        rnd = random.Random(1)
        for _ in range(epochs):
            rnd.shuffle(idx)
            for i in idx:
                f = X[i]
                if not f:
                    continue
                v = 1.0 / math.sqrt(len(f))
                z = self.b + sum(self.w[k] for k in f) * v
                p = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
                err = p - y[i]
                for k in f:
                    self.w[k] -= lr * (err * v + l2 * self.w[k])
                self.b -= lr * err * 0.1

    def prob(self, f):
        if not f:
            return 0.5
        v = 1.0 / math.sqrt(len(f))
        z = self.b + sum(self.w.get(k, 0.0) for k in f) * v
        return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))


# ============================================================================
# 4. ENTITY / EVIDENCE EXTRACTION + GEO
# ============================================================================
PATTERNS = {
    "species": r"\b(?:ivory|tusks?|rhino(?:ceros)?\s+horns?(?:\s+powder)?|rhino\s+powder|pangolin(?:\s+scales?)?|"
               r"tiger\s+(?:skin|bone|bones|pelt|claws?|teeth|wine)|leopard\s+(?:pelt|skin|claws?)|bear\s+bile|"
               r"sea\s+turtle(?:\s+shells?)?|hawksbill(?:\s+shells?)?|totoaba|shahtoosh|elephant\s+(?:hide|skin|hair)|"
               r"star\s+tortoises?|red\s+sanders?|mongoose\s+hair|tokay(?:\s+gecko)?)\b",
    "payment": r"\b(?:bitcoin|btc|monero|xmr|crypto(?:currency)?|escrow|usdt|western\s+union|gift\s+cards?|"
               r"wire\s+transfer|wallet|hawala)\b",
    "evasion": r"\b(?:discreet(?:ly)?|discrete(?:ly)?|stealth(?:\s+packaging)?|no\s+questions(?:\s+asked)?|"
               r"undeclared|customs\s+(?:safe|proof|issues?)|encrypted(?:\s+chat)?|pgp|telegram|wickr|"
               r"signal\s+only|whatsapp\s+only|private\s+channels?|[a-z0-9]+\.onion|onion|quietly)\b",
    "quantity": r"\b(?:bulk|wholesale|\d+\s*(?:kg|kgs|pcs|pieces|tonnes?))\b",
}
PAT_RE = {k: re.compile(v, re.I) for k, v in PATTERNS.items()}

GAZ = {
    "mombasa": (-4.04, 39.67), "nairobi": (-1.29, 36.82), "kenya": (0.02, 37.9), "zanzibar": (-6.16, 39.2),
    "tanzania": (-6.37, 34.89), "maputo": (-25.97, 32.57), "mozambique": (-18.7, 35.5), "lagos": (6.52, 3.38),
    "nigeria": (9.08, 8.67), "dubai": (25.2, 55.27), "mumbai": (19.07, 72.88), "chennai": (13.08, 80.27),
    "pune": (18.52, 73.86), "delhi": (28.61, 77.2), "kolkata": (22.57, 88.36), "guwahati": (26.14, 91.74),
    "imphal": (24.82, 93.94), "moreh": (24.25, 94.3), "yangon": (16.87, 96.2), "myanmar": (21.9, 95.96),
    "dhaka": (23.81, 90.41), "kathmandu": (27.7, 85.3), "nepal": (28.4, 84.1),
    "laos": (19.86, 102.5), "vientiane": (17.97, 102.6), "bangkok": (13.75, 100.5),
    "thailand": (15.87, 100.99), "hanoi": (21.03, 105.85), "vietnam": (14.06, 108.28),
    "kuala lumpur": (3.14, 101.69), "singapore": (1.35, 103.82), "hong kong": (22.32, 114.17),
    "guangzhou": (23.13, 113.26), "china": (35.86, 104.2),
}


def find_entities(text):
    norm = normalize(text)  # same length as text, so offsets are valid for the original
    spans = []
    for cat, rx in PAT_RE.items():
        for m in rx.finditer(norm):
            spans.append((m.start(), m.end(), cat, text[m.start():m.end()]))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out, last = [], -1
    for s, e, c, t in spans:
        if s >= last:
            out.append({"start": s, "end": e, "cat": c, "text": t})
            last = e
    return out


def find_locations(text):
    low = text.lower()
    found = []
    for name, (lat, lon) in GAZ.items():
        m = re.search(r"\b" + re.escape(name) + r"\b", low)
        if m:
            found.append((m.start(), {"name": name.title(), "lat": lat, "lon": lon}))
    found.sort(key=lambda x: x[0])
    return [f[1] for f in found]


# ============================================================================
# 5. DATABASE
# ============================================================================
CATEGORIES = ["Home & Kitchen", "Fashion", "Electronics", "Handicrafts", "Antiques & Collectibles",
              "Books & Stationery", "Toys & Kids", "Health & Beauty", "Other"]


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with closing(db()) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS scans(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, text TEXT,
                score INTEGER, level TEXT, nb REAL, lr REAL, entities TEXT);
            CREATE TABLE IF NOT EXISTS feedback(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, text TEXT, label INTEGER);
            CREATE TABLE IF NOT EXISTS imported(
                id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, label INTEGER);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE COLLATE NOCASE,
                pw TEXT, salt TEXT, role TEXT, active INTEGER DEFAULT 1, created TEXT);
            CREATE TABLE IF NOT EXISTS listings(
                id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER, title TEXT, description TEXT,
                price REAL, category TEXT, status TEXT, score INTEGER, level TEXT, nb REAL, lr REAL,
                evidence REAL, entities TEXT, detail TEXT, reason TEXT, message TEXT,
                admin_action TEXT, created TEXT, decided_at TEXT);
            CREATE TABLE IF NOT EXISTS orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT, buyer_id INTEGER, listing_id INTEGER, created TEXT);
            CREATE TABLE IF NOT EXISTS reports(
                id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER, buyer_id INTEGER,
                reason TEXT, status TEXT DEFAULT 'open', created TEXT,
                admin_response TEXT DEFAULT '', action_taken TEXT DEFAULT '');
            """
        )
        report_cols = [r["name"] for r in c.execute("PRAGMA table_info(reports)")]
        if "admin_response" not in report_cols:
            c.execute("ALTER TABLE reports ADD COLUMN admin_response TEXT DEFAULT ''")
        if "action_taken" not in report_cols:
            c.execute("ALTER TABLE reports ADD COLUMN action_taken TEXT DEFAULT ''")
        cols = [r["name"] for r in c.execute("PRAGMA table_info(scans)")]
        if "source" not in cols:
            c.execute("ALTER TABLE scans ADD COLUMN source TEXT DEFAULT 'analyzer'")
        if "listing_id" not in cols:
            c.execute("ALTER TABLE scans ADD COLUMN listing_id INTEGER")
        c.commit()


def feedback_rows():
    with closing(db()) as c:
        return [(r["text"], r["label"]) for r in c.execute("SELECT text,label FROM feedback")]


def imported_rows():
    with closing(db()) as c:
        return [(r["text"], r["label"]) for r in c.execute("SELECT text,label FROM imported")]


def get_only():
    with closing(db()) as c:
        r = c.execute("SELECT value FROM meta WHERE key='only'").fetchone()
    return bool(r and r["value"] == "1")


POS = {"1", "illicit", "illegal", "yes", "true", "suspicious", "trafficking"}
NEG = {"0", "legit", "legal", "benign", "clean", "no", "false", "normal", "safe"}


def parse_csv_rows(raw):
    """Parse CSV text with columns text,label. Returns (rows, skipped)."""
    raw = raw.lstrip("﻿")
    rd = csv.DictReader(io.StringIO(raw))
    if not rd.fieldnames:
        raise ValueError("The file is empty.")
    names = {n.strip().lower(): n for n in rd.fieldnames if n}
    if "text" not in names or "label" not in names:
        raise ValueError("CSV needs two columns named: text,label")
    rows, skipped = [], 0
    for r in rd:
        t = (r.get(names["text"]) or "").strip()[:2000]
        l = (r.get(names["label"]) or "").strip().lower()
        if not t or (l not in POS and l not in NEG):
            skipped += 1
            continue
        rows.append((t, 1 if l in POS else 0))
    return rows, skipped


def store_rows(rows, only):
    seen, uniq = set(), []
    for t, l in rows:
        if t not in seen:
            seen.add(t)
            uniq.append((t, l))
    uniq = uniq[:20000]
    if len(uniq) < 10 or len(set(l for _, l in uniq)) < 2:
        raise ValueError("Need at least 10 valid rows containing both labels (1 = illicit, 0 = legit).")
    with closing(db()) as c:
        c.execute("DELETE FROM imported")
        c.executemany("INSERT INTO imported(text,label) VALUES(?,?)", uniq)
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('only',?)", ("1" if only else "0",))
        c.commit()
    return len(uniq)


DATASET_NAME_RE = re.compile(r"^dataset[\w .()\-]*\.csv$", re.I)


def list_dataset_files():
    out = []
    for fn in sorted(os.listdir(BASE)):
        if DATASET_NAME_RE.match(fn):
            try:
                with open(os.path.join(BASE, fn), encoding="utf-8-sig", errors="replace") as fh:
                    n = max(0, sum(1 for _ in fh) - 1)
            except OSError:
                continue
            out.append({"name": fn, "rows": n})
    return out


# ============================================================================
# 6. ENGINE
# ============================================================================
def level_of(score):
    if score >= 65:
        return "CRITICAL"
    if score >= 35:
        return "SUSPICIOUS"
    return "CLEAN"


class Engine:
    def __init__(self):
        self.lock = threading.Lock()
        self.feed_pool = make_dataset(seed=99)  # unseen listings for the live monitor
        self.train()

    def build_base(self):
        imp = imported_rows()
        data = list(imp) if (imp and get_only()) else make_dataset() + imp
        random.Random(42).shuffle(data)
        cut = int(len(data) * 0.8)
        self.train_base, self.test = data[:cut], data[cut:]
        self.n_imported, self.only = len(imp), bool(imp and get_only())

    def train(self):
        self.build_base()
        extra = feedback_rows()
        data = self.train_base + extra
        X = [feats(t) for t, _ in data]
        y = [l for _, l in data]
        nb, lr = NB(), LR()
        nb.fit(X, y)
        lr.fit(X, y, epochs=30 if len(X) < 1500 else 15)
        self.nb, self.lr = nb, lr
        self.n_train, self.n_extra = len(data), len(extra)
        self.metrics = self.evaluate()

    def evaluate(self):
        def kw(t):
            return 1.0 if PAT_RE["species"].search(t) else 0.0

        models = [
            ("Naive Bayes", lambda t: self.nb.prob(feats(t))),
            ("Logistic Regression", lambda t: self.lr.prob(feats(t))),
            ("Ensemble (NB + LR)", lambda t: (lambda f: (self.nb.prob(f) + self.lr.prob(f)) / 2)(feats(t))),
            ("Keyword baseline", kw),
        ]
        out = []
        for name, fn in models:
            tp = fp = fn_ = tn = 0
            t0 = time.perf_counter()
            for text, label in self.test:
                pred = 1 if fn(text) >= 0.5 else 0
                if pred and label:
                    tp += 1
                elif pred and not label:
                    fp += 1
                elif not pred and label:
                    fn_ += 1
                else:
                    tn += 1
            ms = (time.perf_counter() - t0) * 1000.0 / max(1, len(self.test))
            n = float(max(1, tp + fp + fn_ + tn))
            prec = tp / float(tp + fp) if tp + fp else 0.0
            rec = tp / float(tp + fn_) if tp + fn_ else 0.0
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
            out.append({"model": name, "accuracy": round(100 * (tp + tn) / n, 1),
                        "precision": round(100 * prec, 1), "recall": round(100 * rec, 1),
                        "f1": round(100 * f1, 1), "latency_ms": round(ms, 3),
                        "fp": fp, "fn": fn_, "tp": tp, "tn": tn})
        return out

    def score_parts(self, text):
        f = feats(text)
        nbp, lrp = self.nb.prob(f), self.lr.prob(f)
        ml = (nbp + lrp) / 2.0
        ents = find_entities(text)
        cats = set(e["cat"] for e in ents)
        ev = 0.4 * ("species" in cats) + 0.25 * ("payment" in cats) + 0.25 * ("evasion" in cats) + 0.1 * ("quantity" in cats)
        score = int(round(100 * (0.75 * ml + 0.25 * ev)))
        return {"f": f, "nb": nbp, "lr": lrp, "ml": ml, "ents": ents, "ev": ev, "score": score}

    def analyze(self, text, save=True, source="analyzer"):
        t0 = time.perf_counter()
        p = self.score_parts(text)
        f, ents, score = p["f"], p["ents"], p["score"]
        level = level_of(score)
        norm = normalize(text)

        heat = []
        for m in re.finditer(r"\S+", text):
            clean = re.sub(r"[^a-z0-9]", "", norm[m.start():m.end()])
            w = 0.0
            if clean and clean not in STOP and clean in self.nb.vocab:
                w = self.nb.llr(clean)
            cat = ""
            for e in ents:
                if m.start() < e["end"] and m.end() > e["start"]:
                    cat = e["cat"]
                    break
            heat.append({"t": m.group(0), "w": round(w, 2), "cat": cat})

        drivers = []
        for k in f:
            if k in self.nb.vocab:
                drivers.append({"token": k.replace("_", " "), "weight": round(self.nb.llr(k), 2)})
        drivers.sort(key=lambda d: -abs(d["weight"]))
        drivers = drivers[:10]

        res = {
            "text": text, "score": score, "level": level,
            "nb": round(p["nb"] * 100, 1), "lr": round(p["lr"] * 100, 1), "evidence": round(p["ev"] * 100, 1),
            "entities": ents, "heat": heat, "drivers": drivers, "locations": find_locations(text),
            "ms": round((time.perf_counter() - t0) * 1000, 2), "ts": now(),
        }
        if save:
            res["id"] = self.save_scan(res, source, None)
        return res

    def save_scan(self, res, source, listing_id):
        with closing(db()) as c:
            cur = c.execute(
                "INSERT INTO scans(ts,text,score,level,nb,lr,entities,source,listing_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (res["ts"], res["text"], res["score"], res["level"], res["nb"], res["lr"],
                 json.dumps(res["entities"]), source, listing_id))
            c.commit()
            return cur.lastrowid

    def state(self):
        with closing(db()) as c:
            hist = [dict(r) for r in c.execute(
                "SELECT id,ts,text,score,level,source FROM scans ORDER BY id DESC LIMIT 15")]
            total = c.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
            flagged = c.execute("SELECT COUNT(*) FROM scans WHERE level!='CLEAN'").fetchone()[0]
            critical = c.execute("SELECT COUNT(*) FROM scans WHERE level='CRITICAL'").fetchone()[0]
        return {
            "metrics": self.metrics, "n_train": self.n_train, "n_test": len(self.test),
            "n_extra": self.n_extra, "n_imported": self.n_imported, "only": self.only, "samples": SAMPLES,
            "history": hist, "stats": {"total": total, "flagged": flagged, "critical": critical},
        }

    # ---------------- trap-case testing ----------------
    def trap_eval(self):
        rows, ok_h, ok_k = [], 0, 0
        for smp in SAMPLES:
            p = self.score_parts(smp["text"])
            want = 1 if smp["expect"] == "illicit" else 0
            hy = 1 if p["score"] >= 35 else 0
            kw = 1 if PAT_RE["species"].search(smp["text"]) else 0
            ok_h += hy == want
            ok_k += kw == want
            rows.append({"id": smp["id"], "title": smp["title"], "expect": smp["expect"], "hard": smp["hard"],
                         "score": p["score"], "level": level_of(p["score"]),
                         "hybrid_ok": hy == want, "keyword_ok": kw == want})
        n = len(SAMPLES)
        return {"rows": rows, "n": n, "hybrid_correct": ok_h, "keyword_correct": ok_k}

    # ---------------- coded-language evaluation ----------------
    def coded_eval(self):
        data = build_coded_eval()
        tiers = {}
        misses = []
        for tier, text, label in data:
            kw = bool(PAT_RE["species"].search(text))  # raw keyword search, no normalization
            p = self.score_parts(text)
            fr = feats_raw(text)
            mlraw = (self.nb.prob(fr) + self.lr.prob(fr)) / 2 >= 0.5  # ML that cannot undo leetspeak
            ml = p["ml"] >= 0.5
            hy = p["score"] >= 35
            t = tiers.setdefault(tier, {"n": 0, "keyword": 0, "mlraw": 0, "ml": 0, "hybrid": 0})
            t["n"] += 1
            t["keyword"] += kw
            t["mlraw"] += mlraw
            t["ml"] += ml
            t["hybrid"] += hy
            if label == 1 and not hy and len(misses) < 14:
                misses.append({"tier": tier, "text": text, "score": p["score"]})
        info = [
            ("A", "Coded term + strong hidden-payment / secret-shipping context", 1),
            ("B", "Coded term + only weak context ('serious buyers only')", 1),
            ("C", "Obfuscated spelling (1v0ry, r41n0 h0rn) + some context", 1),
            ("D", "Innocent listings that use similar words (false-alarm test)", 0),
        ]
        out = []
        for key, desc, lab in info:
            t = tiers.get(key)
            if not t:
                continue
            n = float(t["n"])
            out.append({"tier": key, "desc": desc, "label": lab, "n": t["n"],
                        "keyword": round(100 * t["keyword"] / n, 1),
                        "mlraw": round(100 * t["mlraw"] / n, 1),
                        "ml": round(100 * t["ml"] / n, 1),
                        "hybrid": round(100 * t["hybrid"] / n, 1)})
        return {"tiers": out, "misses": misses, "terms": CODED_EVAL_TERMS}


# ----------------------------------------------------------------------------
# held-out coded-language test set. The coded words below never appear in the
# built-in data or in the bundled dataset_*.csv files, so this tests generalisation.
# ----------------------------------------------------------------------------
CODED_EVAL_TERMS = [
    "hard white rods", "old mountain gems", "jungle gold dust", "sea gems", "scaly bits", "spotted rugs",
    "big cat souvenirs", "heirloom white pieces", "night tonic roots", "ancient hard bits",
    "grey hide pieces", "forest hair brushes",
]
EVAL_OPEN = ["Selling", "For sale:", "Available:", "Listing:", "Looking to sell"]  # neutral openers
WEAK_CONTEXT = ["Serious buyers only", "Limited stock, rare find", "Price negotiable for trusted buyers",
                "No time wasters", "Old family collection, sold as is", "Reply with your offer"]
EVAL_NEG_ITEMS = [
    "white gold ring with 18k hallmark", "mountain bike spare parts", "jungle gold herbal tea box",
    "ayurvedic tonic with licence number", "sea glass gems resin earrings", "scaly pattern phone cover",
    "spotted cotton rug", "big cat print cushion covers", "heirloom brass pieces",
    "ancient history book set", "grey leather wallet", "hard white plastic storage rods",
]


def leet(word):
    return "".join({"i": "1", "o": "0", "e": "3", "a": "4"}.get(ch, ch) for ch in word)


def build_coded_eval(seed=123):
    r = random.Random(seed)
    data = []
    # Neutral openers are used so the models cannot recognise the TEMPLATE ("WTS:", "raw", "bulk lot of"),
    # which appears only in the illicit training examples. Only the coded term and the context differ.
    for _ in range(60):  # tier A: coded term + strong context
        data.append(("A", "%s %s. %s. %s." % (r.choice(EVAL_OPEN), r.choice(CODED_EVAL_TERMS),
                                               r.choice(EVADE), r.choice(PAY)), 1))
    for _ in range(40):  # tier B: coded term + weak context
        data.append(("B", "%s %s. %s." % (r.choice(EVAL_OPEN), r.choice(CODED_EVAL_TERMS),
                                           r.choice(WEAK_CONTEXT)), 1))
    base = ["ivory carvings", "rhino horn", "pangolin scales", "tiger bone", "elephant tusks", "leopard pelt"]
    for _ in range(40):  # tier C: obfuscated species word + weak context (isolates the normalization step)
        data.append(("C", "%s %s. %s." % (r.choice(EVAL_OPEN), leet(r.choice(base)), r.choice(WEAK_CONTEXT)), 1))
    for _ in range(60):  # tier D
        data.append(("D", "%s %s. %s. %s." % (r.choice(SELL_B), r.choice(EVAL_NEG_ITEMS), r.choice(COND),
                                               r.choice(SHIP_B)), 0))
    return data


# ============================================================================
# 7. TEST SAMPLES  (expect = what a correct system should say)
# ============================================================================
def S(i, title, expect, text, hard=False):
    return {"id": i, "title": title, "expect": expect, "text": text, "hard": hard}


SAMPLES = [
    S(1, "Ivory + stealth + Monero", "illicit", "WTS: 5kg raw ivory carvings from Mombasa stock. Stealth packaging, ships undeclared via Dubai to Hong Kong. Monero or BTC escrow only."),
    S(2, "Pangolin bulk lot", "illicit", "Pangolin scales, bulk lot, 10kg ready. Contact on Telegram only, no questions asked. Crypto payment, wallet provided after PGP verification."),
    S(3, "Tiger bone + leopard pelt", "illicit", "Tiger bone wine and leopard pelt available. Private channel only, ships discreetly from Hanoi to Guangzhou. Bitcoin accepted."),
    S(4, "Rhino powder 'medicine'", "illicit", "Rhino horn powder traditional medicine 200g jars, vendor in Laos, crypto escrow, message me on Wickr for photos."),
    S(5, "Turtle shell wholesale", "illicit", "Hawksbill shell combs and sea turtle shell jewelry, wholesale, customs safe packaging, pay Western Union or USDT."),
    S(6, "Coded language (no species word)", "illicit", "Selling white gold carvings and mountain medicine powder from old stock. Message via encrypted chat. Pay with gift cards or crypto."),
    S(7, "Ceramic mugs (normal)", "legit", "Handmade ceramic coffee mugs set of 4, dishwasher safe. Free shipping over $50, pay by card or PayPal."),
    S(8, "Ivory-coloured dress (trap)", "legit", "Ivory white wedding dress size 8, worn once, dry cleaned. Pickup in Chennai or tracked shipping."),
    S(9, "Tiger Balm (trap)", "legit", "Tiger Balm muscle rub, 3 jars, unopened. Accepting UPI or bank transfer, returns within 30 days."),
    S(10, "Web design in Bitcoin (trap)", "legit", "Freelance web design services for small businesses. Payment accepted in Bitcoin or PayPal, invoice provided."),
    S(11, "Star tortoise babies (India)", "illicit", "Indian star tortoise babies, 2 inch, healthy. Ships discreetly pan India, pay UPI or crypto, DM on Telegram only."),
    S(12, "Red sanders logs", "illicit", "Red sanders logs, 1 tonne ready, from Chennai to Dubai. No questions asked, customs safe, payment in USDT."),
    S(13, "Mongoose hair brushes", "illicit", "Pure mongoose hair brush sets, wholesale 100 pcs. Ships quietly, private channel only, crypto accepted."),
    S(14, "Shahtoosh shawl", "illicit", "Genuine shahtoosh ring shawl, vintage, from Kathmandu stock. Bitcoin escrow, stealth packaging."),
    S(15, "Leopard claw pendant", "illicit", "Real leopard claws, 10 pcs, set in silver. Message on Wickr, undeclared shipping, XMR only."),
    S(16, "Obfuscated spelling", "illicit", "Sell1ng r4w 1v0ry c4rv1ngs, 5kg. Encrypted chat only, Monero accepted, ships stealth."),
    S(17, "Coded + strong context", "illicit", "Vendor stock: hard white rods and old mountain gems. Contact on Telegram only. BTC or XMR via escrow."),
    S(18, "Coded, weak context (hard)", "illicit", "Heirloom white pieces from an old collection. Serious buyers only, message me privately.", True),
    S(19, "Sofa set (normal)", "legit", "Ivory coloured three seater sofa set, fabric, 2 years old. Pickup in Pune, pay by UPI or cash."),
    S(20, "Plush toys for kids", "legit", "Soft rhino and elephant plush toys, pack of 3, cotton filling, free delivery. Pay by card."),
    S(21, "Mango wood elephant", "legit", "Hand carved elephant figurine in mango wood, certified legal timber with invoice. Returns accepted."),
    S(22, "Tiger print bedsheet", "legit", "Tiger print cotton bedsheet, double size, brand new. Pay by card, free shipping."),
    S(23, "Stamp collectors group", "legit", "Telegram channel for stamp collectors in India. Free to join, share your collection and trade stamps."),
    S(24, "Tortoise-shell sunglasses (trap)", "legit", "Tortoise shell pattern sunglasses, acetate frames, UV protection. Accepting UPI, returns within 7 days."),
    S(25, "Shelter donation drive (trap)", "legit", "Donation drive for a pangolin and wildlife rescue centre. Donate by UPI or crypto, tax receipt provided.", True),
    S(26, "White gold ring (trap)", "legit", "White gold ring, 18k hallmark with certificate. Pay by card, free insured shipping.", True),
]


# ============================================================================
# 8. AUTH + MARKETPLACE LOGIC
# ============================================================================
SESSIONS = {}
SESS_LOCK = threading.Lock()


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 60000).hex()


def create_user(username, pw, role):
    salt = secrets.token_hex(16)
    with closing(db()) as c:
        cur = c.execute("INSERT INTO users(username,pw,salt,role,active,created) VALUES(?,?,?,?,1,?)",
                        (username, hash_pw(pw, salt), salt, role, now()))
        c.commit()
        return cur.lastrowid


def check_login(username, pw):
    with closing(db()) as c:
        u = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not u:
        hash_pw(pw, "00" * 16)  # keep timing similar
        return None
    if hmac.compare_digest(hash_pw(pw, u["salt"]), u["pw"]):
        return u
    return None


def user_public(u):
    return {"id": u["id"], "username": u["username"], "role": u["role"], "active": bool(u["active"])}


def seller_risk(uid):
    """Seller risk 0-100 built from the history of the seller's listings."""
    with closing(db()) as c:
        rows = c.execute("SELECT level,admin_action FROM listings WHERE seller_id=?", (uid,)).fetchall()
        upheld = c.execute(
            "SELECT COUNT(*) FROM reports r JOIN listings l ON l.id=r.listing_id "
            "WHERE l.seller_id=? AND r.status='upheld'", (uid,)).fetchone()[0]
    pts, flagged = 0.0, 0
    for r in rows:
        if r["admin_action"] == "approved":
            continue  # an admin cleared this listing
        if r["level"] == "CRITICAL":
            pts += 30
            flagged += 1
        elif r["level"] == "SUSPICIOUS":
            pts += 12
            flagged += 1
        if r["admin_action"] == "rejected":
            pts += 20
    pts += 15 * upheld
    if flagged >= 2:
        pts *= 1.25  # repeat-offender multiplier
    score = int(min(100, round(pts)))
    label = "HIGH" if score >= 60 else ("WATCH" if score >= 25 else "GOOD")
    return score, label


def decide(score, srisk):
    """Return (status, escalated). Higher seller risk lowers the thresholds."""
    def outcome(pub, blk):
        return "blocked" if score >= blk else ("review" if score >= pub else "published")

    default = outcome(35, 65)
    if srisk >= 60:
        status = outcome(15, 50)
    elif srisk >= 25:
        status = outcome(25, 60)
    else:
        status = default
    return status, status != default


CAT_NAMES = {"species": "protected-wildlife terms", "payment": "hard-to-trace payment wording",
             "evasion": "concealed shipping or contact wording", "quantity": "bulk-quantity wording"}


def apply_listing_policy(text, res):
    """Combine the model score with explicit wildlife-trade policy rules.

    This is a safety gate for marketplace publishing, not a legal determination.
    Educational/replica/photography contexts are treated cautiously to reduce
    obvious false positives, while explicit sale of protected animal parts is
    escalated reliably even when the learned model score is low.
    """
    norm = normalize(text).lower()
    sale_context = bool(re.search(r"\b(sell(?:ing)?|for sale|available now|wts|vendor stock|offering|buy|price|order|purchase|raw|carved)\b", norm))
    explicit_critical = bool(re.search(
        r"\b(raw ivory|ivory carvings?|elephant tusks?|rhino(?:ceros)? horns?(?: powder)?|rhino horn powder|"
        r"pangolin scales?|tiger skins?|tiger bones?|leopard pelts?|leopard skins?|bear bile|"
        r"sea turtle shells?|hawksbill shells?|totoaba bladder|shahtoosh|elephant hide|elephant skin)\b", norm))
    contextual_safe = bool(re.search(
        r"\b(photograph(?:y)?|poster|documentary|educational|conservation|toy|plush|print|"
        r"faux|synthetic|imitation|replica|resin|pattern|colour|color|book about|history of)\b", norm))
    species = bool(PAT_RE["species"].search(norm))
    evasive_combo = bool(PAT_RE["evasion"].search(norm) and PAT_RE["payment"].search(norm))
    if explicit_critical and (sale_context or not contextual_safe):
        res["score"] = max(int(res["score"]), 75)
        res["level"] = "CRITICAL"
        res["policy_reason"] = "Explicit protected-wildlife product wording detected; automatically blocked."
    elif (species and sale_context and not contextual_safe) or evasive_combo:
        res["score"] = max(int(res["score"]), 40)
        res["level"] = "SUSPICIOUS"
        res["policy_reason"] = "Wildlife or high-risk transaction wording requires human review."
    else:
        res["level"] = level_of(int(res["score"]))
        res["policy_reason"] = ""
    return res


def post_listing(uid, title, desc, price, category):
    srisk, _ = seller_risk(uid)
    text = title + ". " + desc
    res = apply_listing_policy(text, ENGINE.analyze(text, save=False))
    status, escalated = decide(res["score"], srisk)
    if res["level"] == "CRITICAL":
        status = "blocked"
    elif res["level"] == "SUSPICIOUS":
        status = "review"
    cats = sorted({e["cat"] for e in res["entities"]})
    if status == "published":
        msg = "Your listing is live."
    elif status == "review":
        msg = "Held for review. An admin will check it before buyers can see it."
    else:
        msg = "Not published. This listing looks like it breaks our wildlife-trade policy."
    if status != "published" and cats:
        msg += " Flagged for: " + ", ".join(CAT_NAMES[c] for c in cats) + "."
    if escalated:
        msg += " Extra checks were applied because of your account history."
    reason = "AI score %d (%s)" % (res["score"], res["level"])
    if res.get("policy_reason"):
        reason += " - " + res["policy_reason"]
    if escalated:
        reason += " - stricter limits, seller risk %d" % srisk
    detail = json.dumps({"heat": res["heat"], "drivers": res["drivers"], "locations": res["locations"]})
    with closing(db()) as c:
        cur = c.execute(
            "INSERT INTO listings(seller_id,title,description,price,category,status,score,level,nb,lr,evidence,"
            "entities,detail,reason,message,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, title, desc, price, category, status, res["score"], res["level"], res["nb"], res["lr"],
             res["evidence"], json.dumps(res["entities"]), detail, reason, msg, now()))
        c.commit()
        lid = cur.lastrowid
    ENGINE.save_scan(res, "listing", lid)
    return {"id": lid, "status": status, "message": msg}


def admin_decide(listing_id, action):
    with closing(db()) as c:
        l = c.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        if not l:
            raise ValueError("Listing not found.")
        if l["status"] == "removed":
            raise ValueError("The seller already removed this listing.")
        if action == "approve":
            if l["level"] == "CRITICAL":
                raise ValueError("Critical listings are auto-blocked and cannot be approved from this queue.")
            status, label, rep = "published", 0, "dismissed"
        elif action == "reject":
            status, label, rep = "rejected", 1, "upheld"
        else:
            raise ValueError("Unknown action.")
        c.execute("UPDATE listings SET status=?, admin_action=?, decided_at=? WHERE id=?",
                  (status, "approved" if action == "approve" else "rejected", now(), listing_id))
        response = ("Admin reviewed this listing and approved it as acceptable for the marketplace." if action == "approve"
                    else "Admin reviewed this listing and rejected it for a policy violation.")
        action_text = "Listing approved and published" if action == "approve" else "Listing rejected and kept unpublished"
        c.execute("UPDATE reports SET status=?, admin_response=?, action_taken=? WHERE listing_id=? AND status='open'",
                  (rep, response, action_text, listing_id))
        c.execute("INSERT INTO feedback(ts,text,label) VALUES(?,?,?)",
                  (now(), l["title"] + ". " + l["description"], label))
        c.commit()
    with ENGINE.lock:
        ENGINE.train()  # human-in-the-loop: the decision becomes training data


def listing_card(r):
    return {"id": r["id"], "title": r["title"], "description": r["description"], "price": r["price"],
            "category": r["category"], "seller": r["seller"], "created": r["created"]}


DEMO_USERS = [("admin", "admin123", "admin"), ("seller1", "seller123", "seller"),
              ("craft_house", "seller123", "seller"), ("rare_finds", "seller123", "seller"),
              ("green_grove", "seller123", "seller"), ("artisan_lane", "seller123", "seller"),
              ("buyer1", "buyer123", "buyer"), ("buyer2", "buyer123", "buyer")]
DEMO_LISTINGS = [
    ("seller1", "Handmade ceramic coffee mugs (set of 4)", "Dishwasher safe, glossy glaze. Free shipping over Rs 999, pay by card or UPI.", 899, "Home & Kitchen"),
    ("seller1", "Ivory white wedding dress, size 8", "Worn once and dry cleaned. Pickup in Chennai or tracked shipping.", 6500, "Fashion"),
    ("seller1", "Tiger Balm muscle rub (3 jars)", "Unopened jars. Returns accepted within 30 days, pay by UPI.", 450, "Health & Beauty"),
    ("seller1", "Bluetooth speaker, 20W", "Brand new with warranty, 12 hour battery. Cash on delivery available.", 2299, "Electronics"),
    ("seller1", "Python programming book", "Beginner friendly, excellent condition. Pickup in Vellore.", 350, "Books & Stationery"),
    ("seller1", "Rhino and elephant plush toys (pack of 3)", "Soft cotton filling, safe for toddlers. Free delivery, pay by card.", 799, "Toys & Kids"),
    ("craft_house", "Hand carved elephant figurine, mango wood", "Certified legal timber with invoice. Returns accepted within 7 days.", 1450, "Handicrafts"),
    ("craft_house", "Brass pooja lamp", "Traditional design, polished. Ships in 2 days with tracking.", 1190, "Home & Kitchen"),
    ("craft_house", "Handloom cotton kurta", "Natural dyes, sizes M to XXL. Pay by card or UPI, returns accepted.", 1599, "Fashion"),
    ("craft_house", "Antique brass bell from old temple town", "Heirloom piece with seller certificate. Pickup or tracked courier.", 2400, "Antiques & Collectibles"),
    ("rare_finds", "Brass pooja lamp, small", "Polished brass lamp. Ships in 2 days with tracking, pay by card.", 650, "Home & Kitchen"),
    ("rare_finds", "Traditional rhino horn powder 200g", "Old family stock for traditional medicine. Ships discreetly, crypto escrow, contact on Telegram only.", 9000, "Health & Beauty"),
    ("rare_finds", "Pangolin scales dried, bulk lot", "10kg ready. No questions asked, Monero only, customs safe packaging.", 15000, "Other"),
    ("seller1", "Reusable stainless-steel water bottle", "750ml insulated bottle, leakproof lid, free delivery and UPI payment.", 699, "Home & Kitchen"),
    ("seller1", "Wireless keyboard and mouse combo", "Quiet keys, USB receiver, 1-year warranty and standard invoice.", 1499, "Electronics"),
    ("seller1", "Beginner watercolor art kit", "12 colors, brushes and recycled-paper sketchbook for students.", 499, "Books & Stationery"),
    ("seller1", "Cotton tote bag with leaf print", "Reusable hand-printed bag made by a local craft group.", 299, "Fashion"),
    ("craft_house", "Handwoven palm-leaf storage basket", "Natural fibers from a legal source; artisan cooperative receipt included.", 850, "Handicrafts"),
    ("craft_house", "Recycled glass table vase", "Hand-finished decorative vase, safely packed for shipping.", 720, "Home & Kitchen"),
    ("craft_house", "Wooden elephant keepsake, certified plantation wood", "Small decorative keepsake with source certificate and seller invoice.", 990, "Handicrafts"),
    ("rare_finds", "Rare carved horn pieces, discreet shipping", "Traditional horn pieces available in bulk; private channel and crypto payment only.", 12000, "Antiques & Collectibles"),
    ("rare_finds", "Wildlife scale powder, no questions asked", "Bulk stock, encrypted contact, undeclared shipping and wallet payment.", 9800, "Other"),
    ("rare_finds", "Leopard pelt vintage collector item", "Rare pelt, private sale, ships quietly with no paperwork.", 22000, "Antiques & Collectibles"),
    ("green_grove", "Indoor snake plant in ceramic pot", "Low-maintenance indoor plant with care instructions and local delivery.", 599, "Home & Garden"),
    ("green_grove", "Organic cotton kitchen towel set", "Four reusable towels with natural dyes and washable fabric.", 399, "Home & Kitchen"),
    ("artisan_lane", "Handmade clay tea cup set", "Four glazed cups made by a local pottery studio; care card included.", 780, "Handicrafts"),
    ("artisan_lane", "Reclaimed wood photo frame", "Hand-finished frame made from reclaimed wood with source details.", 520, "Home & Decor"),
]


def seed_demo():
    with closing(db()) as c:
        n = c.execute("SELECT COUNT(*) FROM users WHERE role!='admin'").fetchone()[0]
    if n:
        return
    ids = {}
    for name, pw, role in DEMO_USERS:
        with closing(db()) as c:
            ex = c.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
        ids[name] = ex["id"] if ex else create_user(name, pw, role)
    for seller, title, desc, price, cat in DEMO_LISTINGS:
        post_listing(ids[seller], title, desc, price, cat)


def reset_market():
    with closing(db()) as c:
        c.execute("DELETE FROM listings")
        c.execute("DELETE FROM orders")
        c.execute("DELETE FROM reports")
        c.execute("DELETE FROM scans WHERE source='listing'")
        c.execute("DELETE FROM users WHERE role!='admin'")
        c.commit()
    SESSIONS.clear()
    ids = {}
    with closing(db()) as c:
        a = c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone()
    for name, pw, role in DEMO_USERS:
        if name == "admin" and a:
            continue
        ids[name] = create_user(name, pw, role)
    for seller, title, desc, price, cat in DEMO_LISTINGS:
        post_listing(ids[seller], title, desc, price, cat)


def market_stats():
    with closing(db()) as c:
        st = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM listings GROUP BY status")}
        roles = {r["role"]: r["n"] for r in c.execute("SELECT role, COUNT(*) n FROM users GROUP BY role")}
        orders = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        reports = c.execute("SELECT COUNT(*) FROM reports WHERE status='open'").fetchone()[0]
        queue = c.execute(
            "SELECT COUNT(*) FROM listings l WHERE l.status='review' OR "
            "(l.status='published' AND EXISTS(SELECT 1 FROM reports r WHERE r.listing_id=l.id AND r.status='open'))"
        ).fetchone()[0]
    return {"published": st.get("published", 0), "review": st.get("review", 0), "blocked": st.get("blocked", 0),
            "rejected": st.get("rejected", 0), "sellers": roles.get("seller", 0), "buyers": roles.get("buyer", 0),
            "orders": orders, "open_reports": reports, "queue": queue}


def admin_listing_rows(where="", args=()):
    with closing(db()) as c:
        rows = c.execute(
            "SELECT l.*, u.username AS seller, "
            "(SELECT COUNT(*) FROM reports r WHERE r.listing_id=l.id AND r.status='open') AS open_reports "
            "FROM listings l JOIN users u ON u.id=l.seller_id " + where + " ORDER BY l.id DESC", args).fetchall()
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
            return self._page("admin.html")
        if p == "/api/me":
            usr = self._user()
            return self._send(200, {"user": user_public(usr) if usr else None, "categories": CATEGORIES})

        # ---- public marketplace ----
        if p == "/api/listings":
            term = (q.get("q", [""])[0] or "").strip().lower()
            cat = q.get("category", [""])[0]
            sql = ("SELECT l.*, u.username AS seller FROM listings l JOIN users u ON u.id=l.seller_id "
                   "WHERE l.status='published' AND u.active=1")
            args = []
            if term:
                sql += " AND (LOWER(l.title) LIKE ? OR LOWER(l.description) LIKE ?)"
                args += ["%" + term + "%"] * 2
            if cat:
                sql += " AND l.category=?"
                args.append(cat)
            sql += " ORDER BY l.id DESC LIMIT 200"
            with closing(db()) as c:
                rows = c.execute(sql, args).fetchall()
            return self._send(200, [listing_card(r) for r in rows])

        # ---- buyer ----
        if p == "/api/orders":
            usr = self._need("buyer")
            if not usr:
                return
            with closing(db()) as c:
                rows = c.execute(
                    "SELECT o.id, o.created, l.title, l.price, u.username AS seller FROM orders o "
                    "JOIN listings l ON l.id=o.listing_id JOIN users u ON u.id=l.seller_id "
                    "WHERE o.buyer_id=? ORDER BY o.id DESC", (usr["id"],)).fetchall()
            return self._send(200, [dict(r) for r in rows])

        if p == "/api/buyer/reports":
            usr = self._need("buyer")
            if not usr:
                return
            with closing(db()) as c:
                rows = c.execute(
                    "SELECT r.id, r.reason, r.status, r.created, r.admin_response, r.action_taken, "
                    "l.id AS listing_id, l.title, l.category, l.status AS listing_status "
                    "FROM reports r JOIN listings l ON l.id=r.listing_id "
                    "WHERE r.buyer_id=? ORDER BY r.id DESC", (usr["id"],)).fetchall()
            return self._send(200, [dict(r) for r in rows])

        # ---- seller ----
        if p == "/api/seller/listings":
            usr = self._need("seller")
            if not usr:
                return
            with closing(db()) as c:
                rows = c.execute("SELECT id,title,price,category,status,message,created FROM listings "
                                 "WHERE seller_id=? AND status!='removed' ORDER BY id DESC", (usr["id"],)).fetchall()
            sr, lab = seller_risk(usr["id"])
            return self._send(200, {"listings": [dict(r) for r in rows], "standing": lab,
                                    "active": bool(usr["active"])})

        # ---- everything below is admin only ----
        if not p.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._need("admin"):
            return
        if p == "/api/state":
            return self._send(200, ENGINE.state())
        if p == "/api/analytics":
            return self._send(200, analytics())
        if p == "/api/feed":
            text, _label = random.choice(ENGINE.feed_pool)
            res = ENGINE.analyze(text, save=False)
            res["heat"] = []
            return self._send(200, res)
        if p == "/api/export":
            return self._send(200, export_csv(), "text/csv",
                              {"Content-Disposition": "attachment; filename=ivoryblock_cases.csv"})
        if p == "/api/report":
            sid = int(q.get("id", ["0"])[0])
            md = report_md(sid)
            if md is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, md, "text/markdown",
                              {"Content-Disposition": "attachment; filename=ivoryblock_case_%d.md" % sid})
        if p == "/api/admin/overview":
            return self._send(200, market_stats())
        if p == "/api/admin/queue":
            rows = admin_listing_rows(
                "WHERE l.status='review' OR (l.status='published' AND "
                "EXISTS(SELECT 1 FROM reports r WHERE r.listing_id=l.id AND r.status='open'))")
            return self._send(200, rows)
        if p == "/api/admin/listings":
            st = q.get("status", [""])[0]
            rows = admin_listing_rows("WHERE l.status=?", (st,)) if st else admin_listing_rows()
            return self._send(200, rows)
        if p == "/api/admin/listing":
            lid = int(q.get("id", ["0"])[0])
            rows = admin_listing_rows("WHERE l.id=?", (lid,))
            if not rows:
                return self._send(404, {"error": "not found"})
            with closing(db()) as c:
                r = c.execute("SELECT detail, entities FROM listings WHERE id=?", (lid,)).fetchone()
                reps = c.execute("SELECT r.id, r.reason, r.status, r.created, r.admin_response, r.action_taken, u.username FROM reports r "
                                 "JOIN users u ON u.id=r.buyer_id WHERE r.listing_id=? ORDER BY r.id DESC",
                                 (lid,)).fetchall()
            out = rows[0]
            out["detail"] = json.loads(r["detail"] or "{}")
            out["entities"] = json.loads(r["entities"] or "[]")
            out["reports"] = [dict(x) for x in reps]
            return self._send(200, out)
        if p == "/api/admin/sellers":
            return self._send(200, sellers_overview())
        if p == "/api/admin/trap_eval":
            return self._send(200, ENGINE.trap_eval())
        if p == "/api/admin/coded_eval":
            return self._send(200, ENGINE.coded_eval())
        if p == "/api/admin/datasets":
            return self._send(200, list_dataset_files())
        self._send(404, {"error": "not found"})

    # ---- POST ----
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            self._post(urlparse(self.path).path, data)
        except ValueError as ve:
            self._send(400, {"error": str(ve)})
        except Exception as ex:  # keep the server alive
            self._send(500, {"error": str(ex)})

    def _post(self, p, data):
        # ---- auth ----
        if p == "/api/register":
            name = str(data.get("username", "")).strip()
            pw = str(data.get("password", ""))
            role = data.get("role")
            if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", name):
                raise ValueError("Username must be 3-20 letters, numbers or underscores.")
            if len(pw) < 6:
                raise ValueError("Password must be at least 6 characters.")
            if role not in ("seller", "buyer"):
                raise ValueError("Choose seller or buyer.")
            try:
                uid = create_user(name, pw, role)
            except sqlite3.IntegrityError:
                raise ValueError("That username is already taken.")
            with closing(db()) as c:
                usr = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            return self._send(200, {"user": user_public(usr)}, extra=self._login_cookie(uid))
        if p == "/api/login":
            usr = check_login(str(data.get("username", "")).strip(), str(data.get("password", "")))
            if not usr:
                return self._send(401, {"error": "Wrong username or password."})
            return self._send(200, {"user": user_public(usr)}, extra=self._login_cookie(usr["id"]))
        if p == "/api/logout":
            tok = self._token()
            with SESS_LOCK:
                SESSIONS.pop(tok, None)
            return self._send(200, {"ok": True}, extra={"Set-Cookie": "ib_session=; Path=/; Max-Age=0"})

        # ---- seller ----
        if p == "/api/seller/post":
            usr = self._need("seller")
            if not usr:
                return
            if not usr["active"]:
                return self._send(403, {"error": "Your seller account is suspended. Contact the admin."})
            title = str(data.get("title", "")).strip()
            desc = str(data.get("description", "")).strip()
            cat = data.get("category")
            try:
                price = float(data.get("price"))
            except (TypeError, ValueError):
                raise ValueError("Enter a valid price.")
            if not (3 <= len(title) <= 120):
                raise ValueError("Title must be 3-120 characters.")
            if not (10 <= len(desc) <= 2000):
                raise ValueError("Description must be 10-2000 characters.")
            if not (0 <= price <= 10000000):
                raise ValueError("Price must be between 0 and 10,000,000.")
            if cat not in CATEGORIES:
                raise ValueError("Pick a category.")
            return self._send(200, post_listing(usr["id"], title, desc, price, cat))
        if p == "/api/seller/remove":
            usr = self._need("seller")
            if not usr:
                return
            with closing(db()) as c:
                c.execute("UPDATE listings SET status='removed' WHERE id=? AND seller_id=?",
                          (int(data.get("id", 0)), usr["id"]))
                c.commit()
            return self._send(200, {"ok": True})

        # ---- buyer ----
        if p == "/api/buy":
            usr = self._need("buyer")
            if not usr:
                return
            lid = int(data.get("listing_id", 0))
            with closing(db()) as c:
                l = c.execute("SELECT id FROM listings WHERE id=? AND status='published'", (lid,)).fetchone()
                if not l:
                    raise ValueError("This listing is no longer available.")
                c.execute("INSERT INTO orders(buyer_id,listing_id,created) VALUES(?,?,?)", (usr["id"], lid, now()))
                c.commit()
            return self._send(200, {"ok": True})
        if p == "/api/report":
            usr = self._need("buyer")
            if not usr:
                return
            lid = int(data.get("listing_id", 0))
            reason = str(data.get("reason", "")).strip()[:300] or "Looks like wildlife trafficking"
            with closing(db()) as c:
                l = c.execute("SELECT id,status FROM listings WHERE id=?", (lid,)).fetchone()
                if not l or l["status"] != "published":
                    raise ValueError("This listing cannot be reported.")
                dup = c.execute("SELECT 1 FROM reports WHERE listing_id=? AND buyer_id=? AND status='open'",
                                (lid, usr["id"])).fetchone()
                if dup:
                    raise ValueError("You already reported this listing.")
                c.execute("INSERT INTO reports(listing_id,buyer_id,reason,status,created) VALUES(?,?,?,?,?)",
                          (lid, usr["id"], reason, "open", now()))
                open_n = c.execute("SELECT COUNT(*) FROM reports WHERE listing_id=? AND status='open'",
                                   (lid,)).fetchone()[0]
                if open_n >= 2:  # two independent reports hide the listing until an admin looks
                    c.execute("UPDATE listings SET status='review', reason=? WHERE id=?",
                              ("Hidden after %d buyer reports" % open_n, lid))
                c.commit()
            return self._send(200, {"ok": True})

        # ---- everything below is admin only ----
        if not self._need("admin"):
            return
        if p == "/api/analyze":
            text = str(data.get("text", ""))[:5000].strip()
            if not text:
                return self._send(400, {"error": "empty"})
            return self._send(200, ENGINE.analyze(text, save=bool(data.get("save", True))))
        if p == "/api/batch":
            texts = [str(t)[:5000] for t in data.get("texts", [])][:60]
            return self._send(200, [ENGINE.analyze(t, source="batch") for t in texts if t.strip()])
        if p == "/api/feedback":
            text = str(data.get("text", ""))[:5000].strip()
            label = 1 if int(data.get("label", 0)) else 0
            if not text:
                return self._send(400, {"error": "empty"})
            with ENGINE.lock:
                with closing(db()) as c:
                    c.execute("INSERT INTO feedback(ts,text,label) VALUES(?,?,?)", (now(), text, label))
                    c.commit()
                ENGINE.train()
            return self._send(200, {"ok": True, "n_train": ENGINE.n_train})
        if p == "/api/import":
            rows, skipped = parse_csv_rows(str(data.get("csv", "")))
            with ENGINE.lock:
                added = store_rows(rows, bool(data.get("only")))
                ENGINE.train()
            return self._send(200, {"ok": True, "added": added, "skipped": skipped,
                                    "n_train": ENGINE.n_train, "n_test": len(ENGINE.test)})
        if p == "/api/admin/load_datasets":
            names = [str(x) for x in data.get("files", [])]
            allowed = {d["name"] for d in list_dataset_files()}
            rows, skipped = [], 0
            for nm in names:
                if nm not in allowed:
                    raise ValueError("Unknown dataset file: " + nm)
                with open(os.path.join(BASE, nm), encoding="utf-8-sig") as fh:
                    r_, s_ = parse_csv_rows(fh.read())
                rows += r_
                skipped += s_
            if not rows:
                raise ValueError("Select at least one dataset file.")
            with ENGINE.lock:
                added = store_rows(rows, bool(data.get("only")))
                ENGINE.train()
            return self._send(200, {"ok": True, "added": added, "skipped": skipped,
                                    "n_train": ENGINE.n_train, "n_test": len(ENGINE.test)})
        if p == "/api/reset_import":
            with ENGINE.lock:
                with closing(db()) as c:
                    c.execute("DELETE FROM imported")
                    c.execute("DELETE FROM meta WHERE key='only'")
                    c.commit()
                ENGINE.train()
            return self._send(200, {"ok": True, "n_train": ENGINE.n_train})
        if p == "/api/admin/report_response":
            report_id = int(data.get("report_id", 0))
            response = str(data.get("response", "")).strip()[:1000]
            outcome = str(data.get("outcome", "dismissed"))
            if not response:
                raise ValueError("Enter a response for the buyer.")
            if outcome not in ("upheld", "dismissed"):
                raise ValueError("Choose a valid report outcome.")
            with closing(db()) as c:
                rep = c.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
                if not rep:
                    raise ValueError("Report not found.")
                l = c.execute("SELECT * FROM listings WHERE id=?", (rep["listing_id"],)).fetchone()
                action_text = ("Listing flagged for violation" if outcome == "upheld" else "Listing retained after review")
                c.execute("UPDATE reports SET status=?, admin_response=?, action_taken=? WHERE id=?",
                          (outcome, response, action_text, report_id))
                if outcome == "upheld" and l and l["status"] in ("published", "review"):
                    c.execute("UPDATE listings SET status='rejected', admin_action='rejected', decided_at=? WHERE id=?",
                              (now(), l["id"]))
                c.commit()
            return self._send(200, {"ok": True})
        if p == "/api/admin/decide":
            admin_decide(int(data.get("listing_id", 0)), str(data.get("action", "")))
            return self._send(200, {"ok": True, "n_train": ENGINE.n_train})
        if p == "/api/admin/seller_active":
            with closing(db()) as c:
                c.execute("UPDATE users SET active=? WHERE id=? AND role='seller'",
                          (1 if data.get("active") else 0, int(data.get("user_id", 0))))
                c.commit()
            return self._send(200, {"ok": True})
        if p == "/api/admin/reset_market":
            reset_market()
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


def ensure_admin():
    with closing(db()) as c:
        a = c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone()
    if not a:
        create_user("admin", "admin123", "admin")


def main():
    global ENGINE
    init_db()
    print("Training models ...")
    ENGINE = Engine()
    ensure_admin()
    seed_demo()
    print("Ready. Training samples: %d, test samples: %d" % (ENGINE.n_train, len(ENGINE.test)))

    # Render provides PORT as an environment variable. When running locally,
    # keep the original behaviour and use the first available port from 8000.
    render_port = os.environ.get("PORT")
    if render_port:
        port = int(render_port)
        srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        url = "http://0.0.0.0:%d" % port
        open_browser = False
    else:
        port = 8000
        srv = None
        while srv is None and port < 8020:
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            except OSError:
                port += 1
        if srv is None:
            print("No free port found.")
            sys.exit(1)
        url = "http://127.0.0.1:%d" % port
        open_browser = "--no-browser" not in sys.argv

    print("")
    print("  GreenBazaar marketplace : " + url)
    print("  IvoryBlock admin console: " + url + "/admin")
    print("  Demo logins: admin/admin123  seller1/seller123  rare_finds/seller123  buyer1/buyer123")
    print("  (press Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
