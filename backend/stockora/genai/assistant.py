"""Owner assistant: questions in English or Hindi, answered from the store's own data.

Agent loop
  guard -> detect language -> route intent (rules first, LLM fallback) -> call ONE tool
  -> compose answer (LLM, or templates when offline) -> grounding check -> reply

Design choices worth defending in an interview
* Rules-first routing: most shop questions are a few patterns; rules are free,
  instant and testable. The LLM handles only what rules cannot.
* Numbers come only from tools. An LLM answer containing a number that is not in
  the tool evidence is discarded and replaced by a template answer.
* The assistant can read and plan, never send or pay (see guardrails.AGENT_TOOLS).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

from ..matching import ProductMatcher
from ..service import StoreBrain
from .guardrails import numbers_grounded, redact, scan_untrusted, tool_allowed
from .llm import LLM
from .rag import HybridRetriever

# Each intent has English and Hinglish patterns plus their pure-Hindi (Devanagari) equivalents.
# Patterns were added only from the dev split of evals/datasets/assistant_eval_v2.jsonl or as direct
# Hindi translations of an existing pattern; the held-out split is never used for tuning.
INTENTS: dict[str, list[str]] = {
    "briefing": [r"briefing", r"summary", r"aaj ka (hisaab|report)", r"how (is|was) (the )?(shop|store|business|today)",
                 r"kaisa (raha|chal)", r"सारांश", r"ब्रीफिंग", r"आज का (हिसाब|रिपोर्ट)", r"(कैसी|कैसा) (चल|रहा|रही)"],
    "expiry": [r"expir", r"kharab", r"एक्सपायर", r"खराब", r"सड़"],
    "dead_stock": [r"dead stock", r"nahi? bik", r"not selling", r"slow mov", r"pada (hua|hai)", r"नहीं बिक",
                   r"(धीमे|धीमा|धीमी) बिक", r"पड़ा (हुआ|है)"],
    "lost_sales": [r"lost sale", r"missed sale", r"nuksaan|nuksan|nukasan", r"loss", r"नुकसान", r"छूट",
                   r"stockouts? cost", r"cost me", r"\bran out\b", r"\blos[et]\b", r"stockouts?", r"\bmiss(ed)?\b",
                   r"(माँग|मांग) पूरी नहीं"],
    "udhaar": [r"udh?aa?r", r"credit", r"baaki|baki", r"\bdues?\b", r"उधार", r"collect", r"owes? me", r"pending (money|payment)",
               r"बाकी|बकाया", r"क्रेडिट", r"वसूल", r"पैसे देने", r"भुगतान बाकी"],
    "order_plan": [r"\border", r"kya mang", r"mangw?a", r"purchase", r"kharid", r"reorder", r"मंगा", r"ऑर्डर",
                   r"restock", r"मँगा|मंगवा", r"खरीद", r"रीऑर्डर|रीस्टॉक"],
    "documents": [r"scheme|skeem|offer", r"minimum order", r"delivery", r"\breturns?\b", r"policy", r"terms",
                  r"स्कीम", r"distributor", r"योजना|ऑफर", r"न्यूनतम ऑर्डर", r"डिलीवरी", r"वापस", r"नीति", r"शर्त",
                  r"वितरक|डिस्ट्रीब्यूटर"],
    "forecast": [r"forecast", r"kitna bikega|kitne bikenge", r"how (much|many) will", r"demand", r"agle hafte",
                 r"next week", r"बिकेगा", r"बिकेगी|बिकेंगे", r"बिक्री हो(गी|गा)", r"माँग|मांग", r"पूर्वानुमान",
                 r"(अगले|आने वाले) (हफ्ते|सप्ताह)"],
    "low_stock": [r"khatam", r"low stock", r"running (out|low)", r"out of stock", r"\bkam\b", r"kaunsa maal",
                  r"खत्म", r"refill", r"\bruns? out\b", r"कम हो", r"कम पड़", r"कम स्टॉक", r"स्टॉक से बाहर"],
    "stock_lookup": [r"\bstock\b", r"kitn[aie] (hai|hain|bach)", r"bach[aie]\b", r"how (many|much)", r"left", r"बची|बचे",
                     r"बचा", r"कितना"],
}
PRODUCT_INTENTS = {"stock_lookup", "forecast"}
PRIORITY = ["expiry", "dead_stock", "lost_sales", "udhaar", "documents", "forecast", "order_plan",
            "low_stock", "briefing", "stock_lookup"]
HINGLISH = re.compile(r"\b(kya|kitna|kitne|hai|hain|karna|chahiye|kaun|kaunsa|maal|khatam|bata|batao|aaj|kal|"
                      r"mahine|wala|mera|meri|dukaan|kitni|hua|bikega|mangana|udhaar|udhar|munafa)\b", re.I)


@dataclass
class Reply:
    answer: str
    intent: str
    language: str
    evidence: dict = field(default_factory=dict)
    grounded: bool = True
    used_llm: bool = False
    product: str | None = None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def detect_language(text: str) -> str:
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    return "hinglish" if HINGLISH.search(text) else "en"


def rule_route(text: str) -> tuple[str | None, int]:
    t = text.lower()
    scores = {i: sum(1 for p in pats if re.search(p, t)) for i, pats in INTENTS.items()}
    best = max(scores.values())
    if best == 0:
        return None, 0
    tied = [i for i in PRIORITY if scores[i] == best]
    return tied[0], best


_QWORDS = re.compile(
    r"\b(how|many|much|left|is|are|the|in|stock|of|will|sell|next|week|what|kitn[aie]|bach[aie]|hai|hain|kya|"
    r"bikega|bikenge|bikegi|agle|hafte|ka|ki|ke|forecast|demand|mera|meri|abhi|batao|bata|please|pls)\b", re.I)


# Common brands and grocery words written in Devanagari -> catalogue spelling, so Hindi questions
# find products offline (with an LLM, the model passes English product names to the tools).
HINDI_WORDS = {
    "मैगी": "maggi", "पारले-जी": "parle-g", "पारले": "parle", "अमूल": "amul", "टाटा": "tata", "आशीर्वाद": "aashirvaad",
    "फॉर्च्यून": "fortune", "बिसलेरी": "bisleri", "हल्दीराम": "haldiram", "ब्रिटानिया": "britannia", "कोलगेट": "colgate",
    "सर्फ": "surf excel", "डाबर": "dabur", "कैडबरी": "cadbury", "ओरियो": "oreo", "थम्स अप": "thums up", "कोका-कोला": "coca-cola",
    "आटा": "atta", "नमक": "salt", "चीनी": "sugar", "तेल": "oil", "दूध": "milk", "ब्रेड": "bread", "बिस्कुट": "biscuits",
    "चाय": "tea", "साबुन": "soap", "दाल": "dal", "चावल": "rice", "घी": "ghee", "पनीर": "paneer", "अंडे": "eggs", "मसाला": "masala",
    "हल्दी": "turmeric", "पानी": "water", "नूडल्स": "noodles", "शैम्पू": "shampoo", "दही": "curd",
}
_HINDI_Q = re.compile(r"(कितना|कितनी|कितने|बचा|बची|बचे|है|हैं|क्या|का|की|के|में|अगले|हफ्ते|बिकेगा|बिकेगी|स्टॉक|बताओ|बताइए)")


HINDI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
_EN_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
HINDI_FESTIVALS = {
    "Makar Sankranti": "मकर संक्रांति", "Holi": "होली", "Eid al-Fitr": "ईद-उल-फ़ित्र", "Eid al-Adha": "ईद-उल-अज़हा",
    "Raksha Bandhan": "रक्षा बंधन", "Janmashtami": "जन्माष्टमी", "Ganesh Chaturthi": "गणेश चतुर्थी",
    "Navratri": "नवरात्रि", "Dussehra": "दशहरा", "Diwali": "दिवाली", "Chhath Puja": "छठ पूजा", "Christmas": "क्रिसमस",
}


def hindi_festival(label: str) -> str:
    """'Diwali (08 Nov)' -> 'दिवाली (8 नवंबर)'. Unknown names are left as they are."""
    m = re.fullmatch(r"(.+?) \((\d{1,2}) ([A-Za-z]{3})\)", label.strip())
    if not m or m.group(3) not in _EN_MONTHS:
        return HINDI_FESTIVALS.get(label.strip(), label)
    name = HINDI_FESTIVALS.get(m.group(1), m.group(1))
    return f"{name} ({int(m.group(2))} {HINDI_MONTHS[_EN_MONTHS.index(m.group(3))]})"


HINDI_ACTIONS = {
    "Expired: remove from shelf now and claim return/credit note from distributor":
        "एक्सपायर हो चुका है: अभी शेल्फ से हटाएँ और वितरक से वापसी या क्रेडिट नोट माँगें",
    "Sell at 25-30% off today, or return to distributor if they accept near-expiry stock":
        "आज 25-30% छूट पर बेचें, या वितरक लेता हो तो उसे वापस करें",
    "Offer 10-15% off or a combo (e.g. with a fast seller); move to the counter":
        "10-15% छूट या किसी तेज़ बिकने वाले सामान के साथ कॉम्बो दें; काउंटर पर रखें",
    "Move to the front shelf/counter; stop reordering until it clears":
        "आगे की शेल्फ या काउंटर पर रखें; बिकने तक दोबारा न मंगाएँ",
}
_FEST_ON = re.compile(r"([A-Z][A-Za-z' -]+?) on (\d{1,2} [A-Z][a-z]{2})")


def localize_hi(obj):
    """Translate the fixed English phrases in tool results (festival names and dates, expiry actions)
    into Hindi, so a Hindi answer has nothing English to copy. Names and numbers are unchanged."""
    if isinstance(obj, list):
        return [localize_hi(v) for v in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    for k, v in obj.items():
        if k == "festivals_in_window" and isinstance(v, list):
            out[k] = [hindi_festival(x) if isinstance(x, str) else x for x in v]
        elif k in ("festivals", "festival_note") and isinstance(v, str):
            out[k] = _FEST_ON.sub(lambda m: hindi_festival(f"{m.group(1)} ({m.group(2)})"), v)
        elif k == "action" and isinstance(v, str):
            out[k] = HINDI_ACTIONS.get(v, v)
        else:
            out[k] = localize_hi(v)
    return out


def product_text(text: str) -> str:
    """Remove question words so only the product phrase is matched (Hindi brand names transliterated)."""
    for hi, en in sorted(HINDI_WORDS.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(hi, f" {en} ")
    text = _HINDI_Q.sub(" ", text)
    return re.sub(r"\s+", " ", _QWORDS.sub(" ", re.sub(r"[?!.,।]", " ", text))).strip()


class OwnerAssistant:
    def __init__(self, brain: StoreBrain, llm: LLM | None = None, agentic: bool = False):
        self.brain = brain
        self.llm = llm
        self.agentic = agentic   # LLM chooses and combines tools (needs an LLM with function calling)
        repo = brain.repo
        pairs = [(r["product_id"], r["alias"]) for r in repo.aliases()]
        self.matcher = ProductMatcher(pairs) if pairs else None    # a brand-new shop has no items yet
        self.retriever = HybridRetriever.from_repo(repo)

    # ------------------------------------------------------------- main entry
    def ask(self, question: str, language: str | None = None, history: list[dict] | None = None) -> Reply:
        """language: 'en' or 'hi' for the reply (default: Hindi for Devanagari questions, else English).
        history: earlier turns [{role, content}] so follow-up questions work in agent mode."""
        lang = detect_language(question)
        out = language if language in ("en", "hi") else ("hi" if lang == "hi" else "en")
        guard = scan_untrusted(question)
        if not guard.allowed:
            msg = ("मैं सिर्फ़ आपकी दुकान के स्टॉक, बिक्री और रीस्टॉक में मदद कर सकता हूँ। "
                   "कोई भी ऑर्डर या भुगतान सिर्फ़ आपकी मंज़ूरी से होता है।"
                   if out == "hi" else
                   "I can only help with your shop's stock, sales and restocking. "
                   "Orders and payments always need your approval.")
            return Reply(msg, "refused", lang, flags=["prompt_injection"])

        if self.agentic and self.llm is not None:
            try:
                r = self._agent(guard.cleaned, out, history or [])
            except Exception as e:  # noqa: BLE001 - fall back to the rules pipeline
                r = Reply("", "chat", lang, flags=[f"llm_error:{str(e)[:80]}"])
            if r is not None and r.answer:
                return r
            fallback_flags = r.flags if r is not None else []
        else:
            fallback_flags = []
        lang_out = out

        intent, _ = rule_route(guard.cleaned)
        product = self._find_product(guard.cleaned)
        if intent is None and product is not None:
            intent = "stock_lookup"
        ambiguous = self._ambiguous(guard.cleaned) if product is None else []
        if intent is None and ambiguous:
            intent = "stock_lookup"
        if intent in PRODUCT_INTENTS and product is None and not ambiguous:
            intent = "low_stock" if intent == "stock_lookup" else "order_plan"
        used_llm = False
        if intent is None and self.llm is not None:
            intent, product_name = self._llm_route(guard.cleaned)
            used_llm = True
            if product_name and product is None:
                product = self._find_product(product_name)
        if intent is None:
            return Reply(self._help(lang_out), "help", lang, flags=fallback_flags)
        if not tool_allowed("assistant", intent if intent != "stock_lookup" else "stock_lookup"):
            return Reply("Not permitted.", "refused", lang, flags=["tool_not_allowed"])

        evidence = self._run_tool(intent, product, guard.cleaned, ambiguous)
        template = self._template(intent, evidence, lang_out)
        answer, grounded, flags = template, True, list(fallback_flags)
        if self.llm is not None and not self.agentic:
            try:
                llm_answer = self._compose(guard.cleaned, intent, evidence, lang_out)
                ok, missing = numbers_grounded(llm_answer, json.dumps(evidence, default=str))
                if ok and llm_answer.strip():
                    answer, used_llm = llm_answer.strip(), True
                else:
                    flags.append(f"ungrounded_numbers:{','.join(missing[:5])}")
                    grounded = False   # LLM answer rejected; template served instead
            except Exception as e:  # noqa: BLE001 - LLM outage must not break the shop
                flags.append(f"llm_error:{str(e)[:80]}")
        name = self.brain.repo.product(product)["name"] if product else None
        return Reply(answer, intent, lang, evidence, grounded, used_llm, name, flags)

    # ------------------------------------------------------------- routing helpers
    def _find_product(self, text: str) -> int | None:
        text = product_text(text)
        if not text:
            return None
        if self.matcher is None:
            return None
        m, cands = self.matcher.resolve(text)
        if m:
            return m.product_id
        # Questions contain extra words; accept a clear leader at a lower bar.
        if cands and cands[0].score >= 0.42 and (len(cands) == 1 or cands[0].score - cands[1].score >= 0.12):
            return cands[0].product_id
        return None

    def _ambiguous(self, text: str) -> list[int]:
        """Several close candidates (e.g. 'Maggi' -> 70g and 4-pack): answer for all of them."""
        text = product_text(text)
        if not text:
            return []
        if self.matcher is None:
            return []
        cands = self.matcher.top(text, k=4)
        if not cands or cands[0].score < 0.3:
            return []
        return [c.product_id for c in cands if c.score >= cands[0].score - 0.1][:3]

    def _llm_route(self, text: str) -> tuple[str | None, str | None]:
        prompt = ("Classify a kirana shop owner's question. Reply JSON {\"intent\": one of "
                  f"{list(INTENTS)} or null, \"product\": product name mentioned or null}}.")
        try:
            r = self.llm.chat([{"role": "system", "content": prompt}, {"role": "user", "content": text}],
                              tier="fast", json_mode=True, agent="assistant.router", max_tokens=100)
            data = r.json()
            intent = data.get("intent")
            return (intent if intent in INTENTS else None), data.get("product")
        except Exception:  # noqa: BLE001
            return None, None

    def _compose(self, question: str, intent: str, evidence: dict, lang: str) -> str:
        style = {"hi": "Reply in simple Hindi, in Devanagari script; keep product names as written.",
                 "en": "Reply in simple English."}.get(lang, "Reply in simple English.")
        system = (f"You are Stockora, an assistant for an Indian kirana store owner. {style} "
                  "Use ONLY facts and numbers from TOOL_RESULT; copy numbers exactly; never invent numbers. "
                  "Use ₹ for money. Be brief: at most 5 short lines or bullets. "
                  "If an action is needed (ordering, collecting udhaar), say the owner must approve it in the app.")
        user = f"QUESTION: {question}\nTOOL ({intent}) RESULT:\n{redact(json.dumps(evidence, default=str))[:6000]}"
        r = self.llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                          tier="smart", agent=f"assistant.{intent}", max_tokens=400)
        return r.text

    # ------------------------------------------------------------- tools
    def _run_tool(self, intent: str, product: int | None, text: str, ambiguous: list[int] | None = None) -> dict:
        b = self.brain
        if intent == "stock_lookup":
            ids = [product] if product else (ambiguous or [])
            rows = [r for r in b.stock_summary() if r["product_id"] in ids]
            return {"item": rows[0]} if len(rows) == 1 else {"items": rows}
        if intent == "forecast" and product is None and ambiguous:
            return {"forecasts": [b.forecast_for(p) for p in ambiguous]}
        if intent == "low_stock":
            return {"low_stock": b.stock_summary(low_only=True)[:10]}
        if intent == "forecast":
            return {"forecast": b.forecast_for(product)}
        if intent == "order_plan":
            plan = b.order_plan()
            return {"lines": len(plan.lines),
                    "top_lines": [{"name": l.name, "qty": l.qty, "days_of_stock": l.days_of_stock,
                                   "reason": l.reason} for l in plan.lines[:8]],
                    "festivals": plan.festival_note}
        if intent == "expiry":
            return {"expiring": b.expiry()[:8]}
        if intent == "dead_stock":
            return {"dead_stock": b.dead_stock()[:8]}
        if intent == "lost_sales":
            return b.lost_sales()
        if intent == "udhaar":
            rec = b.repo.receivables()
            return {"total_due": round(sum(r["due"] for r in rec), 2),
                    "customers": [{k: r[k] for k in ("name", "due", "oldest")} for r in rec[:8]]}
        if intent == "documents":
            return {"passages": self.retriever.search(text, k=3)}
        if intent == "briefing":
            return b.morning_briefing()
        return {}

    # ------------------------------------------------------------- offline answers
    def _template(self, intent: str, ev: dict, lang: str) -> str:
        """Deterministic answers in clean English or clean Hindi (Devanagari)."""
        hi = lang == "hi"
        rs = lambda x: f"₹{x:,.0f}"  # noqa: E731
        d = lambda x: "–" if x is None else f"{x:g}"  # noqa: E731

        if intent == "stock_lookup" and "items" in ev:
            if not ev["items"]:
                return "यह उत्पाद आपकी सूची में नहीं मिला।" if hi else "I couldn't find that product in your list."
            return "\n".join(
                (f"• {it['name']}: {it['stock']:.0f} स्टॉक में, लगभग {d(it['days_of_stock'])} दिन चलेगा" if hi else
                 f"• {it['name']}: {it['stock']:.0f} in stock, lasts about {d(it['days_of_stock'])} days")
                for it in ev["items"])
        if intent == "stock_lookup":
            it = ev["item"]
            if hi:
                return (f"{it['name']}: {it['stock']:.0f} स्टॉक में हैं, लगभग {d(it['days_of_stock'])} दिन चलेगा। "
                        f"अगले 7 दिन में लगभग {d(it['forecast_7d'])} बिकने का अनुमान है।")
            return (f"{it['name']}: {it['stock']:.0f} in stock, enough for about {d(it['days_of_stock'])} days. "
                    f"Expected sales over the next 7 days: {d(it['forecast_7d'])}.")
        if intent == "forecast" and "forecasts" in ev:
            return "\n".join(
                (f"• {f['name']}: 7 दिन में {f['next_7_days']}, 14 दिन में {f['next_14_days']}" if hi else
                 f"• {f['name']}: {f['next_7_days']} in 7 days, {f['next_14_days']} in 14 days") for f in ev["forecasts"])
        if intent == "forecast":
            f = ev["forecast"]
            fest = ", ".join(hindi_festival(x) if hi else x for x in f["festivals_in_window"])
            if hi:
                return (f"{f['name']}: अगले 7 दिन में लगभग {d(f['next_7_days'])} और 14 दिन में {d(f['next_14_days'])} "
                        f"बिकने का अनुमान है।" + (f" त्योहार: {fest}।" if fest else ""))
            return (f"{f['name']}: about {d(f['next_7_days'])} expected in the next 7 days and {d(f['next_14_days'])} in 14 days."
                    + (f" Includes {fest}." if fest else ""))
        if intent == "low_stock":
            rows = ev["low_stock"]
            if not rows:
                return "अभी कोई सामान खत्म होने वाला नहीं है।" if hi else "Nothing is running low right now."
            items = "\n".join((f"• {r['name']}: {r['stock']:.0f} बचे, {d(r['days_of_stock'])} दिन" if hi else
                               f"• {r['name']}: {r['stock']:.0f} left, {d(r['days_of_stock'])} days") for r in rows[:6])
            return ("ये सामान जल्दी खत्म होगा:\n" if hi else "These items will run out soon:\n") + items
        if intent == "order_plan":
            if not ev["top_lines"]:
                return "अभी कुछ मंगाने की ज़रूरत नहीं है।" if hi else "Nothing needs restocking right now."
            lines = "\n".join(f"• {l['name']}: {l['qty']:.0f}" for l in ev["top_lines"][:6])
            head = (f"{ev['lines']} सामान मंगाने हैं। रीस्टॉक पेज पर मंज़ूरी दें:\n" if hi
                    else f"{ev['lines']} items to restock. Approve them on the Restock page:\n")
            return head + lines
        if intent == "expiry":
            rows = ev["expiring"]
            if not rows:
                return "कोई बैच एक्सपायरी के खतरे में नहीं है।" if hi else "No batch is at risk of expiring."
            def when(n: int) -> str:
                if n <= 0:
                    return "पहले ही एक्सपायर हो चुके हैं" if hi else "have already expired"
                return f"{n} दिन में एक्सपायर हो सकते हैं" if hi else f"may expire in {n} day{'' if n == 1 else 's'}"
            return "\n".join(
                (f"• {r['name']}: {r['leftover']:.0f} यूनिट {when(r['days_left'])}" if hi else
                 f"• {r['name']}: {r['leftover']:.0f} units {when(r['days_left'])}. {r['action']}") for r in rows[:5])
        if intent == "dead_stock":
            rows = ev["dead_stock"]
            if not rows:
                return "हर सामान पिछले 30 दिन में बिका है।" if hi else "Every item sold in the last 30 days."
            return "\n".join((f"• {r['name']}: {r['qty']:.0f} यूनिट, {r['idle_days']}+ दिन से नहीं बिका" if hi else
                              f"• {r['name']}: {r['qty']:.0f} units, not sold in {r['idle_days']}+ days") for r in rows[:5])
        if intent == "lost_sales":
            if not ev["top_items"]:
                return "हाल में कोई सामान खत्म नहीं हुआ।" if hi else "No item ran out recently."
            top = ", ".join(f"{t['name']} ({t['days_out']})" for t in ev["top_items"][:3])
            if hi:
                return (f"पिछले {ev['period_days']} दिन में {ev['items_affected']} सामान कुल {ev['stockout_days']} दिन खत्म रहे; "
                        f"लगभग {ev['lost_units']:.0f} यूनिट की मांग पूरी नहीं हुई। सबसे ज़्यादा: {top}।")
            return (f"In the last {ev['period_days']} days, {ev['items_affected']} items were out of stock for "
                    f"{ev['stockout_days']} days in total; about {ev['lost_units']:.0f} units of demand went unserved. "
                    f"Most often: {top}.")
        if intent == "udhaar":
            def since(iso: str) -> str:
                try:
                    day = datetime.fromisoformat(iso[:10])
                    if hi:
                        return f"{day.day} {HINDI_MONTHS[day.month - 1]}"
                    return day.strftime("%d %b").lstrip("0")
                except ValueError:
                    return iso
            rows = "\n".join((f"• {c['name']}: {rs(c['due'])} ({since(c['oldest'])} से)" if hi else
                              f"• {c['name']}: {rs(c['due'])} (since {since(c['oldest'])})") for c in ev["customers"][:6])
            return (f"कुल उधार बाकी: {rs(ev['total_due'])}\n" if hi else f"Total udhaar due: {rs(ev['total_due'])}\n") + rows
        if intent == "documents":
            ps = ev["passages"]
            if not ps:
                return "इस बारे में कोई दस्तावेज़ नहीं मिला।" if hi else "No document covers this."
            return "\n".join(f"• {p_['text']} ({p_['title']})" for p_ in ps[:2])
        if intent == "briefing":
            low = ", ".join(r["name"] for r in ev["low_stock"][:3]) or "–"
            exp = ", ".join(r["name"] for r in ev["expiring"][:3]) or "–"
            if hi:
                return (f"कल {ev['yesterday_units']:.0f} यूनिट बिके। जल्दी खत्म होने वाले: {low}। एक्सपायरी का खतरा: {exp}। "
                        f"{ev['reorder_items']} सामान मंगाने हैं।")
            return (f"Yesterday {ev['yesterday_units']:.0f} units sold. Running low: {low}. Expiry risk: {exp}. "
                    f"{ev['reorder_items']} items to restock. {ev['festivals']}").strip()
        return self._help(lang)

    @staticmethod
    def _help(lang: str) -> str:
        if lang == "hi":
            return ("आप पूछ सकते हैं: 'कौन सा सामान खत्म होने वाला है?', 'मैगी कितनी बची है?', "
                    "'इस हफ्ते क्या मंगाना है?', 'क्या एक्सपायर होने वाला है?', 'कुल उधार कितना है?'")
        return ("Try: 'What will run out this week?', 'How many Maggi are left?', 'What should I restock?', "
                "'What is about to expire?', 'How much udhaar is due?'")

    # ------------------------------------------------------------- agent mode (LLM + tools)
    def _store_line(self) -> str:
        st = self.brain.repo.store()
        return f"{st['name']}, {st['city']}" if st["city"] else st["name"]

    def _agent(self, question: str, lang: str, history: list[dict]) -> Reply | None:
        """LLM picks tools (several if needed), reads their results, answers. None -> caller falls back."""
        language = ("Reply only in Hindi, in Devanagari script, with digits written as 0-9. "
                    "Keep product, brand, customer and distributor names and batch numbers exactly as written in "
                    "the tool results. Every other word must be Hindi: write dates as the day and the Hindi month "
                    "(for example 8 नवंबर, never 08 Nov or 2026-11-08), money as ₹ followed by the amount, and never "
                    "use English words such as expiry, batch, stock, restock or Rs."
                    if lang == "hi" else
                    "Reply in clear, simple English. Write dates like 8 Nov and money as ₹ followed by the amount.")
        system = (f"You are Stockora, the stock assistant for {self._store_line()}, an Indian kirana store. "
                  f"Today is {self.brain.today:%A %d %B %Y}. {language} "
                  "Always call tools to get facts before answering questions about the shop; call more than one "
                  "tool when the question needs it. Use ONLY numbers that appear in tool results, copied exactly. "
                  "Start with a one-line answer, then at most 6 short bullet points starting with '•'. "
                  "Never say an order was placed or money was sent: the owner approves restocking on the Restock page. "
                  "You help with everything about running this shop: stock, sales forecasts, festival and seasonal "
                  "demand, expiry, what to order, items that ran out, items not selling, udhaar, and the distributors' "
                  "schemes, offers, minimum order values, delivery days and return policies (use the documents tool "
                  "for those). Only if a question has nothing to do with the shop (general knowledge, news, sports, "
                  "weather, writing poems), say briefly that you can only help with the shop.")
        msgs: list[dict] = [{"role": "system", "content": system}]
        for h in history[-6:]:
            if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
                msgs.append({"role": h["role"], "content": h["content"][:1500]})
        msgs.append({"role": "user", "content": question})
        evidence: dict = {}
        used: list[str] = []
        for _ in range(4):
            r = self.llm.chat(msgs, tools=AGENT_TOOLS, tier="smart", agent="assistant.agent", max_tokens=600)
            if not r.tool_calls:
                answer = (r.text or "").strip()
                if not answer:
                    return None
                known = f"{json.dumps(evidence, default=str)} {question} {self.brain.today.isoformat()} {self.brain.today:%d %Y}"
                ok, missing = numbers_grounded(answer, known)
                if not ok:
                    return Reply("", used[0] if used else "chat", detect_language(question), evidence, False, True,
                                 flags=[f"ungrounded_numbers:{','.join(missing[:5])}"])
                return Reply(answer, used[0] if used else "chat", detect_language(question), evidence, True, True,
                             flags=[f"tools:{','.join(used)}"] if used else [])
            msgs.append({"role": "assistant", "content": r.text or "", "tool_calls": r.tool_calls})
            for call in r.tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self._agent_tool(name, args, lang)
                used.append(name)
                evidence[f"{name}#{len(used)}"] = result
                msgs.append({"role": "tool", "tool_call_id": call.get("id", name),
                             "content": redact(json.dumps(result, default=str))[:6000]})
        return None

    def _agent_tool(self, name: str, args: dict, lang: str = "en") -> dict:
        if name not in AGENT_TOOL_INTENTS or not tool_allowed("assistant", AGENT_TOOL_INTENTS[name]):
            return {"error": f"unknown tool {name}"}
        intent = AGENT_TOOL_INTENTS[name]
        product, ambiguous = None, []
        if intent in PRODUCT_INTENTS:
            q = str(args.get("product", ""))
            product = self._find_product(q)
            ambiguous = self._ambiguous(q) if product is None else []
            if product is None and not ambiguous:
                return {"error": f"no product matches '{q}'"}
        try:
            result = self._run_tool(intent, product, str(args.get("query", "")), ambiguous)
            return localize_hi(result) if lang == "hi" else result
        except Exception as e:  # noqa: BLE001 - a broken tool must not break the chat
            return {"error": str(e)[:200]}


_P = {"type": "object", "properties": {"product": {"type": "string", "description": "Product name as the owner said it"}},
      "required": ["product"]}
_NONE = {"type": "object", "properties": {}}


def _tool(name: str, desc: str, params: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


AGENT_TOOLS = [
    _tool("stock_lookup", "Stock on hand, days it lasts and 7-day forecast for a product.", _P),
    _tool("forecast", "Daily sales forecast for the next 14 days for a product, with festivals.", _P),
    _tool("low_stock", "Products that will run out soonest.", _NONE),
    _tool("expiry", "Batches that will not sell before their expiry date, with suggested actions.", _NONE),
    _tool("restock_plan", "What to order or buy today and how many of each, including extra stock for upcoming "
                          "festivals. Use for any question about ordering, buying or restocking.", _NONE),
    _tool("stockouts", "Items that ran out in the last 30 days: how many days each was unavailable and how much "
                       "demand was missed.", _NONE),
    _tool("not_selling", "Items with no sales in 30 days.", _NONE),
    _tool("udhaar", "Customer credit (udhaar) due.", _NONE),
    _tool("documents", "Search the distributors' circulars: schemes and offers on products, minimum order value, "
                       "delivery days, and return or replacement policies.",
          {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
    _tool("briefing", "General summary of the shop today: units sold yesterday, items running low, items expiring and "
                      "the number of items to restock. For what exactly to order, use restock_plan.", _NONE),
]
AGENT_TOOL_INTENTS = {"stock_lookup": "stock_lookup", "forecast": "forecast", "low_stock": "low_stock",
                      "expiry": "expiry", "restock_plan": "order_plan", "stockouts": "lost_sales",
                      "not_selling": "dead_stock", "udhaar": "udhaar", "documents": "documents", "briefing": "briefing"}
