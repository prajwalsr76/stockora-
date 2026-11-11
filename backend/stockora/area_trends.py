"""Privacy-preserving area trends: "Beverages are selling more in your area this week".

Why not blockchain: a blockchain copies every record to every participant and makes it
permanent, the opposite of keeping a shop's sales private. What protects shops here:

1. Data minimisation   Only a per-category *relative change* (this week vs the shop's own
                       previous 4 weeks) ever leaves the shop. No units, rupees, product
                       names or customer data.
2. Local differential  Before sending, each shop clips its change to [-50%, +50%] and adds
   privacy             Laplace noise (scale = 1.0 / epsilon). Even the server cannot know a
                       single shop's true figure.
3. k-anonymity         An area/category/week is published only if at least `k` (default 5)
                       distinct shops reported; small areas see nothing rather than one
                       neighbour's numbers.
4. Unlinkability       Each report carries a fresh random token, so reports from the same
                       shop cannot be joined across weeks.

Published per area x category x week: number of shops, share of shops whose (noisy)
change is up, and the median noisy change.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from statistics import median

import numpy as np

CLIP = 0.5          # +-50%: bounds each shop's influence (sensitivity = 1.0)
DEFAULT_EPSILON = 2.0
DEFAULT_K = 5


@dataclass(frozen=True)
class Report:
    """The only thing a shop ever sends."""
    token: str          # random per report; not linked to the shop
    area: str           # coarse area, e.g. first 4 digits of the PIN code
    week: str           # ISO week, e.g. "2026-W45"
    category: str
    change: float       # noisy, clipped relative change


def local_change(this_week: float, prev_4_weeks_avg: float) -> float | None:
    """Relative change of a shop's own sales. None when there is no baseline."""
    if prev_4_weeks_avg <= 0:
        return None
    return float(np.clip(this_week / prev_4_weeks_avg - 1.0, -CLIP, CLIP))


def make_report(area: str, week: str, category: str, change: float, epsilon: float = DEFAULT_EPSILON,
                rng: np.random.Generator | None = None) -> Report:
    """Runs ON THE SHOP's device: clip, add Laplace noise, attach a fresh random token."""
    rng = rng or np.random.default_rng()
    noisy = float(np.clip(change, -CLIP, CLIP) + rng.laplace(0.0, (2 * CLIP) / epsilon))
    return Report(secrets.token_hex(8), area, week, category, round(noisy, 4))


@dataclass
class AreaTrend:
    area: str
    week: str
    category: str
    shops: int
    share_up: float
    median_change: float

    def message(self) -> str:
        direction = "more" if self.median_change > 0.03 else "less" if self.median_change < -0.03 else "about the same"
        return (f"{self.category}: selling {direction} in your area this week "
                f"({round(self.share_up * self.shops)} of {self.shops} shops up)")


def aggregate(reports: list[Report], k: int = DEFAULT_K) -> list[AreaTrend]:
    """Runs on the server. Publishes only groups with at least k distinct reports."""
    groups: dict[tuple[str, str, str], list[float]] = {}
    seen: set[str] = set()
    for r in reports:
        if r.token in seen:          # one vote per token
            continue
        seen.add(r.token)
        groups.setdefault((r.area, r.week, r.category), []).append(r.change)
    out = []
    for (area, week, cat), vals in groups.items():
        if len(vals) < k:
            continue
        out.append(AreaTrend(area, week, cat, len(vals), round(sum(v > 0 for v in vals) / len(vals), 3),
                             round(median(vals), 4)))
    return sorted(out, key=lambda t: (t.area, t.week, -abs(t.median_change)))


def trends_for_shop(trends: list[AreaTrend], area: str, week: str, min_change: float = 0.08) -> list[str]:
    """What a shopkeeper sees: notable moves in their own area only."""
    return [t.message() for t in trends if t.area == area and t.week == week and abs(t.median_change) >= min_change]


# ----------------------------------------------------------------- shop side
def iso_week(day) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def shop_reports(repo, today, area: str, epsilon: float = DEFAULT_EPSILON) -> list[Report]:
    """Build this shop's reports for the last full week (Mon-Sun before `today`). Runs on the shop."""
    from datetime import timedelta
    end = today - timedelta(days=today.weekday())          # this Monday
    start = end - timedelta(days=7)
    base_start = start - timedelta(days=28)
    cats = {p["id"]: p["category"] for p in repo.products(active_only=False)}
    this: dict[str, float] = {}
    base: dict[str, float] = {}
    for r in repo.daily_sales(base_start):
        d = r["day"]
        c = cats.get(r["product_id"])
        if c is None:
            continue
        if start.isoformat() <= d < end.isoformat():
            this[c] = this.get(c, 0.0) + r["units"]
        elif base_start.isoformat() <= d < start.isoformat():
            base[c] = base.get(c, 0.0) + r["units"] / 4
    week = iso_week(start)
    out = []
    for c, b in base.items():
        ch = local_change(this.get(c, 0.0), b)
        if ch is not None:
            out.append(make_report(area, week, c, ch, epsilon))
    return out


# ----------------------------------------------------------------- hub side (central server)
HUB_SCHEMA = """
CREATE TABLE IF NOT EXISTS hub_reports (
  token TEXT PRIMARY KEY, area TEXT NOT NULL, week TEXT NOT NULL, category TEXT NOT NULL,
  change REAL NOT NULL, received_at TEXT NOT NULL
);
"""


def hub_store(conn, reports: list[dict]) -> int:
    conn.executescript(HUB_SCHEMA)
    n = 0
    for r in reports:
        ch = max(-CLIP - 10, min(CLIP + 10, float(r["change"])))       # reject absurd values
        cur = conn.execute("INSERT OR IGNORE INTO hub_reports VALUES (?,?,?,?,?,datetime('now'))",
                           (str(r["token"])[:32], str(r["area"])[:12], str(r["week"])[:10],
                            str(r["category"])[:40], ch))
        n += cur.rowcount
    conn.commit()
    return n


def hub_trends(conn, area: str, week: str | None = None, k: int = DEFAULT_K) -> list[AreaTrend]:
    conn.executescript(HUB_SCHEMA)
    sql = "SELECT token, area, week, category, change FROM hub_reports WHERE area=?"
    args = [area]
    if week:
        sql += " AND week=?"
        args.append(week)
    reps = [Report(*r) for r in conn.execute(sql, args).fetchall()]
    return aggregate(reps, k=k)
