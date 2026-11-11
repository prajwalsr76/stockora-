"""Ordering cadence by shelf life, shared by the reorder agent and the policy simulator.

Real shops get bread and milk daily, dairy and eggs every few days, dry goods weekly.
A single weekly cycle for a 10-day-shelf-life item cannot work: the stock needed to last
a week (plus delivery time) would expire first.
"""


def review_days(shelf_life: int | None) -> int:
    s = shelf_life or 365
    return 1 if s <= 7 else 3 if s <= 21 else 7


def shelf_cap_days(shelf_life: int | None) -> float:
    """Most days of demand worth holding: fresh items up to ~90% of shelf life, others 60%."""
    s = shelf_life or 365
    return 0.9 * s if s <= 21 else 0.6 * s


def is_fresh(shelf_life: int | None) -> bool:
    return (shelf_life or 365) <= 21
