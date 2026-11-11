"""Guardrails for agents that read untrusted text (bill photos, chat messages, documents).

Principles
1. Untrusted text is DATA. It can never grant permissions or trigger actions.
2. Least privilege: each agent only gets the tools it needs; none can pay or send.
3. Anything that spends money or messages a third party needs explicit owner approval.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules|prompts?)",
    r"disregard (the |all )?(system|previous) (prompt|instructions)",
    r"you are now\b", r"act as (an? )?(admin|developer|system)",
    r"\bsystem prompt\b", r"\bjailbreak\b", r"\bdeveloper mode\b",
    r"(approve|send|place|confirm) (this |the |all )?orders? (automatically|without)",
    r"(transfer|pay|send) (rs\.?|₹|inr)?\s*\d", r"reveal (your|the) (prompt|instructions|api key)",
    r"pichle (sab )?instructions? (bhool|ignore)", r"sab (niyam|rules) (bhool|ignore)",
    # Pure-Hindi (Devanagari) equivalents of the English patterns above.
    r"(निर्देश|नियम|प्रॉम्प्ट)\S*( को)? (भूल|अनदेखा|नज़रअंदाज़|नजरअंदाज)",
    r"अब (आप|तुम) (एक )?(एडमिन|डेवलपर|सिस्टम)", r"(एडमिन|डेवलपर|सिस्टम) की तरह",
    r"सिस्टम प्रॉम्प्ट", r"जेलब्रेक", r"डेवलपर मोड",
    r"ऑर्डर\S* .{0,25}(अपने आप|बिना पूछे|पूछे बिना|बिना मंज़ूरी|बिना मंजूरी)",
    r"(अपने आप|बिना पूछे|पूछे बिना|बिना मंज़ूरी|बिना मंजूरी) .{0,25}ऑर्डर",
    r"\d[\d,]*\s*(रुपये|रुपए|रु\.?|₹) .{0,15}(भेज|ट्रांसफर)", r"(भेज|ट्रांसफर) .{0,15}(₹|रु\.?)\s*\d",
    r"(प्रॉम्प्ट|एपीआई की|पासवर्ड) .{0,12}(बता|दिखा)",
]
_INJ = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]
_PHONE = re.compile(r"(\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}\b")

# Which tools each agent may call. Nothing here can move money.
AGENT_TOOLS = {
    "assistant": {"stock_lookup", "low_stock", "forecast", "order_plan", "expiry", "dead_stock",
                  "lost_sales", "udhaar", "documents", "briefing"},
    "intake": {"match_product", "validate_bill"},
    "reorder": {"plan_orders", "draft_orders"},
}
REQUIRES_APPROVAL = {"send_order", "approve_order", "post_bill", "send_message"}
MAX_ORDER_VALUE = 200_000      # hard cap per drafted order (Rs)
MAX_LINE_QTY = 2_000


@dataclass
class GuardResult:
    allowed: bool
    flags: list[str]
    cleaned: str


def scan_untrusted(text: str) -> GuardResult:
    """Detect instruction-like content in untrusted input; strip control characters."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f​-‏‪-‮]", "", text or "")
    flags = [p.pattern for p in _INJ if p.search(cleaned)]
    return GuardResult(allowed=not flags, flags=flags, cleaned=cleaned[:4000])


def tool_allowed(agent: str, tool: str) -> bool:
    return tool in AGENT_TOOLS.get(agent, set())


def check_order_limits(lines: list[dict]) -> list[str]:
    problems = []
    total = 0.0
    for l in lines:
        if l["qty"] <= 0 or l["qty"] > MAX_LINE_QTY:
            problems.append(f"line qty {l['qty']} outside 1..{MAX_LINE_QTY}")
        total += l["qty"] * l["unit_price"]
    if total > MAX_ORDER_VALUE:
        problems.append(f"order value Rs {total:.0f} exceeds cap Rs {MAX_ORDER_VALUE}")
    return problems


def redact(text: str) -> str:
    """Mask phone numbers before logging or sending text to an LLM provider."""
    return _PHONE.sub(lambda m: m.group(0)[:-5] + "XXXXX", text)


def numbers_grounded(answer: str, evidence: str, tolerance: float = 0.011) -> tuple[bool, list[str]]:
    """Every number >= 10 in the answer must appear (within rounding) in the tool evidence.

    Catches the most damaging LLM failure for a shop owner: a confident wrong number.
    """
    def nums(s: str) -> list[float]:
        return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", s)]
    ev = nums(evidence)
    missing = []
    for n in nums(answer):
        if n < 10:
            continue
        if not any(abs(n - e) <= max(tolerance * max(abs(e), 1), 0.51) for e in ev):
            missing.append(str(n))
    return (not missing, missing)
