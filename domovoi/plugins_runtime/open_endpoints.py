"""Find a staged plugin's ``@open_endpoint`` and ``@device_endpoint``
routes for the install preview (design §4.11 / §5.1, PLG-1).

Both processes gate plugin mutations on the admin tier by default; a
route author moves a route off it with ``@device_endpoint`` (any paired
household device — the household token or an admin Bearer) or, rarely,
``@open_endpoint`` (no credential at all), both from ``domovoi.sdk`` /
``domovoi.webkit``. The trust screen promises to list every such route,
each tier under its own heading, so the admin sees who can call the
plugin BEFORE confirming. That list has to come from the staged source
without importing it — importing runs plugin code, and nothing runs
pre-confirm — so the package's Python files are walked with :mod:`ast`
(:mod:`domovoi.route_markers`, the one walk the load-time check uses
too) and every function that carries a marker is reported together with
the router decorator on the same ``def``. A function carrying BOTH
markers is reported as a conflict, which the installer refuses (the
runtime refuses to load it too).

The walk runs in a **throwaway subprocess** (``python -I route_markers.py
<package dir>`` — that file imports only the stdlib): ``ast.parse`` never
executes code, but a crafted source can still exhaust the parser
(recursion depth, memory), and that must not take the core down. The
subprocess is time-boxed; a failure refuses the install (fail closed —
a package the parser cannot read is one the trust screen cannot
describe).

The walk is static: it knows the documented shape (``@router.post("/x")``
over the marker over the ``def``, the marker imported under its own name
or a local alias). A marker applied any other way — ``setattr`` of the
marker attribute, a call instead of a decorator — is invisible HERE, and
that is why the load holds every route that is off the admin tier at
runtime against this same walk and refuses the ones it does not list
(``domovoi.webkit.unlisted_tier_routes``): what the trust screen did not
show never serves.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from domovoi import route_markers
from domovoi.route_markers import MARKERS, scan_marked_endpoints  # noqa: F401

__all__ = [
    "scan_marked_endpoints",
    "scan_open_endpoints",
    "scan_device_endpoints",
    "collect_marked_endpoints",
    "collect_open_endpoints",
    "OpenEndpointScanError",
]

SCAN_TIMEOUT_SEC = 60


class OpenEndpointScanError(RuntimeError):
    """The subprocess scan failed or timed out."""


def scan_open_endpoints(package_dir: Path) -> list[dict[str, Any]]:
    """The ``@open_endpoint`` routes of :func:`scan_marked_endpoints`."""
    return scan_marked_endpoints(package_dir)["open"]


def scan_device_endpoints(package_dir: Path) -> list[dict[str, Any]]:
    """The ``@device_endpoint`` routes of :func:`scan_marked_endpoints`."""
    return scan_marked_endpoints(package_dir)["device"]


def collect_open_endpoints(
    package_dir: Path, *, timeout: float = SCAN_TIMEOUT_SEC
) -> list[dict[str, Any]]:
    """The ``open`` half of :func:`collect_marked_endpoints`."""
    return collect_marked_endpoints(package_dir, timeout=timeout)["open"]


def collect_marked_endpoints(
    package_dir: Path, *, timeout: float = SCAN_TIMEOUT_SEC
) -> dict[str, list[dict[str, Any]]]:
    """Run :func:`scan_marked_endpoints` in a throwaway subprocess and
    return its ``{"open", "device", "conflicts"}`` records. Raises
    :class:`OpenEndpointScanError` when the scan fails, times out, or
    reports a file it could not parse."""
    # Run the walk's own file as a plain script: it imports only the
    # stdlib, so the child needs no domovoi on its path, and -I (isolated:
    # no env vars, no site customisation, no script dir or cwd on
    # sys.path) keeps the walk inert.
    proc_args = [
        sys.executable, "-I", "-X", "utf8",
        str(Path(route_markers.__file__).resolve()), str(package_dir),
    ]
    try:
        proc = subprocess.run(
            proc_args, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise OpenEndpointScanError(
            f"open-endpoint scan of {package_dir.name} exceeded {timeout:.0f}s"
        )
    except OSError as e:  # pragma: no cover — interpreter missing
        raise OpenEndpointScanError(f"open-endpoint scan could not start: {e}")
    try:
        payload = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("error"):
        raise OpenEndpointScanError(f"open-endpoint scan: {payload['error']}")
    if proc.returncode != 0 or not isinstance(payload, dict):
        raise OpenEndpointScanError(
            "open-endpoint scan failed: "
            + (proc.stderr or proc.stdout or "").strip()[-1500:]
        )
    return {
        "open": list(payload.get("endpoints") or []),
        "device": list(payload.get("device_endpoints") or []),
        "conflicts": list(payload.get("conflicts") or []),
    }
