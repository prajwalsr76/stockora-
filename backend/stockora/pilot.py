"""Pilot report: measure a real shop before vs. while using Stockora.

Design: a SHADOW period (owner orders as usual; Stockora only records) gives the
baseline, then the ASSISTED period (owner uses Stockora's suggestions). Both are
measured from the same ledger, so the comparison uses the shop's real records.

  python -m stockora.pilot --db shop.db --shadow-start 2026-11-10 --assisted-start 2026-11-17

Caveats printed with every report: one shop, a few weeks, and seasons differ
between periods (e.g. a festival in one of them). Treat results as a case study,
not proof.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from .agents.reorder import edit_rate
from .db import Repo, connect


def period_metrics(repo: Repo, start: date, end: date) -> dict:
    """Metrics for [start, end) from the ledger."""
    s, e = start.isoformat(), end.isoformat()
    sid = repo.store_id
    days = max((end - start).days, 1)
    active = [p["id"] for p in repo.products()]
    sold = repo.one("SELECT -SUM(qty) u FROM stock_movements "
                    "WHERE store_id=? AND kind='sale' AND day>=? AND day<?", sid, s, e)["u"] or 0
    expired = repo.one("SELECT -SUM(qty) w FROM stock_movements WHERE store_id=? "
                       "AND kind='expire' AND qty<0 AND day>=? AND day<?", sid, s, e)["w"] or 0
    outs = repo.one("SELECT COUNT(*) n FROM stockout_days WHERE store_id=? AND day>=? AND day<?", sid, s, e)["n"]
    # average units on the shelf, sampled daily
    inv = [repo.one("SELECT SUM(qty) v FROM stock_movements WHERE store_id=? AND day<=?", sid,
                    (start + timedelta(days=k)).isoformat())["v"] or 0 for k in range(days)]
    orders = repo.one("SELECT COUNT(*) n, SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) a FROM orders "
                      "WHERE store_id=? AND substr(created_at,1,10)>=? AND substr(created_at,1,10)<?", sid, s, e)
    avg_stock = sum(inv) / len(inv)
    return {
        "days": days,
        "units_sold_per_day": round(sold / days, 2),
        "stockout_day_rate": round(outs / max(len(active) * days, 1), 4),
        "expired_units": round(expired, 1),
        "avg_stock_units": round(avg_stock, 1),
        "days_of_stock_held": round(avg_stock / (sold / days), 1) if sold else None,
        "orders_drafted": orders["n"] or 0,
        "orders_approved": orders["a"] or 0,
    }


def report(repo: Repo, shadow_start: date, assisted_start: date, end: date | None = None) -> str:
    end = end or date.today()
    base = period_metrics(repo, shadow_start, assisted_start)
    pilot = period_metrics(repo, assisted_start, end)

    def change(k: str) -> str:
        a, b = base[k], pilot[k]
        return "n/a" if not a or b is None else f"{(b - a) / abs(a) * 100:+.1f}%"

    def fmt(v) -> str:
        return "n/a" if v is None else f"{v:,}"

    rows = [
        ("Stockout days (share of product-days)", "stockout_day_rate"), ("Expired units", "expired_units"),
        ("Units sold per day", "units_sold_per_day"), ("Average units on the shelf", "avg_stock_units"),
        ("Days of stock held", "days_of_stock_held"),
    ]
    er = edit_rate(repo)
    lines = [
        f"# Stockora pilot report: {repo.store()['name']}",
        "",
        f"Shadow period (owner's usual ordering): {shadow_start} to {assisted_start - timedelta(days=1)} ({base['days']} days)",
        f"Assisted period (Stockora suggestions): {assisted_start} to {end - timedelta(days=1)} ({pilot['days']} days)",
        "",
        "| Metric | Shadow | Assisted | Change |",
        "|---|---|---|---|",
        *[f"| {label} | {fmt(base[k])} | {fmt(pilot[k])} | {change(k)} |" for label, k in rows],
        "",
        f"Orders drafted / approved in assisted period: {pilot['orders_drafted']} / {pilot['orders_approved']}",
        f"Owner edit rate on suggestions: {'n/a' if er is None else f'{er:.0%}'} (lower = suggestions match the owner's judgement)",
        "",
        "Caveats: one shop over a few weeks; demand differs between periods (festivals, weather, salary week),",
        "and sales only count what was recorded. Report as a pilot case study, not as proof.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stockora.pilot")
    ap.add_argument("--db", required=True)
    ap.add_argument("--store", type=int, default=1)
    ap.add_argument("--shadow-start", required=True, type=date.fromisoformat)
    ap.add_argument("--assisted-start", required=True, type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat, default=None)
    ap.add_argument("--out", default=None, help="write the report to this .md file")
    a = ap.parse_args(argv)
    text = report(Repo(connect(a.db), a.store), a.shadow_start, a.assisted_start, a.end)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
