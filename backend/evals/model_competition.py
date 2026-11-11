"""Model competition: which forecaster is best across DIFFERENT real datasets?

  python -m evals.model_competition <favorita_folder> <m5_folder> <rohlik_folder>   # hours; resumable
  python -m evals.model_competition <fav> <m5> <rohlik> --only=favorita            # or --only=m5:CA_1, --only=rohlik

Datasets (none of them Indian): Favorita (Ecuador, store x product family), Walmart M5 (USA, single
items, 3 stores), Rohlik (Czech Republic / Hungary online grocer, item x warehouse, 3 warehouses).

Candidates (all see exactly the same history at each forecast origin):
  local / classical   ma28, dow_mean_4w, ses_dow (weekday-adjusted exponential smoothing,
                      alpha chosen per series), tsb_dow (intermittent demand)
  global ML           gbm (current Stockora model), gbm_plus (+ 91-day level, days since
                      last sale), gbm_plus_l2 (squared loss), gbm_plus_big (larger trees),
                      gbm_bag (3 feature-subsampled models, averaged)
                      lgb_poisson, lgb_tweedie (LightGBM), xgb_poisson, xgb_tweedie (XGBoost), all on the
                      same features and training rows; settings fixed before any run (no tuning)
  ensembles           averages of global ML and classical models

Protocol, to avoid tuning to one dataset:
  * validation windows pick the winner, test windows (later, never used for choosing) report it
  * the winner must be ONE configuration for all datasets
  * score = mean over datasets of weekly WAPE relative to the 28-day moving average
  * Rohlik days with availability < 1 (sold out during the day) are censored: not scored
"""
from __future__ import annotations

import gc
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from stockora.forecasting.classical import ses_dow, tsb_dow
from stockora.forecasting.model import Forecaster, wape

ROOT = Path(__file__).parent
CACHE = ROOT / ".competition_cache_v2"    # one file per (dataset, window): reruns skip finished work

FAVORITA = {"horizon": 16, "meta": ["store", "city", "store_type", "cluster"], "rows": 1_000_000,
            "val": ["2017-06-14", "2017-06-30"], "test": ["2017-07-15", "2017-07-30"]}
M5 = {"horizon": 28, "meta": ["store", "state"], "rows": 1_500_000, "stores": ["CA_1", "TX_1", "WI_1"],
      "val": ["2016-04-24"], "test": ["2016-05-22"]}
# Rohlik: 2 validation then 2 later test windows of 14 days at the end of the training file.
ROHLIK = {"horizon": 14, "meta": ["warehouse", "l2"], "rows": 1_500_000,
          "val": ["2024-04-07", "2024-04-21"], "test": ["2024-05-05", "2024-05-19"]}
PLUS = ["r91", "since_sale"]
# LightGBM / XGBoost settings, fixed before the first run and never changed after seeing results.
LGB = dict(n_estimators=600, learning_rate=0.05, num_leaves=63, min_child_samples=50, subsample=0.8, subsample_freq=1,
           colsample_bytree=0.8, reg_lambda=1.0, verbose=-1, n_jobs=2, random_state=0)
from stockora.forecasting.model import TWEEDIE_POWER, XGB_PARAMS as XGB  # noqa: E402  (one source of truth)
ENSEMBLES = {
    "ens_gbm_ses": {"gbm_plus": 0.5, "ses_dow": 0.5},
    "ens_gbm_ses_75": {"gbm_plus": 0.75, "ses_dow": 0.25},
    "ens_bag_ses_75": {"gbm_bag": 0.75, "ses_dow": 0.25},
    "ens_bag_tsb_75": {"gbm_bag": 0.75, "tsb_dow": 0.25},
    "ens_bag_ses": {"gbm_bag": 0.5, "ses_dow": 0.5},
    "ens_bag_ses_tsb": {"gbm_bag": 0.5, "ses_dow": 0.25, "tsb_dow": 0.25},
    "ens_gbms": {"gbm_plus": 1 / 3, "gbm_plus_l2": 1 / 3, "gbm_plus_big": 1 / 3},
    "ens_all": {"gbm_bag": 0.4, "gbm_plus_l2": 0.2, "gbm_plus_big": 0.2, "ses_dow": 0.2},
    "ens_lgb_ses_75": {"lgb_tweedie": 0.75, "ses_dow": 0.25},
    "ens_xgb_ses_75": {"xgb_tweedie": 0.75, "ses_dow": 0.25},
    "ens_bag_lgb_ses": {"gbm_bag": 0.375, "lgb_tweedie": 0.375, "ses_dow": 0.25},
    "ens_boost3_ses": {"gbm_bag": 0.25, "lgb_tweedie": 0.25, "xgb_tweedie": 0.25, "ses_dow": 0.25},
}
NEW_LIBS = ("lgb_poisson", "lgb_tweedie", "xgb_poisson", "xgb_tweedie")


