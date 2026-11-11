"""Real-shop path: cold start, onboarding from billing-app style files, stock counts, pilot report."""
from datetime import date, timedelta

import pandas as pd
import pytest

from stockora import onboarding
from stockora.db import Repo, connect
from stockora.onboard import main as onboard_cli
from stockora.pilot import period_metrics, report
from stockora.service import MODEL_MIN_DAYS, StoreBrain, history_frame

from .conftest import TODAY


def _new_shop():
    conn = connect()
    repo = Repo.create_store(conn, "New Shop", "27", cash_on_hand=20000)
    maggi = repo.add_product(sku="SKU050", name="Maggi 2-Minute Masala Noodles 70g", category="instant_food",
                             mrp=15, gst_rate=5, shelf_life_days=270)
    local = repo.add_product(sku=None, name="Local Namkeen 200g", category="snacks", mrp=40, gst_rate=5,
                             shelf_life_days=90)
    return conn, repo, maggi, local


class TestColdStart:
    def test_brand_new_shop_gets_forecasts_without_history(self):
        _, repo, maggi, local = _new_shop()
        brain = StoreBrain(repo, TODAY).refresh()
        assert brain.mode == "learning" and brain.history_days == 0
        assert all(len(brain.forecasts[p]) == 14 and min(brain.forecasts[p]) > 0 for p in (maggi, local))

    def test_history_starts_at_first_movement_not_400_days_ago(self):
        _, repo, maggi, _ = _new_shop()
        repo.receive(maggi, 100, 13, TODAY - timedelta(days=10))
        for k in range(10, 0, -1):
            repo.sell(maggi, 8, 15, TODAY - timedelta(days=k))
        h = history_frame(repo, today=TODAY)
        assert len(h) == 10 and h["sales"].min() == 8

    def test_recent_sales_dominate_after_four_weeks(self):
        _, repo, maggi, _ = _new_shop()
        repo.receive(maggi, 2000, 13, TODAY - timedelta(days=40))
        for k in range(35, 0, -1):
            repo.sell(maggi, 5, 15, TODAY - timedelta(days=k))
        brain = StoreBrain(repo, TODAY).refresh()
        assert 4 <= brain.forecasts[maggi][0] <= 7    # its own 5/day, not the catalog's 30/day
        assert brain.observed[maggi] == 28

    def test_new_item_without_sales_is_ordered_cautiously(self):
        _, repo, maggi, local = _new_shop()
        d = repo.add_distributor("Om Sai", lead_time_days=1)
        repo.set_price(d, local, 33)
        brain = StoreBrain(repo, TODAY).refresh()
        line = next(l for l in brain.order_plan().lines if l.product_id == local)
        assert "no sales recorded yet" in line.reason
        assert line.qty <= sum(brain.forecasts[local][:8]) * 0.5 + 1

    def test_switches_to_model_with_enough_history(self, seeded):
        assert seeded[0].mode == "model" and seeded[0].history_days >= MODEL_MIN_DAYS


