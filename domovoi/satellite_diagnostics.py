"""Read-only satellite diagnostics: the probe allowlist, the per-satellite
rate limit and the cleaning every answer gets on the core.

Owner decision 2026-11-22: the safe alternative to a remote terminal. An
admin sees from the dashboard what they would otherwise SSH in for, from
a FIXED set of named probes, with no command input of any kind. The core
sends ``diag_request {probe}`` carrying a name from :data:`PROBES` and
nothing else; the satellite (satellite/diagnostics.py) refuses a name it
doesn't know and answers ``diag_result {probe, ok, text, at}``.

Only a device whose hello declared ``diagnostics`` is ever sent one
(domovoi/satellite_abilities.py). The satellite caps and scrubs its own
answer; :func:`clean_result` does it again here, because the core doesn't
take a device's word for what is safe to put on a page: the text is cut
to :data:`MAX_TEXT_CHARS`, control characters go, and anything
secret-looking is replaced.
"""

from __future__ import annotations

import re
import time
from collections import deque
from typing import Any

#: The probes, in the order the dashboard lists them, with their labels.
#: The satellite's list is the same (satellite/diagnostics.py PROBES).
PROBES: dict[str, str] = {
    "recent_logs": "Recent log lines",
    "network": "Network",
    "system": "Disk, memory and load",
    "audio": "Audio devices",
    "versions": "Versions",
    "service": "Restarts and last exit",
    "mic_dsp": "Mic DSP (XVF3800)",
}

#: Probes only a device that declared an ability gets (run-all leaves them
#: out for any other; one asked for by name is refused with 409).
PROBE_ABILITY: dict[str, str] = {"mic_dsp": "mic_dsp"}


def probes_for(abilities) -> dict[str, str]:
    """The probes a device with ``abilities`` (satellite_abilities.Abilities)
    is asked for, in order."""
    return {name: label for name, label in PROBES.items()
            if name not in PROBE_ABILITY or abilities.allows(PROBE_ABILITY[name])}

#: Most characters of one probe's text the core keeps (the satellite cuts
#: at the same size; a little slack for its "[cut short]" marker).
MAX_TEXT_CHARS = 4096
#: How long the core waits for one answer.
PROBE_TIMEOUT_S = 15.0
#: Per satellite: at most this many probe runs in any window (a run-all
#: counts one per probe): three run-alls a minute, or single re-runs.
RATE_WINDOW_S = 60.0
RATE_MAX_RUNS = 18

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")
_SECRET_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [redacted]"),
    (re.compile(
        r"(?i)([\w-]*(?:token|password|passwd|passphrase|secret|api[_-]?key|apikey|psk|cookie|"
        r"auth|credential|private[_-]?key)[\w-]*[\"']?)(\s*[=:]\s*)(?!\[redacted\])"
        r"(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;}]+)"
    ), r"\1\2[redacted]"),
    (re.compile(r"(?i)\b(password|passwd|passphrase|secret|psk)(\s+)(?!\[redacted\])([^\s,;]+)"),
     r"\1\2[redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
     "[redacted private key]"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[redacted]"),
    (re.compile(r"(?<![\w/.-])[A-Za-z0-9+/_-]{40,}={0,2}(?![\w/])"), "[redacted]"),
]


def clean_text(raw: Any, limit: int = MAX_TEXT_CHARS) -> str:
    """A probe's text as the core keeps and serves it."""
    out = _CONTROL.sub("", str(raw or "")).replace("\r", "")
    if len(out) > limit * 2:
        out = out[: limit * 2]  # bound the regex work before the rules run
    for pattern, repl in _SECRET_RULES:
        out = pattern.sub(repl, out)
    if len(out) > limit:
        out = out[: limit - 12].rstrip() + "\n[cut short]"
    return out


def clean_result(probe: str, ctrl: dict[str, Any]) -> dict[str, Any]:
    """A ``diag_result`` frame as the API returns it. ``probe`` is the
    name the core ASKED for (never the device's echo), ``at`` a number."""
    at = ctrl.get("at")
    try:
        at_f = float(at)
    except (TypeError, ValueError):
        at_f = time.time()
    return {
        "probe": probe,
        "label": PROBES.get(probe, probe),
        "ok": ctrl.get("ok") is True,
        "text": clean_text(ctrl.get("text")),
        "at": round(at_f, 3),
    }


def failed_result(probe: str, text: str) -> dict[str, Any]:
    """The entry for a probe that got no answer (timeout, disconnect)."""
    return {"probe": probe, "label": PROBES.get(probe, probe), "ok": False,
            "text": text, "at": round(time.time(), 3)}


class RateLimiter:
    """At most ``max_runs`` probe runs per satellite in any ``window``
    seconds. :meth:`take` books ``n`` runs or none."""

    def __init__(self, max_runs: int = RATE_MAX_RUNS, window: float = RATE_WINDOW_S,
                 clock: Any = time.monotonic) -> None:
        self.max_runs = max_runs
        self.window = window
        self._clock = clock
        self._runs: dict[str, deque[float]] = {}

    def take(self, key: str, n: int = 1) -> float:
        """0.0 when booked, else the seconds until ``n`` runs fit."""
        now = self._clock()
        runs = self._runs.setdefault(key, deque())
        while runs and now - runs[0] >= self.window:
            runs.popleft()
        if len(runs) + n > self.max_runs:
            if n > self.max_runs or not runs:
                return self.window
            idx = len(runs) + n - self.max_runs - 1
            return max(0.1, self.window - (now - runs[idx]))
        runs.extend([now] * n)
        return 0.0
