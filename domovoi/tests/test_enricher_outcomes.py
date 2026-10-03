"""B3: the library enricher stamps a track done only when a provider
actually answered.

Before this fix, ``enrich_library`` stamped ``enriched_at`` after every
attempt: with no AcoustID key and no Shazam add-on it marked a whole
library "no match" without sending one request (the Beelink: 5,232 rows,
0 MusicBrainz ids), and a network error was a "no match" too. Covered
here:

* the gates — disabled, internet turned off, probe offline, no provider —
  return at once, touch no row and call no provider; the voice trigger
  and the admin endpoint answer honestly instead of starting a sweep;
* each provider's verdict (match / no-match / not asked / error), with
  the real ``_enrich_via_acoustid`` / ``_enrich_via_shazam`` against a
  fake ``acoustid.match`` / a fake ``shazamio`` module (never the
  network, never the real shazamio import);
* the sweep: an error is not stamped, five in a row stop it, a file that
  can't be fingerprinted doesn't count toward that, a genuine no-match is
  ``no_match``, a match is ``matched``, a box where no provider can run
  stops after one file;
* V020 itself: idempotent by construction (DB-free), and present on the
  lane (DB).

The one-off recovery of legacy stamps is test_enricher_recovery.py.
"""

from __future__ import annotations

import logging
import re
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

from domovoi import connectivity, egress
from domovoi.handlers.library import LibraryHandler, _format_enrich_summary
from domovoi.models import Context
from domovoi.tests.conftest import requires_db
from domovoi.tests.enrich_outcome_testkit import V020, apply_v020, v020_statements
from domovoi.workers import library_enricher as le
from domovoi.workers.library_enricher import (
    EnrichmentResult,
    Lookup,
    Verdict,
    enrich_library,
)

ENRICHER = "domovoi.workers.library_enricher"


def _match(**kw) -> Lookup:
    return Lookup(Verdict.MATCH, result=EnrichmentResult(**kw))


NO_MATCH = Lookup(Verdict.NO_MATCH, detail="test: nothing")
NET_ERROR = Lookup(Verdict.ERROR, detail="test: connection refused", transient=True)
FILE_ERROR = Lookup(Verdict.ERROR, detail="test: can't decode this file", transient=False)
NOT_ASKED = Lookup(Verdict.NOT_ASKED, detail="test: not asked")


class _Probe:
    def __init__(self, online: bool) -> None:
        self.online = online


@pytest.fixture
def probe():
    """Register a fake connectivity probe; restore none afterwards."""
    def _set(online: bool) -> _Probe:
        p = _Probe(online)
        connectivity.set_current_probe(p)  # type: ignore[arg-type]
        return p

    yield _set
    connectivity.set_current_probe(None)


def _explode(*_a, **_kw):
    raise AssertionError("a provider was asked")


async def _aexplode(*_a, **_kw):
    raise AssertionError("a provider was asked")


# ═══ Gates: nothing asked, nothing stamped ═══════════════════════════════


@pytest.mark.asyncio
async def test_internet_off_skips_before_anything(monkeypatch) -> None:
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", True)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    monkeypatch.setattr(f"{ENRICHER}.session_scope", _explode)
    monkeypatch.setattr(f"{ENRICHER}._enrich_one", _aexplode)
    with egress.override_policy("never"):
        counts = await enrich_library()
    assert counts["skipped"] == "internet_off"
    assert counts["scanned"] == counts["matched"] == counts["no_match"] == 0


@pytest.mark.asyncio
async def test_probe_offline_skips_before_anything(monkeypatch, probe) -> None:
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", True)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    monkeypatch.setattr(f"{ENRICHER}.session_scope", _explode)
    monkeypatch.setattr(f"{ENRICHER}._enrich_one", _aexplode)
    probe(False)
    counts = await enrich_library()
    assert counts["skipped"] == "offline"


@pytest.mark.asyncio
async def test_disabled_skips_before_anything(monkeypatch) -> None:
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", False)
    monkeypatch.setattr(f"{ENRICHER}.session_scope", _explode)
    counts = await enrich_library()
    assert counts["skipped"] == "disabled"


