"""Agent workflows as explicit graphs with a human-approval interrupt.

The node functions are plain Python (unit-tested). They are wired two ways:
  * `run_graph`  - a tiny built-in runner (no extra dependencies)
  * `build_langgraph_*` - the same nodes as a LangGraph StateGraph with a
    checkpointer and interrupt_before the approval node (pip install langgraph)

Workflows
  daily_ops:   refresh_forecast -> scan_expiry -> plan_orders -> guard -> [APPROVAL] -> commit
  bill_intake: extract -> validate -> match -> route -> [REVIEW if needed] -> post
"""
from __future__ import annotations

from typing import Any, Callable, TypedDict

from ..genai import intake
from ..genai.guardrails import check_order_limits
from ..service import StoreBrain
from . import reorder

Node = Callable[[dict], dict]


class DailyOpsState(TypedDict, total=False):
    brain: Any
    expiring: Any
    plan: Any
    guard_issues: list
    approved: bool
    order_ids: list
    log: list


def n_refresh(s: dict) -> dict:
    s["brain"].refresh()
    return {"log": s.get("log", []) + ["forecast refreshed"]}


def n_expiry(s: dict) -> dict:
    exp = s["brain"].expiry()
    return {"expiring": exp, "log": s["log"] + [f"{len(exp)} expiry alerts"]}


def n_plan(s: dict) -> dict:
    plan = s["brain"].order_plan()
    return {"plan": plan, "log": s["log"] + [f"plan Rs {plan.total:,.0f}, {len(plan.lines)} lines"]}


def n_guard(s: dict) -> dict:
    issues = []
    by_dist: dict[int, list] = {}
    for l in s["plan"].lines:
        by_dist.setdefault(l.distributor_id, []).append({"qty": l.qty, "unit_price": l.unit_price})
    for lines in by_dist.values():
        issues += check_order_limits(lines)
    return {"guard_issues": issues, "log": s["log"] + [f"guardrails: {len(issues)} issues"]}


def n_commit(s: dict) -> dict:
    if not s.get("approved"):
        return {"log": s["log"] + ["not approved: nothing committed"]}
    ids = reorder.commit_plan(s["brain"].repo, s["plan"])
    return {"order_ids": ids, "log": s["log"] + [f"draft orders {ids} created for owner review"]}


DAILY_OPS: list[tuple[str, Node]] = [("refresh", n_refresh), ("expiry", n_expiry), ("plan", n_plan),
                                     ("guard", n_guard), ("commit", n_commit)]
DAILY_OPS_INTERRUPT = "commit"   # pause before committing; the owner sets state["approved"]


# ------------------------------------------------------------------ bill intake
def b_validate(s: dict) -> dict:
    repo = s["repo"]
    issues = intake.validate_bill(s["bill"], repo.store()["state_code"], repo)
    return {"issues": issues}


def b_match(s: dict) -> dict:
    return {"matches": intake.match_lines(s["repo"], s["bill"])}


def b_route(s: dict) -> dict:
    return {"needs_review": intake.review_needed(s["issues"], s["matches"])}


def b_post(s: dict) -> dict:
    if s.get("needs_review") and not s.get("reviewed"):
        return {"status": "waiting_for_review"}
    if any(i["severity"] == "error" for i in s["issues"]) and not s.get("override_errors"):
        return {"status": "rejected"}
    pid = intake.post_bill(s["repo"], s["bill"], s["matches"])
    return {"purchase_id": pid, "status": "posted"}


BILL_INTAKE: list[tuple[str, Node]] = [("validate", b_validate), ("match", b_match), ("route", b_route),
                                       ("post", b_post)]


def run_graph(nodes: list[tuple[str, Node]], state: dict, interrupt_before: str | None = None,
              resume_from: str | None = None) -> tuple[dict, str | None]:
    """Run nodes in order, merging returned updates. Returns (state, paused_at)."""
    started = resume_from is None
    for name, fn in nodes:
        if not started:
            started = name == resume_from
            if not started:
                continue
        elif name == interrupt_before and resume_from != name:
            return state, name
        state = {**state, **fn(state)}
    return state, None


def daily_ops(brain: StoreBrain) -> tuple[dict, str | None]:
    """Run until the approval interrupt."""
    return run_graph(DAILY_OPS, {"brain": brain, "log": []}, interrupt_before=DAILY_OPS_INTERRUPT)


def resume_daily_ops(state: dict, approved: bool) -> dict:
    final, _ = run_graph(DAILY_OPS, {**state, "approved": approved}, resume_from=DAILY_OPS_INTERRUPT)
    return final


# ------------------------------------------------------------------ LangGraph wiring
def build_langgraph_daily_ops(brain: StoreBrain):
    """Same workflow as a LangGraph graph with checkpointing and an approval interrupt.

    The checkpointer saves the state after every node, so the state must hold only
    plain data. The StoreBrain (which holds the database connection) and the order
    plan object therefore stay outside the state: the brain is bound to the nodes
    here, and the plan is kept in a per-graph holder and summarised in the state.
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    held: dict = {}                      # live objects that must not be checkpointed

    def bind(fn: Node) -> Node:
        def node(s: dict) -> dict:
            out = fn({**s, "brain": brain, "plan": held.get("plan")})
            if "plan" in out:
                held["plan"] = out["plan"]
                out["plan"] = {"lines": len(held["plan"].lines)}
            if "expiring" in out:
                out["expiring"] = len(out["expiring"])
            return out
        return node

    g = StateGraph(DailyOpsState)
    for name, fn in DAILY_OPS:
        g.add_node(name, bind(fn))
    g.add_edge(START, DAILY_OPS[0][0])
    for (a, _), (b, _) in zip(DAILY_OPS, DAILY_OPS[1:]):
        g.add_edge(a, b)
    g.add_edge(DAILY_OPS[-1][0], END)
    return g.compile(checkpointer=MemorySaver(), interrupt_before=[DAILY_OPS_INTERRUPT])
