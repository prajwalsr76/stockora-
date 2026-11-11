"""Build a demo store: real Indian products, synthetic (labelled) history.

Distributors and customers are fictional. Product names, brands, HSN codes and
GST slabs are real; MRPs are approximate and get overwritten by real bills.
"""
from __future__ import annotations

import sqlite3
from collections import deque
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .db import Repo, now
from .forecasting.simulator import StoreProfile, demand_frame, gut_feel_sales
from .india import gstin_checksum, load_catalog

DISTRIBUTORS = [
    # name, lead_time, min_order, price factor vs typical, delivery weekdays
    ("Shree Ganesh Traders", 1, 1500, 1.00, "0,1,2,3,4,5"),
    ("Nashik FMCG Distributors", 2, 3000, 0.975, "1,3,5"),
    ("Balaji Wholesale Mart", 3, 5000, 0.96, "2,5"),
]
SCHEMES = {  # sku -> (distributor index, buy, free, text)
    "SKU010": (1, 24, 2, "Parle-G: 24 + 2 free"),
    "SKU050": (0, 96, 6, "Maggi 70g: 96 + 6 free"),
    "SKU041": (2, 12, 1, "Fortune Sunflower: 12 + 1 free"),
    "SKU069": (1, 10, 1, "Surf Excel 1kg: 10 + 1 free"),
    "SKU018": (0, 48, 4, "Dairy Milk 23g: 48 + 4 free"),
    "SKU039": (2, 25, 1, "Tata Salt: 25 + 1 free"),
}
CIRCULARS = [
    ("Nashik FMCG Distributors - November scheme circular", "scheme",
     "Diwali scheme valid 1-15 November: Parle-G 250g buy 24 get 2 free. Surf Excel Easy Wash 1kg buy 10 get 1 free. "
     "Delivery on Monday, Wednesday and Friday. Minimum order value Rs 3000. Near-expiry biscuits accepted for "
     "replacement up to 7 days before expiry."),
    ("Shree Ganesh Traders - terms", "terms",
     "Daily delivery except Sunday. Minimum order Rs 1500. Payment within 7 days, 1% cash discount on same-day payment. "
     "Maggi 70g scheme: 96 plus 6 free. Dairy Milk 23g: 48 plus 4 free. Dairy and bread returns accepted only if "
     "reported on the day of delivery."),
    ("Balaji Wholesale Mart - price list note", "terms",
     "Lowest wholesale rates, delivery Wednesday and Saturday, lead time 3 days, minimum order Rs 5000. "
     "Fortune Sunflower oil 1L: 12 plus 1 free. Tata Salt 1kg: 25 plus 1 free. No returns on oil and staples."),
    ("Store policy - credit (udhaar)", "policy",
     "Credit limit Rs 3000 per regular customer. Remind customers with dues older than 30 days. "
     "No new credit if dues are older than 60 days."),
]


def _fake_gstin(state: str, i: int) -> str:
    first14 = f"{state}ABCDE{1000 + i:04d}F1Z"
    return first14 + gstin_checksum(first14)