def test_provider_available_is_a_key_or_shazamio(monkeypatch) -> None:
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda name, *a: None if name == "shazamio" else real(name, *a),
    )
    monkeypatch.setattr(f"{ENRICHER}.settings.acoustid_api_key", "")
    assert le.provider_available() is False
    monkeypatch.setattr(f"{ENRICHER}.settings.acoustid_api_key", "   ")
    assert le.provider_available() is False
    monkeypatch.setattr(f"{ENRICHER}.settings.acoustid_api_key", "app-key")
    assert le.provider_available() is True
    monkeypatch.setattr(f"{ENRICHER}.settings.acoustid_api_key", "")
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda name, *a: object() if name == "shazamio" else real(name, *a),
    )
    assert le.provider_available() is True


@requires_db
@pytest.mark.asyncio
async def test_no_provider_stamps_nothing_and_says_how_many_wait(
    db_session, monkeypatch, tmp_path, caplog,
) -> None:
    await apply_v020()
    f = tmp_path / "a.mp3"
    f.write_bytes(b"")
    await db_session.execute(
        text("INSERT INTO library_tracks (file_path, title) VALUES (:fp, 'a'), (:fp2, 'b')"),
        {"fp": str(f), "fp2": str(tmp_path / "b.mp3")},
    )
    # ...and one legacy stamp the recovery would requeue: it waits too.
    await db_session.execute(
        text("INSERT INTO library_tracks (file_path, title, enriched_at) VALUES (:fp, 'c', NOW())"),
        {"fp": str(tmp_path / "c.mp3")},
    )
    await db_session.commit()
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", True)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: False)
    monkeypatch.setattr(f"{ENRICHER}._enrich_one", _aexplode)

    with caplog.at_level(logging.INFO, logger=ENRICHER):
        counts = await enrich_library()

    assert counts["skipped"] == "no_provider"
    assert counts["requeued_legacy"] == 0
    rows = (await db_session.execute(
        text("SELECT title, enriched_at IS NOT NULL AS stamped, enrich_outcome FROM library_tracks ORDER BY id")
    )).all()
    assert [(r.title, r.stamped, r.enrich_outcome) for r in rows] == [
        ("a", False, None), ("b", False, None), ("c", True, None),
    ]
    assert any(
        "no AcoustID key and no Shazam add-on; 3 tracks wait for one" in r.getMessage()
        for r in caplog.records
    )


# ═══ AcoustID verdicts (real function, fake acoustid.match) ═══════════════


@pytest.fixture
def acoustid_key(monkeypatch):
    monkeypatch.setattr(f"{ENRICHER}.settings.acoustid_api_key", "app-key")
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_acoustid_min_score", 0.7)
    monkeypatch.setattr(le, "_key_hint_logged", False)


@pytest.mark.asyncio
async def test_acoustid_web_service_error_is_an_error(acoustid_key, tmp_path) -> None:
    import acoustid

    def boom(*_a, **_kw):
        raise acoustid.WebServiceError("HTTP request failed: Connection refused")

    with patch("acoustid.match", boom):
        out = await le._enrich_via_acoustid(tmp_path / "x.mp3")
    assert out.verdict is Verdict.ERROR
    assert out.transient is True


@pytest.mark.asyncio
async def test_acoustid_connection_error_is_an_error(acoustid_key, tmp_path) -> None:
    def boom(*_a, **_kw):
        raise ConnectionResetError("reset by peer")

    with patch("acoustid.match", boom):
        out = await le._enrich_via_acoustid(tmp_path / "x.mp3")
    assert out.verdict is Verdict.ERROR
    assert out.transient is True


@pytest.mark.asyncio
async def test_acoustid_under_never_is_an_error_without_a_request(acoustid_key, tmp_path) -> None:
    with patch("acoustid.match", _explode), egress.override_policy("never"):
        out = await le._enrich_via_acoustid(tmp_path / "x.mp3")
    assert out.verdict is Verdict.ERROR
    assert out.transient is True


@pytest.mark.asyncio
async def test_acoustid_unreadable_file_is_not_asked(acoustid_key, tmp_path) -> None:
    import acoustid

    def bad(*_a, **_kw):
        raise acoustid.FingerprintGenerationError("audio could not be decoded")

    with patch("acoustid.match", bad):
        out = await le._enrich_via_acoustid(tmp_path / "x.mp3")
    assert out.verdict is Verdict.NOT_ASKED
    assert out.unusable is False


