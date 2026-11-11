"""Item-level pieces added for Walmart M5: US calendar, price and intermittent-demand features."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stockora.datasets.m5 import USCalendar
from stockora.forecasting.model import PRICE_FEATURES, Forecaster

M5 = Path(__file__).resolve().parents[2] / "data" / "m5"


def _cal(days):
    none = pd.Series([None] * len(days), dtype=object)
    c = pd.DataFrame({"date": days.strftime("%Y-%m-%d"), "event_name_1": none, "event_type_1": none,
                      "event_name_2": none, "event_type_2": none, "snap_CA": 0, "snap_TX": 0, "snap_WI": 0})
    c.loc[c["date"] == "2016-02-07", ["event_name_1", "event_type_1"]] = ["SuperBowl", "Sporting"]
    c.loc[pd.to_datetime(c["date"]).dt.day <= 10, "snap_TX"] = 1
    return c


def test_us_calendar_events_and_state_snap():
    days = pd.date_range("2016-01-01", "2016-03-31")
    t = USCalendar(_cal(days)).table(pd.DataFrame({"state": ["CA", "TX"], "category": ["x", "x"]}), days)
    j = list(days).index(pd.Timestamp("2016-02-07"))
    assert t["ev_sporting"][0, j] == 1 and t["event"][0, j] == 2 and t["days_to_event"][0, j - 3] == 3
    assert t["snap"][0].sum() == 0 and t["snap"][1, 0] == 1 and t["snap"][1, 15] == 0


def _intermittent_hist(days, n=12, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        p = 0.15 + 0.05 * (i % 4)
        price = np.where(np.arange(len(days)) % 60 < 10, 3.0, 4.0)       # markdowns every 60 days
        lam = p * np.where(price < 4, 3.0, 1.0)
        rows.append(pd.DataFrame({"day": days, "sku": f"s|{i}", "category": "FOODS_1", "store": "s", "state": "CA",
                                  "sales": rng.poisson(lam).astype(float), "price": price,
                                  "promo": 1 - price / 4.0, "exclude": False}))
    return pd.concat(rows, ignore_index=True)


def test_item_level_features_build_and_forecast():
    days = pd.date_range("2015-01-01", "2016-03-31")
    hist = _intermittent_hist(days)
    f = Forecaster(calendar=USCalendar(_cal(days)), horizon=7, meta_cols=["store"], max_train_rows=60_000,
                   extra_features=["r91", "since_sale"]).fit(hist, step=7)
    assert {"r91", "since_sale", *PRICE_FEATURES} <= set(f.features)
    pred = f.predict(hist, origin=days[-8].date())
    assert len(pred) == 12 * 7 and (pred["forecast"] >= 0).all()
    # markdown days (promo > 0) should be forecast higher on average than full-price days
    fut = hist[hist["day"] > days[-8]]
    future = fut.set_index(["sku", fut["day"].dt.date])["promo"]
    pred["promo"] = [future.get((s, d), 0) for s, d in zip(pred["sku"], pred["day"])]
    if pred["promo"].gt(0).any() and pred["promo"].eq(0).any():
        assert pred.loc[pred.promo > 0, "forecast"].mean() > pred.loc[pred.promo == 0, "forecast"].mean()


@pytest.mark.skipif(not (M5 / "calendar.csv").exists(), reason="M5 data not downloaded")
def test_m5_loader_on_real_file():
    from stockora.datasets.m5 import load
    hist, cal = load(M5, stores=["WI_1"], start="2016-01-01")
    assert hist["sku"].nunique() > 2000 and hist["day"].max() == pd.Timestamp("2016-06-19")
    assert hist.loc[hist["day"] == "2015-12-25", "exclude"].all() if (hist["day"] == "2015-12-25").any() else True
    assert hist["exclude"].mean() < 0.2 and set(hist["state"]) == {"WI"}


def test_bagging_and_smoothing_blend():
    """The production options chosen by the model competition: averaged GBMs blended with SES."""
    days = pd.date_range("2015-06-01", "2016-03-31")
    hist = _intermittent_hist(days, n=8)
    cal = USCalendar(_cal(days))
    kw = dict(calendar=cal, horizon=7, meta_cols=["store"], max_train_rows=40_000, compute_sigma=False)
    plain = Forecaster(**kw).fit(hist, step=7)
    blend = Forecaster(**kw, learner="sk_bag", bag=2, ses_weight=0.4).fit(hist, step=7)
    assert len(blend.extra_models) == 1
    a, b = plain.predict(hist), blend.predict(hist)
    assert len(a) == len(b) and (b["forecast"] >= 0).all()
    assert not np.allclose(a["forecast"].to_numpy(), b["forecast"].to_numpy())
