"""Lyric-search gate — measure "play the song that goes …", "what's the
song that goes …" and lyrics as the last resort of "play …" on the frozen,
held-out request set built from the household's own library (lyrics-build
CONTRACT §17). It was never fitted to: run it once when the code is done.

The gate data holds REAL LYRICS (household data, copyrighted — never
commit it, never open it, never print it). It lives OUTSIDE the repo, in a
directory with:

  library.json     the library snapshot (ids, titles, artists, durations)
  pool.json        ids only: the TARGET songs (in the evaluation library,
                   with lyrics), the ABSENT songs (taken out of it) and the
                   DISTRACTORS (in it, with lyrics)
  raw/<id>.json    LRCLIB's answer for each pool song, as the shipped
                   ``ask_lrclib`` gave it
  gate_set.json    the requests (their phrases ARE lyrics)
  gate_tr.jsonl    Whisper small.en transcripts of each request, 3 voices
  MANIFEST.sha256  the hashes of the above (raw/ as one listing hash)

What it does, in-process with ``USE_STUBS=true``:

1. Loads a ``_test`` database (``DATABASE_URL``; the script refuses any
   other, and ``domovoi_test`` / ``domovoi2_test``): V021, the library
   minus the ABSENT songs (every copy of each), and for the targets and
   distractors LRCLIB's stored answers through the shipped write path
   (``store.ensure_rows`` + ``store.record_lrclib``), then the line index
   (``lyrics_index.catch_up``) until nothing is pending.
2. Parses each transcript the way production does
   (``router.normalize_transcript`` → ``strip_leading_filler`` → the first
   fast path over the REAL handler registry): the lyric play path →
   ``resolve_lyrics(mode="explicit")``, the lyric find path →
   ``mode="identify"``; music's "play X by Y" / "play X" → the spoken-name
   resolver (a play or an ask is a NAME outcome), else an emulation of
   MPD's tag then file-name search (a hit is an MPD outcome), else the
   last resort's readings → ``mode="fallback"`` (a LYRIC outcome).
   Anything else is NOT ROUTED. Thresholds are the Settings DEFAULTS.
3. Scores every item and prints TOTALS ONLY: per group × voice, the
   ship bars over the three TTS voices (wrong plays <= 2 % of the positive
   items, false plays <= 1 % of the negative items), resolve_lyrics
   latency. ``--dump-decisions`` writes qid, group, variant, voice, path,
   decision, reason, candidate track ids and scores — never a text.
4. Empties the lane (``TRUNCATE library_tracks RESTART IDENTITY CASCADE``)
   so no lyric stays in it.

NEVER TUNE ON THIS. No constant, threshold, rule, stop word, carrier or
pattern may be derived from gate items. A fix after the run needs a reason
that does not come from reading gate items (a crash, a rule not applied as
written, a bug a unit test finds), is re-run once, and is labelled a
re-run.

A manifest mismatch prints "NOT THE GATE" and exits 2.

Usage:
    DATABASE_URL=postgresql+asyncpg://…/domovoiN_test \\
    python scripts/eval_lyric_search.py --gate-dir <dir> [--voices say,lessac,amy,ryan] \\
        [--dump-decisions FILE] [--json FILE]

``--gate-dir`` defaults to $DOMOVOI_LYRICS_GATE_DIR.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
os.environ.setdefault("USE_STUBS", "true")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_ENV = "DOMOVOI_LYRICS_GATE_DIR"
GATE_FILES = ("library.json", "pool.json", "gate_set.json", "gate_tr.jsonl")
RAW_ENTRY = "raw/"
TTS_VOICES = ("lessac", "amy", "ryan")
POSITIVE = ("P-EC", "P-EV", "P-ID", "P-FB", "P-MC", "P-MV")
NEGATIVE = ("N-AE", "N-AF", "N-TI", "N-GE", "N-IE", "N-IF")
REFUSED_DBS = ("domovoi_test", "domovoi2_test")
#: [EV15] ship bars, fixed before the run.
WRONG_PLAY_BAR = 2.0
FALSE_PLAY_BAR = 1.0


# ─── The gate data ─────────────────────────────────────────────────────────


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_listing_hash(gate_dir: Path) -> str:
    """One hash over raw/: each file's sha256 and name, sorted by name."""
    raw = gate_dir / "raw"
    lines = [f"{_sha256(p)}  {p.name}" for p in sorted(raw.glob("*.json"), key=lambda p: p.name)]
    return _sha256_bytes("\n".join(lines).encode("utf-8"))


