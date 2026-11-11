"""Stockora evaluation suite. Writes evals/report.json and evals/REPORT.md.

  python -m evals.run_evals            full suite (~2-3 min)
  python -m evals.run_evals --quick    fewer backtest origins (CI)

Every metric states what data it was computed on. Synthetic data is labelled.
"""
from __future__ import annotations

import json
import random
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from stockora.db import connect
from stockora.forecasting.model import backtest
from stockora.forecasting.policy_sim import compare_policies
from stockora.forecasting.simulator import demand_frame, gut_feel_sales
from stockora.genai import intake
from stockora.genai.assistant import OwnerAssistant
from stockora.genai.guardrails import numbers_grounded
from stockora.genai.rag import HybridRetriever
from stockora.india import gstin_checksum, load_catalog
from stockora.matching import ProductMatcher
from stockora.seed import build_demo_store
from stockora.service import StoreBrain

HERE = Path(__file__).parent
TODAY = date(2026, 11, 7)


def jsonl(name: str) -> list[dict]:
    return [json.loads(l) for l in (HERE / "datasets" / name).read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------------------------ forecasting & policy
def eval_forecast(quick: bool) -> dict:
    hist = gut_feel_sales(demand_frame(date(2025, 1, 1), TODAY))
    origins = [date(2026, 10, 24)] if quick else [date(2026, 9, 12), date(2026, 9, 26), date(2026, 10, 10),
                                                    date(2026, 10, 24)]
    bt = backtest(hist, origins)
    m, ma, floor = bt["model"]["wape_weekly"], bt["moving_avg_28"]["wape_weekly"], bt["oracle_noise_floor"]["wape_weekly"]
    bt["gap_closed_vs_moving_avg"] = round((ma - m) / (ma - floor), 3) if ma > floor else None
    bt["data"] = "synthetic demand, 90 real SKUs, Jan 2025 - Nov 2026; scored on true demand"
    return {"backtest": bt, "policy": compare_policies(hist)}


# ------------------------------------------------------------------ product matching
HINGLISH = {"Salt": "namak", "Sugar": "cheeni", "Milk": "doodh", "Oil": "tel", "Tea": "chai", "Soap": "sabun"}


def _typo(word: str, rng: random.Random) -> str:
    j = rng.randrange(1, len(word) - 1)
    op = rng.choice(["delete", "swap", "double"])
    if op == "delete":
        return word[:j] + word[j + 1:]
    if op == "swap":
        return word[:j - 1] + word[j] + word[j - 1] + word[j + 1:]
    return word[:j] + word[j] + word[j:]


def variants(name: str, rng: random.Random) -> list[str]:
    """Five increasingly messy ways a distributor bill or an owner might write a product."""
    words = name.split()
    long_idx = [i for i, w in enumerate(words) if len(w) >= 5 and w.isalpha()]
    out = []
    v = re.sub(r"(\d)g\b", r"\1 GM", name).replace("ml", " ML").replace("kg", " KG").upper()
    out.append(v)                                                  # 1. bill style caps + units
    w1 = words.copy()
    if long_idx:
        i = rng.choice(long_idx)
        w1[i] = _typo(w1[i], rng)
    out.append(" ".join(w1).lower())                               # 2. one typo
    swapped = name
    for en, hi in HINGLISH.items():
        swapped = swapped.replace(en, hi)
    out.append(swapped if swapped != name else " ".join(words[1:] + words[:1]))  # 3. Hinglish / word order
    w4 = [w[:4] if len(w) > 5 and w.isalpha() else w for w in words]
    out.append(" ".join(w4))                                       # 4. abbreviated words ("Fort Sunf Refi Oil")
    w5 = words.copy()
    for i in rng.sample(long_idx, min(2, len(long_idx))):
        w5[i] = _typo(w5[i], rng)
    out.append(" ".join(w5[1:] if len(w5) > 2 else w5))            # 5. brand dropped + two typos
    return out


def eval_matching() -> dict:
    cat = load_catalog()
    m = ProductMatcher([(i, c.name) for i, c in enumerate(cat)])
    rng = random.Random(42)
    total = top1 = auto = auto_correct = 0
    errors = []
    for i, c in enumerate(cat):
        for v in variants(c.name, rng):
            total += 1
            hit, cands = m.resolve(v)
            top1 += cands[0].product_id == i
            if hit:
                auto += 1
                auto_correct += hit.product_id == i
            elif cands[0].product_id != i and len(errors) < 8:
                errors.append({"input": v, "expected": c.name, "got": cat[cands[0].product_id].name})
    return {"n": total, "top1_accuracy": round(top1 / total, 4), "auto_accept_rate": round(auto / total, 4),
            "auto_accept_precision": round(auto_correct / max(auto, 1), 4),
            "sent_to_review": round(1 - auto / total, 4), "sample_errors": errors,
            "data": "5 programmatic messy variants per real catalog name: bill caps/units, typo, Hinglish/word order, abbreviations, brand dropped + 2 typos. Caveat: 90 distinct SKUs; stores with many near-identical SKUs will be harder, which is why low-confidence matches go to review"}


# ------------------------------------------------------------------ bill validation
ERROR_TYPES = ["gstin_typo", "line_amount", "grand_total", "cgst_sgst_mismatch", "igst_same_state",
               "invalid_slab", "rate_above_mrp", "already_expired"]
EXPECTED_FIELD = {"gstin_typo": "distributor_gstin", "line_amount": "amount", "grand_total": "grand_total",
                  "cgst_sgst_mismatch": "sgst", "igst_same_state": "igst", "invalid_slab": "gst_rate",
                  "rate_above_mrp": "rate", "already_expired": "expiry"}


def make_bill(rng: random.Random, k: int) -> dict:
    cat = load_catalog()
    first14 = f"27ABCDE{1000 + k % 9000:04d}F1Z"
    lines, taxable, tax = [], 0.0, 0.0
    for c in rng.sample(cat, rng.randint(2, 6)):
        qty = rng.randint(2, 40)
        amount = round(qty * c.cost_price, 2)
        lines.append({"name": c.name, "hsn": c.hsn, "qty": qty, "rate": c.cost_price, "gst_rate": c.gst_rate,
                      "mrp": c.mrp, "expiry": (TODAY + timedelta(days=c.shelf_life_days)).isoformat(),
                      "amount": amount})
        taxable += amount
        tax += round(amount * c.gst_rate / 100, 2)
    half = round(tax / 2, 2)
    return {"distributor_name": "Test Distributor", "distributor_gstin": first14 + gstin_checksum(first14),
            "bill_no": f"T-{k}", "bill_date": TODAY.isoformat(), "lines": lines,
            "taxable_total": round(taxable, 2), "cgst": half, "sgst": round(tax - half, 2), "igst": 0,
            "grand_total": round(taxable + tax, 2)}


def corrupt(b: dict, kind: str, rng: random.Random) -> dict:
    l = b["lines"][0]
    if kind == "gstin_typo":
        g = b["distributor_gstin"]
        b["distributor_gstin"] = g[:9] + str((int(g[9]) + 1) % 10) + g[10:]
    elif kind == "line_amount":
        l["amount"] = round(l["amount"] * 1.1 + 5, 2)
    elif kind == "grand_total":
        b["grand_total"] = round(b["grand_total"] + rng.choice([10, 50, 100]), 2)
    elif kind == "cgst_sgst_mismatch":
        b["sgst"] = round(b["sgst"] + 7.5, 2)
        b["grand_total"] = round(b["grand_total"] + 7.5, 2)
    elif kind == "igst_same_state":
        b["igst"], b["cgst"], b["sgst"] = round(b["cgst"] + b["sgst"], 2), 0, 0
        if b["igst"] == 0:
            b["igst"] = 1.0
            b["grand_total"] = round(b["grand_total"] + 1.0, 2)
    elif kind == "invalid_slab":
        l["gst_rate"] = 12
    elif kind == "rate_above_mrp":
        l["rate"] = l["mrp"] + 2
        l["amount"] = round(l["qty"] * l["rate"], 2)
    elif kind == "already_expired":
        l["expiry"] = (TODAY - timedelta(days=3)).isoformat()
    return b


def eval_bills(n: int = 240) -> dict:
    rng = random.Random(7)
    detected = {k: [0, 0] for k in ERROR_TYPES}
    clean_fp = clean_n = 0
    for k in range(n):
        raw = make_bill(rng, k)
        kind = ERROR_TYPES[k % len(ERROR_TYPES)] if k % 3 else None
        if kind:
            raw = corrupt(raw, kind, rng)
        bill = intake.Bill.model_validate(raw)
        errors = [i for i in intake.validate_bill(bill, "27") if i["severity"] == "error"]
        if kind:
            detected[kind][1] += 1
            detected[kind][0] += any(EXPECTED_FIELD[kind] in i["field"] for i in errors)
        else:
            clean_n += 1
            clean_fp += bool(errors)
    per_type = {k: round(a / b, 3) for k, (a, b) in detected.items()}
    caught = sum(a for a, _ in detected.values())
    total = sum(b for _, b in detected.values())
    return {"n_bills": n, "error_detection_recall": round(caught / total, 4), "per_error_type": per_type,
            "false_alarm_rate_clean_bills": round(clean_fp / max(clean_n, 1), 4),
            "data": "synthetic GST bills from real catalog items; one injected error per corrupted bill"}


# ------------------------------------------------------------------ assistant, guardrails, RAG
def eval_assistant(brain: StoreBrain) -> dict:
    a = OwnerAssistant(brain)
    res = {"dev": [0, 0], "heldout": [0, 0]}
    misses = []
    lat = []
    for row in jsonl("assistant_questions.jsonl"):
        t = time.perf_counter()
        r = a.ask(row["q"])
        lat.append((time.perf_counter() - t) * 1000)
        ok = r.intent == row["intent"]
        res[row["split"]][0] += ok
        res[row["split"]][1] += 1
        if not ok:
            misses.append({"q": row["q"], "expected": row["intent"], "got": r.intent, "split": row["split"]})
    attacks = jsonl("attacks.jsonl")
    blocked = sum(a.ask(x["q"]).intent == "refused" for x in attacks if x["attack"])
    false_ref = sum(a.ask(x["q"]).intent == "refused" for x in attacks if not x["attack"])
    n_att = sum(x["attack"] for x in attacks)
    lat.sort()
    return {"routing_accuracy_dev": round(res["dev"][0] / res["dev"][1], 4),
            "routing_accuracy_heldout": round(res["heldout"][0] / res["heldout"][1], 4),
            "n_dev": res["dev"][1], "n_heldout": res["heldout"][1], "misses": misses,
            "injection_block_rate": round(blocked / n_att, 4),
            "benign_false_refusal_rate": round(false_ref / (len(attacks) - n_att), 4),
            "p50_latency_ms_offline": round(lat[len(lat) // 2], 1),
            "note": "Offline mode (rules + templates, no LLM). Held-out questions were written before running "
                    "the router on them and are not used for tuning."}


def eval_grounding(brain: StoreBrain) -> dict:
    a = OwnerAssistant(brain)
    rng = random.Random(1)
    caught = n_pert = false_alarm = n_clean = 0
    for row in jsonl("assistant_questions.jsonl"):
        r = a.ask(row["q"])
        ev = json.dumps(r.evidence, default=str)
        ok, _ = numbers_grounded(r.answer, ev)
        n_clean += 1
        false_alarm += not ok
        nums = [m for m in re.finditer(r"\d[\d,]*\.?\d*", r.answer) if float(m.group().replace(",", "")) >= 10]
        if not nums:
            continue
        m = rng.choice(nums)
        val = float(m.group().replace(",", ""))
        fake = f"{val * rng.uniform(1.07, 1.4):.0f}"
        perturbed = r.answer[:m.start()] + fake + r.answer[m.end():]
        n_pert += 1
        caught += not numbers_grounded(perturbed, ev)[0]
    return {"hallucinated_number_catch_rate": round(caught / max(n_pert, 1), 4), "n_perturbed": n_pert,
            "false_alarm_rate": round(false_alarm / n_clean, 4),
            "data": "assistant answers with one number inflated 7-40% (simulated LLM hallucination)"}


def eval_rag(brain: StoreBrain) -> dict:
    r = HybridRetriever.from_repo(brain.repo)
    cases = [("parle g scheme", "Nashik"), ("maggi free offer", "Shree Ganesh"), ("oil returns allowed", "Balaji"),
             ("which days does balaji deliver", "Balaji"), ("credit limit per customer", "udhaar"),
             ("near expiry biscuits replacement", "Nashik"), ("cash discount same day payment", "Shree Ganesh"),
             ("dudh bread wapas karna", "Shree Ganesh"), ("minimum order 3000", "Nashik"),
             ("tata salt scheme", "Balaji"), ("old dues 60 days no new credit", "udhaar"),
             ("surf excel offer", "Nashik")]
    h1 = h3 = 0
    for q, want in cases:
        titles = [x["title"] for x in r.search(q, k=3)]
        h1 += want.lower() in titles[0].lower()
        h3 += any(want.lower() in t.lower() for t in titles)
    return {"n": len(cases), "hit_at_1": round(h1 / len(cases), 3), "hit_at_3": round(h3 / len(cases), 3)}


def to_markdown(r: dict) -> str:
    f, p = r["forecast"]["backtest"], r["forecast"]["policy"]
    g, s, lean = p["gut_feel"], p["stockora"], p["stockora_lean"]
    pct = lambda a, b: f"{(b - a) / a * 100:+.1f}%"  # noqa: E731
    a, b, m = r["assistant"], r["bills"], r["matching"]
    return f"""# Stockora evaluation report

Generated {r['generated']} in {r['seconds']} s. Synthetic data is labelled as such; see `evals/run_evals.py`.

## Forecasting (rolling-origin backtest, {f['data']})
| Method | WAPE weekly | WAPE daily | Bias |
|---|---|---|---|
| Stockora model | {f['model']['wape_weekly']} | {f['model']['wape_daily']} | {f['model']['bias']} |
| 28-day moving average | {f['moving_avg_28']['wape_weekly']} | {f['moving_avg_28']['wape_daily']} | {f['moving_avg_28']['bias']} |
| Same weekday last week | {f['seasonal_naive']['wape_weekly']} | {f['seasonal_naive']['wape_daily']} | {f['seasonal_naive']['bias']} |
| Noise floor (true expected demand) | {f['oracle_noise_floor']['wape_weekly']} | {f['oracle_noise_floor']['wape_daily']} | {f['oracle_noise_floor']['bias']} |

The model closes **{f['gap_closed_vs_moving_avg'] * 100:.0f}%** of the gap between the moving average and the noise floor.

## Ordering policy, closed-loop simulation ({p['window']}, synthetic demand)
| Metric | Gut feel | Stockora | Change | Stockora lean | Change |
|---|---|---|---|---|---|
| Stockout days | {g['stockout_day_rate']:.1%} | {s['stockout_day_rate']:.1%} | {pct(g['stockout_day_rate'], s['stockout_day_rate'])} | {lean['stockout_day_rate']:.1%} | {pct(g['stockout_day_rate'], lean['stockout_day_rate'])} |
| Fill rate | {g['fill_rate']:.1%} | {s['fill_rate']:.1%} | {pct(g['fill_rate'], s['fill_rate'])} | {lean['fill_rate']:.1%} | {pct(g['fill_rate'], lean['fill_rate'])} |
| Expired stock | – | – | {pct(g['expired_value'], s['expired_value'])} | – | {pct(g['expired_value'], lean['expired_value'])} |
| Stock held | – | – | {pct(g['avg_inventory_value'], s['avg_inventory_value'])} | – | {pct(g['avg_inventory_value'], lean['avg_inventory_value'])} |

## Product matching ({m['n']} messy names: caps/units, typos, Hinglish, abbreviations, missing brand)
Top-1 accuracy **{m['top1_accuracy']:.1%}**; auto-accepted {m['auto_accept_rate']:.1%} with precision **{m['auto_accept_precision']:.1%}**; {m['sent_to_review']:.1%} sent to human review.

## Bill validation ({b['n_bills']} bills)
Error detection recall **{b['error_detection_recall']:.1%}**; false alarms on clean bills **{b['false_alarm_rate_clean_bills']:.1%}**.
Per type: {', '.join(f'{k} {v:.0%}' for k, v in b['per_error_type'].items())}

## Owner assistant (offline mode)
Intent routing: dev **{a['routing_accuracy_dev']:.1%}** (n={a['n_dev']}), held-out **{a['routing_accuracy_heldout']:.1%}** (n={a['n_heldout']}).
Prompt-injection block rate **{a['injection_block_rate']:.0%}**, benign false refusals **{a['benign_false_refusal_rate']:.0%}**, p50 latency {a['p50_latency_ms_offline']} ms.
Hallucinated-number guard: catches **{r['grounding']['hallucinated_number_catch_rate']:.0%}** of inflated numbers, false alarms {r['grounding']['false_alarm_rate']:.0%}.
RAG over distributor documents: hit@1 **{r['rag']['hit_at_1']:.0%}**, hit@3 **{r['rag']['hit_at_3']:.0%}**.
"""


def main() -> None:
    quick = "--quick" in sys.argv
    t0 = time.time()
    repo, _ = build_demo_store(connect(), TODAY, days=300)
    brain = StoreBrain(repo, TODAY).refresh()
    report = {"generated": date.today().isoformat(), "forecast": eval_forecast(quick),
              "matching": eval_matching(), "bills": eval_bills(), "assistant": eval_assistant(brain),
              "grounding": eval_grounding(brain), "rag": eval_rag(brain)}
    report["seconds"] = round(time.time() - t0, 1)
    (HERE / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (HERE / "REPORT.md").write_text(to_markdown(report), encoding="utf-8")
    print(to_markdown(report))


if __name__ == "__main__":
    main()
