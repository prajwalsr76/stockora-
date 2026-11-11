"""Application service layer: the single place the API, MCP server and assistant call.

Keeping business logic here (not in HTTP handlers) means every capability is
unit-tested once and exposed identically over REST, MCP and the chat agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from .agents import inventory_health, reorder
from .db import Repo
from .forecasting.coldstart import coldstart_forecasts
from .forecasting.model import Forecaster
from .india import next_festivals


def history_frame(repo: Repo, days: int = 400, today: date | None = None) -> pd.DataFrame:
    """Daily sales per product from the ledger, zero-filled, with stockout flags.

    Each product's history starts on its first recorded movement (purchase, sale or
    opening stock), so a shop that joined three weeks ago is not padded with a year
    of fake zero sales.
    """
    today = today or date.today()
    start = today - timedelta(days=days)
    prods = {p["id"]: p for p in repo.products()}
    first = {r["product_id"]: date.fromisoformat(r["d"]) for r in repo.q(
        "SELECT product_id, MIN(day) d FROM (SELECT product_id, day FROM stock_movements WHERE store_id=? "
        "UNION ALL SELECT product_id, day FROM sales_history WHERE store_id=?) GROUP BY product_id",
        repo.store_id, repo.store_id)}
    cols = ["product_id", "day", "sales", "stockout", "sku", "category"]
    frames = []
    for pid, p in prods.items():
        if pid not in first:
            continue
        s = max(start, first[pid])
        rng = pd.date_range(s, today - timedelta(days=1)).date
        if len(rng):
            frames.append(pd.DataFrame({"product_id": pid, "day": rng}))
    if not frames:
        return pd.DataFrame(columns=cols)
    grid = pd.concat(frames, ignore_index=True)
    sales = pd.DataFrame([dict(r) for r in repo.daily_sales(start)],
                         columns=["product_id", "day", "units", "revenue"])
    if len(sales):
        sales["day"] = pd.to_datetime(sales["day"]).dt.date
        grid = grid.merge(sales[["product_id", "day", "units"]], on=["product_id", "day"], how="left")
    else:
        grid["units"] = 0.0
    grid["units"] = grid["units"].fillna(0.0)
    outs = repo.stockout_set()
    grid["stockout"] = [(p, d.isoformat()) in outs for p, d in zip(grid["product_id"], grid["day"])]
    grid["sku"] = grid["product_id"].astype(str)
    grid["category"] = grid["product_id"].map(lambda p: prods[p]["category"])
    grid["day"] = pd.to_datetime(grid["day"])
    return grid.rename(columns={"units": "sales"})[cols]


MODEL_MIN_DAYS = 120   # days of shop history before the ML model takes over


@dataclass
class StoreBrain:
    """Trained forecaster + cached forecasts for one store."""

    repo: Repo
    today: date = field(default_factory=date.today)
    forecaster: Forecaster | None = None
    forecasts: dict[int, list[float]] = field(default_factory=dict)
    sigma: dict[int, float] = field(default_factory=dict)
    mode: str = "learning"          # "learning" (cold start) or "model" (trained ML forecaster)
    history_days: int = 0
    observed: dict[int, int] = field(default_factory=dict)   # days of sales data per product

    def refresh(self) -> "StoreBrain":
        hist = history_frame(self.repo, today=self.today)
        products = [dict(p) for p in self.repo.products()]
        self.history_days = int(hist["day"].nunique()) if len(hist) else 0
        # Cold-start forecasts for everything; the ML model then overrides products it can forecast.
        self.forecasts, self.sigma, self.observed = coldstart_forecasts(hist, products, self.today)
        self.mode, self.forecaster = "learning", None
        if self.history_days >= MODEL_MIN_DAYS:
            fc_model = Forecaster().fit(hist, step=4)
            fc = fc_model.predict(hist)
            for s_, g in fc.groupby("sku"):
                self.forecasts[int(s_)] = g.sort_values("h")["forecast"].round(3).tolist()
                self.observed[int(s_)] = max(self.observed.get(int(s_), 0), MODEL_MIN_DAYS)
            for k, v in fc_model.sigma.items():
                self.sigma[int(k)] = float(v)
            self.mode, self.forecaster = "model", fc_model
        return self

    def status(self) -> dict:
        return {"mode": self.mode, "history_days": self.history_days, "model_min_days": MODEL_MIN_DAYS}

    # ---------------------------------------------------------------- capabilities
    def _runs_out(self, qty: float, fc: list[float]) -> tuple[float | None, str | None]:
        """Days until stock hits zero walking the daily forecast (not an average), and that date."""
        if qty <= 0:
            return 0.0, self.today.isoformat()
        if not fc or sum(fc) <= 0:
            return None, None
        left = qty
        for i, f in enumerate(fc):
            if f >= left:
                days = i + left / f if f > 0 else float(i)
                return round(days, 1), (self.today + timedelta(days=int(days))).isoformat()
            left -= f
        avg = sum(fc) / len(fc)
        days = len(fc) + left / avg
        return round(days, 1), (self.today + timedelta(days=int(days))).isoformat()

    def stock_summary(self, low_only: bool = False, only: int | None = None) -> list[dict]:
        stock = self.repo.stock_all()
        sales = self.repo.daily_sales(self.today - timedelta(days=28))
        sold7: dict[int, float] = {}
        sold28: dict[int, float] = {}
        cut7 = (self.today - timedelta(days=7)).isoformat()
        for r in sales:
            sold28[r["product_id"]] = sold28.get(r["product_id"], 0.0) + r["units"]
            if r["day"] >= cut7:
                sold7[r["product_id"]] = sold7.get(r["product_id"], 0.0) + r["units"]
        out = []
        for p in self.repo.products():
            pid = p["id"]
            if only is not None and pid != only:
                continue
            fc = self.forecasts.get(pid, [])
            f7 = sum(fc[:7])
            daily = f7 / 7 if fc else 0.0
            qty = stock.get(pid, 0.0)
            days, runs_out_on = self._runs_out(qty, fc)
            sig = float(self.sigma.get(pid, 0.0))
            lead = 1 if (p["shelf_life_days"] or 999) <= 7 else 2
            z = reorder.SERVICE_Z.get(p["category"], 2.0)
            band = 1.28 * sig * 7 ** 0.5                     # 80% range of the 7-day total
            s7 = sold7.get(pid, 0.0)
            row = {"product_id": pid, "name": p["name"], "category": p["category"], "unit": p["unit"],
                   "stock": qty, "mrp": p["mrp"], "shelf_life_days": p["shelf_life_days"],
                   "daily_rate": round(daily, 2),
                   "forecast_7d": round(f7, 1),
                   "forecast_7d_low": round(max(f7 - band, 0.0), 1),
                   "forecast_7d_high": round(f7 + band, 1),
                   "days_of_stock": days, "runs_out_on": runs_out_on,
                   "reorder_point": round(sum(fc[:lead]) + z * sig * lead ** 0.5, 1) if fc else None,
                   "sold_7d": round(s7, 1), "sold_28d": round(sold28.get(pid, 0.0), 1),
                   "trend_pct": round((f7 / s7 - 1) * 100) if s7 > 0 else None}
            if not low_only or (days is not None and days < 4):
                out.append(row)
        return sorted(out, key=lambda r: (r["days_of_stock"] is None, r["days_of_stock"] or 0))

    def product_detail(self, product_id: int) -> dict:
        p = self.repo.product(product_id)
        if p is None:
            raise KeyError("product not found")
        row = self.stock_summary(only=product_id)[0]
        since = self.today - timedelta(days=56)
        sold = {r["day"]: r["units"] for r in self.repo.daily_sales(since) if r["product_id"] == product_id}
        outs = {d for pid, d in self.repo.stockout_set() if pid == product_id}
        history = []
        for k in range(56, 0, -1):
            d = (self.today - timedelta(days=k)).isoformat()
            history.append({"day": d, "units": round(sold.get(d, 0.0), 2), "stockout": d in outs})
        fc = self.forecasts.get(product_id, [])
        sig = float(self.sigma.get(product_id, 0.0))
        left = row["stock"]
        forecast = []
        for i, f in enumerate(fc):
            left -= f
            forecast.append({"day": (self.today + timedelta(days=i)).isoformat(), "units": round(f, 2),
                             "low": round(max(f - 1.28 * sig, 0.0), 2), "high": round(f + 1.28 * sig, 2),
                             "stock_after": round(left, 1)})
        risk = {a["batch_id"]: a for a in inventory_health.expiry_alerts(
            self.repo, {product_id: fc}, self.today) if a["product_id"] == product_id}
        batches = []
        for b in self.repo.open_batches(product_id):
            days_left = (date.fromisoformat(b["expiry_date"]) - self.today).days if b["expiry_date"] else None
            batches.append({"batch_id": b["id"], "batch_no": b["batch_no"], "qty": round(b["remaining"], 1),
                            "received": b["received_at"], "expiry": b["expiry_date"], "days_left": days_left,
                            "at_risk": round(risk[b["id"]]["leftover"], 1) if b["id"] in risk else 0.0})
        fests = [f"{f.name} ({f.day:%d %b})" for f in next_festivals(self.today, 14)]
        return {**row, "history": history, "forecast": forecast, "batches": batches,
                "festivals_in_window": fests, "mode": self.mode}

    def shop_trend(self, past_days: int = 28) -> dict:
        """Whole-shop units: actual sales for the last `past_days`, forecast for the next 14 with an 80% range."""
        since = self.today - timedelta(days=past_days)
        tot: dict[str, float] = {}
        for r in self.repo.daily_sales(since):
            if r["day"] < self.today.isoformat():
                tot[r["day"]] = tot.get(r["day"], 0.0) + r["units"]
        actual = [{"day": (since + timedelta(days=k)).isoformat(),
                   "units": round(tot.get((since + timedelta(days=k)).isoformat(), 0.0), 1)} for k in range(past_days)]
        horizon = max((len(v) for v in self.forecasts.values()), default=0)
        var = sum(s_ ** 2 for s_ in self.sigma.values())     # independent errors: variances add
        forecast = []
        for i in range(horizon):
            u = sum(v[i] for v in self.forecasts.values() if len(v) > i)
            band = 1.28 * var ** 0.5
            forecast.append({"day": (self.today + timedelta(days=i)).isoformat(), "units": round(u, 1),
                             "low": round(max(u - band, 0.0), 1), "high": round(u + band, 1)})
        fests = [{"name": f.name, "day": f.day.isoformat()} for f in next_festivals(self.today, 14)]
        return {"actual": actual, "forecast": forecast, "festivals": fests}

    def forecast_for(self, product_id: int) -> dict:
        p = self.repo.product(product_id)
        if p is None:
            raise KeyError("product not found")
        fc = self.forecasts.get(product_id, [])
        days = [(self.today + timedelta(days=i)).isoformat() for i in range(len(fc))]
        fests = [f"{f.name} ({f.day:%d %b})" for f in next_festivals(self.today, 14)]
        return {"product_id": product_id, "name": p["name"], "daily": dict(zip(days, fc)),
                "next_7_days": round(sum(fc[:7]), 1), "next_14_days": round(sum(fc), 1),
                "festivals_in_window": fests}

    def order_plan(self) -> reorder.OrderPlan:
        return reorder.plan_orders(self.repo, self.forecasts, self.sigma, self.today, self.observed, cash_aware=False)

    def expiry(self) -> list[dict]:
        return inventory_health.expiry_alerts(self.repo, self.forecasts, self.today)

    def dead_stock(self) -> list[dict]:
        return inventory_health.dead_stock(self.repo, self.today)

    def lost_sales(self, days: int = 30) -> dict:
        return inventory_health.lost_sales(self.repo, self.today, days)

    def morning_briefing(self) -> dict:
        y = self.today - timedelta(days=1)
        units = sum(r["units"] for r in self.repo.daily_sales(y) if r["day"] == y.isoformat())
        tracked = self.repo.one("SELECT COUNT(*) n FROM stockout_days WHERE store_id=?", self.repo.store_id)["n"]
        plan = self.order_plan()
        return {
            "date": self.today.isoformat(),
            "yesterday_units": round(units, 1),
            "low_stock": self.stock_summary(low_only=True)[:8],
            "expiring": self.expiry()[:5],
            "reorder_items": len(plan.lines),
            "collect_from": plan.collect_from[:3],
            "festivals": plan.festival_note,
            # None until the shop has recorded at least one stockout in Stockora
            "stockout_days_30d": self.lost_sales()["stockout_days"] if tracked else None,
        }