def build_demo_store(conn: sqlite3.Connection, today: date | None = None, days: int = 400,
                     profile: StoreProfile | None = None) -> tuple[Repo, pd.DataFrame]:
    """Create a store with products, distributors, prices, ledger history, udhaar and documents.

    Returns (repo, history dataframe with true demand for evaluation).
    """
    today = today or date.today()
    profile = profile or StoreProfile()
    repo = Repo.create_store(conn, profile.name, profile.state_code, profile.city,
                             _fake_gstin(profile.state_code, 0), cash_on_hand=18000)
    rng = np.random.default_rng(profile.seed)
    catalog = load_catalog()

    sku_to_pid = {}
    for item in catalog:
        sku_to_pid[item.sku] = repo.add_product(
            sku=item.sku, name=item.name, brand=item.brand, category=item.category, unit=item.unit,
            pack_size=item.pack_size, mrp=item.mrp, hsn=item.hsn, gst_rate=item.gst_rate,
            shelf_life_days=item.shelf_life_days)

    dist_ids = [repo.add_distributor(n, lt, mov, phone=f"+91 98{rng.integers(10**7, 10**8)}",
                                     gstin=_fake_gstin(profile.state_code, i + 1),
                                     state_code=profile.state_code, delivery_weekdays=wd)
                for i, (n, lt, mov, _, wd) in enumerate(DISTRIBUTORS)]
    stamp = (today - timedelta(days=3)).isoformat()
    for item in catalog:
        carried = rng.choice(len(DISTRIBUTORS), size=rng.integers(1, 4), replace=False)
        for k in carried:
            factor = DISTRIBUTORS[k][3] * rng.uniform(0.985, 1.015)
            buy = free = 0
            text = None
            if item.sku in SCHEMES and SCHEMES[item.sku][0] == k:
                _, buy, free, text = SCHEMES[item.sku]
            repo.set_price(dist_ids[k], sku_to_pid[item.sku], round(item.cost_price * factor, 2),
                           buy, free, text, stamp)
        if item.sku in SCHEMES:  # make sure the scheme distributor carries it
            k, buy, free, text = SCHEMES[item.sku]
            repo.set_price(dist_ids[k], sku_to_pid[item.sku], round(item.cost_price * DISTRIBUTORS[k][3], 2),
                           buy, free, text, stamp)

    start = today - timedelta(days=days)
    hist = gut_feel_sales(demand_frame(start, today - timedelta(days=1), profile))
    _replay_ledger(repo, hist, sku_to_pid, {c.sku: c for c in catalog}, rng)

    # Realistic working capital: about 4 days of recent sales value.
    recent = hist[hist["day"] >= pd.Timestamp(today - timedelta(days=30))]
    mrp = {c.sku: c.mrp for c in catalog}
    daily_rev = float((recent["sales"] * recent["sku"].map(mrp)).sum() / 30)
    repo.set_cash(round(daily_rev * 4, -2))

    # A few over-bought perishables, as happens in real stores: these drive expiry alerts.
    for sku, qty, days_to_expiry in [("SKU003", 30, 3), ("SKU008", 18, 2), ("SKU007", 15, 4), ("SKU090", 20, 6)]:
        item = next(c for c in catalog if c.sku == sku)
        repo.receive(sku_to_pid[sku], qty, item.cost_price, today - timedelta(days=1),
                     expiry=today + timedelta(days=days_to_expiry), batch_no=f"OVR-{sku}")

    # A slow item the owner bought once and never sold: shows up as dead stock.
    dead = repo.add_product(sku="SKU091", name="Dabur Chyawanprash 1kg", brand="Dabur", category="personal_care",
                            unit="kg", pack_size=1, mrp=395, hsn="3004", gst_rate=5, shelf_life_days=730)
    repo.receive(dead, 6, 330, today - timedelta(days=58), expiry=today + timedelta(days=600), batch_no="CHY-01")

    # Loose staples sold by weight at the counter (price per kg; prices approximate, synthetic sales).
    for sku, name, mrp, hsn, gst, life, per_day in [
            ("LOOSE01", "Sugar (loose)", 46, "1701", 5, 365, 6.0),
            ("LOOSE02", "Toor Dal (loose)", 160, "0713", 0, 270, 2.5),
            ("LOOSE03", "Rice Kolam (loose)", 62, "1006", 0, 365, 5.0)]:
        pid = repo.add_product(sku=sku, name=name, brand="", category="staples", unit="kg", pack_size=1,
                               mrp=mrp, hsn=hsn, gst_rate=gst, shelf_life_days=life, loose=1)
        repo.receive(pid, per_day * 40, round(mrp * 0.88, 2), today - timedelta(days=31),
                     expiry=today + timedelta(days=life - 31), batch_no=f"{sku}-A")
        for k in range(30, 0, -1):
            sold = round(float(rng.gamma(4.0, per_day / 4.0)), 2)
            if sold > 0:
                repo.sell(pid, sold, mrp, today - timedelta(days=k), ref="seed")
    repo.conn.execute("UPDATE stores SET address=?, gst_scheme='regular', invoice_prefix='SGS', is_demo=1, "
                      "size='medium' WHERE id=?",
                      ("Sample address, Main Road", repo.store_id))

    names = ["Ramesh Patil", "Sunita Jadhav", "Imran Shaikh", "Priya Kulkarni", "Vikas More", "Anjali Deshmukh"]
    for i, n in enumerate(names):
        cid = repo.add_customer(n, f"+91 97{rng.integers(10**7, 10**8)}")
        given = today - timedelta(days=int(rng.integers(5, 75)))
        repo.credit(cid, float(rng.integers(6, 32) * 100), given, "monthly ration")
        if i % 2 == 0:
            repo.credit(cid, -float(rng.integers(2, 6) * 100), given + timedelta(days=3), "part payment")
    for title, kind, body in CIRCULARS:
        repo.add_document(title, kind, body)
    repo.conn.commit()
    hist["product_id"] = hist["sku"].map(sku_to_pid)
    return repo, hist


