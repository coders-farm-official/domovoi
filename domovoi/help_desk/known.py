"""The values this install holds that help must never show (exact match).

:func:`gather` reads them for one request; :func:`domovoi.help_desk.redact.known_values`
makes every :func:`~domovoi.help_desk.redact.redact` in that request scrub
them. Read each time (a person added, a token rotated, a satellite on a new
network is scrubbed at once), and never kept or returned anywhere:

* the household token, the setup code, the admin password's hash (never the
  password, which Domovoi doesn't have), admin session, satellite pairing
  and household token hashes;
* the Wi-Fi network names, host names and gateways the satellites reported
  in their health samples (V022) and the live snapshot;
* this server's host name, and the host and domain of every configured URL
  (``settings``: Ollama, SearXNG, Letta, the database, the advertised core
  address…, and ``DOMOVOI_URL``);
* people's names (whole, and each part of three letters or more);
* every secret setting (``config_schema.SECRET_SETTING_NAMES``: the
  AcoustID key, the Letta token, the database URL's password…, and any
  setting whose name says key, token, secret or password), and every plugin
  setting marked ``kind="secret"`` (the core's plugin config registry) or
  named like one in a plugin's ``~/.domovoi/plugins/<slug>.env``.

Room names are not here: the household sees them everywhere already; the
problem report replaces them itself unless the person keeps them.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import socket
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from sqlalchemy import text

log = logging.getLogger(__name__)


def _url_values(url: str) -> list[str]:
    try:
        parts = urlsplit(url)
    except ValueError:
        return []
    out: list[str] = []
    # Credentials in a URL are secret wherever it points.
    try:
        if parts.password:
            out.append(parts.password)
    except ValueError:
        pass
    host = parts.hostname or ""
    if not host or host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"):
        return out
    out.append(host)
    labels = host.split(".")
    if len(labels) >= 2 and not host.replace(".", "").isdigit():
        out.append(".".join(labels[-2:]))   # the domain
        out.append(labels[0])               # the bare host
    if parts.username and parts.username not in ("domovoi",):
        out.append(parts.username)
    return out


def config_values(settings: Any = None) -> list[str]:
    """This server's own names, and those in its configured URLs."""
    out: list[str] = []
    for name in (socket.gethostname(), platform.node()):
        if name:
            out += [name, name.split(".")[0]]
    if settings is None:
        try:
            from domovoi.config import settings as settings  # noqa: PLW0127
        except Exception:  # noqa: BLE001
            settings = None
    urls: list[str] = [os.environ.get("DOMOVOI_URL", "")]
    if settings is not None:
        try:
            dumped = settings.model_dump()
        except Exception:  # noqa: BLE001
            dumped = {}
        urls += [v for v in dumped.values() if isinstance(v, str) and "://" in v]
    for u in urls:
        out += _url_values(u)
    # The secret settings are configured values too (core and plugins').
    out += secret_values(settings)
    return out


_SECRETISH = re.compile(r"(?i)(?:^|_)(?:api_?key|key|token|secret|password|passwd|pass|psk|credentials?)$")


def secret_values(settings: Any = None) -> list[str]:
    """The values of every secret setting, core and plugins'."""
    out: list[str] = []
    if settings is None:
        try:
            from domovoi.config import settings as settings  # noqa: PLW0127
        except Exception:  # noqa: BLE001
            settings = None
    try:
        from domovoi.config_schema import SECRET_SETTING_NAMES
    except Exception:  # noqa: BLE001
        SECRET_SETTING_NAMES = frozenset()  # noqa: N806
    if settings is not None:
        try:
            dumped = settings.model_dump()
        except Exception:  # noqa: BLE001
            dumped = {}
        for name, value in dumped.items():
            if not isinstance(value, str) or not value.strip():
                continue
            if name in SECRET_SETTING_NAMES or _SECRETISH.search(name):
                out.append(value)
                if "://" in value:
                    out += _url_values(value)
    # Plugins: fields marked secret where the registry lives (the core) …
    try:
        from domovoi.plugins_runtime.config_bridge import PLUGIN_CONFIG

        for cfg in list(getattr(PLUGIN_CONFIG, "_configs", {}).values()):
            for spec in cfg.fieldspecs:
                if spec.kind == "secret":
                    v = getattr(cfg.instance, spec.name, None)
                    if isinstance(v, str) and v.strip():
                        out.append(v)
    except Exception as e:  # noqa: BLE001
        log.debug("help known values: plugin config unreadable: %s", e)
    # … and, in either process, the plugins' own settings files.
    try:
        for f in (Path.home() / ".domovoi" / "plugins").glob("*.env"):
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                key, sep, value = line.partition("=")
                value = value.strip().strip("'\"")
                if sep and value and _SECRETISH.search(key.strip()):
                    out.append(value)
    except OSError:
        pass
    # Secrets handed to this process in its environment.
    for key, value in os.environ.items():
        if value and _SECRETISH.search(key) and len(value) >= 6:
            out.append(value)
    return out


def name_values(names: Iterable[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        n = " ".join(str(n or "").split())
        if not n:
            continue
        out.append(n)
        out += [p for p in n.replace("'s", " ").replace("’s", " ").split() if len(p) >= 3]
    return out


def sample_values(samples: Iterable[dict[str, Any]]) -> list[str]:
    """Network names, host names and gateways in satellite health samples
    (or the live snapshot's Wi-Fi rows)."""
    out: list[str] = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        wifi = s.get("wifi") if isinstance(s.get("wifi"), dict) else s
        for v in (wifi.get("ssid"), s.get("hostname"), s.get("gateway")):
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
    return out


async def _scalars(session: Any, sql: str) -> list[str]:
    try:
        return [str(v) for v in (await session.execute(text(sql))).scalars().all() if v]
    except Exception as e:  # noqa: BLE001 — a table a lane hasn't got: nothing from it
        log.debug("help known values: %s: %s", sql[:40], e)
        await session.rollback()
        return []


async def gather(session: Any, *, extra_samples: Iterable[dict[str, Any]] = ()) -> list[str]:
    """Every known value, from the database, the files and the settings."""
    from domovoi import admin_auth

    values: list[str] = []
    values += await _scalars(session, "SELECT token FROM household_device_tokens")
    values += await _scalars(session, "SELECT token_hash FROM household_device_tokens")
    values += await _scalars(session, "SELECT password_hash FROM admin_auth")
    values += await _scalars(session, "SELECT token_hash FROM admin_sessions")
    values += await _scalars(session, "SELECT token_hash FROM satellite_pairings")
    values += name_values(await _scalars(session, "SELECT name FROM people"))
    try:
        rows = (await session.execute(text(
            "SELECT DISTINCT ON (room_id) sample FROM satellite_health ORDER BY room_id, received_at DESC"
        ))).scalars().all()
    except Exception:  # noqa: BLE001
        await session.rollback()
        rows = []
    values += sample_values([r for r in rows if isinstance(r, dict)])
    values += sample_values(extra_samples)
    for read in (admin_auth.read_setup_code, admin_auth.read_device_token_file):
        try:
            v = read()
        except Exception:  # noqa: BLE001
            v = None
        if v:
            values.append(v)
    values += config_values()
    return values