@pytest.mark.asyncio
async def test_a_refused_key_logs_the_application_key_hint_once(acoustid_key, tmp_path, caplog) -> None:
    import acoustid

    def refused(*_a, **_kw):
        raise acoustid.WebServiceError(
            "invalid API key", '{"status": "error", "error": {"code": 4, "message": "invalid API key"}}'
        )

    with patch("acoustid.match", refused), caplog.at_level(logging.WARNING, logger=ENRICHER):
        first = await le._enrich_via_acoustid(tmp_path / "x.mp3")
        second = await le._enrich_via_acoustid(tmp_path / "y.mp3")
    assert first.verdict is second.verdict is Verdict.ERROR
    hints = [r for r in caplog.records if "APPLICATION key" in r.getMessage()]
    assert len(hints) == 1
    assert "https://acoustid.org/new-application" in hints[0].getMessage()


# ═══ Shazam verdicts (real function, fake shazamio module) ════════════════


def _fake_shazamio(monkeypatch, recognize):
    mod = types.ModuleType("shazamio")

    class Shazam:
        async def recognize(self, path):
            return await recognize(path)

    mod.Shazam = Shazam
    monkeypatch.setitem(sys.modules, "shazamio", mod)


@pytest.mark.asyncio
async def test_a_shazam_network_exception_is_a_transient_error(monkeypatch, tmp_path) -> None:
    async def down(_path):
        raise ConnectionRefusedError("no route")

    _fake_shazamio(monkeypatch, down)
    out = await le._enrich_via_shazam(tmp_path / "x.mp3")
    assert out.verdict is Verdict.ERROR
    assert out.transient is True


@pytest.mark.asyncio
async def test_a_shazam_decode_exception_is_a_file_error(monkeypatch, tmp_path) -> None:
    async def broken(_path):
        raise ValueError("could not decode audio")

    _fake_shazamio(monkeypatch, broken)
    out = await le._enrich_via_shazam(tmp_path / "x.mp3")
    assert out.verdict is Verdict.ERROR
    assert out.transient is False


@pytest.mark.asyncio
async def test_shazam_under_never_is_an_error_without_a_request(monkeypatch, tmp_path) -> None:
    _fake_shazamio(monkeypatch, _aexplode)
    with egress.override_policy("never"):
        out = await le._enrich_via_shazam(tmp_path / "x.mp3")
    assert out.verdict is Verdict.ERROR


