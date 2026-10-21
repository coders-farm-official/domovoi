"""The Windows satellite Setup this server hands out, and its setup codes.

Spare-device satellites on Windows, the double-click way (2026-10-21).
A household makes a Windows laptop a room with ``DomovoiSatelliteSetup.exe``
(windows/installer/DomovoiSatelliteSetup.iss): a small wizard in front of
the same installer the dashboard's PowerShell command runs
(``satellite/scripts/install-windows.ps1``). It needs two things from the
household, both shown on the dashboard: this server's address and a short
**setup code**. A download from the dashboard's link carries both in its
file name, so usually nobody types anything.

This module is the core's half:

* **Setup codes** (:func:`make_code`, :func:`lookup`). An admin's "make the
  code" (Settings → Satellites → add a spare device) asks for one through
  ``POST /v1/admin/satellite-setup/codes``; it names that room's enrolment
  payload (``domovoi-room:1?core=…&fp=…&room=…``, satellite/enrolment.py)
  for :data:`CODE_TTL_SEC` (24 hours, like the server's own first-run setup
  code). ``GET /v1/satellite-setup/codes/{code}`` answers the Setup with
  the payload and the satellite bundle's digest — exactly what the
  PowerShell command quotes. **A code is not a credential**: the payload
  carries no secret (an address, a public fingerprint, a room name), the
  device still has to be approved with its own six-digit code, and the
  server still has to prove the fingerprint before anything is written. A
  code is only a short name for a payload, so it may sit in a file name
  and in a URL (which a log may keep): it expires, and it names nothing
  secret. Codes live in this process's memory only: a restart forgets
  them (the dashboard makes a new one), and at most :data:`MAX_CODES` are
  live, the oldest dropped first.
* **The Setup file** (:func:`fetch`, :func:`cached_file`, :func:`status`).
  ``DomovoiSatelliteSetup.exe`` is attached to Domovoi's GitHub releases
  by scripts/release/finish-release.ps1, beside ``DomovoiSetup.exe``, and
  listed in that release's ``SHA256SUMS``. Only when an admin presses the
  dashboard's button, this server downloads it from the release that
  ``android/release.json`` names (the release this checkout belongs to),
  else from the latest release, through the outbound gate
  (domovoi/net_safety.py), and keeps it only if its SHA-256 is the one that
  release's ``SHA256SUMS`` lists and it is a Windows program. It is not
  code-signed yet (owner decision 2026-10-21), so what vouches for it is
  GitHub's HTTPS and the release's own checksum list: said plainly in
  docs/SECURITY_PRIVACY.md. Refused under ``INTERNET_ACCESS=never``. The
  laptops then download it from this server, on the LAN.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SETUP_ASSET = "DomovoiSatelliteSetup.exe"
SUMS_ASSET = "SHA256SUMS"
#: The one place the Setup comes from: this repo's GitHub releases.
RELEASE_DOWNLOAD = "https://github.com/coders-farm-official/domovoi/releases/download/"
LATEST_DOWNLOAD = "https://github.com/coders-farm-official/domovoi/releases/latest/download/"
CACHE_BUCKET = "satellite_setup"
RECORD_NAME = "setup.json"
MAX_SETUP_BYTES = 64 * 1024 * 1024
MAX_SUMS_BYTES = 64 * 1024
#: The public routes (domovoi/main.py).
CODES_PATH = "/v1/satellite-setup/codes/"
DOWNLOAD_PATH = "/v1/satellite-setup/download/"
#: The short form a person types into the laptop's browser.
SHORT_PATH = "/win/"

#: How long a setup code names its room.
CODE_TTL_SEC = 24 * 3600
MAX_CODES = 64
#: Crockford's base 32: no I, L, O or U, so a code read aloud or typed from
#: a phone's screen has no look-alikes; 8 of them are 40 bits.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LEN = 8
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
#: A host the file name may carry: an IPv4 address or a DNS name (no IPv6,
#: whose colons a Windows file name can't hold).
_NAME_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


class SetupCodeError(ValueError):
    """A payload a setup code can't name (the message is safe to show)."""


# ─── setup codes ──────────────────────────────────────────────────────────

_codes: dict[str, dict[str, Any]] = {}
_codes_lock = threading.Lock()


def normalize_code(text: str) -> str | None:
    """The code as stored (8 characters of :data:`ALPHABET`), from what a
    person typed: case, dashes and spaces don't matter, and O, I and L are
    read as 0, 1 and 1. None when it can't be a code."""
    if not isinstance(text, str) or len(text) > 32:
        return None
    code = re.sub(r"[\s-]", "", text).upper().translate(_LOOKALIKES)
    if len(code) != CODE_LEN or any(c not in ALPHABET for c in code):
        return None
    return code


