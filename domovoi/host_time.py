"""The Domovoi server's own clock and time zone, for the satellites.

A satellite has no battery-backed clock and, out of the box, no idea where
it is: a stock Pi OS image boots in Europe/London with the date the image
was built. Nothing on the LAN corrects that unless the house has internet
for NTP - and the time zone is never corrected by anything. The result is
a room whose "what time is it" is off by five hours and whose log lines
carry a date from June.

The server is the authority on both. This module answers "what zone is
this host in, and what time is it" in a form a device can apply directly:
an IANA zone name (what ``timedatectl set-timezone`` takes) and a Unix
timestamp. Served by ``GET /v1/time`` in the core and baked into prepared
media by the builder; stdlib only, so the web process can import it too.

Finding the zone NAME is the hard part - Python knows the current offset
but not the region behind it - and the answer is platform-specific. The
order below is most-authoritative first:

1. ``TZ`` in the environment, when it names a zone.
2. ``/etc/timezone`` (Debian and friends write it; systemd does not).
3. Where ``/etc/localtime`` points, which is what ``timedatectl`` set.
4. The optional ``tzlocal`` package, if someone installed it.
5. The Windows registry zone, mapped through the CLDR table below.
6. A fixed ``Etc/GMT+N`` zone from the current offset - right today,
   wrong across the next DST change, but better than London.
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# IANA names: "Region/City", "Etc/GMT+5", "UTC". Nothing else is handed
# to a device to pass to timedatectl.
_ZONE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}$")

_ETC_TIMEZONE = Path("/etc/timezone")
_ETC_LOCALTIME = Path("/etc/localtime")

# Windows zone id -> IANA zone, per Unicode CLDR windowsZones (territory
# 001). Modern canonical names where CLDR still lists a legacy alias
# (Asia/Kolkata rather than Asia/Calcutta): the device checks the name
# against its own tzdata before applying it, and Debian's tzdata carries
# both spellings, so either would work - the modern one is what an operator
# expects to read back from `timedatectl`.
WINDOWS_ZONES: dict[str, str] = {
    "Dateline Standard Time": "Etc/GMT+12",
    "UTC-11": "Etc/GMT+11",
    "Aleutian Standard Time": "America/Adak",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Marquesas Standard Time": "Pacific/Marquesas",
    "Alaskan Standard Time": "America/Anchorage",
    "UTC-09": "Etc/GMT+9",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "UTC-08": "Etc/GMT+8",
    "Pacific Standard Time": "America/Los_Angeles",
    "US Mountain Standard Time": "America/Phoenix",
    "Mountain Standard Time (Mexico)": "America/Mazatlan",
    "Mountain Standard Time": "America/Denver",
    "Yukon Standard Time": "America/Whitehorse",
    "Central America Standard Time": "America/Guatemala",
    "Central Standard Time": "America/Chicago",
    "Easter Island Standard Time": "Pacific/Easter",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Canada Central Standard Time": "America/Regina",
    "SA Pacific Standard Time": "America/Bogota",
    "Eastern Standard Time (Mexico)": "America/Cancun",
    "Eastern Standard Time": "America/New_York",
    "Haiti Standard Time": "America/Port-au-Prince",
    "Cuba Standard Time": "America/Havana",
    "US Eastern Standard Time": "America/Indiana/Indianapolis",
    "Turks And Caicos Standard Time": "America/Grand_Turk",
    "Paraguay Standard Time": "America/Asuncion",
    "Atlantic Standard Time": "America/Halifax",
    "Venezuela Standard Time": "America/Caracas",
    "Central Brazilian Standard Time": "America/Cuiaba",
    "SA Western Standard Time": "America/La_Paz",
    "Pacific SA Standard Time": "America/Santiago",
    "Newfoundland Standard Time": "America/St_Johns",
    "Tocantins Standard Time": "America/Araguaina",
    "E. South America Standard Time": "America/Sao_Paulo",
    "SA Eastern Standard Time": "America/Cayenne",
    "Argentina Standard Time": "America/Argentina/Buenos_Aires",
    "Montevideo Standard Time": "America/Montevideo",
    "Magallanes Standard Time": "America/Punta_Arenas",
    "Saint Pierre Standard Time": "America/Miquelon",
    "Bahia Standard Time": "America/Bahia",
    "UTC-02": "Etc/GMT+2",
    "Greenland Standard Time": "America/Nuuk",
    "Azores Standard Time": "Atlantic/Azores",
    "Cape Verde Standard Time": "Atlantic/Cape_Verde",
    "UTC": "Etc/UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "Sao Tome Standard Time": "Africa/Sao_Tome",
    "Morocco Standard Time": "Africa/Casablanca",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris",
    "Central European Standard Time": "Europe/Warsaw",
    "W. Central Africa Standard Time": "Africa/Lagos",
    "GTB Standard Time": "Europe/Bucharest",
    "Middle East Standard Time": "Asia/Beirut",
    "Egypt Standard Time": "Africa/Cairo",
    "E. Europe Standard Time": "Europe/Chisinau",
    "West Bank Standard Time": "Asia/Hebron",
    "South Africa Standard Time": "Africa/Johannesburg",
    "FLE Standard Time": "Europe/Kyiv",
    "Israel Standard Time": "Asia/Jerusalem",
    "South Sudan Standard Time": "Africa/Juba",
    "Kaliningrad Standard Time": "Europe/Kaliningrad",
    "Sudan Standard Time": "Africa/Khartoum",
    "Libya Standard Time": "Africa/Tripoli",
    "Namibia Standard Time": "Africa/Windhoek",
    "Jordan Standard Time": "Asia/Amman",
    "Arabic Standard Time": "Asia/Baghdad",
    "Syria Standard Time": "Asia/Damascus",
    "Turkey Standard Time": "Europe/Istanbul",
    "Arab Standard Time": "Asia/Riyadh",
    "Belarus Standard Time": "Europe/Minsk",
    "Russian Standard Time": "Europe/Moscow",
    "E. Africa Standard Time": "Africa/Nairobi",
    "Volgograd Standard Time": "Europe/Volgograd",
    "Iran Standard Time": "Asia/Tehran",
    "Arabian Standard Time": "Asia/Dubai",
    "Astrakhan Standard Time": "Europe/Astrakhan",
    "Azerbaijan Standard Time": "Asia/Baku",
    "Russia Time Zone 3": "Europe/Samara",
    "Mauritius Standard Time": "Indian/Mauritius",
    "Saratov Standard Time": "Europe/Saratov",
    "Georgian Standard Time": "Asia/Tbilisi",
    "Caucasus Standard Time": "Asia/Yerevan",
    "Afghanistan Standard Time": "Asia/Kabul",
    "West Asia Standard Time": "Asia/Tashkent",
    "Ekaterinburg Standard Time": "Asia/Yekaterinburg",
    "Pakistan Standard Time": "Asia/Karachi",
    "Qyzylorda Standard Time": "Asia/Qyzylorda",
    "India Standard Time": "Asia/Kolkata",
    "Sri Lanka Standard Time": "Asia/Colombo",
    "Nepal Standard Time": "Asia/Kathmandu",
    "Central Asia Standard Time": "Asia/Bishkek",
    "Bangladesh Standard Time": "Asia/Dhaka",
    "Omsk Standard Time": "Asia/Omsk",
    "Myanmar Standard Time": "Asia/Yangon",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Altai Standard Time": "Asia/Barnaul",
    "W. Mongolia Standard Time": "Asia/Hovd",
    "North Asia Standard Time": "Asia/Krasnoyarsk",
    "N. Central Asia Standard Time": "Asia/Novosibirsk",
    "Tomsk Standard Time": "Asia/Tomsk",
    "China Standard Time": "Asia/Shanghai",
    "North Asia East Standard Time": "Asia/Irkutsk",
    "Singapore Standard Time": "Asia/Singapore",
    "W. Australia Standard Time": "Australia/Perth",
    "Taipei Standard Time": "Asia/Taipei",
    "Ulaanbaatar Standard Time": "Asia/Ulaanbaatar",
    "Aus Central W. Standard Time": "Australia/Eucla",
    "Transbaikal Standard Time": "Asia/Chita",
    "Tokyo Standard Time": "Asia/Tokyo",
    "North Korea Standard Time": "Asia/Pyongyang",
    "Korea Standard Time": "Asia/Seoul",
    "Yakutsk Standard Time": "Asia/Yakutsk",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "AUS Central Standard Time": "Australia/Darwin",
    "E. Australia Standard Time": "Australia/Brisbane",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "West Pacific Standard Time": "Pacific/Port_Moresby",
    "Tasmania Standard Time": "Australia/Hobart",
    "Vladivostok Standard Time": "Asia/Vladivostok",
    "Lord Howe Standard Time": "Australia/Lord_Howe",
    "Bougainville Standard Time": "Pacific/Bougainville",
    "Russia Time Zone 10": "Asia/Srednekolymsk",
    "Magadan Standard Time": "Asia/Magadan",
    "Norfolk Standard Time": "Pacific/Norfolk",
    "Sakhalin Standard Time": "Asia/Sakhalin",
    "Central Pacific Standard Time": "Pacific/Guadalcanal",
    "Russia Time Zone 11": "Asia/Kamchatka",
    "New Zealand Standard Time": "Pacific/Auckland",
    "UTC+12": "Etc/GMT-12",
    "Fiji Standard Time": "Pacific/Fiji",
    "Chatham Islands Standard Time": "Pacific/Chatham",
    "UTC+13": "Etc/GMT-13",
    "Tonga Standard Time": "Pacific/Tongatapu",
    "Samoa Standard Time": "Pacific/Apia",
    "Line Islands Standard Time": "Pacific/Kiritimati",
}


def is_zone_name(value: Any) -> bool:
    """Shaped like an IANA zone name. Existence is the device's check -
    the host may have no tzdata at all (Windows without the package)."""
    return (
        isinstance(value, str)
        and bool(_ZONE_NAME_RE.match(value))
        and value != "localtime"
    )


def _from_env(environ=os.environ) -> str | None:
    tz = (environ.get("TZ") or "").strip()
    # A leading colon is the POSIX "this is a file" form.
    tz = tz[1:] if tz.startswith(":") else tz
    return tz if is_zone_name(tz) else None


def _from_etc_timezone(path: Path = _ETC_TIMEZONE) -> str | None:
    try:
        tz = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return tz if is_zone_name(tz) else None


def _from_localtime_link(path: Path = _ETC_LOCALTIME) -> str | None:
    """``/etc/localtime -> /usr/share/zoneinfo/America/New_York``. Take the
    part after ``zoneinfo/``; anything else (a copied file, a relative link
    into somewhere odd) is not an answer."""
    try:
        target = os.readlink(path)
    except (OSError, ValueError):
        return None
    target = str(target).replace("\\", "/")
    marker = "zoneinfo/"
    if marker not in target:
        return None
    tz = target.split(marker, 1)[1]
    # posix/ and right/ are alternate trees under zoneinfo on some distros.
    for prefix in ("posix/", "right/"):
        if tz.startswith(prefix):
            tz = tz[len(prefix):]
    return tz if is_zone_name(tz) else None


def _from_tzlocal() -> str | None:
    try:
        from tzlocal import get_localzone_name  # type: ignore[import-not-found]
    except Exception:      # noqa: BLE001 - optional package, any failure = absent
        return None
    try:
        tz = get_localzone_name()
    except Exception:      # noqa: BLE001
        return None
    return tz if is_zone_name(tz) else None


def windows_zone_id() -> str | None:
    """The registry's zone id (``Eastern Standard Time``), Windows only."""
    if sys.platform != "win32":
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "TimeZoneKeyName")
    except Exception:      # noqa: BLE001 - a missing key is just "unknown"
        return None
    return str(value).strip().rstrip("\x00") or None


