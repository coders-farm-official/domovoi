"""What each tier sees of the incidents, and the banner every page shows.

Three tiers, the dashboard's usual ones:

* **anyone** (no credential — a guest's browser, an unpaired tablet):
  :func:`public_summary` — only whether something needs an admin's
  attention and the banner's colour. Never a container, never a word of
  what failed.
* **household** (a paired device): :func:`incident_view` without details
  — what failed in plain words, when, whether it is acknowledged or
  cleared, and the banner. This is also what the ``containers.changed``
  realtime event carries (the state socket admits this tier and up).
* **admin**: the same plus what was expected and what was found (image
  references and IDs, settings, exit codes) and who acknowledged or
  cleared it, with their note.

The banner shows while any tamper incident is open (only clearing hides
it) or any failure is open and not acknowledged. Red for tamper and for a
required container's failure, amber for an optional one's.
"""

from __future__ import annotations

from typing import Any

from domovoi.container_guard.model import FAILURE, SEVERITY_ERROR, TAMPER
from domovoi.container_guard.store import iso

GUEST_TEXT = "Something needs an admin's attention."


def _visible(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The open incidents the banner shows."""
    return [
        r for r in rows
        if r.get("resolved_at") is None
        and (r["class"] == TAMPER or r.get("acknowledged_at") is None)
    ]


def banner(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``{show, tone, text, count, incident_ids, frozen}``. ``tone`` is
    ``tamper`` (red, risky operations paused), ``error`` (red) or ``warn``
    (amber); None when there is nothing to show."""
    open_rows = [r for r in rows if r.get("resolved_at") is None]
    frozen = any(r["class"] == TAMPER for r in open_rows)
    shown = _visible(open_rows)
    if not shown:
        return {"show": False, "tone": None, "text": "", "count": 0,
                "incident_ids": [], "frozen": frozen}
    tampers = [r for r in shown if r["class"] == TAMPER]
    if tampers:
        tone = "tamper"
    elif any(r.get("severity") == SEVERITY_ERROR for r in shown):
        tone = "error"
    else:
        tone = "warn"
    if len(shown) == 1:
        text = str(shown[0].get("summary") or "A Domovoi container needs attention.")
    else:
        text = f"{len(shown)} problems with Domovoi's containers need attention."
    if tampers:
        text += " Plugin installs, updates and room upgrades are paused until an admin reviews it."
    return {
        "show": True,
        "tone": tone,
        "text": text,
        "count": len(shown),
        "incident_ids": [int(r["id"]) for r in shown],
        "frozen": frozen,
    }


def public_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The open tier's whole answer."""
    b = banner(rows)
    tone = None
    if b["show"]:
        tone = "warn" if b["tone"] == "warn" else "err"
    return {"attention": bool(b["show"]), "tone": tone,
            "text": GUEST_TEXT if b["show"] else ""}


def incident_view(row: dict[str, Any], *, admin: bool) -> dict[str, Any]:
    out = {
        "id": int(row["id"]),
        "class": row["class"],
        "kind": row["kind"],
        "severity": row["severity"],
        "label": row["label"],
        "summary": row["summary"],
        "owner": row["owner"],
        "container": row.get("container"),
        "opened_at": iso(row.get("opened_at")),
        "last_seen_at": iso(row.get("last_seen_at")),
        "resolved_at": iso(row.get("resolved_at")),
        "resolution": row.get("resolution"),
        "acknowledged": row.get("acknowledged_at") is not None,
        "acknowledged_at": iso(row.get("acknowledged_at")),
        "cleared_at": iso(row.get("cleared_at")),
        "open": row.get("resolved_at") is None,
        "freezes": row["class"] == TAMPER and row.get("resolved_at") is None,
        "can_acknowledge": row["class"] == FAILURE and row.get("resolved_at") is None
        and row.get("acknowledged_at") is None,
        "can_clear": row["class"] == TAMPER and row.get("resolved_at") is None,
    }
    if admin:
        out.update(
            expected=row.get("expected") or {},
            found=row.get("found") or {},
            acknowledged_by=row.get("acknowledged_by"),
            cleared_by=row.get("cleared_by"),
            clear_note=row.get("clear_note"),
        )
    return out


def household_payload(rows: list[dict[str, Any]], *, admin: bool = False) -> dict[str, Any]:
    """The list read (and the realtime event): every open incident and the
    recently resolved ones, the banner, and whether the freeze is on."""
    b = banner(rows)
    return {
        "incidents": [incident_view(r, admin=admin) for r in rows],
        "banner": b,
        "frozen": b["frozen"],
        "attention": b["show"],
    }