@pytest.mark.asyncio
async def test_shazam_not_installed_is_not_asked_and_unusable(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(sys.modules, "shazamio", None)  # import raises ImportError
    out = await le._enrich_via_shazam(tmp_path / "x.mp3")
    assert out.verdict is Verdict.NOT_ASKED
    assert out.unusable is True


# ═══ Combining the two (DB-free) ══════════════════════════════════════════


@pytest.mark.parametrize(
    ("ac", "sh", "want"),
    [
        (_match(title="A"), _aexplode, Verdict.MATCH),      # Shazam not asked after a match
        (NO_MATCH, _match(title="S"), Verdict.MATCH),
        (NET_ERROR, _match(title="S"), Verdict.MATCH),
        (NO_MATCH, NO_MATCH, Verdict.NO_MATCH),
        (NOT_ASKED, NO_MATCH, Verdict.NO_MATCH),             # no key, Shazam answered
        (NO_MATCH, NOT_ASKED, Verdict.NO_MATCH),             # AcoustID answered, no Shazam
        (NO_MATCH, NET_ERROR, Verdict.ERROR),                # one raised: not decided
        (NET_ERROR, NO_MATCH, Verdict.ERROR),
        (NOT_ASKED, NOT_ASKED, Verdict.NOT_ASKED),           # nobody answered
    ],
)
@pytest.mark.asyncio
async def test_enrich_one_combines_the_verdicts(monkeypatch, ac, sh, want) -> None:
    async def _ac(_p):
        return ac

    async def _sh(_p):
        return await sh(_p) if callable(sh) else sh

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", _ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _sh)
    out = await le._enrich_one(Path("x.mp3"), le._Providers())
    assert out.verdict is want


# ═══ The sweep (DB) ═══════════════════════════════════════════════════════


@pytest.fixture
async def lane(db_session, monkeypatch):
    await apply_v020()
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", True)
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_delay_sec", 0)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    yield db_session


async def _tracks(session, tmp_path, n: int) -> list[int]:
    ids = []
    for i in range(n):
        f = tmp_path / f"t{i}.mp3"
        f.write_bytes(b"")
        row = (await session.execute(
            text("INSERT INTO library_tracks (file_path, title) VALUES (:fp, :t) RETURNING id"),
            {"fp": str(f), "t": f"file {i}"},
        )).first()
        ids.append(int(row[0]))
    await session.commit()
    return ids


async def _state(session) -> list[tuple]:
    rows = (await session.execute(text(
        "SELECT title, artist, musicbrainz_recording_id, enriched_at IS NOT NULL, enrich_outcome "
        "FROM library_tracks ORDER BY id"
    ))).all()
    return [tuple(r) for r in rows]


def _providers(monkeypatch, ac, sh):
    """Patch the two provider calls with fixed verdicts (or callables)."""
    calls = {"ac": 0, "sh": 0}

    async def _ac(p):
        calls["ac"] += 1
        return ac(p) if callable(ac) else ac

    async def _sh(p):
        calls["sh"] += 1
        return sh(p) if callable(sh) else sh

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", _ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _sh)
    return calls


@requires_db
@pytest.mark.asyncio
async def test_an_error_is_never_stamped(lane, monkeypatch, tmp_path) -> None:
    await _tracks(lane, tmp_path, 2)
    _providers(monkeypatch, NET_ERROR, NO_MATCH)
    counts = await enrich_library()
    assert counts["errors"] == 2
    assert counts["no_match"] == counts["matched"] == 0
    assert all(stamped is False and outcome is None for *_x, stamped, outcome in await _state(lane))


@requires_db
@pytest.mark.asyncio
async def test_five_errors_in_a_row_stop_the_sweep(lane, monkeypatch, tmp_path) -> None:
    await _tracks(lane, tmp_path, 8)
    calls = _providers(monkeypatch, NET_ERROR, NOT_ASKED)
    counts = await enrich_library()
    assert counts["aborted"] is True
    assert counts["errors"] == le.MAX_CONSECUTIVE_ERRORS == 5
    assert counts["scanned"] == 8
    assert calls["ac"] == 5                     # the last three never asked
    assert all(stamped is False for *_x, stamped, _o in await _state(lane))


@requires_db
@pytest.mark.asyncio
async def test_a_match_resets_the_error_run(lane, monkeypatch, tmp_path) -> None:
    await _tracks(lane, tmp_path, 9)
    seq = iter([NET_ERROR] * 4 + [_match(title="ok", musicbrainz_recording_id="mb")] + [NET_ERROR] * 4)
    _providers(monkeypatch, lambda _p: next(seq), NOT_ASKED)
    counts = await enrich_library()
    assert counts["aborted"] is False
    assert counts["matched"] == 1 and counts["errors"] == 8


@requires_db
@pytest.mark.asyncio
async def test_file_specific_failures_never_stop_the_sweep(lane, monkeypatch, tmp_path) -> None:
    """An album of files nothing can decode must not wedge every future
    sweep at its first five tracks."""
    await _tracks(lane, tmp_path, 7)
    seq = iter([FILE_ERROR, NOT_ASKED] * 3 + [NO_MATCH])
    _providers(monkeypatch, NOT_ASKED, lambda _p: next(seq))
    counts = await enrich_library()
    assert counts["aborted"] is False
    assert counts["errors"] == 6 and counts["no_match"] == 1
    state = await _state(lane)
    assert [s[-1] for s in state] == [None] * 6 + ["no_match"]


@requires_db
@pytest.mark.asyncio
async def test_a_genuine_no_match_and_a_match_are_stamped(lane, monkeypatch, tmp_path) -> None:
    await _tracks(lane, tmp_path, 2)
    seq = iter([NO_MATCH, _match(title="Creep", artist="Radiohead", musicbrainz_recording_id="mb-1")])
    _providers(monkeypatch, lambda _p: next(seq), NO_MATCH)
    counts = await enrich_library()
    assert (counts["no_match"], counts["matched"]) == (1, 1)
    assert await _state(lane) == [
        ("file 0", None, None, True, "no_match"),
        ("Creep", "Radiohead", "mb-1", True, "matched"),
    ]


@requires_db
@pytest.mark.asyncio
async def test_a_box_where_no_provider_can_run_stops_after_one_file(lane, monkeypatch, tmp_path) -> None:
    """A key is set (so a provider is "available"), but fpcalc is missing
    and shazamio isn't installed: nothing can answer, nothing is stamped,
    and the sweep doesn't walk the library doing nothing."""
    await _tracks(lane, tmp_path, 4)
    calls = _providers(
        monkeypatch,
        Lookup(Verdict.NOT_ASKED, detail="fpcalc not found", unusable=True),
        Lookup(Verdict.NOT_ASKED, detail="shazamio not installed", unusable=True),
    )
    counts = await enrich_library()
    assert counts["aborted"] is True
    assert calls == {"ac": 1, "sh": 1}
    assert all(stamped is False for *_x, stamped, _o in await _state(lane))


@requires_db
@pytest.mark.asyncio
async def test_a_second_concurrent_sweep_does_not_start(lane, monkeypatch) -> None:
    monkeypatch.setattr(le, "_sweep_running", True)
    monkeypatch.setattr(f"{ENRICHER}._sweep", _aexplode)
    counts = await enrich_library()
    assert counts["skipped"] == "running"


# ═══ V020 ══════════════════════════════════════════════════════════════════


def test_v020_is_idempotent_by_construction() -> None:
    sql = V020.read_text(encoding="utf-8")
    assert b"\r\n" not in V020.read_bytes()
    statements = v020_statements()
    assert len(statements) == 2
    alter, update = statements
    assert re.fullmatch(
        r"ALTER TABLE library_tracks ADD COLUMN IF NOT EXISTS enrich_outcome TEXT", alter
    )
    # The backfill only ever touches rows with no outcome yet, and only
    # stamped rows that carry a MusicBrainz id: a re-run changes nothing.
    flat = " ".join(update.split())
    assert flat.startswith("UPDATE library_tracks SET enrich_outcome = 'matched' WHERE")
    for guard in ("enrich_outcome IS NULL", "enriched_at IS NOT NULL",
                  "musicbrainz_recording_id IS NOT NULL"):
        assert guard in flat
    assert "CHECK" not in sql.upper().split("--")[0]


@requires_db
@pytest.mark.asyncio
async def test_v020_column_is_on_the_lane(db_session) -> None:
    await apply_v020()
    await apply_v020()  # twice: a no-op
    row = (await db_session.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'library_tracks' AND column_name = 'enrich_outcome'"
    ))).first()
    assert row is not None and row[0] == "text"


