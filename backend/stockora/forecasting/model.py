"""Demand forecasting: one global gradient-boosted model across all series.

* Vectorised: features are built from (series x day) arrays, so it trains on millions
  of rows (tested on the real Favorita dataset: 1,782 series x 1,684 days).
* Market-agnostic: a Calendar supplies what is known about future days (Indian
  festivals for kirana stores, Ecuador holidays and paydays for Favorita).
* Known-future promotions are used when the data has them.
* Direct multi-horizon: each horizon h = 1..H is a row with the same origin features.
* Final forecast = 75% XGBoost (Tweedie loss) + 25% per-series weekday-adjusted exponential smoothing.
  Picked by a 25-candidate model competition on three real datasets (Favorita, Walmart M5, Rohlik;
  none Indian), see evals/model_competition.py and evals/COMPETITION_REPORT.md. If xgboost is not
  installed, the previous winner (average of 3 scikit-learn boosted models) is used instead.
* Targets are scaled by each series' recent level so small and large series share patterns.
* Stockout days are censored demand (sales understate it): two-pass EM-style imputation.
  Closed days (`exclude`) are dropped entirely.

The LLM never produces numbers; it only explains this model's output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .calendars import Calendar, IndiaCalendar, calendar_day_features
from .classical import ses_dow

HORIZON = 14
# Feature set chosen by ablation on real Favorita data (3 test periods): "last_year" cut holiday-day
# error from 0.28 to 0.20; yesterday/3-day features ("lag1", "r3") added nothing, so they are not used.
BASE_FEATURES = ["h", "dow", "dom", "month", "r7", "r28", "same_dow", "dow_profile", "trend",
                 "zero_share", "last_year", "cat"]
PROMO_FEATURES = ["promo", "promo_rel"]
# Known-future shelf price (M5 has weekly prices; a kirana knows its own selling price).
PRICE_FEATURES = ["price_change", "price_vs_91", "log_price"]
Y_CAP = 30.0          # sales above 30x the recent level are treated as one-off anomalies in training
# XGBoost settings used in the model competition (fixed before its first run, never tuned on results).
TWEEDIE_POWER = 1.2
XGB_PARAMS = dict(n_estimators=600, learning_rate=0.05, max_depth=8, min_child_weight=10, subsample=0.8,
                  colsample_bytree=0.8, reg_lambda=1.0, tree_method="hist", max_bin=255, n_jobs=2, random_state=0)


def wape(actual, pred) -> float:
    actual, pred = np.asarray(actual, float), np.asarray(pred, float)
    denom = np.abs(actual).sum()
    return float(np.abs(actual - pred).sum() / denom) if denom > 0 else float("nan")


def rmsle(actual, pred) -> float:
    a, p = np.clip(np.asarray(actual, float), 0, None), np.clip(np.asarray(pred, float), 0, None)
    return float(np.sqrt(np.mean((np.log1p(p) - np.log1p(a)) ** 2)))


@dataclass
class Panel:
    """Series x day arrays built from a long history frame."""
    keys: list
    days: pd.DatetimeIndex          # full grid incl. future days (for calendar)
    n_hist: int                     # number of history days (index of last history day + 1)
    y: np.ndarray                   # raw sales (0 where missing)
    valid: np.ndarray               # observed and not a stockout / closed day
    censored: np.ndarray            # stockout day: sales is a lower bound
    excluded: np.ndarray            # closed / not yet trading
    promo: np.ndarray | None
    meta: pd.DataFrame
    price: np.ndarray | None = None


def build_panel(hist: pd.DataFrame, horizon: int, meta_cols: list[str]) -> Panel:
    h = hist.copy()
    h["day"] = pd.to_datetime(h["day"])
    keys = list(dict.fromkeys(h["sku"]))
    kpos = {k: i for i, k in enumerate(keys)}
    d0, d1 = h["day"].min(), h["day"].max()
    days = pd.date_range(d0, d1 + pd.Timedelta(days=horizon))
    n, D, nh = len(keys), len(days), (d1 - d0).days + 1
    si = h["sku"].map(kpos).to_numpy()
    di = ((h["day"] - d0).dt.days).to_numpy()
    y = np.zeros((n, D))
    present = np.zeros((n, D), bool)
    y[si, di] = h["sales"].to_numpy(float)
    present[si, di] = True
    stockout = np.zeros((n, D), bool)
    if "stockout" in h:
        stockout[si, di] = h["stockout"].to_numpy(bool)
    excluded = ~present
    if "exclude" in h:
        ex = np.zeros((n, D), bool)
        ex[si, di] = h["exclude"].to_numpy(bool)
        excluded |= ex
    excluded[:, nh:] = True
    promo = None
    if "promo" in h:
        promo = np.zeros((n, D))
        promo[si, di] = h["promo"].fillna(0).to_numpy(float)
    price = None
    if "price" in h:
        price = np.full((n, D), np.nan)
        price[si, di] = h["price"].to_numpy(float)
        price = pd.DataFrame(price.T).ffill().bfill().to_numpy().T      # carry the last known price
    first = h.groupby("sku").first()
    meta = pd.DataFrame({"sku": keys})
    for c in ["category", *meta_cols]:
        meta[c] = [first.loc[k, c] for k in keys]
    valid = ~excluded & ~stockout
    return Panel(keys, days, nh, y, valid, stockout & ~excluded, excluded, promo, meta, price)


def _roll_mean(a: np.ndarray, mask: np.ndarray, w: int) -> np.ndarray:
    """Mean over the trailing w days (inclusive) using only masked-in values; NaN if none."""
    v = np.where(mask, a, 0.0)
    c = mask.astype(float)
    cs = np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(v, 1)], 1)
    cc = np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(c, 1)], 1)
    idx = np.arange(a.shape[1])
    lo = np.maximum(idx + 1 - w, 0)
    s = cs[:, idx + 1] - cs[:, lo]
    n = cc[:, idx + 1] - cc[:, lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, s / n, np.nan)


def _last_year(prep: dict, s: np.ndarray, o: np.ndarray, t: np.ndarray) -> np.ndarray:
    ok = o - 364 >= 0
    out = np.full(len(s), np.nan)
    tt = np.clip(t - 364 + 3, 0, prep["r7"].shape[1] - 1)   # r7 ending 3 days after = centred window
    oo = np.clip(o - 364, 0, prep["r28"].shape[1] - 1)
    num, den = prep["r7"][s, tt], prep["r28"][s, oo]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[ok] = (num / np.where(den > 0, den, np.nan))[ok]
    return np.clip(np.nan_to_num(out, nan=1.0), 0, 20)


@dataclass
class Forecaster:
    calendar: Calendar = field(default_factory=IndiaCalendar)
    horizon: int = HORIZON
    meta_cols: list[str] = field(default_factory=list)   # extra static categoricals (e.g. store, city)
    max_train_rows: int = 2_500_000
    drop_features: list[str] = field(default_factory=list)   # for ablation studies
    # Defaults below were chosen by evals/model_competition.py: best on validation AND held-out test
    # windows of two real datasets (Favorita store x family, Walmart M5 single items).
    extra_features: list[str] = field(default_factory=lambda: ["r91", "since_sale"])
    compute_sigma: bool = True       # out-of-sample error for safety stock (an extra model fit)
    keep_train: bool = False         # keep the training table (model comparison experiments)
    train_: pd.DataFrame | None = None
    cat_idx: list[int] = field(default_factory=list)
    extra_models: list = field(default_factory=list)   # averaged with `model` at predict time
    # "xgb_tweedie" (competition winner, Nov 2026) or "sk_bag" (previous winner: `bag` scikit-learn models).
    # The scikit-learn `model` is always fitted too: it imputes censored days and measures the error (sigma).
    learner: str = "xgb_tweedie"
    learner_used: str = ""
    ml_models: list = field(default_factory=list)     # the models averaged at predict time
    bag: int = 3                     # sk_bag only: number of boosted models averaged (extra ones use 70% of features each)
    ses_weight: float = 0.25         # blend weight of weekday-adjusted exponential smoothing (per series)
    min_history: int = 28
    categories: list[str] = field(default_factory=list)
    model: HistGradientBoostingRegressor | None = None
    sigma: dict = field(default_factory=dict)
    features: list[str] = field(default_factory=list)
    cat_maps: dict = field(default_factory=dict)

    def _meta_all(self) -> list[str]:
        return self.meta_cols + [c for c in self.calendar.meta_needed if c not in self.meta_cols]

    # ------------------------------------------------------------- features
    def _prepare(self, p: Panel) -> dict:
        y, valid = p.y, p.valid
        r3 = _roll_mean(y, valid, 3)
        r7 = _roll_mean(y, valid, 7)
        r28 = _roll_mean(y, valid, 28)
        r91 = _roll_mean(y, valid, 91)
        # mean of the last 4 same-weekday values ending at t
        sw_sum = np.zeros_like(y)
        sw_n = np.zeros_like(y)
        for k in range(4):
            sh = 7 * k
            sw_sum[:, sh:] += np.where(valid[:, :y.shape[1] - sh], y[:, :y.shape[1] - sh], 0)
            sw_n[:, sh:] += valid[:, :y.shape[1] - sh]
        with np.errstate(invalid="ignore", divide="ignore"):
            sw = np.where(sw_n > 0, sw_sum / sw_n, np.nan)
            nz = _roll_mean((y > 0).astype(float), valid, 28)
        obs28 = _roll_mean(np.ones_like(y), valid, 28) * 0  # placeholder for count below
        cnt28 = np.cumsum(valid, 1)
        cnt28 = cnt28 - np.concatenate([np.zeros((y.shape[0], 28)), cnt28[:, :-28]], 1)
        # rolling 91-day peak of observed sales (used to cap forecasts)
        masked = np.where(valid, y, 0.0)
        # days since the last observed sale (capped at 60): key for slow, intermittent items
        idx = np.arange(y.shape[1])
        last = np.where(valid & (y > 0), idx, -10_000)
        since_sale = np.minimum(idx - np.maximum.accumulate(last, axis=1), 60).astype(float)
        peak91 = pd.DataFrame(masked.T).rolling(91, min_periods=1).max().to_numpy().T
        cal = self.calendar.table(p.meta, p.days)
        cal_hist_mean = {f: _roll_mean(cal[f], np.ones_like(cal[f], bool), 28) for f in self.calendar.ratio_features}
        promo_r28 = _roll_mean(p.promo, np.ones_like(p.promo, bool), 28) if p.promo is not None else None
        day_feats = calendar_day_features(p.days)
        price_r91 = _roll_mean(np.nan_to_num(p.price), ~np.isnan(p.price), 91) if p.price is not None else None
        del obs28
        return dict(since_sale=since_sale, peak91=peak91, r3=r3, r7=r7, r28=r28, r91=r91, sw=sw, nz=nz, cnt28=cnt28, cal=cal, calm=cal_hist_mean,
                    promo_r28=promo_r28, price_r91=price_r91, day=day_feats)

    def _rows(self, p: Panel, prep: dict, origins: np.ndarray, with_target: bool) -> pd.DataFrame:
        n = len(p.keys)
        cats = p.meta["category"].map(lambda c: self.categories.index(c) if c in self.categories else -1).to_numpy()
        meta_codes = {c: p.meta[c].map(lambda v, c=c: self.cat_maps[c].get(v, -1)).to_numpy() for c in self.meta_cols}
        frames = []
        for h in range(1, self.horizon + 1):
            s = np.repeat(np.arange(n), len(origins))
            o = np.tile(origins, n)
            t = o + h
            ok = (prep["cnt28"][s, o] >= 7) & (t < p.y.shape[1])
            if with_target:
                ok &= ~p.excluded[s, t]
            s, o, t = s[ok], o[ok], t[ok]
            if len(s) == 0:
                continue
            r28 = prep["r28"][s, o]
            # robust level: a dead month must not make the scale ~0 (targets would explode)
            scale = np.maximum(np.maximum(np.nan_to_num(r28, nan=0.0),
                                          0.5 * np.nan_to_num(prep["r91"][s, o], nan=0.0)), 0.05)
            last_same = t - 7 * ((h + 6) // 7)
            sw = prep["sw"][s, last_same]
            f = {
                "sidx": s, "oidx": o, "tidx": t, "h": np.full(len(s), h), "scale": scale,
                "dow": prep["day"]["dow"][t], "dom": prep["day"]["dom"][t], "month": prep["day"]["month"][t],
                "lag1": np.where(p.valid[s, o], p.y[s, o], np.nan_to_num(prep["r7"][s, o], nan=0)) / scale,
                "r3": np.nan_to_num(prep["r3"][s, o], nan=0) / scale,
                "r7": np.nan_to_num(prep["r7"][s, o], nan=0) / scale,
                # last year's seasonal shape: (7-day mean around t-364) / (28-day level at o-364)
                "last_year": _last_year(prep, s, o, t),
                "r28": np.nan_to_num(r28, nan=0) / scale,
                "r91": np.nan_to_num(prep["r91"][s, o], nan=0) / scale,
                "since_sale": prep["since_sale"][s, o],
                "same_dow": np.nan_to_num(sw, nan=0) / scale,
                "dow_profile": np.nan_to_num(sw / np.where(r28 > 0, r28, np.nan), nan=1.0),
                "trend": np.nan_to_num(r28 / np.where(prep["r91"][s, o] > 0, prep["r91"][s, o], np.nan), nan=1.0),
                "zero_share": 1 - np.nan_to_num(prep["nz"][s, o], nan=0),
                "cat": cats[s],
            }
            for c in self.meta_cols:
                f[c] = meta_codes[c][s]
            for name in self.calendar.feature_names:
                f[name] = prep["cal"][name][s, t]
            for name in self.calendar.ratio_features:
                f[f"{name}_ratio"] = prep["cal"][name][s, t] / np.maximum(prep["calm"][name][s, o], 1e-6)
            f["event"] = prep["cal"][self.calendar.event_feature][s, t] if self.calendar.event_feature in prep["cal"] \
                else prep["cal"]["event"][s, t]
            if p.promo is not None:
                f["promo"] = p.promo[s, t]
                f["promo_rel"] = p.promo[s, t] / (np.nan_to_num(prep["promo_r28"][s, o], nan=0) + 1)
            if p.price is not None:
                pt, po = p.price[s, t], p.price[s, o]
                with np.errstate(invalid="ignore", divide="ignore"):
                    f["price_change"] = np.nan_to_num(pt / po, nan=1.0, posinf=1.0)
                    f["price_vs_91"] = np.nan_to_num(pt / prep["price_r91"][s, o], nan=1.0, posinf=1.0)
                f["log_price"] = np.log1p(np.nan_to_num(pt, nan=0.0))
            if with_target:
                f["actual"] = p.y[s, t]
                f["y"] = np.minimum(p.y[s, t] / scale, Y_CAP)
                f["censored"] = p.censored[s, t]
            frames.append(pd.DataFrame(f))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _feature_list(self, has_promo: bool, has_price: bool = False) -> list[str]:
        feats = BASE_FEATURES + self.extra_features + self.meta_cols + self.calendar.feature_names + \
            [f"{x}_ratio" for x in self.calendar.ratio_features]
        feats = feats + (PROMO_FEATURES if has_promo else []) + (PRICE_FEATURES if has_price else [])
        return [f for f in feats if f not in self.drop_features]

    # ------------------------------------------------------------- public API
    def fit(self, hist: pd.DataFrame, train_end: date | None = None, step: int = 3,
            lookback_days: int | None = None) -> "Forecaster":
        """hist columns: day, sku, category, sales; optional stockout, exclude, promo, meta_cols."""
        hist = hist.copy()
        hist["day"] = pd.to_datetime(hist["day"])
        if train_end is not None:
            hist = hist[hist["day"] <= pd.Timestamp(train_end)]
        self.categories = sorted(hist["category"].unique())
        self.cat_maps = {c: {v: i for i, v in enumerate(sorted(hist[c].astype(str).unique()))} for c in self.meta_cols}
        for c in self.meta_cols:
            hist[c] = hist[c].astype(str)
        p = build_panel(hist, self.horizon, self._meta_all())
        prep = self._prepare(p)
        last = p.n_hist - 1 - self.horizon
        first = self.min_history
        if lookback_days:
            first = max(first, last - lookback_days)
        origins = np.arange(last, first - 1, -step)[::-1]
        est_rows = len(origins) * len(p.keys) * self.horizon
        if est_rows > self.max_train_rows:                 # keep the most recent origins
            keep = max(1, self.max_train_rows // (len(p.keys) * self.horizon))
            origins = origins[-keep:]
        data = self._rows(p, prep, origins, with_target=True)
        self.features = self._feature_list(p.promo is not None, p.price is not None)
        self.cat_idx = [self.features.index(c) for c in ["cat", *self.meta_cols]]
        self.model = self.make_gbm()
        obs = ~data["censored"].to_numpy()
        self.model.fit(data.loc[obs, self.features], data.loc[obs, "y"])
        cens = ~obs
        if cens.any():                                     # EM-style censored-demand imputation
            est = self.model.predict(data.loc[cens, self.features])
            data.loc[cens, "y"] = np.maximum(data.loc[cens, "y"].to_numpy(), est)
            self.model.fit(data[self.features], data["y"])
        self.extra_models, self.ml_models, self.learner_used = [], [], "sk_bag"
        if self.learner == "xgb_tweedie":
            try:
                import xgboost as xgb
                m = xgb.XGBRegressor(objective="reg:tweedie", tweedie_variance_power=TWEEDIE_POWER, **XGB_PARAMS)
                self.ml_models = [m.fit(data[self.features], data["y"])]
                self.learner_used = "xgb_tweedie"
            except ImportError:     # pragma: no cover - fall back to the previous winner
                pass
        if not self.ml_models:
            self.extra_models = [self.make_gbm(max_features=0.7, random_state=k).fit(data[self.features], data["y"])
                                 for k in range(1, self.bag)]
            self.ml_models = [self.model, *self.extra_models]
        # Uncertainty for safety stock must come from days the model has NOT seen: in-sample
        # residuals understate real error. Refit without the last `holdout` origins and measure
        # residuals on them; scale per series (relative error x level).
        if self.keep_train:
            self.train_ = data
        if self.compute_sigma:
            self.sigma = self._out_of_sample_sigma(data, p)
        return self

    def make_gbm(self, **overrides) -> HistGradientBoostingRegressor:
        params = dict(loss="poisson", max_iter=400, learning_rate=0.06, max_leaf_nodes=63, min_samples_leaf=50,
                      l2_regularization=1.0, categorical_features=self.cat_idx, early_stopping=False, random_state=0)
        params.update(overrides)
        return HistGradientBoostingRegressor(**params)

    def _out_of_sample_sigma(self, data: pd.DataFrame, p: Panel, holdout_days: int = 56) -> dict:
        cut = data["oidx"].max() - holdout_days
        train, test = data[data["oidx"] <= cut], data[(data["oidx"] > cut) & ~data["censored"]]
        if len(test) == 0 or len(train) < 1000:
            test = data[~data["censored"]]
            m = self.model
        else:
            m = HistGradientBoostingRegressor(**self.model.get_params()).fit(train[self.features], train["y"])
        pred = np.minimum(m.predict(test[self.features]), Y_CAP)
        rel = pd.Series(test["y"].to_numpy() - pred).groupby(test["sidx"].to_numpy()).std()   # in scale units
        level = test.groupby("sidx")["scale"].last()
        pooled = float(np.nanmedian(rel)) if len(rel) else 0.0
        out = {}
        for i in range(len(p.keys)):
            r = rel.get(i, np.nan)
            out[p.keys[i]] = float((r if np.isfinite(r) else pooled) * level.get(i, 0.0))
        return out

    def predict(self, hist: pd.DataFrame, origin: date | None = None, raw_rows: bool = False) -> pd.DataFrame:
        """Forecast the next `horizon` days for every series from origin (default: last history day)."""
        if self.model is None:
            raise RuntimeError("fit() first")
        hist = hist.copy()
        hist["day"] = pd.to_datetime(hist["day"])
        if origin is not None:
            # keep future rows only for known-future columns (promotions); sales there are not used
            future = hist["day"] > pd.Timestamp(origin)
            if "promo" in hist:
                hist.loc[future, "sales"] = 0.0
                hist.loc[future, "exclude"] = True
            else:
                hist = hist[~future]
        for c in self.meta_cols:
            hist[c] = hist[c].astype(str)
        p = build_panel(hist, self.horizon, self._meta_all())
        o = (pd.Timestamp(origin) - p.days[0]).days if origin is not None else p.n_hist - 1
        if origin is not None and "promo" in hist:
            p.excluded[:, o + 1:] = True
            p.valid[:, o + 1:] = False
        prep = self._prepare(p)
        rows = self._rows(p, prep, np.array([o]), with_target=False)
        peak = prep["peak91"][rows["sidx"].to_numpy(), rows["oidx"].to_numpy()]
        rows["cap"] = np.maximum(3 * np.nan_to_num(peak, nan=0.0), Y_CAP * rows["scale"].to_numpy())
        rows["sku"] = [p.keys[i] for i in rows["sidx"]]
        rows["day"] = [p.days[i].date() for i in rows["tidx"]]
        rows["fest_mult"] = rows["event"]
        if raw_rows:
            return rows
        rows["forecast"] = self.apply(rows, self.ml_models or [self.model, *self.extra_models])
        if self.ses_weight > 0:
            # Blend with a local per-series model: the global model shares patterns across products,
            # smoothing tracks each product's own recent level. Measured on two real datasets.
            dows = p.days.dayofweek.to_numpy()
            fut = dows[o + 1:o + 1 + self.horizon]
            if len(fut) < self.horizon:
                fut = (np.arange(1, self.horizon + 1) + dows[o]) % 7
            local = ses_dow(p.y[:, :o + 1], p.valid[:, :o + 1], dows[:o + 1], fut)
            ses = local[rows["sidx"].to_numpy(), rows["h"].to_numpy() - 1]
            w = self.ses_weight
            rows["forecast"] = np.clip((1 - w) * rows["forecast"].to_numpy() + w * ses, 0, rows["cap"].to_numpy())
        return rows[["sku", "day", "h", "forecast", "event", "fest_mult"]]

    def apply(self, rows: pd.DataFrame, models: list) -> np.ndarray:
        """Average of the models' (scaled) predictions, back in units and capped."""
        raw = np.mean([np.maximum(m.predict(rows[self.features]), 0) for m in models], axis=0)
        return np.clip(raw * rows["scale"].to_numpy(), 0, rows["cap"].to_numpy())


