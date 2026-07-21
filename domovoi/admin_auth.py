"""Admin authentication — the v1 lightweight model (design §7.2/§7.3,
V1 scope amendment).

Shared by BOTH processes (core :6370 and web :6369): every primitive
here works against the same Postgres ``admin_auth`` / ``admin_sessions``
tables, so a token minted by the web dashboard's login endpoint
authorizes gated routes on the core too. The module deliberately never
imports ``domovoi.plugins_runtime`` — the web process must stay outside
the plugin runtime (design §5.1).

The model, in one paragraph: first boot writes an 8-word **setup code**
to ``~/.domovoi/setup-code.txt`` and prints it to the core console —
proof of possession of the server. ``POST /api/auth/setup`` requires
that code, hashes the chosen password with **argon2id**, and deletes
the code file. ``POST /api/auth/login`` verifies the password (behind
a per-source exponential backoff) and mints a 256-bit bearer token of
which only the sha256 is stored, with a **30-day sliding expiry**.
Mutating admin endpoints accept ONLY ``Authorization: Bearer`` — the
``SameSite=Strict`` cookie set at login exists solely so plain GET page
loads can render authenticated state (CSRF: a cross-site POST carries
nothing that authorizes it). ``python -m domovoi.main --reset-admin``
clears the credential + sessions and regenerates the setup code.

DEFERRED (documented hardening backlog, scope amendment): TLS /
fingerprint pinning and satellite pairing tokens. v1 admin flows run
over plain LAN HTTP.
"""

from __future__ import annotations

import hashlib
import logging
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
# Failed-login backoff: 1 s doubling per failure, capped at 5 min (§7.3).
BACKOFF_BASE_SEC = 1.0
BACKOFF_CAP_SEC = 300.0
# Outbound-fetch tier (add-by-url without an admin session): per-source
# request budget within the window.
URL_FETCH_WINDOW_SEC = 60.0
URL_FETCH_MAX_PER_WINDOW = 10

# Trusted immediate peers whose ``X-Forwarded-For`` we honor for the
# throttle identity. EMPTY by default (v1 has no TLS/reverse proxy — see
# the DEFERRED note above): with nothing trusted, a client-supplied XFF is
# ignored and throttling keys on the real transport peer, so a LAN attacker
# can't mint unlimited fresh zero-failure buckets by rotating the header.
# An operator fronting the core with a real proxy adds that proxy's LAN
# address here (e.g. {"127.0.0.1", "::1"}).
TRUSTED_PROXIES: set[str] = set()

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


# ─── Setup code (proof of possession of the server, §7.2) ─────────────────

# 256 short common words → 8 words = 64 bits of entropy. Plain ASCII so
# the code survives any console / copy-paste path (Windows cp1252 hosts).
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


def generate_setup_code() -> str:
    return "-".join(secrets.choice(_WORDS[:256]) for _ in range(8))


def write_setup_code(code: str) -> Path:
    path = setup_code_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code + "\n", encoding="utf-8")
    return path


def read_setup_code() -> str | None:
    try:
        code = setup_code_path().read_text(encoding="utf-8").strip()
        return code or None
    except OSError:
        return None


def delete_setup_code() -> None:
    try:
        setup_code_path().unlink(missing_ok=True)
    except OSError as e:  # pragma: no cover — FS trouble
        log.warning("could not delete setup code file: %s", e)


def verify_setup_code(candidate: str) -> bool:
    """Constant-time compare of the presented code against the file. A
    missing/empty file always fails — no code, no setup."""
    actual = read_setup_code()
    if actual is None:
        return False
    return secrets.compare_digest(candidate.strip().encode(), actual.encode())


async def ensure_setup_code_if_unclaimed() -> str | None:
    """Core-boot hook (§7.2): when no admin credential exists yet, make
    sure a setup code file exists and print the code to the console so
    the operator can complete first-run setup. Reuses an existing file's
    code (a restart must not invalidate the code the operator already
    read). Returns the active code, or None when setup is complete."""
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
    (§7.3: 30-day sliding) and stamps ``last_used_at`` on success."""
    if not token:
        return False
    row = (
        await session.execute(
            text(
                "UPDATE admin_sessions SET last_used_at = now(), "
                f"expires_at = now() + interval '{SESSION_TTL_DAYS} days' "
                "WHERE token_hash = :h AND expires_at > now() RETURNING 1"
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
    the new code (the CLI prints it)."""
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
    login backoff and the outbound-fetch limiter). With no trusted proxy
    configured (the v1 default), throttling always keys on the real peer.
    A legit reverse proxy is added to ``TRUSTED_PROXIES`` by the operator,
    at which point its forwarded client becomes the key."""
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
    :data:`GLOBAL_LOGIN_MAX_FAILURES`, until the window drains."""

    def __init__(self) -> None:
        # source → (consecutive_failures, last_failure_monotonic)
        self._failures: dict[str, tuple[int, float]] = {}
        # Monotonic timestamps of ALL recent failures (any source) for the
        # global ceiling. Pruned to the window on read/write.
        self._global: list[float] = []

    def _prune_global(self, now: float) -> None:
        cutoff = now - GLOBAL_LOGIN_WINDOW_SEC
        if self._global and self._global[0] < cutoff:
            self._global = [t for t in self._global if t >= cutoff]

    def _global_retry_after(self, now: float) -> float:
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
            delay = min(BACKOFF_BASE_SEC * (2 ** (failures - 1)), BACKOFF_CAP_SEC)
            wait = max(wait, (last + delay) - now)
        return max(0.0, wait)

    def record_failure(self, source: str) -> None:
        now = time.monotonic()
        failures, _ = self._failures.get(source, (0, 0.0))
        self._failures[source] = (failures + 1, now)
        self._global.append(now)
        self._prune_global(now)

    def record_success(self, source: str) -> None:
        # Clears the source's OWN streak; the global ceiling is an
        # aggregate-abuse backstop and drains only on its own window, so a
        # single success can't reset it.
        self._failures.pop(source, None)

    def reset(self) -> None:
        self._failures.clear()
        self._global.clear()


LOGIN_BACKOFF = LoginBackoff()


def enforce_login_backoff(request: Request) -> str:
    """Raise 429 (with Retry-After) while the source is throttled;
    return the source key for the subsequent record_* call."""
    source = request_source(request)
    wait = LOGIN_BACKOFF.retry_after(source)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"too many failed attempts — retry in {wait:.0f}s",
            headers={"Retry-After": str(max(1, int(wait + 0.999)))},
        )
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
