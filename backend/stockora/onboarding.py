"""Onboard a real shop from spreadsheets (CSV or Excel).

Designed for the files shops already have: item lists and sales reports exported
from billing apps (Vyapar, myBillBook, Marg, Tally) or a hand-made Excel sheet.
Column names are matched flexibly ("Item Name", "Product", "Qty", "Sale Qty",
"Tax Rate", "GST@5%"...). Missing details (category, HSN, GST, shelf life) are
filled from the reference catalog when a product is recognised.

Nothing is guessed silently: every auto-filled field and every skipped row is
listed in the report.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .db import Repo
from .india import VALID_GST_RATES, load_catalog, validate_gstin
from .matching import ProductMatcher

HEADERS = {
    "name": ["name", "item", "itemname", "product", "productname", "particulars", "description", "itemdescription"],
    "sku": ["sku", "itemcode", "code", "productcode", "barcode"],
    "category": ["category", "itemcategory", "group", "itemgroup", "stockgroup"],
    "mrp": ["mrp", "maxretailprice"],
    "sale_price": ["saleprice", "sellingprice", "salesprice", "rate", "priceunit", "price", "unitprice"],
    "cost_price": ["purchaseprice", "costprice", "cost", "buyingprice", "purchaserate"],
    "gst_rate": ["gst", "gstrate", "taxrate", "tax", "gstpercent", "gstpct"],
    "hsn": ["hsn", "hsncode", "hsnsac", "hsnsaccode"],
    "stock": ["stock", "openingstock", "currentstock", "stockquantity", "quantityinstock", "closingstock", "qtyinstock", "closingqty", "closingbalance", "balanceqty"],
    "expiry": ["expiry", "expirydate", "exp", "expdate", "bestbefore"],
    "shelf_life_days": ["shelflife", "shelflifedays"],
    "unit": ["unit", "uom", "units"],
    "date": ["date", "invoicedate", "billdate", "saledate", "txndate", "transactiondate"],
    "qty": ["qty", "quantity", "saleqty", "soldqty", "units", "nos"],
    "amount": ["amount", "total", "totalamount", "value", "netamount", "saleamount"],
    "distributor": ["distributor", "supplier", "vendor", "party", "partyname", "suppliername"],
    "phone": ["phone", "mobile", "phoneno", "mobileno", "contact"],
    "gstin": ["gstin", "gstno", "gstnumber"],
    "lead_time_days": ["leadtime", "leadtimedays", "deliverydays"],
    "min_order_value": ["minorder", "minimumorder", "minordervalue", "moq"],
    "scheme_buy": ["schemebuy", "buy"],
    "scheme_free": ["schemefree", "free", "freeqty"],
    "scheme_text": ["scheme", "offer", "schemetext"],
}
CATEGORY_WORDS = {
    "dairy": ["dairy", "milk", "curd", "dahi", "paneer", "butter", "cheese", "ice cream"],
    "bakery": ["bakery", "bread", "pav", "bun", "rusk", "toast"],
    "snacks": ["snack", "biscuit", "namkeen", "chips", "chocolate", "confectionery", "wafer"],
    "beverages": ["beverage", "drink", "tea", "coffee", "juice", "water", "cold drink", "soft drink"],
    "staples": ["staple", "grocery", "atta", "flour", "rice", "dal", "pulse", "sugar", "salt", "kirana"],
    "oil_ghee": ["oil", "ghee", "vanaspati"],
    "spices": ["spice", "masala", "haldi", "mirch", "jeera"],
    "personal_care": ["personal", "soap", "shampoo", "toothpaste", "cosmetic", "hair", "cream"],
    "home_care": ["home care", "detergent", "cleaner", "dishwash", "household", "phenyl"],
    "instant_food": ["instant", "noodle", "pasta", "ready", "breakfast", "sauce", "ketchup", "jam"],
    "festive": ["dry fruit", "sweet", "mithai", "pooja", "gift", "festive"],
    "fasting": ["fasting", "vrat", "upvas"],
    "stationery": ["stationery", "notebook", "pen", "pencil"],
}
DEFAULT_SHELF_LIFE = {"dairy": 7, "bakery": 5, "snacks": 150, "beverages": 180, "staples": 270, "oil_ghee": 270,
                      "spices": 365, "personal_care": 730, "home_care": 730, "instant_food": 240, "festive": 180,
                      "fasting": 270, "stationery": 1825, "general": 365}


@dataclass
class Report:
    step: str
    added: int = 0
    updated: int = 0
    skipped: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"[{self.step}] added {self.added}, updated {self.updated}, skipped {len(self.skipped)}"]
        if self.unknown_columns:
            lines.append(f"  ignored columns: {', '.join(self.unknown_columns)}")
        for title, items in (("auto-filled", self.filled), ("warnings", self.warnings), ("skipped", self.skipped)):
            if items:
                lines.append(f"  {title} ({len(items)}):")
                lines += [f"    - {x}" for x in items[:15]]
                if len(items) > 15:
                    lines.append(f"    ... and {len(items) - 15} more (see onboarding_report.json)")
        return "\n".join(lines)


# ----------------------------------------------------------------- reading helpers
def _key(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


def read_raw(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path)
    else:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    return raw.dropna(how="all")


def detect_columns(columns) -> dict[str, str | None]:
    """Original column name -> canonical field (or None if not recognised). Each field is used once."""
    lookup = {alias: canon for canon, aliases in HEADERS.items() for alias in aliases}
    out, used = {}, set()
    for col in columns:
        k = _key(col)
        canon = lookup.get(k)
        if canon is None:  # e.g. "GST @ 5%" or "Qty (Nos)"
            canon = next((c for a, c in lookup.items() if len(a) >= 3 and k.startswith(a)), None)
        if canon and canon not in used:
            out[str(col)] = canon
            used.add(canon)
        else:
            out[str(col)] = None
    return out


def read_table(path: str | Path, mapping: dict[str, str | None] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Read CSV/XLSX and rename columns to canonical names. Returns (frame, unknown columns).
    `mapping` (original column -> field or None to ignore) overrides the automatic detection."""
    raw = read_raw(path)
    raw.columns = [str(c) for c in raw.columns]
    found = detect_columns(raw.columns)
    if mapping:
        found.update({k: (v or None) for k, v in mapping.items() if k in found})
        seen: set[str] = set()
        for k, v in list(found.items()):        # a field chosen twice keeps its first column only
            if v in seen:
                found[k] = None
            elif v:
                seen.add(v)
    rename = {c: f for c, f in found.items() if f in HEADERS}
    unknown = [c for c, f in found.items() if f not in HEADERS]
    return raw.rename(columns=rename)[list(rename.values())], unknown


