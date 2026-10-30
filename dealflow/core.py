import hashlib, json, math, re, sqlite3
from dataclasses import dataclass
from pathlib import Path

THESIS = {"impact_areas": ["climate", "biodiversity", "health", "education"], "stages": ["seed", "series a"], "region": "Europe"}
FIELDS = ("name", "sector", "stage", "country", "ask", "contact_email")
CONFIDENCE_THRESHOLD = .75
EUROPE = {"albania","andorra","austria","belgium","bosnia and herzegovina","bulgaria","croatia","cyprus","czechia","czech republic","denmark","england","estonia","finland","france","germany","greece","hungary","iceland","ireland","italy","kosovo","latvia","liechtenstein","lithuania","luxembourg","malta","moldova","monaco","montenegro","netherlands","north macedonia","northern ireland","norway","poland","portugal","romania","san marino","scotland","serbia","slovakia","slovenia","spain","sweden","switzerland","uk","ukraine","united kingdom","wales"}
FREEMAIL = {"gmail.com","googlemail.com","outlook.com","hotmail.com","live.com","yahoo.com","icloud.com","me.com","proton.me","protonmail.com","gmx.de","gmx.net","web.de","t-online.de","mail.com","aol.com"}

@dataclass
class Deal:
    name: str; sector: str; stage: str; country: str; ask: str; contact_email: str
    source: str = ""; score: int = 0; reasons: list = None

class Extractor:
    """Extractor contract: extract fields and a confidence number in [0, 1]."""
    def extract(self, text): raise NotImplementedError

class RulesExtractor(Extractor):
    def extract(self, text):
        def get(label):
            m = re.search(r"^\s*" + re.escape(label) + r"\s*:\s*(.+?)\s*$", text, re.I|re.M)
            return m.group(1).strip() if m else ""
        result = {"name": get("Company"), "sector": get("Sector"), "stage": get("Stage"), "country": get("Country"), "ask": get("Ask"), "contact_email": (re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", get("Contact")) or [""])[0]}
        result["confidence"] = 1.0 if all(result[f] for f in FIELDS) else .4
        return result

def validate(raw):
    if not isinstance(raw, dict): return False, "extractor returned no record"
    missing = [f for f in FIELDS if not isinstance(raw.get(f), str) or not raw[f].strip()]
    if missing: return False, "missing: " + ", ".join(missing)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw["contact_email"]): return False, "invalid email"
    c = raw.get("confidence")
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or not 0 <= c <= 1: return False, "invalid confidence"
    if c < CONFIDENCE_THRESHOLD: return False, "low confidence extraction"
    return True, "ok"

def load_inputs(folder):
    folder, records = Path(folder), []
    for path in sorted(folder.glob("*.json")):
        try: data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError): records.append(("", str(path))); continue  # unreadable file -> human review, rest of folder continues
        for item in (data if isinstance(data, list) else [data]):
            body = item.get("body", item.get("text")) if isinstance(item, dict) else item
            records.append((body if isinstance(body, str) else "", str(path)))
    for path in sorted(folder.glob("*.txt")):
        try: records.append((path.read_text(encoding="utf-8"), str(path)))
        except (OSError, UnicodeDecodeError): records.append(("", str(path)))  # unreadable -> human review, folder continues
    return records

def deal_key(deal):
    # Same company name + same company domain = same deal. On a free-mail domain the domain says nothing, so the full address is used.
    email = deal.contact_email.strip().lower(); domain = email.split("@")[-1]
    return (deal.name.strip().lower(), email if domain in FREEMAIL else domain)

def dedupe(deals):
    seen, out = set(), []
    for deal in deals:
        if deal_key(deal) not in seen: seen.add(deal_key(deal)); out.append(deal)
    return out

def score(deal, thesis=THESIS):
    sector, stage, country = deal.sector.lower(), deal.stage.lower(), deal.country.lower()
    impact = any(re.search(r"\b" + re.escape(x.lower()) + r"\b", sector) for x in thesis["impact_areas"])  # multi-word and hyphenated sectors match too
    stages = [x.lower() for x in thesis["stages"]]; stage_ok = stage in stages; stage_txt = " / ".join(thesis["stages"])
    europe = country in EUROPE if thesis.get("region") == "Europe" else country == str(thesis.get("region", "")).lower()
    region = thesis.get("region", ""); areas = ", ".join(thesis["impact_areas"])
    checks = [("impact area", impact, 40, f"impact area in thesis ({areas})" if impact else f"impact area outside thesis ({areas})"), ("stage", stage_ok, 30, f"stage in thesis ({stage_txt})" if stage_ok else f"stage outside thesis ({stage_txt})"), ("geography", europe, 30, f"{region} geography" if europe else f"outside {region} geography")]
    return sum(p for _, m, p, _ in checks if m), [dict(criterion=c, matched=m, points=p if m else 0, reason=r) for c,m,p,r in checks]

