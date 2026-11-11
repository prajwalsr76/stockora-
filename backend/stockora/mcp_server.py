"""Stockora as an MCP server: any MCP client (Claude Desktop, Cursor, VS Code, your own
agents) can query the store with the same tools the in-app assistant uses.

Read tools are open. The only write tool drafts orders; it cannot send or pay.

Run:  python -m stockora.mcp_server            (stdio, for desktop clients)
      python -m stockora.mcp_server --http      (streamable HTTP)
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from .agents import reorder
from .db import Repo, connect
from .service import StoreBrain

_brain: StoreBrain | None = None


def get_brain() -> StoreBrain:
    global _brain
    if _brain is None:
        db = Path(os.getenv("STOCKORA_DB", "stockora.db"))
        conn = connect(db)
        if conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 0:
            from .seed import build_demo_store
            build_demo_store(conn, date.today())
        _brain = StoreBrain(Repo(conn, int(os.getenv("STOCKORA_STORE_ID", "1")))).refresh()
    return _brain


def set_brain(brain: StoreBrain) -> None:
    """Inject a brain (tests, or when embedded in the API process)."""
    global _brain
    _brain = brain


server = MCPServer(
    name="stockora",
    instructions="Inventory, forecasts, orders, expiry and udhaar for an Indian kirana store. "
                 "Quantities are units; money is INR. Orders are drafts until the owner approves them in the app.",
)


@server.tool(description="Products running low: stock, 7-day forecast and days of stock left.")
def low_stock(limit: int = 10) -> list[dict]:
    return get_brain().stock_summary(low_only=True)[:limit]


@server.tool(description="Stock and forecast for one product (fuzzy name match, e.g. 'parle g').")
def product_status(name: str) -> dict:
    b = get_brain()
    from .matching import ProductMatcher
    m = ProductMatcher([(r["product_id"], r["alias"]) for r in b.repo.aliases()])
    cands = m.top(name, k=1)
    if not cands or cands[0].score < 0.3:
        return {"error": f"no product matches '{name}'"}
    pid = cands[0].product_id
    row = next(r for r in b.stock_summary() if r["product_id"] == pid)
    return {**row, "forecast": b.forecast_for(pid)}


@server.tool(description="Suggested purchase orders (cash-aware, scheme-aware) with reasons.")
def order_plan() -> dict:
    p = get_brain().order_plan()
    d = p.to_dict()
    d["lines"] = d["lines"][:25]
    d["deferred"] = d["deferred"][:15]
    return d


@server.tool(description="Create DRAFT orders from the current plan. Nothing is sent; the owner must approve in the app.")
def draft_orders() -> dict:
    b = get_brain()
    ids = reorder.commit_plan(b.repo, b.order_plan())
    return {"draft_order_ids": ids, "note": "Drafts only. The owner approves or edits them in Stockora."}


@server.tool(description="Batches likely to expire unsold, with value at risk and suggested action.")
def expiry_risk() -> list[dict]:
    return get_brain().expiry()


@server.tool(description="Items that ran out in the last N days: days out of stock and units of demand missed.")
def lost_sales(days: int = 30) -> dict:
    return get_brain().lost_sales(days)


@server.tool(description="Customer credit (udhaar) outstanding, largest first.")
def udhaar() -> list[dict]:
    return get_brain().repo.receivables()


@server.tool(description="Morning briefing: yesterday's sales, low stock, expiry, suggested order, festivals.")
def morning_briefing() -> dict:
    return get_brain().morning_briefing()


@server.resource("stockora://store/profile", description="Store profile")
def store_profile() -> dict:
    s = get_brain().repo.store()
    return {"name": s["name"], "city": s["city"], "state_code": s["state_code"], "gstin": s["gstin"]}


if __name__ == "__main__":
    server.run("streamable-http" if "--http" in sys.argv else "stdio")
