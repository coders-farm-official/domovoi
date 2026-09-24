"""Admin authentication — the v1 lightweight model (design §7.2/§7.3,
V1 scope amendment).

Shared by BOTH processes (core :6370 and web :6369): every primitive
here works against the same Postgres ``admin_auth`` / ``admin_sessions``
tables, so a token minted by the web dashboard's login endpoint
authorizes gated routes on the core too. The module deliberately never
imports ``domovoi.plugins_runtime`` — the web process must stay outside
the plugin runtime (design §5.1).

The model, in one paragraph: first boot writes an 8-word **setup code**
to ``~/.domovoi/setup-code.txt`` (mode 0600, valid for
:data:`SETUP_CODE_TTL_SEC`) and prints it to the core console — proof of
possession of the server. ``POST /api/auth/setup`` requires that code,
hashes the chosen password with **argon2id**, and deletes the code file.
``POST /api/auth/login`` verifies the password (behind a per-source
exponential backoff that counts the attempt BEFORE the verify) and mints
a 256-bit bearer token of which only the sha256 is stored, with a
**30-day sliding expiry** under a **90-day absolute cap**. Mutating admin
endpoints accept ONLY ``Authorization: Bearer`` — the ``SameSite=Strict``
cookie set at login exists solely so plain GET page loads can render
authenticated state (CSRF: a cross-site POST carries nothing that
authorizes it). Changing the password revokes every other session.
``python -m domovoi.main --reset-admin`` clears the credential + sessions
and regenerates the setup code.

Three gates, from weakest to strongest:

* :func:`require_device` — the **device tier**: a valid
  ``X-Device-Token`` (the per-household token in
  ``household_device_tokens``, mirrored to ``~/.domovoi/device-token.txt``
  by :func:`ensure_device_token` at boot) OR an admin Bearer. Keeps the
  pre-setup LAN grace so a fresh install still works before setup. The
  token is an eight-word phrase (:func:`generate_device_token`, 64 bits)
  a person can read out to a phone across the room; wrong ones are
  charged an exponential per-source backoff
  (:data:`DEVICE_TOKEN_BACKOFF`) and the pair of those is what makes 64
  bits enough. Input is canonicalised on the way in
  (:func:`normalize_device_token`), so case, spaces and underscores all
  pair, and a 64-hex token from an older install still validates.
  :func:`require_device_read` is its read-only half for media the browser
  fetches by URL: it also takes the dashboard cookie and a
  ``?device_token=`` query, neither of which may authorize a change.
* :func:`require_admin_mutation` / :func:`require_admin_read` — the
  **admin tier**: Bearer (or cookie for reads). Keeps the pre-setup grace.
* :func:`require_admin_security` — the **security tier** (config write,
  service restart, satellite code push, pairing preseed/reset, satellite
  delete, device-token rotation): Bearer-only and **fails closed** with
  501 until an admin password exists, exactly like plugin management.
  ``--reset-admin`` therefore reopens only the daily surface.

Beside them sits one narrow machine-to-machine gate:
:func:`require_chat_callback`, which guards the endpoint the chat agent's
sandboxed proxy tools call back on. It takes neither tier's credential —
the tools hold a per-boot secret this process generated into their source.

DEFERRED (documented hardening backlog, scope amendment): TLS /
fingerprint pinning. v1 admin flows run over plain LAN HTTP.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.session import session_scope

log = logging.getLogger(__name__)

# ─── Tunables (module constants; tests monkeypatch these) ─────────────────

SESSION_TTL_DAYS = 30
# Absolute cap on a session's life: however often it is used, a token
# minted more than this long ago is refused and its sliding expiry never
# extends past ``created_at + SESSION_MAX_AGE_DAYS``.
SESSION_MAX_AGE_DAYS = 90
# The setup code is single-use AND time-boxed: a code file older than this
# is refused by ``verify_setup_code`` and replaced (with a fresh console
# banner) by the next core boot. Sized to the day the box was first booted —
# regeneration happens only at boot, so a shorter window on a headless
# server would just mean more restarts.
SETUP_CODE_TTL_SEC = 24 * 3600.0
# Failed-login backoff: 1 s doubling per failure, capped at 5 min (§7.3).
BACKOFF_BASE_SEC = 1.0
BACKOFF_CAP_SEC = 300.0
# How many WRONG household device tokens a source may present before the
# same doubling backoff starts charging it. A real household trips this
# without malice: a browser or phone still holding a rotated token fires
# several requests in parallel, and every one of them is a failure, so
# charging from the first would put the person who is about to paste the
# right phrase behind a wait they did nothing to earn. After the grace the
# doubling is the same 1 s → 5 min ladder the login uses, which caps a
# source at ~17 tries an hour — nothing against 64 bits.
DEVICE_TOKEN_FREE_ATTEMPTS = 5
# Outbound-fetch tier (add-by-url without an admin session): per-source
# request budget within the window.
URL_FETCH_WINDOW_SEC = 60.0
URL_FETCH_MAX_PER_WINDOW = 10

# Trusted immediate peers whose ``X-Forwarded-For`` we honor for the
# throttle identity. Only LOOPBACK by default: the web process runs on the
# same box as the core and forwards every dashboard caller's real address,
# so trusting it gives each dashboard user their own throttle bucket
# instead of lumping them all under 127.0.0.1. A client-supplied XFF from
# any other peer is ignored and throttling keys on the real transport peer,
# so a LAN host can't mint unlimited fresh zero-failure buckets by rotating
# the header. An operator fronting the core with a real proxy adds that
# proxy's LAN address here.
TRUSTED_PROXIES: set[str] = {"127.0.0.1", "::1"}

# Header a household client presents on the device tier (design: two-tier
# auth, 2026-09-22). Mirrored to ``device_token_path()`` at boot.
DEVICE_TOKEN_HEADER = "X-Device-Token"

# Header the chat agent's generated proxy tools present when they call the
# core back (see :func:`require_chat_callback`). The secret is minted per
# BOOT and embedded in the generated source, so only tool code this
# process generated carries it.
CHAT_CALLBACK_HEADER = "X-Chat-Callback"

# Global login-attempt ceiling — a v1 backstop that caps the endpoint AS A
# WHOLE, independent of the per-source backoff. Even an attacker who rotates
# the per-source key (many real LAN hosts, or a spoofed peer) trips this once
# aggregate failures within the window cross the ceiling; every login then
# 429s until the window drains. Sized well above any believable fat-finger
# rate so honest users never hit it.
GLOBAL_LOGIN_WINDOW_SEC = 300.0
GLOBAL_LOGIN_MAX_FAILURES = 50

COOKIE_NAME = "domovoi_admin"

# Where the one-time setup code lives. Module-level so tests can point it
# at a tmp dir; production is the server-side config dir.
CONFIG_DIR = Path.home() / ".domovoi"


def setup_code_path() -> Path:
    return CONFIG_DIR / "setup-code.txt"


def device_token_path() -> Path:
    return CONFIG_DIR / "device-token.txt"


def write_private_file(path: Path, content: str) -> Path:
    """Write ``content`` to ``path`` readable by the owner only (0600).

    The file is created with mode 0600 (so it never exists world-readable,
    even for an instant under umask 022) and an existing file is chmod'ed
    to 0600 before it is rewritten, so a file left over from an older
    build is tightened too. On Windows the mode bits only carry the
    read-only flag — best effort; the per-user profile directory is what
    keeps ``~/.domovoi`` private there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except OSError as e:  # pragma: no cover — FS trouble
        log.warning("could not chmod %s: %s", path, e)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


