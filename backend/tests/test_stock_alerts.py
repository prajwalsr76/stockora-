from datetime import timedelta

from stockora.agents import stock_alerts as ml
from stockora.db import Repo, connect

from .conftest import TODAY


def shop():
    conn = connect()
    repo = Repo.create_store(conn, "Shop", "27", cash_on_hand=10000)
    oil = repo.add_product(sku="O", name="Fortune Sunflower Refined Oil 1L", category="oil_ghee", mrp=165,
                           gst_rate=5, shelf_life_days=270)
    salt = repo.add_product(sku="S", name="Tata Salt 1kg", category="staples", mrp=28, gst_rate=0, shelf_life_days=730)
    return repo, oil, salt


def test_missed_requests_suggest_new_and_understocked_items():
    repo, oil, salt = shop()
    for k, item in enumerate(["Amul Kool Kesar 200ml", "amul kool kesar", "Amul Kool Kesar 200ml", "Tata Salt 1kg"]):
        ml.log_request(repo, item, TODAY - timedelta(days=k))
    for k in range(3):
        ml.log_request(repo, "tata salt", TODAY - timedelta(days=5 + k))
    res = {r["item"]: r for r in ml.missed_request_suggestions(repo, TODAY)}
    assert "Tata Salt 1kg" in res and "runs out" in res["Tata Salt 1kg"]["action"]
    kool = next(r for k, r in res.items() if "kool" in k.lower())
    assert kool["asked_times"] == 3 and "Consider stocking" in kool["action"]


def test_count_mismatch_from_stock_counts():
    repo, oil, _ = shop()
    repo.receive(oil, 40, 150, TODAY - timedelta(days=10))
    for k in range(5):
        repo.sell(oil, 2, 165, TODAY - timedelta(days=9 - k))
    repo.adjust_to_count(oil, 27, TODAY - timedelta(days=3))     # 30 expected, 27 found
    repo.adjust_to_count(oil, 25, TODAY)                          # 27 expected, 25 found
    res = ml.count_mismatch(repo, TODAY)
    assert res[0]["missing_units"] == 5 and res[0]["counts_short"] == 2 and "value_lost" not in res[0]


def test_udhaar_risk_and_summary():
    repo, oil, salt = shop()
    a, b = repo.add_customer("Old Dues"), repo.add_customer("Good Payer")
    repo.credit(a, 4000, TODAY - timedelta(days=80))
    repo.credit(b, 500, TODAY - timedelta(days=5))
    repo.credit(b, -200, TODAY - timedelta(days=2))
    risk = {r["name"]: r for r in ml.udhaar_risk(repo, TODAY)}
    assert risk["Old Dues"]["risk"] == "high" and risk["Good Payer"]["risk"] == "low"
    assert set(ml.alerts_summary(repo, TODAY)) == {"missed_requests", "count_mismatch", "udhaar_risk"}
