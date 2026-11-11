"""Saved forecasts (fast start, fallback) and the first-run setup: new shop, starter items, spreadsheet import."""
import json
import pickle
import time
from datetime import date
from types import SimpleNamespace

import pytest

from stockora import model_store, onboarding


def fake_brain(**kw):
    base = dict(forecasts={1: [1.0, 2.0]}, sigma={1: 0.5}, observed={1: 30}, mode="learning", history_days=30,
                today=date(2026, 11, 11))
    base.update(kw)
    return SimpleNamespace(**base)


def test_model_store_saves_versions_and_falls_back(tmp_path):
    path = tmp_path / "store1.pkl"
    m1 = model_store.save(fake_brain(), path, "xgb_tweedie")
    m2 = model_store.save(fake_brain(forecasts={1: [9.0]}), path, "xgb_tweedie")
    assert (m1["version"], m2["version"]) == (1, 2) and (tmp_path / "store1.prev").exists()
    b = fake_brain(forecasts={})
    assert model_store.load(b, path)["version"] == 2 and b.forecasts == {1: [9.0]}
    path.write_bytes(b"broken")                              # a damaged file falls back to the previous version
    b = fake_brain(forecasts={})
    meta = model_store.load(b, path)
    assert meta["file"] == "store1.prev" and b.forecasts == {1: [1.0, 2.0]}
    assert model_store.is_current(meta, date(2026, 11, 11)) and not model_store.is_current(meta, date(2026, 11, 12))


def test_column_detection_and_manual_fix(tmp_path):
    f = tmp_path / "items.csv"
    f.write_text("Item Name,M.R.P.,Tax %,Closing Stock,Rack\nTata Salt 1kg,28,0,12,A1\nMy Own Pickle,90,12,4,B2\n")
    pv = onboarding.preview(f, "products")
    fields = {c["name"]: c["field"] for c in pv["columns"]}
    assert fields == {"Item Name": "name", "M.R.P.": "mrp", "Tax %": "gst_rate", "Closing Stock": "stock", "Rack": None}
    assert pv["rows"] == 2 and pv["missing"] == []
    df, unknown = onboarding.read_table(f, {"Rack": "category", "Tax %": None})
    assert "category" in df and "gst_rate" not in df and unknown == ["Tax %"]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    pytest.importorskip("fastapi")
    import os

    from fastapi.testclient import TestClient
    db = tmp_path_factory.mktemp("app") / "app.db"
    os.environ["STOCKORA_DB"] = str(db)
    os.environ.pop("STOCKORA_STORE_ID", None)
    os.environ.pop("STOCKORA_API_KEY", None)
    from stockora.api import main
    main._state = None
    c = TestClient(main.app)
    yield c, main
    main._state = None
    os.environ.pop("STOCKORA_DB", None)


def test_first_run_setup_flow(client, tmp_path):
    c, main = client
    st = c.get("/api/store").json()
    assert st["is_demo"] and c.get("/api/status").json()["model"]["version"] >= 1
    demo_id = st["id"]

    r = c.post("/api/shops", json={"name": "Patil Kirana", "city": "Pune", "state_code": "27", "size": "small",
                                   "gst_scheme": "unregistered"})
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    st = c.get("/api/store").json()
    assert st["id"] == new_id and not st["is_demo"] and st["products"] == 0
    assert c.post("/api/shops", json={"name": "Bad GST", "state_code": "27", "gst_scheme": "regular"}).status_code == 422

    cat = c.get("/api/catalog").json()
    salt = next(x for x in cat if x["name"] == "Tata Salt 1kg")
    r = c.post("/api/shops/current/items", json={"items": [
        {"sku": salt["sku"], "stock": 20},
        {"name": "Sugar (loose)", "mrp": 46, "gst_rate": 5, "category": "staples", "loose": True, "stock": 25},
        {"name": "No Price Thing"}]})
    assert r.json()["added"] == 2 and len(r.json()["skipped"]) == 1

    items = tmp_path / "items.csv"
    items.write_text("Item Name,M.R.P.,Tax %,Closing Stock,Rack\nMaggi 2-Minute Masala Noodles 70g,15,5,48,A1\n")
    pv = c.post("/api/import/preview?kind=products", files={"file": ("items.csv", items.read_bytes(), "text/csv")}).json()
    assert pv["missing"] == [] and pv["rows"] == 1
    r = c.post("/api/import?kind=products&mapping=" + json.dumps({"Rack": None}),
               files={"file": ("items.csv", items.read_bytes(), "text/csv")})
    assert r.status_code == 200 and r.json()["added"] == 1
    sales = tmp_path / "sales.csv"
    sales.write_text("Bill Date,Item,Qty\n01/11/2026,Maggi 2-Minute Masala Noodles 70g,6\n02/11/2026,Tata Salt 1kg,3\n")
    r = c.post("/api/import?kind=sales", files={"file": ("sales.csv", sales.read_bytes(), "text/csv")})
    assert r.json()["added"] == 2
    assert c.post("/api/import/preview?kind=sales", files={"file": ("x.pdf", b"%PDF", "application/pdf")}).status_code == 422

    names = {p["name"] for p in c.get("/api/billing/products").json()}
    assert names == {"Tata Salt 1kg", "Sugar (loose)", "Maggi 2-Minute Masala Noodles 70g"}
    for _ in range(60):                                       # background retrain picks up the new items
        if not c.get("/api/status").json()["model"]["retraining"]:
            break
        time.sleep(1)
    assert {r["name"] for r in c.get("/api/stock").json()} == names

    assert c.post(f"/api/shops/{demo_id}/open").status_code == 200
    assert c.get("/api/store").json()["is_demo"]
    shops = c.get("/api/shops").json()
    assert [s["active"] for s in shops] == [True, False] and shops[1]["name"] == "Patil Kirana"

    t0 = time.time()                                          # restart: saved forecasts load at once
    restarted = main.AppState()
    assert time.time() - t0 < 5 and restarted.repo.store_id == demo_id
    path = model_store.path_for(main.Path(main.os.environ["STOCKORA_DB"]), demo_id)
    assert pickle.loads(path.read_bytes())["meta"]["learner"] in ("xgb_tweedie", "sk_bag")