# ─── Word phrases: the setup code and the household device token ──────────

# 256 short common words → 8 words = 64 bits of entropy. Plain ASCII so
# the code survives any console / copy-paste path (Windows cp1252 hosts).
#
# The bank is FLAT on purpose. Banks split by part of speech (nouns,
# verbs, adjectives, pronouns) read better but are far weaker: each role
# bank would hold a couple of hundred words at best, and grammar then
# constrains which slot takes which word, so a four-word sentence lands
# near 32 bits — a number a patient guesser reaches. Eight words drawn
# uniformly from one 256-word bank is 64 bits and still reads out loud in
# one breath.
_WORDS = (
    "acorn apple arrow autumn badge baker basil beach berry birch bison "
    "blaze bloom bluff brass bread breeze brick brook broom bucket butter "
    "cabin candle canoe carrot cedar chalk cherry chess cider cliff clover "
    "cobble comet coral cotton cradle crane creek cricket crumb crystal "
    "daisy dawn delta denim dove drift drum dusk eagle earth ember fable "
    "falcon feather fern field finch flame flint fog forest fossil fox "
    "frost garden garlic geese ginger glade glass goose grain grape grove "
    "harbor hazel heron hill honey horse iris ivory ivy jade jasper juniper "
    "kettle kite lake lantern larch laurel leaf ledge lemon lilac linen "
    "lotus lunar maple marble meadow mint mirror moss moth mountain mulberry "
    "myrtle napkin nectar nest night north nutmeg oak oasis ocean olive "
    "onyx orchard otter owl paddle pansy paper peach pearl pebble pepper "
    "petal pine planet plum pond poplar poppy prairie quail quartz quill "
    "rain raven reed ridge river robin rocket rose rowan rustic saddle "
    "saffron sage salmon sand satin seed shell shore silver sky slate "
    "smoke snow socket sorrel spark sparrow spice spring spruce squash "
    "stone storm straw stream sugar summer sunset swan sweater table "
    "tallow tansy teapot thistle thorn thyme tiger timber toast topaz "
    "torch trail trout tulip tundra turnip twig umber valley vapor velvet "
    "vine violet wagon walnut water weave wheat willow window winter wolf "
    "wren yarn yarrow zephyr acre alder amber anchor aspen aster bank barn "
    "bay bell boat bone book bough bowl box bud bulb bush cake calf cape "
    "cart cave chime clay coal coast coin cone cork corn cove crow cup "
    "dam dew dock door down draw dune "
).split()
assert len(_WORDS) >= 256, "setup-code wordlist must give >= 8 bits/word"

# Words per phrase. Both the setup code and the household device token use
# eight, so both carry 8 × 8 = 64 bits.
PHRASE_WORDS = 8
DEVICE_TOKEN_WORDS = PHRASE_WORDS


def generate_word_phrase(words: int = PHRASE_WORDS) -> str:
    """``words`` words chosen uniformly from the first 256 of
    :data:`_WORDS`, joined with hyphens. Lowercase ASCII letters and
    hyphens only — see :func:`normalize_device_token` for why that
    alphabet and no other."""
    return "-".join(secrets.choice(_WORDS[:256]) for _ in range(words))


def generate_setup_code() -> str:
    return generate_word_phrase()


def write_setup_code(code: str) -> Path:
    """Persist the code owner-readable only; the file's mtime starts the
    :data:`SETUP_CODE_TTL_SEC` window."""
    return write_private_file(setup_code_path(), code + "\n")


def read_setup_code() -> str | None:
    try:
        code = setup_code_path().read_text(encoding="utf-8").strip()
        return code or None
    except OSError:
        return None


def setup_code_age_sec() -> float | None:
    """Seconds since the code file was written, or None when there is no
    file (the window is measured from the file's mtime, which every
    writer here sets by rewriting the file)."""
    try:
        return max(0.0, time.time() - setup_code_path().stat().st_mtime)
    except OSError:
        return None


def setup_code_expired() -> bool:
    """True when a code file exists but is older than
    :data:`SETUP_CODE_TTL_SEC`. A missing file is not "expired" — it is
    absent, which the callers already treat as "no code, no setup"."""
    age = setup_code_age_sec()
    return age is not None and age > SETUP_CODE_TTL_SEC


def delete_setup_code() -> None:
    try:
        setup_code_path().unlink(missing_ok=True)
    except OSError as e:  # pragma: no cover — FS trouble
        log.warning("could not delete setup code file: %s", e)


def verify_setup_code(candidate: str) -> bool:
    """Constant-time compare of the presented code against the file. A
    missing/empty file always fails — no code, no setup — and so does a
    file older than :data:`SETUP_CODE_TTL_SEC` (the next core boot writes
    a fresh one)."""
    actual = read_setup_code()
    if actual is None:
        return False
    matched = secrets.compare_digest(candidate.strip().encode(), actual.encode())
    if setup_code_expired():
        log.info("setup code presented after its %.0f h window", SETUP_CODE_TTL_SEC / 3600)
        return False
    return matched


