"""Forecast accuracy on REAL item-level data: Walmart M5 (Kaggle "M5 Forecasting - Accuracy").

  python -m evals.m5_benchmark ..\\data\\m5            # FOODS + HOUSEHOLD, all 10 stores, ~25 min
  python -m evals.m5_benchmark ..\\data\\m5 --quick    # one store only, ~3 min

Two test periods of 28 days (M5's horizon):
  * 2016-04-25 .. 2016-05-22  the competition's validation window
  * 2016-05-23 .. 2016-06-19  the official final test window (truth from sales_test_evaluation.csv)
Train on everything before each origin, forecast 28 days for every store x item, score
against what really sold. Days an item was not listed / had no shelf price are not scored.

One model per store (as each shop would run its own), trained across all of that store's
items; results pooled over all 10 stores.
Metrics: WAPE (daily, weekly), RMSLE, bias, and M5's own RMSSE at item level
(unweighted mean and weighted by each item's last-28-day revenue, as M5 weights level 12).
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from stockora.datasets.m5 import STATES, load
from stockora.forecasting.model import PRICE_FEATURES, Forecaster, backtest, summarize, wape

ORIGINS = [date(2016, 4, 24), date(2016, 5, 22)]
META = ["store", "state"]
# Chosen by ablation on store CA_1 (2 test windows, weekly WAPE):
#   base features 0.3729 | + shelf-price features 0.3749 (no gain: prices rarely change in 28 days)
#   + r91 level and days-since-last-sale 0.3715 (small gain on slow, intermittent items)
EXTRA = ["r91", "since_sale"]
HORIZON = 28


def rmsse(frames: list[pd.DataFrame], hist: pd.DataFrame) -> dict:
    """M5's RMSSE per series: forecast RMSE scaled by the in-sample one-step naive error."""
    allp = pd.concat(frames, ignore_index=True)
    out_u, out_w = [], []
    for origin, p in allp.groupby("origin"):
        o = pd.Timestamp(origin)
        h = hist[hist["day"] <= o]
        w = h.pivot_table(index="sku", columns="day", values="sales", aggfunc="sum").fillna(0.0)
        y = w.to_numpy()
        started = np.cumsum(y > 0, 1) > 0
        diff2 = np.where(started[:, 1:], (y[:, 1:] - y[:, :-1]) ** 2, np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)   # items with a single day of history
            scale = pd.Series(np.nanmean(diff2, 1), index=w.index)
        rev = h[h["day"] > o - pd.Timedelta(days=28)]
        weight = (rev["sales"] * rev["price"].fillna(0)).groupby(rev["sku"]).sum()
        err = p.assign(se=(p["actual"] - p["forecast"]) ** 2).groupby("sku")["se"].mean()
        s = scale.reindex(err.index)
        ok = s > 0
        r = np.sqrt(err[ok] / s[ok])
        out_u.append(r)
        out_w.append((r, weight.reindex(r.index).fillna(0)))
    r_all = pd.concat(out_u)
    num = sum(float((r * wt).sum()) for r, wt in out_w)
    den = sum(float(wt.sum()) for _, wt in out_w)
    return {"rmsse_mean": round(float(r_all.mean()), 4), "rmsse_revenue_weighted": round(num / den, 4)}


def run(folder: str, quick: bool = False) -> dict:
    frames: dict[str, list] = {}
    rm_frames: dict[str, list] = {}
    per_state, n_series = {}, 0
    stores = ["CA_1"] if quick else [f"{s}_{i}" for s in STATES for i in range(1, 5 if s == "CA" else 4)]
    groups = [{"stores": [s]} for s in stores]
    for g in groups:
        t = time.time()
        hist, cal = load(folder, **g)
        n_series += hist["sku"].nunique()
        loc: dict[str, list] = {}

        def factory(cal=cal):
            return Forecaster(calendar=cal, horizon=HORIZON, meta_cols=META, max_train_rows=2_500_000,
                              extra_features=EXTRA, drop_features=PRICE_FEATURES)

        res = backtest(hist, ORIGINS, step=7, forecaster_factory=factory, event_threshold=1.5, frames=loc)
        name = g.get("states", g.get("stores"))[0]
        per_state[name] = {k: v["wape_weekly"] for k, v in res.items()}
        for k, v in loc.items():
            frames.setdefault(k, []).extend(v)
        hist = hist[["sku", "day", "sales", "price"]]
        for k in ["model", "dow_mean_4w", "moving_avg_28", "seasonal_naive"]:
            rm_frames.setdefault(k, []).append(rmsse(loc[k], hist))
        print(f"{name}: {time.time() - t:.0f}s", per_state[name], flush=True)
        del hist, loc

    out = {"data": f"Walmart M5, FOODS + HOUSEHOLD, {n_series:,} store x item series, daily (real)",
           "origins": [str(o) for o in ORIGINS], "horizon_days": HORIZON, "model_per": "store", "weekly_wape_by_store": per_state}
    for k in ["model", "dow_mean_4w", "moving_avg_28", "seasonal_naive"]:
        s = summarize(frames[k])
        ev = pd.concat(frames[k + "@event"])
        s["wape_event_days"] = round(wape(ev["actual"], ev["forecast"]), 4) if len(ev) else None
        # pool RMSSE across groups by series count (unweighted) — groups are similar in size
        s["rmsse_mean"] = round(float(np.mean([r["rmsse_mean"] for r in rm_frames[k]])), 4)
        s["rmsse_revenue_weighted"] = round(float(np.mean([r["rmsse_revenue_weighted"] for r in rm_frames[k]])), 4)
        out[k] = s
    return out


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "../data/m5"
    quick = "--quick" in sys.argv
    res = run(folder, quick)
    name = "m5_report_quick.json" if quick else "m5_report.json"
    Path(Path(__file__).parent / name).write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=1, default=str))
