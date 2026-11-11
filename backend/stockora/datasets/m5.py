"""Walmart M5 (Kaggle "M5 Forecasting - Accuracy"): REAL daily sales of individual items.

3,049 products x 10 stores in California, Texas and Wisconsin, Jan 2011 - Jun 2016,
with weekly shelf prices, US holidays/events and SNAP (food-stamp) days per state.
Unlike Favorita (store x family) this is item level: sparse, intermittent demand,
which is what a kirana sees for most of its SKUs.

Files (from Kaggle or the Nixtla mirror), in data/m5/:
  sales_train_evaluation.csv  calendar.csv  sell_prices.csv  [sales_test_evaluation.csv]
`sales_test_evaluation.csv` holds the official 28-day test period (d_1942-d_1969),
released after the competition. When present, the official test window can be scored.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..forecasting.calendars import Calendar

# M5 categories a kirana also sells (HOBBIES = toys/crafts is left out by default).
KIRANA_CATS = ("FOODS", "HOUSEHOLD")
CATEGORY = {"FOODS_1": "staples", "FOODS_2": "staples", "FOODS_3": "instant_food",
            "HOUSEHOLD_1": "home_care", "HOUSEHOLD_2": "home_care", "HOBBIES_1": "general", "HOBBIES_2": "general"}
STATES = ("CA", "TX", "WI")


class USCalendar(Calendar):
    """From M5's calendar.csv. Series meta needs `state` (SNAP days differ by state)."""

    feature_names = ["ev_sporting", "ev_cultural", "ev_national", "ev_religious", "days_to_event",
                     "days_after_event", "snap"]
    ratio_features = ["snap"]
    event_feature = "event"
    meta_needed = ["state"]

    def __init__(self, cal: pd.DataFrame):
        c = cal.copy()
        c["date"] = pd.to_datetime(c["date"])
        self.c = c.set_index("date")

    def table(self, meta: pd.DataFrame, days: pd.DatetimeIndex) -> dict[str, np.ndarray]:
        n, D = len(meta), len(days)
        c = self.c.reindex(days)
        out: dict[str, np.ndarray] = {}
        any_ev = np.zeros(D)
        for t in ["Sporting", "Cultural", "National", "Religious"]:
            hit = ((c["event_type_1"] == t) | (c["event_type_2"] == t)).to_numpy(float)
            out[f"ev_{t.lower()}"] = np.tile(hit, (n, 1))
            any_ev = np.maximum(any_ev, hit)
        to, after = np.zeros(D), np.zeros(D)
        nxt = 30.0
        for j in range(D - 1, -1, -1):
            nxt = 0.0 if any_ev[j] else min(nxt + 1, 30.0)
            to[j] = nxt
        prv = 30.0
        for j in range(D):
            prv = 0.0 if any_ev[j] else min(prv + 1, 30.0)
            after[j] = prv
        out["days_to_event"] = np.tile(to, (n, 1))
        out["days_after_event"] = np.tile(after, (n, 1))
        snap = {s: c.get(f"snap_{s}", pd.Series(0, index=days)).fillna(0).to_numpy(float) for s in STATES}
        out["snap"] = np.stack([snap.get(s, np.zeros(D)) for s in meta["state"]])
        # Major US holidays (Christmas, Thanksgiving, ...) are "National"; Super Bowl is Sporting.
        out["event"] = 1 + out["ev_national"] + out["ev_religious"] + out["ev_sporting"] + out["ev_cultural"]
        return out