def _wide(hist: pd.DataFrame, origin: pd.Timestamp, keys: list[str]):
    h = hist[hist["day"] <= origin]
    if "stockout" in h:              # censored days are not a level for the local models either
        h = h.assign(exclude=h["exclude"].astype(bool) | h["stockout"].astype(bool))
    y = h.pivot_table(index="sku", columns="day", values="sales", aggfunc="sum").reindex(keys)
    ex = h.pivot_table(index="sku", columns="day", values="exclude", aggfunc="max").reindex(keys)
    days = pd.DatetimeIndex(y.columns)
    yv = y.to_numpy(float)
    valid = ~(ex.fillna(True).to_numpy(bool)) & np.isfinite(yv)
    return np.nan_to_num(yv), valid, days


def candidates(hist: pd.DataFrame, origin: str, cfg: dict, calendar) -> tuple[pd.DataFrame, dict]:
    o = pd.Timestamp(origin)
    t0 = time.time()
    fc = Forecaster(calendar=calendar, horizon=cfg["horizon"], meta_cols=cfg["meta"], max_train_rows=cfg["rows"],
                    compute_sigma=False, keep_train=True, learner="sk_bag",
                    extra_features=[], bag=1, ses_weight=0.0      # candidates are built from this base
                    ).fit(hist, train_end=o.date(), step=7)
    rows = fc.predict(hist, o.date(), raw_rows=True)
    tr = fc.train_
    base, plus = fc.features, fc.features + PLUS

    def gbm(feats, **kw):
        m = fc.make_gbm(**kw).fit(tr[feats], tr["y"])
        return np.maximum(m.predict(rows[feats]), 0)

    scale, cap = rows["scale"].to_numpy(), rows["cap"].to_numpy()
    raw = {"gbm": np.maximum(fc.model.predict(rows[base]), 0), "gbm_plus": gbm(plus),
           "gbm_plus_l2": gbm(plus, loss="squared_error"),
           "gbm_plus_big": gbm(plus, max_leaf_nodes=127, min_samples_leaf=100, max_iter=600, learning_rate=0.05)}
    bag = [raw["gbm_plus"], gbm(plus, max_features=0.7, random_state=1), gbm(plus, max_features=0.7, random_state=2)]
    raw["gbm_bag"] = np.mean(bag, axis=0)
    raw.update(boosters(tr, rows, plus, ["cat", *cfg["meta"]]))
    preds = {k: np.clip(v * scale, 0, cap) for k, v in raw.items()}
    fit_s = time.time() - t0

    keys = sorted(rows["sku"].unique())
    y, valid, days = _wide(hist, o, keys)
    H = cfg["horizon"]
    future = pd.date_range(o + pd.Timedelta(days=1), periods=H)
    fd = future.dayofweek.to_numpy()
    dows = days.dayofweek.to_numpy()
    kpos = {k: i for i, k in enumerate(keys)}
    si = rows["sku"].map(kpos).to_numpy()
    hi = rows["h"].to_numpy() - 1
    v = np.where(valid, y, 0.0)
    for name, mat in {
        "ses_dow": ses_dow(y, valid, dows, fd),
        "tsb_dow": tsb_dow(y, valid, dows, fd),
        "ma28": np.repeat((v[:, -28:].sum(1) / np.maximum(valid[:, -28:].sum(1), 1))[:, None], H, 1),
    }.items():
        preds[name] = mat[si, hi]
    dm = np.zeros((len(keys), H))
    for j, d in enumerate(fd):
        cols = np.where(dows == d)[0][-4:]
        dm[:, j] = v[:, cols].sum(1) / np.maximum(valid[:, cols].sum(1), 1)
    preds["dow_mean_4w"] = dm[si, hi]
    for name, w in ENSEMBLES.items():
        preds[name] = sum(wt * preds[k] for k, wt in w.items())

    # RMSSE scale: mean squared one-step change since each series' first sale
    started = np.cumsum(v > 0, 1) > 0
    d2 = np.where(started[:, 1:] & valid[:, 1:] & valid[:, :-1], np.diff(y, axis=1) ** 2, np.nan)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            rscale = np.nanmean(d2, 1)
    frame = pd.DataFrame({"sku": rows["sku"].to_numpy(), "day": pd.to_datetime(rows["day"]), "h": rows["h"].to_numpy(),
                          "rscale": rscale[si], **preds})
    truth = hist[(hist["day"] > o) & (hist["day"] <= o + pd.Timedelta(days=H)) & ~hist["exclude"].astype(bool)]
    if "stockout" in truth:          # sold out during the day: sales understate demand, not scored
        truth = truth[~truth["stockout"].astype(bool)]
    frame = frame.merge(truth[["sku", "day", "sales"]].rename(columns={"sales": "actual"}), on=["sku", "day"])
    frame["origin"] = origin
    return frame, {"fit_seconds": round(fit_s)}