def redact(value):
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted email]", str(value))
    return re.sub(r"(?<![\w€$])(?:\+\s?|\(0\)\s?|0)\d[\d ()./-]{6,}\d(?!\d)", "[redacted phone]", value)  # phones start with + or 0, so amounts like 2 500 000 survive

def init_db(conn):
    conn.executescript("""CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY, name TEXT, sector TEXT, stage TEXT, country TEXT, ask TEXT, contact_email TEXT, domain TEXT, source TEXT, score INTEGER, reasons TEXT, week TEXT, UNIQUE(name, domain)); CREATE TABLE IF NOT EXISTS reviews(source TEXT, reason TEXT, raw TEXT, digest TEXT UNIQUE, week TEXT);""")
    for table in ("deals", "reviews"):  # databases from before the week column get it added
        if "week" not in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]: conn.execute(f"ALTER TABLE {table} ADD COLUMN week TEXT")

def this_week():
    y, w, _ = __import__("datetime").date.today().isocalendar(); return f"{y}-W{w:02d}"

def save(conn, deals, reviews, week=None):
    week = week or this_week()
    init_db(conn)
    for d in deals: conn.execute("INSERT INTO deals(name,sector,stage,country,ask,contact_email,domain,source,score,reasons,week) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name, domain) DO UPDATE SET week=excluded.week, sector=excluded.sector, stage=excluded.stage, country=excluded.country, ask=excluded.ask, contact_email=excluded.contact_email, source=excluded.source, score=excluded.score, reasons=excluded.reasons", (d.name,d.sector,d.stage,d.country,d.ask,d.contact_email,deal_key(d)[1],d.source,d.score,json.dumps(d.reasons),week))
    for source, reason, raw in reviews:
        row = (redact(source), redact(reason), redact(raw)); key = hashlib.sha256(json.dumps(row).encode()).hexdigest()
        conn.execute("INSERT INTO reviews(source,reason,raw,digest,week) VALUES(?,?,?,?,?) ON CONFLICT(digest) DO UPDATE SET week=excluded.week", row + (key, week))
    conn.commit()

def digest(conn, week=None):
    """Digest for one ISO week (deals and review items seen that week); week=None lists everything stored."""
    w = "" if week is None else " WHERE week = ?"; args = () if week is None else (week,)
    rows = conn.execute(f"SELECT name,sector,stage,country,ask,score,reasons FROM deals{w} ORDER BY score DESC,name", args).fetchall(); reviews = conn.execute(f"SELECT source,reason,raw FROM reviews{w} ORDER BY source", args).fetchall()
    lines = ["# Ananda Impact Ventures — weekly dealflow digest", "", "_Demo output; confidential contact details redacted._", ""]
    for name, sector, stage, country, ask, points, reasons in rows:
        try: explained = json.loads(reasons or "[]")
        except (TypeError, json.JSONDecodeError): explained = [{"criterion": "summary", "matched": True, "reason": reasons or ""}]
        why = "; ".join(x["criterion"] + (" matched" if x.get("matched") else " not matched") + ": " + x["reason"] for x in explained)
        lines += [f"## {redact(name)} — {points}/100", f"- {redact(sector)} · {redact(stage)} · {redact(country)}", f"- Ask: {redact(ask)}", f"- Why: {redact(why)}", ""]
    lines += [f"Human review queue: {len(reviews)} item(s)."] + [f"- {redact(s)}: {redact(r)} — {redact(raw)[:160]}" for s,r,raw in reviews]
    return "\n".join(lines) + "\n"

def run(folder, db_path, digest_path, extractor=None):
    extractor, deals, reviews = extractor or RulesExtractor(), [], []
    for text, source in load_inputs(folder):
        try:
            raw = extractor.extract(text); ok, reason = validate(raw)
            if not ok: reviews.append((source, reason, text)); continue
            deal = Deal(source=source, **{f: raw[f].strip() for f in FIELDS}); deal.score, deal.reasons = score(deal)
        except Exception as exc:  # one bad record goes to the review queue; the rest of the folder continues
            reviews.append((source, f"processing error: {type(exc).__name__}", text)); continue
        deals.append(deal)
    deals = dedupe(deals); week = this_week()
    conn = sqlite3.connect(db_path); save(conn, deals, reviews, week); Path(digest_path).write_text(digest(conn, week)); conn.close(); return len(deals), len(reviews)
