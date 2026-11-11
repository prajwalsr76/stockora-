"""Persistence: SQLite for local/dev, portable SQL for Postgres later.

Design rules
* Stock is a ledger. Current stock = SUM(stock_movements.qty). Nothing is overwritten.
* Every row carries store_id and every query filters on it (tenant isolation).
* Sales consume batches First-Expiry-First-Out (FEFO).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT, state_code TEXT NOT NULL,
  gstin TEXT, cash_on_hand REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL REFERENCES stores(id),
  sku TEXT, name TEXT NOT NULL, brand TEXT, category TEXT NOT NULL, unit TEXT,
  pack_size REAL, mrp REAL NOT NULL, hsn TEXT, gst_rate REAL NOT NULL DEFAULT 0,
  shelf_life_days INTEGER, active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(store_id, sku)
);
CREATE TABLE IF NOT EXISTS product_aliases (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
  alias TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'manual',
  UNIQUE(store_id, alias)
);
CREATE TABLE IF NOT EXISTS distributors (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, name TEXT NOT NULL,
  phone TEXT, gstin TEXT, state_code TEXT, lead_time_days INTEGER NOT NULL DEFAULT 2,
  delivery_weekdays TEXT NOT NULL DEFAULT '0,1,2,3,4,5', min_order_value REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS distributor_prices (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, distributor_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL, price REAL NOT NULL, scheme_buy INTEGER NOT NULL DEFAULT 0,
  scheme_free INTEGER NOT NULL DEFAULT 0, scheme_text TEXT, updated_at TEXT NOT NULL,
  UNIQUE(distributor_id, product_id)
);
CREATE TABLE IF NOT EXISTS purchases (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, distributor_id INTEGER,
  bill_no TEXT, bill_date TEXT, total REAL, status TEXT NOT NULL DEFAULT 'posted',
  source TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
  batch_no TEXT, expiry_date TEXT, cost_price REAL, received_at TEXT NOT NULL,
  purchase_id INTEGER
);
CREATE TABLE IF NOT EXISTS stock_movements (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
  batch_id INTEGER, qty REAL NOT NULL, kind TEXT NOT NULL, unit_price REAL,
  ref TEXT, day TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mov_store_prod ON stock_movements(store_id, product_id);
CREATE INDEX IF NOT EXISTS ix_mov_store_day ON stock_movements(store_id, day, kind);
CREATE INDEX IF NOT EXISTS ix_mov_batch ON stock_movements(batch_id);
CREATE TABLE IF NOT EXISTS sales_history (
  store_id INTEGER NOT NULL, product_id INTEGER NOT NULL, day TEXT NOT NULL,
  units REAL NOT NULL, revenue REAL, source TEXT,
  PRIMARY KEY(store_id, product_id, day)
);
CREATE TABLE IF NOT EXISTS stockout_days (
  store_id INTEGER NOT NULL, product_id INTEGER NOT NULL, day TEXT NOT NULL,
  PRIMARY KEY(store_id, product_id, day)
);
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, name TEXT NOT NULL, phone TEXT
);
CREATE TABLE IF NOT EXISTS credit_entries (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, customer_id INTEGER NOT NULL,
  amount REAL NOT NULL, note TEXT, day TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, distributor_id INTEGER NOT NULL,
  status TEXT NOT NULL, total REAL NOT NULL DEFAULT 0, message TEXT,
  created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT
);
CREATE TABLE IF NOT EXISTS order_lines (
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
  qty_suggested REAL NOT NULL, qty_final REAL, unit_price REAL NOT NULL, reason TEXT
);
CREATE TABLE IF NOT EXISTS owner_prefs (
  store_id INTEGER NOT NULL, product_id INTEGER NOT NULL, qty_bias REAL NOT NULL DEFAULT 1.0,
  edits INTEGER NOT NULL DEFAULT 0, accepts INTEGER NOT NULL DEFAULT 0,
  preferred_distributor_id INTEGER, PRIMARY KEY(store_id, product_id)
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, title TEXT NOT NULL,
  kind TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, number TEXT NOT NULL, doc_type TEXT NOT NULL,
  fy TEXT NOT NULL, seq INTEGER NOT NULL, day TEXT NOT NULL, created_at TEXT NOT NULL,
  customer_id INTEGER, customer_name TEXT, customer_phone TEXT, customer_gstin TEXT,
  customer_state_code TEXT, place_of_supply TEXT NOT NULL, supply_type TEXT NOT NULL,
  gross REAL NOT NULL, discount REAL NOT NULL, taxable REAL NOT NULL, cgst REAL NOT NULL,
  sgst REAL NOT NULL, igst REAL NOT NULL, round_off REAL NOT NULL, total REAL NOT NULL,
  paid_cash REAL NOT NULL DEFAULT 0, paid_upi REAL NOT NULL DEFAULT 0, udhaar REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active', cancelled_at TEXT, cancel_reason TEXT,
  UNIQUE(store_id, number), UNIQUE(store_id, doc_type, fy, seq)
);
CREATE INDEX IF NOT EXISTS ix_inv_store_day ON invoices(store_id, day);
CREATE TABLE IF NOT EXISTS invoice_lines (
  id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id), line_no INTEGER NOT NULL,
  product_id INTEGER NOT NULL, name TEXT NOT NULL, hsn TEXT, qty REAL NOT NULL, unit TEXT NOT NULL,
  unit_price REAL NOT NULL, mrp REAL, discount REAL NOT NULL, taxable REAL NOT NULL, gst_rate REAL NOT NULL,
  cgst REAL NOT NULL, sgst REAL NOT NULL, igst REAL NOT NULL, amount REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS credit_notes (
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, number TEXT NOT NULL, fy TEXT NOT NULL,
  seq INTEGER NOT NULL, invoice_id INTEGER NOT NULL REFERENCES invoices(id), day TEXT NOT NULL,
  created_at TEXT NOT NULL, reason TEXT NOT NULL, taxable REAL NOT NULL, cgst REAL NOT NULL,
  sgst REAL NOT NULL, igst REAL NOT NULL, round_off REAL NOT NULL, total REAL NOT NULL,
  refund_mode TEXT NOT NULL, UNIQUE(store_id, number), UNIQUE(store_id, fy, seq)
);
CREATE TABLE IF NOT EXISTS credit_note_lines (
  id INTEGER PRIMARY KEY, credit_note_id INTEGER NOT NULL REFERENCES credit_notes(id),
  invoice_line_id INTEGER NOT NULL, product_id INTEGER NOT NULL, qty REAL NOT NULL,
  taxable REAL NOT NULL, cgst REAL NOT NULL, sgst REAL NOT NULL, igst REAL NOT NULL, amount REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS llm_traces (
  id INTEGER PRIMARY KEY, store_id INTEGER, agent TEXT NOT NULL, model TEXT NOT NULL,
  prompt_tokens INTEGER, completion_tokens INTEGER, latency_ms INTEGER,
  cost_usd REAL, ok INTEGER NOT NULL, error TEXT, created_at TEXT NOT NULL
);
"""