def boosters(tr: pd.DataFrame, rows: pd.DataFrame, feats: list[str], cats: list[str]) -> dict:
    """LightGBM and XGBoost (Poisson and Tweedie losses) on exactly the gbm_plus features and rows."""
    import lightgbm as lgb
    import xgboost as xgb
    cats = [c for c in cats if c in feats]
    X, Xp, y = tr[feats], rows[feats], tr["y"].to_numpy()
    out = {}
    for name, obj in (("lgb_poisson", dict(objective="poisson")),
                      ("lgb_tweedie", dict(objective="tweedie", tweedie_variance_power=TWEEDIE_POWER))):
        m = lgb.LGBMRegressor(**LGB, **obj)
        m.fit(X, y, categorical_feature=cats)
        out[name] = np.maximum(m.predict(Xp), 0)
    for name, obj in (("xgb_poisson", dict(objective="count:poisson")),
                      ("xgb_tweedie", dict(objective="reg:tweedie", tweedie_variance_power=TWEEDIE_POWER))):
        m = xgb.XGBRegressor(**XGB, **obj)        # category codes used as ordered numbers
        m.fit(X, y)
        out[name] = np.maximum(m.predict(Xp), 0)
    return out


def scores(frame: pd.DataFrame, names: list[str]) -> dict:
    out = {}
    blk = (frame["h"] - 1) // 7
    for n in names:
        wk = frame.assign(b=blk).groupby(["origin", "sku", "b"])[["actual", n]].sum()
        se = (frame["actual"] - frame[n]) ** 2
        per = pd.DataFrame({"sku": frame["sku"], "origin": frame["origin"], "se": se, "s": frame["rscale"]})
        g = per.groupby(["origin", "sku"]).agg(se=("se", "mean"), s=("s", "first"))
        g = g[g["s"] > 0]
        out[n] = {"wape_daily": round(wape(frame["actual"], frame[n]), 4),
                  "wape_weekly": round(wape(wk["actual"], wk[n]), 4),
                  "rmsse": round(float(np.sqrt(g["se"] / g["s"]).mean()), 4),
                  "bias": round(float(frame[n].sum() / frame["actual"].sum() - 1), 4)}
    return out


def _cached(name: str, make) -> pd.DataFrame:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{name}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    t = time.time()
    f = make()
    f.to_pickle(path)
    print({"unit": name, "seconds": round(time.time() - t)}, flush=True)
    return f


