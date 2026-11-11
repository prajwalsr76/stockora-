"""PDF bills and credit notes: A4 for printing or sharing, 80 mm and 58 mm for thermal printers.

Uses fpdf2 (pure Python) with the bundled DejaVu Sans Condensed font, which has the ₹ sign.
Thermal pages are drawn twice: once to measure the height, then on a page of exactly that height,
so the printer does not feed blank paper.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fpdf import FPDF

from .india import STATE_CODES

FONT_DIR = Path(__file__).parent / "reference" / "fonts"
LAYOUTS = {"a4": 210.0, "80mm": 80.0, "58mm": 58.0}
MARGIN = {"a4": 12.0, "80mm": 4.0, "58mm": 3.0}

_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _three(n: int) -> str:
    h, r = divmod(n, 100)
    return ((_ONES[h] + " Hundred" + (" " if r else "")) if h else "") + (_two(r) if r else "")


def _words(n: int) -> str:
    """Indian numbering in words: crore, lakh, thousand, hundred."""
    if n == 0:
        return ""
    for size, name in ((10**7, "Crore"), (10**5, "Lakh"), (10**3, "Thousand")):
        if n >= size:
            head, rest = divmod(n, size)
            return f"{_words(head)} {name}" + (f" {_words(rest)}" if rest else "")
    return _three(n)


def rupees_in_words(amount: float) -> str:
    """1,25,430 -> 'Rupees One Lakh Twenty Five Thousand Four Hundred Thirty Only'."""
    n = int(round(amount))
    return f"Rupees {_words(n) or 'Zero'} Only"


def _inr(x: float) -> str:
    """Indian digit grouping: 125430.5 -> 1,25,430.50"""
    neg = x < 0
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + f"{whole}.{frac}"


def _qty(q: float, unit: str) -> str:
    return f"{q:g}" if unit == "pcs" else f"{q:g} {unit}"


class _Doc(FPDF):
    def __init__(self, width: float, height: float, margin: float):
        super().__init__(unit="mm", format=(width, height))
        self.set_margins(margin, margin, margin)
        self.set_auto_page_break(auto=height > 300, margin=margin)
        self.add_font("DV", "", str(FONT_DIR / "DejaVuSansCondensed.ttf"))
        self.add_font("DV", "B", str(FONT_DIR / "DejaVuSansCondensed-Bold.ttf"))
        self.set_font("DV", "", 9)
        self.add_page()

    def txt(self, text: str, size: float = 9, bold: bool = False, align: str = "L", h: float | None = None) -> None:
        self.set_font("DV", "B" if bold else "", size)
        self.multi_cell(0, h or size * 0.45, text, align=align, new_x="LMARGIN", new_y="NEXT")

    def rule(self, gap: float = 1.2) -> None:
        y = self.get_y() + gap / 2
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.set_y(y + gap / 2)

    def row(self, cells: list[tuple[str, float, str]], size: float = 8, bold: bool = False, h: float = 4.2) -> None:
        """cells: (text, width mm, align). The first cell wraps; the others are on its first line."""
        self.set_font("DV", "B" if bold else "", size)
        x0, y0 = self.l_margin, self.get_y()
        text, w, align = cells[0]
        self.set_xy(x0, y0)
        self.multi_cell(w, h, text, align=align, new_x="RIGHT", new_y="TOP")
        y_end = self.get_y()
        lines = len(self.multi_cell(w, h, text, dry_run=True, output="LINES"))
        x = x0 + w
        for text, w, align in cells[1:]:
            self.set_xy(x, y0)
            self.cell(w, h, text, align=align)
            x += w
        self.set_xy(x0, max(y_end, y0 + h * lines))


def _shop_lines(shop: dict) -> list[str]:
    out = []
    if shop.get("address"):
        out.append(shop["address"])
    city = ", ".join(x for x in [shop.get("city"), STATE_CODES.get(shop["state_code"], "")] if x)
    if city:
        out.append(city)
    if shop.get("phone"):
        out.append(f"Phone: {shop['phone']}")
    if shop.get("gstin") and shop.get("gst_scheme") != "unregistered":
        out.append(f"GSTIN: {shop['gstin']}")
    return out


def _tax_by_rate(lines: list[dict]) -> list[tuple[float, float, float, float, float]]:
    agg: dict[float, list[float]] = {}
    for ln in lines:
        a = agg.setdefault(ln["gst_rate"], [0.0, 0.0, 0.0, 0.0])
        for i, k in enumerate(("taxable", "cgst", "sgst", "igst")):
            a[i] += ln[k]
    return [(r, *v) for r, v in sorted(agg.items())]


# ------------------------------------------------------------------ A4
def _a4(pdf: _Doc, doc: dict, shop: dict, kind: str) -> None:
    inv = doc if kind == "invoice" else doc["invoice"]
    title = doc["title"] if kind == "invoice" else "Credit Note"
    tax = inv["doc_type"] == "tax_invoice"
    inter = kind == "invoice" and doc["supply_type"] == "inter" or (kind != "invoice" and doc["igst"] > 0)
    pdf.txt(title.upper(), 13, True, "C")
    if kind == "invoice" and doc["status"] == "cancelled":
        pdf.set_text_color(200, 30, 30)
        pdf.txt(f"CANCELLED: {doc.get('cancel_reason') or ''}", 11, True, "C")
        pdf.set_text_color(0, 0, 0)
    pdf.ln(1)
    pdf.txt(shop["name"], 12, True)
    for s in _shop_lines(shop):
        pdf.txt(s, 9)
    pdf.rule(3)
    left = [f"{title} no: {doc['number']}", f"Date: {date.fromisoformat(doc['day']):%d %b %Y}"]
    if kind != "invoice":
        left.append(f"Against bill: {inv['number']} dated {date.fromisoformat(inv['day']):%d %b %Y}")
        left.append(f"Reason: {doc['reason']}")
    pos = inv["place_of_supply"]
    left.append(f"Place of supply: {STATE_CODES.get(pos, pos)} ({pos})")
    for s in left:
        pdf.txt(s, 9)
    if inv.get("customer_name") or inv.get("customer_gstin"):
        pdf.ln(1)
        pdf.txt("Bill to: " + ", ".join(x for x in [inv.get("customer_name"), inv.get("customer_phone")] if x), 9, True)
        if inv.get("customer_gstin"):
            pdf.txt(f"GSTIN: {inv['customer_gstin']}", 9)
    pdf.ln(2)

    lines = doc["lines"]
    if tax:
        cols = [("Item", 58, "L"), ("HSN", 14, "L"), ("Qty", 14, "R"), ("Rate", 17, "R"), ("Disc", 13, "R"),
                ("Taxable", 18, "R"), ("GST%", 10, "R")]
        cols += [("IGST", 21, "R")] if inter else [("CGST", 10.5, "R"), ("SGST", 10.5, "R")]
        cols += [("Amount", 21, "R")]
    else:
        cols = [("Item", 92, "L"), ("HSN", 16, "L"), ("Qty", 20, "R"), ("Rate", 22, "R"), ("Disc", 16, "R"),
                ("Amount", 20, "R")]
    pdf.set_fill_color(235, 235, 235)
    pdf.row([(c, w, a) for c, w, a in cols], 8, True)
    pdf.rule(0.6)
    for i, ln in enumerate(lines, 1):
        qty = ln["qty"]
        disc = ln.get("discount", 0.0) if kind == "invoice" else 0.0
        vals = {"Item": f"{i}. {ln['name']}", "HSN": ln.get("hsn") or "", "Qty": _qty(qty, ln["unit"]),
                "Rate": _inr(ln["unit_price"]), "Disc": _inr(disc) if disc else "", "Taxable": _inr(ln["taxable"]),
                "GST%": f"{ln['gst_rate']:g}", "CGST": _inr(ln["cgst"]), "SGST": _inr(ln["sgst"]),
                "IGST": _inr(ln["igst"]), "Amount": _inr(ln["amount"])}
        pdf.row([(vals[c], w, a) for c, w, a in cols], 8)
    pdf.rule(1.5)

    def total_row(label: str, value: str, bold: bool = False) -> None:
        pdf.row([("", 110, "L"), (label, 50, "L"), (value, 26, "R")], 9 if not bold else 10, bold, 5)

    if kind == "invoice":
        total_row("Items value", _inr(doc["gross"]))
        if doc["discount"]:
            total_row("Discount", "-" + _inr(doc["discount"]))
    if tax:
        total_row("Taxable value", _inr(doc["taxable"]))
        if inter:
            total_row("IGST", _inr(doc["igst"]))
        else:
            total_row("CGST", _inr(doc["cgst"]))
            total_row("SGST", _inr(doc["sgst"]))
    if abs(doc["round_off"]) >= 0.005:
        total_row("Round off", _inr(doc["round_off"]))
    total_row("Total" if kind == "invoice" else "Total credit", "₹" + _inr(doc["total"]), True)
    pdf.ln(1)
    pdf.txt(rupees_in_words(doc["total"]), 9)
    if kind == "invoice":
        pays = [f"{m} ₹{_inr(doc[k])}" for m, k in (("Cash", "paid_cash"), ("UPI", "paid_upi"), ("Udhaar", "udhaar"))
                if doc[k]]
        pdf.txt("Paid by: " + ", ".join(pays), 9)
    else:
        pdf.txt(f"Refund: {doc['refund_mode'].upper() if doc['refund_mode'] != 'udhaar' else 'adjusted in udhaar'}", 9)

    if tax and lines:
        pdf.ln(2)
        pdf.txt("Tax summary", 9, True)
        head = [("GST rate", 30, "L"), ("Taxable", 35, "R")]
        head += [("IGST", 35, "R")] if inter else [("CGST", 30, "R"), ("SGST", 30, "R")]
        pdf.row(head, 8, True)
        for rate, taxable, cgst, sgst, igst in _tax_by_rate(lines):
            vals = [(f"{rate:g}%", 30, "L"), (_inr(taxable), 35, "R")]
            vals += [(_inr(igst), 35, "R")] if inter else [(_inr(cgst), 30, "R"), (_inr(sgst), 30, "R")]
            pdf.row(vals, 8)
    pdf.ln(3)
    if inv["doc_type"] == "bill_of_supply":
        pdf.txt("Composition taxable person, not eligible to collect tax on supplies.", 8)
    if tax:
        pdf.txt("Prices include GST. This is a computer-generated document.", 8)
        pdf.ln(6)
        pdf.txt(f"For {shop['name']}", 9, align="R")
        pdf.ln(6)
        pdf.txt("Authorised signatory", 8, align="R")
    else:
        pdf.txt("This is a computer-generated document.", 8)


# ------------------------------------------------------------------ thermal
def _thermal(pdf: _Doc, doc: dict, shop: dict, kind: str, narrow: bool) -> None:
    inv = doc if kind == "invoice" else doc["invoice"]
    tax = inv["doc_type"] == "tax_invoice"
    s = 7 if narrow else 8
    w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.txt(shop["name"], s + 2, True, "C")
    for line in _shop_lines(shop):
        pdf.txt(line, s - 0.5, align="C")
    pdf.rule(2)
    title = doc["title"] if kind == "invoice" else "Credit Note"
    pdf.txt(title.upper(), s + 1, True, "C")
    if kind == "invoice" and doc["status"] == "cancelled":
        pdf.txt("*** CANCELLED ***", s + 1, True, "C")
    pdf.txt(f"No: {doc['number']}", s)
    pdf.txt(f"Date: {date.fromisoformat(doc['day']):%d %b %Y}", s)
    if kind != "invoice":
        pdf.txt(f"Against bill: {inv['number']}", s)
    if inv.get("customer_name"):
        pdf.txt(f"Customer: {inv['customer_name']}", s)
    if inv.get("customer_gstin"):
        pdf.txt(f"GSTIN: {inv['customer_gstin']}", s)
    pdf.rule(2)
    qw, aw = w * 0.42, w * 0.3
    for ln in doc["lines"]:
        pdf.txt(ln["name"] + (f" ({ln['gst_rate']:g}%)" if tax else ""), s, True)
        disc = ln.get("discount", 0.0) if kind == "invoice" else 0.0
        left = f"{_qty(ln['qty'], ln['unit'])} x {_inr(ln['unit_price'])}" + (f" -{_inr(disc)}" if disc else "")
        pdf.row([(left, qw + (w - qw - aw), "L"), (_inr(ln["amount"]), aw, "R")], s, h=s * 0.45)
    pdf.rule(2)

    def tot(label: str, value: str, bold: bool = False) -> None:
        pdf.row([(label, w - aw, "L"), (value, aw, "R")], s + (1 if bold else 0), bold, h=(s + 1) * 0.45)

    if kind == "invoice" and doc["discount"]:
        tot("Discount", "-" + _inr(doc["discount"]))
    if tax:
        tot("Taxable", _inr(doc["taxable"]))
        if doc["igst"]:
            tot("IGST", _inr(doc["igst"]))
        else:
            tot("CGST", _inr(doc["cgst"]))
            tot("SGST", _inr(doc["sgst"]))
    if abs(doc["round_off"]) >= 0.005:
        tot("Round off", _inr(doc["round_off"]))
    tot("TOTAL", "₹" + _inr(doc["total"]), True)
    if kind == "invoice":
        for m, k in (("Cash", "paid_cash"), ("UPI", "paid_upi"), ("Udhaar", "udhaar")):
            if doc[k]:
                tot(m, _inr(doc[k]))
    if tax and not narrow:
        pdf.rule(2)
        for rate, taxable, cgst, sgst, igst in _tax_by_rate(doc["lines"]):
            gst = f"IGST {_inr(igst)}" if igst else f"C {_inr(cgst)} S {_inr(sgst)}"
            pdf.txt(f"GST {rate:g}% on {_inr(taxable)}: {gst}", s - 0.5)
    pdf.rule(2)
    if inv["doc_type"] == "bill_of_supply":
        pdf.txt("Composition taxable person, not eligible to collect tax on supplies.", s - 1, align="C")
    elif tax:
        pdf.txt("Prices include GST", s - 1, align="C")
    pdf.txt("Thank you! Visit again", s, align="C")


def render(doc: dict, shop: dict, layout: str = "a4", kind: str = "invoice") -> bytes:
    """doc: billing.get_invoice(...) or billing.get_credit_note(...); shop: billing.store_settings(...)."""
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {', '.join(LAYOUTS)}")
    width, margin = LAYOUTS[layout], MARGIN[layout]
    if layout == "a4":
        pdf = _Doc(width, 297.0, margin)
        _a4(pdf, doc, shop, kind)
        return bytes(pdf.output())
    probe = _Doc(width, 3000.0, margin)
    _thermal(probe, doc, shop, kind, layout == "58mm")
    height = probe.get_y() + margin + 2
    pdf = _Doc(width, height, margin)
    _thermal(pdf, doc, shop, kind, layout == "58mm")
    return bytes(pdf.output())