def verify_manifest(gate_dir: Path) -> tuple[bool, str, list[str]]:
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
    for name in (*GATE_FILES, RAW_ENTRY):
        if name not in expected:
            problems.append(f"{name}: not in the manifest")
            continue
        if name == RAW_ENTRY:
            if not (gate_dir / "raw").is_dir():
                problems.append("raw/: missing")
            elif raw_listing_hash(gate_dir) != expected[name]:
                problems.append("raw/: listing hash differs")
        elif not (gate_dir / name).is_file():
            problems.append(f"{name}: missing")
        elif _sha256(gate_dir / name) != expected[name]:
            problems.append(f"{name}: hash differs")
    return not problems, _sha256(manifest), problems


def _git_sha() -> str:
    try:
        sha = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return (sha or "unknown") + ("-dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "unknown"


def song_key(title: str | None, artist: str | None) -> str:
    """One song whatever the album copy: the alias keys of its clean title
    and of its primary performer (the gate builder's key)."""
    from domovoi.handlers.shared.spoken_names import alias_key, clean_title, split_credit

    parts = split_credit(artist or "")
    return f"{alias_key(clean_title(title or ''))}|{alias_key(parts[0] if parts else (artist or ''))}"


def answer_from_raw(raw: dict) -> Any:
    """The stored answer as the ``LrclibAnswer`` the worker would record."""
    from domovoi.clients.lrclib import LrclibRecord
    from domovoi.workers.lyrics_fetch import LrclibAnswer

    rec = raw.get("record")
    record = None if rec is None else LrclibRecord(
        id=int(rec["id"]), track_name=rec.get("track_name") or "", artist_name=rec.get("artist_name") or "",
        album_name=rec.get("album_name"), duration=rec.get("duration"),
        instrumental=bool(rec.get("instrumental")), plain=rec.get("plain"), synced=rec.get("synced"),
    )
    synced = raw.get("synced")
    return LrclibAnswer(
        status=raw["status"], match=raw.get("match"), record=record, plain=raw.get("plain"),
        synced=None if synced is None else tuple((int(ms), str(t)) for ms, t in synced),
        error=raw.get("error"),
    )


# ─── The database ─────────────────────────────────────────────────────────


def check_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    name = urlsplit(url.replace("+asyncpg", "")).path.lstrip("/")
    print(f"DATABASE_URL={url}")
    if not name.endswith("_test") or name in REFUSED_DBS:
        raise SystemExit(f"refusing database {name!r}: a lane's *_test database only, never {REFUSED_DBS}")
    return name


_LIB_COLUMNS = ("id", "file_path", "title", "artist", "album", "duration_sec", "source", "source_id",
                "added_at", "added_via", "musicbrainz_recording_id", "enriched_at", "favorited")


def _ts(v: str | None) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


async def load_database(library: list[dict], pool: dict, raw_dir: Path) -> dict[str, int]:
    from sqlalchemy import text

    from domovoi.db.session import SessionLocal, engine
    from domovoi.lyrics import store
    from domovoi.tests.lyrics_testkit import apply_v021
    from domovoi.workers import lyrics_index

    async with engine.connect() as conn:
        others = (await conn.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()"
        ))).scalar_one()
    if others:
        raise SystemExit(f"the lane is busy ({others} other connections): refusing to load it")
    await apply_v021()
    absent_keys = {song_key(r["title"], r["artist"]) for r in library if r["id"] in set(pool["absent"])}
    rows = [r for r in library if song_key(r["title"], r["artist"]) not in absent_keys]
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE library_tracks RESTART IDENTITY CASCADE"))
        cols = ", ".join(_LIB_COLUMNS)
        params = ", ".join(f":{c}" for c in _LIB_COLUMNS)
        await conn.execute(text(f"INSERT INTO library_tracks ({cols}) VALUES ({params})"), [
            {**{c: r.get(c) for c in _LIB_COLUMNS},
             "added_at": _ts(r.get("added_at")) or datetime.now(timezone.utc),
             "enriched_at": _ts(r.get("enriched_at")),
             "favorited": bool(r.get("favorited")),
             "added_via": r.get("added_via") if r.get("added_via") in ("voice", "manual") else None}
            for r in rows
        ])
        await conn.execute(text(
            "SELECT setval('library_tracks_id_seq', (SELECT max(id) FROM library_tracks))"
        ))
    by_id = {r["id"]: r for r in rows}
    with_lyrics = [i for i in pool["targets"] + pool["distractors"] if i in by_id]
    async with SessionLocal() as s:
        await store.ensure_rows(s, with_lyrics)
        for tid in with_lyrics:
            raw = json.loads((raw_dir / f"{tid}.json").read_text(encoding="utf-8"))
            r = by_id[tid]
            await store.record_lrclib(
                s, tid, answer_from_raw(raw),
                query_md5=store.query_md5(r["title"], r["artist"], r.get("album"), r.get("duration_sec")),
            )
        await s.commit()
    indexed = 0
    while True:
        out = await lyrics_index.catch_up(budget_sec=120.0, batch=200)
        indexed += out["indexed"]
        if out["pending"] == 0:
            break
    async with engine.connect() as conn:
        counts = (await conn.execute(text(
            "SELECT (SELECT count(*) FROM library_tracks), (SELECT count(*) FROM track_lyrics),"
            " (SELECT count(*) FROM track_lyrics WHERE eff_source IS NOT NULL),"
            " (SELECT count(*) FROM track_lyrics WHERE has_synced),"
            " (SELECT count(*) FROM track_lyric_lines)"
        ))).one()
    return {"library_rows": counts[0], "absent_rows_removed": len(library) - len(rows),
            "lyric_rows": counts[1], "with_lyrics": counts[2], "timed": counts[3],
            "index_rows": counts[4], "indexed": indexed}


