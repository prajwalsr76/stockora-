"""Forecasts for shops (or products) with little history.

A real shop starts with zero history in Stockora. Until a product has enough
data for the ML model, its forecast blends:
  * its own recent sales (weight grows with days observed, full weight at 28 days)
  * a prior: the typical daily demand for that product (reference catalog) or its
    category, scaled to this shop's size (estimated from products that do have data)
and applies the festival and season calendar. Stockout days are ignored.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median

import pandas as pd

from ..india import festival_multiplier, load_catalog, season_multiplier
from .model import HORIZON

FULL_WEIGHT_DAYS = 28


def _priors() -> tuple[dict[str, float], dict[str, float]]:
    cat = load_catalog()
    by_sku = {c.sku: c.base_daily_demand for c in cat}
    by_cat: dict[str, list[float]] = {}
    for c in cat:
        by_cat.setdefault(c.category, []).append(c.base_daily_demand)
    return by_sku, {k: median(v) for k, v in by_cat.items()}


def _calendar_mult(day: date, category: str) -> float:
    return festival_multiplier(day, category) * season_multiplier(day, category)


def coldstart_forecasts(hist: pd.DataFrame, products: list[dict], today: date
                        ) -> tuple[dict[int, list[float]], dict[int, float], dict[int, int]]:
    """Returns (forecasts per product_id, sigma per product_id, days observed per product_id).

    hist columns: product_id, day, sales, stockout, category. products: dicts with id, sku, category.
    """
    by_sku, by_cat = _priors()
    stats: dict[int, tuple[float, int]] = {}
    if len(hist):
        h = hist.copy()
        h["day"] = pd.to_datetime(h["day"]).dt.date
        for pid, g in h.groupby("product_id"):
            g = g.sort_values("day")
            valid = g[~g["stockout"]].tail(FULL_WEIGHT_DAYS)
            if len(valid):
                cat = g["category"].iloc[0]
                # de-seasonalise: divide each day by its calendar multiplier
                adj = [s / _calendar_mult(d, cat) for s, d in zip(valid["sales"], valid["day"])]
                stats[int(pid)] = (sum(adj) / len(adj), len(valid))

    def prior(p: dict) -> float:
        return by_sku.get(p.get("sku") or "", by_cat.get(p["category"], 1.0))

    ratios = [stats[p["id"]][0] / prior(p) for p in products
              if p["id"] in stats and stats[p["id"]][1] >= 7 and prior(p) > 0]
    size = median(ratios) if ratios else 1.0
    size = min(max(size, 0.1), 10.0)

    forecasts, sigma, observed = {}, {}, {}
    for p in products:
        level_obs, n = stats.get(p["id"], (0.0, 0))
        w = min(n / FULL_WEIGHT_DAYS, 1.0)
        level = w * level_obs + (1 - w) * prior(p) * size
        days = [today + timedelta(days=h) for h in range(HORIZON)]
        forecasts[p["id"]] = [round(level * _calendar_mult(d, p["category"]), 3) for d in days]
        # Poisson-like uncertainty, inflated while we know little about this product
        sigma[p["id"]] = round(math.sqrt(max(level, 0.05)) * (1.2 + 0.8 * (1 - w)), 3)
        observed[p["id"]] = n
    return forecasts, sigma, observed
