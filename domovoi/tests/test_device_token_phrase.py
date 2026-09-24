"""The household device token is a phrase a person can read out — or
anything printable they chose instead — and wrong guesses are slowed down.

Three halves that only make sense together. The token dropped from 256 bits
of hex to a 64-bit eight-word phrase so somebody can say it across a room
to a phone; an admin may replace that with any printable ASCII of 12
characters or more, which is why the alphabet lives at the SET endpoint and
not in the transports; and both are only comfortable because presenting a
wrong one costs an exponential per-source backoff. Test all three or none.

Everything here is DB-FREE: the shape and alphabet tests are pure, the
normalisation tests drive ``validate_device_token`` over a stub session
that answers with one stored hash, and the gate tests go through
``auth_testkit``'s mini app wearing the real ``require_device``.
"""

from __future__ import annotations

import base64
import re
import secrets
import urllib.parse

import pytest

from domovoi import admin_auth
from domovoi.tests.auth_testkit import (
    HEADER,
    bearer,
    install_fake_db,
    make_request,
    mini_client,
)

# 8 lowercase words joined by single hyphens, nothing else.
PHRASE = re.compile(r"^[a-z]+(?:-[a-z]+){7}$")

# What a 256-bit hex token looked like before this change. Every install
# that booted before it still has one of these.
LEGACY_HEX = "a3f0" * 16


# ═══ The phrase itself ════════════════════════════════════════════════════


def test_the_word_bank_still_buys_eight_bits_a_word() -> None:
    """The entropy claim in one assertion, mirroring the module-level
    assert the setup code has always carried. 256 DISTINCT words is the
    part that is easy to lose: a duplicate in the first 256 quietly makes
    the draw non-uniform and costs real bits."""
    bank = admin_auth._WORDS[:256]
    assert len(admin_auth._WORDS) >= 256
    assert len(bank) == 256
    assert len(set(bank)) == 256, "a duplicate word costs entropy"
    assert admin_auth.DEVICE_TOKEN_WORDS == 8
    # 256 choices per word, 8 words → 2**64.
    assert 256 ** admin_auth.DEVICE_TOKEN_WORDS == 2 ** 64
    # The setup code Kamron already lives with is the same strength — that
    # is the whole argument for this being enough.
    assert admin_auth.generate_setup_code().count("-") == 7


def test_generated_tokens_are_eight_word_phrases_from_the_bank() -> None:
    bank = set(admin_auth._WORDS[:256])
    seen = set()
    for _ in range(50):
        token = admin_auth.generate_device_token()
        assert PHRASE.match(token), token
        words = token.split("-")
        assert len(words) == 8
        assert set(words) <= bank
        seen.add(token)
    # 50 draws from 2**64 that collide would mean the generator is broken.
    assert len(seen) == 50


def test_a_generated_token_is_already_canonical() -> None:
    for _ in range(20):
        token = admin_auth.generate_device_token()
        assert admin_auth.normalize_device_token(token) == token


# ═══ The three transports it has to survive ═══════════════════════════════