async def empty_lane() -> None:
    from sqlalchemy import text

    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE library_tracks RESTART IDENTITY CASCADE"))


# ─── Routing, as production does ──────────────────────────────────────────


def route(raw_text: str) -> tuple[str, Any]:
    """(kind, payload): ("lyric", (mode, phrase)) for the lyric fast paths,
    ("play", (query, said)) for music's "play X [by Y]", ("other", None)."""
    from domovoi.handlers import music
    from domovoi.router import first_fast_path, normalize_transcript, strip_leading_filler

    t = strip_leading_filler(normalize_transcript(raw_text or ""))
    hit = first_fast_path(t)
    if hit is None:
        return "other", None
    handler, fp, m = hit
    if handler.name != "music":
        return "other", None
    if fp.pattern is music._LYRIC_PLAY_RE:
        return "lyric", ("explicit", music._clean_capture(m.group("lyrics")))
    if fp.pattern is music._LYRIC_FIND_RE:
        return "lyric", ("identify", music._clean_capture(m.group("lyrics")))
    if fp.pattern is music._PLAY_ARTIST_RE:
        return "play", ({"title": music._clean_capture(m.group(1)), "artist": music._clean_capture(m.group(2))},
                        music._clean_capture(m.group(0)[len("play "):]))
    if fp.pattern is music._PLAY_ANY_RE:
        return "play", ({"any": music._clean_capture(m.group(1))}, None)
    return "other", None