def display_code(code: str) -> str:
    """``K7M4QX2P`` as people read it: ``K7M4-QX2P``."""
    return f"{code[:4]}-{code[4:]}"


def _expire(now: float) -> None:
    for code in [c for c, t in _codes.items() if t["expires_at"] <= now]:
        del _codes[code]


def make_code(payload: str, *, now: float | None = None) -> dict[str, Any]:
    """A setup code for ``payload`` (refused unless satellite/enrolment.py
    parses it), live for :data:`CODE_TTL_SEC` from now. Asking again for
    the same payload while its code lives gives the same code, with the
    time renewed. ``{code, expires_at, payload, core, room, room_id}``."""
    from satellite import enrolment

    try:
        parsed = enrolment.parse(payload)
    except enrolment.EnrolmentError as e:
        raise SetupCodeError(f"not an enrolment payload: {e}") from None
    now = time.time() if now is None else now
    with _codes_lock:
        _expire(now)
        code = next((c for c, t in _codes.items() if t["payload"] == payload), None)
        if code is None:
            while len(_codes) >= MAX_CODES:
                del _codes[min(_codes, key=lambda c: _codes[c]["made_at"])]
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
            while code in _codes:
                code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
            _codes[code] = {"payload": payload, "made_at": now}
        _codes[code]["expires_at"] = now + CODE_TTL_SEC
        entry = dict(_codes[code])
    return {"code": code, "expires_at": entry["expires_at"], "payload": payload,
            "core": parsed.core, "room": parsed.room, "room_id": parsed.room_id}


def lookup(text: str, *, now: float | None = None) -> str | None:
    """The payload a live code names, or None (unknown, expired, or not a
    code at all)."""
    code = normalize_code(text)
    if code is None:
        return None
    now = time.time() if now is None else now
    with _codes_lock:
        _expire(now)
        entry = _codes.get(code)
        return entry["payload"] if entry else None


def expires_at(text: str) -> float | None:
    code = normalize_code(text)
    with _codes_lock:
        entry = _codes.get(code) if code else None
        return entry["expires_at"] if entry else None


def forget_codes() -> None:
    """Tests: drop every code."""
    with _codes_lock:
        _codes.clear()


def server_text(core: str) -> str:
    """The server as the Setup's "Domovoi server" box takes it, shortest
    first: ``192.168.1.20`` for ``http://192.168.1.20:6370`` (the Setup
    adds http:// and :6370), ``domovoi.local:6400`` for another port, the
    whole URL for https."""
    parts = urllib.parse.urlsplit(core)
    host = parts.hostname or ""
    shown = f"[{host}]" if ":" in host else host
    if parts.scheme != "http":
        return core
    return shown if (parts.port or 80) == 6370 else f"{shown}:{parts.port or 80}"


def download_name(code: str, core: str) -> str:
    """The file name a download from the dashboard's link gets, which the
    Setup reads its code and server back out of:
    ``DomovoiSatelliteSetup_K7M4QX2P_192.168.1.20_6370.exe``. An https
    server or an IPv6 address doesn't fit a file name: then it carries the
    code only (``DomovoiSatelliteSetup_K7M4QX2P.exe``) and the Setup asks
    for the server. A browser that adds " (1)" to a second download keeps
    it readable."""
    stem = f"DomovoiSatelliteSetup_{code}"
    parts = urllib.parse.urlsplit(core)
    host = (parts.hostname or "").lower()
    if parts.scheme == "http" and _NAME_HOST_RE.match(host) and ".." not in host:
        return f"{stem}_{host}_{parts.port or 80}.exe"
    return f"{stem}.exe"


# ─── the Setup file, from the GitHub release ──────────────────────────────

_job: dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "ok": None,
                        "message": None}
_job_lock = threading.Lock()


def _bucket() -> Path:
    from domovoi.satellite_media import cache

    return cache.bucket(CACHE_BUCKET)


def release_tag() -> str | None:
    """The release this checkout belongs to: the tag android/release.json
    names (the release scripts write it at the tag), or None before the
    first release."""
    from domovoi import android_app

    try:
        rel = android_app.load_manifest().get("release")
    except android_app.ManifestError:
        return None
    return rel.get("tag") if isinstance(rel, dict) else None


def sources(tag: str | None) -> list[tuple[str, str]]:
    """``(label, base URL)`` to try, in order: this checkout's release,
    then the latest one (an older release has no Setup)."""
    out = []
    if tag and re.fullmatch(r"v[0-9][0-9A-Za-z.-]{0,40}", tag):
        out.append((tag, f"{RELEASE_DOWNLOAD}{tag}/"))
    out.append(("latest", LATEST_DOWNLOAD))
    return out


def parse_sums(text: str, name: str = SETUP_ASSET) -> str | None:
    """The SHA-256 ``SHA256SUMS`` lists for ``name`` (``<hex>  <name>``,
    sha256sum's format, a ``*`` before the name allowed), or None."""
    for line in text.splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line.strip())
        if m and m.group(2) == name:
            return m.group(1).lower()
    return None


