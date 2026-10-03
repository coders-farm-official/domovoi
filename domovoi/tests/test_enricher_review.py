"""Song recognition: the 2026-10-03 review fixes.

* A key AcoustID refuses (error 4 / 17, an Beelink-style USER key) is like
  no key for the rest of the sweep: AcoustID isn't asked again and Shazam's
  answer decides. Before, every Shazam no-match counted as an error, five
  of them aborted the sweep at the same place every time, and the same
  audio went to Shazam again on every sweep.
* A lookup that one provider failed while the other answered is not
  decided (not stamped) but doesn't count toward stopping the sweep.
* The legacy rows (stamped before V020 with nothing to show a provider
  answered) are put back in the queue only once a provider has actually
  answered for some file: a refused key, a provider that can't run, or a
  line that is down leave every one of them exactly as it was — the
  Beelink's unanswered box changes nothing at boot.
* The Shazam add-on is imported in a throwaway interpreter first: a build
  that crashes at import (shazamio-core on CPython 3.14) is skipped, not
  allowed to take the core down.
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from domovoi.tests.test_enricher_recovery import _row, _seed_live_like, lane  # noqa: F401  (fixture)
from domovoi.workers import library_enricher as le
from domovoi.workers.library_enricher import EnrichmentResult, Lookup, Verdict, enrich_library

ENRICHER = "domovoi.workers.library_enricher"


@pytest.fixture
def refused_key(monkeypatch):
    """A key AcoustID refuses, through the REAL _enrich_via_acoustid."""
    import acoustid

    monkeypatch.setattr(f"{ENRICHER}.settings.acoustid_api_key", "user-key")
    monkeypatch.setattr(le, "_key_hint_logged", False)
    asked: list[str] = []

    def refused(_key, path):
        asked.append(Path(path).name)
        raise acoustid.WebServiceError(
            "invalid API key", '{"status": "error", "error": {"code": 4, "message": "invalid API key"}}'
        )

    with patch("acoustid.match", refused):
        yield asked


def _fake_shazam(monkeypatch, recognize) -> list[str]:
    asked: list[str] = []
    mod = types.ModuleType("shazamio")

    class Shazam:
        async def recognize(self, path):
            asked.append(Path(path).name)
            return await recognize(Path(path))

    mod.Shazam = Shazam
    monkeypatch.setitem(sys.modules, "shazamio", mod)
    return asked


# ─── DB-free ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refused_key_is_like_no_key(refused_key, tmp_path, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=ENRICHER):
        out = await le._enrich_via_acoustid(tmp_path / "x.mp3")
        again = await le._enrich_via_acoustid(tmp_path / "y.mp3")
    assert out.verdict is Verdict.NOT_ASKED and out.unusable is True
    assert again.verdict is Verdict.NOT_ASKED
    assert len([r for r in caplog.records if "APPLICATION key" in r.getMessage()]) == 1


@pytest.mark.asyncio
async def test_with_shazam_a_refused_key_lets_shazam_decide(refused_key, monkeypatch, tmp_path) -> None:
    async def nothing(_p):
        return {}

    _fake_shazam(monkeypatch, nothing)
    providers = le._Providers()
    first = await le._enrich_one(tmp_path / "a.mp3", providers)
    assert first.verdict is Verdict.NO_MATCH            # a genuine no-match now
    assert providers.acoustid is False                  # not asked again this sweep
    second = await le._enrich_one(tmp_path / "b.mp3", providers)
    assert second.verdict is Verdict.NO_MATCH
    assert refused_key == ["a.mp3"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("ac", "sh", "transient"), [
    (Lookup(Verdict.ERROR, transient=True), Lookup(Verdict.NO_MATCH), False),   # Shazam answered
    (Lookup(Verdict.NO_MATCH), Lookup(Verdict.ERROR, transient=True), False),   # AcoustID answered
    (Lookup(Verdict.ERROR, transient=True), Lookup(Verdict.NOT_ASKED), True),   # nobody answered
    (Lookup(Verdict.ERROR, transient=True), Lookup(Verdict.ERROR, transient=True), True),
])
async def test_an_error_stops_the_sweep_only_when_nobody_answered(monkeypatch, ac, sh, transient) -> None:
    async def _ac(_p):
        return ac

    async def _sh(_p):
        return sh

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", _ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _sh)
    out = await le._enrich_one(Path("x.mp3"), le._Providers())
    assert out.verdict is Verdict.ERROR                 # still not decided, never stamped
    assert out.transient is transient


@pytest.mark.asyncio
async def test_a_shazamio_that_crashes_at_import_is_skipped(monkeypatch, tmp_path) -> None:
    monkeypatch.delitem(sys.modules, "shazamio", raising=False)
    monkeypatch.setattr(le, "_shazam_import_ok", None)
    monkeypatch.setattr(le.importlib.util, "find_spec", lambda name: object() if name == "shazamio" else None)
    probes: list[int] = []

    def crashed() -> bool:
        probes.append(1)
        return False

    monkeypatch.setattr(le, "_probe_shazamio_import", crashed)
    out = await le._enrich_via_shazam(tmp_path / "x.mp3")
    assert out.verdict is Verdict.NOT_ASKED and out.unusable is True
    assert "shazamio" not in sys.modules                 # never imported in this process
    await le._enrich_via_shazam(tmp_path / "y.mp3")
    assert probes == [1]                                  # probed once per process
    monkeypatch.setattr(f"{ENRICHER}.settings.acoustid_api_key", "")
    assert le.provider_available() is False


def test_the_import_probe_runs_a_throwaway_interpreter(monkeypatch) -> None:
    import subprocess

    seen: list[list[str]] = []

    def fake_run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, -11, b"", b"Segmentation fault")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert le._probe_shazamio_import() is False
    assert seen == [[sys.executable, "-c", "import shazamio"]]


# ─── the sweep (DB) ───────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_refused_key_without_shazam_touches_nothing(lane, refused_key, monkeypatch, tmp_path) -> None:  # noqa: F811
    """The Beelink if its key is a user key: the sweep stops at once, and
    every legacy row stays exactly as it was (no requeue, nothing
    stamped)."""
    ids = await _seed_live_like(lane, tmp_path)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    monkeypatch.setitem(sys.modules, "shazamio", None)       # not installed
    before = {k: await _row(lane, v) for k, v in ids.items()}
    counts = await enrich_library()
    assert counts["aborted"] is True and counts["requeued_legacy"] == 0
    assert counts["matched"] == counts["no_match"] == 0
    assert len(refused_key) == 1                             # one try, then unusable
    for k, v in ids.items():
        assert await _row(lane, v) == before[k], k


@requires_db
@pytest.mark.asyncio
async def test_a_refused_key_with_shazam_makes_progress(lane, refused_key, monkeypatch, tmp_path) -> None:  # noqa: F811
    """The ops review's stall: a refused key plus a Shazam that misses the
    first five files used to abort at the same place every sweep."""
    paths = []
    for i in range(8):
        p = tmp_path / f"t{i}.mp3"
        p.write_bytes(b"")
        paths.append(p)
        await lane.execute(text("INSERT INTO library_tracks (file_path, title) VALUES (:fp, :t)"),
                           {"fp": str(p), "t": f"file {i}"})
    await lane.commit()
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    misses = {p.name for p in paths[:5]}

    async def recognize(path):
        if path.name in misses:
            return {}
        return {"track": {"title": f"Song {path.stem}", "subtitle": "Artist"}}

    shazam_asked = _fake_shazam(monkeypatch, recognize)
    counts = await enrich_library()
    assert counts["aborted"] is False
    assert (counts["no_match"], counts["matched"]) == (5, 3)
    assert len(refused_key) == 1 and len(shazam_asked) == 8
    outcomes = (await lane.execute(text(
        "SELECT enrich_outcome FROM library_tracks ORDER BY id"))).scalars().all()
    assert outcomes == ["no_match"] * 5 + ["matched"] * 3
    # A second sweep finds nothing left to send.
    again = await enrich_library()
    assert again["scanned"] == 0 and len(shazam_asked) == 8


@requires_db
@pytest.mark.asyncio
async def test_legacy_rows_are_requeued_only_after_a_real_answer(lane, monkeypatch, tmp_path) -> None:  # noqa: F811
    ids = await _seed_live_like(lane, tmp_path)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)

    async def down(_p):
        return Lookup(Verdict.ERROR, detail="down", transient=True)

    async def not_installed(_p):
        return Lookup(Verdict.NOT_ASKED, detail="shazamio not installed")

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", down)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", not_installed)
    counts = await enrich_library()
    assert counts["requeued_legacy"] == 0 and counts["errors"] == 3
    legacy = await _row(lane, ids["legacy_unanswered"])
    assert legacy.enriched_at is not None and legacy.enrich_outcome is None   # untouched

    async def found(p):
        return Lookup(Verdict.MATCH, result=EnrichmentResult(
            title="Catalog", artist="Filled", musicbrainz_recording_id=f"mb-{p.stem}", source="acoustid"))

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", found)
    counts = await enrich_library()
    assert counts["requeued_legacy"] == 2 and counts["matched"] == 3
    legacy = await _row(lane, ids["legacy_unanswered"])
    # Fill-only: the title it had stays, the empty artist is filled.
    assert (legacy.title, legacy.artist, legacy.enrich_outcome) == ("01 - track", "Filled", "matched")
