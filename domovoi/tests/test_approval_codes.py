"""Approval codes: six digits, wherever they are minted (SAT-5).

Two ends generate them and they must agree on the shape, because the
customer types whichever one they were given into the same box:

* the setup portal on the device (``satellite.portal_transport``), for a
  satellite that onboarded over its own Wi-Fi AP;
* the core (``domovoi.satellite_media.overlay``), for a satellite that
  parked for approval without bringing a code of its own.

No database and no radio — these are pure generators, so this file runs
everywhere and can never hide behind ``requires_db``.
"""

from __future__ import annotations

import re

from domovoi.satellite_media import overlay
from satellite import portal_transport

SIX_DIGITS = re.compile(r"\A[0-9]{6}\Z")


def test_both_ends_agree_on_the_number_of_digits() -> None:
    assert overlay.APPROVAL_CODE_DIGITS == portal_transport.APPROVAL_CODE_DIGITS == 6


def test_the_core_mints_six_digits() -> None:
    for _ in range(200):
        assert SIX_DIGITS.match(overlay.generate_approval_code())


def test_the_portal_mints_six_digits() -> None:
    for _ in range(200):
        assert SIX_DIGITS.match(portal_transport.generate_approval_code())


def test_a_leading_zero_survives() -> None:
    """A code drawn low must still be six characters — the customer types
    the zero, and a format that dropped it would hand them a code the
    server can never match."""

    class _Low:
        """Always draws the smallest value on offer."""

        def randbelow(self, n: int) -> int:
            return 0

        def choice(self, seq):
            return seq[0]

    assert overlay.generate_approval_code(rng=_Low()) == "000000"


def test_codes_vary() -> None:
    """A constant would be a credential in name only."""
    assert len({overlay.generate_approval_code() for _ in range(50)}) > 1
    assert len({portal_transport.generate_approval_code() for _ in range(50)}) > 1
