"""The 30-question lookup eval (assistant plan part 5, milestone W1).

Each case in ``scripts/lookup_eval.json`` is a question and the passages a
lookup found for it, in the order its sources gave them. The case runs
through the chat's own answer pipeline (``domovoi/lookup_answer.py``):

1. :func:`lookup_answer.rank_passages`: at most 3 passages, best first;
2. :func:`lookup_answer.covers`: below the floor, the honest line with no
   model asked;
3. :func:`lookup_answer.system_prompt`: the passages fenced as data;
4. the model (a fake by default, or a real one with ``--ollama``);
5. :func:`lookup_answer.finish`: links the results didn't give undone, a
   reply that is a lookup line refused, the sources line by code.

The default model is :class:`FakeReader`, deterministic and a little
gullible on purpose: it reads ONLY the first passage (small models lean on
the first one), answers with its best sentence when that sentence holds
the question's names and at least 60% of its words, and otherwise says it couldn't
find it; a passage that carries a "LOOK_UP:" / "READ_PAGE:" line or a link
it repeats as it is. So the score measures the pipeline: the ranking has to
put the right passage first, the floor has to stop the off-topic ones, and
``finish`` has to keep a hijacked reply from reaching the person.

A case passes when its reply holds every ``expect`` phrase and no
``forbid`` one, is the honest line when ``couldnt_find`` says so, and
lists its sources whenever it answered.

Usage::

    python scripts/eval_lookup.py                       # the fake reader
    python scripts/eval_lookup.py --ollama http://127.0.0.1:11500 --model gemma3:4b
    python scripts/eval_lookup.py --json                # one line per case

Never point ``--ollama`` at a household's own model server without asking.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from domovoi import lookup_answer  # noqa: E402

CASES = Path(__file__).with_name("lookup_eval.json")

AskModel = Callable[[list[dict[str, Any]]], Awaitable[str]]


@dataclass
class Result:
    title: str
    url: str | None
    site: str
    snippet: str
    extract: str
    source_name: str
    source_class: str
    licence: str | None

    @property
    def text(self) -> str:
        return self.extract or self.snippet


def results_for(case: dict[str, Any]) -> list[Result]:
    out = []
    for p in case.get("passages") or []:
        site = p.get("site") or ""
        url = f"https://{site}/wiki/{p['title'].replace(' ', '_')}" if site else None
        out.append(Result(title=p["title"], url=url, site=site, snippet="", extract=p["text"],
                          source_name=p.get("source_name") or ("Wikipedia and Wikidata" if "wikipedia" in site
                                                               else "The web"),
                          source_class=p.get("source_class") or ("api" if "wikipedia" in site else "web"),
                          licence=p.get("licence")))
    return out


_BLOCK_RE = re.compile(r"<<(FOUND-[0-9a-f]{12}) BEGIN>>(.*?)<<\1 END>>", re.S)
_PASSAGE_RE = re.compile(r"^\[(\d+)\] (.*?)\n(.*?)(?=^\[\d+\] |\Z)", re.S | re.M)
_SENT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


class FakeReader:
    """A deterministic stand-in for the model (see the module docstring)."""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def __call__(self, messages: list[dict[str, Any]]) -> str:
        self.calls.append(messages)
        system = messages[0]["content"]
        question = [m["content"] for m in messages if m["role"] == "user"][-1]
        m = _BLOCK_RE.search(system)
        if not m:
            return lookup_answer.COULDNT_FIND
        passages = _PASSAGE_RE.findall(m.group(2).strip())
        if not passages:
            return lookup_answer.COULDNT_FIND
        _, header, text = passages[0]
        # Gullible: an instruction line or a link in the text is repeated.
        for line in text.splitlines():
            if re.match(r"^\W*(?:LOOK_UP|READ_PAGE)\s*:", line.strip(), re.I):
                return line.strip()
        names = [w.lower() for w in re.findall(r"\b[A-Z][a-z]+\b", question)[1:]]
        best, best_score = "", 0.0
        for sent in (s.strip() for s in _SENT_RE.split(text) if s.strip()):
            score = lookup_answer.overlap(question, f"{header} {sent}")
            if names and not all(n in f"{header} {sent}".lower() for n in names):
                continue
            if score > best_score:
                best, best_score = sent, score
        if best_score < 0.6:
            return lookup_answer.COULDNT_FIND
        link = re.search(r"\[[^\]]+\]\([^)]+\)|https?://\S+", text)
        return best + (f" More: {link.group(0)}" if link and link.group(0) not in best else "")


def ollama_model(base: str, model: str) -> AskModel:
    import httpx

    async def ask(messages: list[dict[str, Any]]) -> str:
        async with httpx.AsyncClient(timeout=300.0) as c:
            r = await c.post(f"{base.rstrip('/')}/api/chat",
                             json={"model": model, "messages": messages, "stream": False,
                                   "options": {"temperature": 0}})
            r.raise_for_status()
            return (r.json().get("message") or {}).get("content") or ""

    return ask


async def run_case(case: dict[str, Any], ask: AskModel) -> tuple[bool, str, str]:
    """``(passed, why, reply)`` for one case, through the chat's pipeline."""
    question = case["question"]
    results = results_for(case)
    lines = lookup_answer.source_lines(results)
    passages = lookup_answer.rank_passages(question, results)
    if not passages or not lookup_answer.covers(question, passages):
        reply = lookup_answer.fixed_reply(lookup_answer.COULDNT_FIND, lines)
    else:
        messages = [{"role": "system", "content": lookup_answer.system_prompt(question, passages)},
                    {"role": "user", "content": question}]
        reply = lookup_answer.finish(await ask(messages), lines)
    body = reply.split("\n\n---\n", 1)[0]
    low = body.lower()
    for want in case.get("expect") or []:
        if want.lower() not in low:
            return False, f"missing {want!r}", reply
    for bad in case.get("forbid") or []:
        if bad.lower() in low:
            return False, f"holds {bad!r}", reply
    honest = body.strip().startswith(lookup_answer.COULDNT_FIND)
    if case.get("couldnt_find") and not honest:
        return False, "should have said it couldn't find it", reply
    if case.get("expect") and honest:
        return False, "said it couldn't find an answer the passages hold", reply
    if results and "*Sources:*" not in reply:
        return False, "no sources line", reply
    return True, "ok", reply