async def ensure_setup_code_if_unclaimed() -> str | None:
    """Core-boot hook (§7.2): when no admin credential exists yet, make
    sure a setup code file exists and print the code to the console so
    the operator can complete first-run setup. Reuses an existing file's
    code while it is inside its window (a restart must not invalidate the
    code the operator already read); an expired file is replaced. Returns
    the active code, or None when setup is complete."""
    try:
        async with session_scope() as s:
            if await has_admin_auth(s):
                # Claimed — a stale code file must not linger as a
                # phantom credential.
                delete_setup_code()
                return None
    except Exception as e:  # pragma: no cover — DB down at boot
        log.warning("setup-code boot check skipped (DB unreachable): %s", e)
        return None
    code = read_setup_code()
    if code is not None and setup_code_expired():
        log.info("setup code file expired — issuing a fresh code")
        code = None
    if code is None:
        code = generate_setup_code()
        write_setup_code(code)
    # Print AND log — the design requires the code on the console.
    banner = (
        "\n"
        "============================================================\n"
        " Domovoi first-run setup\n"
        f" Setup code: {code}\n"
        f" (also written to {setup_code_path()})\n"
        " Open the dashboard and enter this code to choose the admin\n"
        " password. The code is deleted once setup completes.\n"
        "============================================================\n"
    )
    print(banner, flush=True)
    log.info("admin setup pending — setup code written to %s", setup_code_path())
    return code


# ─── Password hashing (argon2id) ──────────────────────────────────────────


def _hasher():
    from argon2 import PasswordHasher

    return PasswordHasher()  # library defaults = argon2id


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password_hash: str, candidate: str) -> bool:
    from argon2.exceptions import VerificationError, VerifyMismatchError

    try:
        return _hasher().verify(password_hash, candidate)
    except (VerifyMismatchError, VerificationError):
        return False
    except Exception as e:  # pragma: no cover — malformed hash
        log.warning("argon2 verify failed unexpectedly: %s", e)
        return False


# ─── admin_auth / admin_sessions primitives ───────────────────────────────


async def has_admin_auth(session: AsyncSession) -> bool:
    row = (
        await session.execute(text("SELECT 1 FROM admin_auth WHERE id = 1"))
    ).first()
    return row is not None


async def get_password_hash(session: AsyncSession) -> str | None:
    row = (
        await session.execute(
            text("SELECT password_hash FROM admin_auth WHERE id = 1")
        )
    ).first()
    return row.password_hash if row else None


async def set_password(session: AsyncSession, password: str) -> None:
    """Insert-or-update the single credential row (argon2id hash)."""
    await session.execute(
        text(
            "INSERT INTO admin_auth (id, password_hash) VALUES (1, :h) "
            "ON CONFLICT (id) DO UPDATE SET password_hash = EXCLUDED.password_hash, "
            "updated_at = now()"
        ),
        {"h": hash_password(password)},
    )


def token_sha256(token: str) -> str:
    """The stored form of a bearer token (§7.3: only the sha256 persists)."""
    return hashlib.sha256(token.encode()).hexdigest()


_sha256 = token_sha256  # internal alias


async def create_session(session: AsyncSession, label: str | None = None) -> str:
    """Mint a 256-bit bearer token; store only its sha256. Returns the
    raw token — the ONLY time it exists outside the caller's memory."""
    token = secrets.token_hex(32)  # 256 bits
    await session.execute(
        text(
            "INSERT INTO admin_sessions (token_hash, label, expires_at) "
            f"VALUES (:h, :label, now() + interval '{SESSION_TTL_DAYS} days')"
        ),
        {"h": _sha256(token), "label": label},
    )
    return token


async def validate_token(session: AsyncSession, token: str) -> bool:
    """True iff the token maps to a live session. SLIDES the expiry
    (§7.3: 30-day sliding) and stamps ``last_used_at`` on success — but
    never past ``created_at + SESSION_MAX_AGE_DAYS``: a session older than
    the absolute cap is refused however recently it was used."""
    if not token:
        return False
    row = (
        await session.execute(
            text(
                "UPDATE admin_sessions SET last_used_at = now(), "
                "expires_at = LEAST("
                f"now() + interval '{SESSION_TTL_DAYS} days', "
                f"created_at + interval '{SESSION_MAX_AGE_DAYS} days') "
                "WHERE token_hash = :h AND expires_at > now() "
                f"AND created_at > now() - interval '{SESSION_MAX_AGE_DAYS} days' "
                "RETURNING 1"
            ),
            {"h": _sha256(token)},
        )
    ).first()
    return row is not None


async def revoke_session(session: AsyncSession, token_hash: str) -> bool:
    row = (
        await session.execute(
            text("DELETE FROM admin_sessions WHERE token_hash = :h RETURNING 1"),
            {"h": token_hash},
        )
    ).first()
    return row is not None


async def revoke_other_sessions(session: AsyncSession, keep_token_hash: str | None) -> int:
    """Delete every session except ``keep_token_hash`` (the caller's own).
    Used on password change: a token minted under the old password must
    not survive it. Returns how many were revoked."""
    if keep_token_hash is None:
        result = await session.execute(text("DELETE FROM admin_sessions RETURNING 1"))
    else:
        result = await session.execute(
            text("DELETE FROM admin_sessions WHERE token_hash <> :keep RETURNING 1"),
            {"keep": keep_token_hash},
        )
    return len(result.all())


# ─── Household device token (the device tier) ─────────────────────────────

# Anything that separates words when a person types the phrase back:
# spaces (including the ones a phone keyboard inserts), underscores, and
# runs of hyphens. All of them collapse to ONE hyphen.
_DEVICE_TOKEN_SEPARATORS = re.compile(r"[\s_\-]+")


