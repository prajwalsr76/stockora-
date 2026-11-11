"""Product entity resolution: messy bill / voice names -> catalog products.

Character n-gram TF-IDF (robust to typos, abbreviations and transliteration)
plus pack-size agreement. Every human correction becomes a new alias, so the
matcher improves per store over time without retraining an LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

_UNIT_FIXES = [
    (r"(\d+(?:\.\d+)?)\s*(?:gms?|grams?|gm\.?)\b", r"\1g"),
    (r"(\d+(?:\.\d+)?)\s*(?:kgs?|kilo)\b", r"\1kg"),
    (r"(\d+(?:\.\d+)?)\s*(?:ltrs?|litres?|liters?|lt)\b", r"\1l"),
    (r"(\d+(?:\.\d+)?)\s*(?:mls?)\b", r"\1ml"),
]
_WORD_FIXES = {
    "pkt": "pack", "pcs": "pack", "bisc": "biscuits", "biscuit": "biscuits", "chips": "",
    "sunflwr": "sunflower", "refnd": "refined", "tel": "oil", "namak": "salt",
    "chini": "sugar", "cheeni": "sugar", "doodh": "milk", "dahi": "dahi", "ghee": "ghee",
    "chai": "tea", "atta": "atta", "maida": "maida", "sabun": "soap",
}
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)(kg|g|ml|l)\b")


def normalize(text: str) -> str:
    t = text.lower().replace("-", " ").replace("&", " and ")
    t = re.sub(r"[^\w\s.]", " ", t)
    for pat, rep in _UNIT_FIXES:
        t = re.sub(pat, rep, t)
    words = [_WORD_FIXES.get(w, w) for w in t.split()]
    return " ".join(w for w in words if w)


def size_in_base(text: str) -> float | None:
    """Pack size in grams or millilitres, e.g. '1kg' -> 1000."""
    m = _SIZE_RE.search(normalize(text))
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2)
    return value * 1000 if unit in ("kg", "l") else value


@dataclass
class Match:
    product_id: int
    alias: str
    score: float


class ProductMatcher:
    """Fit on (product_id, alias) pairs; query with a raw name."""

    AUTO_ACCEPT = 0.62      # top score needed to accept without review
    MIN_MARGIN = 0.08       # top must beat the runner-up (different product) by this

    def __init__(self, pairs: list[tuple[int, str]]):
        if not pairs:
            raise ValueError("matcher needs at least one alias")
        self.ids = [p for p, _ in pairs]
        self.aliases = [a for _, a in pairs]
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
        self.matrix = self.vec.fit_transform([normalize(a) for a in self.aliases])
        self.sizes = [size_in_base(a) for a in self.aliases]

    def top(self, raw: str, k: int = 5) -> list[Match]:
        q = normalize(raw)
        sims = linear_kernel(self.vec.transform([q]), self.matrix).ravel()
        qsize = size_in_base(raw)
        if qsize is not None:
            for i, s in enumerate(self.sizes):
                if s is not None:
                    sims[i] += 0.08 if abs(s - qsize) < 1e-6 else -0.12
        best: dict[int, Match] = {}
        for i in sims.argsort()[::-1]:
            pid = self.ids[i]
            if pid not in best:
                best[pid] = Match(pid, self.aliases[i], float(min(1.0, max(0.0, sims[i]))))
            if len(best) >= k:
                break
        return list(best.values())

    def resolve(self, raw: str) -> tuple[Match | None, list[Match]]:
        """Return (confident match or None, candidates for human review)."""
        cands = self.top(raw)
        if not cands:
            return None, []
        top = cands[0]
        runner = cands[1].score if len(cands) > 1 else 0.0
        if top.score >= self.AUTO_ACCEPT and top.score - runner >= self.MIN_MARGIN:
            return top, cands
        return None, cands