def preview(path: str | Path, kind: str, rows: int = 5) -> dict:
    """What the import will see: detected columns, required ones missing, and the first rows."""
    raw = read_raw(path)
    raw.columns = [str(c) for c in raw.columns]
    found = detect_columns(raw.columns)
    need = {"products": ["name"], "sales": ["date", "name", "qty"]}[kind]
    have = set(v for v in found.values() if v)
    sample = raw.head(rows).astype(object).where(raw.head(rows).notna(), None)
    return {"columns": [{"name": c, "field": f} for c, f in found.items()], "rows": int(len(raw)),
            "missing": [n for n in need if n not in have],
            "sample": [{str(k): (str(v) if v is not None else "") for k, v in r.items()} for r in sample.to_dict("records")],
            "fields": FIELD_LABELS[kind]}


FIELD_LABELS = {
    "products": {"name": "Item name", "mrp": "MRP", "sale_price": "Selling price", "cost_price": "Purchase price",
                 "gst_rate": "GST %", "hsn": "HSN code", "stock": "Stock now", "category": "Category",
                 "unit": "Unit", "expiry": "Expiry date", "shelf_life_days": "Shelf life (days)", "sku": "Item code / barcode"},
    "sales": {"date": "Date", "name": "Item name", "qty": "Quantity sold", "amount": "Amount", "sale_price": "Price"},
}


