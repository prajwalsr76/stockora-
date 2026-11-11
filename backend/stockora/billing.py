"""Counter billing: GST invoices, bills of supply, cancellations and credit notes.

How a bill works
  1. The owner picks items (search or barcode), quantities (whole packs, or kg/g/L/ml for loose items),
     optional discounts and how the customer pays (cash, UPI, udhaar or a mix).
  2. `create_invoice` checks everything, computes GST, gives the next number in the shop's series and,
     in ONE database transaction, saves the invoice, takes the goods out of stock batch by batch
     (earliest expiry first, FEFO) and adds any udhaar to the customer's account.
  3. Because stock changes at once, run-out dates, expiry alerts and the restock list change at once.
     The demand forecast itself is retrained on the next refresh, using these sales.

GST rules used (CGST Act s.31 and Rule 46; Legal Metrology rules for MRP)
  * Retail prices include GST. Taxable value = price after discount / (1 + rate); tax = the rest.
  * Same state (always the case for a counter sale) -> CGST + SGST, half each.
    A registered buyer from another state (GSTIN with a different state code) -> IGST.
  * A packaged item can never be sold above its MRP. For loose items the shop's price per kg or
    litre is stored in the MRP column and the same check applies.
  * Invoice numbers: one consecutive series per financial year (April-March), at most 16 characters,
    for example INV/26-27/0001. Credit notes have their own series, CN/26-27/0001.
  * A shop under the composition scheme issues a "bill of supply" and cannot collect tax; an
    unregistered shop issues a plain bill. Neither shows GST.
  * A bill can be cancelled only in the month it was issued (before the monthly GST return is filed).
    After that, or for a partial return, a credit note is issued instead.

Reversals are written as positive `sale` movements on the same batches and the original sale day, so
every sales total in the app (forecast history, pilot report, alerts) nets them out automatically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .db import InsufficientStock, Repo, now
from .india import STATE_CODES, money, validate_gstin

ZERO = Decimal("0.00")
LOOSE_UNITS = {"kg": ("kg", 1), "g": ("kg", 1000), "l": ("L", 1), "ml": ("L", 1000)}
DOC_TYPES = {"regular": "tax_invoice", "composition": "bill_of_supply", "unregistered": "bill"}
DOC_TITLES = {"tax_invoice": "Tax Invoice", "bill_of_supply": "Bill of Supply", "bill": "Bill"}
PAYMENT_MODES = ("cash", "upi", "udhaar")


class BillingError(ValueError):
    """A problem the owner can fix (wrong quantity, price above MRP, payment does not add up...)."""


@dataclass
class _Line:
    product: dict
    qty: float
    unit: str
    unit_price: Decimal
    gross: Decimal
    line_discount: Decimal
    discount: Decimal = ZERO
    taxable: Decimal = ZERO
    gst_rate: float = 0.0
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    igst: Decimal = ZERO
    amount: Decimal = ZERO


# ------------------------------------------------------------------ helpers
def financial_year(day: date) -> str:
    """'26-27' for any day from 1 Apr 2026 to 31 Mar 2027."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def clean_prefix(prefix: str | None) -> str:
    p = re.sub(r"[^A-Z0-9]", "", (prefix or "INV").upper())[:5]
    return p or "INV"


def to_rupee(x: Decimal) -> Decimal:
    return x.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def store_settings(repo: Repo) -> dict:
    st = dict(repo.store())
    st["gst_scheme"] = st.get("gst_scheme") or "regular"
    st["invoice_prefix"] = clean_prefix(st.get("invoice_prefix"))
    st["state"] = STATE_CODES.get(st["state_code"], st["state_code"])
    st["doc_type"] = DOC_TYPES[st["gst_scheme"]]
    return st