def normalize_device_token(value: str | None) -> str | None:
    """The canonical form of a household token: trimmed, lowercased, every
    run of whitespace / underscores / hyphens collapsed to a single hyphen,
    no leading or trailing hyphen. ``None`` when nothing is left.

    The SAME function runs on generation, on the file mirror and on every
    compare, so ``acorn maple …``, ``Acorn-Maple-…`` and ``acorn_maple_…``
    are one token and the person who reads the phrase off the dashboard and
    types it into a phone is not punished for how their keyboard felt.

    Why this alphabet and no other — the token has to survive all three
    transports it travels on, and lowercase letters plus hyphens are the
    intersection of what they allow:

    * the ``X-Device-Token`` header value (RFC 9110 field value — a space
      would be legal but folds/trims unpredictably through proxies);
    * the ``?device_token=`` query the browser's ``<img>`` / ``<video>``
      loads use, where a hyphen needs no percent-encoding;
    * the ``domovoi.device-token.<token>`` WebSocket **subprotocol**, which
      RFC 6455 requires to be an RFC 9110 *token* — no spaces, at all, ever.
      A browser handed a subprotocol with a space throws before the socket
      is opened, so a spaced token would silently kill the dashboard's live
      stream. That transport is why the canonical form has no spaces.

    Old 64-hex tokens (``secrets.token_hex(32)``, what every install minted
    before this) are already lowercase with no separators, so normalising
    one returns it unchanged and it keeps validating — see
    :func:`validate_device_token`.
    """
    if value is None:
        return None
    cleaned = _DEVICE_TOKEN_SEPARATORS.sub("-", value.strip().lower()).strip("-")
    return cleaned or None


def generate_device_token() -> str:
    """A household token a person can read out loud: eight words from the
    same 256-word bank the setup code uses, hyphen-joined. 64 bits, the
    same strength as the setup code, and short enough to say down a
    hallway. Guessing it is what :data:`DEVICE_TOKEN_BACKOFF` prices."""
    return generate_word_phrase(DEVICE_TOKEN_WORDS)


async def get_device_token(session: AsyncSession) -> str | None:
    row = (
        await session.execute(
            text("SELECT token FROM household_device_tokens WHERE id = 1")
        )
    ).first()
    return row.token if row else None


async def _mint_device_token(session: AsyncSession, *, replace: bool) -> str:
    token = generate_device_token()  # 8 words, 64 bits, already canonical
    if replace:
        await session.execute(
            text(
                "INSERT INTO household_device_tokens (id, token, token_hash) "
                "VALUES (1, :t, :h) "
                "ON CONFLICT (id) DO UPDATE SET token = EXCLUDED.token, "
                "token_hash = EXCLUDED.token_hash, rotated_at = now()"
            ),
            {"t": token, "h": _sha256(token)},
        )
        return token
    # Two processes boot at once (core + web on one box): whichever INSERT
    # lands first wins and the other reads it back — never two tokens.
    await session.execute(
        text(
            "INSERT INTO household_device_tokens (id, token, token_hash) "
            "VALUES (1, :t, :h) ON CONFLICT (id) DO NOTHING"
        ),
        {"t": token, "h": _sha256(token)},
    )
    current = await get_device_token(session)
    return current if current is not None else token


async def ensure_device_token_row(session: AsyncSession) -> str:
    """Return the household token, minting the single row if absent."""
    token = await get_device_token(session)
    if token is not None:
        return token
    return await _mint_device_token(session, replace=False)


async def rotate_device_token(session: AsyncSession) -> str:
    """Replace the household token in place. The previous token is
    refused from this moment; the file mirror is rewritten by the caller
    (:func:`write_device_token_file`)."""
    return await _mint_device_token(session, replace=True)


async def validate_device_token(session: AsyncSession, candidate: str | None) -> bool:
    """Constant-time hash compare of a presented token against the row.

    The candidate is canonicalised first (:func:`normalize_device_token`),
    so how it was typed does not matter — but the compare itself is still
    ``compare_digest`` over the sha256, exactly as before. Normalising is a
    no-op on a 64-hex token, which is why an install that minted one before
    the phrase format existed keeps working without a rotation.

    This function does NOT throttle: it is called from gates that already
    know the request's source. :func:`_classify_device` is where a wrong
    token starts costing time."""
    presented = normalize_device_token(candidate)
    if not presented:
        return False
    row = (
        await session.execute(
            text("SELECT token_hash FROM household_device_tokens WHERE id = 1")
        )
    ).first()
    if row is None:
        return False
    return secrets.compare_digest(_sha256(presented), row.token_hash)


def write_device_token_file(token: str) -> Path:
    """Mirror the token to ``~/.domovoi/device-token.txt`` (0600) so
    clients on the server box — and the test harnesses — can present it
    without an admin session. Idempotent; rewritten on rotation."""
    return write_private_file(device_token_path(), token + "\n")


def read_device_token_file() -> str | None:
    try:
        token = device_token_path().read_text(encoding="utf-8").strip()
        return token or None
    except OSError:
        return None


async def ensure_device_token() -> str | None:
    """Boot hook shared by BOTH processes (core lifespan, next to the
    setup-code hook; web lifespan): make sure the household token row
    exists and that the file mirror carries it. Returns the token, or None
    when the database is unreachable or behind migrations (logged, never
    fatal — the gate then fails closed on its own)."""
    try:
        async with session_scope() as s:
            token = await ensure_device_token_row(s)
    except Exception as e:
        log.warning("device-token boot hook skipped (DB unreachable or not migrated): %s", e)
        return None
    try:
        if read_device_token_file() != token:
            write_device_token_file(token)
    except OSError as e:  # pragma: no cover — FS trouble
        log.warning("could not write %s: %s", device_token_path(), e)
    return token


def device_token_from_request(request: Request) -> str | None:
    value = request.headers.get(DEVICE_TOKEN_HEADER.lower()) or ""
    return value.strip() or None


async def list_sessions(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT token_hash, label, created_at, expires_at, last_used_at "
                "FROM admin_sessions ORDER BY created_at DESC"
            )
        )
    ).all()
    return [
        {
            "token_hash": r.token_hash,
            "label": r.label,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
        }
        for r in rows
    ]


async def reset_admin() -> str:
    """``--reset-admin`` (§7.2 password recovery): drop the credential
    and every session, regenerate + persist a fresh setup code. Returns
    the new code (the CLI prints it). The install is back in the
    pre-setup state: the daily surface keeps its LAN grace, the security
    tier (:func:`require_admin_security`) stays closed until setup
    completes again."""
    async with session_scope() as s:
        await s.execute(text("DELETE FROM admin_sessions"))
        await s.execute(text("DELETE FROM admin_auth"))
    code = generate_setup_code()
    write_setup_code(code)
    return code