def add_items(repo: Repo, items: list[dict], today: date) -> Report:
    """Wizard 'start from zero': items picked from the catalog (by sku) or typed in, with a shelf count."""
    from datetime import timedelta
    rep = Report("starter items")
    catalog = {c.sku: c for c in load_catalog()}
    existing = {p["name"].lower(): p["id"] for p in repo.products(active_only=False)}
    with repo.tx():
        for it in items:
            ref = catalog.get(it.get("sku") or "")
            name = (it.get("name") or (ref.name if ref else "")).strip()
            mrp = num(it.get("mrp")) or (ref.mrp if ref else None)
            if not name or not mrp or mrp <= 0:
                rep.skipped.append(f"'{name or '?'}': needs a name and a price")
                continue
            category = it.get("category") or (ref.category if ref else guess_category(name) or "general")
            gst = num(it.get("gst_rate"))
            gst = gst if gst is not None else (ref.gst_rate if ref else 0.0)
            if gst not in VALID_GST_RATES:
                rep.skipped.append(f"'{name}': GST {gst}% is not a current slab (0/5/18/40)")
                continue
            shelf = int(num(it.get("shelf_life_days")) or (ref.shelf_life_days if ref else DEFAULT_SHELF_LIFE.get(category, 365)))
            loose = 1 if it.get("loose") else 0
            if name.lower() in existing:
                pid = existing[name.lower()]
                rep.updated += 1
            else:
                sku = ref.sku if ref and not repo.one("SELECT 1 FROM products WHERE store_id=? AND sku=?",
                                                      repo.store_id, ref.sku) else None
                pid = repo.add_product(sku=sku, name=name, brand=ref.brand if ref else None, category=category,
                                       unit=it.get("unit") or (ref.unit if ref else ("kg" if loose else None)),
                                       pack_size=ref.pack_size if ref else (1 if loose else None), mrp=mrp,
                                       hsn=it.get("hsn") or (ref.hsn if ref else ""), gst_rate=gst,
                                       shelf_life_days=shelf, loose=loose)
                existing[name.lower()] = pid
                rep.added += 1
            qty = num(it.get("stock"))
            if qty and qty > 0:
                repo.receive(pid, qty, round(mrp * 0.88, 2), today, expiry=today + timedelta(days=shelf),
                             batch_no="OPENING", ref="shelf count", kind="opening")
        repo.audit("onboarding", "add_items", {"added": rep.added, "updated": rep.updated})
    return rep


def num(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    return float(m.group().replace(",", "")) if m else None


def parse_day(v) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d-%b-%Y",
                "%d-%b-%y", "%m/%Y", "%m/%y"):
        try:
            d = datetime.strptime(s, fmt).date()
            if fmt in ("%m/%Y", "%m/%y"):   # expiry like 11/26 -> last day of month
                nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
                d = date.fromordinal(nxt.toordinal() - 1)
            return d
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=True).date()   # Indian dates are day-first
    except (ValueError, TypeError):
        return None


def guess_category(text: str) -> str | None:
    t = str(text).lower()
    for cat, words in CATEGORY_WORDS.items():
        if any(w in t for w in words):
            return cat
    return None


def _text(v) -> str:
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