def update_store_settings(repo: Repo, **changes) -> dict:
    allowed = {"name", "address", "phone", "city", "gstin", "state_code", "gst_scheme", "invoice_prefix"}
    sets = {k: v for k, v in changes.items() if k in allowed and v is not None}
    if "gst_scheme" in sets and sets["gst_scheme"] not in DOC_TYPES:
        raise BillingError("GST scheme must be regular, composition or unregistered")
    if "state_code" in sets and sets["state_code"] not in STATE_CODES:
        raise BillingError(f"unknown state code {sets['state_code']}")
    if "invoice_prefix" in sets:
        sets["invoice_prefix"] = clean_prefix(sets["invoice_prefix"])
    if "name" in sets and not str(sets["name"]).strip():
        raise BillingError("shop name cannot be empty")
    if sets.get("gstin"):
        g = sets["gstin"].strip().upper()
        check = validate_gstin(g)
        if not check.valid:
            raise BillingError(f"GSTIN is not valid: {check.reason}")
        sets["gstin"] = g
        sets.setdefault("state_code", check.state_code)
        if sets["state_code"] != check.state_code:
            raise BillingError("the GSTIN's first two digits must match the shop's state code")
    elif "gstin" in sets:
        sets["gstin"] = None
    scheme = sets.get("gst_scheme", store_settings(repo)["gst_scheme"])
    gstin = sets["gstin"] if "gstin" in sets else repo.store()["gstin"]
    if scheme in ("regular", "composition") and not gstin:
        raise BillingError("a GST-registered shop needs its GSTIN; choose 'unregistered' if the shop has none")
    if sets:
        cols = ", ".join(f"{k}=?" for k in sets)
        with repo.tx():
            repo.conn.execute(f"UPDATE stores SET {cols} WHERE id=?", (*sets.values(), repo.store_id))
            repo.audit("owner", "store_settings", sets)
    return store_settings(repo)


def _next_seq(repo: Repo, table: str, fy: str) -> int:
    r = repo.one(f"SELECT COALESCE(MAX(seq),0) m FROM {table} WHERE store_id=? AND fy=?", repo.store_id, fy)
    return int(r["m"]) + 1


def _qty_in_product_unit(product: dict, qty: float, qty_unit: str | None) -> tuple[float, str]:
    """Whole packs for packaged goods; kg or L (from kg/g/L/ml) for loose goods."""
    if qty is None or qty <= 0:
        raise BillingError(f"{product['name']}: quantity must be more than 0")
    if product.get("loose"):
        base = "L" if (product.get("unit") or "").lower() in ("l", "ml") else "kg"
        unit = (qty_unit or base).lower()
        if unit not in LOOSE_UNITS or LOOSE_UNITS[unit][0] != base:
            raise BillingError(f"{product['name']} is sold by {base}; use {'kg or g' if base == 'kg' else 'L or ml'}")
        q = round(qty / LOOSE_UNITS[unit][1], 3)
        if q <= 0:
            raise BillingError(f"{product['name']}: quantity is too small")
        return q, base
    if qty_unit not in (None, "", "pcs"):
        raise BillingError(f"{product['name']} is sold in whole packs, not by {qty_unit}")
    if abs(qty - round(qty)) > 1e-9:
        raise BillingError(f"{product['name']} is sold in whole packs; quantity must be a whole number")
    return float(round(qty)), "pcs"


def _price_breakup(lines: list[_Line], doc_type: str, inter_state: bool) -> None:
    for ln in lines:
        net = ln.gross - ln.discount
        ln.amount = net
        if doc_type != "tax_invoice":
            ln.taxable, ln.gst_rate = net, 0.0
            continue
        rate = Decimal(str(ln.product["gst_rate"]))
        ln.gst_rate = float(rate)
        ln.taxable = money(net * 100 / (100 + rate))
        tax = net - ln.taxable
        if inter_state:
            ln.igst = tax
        else:
            ln.cgst = money(tax / 2)
            ln.sgst = tax - ln.cgst


