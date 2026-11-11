"""Classical per-series forecasters, vectorised over all series at once.

These are the strong "local" baselines of the M4/M5 forecasting competitions. They are
used in the model competition (evals/model_competition.py) and as ensemble members:

  * ses_dow  simple exponential smoothing on weekday-adjusted sales; the smoothing
             constant is chosen per series from a grid by one-step-ahead error
  * tsb_dow  Teunter-Syntetos-Babai for intermittent demand (how often an item sells x
             how many when it does), also weekday-adjusted

Inputs are wide arrays: y (series x days) and valid (bool, same shape: observed and not a
closed / not-yet-listed day). `dows` is the weekday (0-6) of each column.
"""
from __future__ import annotations

import warnings

import numpy as np

ALPHAS = np.array([0.03, 0.08, 0.15, 0.3])


def dow_factors(y: np.ndarray, valid: np.ndarray, dows: np.ndarray, window: int = 84, shrink: float = 3.0) -> np.ndarray:
    """Per-series weekday multipliers (series x 7), shrunk towards 1 when there is little data."""
    y, valid, dows = y[:, -window:], valid[:, -window:], dows[-window:]
    v = np.where(valid, y, 0.0)
    total = v.sum(1)
    count = valid.sum(1)
    mean = np.divide(total, count, out=np.zeros_like(total), where=count > 0)
    f = np.ones((y.shape[0], 7))
    for d in range(7):
        m = dows == d
        s, c = v[:, m].sum(1), valid[:, m].sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            f[:, d] = (s + shrink * mean) / ((c + shrink) * mean)
    f = np.where(np.isfinite(f) & (mean[:, None] > 0), f, 1.0)
    return f / f.mean(1, keepdims=True)


def _deseason(y, valid, dows, f):
    return np.where(valid, y / np.maximum(f[:, dows], 1e-6), np.nan)


def ses_dow(y: np.ndarray, valid: np.ndarray, dows: np.ndarray, future_dows: np.ndarray,
            fit_days: int = 364) -> np.ndarray:
    """Weekday-adjusted SES. Returns (series x horizon)."""
    f = dow_factors(y, valid, dows)
    x = _deseason(y, valid, dows, f)[:, -fit_days:]
    n, D = x.shape
    first = np.where(np.isfinite(x), x, np.nan)
    with warnings.catch_warnings():               # new products have no observed days yet: start at 0
        warnings.simplefilter("ignore", RuntimeWarning)
        start = np.nanmean(first[:, :28], 1)
        start = np.where(np.isfinite(start), start, np.nanmean(first, 1))
    start = np.nan_to_num(start, nan=0.0)
    best_err = np.full(n, np.inf)
    best_level = start.copy()
    for a in ALPHAS:
        level = start.copy()
        err = np.zeros(n)
        cnt = np.zeros(n)
        for t in range(D):
            xt = x[:, t]
            ok = np.isfinite(xt)
            e = np.where(ok, xt - level, 0.0)
            if t >= 28:
                err += e * e
                cnt += ok
            level = level + a * e
        mse = np.divide(err, cnt, out=np.full(n, np.inf), where=cnt > 0)
        better = mse < best_err
        best_err = np.where(better, mse, best_err)
        best_level = np.where(better, level, best_level)
    return np.maximum(best_level[:, None] * f[:, future_dows], 0.0)


def tsb_dow(y: np.ndarray, valid: np.ndarray, dows: np.ndarray, future_dows: np.ndarray,
            alpha_p: float = 0.1, alpha_z: float = 0.1, fit_days: int = 364) -> np.ndarray:
    """Teunter-Syntetos-Babai: smoothed probability of a sale x smoothed size of a sale."""
    f = dow_factors(y, valid, dows)
    x = _deseason(y, valid, dows, f)[:, -fit_days:]
    n, D = x.shape
    obs = np.isfinite(x)
    nz = obs & (np.nan_to_num(x) > 0)
    p = np.clip(nz[:, :56].sum(1) / np.maximum(obs[:, :56].sum(1), 1), 0.01, 1.0)
    zs = np.where(nz[:, :56], x[:, :56], 0.0).sum(1) / np.maximum(nz[:, :56].sum(1), 1)
    z = np.where(zs > 0, zs, 1.0)
    for t in range(D):
        o, h = obs[:, t], nz[:, t]
        p = np.where(o, p + alpha_p * (h - p), p)
        z = np.where(h, z + alpha_z * (np.nan_to_num(x[:, t]) - z), z)
    return np.maximum((p * z)[:, None] * f[:, future_dows], 0.0)