# ----------------------------------------------------------------- import steps
def import_products(repo: Repo, path: str | Path, today: date, mapping: dict | None = None) -> Report:
    df, unknown = read_table(path, mapping)
    rep = Report("products", unknown_columns=unknown)
    if "name" not in df:
        rep.skipped.append("no product-name column found")
        return rep
    catalog = load_catalog()
    cat_matcher = ProductMatcher([(i, c.name) for i, c in enumerate(catalog)])
    existing = {p["name"].lower(): p["id"] for p in repo.products(active_only=False)}
    with repo.tx():
        for i, row in df.iterrows():
            name = _text(row.get("name"))
            if not name:
                continue
            ref, _ = cat_matcher.resolve(name)
            ref_item = catalog[ref.product_id] if ref else None
            filled = []
            mrp = num(row.get("mrp")) or num(row.get("sale_price"))
            if mrp is None and ref_item:
                mrp, _ = ref_item.mrp, filled.append("MRP (approximate, from catalog: please check)")
            if not mrp:
                rep.skipped.append(f"row {i + 2} '{name}': no MRP or sale price")
                continue
            category = guess_category(_text(row.get("category"))) or guess_category(name)
            if ref_item and (category is None or category != ref_item.category):
                category = ref_item.category
                filled.append("category")
            category = category or "general"
            gst = num(row.get("gst_rate"))
            if gst is None and ref_item:
                gst = ref_item.gst_rate
                filled.append("GST rate")
            if gst is not None and gst not in VALID_GST_RATES:
                rep.warnings.append(f"'{name}': GST {gst}% is not a current slab (0/5/18/40); check after the Sep 2025 reform")
            hsn = _text(row.get("hsn")).split(".")[0]
            if hsn.isdigit() and len(hsn) in (3, 5, 7):   # Excel drops the leading zero of HSN codes
                hsn = hsn.zfill(len(hsn) + 1)
            hsn = hsn or (ref_item.hsn if ref_item else "")
            if not _text(row.get("hsn")) and ref_item:
                filled.append("HSN")
            shelf = num(row.get("shelf_life_days"))
            if shelf is None:
                shelf = ref_item.shelf_life_days if ref_item else DEFAULT_SHELF_LIFE.get(category, 365)
                filled.append("shelf life")
            sku = _text(row.get("sku")) or (ref_item.sku if ref_item else None)
            if filled:
                rep.filled.append(f"'{name}': {', '.join(filled)}" + (f" (matched '{ref_item.name}')" if ref_item else ""))
            if name.lower() in existing:
                pid = existing[name.lower()]
                repo.conn.execute("UPDATE products SET mrp=?, gst_rate=?, category=?, hsn=? WHERE id=? AND store_id=?",
                                  (mrp, gst or 0, category, hsn, pid, repo.store_id))
                rep.updated += 1
            else:
                if sku and repo.one("SELECT 1 FROM products WHERE store_id=? AND sku=?", repo.store_id, sku):
                    sku = None
                pid = repo.add_product(sku=sku, name=name, brand=ref_item.brand if ref_item else None,
                                       category=category, unit=_text(row.get("unit")) or None,
                                       pack_size=None, mrp=mrp, hsn=hsn, gst_rate=gst or 0,
                                       shelf_life_days=int(shelf))
                if ref_item and ref_item.name.lower() != name.lower():
                    repo.add_alias(pid, ref_item.name, "catalog")
                existing[name.lower()] = pid
                rep.added += 1
            qty = num(row.get("stock"))
            if qty and qty > 0:
                cost = num(row.get("cost_price")) or round(mrp * 0.88, 2)
                exp = parse_day(row.get("expiry"))
                repo.receive(pid, qty, cost, today, expiry=exp, batch_no="OPENING", ref="opening stock",
                             kind="opening")
        repo.audit("onboarding", "import_products", {"added": rep.added, "updated": rep.updated})
    return rep


def import_distributors(repo: Repo, path: str | Path) -> Report:
    df, unknown = read_table(path)
    rep = Report("distributors", unknown_columns=unknown)
    col = "distributor" if "distributor" in df else "name"
    existing = {d["name"].lower() for d in repo.distributors()}
    with repo.tx():
        for i, row in df.iterrows():
            name = _text(row.get(col))
            if not name:
                continue
            if name.lower() in existing:
                rep.skipped.append(f"'{name}' already exists")
                continue
            gstin = _text(row.get("gstin")) or None
            state = None
            if gstin:
                chk = validate_gstin(gstin)
                if chk.valid:
                    state = chk.state_code
                else:
                    rep.warnings.append(f"'{name}': GSTIN {gstin} invalid ({chk.reason})")
            repo.add_distributor(name, int(num(row.get("lead_time_days")) or 2),
                                 float(num(row.get("min_order_value")) or 0), _text(row.get("phone")),
                                 gstin, state)
            existing.add(name.lower())
            rep.added += 1
    return rep


def import_prices(repo: Repo, path: str | Path) -> Report:
    df, unknown = read_table(path)
    rep = Report("prices", unknown_columns=unknown)
    dists = {d["name"].lower(): d["id"] for d in repo.distributors()}
    matcher = ProductMatcher([(r["product_id"], r["alias"]) for r in repo.aliases()])
    with repo.tx():
        for i, row in df.iterrows():
            dname, pname = _text(row.get("distributor")), _text(row.get("name"))
            price = num(row.get("cost_price")) or num(row.get("sale_price"))
            did = dists.get(dname.lower())
            hit, cands = matcher.resolve(pname) if pname else (None, [])
            if did is None:
                rep.skipped.append(f"row {i + 2}: unknown distributor '{dname}' (add it to distributors first)")
                continue
            if hit is None:
                best = repo.product(cands[0].product_id)["name"] if cands else "-"
                rep.skipped.append(f"row {i + 2}: no confident product match for '{pname}' (closest: {best})")
                continue
            if not price:
                rep.skipped.append(f"row {i + 2}: no price for '{pname}'")
                continue
            repo.set_price(did, hit.product_id, price, int(num(row.get("scheme_buy")) or 0),
                           int(num(row.get("scheme_free")) or 0), _text(row.get("scheme_text")) or None)
            rep.added += 1
    return rep


