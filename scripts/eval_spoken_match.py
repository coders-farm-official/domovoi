"""Spoken-match gate — measure the spoken-name resolver on the frozen,
held-out request set it was never fitted to.

The gate data (household data — never commit it) lives OUTSIDE the repo,
in a directory with three files and a manifest of their sha256 hashes:

  audit_set_v2.json   the requests: 97 stylized ("play suicide boys" for
                      $uicideboy$), 45 ordinary and 195 not-in-library,
                      each with the library ids a right answer plays
  audit_tr.jsonl      Whisper transcripts of each request, 3 TTS voices
  library.json        the library snapshot the expected ids refer to
  MANIFEST.sha256     the three hashes

Each transcript is parsed the way production parses it
(``router.normalize_transcript`` → ``strip_leading_filler`` → the first
fast path over the REAL handler registry; only music's "play X by Y" and
"play X" build a request, anything else counts as "not routed"), resolved
against an index of library.json with the thresholds from the Settings
DEFAULTS (not the live settings), and scored: a play is right when at
least half of the rows it plays are expected; an ask is right when the
first entity it offers is.

NEVER TUNE ON THIS. Run it once when the code is done; again only after a
fix justified WITHOUT looking at gate items (a crash, a rule applied as
designed, a bug a unit test found) — and say so in the commit.
No constant, rule, alias, stop word or fixture may be derived from it. It
prints totals only; ``--dump-decisions`` writes per-item results for an
independent audit, and whoever works on the resolver does not open that file.

Every group also says how it did on the requests production ROUTED to
music ("of routed"): a transcript whose "play" Whisper misheard ("Place …",
"PlayTech 9") never reaches the resolver, and that is the parser's loss,
not the ranker's.

``--mode production`` adds today's search (the cascade) where production
runs it, and reports it two ways: with the did-you-mean dialog ON (a
spoken turn: an ask is asked, and whatever the answer, that turn never
goes on to today's search — only "none" does) and OFF (the dashboard's
play box, or the setting off: an ask goes on to today's search like a
"none").

Usage:
    python scripts/eval_spoken_match.py --gate-dir <dir>          # the gate
    python scripts/eval_spoken_match.py --source say               # perfect text
    python scripts/eval_spoken_match.py --mode production          # + today's search as fallback
    python scripts/eval_spoken_match.py --no-ask --voices lessac
    python scripts/eval_spoken_match.py --check-names domovoi/tests/fixtures/spoken_match_library.json

``--gate-dir`` defaults to $DOMOVOI_SPOKEN_GATE_DIR. A manifest mismatch
prints "NOT THE GATE" and exits 2 (``--allow-modified`` runs anyway and
labels the output). ``--check-names FILE`` (a JSON file — every string in
it — or a text file, one name per line) prints which of those names occur
in the gate, so no fixture or test example is written from a gate item.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
os.environ.setdefault("USE_STUBS", "true")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_ENV = "DOMOVOI_SPOKEN_GATE_DIR"
GATE_FILES = ("audit_set_v2.json", "audit_tr.jsonl", "library.json")
TARGET_GROUPS = ("stylized", "ordinary")
#: Absent requests by how they were drawn.
ABSENT_SUBGROUPS = {
    "absent-popular": "absent: popular",
    "absent-near": "absent: near-miss",
    "absent-song": "absent: songs",
    "absent-song-by-libartist": "absent: songs",
}


# ─── Data ──────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest(gate_dir: Path) -> tuple[bool, str, list[str]]:
    """(every file matches the manifest, the manifest's own hash, problems)."""
    manifest = gate_dir / "MANIFEST.sha256"
    if not manifest.is_file():
        return False, "", [f"no {manifest.name}"]
    expected: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition(" ")
        expected[name.strip().lstrip("*")] = digest.strip().lower()
    problems = []
    for name in GATE_FILES:
        path = gate_dir / name
        if name not in expected:
            problems.append(f"{name}: not in the manifest")
        elif not path.is_file():
            problems.append(f"{name}: missing")
        elif _sha256(path) != expected[name]:
            problems.append(f"{name}: hash differs")
    return not problems, _sha256(manifest), problems


def load_gate(gate_dir: Path) -> tuple[list[dict], list[dict], list[dict]]:
    items = json.loads((gate_dir / "audit_set_v2.json").read_text(encoding="utf-8"))
    transcripts = [
        json.loads(line)
        for line in (gate_dir / "audit_tr.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    library = json.loads((gate_dir / "library.json").read_text(encoding="utf-8"))
    return items, transcripts, library


def _git_sha() -> str:
    try:
        sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return (sha or "unknown") + ("-dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "unknown"


# ─── Parsing, as production does ──────────────────────────────────────────


def parse_request(raw: str) -> dict[str, str] | None:
    """The query MusicHandler would build from a transcript, or None when
    production would not route it to music's "play X [by Y]"."""
    from domovoi.handlers import music
    from domovoi.router import first_fast_path, normalize_transcript, strip_leading_filler

    t = strip_leading_filler(normalize_transcript(raw))
    hit = first_fast_path(t)
    if hit is None:
        return None
    handler, fp, m = hit
    if handler.name != "music":
        return None
    if fp.pattern is music._PLAY_ARTIST_RE:
        return {
            "title": music._clean_capture(m.group(1)),
            "artist": music._clean_capture(m.group(2)),
        }
    if fp.pattern is music._PLAY_ANY_RE:
        return {"any": music._clean_capture(m.group(1))}
    return None


class TodaysSearch:
    """A simulation of today's cascade on the library: MPD's
    case-insensitive substring tag search (title AND artist, or any of
    artist / album / title), first hit in path order, then the filename
    search with every word ANDed (outside a bracketed video id, as
    ``RealMPDClient`` checks its filename hits)."""

    def __init__(self, tracks: list[dict]) -> None:
        self.order = sorted(tracks, key=lambda t: t.get("file_path") or "")

    def play(self, query: dict[str, str]) -> int | None:
        cf = lambda s: (s or "").casefold()  # noqa: E731
        q = {k: cf(v) for k, v in query.items() if v}
        if not q:
            return None
        if "any" in q:
            hit = next(
                (t for t in self.order
                 if any(q["any"] in cf(t.get(f)) for f in ("artist", "album", "title"))),
                None,
            )
        else:
            hit = next(
                (t for t in self.order
                 if all(v in cf(t.get(k)) for k, v in q.items())),
                None,
            )
        if hit is None:
            # The filename leg, as the real client checks its hits: every
            # word in the file name, not inside a bracketed video id.
            from domovoi.clients.mpd import filename_hit_matches

            words = tuple(q.values())
            hit = next(
                (t for t in self.order if filename_hit_matches(t.get("file_path") or "", words)),
                None,
            )
        return int(hit["id"]) if hit else None


# ─── The run ───────────────────────────────────────────────────────────────


def _defaults() -> tuple[float, float]:
    from domovoi.config import Settings

    fields = Settings.model_fields
    return (
        float(fields["music_match_play_threshold"].default),
        float(fields["music_match_ask_threshold"].default),
    )


def _load_aliases(path: str | None) -> list[dict]:
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("declared_not_from_gate") is not True:
        raise SystemExit(
            "--aliases: the file must be an object with "
            '"declared_not_from_gate": true and "aliases": [...] — an alias '
            "file written from gate items makes the run meaningless"
        )
    from domovoi.handlers.shared.spoken_names import alias_key

    rows = []
    for n, a in enumerate(data.get("aliases") or [], start=1):
        row = dict(a)
        row.setdefault("id", n)
        row.setdefault("source", "manual")
        if row.get("target_type") != "track" and not row.get("target_key"):
            row["target_key"] = alias_key(row.get("target_name") or "")
        rows.append(row)
    return rows


def _played_rows(candidate) -> tuple[int, ...]:
    ids = candidate.track_ids
    if candidate.ref.type in ("title", "track"):
        return ids[:1]
    return ids


def _right(candidate, gt: set[int]) -> bool:
    rows = _played_rows(candidate)
    return bool(rows) and sum(1 for r in rows if r in gt) * 2 >= len(rows)


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else float("nan")


def run_gate(
    gate_dir: str | Path | None = None,
    *,
    source: str = "core",
    mode: str = "resolver",
    ask: bool = True,
    voices: Iterable[str] | None = None,
    aliases: str | None = None,
    allow_modified: bool = False,
    dump_decisions: str | None = None,
) -> dict[str, Any]:
    """Run the gate in-process and return its aggregates (see
    :func:`format_table`). Raises SystemExit(2) on a manifest mismatch
    unless ``allow_modified``."""
    from domovoi.handlers.shared.library_match import build_index, resolve

    gate = Path(gate_dir or os.environ.get(GATE_ENV) or "")
    if not str(gate_dir or os.environ.get(GATE_ENV) or ""):
        raise SystemExit(f"no gate dir: pass --gate-dir or set {GATE_ENV}")
    ok, manifest_hash, problems = verify_manifest(gate)
    if not ok and not allow_modified:
        print("NOT THE GATE: " + "; ".join(problems), file=sys.stderr)
        raise SystemExit(2)
    items, transcripts, library = load_gate(gate)
    by_qid = {it["qid"]: it for it in items}
    voice_filter = [v.strip() for v in (voices or []) if v and v.strip()]

    work: list[tuple[dict, str, str]] = []
    if source == "say":
        work = [(it, "text", it["say"]) for it in items]
    else:
        for tr in transcripts:
            it = by_qid.get(tr.get("qid"))
            if it is None:
                continue
            if voice_filter and not any(v in tr.get("voice", "") for v in voice_filter):
                continue
            work.append((it, tr.get("voice", ""), tr.get(source) or ""))

    play_t, ask_t = _defaults()
    t0 = time.perf_counter()
    index = build_index(library, _load_aliases(aliases))
    build_s = time.perf_counter() - t0
    todays = TodaysSearch(library) if mode == "production" else None

    counts: dict[str, Counter] = defaultdict(Counter)
    # production mode, dialog OFF: an ask goes on to today's search.
    counts_off: dict[str, Counter] = defaultdict(Counter)
    latencies: list[float] = []

    def judge(group: str, gt: set[int], res: Any, decision: str, query: dict) -> tuple[str, int | None]:
        """(outcome, today's search's row) for one decision. Only a
        "none" goes on to today's search (production mode)."""
        best = res.best
        fallback = None
        if group == "absent":
            outcome = {"play": "false_play", "ask": "ask", "none": "none"}[decision]
            if todays is not None and decision == "none":
                fallback = todays.play(query)
                if fallback is not None:
                    outcome = "false_play_fallback"
            return outcome, fallback
        if decision == "play":
            return ("right" if _right(best, gt) else "wrong"), None
        if decision == "ask":
            if _right(best, gt):
                return "ask_right", None
            spoken = res.candidates[:2]
            return ("ask_right_second" if any(_right(c, gt) for c in spoken[1:]) else "ask_wrong"), None
        outcome = "miss"
        if todays is not None:
            fallback = todays.play(query)
            if fallback is not None:
                outcome = "right_fallback" if fallback in gt else "wrong_fallback"
        return outcome, fallback
    dump = open(dump_decisions, "w", encoding="utf-8") if dump_decisions else None
    try:
        for it, voice, text in work:
            group = it["group"]
            gt = {int(x) for x in it.get("gt_ids") or []}
            groups = [group]
            if group == "absent":
                groups.append(ABSENT_SUBGROUPS.get(it.get("cat", ""), "absent: other"))
            query = parse_request(text)
            outcome: str
            outcome_off: str
            res = None
            fallback = None
            if query is None:
                outcome = outcome_off = "not_routed"
            else:
                q0 = time.perf_counter()
                res = resolve(index, query, play_threshold=play_t, ask_threshold=ask_t)
                latencies.append((time.perf_counter() - q0) * 1000.0)
                decision = res.decision
                if decision == "ask" and not ask:
                    decision = "none"
                outcome, fallback = judge(group, gt, res, decision, query)
                outcome_off = outcome
                if todays is not None and decision == "ask":
                    outcome_off, _ = judge(group, gt, res, "none", query)
            for g in groups:
                counts[g]["n"] += 1
                counts[g][outcome] += 1
                counts_off[g]["n"] += 1
                counts_off[g][outcome_off] += 1
            if dump is not None:
                dump.write(json.dumps({
                    "qid": it["qid"], "group": group, "cat": it.get("cat"), "voice": voice,
                    "text": text, "query": query, "outcome": outcome,
                    "decision": res.decision if res else None,
                    "reason": res.reason if res else None,
                    "heard": res.heard if res else None,
                    "candidates": [
                        {"type": c.ref.type, "label": c.ref.label, "artist": c.ref.artist_label,
                         "score": round(c.score, 4), "via": c.via, "rows": list(_played_rows(c))[:5]}
                        for c in (res.candidates if res else ())
                    ],
                    "fallback_id": fallback,
                }, ensure_ascii=False) + "\n")
    finally:
        if dump is not None:
            dump.close()

    def summarize(table: dict[str, Counter]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for g, c in table.items():
            n = c["n"]
            routed = n - c["not_routed"]
            if g.startswith("absent"):
                fp = c["false_play"] + c["false_play_fallback"]
                out[g] = {
                    "n": n,
                    "routed": routed,
                    "false_play": _pct(fp, n),
                    "false_play_routed": _pct(fp, routed),
                    "false_play_resolver": _pct(c["false_play"], n),
                    "false_play_todays_search": _pct(c["false_play_fallback"], n),
                    "ask": _pct(c["ask"], n),
                    "nothing": _pct(c["none"], n),
                    "not_routed": _pct(c["not_routed"], n),
                }
            else:
                auto = c["right"] + c["right_fallback"]
                confirm = auto + c["ask_right"]
                out[g] = {
                    "n": n,
                    "routed": routed,
                    "auto_play": _pct(auto, n),
                    "auto_play_routed": _pct(auto, routed),
                    "with_confirm": _pct(confirm, n),
                    "with_confirm_routed": _pct(confirm, routed),
                    "right_among_spoken": _pct(confirm + c["ask_right_second"], n),
                    "wrong": _pct(c["wrong"] + c["wrong_fallback"], n),
                    "wrong_suggestion": _pct(c["ask_wrong"] + c["ask_right_second"], n),
                    "miss": _pct(c["miss"], n),
                    "not_routed": _pct(c["not_routed"], n),
                    "todays_search_right": _pct(c["right_fallback"], n),
                    "todays_search_wrong": _pct(c["wrong_fallback"], n),
                }
        return out

    groups_out = summarize(counts)
    groups_off = summarize(counts_off) if todays is not None and ask else None
    lat = sorted(latencies)
    stats = index.stats()
    return {
        "meta": {
            "git": _git_sha(),
            "manifest_sha256": manifest_hash,
            "is_gate": ok,
            "problems": problems,
            "source": source,
            "mode": mode,
            "ask": ask,
            "voices": voice_filter or "all",
            "aliases": aliases or None,
            "play_threshold": play_t,
            "ask_threshold": ask_t,
            "transcripts": len(work),
            "evaluated": len(latencies),
            "tracks": stats["tracks"],
            "entities": stats["entities"],
            "forms": stats["forms"],
            "build_s": round(build_s, 2),
            "query_ms_p50": round(statistics.median(lat), 1) if lat else None,
            "query_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
            "query_ms_max": round(lat[-1], 1) if lat else None,
        },
        "groups": groups_out,
        # production mode with the dialog on: the same requests with the
        # dialog OFF (the dashboard's play box) — an ask goes on to today's
        # search. None otherwise.
        "groups_dialog_off": groups_off,
    }


def format_table(result: dict[str, Any]) -> str:
    m = result["meta"]
    out = _format_groups(m, result["groups"], with_head=True)
    off = result.get("groups_dialog_off")
    if off:
        out += (
            "\n\nproduction, did-you-mean OFF (the dashboard's play box, or the "
            "setting off): an ask goes on to today's search\n"
        )
        out += _format_groups(m, off, with_head=False)
    return out


def _format_groups(m: dict[str, Any], g: dict[str, Any], *, with_head: bool) -> str:
    head = (
        f"spoken-match gate{'' if m['is_gate'] else ' (NOT THE GATE: ' + '; '.join(m['problems']) + ')'}"
        f" — git {m['git']} — MANIFEST sha256 {m['manifest_sha256'][:16]}…\n"
        f"source {m['source']}, mode {m['mode']}, ask {'on' if m['ask'] else 'off'}, "
        f"voices {m['voices']}, thresholds play {m['play_threshold']} / ask {m['ask_threshold']}"
        f"{', aliases ' + m['aliases'] if m['aliases'] else ''}\n"
        f"{m['transcripts']} transcripts ({m['evaluated']} reached the resolver); index "
        f"{m['tracks']} tracks / {m['entities']} entities / {m['forms']} forms built in "
        f"{m['build_s']} s; query p50 {m['query_ms_p50']} ms, p95 {m['query_ms_p95']} ms, "
        f"max {m['query_ms_max']} ms\n"
    )
    lines = [head] if with_head else []
    if with_head and m["mode"] == "production":
        lines.append(
            "production, did-you-mean ON (a spoken turn): an ask never goes on to "
            "today's search; only a \"none\" does"
        )
    lines.append(
        f"{'group':<20}{'n':>5}{'auto-play':>11}{'+confirm':>10}{'spoken':>8}"
        f"{'wrong':>8}{'wrong-sugg':>12}{'miss':>7}{'not-routed':>12}"
        f"{'routed: auto':>14}{'+confirm':>10}"
    )
    for name in TARGET_GROUPS:
        r = g.get(name)
        if not r:
            continue
        lines.append(
            f"{name:<20}{r['n']:>5}{r['auto_play']:>10.1f}%{r['with_confirm']:>9.1f}%"
            f"{r['right_among_spoken']:>7.1f}%{r['wrong']:>7.1f}%{r['wrong_suggestion']:>11.1f}%"
            f"{r['miss']:>6.1f}%{r['not_routed']:>11.1f}%"
            f"{r['auto_play_routed']:>13.1f}%{r['with_confirm_routed']:>9.1f}%"
        )
        if m["mode"] == "production":
            lines.append(
                f"{'  of which today':<20}{'':>5}{r['todays_search_right']:>10.1f}%{'':>18}"
                f"{r['todays_search_wrong']:>7.1f}%"
            )
    lines.append("")
    lines.append(
        f"{'group':<20}{'n':>5}{'false-play':>12}{'ask':>8}{'nothing':>10}{'not-routed':>12}"
        f"{'routed: false-play':>20}"
    )
    for name in sorted(k for k in g if k.startswith("absent")):
        r = g[name]
        lines.append(
            f"{name:<20}{r['n']:>5}{r['false_play']:>11.1f}%{r['ask']:>7.1f}%"
            f"{r['nothing']:>9.1f}%{r['not_routed']:>11.1f}%{r['false_play_routed']:>19.1f}%"
        )
        if m["mode"] == "production" and r["false_play_todays_search"]:
            lines.append(f"{'  of which today':<20}{'':>5}{r['false_play_todays_search']:>11.1f}%")
    return "\n".join(lines)


# ─── --check-names ─────────────────────────────────────────────────────────


def _strings(data: Any) -> Iterable[str]:
    if isinstance(data, str):
        yield data
    elif isinstance(data, dict):
        for v in data.values():
            yield from _strings(v)
    elif isinstance(data, (list, tuple)):
        for v in data:
            yield from _strings(v)


def _names_in(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        found = list(_strings(json.loads(raw)))
    else:
        found = [line.strip() for line in raw.splitlines()]
    out: dict[str, None] = {}
    for s in found:
        s = s.strip()
        # Paths and ids are not names: keep a path's last two parts.
        if "/" in s or "\\" in s:
            parts = re.split(r"[\\/]+", s)
            for p in parts[-3:]:
                p = re.sub(r"\.\w{2,4}$", "", p).strip()
                if p:
                    out.setdefault(p, None)
            continue
        if s and not s.isdigit():
            out.setdefault(s, None)
    return list(out)


def check_names(path: str, gate_dir: str | Path | None = None) -> list[tuple[str, Counter]]:
    """[(name, Counter(group → items))] for every name in ``path`` that a
    gate request says, or that names one of a gate request's expected
    library entries. Item texts are never printed."""
    from domovoi.handlers.shared.spoken_names import alias_key, entity_names, spoken_forms

    gate = Path(gate_dir or os.environ.get(GATE_ENV) or "")
    items, _, library = load_gate(gate)
    rows = {int(r["id"]): r for r in library}

    def words(s: str) -> str:
        forms = spoken_forms(s)
        return f" {forms[0]} " if forms else ""

    gate_says: list[tuple[str, str]] = []
    gate_keys: dict[str, Counter] = defaultdict(Counter)
    for it in items:
        group = it["group"]
        gate_says.append((group, words(it.get("say") or "")))
        names: list[str] = list(_strings(it.get("gt")))
        for rid in it.get("gt_ids") or []:
            r = rows.get(int(rid))
            if r:
                names.extend(n for _, n in entity_names(r.get("title"), r.get("artist"), r.get("album")))
        for n in names:
            k = alias_key(n)
            if k:
                gate_keys[k][group] += 1

    hits: list[tuple[str, Counter]] = []
    for name in _names_in(Path(path)):
        k = alias_key(name)
        w = words(name)
        c: Counter = Counter()
        if k and k in gate_keys:
            c.update(gate_keys[k])
        if w.strip():
            for group, say in gate_says:
                if w in say:
                    c[group] += 1
        if c:
            hits.append((name, c))
    return hits


# ─── CLI ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gate-dir", default=os.environ.get(GATE_ENV))
    ap.add_argument("--allow-modified", action="store_true")
    ap.add_argument("--source", choices=("core", "say"), default="core")
    ap.add_argument("--mode", choices=("resolver", "production"), default="resolver")
    ap.add_argument("--no-ask", action="store_true", help="music_choice off: asks fall through")
    ap.add_argument("--voices", default="", help="comma-separated voice name parts (default all)")
    ap.add_argument("--aliases", help='JSON {"declared_not_from_gate": true, "aliases": [...]}')
    ap.add_argument("--json", dest="json_out", help="write the aggregates here")
    ap.add_argument("--dump-decisions", help="per-item results (for an independent audit; never read while building)")
    ap.add_argument("--check-names", help="list which names in this file occur in the gate")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not args.gate_dir:
        print(f"no gate dir: pass --gate-dir or set {GATE_ENV}", file=sys.stderr)
        return 2
    if args.check_names:
        ok, _, problems = verify_manifest(Path(args.gate_dir))
        if not ok:
            print("warning: NOT THE GATE (" + "; ".join(problems) + ")", file=sys.stderr)
        hits = check_names(args.check_names, args.gate_dir)
        names = _names_in(Path(args.check_names))
        for name, c in hits:
            print(f"IN GATE  {name!r}  ({', '.join(f'{g} {n}' for g, n in sorted(c.items()))})")
        print(f"{len(names) - len(hits)} of {len(names)} names clear, {len(hits)} in the gate")
        return 1 if hits else 0

    result = run_gate(
        args.gate_dir, source=args.source, mode=args.mode, ask=not args.no_ask,
        voices=[v for v in args.voices.split(",") if v], aliases=args.aliases,
        allow_modified=args.allow_modified, dump_decisions=args.dump_decisions,
    )
    print(format_table(result))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