def load(folder: str | Path, states: list[str] | None = None, cats: tuple[str, ...] = KIRANA_CATS,
         start: str = "2014-04-01", stores: list[str] | None = None,
         include_test: bool = True) -> tuple[pd.DataFrame, USCalendar]:
    """Return (long history frame for Forecaster, calendar).

    Columns: day, sku ("store|item"), category (dept), sales, promo, price, exclude, store, state, cat, dept.
    `promo` is the known-in-advance markdown: 1 - shelf price / regular price (13-week max).
    `exclude` marks days before an item was first sold in a store, weeks it had no shelf price
    (not on sale), and days a store was closed (Christmas): these are not zero demand.
    """
    folder = Path(folder)
    cal = pd.read_csv(folder / "calendar.csv")
    sales = pd.read_csv(folder / "sales_train_evaluation.csv")
    keep = sales["cat_id"].isin(cats)
    if states:
        keep &= sales["state_id"].isin(states)
    if stores:
        keep &= sales["store_id"].isin(stores)
    sales = sales[keep]
    test_path = folder / "sales_test_evaluation.csv"
    if include_test and test_path.exists():
        test = pd.read_csv(test_path)
        sales = sales.merge(test.drop(columns=["dept_id", "cat_id", "state_id"]), on=["item_id", "store_id"], how="left")

    if "d" not in cal:                       # the Nixtla mirror drops the "d" column; d_1 is 2011-01-29
        cal["d"] = [f"d_{i}" for i in range(1, len(cal) + 1)]
    d2date = dict(zip(cal["d"], pd.to_datetime(cal["date"])))
    id_cols = ["item_id", "dept_id", "cat_id", "store_id", "state_id"]
    dcols = [c for c in sales.columns if c.startswith("d_") and d2date[c] >= pd.Timestamp(start)]
    vals = sales[dcols].to_numpy(np.float32)

    # first sale ever (over the full history, not just the window): before that the item was not listed
    all_d = [c for c in sales.columns if c.startswith("d_")]
    first_idx = np.argmax(sales[all_d].to_numpy() > 0, axis=1)
    first_day = np.array([d2date[all_d[i]] for i in first_idx])

    n, D = vals.shape
    days = pd.DatetimeIndex([d2date[c] for c in dcols])
    long = pd.DataFrame({
        "day": np.tile(days.to_numpy(), n),
        "sales": vals.ravel(),
        "row": np.repeat(np.arange(n), D),
    })
    meta = sales[id_cols].reset_index(drop=True)
    long["store"] = meta["store_id"].to_numpy()[long["row"]]
    long["item"] = meta["item_id"].to_numpy()[long["row"]]

    # prices: weekly shelf price per store x item
    wk = dict(zip(pd.to_datetime(cal["date"]), cal["wm_yr_wk"]))
    st = meta["store_id"].unique()
    pr = pd.read_csv(folder / "sell_prices.csv", dtype={"store_id": "category", "item_id": "category",
                                                        "wm_yr_wk": "int32", "sell_price": "float32"})
    pr = pr[pr["store_id"].isin(st) & pr["item_id"].isin(meta["item_id"].unique())].copy()
    pr["store_id"] = pr["store_id"].astype(str)
    pr["item_id"] = pr["item_id"].astype(str)
    pr = pr.sort_values(["store_id", "item_id", "wm_yr_wk"])
    pr["regular"] = pr.groupby(["store_id", "item_id"])["sell_price"].transform(lambda s: s.rolling(13, min_periods=1).max())
    long["wm_yr_wk"] = long["day"].map(wk).astype("int32")
    long = long.merge(pr.rename(columns={"store_id": "store", "item_id": "item"}),
                      on=["store", "item", "wm_yr_wk"], how="left")
    no_price = long["sell_price"].isna()
    long["promo"] = np.clip(1 - long["sell_price"] / long["regular"], 0, 1).fillna(0).astype(np.float32)

    store_total = long.groupby(["store", "day"])["sales"].transform("sum")
    not_listed = long["day"].to_numpy() < first_day[long["row"].to_numpy()]
    is_test = long["day"] > pd.Timestamp("2016-05-22")
    closed = (store_total == 0) & ~is_test
    long["exclude"] = closed.to_numpy() | not_listed | no_price.to_numpy()

    r = long["row"].to_numpy()
    out = pd.DataFrame({
        "day": long["day"], "sku": long["store"] + "|" + long["item"],
        "category": meta["dept_id"].to_numpy()[r], "sales": long["sales"].astype(float),
        "promo": long["promo"].astype(float), "price": long["sell_price"].astype(float),
        "exclude": long["exclude"].astype(bool), "store": long["store"], "state": meta["state_id"].to_numpy()[r],
        "cat": meta["cat_id"].to_numpy()[r],
    })
    return out, USCalendar(cal)