class TestOnboarding:
    def _files(self, folder):
        pd.DataFrame({
            "Item Name": ["PARLE G 250GM", "Amul Taaza 500 ml", "Shree Ganesh Farsan 250g"],
            "Category": ["Biscuits", "Milk", "Namkeen"],
            "Sale Price": [25, 28, 60], "Purchase Price": [21.5, 26.2, 48],
            "Tax Rate": ["GST@5%", "Exempted", "GST@5%"], "HSN": [1905, 401, ""],
            "Current Stock": [40, 0, 15], "Remarks": ["", "", "local"],
        }).to_excel(folder / "products.xlsx", index=False)
        pd.DataFrame({"Supplier Name": ["Om Sai Agencies"], "Mobile No": ["9876500001"],
                      "GSTIN": ["27AAPFU0939F1ZX"], "Lead Time Days": [1]}).to_csv(folder / "distributors.csv", index=False)
        pd.DataFrame({"Distributor": ["Om Sai Agencies", "Om Sai Agencies"], "Item Name": ["parle g", "amul taaza"],
                      "Purchase Price": [21.5, 26.2], "Scheme Buy": [24, 0], "Scheme Free": [2, 0]}
                     ).to_csv(folder / "prices.csv", index=False)
        rows = [{"Date": (TODAY - timedelta(days=k)).strftime("%d/%m/%Y"), "Item Name": n, "Qty": q}
                for k in range(1, 21) for n, q in [("PARLE G 250GM", 10), ("Amul Taaza 500 ml", 25), ("Unknown Thing", 1)]]
        pd.DataFrame(rows).to_csv(folder / "sales.csv", index=False)

    def test_import_folder_end_to_end(self, tmp_path):
        self._files(tmp_path)
        conn = connect()
        repo = Repo.create_store(conn, "Patil Kirana", "27", cash_on_hand=25000)
        reports = {r.step: r for r in onboarding.onboard_folder(repo, tmp_path, TODAY)}
        prods = {p["name"]: p for p in repo.products()}
        assert reports["products"].added == 3 and "Remarks" in reports["products"].unknown_columns
        assert prods["Amul Taaza 500 ml"]["hsn"] == "0401" and prods["Amul Taaza 500 ml"]["gst_rate"] == 0
        assert prods["PARLE G 250GM"]["category"] == "snacks" and prods["PARLE G 250GM"]["gst_rate"] == 5
        assert repo.stock(prods["PARLE G 250GM"]["id"]) == 40          # opening stock only
        assert any("checksum" in w for w in reports["distributors"].warnings)
        assert reports["prices"].added == 2
        assert reports["sales history"].added == 40
        assert any("Unknown Thing" in s for s in reports["sales history"].skipped)
        assert (tmp_path / "onboarding_report.json").exists()
        brain = StoreBrain(repo, TODAY).refresh()
        assert brain.history_days == 20 and brain.mode == "learning"
        assert 20 <= sum(brain.forecasts[prods["Amul Taaza 500 ml"]["id"]][:1]) <= 40

    def test_cli_create_and_status(self, tmp_path, capsys):
        self._files(tmp_path)
        db = tmp_path / "shop.db"
        assert onboard_cli(["create", "--db", str(db), "--name", "Patil Kirana", "--state", "27",
                            "--cash", "25000", "--folder", str(tmp_path)]) == 0
        assert onboard_cli(["status", "--db", str(db)]) == 0
        out = capsys.readouterr().out
        assert "Patil Kirana" in out and "learning" in out
        assert onboard_cli(["create", "--db", str(db), "--name", "X", "--state", "99"]) == 2

    @pytest.mark.parametrize("raw,expected", [
        ("06/11/2026", date(2026, 11, 6)), ("2026-11-06", date(2026, 11, 6)), ("6-Nov-2026", date(2026, 11, 6)),
        ("11/26", date(2026, 11, 30)), ("garbage", None)])
    def test_dates_are_day_first(self, raw, expected):
        assert onboarding.parse_day(raw) == expected

    def test_flexible_headers(self, tmp_path):
        p = tmp_path / "x.csv"
        pd.DataFrame({"Item Name": ["a"], "Qty (Nos)": [1], "GST @ 5%": [5], "Bill Date": ["01/11/2026"]}).to_csv(p, index=False)
        df, unknown = onboarding.read_table(p)
        assert {"name", "qty", "gst_rate", "date"} <= set(df.columns) and unknown == []


class TestDailyUse:
    def test_stock_count_correction_both_ways(self):
        _, repo, maggi, _ = _new_shop()
        repo.receive(maggi, 50, 13, TODAY - timedelta(days=3))
        assert repo.adjust_to_count(maggi, 44, TODAY) == -6 and repo.stock(maggi) == 44
        assert repo.adjust_to_count(maggi, 60, TODAY) == 16 and repo.stock(maggi) == 60
        assert repo.adjust_to_count(maggi, 0, TODAY) == -60 and (maggi, TODAY.isoformat()) in repo.stockout_set()
        with pytest.raises(ValueError):
            repo.adjust_to_count(maggi, -1, TODAY)

    def test_history_counts_for_forecast_but_not_stock(self):
        _, repo, maggi, _ = _new_shop()
        repo.add_sales_history([(maggi, (TODAY - timedelta(days=2)).isoformat(), 9, 135.0)], "test")
        assert repo.stock(maggi) == 0
        assert sum(r["units"] for r in repo.daily_sales()) == 9


class TestPilotReport:
    def test_metrics_and_report(self):
        _, repo, maggi, _ = _new_shop()
        start = TODAY - timedelta(days=14)
        repo.receive(maggi, 60, 13, start, expiry=TODAY + timedelta(days=200))
        for k in range(14):
            d = start + timedelta(days=k)
            if repo.stock(maggi) >= 5:
                repo.sell(maggi, 5, 15, d)
        m = period_metrics(repo, start, start + timedelta(days=7))
        assert m["units_sold_per_day"] == 5 and m["stockout_day_rate"] == 0 and "revenue_per_day" not in m
        text = report(repo, start, start + timedelta(days=7), TODAY)
        assert "Shadow" in text and "Assisted" in text and "case study" in text
