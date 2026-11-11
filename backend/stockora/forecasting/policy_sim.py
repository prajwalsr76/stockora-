"""Closed-loop policy simulation: owner's gut feel vs. Stockora, on identical demand.

Both policies see the same true daily demand, start with the same stock, face the
same lead times and shelf lives (stock expires FIFO). Only the ordering rule differs.
Demand is synthetic (see simulator.py) and results are reported as such.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..india import load_catalog
from ..ordering_rules import is_fresh, review_days, shelf_cap_days
from .model import Forecaster

LEAD_PERISHABLE, LEAD_OTHER, REVIEW = 1, 2, 7
Z = 1.5


@dataclass
class SimResult:
    stockout_day_rate: float
    fill_rate: float
    expired_value: float
    avg_inventory_value: float
    revenue: float
    gross_profit: float

    def as_dict(self) -> dict:
        return {k: round(float(v), 4) for k, v in self.__dict__.items()}


def _run(demand: dict[str, np.ndarray], start_stock: dict[str, float], policy, items: dict) -> SimResult:
    n = len(next(iter(demand.values())))
    out_days = total_days = 0
    sold_total = demand_total = expired_value = inv_value_sum = revenue = cost_of_sales = 0.0
    for sku, d in demand.items():
        item = items[sku]
        review = review_days(item.shelf_life_days)
        lead = LEAD_PERISHABLE if is_fresh(item.shelf_life_days) else LEAD_OTHER
        batches: deque[list] = deque([[start_stock[sku], item.shelf_life_days]])  # [qty, days_to_expiry]
        pipeline: dict[int, float] = {}
        sales_hist: list[float] = []
        out_hist: list[bool] = []
        for t in range(n):
            if t in pipeline:
                batches.append([pipeline.pop(t), item.shelf_life_days])
            on_hand = sum(b[0] for b in batches)
            sell = min(on_hand, d[t])
            left = sell
            while left > 1e-9 and batches:
                take = min(left, batches[0][0])
                batches[0][0] -= take
                left -= take
                if batches[0][0] <= 1e-9:
                    batches.popleft()
            stockout = d[t] > on_hand + 1e-9
            out_days += stockout
            total_days += 1
            sold_total += sell
            demand_total += d[t]
            revenue += sell * item.mrp
            cost_of_sales += sell * item.cost_price
            sales_hist.append(sell)
            out_hist.append(stockout)
            for b in batches:                      # age stock; discard expired
                b[1] -= 1
            while batches and batches[0][1] <= 0:
                expired_value += batches.popleft()[0] * item.cost_price
            stock = sum(b[0] for b in batches)
            inv_value_sum += stock * item.cost_price
            if t % review == 0:
                q = policy(sku, t, stock, sum(pipeline.values()), sales_hist, out_hist, review, lead)
                if q > 0:
                    pipeline[t + lead] = pipeline.get(t + lead, 0.0) + q
    return SimResult(out_days / total_days, sold_total / demand_total, expired_value,
                     inv_value_sum / n, revenue, revenue - cost_of_sales - expired_value)


def gut_feel_policy(seed: int = 3):
    rng = np.random.default_rng(seed)

    def policy(sku, t, stock, pipe, sales, outs, review, lead):
        lo = max(0, t - 14)
        ok = [s for s, o in zip(sales[lo:], outs[lo:]) if not o]
        est = np.mean(ok) if ok else (max(sales[lo:]) * 1.2 if sales[lo:] else 0)
        return float(math.ceil(max(0.0, est * (review + lead) * rng.uniform(0.85, 1.2) - stock - pipe)))
    return policy


def stockora_policy(forecasts: dict[int, pd.DataFrame], sigma: dict[str, float], items: dict, z: float = Z):
    """forecasts: review-day offset -> forecast frame (sku, h, forecast)."""
    cache: dict = {}

    def policy(sku, t, stock, pipe, sales, outs, review, lead):
        origin = max(k for k in forecasts if k <= t)
        if (origin, sku) not in cache:
            f = forecasts[origin]
            cache[(origin, sku)] = f[f["sku"] == sku].sort_values("h")["forecast"].to_numpy()
        vec = cache[(origin, sku)]
        # Decided at the end of day t; arrives at the start of t+lead; the next order arrives
        # `review` days later. So this order (plus stock and pipeline) must cover days
        # t+1 .. t+lead+review-1. vec[0] is the forecast for day `origin`.
        offset = t - origin
        cover = lead + review - 1
        window = vec[offset + 1:offset + 1 + cover]
        if len(window) < cover:
            window = np.concatenate([window, np.full(cover - len(window), vec.mean())])
        target = window.sum() + z * sigma.get(sku, 0.0) * math.sqrt(cover)
        item = items[sku]
        cap = vec.mean() * shelf_cap_days(item.shelf_life_days)
        return float(math.ceil(max(0.0, min(target - stock - pipe, cap - stock - pipe))))
    return policy


def compare_policies(hist: pd.DataFrame, window_days: int = 70) -> dict:
    """hist: simulator output with day, sku, category, demand, sales, stockout (gut-feel history)."""
    hist = hist.copy()
    hist["day"] = pd.to_datetime(hist["day"])
    items = {c.sku: c for c in load_catalog()}
    days = sorted(hist["day"].unique())
    start = pd.Timestamp(days[-window_days])
    pre = hist[hist["day"] < start]
    win = hist[hist["day"] >= start]
    demand = {s: g.sort_values("day")["demand"].to_numpy() for s, g in win.groupby("sku")}
    start_stock = {s: float(g.sort_values("day")["demand"].tail(7).sum()) for s, g in pre.groupby("sku")}

    # Stockora forecasts once per week, using only data available at that time.
    fc_model = Forecaster().fit(pre, step=4)
    forecasts = {}
    running = pre.copy()
    for k in range(0, window_days, REVIEW):
        origin = start + pd.Timedelta(days=k) - pd.Timedelta(days=1)
        known = pd.concat([running, win[win["day"] <= origin]])
        forecasts[k] = fc_model.predict(known, origin.date())
    sigma = {k: float(v) for k, v in fc_model.sigma.items()}

    gut = _run(demand, start_stock, gut_feel_policy(), items)
    smart = _run(demand, start_stock, stockora_policy(forecasts, sigma, items, Z), items)
    lean = _run(demand, start_stock, stockora_policy(forecasts, sigma, items, 0.5), items)
    return {"window": f"{start.date()} to {pd.Timestamp(days[-1]).date()}", "days": window_days,
            "gut_feel": gut.as_dict(), "stockora": smart.as_dict(), "stockora_lean": lean.as_dict(),
            "note": "Synthetic demand (simulator.py). Same demand, start stock, lead times and shelf lives."}