# ----------------------------------------------------------------- baselines (vectorised)
def _wide(hist: pd.DataFrame, origin: date) -> pd.DataFrame:
    h = hist.copy()
    h["day"] = pd.to_datetime(h["day"])
    h = h[h["day"] <= pd.Timestamp(origin)]
    if "exclude" in h:
        h = h[~h["exclude"].astype(bool)]
    return h.pivot_table(index="sku", columns="day", values="sales", aggfunc="sum")


def baseline_seasonal_naive(hist: pd.DataFrame, origin: date, horizon: int = HORIZON) -> pd.DataFrame:
    """Same weekday last week."""
    w = _wide(hist, origin)
    out = []
    for h in range(1, horizon + 1):
        d = pd.Timestamp(origin) + pd.Timedelta(days=h)
        ref = d - pd.Timedelta(days=7 * ((h - 1) // 7 + 1))
        vals = w[ref] if ref in w.columns else pd.Series(0.0, index=w.index)
        out.append(pd.DataFrame({"sku": w.index, "day": d.date(), "forecast": vals.fillna(0).to_numpy()}))
    return pd.concat(out, ignore_index=True)


def baseline_moving_average(hist: pd.DataFrame, origin: date, window: int = 28, horizon: int = HORIZON) -> pd.DataFrame:
    w = _wide(hist, origin)
    avg = w.iloc[:, -window:].mean(axis=1).fillna(0)
    return pd.concat([pd.DataFrame({"sku": w.index, "day": (pd.Timestamp(origin) + pd.Timedelta(days=h)).date(),
                                    "forecast": avg.to_numpy()}) for h in range(1, horizon + 1)], ignore_index=True)


def baseline_dow_mean(hist: pd.DataFrame, origin: date, weeks: int = 4, horizon: int = HORIZON) -> pd.DataFrame:
    """Mean of the same weekday over the last `weeks` weeks (a strong retail baseline)."""
    w = _wide(hist, origin)
    out = []
    for h in range(1, horizon + 1):
        d = pd.Timestamp(origin) + pd.Timedelta(days=h)
        k0 = (h - 1) // 7 + 1
        refs = [d - pd.Timedelta(days=7 * k) for k in range(k0, k0 + weeks)]
        cols = [r for r in refs if r in w.columns]
        vals = w[cols].mean(axis=1) if cols else pd.Series(0.0, index=w.index)
        out.append(pd.DataFrame({"sku": w.index, "day": d.date(), "forecast": vals.fillna(0).to_numpy()}))
    return pd.concat(out, ignore_index=True)


def score(pred: pd.DataFrame, truth: pd.Series, origin: date) -> pd.DataFrame:
    p = pred.copy()
    p["actual"] = [truth.get((s, d), np.nan) for s, d in zip(p["sku"], p["day"])]
    p = p.dropna(subset=["actual"])
    p["block"] = [(pd.Timestamp(d) - pd.Timestamp(origin)).days // 7 if False else
                  (pd.Timestamp(d) - pd.Timestamp(origin) - pd.Timedelta(days=1)).days // 7 for d in p["day"]]
    return p


def summarize(frames: list[pd.DataFrame]) -> dict:
    allp = pd.concat(frames, ignore_index=True)
    weekly = allp.groupby(["sku", "origin", "block"])[["actual", "forecast"]].sum()
    return {"wape_daily": round(wape(allp["actual"], allp["forecast"]), 4),
            "wape_weekly": round(wape(weekly["actual"], weekly["forecast"]), 4),
            "rmsle_daily": round(rmsle(allp["actual"], allp["forecast"]), 4),
            "bias": round(float((allp["forecast"].sum() - allp["actual"].sum()) / allp["actual"].sum()), 4)}


def backtest(hist: pd.DataFrame, origins: list[date], step: int = 3, forecaster_factory=None,
             truth_col: str | None = None, event_threshold: float = 1.15, frames: dict | None = None) -> dict:
    """Rolling-origin backtest against baselines. Scores on true demand when the data has it."""
    hist = hist.copy()
    hist["day"] = pd.to_datetime(hist["day"])
    truth_col = truth_col or ("demand" if "demand" in hist.columns else "sales")
    scored = hist
    if "exclude" in hist:
        scored = hist[~hist["exclude"].astype(bool)]
    truth = scored.set_index(["sku", scored["day"].dt.date])[truth_col]
    oracle = hist.set_index(["sku", hist["day"].dt.date])["expected"] if "expected" in hist else None
    factory = forecaster_factory or (lambda: Forecaster())
    names = ["model", "dow_mean_4w", "moving_avg_28", "seasonal_naive"] + (["oracle_noise_floor"] if oracle is not None else [])
    res = {n: [] for n in names}
    ev = {n: [] for n in names}
    for origin in origins:
        fc = factory().fit(hist, train_end=origin, step=step)
        H = fc.horizon
        model_pred = fc.predict(hist, origin)
        preds = {"model": model_pred[["sku", "day", "forecast"]],
                 "dow_mean_4w": baseline_dow_mean(hist, origin, horizon=H),
                 "moving_avg_28": baseline_moving_average(hist, origin, horizon=H),
                 "seasonal_naive": baseline_seasonal_naive(hist, origin, horizon=H)}
        if oracle is not None:
            preds["oracle_noise_floor"] = model_pred[["sku", "day"]].assign(
                forecast=[oracle.get((s, d), np.nan) for s, d in zip(model_pred["sku"], model_pred["day"])])
        ev_days = set(zip(model_pred.loc[model_pred["event"] > event_threshold, "sku"],
                          model_pred.loc[model_pred["event"] > event_threshold, "day"]))
        for name, p in preds.items():
            sc = score(p, truth, origin).assign(origin=str(origin))
            res[name].append(sc)
            ev[name].append(sc[[k in ev_days for k in zip(sc["sku"], sc["day"])]])
    if frames is not None:                      # raw scored rows, for pooling across runs
        for name in names:
            frames.setdefault(name, []).extend(res[name])
            frames.setdefault(name + "@event", []).extend(ev[name])
    out = {}
    for name in names:
        out[name] = summarize(res[name])
        evp = pd.concat(ev[name])
        out[name]["wape_event_days"] = round(wape(evp["actual"], evp["forecast"]), 4) if len(evp) else None
    return out
