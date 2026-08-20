"""Pairing codes: entropy, forgiving input, and a hash worth trusting."""

from __future__ import annotations

from datetime import timedelta

import pytest

from domovoi_plugin_ltl_remote import pairing
from domovoi_plugin_ltl_remote.pairing import PairingError


def test_wordlist_carries_at_least_eight_bits_per_word():
    """Eight words only means 64 bits if the list has 256 entries to
    choose from."""
    assert len(pairing._WORDS) >= 256
    assert len(set(pairing._WORDS[:256])) == 256


def test_generated_codes_are_eight_known_words():
    code = pairing.generate_code()
    words = code.split("-")
    assert len(words) == pairing.CODE_WORDS
    assert all(w in pairing._WORDS[:256] for w in words)


def test_generated_codes_differ():
    """Not a proof of entropy, but it does catch a seeded or constant
    generator, which is the failure that would matter."""
    assert len({pairing.generate_code() for _ in range(64)}) > 60


@pytest.mark.parametrize(
    "typed",
    [
        "maple heron brick oak fern dawn owl river",
        "MAPLE-HERON-BRICK-OAK-FERN-DAWN-OWL-RIVER",
        "  maple  heron,brick_oak.fern dawn owl river  ",
        "Maple-Heron-Brick-Oak-Fern-Dawn-Owl-River\n",
    ],
)
def test_normalization_forgives_how_people_actually_type(typed):
    """Someone is retyping eight words off a screen. Spaces instead of
    hyphens, a capital first letter, and a trailing newline from a paste
    must all still pair."""
    assert pairing.normalize_code(typed) == (
        "maple-heron-brick-oak-fern-dawn-owl-river"
    )


def test_hash_is_stable_across_typing_variants():
    canonical = "maple-heron-brick-oak-fern-dawn-owl-river"
    assert pairing.hash_code(canonical) == pairing.hash_code(canonical.upper())
    assert pairing.hash_code(canonical) == pairing.hash_code(
        canonical.replace("-", "  ")
    )


def test_hash_differs_between_codes():
    a = pairing.hash_code("maple-heron-brick-oak-fern-dawn-owl-river")
    b = pairing.hash_code("maple-heron-brick-oak-fern-dawn-owl-raven")
    assert a != b
    assert len(a) == 64


def test_hash_is_domain_separated():
    """The label in front of the code stops a hash from this scheme
    matching one computed anywhere else over the same words."""
    import hashlib

    code = "maple-heron-brick-oak-fern-dawn-owl-river"
    assert pairing.hash_code(code) != hashlib.sha256(code.encode()).hexdigest()


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "maple-heron",
        "maple-heron-brick-oak-fern-dawn-owl-river-extra",
        "maple-heron-brick-oak-fern-dawn-owl-zzzz",
        None,
        12345,
    ],
)
def test_bad_codes_are_refused(bad):
    with pytest.raises(PairingError):
        pairing.normalize_code(bad)


def test_a_typo_names_the_offending_word():
    """"Invalid code" sends someone back to square one; naming the word
    turns it into a one-character fix."""
    with pytest.raises(PairingError, match="zzzz"):
        pairing.normalize_code("maple-heron-brick-oak-fern-dawn-owl-zzzz")


# ─── enrollment ──────────────────────────────────────────────────────────


def test_enrollment_hashes_its_own_code():
    enrollment = pairing.new_enrollment()
    assert enrollment.code_hash == pairing.hash_code(enrollment.code)
    assert not enrollment.expired


def test_an_expired_enrollment_reports_itself():
    assert pairing.new_enrollment(ttl=timedelta(seconds=-1)).expired


def test_the_registration_payload_never_carries_the_code():
    """This is the load-bearing property of the whole pairing design: a
    dump of LTL's pending table must not be replayable into a claim."""
    enrollment = pairing.new_enrollment()
    payload = enrollment.registration_payload(
        dh_pub_b64="ZGg", sig_pub_b64="c2ln",
        fingerprint="AAAA BBBB", hostname="hearth",
    )
    serialized = repr(payload)
    assert enrollment.code not in serialized
    for word in enrollment.code.split("-"):
        assert f'"{word}"' not in serialized
    assert payload["code_hash"] == enrollment.code_hash


def test_device_fingerprints_are_short_and_key_specific():
    from domovoi_plugin_ltl_remote import crypto

    a, b = crypto.generate_keypair(), crypto.generate_keypair()
    fp = pairing.device_fingerprint(a.public_raw)
    assert len(fp.split(" ")) == 4
    assert fp != pairing.device_fingerprint(b.public_raw)
    assert fp == pairing.device_fingerprint(a.public_raw)
