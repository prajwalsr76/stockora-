"""Rohlik loader rules (censoring, closed days, promotions) and the LightGBM / XGBoost candidates."""
import numpy as np
import pandas as pd
import pytest

from stockora.datasets import rohlik


@pytest.fixture
def tiny_rohlik(tmp_path):
    days = pd.date_range("2024-01-01", periods=10)
    rows = []
    for uid, wh in ((1, "Prague_1"), (2, "Brno_1")):
        for i, d in enumerate(days):
            if uid == 2 and i == 4:
                continue                                   # not offered that day: no row
            rows.append({"unique_id": uid, "date": d.date().isoformat(), "warehouse": wh, "total_orders": 5000.0,
                         "sales": 10.0 + i, "sell_price_main": 20.0, "availability": 0.5 if i == 3 else 1.0,
                         **{f"type_{k}_discount": (0.2 if (k == 2 and i == 6) else 0.0) for k in range(7)}})
    pd.DataFrame(rows).to_csv(tmp_path / "sales_train.csv", index=False)
    pd.DataFrame({"unique_id": [1, 2], "product_unique_id": [1, 2], "name": ["Bread_1", "Apple_1"],
                  "L1_category_name_en": ["Bakery", "Fruit and vegetable"], "L2_category_name_en": ["B2", "F2"],
                  "L3_category_name_en": ["B3", "F3"], "L4_category_name_en": ["B4", "F4"],
                  "warehouse": ["Prague_1", "Brno_1"]}).to_csv(tmp_path / "inventory.csv", index=False)
    cal = [{"date": d.date().isoformat(), "holiday_name": "", "holiday": int(i == 8), "shops_closed": int(i == 9),
            "winter_school_holidays": 0, "school_holidays": 0, "warehouse": wh}
           for wh in ("Prague_1", "Brno_1") for i, d in enumerate(days)]
    pd.DataFrame(cal).to_csv(tmp_path / "calendar.csv", index=False)
    return tmp_path


def test_rohlik_loader_marks_censored_closed_and_missing_days(tiny_rohlik):
    hist, cal = rohlik.load(tiny_rohlik, warehouses=None)
    assert set(hist["sku"]) == {"Prague_1|1", "Brno_1|2"}
    assert set(hist["category"]) == {"bakery", "produce"}
    p1 = hist[hist["sku"] == "Prague_1|1"].reset_index(drop=True)
    assert p1.loc[3, "stockout"] and not p1.loc[2, "stockout"]           # availability 0.5 -> censored
    assert p1.loc[9, "exclude"] and not p1.loc[8, "exclude"]             # shops closed -> excluded
    assert p1.loc[6, "promo"] == pytest.approx(0.2) and p1.loc[5, "promo"] == 0
    assert "total_orders" not in hist                                    # not known in advance
    assert len(hist[hist["sku"] == "Brno_1|2"]) == 9                     # missing day stays missing
    meta = pd.DataFrame({"warehouse": ["Prague_1", "Brno_1"], "category": ["bakery", "produce"]})
    t = cal.table(meta, pd.date_range("2024-01-01", periods=10))
    assert t["holiday"][0, 8] == 1 and t["closed_next"][0, 8] == 1 and t["days_to_holiday"][0, 7] == 1


def test_boosters_train_on_the_same_table():
    pytest.importorskip("lightgbm")
    pytest.importorskip("xgboost")
    from evals.model_competition import boosters
    rng = np.random.default_rng(0)
    n = 2000
    tr = pd.DataFrame({"a": rng.normal(size=n), "cat": rng.integers(0, 3, n)})
    tr["y"] = np.exp(0.5 * tr["a"] + 0.3 * tr["cat"]) * rng.gamma(2.0, 0.5, n)
    rows = tr.iloc[:50].copy()
    out = boosters(tr, rows, ["a", "cat"], ["cat"])
    assert set(out) == {"lgb_poisson", "lgb_tweedie", "xgb_poisson", "xgb_tweedie"}
    for v in out.values():
        assert v.shape == (50,) and (v >= 0).all()
        assert np.corrcoef(v, rows["y"])[0, 1] > 0.3                     # learned the signal