def _allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split a bill discount across lines in proportion to their value; the last line takes the paise left."""
    if total <= 0:
        return [ZERO] * len(weights)
    s = sum(weights)
    out = [money(total * w / s) if s > 0 else ZERO for w in weights]
    out[-1] += total - sum(out)
    return out


# ------------------------------------------------------------------ create
def create_invoice(repo: Repo, lines: list[dict], payment: dict | None = None, customer: dict | None = None,
                   bill_discount: float = 0.0, day: date | None = None, actor: str = "owner") -> dict:
    """Validate, compute, number and save a bill; deduct stock (FEFO); post udhaar. All or nothing."""
    day = day or date.today()
    st = store_settings(repo)
    doc_type = st["doc_type"]
    if not lines:
        raise BillingError("add at least one item")
    if len(lines) > 200:
        raise BillingError("a bill can have at most 200 lines")

    built: list[_Line] = []
    for raw in lines:
        p = repo.product(int(raw.get("product_id", 0)))
        if p is None:
            raise BillingError(f"product {raw.get('product_id')} not found")
        p = dict(p)
        qty, unit = _qty_in_product_unit(p, float(raw.get("qty") or 0), raw.get("qty_unit"))
        price = money(raw["unit_price"]) if raw.get("unit_price") is not None else money(p["mrp"])
        if price <= 0:
            raise BillingError(f"{p['name']}: price must be more than 0")
        if price > money(p["mrp"]):
            what = "its price per " + unit if p.get("loose") else f"MRP ₹{p['mrp']:g}"
            raise BillingError(f"{p['name']}: ₹{price} is above {what} (selling above MRP is not allowed)")
        gross = money(Decimal(str(qty)) * price)
        disc = money(raw.get("discount") or 0)
        if disc < 0 or disc > gross:
            raise BillingError(f"{p['name']}: discount must be between ₹0 and ₹{gross}")
        built.append(_Line(p, qty, unit, price, gross, disc))

    bill_disc = money(bill_discount or 0)
    after_line = [ln.gross - ln.line_discount for ln in built]
    if bill_disc < 0 or bill_disc > sum(after_line):
        raise BillingError("bill discount cannot be more than the bill value")
    for ln, extra in zip(built, _allocate(bill_disc, after_line)):
        ln.discount = ln.line_discount + extra

    # Customer and place of supply
    cust = dict(customer or {})
    gstin = (cust.get("gstin") or "").strip().upper() or None
    buyer_state = st["state_code"]
    if gstin:
        if doc_type != "tax_invoice":
            raise BillingError("only a GST-registered shop can issue a tax invoice to a GSTIN holder")
        check = validate_gstin(gstin)
        if not check.valid:
            raise BillingError(f"customer GSTIN is not valid: {check.reason}")
        buyer_state = check.state_code
    inter = doc_type == "tax_invoice" and buyer_state != st["state_code"]
    _price_breakup(built, doc_type, inter)

    net_total = sum(ln.amount for ln in built)
    total = to_rupee(net_total)
    round_off = total - net_total

    pay = {m: money((payment or {}).get(m) or 0) for m in PAYMENT_MODES}
    if any(v < 0 for v in pay.values()):
        raise BillingError("payment amounts cannot be negative")
    if sum(pay.values()) == 0:
        pay["cash"] = total
    if sum(pay.values()) != total:
        raise BillingError(f"payments add up to ₹{sum(pay.values())} but the bill is ₹{total}")

    cid = cust.get("id")
    if cid is not None and repo.one("SELECT id FROM customers WHERE id=? AND store_id=?", cid, repo.store_id) is None:
        raise BillingError("customer not found")
    if pay["udhaar"] > 0 and cid is None and not (cust.get("name") or "").strip():
        raise BillingError("udhaar needs a customer: pick one or type a name")

    fy = financial_year(day)
    with repo.tx():
        if cid is None and (cust.get("name") or "").strip() and (pay["udhaar"] > 0 or cust.get("save")):
            cid = repo.add_customer(cust["name"].strip(), (cust.get("phone") or "").strip())
        if cid is not None:
            row = repo.one("SELECT name, phone FROM customers WHERE id=?", cid)
            cust.setdefault("name", row["name"])
            cust["phone"] = cust.get("phone") or row["phone"]
        seq = _next_seq(repo, "invoices", fy)
        number = f"{st['invoice_prefix']}/{fy}/{seq:04d}"
        cur = repo.conn.execute(
            "INSERT INTO invoices(store_id, number, doc_type, fy, seq, day, created_at, customer_id, customer_name, "
            "customer_phone, customer_gstin, customer_state_code, place_of_supply, supply_type, gross, discount, "
            "taxable, cgst, sgst, igst, round_off, total, paid_cash, paid_upi, udhaar) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (repo.store_id, number, doc_type, fy, seq, day.isoformat(), now(), cid,
             (cust.get("name") or "").strip() or None, (cust.get("phone") or "").strip() or None, gstin,
             buyer_state if gstin else None, buyer_state, "inter" if inter else "intra",
             float(sum(ln.gross for ln in built)), float(sum(ln.discount for ln in built)),
             float(sum(ln.taxable for ln in built)), float(sum(ln.cgst for ln in built)),
             float(sum(ln.sgst for ln in built)), float(sum(ln.igst for ln in built)),
             float(round_off), float(total), float(pay["cash"]), float(pay["upi"]), float(pay["udhaar"])))
        inv_id = cur.lastrowid
        for i, ln in enumerate(built, 1):
            p = ln.product
            repo.conn.execute(
                "INSERT INTO invoice_lines(invoice_id, line_no, product_id, name, hsn, qty, unit, unit_price, mrp, "
                "discount, taxable, gst_rate, cgst, sgst, igst, amount) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (inv_id, i, p["id"], p["name"], p["hsn"], ln.qty, ln.unit, float(ln.unit_price), p["mrp"],
                 float(ln.discount), float(ln.taxable), ln.gst_rate, float(ln.cgst), float(ln.sgst),
                 float(ln.igst), float(ln.amount)))
            try:   # FEFO: earliest-expiry batches first; refuses to sell more than is in stock
                repo.sell(p["id"], ln.qty, float(ln.amount) / ln.qty, day, ref=number)
            except InsufficientStock as e:
                raise InsufficientStock(f"{e}. If the shelf has more, correct the count on the Inventory page.") from e
        if pay["udhaar"] > 0:
            repo.credit(cid, float(pay["udhaar"]), day, note=f"Bill {number}")
        repo.audit(actor, "invoice", {"number": number, "total": float(total), "lines": len(built)})
    return get_invoice(repo, inv_id)


# ------------------------------------------------------------------ read
def get_invoice(repo: Repo, invoice_id: int) -> dict | None:
    inv = repo.one("SELECT * FROM invoices WHERE id=? AND store_id=?", invoice_id, repo.store_id)
    if inv is None:
        return None
    out = dict(inv)
    out["title"] = DOC_TITLES[out["doc_type"]]
    lines = [dict(r) for r in repo.q("SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY line_no", invoice_id)]
    returned = {r["invoice_line_id"]: r["q"] for r in repo.q(
        "SELECT l.invoice_line_id, SUM(l.qty) q FROM credit_note_lines l JOIN credit_notes c ON c.id=l.credit_note_id "
        "WHERE c.invoice_id=? GROUP BY l.invoice_line_id", invoice_id)}
    for ln in lines:
        ln["returned_qty"] = round(returned.get(ln["id"], 0.0), 3)
    out["lines"] = lines
    out["credit_notes"] = [dict(r) for r in repo.q(
        "SELECT * FROM credit_notes WHERE invoice_id=? ORDER BY id", invoice_id)]
    out["place_of_supply_name"] = STATE_CODES.get(out["place_of_supply"], out["place_of_supply"])
    out["can_cancel"] = _cancel_problem(repo, out, date.today()) is None
    return out


def find_by_number(repo: Repo, number: str) -> dict | None:
    r = repo.one("SELECT id FROM invoices WHERE store_id=? AND number=?", repo.store_id, number)
    return get_invoice(repo, r["id"]) if r else None


def list_invoices(repo: Repo, q: str = "", day_from: str | None = None, day_to: str | None = None,
                  status: str | None = None, limit: int = 50) -> list[dict]:
    sql = ("SELECT i.id, i.number, i.doc_type, i.day, i.customer_name, i.customer_phone, i.total, i.status, "
           "i.paid_cash, i.paid_upi, i.udhaar, (SELECT COUNT(*) FROM invoice_lines l WHERE l.invoice_id=i.id) AS lines, "
           "(SELECT COUNT(*) FROM credit_notes c WHERE c.invoice_id=i.id) AS credit_notes "
           "FROM invoices i WHERE i.store_id=?")
    args: list = [repo.store_id]
    if q.strip():
        like = f"%{q.strip()}%"
        sql += (" AND (i.number LIKE ? OR i.customer_name LIKE ? OR i.customer_phone LIKE ? OR EXISTS "
                "(SELECT 1 FROM invoice_lines l WHERE l.invoice_id=i.id AND l.name LIKE ?))")
        args += [like, like, like, like]
    if day_from:
        sql += " AND i.day>=?"
        args.append(day_from)
    if day_to:
        sql += " AND i.day<=?"
        args.append(day_to)
    if status in ("active", "cancelled"):
        sql += " AND i.status=?"
        args.append(status)
    sql += " ORDER BY i.day DESC, i.seq DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    return [dict(r) for r in repo.q(sql, *args)]


# ------------------------------------------------------------------ cancel
def _cancel_problem(repo: Repo, inv: dict, today: date) -> str | None:
    if inv["status"] != "active":
        return "this bill is already cancelled"
    if repo.one("SELECT 1 FROM credit_notes WHERE invoice_id=?", inv["id"]):
        return "items from this bill were already returned; issue a credit note for the rest instead"
    issued = date.fromisoformat(inv["day"])
    if (issued.year, issued.month) != (today.year, today.month):
        return "a bill can be cancelled only in the month it was issued; issue a credit note instead"
    return None


def _reverse_sales(repo: Repo, invoice: dict, product_id: int, qty: float, ref: str) -> None:
    """Put `qty` of a product back into the batches this bill took it from (latest taken first)."""
    taken = repo.q("SELECT batch_id, -SUM(qty) q, MAX(day) day, MAX(unit_price) price FROM stock_movements "
                   "WHERE store_id=? AND product_id=? AND kind='sale' AND (ref=? OR ref LIKE ?) "
                   "GROUP BY batch_id HAVING q > 1e-9 ORDER BY batch_id DESC",
                   repo.store_id, product_id, invoice["number"], f"%:{invoice['number']}")
    left = qty
    for b in taken:
        give = min(left, b["q"])
        repo._move(product_id, b["batch_id"], give, "sale", b["price"], ref, date.fromisoformat(invoice["day"]))
        left -= give
        if left <= 1e-9:
            break
    if left > 1e-6:
        raise BillingError("cannot return more than this bill sold")
    if repo.stock(product_id) > 1e-9:   # the item is back on the shelf: that day was not a stock-out
        repo.conn.execute("DELETE FROM stockout_days WHERE store_id=? AND product_id=? AND day=?",
                          (repo.store_id, product_id, invoice["day"]))


def cancel_invoice(repo: Repo, invoice_id: int, reason: str, day: date | None = None,
                   actor: str = "owner") -> dict:
    day = day or date.today()
    inv = get_invoice(repo, invoice_id)
    if inv is None:
        raise BillingError("bill not found")
    if not (reason or "").strip():
        raise BillingError("give a reason for cancelling")
    problem = _cancel_problem(repo, inv, day)
    if problem:
        raise BillingError(problem)
    with repo.tx():
        by_product: dict[int, float] = {}
        for ln in inv["lines"]:
            by_product[ln["product_id"]] = by_product.get(ln["product_id"], 0) + ln["qty"]
        for pid, q in by_product.items():
            _reverse_sales(repo, inv, pid, q, f"CANCEL:{inv['number']}")
        if inv["udhaar"] > 0 and inv["customer_id"]:
            repo.credit(inv["customer_id"], -inv["udhaar"], day, note=f"Bill {inv['number']} cancelled")
        repo.conn.execute("UPDATE invoices SET status='cancelled', cancelled_at=?, cancel_reason=? WHERE id=?",
                          (now(), reason.strip()[:200], invoice_id))
        repo.audit(actor, "invoice_cancel", {"number": inv["number"], "reason": reason.strip()[:200]})
    return get_invoice(repo, invoice_id)


# ------------------------------------------------------------------ returns (credit notes)
def create_credit_note(repo: Repo, invoice_id: int, items: list[dict], reason: str, refund_mode: str = "cash",
                       restock: bool = True, day: date | None = None, actor: str = "owner") -> dict:
    """Customer returns some items: credit note with GST reversed in proportion, stock and udhaar adjusted.
    items: [{invoice_line_id, qty}] with qty in the line's unit (packs, kg or L).
    restock=False for damaged goods: they come back and are written off at once."""
    day = day or date.today()
    inv = get_invoice(repo, invoice_id)
    if inv is None:
        raise BillingError("bill not found")
    if inv["status"] != "active":
        raise BillingError("this bill is cancelled; nothing to return")
    if refund_mode not in ("cash", "upi", "udhaar"):
        raise BillingError("refund must be cash, upi or udhaar")
    if refund_mode == "udhaar" and not inv["customer_id"]:
        raise BillingError("this bill has no customer, so the refund cannot go to udhaar")
    if not (reason or "").strip():
        raise BillingError("give a reason for the return")
    by_id = {ln["id"]: ln for ln in inv["lines"]}
    chosen = []
    for it in items:
        ln = by_id.get(int(it.get("invoice_line_id", 0)))
        q = float(it.get("qty") or 0)
        if ln is None:
            raise BillingError("item is not on this bill")
        if q <= 0:
            continue
        if ln["unit"] == "pcs" and abs(q - round(q)) > 1e-9:
            raise BillingError(f"{ln['name']}: return whole packs")
        if q > ln["qty"] - ln["returned_qty"] + 1e-9:
            raise BillingError(f"{ln['name']}: only {ln['qty'] - ln['returned_qty']:g} {ln['unit']} can still be returned")
        chosen.append((ln, q))
    if not chosen:
        raise BillingError("choose at least one item and quantity to return")

    parts = []
    for ln, q in chosen:
        f = Decimal(str(q)) / Decimal(str(ln["qty"]))
        full = q >= ln["qty"] - ln["returned_qty"] - 1e-9 and ln["returned_qty"] == 0
        part = {k: (Decimal(str(ln[k])) if full else money(Decimal(str(ln[k])) * f))
                for k in ("taxable", "cgst", "sgst", "igst", "amount")}
        parts.append((ln, q, part))
    net = sum(p["amount"] for _, _, p in parts)
    total = to_rupee(net)
    fy = financial_year(day)
    with repo.tx():
        seq = _next_seq(repo, "credit_notes", fy)
        number = f"CN/{fy}/{seq:04d}"
        cur = repo.conn.execute(
            "INSERT INTO credit_notes(store_id, number, fy, seq, invoice_id, day, created_at, reason, taxable, cgst, "
            "sgst, igst, round_off, total, refund_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (repo.store_id, number, fy, seq, invoice_id, day.isoformat(), now(), reason.strip()[:200],
             float(sum(p["taxable"] for _, _, p in parts)), float(sum(p["cgst"] for _, _, p in parts)),
             float(sum(p["sgst"] for _, _, p in parts)), float(sum(p["igst"] for _, _, p in parts)),
             float(total - net), float(total), refund_mode))
        cn_id = cur.lastrowid
        for ln, q, part in parts:
            repo.conn.execute(
                "INSERT INTO credit_note_lines(credit_note_id, invoice_line_id, product_id, qty, taxable, cgst, sgst, "
                "igst, amount) VALUES (?,?,?,?,?,?,?,?,?)",
                (cn_id, ln["id"], ln["product_id"], q, float(part["taxable"]), float(part["cgst"]),
                 float(part["sgst"]), float(part["igst"]), float(part["amount"])))
            ref = f"RET:{number}:{inv['number']}"   # ends with the bill number, so later reversals net it out
            before = {r["batch_id"]: r["q"] for r in repo.q(
                "SELECT batch_id, SUM(qty) q FROM stock_movements WHERE store_id=? AND product_id=? AND ref=? "
                "GROUP BY batch_id", repo.store_id, ln["product_id"], ref)}
            _reverse_sales(repo, inv, ln["product_id"], q, ref)
            if not restock:   # damaged: back into the batch it came from, then written off
                for r in repo.q("SELECT batch_id, SUM(qty) q FROM stock_movements WHERE store_id=? AND product_id=? "
                                "AND ref=? GROUP BY batch_id", repo.store_id, ln["product_id"], ref):
                    back = r["q"] - before.get(r["batch_id"], 0)
                    if back > 1e-9:
                        repo._move(ln["product_id"], r["batch_id"], -back, "adjust", None,
                                   f"returned damaged {number}", day)
        if refund_mode == "udhaar":
            repo.credit(inv["customer_id"], -float(total), day, note=f"Return {number} on bill {inv['number']}")
        repo.audit(actor, "credit_note", {"number": number, "invoice": inv["number"], "total": float(total)})
    return get_credit_note(repo, cn_id)


def get_credit_note(repo: Repo, cn_id: int) -> dict | None:
    cn = repo.one("SELECT * FROM credit_notes WHERE id=? AND store_id=?", cn_id, repo.store_id)
    if cn is None:
        return None
    out = dict(cn)
    out["lines"] = [dict(r) for r in repo.q(
        "SELECT l.*, il.name, il.hsn, il.unit, il.unit_price, il.gst_rate FROM credit_note_lines l "
        "JOIN invoice_lines il ON il.id=l.invoice_line_id WHERE l.credit_note_id=? ORDER BY l.id", cn_id)]
    inv = repo.one("SELECT number, day, customer_name, customer_phone, customer_gstin, place_of_supply, doc_type "
                   "FROM invoices WHERE id=?", out["invoice_id"])
    out["invoice"] = dict(inv)
    return out


# ------------------------------------------------------------------ daily summary (units)
def daily_summary(repo: Repo, day: date) -> dict:
    """What left the shop on a day, in units: packs, kg and litres are kept apart."""
    d = day.isoformat()
    counts = repo.one("SELECT SUM(status='active') active, SUM(status='cancelled') cancelled FROM invoices "
                      "WHERE store_id=? AND day=?", repo.store_id, d)
    sold = repo.q(
        "SELECT l.product_id, l.name, p.category, l.unit, SUM(l.qty) q FROM invoice_lines l "
        "JOIN invoices i ON i.id=l.invoice_id JOIN products p ON p.id=l.product_id "
        "WHERE i.store_id=? AND i.day=? AND i.status='active' GROUP BY l.product_id, l.unit", repo.store_id, d)
    back = {(r["product_id"], r["unit"]): r["q"] for r in repo.q(
        "SELECT l.product_id, il.unit, SUM(l.qty) q FROM credit_note_lines l JOIN credit_notes c ON c.id=l.credit_note_id "
        "JOIN invoice_lines il ON il.id=l.invoice_line_id JOIN invoices i ON i.id=c.invoice_id "
        "WHERE c.store_id=? AND i.day=? GROUP BY l.product_id, il.unit", repo.store_id, d)}
    items, by_cat, totals = [], {}, {"pcs": 0.0, "kg": 0.0, "L": 0.0}
    for r in sold:
        net = round(r["q"] - back.get((r["product_id"], r["unit"]), 0.0), 3)
        if net <= 1e-9:
            continue
        items.append({"product_id": r["product_id"], "name": r["name"], "category": r["category"],
                      "unit": r["unit"], "qty": net})
        totals[r["unit"]] = round(totals.get(r["unit"], 0.0) + net, 3)
        key = (r["category"], r["unit"])
        by_cat[key] = round(by_cat.get(key, 0.0) + net, 3)
    items.sort(key=lambda x: (x["unit"] != "pcs", -x["qty"]))
    cns = repo.one("SELECT COUNT(*) n FROM credit_notes WHERE store_id=? AND day=?", repo.store_id, d)["n"]
    return {"day": d, "bills": int(counts["active"] or 0), "cancelled": int(counts["cancelled"] or 0),
            "returns": int(cns), "units": totals,
            "by_category": [{"category": c, "unit": u, "qty": q} for (c, u), q in
                            sorted(by_cat.items(), key=lambda kv: (kv[0][1] != "pcs", -kv[1]))],
            "items": items}


# ------------------------------------------------------------------ products for the counter
def counter_products(repo: Repo) -> list[dict]:
    stock = repo.stock_all()
    rows = repo.q("SELECT id, name, brand, category, unit, pack_size, mrp, hsn, gst_rate, barcode, loose "
                  "FROM products WHERE store_id=? AND active=1 ORDER BY name", repo.store_id)
    return [{**dict(r), "stock": round(stock.get(r["id"], 0.0), 3)} for r in rows]


def set_barcode(repo: Repo, product_id: int, code: str | None) -> dict:
    p = repo.product(product_id)
    if p is None:
        raise BillingError("product not found")
    code = re.sub(r"\s", "", code or "") or None
    if code and not re.fullmatch(r"[0-9A-Za-z\-]{4,32}", code):
        raise BillingError("barcode should be 4-32 letters or digits")
    if code:
        other = repo.one("SELECT name FROM products WHERE store_id=? AND barcode=? AND id<>?",
                         repo.store_id, code, product_id)
        if other:
            raise BillingError(f"this barcode is already linked to {other['name']}")
    with repo.tx():
        repo.conn.execute("UPDATE products SET barcode=? WHERE id=? AND store_id=?", (code, product_id, repo.store_id))
        repo.audit("owner", "barcode", {"product_id": product_id, "barcode": code})
    return dict(repo.product(product_id))


def product_by_barcode(repo: Repo, code: str) -> dict | None:
    r = repo.one("SELECT id FROM products WHERE store_id=? AND barcode=? AND active=1", repo.store_id, code.strip())
    if r is None:
        return None
    return next((p for p in counter_products(repo) if p["id"] == r["id"]), None)


def whatsapp_text(inv: dict, shop_name: str) -> str:
    """Plain-text bill for a WhatsApp message (used when the phone cannot share the PDF itself)."""
    lines = [f"{shop_name}", f"{inv['title']} {inv['number']} · {date.fromisoformat(inv['day']):%d %b %Y}", ""]
    for ln in inv["lines"]:
        q = f"{ln['qty']:g} {ln['unit']}" if ln["unit"] != "pcs" else f"{ln['qty']:g} x"
        lines.append(f"{q} {ln['name']}  ₹{ln['amount']:,.2f}")
    if inv["discount"]:
        lines.append(f"Discount included: ₹{inv['discount']:,.2f}")
    lines += ["", f"Total: ₹{inv['total']:,.0f}"]
    if inv["udhaar"]:
        lines.append(f"Added to udhaar: ₹{inv['udhaar']:,.0f}")
    lines += ["", "Thank you!"]
    return "\n".join(lines)