def import_sales_history(repo: Repo, path: str | Path, source: str = "billing export",
                         mapping: dict | None = None) -> Report:
    """Past sales lines (date, item, qty[, amount]) -> sales_history. Does not change current stock."""
    df, unknown = read_table(path, mapping)
    rep = Report("sales history", unknown_columns=unknown)
    missing = [c for c in ("date", "name", "qty") if c not in df]
    if missing:
        rep.skipped.append(f"missing required columns: {', '.join(missing)}")
        return rep
    matcher = ProductMatcher([(r["product_id"], r["alias"]) for r in repo.aliases()])
    cache: dict[str, int | None] = {}
    unmatched: dict[str, float] = {}
    agg: dict[tuple[int, str], list[float]] = {}
    bad_dates = 0
    for _, row in df.iterrows():
        name = _text(row.get("name"))
        d = parse_day(row.get("date"))
        q = num(row.get("qty"))
        if not name or q is None:
            continue
        if d is None:
            bad_dates += 1
            continue
        if name not in cache:
            hit, _ = matcher.resolve(name)
            cache[name] = hit.product_id if hit else None
        pid = cache[name]
        if pid is None:
            unmatched[name] = unmatched.get(name, 0) + q
            continue
        amt = num(row.get("amount"))
        if amt is None and num(row.get("sale_price")) is not None:
            amt = num(row.get("sale_price")) * q
        cur = agg.setdefault((pid, d.isoformat()), [0.0, 0.0])
        cur[0] += q
        cur[1] += amt or 0.0
    with repo.tx():
        rep.added = repo.add_sales_history([(p, d, u, r or None) for (p, d), (u, r) in agg.items()], source)
    if bad_dates:
        rep.warnings.append(f"{bad_dates} rows had unreadable dates (use DD/MM/YYYY)")
    for name, q in sorted(unmatched.items(), key=lambda x: -x[1]):
        rep.skipped.append(f"'{name}' ({q:g} units): not in your product list; add it to products and re-import")
    if agg:
        days = sorted({d for _, d in agg})
        rep.warnings.append(f"history covers {days[0]} to {days[-1]} ({len(days)} days with sales)")
    return rep


def onboard_folder(repo: Repo, folder: str | Path, today: date) -> list[Report]:
    """Import whichever of products/distributors/prices/sales files exist in `folder` (csv or xlsx)."""
    folder = Path(folder)

    def find(stem: str) -> Path | None:
        for ext in (".xlsx", ".xls", ".csv"):
            p = folder / f"{stem}{ext}"
            if p.exists():
                return p
        return None

    reports = []
    if f := find("products"):
        reports.append(import_products(repo, f, today))
    if f := find("distributors"):
        reports.append(import_distributors(repo, f))
    if f := find("prices"):
        reports.append(import_prices(repo, f))
    if f := find("sales"):
        reports.append(import_sales_history(repo, f))
    (folder / "onboarding_report.json").write_text(
        json.dumps([asdict(r) for r in reports], indent=2, ensure_ascii=False), encoding="utf-8")
    return reports


TEMPLATES = {
    "products": ["Item Name", "Category", "MRP", "Purchase Price", "GST %", "HSN", "Current Stock", "Expiry"],
    "distributors": ["Distributor", "Phone", "GSTIN", "Lead Time Days", "Min Order Value"],
    "prices": ["Distributor", "Item Name", "Purchase Price", "Scheme Buy", "Scheme Free", "Scheme"],
    "sales": ["Date", "Item Name", "Qty", "Amount"],
}


def write_templates(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for stem, cols in TEMPLATES.items():
        p = folder / f"{stem}.csv"
        if not p.exists():
            pd.DataFrame(columns=cols).to_csv(p, index=False)
            out.append(p)
    return out