# ═══ The voice trigger ═════════════════════════════════════════════════════


def _ctx(online: bool = True) -> Context:
    return Context(session_id=None, room_id="kitchen", online=online)


@pytest.mark.asyncio
async def test_voice_under_never_says_so_and_starts_nothing(monkeypatch) -> None:
    monkeypatch.setattr(f"{ENRICHER}.enrich_library", _aexplode)
    with egress.override_policy("never"):
        resp = await LibraryHandler()._enrich(_ctx(online=False), None)
    assert resp.text == "I'm set to stay off the internet, so I can't identify songs."


@pytest.mark.asyncio
async def test_voice_offline_says_it_will_wait(monkeypatch) -> None:
    monkeypatch.setattr(f"{ENRICHER}.enrich_library", _aexplode)
    resp = await LibraryHandler()._enrich(_ctx(online=False), None)
    assert resp.text == (
        "I can't reach the internet right now, so I'll identify your songs when it's back."
    )


@pytest.mark.asyncio
async def test_voice_without_a_provider_names_what_it_needs(monkeypatch) -> None:
    monkeypatch.setattr(f"{ENRICHER}.enrich_library", _aexplode)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: False)
    resp = await LibraryHandler()._enrich(_ctx(), None)
    assert resp.text == (
        "I can't identify songs yet: that needs a free AcoustID key or the Shazam add-on."
    )


