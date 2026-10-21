"""Long documents: the seam for map/reduce (D2) and the heads-up rules (D3).

D1 answers a question from the best passages (:mod:`.answer`) and a
summary of a document that fits one pass. Two things are left for later
milestones, and meet the code here:

* **Map/reduce (D2).** :func:`map_chunks` already groups a document's
  parts into the pieces a map step reads: about :data:`MAP_CHUNK_TOKENS`
  tokens each, whatever the model's window (D0: a CPU box gets slower per
  token with a bigger prompt, so CPU document jobs keep about 3.5K-token
  parts). :func:`summarize_long` is where the map pass, progress, stop and
  resume go; until then a summary of a long document answers with
  :func:`domovoi.doc_qa.answer.summary_too_long_text`, and no model call.
* **"Search the whole document" (D2)** after "I couldn't find that" is the
  same map pass with a yes/no question per chunk; the reply offers no
  such control until it exists (a control that can't do what it says is
  hidden, not faked).
* **The heads-up (D3).** :func:`heads_up` is asked before a reply is
  written; it returns no card in D1. The weak-spots table and its rules
  plug in here.
"""

from __future__ import annotations

from typing import Any, Sequence

#: About 3.5K tokens of document per map step (D0 results, change 4).
MAP_CHUNK_TOKENS = 3500
#: Characters per token for sizing chunks: llama3.2 measured about 4.75 on
#: the contract (D0); 4 keeps a margin for denser text.
CHARS_PER_TOKEN = 4.0


def map_chunks(parts: Sequence[dict[str, Any]], tokens: int = MAP_CHUNK_TOKENS) -> list[list[int]]:
    """Part indexes grouped in order into chunks of at most ``tokens``
    (a part bigger than that is a chunk of its own)."""
    limit = tokens * CHARS_PER_TOKEN
    out: list[list[int]] = []
    cur: list[int] = []
    size = 0.0
    for i, p in enumerate(parts):
        n = len(str(p.get("text") or "")) + len(str(p.get("head") or ""))
        if cur and size + n > limit:
            out.append(cur)
            cur, size = [], 0.0
        cur.append(i)
        size += n
    if cur:
        out.append(cur)
    return out


class NotYet(Exception):
    """A long-document job that arrives with D2."""


async def summarize_long(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover — D2
    raise NotYet("map/reduce summaries arrive with D2")


def heads_up(kind: str, docs: Sequence[dict[str, Any]], question: str) -> dict[str, Any] | None:
    """The heads-up card for this request, or None. D3 fills this in from
    the measured weak-spots table; D1 shows none."""
    return None
