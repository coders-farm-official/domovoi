"""Routing eval — run a fixed utterance corpus through the intent router.

The tool router is model-driven, so a plain unit test can't tell you
whether "who wrote the odyssey" still lands on the calculator on the box
you actually run. This script does, against either of two targets:

  --core URL     End-to-end: POST every utterance at <URL>/v1/intent and
                 compare matched_handler / matched_path. This is the real
                 router on a running core (its registry, its plugins, its
                 tool model). It writes intents_log / conversation_log
                 rows on that core, so use a bench room id, and only send
                 read-shaped utterances at a production box.

  --ollama URL   Router-only, no core and no database: replays the
                 router's step 1 (fast-path dry run over the core
                 registry) and step 2 (RealOllamaClient.route with THIS
                 checkout's routing prompt, tool-offer gate and handler
                 schemas) against an Ollama server. Use it to A/B a prompt
                 or description edit before deploying, or to compare tool
                 models with --model. Needs the `ollama` package (the
                 real-clients extra). Core handlers only — installed
                 plugins' tools are not offered here.

Usage:
    python scripts/eval_routing.py --core http://192.168.0.117:6370 --room bench
    python scripts/eval_routing.py --ollama http://localhost:11434 --model qwen3:8b
    python scripts/eval_routing.py --ollama http://localhost:11434 --only qa --only double_check
    python scripts/eval_routing.py --list

Exit status is 1 when any case misroutes, so it can gate a deploy. Pass
--jsonl PATH to append one result line per case (target, model, utterance,
expected, got, latency) so a run's evidence survives.

The corpus lives in scripts/routing_corpus.json; the same file backs the
DB-free assertions in domovoi/tests/test_routing_corpus.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_CORPUS = HERE / "routing_corpus.json"

# Keep every client import stub-safe: this script never touches the core's
# Whisper/TTS clients, and the Ollama client it needs is built by hand.
os.environ.setdefault("USE_STUBS", "true")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass
class Case:
    utterance: str
    handler: str | None
    path: str | None
    tags: list[str]
    note: str


@dataclass
class Result:
    utterance: str
    expected_handler: str | None
    expected_path: str | None
    got_handler: str | None
    got_path: str | None
    latency_ms: int
    ok: bool
    detail: str = ""


def load_corpus(path: Path) -> list[Case]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Case(
            utterance=c["utterance"],
            handler=c.get("handler"),
            path=c.get("path"),
            tags=list(c.get("tags") or []),
            note=c.get("note", ""),
        )
        for c in data["cases"]
    ]


def judge(case: Case, handler: str | None, path: str | None) -> bool:
    if handler != case.handler:
        return False
    if case.path is not None and path != case.path:
        return False
    return True


# ─── Target: a running core ───────────────────────────────────────────


def run_core(cases: list[Case], base_url: str, room: str, timeout: float) -> list[Result]:
    results: list[Result] = []
    for case in cases:
        body = json.dumps({"transcript": case.utterance, "room_id": room}).encode()
        req = urllib.request.Request(
            base_url.rstrip("/") + "/v1/intent",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            results.append(
                Result(
                    case.utterance, case.handler, case.path, None, None,
                    int((time.monotonic() - t0) * 1000), False, f"request failed: {e}",
                )
            )
            continue
        latency = int((time.monotonic() - t0) * 1000)
        handler = payload.get("matched_handler") or None
        path = payload.get("matched_path") or None
        results.append(
            Result(
                case.utterance, case.handler, case.path, handler, path, latency,
                judge(case, handler, path), (payload.get("text") or "")[:80],
            )
        )
    return results


# ─── Target: Ollama directly (router-only) ────────────────────────────


async def run_ollama(
    cases: list[Case], base_url: str, model: str | None
) -> list[Result]:
    from domovoi.clients.ollama import RealOllamaClient
    from domovoi.config import settings
    from domovoi.handlers import HANDLERS
    from domovoi.handlers.base import as_fast_path
    from domovoi.router import _LEADING_FILLER_RE, offered_tool_schemas

    tool_model = model or settings.ollama_tool_model
    client = RealOllamaClient(
        url=base_url, qa_model=settings.ollama_model, tool_model=tool_model
    )

    def fast_path_winner(normalized: str) -> str | None:
        for h in HANDLERS:
            for entry in h.fast_paths:
                if as_fast_path(entry).pattern.match(normalized):
                    return h.name
        return None

    results: list[Result] = []
    for case in cases:
        # Mirror router.route(): lowercase, strip terminal punctuation,
        # strip leading filler, then fast paths before the LLM.
        normalized = case.utterance.lower().strip().rstrip(".,!?")
        normalized = _LEADING_FILLER_RE.sub("", normalized)
        t0 = time.monotonic()
        winner = fast_path_winner(normalized)
        if winner is not None:
            handler, path, detail = winner, "fast", "fast path"
        else:
            schemas = offered_tool_schemas(normalized)
            withheld = sorted(
                {h.name for h in HANDLERS} - {s["name"] for s in schemas}
            )
            call = await client.route(case.utterance, schemas)
            if call is None:
                handler, path = None, "qa"
            else:
                handler, path = call.get("handler"), "llm"
            detail = f"offered {len(schemas)} tools"
            if withheld:
                detail += f", withheld {','.join(withheld)}"
            if call is not None and call.get("args"):
                detail += f"; args={json.dumps(call['args'])[:60]}"
        latency = int((time.monotonic() - t0) * 1000)
        results.append(
            Result(
                case.utterance, case.handler, case.path, handler, path, latency,
                judge(case, handler, path), detail,
            )
        )
    return results


# ─── Reporting ────────────────────────────────────────────────────────


def _fmt(handler: str | None, path: str | None) -> str:
    return f"{handler or '-'}/{path or '-'}"


def report(results: list[Result], cases: list[Case]) -> int:
    notes = {c.utterance: c.note for c in cases}
    failures = 0
    for r in results:
        mark = "ok  " if r.ok else "FAIL"
        want = _fmt(r.expected_handler, r.expected_path or "*")
        got = _fmt(r.got_handler, r.got_path)
        print(f"{mark} {r.latency_ms:6d}ms  {r.utterance!r:55} want {want:22} got {got:22} {r.detail}")
        if not r.ok:
            failures += 1
            if notes.get(r.utterance):
                print(f"           note: {notes[r.utterance]}")
    print()
    print(f"{len(results) - failures}/{len(results)} routed as expected"
          + (f", {failures} misrouted" if failures else ""))
    return 1 if failures else 0


def append_jsonl(path: Path, results: list[Result], target: str, model: str | None) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for r in results:
            row = {"ts": stamp, "target": target, "model": model, **asdict(r)}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = p.add_mutually_exclusive_group()
    target.add_argument("--core", metavar="URL", help="running core, e.g. http://192.168.0.117:6370")
    target.add_argument("--ollama", metavar="URL", help="Ollama server, e.g. http://localhost:11434")
    p.add_argument("--model", help="tool model for --ollama (default: settings.ollama_tool_model)")
    p.add_argument("--room", default="eval", help="room_id for --core turns (default: eval)")
    p.add_argument("--only", action="append", default=[], metavar="TAG", help="run only cases with this tag (repeatable)")
    p.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    p.add_argument("--timeout", type=float, default=120.0, help="per-request timeout for --core, seconds")
    p.add_argument("--jsonl", type=Path, help="append one result row per case to this file")
    p.add_argument("--list", action="store_true", help="print the corpus and exit")
    args = p.parse_args(argv)

    cases = load_corpus(args.corpus)
    if args.only:
        cases = [c for c in cases if set(c.tags) & set(args.only)]
    if args.list:
        for c in cases:
            print(f"{_fmt(c.handler, c.path or '*'):22} {c.utterance!r:55} [{', '.join(c.tags)}] {c.note}")
        return 0
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2
    if not (args.core or args.ollama):
        p.error("pick a target: --core URL or --ollama URL (or --list)")

    if args.core:
        print(f"target: core {args.core} (room {args.room}), {len(cases)} cases")
        results = run_core(cases, args.core, args.room, args.timeout)
        target_name, model = args.core, None
    else:
        results = asyncio.run(run_ollama(cases, args.ollama, args.model))
        target_name, model = args.ollama, args.model
        print(f"target: ollama {args.ollama} model {model or '(settings default)'}, {len(cases)} cases")

    if args.jsonl:
        append_jsonl(args.jsonl, results, target_name, model)
    return report(results, cases)


if __name__ == "__main__":
    sys.exit(main())
