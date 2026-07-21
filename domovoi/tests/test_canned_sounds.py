"""Tests for wake-word greeting rendering.

``_greeting_entries`` is pure (takes (text, category) rows, no DB), so most
of this runs without Postgres. A ``@requires_db`` test covers the greeting seed
via the repository.
"""

from __future__ import annotations

import pytest

from domovoi.canned_sounds import _greeting_entries
from domovoi.config import settings
from domovoi.tests.conftest import requires_db


def test_greeting_entries_resolves_name_and_prefixes():
    name = settings.bot_name
    rows = [
        ("Hey!", "generic"),
        ("{name} here.", "generic"),
        ("Sup, boss?", "funny"),
        ("{name}, reporting for duty.", "funny"),
    ]
    entries = _greeting_entries(rows)
    assert len(entries) == 4

    names = [mp3 for mp3, _, _ in entries]
    assert len(set(names)) == 4  # unique filenames

    by_text = {text: mp3 for mp3, _, text in entries}
    # {name} resolved to the configured bot name; no placeholder remains.
    assert f"{name} here." in by_text
    assert all("{name}" not in text for _, _, text in entries)
    # category drives the prefix the Pi weights by.
    assert by_text["Hey!"].startswith("greet_")
    assert not by_text["Hey!"].startswith("greet_funny_")
    assert by_text["Sup, boss?"].startswith("greet_funny_")
    # well-formed: shared stem, right suffixes, non-empty.
    for mp3, sidecar, text in entries:
        assert mp3.endswith(".mp3") and sidecar.endswith(".voice")
        assert mp3[:-len(".mp3")] == sidecar[:-len(".voice")]
        assert text.strip()


def test_greeting_entries_dedupes_collisions():
    # Same resolved text → same filename → a single entry.
    rows = [("Hey!", "generic"), ("Hey!", "generic")]
    assert len(_greeting_entries(rows)) == 1


def test_greeting_entries_empty():
    assert _greeting_entries([]) == []


@requires_db
@pytest.mark.asyncio
async def test_seed_bank_present_and_renderable(db_session):
    from domovoi.db.repositories import ClientGreetingsRepository

    rows = await ClientGreetingsRepository(db_session).all_enabled()
    cats = {category for _, category in rows}
    assert {"generic", "funny"} <= cats
    assert len(rows) >= 70  # the seed set (78), minus any rows a test disabled

    entries = _greeting_entries(rows)
    # Every clip renders placeholder-free, with a unique filename.
    assert all("{name}" not in text for _, _, text in entries)
    names = [mp3 for mp3, _, _ in entries]
    assert len(set(names)) == len(names)
