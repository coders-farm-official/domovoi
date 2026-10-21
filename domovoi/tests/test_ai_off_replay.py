"""Replay real utterances through route() with the household's AI
features off: every fixed-phrase command still runs, nothing asks a
language model, and everything else gets the fixed home-commands line.

Two corpora:

* the routing corpus in the repo (``scripts/routing_corpus.json``), every
  case, always;
* recorded voice-test turns (JSON lines with an ``utterance`` and the
  ``handler`` that answered it then), when ``DOMOVOI_AI_OFF_TURNS`` names
  such a file: the maintainers' functional-test harness keeps one outside
  the repo. Only turns a core handler answered are replayed (a plugin's
  handler isn't registered here).

What counts: the turn whose fast path route() takes is answered by that
handler on its fast path (or its offline fallback / a parked
confirmation); a turn no fast path takes — or one its fast path declined —
gets ``ai_features.OFF_LINE`` (or ``OFF_LOOKUP_LINE`` for the weather and
the like); and no turn reaches the Ollama client at all. A model call
raises a BaseException, so no handler's ``except Exception`` can hide it.

Needs the test database (handlers read and write their tables).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest

import domovoi.router as router_mod
from domovoi import ai_features
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db

REPO = Path(__file__).resolve().parents[2]
TURNS_ENV = "DOMOVOI_AI_OFF_TURNS"


class ModelCalled(BaseException):
    pass


class _NoModel:
    calls: list[str] = []

    def __getattr__(self, name):
        type(self).calls.append(name)
        raise ModelCalled(name)


def _core_handler(name: str | None) -> bool:
    h = HANDLER_BY_NAME.get(name or "")
    return h is not None and getattr(h, "plugin_slug", None) is None


async def _replay(utterances: list[str], monkeypatch) -> dict:
    from domovoi.db.session import SessionLocal

    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: _NoModel())
    _NoModel.calls = []
    out = {"fast": Counter(), "off_line": [], "lookup_line": [], "wrong": [], "model_calls": []}
    with ai_features.override(False):
        for said in utterances:
            plan = router_mod.plan_route(said)
            async with SessionLocal() as s:
                try:
                    r = await router_mod.route(
                        Intent(transcript=said, room_id="kitchen"),
                        Context(room_id="kitchen", online=True), s,
                    )
                except ModelCalled as e:
                    out["model_calls"].append((said, str(e)))
                    continue
                finally:
                    await s.rollback()
            if r.text == ai_features.OFF_LINE:
                out["off_line"].append(said)
            elif r.text == ai_features.OFF_LOOKUP_LINE and r.matched_handler in (None, "double_check"):
                out["lookup_line"].append(said)
            elif plan is not None and r.matched_handler == plan.handler.name and r.matched_path in (
                "fast", "fast_offline", "confirmation",
            ):
                out["fast"][r.matched_handler] += 1
            else:
                out["wrong"].append((said, r.matched_handler, r.matched_path, r.text[:80]))
    return out


def _check(result: dict) -> None:
    assert result["model_calls"] == [], result["model_calls"]
    assert _NoModel.calls == []
    assert result["wrong"] == [], result["wrong"]


@requires_db
@pytest.mark.asyncio
async def test_the_routing_corpus_with_ai_off(db_session, monkeypatch) -> None:
    cases = json.loads((REPO / "scripts/routing_corpus.json").read_text(encoding="utf-8"))["cases"]
    said = list(dict.fromkeys(c["utterance"] for c in cases))
    result = await _replay(said, monkeypatch)
    _check(result)
    fast = sum(result["fast"].values())
    # Every case is accounted for: a command on its fast path, or the line.
    assert fast + len(result["off_line"]) + len(result["lookup_line"]) == len(said)
    assert fast > 0 and result["off_line"]


@requires_db
@pytest.mark.asyncio
async def test_recorded_voice_turns_with_ai_off(db_session, monkeypatch) -> None:
    path = os.environ.get(TURNS_ENV)
    if not path:
        pytest.skip(f"{TURNS_ENV} not set (the recorded turns live outside the repo)")
    turns: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        u = (row.get("utterance") or "").strip()
        h = row.get("handler") or row.get("then_handler")
        if u and u not in turns and _core_handler(h):
            turns[u] = h
    result = await _replay(list(turns), monkeypatch)
    report = {
        "turns": len(turns),
        "fast": sum(result["fast"].values()),
        "off_line": len(result["off_line"]),
        "lookup_line": len(result["lookup_line"]),
        "by_handler": dict(result["fast"].most_common()),
        "off_line_utterances": result["off_line"],
        "lookup_line_utterances": result["lookup_line"],
        "wrong": result["wrong"],
        "model_calls": result["model_calls"],
    }
    out = os.environ.get(TURNS_ENV + "_REPORT")
    if out:
        Path(out).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    _check(result)
