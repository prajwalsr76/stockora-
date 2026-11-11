"""Area trends privacy, market calendars, and forecaster robustness on real-data shapes."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from stockora.area_trends import CLIP, Report, aggregate, local_change, make_report, trends_for_shop
from stockora.forecasting.calendars import EcuadorCalendar, IndiaCalendar
from stockora.forecasting.model import Forecaster, baseline_dow_mean, rmsle, wape


class TestAreaTrends:
    def test_report_contains_no_raw_numbers_and_is_clipped(self):
        r = make_report("4220", "2026-W45", "beverages", local_change(5000, 1000), epsilon=1e9,
                        rng=np.random.default_rng(0))
        assert set(r.__dict__) == {"token", "area", "week", "category", "change"}
        assert abs(r.change - CLIP) < 1e-6             # +400% clipped to +50%: influence is bounded

    def test_tokens_are_unlinkable(self):
        a = make_report("4220", "W1", "dairy", 0.1)
        b = make_report("4220", "W2", "dairy", 0.1)
        assert a.token != b.token

    def test_k_anonymity_withholds_small_groups(self):
        reps = [Report(f"t{i}", "A", "W1", "dairy", 0.2) for i in range(4)]
        assert aggregate(reps, k=5) == []
        reps.append(Report("t9", "A", "W1", "dairy", 0.2))
        assert len(aggregate(reps, k=5)) == 1

    def test_duplicate_tokens_count_once(self):
        reps = [Report("same", "A", "W1", "dairy", 0.3)] * 10
        assert aggregate(reps, k=2) == []

    def test_noise_hides_individuals_but_crowd_signal_survives(self):
        rng = np.random.default_rng(1)
        reps = [make_report("A", "W1", "snacks", 0.3, epsilon=2.0, rng=rng) for _ in range(400)]
        assert np.std([r.change for r in reps]) > 0.5          # each report is very noisy
        t = aggregate(reps, k=5)[0]
        assert t.median_change > 0.1 and t.share_up > 0.6       # but the area trend is clear
        assert "more" in t.message()
        assert trends_for_shop([t], "A", "W1") and not trends_for_shop([t], "B", "W1")

    def test_no_baseline_no_report(self):
        assert local_change(10, 0) is None


def _toy_history(days=200, series=6, promo=False):
    rng = np.random.default_rng(0)
    rows = []
    start = date(2024, 1, 1)
    for s in range(series):
        level = 5 + 10 * s
        for d in range(days):
            day = start + timedelta(days=d)
            mu = level * (1.3 if day.weekday() >= 5 else 1.0)
            p = int(rng.random() < 0.1) if promo else 0
            rows.append({"day": day, "sku": f"s{s}", "category": "dairy" if s % 2 else "snacks",
                         "sales": float(rng.poisson(mu * (1.5 if p else 1.0))), "promo": p,
                         "city": "Quito", "state": "Pichincha", "exclude": False, "stockout": False})
    return pd.DataFrame(rows)


class TestForecaster:
    def test_beats_baseline_on_learnable_pattern_with_promotions(self):
        h = _toy_history(promo=True)
        origin = date(2024, 6, 1)
        fc = Forecaster(horizon=7).fit(h, train_end=origin, step=2)
        pred = fc.predict(h, origin)
        truth = h.set_index(["sku", "day"])["sales"]
        a = [truth[(s, d)] for s, d in zip(pred["sku"], pred["day"])]
        base = baseline_dow_mean(h, origin, horizon=7)
        b = [truth[(s, d)] for s, d in zip(base["sku"], base["day"])]
        assert wape(a, pred["forecast"]) < wape(b, base["forecast"])

    def test_dead_month_then_restart_does_not_explode(self):
        h = _toy_history(days=150, series=3)
        dead = (h["sku"] == "s2") & (h["day"] >= date(2024, 4, 1)) & (h["day"] < date(2024, 5, 1))
        h.loc[dead, "sales"] = 0.0
        spike = (h["sku"] == "s2") & (h["day"] == date(2024, 5, 2))
        h.loc[spike, "sales"] = 5000.0
        fc = Forecaster(horizon=7).fit(h, step=1)
        pred = fc.predict(h)
        assert np.isfinite(pred["forecast"]).all() and pred["forecast"].max() < 20000

    def test_excluded_days_are_not_zero_demand(self):
        h = _toy_history(days=120, series=2)
        closed = h["day"].isin([date(2024, 3, 1), date(2024, 3, 8)])
        h.loc[closed, ["sales", "exclude"]] = [0.0, True]
        fc = Forecaster(horizon=7).fit(h, step=1)
        assert fc.predict(h)["forecast"].min() > 1

    def test_metrics(self):
        assert rmsle([0, 0], [0, 0]) == 0 and abs(wape([10, 10], [5, 15]) - 0.5) < 1e-9


class TestCalendars:
    def test_ecuador_local_regional_national_and_transferred(self):
        hol = pd.DataFrame([
            {"date": "2017-08-10", "type": "Holiday", "locale": "National", "locale_name": "Ecuador",
             "description": "Primer Grito", "transferred": True},     # moved: not a day off
            {"date": "2017-08-11", "type": "Transfer", "locale": "National", "locale_name": "Ecuador",
             "description": "Traslado", "transferred": False},
            {"date": "2017-08-05", "type": "Holiday", "locale": "Local", "locale_name": "Quito",
             "description": "Fundacion", "transferred": False},
            {"date": "2017-08-07", "type": "Holiday", "locale": "Regional", "locale_name": "Guayas",
             "description": "Prov", "transferred": False},
        ])
        meta = pd.DataFrame({"city": ["Quito", "Guayaquil"], "state": ["Pichincha", "Guayas"], "category": ["x", "x"]})
        days = pd.date_range("2017-08-01", "2017-08-20")
        t = EcuadorCalendar(hol).table(meta, days)
        i = lambda d: list(days).index(pd.Timestamp(d))  # noqa: E731
        assert t["nat_holiday"][0, i("2017-08-10")] == 0 and t["nat_holiday"][1, i("2017-08-11")] == 1
        assert t["loc_holiday"][0, i("2017-08-05")] == 1 and t["loc_holiday"][1, i("2017-08-05")] == 0
        assert t["reg_holiday"][1, i("2017-08-07")] == 1 and t["reg_holiday"][0, i("2017-08-07")] == 0
        assert t["payday"][0, i("2017-08-15")] == 1 and t["payday"][0, i("2017-08-31") if False else i("2017-08-16")] == 0
        assert t["days_to_nat_holiday"][0, i("2017-08-09")] == 2

    def test_india_calendar_shapes(self):
        meta = pd.DataFrame({"category": ["festive", "stationery"]})
        t = IndiaCalendar().table(meta, pd.date_range("2026-10-25", "2026-11-10"))
        assert t["fest_mult"].shape == (2, 17) and t["fest_mult"][0].max() > 2 and t["fest_mult"][1].max() == 1
