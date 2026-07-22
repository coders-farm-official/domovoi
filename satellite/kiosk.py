"""Kiosk helpers for video satellites — display-page URL + browser service.

Deliberately sounddevice-free (importable on any dev host, like
``devices.py``): the kiosk systemd unit's launch script shells
``python -m satellite.kiosk --print-url`` so the URL derivation lives here —
unit-testable Python — instead of duplicated shell string-mangling.

The kiosk browser itself is a SEPARATE systemd unit
(``domovoi-kiosk.service``: cage + chromium in kiosk mode, see
VIDEO_SATELLITE.md) so satellite-client restarts never kill the browser.
This module only derives its URL, checks its liveness, and bounces it via
the sudoers-allowlisted restart (mirroring the client self-restart entry).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import quote, urlsplit

# Same location client.py uses; duplicated (not imported) so this module
# never drags in the sounddevice-heavy client.
CONFIG_DIR = Path("~/.domovoi").expanduser()
CONFIG_PATH = CONFIG_DIR / "config.toml"

WEB_DASHBOARD_PORT = 6369
KIOSK_UNIT = "domovoi-kiosk.service"


def build_kiosk_url(
    domovoi_url: str, room_id: str, override: str | None = None
) -> str:
    """Derive the fullscreen display-page URL from the core's WS URL: the
    web dashboard lives on the same host at :6369, and the page is
    ``display.html?room=<room_id>``. ws→http, wss→https. An explicit
    ``[display] kiosk_url`` override wins verbatim (point it anywhere)."""
    if override:
        return override
    parts = urlsplit(domovoi_url)
    scheme = "https" if parts.scheme == "wss" else "http"
    host = parts.hostname or "domovoi.local"
    return (
        f"{scheme}://{host}:{WEB_DASHBOARD_PORT}"
        f"/display.html?room={quote(room_id, safe='')}"
    )


def url_from_config(path: Path = CONFIG_PATH) -> str:
    """The kiosk URL for THIS satellite, read from its config.toml —
    what ``--print-url`` (and the launch script) uses."""
    with open(path, "rb") as f:
        d = tomllib.load(f)
    sat = d.get("satellite", {})
    display = d.get("display", {})
    return build_kiosk_url(
        str(sat.get("domovoi_url", "ws://domovoi.local:6370")),
        str(sat.get("room_id", "kitchen")),
        override=(str(display["kiosk_url"]) if display.get("kiosk_url") else None),
    )


def kiosk_alive(run=subprocess.run) -> bool:
    """Whether the kiosk browser service is currently active. ``run`` is
    injectable for tests."""
    try:
        r = run(
            ["systemctl", "is-active", "--quiet", KIOSK_UNIT],
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def restart_kiosk(run=subprocess.run) -> bool:
    """Bounce the kiosk browser service via the sudoers-allowlisted
    restart (VIDEO_SATELLITE.md documents the NOPASSWD line, mirroring the
    client's own self-restart entry). True when the command was accepted."""
    try:
        r = run(
            ["sudo", "-n", "systemctl", "--no-block", "restart", KIOSK_UNIT],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Domovoi kiosk helpers")
    p.add_argument(
        "--print-url",
        action="store_true",
        help="print this satellite's display-page URL (from config.toml)",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"config path (default {CONFIG_PATH})",
    )
    args = p.parse_args(argv)
    if args.print_url:
        print(url_from_config(args.config))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
