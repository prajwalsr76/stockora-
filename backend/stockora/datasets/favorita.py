"""Corporacion Favorita (Kaggle "Store Sales - Time Series Forecasting"): REAL daily sales.

54 grocery stores in 22 Ecuadorian cities, 33 product families, Jan 2013 - Aug 2017,
with promotions and national/regional/local holidays. Data is store x family per day
(not individual items). Download from Kaggle and place the CSVs in data/favorita/.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..forecasting.calendars import EcuadorCalendar

# Product families a kirana also sells, mapped to Stockora categories.
KIRANA_FAMILIES = {
    "GROCERY I": "staples", "GROCERY II": "staples", "BEVERAGES": "beverages", "DAIRY": "dairy",
    "BREAD/BAKERY": "bakery", "EGGS": "dairy", "CLEANING": "home_care", "HOME CARE": "home_care",
    "PERSONAL CARE": "personal_care", "BABY CARE": "personal_care", "PRODUCE": "produce",
    "SCHOOL AND OFFICE SUPPLIES": "stationery", "PREPARED FOODS": "instant_food", "FROZEN FOODS": "instant_food",
}
PERISHABLE = {"DAIRY", "BREAD/BAKERY", "EGGS", "PRODUCE", "MEATS", "POULTRY", "SEAFOOD", "DELI", "PREPARED FOODS"}


def load(folder: str | Path, families: list[str] | None = None) -> tuple[pd.DataFrame, EcuadorCalendar]:
    """Return (long history frame for Forecaster, calendar).

    Columns: day, sku ("store|family"), category (family), sales, promo, exclude,
    store, city, state, store_type, cluster.
    `exclude` marks days a store was closed or not yet open, and days before a family
    was first sold at a store: these are not zero demand.
    """
    folder = Path(folder)
    tr = pd.read_csv(folder / "train.csv", parse_dates=["date"],
                     dtype={"store_nbr": "int16", "family": "category", "sales": "float32", "onpromotion": "int32"})
    if families:
        tr = tr[tr["family"].isin(families)]
    stores = pd.read_csv(folder / "stores.csv")
    hol = pd.read_csv(folder / "holidays_events.csv")

    # Complete grid: Christmas days are missing from the file (stores closed).
    full_days = pd.date_range(tr["date"].min(), tr["date"].max())
    tr["family"] = tr["family"].astype(str)
    grid = pd.MultiIndex.from_product([sorted(tr["store_nbr"].unique()), sorted(tr["family"].unique()), full_days],
                                      names=["store_nbr", "family", "date"]).to_frame(index=False)
    tr = grid.merge(tr.drop(columns=["id"], errors="ignore"), on=["store_nbr", "family", "date"], how="left")
    missing = tr["sales"].isna()
    tr["sales"] = tr["sales"].fillna(0.0)
    tr["onpromotion"] = tr["onpromotion"].fillna(0)

    store_total = tr.groupby(["store_nbr", "date"])["sales"].transform("sum")
    closed = (store_total == 0) | missing
    started = tr.groupby(["store_nbr", "family"])["sales"].transform(lambda s: (s > 0).cummax())
    tr["exclude"] = closed | ~started.astype(bool)

    tr = tr.merge(stores, on="store_nbr", how="left")
    out = pd.DataFrame({
        "day": tr["date"], "sku": tr["store_nbr"].astype(str) + "|" + tr["family"], "category": tr["family"],
        "sales": tr["sales"].astype(float), "promo": tr["onpromotion"].astype(float), "exclude": tr["exclude"],
        "store": tr["store_nbr"].astype(str), "city": tr["city"], "state": tr["state"],
        "store_type": tr["type"], "cluster": tr["cluster"].astype(str),
    })
    return out, EcuadorCalendar(hol)