def _from_windows(zone_id: str | None = None) -> str | None:
    zone_id = zone_id if zone_id is not None else windows_zone_id()
    if not zone_id:
        return None
    return WINDOWS_ZONES.get(zone_id)


def _fixed_offset_zone(offset_seconds: int | None = None) -> str | None:
    """``Etc/GMT-5`` for UTC+5 - the sign is inverted, by POSIX tradition.
    Whole hours only; a half-hour zone with no name is left unnamed."""
    if offset_seconds is None:
        off = datetime.now().astimezone().utcoffset()
        offset_seconds = int(off.total_seconds()) if off is not None else 0
    if offset_seconds % 3600:
        return None
    hours = offset_seconds // 3600
    if hours == 0:
        return "Etc/UTC"
    return f"Etc/GMT{'-' if hours > 0 else '+'}{abs(hours)}"


def local_timezone_name() -> str | None:
    """This host's IANA zone name, or None when nothing can say."""
    for probe in (
        _from_env, _from_etc_timezone, _from_localtime_link,
        _from_tzlocal, _from_windows, _fixed_offset_zone,
    ):
        try:
            tz = probe()
        except Exception:      # noqa: BLE001 - one bad probe must not hide the rest
            tz = None
        if tz:
            return tz
    return None


def server_time_document(now: float | None = None) -> dict[str, Any]:
    """What ``GET /v1/time`` returns and a device applies.

    ``epoch`` is the value to set the clock from; ``tz`` the zone to set;
    ``iso`` and ``utc_offset_sec`` are for a human reading the response.
    """
    now = time.time() if now is None else now
    local = datetime.fromtimestamp(now).astimezone()
    off = local.utcoffset()
    return {
        "tz": local_timezone_name(),
        "epoch": now,
        "iso": local.isoformat(timespec="seconds"),
        "utc_offset_sec": int(off.total_seconds()) if off is not None else 0,
    }