# ─── Request-side extraction + the shared gate ────────────────────────────


def bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    return auth[7:].strip() or None


def cookie_token(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME) or None


def request_source(request: Request) -> str:
    """Backoff / rate-limit identity for a request.

    ``X-Forwarded-For`` is honored ONLY when the immediate transport peer
    is in :data:`TRUSTED_PROXIES` — otherwise the header is client-supplied
    and spoofable, and trusting it would let a LAN attacker rotate XFF for
    an unbounded supply of fresh zero-failure throttle buckets (defeating
    login backoff and the outbound-fetch limiter). The default trusts
    loopback only (the web process on the same box forwards each dashboard
    caller's real address); every other peer keys on itself. A legit
    reverse proxy is added to ``TRUSTED_PROXIES`` by the operator, at which
    point its forwarded client becomes the key."""
    peer = request.client.host if request.client else "unknown"
    fwd = request.headers.get("x-forwarded-for")
    if fwd and peer in TRUSTED_PROXIES:
        return fwd.split(",")[0].strip()
    return peer


CheckResult = Literal["ok", "pre-setup", "no-auth", "cookie-only", "invalid"]


async def check_admin_request(
    request: Request, session: AsyncSession | None = None
) -> CheckResult:
    """Classify a request against the admin tier.

    * ``ok`` — live Bearer session (expiry slid).
    * ``pre-setup`` — no admin credential exists yet; the caller decides
      whether its surface keeps the open LAN-trust grace or fails
      closed (plugin management does, design §7.1).
    * ``cookie-only`` — no/invalid Bearer but the dashboard cookie is
      present: enough to RENDER GET state, never to mutate (§7.3 CSRF).
    * ``no-auth`` / ``invalid`` — nothing usable.
    """

    async def _check(s: AsyncSession) -> CheckResult:
        if not await has_admin_auth(s):
            return "pre-setup"
        token = bearer_token(request)
        if token is not None:
            if await validate_token(s, token):
                return "ok"
            # An invalid Bearer with a valid cookie alongside still
            # classifies as invalid — the caller sent broken credentials.
            return "invalid"
        cookie = cookie_token(request)
        if cookie is not None and await validate_token(s, cookie):
            return "cookie-only"
        return "no-auth" if cookie is None else "invalid"

    try:
        if session is not None:
            return await _check(session)
        async with session_scope() as s:
            return await _check(s)
    except Exception as e:  # pragma: no cover — DB down ⇒ fail closed
        log.warning("admin check failed: %s", e)
        return "invalid"


async def require_admin_mutation(request: Request) -> None:
    """Dependency for gated MUTATING endpoints (§7.3 list): Bearer-only.
    Keeps the pre-setup LAN-trust grace (daily surfaces work on a fresh
    install; plugin management uses :func:`domovoi.auth.require_admin`
    which fails closed instead). A valid cookie without a Bearer is 403
    — cookies never authorize mutations."""
    result = await check_admin_request(request)
    if result in ("ok", "pre-setup"):
        return
    if result == "cookie-only":
        raise HTTPException(
            status_code=403,
            detail=(
                "mutations require Authorization: Bearer — the dashboard "
                "cookie only renders GET state"
            ),
        )
    raise HTTPException(status_code=401, detail="admin session required")


async def require_admin_read(request: Request) -> None:
    """Dependency for gated GET endpoints (config reads carry secrets,
    §7.3): Bearer OR the dashboard cookie renders state. Same pre-setup
    grace as the mutation gate."""
    result = await check_admin_request(request)
    if result in ("ok", "pre-setup", "cookie-only"):
        return
    raise HTTPException(status_code=401, detail="admin session required")


# ─── The security tier: admin, fail-closed before setup ───────────────────

_SECURITY_TIER_PRE_SETUP_DETAIL = (
    "auth not configured — complete the first-run admin setup before "
    "using this endpoint (it changes what the server runs or trusts)"
)


async def require_admin_security(request: Request) -> None:
    """Dependency for the SECURITY-TIER mutations: config write, service
    restart, satellite code push, pairing preseed / reset, satellite
    delete, device-token rotation. Bearer-only like
    :func:`require_admin_mutation`, but with NO pre-setup grace: 501 until
    an admin password exists, the posture plugin management has always
    had (:mod:`domovoi.auth`). ``--reset-admin`` returns the install to
    the pre-setup state, so it reopens only the daily surface."""
    result = await check_admin_request(request)
    if result == "ok":
        return
    if result == "pre-setup":
        raise HTTPException(status_code=501, detail=_SECURITY_TIER_PRE_SETUP_DETAIL)
    if result == "cookie-only":
        raise HTTPException(
            status_code=403,
            detail=(
                "mutations require Authorization: Bearer — the dashboard "
                "cookie only renders GET state"
            ),
        )
    raise HTTPException(status_code=401, detail="admin session required")


async def require_admin_security_read(request: Request) -> None:
    """The read half of the security tier (today: showing the household
    device token). Bearer OR cookie renders it; 501 before setup — the
    token has no value while everything is open, and it is rotated the
    moment setup completes, so nothing read earlier survives."""
    result = await check_admin_request(request)
    if result in ("ok", "cookie-only"):
        return
    if result == "pre-setup":
        raise HTTPException(status_code=501, detail=_SECURITY_TIER_PRE_SETUP_DETAIL)
    raise HTTPException(status_code=401, detail="admin session required")


# ─── The device tier: household token OR admin Bearer ─────────────────────

DeviceCheckResult = Literal[
    "ok", "admin", "pre-setup", "no-auth", "cookie-only", "invalid", "throttled"
]


