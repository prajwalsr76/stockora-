from datetime import date

import pytest

from stockora.db import Repo, connect
from stockora.seed import build_demo_store
from stockora.service import StoreBrain

TODAY = date(2026, 11, 7)   # day before Diwali 2026: festival logic is exercised


@pytest.fixture(scope="session")
def seeded():
    """A full demo store with a trained forecaster. Treat as read-only."""
    repo, hist = build_demo_store(connect(), TODAY, days=260)
    brain = StoreBrain(repo, TODAY).refresh()
    return brain, hist


@pytest.fixture
def fresh():
    """A fresh seeded store for tests that write."""
    repo, _ = build_demo_store(connect(), TODAY, days=200)
    return StoreBrain(repo, TODAY).refresh()


@pytest.fixture
def tiny():
    """Minimal store: 2 products, 2 distributors, no history."""
    conn = connect()
    repo = Repo.create_store(conn, "Test Store", "27", "Pune", cash_on_hand=10000)
    milk = repo.add_product(sku="M", name="Amul Taaza Toned Milk 500ml", category="dairy", unit="ml",
                            pack_size=500, mrp=28, hsn="0401", gst_rate=0, shelf_life_days=2)
    salt = repo.add_product(sku="S", name="Tata Salt 1kg", category="staples", unit="kg",
                            pack_size=1, mrp=28, hsn="2501", gst_rate=0, shelf_life_days=730)
    fast = repo.add_distributor("Fast Traders", lead_time_days=1)
    slow = repo.add_distributor("Cheap Wholesale", lead_time_days=3, min_order_value=0)
    repo.set_price(fast, milk, 26.0)
    repo.set_price(slow, milk, 24.0)
    repo.set_price(fast, salt, 25.0)
    repo.set_price(slow, salt, 24.5, scheme_buy=25, scheme_free=1, scheme_text="25 + 1 free")
    conn.commit()
    return repo, {"milk": milk, "salt": salt, "fast": fast, "slow": slow}