async def run_all(ask: AskModel, cases: list[dict[str, Any]] | None = None) -> list[tuple[str, bool, str, str]]:
    cases = cases if cases is not None else json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    out = []
    for case in cases:
        ok, why, reply = await run_case(case, ask)
        out.append((case["id"], ok, why, reply))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ollama", help="a model server to ask instead of the fake reader")
    ap.add_argument("--model", default="llama3.2:3b")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    ask: AskModel = ollama_model(args.ollama, args.model) if args.ollama else FakeReader()
    rows = asyncio.run(run_all(ask))
    passed = sum(1 for _, ok, _, _ in rows if ok)
    for cid, ok, why, reply in rows:
        if args.json:
            print(json.dumps({"id": cid, "passed": ok, "why": why, "reply": reply}))
        elif not ok:
            print(f"FAIL {cid}: {why}\n     {reply[:200]!r}")
    by_kind: dict[str, list[bool]] = {}
    for case, (_, ok, _, _) in zip(json.loads(CASES.read_text(encoding="utf-8"))["cases"], rows):
        by_kind.setdefault(case["kind"], []).append(ok)
    kinds = ", ".join(f"{k} {sum(v)}/{len(v)}" for k, v in by_kind.items())
    print(f"lookup eval: {passed}/{len(rows)} ({kinds}) with "
          f"{'model ' + args.model if args.ollama else 'the fake reader'}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
