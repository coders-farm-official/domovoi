"""V002 recency contract — deliberately DB-FREE so it can never skip.

The DB-backed behavior (play persists, the trim reclaims, pagination) is
covered in ``test_web_api.py`` under ``requires_db``, which SKIPS on a box
without Postgres. Everything checkable without a database lives here
instead: the column list and the row mapper agreeing on arity and order,
the response model exposing the new fields, and the migration actually
declaring what the queries read.

That split is the lesson from two previously-shipped-broken tests: a
green run that skipped every assertion proves nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from domovoi_plugin_radio.web import (
    _RECENT_LIMIT,
    _STATION_COLUMNS,
    RadioStation,
    RadioStationPlay,
    _row_to_station,
)

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "V002__recent_plays.sql"
).read_text(encoding="utf-8")

STATION_COLUMN_NAMES = [
    c.strip() for c in _STATION_COLUMNS.replace("\n", " ").split(",") if c.strip()
]


def test_station_columns_end_with_the_v002_pair() -> None:
    """``_row_to_station`` reads by positional index, so the ORDER of the
    shared column list is load-bearing — appending anywhere but the end
    silently shifts every field after it."""
    assert STATION_COLUMN_NAMES[-2:] == ["last_played_at", "created_by_play"]


def test_row_mapper_consumes_every_column() -> None:
    """Build a synthetic row of exactly the declared width and assert the
    mapper reads the last index. A short row raises IndexError here rather
    than 500ing at runtime."""
    played = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    row: list[object] = [
        1, "KEXP", "online", "http://kexp.example/s.mp3", None,
        None, None, "KEXP", "uuid-kexp",
        "US", "en", ["indie"], True,
        180, None,
        None, None,
        None, None, None,
        played, True,
    ]
    assert len(row) == len(STATION_COLUMN_NAMES), (
        f"synthetic row has {len(row)} values for "
        f"{len(STATION_COLUMN_NAMES)} declared columns"
    )
    station = _row_to_station(row)
    assert station.last_played_at == played
    assert station.created_by_play is True


def test_response_model_exposes_recency_fields() -> None:
    fields = RadioStation.model_fields
    assert "last_played_at" in fields
    assert "created_by_play" in fields
    # Defaults matter: every other read endpoint builds this model from
    # rows that predate a play, and must not require the new fields.
    fresh = RadioStation(id=1, name="KEXP")
    assert fresh.last_played_at is None
    assert fresh.created_by_play is False


def test_play_request_accepts_both_shapes() -> None:
    """Either an existing row id, or the directory-hit shape. Both are
    optional at the schema level; ``POST /play`` enforces the pairing so
    the error message can explain which half is missing."""
    by_id = RadioStationPlay(station_id=7)
    assert by_id.station_id == 7 and by_id.name is None

    as_hit = RadioStationPlay(
        name="KEXP", stream_url="http://kexp.example/s.mp3",
        external_id="uuid-kexp", tags=["indie"],
    )
    assert as_hit.station_id is None
    assert as_hit.source == "online"        # defaulted, not required
    assert as_hit.tags == ["indie"]


def test_migration_declares_what_the_queries_read() -> None:
    sql = MIGRATION.lower()
    assert "alter table radio_stations" in sql
    assert "add column last_played_at  timestamptz" in sql
    assert "add column created_by_play boolean not null default false" in sql
    # The Recent read and the trim both rely on this index; its predicate
    # must match the queries' ``last_played_at IS NOT NULL``.
    assert "idx_radio_stations_recent" in sql
    assert "where last_played_at is not null" in sql


def test_recent_limit_is_the_product_decision() -> None:
    """Ten, and no history beyond that — the trim enforces it, so this is
    the one knob that changes the strip's size."""
    assert _RECENT_LIMIT == 10
