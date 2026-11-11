"""Reorder agent: forecast -> cash-aware, scheme-aware purchase orders awaiting owner approval.

What makes it different from a low-stock alert:
  * Orders are sized from festival-aware forecasts over lead time + review period.
  * Shelf life caps quantity (never order more than sells before expiry).
  * Distributor trade schemes ("buy 10 get 1") are priced in, including bumping to
    the scheme threshold when it is cheaper per unit and still sells in time.
  * CASH-AWARE: total spend is limited to cash in hand plus likely udhaar
    collections; lines are prioritised by stockout risk x profit, and the agent
    tells the owner which credit customers to collect from to fund the rest.
  * Learns the owner: every edit nudges a per-product bias, so suggestions converge
    to what the owner would actually order (tracked as edit rate).
  * Never places an order: it drafts; the owner approves.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date

from ..db import Repo
from ..india import next_festivals
from ..ordering_rules import review_days, shelf_cap_days
from .inventory_health import expiry_alerts

# Safety levels chosen on REAL demand (Favorita replay, evals/real_policy.py): with out-of-sample
# uncertainty, z~3 for fresh items and ~2.5 for staples beat gut-feel ordering on stockouts while
# holding less stock. Slow, optional categories get lower levels to protect cash.
SERVICE_Z = {"dairy": 3.0, "bakery": 3.0, "produce": 3.0, "staples": 2.5, "oil_ghee": 2.5, "instant_food": 2.5,
             "snacks": 2.5, "beverages": 2.5, "personal_care": 2.0, "home_care": 2.0, "spices": 2.0,
             "festive": 1.5, "fasting": 1.5, "stationery": 1.5}
REVIEW_DAYS = 7
CASH_FLOAT = 0.15        # keep 15% of cash for change and daily expenses


@dataclass
class Line:
    product_id: int
    name: str
    category: str
    stock: float
    pending: float
    forecast_cover: float
    safety: float
    qty: float
    distributor_id: int
    distributor: str
    unit_price: float
    free_units: int
    line_cost: float
    priority: float
    days_of_stock: float
    reason: str
    scheme_note: str = ""


@dataclass
class OrderPlan:
    day: str
    cash_available: float
    expected_collections: float
    budget: float
    lines: list[Line] = field(default_factory=list)
    deferred: list[Line] = field(default_factory=list)
    collect_from: list[dict] = field(default_factory=list)
    below_minimum: list[str] = field(default_factory=list)
    messages: dict[int, str] = field(default_factory=dict)
    festival_note: str = ""

    @property
    def total(self) -> float:
        return round(sum(l.line_cost for l in self.lines), 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        return d


def expected_collections(receivables: list[dict], today: date) -> float:
    """Probability-weighted udhaar likely to come in within a week (recent dues pay back more)."""
    total = 0.0
    for r in receivables:
        age = (today - date.fromisoformat(r["oldest"])).days if r["oldest"] else 0
        p = 0.45 if age <= 15 else 0.25 if age <= 45 else 0.08
        total += r["due"] * p
    return round(total, 2)


def _choose_offer(offers: list, qty: float, max_qty: float, preferred: int | None) -> tuple | None:
    """Pick (offer, qty, free, cost) minimising cost per usable unit, honouring schemes."""
    best = None
    for o in offers:
        candidates = [qty]
        if o["scheme_buy"] and qty < o["scheme_buy"] <= max_qty and o["scheme_buy"] <= qty * 1.6:
            candidates.append(float(o["scheme_buy"]))      # bump to unlock the scheme
        for q in candidates:
            free = int(q // o["scheme_buy"]) * o["scheme_free"] if o["scheme_buy"] else 0
            cost = q * o["price"]
            per_unit = cost / (q + free)
            if o["distributor_id"] == preferred:
                per_unit *= 0.97                            # owner's preferred supplier wins near-ties
            key = (per_unit, cost)
            if best is None or key < best[0]:
                best = (key, o, q, free, cost)
    return best[1:] if best else None


NEW_ITEM_FACTOR = 0.5   # items with no sales data yet: order half the estimate and learn


def plan_orders(repo: Repo, forecasts: dict[int, list[float]], sigma: dict[int, float],
                today: date, observed: dict[int, int] | None = None, cash_aware: bool = True) -> OrderPlan:
    """forecasts: product_id -> next 14 daily forecasts (units); sigma: residual std per day.

    cash_aware=False restocks everything that is needed: no cash budget and no distributor
    minimum order values (the Stockora app runs this way; it focuses on availability)."""
    store = repo.store()
    stock = repo.stock_all()
    pending = repo.pending_order_qty()
    prefs = repo.prefs()
    receivables = repo.receivables()
    collections = expected_collections(receivables, today)
    cash = float(store["cash_on_hand"])
    budget = round(cash * (1 - CASH_FLOAT) + collections, 2)

    fests = next_festivals(today, 21)
    plan = OrderPlan(today.isoformat(), cash, collections, budget,
                     festival_note=", ".join(f"{f.name} on {f.day:%d %b}" for f in fests))

    # Units that will expire unsold are not really available.
    expiring: dict[int, float] = {}
    for a in expiry_alerts(repo, forecasts, today, window_days=60):
        expiring[a["product_id"]] = expiring.get(a["product_id"], 0.0) + a["leftover"]

    candidates: list[Line] = []
    for p in repo.products():
        pid = p["id"]
        fc = forecasts.get(pid)
        offers = repo.prices_for(pid)
        if not fc or not offers:
            continue
        shelf = p["shelf_life_days"] or 365
        perishable = shelf <= 7
        review = review_days(shelf)          # milk/bread daily, dairy/eggs every 3 days, dry goods weekly
        avg_daily = max(sum(fc) / len(fc), 1e-6)
        on_hand = max(stock.get(pid, 0.0) - expiring.get(pid, 0.0), 0.0)
        incoming = pending.get(pid, 0.0)
        days_left = on_hand / avg_daily
        fastest = min(o["lead_time_days"] for o in offers)
        urgent = days_left < fastest + 1
        usable = [o for o in offers if not urgent or o["lead_time_days"] == fastest]
        if perishable:
            usable = [o for o in usable if o["lead_time_days"] < shelf] or usable
        pref = prefs.get(pid, {})
        z = SERVICE_Z.get(p["category"], 2.0)
        best = None
        for o in usable:
            lead = o["lead_time_days"]
            cover_days = min(lead + review, len(fc))
            need = sum(fc[:cover_days])
            safety = z * sigma.get(pid, 0.0) * math.sqrt(cover_days)
            raw = (need + safety - on_hand - incoming) * pref.get("qty_bias", 1.0)
            if observed is not None and observed.get(pid, 0) == 0:
                raw = (need * NEW_ITEM_FACTOR - on_hand - incoming) * pref.get("qty_bias", 1.0)
            max_qty = max(0.0, avg_daily * shelf_cap_days(shelf) - on_hand - incoming)
            qty = min(raw, max_qty)
            if qty < 0.5:
                continue
            qty = float(math.ceil(qty))
            choice = _choose_offer([o], qty, max(max_qty, qty), pref.get("preferred_distributor_id"))
            if choice is None:
                continue
            offer, q, free, cost = choice
            per_unit = cost / (q + free)
            if offer["distributor_id"] == pref.get("preferred_distributor_id"):
                per_unit *= 0.97
            if best is None or per_unit < best[0]:
                best = (per_unit, offer, q, qty, free, cost, need, safety, cover_days, lead)
        if best is None:
            continue
        _, offer, q, qty, free, cost, need, safety, cover_days, lead = best
        risk = 1.0 if days_left < lead else 0.6 if days_left < lead + 2 else 0.3
        margin = max(p["mrp"] - offer["price"], 0.0)
        priority = risk * (margin * avg_daily + 1)
        window = fc[:cover_days]
        fest_peak = max(window) / (sum(window) / len(window)) if sum(window) > 0 else 1
        reason = (f"forecast {need:.0f} over {cover_days} days (lead {lead}d + "
                  f"{ {1: 'daily', 3: 'every-3-days'}.get(review, 'weekly') } review) + safety {safety:.0f}, stock {on_hand:.0f}"
                  + (f", incoming {incoming:.0f}" if incoming else ""))
        if observed is not None and observed.get(pid, 0) == 0:
            reason += "; no sales recorded yet, so a cautious estimate from similar items"
        elif observed is not None and observed.get(pid, 0) < 28:
            reason += f"; still learning this item ({observed[pid]} days of data)"
        if urgent:
            reason += "; urgent: runs out before a normal delivery"
        if fest_peak > 1.3 and plan.festival_note:
            reason += f"; festival demand ahead ({plan.festival_note})"
        scheme_note = ""
        if free:
            label = offer["scheme_text"] or f"buy {offer['scheme_buy']} get {offer['scheme_free']}"
            scheme_note = f"{label}: +{free} free"
            if q > qty:
                scheme_note += f" (bumped {qty:.0f}->{q:.0f} to unlock)"
        candidates.append(Line(pid, p["name"], p["category"], on_hand, incoming, round(need, 1),
                               round(safety, 1), q, offer["distributor_id"], offer["distributor"],
                               offer["price"], free, round(cost, 2), round(priority, 3),
                               round(days_left, 1), reason, scheme_note))

    # Cash-aware selection: greedy by priority per rupee within budget.
    spent = 0.0
    for line in sorted(candidates, key=lambda l: l.priority / max(l.line_cost, 1), reverse=True):
        if not cash_aware or spent + line.line_cost <= budget:
            plan.lines.append(line)
            spent += line.line_cost
        else:
            plan.deferred.append(line)

    # Distributor minimum order values.
    mov = {d["id"]: (d["name"], d["min_order_value"]) for d in repo.distributors()}
    by_dist: dict[int, list[Line]] = {}
    for l in plan.lines:
        by_dist.setdefault(l.distributor_id, []).append(l)
    for did, lines in list(by_dist.items()):
        name, minimum = mov[did]
        value = sum(l.line_cost for l in lines)
        urgent = any(l.days_of_stock < 2 for l in lines)
        if cash_aware and value < minimum and not urgent:
            plan.below_minimum.append(f"{name}: order value Rs {value:.0f} below minimum Rs {minimum:.0f}; held")
            for l in lines:
                plan.lines.remove(l)
                plan.deferred.append(l)
            del by_dist[did]

    # Which udhaar to collect to fund deferred lines.
    shortfall = sum(l.line_cost for l in plan.deferred)
    if shortfall > 0:
        got = 0.0
        for r in sorted(receivables, key=lambda r: r["due"], reverse=True):
            plan.collect_from.append({"customer": r["name"], "phone": r["phone"], "due": round(r["due"], 2),
                                      "oldest_since": r["oldest"]})
            got += r["due"]
            if got >= shortfall:
                break

    for did, lines in by_dist.items():
        plan.messages[did] = draft_message(store["name"], mov[did][0], lines)
    return plan


def draft_message(store_name: str, distributor: str, lines: list[Line]) -> str:
    body = "\n".join(f"{i}. {l.name} - {l.qty:.0f}" + (f" ({l.scheme_note})" if l.scheme_note else "")
                     for i, l in enumerate(lines, 1))
    total = sum(l.line_cost for l in lines)
    return (f"Namaste {distributor} ji,\n{store_name} se order:\n{body}\n"
            f"Approx total: Rs {total:,.0f}\nKripya delivery date confirm karein. Dhanyavaad!")


def commit_plan(repo: Repo, plan: OrderPlan) -> list[int]:
    """Persist the plan as DRAFT orders. Nothing is sent until approve_order()."""
    by_dist: dict[int, list[Line]] = {}
    for l in plan.lines:
        by_dist.setdefault(l.distributor_id, []).append(l)
    ids = []
    with repo.tx():
        for did, lines in by_dist.items():
            ids.append(repo.create_order(did, [{"product_id": l.product_id, "qty": l.qty,
                                                "unit_price": l.unit_price, "reason": l.reason} for l in lines],
                                         plan.messages.get(did, "")))
        repo.audit("agent:reorder", "draft_orders", {"orders": ids, "total": plan.total})
    return ids


def approve_order(repo: Repo, order_id: int, edits: dict[int, float] | None = None,
                  actor: str = "owner") -> dict:
    """Owner approval (the human-in-the-loop gate). Edits are learned as preferences."""
    order = repo.order(order_id)
    if order is None:
        raise KeyError("order not found")
    if order["status"] != "draft":
        raise ValueError(f"order is {order['status']}, only drafts can be approved")
    edits = edits or {}
    with repo.tx():
        total = 0.0
        for l in order["lines"]:
            final = float(edits.get(l["product_id"], l["qty_suggested"]))
            if final < 0:
                raise ValueError("quantity cannot be negative")
            repo.conn.execute("UPDATE order_lines SET qty_final=? WHERE id=?", (final, l["id"]))
            repo.learn_from_decision(l["product_id"], l["qty_suggested"], final, order["distributor_id"])
            total += final * l["unit_price"]
        repo.conn.execute("UPDATE orders SET status='approved', total=?, decided_at=datetime('now'), decided_by=? "
                          "WHERE id=? AND store_id=?", (round(total, 2), actor, order_id, repo.store_id))
        repo.audit(actor, "approve_order", {"order_id": order_id, "edits": edits})
    return repo.order(order_id)


def reject_order(repo: Repo, order_id: int, actor: str = "owner") -> None:
    with repo.tx():
        repo.conn.execute("UPDATE orders SET status='cancelled', decided_at=datetime('now'), decided_by=? "
                          "WHERE id=? AND store_id=? AND status='draft'", (actor, order_id, repo.store_id))
        repo.audit(actor, "reject_order", {"order_id": order_id})


def edit_rate(repo: Repo) -> float | None:
    r = repo.one("SELECT SUM(edits) e, SUM(accepts) a FROM owner_prefs WHERE store_id=?", repo.store_id)
    total = (r["e"] or 0) + (r["a"] or 0)
    return round(r["e"] / total, 3) if total else None
