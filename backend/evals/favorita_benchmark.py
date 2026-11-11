"""Forecast accuracy on REAL data: Corporacion Favorita (Kaggle "Store Sales - Time Series Forecasting").

  python -m evals.favorita_benchmark ..\\data\\favorita            # 3 test periods, ~5 min
  python -m evals.favorita_benchmark ..\\data\\favorita --ablation # also feature ablation (~20 min)

Each test period: train on everything before the origin, forecast the next 16 days
(Kaggle's horizon) for all 1,782 store x family series, score against what really sold.
Closed days are excluded from scoring.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from stockora.datasets.favorita import load
from stockora.forecasting.model import Forecaster, backtest

ORIGINS = [date(2017, 5, 31), date(2017, 6, 30), date(2017, 7, 30)]
META = ["store", "city", "store_type", "cluster"]


def run(folder: str, ablation: bool = False) -> dict:
    hist, cal = load(folder)
    variants = {"final": []}
    if ablation:
        variants.update({"with_lag1_r3": "ADD", "no_last_year": ["last_year"]})
    out = {"data": "Favorita, 54 stores, 1,782 store x family series, daily, 2013-01-01 to 2017-08-15 (real)",
           "origins": [str(o) for o in ORIGINS], "horizon_days": 16}
    for name, drop in variants.items():
        def factory(drop=drop):
            f = Forecaster(calendar=cal, horizon=16, meta_cols=META, max_train_rows=1_000_000,
                           drop_features=[] if drop == "ADD" else drop)
            return f
        if drop == "ADD":
            import stockora.forecasting.model as m
            saved = list(m.BASE_FEATURES)
            m.BASE_FEATURES[:] = saved[:4] + ["lag1", "r3"] + saved[4:]
            res = backtest(hist, ORIGINS, step=7, forecaster_factory=factory, event_threshold=1.5)
            m.BASE_FEATURES[:] = saved
        else:
            res = backtest(hist, ORIGINS, step=7, forecaster_factory=factory, event_threshold=1.5)
        out[name] = res
    return out


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "../data/favorita"
    res = run(folder, "--ablation" in sys.argv)
    Path(Path(__file__).parent / "favorita_report.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=1, default=str))
