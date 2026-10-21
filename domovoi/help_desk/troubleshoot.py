"""Troubleshooting from Domovoi's own records: read-only summaries.

"Why is the kitchen offline?", "why did the update fail?": the honest
answer is in records the system already keeps, so help reads those rather
than guessing. :func:`findings` turns one :class:`State` (gathered by the
web layer, ``web/backend/api/help.py``) into :class:`Finding` lines:

* **Home's attention checks** (``web/static/home.jsx``,
  ``HomeAttentionRows``), the same rules in the same words: the database
  or the core not answering, speech recognition off or on its fallback,
  rooms offline or waiting, plugins that failed or are degraded, the last
  update failed or was rolled back, the internet question unanswered;
* **the rooms' health telemetry and self-checks** (V022): for a room that
  is offline, or online with a problem it reported, its last health
  sample (could it reach the server, the router; signal strength; heat;
  under-voltage; the mic privacy switch; a pending reboot) and its last
  outage report (why the process last ended, the self-check actions it
  took);
* **the update record** (``git_version.load_last_update``): the result,
  the failed step, the error line;
* **the container guard** (V024): the open incidents' plain summaries.

Read-only, and never raw: known fields are picked by name, every free-text
value goes through :func:`.redact.redact`, and gateway addresses, host
names, network names, boot ids, SHAs and tokens are never read at all.
What Home shows only to an admin (update details, approvals, the internet
question) is here only when the asker is one (``admin=True``); others get
one line saying an admin can see it.

**The Health checks seam.** The Settings regroup (wf/settings-regroup)
brings one Health page with checks of its own. Until it lands, Home's
rules above are the checks; a later check registers with
:func:`register_check` (a function from :class:`State` to findings) and
help includes it with no change here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from domovoi.help_desk.redact import redact

log = logging.getLogger(__name__)

#: A Pi this hot (°C) is worth saying (the firmware throttles near 80-85).
HOT_C = 80.0
#: A Wi-Fi signal this weak (dBm) is worth saying.
WEAK_SIGNAL_DBM = -75


@dataclass(frozen=True)
class Finding:
    key: str
    tone: str  # err | warn | info
    text: str
    link: str | None = None
    #: 'open' (anyone who can read help) or 'admin' (shown only to one).
    scope: str = "open"
    details: tuple[str, ...] = ()
    #: The room it is about, for "why is the kitchen…" questions.
    room: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "tone": self.tone, "text": self.text, "link": self.link,
                "scope": self.scope, "details": list(self.details), "room": self.room}


@dataclass
class State:
    health: dict[str, Any] = field(default_factory=dict)
    rooms: list[dict[str, Any]] = field(default_factory=list)
    #: room -> its newest health sample (``satellite_health.sample``).
    sat_health: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: room -> its newest outage report (``satellite_outages.report``) and when.
    outages: dict[str, dict[str, Any]] = field(default_factory=dict)
    plugins: list[dict[str, Any]] = field(default_factory=list)
    last_update: dict[str, Any] | None = None
    guard: dict[str, Any] | None = None
    internet: str | None = None
    now: datetime | None = None


Check = Callable[[State], list[Finding]]
_CHECKS: list[Check] = []


def register_check(check: Check) -> None:
    """Add a check (the Health page's, later). Idempotent per function."""
    if check not in _CHECKS:
        _CHECKS.append(check)


def unregister_check(check: Check) -> None:
    if check in _CHECKS:
        _CHECKS.remove(check)


def _ago(when: Any, now: datetime) -> str | None:
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(when, datetime):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    sec = max(0, int((now - when).total_seconds()))
    if sec < 90:
        return "just now"
    if sec < 3600:
        return f"{sec // 60} minutes ago"
    if sec < 172800:
        return f"{sec // 3600} hours ago"
    return f"{sec // 86400} days ago"


def _signal(sample: dict[str, Any]) -> int | None:
    wifi = sample.get("wifi")
    if isinstance(wifi, dict):
        for k in ("signal_dbm", "signal", "rssi"):
            v = wifi.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
    return None


def _sample_notes(sample: dict[str, Any], *, offline: bool) -> list[str]:
    """What a room's health sample says, in words; addresses never read."""
    notes: list[str] = []
    if sample.get("core_ok") is False or sample.get("core_host_ok") is False:
        notes.append("it couldn't reach the Domovoi server")
    if sample.get("gateway_ok") is False or sample.get("default_route") is False:
        notes.append("it couldn't reach your router (a Wi-Fi or network problem)")
    wifi = sample.get("wifi") if isinstance(sample.get("wifi"), dict) else {}
    if wifi.get("mode") == "ap":
        notes.append("it is showing its setup hotspot: it isn't on your Wi-Fi")
    sig = _signal(sample)
    if sig is not None and sig <= WEAK_SIGNAL_DBM:
        notes.append(f"its Wi-Fi signal was weak ({sig} dBm)")
    temp = sample.get("soc_temp_c")
    if isinstance(temp, (int, float)) and not isinstance(temp, bool) and temp >= HOT_C:
        notes.append(f"it was running hot ({round(temp)} °C)")
    flags = sample.get("throttled_flags")
    if isinstance(flags, list) and any("under" in str(f).lower() for f in flags):
        notes.append("its power supply was too weak (under-voltage)")
    if sample.get("mic_blocked") is True:
        notes.append("its microphone is switched off in the device's privacy settings")
    if isinstance(sample.get("reboot_needed"), str) and sample["reboot_needed"].strip():
        notes.append("it needs a restart: " + redact(sample["reboot_needed"], limit=160))
    if isinstance(sample.get("settings_ignored"), list):
        for n in sample["settings_ignored"][:2]:
            notes.append("a setting was ignored: " + redact(n, limit=120))
    return notes


def _outage_notes(report: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    reason = report.get("last_exit_reason")
    if isinstance(reason, str) and reason.strip():
        if reason.startswith("shutdown:"):
            notes.append("it was shut down or restarted on purpose last time")
        else:
            notes.append("last time it stopped because: " + redact(reason, limit=160))
    elif report:
        notes.append("it left no reason when it last stopped (a power cut, or a crash it never saw)")
    actions = report.get("actions")
    if isinstance(actions, list):
        for a in actions[-3:]:
            if isinstance(a, dict) and a.get("action"):
                notes.append(f"self-check: {redact(a.get('action'), limit=60)}"
                             + (f" ({redact(a.get('reason'), limit=100)})" if a.get("reason") else ""))
    diag = report.get("offline_diag")
    if isinstance(diag, list) and diag:
        notes.append(f"it tried to reconnect {len(diag)} times while it was offline")
    return notes


def _room_findings(st: State, now: datetime) -> list[Finding]:
    out: list[Finding] = []
    for r in st.rooms:
        room = str(r.get("room_id") or "")
        if not room:
            continue
        status = r.get("status")
        sample = (st.sat_health.get(room) or {}).get("sample") or {}
        outage = st.outages.get(room) or {}
        report = outage.get("report") or {}
        if status == "offline":
            seen = _ago(r.get("last_seen_at") or (st.sat_health.get(room) or {}).get("received_at"), now)
            details = [f"last heard from {seen}"] if seen else []
            details += _sample_notes(sample, offline=True)
            details += _outage_notes(report)
            out.append(Finding(f"offline:{room}", "warn", f"{room} is offline · it can't hear anyone right now",
                               "#settings/satellites", details=tuple(details), room=room))
        elif status == "waiting":
            out.append(Finding(f"waiting:{room}", "warn", f"{room} was set up but hasn't connected yet",
                               "#settings/satellites", details=(
                                   "it has never connected: check it is powered and on the same Wi-Fi",), room=room))
        elif status == "online":
            notes = _sample_notes(sample, offline=False)
            if notes:
                out.append(Finding(f"room:{room}", "warn", f"{room} is online but reported a problem",
                                   "#settings/satellites", details=tuple(notes), room=room))
            elif report and outage.get("reported_at") and _recent(outage.get("reported_at"), now):
                out.append(Finding(f"outage:{room}", "info", f"{room} was offline recently and came back",
                                   "#settings/satellites", details=tuple(_outage_notes(report)), room=room))
    return out


def _recent(when: Any, now: datetime, hours: int = 24) -> bool:
    ago = _ago(when, now)
    if ago is None:
        return False
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).total_seconds() <= hours * 3600


def _update_findings(st: State, *, admin: bool) -> list[Finding]:
    last = st.last_update or {}
    status = last.get("status")
    if status not in ("failed", "rollback_failed", "rolled_back"):
        return []
    if not admin:
        return [Finding("update", "warn",
                        "the last update didn't finish · an admin can read why in Settings → Configuration",
                        "#settings/configuration", scope="open")]
    failed_steps = [s.get("name") for s in last.get("steps") or []
                    if isinstance(s, dict) and s.get("status") in ("failed", "error")]
    details: list[str] = []
    if failed_steps:
        details.append("the step that failed: " + ", ".join(redact(s, limit=60) for s in failed_steps if s))
    if last.get("error"):
        details.append("the error: " + redact(last["error"], limit=240))
    if last.get("db_restored"):
        details.append("the database was put back as it was before the update")
    if last.get("finished_at"):
        details.append(f"finished at {redact(last['finished_at'], limit=40)}")
    text = {"failed": "the last update failed",
            "rollback_failed": "the last update failed, and so did putting the old version back",
            "rolled_back": "the last update was rolled back: the old version is running"}[status]
    return [Finding("update", "err" if status != "rolled_back" else "warn", text,
                    "#settings/configuration", scope="admin", details=tuple(details))]


def _plugin_findings(st: State) -> list[Finding]:
    out: list[Finding] = []
    for p in st.plugins:
        if p.get("status") == "uninstalled":
            continue
        name = str(p.get("name") or p.get("slug"))
        err = p.get("last_error")
        details = (("the error: " + redact(err, limit=200),) if err else ())
        if p.get("status") == "load_error":
            out.append(Finding(f"plugin:{p.get('slug')}", "err", f"the {name} plugin failed to load", "#plugins",
                               details=details))
        elif p.get("enabled") is not False and p.get("status") == "degraded":
            out.append(Finding(f"plugin:{p.get('slug')}", "warn", f"the {name} plugin is degraded", "#plugins",
                               details=details))
    return out


def _guard_findings(st: State) -> list[Finding]:
    g = st.guard or {}
    banner = g.get("banner") or {}
    if not banner.get("show"):
        return []
    details = tuple(redact(i.get("summary"), limit=200) for i in g.get("incidents") or []
                    if isinstance(i, dict) and i.get("open") and i.get("summary"))
    tone = "warn" if banner.get("tone") == "warn" else "err"
    return [Finding("containers", tone, redact(banner.get("text") or "a Domovoi container needs attention",
                                               limit=240), "#containers", details=details[:5])]


def findings(st: State, *, admin: bool = False) -> list[Finding]:
    """Every problem the records show right now, err first, in Home's order.

    The private values the State itself carries (the satellites' network and
    host names, this server's own names and its configured hosts) are
    scrubbed on top of whatever the caller set with ``known_values``."""
    from domovoi.help_desk.known import config_values, sample_values
    from domovoi.help_desk.redact import _KNOWN, known_values

    own = sample_values([(r or {}).get("sample") or {} for r in st.sat_health.values()])
    own += sample_values([r.get("wifi") for r in st.rooms if isinstance(r.get("wifi"), dict)])
    with known_values([*_KNOWN.get(), *own, *config_values()]):
        return _findings(st, admin=admin)


def _findings(st: State, *, admin: bool = False) -> list[Finding]:
    now = st.now or datetime.now(timezone.utc)
    out: list[Finding] = []
    out += _guard_findings(st)
    h = st.health or {}
    db_down = h.get("db_reachable") is False
    core_down = h.get("domovoi_reachable") is False
    if db_down:
        out.append(Finding("db", "err", "the database isn't answering · nothing new can be saved", "#settings"))
    if core_down:
        out.append(Finding("core", "err",
                           "the Domovoi server is running but can't answer · most likely its database"
                           if h.get("core_alive") is True else
                           "the Domovoi server isn't answering · rooms show the last known state",
                           "#settings/satellites"))
    if not db_down and not core_down:
        if h.get("stt") == "unavailable":
            out.append(Finding("stt", "err", "speech recognition is off · the rooms can't understand anyone",
                               "#settings/models"))
        elif h.get("stt") == "fallback":
            out.append(Finding("stt", "warn", "speech recognition is on its slower fallback model",
                               "#settings/models"))
        out += _room_findings(st, now)
        out += _plugin_findings(st)
        out += _update_findings(st, admin=admin)
        if admin and st.internet == "":
            out.append(Finding("internet", "warn", "tell Domovoi whether this box has internet",
                               "#settings/internet", scope="admin"))
    for check in list(_CHECKS):
        try:
            out += [f for f in check(st) if admin or f.scope != "admin"]
        except Exception as e:  # noqa: BLE001 — one check never costs the rest
            log.warning("help: a health check failed: %s", e)
    rank = {"err": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda f: rank.get(f.tone, 3))


def for_question(question: str, found: list[Finding]) -> list[Finding]:
    """The findings a question is about: those naming a room it names, else
    all of them (a "why isn't it working" question)."""
    q = (question or "").lower()
    named = [f for f in found if f.room and f.room.lower().replace("_", " ") in q.replace("_", " ")]
    return named or found


def render(found: list[Finding]) -> str:
    if not found:
        return "No problems are showing right now: every check Domovoi runs is fine."
    lines = []
    for f in found:
        lines.append(f"- {f.text}" + (f" (see {f.link})" if f.link else ""))
        for d in f.details:
            lines.append(f"  - {d}")
    return "\n".join(lines)
