"""Ordering policy on REAL demand: replay Favorita daily sales through two ordering rules.

Same real demand, starting stock, lead times and shelf lives for both:
  * gut feel   - reorder from the recent average of what sold (what most shops do)
  * Stockora - order up to forecast(lead + review) + safety stock, capped by shelf life

Favorita has no prices, so results are in units: stockout days, share of demand served,
share of received stock that expired, and days of stock held.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from stockora.datasets.favorita import KIRANA_FAMILIES, load
from stockora.forecasting.model import Forecaster
from stockora.forecasting.policy_sim import REVIEW, _run, gut_feel_policy, stockora_policy
from stockora.ordering_rules import shelf_cap_days

SHELF = {"DAIRY": 10, "BREAD/BAKERY": 4, "EGGS": 14, "PRODUCE": 5, "PREPARED FOODS": 3, "FROZEN FOODS": 90}


def run(folder: str, stores: int = 12, window_days: int = 56, end: date = date(2017, 8, 15),
        z_levels=(1.0, 1.5, 2.0, 2.5, 3.0), perishable_z=None) -> dict:
    hist, cal = load(folder, families=list(KIRANA_FAMILIES))
    start = pd.Timestamp(end) - pd.Timedelta(days=window_days - 1)
    pre = hist[hist["day"] < start]
    win = hist[(hist["day"] >= start) & (hist["day"] <= pd.Timestamp(end))]
    keep_stores = sorted(hist["store"].unique(), key=int)[:stores]
    win_keys = sorted(win.loc[win["store"].isin(keep_stores), "sku"].unique())
    active = pre[pre["day"] >= start - pd.Timedelta(days=28)].groupby("sku")["sales"].sum()
    win_keys = [k for k in win_keys if active.get(k, 0) > 0]

    fc = Forecaster(calendar=cal, horizon=16, meta_cols=["store", "city", "store_type", "cluster"],
                    max_train_rows=1_000_000).fit(pre, step=7)
    forecasts = {}
    for k in range(0, window_days, REVIEW):
        origin = start + pd.Timedelta(days=k) - pd.Timedelta(days=1)
        known = hist[hist["day"] <= origin]
        f = fc.predict(known, origin.date())
        forecasts[k] = f[f["sku"].isin(win_keys)]
    sigma = {k: v for k, v in fc.sigma.items() if k in win_keys}

    demand = {k: g.sort_values("day")["sales"].to_numpy(float) for k, g in win[win["sku"].isin(win_keys)].groupby("sku")}
    # starting stock: a few days of recent sales, never more than the item can hold before expiring
    start_stock = {k: float(g.sort_values("day")["sales"].tail(14).mean() *
                            min(4.0, shelf_cap_days(SHELF.get(k.split("|", 1)[1], 180))))
                   for k, g in pre[pre["sku"].isin(win_keys)].groupby("sku")}
    items = {k: SimpleNamespace(shelf_life_days=SHELF.get(k.split("|", 1)[1], 180), mrp=1.0, cost_price=0.75)
             for k in win_keys}

    def summarise(res, total_received_proxy):
        return {"stockout_day_rate": round(res.stockout_day_rate, 4), "fill_rate": round(res.fill_rate, 4),
                "expired_units": round(res.expired_value / 0.75, 0),
                "avg_stock_units": round(res.avg_inventory_value / 0.75, 0)}

    out = {"window": f"{start.date()} to {end}", "series": len(win_keys), "stores": len(keep_stores),
           "families": sorted({k.split('|', 1)[1] for k in win_keys})}
    perish = [k for k in win_keys if k.split("|", 1)[1] in SHELF and SHELF[k.split("|", 1)[1]] <= 14]
    for label, keys in (("all", win_keys), ("perishables", perish)):
        d = {k: demand[k] for k in keys}
        s0 = {k: start_stock.get(k, 0.0) for k in keys}
        it = {k: items[k] for k in keys}
        g = _run(d, s0, gut_feel_policy(), it)
        total = sum(v.sum() for v in d.values())
        runs = [("gut_feel", g)]
        for z in z_levels:
            runs.append((f"stockora_z{z}", _run(d, s0, stockora_policy(
                {o: f[f["sku"].isin(keys)] for o, f in forecasts.items()}, sigma, it, z), it)))
        res = {}
        for name, r in runs:
            res[name] = summarise(r, total)
            res[name]["expired_share_of_demand"] = round(res[name]["expired_units"] / total, 4)
            res[name]["days_of_stock_held"] = round(res[name]["avg_stock_units"] / (total / window_days), 2)
        out[label] = res
    return out


if __name__ == "__main__":
    res = run(sys.argv[1] if len(sys.argv) > 1 else "../data/favorita")
    Path(Path(__file__).parent / "real_policy_report.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=1, default=str))
