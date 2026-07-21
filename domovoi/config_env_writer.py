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
