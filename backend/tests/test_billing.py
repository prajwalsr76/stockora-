"""Counter billing: GST maths, numbering, stock (FEFO), udhaar, cancel, credit notes, PDFs and the API."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from stockora import billing, invoice_pdf
from stockora.billing import BillingError
from stockora.db import InsufficientStock, Repo, connect
from stockora.india import gstin_checksum
from stockora.seed import build_demo_store

DAY = date(2026, 11, 7)


@pytest.fixture
def shop():
    repo, _ = build_demo_store(connect(), DAY, days=45)
    products = {p["name"]: p for p in billing.counter_products(repo)}
    return repo, products


def pid(products, name):
    return products[name]["id"]


def gstin(state: str) -> str:
    first14 = f"{state}AAACB1234C1Z"
    return first14 + gstin_checksum(first14)


def test_gst_breakup_rounding_and_number(shop):
    repo, P = shop
    inv = billing.create_invoice(repo, [
        {"product_id": pid(P, "Tata Salt 1kg"), "qty": 2},                                # 0% GST
        {"product_id": pid(P, "Maggi 2-Minute Masala Noodles 70g"), "qty": 3, "discount": 2},   # 5% GST
        {"product_id": pid(P, "Sugar (loose)"), "qty": 750, "qty_unit": "g"},             # loose, 5% GST
    ], bill_discount=1, day=DAY)
    assert inv["number"] == "SGS/26-27/0001" and len(inv["number"]) <= 16
    assert inv["doc_type"] == "tax_invoice" and inv["supply_type"] == "intra"
    for ln in inv["lines"]:
        tax = Decimal(str(ln["cgst"])) + Decimal(str(ln["sgst"]))
        assert Decimal(str(ln["taxable"])) + tax == Decimal(str(ln["amount"]))
        assert abs(ln["cgst"] - ln["sgst"]) <= 0.011 and ln["igst"] == 0
    sugar = next(ln for ln in inv["lines"] if ln["name"] == "Sugar (loose)")
    assert sugar["qty"] == 0.75 and sugar["unit"] == "kg"
    net = sum(Decimal(str(ln["amount"])) for ln in inv["lines"])
    assert Decimal(str(inv["total"])) == net.quantize(Decimal("1"), rounding="ROUND_HALF_UP")
    assert inv["gross"] - inv["discount"] == pytest.approx(float(net))
    assert inv["discount"] == pytest.approx(3.0)
    assert inv["paid_cash"] == inv["total"]          # no payment given -> cash
    second = billing.create_invoice(repo, [{"product_id": pid(P, "Tata Salt 1kg"), "qty": 1}], day=DAY)
    assert second["number"] == "SGS/26-27/0002"
    nxt = billing.create_invoice(repo, [{"product_id": pid(P, "Tata Salt 1kg"), "qty": 1}], day=date(2027, 4, 1))
    assert nxt["number"] == "SGS/27-28/0001"          # new financial year, new series


def test_stock_leaves_earliest_expiry_batch_first(shop):
    repo, P = shop
    milk = pid(P, "Mother Dairy Dahi 400g")
    batches = repo.open_batches(milk)
    first = batches[0]
    take = min(2.0, first["remaining"])
    before = repo.stock(milk)
    billing.create_invoice(repo, [{"product_id": milk, "qty": take}], day=DAY)
    assert repo.stock(milk) == pytest.approx(before - take)
    after = {b["id"]: b["remaining"] for b in repo.open_batches(milk)}
    assert after.get(first["id"], 0) == pytest.approx(first["remaining"] - take)


def test_rules_reject_bad_bills(shop):
    repo, P = shop
    salt = pid(P, "Tata Salt 1kg")
    with pytest.raises(BillingError, match="above MRP"):
        billing.create_invoice(repo, [{"product_id": salt, "qty": 1, "unit_price": 29}], day=DAY)
    with pytest.raises(BillingError, match="whole packs"):
        billing.create_invoice(repo, [{"product_id": salt, "qty": 1.5}], day=DAY)
    with pytest.raises(BillingError, match="sold by kg"):
        billing.create_invoice(repo, [{"product_id": pid(P, "Sugar (loose)"), "qty": 1, "qty_unit": "ml"}], day=DAY)
    with pytest.raises(BillingError, match="payments add up"):
        billing.create_invoice(repo, [{"product_id": salt, "qty": 1}], payment={"cash": 20}, day=DAY)
    with pytest.raises(BillingError, match="udhaar needs a customer"):
        billing.create_invoice(repo, [{"product_id": salt, "qty": 1}], payment={"udhaar": 28}, day=DAY)
    with pytest.raises(BillingError, match="discount"):
        billing.create_invoice(repo, [{"product_id": salt, "qty": 1, "discount": 30}], day=DAY)
    with pytest.raises(BillingError, match="GSTIN is not valid"):
        billing.create_invoice(repo, [{"product_id": salt, "qty": 1}], customer={"gstin": "27AAACB1234C1Z0"}, day=DAY)
    assert billing.list_invoices(repo) == []


def test_a_failed_bill_changes_nothing(shop):
    repo, P = shop
    salt, maggi = pid(P, "Tata Salt 1kg"), pid(P, "Maggi 2-Minute Masala Noodles 70g")
    s0, m0 = repo.stock(salt), repo.stock(maggi)
    with pytest.raises(InsufficientStock, match="correct the count"):
        billing.create_invoice(repo, [{"product_id": salt, "qty": 1}, {"product_id": maggi, "qty": m0 + 5}], day=DAY)
    assert repo.stock(salt) == s0 and repo.stock(maggi) == m0
    assert billing.list_invoices(repo) == []
    assert billing.create_invoice(repo, [{"product_id": salt, "qty": 1}], day=DAY)["number"].endswith("/0001")


def test_split_payment_with_udhaar_goes_to_customer_account(shop):
    repo, P = shop
    inv = billing.create_invoice(repo, [{"product_id": pid(P, "Amul Pure Ghee 1L"), "qty": 1}],
                                 payment={"cash": 100, "udhaar": P["Amul Pure Ghee 1L"]["mrp"] - 100},
                                 customer={"name": "Test Customer", "phone": "9876543210"}, day=DAY)
    due = {r["name"]: r["due"] for r in repo.receivables()}
    assert due["Test Customer"] == pytest.approx(inv["udhaar"]) and inv["customer_id"]
    assert inv["paid_cash"] == 100


def test_registered_buyer_from_another_state_gets_igst(shop):
    repo, P = shop
    inv = billing.create_invoice(repo, [{"product_id": pid(P, "Surf Excel Easy Wash 1kg"), "qty": 2}],
                                 customer={"name": "Hotel Kaveri", "gstin": gstin("29")}, day=DAY)
    assert inv["supply_type"] == "inter" and inv["place_of_supply"] == "29"
    assert inv["igst"] > 0 and inv["cgst"] == inv["sgst"] == 0
    same = billing.create_invoice(repo, [{"product_id": pid(P, "Surf Excel Easy Wash 1kg"), "qty": 1}],
                                  customer={"name": "Local Co", "gstin": gstin("27")}, day=DAY)
    assert same["supply_type"] == "intra" and same["cgst"] > 0 and same["igst"] == 0


def test_cancel_restores_stock_udhaar_and_sales_history(shop):
    repo, P = shop
    ghee = pid(P, "Amul Pure Ghee 1L")
    before_stock = repo.stock(ghee)
    before_sales = sum(r["units"] for r in repo.daily_sales(DAY) if r["product_id"] == ghee)
    inv = billing.create_invoice(repo, [{"product_id": ghee, "qty": 2}], payment={"udhaar": 2 * P["Amul Pure Ghee 1L"]["mrp"]},
                                 customer={"name": "Cancel Test"}, day=DAY)
    assert repo.stock(ghee) == before_stock - 2
    out = billing.cancel_invoice(repo, inv["id"], "wrong item", day=DAY)
    assert out["status"] == "cancelled" and not out["can_cancel"]
    assert repo.stock(ghee) == pytest.approx(before_stock)
    assert sum(r["units"] for r in repo.daily_sales(DAY) if r["product_id"] == ghee) == pytest.approx(before_sales)
    assert "Cancel Test" not in {r["name"] for r in repo.receivables()}
    with pytest.raises(BillingError, match="already cancelled"):
        billing.cancel_invoice(repo, inv["id"], "again", day=DAY)
    old = billing.create_invoice(repo, [{"product_id": ghee, "qty": 1}], day=DAY)
    with pytest.raises(BillingError, match="month it was issued"):
        billing.cancel_invoice(repo, old["id"], "late", day=DAY + timedelta(days=30))


def test_credit_note_partial_return(shop):
    repo, P = shop
    maggi = pid(P, "Maggi 2-Minute Masala Noodles 70g")
    inv = billing.create_invoice(repo, [{"product_id": maggi, "qty": 4}], payment={"udhaar": 4 * 15},
                                 customer={"name": "Return Test"}, day=DAY)
    line = inv["lines"][0]
    stock = repo.stock(maggi)
    cn = billing.create_credit_note(repo, inv["id"], [{"invoice_line_id": line["id"], "qty": 1}], "damaged pack",
                                    refund_mode="udhaar", restock=True, day=DAY)
    assert cn["number"] == "CN/26-27/0001" and cn["invoice"]["number"] == inv["number"]
    assert cn["lines"][0]["amount"] == pytest.approx(line["amount"] / 4, abs=0.01)
    assert cn["cgst"] + cn["sgst"] == pytest.approx((line["cgst"] + line["sgst"]) / 4, abs=0.02)
    assert repo.stock(maggi) == pytest.approx(stock + 1)
    assert {r["name"]: r["due"] for r in repo.receivables()}["Return Test"] == pytest.approx(60 - cn["total"])
    again = billing.get_invoice(repo, inv["id"])
    assert again["lines"][0]["returned_qty"] == 1 and not again["can_cancel"]
    with pytest.raises(BillingError, match="can still be returned"):
        billing.create_credit_note(repo, inv["id"], [{"invoice_line_id": line["id"], "qty": 4}], "too many", day=DAY)
    stock = repo.stock(maggi)
    billing.create_credit_note(repo, inv["id"], [{"invoice_line_id": line["id"], "qty": 1}], "torn", restock=False, day=DAY)
    assert repo.stock(maggi) == pytest.approx(stock)          # damaged: written off, not back on the shelf
    summary = billing.daily_summary(repo, DAY)
    assert summary["returns"] == 2 and summary["units"]["pcs"] == pytest.approx(2)


def test_composition_and_unregistered_shops_issue_bills_without_gst(shop):
    repo, P = shop
    billing.update_store_settings(repo, gst_scheme="composition")
    bos = billing.create_invoice(repo, [{"product_id": pid(P, "Maggi 2-Minute Masala Noodles 70g"), "qty": 2}], day=DAY)
    assert bos["doc_type"] == "bill_of_supply" and bos["title"] == "Bill of Supply"
    assert bos["cgst"] == bos["sgst"] == bos["igst"] == 0 and bos["taxable"] == bos["total"] == 30
    with pytest.raises(BillingError, match="only a GST-registered shop"):
        billing.create_invoice(repo, [{"product_id": pid(P, "Tata Salt 1kg"), "qty": 1}],
                               customer={"gstin": gstin("27")}, day=DAY)
    billing.update_store_settings(repo, gst_scheme="unregistered", gstin="")
    assert billing.create_invoice(repo, [{"product_id": pid(P, "Tata Salt 1kg"), "qty": 1}], day=DAY)["title"] == "Bill"
    with pytest.raises(BillingError, match="needs its GSTIN"):
        billing.update_store_settings(repo, gst_scheme="regular")
    with pytest.raises(BillingError, match="must match"):
        billing.update_store_settings(repo, gst_scheme="regular", gstin=gstin("29"), state_code="27")


def test_daily_summary_keeps_packs_and_kilos_apart(shop):
    repo, P = shop
    billing.create_invoice(repo, [{"product_id": pid(P, "Tata Salt 1kg"), "qty": 3},
                                  {"product_id": pid(P, "Rice Kolam (loose)"), "qty": 2.5, "qty_unit": "kg"}], day=DAY)
    s = billing.daily_summary(repo, DAY)
    assert s["bills"] == 1 and s["units"]["pcs"] == 3 and s["units"]["kg"] == 2.5
    assert {(c["category"], c["unit"]) for c in s["by_category"]} == {("staples", "pcs"), ("staples", "kg")}


def test_pdfs_render_for_every_layout(shop):
    repo, P = shop
    inv = billing.create_invoice(repo, [{"product_id": pid(P, "Tata Salt 1kg"), "qty": 1},
                                        {"product_id": pid(P, "Sugar (loose)"), "qty": 0.5}], day=DAY)
    shop_info = billing.store_settings(repo)
    sizes = {}
    for layout in ("a4", "80mm", "58mm"):
        pdf = invoice_pdf.render(inv, shop_info, layout)
        assert pdf[:5] == b"%PDF-" and len(pdf) > 1000
        sizes[layout] = len(pdf)
    cn = billing.create_credit_note(repo, inv["id"], [{"invoice_line_id": inv["lines"][0]["id"], "qty": 1}], "return",
                                    day=DAY)
    assert invoice_pdf.render(cn, shop_info, "80mm", kind="credit_note")[:5] == b"%PDF-"
    assert invoice_pdf.rupees_in_words(125430) == "Rupees One Lakh Twenty Five Thousand Four Hundred Thirty Only"


def test_barcode_link_and_lookup(shop):
    repo, P = shop
    salt = pid(P, "Tata Salt 1kg")
    billing.set_barcode(repo, salt, "8901234567890")
    assert billing.product_by_barcode(repo, "8901234567890")["id"] == salt
    with pytest.raises(BillingError, match="already linked"):
        billing.set_barcode(repo, pid(P, "Maggi 2-Minute Masala Noodles 70g"), "8901234567890")


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE stores (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT, state_code TEXT NOT NULL, "
                "gstin TEXT, cash_on_hand REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
    old.execute("INSERT INTO stores(name, state_code, created_at) VALUES ('Old Shop', '27', '2026-01-01')")
    old.commit()
    old.close()
    conn = connect(path)
    st = Repo(conn, 1).store()
    assert st["gst_scheme"] == "regular" and st["invoice_prefix"] == "INV" and st["name"] == "Old Shop"


def test_billing_api(shop, monkeypatch):
    pytest.importorskip("fastapi")
    import threading

    from fastapi.testclient import TestClient

    from stockora.api import main

    repo, P = shop
    state = main.AppState.__new__(main.AppState)
    state.lock, state.repo, state.conn = threading.RLock(), repo, repo.conn
    monkeypatch.setattr(main, "_state", state)
    monkeypatch.delenv("STOCKORA_API_KEY", raising=False)
    c = TestClient(main.app)
    salt = pid(P, "Tata Salt 1kg")
    assert c.post(f"/api/products/{salt}/barcode", json={"barcode": "8901111111117"}).status_code == 200
    assert c.get("/api/billing/barcode/8901111111117").json()["id"] == salt
    assert c.get("/api/billing/barcode/0000").status_code == 404
    r = c.post("/api/invoices", json={"lines": [{"product_id": salt, "qty": 2}], "payment": {"upi": 56},
                                      "customer": {"name": "Asha", "phone": "9876500000"}})
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["paid_upi"] == 56
    assert c.post("/api/invoices", json={"lines": [{"product_id": salt, "qty": 1, "unit_price": 99}]}).status_code == 422
    assert c.post("/api/invoices", json={"lines": [{"product_id": salt, "qty": 100000}]}).status_code == 409
    pdf = c.get(f"/api/invoices/{inv['id']}/pdf?layout=58mm")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    wa = c.get(f"/api/invoices/{inv['id']}/whatsapp").json()
    assert wa["url"].startswith("https://wa.me/919876500000?text=") and inv["number"] in wa["text"]
    assert c.get("/api/invoices?q=Asha").json()[0]["number"] == inv["number"]
    assert c.get("/api/invoices/summary").json()["bills"] >= 0
    cn = c.post(f"/api/invoices/{inv['id']}/credit-notes",
                json={"items": [{"invoice_line_id": inv["lines"][0]["id"], "qty": 1}], "reason": "return"})
    assert cn.status_code == 200 and c.get(f"/api/credit-notes/{cn.json()['id']}/pdf").status_code == 200
    assert c.post(f"/api/invoices/{inv['id']}/cancel", json={"reason": "test"}).status_code == 422
    assert c.put("/api/store/settings", json={"gst_scheme": "regular", "gstin": "27AAACB1234C1Z0"}).status_code == 422
    assert c.get("/api/store/settings").json()["invoice_prefix"] == "SGS"