class InsufficientStock(Exception):
    pass


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL") if str(path) != ":memory:" else None
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


# Columns added after the first release. connect() adds any that an older database is missing,
# so existing stockora.db files keep working without deleting data.
ADDED_COLUMNS = {
    "stores": [("address", "TEXT"), ("phone", "TEXT"),
               ("gst_scheme", "TEXT NOT NULL DEFAULT 'regular'"),   # regular | composition | unregistered
               ("invoice_prefix", "TEXT NOT NULL DEFAULT 'INV'"),
               ("size", "TEXT"),                                     # small | medium | large (wizard)
               ("is_demo", "INTEGER NOT NULL DEFAULT 0")],
    "products": [("barcode", "TEXT"),
                 ("loose", "INTEGER NOT NULL DEFAULT 0")],          # 1 = sold by weight/volume (kg or L)
}


def migrate(conn: sqlite3.Connection) -> None:
    for table, cols in ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                if (table, name) == ("stores", "is_demo"):   # databases made before this column: the seeded demo shop
                    conn.execute("UPDATE stores SET is_demo=1 WHERE name='Sharma General Store' AND city='Nashik'")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_product_barcode ON products(store_id, barcode) "
                 "WHERE barcode IS NOT NULL")
    conn.commit()


@dataclass
class Repo:
    """All data access for ONE store. store_id is fixed at construction (tenant isolation)."""

    conn: sqlite3.Connection
    store_id: int

    # ------------------------------------------------------------- helpers
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def q(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def audit(self, actor: str, action: str, detail: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO audit_log(store_id, actor, action, detail, created_at) VALUES (?,?,?,?,?)",
            (self.store_id, actor, action, json.dumps(detail or {}, default=str), now()),
        )

    # ------------------------------------------------------------- stores
    @staticmethod
    def create_store(conn: sqlite3.Connection, name: str, state_code: str, city: str = "",
                     gstin: str | None = None, cash_on_hand: float = 0) -> "Repo":
        cur = conn.execute(
            "INSERT INTO stores(name, city, state_code, gstin, cash_on_hand, created_at) VALUES (?,?,?,?,?,?)",
            (name, city, state_code, gstin, cash_on_hand, now()),
        )
        conn.commit()
        return Repo(conn, cur.lastrowid)

    @staticmethod
    def setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
        r = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    @staticmethod
    def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute("INSERT INTO app_settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))
        conn.commit()

    def store(self) -> sqlite3.Row:
        return self.one("SELECT * FROM stores WHERE id=?", self.store_id)

    def set_cash(self, amount: float) -> None:
        self.conn.execute("UPDATE stores SET cash_on_hand=? WHERE id=?", (amount, self.store_id))

    # ------------------------------------------------------------- products
    def add_product(self, **p: Any) -> int:
        cols = ["sku", "name", "brand", "category", "unit", "pack_size", "mrp", "hsn",
                "gst_rate", "shelf_life_days", "barcode", "loose"]
        p.setdefault("loose", 0)
        cur = self.conn.execute(
            f"INSERT INTO products(store_id, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
            (self.store_id, *[p.get(c) for c in cols]),
        )
        self.add_alias(cur.lastrowid, p["name"], "catalog")
        return cur.lastrowid

    def add_alias(self, product_id: int, alias: str, source: str = "manual") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO product_aliases(store_id, product_id, alias, source) VALUES (?,?,?,?)",
            (self.store_id, product_id, alias.strip().lower(), source),
        )

    def products(self, active_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM products WHERE store_id=?" + (" AND active=1" if active_only else "")
        return self.q(sql + " ORDER BY id", self.store_id)

    def product(self, product_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM products WHERE id=? AND store_id=?", product_id, self.store_id)

    def aliases(self) -> list[sqlite3.Row]:
        return self.q("SELECT product_id, alias FROM product_aliases WHERE store_id=?", self.store_id)

    # ------------------------------------------------------------- distributors
    def add_distributor(self, name: str, lead_time_days: int = 2, min_order_value: float = 0,
                        phone: str = "", gstin: str | None = None, state_code: str | None = None,
                        delivery_weekdays: str = "0,1,2,3,4,5") -> int:
        cur = self.conn.execute(
            "INSERT INTO distributors(store_id, name, phone, gstin, state_code, lead_time_days, "
            "delivery_weekdays, min_order_value) VALUES (?,?,?,?,?,?,?,?)",
            (self.store_id, name, phone, gstin, state_code, lead_time_days, delivery_weekdays, min_order_value),
        )
        return cur.lastrowid

    def distributors(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM distributors WHERE store_id=? ORDER BY id", self.store_id)

    def set_price(self, distributor_id: int, product_id: int, price: float,
                  scheme_buy: int = 0, scheme_free: int = 0, scheme_text: str | None = None,
                  updated_at: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO distributor_prices(store_id, distributor_id, product_id, price, scheme_buy, "
            "scheme_free, scheme_text, updated_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(distributor_id, product_id) DO UPDATE SET price=excluded.price, "
            "scheme_buy=excluded.scheme_buy, scheme_free=excluded.scheme_free, "
            "scheme_text=excluded.scheme_text, updated_at=excluded.updated_at",
            (self.store_id, distributor_id, product_id, price, scheme_buy, scheme_free,
             scheme_text, updated_at or now()),
        )

    def prices_for(self, product_id: int) -> list[sqlite3.Row]:
        return self.q(
            "SELECT dp.*, d.name AS distributor, d.lead_time_days, d.min_order_value, d.delivery_weekdays "
            "FROM distributor_prices dp JOIN distributors d ON d.id=dp.distributor_id "
            "WHERE dp.store_id=? AND dp.product_id=? ORDER BY dp.price",
            self.store_id, product_id,
        )

    # ------------------------------------------------------------- stock ledger
    def receive(self, product_id: int, qty: float, cost_price: float, day: date,
                expiry: date | None = None, batch_no: str | None = None,
                purchase_id: int | None = None, ref: str = "", kind: str = "purchase") -> int:
        if qty <= 0:
            raise ValueError("received qty must be positive")
        cur = self.conn.execute(
            "INSERT INTO batches(store_id, product_id, batch_no, expiry_date, cost_price, received_at, purchase_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (self.store_id, product_id, batch_no, expiry.isoformat() if expiry else None,
             cost_price, day.isoformat(), purchase_id),
        )
        self._move(product_id, cur.lastrowid, qty, kind, cost_price, ref, day)
        return cur.lastrowid

    def _move(self, product_id: int, batch_id: int | None, qty: float, kind: str,
              unit_price: float | None, ref: str, day: date) -> None:
        self.conn.execute(
            "INSERT INTO stock_movements(store_id, product_id, batch_id, qty, kind, unit_price, ref, day, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (self.store_id, product_id, batch_id, qty, kind, unit_price, ref, day.isoformat(), now()),
        )

    def stock(self, product_id: int) -> float:
        r = self.one("SELECT COALESCE(SUM(qty),0) s FROM stock_movements WHERE store_id=? AND product_id=?",
                     self.store_id, product_id)
        return float(r["s"])

    def stock_all(self) -> dict[int, float]:
        rows = self.q("SELECT product_id, SUM(qty) s FROM stock_movements WHERE store_id=? GROUP BY product_id",
                      self.store_id)
        return {r["product_id"]: float(r["s"]) for r in rows}

    def open_batches(self, product_id: int | None = None) -> list[dict]:
        """Batches with remaining qty > 0, earliest expiry first (FEFO)."""
        sql = (
            "SELECT b.*, COALESCE(SUM(m.qty),0) AS remaining FROM batches b "
            "LEFT JOIN stock_movements m ON m.batch_id=b.id AND m.store_id=b.store_id "
            "WHERE b.store_id=? " + ("AND b.product_id=? " if product_id else "") +
            "GROUP BY b.id HAVING remaining > 0 "
            "ORDER BY CASE WHEN b.expiry_date IS NULL THEN 1 ELSE 0 END, b.expiry_date, b.id"
        )
        args = (self.store_id, product_id) if product_id else (self.store_id,)
        return [dict(r) for r in self.q(sql, *args)]

    def sell(self, product_id: int, qty: float, unit_price: float, day: date, ref: str = "") -> None:
        """Record a sale, consuming batches FEFO. Rejects sales above recorded stock."""
        if qty <= 0:
            raise ValueError("sold qty must be positive")
        p = self.product(product_id)
        if p is None:
            raise KeyError(f"product {product_id} not in this store")
        if unit_price > p["mrp"] + 1e-9:
            raise ValueError(f"selling price {unit_price} exceeds MRP {p['mrp']} (Legal Metrology)")
        available = self.stock(product_id)
        if qty > available + 1e-9:
            raise InsufficientStock(f"{p['name']}: selling {qty}, only {available} in stock")
        left = qty
        for b in self.open_batches(product_id):
            take = min(left, b["remaining"])
            self._move(product_id, b["id"], -take, "sale", unit_price, ref, day)
            left -= take
            if left <= 1e-9:
                break
        if self.stock(product_id) <= 1e-9:
            self.mark_stockout(product_id, day)

    def write_off(self, batch_id: int, qty: float, day: date, reason: str = "expired") -> None:
        b = self.one("SELECT * FROM batches WHERE id=? AND store_id=?", batch_id, self.store_id)
        if b is None:
            raise KeyError("batch not found")
        self._move(b["product_id"], batch_id, -abs(qty), "expire" if reason == "expired" else "adjust",
                   b["cost_price"], reason, day)

    def mark_stockout(self, product_id: int, day: date) -> None:
        self.conn.execute("INSERT OR IGNORE INTO stockout_days(store_id, product_id, day) VALUES (?,?,?)",
                          (self.store_id, product_id, day.isoformat()))

    def daily_sales(self, since: date | None = None) -> list[sqlite3.Row]:
        """Units and revenue per product per day: ledger sales plus imported past sales."""
        cond = " AND day>=?" if since else ""
        args: list[Any] = [self.store_id] + ([since.isoformat()] if since else [])
        sql = (f"SELECT product_id, day, SUM(units) AS units, SUM(revenue) AS revenue FROM ("
               f"SELECT product_id, day, -qty AS units, -qty*unit_price AS revenue FROM stock_movements "
               f"WHERE store_id=? AND kind='sale'{cond} "
               f"UNION ALL SELECT product_id, day, units, revenue FROM sales_history WHERE store_id=?{cond}) "
               f"GROUP BY product_id, day ORDER BY day")
        return self.q(sql, *(args + args))

    def add_sales_history(self, rows: list[tuple[int, str, float, float | None]], source: str) -> int:
        """Past sales (product_id, ISO day, units, revenue) imported from a billing app. Not stock movements."""
        self.conn.executemany(
            "INSERT INTO sales_history(store_id, product_id, day, units, revenue, source) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(store_id, product_id, day) DO UPDATE SET units=units+excluded.units, "
            "revenue=COALESCE(revenue,0)+COALESCE(excluded.revenue,0)",
            [(self.store_id, pid, d, u, rev, source) for pid, d, u, rev in rows])
        return len(rows)

    def adjust_to_count(self, product_id: int, counted: float, day: date, actor: str = "owner") -> float:
        """Physical stock count: set stock to `counted`. Returns the difference applied."""
        if counted < 0:
            raise ValueError("count cannot be negative")
        diff = counted - self.stock(product_id)
        if abs(diff) < 1e-9:
            return 0.0
        if diff > 0:
            p = self.product(product_id)
            cost = self.one("SELECT AVG(cost_price) c FROM batches WHERE store_id=? AND product_id=?",
                            self.store_id, product_id)["c"] or round(p["mrp"] * 0.88, 2)
            exp = day + timedelta(days=p["shelf_life_days"]) if p["shelf_life_days"] else None
            self.receive(product_id, diff, cost, day, expiry=exp, batch_no="COUNT", ref="stock count", kind="adjust")
        else:
            left = -diff
            for b in self.open_batches(product_id):
                take = min(left, b["remaining"])
                self._move(product_id, b["id"], -take, "adjust", b["cost_price"], "stock count", day)
                left -= take
                if left <= 1e-9:
                    break
            if self.stock(product_id) <= 1e-9:
                self.mark_stockout(product_id, day)
        self.audit(actor, "stock_count", {"product_id": product_id, "counted": counted, "diff": diff})
        return diff

    def stockout_set(self) -> set[tuple[int, str]]:
        return {(r["product_id"], r["day"]) for r in
                self.q("SELECT product_id, day FROM stockout_days WHERE store_id=?", self.store_id)}

    # ------------------------------------------------------------- udhaar (customer credit)
    def add_customer(self, name: str, phone: str = "") -> int:
        return self.conn.execute("INSERT INTO customers(store_id, name, phone) VALUES (?,?,?)",
                                 (self.store_id, name, phone)).lastrowid

    def credit(self, customer_id: int, amount: float, day: date, note: str = "") -> None:
        """amount > 0: goods given on credit; amount < 0: customer paid back."""
        self.conn.execute(
            "INSERT INTO credit_entries(store_id, customer_id, amount, note, day, created_at) VALUES (?,?,?,?,?,?)",
            (self.store_id, customer_id, amount, note, day.isoformat(), now()),
        )

    def receivables(self) -> list[dict]:
        rows = self.q(
            "SELECT c.id, c.name, c.phone, SUM(e.amount) AS due, MIN(CASE WHEN e.amount>0 THEN e.day END) AS oldest, "
            "MAX(CASE WHEN e.amount<0 THEN e.day END) AS last_paid "
            "FROM customers c JOIN credit_entries e ON e.customer_id=c.id "
            "WHERE c.store_id=? GROUP BY c.id HAVING due > 0.009 ORDER BY due DESC",
            self.store_id,
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- orders
    def create_order(self, distributor_id: int, lines: list[dict], message: str) -> int:
        total = sum(l["qty"] * l["unit_price"] for l in lines)
        oid = self.conn.execute(
            "INSERT INTO orders(store_id, distributor_id, status, total, message, created_at) VALUES (?,?,?,?,?,?)",
            (self.store_id, distributor_id, "draft", round(total, 2), message, now()),
        ).lastrowid
        for l in lines:
            self.conn.execute(
                "INSERT INTO order_lines(order_id, product_id, qty_suggested, unit_price, reason) VALUES (?,?,?,?,?)",
                (oid, l["product_id"], l["qty"], l["unit_price"], l.get("reason", "")),
            )
        return oid

    def order(self, order_id: int) -> dict | None:
        o = self.one("SELECT o.*, d.name AS distributor FROM orders o JOIN distributors d ON d.id=o.distributor_id "
                     "WHERE o.id=? AND o.store_id=?", order_id, self.store_id)
        if o is None:
            return None
        lines = self.q("SELECT ol.*, p.name FROM order_lines ol JOIN products p ON p.id=ol.product_id "
                       "WHERE ol.order_id=?", order_id)
        return {**dict(o), "lines": [dict(l) for l in lines]}

    def orders(self, status: str | None = None) -> list[dict]:
        sql = "SELECT id FROM orders WHERE store_id=?" + (" AND status=?" if status else "") + " ORDER BY id DESC"
        args = (self.store_id, status) if status else (self.store_id,)
        return [self.order(r["id"]) for r in self.q(sql, *args)]

    def pending_order_qty(self) -> dict[int, float]:
        rows = self.q(
            "SELECT ol.product_id, SUM(COALESCE(ol.qty_final, ol.qty_suggested)) q FROM order_lines ol "
            "JOIN orders o ON o.id=ol.order_id WHERE o.store_id=? AND o.status IN ('approved','sent') "
            "GROUP BY ol.product_id", self.store_id)
        return {r["product_id"]: float(r["q"]) for r in rows}

    # ------------------------------------------------------------- owner preference learning
    def prefs(self) -> dict[int, dict]:
        return {r["product_id"]: dict(r) for r in
                self.q("SELECT * FROM owner_prefs WHERE store_id=?", self.store_id)}

    def learn_from_decision(self, product_id: int, suggested: float, final: float,
                            distributor_id: int | None = None, alpha: float = 0.3) -> None:
        """Exponentially smooth the owner's edit ratio into a per-product quantity bias."""
        cur = self.one("SELECT * FROM owner_prefs WHERE store_id=? AND product_id=?", self.store_id, product_id)
        bias = cur["qty_bias"] if cur else 1.0
        edited = abs(final - suggested) > 1e-9
        if suggested > 0:
            ratio = max(0.0, min(3.0, final / suggested))
            bias = (1 - alpha) * bias + alpha * bias * ratio
        self.conn.execute(
            "INSERT INTO owner_prefs(store_id, product_id, qty_bias, edits, accepts, preferred_distributor_id) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(store_id, product_id) DO UPDATE SET qty_bias=excluded.qty_bias, "
            "edits=owner_prefs.edits+excluded.edits, accepts=owner_prefs.accepts+excluded.accepts, "
            "preferred_distributor_id=COALESCE(excluded.preferred_distributor_id, owner_prefs.preferred_distributor_id)",
            (self.store_id, product_id, round(bias, 4), int(edited), int(not edited), distributor_id),
        )

    # ------------------------------------------------------------- documents & traces
    def add_document(self, title: str, kind: str, body: str) -> int:
        return self.conn.execute(
            "INSERT INTO documents(store_id, title, kind, body, created_at) VALUES (?,?,?,?,?)",
            (self.store_id, title, kind, body, now())).lastrowid

    def documents(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM documents WHERE store_id=? ORDER BY id", self.store_id)

    def log_trace(self, agent: str, model: str, prompt_tokens: int, completion_tokens: int,
                  latency_ms: int, cost_usd: float, ok: bool, error: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO llm_traces(store_id, agent, model, prompt_tokens, completion_tokens, latency_ms, "
            "cost_usd, ok, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.store_id, agent, model, prompt_tokens, completion_tokens, latency_ms, cost_usd,
             int(ok), error, now()),
        )
