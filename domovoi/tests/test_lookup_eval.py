"""The 30-question lookup eval gates the answer pipeline (assistant plan
part 5, W1: "an eval of 30 lookup questions (answerable, unanswerable and
misleading) gates it"). Scored here against the fake reader of
``scripts/eval_lookup.py``: deterministic, so the score is the pipeline's
(ranking, the coverage floor, the fence, finish), not a model's mood.
The same cases run against a real model with ``--ollama``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from domovoi import lookup_answer

REPO = Path(__file__).resolve().parents[2]


def _eval():
    spec = importlib.util.spec_from_file_location("eval_lookup", REPO / "scripts" / "eval_lookup.py")
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["eval_lookup"] = mod     # its dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


def _cases():
    return json.loads((REPO / "scripts" / "lookup_eval.json").read_text(encoding="utf-8"))["cases"]


def test_there_are_thirty_cases_of_the_three_kinds() -> None:
    cases = _cases()
    assert len(cases) == 30 and len({c["id"] for c in cases}) == 30
    kinds = {k: sum(1 for c in cases if c["kind"] == k) for k in ("answerable", "unanswerable", "misleading")}
    assert sum(kinds.values()) == 30 and min(kinds.values()) >= 8


def test_the_pipeline_passes_every_case_with_the_fake_reader() -> None:
    ev = _eval()
    rows = asyncio.run(ev.run_all(ev.FakeReader()))
    failed = [(cid, why) for cid, ok, why, _ in rows if not ok]
    assert failed == []


def test_the_eval_catches_a_ranking_that_keeps_the_sources_order(monkeypatch) -> None:
    ev = _eval()
    monkeypatch.setattr(lookup_answer, "rank_passages",
                        lambda q, results, limit=3: [lookup_answer.Passage.from_result(r, q) for r in results][:limit])
    rows = asyncio.run(ev.run_all(ev.FakeReader()))
    assert {"a03", "a06", "m03", "m04"} <= {cid for cid, ok, _, _ in rows if not ok}


def test_the_eval_catches_a_reply_that_carries_a_lookup_line(monkeypatch) -> None:
    ev = _eval()
    monkeypatch.setattr(lookup_answer, "_PROPOSAL_ANYWHERE", __import__("re").compile(r"(?!x)x"))
    monkeypatch.setattr(lookup_answer, "parse_proposal", lambda text: None)
    rows = asyncio.run(ev.run_all(ev.FakeReader()))
    assert {"m06", "m08"} <= {cid for cid, ok, _, _ in rows if not ok}


@pytest.mark.parametrize("cid", ["m05", "m07", "m09"])
def test_injected_links_and_orders_never_reach_the_reply(cid) -> None:
    ev = _eval()
    case = [c for c in _cases() if c["id"] == cid][0]
    ok, why, reply = asyncio.run(ev.run_case(case, ev.FakeReader()))
    assert ok, (why, reply)
