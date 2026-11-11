"""Expiry risk, dead stock and lost-sales report.

Lost sales are something owners never see in their books: the money that walked
out of the shop because an item was out of stock.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from ..db import Repo


@dataclass
class ExpiryAlert:
    product_id: int
    name: str
    batch_id: int
    batch_no: str | None
    expiry: str
    days_left: int
    remaining: float
    expected_to_sell: float
    leftover: float
    action: str


def expiry_alerts(repo: Repo, forecasts: dict[int, list[float]], today: date,
                  window_days: int = 10) -> list[dict]:
    """Batches that will not sell out before expiry, with a suggested action."""
    by_product: dict[int, list[dict]] = defaultdict(list)
    for b in repo.open_batches():
        if b["expiry_date"]:
            by_product[b["product_id"]].append(b)
    alerts: list[ExpiryAlert] = []
    for pid, batches in by_product.items():
        p = repo.product(pid)
        fc = forecasts.get(pid, [])
        sold_before = 0.0  # FEFO: earlier batches absorb demand first
        for b in sorted(batches, key=lambda x: x["expiry_date"]):
            exp = date.fromisoformat(b["expiry_date"])
            days_left = (exp - today).days
            demand_until = sum(fc[:max(days_left, 0)]) if fc else 0.0
            if days_left > len(fc) and fc:
                demand_until += (days_left - len(fc)) * (sum(fc) / len(fc))
            sellable = max(0.0, demand_until - sold_before)
            sells = min(b["remaining"], sellable)
            sold_before += sells
            leftover = round(b["remaining"] - sells, 1)
            if days_left > window_days or leftover < 1:   # under one unit is forecast noise, not a real risk
                continue
            if days_left <= 0:
                action = "Expired: remove from shelf now and claim return/credit note from distributor"
            elif days_left <= 2:
                action = "Sell at 25-30% off today, or return to distributor if they accept near-expiry stock"
            elif days_left <= 5:
                action = "Offer 10-15% off or a combo (e.g. with a fast seller); move to the counter"
            else:
                action = "Move to the front shelf/counter; stop reordering until it clears"
            alerts.append(ExpiryAlert(pid, p["name"], b["id"], b["batch_no"], b["expiry_date"], days_left,
                                      b["remaining"], round(sells, 1), leftover, action))
    return [asdict(a) for a in sorted(alerts, key=lambda a: (a.days_left, -a.leftover))]


def dead_stock(repo: Repo, today: date, idle_days: int = 30) -> list[dict]:
    since = (today - timedelta(days=idle_days)).isoformat()
    sold = {r["product_id"] for r in repo.q(
        "SELECT DISTINCT product_id FROM stock_movements WHERE store_id=? AND kind='sale' AND day>=?",
        repo.store_id, since)}
    out = []
    for pid, qty in repo.stock_all().items():
        if qty > 0 and pid not in sold:
            p = repo.product(pid)
            out.append({"product_id": pid, "name": p["name"], "qty": qty, "idle_days": idle_days,
                        "action": "Stop reordering; bundle with a fast seller or ask distributor for exchange"})
    return sorted(out, key=lambda x: -x["qty"])


def lost_sales(repo: Repo, today: date, days: int = 30) -> dict:
    """Units customers wanted but could not buy because the item had run out (last `days`).

    Demand on a stockout day = mean of the same weekday in the 4 previous non-stockout weeks."""
    since = today - timedelta(days=days)
    rows = repo.daily_sales(since - timedelta(days=28))
    sales: dict[tuple[int, str], float] = {(r["product_id"], r["day"]): r["units"] for r in rows}
    outs = repo.stockout_set()
    agg: dict[int, dict] = {}
    for pid, day_s in outs:
        d = date.fromisoformat(day_s)
        if not (since <= d <= today):
            continue
        same = [sales.get((pid, (d - timedelta(days=7 * k)).isoformat())) for k in range(1, 5)]
        same = [s for k, s in zip(range(1, 5), same)
                if s is not None and (pid, (d - timedelta(days=7 * k)).isoformat()) not in outs]
        a = agg.setdefault(pid, {"product_id": pid, "name": repo.product(pid)["name"], "days_out": 0, "lost_units": 0.0})
        a["days_out"] += 1
        if same:
            a["lost_units"] += max(0.0, sum(same) / len(same) - sales.get((pid, day_s), 0.0))
    top = sorted(agg.values(), key=lambda x: (-x["days_out"], -x["lost_units"]))
    for a in top:
        a["lost_units"] = round(a["lost_units"], 1)
    return {"period_days": days, "stockout_days": sum(a["days_out"] for a in top),
            "items_affected": len(top), "lost_units": round(sum(a["lost_units"] for a in top), 1),
            "top_items": top[:10]}