# RFC 9110 `token` characters — what a WebSocket subprotocol name is
# allowed to contain (RFC 6455 §4.1 defers to it).
_TCHAR = set("!#$%&'*+-.^_`|~0123456789"
             "abcdefghijklmnopqrstuvwxyz"
             "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _b64url(token: str) -> str:
    """What ``data.js`` puts in the subprotocol: base64url of the UTF-8
    token, ``=`` padding stripped."""
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")


# A token an admin may now choose. Every awkward character in the printable
# ASCII range, including the ones that are illegal in an RFC 9110 token and
# the comma that the subprotocol header is split on.
CUSTOM_TOKENS = [
    "MyT0ken!!going",
    "a token with spaces",
    "p@ss,word,12345",
    '100% "safe", really!',
    "back\\slash;colon:slash/x",
    "=equals=and=more=",
    "~!#$&*+^_`|.-tchar",
    " ".join(["word"] * 8),
    "".join(chr(c) for c in range(0x20, 0x7F)).strip(),   # every printable one
]


@pytest.mark.parametrize(
    "token",
    [admin_auth.generate_device_token() for _ in range(3)] + CUSTOM_TOKENS,
)
def test_the_token_survives_every_transport_it_travels_on(token: str) -> None:
    """Header value, query parameter, file mirror and WebSocket
    subprotocol — for ARBITRARY printable ASCII, not only for a generated
    phrase. The subprotocol used to be the strict one and it is why the
    canonical form had no spaces; it is now base64url-encoded, which is what
    lets the token be anything."""
    # 1. `X-Device-Token` header value: printable ASCII, no CTLs, no
    #    leading/trailing whitespace that a proxy (or OkHttp, or the
    #    server's own .strip()) would trim away.
    assert token == token.strip()
    assert all(0x20 <= ord(c) <= 0x7E for c in token)
    # It also has to fit through the header encoder unchanged. Starlette
    # decodes header bytes as latin-1; for ASCII that is the identity, and
    # for anything else it would arrive mojibake'd and silently never match
    # — which is exactly why non-ASCII is refused at the SET endpoint.
    assert token.encode("latin-1").decode("latin-1") == token

    # 2. `?device_token=` query: percent-encoded by data.js, decoded back
    #    to the same bytes by Starlette.
    quoted = urllib.parse.quote(token, safe="")
    parsed = urllib.parse.parse_qs(f"{admin_auth.DEVICE_TOKEN_QUERY}={quoted}")
    assert parsed[admin_auth.DEVICE_TOKEN_QUERY] == [token]

    # 3. `~/.domovoi/device-token.txt`, written as `token + "\n"` and read
    #    back with .strip(): a round trip must be lossless.
    assert (token + "\n").strip() == token

    # 4. `domovoi.device-token-b64.<base64url>` WebSocket subprotocol:
    #    every character of the ELEMENT must be an RFC 9110 tchar, and it
    #    must not contain the comma that separates offers in the header.
    subprotocol = f"domovoi.device-token-b64.{_b64url(token)}"
    assert set(subprotocol) <= _TCHAR
    assert "," not in subprotocol and " " not in subprotocol
    # ...and it has to decode back to the token the browser started with.
    payload = subprotocol[len("domovoi.device-token-b64."):]
    pad = "=" * (-len(payload) % 4)
    assert base64.urlsafe_b64decode(payload + pad).decode("utf-8") == token

    # The cap is what keeps that element a knowable size.
    assert len(token) <= admin_auth.DEVICE_TOKEN_MAX_LEN


@pytest.mark.parametrize("token", [admin_auth.generate_device_token() for _ in range(5)])
def test_a_generated_phrase_is_also_legal_in_the_raw_subprotocol(token: str) -> None:
    """Split out from the transport test on purpose: this is a property of
    the GENERATOR, not of the token rule. It is what lets the dashboard
    offer the legacy raw element alongside the b64 one during a rollout,
    and it is why an install upgrading from before this change keeps its
    live state stream."""
    assert set(f"domovoi.device-token.{token}") <= _TCHAR
    # The longest possible phrase from this bank is 71 characters.
    assert len(token) <= 71
    assert len(token) >= admin_auth.DEVICE_TOKEN_MIN_LEN


# Which chosen tokens the LEGACY raw element can still carry, and which
# it cannot. This is the partition data.js branches on: it adds the raw
# element only for the first group, because the WebSocket constructor
# validates every element and one illegal one throws the whole call away.
RAW_LEGAL = ["MyT0ken!!going", "~!#$&*+^_`|.-tchar"]
RAW_ILLEGAL = [t for t in CUSTOM_TOKENS if t not in RAW_LEGAL]


@pytest.mark.parametrize("token", RAW_ILLEGAL)
def test_most_chosen_tokens_are_ILLEGAL_in_the_raw_subprotocol(token: str) -> None:
    """The negative half, and the reason the b64 element exists. Each of
    these would make `new WebSocket(...)` throw before a byte left the tab
    (and uvicorn answer the handshake 400 before any ASGI code ran), so the
    dashboard must NOT offer the raw form for them."""
    assert not set(f"domovoi.device-token.{token}") <= _TCHAR


@pytest.mark.parametrize("token", RAW_LEGAL)
def test_some_chosen_tokens_are_legal_raw_too(token: str) -> None:
    r"""The illegal set is space plus " ( ) , / : ; < = > ? @ [ \ ] { } —
    NOT "anything but lowercase letters and hyphens". `MyT0ken!!going` was
    always a legal subprotocol; it was auth.js lowercasing it and the SET
    endpoint not existing that made it unusable. These are the tokens for
    which data.js offers BOTH elements, so a server that has not been
    restarted yet still has something it recognises to echo back."""
    assert set(f"domovoi.device-token.{token}") <= _TCHAR


def test_both_web_subprotocol_prefixes_are_legal_tokens() -> None:
    from web.backend.main import (
        WS_DEVICE_TOKEN_SUBPROTOCOL,
        WS_DEVICE_TOKEN_SUBPROTOCOL_B64,
    )

    assert set(WS_DEVICE_TOKEN_SUBPROTOCOL) <= _TCHAR
    assert set(WS_DEVICE_TOKEN_SUBPROTOCOL_B64) <= _TCHAR
    # base64url's whole alphabet is tchar, so ANY token's encoding is legal.
    assert set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") <= _TCHAR


# ═══ Forgiving input, canonical storage ═══════════════════════════════════


CANONICAL = "acorn-maple-river-thistle-harbor-quartz-willow-ember"


@pytest.mark.parametrize(
    "typed",
    [
        CANONICAL,
        "  " + CANONICAL + "  ",
        CANONICAL.replace("-", " "),
        CANONICAL.replace("-", "_"),
        CANONICAL.upper(),
        "Acorn Maple River Thistle Harbor Quartz Willow Ember",
        "acorn  maple\triver\nthistle harbor quartz willow ember",
        CANONICAL.replace("-", "--"),
        "-" + CANONICAL + "-",
        "acorn - maple - river - thistle - harbor - quartz - willow - ember",
    ],
)
def test_however_it_was_typed_it_normalises_to_one_token(typed: str) -> None:
    assert admin_auth.normalize_device_token(typed) == CANONICAL


@pytest.mark.parametrize("empty", [None, "", "   ", "---", " _ - _ "])
def test_nothing_normalises_to_nothing(empty) -> None:
    assert admin_auth.normalize_device_token(empty) is None


def test_normalising_a_legacy_hex_token_leaves_it_alone() -> None:
    """The compatibility hinge: an install that minted 64 hex characters
    before the phrase existed must keep validating without a rotation, and
    that holds only because normalisation is the identity on lowercase hex
    with no separators."""
    assert admin_auth.normalize_device_token(LEGACY_HEX) == LEGACY_HEX
    assert admin_auth.normalize_device_token(f"  {LEGACY_HEX}\n") == LEGACY_HEX


class _StoredToken:
    """A stand-in ``AsyncSession`` that answers the one SELECT
    ``validate_device_token`` makes: the stored plaintext and its hash.

    The plaintext is in the row because the "is the stored token itself
    canonical" test is derived from it — that is what makes the two-form
    match need no migration and no flag column."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.hash = admin_auth.token_sha256(token)

    async def execute(self, *_a, **_kw):
        stored_token, stored_hash = self.token, self.hash

        class _Row:
            token = stored_token
            token_hash = stored_hash

        class _Result:
            def first(self_inner):
                return _Row()

        return _Result()


class _MismatchedRow(_StoredToken):
    """A row whose ``token_hash`` is NOT the hash of its ``token``.

    Not a shape the current writers can produce — every one of them stores
    ``sha256(token)``. It is the shape the PREVIOUS writer produced for a
    non-canonical token, and the one a future "optimisation" would
    reintroduce by hashing the canonical form again."""

    def __init__(self, token: str, hashed: str) -> None:
        super().__init__(token)
        self.hash = admin_auth.token_sha256(hashed)


@pytest.mark.asyncio
async def test_the_canonical_guard_changes_no_answer_while_the_row_is_consistent() -> None:
    """``_canonical_token_hash`` is provably redundant for every row the
    current code can write, and this pins that so nobody has to re-derive
    it: ``normalize`` is idempotent, so a candidate's canonical form is
    always canonical and can never equal a NON-canonical stored value —
    the canonical compare fails on its own, sentinel or no sentinel.

    Measured rather than argued: every stored token below against every
    respelling below, with the guard and with ``canonical_hash =
    stored_hash`` unconditionally. Zero differences. The companion test
    underneath is the reason the guard stays anyway."""
    stored_tokens = [
        CANONICAL, LEGACY_HEX, "MyT0ken!!going", "Maple Street, 1984!",
        "$$bills_yall-market!!1999", "frontdoorcats1999", "a--b", "a__b",
        "a_-b", "-leading", "trailing-", "My House Is Red",
    ]
    differences, pairs = [], 0
    for stored in stored_tokens:
        stored_hash = admin_auth.token_sha256(stored)
        canonical = admin_auth.normalize_device_token(stored)
        guarded = stored_hash if canonical == stored else admin_auth._NO_CANONICAL_FORM
        for candidate in [
            stored, stored.upper(), stored.lower(), f"  {stored}  ",
            stored.replace("-", "_"), stored.replace("_", "-"),
            stored.replace("-", " "), stored.replace(" ", "-"),
            canonical or "", (canonical or "").upper(), stored + "x", stored[:-1],
            "", "   ", "---", " _ - _ ",
        ]:
            pairs += 1
            exact = admin_auth.token_sha256(candidate.strip())
            cform = admin_auth.token_sha256(admin_auth.normalize_device_token(candidate) or "")
            with_guard = secrets.compare_digest(exact, stored_hash) | secrets.compare_digest(cform, guarded)
            without = secrets.compare_digest(exact, stored_hash) | secrets.compare_digest(cform, stored_hash)
            if bool(with_guard) != bool(without):
                differences.append((stored, candidate))
    assert pairs >= 190, pairs
    assert differences == []


@pytest.mark.asyncio
async def test_a_chosen_token_stays_closed_to_respellings_on_a_mis_hashed_row(
    monkeypatch,
) -> None:
    """...and THIS is what the guard is for, so do not delete it as dead.

    The two-form rule promises that a token an admin chose is matched
    character for character and that no RESPELLING of it opens the door.
    That promise has to rest on the stored plaintext, not on how the row's
    hash happened to be computed — and this codebase's own writer hashed
    the canonical form until the custom-token work landed. On a row shaped
    like that, the guard is the only thing standing between a chosen token
    and every capitalisation of it.

    The row is broken either way (the chosen token does not open it at
    all), so this is blast radius, not a rescue: with the guard exactly one
    string still works, without it a whole family does."""
    monkeypatch.setattr(admin_auth, "_canonical_hash_cache", None)
    chosen = "MyT0ken!!going"
    canonical = admin_auth.normalize_device_token(chosen)
    assert canonical == "myt0ken!!going" != chosen
    session = _MismatchedRow(chosen, canonical)

    for respelling in ("MYT0KEN!!GOING", "MyT0ken!!Going", f"  {chosen}  ", "mYt0KEN!!goinG"):
        monkeypatch.setattr(admin_auth, "_canonical_hash_cache", None)
        assert await admin_auth.validate_device_token(session, respelling) is False, respelling
        # Deleting the guard means canonical_hash = stored_hash, and that
        # is the compare it would then run. It matches. That is the A/B.
        assert secrets.compare_digest(
            admin_auth.token_sha256(admin_auth.normalize_device_token(respelling) or ""),
            session.hash,
        ), respelling

    # A consistent row is untouched by any of this.
    monkeypatch.setattr(admin_auth, "_canonical_hash_cache", None)
    assert await admin_auth.validate_device_token(_StoredToken(chosen), chosen) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "typed",
    [CANONICAL, CANONICAL.upper(), CANONICAL.replace("-", " "), f"  {CANONICAL} "],
)
async def test_validate_accepts_any_spelling_of_the_stored_phrase(typed: str) -> None:
    session = _StoredToken(CANONICAL)
    assert await admin_auth.validate_device_token(session, typed) is True


@pytest.mark.asyncio
async def test_validate_still_refuses_a_different_phrase() -> None:
    session = _StoredToken(CANONICAL)
    wrong = CANONICAL.replace("acorn", "bison")
    assert await admin_auth.validate_device_token(session, wrong) is False
    assert await admin_auth.validate_device_token(session, None) is False
    assert await admin_auth.validate_device_token(session, "") is False


@pytest.mark.asyncio
async def test_a_legacy_hex_token_still_validates() -> None:
    """An upgraded install keeps every paired browser and phone: the row
    still holds the old hash, and the old token still hashes to it."""
    session = _StoredToken(LEGACY_HEX)
    assert await admin_auth.validate_device_token(session, LEGACY_HEX) is True
    assert await admin_auth.validate_device_token(session, f" {LEGACY_HEX} ") is True
    assert await admin_auth.validate_device_token(session, LEGACY_HEX.upper()) is True


# ═══ A token an admin CHOSE ═══════════════════════════════════════════════
#
# The rule, all in `validate_custom_device_token`: trim the outer
# whitespace and change nothing else; printable ASCII 0x20-0x7E; 12 to 128
# characters on the trimmed form; not an all-separator string.

CUSTOM = "MyT0ken!!going"


@pytest.mark.parametrize("token", CUSTOM_TOKENS)
def test_a_chosen_token_is_stored_exactly_as_it_was_typed(token: str) -> None:
    assert admin_auth.validate_custom_device_token(token) == token


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("  MyT0ken!!going  ", "MyT0ken!!going"),
        ("\tMyT0ken!!going\n", "MyT0ken!!going"),
        ("two  spaces  inside", "two  spaces  inside"),   # interior is KEPT
        ("MyT0ken!!going\r\n", "MyT0ken!!going"),
    ],
)
def test_only_the_OUTER_whitespace_is_trimmed(typed: str, stored: str) -> None:
    """Not cosmetic. The mirror file is read back with .strip() and OkHttp
    trims a header value, so a stored token with edge whitespace could never
    be presented back — and `ensure_device_token` would rewrite the mirror
    on every boot because the file and the row would never agree."""
    assert admin_auth.validate_custom_device_token(typed) == stored
    # ...and the stored form is a fixed point of the round trip.
    assert (stored + "\n").strip() == stored


def test_the_alphabet_boundary_is_exactly_0x20_through_0x7E() -> None:
    def ok(ch: str) -> bool:
        try:
            admin_auth.validate_custom_device_token(f"abcdef{ch}ghijkl")
            return True
        except admin_auth.DeviceTokenRejected:
            return False

    assert not ok("\x1f")          # last control character
    assert ok("\x20")              # SP — the first legal one
    assert ok("\x7e")              # ~ — the last legal one
    assert not ok("\x7f")          # DEL is a control character, not printable
    assert not ok("\u00e9")        # non-ASCII: latin-1-decoded to nonsense on
    assert not ok("\u00a0")        # the header, UnicodeEncodeError on the proxy
    # Every printable ASCII character is accepted somewhere in a token.
    every = "".join(chr(c) for c in range(0x20, 0x7F)).strip()
    assert admin_auth.validate_custom_device_token(every) == every


def test_non_ascii_is_refused_because_it_would_fail_SILENTLY() -> None:
    """Starlette decodes header bytes as latin-1, so a UTF-8 token arrives
    mojibake'd and merely never compares equal — it looks like a wrong
    token, not like a bad one. Refuse it where a person can read why."""
    with pytest.raises(admin_auth.DeviceTokenRejected) as e:
        admin_auth.validate_custom_device_token("cat-\U0001f431-token")
    assert "non-ASCII" in str(e.value)
    # The proof of the mechanism, so the reason does not rot:
    mojibake = "caf\u00e9-token".encode("utf-8").decode("latin-1")
    assert mojibake != "caf\u00e9-token"


def test_the_floor_is_twelve_measured_on_the_STORED_form() -> None:
    assert admin_auth.DEVICE_TOKEN_MIN_LEN == 12
    with pytest.raises(admin_auth.DeviceTokenRejected) as e:
        admin_auth.validate_custom_device_token("a" * 11)
    assert "at least 12 characters" in str(e.value)
    assert admin_auth.validate_custom_device_token("a" * 12) == "a" * 12
    # Padding does not buy length: the count is on what is stored.
    with pytest.raises(admin_auth.DeviceTokenRejected):
        admin_auth.validate_custom_device_token("   " + "a" * 11 + "   ")


def test_the_cap_is_a_hundred_and_twenty_eight() -> None:
    assert admin_auth.DEVICE_TOKEN_MAX_LEN == 128
    assert len(admin_auth.validate_custom_device_token("x" * 128)) == 128
    with pytest.raises(admin_auth.DeviceTokenRejected) as e:
        admin_auth.validate_custom_device_token("x" * 129)
    assert "at most 128 characters" in str(e.value)


@pytest.mark.parametrize("token", ["- - - - - - -", "-" * 12, "_" * 14, " - _ - _ - _ - _ "])
def test_an_all_separator_token_is_refused(token: str) -> None:
    """It passes the alphabet and (mostly) the floor, and normalises to
    None — so storing it would leave the household matchable only by the
    exact string, with the canonical branch dead. Pure footgun."""
    with pytest.raises(admin_auth.DeviceTokenRejected):
        admin_auth.validate_custom_device_token(token)


@pytest.mark.parametrize("bad", [None, 12, b"abcdefghijkl", ""])
def test_a_non_string_or_empty_token_is_refused(bad) -> None:
    with pytest.raises(admin_auth.DeviceTokenRejected):
        admin_auth.validate_custom_device_token(bad)


def test_a_rejection_never_quotes_the_offending_value() -> None:
    """The message travels back to the client as a 400 detail and into
    whatever it logs. A near-miss household token must not ride along."""
    for bad in ("hunter2", "cat-\U0001f431-token", "x" * 129, "\x01\x02abcdefghijkl"):
        try:
            admin_auth.validate_custom_device_token(bad)
        except admin_auth.DeviceTokenRejected as e:
            assert bad not in str(e), str(e)
            assert bad.strip() not in str(e), str(e)
        else:  # pragma: no cover — every one of these must be refused
            raise AssertionError(bad)


# ─── …and how it is matched ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_chosen_token_is_matched_EXACTLY() -> None:
    session = _StoredToken(CUSTOM)
    assert await admin_auth.validate_device_token(session, CUSTOM) is True
    # Trimming the candidate is the one liberty taken — OkHttp and the
    # header parser already do it.
    assert await admin_auth.validate_device_token(session, f"  {CUSTOM}  ") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "respelled",
    [
        "myt0ken!!going",                 # lowercased
        "MYT0KEN!!GOING",
        "MyT0ken!!-going",
        "MyT0ken!! going",
    ],
)
async def test_a_chosen_token_is_NOT_matched_case_insensitively(respelled: str) -> None:
    """The whole point of storing it verbatim: `MyT0ken!!going` is not
    canonical, so the canonical branch does not apply to it and its
    capitals carry their entropy."""
    session = _StoredToken(CUSTOM)
    assert admin_auth.normalize_device_token(CUSTOM) != CUSTOM
    assert await admin_auth.validate_device_token(session, respelled) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("token", CUSTOM_TOKENS)
async def test_every_chosen_token_matches_itself_and_nothing_near_it(token: str) -> None:
    session = _StoredToken(token)
    assert await admin_auth.validate_device_token(session, token) is True
    assert await admin_auth.validate_device_token(session, token + "x") is False
    assert await admin_auth.validate_device_token(session, token[:-1]) is False


@pytest.mark.asyncio
async def test_a_generated_phrase_is_STILL_matched_forgivingly() -> None:
    """Nothing is taken away from the household that never sets a custom
    token: a generated phrase is canonical, so the canonical branch applies
    and typing it back is as forgiving as it ever was."""
    session = _StoredToken(CANONICAL)
    assert admin_auth.normalize_device_token(CANONICAL) == CANONICAL
    for typed in (
        CANONICAL.upper(),
        CANONICAL.replace("-", " "),
        CANONICAL.replace("-", "_"),
        "Acorn Maple River Thistle Harbor Quartz Willow Ember",
    ):
        assert await admin_auth.validate_device_token(session, typed) is True


@pytest.mark.asyncio
async def test_an_all_separator_candidate_cannot_open_a_canonical_token() -> None:
    """`normalize_device_token` returns None for these, and the old code
    returned False on that before it looked at anything. The exact compare
    now runs first and unconditionally, which is what stops a stored token
    that normalises to None locking the household out — and this is the
    other side of that coin."""
    session = _StoredToken(CANONICAL)
    for candidate in ("---", "   ", " _ - _ ", ""):
        assert await admin_auth.validate_device_token(session, candidate) is False


@pytest.mark.asyncio
async def test_the_canonical_branch_is_dead_for_a_non_canonical_stored_token() -> None:
    """Stated as a property rather than as an implementation detail: for a
    stored token that is not canonical, the ONLY accepted candidate is the
    stored value (after trimming)."""
    session = _StoredToken("Maple Street, 1984!")
    assert await admin_auth.validate_device_token(session, "Maple Street, 1984!") is True
    for near in (
        "maple-street,-1984!",                    # its canonical form
        "maple street, 1984!",
        "MAPLE STREET, 1984!",
        "Maple_Street,_1984!",
    ):
        assert await admin_auth.validate_device_token(session, near) is False


# ═══ The throttle — the half that makes 64 bits comfortable ═══════════════


def _fresh_backoff(**kw) -> admin_auth.LoginBackoff:
    return admin_auth.LoginBackoff(**kw)


def test_the_login_ladder_is_unchanged_by_the_new_knobs() -> None:
    """The admin login's instance must behave exactly as it did: no free
    attempts, global ceiling on."""
    assert admin_auth.LOGIN_BACKOFF.free_attempts == 0
    assert admin_auth.LOGIN_BACKOFF.global_ceiling is True
    b = _fresh_backoff()
    assert b.retry_after("a") == 0.0
    b.record_failure("a")
    assert 0.0 < b.retry_after("a") <= admin_auth.BACKOFF_BASE_SEC


def test_free_attempts_then_a_doubling_ladder() -> None:
    b = _fresh_backoff(free_attempts=2, global_ceiling=False)
    b.record_failure("lan")
    assert b.retry_after("lan") == 0.0          # free
    b.record_failure("lan")
    assert b.retry_after("lan") == 0.0          # free
    b.record_failure("lan")
    first = b.retry_after("lan")
    assert 0.0 < first <= admin_auth.BACKOFF_BASE_SEC
    b.record_failure("lan")
    second = b.retry_after("lan")
    assert second > first                       # it GROWS
    assert second <= 2 * admin_auth.BACKOFF_BASE_SEC
    for _ in range(40):
        b.record_failure("lan")
    assert b.retry_after("lan") <= admin_auth.BACKOFF_CAP_SEC


def test_a_success_clears_the_source_s_streak() -> None:
    b = _fresh_backoff(free_attempts=1, global_ceiling=False)
    for _ in range(4):
        b.record_failure("lan")
    assert b.retry_after("lan") > 0
    b.record_success("lan")
    assert b.retry_after("lan") == 0.0
    # And the ladder starts from the bottom again, grace included.
    b.record_failure("lan")
    assert b.retry_after("lan") == 0.0


def test_one_source_cannot_throttle_another() -> None:
    b = _fresh_backoff(free_attempts=0, global_ceiling=False)
    for _ in range(6):
        b.record_failure("hostile")
    assert b.retry_after("hostile") > 0
    assert b.retry_after("the-kitchen-phone") == 0.0


def test_the_device_instance_has_no_aggregate_ceiling(monkeypatch) -> None:
    """Deliberate, and the one place the two instances differ. An
    aggregate ceiling on the ORDINARY household surface would let any host
    that can reach the port put every phone in the house behind a 429."""
    monkeypatch.setattr(admin_auth, "GLOBAL_LOGIN_MAX_FAILURES", 3)
    b = _fresh_backoff(global_ceiling=False)
    for i in range(20):
        b.record_failure(f"host-{i}")
    assert b.retry_after("a-source-that-never-failed") == 0.0
    assert admin_auth.DEVICE_TOKEN_BACKOFF.global_ceiling is False
    assert admin_auth.DEVICE_TOKEN_BACKOFF.free_attempts == (
        admin_auth.DEVICE_TOKEN_FREE_ATTEMPTS
    )


# ─── …and the same thing through the real gate ────────────────────────────


@pytest.mark.asyncio
async def test_wrong_tokens_start_costing_time_at_the_gate(monkeypatch) -> None:
    """Before this change ``validate_device_token`` was an unthrottled
    constant-time compare: a guesser could spend the whole 64-bit space at
    line rate. Now the source is refused 429 with a ``Retry-After``."""
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="dev-token")
    async with mini_client() as c:
        for i in range(admin_auth.DEVICE_TOKEN_FREE_ATTEMPTS):
            r = await c.post("/device", headers={HEADER: f"guess-{i}"})
            assert r.status_code == 401, f"attempt {i} should still be free"
        # The grace is spent; the next wrong token starts the ladder...
        assert (await c.post("/device", headers={HEADER: "guess-x"})).status_code == 401
        # ...and the one after it is not even looked at.
        r = await c.post("/device", headers={HEADER: "guess-y"})
        assert r.status_code == 429
        assert int(r.headers["retry-after"]) >= 1
        assert "admin" in r.json()["detail"]
        # Even the RIGHT token waits: that is what a throttle means.
        assert (await c.post("/device", headers={HEADER: "dev-token"})).status_code == 429


@pytest.mark.asyncio
async def test_the_right_token_clears_the_counter_at_the_gate(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="dev-token")
    async with mini_client() as c:
        for i in range(admin_auth.DEVICE_TOKEN_FREE_ATTEMPTS):
            assert (await c.post("/device", headers={HEADER: f"typo-{i}"})).status_code == 401
        # Pairing correctly wipes the streak...
        assert (await c.post("/device", headers={HEADER: "dev-token"})).status_code == 200
        # ...so the whole grace is available again and nothing 429s.
        for i in range(admin_auth.DEVICE_TOKEN_FREE_ATTEMPTS):
            assert (await c.post("/device", headers={HEADER: f"typo-{i}"})).status_code == 401
        assert (await c.post("/device", headers={HEADER: "dev-token"})).status_code == 200


@pytest.mark.asyncio
async def test_an_admin_session_is_never_charged_for_a_stale_token(monkeypatch) -> None:
    """The dashboard after a rotation elsewhere: a live Bearer beside a
    token that is now wrong. Charging that would let the device tier lock
    an admin out of the very page that fixes it."""
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="dev-token")
    async with mini_client() as c:
        for _ in range(20):
            r = await c.post(
                "/device", headers={HEADER: "yesterdays-token", **bearer("admin-token")}
            )
            assert r.status_code == 200
        assert admin_auth.DEVICE_TOKEN_BACKOFF.retry_after("127.0.0.1") == 0.0


@pytest.mark.asyncio
async def test_a_request_with_no_token_at_all_is_not_a_guess(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="dev-token")
    async with mini_client() as c:
        for _ in range(30):
            assert (await c.post("/device")).status_code == 401
        assert (await c.post("/device", headers={HEADER: "dev-token"})).status_code == 200


@pytest.mark.asyncio
async def test_the_pre_setup_grace_is_not_charged_either(monkeypatch) -> None:
    """A fresh install answers everything; nothing presented to it is a
    guess, so a throwaway harness cannot throttle itself at boot."""
    install_fake_db(monkeypatch, admin=False, device_token="dev-token")
    async with mini_client() as c:
        for _ in range(30):
            assert (await c.post("/device", headers={HEADER: "anything"})).status_code == 200
        assert admin_auth.DEVICE_TOKEN_BACKOFF.retry_after("127.0.0.1") == 0.0


@pytest.mark.asyncio
async def test_each_source_gets_its_own_ladder_at_the_gate(monkeypatch) -> None:
    """The throttle keys on ``request_source``: the kitchen phone is not
    punished for the guesses of whatever is hammering from the garage."""
    install_fake_db(monkeypatch, admin=True, device_token="dev-token")
    hostile = {"X-Forwarded-For": "10.0.0.9"}
    async with mini_client() as c:
        # httpx's ASGI transport peers as 127.0.0.1, which is a TRUSTED
        # proxy, so X-Forwarded-For picks the bucket — the same way the
        # web process forwards each dashboard caller's real address.
        for i in range(admin_auth.DEVICE_TOKEN_FREE_ATTEMPTS + 2):
            await c.post("/device", headers={HEADER: f"guess-{i}", **hostile})
        assert (await c.post("/device", headers={HEADER: "nope", **hostile})).status_code == 429
        r = await c.post("/device", headers={HEADER: "dev-token", "X-Forwarded-For": "10.0.0.20"})
        assert r.status_code == 200


def test_the_request_helpers_still_read_the_token_verbatim() -> None:
    """Normalisation belongs to validation, not to extraction: what the
    gate forwards to the core has to stay what the client sent."""
    assert admin_auth.device_token_from_request(
        make_request({HEADER: "  Acorn Maple  "})
    ) == "Acorn Maple"
