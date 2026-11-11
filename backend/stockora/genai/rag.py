"""Hybrid retrieval over the store's own documents (scheme circulars, price notes, policies).

BM25 (exact terms like "Parle-G", "24 + 2") fused with character n-gram TF-IDF
(typos, Hinglish spellings) using Reciprocal Rank Fusion. Runs locally with no
embedding API; a dense-embedding retriever can be added as a third ranker.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "on", "in", "to", "and", "is", "for", "with", "per", "be", "are", "kya", "hai", "ka",
         "ki", "ke", "se", "me", "mein", "ko"}


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class Chunk:
    doc_id: int
    title: str
    text: str


def chunk_document(doc_id: int, title: str, body: str, max_chars: int = 320) -> list[Chunk]:
    sentences = re.split(r"(?<=[.!?])\s+", body.strip())
    chunks, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) > max_chars:
            chunks.append(Chunk(doc_id, title, cur.strip()))
            cur = ""
        cur += " " + s
    if cur.strip():
        chunks.append(Chunk(doc_id, title, cur.strip()))
    return chunks


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.avgdl = sum(len(d) for d in docs) / max(len(docs), 1)
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in docs]

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for d, tf in zip(self.docs, self.tf):
            s = 0.0
            for t in query:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl))
            out.append(s)
        return out


class HybridRetriever:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        texts = [f"{c.title}. {c.text}" for c in chunks]
        self.bm25 = BM25([tokens(t) for t in texts])
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.matrix = self.vec.fit_transform(texts) if texts else None

    @classmethod
    def from_repo(cls, repo) -> "HybridRetriever":
        chunks = []
        for d in repo.documents():
            chunks += chunk_document(d["id"], d["title"], d["body"])
        return cls(chunks)

    def search(self, query: str, k: int = 3, rrf_k: int = 60) -> list[dict]:
        if not self.chunks:
            return []
        bm = self.bm25.scores(tokens(query))
        dense = linear_kernel(self.vec.transform([query.lower()]), self.matrix).ravel()
        fused: dict[int, float] = {}
        for ranking in (sorted(range(len(bm)), key=lambda i: -bm[i]),
                        sorted(range(len(dense)), key=lambda i: -dense[i])):
            for rank, i in enumerate(ranking):
                fused[i] = fused.get(i, 0.0) + 1.0 / (rrf_k + rank + 1)
        best = sorted(fused, key=lambda i: -fused[i])[:k]
        return [{"title": self.chunks[i].title, "text": self.chunks[i].text, "doc_id": self.chunks[i].doc_id,
                 "score": round(fused[i], 5), "bm25": round(bm[i], 3)} for i in best]
