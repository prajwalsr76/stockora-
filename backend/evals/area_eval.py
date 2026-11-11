"""Evaluate privacy-preserving area trends on real Favorita data (cities = areas, stores = shops).

Questions answered, for several privacy levels (epsilon):
  1. Accuracy  - does the published (noisy, k-anonymous) area trend point the same way as the
                 true average change of shops in that area?
  2. Usefulness- does this week's area trend help predict a shop's own change next week?
  3. Coverage  - how many area/category/weeks can be published at k shops?
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from stockora.area_trends import aggregate, local_change, make_report
from stockora.datasets.favorita import KIRANA_FAMILIES, load


def weekly_changes(hist: pd.DataFrame, weeks: int = 26) -> pd.DataFrame:
    h = hist[~hist["exclude"]].copy()
    h["week"] = h["day"].dt.to_period("W-SUN")
    w = h.groupby(["city", "store", "category", "week"])["sales"].sum().reset_index()
    w = w.sort_values("week")
    w["base"] = w.groupby(["store", "category"])["sales"].transform(lambda s: s.shift(1).rolling(4, min_periods=4).mean())
    w = w.dropna(subset=["base"])
    last = sorted(w["week"].unique())[-weeks:]
    w = w[w["week"].isin(last)].copy()
    w["change"] = [local_change(a, b) for a, b in zip(w["sales"], w["base"])]
    return w.dropna(subset=["change"])


def run(folder: str, epsilons=(0.5, 1.0, 2.0, 4.0, None), ks=(3, 5)) -> dict:
    hist, _ = load(folder, families=list(KIRANA_FAMILIES))
    w = weekly_changes(hist)
    w["week"] = w["week"].astype(str)
    truth = w.groupby(["city", "week", "category"])["change"].mean()
    nxt = w.sort_values("week").copy()
    nxt["next_change"] = nxt.groupby(["store", "category"])["change"].shift(-1)
    out = {"areas": int(w["city"].nunique()), "shops": int(w["store"].nunique()), "weeks": int(w["week"].nunique()),
           "shops_per_area": w.groupby("city")["store"].nunique().sort_values(ascending=False).to_dict(), "results": []}
    rng = np.random.default_rng(0)
    for k in ks:
        for eps in epsilons:
            reports = [make_report(c, wk, cat, ch, eps if eps else 1e9, rng)
                       for c, wk, cat, ch in zip(w["city"], w["week"], w["category"], w["change"])]
            trends = aggregate(reports, k=k)
            pub = pd.DataFrame([t.__dict__ for t in trends])
            if pub.empty:
                out["results"].append({"k": k, "epsilon": eps, "published": 0})
                continue
            pub["true"] = [truth.get((a, wk, c), np.nan) for a, wk, c in zip(pub["area"], pub["week"], pub["category"])]
            clear = pub[pub["true"].abs() > 0.05]
            acc = float((np.sign(clear["median_change"]) == np.sign(clear["true"])).mean()) if len(clear) else None
            m = nxt.merge(pub.rename(columns={"area": "city"})[["city", "week", "category", "median_change"]],
                          on=["city", "week", "category"]).dropna(subset=["next_change"])
            out["results"].append({
                "k": k, "epsilon": eps if eps else "no noise",
                "published_cells": int(len(pub)), "possible_cells": int(len(truth)),
                "direction_accuracy_on_clear_moves": round(acc, 3) if acc is not None else None,
                "corr_area_trend_vs_shop_next_week": round(float(m["median_change"].corr(m["next_change"])), 3),
                "corr_shop_own_this_week_vs_next_week": round(float(m["change"].corr(m["next_change"])), 3),
            })
    return out


if __name__ == "__main__":
    res = run(sys.argv[1] if len(sys.argv) > 1 else "../data/favorita")
    Path(Path(__file__).parent / "area_report.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=1, default=str))