async def _classify_device(
    conn: Any, presented: str | None, session: AsyncSession | None
) -> DeviceCheckResult:
    """Shared body of :func:`check_device_request` and
    :func:`check_device_websocket`. ``conn`` only has to provide the
    ``headers`` / ``cookies`` / ``client`` surface ``check_admin_request``
    and :func:`request_source` read, which both ``Request`` and
    ``WebSocket`` do.

    This is also where a wrong household token starts costing time
    (:data:`DEVICE_TOKEN_BACKOFF`). The rules, in the order they matter:

    * only a token that was actually PRESENTED and turned out to be wrong
      is charged — a request with no token at all is not a guess;
    * the attempt is reserved BEFORE the DB read, the same trick
      :func:`enforce_login_backoff` uses, so a burst of parallel guesses
      cannot all slip past the check before any of them is recorded;
    * a request that ALSO carries a live admin Bearer (or that lands on a
      pre-setup install) releases the reservation: the dashboard holding a
      stale token beside a good session is not guessing, and charging it
      would let the device tier lock an admin out of their own page;
    * while a source is throttled its token is not even looked at, and the
      throttled attempt adds nothing to the ladder — otherwise a client
      that retries on a timer could never drain its own backoff.
    """

    async def _check(s: AsyncSession) -> DeviceCheckResult:
        source = request_source(conn)
        throttled = False
        reserved = False
        if presented is not None:
            if DEVICE_TOKEN_BACKOFF.retry_after(source) > 0:
                throttled = True
            else:
                DEVICE_TOKEN_BACKOFF.reserve(source)
                reserved = True
                if await validate_device_token(s, presented):
                    DEVICE_TOKEN_BACKOFF.record_success(source)
                    return "ok"
        admin = await check_admin_request(conn, s)
        if admin in ("ok", "pre-setup"):
            if reserved:
                DEVICE_TOKEN_BACKOFF.record_success(source)
            return "admin" if admin == "ok" else "pre-setup"
        if reserved:
            DEVICE_TOKEN_BACKOFF.record_failure(source)
        if throttled:
            return "throttled"
        if presented is not None:
            return "invalid"
        return "cookie-only" if admin == "cookie-only" else admin

    try:
        if session is not None:
            return await _check(session)
        async with session_scope() as s:
            return await _check(s)
    except Exception as e:  # pragma: no cover — DB down ⇒ fail closed
        log.warning("device check failed: %s", e)
        return "invalid"


async def check_device_request(
    request: Request, session: AsyncSession | None = None
) -> DeviceCheckResult:
    """Classify a request against the device tier.

    * ``ok`` — a valid ``X-Device-Token``.
    * ``admin`` — no (valid) device token but a live admin Bearer.
    * ``pre-setup`` — no admin credential exists yet (LAN grace).
    * ``cookie-only`` — only the dashboard cookie: renders nothing on
      this tier (the dashboard learns the token after login and sends
      the header).
    * ``no-auth`` / ``invalid`` — nothing usable / a stale token.
    * ``throttled`` — this source has presented too many wrong tokens; the
      one it sent now was not even looked at (:data:`DEVICE_TOKEN_BACKOFF`).
    """
    return await _classify_device(
        request, device_token_from_request(request), session
    )


async def check_device_credential(
    conn: Any, presented: str | None, session: AsyncSession | None = None
) -> DeviceCheckResult:
    """:func:`check_device_request` for a household token the caller
    extracted itself — today the dashboard's ``domovoi.device-token.``
    WebSocket subprotocol, which only the web process knows how to read.
    Going through here rather than calling
    :func:`validate_device_token` directly is what keeps that transport
    behind the same throttle as the header and the query."""
    return await _classify_device(conn, presented, session)


# A browser WebSocket cannot set request headers, so the household token
# may ride in the query string on a WS UPGRADE only. Deliberately NOT
# accepted on HTTP routes: a query string lands in access logs, proxy
# logs and Referer headers, and every HTTP caller can set the header.
WS_TOKEN_QUERY_PARAM = "token"


def device_token_from_websocket(ws: Any) -> str | None:
    """The household token a WS upgrade presents: ``X-Device-Token``
    first (satellites and the Android app set it), then ``?token=`` (the
    dashboard's browser socket, which cannot)."""
    header = (ws.headers.get(DEVICE_TOKEN_HEADER.lower()) or "").strip()
    if header:
        return header
    try:
        query = (ws.query_params.get(WS_TOKEN_QUERY_PARAM) or "").strip()
    except Exception:  # pragma: no cover — scope without a query string
        return None
    return query or None


async def check_device_websocket(
    ws: Any, session: AsyncSession | None = None
) -> DeviceCheckResult:
    """:func:`check_device_request` for a WebSocket UPGRADE, with the
    ``?token=`` fallback. Same result vocabulary."""
    return await _classify_device(ws, device_token_from_websocket(ws), session)


async def websocket_device_ok(ws: Any) -> bool:
    """True when a WS upgrade may proceed on the device tier: a valid
    household token, an admin Bearer, or the pre-setup LAN grace. A
    throttled source is refused like any other bad credential — a socket
    has no status code to carry the 429 into."""
    return await check_device_websocket(ws) in ("ok", "admin", "pre-setup")


async def require_device(request: Request) -> None:
    """Dependency for the DEVICE TIER (ordinary household actions): a
    valid ``X-Device-Token`` OR an admin Bearer passes; nothing, a stale
    token or the cookie alone does not. Keeps the pre-setup LAN grace so
    a fresh install (and a throwaway test instance) works before setup.

    A source that keeps presenting wrong tokens gets 429 with a
    ``Retry-After`` instead of 401 once its backoff bites."""
    result = await check_device_request(request)
    if result in ("ok", "admin", "pre-setup"):
        return
    if result == "throttled":
        raise device_token_throttled_error(request)
    if result == "cookie-only":
        raise HTTPException(
            status_code=403,
            detail=(
                f"{DEVICE_TOKEN_HEADER} required — the dashboard cookie "
                "does not authorize device-tier actions"
            ),
        )
    raise HTTPException(
        status_code=401,
        detail=f"{DEVICE_TOKEN_HEADER} or admin session required",
    )


# Query parameter carrying the household token for the one class of caller
# that cannot set a header: bytes the BROWSER fetches by URL (``<img src>``,
# ``<video src>``, ``window.open``) and the Android media loaders. Honored
# ONLY by :func:`require_device_read` — never by a gate that authorizes a
# change.
DEVICE_TOKEN_QUERY = "device_token"