def _replay_ledger(repo: Repo, hist: pd.DataFrame, sku_to_pid: dict, items: dict, rng) -> None:
    """Write purchases (weekly, with batches/expiry) and FEFO sales into the ledger in bulk."""
    sid, ts = repo.store_id, now()
    batch_rows, move_rows, out_rows = [], [], []
    next_batch_id = (repo.one("SELECT COALESCE(MAX(id),0) m FROM batches")["m"] or 0) + 1
    for sku, g in hist.groupby("sku", sort=False):
        g = g.sort_values("day").reset_index(drop=True)
        pid, item = sku_to_pid[sku], items[sku]
        cost = item.cost_price
        queue: deque[list] = deque()   # [batch_id, remaining, expiry]
        sales = g["sales"].to_numpy()
        days = g["day"].dt.date.to_numpy()
        for t, d in enumerate(days):
            if t % 7 == 0:
                upcoming = sales[t:t + 7].sum()
                held = sum(b[1] for b in queue)
                qty = float(max(0.0, np.ceil(upcoming * rng.uniform(1.0, 1.25) - held)))
                if t + 7 >= len(days):          # final delivery: leave a realistic mix of closing stock,
                    avg = sales[max(0, t - 28):t].mean() if t else sales[:7].mean()   # 1.5 to 12 days of sales
                    qty = float(max(0.0, np.ceil(sales[t:].sum() + avg * rng.uniform(1.5, 12.0) - held)))
                if qty > 0:
                    exp = d + timedelta(days=item.shelf_life_days)
                    bid = next_batch_id
                    next_batch_id += 1
                    batch_rows.append((bid, sid, pid, f"B{bid:06d}", exp.isoformat(), cost, d.isoformat()))
                    move_rows.append((sid, pid, bid, qty, "purchase", cost, "seed", d.isoformat(), ts))
                    queue.append([bid, qty, exp])
            # discard expired stock
            while queue and queue[0][2] < d:
                bid, rem, _ = queue.popleft()
                if rem > 0:
                    move_rows.append((sid, pid, bid, -rem, "expire", cost, "seed", d.isoformat(), ts))
            left = float(sales[t])
            if left > sum(b[1] for b in queue):
                left = sum(b[1] for b in queue)
            while left > 1e-9 and queue:
                take = min(left, queue[0][1])
                move_rows.append((sid, pid, queue[0][0], -take, "sale", item.mrp, "seed", d.isoformat(), ts))
                queue[0][1] -= take
                left -= take
                if queue[0][1] <= 1e-9:
                    queue.popleft()
            if g["stockout"].iat[t]:
                out_rows.append((sid, pid, d.isoformat()))
    repo.conn.executemany("INSERT INTO batches(id, store_id, product_id, batch_no, expiry_date, cost_price, "
                          "received_at) VALUES (?,?,?,?,?,?,?)", batch_rows)
    repo.conn.executemany("INSERT INTO stock_movements(store_id, product_id, batch_id, qty, kind, unit_price, ref, "
                          "day, created_at) VALUES (?,?,?,?,?,?,?,?,?)", move_rows)
    repo.conn.executemany("INSERT OR IGNORE INTO stockout_days(store_id, product_id, day) VALUES (?,?,?)", out_rows)