@requires_db
@pytest.mark.asyncio
async def test_voice_counts_the_legacy_rows_the_recovery_will_requeue(
    db_session, monkeypatch, tmp_path,
) -> None:
    await apply_v020()
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, enriched_at, enrich_outcome, musicbrainz_recording_id) VALUES "
        "('/m/new.mp3', 'new', NULL, NULL, NULL),"            # unenriched
        "('/m/legacy.mp3', 'legacy', NOW(), NULL, NULL),"     # legacy stamp: waits
        "('/m/matched.mp3', 'm', NOW(), 'matched', 'mb'),"    # done
        "('/m/nomatch.mp3', 'n', NOW(), 'no_match', NULL)"    # done
    ))
    await db_session.commit()
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", True)
    started = []

    async def _fake_sweep():
        started.append(True)
        return le._counts()

    monkeypatch.setattr(f"{ENRICHER}.enrich_library", _fake_sweep)
    resp = await LibraryHandler()._enrich(_ctx(), db_session)
    assert resp.text.startswith("Got it — fingerprinting 2 tracks now.")
    import asyncio

    await asyncio.sleep(0)
    assert started == [True]


def test_the_completion_summary_is_honest() -> None:
    assert _format_enrich_summary(le._counts(skipped="no_provider")) == (
        "I can't identify songs yet: that needs a free AcoustID key or the Shazam add-on."
    )
    assert "stopped identifying songs" in _format_enrich_summary(
        le._counts(aborted=True, errors=5, scanned=9)
    )
    assert _format_enrich_summary(le._counts(scanned=3, matched=2, errors=1)) == (
        "Library enrichment done — identified 2 of 3 tracks, 1 will be tried again later."
    )
    with egress.override_policy("never"):
        assert _format_enrich_summary(le._counts(skipped="internet_off")) == (
            "I'm set to stay off the internet, so I can't identify songs."
        )


# ═══ The admin endpoint (DB-free: the route function itself) ════════════════


@pytest.mark.asyncio
async def test_admin_enrich_under_never_is_the_409(monkeypatch) -> None:
    from fastapi import HTTPException

    from domovoi import main

    monkeypatch.setattr(f"{ENRICHER}.enrich_library", _aexplode)
    with egress.override_policy("never"), pytest.raises(HTTPException) as ei:
        await main.admin_library_enrich()
    assert ei.value.status_code == 409
    assert ei.value.detail == egress.TURNED_OFF_REASON
    assert ei.value.headers == {"X-Domovoi-Refusal": "internet-off"}


@pytest.mark.parametrize(
    ("enabled", "online", "provider", "running", "reason"),
    [
        (False, True, True, False, "disabled"),
        (True, False, True, False, "offline"),
        (True, True, False, False, "no_provider"),
        (True, True, True, True, "running"),
    ],
)
@pytest.mark.asyncio
async def test_admin_enrich_says_why_it_did_not_queue(
    monkeypatch, enabled, online, provider, running, reason,
) -> None:
    from domovoi import main

    monkeypatch.setattr(main.settings, "library_enricher_enabled", enabled)
    monkeypatch.setattr(main.app.state, "probe", _Probe(online), raising=False)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: provider)
    monkeypatch.setattr(le, "_sweep_running", running)
    monkeypatch.setattr(f"{ENRICHER}.enrich_library", _aexplode)
    assert await main.admin_library_enrich() == {"queued": False, "reason": reason}


@pytest.mark.asyncio
async def test_admin_enrich_queues_when_it_can(monkeypatch) -> None:
    import asyncio

    from domovoi import main

    started = []

    async def _fake_sweep():
        started.append(True)
        return le._counts()

    monkeypatch.setattr(main.settings, "library_enricher_enabled", True)
    monkeypatch.setattr(main.app.state, "probe", _Probe(True), raising=False)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    monkeypatch.setattr(le, "_sweep_running", False)
    monkeypatch.setattr(f"{ENRICHER}.enrich_library", _fake_sweep)
    assert await main.admin_library_enrich() == {"queued": True, "worker": "library_enricher"}
    await asyncio.sleep(0)
    assert started == [True]
