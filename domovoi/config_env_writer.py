"""Atomic merge-writer for ``domovoi/.env``.

The web settings editor persists changes here so they survive a restart.
``.env`` is the source of truth (pydantic-settings loads it at startup);
in a stock install the file usually does NOT exist yet, so the writer
creates it on first save.

Contract:
  * Comment, blank, and unrecognized lines are preserved VERBATIM — we
    never clobber operator notes or env-only secrets we don't manage.
  * Only the ``KEY=...`` lines whose key matches a change are rewritten,
    in place. Keys not already present are appended in a labeled block.
  * The write is atomic (temp file + ``os.replace``) so a crash mid-write
    can't leave a half-written ``.env`` that breaks the next boot.

Note on precedence: pydantic-settings reads a real OS environment variable
AHEAD of the ``.env`` file. If a field is also exported in the process
environment, the ``.env`` value written here is shadowed until that export
is removed. Live edits still take effect immediately (we mutate the
``settings`` singleton); only the across-restart persistence is affected.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from datetime import date
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parent / ".env"


def _format_value(value: object) -> str:
    """Render a Python value as a .env right-hand side, quoting when the
    value contains whitespace / comment chars so python-dotenv parses it
    back intact."""
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value)
    if s == "" or any(c.isspace() for c in s) or "#" in s or '"' in s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def next_boot_value(name: str, current: object, env_path: Path | None = None) -> object:
    """The value a restart-tier setting will have after the next restart.

    The live ``settings`` singleton still holds the boot-time value of a
    restart-tier field after a save (it is only persisted, not applied),
    so a check that has to agree with a PENDING change reads it the way
    the next boot will: a real environment variable first (it shadows
    ``.env``, see the module docstring), then the ``.env`` line, and
    ``current`` when neither sets it."""
    key = name.upper()
    for env_key, value in os.environ.items():
        if env_key.upper() == key:
            return value
    path = env_path or _ENV_FILE
    try:
        from dotenv import dotenv_values

        for env_key, value in dotenv_values(path).items():
            if env_key.upper() == key and value is not None:
                return value
    except (OSError, ValueError):  # unreadable, or not UTF-8
        pass
    return current


def write_env_values(
    changes: dict[str, object], env_path: Path | None = None
) -> None:
    """Merge ``changes`` (Settings field name → value) into the .env file,
    creating it if absent. See module docstring for the contract."""
    if not changes:
        return
    path = env_path or _ENV_FILE
    pending = {k.upper(): _format_value(v) for k, v in changes.items()}

    existing: list[str] = []
    if path.exists():
        existing = path.read_text(encoding="utf-8").splitlines()

    out: list[str] = []
    seen: set[str] = set()
    for line in existing:
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            up = key.upper()
            if up in pending:
                out.append(f"{key}={pending[up]}")
                seen.add(up)
                continue
        out.append(line)

    appended = [up for up in pending if up not in seen]
    if appended:
        if out and out[-1].strip() != "":
            out.append("")
        out.append("# Written by the web settings editor.")
        out.extend(f"{up}={pending[up]}" for up in appended)

    content = "\n".join(out) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def env_file_keys(env_path: Path | None = None) -> set[str]:
    """The UPPER-cased keys of every uncommented ``KEY=...`` line in the
    ``.env`` (empty when the file is missing or unreadable). Together with
    the process environment this is what "set by hand" means for a
    setting whose default follows the internet answer: a dashboard save
    writes a line here, and "follow the answer again" comments it out."""
    path = env_path or _ENV_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    keys: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key.lower().startswith("export "):
            key = key[len("export "):].strip()
        if key:
            keys.add(key.upper())
    return keys


def remove_env_keys(names: Iterable[str], env_path: Path | None = None) -> list[str]:
    """COMMENT OUT — never delete — every uncommented ``KEY=...`` line whose
    key matches one of ``names`` (case-insensitively). Each becomes
    ``# KEY=value  (follows INTERNET_ACCESS since <YYYY-MM-DD>)``, so the
    old value stays visible to whoever reads the file and is one edit away
    from coming back. Same atomic temp-file + ``os.replace`` write as
    :func:`write_env_values`; the file's own line endings are kept.

    Returns the UPPER keys commented out. A missing file is a no-op that
    returns []."""
    wanted = {str(n).upper() for n in names if str(n).strip()}
    path = env_path or _ENV_FILE
    if not wanted or not path.exists():
        return []
    raw = path.read_bytes().decode("utf-8")
    lines = raw.splitlines(keepends=True)
    stamp = date.today().isoformat()
    out: list[str] = []
    removed: list[str] = []
    for line in lines:
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        stripped = body.lstrip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            bare = key[len("export "):].strip() if key.lower().startswith("export ") else key
            if bare.upper() in wanted:
                out.append(f"# {stripped}  (follows INTERNET_ACCESS since {stamp}){ending}")
                if bare.upper() not in removed:
                    removed.append(bare.upper())
                continue
        out.append(line)
    if not removed:
        return []
    content = "".join(out)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return removed
