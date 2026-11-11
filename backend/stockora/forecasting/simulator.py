"""Store demand simulator (clearly synthetic; used until a real store has history).

Real kirana sales data is not publicly available, so demand is generated from
documented Indian retail patterns:
  * weekday shape (Sunday/Saturday peaks)
  * salary-week uplift (days 1-5 of the month)
  * seasons (summer beverages, monsoon instant food, June school reopening)
  * festival ramps from reference/festivals.csv (Diwali, Eid, Holi, Navratri, ...)
  * slow trend + Gamma-Poisson (over-dispersed) noise, as in real retail counts

Every number produced here is labelled synthetic in reports and evals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..india import CatalogItem, festival_multiplier, is_salary_window, load_catalog, season_multiplier

WEEKDAY = np.array([0.94, 0.92, 0.95, 0.97, 1.02, 1.12, 1.22])  # Mon..Sun, mean ~1.02


@dataclass
class StoreProfile:
    name: str = "Sharma General Store"
    city: str = "Nashik"
    state_code: str = "27"
    size: float = 1.0           # 1.0 = typical mid-size kirana
    dispersion: float = 4.0     # Gamma shape; lower = noisier
    seed: int = 7


def demand_frame(start: date, end: date, profile: StoreProfile | None = None,
                 catalog: tuple[CatalogItem, ...] | None = None) -> pd.DataFrame:
    """True daily demand per SKU (before any stockouts)."""
    profile = profile or StoreProfile()
    catalog = catalog or load_catalog()
    rng = np.random.default_rng(profile.seed)
    days = pd.date_range(start, end, freq="D")
    n = len(days)
    rows = []
    for item in catalog:
        sku_scale = profile.size * rng.lognormal(0, 0.25)
        trend = 1 + rng.normal(0.0, 0.08) * np.arange(n) / 365
        mu = np.empty(n)
        for i, ts in enumerate(days):
            d = ts.date()
            m = item.base_daily_demand * sku_scale * WEEKDAY[d.weekday()]
            m *= 1.12 if is_salary_window(d) else 1.0
            m *= season_multiplier(d, item.category) * festival_multiplier(d, item.category)
            mu[i] = max(m * trend[i], 0.01)
        lam = rng.gamma(profile.dispersion, mu / profile.dispersion)
        demand = rng.poisson(lam)
        rows.append(pd.DataFrame({"day": days, "sku": item.sku, "category": item.category,
                                  "expected": mu, "demand": demand.astype(float)}))
    return pd.concat(rows, ignore_index=True)


def gut_feel_sales(demand: pd.DataFrame, review_days: int = 7, lead_time: int = 2,
                   seed: int = 11) -> pd.DataFrame:
    """Observed sales when the owner reorders by gut feel, once a week.

    The owner looks at the last two weeks (remembering days he ran out), orders
    "about a week and a bit" of stock, and ignores upcoming festivals. This yields
    censored sales and stockout flags, i.e. what a real store's records look like.
    """
    rng = np.random.default_rng(seed)
    out = []
    for sku, g in demand.groupby("sku", sort=False):
        g = g.sort_values("day")
        d = g["demand"].to_numpy()
        stock = d[:10].sum()
        pipeline: dict[int, float] = {}
        sales = np.zeros_like(d)
        stockout = np.zeros(len(d), dtype=bool)
        for t in range(len(d)):
            stock += pipeline.pop(t, 0.0)
            sales[t] = min(stock, d[t])
            stockout[t] = d[t] > stock
            stock -= sales[t]
            if t % review_days == 0 and t >= 7:
                lo = max(0, t - 14)
                ok = ~stockout[lo:t]
                est = sales[lo:t][ok].mean() if ok.any() else sales[lo:t].max() * 1.2
                cover = (review_days + lead_time) * rng.uniform(0.85, 1.2)
                order = max(0.0, est * cover - stock)
                pipeline[t + lead_time] = pipeline.get(t + lead_time, 0) + float(np.ceil(order))
        out.append(g.assign(sales=sales, stockout=stockout))
    return pd.concat(out, ignore_index=True)


def default_history(end: date | None = None, days: int = 640,
                    profile: StoreProfile | None = None) -> pd.DataFrame:
    end = end or date.today()
    start = end - timedelta(days=days - 1)
    return gut_feel_sales(demand_frame(start, end, profile))
