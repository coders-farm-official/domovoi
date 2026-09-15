"""The server's clock and time zone, as handed to the satellites.

Found on hardware: a satellite's log stamped ``2026-06-18T01:34:58+01:00``
in September, in a house in America/New_York. A Pi has no battery clock
and Pi OS boots in Europe/London; the provision payload's ``tz`` came from
the optional tzlocal package that nobody installs, so it was always null,
and nothing anywhere ever set the clock.

The resolver must name this host's zone on a bare install of either
platform, and ``/v1/time`` must serve what it finds.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

from domovoi import host_time


def test_env_tz_wins_and_is_shape_checked():
    assert host_time._from_env({"TZ": "America/New_York"}) == "America/New_York"
    assert host_time._from_env({"TZ": ":Europe/Paris"}) == "Europe/Paris"
    # A POSIX rule string is not a zone NAME timedatectl could take.
    assert host_time._from_env({"TZ": "EST5EDT,M3.2.0,M11.1.0"}) is None
    assert host_time._from_env({}) is None


def test_etc_timezone_is_read_when_present(tmp_path):
    f = tmp_path / "timezone"
    assert host_time._from_etc_timezone(f) is None
    f.write_text("America/Chicago\n", encoding="utf-8")
    assert host_time._from_etc_timezone(f) == "America/Chicago"
    f.write_text("not a zone!\n", encoding="utf-8")
    assert host_time._from_etc_timezone(f) is None


def test_the_localtime_symlink_names_the_zone(monkeypatch):
    monkeypatch.setattr(
        host_time.os, "readlink", lambda p: "/usr/share/zoneinfo/America/Denver"
    )
    assert host_time._from_localtime_link(Path("/etc/localtime")) == "America/Denver"
    monkeypatch.setattr(
        host_time.os, "readlink", lambda p: "../usr/share/zoneinfo/posix/Asia/Tokyo"
    )
    assert host_time._from_localtime_link(Path("/etc/localtime")) == "Asia/Tokyo"

    def not_a_link(p):
        raise OSError("not a symlink")

    monkeypatch.setattr(host_time.os, "readlink", not_a_link)
    assert host_time._from_localtime_link(Path("/etc/localtime")) is None


def test_windows_zone_ids_map_to_iana_names():
    assert host_time._from_windows("Eastern Standard Time") == "America/New_York"
    assert host_time._from_windows("GMT Standard Time") == "Europe/London"
    assert host_time._from_windows("No Such Zone") is None
    assert host_time._from_windows("") is None
    # Every value in the table is something a device can be handed.
    for zone in host_time.WINDOWS_ZONES.values():
        assert host_time.is_zone_name(zone), zone


def test_the_fixed_offset_fallback_inverts_the_sign_like_posix():
    assert host_time._fixed_offset_zone(-4 * 3600) == "Etc/GMT+4"
    assert host_time._fixed_offset_zone(9 * 3600) == "Etc/GMT-9"
    assert host_time._fixed_offset_zone(0) == "Etc/UTC"
    # Half-hour zones have no Etc/ name; better none than a wrong one.
    assert host_time._fixed_offset_zone(5 * 3600 + 1800) is None


def test_a_whole_hour_host_is_never_left_nameless(monkeypatch):
    """Strip every authoritative source; the offset fallback still answers,
    which is what keeps a Windows host with an unmapped zone from sending
    a device null."""
    for probe in ("_from_env", "_from_etc_timezone", "_from_localtime_link",
                  "_from_tzlocal", "_from_windows"):
        monkeypatch.setattr(host_time, probe, lambda: None)
    monkeypatch.setattr(host_time, "_fixed_offset_zone", lambda: "Etc/GMT+4")
    assert host_time.local_timezone_name() == "Etc/GMT+4"


def test_one_probe_raising_does_not_hide_the_next(monkeypatch):
    def registry_on_fire():
        raise RuntimeError("boom")

    monkeypatch.setattr(host_time, "_from_env", registry_on_fire)
    monkeypatch.setattr(host_time, "_from_etc_timezone", lambda: "Europe/Berlin")
    assert host_time.local_timezone_name() == "Europe/Berlin"


def test_the_first_answer_wins(monkeypatch):
    monkeypatch.setattr(host_time, "_from_env", lambda: "Asia/Tokyo")
    monkeypatch.setattr(host_time, "_from_etc_timezone", lambda: "Europe/Berlin")
    assert host_time.local_timezone_name() == "Asia/Tokyo"


def test_this_host_can_name_its_zone():
    """The whole point. Linux via /etc/timezone or the symlink, Windows via
    the registry table, and the offset fallback under both."""
    tz = host_time.local_timezone_name()
    assert tz and host_time.is_zone_name(tz)


def test_the_document_carries_what_a_device_applies(monkeypatch):
    monkeypatch.setattr(host_time, "local_timezone_name", lambda: "America/New_York")
    doc = host_time.server_time_document(now=1_789_442_730.5)
    assert doc["tz"] == "America/New_York"
    assert doc["epoch"] == 1_789_442_730.5
    assert isinstance(doc["utc_offset_sec"], int)
    # The same instant in the host's zone, offset included, for a human.
    assert doc["iso"].startswith("2026-")
    assert doc["iso"][-6] in "+-" or doc["iso"].endswith("+00:00")


def test_the_core_serves_it_open():
    from domovoi import main

    doc = asyncio.run(main.server_time())
    assert set(doc) == {"tz", "epoch", "iso", "utc_offset_sec"}
    assert any(getattr(r, "path", "") == "/v1/time" for r in main.app.routes)
    src = inspect.getsource(main.server_time)
    # No auth dependency and no DB: a device asks before it is paired, and
    # a satellite must be able to set its clock while Postgres is down.
    assert "Depends" not in src and "session_scope" not in src


def test_usb_adoption_no_longer_depends_on_an_optional_package():
    """The bug: this helper imported tzlocal, which nobody installs, caught
    the ImportError, and returned None - so every card shipped with
    ``tz: null``."""
    from web.backend.api import satellites

    src = inspect.getsource(satellites._server_tz)
    assert "from tzlocal" not in src
    assert "local_timezone_name" in src
    tz = satellites._server_tz()
    assert tz and host_time.is_zone_name(tz)