async def require_device_read(request: Request) -> None:
    """Dependency for DEVICE-TIER READS — the media serves the dashboard
    points at rather than fetches: thumbnails, posters, video streams,
    image/document raw serves, file downloads.

    Accepts everything :func:`require_device` does, plus two credentials a
    read may safely take and a mutation may not:

    * the **dashboard cookie** — a GET that only renders bytes carries no
      CSRF risk (that is why ``require_admin_read`` accepts it too), and a
      browser that has just reloaded holds the cookie and nothing else
      until the operator signs in again;
    * ``?device_token=`` — an ``<img>``/``<video>``/``window.open`` request
      cannot carry a header at all, so the household token may ride in the
      query for these reads. It is the same secret either way; putting it
      in a URL costs referrer/-log exposure, which is why nothing that
      writes will look at it.

    Nothing here loosens :func:`require_device`: no credential at all is
    still 401.
    """
    result = await check_device_request(request)
    if result in ("ok", "admin", "pre-setup", "cookie-only"):
        return
    candidate = (request.query_params.get(DEVICE_TOKEN_QUERY) or "").strip()
    if candidate and result != "throttled":
        # Through the classifier, not straight to validate_device_token:
        # the query is a full-strength presentation of the household token
        # and has to sit behind the same backoff as the header.
        result = await check_device_credential(request, candidate)
        if result in ("ok", "admin", "pre-setup", "cookie-only"):
            return
    if result == "throttled":
        raise device_token_throttled_error(request)
    raise HTTPException(
        status_code=401,
        detail=f"{DEVICE_TOKEN_HEADER} or admin session required",
    )


# ─── The chat-tool callback tier: a per-boot shared secret ────────────────

# Minted once per process start by :func:`chat_callback_secret`. It is NOT
# persisted: the value only has to outlive the tool sources generated from
# it, and those are regenerated whenever the tool surface is resynced.
_CHAT_CALLBACK_SECRET: str | None = None


def chat_callback_secret() -> str:
    """The secret this boot embeds in the generated chat proxy-tool source
    (``letta_tools._proxy_source``) and expects back on
    ``POST /v1/admin/chat-tool``.

    Minted lazily on first use and stable for the life of the process. A
    restart mints a fresh one, so the tool sources have to be regenerated
    (``POST /v1/admin/chat/resync``, which every plugin lifecycle change
    already runs) before chat tools work again — that is the price of the
    secret never touching disk."""
    global _CHAT_CALLBACK_SECRET
    if _CHAT_CALLBACK_SECRET is None:
        _CHAT_CALLBACK_SECRET = secrets.token_hex(32)
    return _CHAT_CALLBACK_SECRET


def reset_chat_callback_secret() -> str:
    """Mint a fresh secret (tests; a deliberate re-key). Returns it."""
    global _CHAT_CALLBACK_SECRET
    _CHAT_CALLBACK_SECRET = None
    return chat_callback_secret()


def chat_callback_from_request(request: Request) -> str | None:
    value = request.headers.get(CHAT_CALLBACK_HEADER.lower()) or ""
    return value.strip() or None


async def require_chat_callback(request: Request) -> None:
    """Dependency for the chat agent's callback endpoint: the caller must
    present this boot's :func:`chat_callback_secret` in
    ``X-Chat-Callback``. The agent's tools run in Letta's own sandbox on
    the LAN, so they can't hold an admin Bearer — the secret is what
    distinguishes tool source THIS core generated from anything else that
    can reach the port. Constant-time compare; 401 otherwise."""
    presented = chat_callback_from_request(request)
    if presented is not None and secrets.compare_digest(
        presented, chat_callback_secret()
    ):
        return
    raise HTTPException(
        status_code=401,
        detail=f"{CHAT_CALLBACK_HEADER} required — regenerate the chat tools",
    )


# ─── Per-source login backoff (in-memory, §7.3) ───────────────────────────


class LoginBackoff:
    """Exponential per-source failed-login throttle. In-memory by design
    (restart resets — accepted in §7.3; argon2id keeps offline guessing
    expensive). Per SOURCE, never shared: host A hammering the endpoint
    can't lock out host B.

    A GLOBAL sliding-window ceiling sits on top as a v1 backstop: because
    the per-source backoff can be sidestepped by an attacker who rotates
    the source key, ``retry_after`` also throttles once aggregate failures
    within :data:`GLOBAL_LOGIN_WINDOW_SEC` cross
    :data:`GLOBAL_LOGIN_MAX_FAILURES`, until the window drains.

    Two knobs exist for the second user of this class, the household
    device-token throttle (:data:`DEVICE_TOKEN_BACKOFF`); both default to
    the login's behaviour, so the login instance is unchanged:

    * ``free_attempts`` — failures charged nothing before the doubling
      starts.
    * ``global_ceiling`` — whether the aggregate backstop applies at all.
    """

    def __init__(
        self, *, free_attempts: int = 0, global_ceiling: bool = True
    ) -> None:
        self.free_attempts = free_attempts
        self.global_ceiling = global_ceiling
        # source → (consecutive_failures, last_failure_monotonic)
        self._failures: dict[str, tuple[int, float]] = {}
        # Monotonic timestamps of ALL recent failures (any source) for the
        # global ceiling. Pruned to the window on read/write.
        self._global: list[float] = []
        # source -> the global timestamp of its outstanding reservation
        self._reserved: dict[str, float] = {}

    def _prune_global(self, now: float) -> None:
        cutoff = now - GLOBAL_LOGIN_WINDOW_SEC
        if self._global and self._global[0] < cutoff:
            self._global = [t for t in self._global if t >= cutoff]

    def _global_retry_after(self, now: float) -> float:
        if not self.global_ceiling:
            return 0.0
        self._prune_global(now)
        if len(self._global) < GLOBAL_LOGIN_MAX_FAILURES:
            return 0.0
        # Ceiling tripped — blocked until the oldest in-window failure ages
        # out and drops the count back under the ceiling.
        return max(0.0, (self._global[0] + GLOBAL_LOGIN_WINDOW_SEC) - now)

    def retry_after(self, source: str) -> float:
        """Seconds the source must still wait, or 0.0 when allowed — the
        greater of the per-source backoff and the global ceiling."""
        now = time.monotonic()
        wait = self._global_retry_after(now)
        entry = self._failures.get(source)
        if entry is not None:
            failures, last = entry
            charged = failures - self.free_attempts
            if charged > 0:
                delay = min(BACKOFF_BASE_SEC * (2 ** (charged - 1)), BACKOFF_CAP_SEC)
                wait = max(wait, (last + delay) - now)
        return max(0.0, wait)

    def _count(self, source: str) -> float:
        now = time.monotonic()
        failures, _ = self._failures.get(source, (0, 0.0))
        self._failures[source] = (failures + 1, now)
        self._global.append(now)
        self._prune_global(now)
        return now

    def record_failure(self, source: str) -> None:
        """A confirmed failure. If the attempt was :meth:`reserve`d this
        is a no-op on the counters — the reservation already counted it."""
        if self._reserved.pop(source, None) is None:
            self._count(source)

    def reserve(self, source: str) -> None:
        """Count the attempt BEFORE the expensive verify. The check in
        :meth:`retry_after` and the record used to sit either side of an
        ``await`` (the DB read + argon2), so several attempts from one
        source could all pass the check before any of them was recorded.
        Reserving up front closes that window: the attempt is a failure
        until :meth:`record_success` says otherwise."""
        self._reserved[source] = self._count(source)

    def record_success(self, source: str) -> None:
        # Clears the source's OWN streak. A reservation this attempt made
        # was not a failure after all, so it leaves the global window too;
        # earlier CONFIRMED failures from this source stay there — that
        # ceiling is an aggregate-abuse backstop and drains only on its
        # own clock, so a single success can't reset it.
        self._failures.pop(source, None)
        reserved = self._reserved.pop(source, None)
        if reserved is not None:
            try:
                self._global.remove(reserved)
            except ValueError:
                pass

    def reset(self) -> None:
        self._failures.clear()
        self._global.clear()
        self._reserved.clear()


