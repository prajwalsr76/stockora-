"""Add products by scanning the barcode, using the open Open Food Facts database.

Most Indian packaged goods carry EAN-13 barcodes starting with 890 (GS1 India).
Open Food Facts is open data (ODbL); it asks API users to send a descriptive User-Agent.
"""
from __future__ import annotations

import re

import requests

API = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
FIELDS = "product_name,product_name_en,brands,quantity,categories_tags"
USER_AGENT = "Stockora/0.1 (open-source kirana inventory project)"

CATEGORY_MAP = [  # Open Food Facts tag fragment -> Stockora category
    ("biscuit", "snacks"), ("chips", "snacks"), ("snack", "snacks"), ("chocolate", "snacks"),
    ("noodle", "instant_food"), ("pasta", "instant_food"), ("breakfast-cereal", "instant_food"),
    ("milk", "dairy"), ("dairies", "dairy"), ("cheese", "dairy"), ("butter", "dairy"), ("yogurt", "dairy"),
    ("bread", "bakery"), ("beverage", "beverages"), ("tea", "beverages"), ("coffee", "beverages"),
    ("oil", "oil_ghee"), ("ghee", "oil_ghee"), ("flour", "staples"), ("rice", "staples"), ("pulse", "staples"),
    ("salt", "staples"), ("sugar", "staples"), ("spice", "spices"), ("condiment", "instant_food"),
]


def valid_ean13(code: str) -> bool:
    if not re.fullmatch(r"\d{13}", code):
        return False
    digits = [int(c) for c in code]
    check = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12])) % 10) % 10
    return check == digits[12]


def lookup(code: str, session=None, timeout: float = 15.0) -> dict | None:
    code = code.strip()
    if not valid_ean13(code) and not re.fullmatch(r"\d{8}|\d{12}", code):
        raise ValueError("not a valid EAN/UPC barcode")
    http = session or requests
    r = http.get(API.format(code=code), params={"fields": FIELDS},
                 headers={"User-Agent": USER_AGENT}, timeout=timeout)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    if data.get("status") != 1:
        return None
    p = data["product"]
    tags = " ".join(p.get("categories_tags", []))
    category = next((c for frag, c in CATEGORY_MAP if frag in tags), "general")
    name = p.get("product_name_en") or p.get("product_name") or ""
    brand = (p.get("brands") or "").split(",")[0].strip()
    qty = p.get("quantity") or ""
    return {"barcode": code, "name": f"{name} {qty}".strip(), "brand": brand, "category": category,
            "made_in_india_prefix": code.startswith("890"), "source": "Open Food Facts (ODbL)"}