def run(fav_folder: str, m5_folder: str, rohlik_folder: str, only: str | None = None) -> dict | None:
    """only='favorita', 'm5:CA_1' or 'rohlik' computes just that part (to split long runs); None = everything + scoring."""
    from stockora.datasets import favorita, m5, rohlik
    frames = {"favorita": [], "m5": [], "rohlik": []}
    info = []
    if only in (None, "favorita"):
        hist = cal = None
        for origin in FAVORITA["val"] + FAVORITA["test"]:
            def make(origin=origin):
                nonlocal hist, cal
                if hist is None:
                    hist, cal = favorita.load(fav_folder)
                f, _ = candidates(hist, origin, FAVORITA, cal)
                f["split"] = "val" if origin in FAVORITA["val"] else "test"
                return f
            frames["favorita"].append(_cached(f"favorita_{origin}", make))
        del hist
        gc.collect()
    for store in M5["stores"]:
        if only not in (None, f"m5:{store}"):
            continue
        hist = cal = None
        for origin in M5["val"] + M5["test"]:
            def make(origin=origin, store=store):
                nonlocal hist, cal
                if hist is None:
                    hist, cal = m5.load(m5_folder, stores=[store])
                f, _ = candidates(hist, origin, M5, cal)
                f["split"] = "val" if origin in M5["val"] else "test"
                f["origin"] = f"{store}@{origin}"
                return f
            frames["m5"].append(_cached(f"m5_{store}_{origin}", make))
        del hist
        gc.collect()
    if only in (None, "rohlik"):
        hist = cal = None
        for origin in ROHLIK["val"] + ROHLIK["test"]:
            def make(origin=origin):
                nonlocal hist, cal
                if hist is None:
                    hist, cal = rohlik.load(rohlik_folder)
                f, _ = candidates(hist, origin, ROHLIK, cal)
                f["split"] = "val" if origin in ROHLIK["val"] else "test"
                return f
            frames["rohlik"].append(_cached(f"rohlik_{origin}", make))
        del hist
        gc.collect()
    if only is not None:
        return None

    names = [c for c in frames["favorita"][0].columns
             if c not in ("sku", "day", "h", "rscale", "actual", "origin", "split")]
    res = {"runs": info, "datasets": {}}
    for ds, fl in frames.items():
        allf = pd.concat(fl, ignore_index=True)
        res["datasets"][ds] = {split: scores(allf[allf["split"] == split], names) for split in ("val", "test")}

    def rel(split):
        return {n: float(np.mean([res["datasets"][d][split][n]["wape_weekly"] / res["datasets"][d][split]["ma28"]["wape_weekly"]
                                  for d in frames])) for n in names}
    val_rel, test_rel = rel("val"), rel("test")
    ranking = sorted(names, key=lambda n: val_rel[n])
    winner = ranking[0]
    res["selection"] = {"metric": "mean over datasets of weekly WAPE / ma28 weekly WAPE (lower is better)",
                        "ranking_on_validation": [{"model": n, "val": round(val_rel[n], 4), "test": round(test_rel[n], 4)}
                                                  for n in ranking],
                        "winner": winner}
    # Where the winner loses: every (dataset, split, metric) where another candidate beat it.
    losses = []
    for d in frames:
        for split in ("val", "test"):
            sc = res["datasets"][d][split]
            for metric in ("wape_weekly", "wape_daily", "rmsse"):
                better = sorted((n for n in names if sc[n][metric] < sc[winner][metric]), key=lambda n: sc[n][metric])
                if better:
                    losses.append({"dataset": d, "split": split, "metric": metric, "winner": sc[winner][metric],
                                   "beaten_by": [{"model": n, "value": sc[n][metric]} for n in better[:5]],
                                   "n_better": len(better)})
    res["winner_losses"] = losses
    return res


if __name__ == "__main__":
    only = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), None)
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = run(paths[0], paths[1], paths[2], only)
    if out is not None:
        (ROOT / "model_competition.json").write_text(json.dumps(out, indent=2))
        print(json.dumps(out["selection"], indent=1))
