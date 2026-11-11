"""Stock alerts a kirana owner rarely sees in the books.

1. Missed requests ("kisi ne maanga")  customers ask for items that are not on the shelf
2. Count mismatches                    shelf counts lower than the records (damage, theft, unbilled
                                       sales): the system then thinks there is more stock than there
                                       is, so items run out without warning
3. Udhaar risk                         customers whose dues are ageing and growing

All numbers come from the shop's own ledger, in units (no money values).
"""
from __future__ import annotations

from datetime import date, timedelta

from ..db import Repo
import re

from ..matching import ProductMatcher, normalize


def _item_key(text: str) -> str:
    """Group spellings of the same unknown item: drop pack sizes and extra spaces."""
    t = re.sub(r"\b\d+(?:\.\d+)?\s*(?:kg|g|ml|l|pcs|pack)\b", " ", normalize(text))
    return " ".join(t.split())

SCHEMA = """
CREATE TABLE IF NOT EXISTS missed_requests (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, item TEXT NOT NULL, product_id INTEGER,
  qty REAL NOT NULL DEFAULT 1, day TEXT NOT NULL
);
"""
ASK_TO_DEMAND = 2.5   # assumption: for every customer who asks, ~1.5 more leave without asking


def _ensure(repo: Repo) -> None:
    repo.conn.executescript(SCHEMA)


def log_request(repo: Repo, item: str, day: date, qty: float = 1) -> dict:
    """Owner taps "kisi ne maanga": a customer asked for something not on the shelf."""
    _ensure(repo)
    matcher = ProductMatcher([(r["product_id"], r["alias"]) for r in repo.aliases()])
    hit, _ = matcher.resolve(item)
    pid = hit.product_id if hit else None
    repo.conn.execute("INSERT INTO missed_requests(store_id, item, product_id, qty, day) VALUES (?,?,?,?,?)",
                      (repo.store_id, item.strip(), pid, qty, day.isoformat()))
    known = repo.product(pid)["name"] if pid else None
    return {"logged": item, "matched_product": known,
            "note": "You stock this item but it ran out" if known else "Not in your product list"}


def missed_request_suggestions(repo: Repo, today: date, days: int = 30) -> list[dict]:
    _ensure(repo)
    rows = repo.q("SELECT item, product_id, qty, day FROM missed_requests WHERE store_id=? AND day>=?",
                  repo.store_id, (today - timedelta(days=days)).isoformat())
    groups: dict[str, dict] = {}
    for r in rows:
        key = f"p{r['product_id']}" if r["product_id"] else _item_key(r["item"])
        g = groups.setdefault(key, {"item": r["item"], "product_id": r["product_id"], "asks": 0.0, "days": set()})
        g["asks"] += r["qty"]
        g["days"].add(r["day"])
    out = []
    for g in groups.values():
        if g["asks"] >= 3 and len(g["days"]) >= 2:
            weekly = g["asks"] / days * 7 * ASK_TO_DEMAND
            stocked = g["product_id"] is not None
            out.append({"item": repo.product(g["product_id"])["name"] if stocked else g["item"],
                        "asked_times": int(g["asks"]), "on_days": len(g["days"]),
                        "estimated_weekly_demand": round(weekly, 1),
                        "action": ("Keep more in stock: customers keep asking after it runs out" if stocked
                                   else "Consider stocking this: customers ask for it regularly")})
    return sorted(out, key=lambda x: -x["asked_times"])


def count_mismatch(repo: Repo, today: date, days: int = 30, min_units: float = 5) -> list[dict]:
    """Negative stock-count corrections vs units sold, per product (units)."""
    since = (today - timedelta(days=days)).isoformat()
    rows = repo.q(
        "SELECT p.id, p.name, "
        "-SUM(CASE WHEN m.kind='adjust' AND m.qty<0 THEN m.qty ELSE 0 END) AS missing, "
        "-SUM(CASE WHEN m.kind='sale' THEN m.qty ELSE 0 END) AS sold, "
        "SUM(CASE WHEN m.kind='adjust' AND m.qty<0 THEN 1 ELSE 0 END) AS times "
        "FROM stock_movements m JOIN products p ON p.id=m.product_id "
        "WHERE m.store_id=? AND m.day>=? GROUP BY p.id HAVING missing > 0", repo.store_id, since)
    out = []
    for r in rows:
        rate = r["missing"] / max(r["missing"] + r["sold"], 1)
        if r["missing"] >= min_units or (rate >= 0.05 and r["times"] >= 2):
            out.append({"product_id": r["id"], "name": r["name"], "missing_units": round(r["missing"], 1),
                        "share_of_movement": round(rate, 3), "counts_short": r["times"],
                        "action": "Check billing for this item, keep it nearer the counter, and count it weekly"})
    return sorted(out, key=lambda x: -x["missing_units"])


def udhaar_risk(repo: Repo, today: date, limit: float = 3000, max_age: int = 60) -> list[dict]:
    """Score each customer's dues: how big, how old, and whether they are still paying."""
    out = []
    for r in repo.receivables():
        age = (today - date.fromisoformat(r["oldest"])).days if r["oldest"] else 0
        since_paid = (today - date.fromisoformat(r["last_paid"])).days if r["last_paid"] else None
        score = min(r["due"] / limit, 2) * 30 + min(age / max_age, 2) * 40 + (30 if since_paid is None or since_paid > 30 else 0)
        level = "high" if score >= 70 else "medium" if score >= 40 else "low"
        reason = [f"Rs {r['due']:,.0f} due"]
        if age:
            reason.append(f"oldest dues {age} days old")
        reason.append("no payment yet" if since_paid is None else f"last paid {since_paid} days ago")
        action = {"high": "Collect before giving more credit", "medium": "Send a polite reminder this week",
                  "low": "No action needed"}[level]
        out.append({"customer_id": r["id"], "name": r["name"], "risk": level, "score": round(score),
                    "why": ", ".join(reason), "action": action})
    return sorted(out, key=lambda x: -x["score"])


def alerts_summary(repo: Repo, today: date) -> dict:
    return {"missed_requests": missed_request_suggestions(repo, today), "count_mismatch": count_mismatch(repo, today),
            "udhaar_risk": udhaar_risk(repo, today)}