def _client():
    """The egress-hooked client (each request, redirects included, refused
    under ``never``). Tests replace it."""
    from domovoi.satellite_media import fetchers

    return fetchers._oww_http_client()


def _get(url: str, dest: Path | None, limit: int) -> bytes | str:
    """GET ``url`` through net_safety (every hop to a public address),
    reading at most ``limit`` bytes: into memory (``dest`` None) or into
    ``dest``, returning its SHA-256. Raises ValueError (``HTTP 404`` for a
    release without the file)."""
    from domovoi import net_safety

    h = hashlib.sha256()
    body = bytearray()
    written = 0
    with _client() as client:
        response = net_safety.open_stream_sync(client, url)
        try:
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            f = open(dest, "wb") if dest is not None else None
            try:
                for chunk in response.iter_bytes(1 << 20):
                    written += len(chunk)
                    if written > limit:
                        raise ValueError(f"larger than {limit} bytes")
                    h.update(chunk)
                    if f is None:
                        body.extend(chunk)
                    else:
                        f.write(chunk)
            finally:
                if f is not None:
                    f.close()
        finally:
            response.close()
    return bytes(body) if dest is None else h.hexdigest()


def read_record() -> dict[str, Any] | None:
    try:
        doc = json.loads((_bucket() / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not _SHA_RE.match(str(doc.get("sha256"))) or \
            not isinstance(doc.get("size"), int):
        return None
    return doc


def cached_file() -> Path | None:
    """The Setup this server holds, if its bytes are still the ones it
    checked when it downloaded them; else None."""
    from domovoi import satellite_windows

    rec = read_record()
    if rec is None:
        return None
    path = _bucket() / SETUP_ASSET
    return path if satellite_windows._matches(path, rec["sha256"], rec["size"]) else None


def status() -> dict[str, Any]:
    from domovoi import egress

    rec = read_record() if cached_file() is not None else None
    return {
        "ready": rec is not None,
        "file": SETUP_ASSET,
        "size": rec["size"] if rec else None,
        "sha256": rec["sha256"] if rec else None,
        "release": rec.get("release") if rec else None,
        "fetched_at": rec.get("fetched_at") if rec else None,
        "internet_off": egress.internet_turned_off(),
        "fetch": dict(_job),
    }


def fetch() -> tuple[bool, str]:
    """Download the Setup from the GitHub release (see the module notes),
    keep it only if ``SHA256SUMS`` there vouches for it. Blocking: run it
    off the event loop. ``(ok, message)``."""
    from domovoi.satellite_media import fetchers

    refused = fetchers._turned_off("the Windows satellite Setup")
    if refused:
        return refused
    dest = _bucket()
    problems: list[str] = []
    for label, base in sources(release_tag()):
        try:
            sums = _get(base + SUMS_ASSET, None, MAX_SUMS_BYTES)
            assert isinstance(sums, bytes)
            want = parse_sums(sums.decode("utf-8", "replace"))
            if want is None:
                problems.append(f"{label}: its {SUMS_ASSET} has no {SETUP_ASSET} (a release from before it)")
                continue
            part = dest / f".{SETUP_ASSET}.download"
            try:
                got = _get(base + SETUP_ASSET, part, MAX_SETUP_BYTES)
                if got != want:
                    raise ValueError(f"checksum mismatch: {SUMS_ASSET} says {want}, got {got}")
                with open(part, "rb") as f:
                    if f.read(2) != b"MZ":
                        raise ValueError("not a Windows program")
                size = part.stat().st_size
                os.replace(part, dest / SETUP_ASSET)
            finally:
                part.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001 — one source's failure, said and the next tried
            problems.append(f"{label}: {e}")
            continue
        record = {"sha256": want, "size": size, "release": label, "fetched_at": time.time()}
        (dest / RECORD_NAME).write_text(json.dumps(record, indent=2), encoding="utf-8")
        return True, f"{SETUP_ASSET} from the {label} release verified ({size} bytes)"
    return False, "; ".join(problems)[:900]


def start_fetch() -> bool:
    """Run :func:`fetch` on a thread unless one is running. True if started."""
    with _job_lock:
        if _job["running"]:
            return False
        _job.update(running=True, started_at=time.time(), finished_at=None, ok=None, message=None)

    def run() -> None:
        try:
            ok, message = fetch()
        except Exception as e:  # noqa: BLE001
            ok, message = False, f"{type(e).__name__}: {e}"
        with _job_lock:
            _job.update(running=False, finished_at=time.time(), ok=ok, message=message)
        log.info("Windows satellite Setup: %s", message)

    threading.Thread(target=run, name="satellite-setup-fetch", daemon=True).start()
    return True
