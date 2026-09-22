"""Create ``domovoi/.env`` for a FRESH checkout — never rewrite an existing one.

``dev.sh`` / ``dev.ps1`` run ``python -m domovoi.env_bootstrap`` before the
first ``docker compose up``. On a checkout that has no ``.env`` yet this
copies ``.env.example`` into place with one change: the Postgres credential
is a freshly generated random password instead of the historical default.
Both ``DATABASE_URL`` (what the core and the web backend connect with) and
``POSTGRES_PASSWORD`` (what ``docker-compose.yml`` hands Postgres on initdb
and Flyway on every migration) carry the same value, so the database is
initialised with a password only this machine knows.

Contract:
  * An existing ``.env`` is NEVER touched — not merged, not re-ordered, not
    re-encoded. The dev install and the appliance keep whatever they have;
    their ``docker-compose.yml`` falls back to the historical default when
    ``POSTGRES_PASSWORD`` is absent, so an old ``.env`` still matches its
    old volume. The file is opened with ``O_EXCL`` so even a race with a
    concurrent writer cannot clobber it.
  * A missing ``.env`` next to an EXISTING ``domovoi-pgdata`` volume (a
    re-clone beside a database that was initialised earlier) gets the
    historical default rather than a random value — otherwise the new
    ``DATABASE_URL`` would not match the password the volume was created
    with. The file says so and points at the rotation runbook.
  * Rotation is an operator step, documented in docs/LINUX_HOST.md
    ("Rotating the Postgres password"); this module never runs it.

DB-free and import-light on purpose: it runs before Postgres exists.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
ENV_FILE = _PKG_DIR / ".env"
ENV_EXAMPLE = _PKG_DIR / ".env.example"

# The credential `.env.example` ships, which is also the compose fallback
# (`${POSTGRES_PASSWORD:-domovoi}`) and the `Settings.database_url` default.
DEFAULT_PASSWORD = "domovoi"
PGDATA_VOLUME = "domovoi-pgdata"

# `postgresql+asyncpg://domovoi:domovoi@` in DATABASE_URL / TEST_DATABASE_URL
# lines, commented or not. Only the password segment is rewritten.
_DSN_CRED_RE = re.compile(
    r"^(?P<lead>[ \t]*#?[ \t]*(?:TEST_)?DATABASE_URL=postgresql\+asyncpg://domovoi:)"
    r"domovoi(?P<tail>@)",
    re.MULTILINE,
)


def generate_password() -> str:
    """32 URL-safe characters (``[A-Za-z0-9_-]``): valid unescaped inside
    the asyncpg DSN, on Flyway's ``-password=`` argument and in a Compose
    ``.env`` line (no ``$``, ``#``, quotes or whitespace)."""
    return secrets.token_urlsafe(24)


def _generated_block(password: str, *, reused_default: bool) -> str:
    if reused_default:
        why = (
            "# The `domovoi-pgdata` Docker volume already existed when this file was\n"
            "# created, so the HISTORICAL DEFAULT is kept to match the password that\n"
            "# database was initialised with. Rotate it: docs/LINUX_HOST.md,\n"
            "# \"Rotating the Postgres password\".\n"
        )
    else:
        why = (
            "# Generated for this machine on first bootstrap; DATABASE_URL above\n"
            "# carries the same value. docker-compose.yml reads this line when it\n"
            "# initialises the `domovoi-pgdata` volume and on every Flyway run.\n"
            "# Rotation (change it in Postgres, then here in BOTH lines, then\n"
            "# restart the core and the web backend): docs/LINUX_HOST.md,\n"
            "# \"Rotating the Postgres password\".\n"
        )
    return (
        "\n"
        "# ─── Postgres credential (written by `python -m domovoi.env_bootstrap`) ──\n"
        + why
        + f"POSTGRES_PASSWORD={password}\n"
    )


def render_fresh_env(template: str, password: str, *, reused_default: bool = False) -> str:
    """The text of a brand-new ``.env``: the example with the DSN password
    swapped for ``password`` and a ``POSTGRES_PASSWORD`` block appended."""
    body, n = _DSN_CRED_RE.subn(rf"\g<lead>{password}\g<tail>", template)
    if n == 0:
        raise ValueError(".env.example no longer carries the default DATABASE_URL credential")
    if not body.endswith("\n"):
        body += "\n"
    return body + _generated_block(password, reused_default=reused_default)


def _pgdata_volume_exists() -> bool:
    """True when Docker already has the core's data volume. Best effort:
    no docker, or any error, reads as "no volume" (fresh)."""
    try:
        proc = subprocess.run(
            ["docker", "volume", "inspect", PGDATA_VOLUME],
            capture_output=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


@dataclass(frozen=True)
class BootstrapResult:
    path: Path
    created: bool            # False: a file was already there and was left alone
    reused_default: bool     # True: kept the template credential (volume pre-existed)

    def __bool__(self) -> bool:
        return self.created


def ensure_env_file(
    env_path: Path | None = None,
    example_path: Path | None = None,
    *,
    password: str | None = None,
    volume_exists: Callable[[], bool] | None = None,
) -> BootstrapResult:
    """Create ``env_path`` from ``example_path`` if — and only if — it does
    not exist. The result is truthy when a file was written and falsy when
    an existing one was left alone. Never modifies an existing file."""
    env_path = env_path or ENV_FILE
    example_path = example_path or ENV_EXAMPLE
    if env_path.exists():
        return BootstrapResult(env_path, created=False, reused_default=False)

    with open(example_path, "r", encoding="utf-8", newline="") as fh:
        template = fh.read()

    reused_default = False
    if password is None:
        probe = volume_exists or _pgdata_volume_exists
        if probe():
            password = DEFAULT_PASSWORD
            reused_default = True
        else:
            password = generate_password()

    content = render_fresh_env(template, password, reused_default=reused_default)

    # O_EXCL: create-only. A file that appears between the exists() check
    # and here wins; we never truncate. 0o600 because the file now holds a
    # credential (mode is advisory on Windows).
    env_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return BootstrapResult(env_path, created=False, reused_default=False)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return BootstrapResult(env_path, created=True, reused_default=reused_default)


def main(argv: list[str] | None = None) -> int:
    result = ensure_env_file()
    if not result.created:
        print(f"{result.path} exists; left untouched")
    elif result.reused_default:
        print(
            f"created {result.path} with the template Postgres credential: the "
            f"`{PGDATA_VOLUME}` volume already exists. Rotate it — "
            "docs/LINUX_HOST.md, \"Rotating the Postgres password\"."
        )
    else:
        print(f"created {result.path} (fresh install: random Postgres password written)")
    if result.created:
        # CORE-9. The example ships SATELLITE_PAIRING_STRICT=true, so the
        # household this file just created starts closed. Say so here
        # rather than in dev.sh/dev.ps1, so both scripts — and anyone who
        # runs the module directly — report the same posture.
        print(
            "  satellite pairing is STRICT: a new satellite parks for approval\n"
            "  on the dashboard. Set SATELLITE_PAIRING_STRICT=false for the older\n"
            "  trust-on-first-use behaviour."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
