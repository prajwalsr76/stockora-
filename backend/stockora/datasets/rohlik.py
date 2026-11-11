"""Rohlik (Kaggle "Rohlik Sales Forecasting Challenge v2"): REAL daily sales of an online grocer.

5,390 products x warehouse (7 warehouses in Prague, Brno, Budapest, Munich, Frankfurt),
Aug 2020 - Jun 2024: fruit and vegetables, bakery, meat and fish. Daily, with the selling
price, up to seven kinds of discount, the share of the day each item was in stock
(`availability`), and per-warehouse holidays, school holidays and closed days.
Sales are mostly in kg or pieces with decimals (weighed goods).

How it is used in Stockora's benchmark (choices made before looking at any results):
  * availability < 1  -> the item ran out during the day: sales understate demand, so the day is
    CENSORED (`stockout`), handled by the forecaster's censored-demand imputation and not scored.
    (The data has no rows with availability 0: days an item was not offered at all have no row.)
  * no row for a day   -> the item was not offered (not listed or out of stock all day): excluded,
    neither trained on nor scored, because it is not zero demand.
  * shops_closed       -> no deliveries that day: excluded.
  * promo              -> the largest of the seven discount columns (planned, known in advance).
  * price              -> sell_price_main (known in advance).
  * `total_orders` is NOT used: it is the warehouse's order count on the day being forecast,
    which a real shop does not know in advance.
The Kaggle `solution.csv` holds no real answers (all zeros), so the competition's own test period
cannot be scored. Stockora scores held-out windows cut from the end of `sales_train.csv`.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..forecasting.calendars import Calendar

DEFAULT_WAREHOUSES = ("Prague_1", "Brno_1", "Budapest_1")     # two countries, three cities
CATEGORY = {"Fruit and vegetable": "produce", "Bakery": "bakery", "Meat and fish": "meat_fish"}


class RohlikCalendar(Calendar):
    """From Rohlik's calendar.csv, per warehouse (Czech, Hungarian and German holidays differ)."""

    feature_names = ["holiday", "school_holidays", "winter_school_holidays", "days_to_holiday",
                     "days_after_holiday", "closed_next"]
    ratio_features: list[str] = []
    event_feature = "event"
    meta_needed = ["warehouse"]

    def __init__(self, cal: pd.DataFrame):
        c = cal.copy()
        c["date"] = pd.to_datetime(c["date"])
        self.c = c

    def table(self, meta: pd.DataFrame, days: pd.DatetimeIndex) -> dict[str, np.ndarray]:
        n, D = len(meta), len(days)
        per_wh: dict[str, dict[str, np.ndarray]] = {}
        for wh in meta["warehouse"].unique():
            c = self.c[self.c["warehouse"] == wh].drop_duplicates("date").set_index("date").reindex(days)
            hol = c["holiday"].fillna(0).to_numpy(float)
            closed = c["shops_closed"].fillna(0).to_numpy(float)
            to, after = np.zeros(D), np.zeros(D)
            nxt = 30.0
            for j in range(D - 1, -1, -1):
                nxt = 0.0 if hol[j] else min(nxt + 1, 30.0)
                to[j] = nxt
            prv = 30.0
            for j in range(D):
                prv = 0.0 if hol[j] else min(prv + 1, 30.0)
                after[j] = prv
            per_wh[wh] = {
                "holiday": hol, "school_holidays": c["school_holidays"].fillna(0).to_numpy(float),
                "winter_school_holidays": c["winter_school_holidays"].fillna(0).to_numpy(float),
                "days_to_holiday": to, "days_after_holiday": after,
                # people stock up the day before the shops close (Christmas, Easter)
                "closed_next": np.r_[closed[1:], 0.0],
            }
        whs = meta["warehouse"].to_numpy()
        out = {k: np.stack([per_wh[w][k] for w in whs]) for k in self.feature_names}
        out["event"] = 1 + out["holiday"] + out["closed_next"]
        assert out["holiday"].shape == (n, D)
        return out


def load(folder: str | Path, warehouses: tuple[str, ...] | list[str] | None = DEFAULT_WAREHOUSES,
         start: str | None = None) -> tuple[pd.DataFrame, RohlikCalendar]:
    """Return (long history frame for Forecaster, calendar).

    Columns: day, sku ("warehouse|unique_id"), category, sales, stockout, exclude, promo, price,
    warehouse, l2, availability.
    """
    folder = Path(folder)
    s = pd.read_csv(folder / "sales_train.csv", parse_dates=["date"])
    if warehouses:
        s = s[s["warehouse"].isin(warehouses)]
    if start:
        s = s[s["date"] >= pd.Timestamp(start)]
    s = s[s["sales"].notna()]
    inv = pd.read_csv(folder / "inventory.csv")[["unique_id", "L1_category_name_en", "L2_category_name_en"]]
    cal = pd.read_csv(folder / "calendar.csv", parse_dates=["date"])
    s = s.merge(inv, on="unique_id", how="left")
    closed = cal.loc[cal["shops_closed"] == 1, ["date", "warehouse"]].assign(closed=True)
    s = s.merge(closed, on=["date", "warehouse"], how="left")
    disc = [c for c in s.columns if c.startswith("type_") and c.endswith("_discount")]
    out = pd.DataFrame({
        "day": s["date"],
        "sku": s["warehouse"] + "|" + s["unique_id"].astype(str),
        "category": s["L1_category_name_en"].map(CATEGORY).fillna("other"),
        "sales": s["sales"].astype(float),
        "stockout": (s["availability"] < 1).to_numpy(),
        "exclude": s["closed"].fillna(False).astype(bool).to_numpy(),
        "promo": s[disc].clip(lower=0).max(axis=1).fillna(0).astype(float),
        "price": s["sell_price_main"].astype(float),
        "warehouse": s["warehouse"],
        "l2": s["L2_category_name_en"].fillna("other"),
        "availability": s["availability"].astype(float),
    })
    return out.sort_values(["sku", "day"]).reset_index(drop=True), RohlikCalendar(cal)