class MpdEmulation:
    """[EV12] MPD's tag search then its file-name search, over the
    evaluation library, first hit by id."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = sorted(rows, key=lambda r: r["id"])

    def play(self, query: dict) -> int | None:
        from domovoi.clients.mpd import filename_hit_matches

        cf = lambda s: (s or "").casefold()  # noqa: E731
        q = {k: cf(v) for k, v in query.items() if v}
        if not q:
            return None
        if "any" in q:
            hit = next((r for r in self.rows
                        if any(q["any"] in cf(r.get(f)) for f in ("title", "artist", "album"))), None)
        else:
            hit = next((r for r in self.rows if all(v in cf(r.get(k)) for k, v in q.items())), None)
        if hit is None:
            words = tuple(q.values())
            hit = next((r for r in self.rows if filename_hit_matches(r.get("file_path") or "", words)), None)
        return int(hit["id"]) if hit else None


# ─── Scoring ──────────────────────────────────────────────────────────────


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 2) if d else float("nan")


class Judge:
    def __init__(self, keys: dict[int, str]) -> None:
        self.keys = keys            # track id → song key

    def hits(self, track_ids, item: dict) -> bool:
        target, tkey = item.get("target"), item.get("target_key")
        return any(int(t) == target or (tkey and self.keys.get(int(t)) == tkey) for t in track_ids)


async def evaluate(items: list[dict], work: list[tuple[dict, str, str]], library_rows: list[dict],
                   dump_path: str | None) -> dict[str, Any]:
    from domovoi.config import Settings, settings
    from domovoi.db.session import SessionLocal
    from domovoi.handlers.music import _lyric_readings
    from domovoi.handlers.shared import library_match

    play_t = float(Settings.model_fields["music_match_play_threshold"].default)
    ask_t = float(Settings.model_fields["music_match_ask_threshold"].default)
    settings.lyrics_search_enabled = True
    settings.music_match_enabled = True
    judge = Judge({r["id"]: song_key(r["title"], r["artist"]) for r in library_rows})
    mpd = MpdEmulation(library_rows)
    counts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    latencies: list[float] = []
    rank = {"play": 2, "ask": 1, "none": 0}
    dump = open(dump_path, "w", encoding="utf-8") if dump_path else None

    async def lyric(session, phrase, mode, artist=None):
        t0 = time.perf_counter()
        res = await library_match.resolve_lyrics(session, phrase, mode=mode, artist=artist,
                                                 play_threshold=play_t, ask_threshold=ask_t)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        return res

    try:
        async with SessionLocal() as session:
            index = await library_match.current_index(session)
            for item, voice, text_ in work:
                group = item["group"]
                positive = group in POSITIVE
                path = decision = reason = mode = None
                cands: list = []
                mpd_id = None
                try:
                    kind, payload = route(text_)
                    if kind == "lyric":
                        mode, phrase = payload
                        res = await lyric(session, phrase, mode)
                        path, decision, reason = "LYRIC", res.decision, res.reason
                        cands = list(res.candidates)
                    elif kind == "play":
                        query, said = payload
                        res = await asyncio.to_thread(library_match.resolve, index, query,
                                                      play_threshold=play_t, ask_threshold=ask_t)
                        if res.decision in ("play", "ask"):
                            path, decision, reason = "NAME", res.decision, res.reason
                            cands = list(res.candidates)
                        else:
                            mpd_id = mpd.play(query)
                            if mpd_id is not None:
                                path, decision, reason = "MPD", "play", "mpd"
                            else:
                                best = None
                                for phrase, artist in _lyric_readings(query, said):
                                    r2 = await lyric(session, phrase, "fallback", artist)
                                    if best is None or rank.get(r2.decision, 0) > rank.get(best.decision, 0):
                                        best = r2
                                    if best.decision == "play":
                                        break
                                mode = "fallback"
                                path = "LYRIC"
                                if best is None:
                                    decision, reason = "none", "no_reading"
                                else:
                                    decision, reason = best.decision, best.reason
                                    cands = list(best.candidates)
                    else:
                        decision, reason = "not_routed", None
                    await session.rollback()
                except Exception as e:  # noqa: BLE001 — the type only: a message may carry the phrase
                    await session.rollback()
                    decision, reason = "error", type(e).__name__

                # The outcome.
                played = None
                if decision == "play":
                    played = [mpd_id] if path == "MPD" else (list(cands[0].track_ids) if cands else [])
                if decision == "not_routed":
                    outcome = "not_routed"
                elif decision == "error":
                    outcome = "error"
                elif positive:
                    if decision == "play":
                        right = judge.hits(played, item)
                        if path == "LYRIC" and mode == "identify":
                            outcome = "told_right" if right else "told_wrong"
                        else:
                            outcome = "right_play" if right else "wrong_play"
                    elif decision == "ask":
                        first = bool(cands) and judge.hits(cands[0].track_ids, item)
                        listed = any(judge.hits(c.track_ids, item) for c in cands)
                        stem = "suggested" if (path == "LYRIC" and mode == "identify") else "ask"
                        outcome = f"{stem}_right_first" if first else (
                            f"{stem}_right_listed" if listed else f"{stem}_wrong")
                    else:
                        outcome = "none"
                else:
                    if decision == "play":
                        if path == "LYRIC":
                            outcome = "false_told" if mode == "identify" else "false_play"
                        else:
                            right = group == "N-TI" and judge.hits(played, item)
                            outcome = f"{path.lower()}_play_right" if right else f"{path.lower()}_play"
                    elif decision == "ask":
                        outcome = f"{path.lower()}_ask"
                    else:
                        outcome = "none"
                vshort = voice_short(voice)
                for v in (vshort, "all-tts" if vshort in TTS_VOICES else None):
                    if v is None:
                        continue
                    c = counts[group][v]
                    c["n"] += 1
                    c[outcome] += 1
                    if path:
                        c[f"path:{path}"] += 1
                if dump is not None:
                    dump.write(json.dumps({
                        "qid": item["qid"], "group": group, "variant": item.get("variant"), "voice": vshort,
                        "path": path, "mode": mode, "decision": decision, "reason": reason, "outcome": outcome,
                        "target": item.get("target"),
                        "candidates": [{"tracks": [int(t) for t in c.track_ids][:5], "score": round(c.score, 4),
                                        "type": c.ref.type} for c in cands[:3]],
                        "mpd_track": mpd_id,
                    }) + "\n")
    finally:
        if dump is not None:
            dump.close()
    lat = sorted(latencies)
    return {
        "counts": {g: {v: dict(c) for v, c in by_v.items()} for g, by_v in counts.items()},
        "latency_ms": {
            "n": len(lat),
            "p50": round(statistics.median(lat), 1) if lat else None,
            "p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
            "max": round(lat[-1], 1) if lat else None,
        },
        "thresholds": {"play": play_t, "ask": ask_t},
    }


def voice_short(voice: str) -> str:
    for v in TTS_VOICES:
        if v in voice:
            return v
    return voice


# ─── The report ───────────────────────────────────────────────────────────

POS_COLS = ("right_play", "wrong_play", "ask_right_first", "ask_right_listed", "ask_wrong",
            "told_right", "told_wrong", "suggested_right_first", "suggested_right_listed", "suggested_wrong",
            "none", "not_routed", "error")
NEG_COLS = ("false_play", "false_told", "lyric_ask", "name_play_right", "name_play", "mpd_play_right",
            "mpd_play", "name_ask", "none", "not_routed", "error")


def bars(counts: dict) -> dict[str, Any]:
    pos_n = wrong = told_wrong = 0
    neg_n = false_play = false_told = 0
    for v in TTS_VOICES:
        for g in POSITIVE:
            c = counts.get(g, {}).get(v, {})
            pos_n += c.get("n", 0)
            wrong += c.get("wrong_play", 0)
            told_wrong += c.get("told_wrong", 0)
        for g in NEGATIVE:
            c = counts.get(g, {}).get(v, {})
            neg_n += c.get("n", 0)
            false_play += c.get("false_play", 0)
            false_told += c.get("false_told", 0)
    return {
        "positive_items": pos_n, "wrong_plays": wrong, "wrong_play_pct": _pct(wrong, pos_n),
        "wrong_play_bar_pct": WRONG_PLAY_BAR, "wrong_play_pass": pos_n > 0 and _pct(wrong, pos_n) <= WRONG_PLAY_BAR,
        "told_wrong": told_wrong, "wrong_incl_told_pct": _pct(wrong + told_wrong, pos_n),
        "negative_items": neg_n, "false_plays": false_play, "false_play_pct": _pct(false_play, neg_n),
        "false_play_bar_pct": FALSE_PLAY_BAR, "false_play_pass": neg_n > 0 and _pct(false_play, neg_n) <= FALSE_PLAY_BAR,
        "false_told": false_told,
    }


def format_report(result: dict[str, Any]) -> str:
    m = result["meta"]
    out = [
        f"lyric-search gate{'' if m['is_gate'] else ' (NOT THE GATE: ' + '; '.join(m['problems']) + ')'}"
        f" — git {m['git']} — MANIFEST sha256 {m['manifest_sha256'][:16]}…",
        f"thresholds play {result['thresholds']['play']} / ask {result['thresholds']['ask']}; "
        f"pool: {m['pool']['targets']} targets, {m['pool']['absent']} absent, {m['pool']['distractors']} distractors; "
        f"database: {m['db']}",
        f"resolve_lyrics: {result['latency_ms']['n']} calls, p50 {result['latency_ms']['p50']} ms, "
        f"p95 {result['latency_ms']['p95']} ms, max {result['latency_ms']['max']} ms",
        "",
    ]
    counts = result["counts"]
    voices = [v for v in ("say", *TTS_VOICES, "all-tts") if any(v in counts.get(g, {}) for g in counts)]
    for title, groups, cols in (("POSITIVE (% of items)", (*POSITIVE, "P-ALL"), POS_COLS),
                                ("NEGATIVE (% of items)", (*NEGATIVE, "N-ALL"), NEG_COLS)):
        used = [c for c in cols if any(counts.get(g, {}).get(v, {}).get(c) for g in groups for v in voices)]
        out.append(title)
        out.append(f"{'group':<7}{'voice':<8}{'n':>5}" + "".join(f"{c[:16]:>17}" for c in used))
        for g in groups:
            for v in voices:
                c = counts.get(g, {}).get(v)
                if not c:
                    continue
                n = c.get("n", 0)
                out.append(f"{g:<7}{v:<8}{n:>5}" + "".join(f"{_pct(c.get(k, 0), n):>16.1f}%" for k in used))
        out.append("")
    b = result["bars"]
    out.append(
        f"SHIP BARS over the three TTS voices: wrong plays {b['wrong_plays']}/{b['positive_items']} = "
        f"{b['wrong_play_pct']}% (bar <= {b['wrong_play_bar_pct']}%) {'PASS' if b['wrong_play_pass'] else 'FAIL'}; "
        f"false plays {b['false_plays']}/{b['negative_items']} = {b['false_play_pct']}% "
        f"(bar <= {b['false_play_bar_pct']}%) {'PASS' if b['false_play_pass'] else 'FAIL'}"
    )
    out.append(f"(identify answers naming the wrong song, not plays: {b['told_wrong']}; "
               f"wrong plays + those = {b['wrong_incl_told_pct']}%; false identify answers on negatives: "
               f"{b['false_told']})")
    return "\n".join(out)


# ─── CLI ───────────────────────────────────────────────────────────────────


async def run(args) -> dict[str, Any]:
    gate = Path(args.gate_dir)
    ok, manifest_hash, problems = verify_manifest(gate)
    if not ok:
        print("NOT THE GATE: " + "; ".join(problems), file=sys.stderr)
        raise SystemExit(2)
    db = check_database_url()
    library = json.loads((gate / "library.json").read_text(encoding="utf-8"))["items"]
    pool = json.loads((gate / "pool.json").read_text(encoding="utf-8"))
    items = json.loads((gate / "gate_set.json").read_text(encoding="utf-8"))
    transcripts = [json.loads(line) for line in (gate / "gate_tr.jsonl").read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    wanted = [v.strip() for v in (args.voices or "").split(",") if v.strip()] or ["say", *TTS_VOICES]
    by_qid = {it["qid"]: it for it in items}
    work: list[tuple[dict, str, str]] = []
    if "say" in wanted:
        work += [(it, "say", it["say"]) for it in items]
    for tr in transcripts:
        it = by_qid.get(tr.get("qid"))
        if it is not None and any(v in tr.get("voice", "") for v in wanted if v != "say"):
            work.append((it, tr["voice"], tr.get("core") or ""))
    t0 = time.perf_counter()
    loaded = await load_database(library, pool, gate / "raw")
    load_s = time.perf_counter() - t0
    print("loaded: " + ", ".join(f"{k} {v}" for k, v in loaded.items()) + f" ({load_s:.0f} s)")
    try:
        absent_keys = {song_key(r["title"], r["artist"]) for r in library if r["id"] in set(pool["absent"])}
        rows = [r for r in library if song_key(r["title"], r["artist"]) not in absent_keys]
        result = await evaluate(items, work, rows, args.dump_decisions)
    finally:
        await empty_lane()
    result["meta"] = {
        "git": _git_sha(), "manifest_sha256": manifest_hash, "is_gate": ok, "problems": problems,
        "db": db, "voices": wanted, "work_items": len(work), "loaded": loaded,
        "pool": {k: len(pool[k]) for k in ("targets", "absent", "distractors")},
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    result["counts"].update(overall(result["counts"]))
    result["bars"] = bars(result["counts"])
    return result


def overall(counts: dict) -> dict[str, dict[str, dict[str, int]]]:
    """"P-ALL" / "N-ALL": every positive / negative group summed, per voice."""
    out: dict[str, dict[str, dict[str, int]]] = {}
    for name, groups in (("P-ALL", POSITIVE), ("N-ALL", NEGATIVE)):
        by_voice: dict[str, Counter] = defaultdict(Counter)
        for g in groups:
            for v, c in counts.get(g, {}).items():
                by_voice[v].update(c)
        out[name] = {v: dict(c) for v, c in by_voice.items()}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gate-dir", default=os.environ.get(GATE_ENV))
    ap.add_argument("--voices", default="say,lessac,amy,ryan")
    ap.add_argument("--dump-decisions", help="per-item results without any text (for an independent audit)")
    ap.add_argument("--json", dest="json_out", help="also write the aggregates here")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not args.gate_dir:
        print(f"no gate dir: pass --gate-dir or set {GATE_ENV}", file=sys.stderr)
        return 2
    # Nothing a module logs reaches the console: a log line may quote a
    # request, and a request here is a lyric.
    logging.disable(logging.CRITICAL)
    result = asyncio.run(run(args))
    print(format_report(result))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_dir = Path(args.gate_dir) / "results"
    results_dir.mkdir(exist_ok=True)
    (results_dir / f"{stamp}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    b = result["bars"]
    return 0 if (b["wrong_play_pass"] and b["false_play_pass"]) else 1


if __name__ == "__main__":
    sys.exit(main())
