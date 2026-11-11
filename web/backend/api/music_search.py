"""The Music library search: how ``GET /api/music/library?q=`` and the
Artists list match what somebody typed (owner, 2026-11-11).

It used to be one ``ILIKE '%q%'``: "em know" missed "Let Em' Know" and
"ti" missed T.I. Now:

* **normalized** — case, Latin accents, apostrophes, dots and hyphens
  don't matter: "T.I." is ``ti``, "Let Em' Know" ``let em know``, "Jay-Z"
  ``jayz``, "Beyoncé" ``beyonce``. The database keeps every track's
  title, artist and album so normalized in ``library_tracks.search_text``
  (V046, ``library_search_norm()``, trigram-indexed); :func:`search_norm`
  is the same rules in Python for the query side.
* **word-based** — every word of the query must appear somewhere in the
  title, artist or album, in any order, at the start of a word or inside
  one ("em know", "know tiller", "beyon").
* **ranked** — a title that IS the query first, then one that holds it as
  words, then rows whose words match whole (T.I.'s songs before Bryson
  Tiller's for "ti"), then word starts; the requested sort orders rows
  that rank the same.
* **typo-tolerant** — when no row holds every word, the rows whose text
  is most like the query (pg_trgm word similarity, the same trigram
  index), best first: "bryson tiler exchnge".

The query is at most :data:`MAX_TOKENS` words; tokens are ``[a-z0-9]``
only, so nothing in them is a LIKE wildcard.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from anyascii import anyascii

#: Most query words matched (the rest are ignored).
MAX_TOKENS = 8
#: The typo fallback's floor (pg_trgm word_similarity, 0-1).
FUZZY_THRESHOLD = 0.45
#: The fallback only runs on a query at least this long (normalized,
#: spaces included): one or two letters are like everything.
FUZZY_MIN_CHARS = 3

_DROP_RE = re.compile(r"['`´‘’.\-]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def search_norm(text: str | None) -> str:
    """``library_search_norm()`` (V046) in Python: lower-case ASCII words.
    Accents fold through anyascii, which knows more scripts than the
    database's fixed Latin map; for those the two agree."""
    s = anyascii(unicodedata.normalize("NFKC", text or "")).lower()
    s = _DROP_RE.sub("", s)
    return _NON_ALNUM_RE.sub(" ", s).strip()


def search_tokens(text: str | None) -> list[str]:
    """The query's words, normalized, each once, at most :data:`MAX_TOKENS`."""
    out: dict[str, None] = {}
    for w in search_norm(text).split():
        out.setdefault(w, None)
    return list(out)[:MAX_TOKENS]


def token_filter(tokens: list[str], params: dict[str, Any], *, column: str = "search_text") -> str:
    """SQL: every token inside ``column``. Adds ``:st<i>`` params."""
    parts = []
    for i, tok in enumerate(tokens):
        params[f"st{i}"] = tok
        parts.append(f"{column} LIKE '%' || :st{i} || '%'")
    return "(" + " AND ".join(parts) + ")" if parts else "TRUE"


def rank_sql(tokens: list[str], params: dict[str, Any]) -> str:
    """SQL integer: how well a matching track matches (higher first).
    Expects :func:`token_filter`'s ``:st<i>`` params; adds ``:sq``."""
    params["sq"] = " ".join(tokens)
    title = "search_title"
    terms = [
        f"(CASE WHEN {title} = :sq THEN 16"
        f" WHEN (' ' || {title} || ' ') LIKE '% ' || :sq || ' %' THEN 8 ELSE 0 END)"
    ]
    for i in range(len(tokens)):
        terms.append(
            f"(CASE WHEN (' ' || search_text || ' ') LIKE '% ' || :st{i} || ' %' THEN 2"
            f" WHEN (' ' || search_text) LIKE '% ' || :st{i} || '%' THEN 1 ELSE 0 END)"
        )
    return "(" + " + ".join(terms) + ")"


def fuzzy_filter(params: dict[str, Any]) -> str:
    """SQL: the typo fallback's filter (uses the trigram index). Expects
    ``:sq``; the caller sets the threshold with :func:`fuzzy_threshold_sql`
    in the same transaction."""
    return ":sq <% search_text"


def fuzzy_rank_sql() -> str:
    """SQL integer: the fallback's ranking (word similarity, 0-100)."""
    return "(round(word_similarity(:sq, search_text) * 100))::int"


def fuzzy_threshold_sql() -> str:
    return (
        "SELECT set_config('pg_trgm.word_similarity_threshold', "
        f"'{FUZZY_THRESHOLD}', true)"
    )


def text_rank(name_norm: str, tokens: list[str]) -> int | None:
    """:func:`rank_sql` for one name held in memory (the Artists list):
    None when a token is missing from it."""
    if not tokens:
        return 0
    if any(t not in name_norm for t in tokens):
        return None
    q = " ".join(tokens)
    padded = f" {name_norm} "
    score = 16 if name_norm == q else (8 if f" {q} " in padded else 0)
    for t in tokens:
        if f" {t} " in padded:
            score += 2
        elif f" {t}" in padded:
            score += 1
    return score


__all__ = [
    "FUZZY_THRESHOLD",
    "MAX_TOKENS",
    "fuzzy_filter",
    "fuzzy_rank_sql",
    "fuzzy_threshold_sql",
    "rank_sql",
    "search_norm",
    "search_tokens",
    "text_rank",
    "token_filter",
]
