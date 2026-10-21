"""BM25 over a document's passages (our own; the Kotlin twin is DocBm25.kt).

Okapi BM25 with the usual k1 = 1.2 and b = 0.75, and the "plus one" idf
(``ln(1 + (N - df + 0.5) / (df + 0.5))``) so a word in most passages still
counts a little and never negatively. Built per question: 30 pages are a
few hundred passages and score in milliseconds.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence

from domovoi.doc_qa.text import Passage

K1 = 1.2
B = 0.75


def scores(passages: Sequence[Passage], query_terms: Sequence[str]) -> list[float]:
    """One score per passage, in the passages' order. A passage that shares
    no term with the query scores 0."""
    n = len(passages)
    if n == 0:
        return []
    q = list(dict.fromkeys(t for t in query_terms if t))
    if not q:
        return [0.0] * n
    lengths = [len(p.terms) for p in passages]
    avg = (sum(lengths) / n) or 1.0
    df: Counter[str] = Counter()
    tfs: list[Counter[str]] = []
    for p in passages:
        tf = Counter(p.terms)
        tfs.append(tf)
        for t in q:
            if t in tf:
                df[t] += 1
    out: list[float] = []
    for p, tf, ln in zip(passages, tfs, lengths):
        s = 0.0
        for t in q:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1.0 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * ln / avg))
        out.append(round(s, 6))
    return out
