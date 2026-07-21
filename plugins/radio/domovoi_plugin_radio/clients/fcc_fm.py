"""FCC FM Query client — the licensed-FM catalog for a US state.

The FCC publishes a public CGI endpoint at
``https://transition.fcc.gov/fcc-bin/fmq`` returning pipe-delimited text
for every licensed FM station matching the query. Slow (~10-30 s for a
populous state) but free, no auth, schema stable for years.

Field layout in the pipe-delimited response (``list=4`` output). Every
row starts with a leading ``|`` so splitting on ``|`` yields an empty
string at position 0 — stripped first. After the strip:

    0  callsign     ("W201CF")
    1  frequency    ("88.1  MHz" — note the " MHz" suffix)
    2  service      (FM | FL | FX | FB | LP1 — see filter below)
    3  channel
    4  direction
    5  (blank)
    6  class
    7  (dash)
    8  status
    9  city
    10 state
    11 country
    12 file_no      (unique-per-station; our dedup key)

Service filter: FM (full power), FL (LPFM), and FX (translators) all
transmit on 87.5–108 MHz and are tunable by a standard FM receiver;
boosters and non-FM-band services are dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator, Protocol

from domovoi_plugin_radio import USER_AGENT

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://transition.fcc.gov/fcc-bin/fmq"


@dataclass
class FmStation:
    """One parsed FM row, in the shape ready for ``radio_stations``."""

    call_sign: str
    frequency_mhz: float
    market_city: str | None
    market_state: str
    facility_id: str           # acts as the external_id for FM rows


class FccFmClient(Protocol):
    async def fetch_state(self, state_code: str) -> list[FmStation]: ...


class RealFccFmClient:
    def __init__(self, base_url: str = _DEFAULT_BASE_URL) -> None:
        self._base_url = base_url

    async def fetch_state(self, state_code: str) -> list[FmStation]:
        if not state_code or len(state_code) != 2:
            log.warning("fcc_fm: invalid state %r", state_code)
            return []

        import httpx

        params = {"state": state_code.upper(), "list": "4"}
        try:
            # The CGI endpoint is slow; this only ever runs as a
            # background job / startup hook, so a long timeout is fine.
            async with httpx.AsyncClient(
                timeout=60.0, headers={"User-Agent": USER_AGENT}
            ) as client:
                response = await client.get(self._base_url, params=params)
                response.raise_for_status()
                body = response.text
        except Exception as e:
            log.warning("fcc_fm: fetch for state %s failed: %s", state_code, e)
            return []

        return list(parse_fmq_response(body))


class FccFmStubClient:
    """Deterministic stub — a small fixed set per state so the upsert
    path is testable without hitting the FCC."""

    async def fetch_state(self, state_code: str) -> list[FmStation]:
        sc = state_code.upper()
        if not sc or len(sc) != 2:
            return []
        return [
            FmStation(
                call_sign=f"K{sc}A",
                frequency_mhz=90.3,
                market_city="Test City",
                market_state=sc,
                facility_id=f"stub-{sc}-1",
            ),
            FmStation(
                call_sign=f"K{sc}B",
                frequency_mhz=97.5,
                market_city="Test City",
                market_state=sc,
                facility_id=f"stub-{sc}-2",
            ),
        ]


_FM_BAND_SERVICES = {"FM", "FL", "FX"}


def parse_fmq_response(body: str) -> Iterator[FmStation]:
    """Parse FCC FMQ pipe-delimited output and yield FmStation rows.

    Resilient by design — the FCC's text dump occasionally has rows
    missing a field or with trailing whitespace; anything unparseable is
    skipped rather than aborting the whole import.
    """
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("|"):
            line = line[1:]
        if not line.strip():
            continue
        lower = line.lower()
        if lower.startswith("callsign|") or lower.startswith("call sign|"):
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 13:            # need file_no at index 12
            continue

        call_sign = parts[0]
        freq_raw = parts[1]
        service = parts[2].upper()
        city = parts[9]
        state = parts[10][:2].upper() if parts[10] else ""
        file_no = parts[12]

        if service not in _FM_BAND_SERVICES:
            continue
        if not call_sign or not freq_raw or not state:
            continue

        # The frequency column carries a " MHz" suffix in the live
        # response — strip non-numeric trailing tokens before float().
        freq_clean = freq_raw.split()[0] if freq_raw else ""
        try:
            frequency_mhz = float(freq_clean)
        except ValueError:
            continue
        if not (87.0 <= frequency_mhz <= 108.0):
            # FM band worldwide; the FCC's data occasionally contains junk.
            continue

        # file_no isn't strictly unique across re-licenses but is unique
        # within an active row; suffix with frequency for extra safety.
        facility_id = f"fcc-{file_no or call_sign}-{frequency_mhz}"

        yield FmStation(
            call_sign=call_sign,
            frequency_mhz=frequency_mhz,
            market_city=city or None,
            market_state=state,
            facility_id=facility_id,
        )


_client: FccFmClient | None = None


def get_fcc_fm_client(*, use_stubs: bool = False) -> FccFmClient:
    global _client
    if _client is None:
        _client = FccFmStubClient() if use_stubs else RealFccFmClient()
    return _client


def _set_fcc_fm_client_for_tests(client: FccFmClient | None) -> None:
    global _client
    _client = client
