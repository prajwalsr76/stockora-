"""Indian business rules: GSTIN, GST tax split, MRP, festivals and reference data.

Rule-based logic lives here on purpose: tax math and compliance checks must be
deterministic, never produced by an LLM.

GST slabs follow the GST 2.0 rationalisation effective 22 Sep 2025
(0 / 5 / 18 / 40 %). Rates in reference/catalog.csv are per-product defaults;
always verify against CBIC notifications (https://cbic-gst.gov.in) before
filing. Bill-extracted rates override catalog defaults.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

REFERENCE_DIR = Path(__file__).parent / "reference"

VALID_GST_RATES = {0, 5, 18, 40}
# Legacy slabs still appear on bills dated before 22 Sep 2025.
LEGACY_GST_RATES = {3, 12, 28}
GST_REFORM_DATE = date(2025, 9, 22)

STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory",
}

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def money(value: float | int | str | Decimal) -> Decimal:
    """Round to paise using commercial rounding (half-up)."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def gstin_checksum(first14: str) -> str:
    """Compute the 15th GSTIN character (mod-36 weighted checksum)."""
    total = 0
    for i, ch in enumerate(first14):
        product = _GSTIN_CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36]


@dataclass(frozen=True)
class GstinCheck:
    valid: bool
    reason: str = ""
    state_code: str = ""
    state: str = ""
    pan: str = ""


def validate_gstin(gstin: str | None) -> GstinCheck:
    """Validate format, state code and checksum of a 15-character GSTIN."""
    if not gstin:
        return GstinCheck(False, "missing")
    g = gstin.strip().upper().replace(" ", "")
    if len(g) != 15:
        return GstinCheck(False, f"length {len(g)}, expected 15")
    if not _GSTIN_RE.match(g):
        return GstinCheck(False, "format does not match GSTIN pattern")
    if g[:2] not in STATE_CODES:
        return GstinCheck(False, f"unknown state code {g[:2]}")
    if gstin_checksum(g[:14]) != g[14]:
        return GstinCheck(False, "checksum mismatch (likely a typo or OCR error)")
    return GstinCheck(True, "", g[:2], STATE_CODES[g[:2]], g[2:12])


@dataclass(frozen=True)
class TaxSplit:
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal

    @property
    def total_tax(self) -> Decimal:
        return self.cgst + self.sgst + self.igst


def gst_split(taxable: float | Decimal, rate: float, seller_state: str, buyer_state: str) -> TaxSplit:
    """Intra-state supply -> CGST + SGST (half each); inter-state -> IGST."""
    t = money(taxable)
    tax = money(t * Decimal(str(rate)) / 100)
    if seller_state == buyer_state:
        half = money(tax / 2)
        return TaxSplit(t, half, tax - half, Decimal("0.00"))
    return TaxSplit(t, Decimal("0.00"), Decimal("0.00"), tax)


def is_valid_rate(rate: float, bill_date: date | None = None) -> bool:
    if rate in VALID_GST_RATES:
        return True
    return bill_date is not None and bill_date < GST_REFORM_DATE and rate in LEGACY_GST_RATES


def mrp_violation(selling_price: float, mrp: float) -> bool:
    """Legal Metrology (Packaged Commodities) Rules: cannot sell above MRP."""
    return money(selling_price) > money(mrp)


# ---------------------------------------------------------------- reference data

@dataclass(frozen=True)
class CatalogItem:
    sku: str
    name: str
    brand: str
    category: str
    pack_size: float
    unit: str
    mrp: float
    hsn: str
    gst_rate: float
    shelf_life_days: int
    base_daily_demand: float
    margin_pct: float

    @property
    def cost_price(self) -> float:
        """Typical distributor price = MRP less retailer margin."""
        return round(self.mrp * (1 - self.margin_pct / 100), 2)


@lru_cache
def load_catalog() -> tuple[CatalogItem, ...]:
    with open(REFERENCE_DIR / "catalog.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return tuple(
        CatalogItem(
            sku=r["sku"], name=r["name"], brand=r["brand"], category=r["category"],
            pack_size=float(r["pack_size"]), unit=r["unit"], mrp=float(r["mrp"]),
            hsn=r["hsn"], gst_rate=float(r["gst_rate"]),
            shelf_life_days=int(r["shelf_life_days"]),
            base_daily_demand=float(r["base_daily_demand"]),
            margin_pct=float(r["margin_pct"]),
        )
        for r in rows
    )


@dataclass(frozen=True)
class Festival:
    day: date
    name: str
    days_before: int
    days_after: int
    effects: dict[str, float]


@lru_cache
def load_festivals() -> tuple[Festival, ...]:
    out = []
    with open(REFERENCE_DIR / "festivals.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            effects = {}
            for part in r["effects"].split("|"):
                cat, mult = part.split(":")
                effects[cat] = float(mult)
            out.append(Festival(date.fromisoformat(r["date"]), r["festival"],
                                int(r["days_before"]), int(r["days_after"]), effects))
    return tuple(sorted(out, key=lambda x: x.day))


def festival_multiplier(day: date, category: str) -> float:
    """Demand multiplier for a category on a day. Effect ramps up towards the festival."""
    mult = 1.0
    for fest in load_festivals():
        delta = (fest.day - day).days  # positive = festival is ahead
        if -fest.days_after <= delta <= fest.days_before and category in fest.effects:
            peak = fest.effects[category]
            if delta >= 0:
                weight = 1 - delta / (fest.days_before + 1)  # ramps 0 -> 1
            else:
                weight = 0.5
            mult = max(mult, 1 + (peak - 1) * weight)
    return mult


def next_festivals(day: date, horizon_days: int = 30) -> list[Festival]:
    end = day + timedelta(days=horizon_days)
    return [f for f in load_festivals() if day <= f.day <= end]


def days_to_next_festival(day: date) -> int:
    upcoming = [f for f in load_festivals() if f.day >= day]
    return (upcoming[0].day - day).days if upcoming else 365


SEASONAL = {
    # month -> category multipliers (summer, monsoon, winter, school reopening)
    4: {"beverages": 1.5, "dairy": 1.15}, 5: {"beverages": 1.7, "dairy": 1.2},
    6: {"beverages": 1.5, "stationery": 2.5},
    7: {"instant_food": 1.25, "stationery": 1.6}, 8: {"instant_food": 1.2},
    11: {"festive": 1.2}, 12: {"festive": 1.15, "beverages": 0.8},
    1: {"beverages": 0.75, "festive": 1.1}, 2: {"beverages": 0.85},
}


def season_multiplier(day: date, category: str) -> float:
    return SEASONAL.get(day.month, {}).get(category, 1.0)


def is_salary_window(day: date) -> bool:
    """Most salaried households get paid on the 1st; spending peaks in days 1-5."""
    return day.day <= 5