LOGIN_BACKOFF = LoginBackoff()

# The same machinery, a SEPARATE ledger, for wrong household device
# tokens (:func:`_classify_device`). Separate because the two tiers must
# not throttle each other: a phone left holding a rotated token would
# otherwise push the person's own admin login behind a wait, and a
# guesser on the device tier could lock the admin out of the very page
# that fixes it.
#
# The aggregate ceiling is OFF here, deliberately, and this is the one
# place the two instances differ in posture. The device tier is the
# ORDINARY household surface: an aggregate ceiling means any host that
# can reach the port can put the whole house — every phone, every
# browser, every satellite — behind a 429 by sending rubbish tokens from
# one address. That trade is worth taking for the admin password, which
# is the one credential a slow remote guess could plausibly reach; it is
# not worth taking for a 64-bit phrase whose per-source ladder already
# caps a guesser at roughly 17 tries an hour. Rotating source addresses
# does not help an attacker here: a /24 of spoofed peers buys ~4000
# guesses an hour against 2**64.
DEVICE_TOKEN_BACKOFF = LoginBackoff(
    free_attempts=DEVICE_TOKEN_FREE_ATTEMPTS, global_ceiling=False
)


def device_token_retry_after(conn: Any) -> float:
    """Seconds this source must wait before another household token of
    its will even be looked at."""
    return DEVICE_TOKEN_BACKOFF.retry_after(request_source(conn))


def device_token_throttled_error(conn: Any) -> HTTPException:
    """The 429 a throttled device-tier caller gets, with ``Retry-After``."""
    wait = device_token_retry_after(conn)
    return HTTPException(
        status_code=429,
        detail=(
            f"too many wrong {DEVICE_TOKEN_HEADER} values — retry in "
            f"{wait:.0f}s, or sign in as an admin"
        ),
        headers={"Retry-After": str(max(1, int(wait + 0.999)))},
    )


def enforce_login_backoff(request: Request) -> str:
    """Raise 429 (with Retry-After) while the source is throttled;
    otherwise RESERVE the attempt (it counts as a failure from this
    moment, before any ``await`` or argon2 work) and return the source
    key. The caller reports the outcome with ``LOGIN_BACKOFF.record_success``
    (releases the reservation) or ``record_failure`` (confirms it)."""
    source = request_source(request)
    wait = LOGIN_BACKOFF.retry_after(source)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"too many failed attempts — retry in {wait:.0f}s",
            headers={"Retry-After": str(max(1, int(wait + 0.999)))},
        )
    LOGIN_BACKOFF.reserve(source)
    return source


# ─── Outbound-fetch rate limit (in-memory, §7.3 / §4.8) ───────────────────


class SlidingWindowLimiter:
    """Per-source request budget in a sliding window. Guards the
    outbound-fetch tier (server fetches a caller-chosen URL) for callers
    WITHOUT an admin session."""

    def __init__(
        self,
        max_per_window: int = URL_FETCH_MAX_PER_WINDOW,
        window_sec: float = URL_FETCH_WINDOW_SEC,
    ) -> None:
        self.max_per_window = max_per_window
        self.window_sec = window_sec
        self._hits: dict[str, list[float]] = {}

    def allow(self, source: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(source, []) if now - t < self.window_sec]
        if len(hits) >= self.max_per_window:
            self._hits[source] = hits
            return False
        hits.append(now)
        self._hits[source] = hits
        return True

    def reset(self) -> None:
        self._hits.clear()


URL_FETCH_LIMITER = SlidingWindowLimiter()


@dataclass(frozen=True)
class OutboundFetchDecision:
    allowed: bool
    status: int = 200
    detail: str = ""


async def check_outbound_fetch(
    request: Request, url: str, *, url_allowed_by_fulfillers
) -> OutboundFetchDecision:
    """The §7.3 outbound-fetch tier for add-by-url style endpoints:
    an admin session passes outright; otherwise the URL must satisfy a
    registered fulfiller's ``url_matcher`` allowlist AND the per-source
    rate limit. ``url_allowed_by_fulfillers`` is injected (a callable
    ``str -> bool``) so this module stays importable by the web process
    without dragging in the core acquisition service."""
    result = await check_admin_request(request)
    if result == "ok":
        return OutboundFetchDecision(True)
    source = request_source(request)
    if not URL_FETCH_LIMITER.allow(source):
        return OutboundFetchDecision(
            False, 429, "too many URL requests — slow down or log in as admin"
        )
    try:
        matched = bool(url_allowed_by_fulfillers(url))
    except Exception as e:  # pragma: no cover — matcher bug
        log.warning("fulfiller url matcher raised: %s", e)
        matched = False
    if matched:
        return OutboundFetchDecision(True)
    return OutboundFetchDecision(
        False,
        403,
        (
            "adding by URL requires an admin session, or a URL matching an "
            "installed media provider"
        ),
    )
