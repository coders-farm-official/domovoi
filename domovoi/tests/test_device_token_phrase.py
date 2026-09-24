"""The household device token is a phrase a person can read out, and
wrong guesses are slowed down.

Two halves that only make sense together. The token dropped from 256 bits
of hex to a 64-bit eight-word phrase so somebody can say it across a room
to a phone, and 64 bits is only comfortable because presenting a wrong one
now costs an exponential per-source backoff. Test both or neither.

Everything here is DB-FREE: the shape and alphabet tests are pure, the
normalisation tests drive ``validate_device_token`` over a stub session
that answers with one stored hash, and the gate tests go through
``auth_testkit``'s mini app wearing the real ``require_device``.
"""

from __future__ import annotations

import re
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


@pytest.mark.parametrize("token", [admin_auth.generate_device_token() for _ in range(5)])
def test_the_token_survives_every_transport_it_travels_on(token: str) -> None:
    """Header value, query parameter and WebSocket subprotocol. The
    subprotocol is the strict one and it is why the canonical form has no
    spaces: a space there is not merely ugly, it is illegal, and the
    browser throws on the WebSocket constructor before the socket opens."""
    # 1. `X-Device-Token` header value: printable ASCII, no CTLs, no
    #    leading/trailing whitespace that a proxy would trim away.
    assert token == token.strip()
    assert all(0x21 <= ord(c) <= 0x7E for c in token)
    # It also has to fit through the header encoder unchanged.
    assert token.encode("latin-1").decode("latin-1") == token

    # 2. `?device_token=` query: hyphens and lowercase letters are
    #    unreserved, so the URL carries the token verbatim.
    assert urllib.parse.quote(token, safe="") == token
    parsed = urllib.parse.parse_qs(f"{admin_auth.DEVICE_TOKEN_QUERY}={token}")
    assert parsed[admin_auth.DEVICE_TOKEN_QUERY] == [token]

    # 3. `domovoi.device-token.<token>` WebSocket subprotocol: every
    #    character must be an RFC 9110 tchar, and the value must not
    #    contain the comma that separates offers in the header.
    subprotocol = f"domovoi.device-token.{token}"
    assert set(subprotocol) <= _TCHAR
    assert "," not in subprotocol and " " not in subprotocol

    # And it stays short enough to read out and to store: the longest
    # possible phrase from this bank is 71 characters.
    assert len(token) <= 71


def test_the_web_subprotocol_prefix_is_still_a_legal_token() -> None:
    from web.backend.main import WS_DEVICE_TOKEN_SUBPROTOCOL

    assert set(WS_DEVICE_TOKEN_SUBPROTOCOL) <= _TCHAR


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
    ``validate_device_token`` makes, with the hash of ``token``."""

    def __init__(self, token: str) -> None:
        self.hash = admin_auth.token_sha256(token)

    async def execute(self, *_a, **_kw):
        stored = self.hash

        class _Row:
            token_hash = stored

        class _Result:
            def first(self_inner):
                return _Row()

        return _Result()


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
