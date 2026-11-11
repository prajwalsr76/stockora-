"""Market calendars: what is known about a future day before it happens.

The forecaster is market-agnostic. A calendar turns (series, day) into features:
  * IndiaCalendar   - kirana stores: festival ramps by category, seasons, salary week
  * EcuadorCalendar - Favorita (real data): national/regional/local holidays,
                      transferred holidays, bridge days, events, public-sector paydays

Each calendar returns arrays shaped (n_series, n_days) so feature building is vectorised.
`ratio_features` are also compared with their own 28-day history at the forecast origin
(e.g. "Diwali effect ahead relative to what is already baked into recent sales").
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ..india import days_to_next_festival, festival_multiplier, is_salary_window, season_multiplier


class Calendar:
    feature_names: list[str] = []
    ratio_features: list[str] = []
    event_feature: str = "event"
    meta_needed: list[str] = []       # series columns the calendar reads (e.g. city, state)

    def table(self, meta: pd.DataFrame, days: pd.DatetimeIndex) -> dict[str, np.ndarray]:
        raise NotImplementedError


class IndiaCalendar(Calendar):
    feature_names = ["fest_mult", "season_mult", "days_to_fest", "salary"]
    ratio_features = ["fest_mult", "season_mult"]
    event_feature = "fest_mult"

    def table(self, meta: pd.DataFrame, days: pd.DatetimeIndex) -> dict[str, np.ndarray]:
        dates = [d.date() for d in days]
        cats = list(meta["category"].unique())
        per_cat = {}
        for c in cats:
            per_cat[c] = (np.array([festival_multiplier(d, c) for d in dates]),
                          np.array([season_multiplier(d, c) for d in dates]))
        dtf = np.array([min(days_to_next_festival(d), 60) for d in dates], float)
        sal = np.array([is_salary_window(d) for d in dates], float)
        idx = [cats.index(c) for c in meta["category"]]
        fest = np.stack([per_cat[cats[i]][0] for i in idx])
        seas = np.stack([per_cat[cats[i]][1] for i in idx])
        n = len(meta)
        return {"fest_mult": fest, "season_mult": seas, "days_to_fest": np.tile(dtf, (n, 1)),
                "salary": np.tile(sal, (n, 1))}


class EcuadorCalendar(Calendar):
    """Built from Favorita's holidays_events.csv. Series meta needs `city` and `state`."""

    feature_names = ["nat_holiday", "reg_holiday", "loc_holiday", "bridge", "work_day", "event_day",
                     "days_to_nat_holiday", "payday"]
    ratio_features: list[str] = []
    event_feature = "event"
    meta_needed = ["city", "state"]

    def __init__(self, holidays: pd.DataFrame):
        h = holidays.copy()
        h["date"] = pd.to_datetime(h["date"])
        h = h[~h["transferred"].astype(str).str.lower().eq("true")]     # moved away: not a day off
        self.h = h

    def table(self, meta: pd.DataFrame, days: pd.DatetimeIndex) -> dict[str, np.ndarray]:
        n, D = len(meta), len(days)
        pos = {d: i for i, d in enumerate(days)}
        out = {k: np.zeros((n, D)) for k in self.feature_names}
        city = meta["city"].to_numpy()
        state = meta["state"].to_numpy()
        for _, r in self.h.iterrows():
            j = pos.get(r["date"])
            if j is None:
                continue
            t = r["type"]
            if t == "Bridge":
                key = "bridge"
            elif t == "Work Day":
                key = "work_day"
            elif t == "Event":
                key = "event_day"
            else:                       # Holiday, Transfer, Additional
                key = {"National": "nat_holiday", "Regional": "reg_holiday", "Local": "loc_holiday"}[r["locale"]]
            if r["locale"] == "National" or key == "event_day":
                out[key][:, j] = 1
            elif r["locale"] == "Regional":
                out[key][state == r["locale_name"], j] = 1
            else:
                out[key][city == r["locale_name"], j] = 1
        nat = out["nat_holiday"][0]
        nxt, dist = 60.0, np.zeros(D)
        for j in range(D - 1, -1, -1):
            nxt = 0.0 if nat[j] else min(nxt + 1, 60.0)
            dist[j] = nxt
        out["days_to_nat_holiday"] = np.tile(dist, (n, 1))
        # Public-sector wages are paid on the 15th and the last day of each month.
        pay = np.array([d.day == 15 or (d + pd.Timedelta(days=1)).day == 1 for d in days], float)
        out["payday"] = np.tile(pay, (n, 1))
        out["event"] = 1 + out["nat_holiday"] + out["reg_holiday"] + out["loc_holiday"] + out["event_day"]
        return out


def calendar_day_features(days: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    return {"dow": days.dayofweek.to_numpy(), "dom": days.day.to_numpy(), "month": days.month.to_numpy(),
            "doy": days.dayofyear.to_numpy()}


def to_date(x) -> date:
    return pd.Timestamp(x).date()
