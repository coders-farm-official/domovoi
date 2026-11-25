"""Help questions about an admin's job, answered by who is asking (N1).

"How do I add a room?", "can you update yourself?", "can you install
plugins?", "can I change the admin password?", "can you delete this
song?" and, with nothing on this house that downloads, "can you download
videos?": each is something only an admin does. So the answer is fixed,
written here, and no model is asked:

* a household member (and every voice turn) hears who can do it and
  where: "An admin adds rooms, on the dashboard: Settings → Household →
  Rooms.";
* a PROVEN admin (``Context.proven_admin``, or in a dashboard chat the
  same server check on the request: a live admin sign-in, never the
  cookie alone and never "no admin set up yet") hears the steps.

Both are the manual's own words, which anyone can read (owner D1, part
a, 2026-11-25): a household-shared chat may hold them. What Home shows
ONLY to an admin (an update's failure details, the internet question,
approvals waiting) is a different thing and never reaches a chat, even an
admin's (D1, part b: ``help_turn_for`` keeps the household view).

WORDS ONLY. Which sentence is said never decides whether anything may be
done: every admin action keeps its own endpoint gate. No plugin or media
provider is ever named (owner, Q1 and N10).

Every place and button named here is the dashboard's own (Settings'
breadcrumbs from ``web/static/settings.jsx``, the Plugins page, Files);
a test checks them against those files.

Standard library only: both processes, and the voice handler, import it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

#: What a plugin that fetches media into the library registers
#: (``domovoi.capabilities.MEDIA_ACQUISITION_FULFILLER``). Named here, not
#: imported: this module stays stdlib-only.
DOWNLOADER_CAPABILITY = "media-acquisition-fulfiller"

_ASK = r"(?:how (?:do|can|would|should) (?:i|we)|how to|can (?:i|we)|could (?:i|we)|is it possible to)"
_YOU = r"(?:can|could|will|would|do) you"


@dataclass(frozen=True)
class AdminTask:
    key: str
    pattern: re.Pattern[str]
    #: What a member hears: who can do it, and where.
    member: str
    #: What a proven admin hears: the steps.
    admin: str
    #: Only while nothing on this house provides this capability (a
    #: feature a plugin would add): with one installed, the question is no
    #: admin's job and the ordinary help answers it.
    unless_provided: str | None = None


TASKS: tuple[AdminTask, ...] = (
    AdminTask(
        "add_room",
        re.compile(rf"\b(?:{_ASK}|{_YOU}) (?:add|set up|connect) (?:a |another |one more |a new |new )?"
                   r"(?:room|satellite)s?\b"),
        "An admin adds rooms, on the dashboard: Settings → Household → Rooms.",
        "In Settings → Household → Rooms. A Raspberry Pi: press prepare a room's SD card, write "
        "the card and start the Pi with it; join its Domovoi-Setup Wi-Fi from your phone, pick your "
        "home Wi-Fi and name the room; when the Pi says a six-digit code, type it under waiting for "
        "approval and press approve. A spare laptop, PC or old phone: press add a spare device, type "
        "the room's name, press make the code and follow the card. The manual's Rooms and devices "
        "pages have each one step by step.",
    ),
    AdminTask(
        "update",
        re.compile(rf"\b(?:{_YOU} (?:update|upgrade) (?:yourself|itself|domovoi)"
                   rf"|{_ASK} (?:update|upgrade) (?:domovoi|you|yourself|the server|this server))\b"),
        "Yes. An admin updates Domovoi on the dashboard, in Settings → System → About & updates.",
        "In Settings → System → About & updates: press Check for updates, then Update Domovoi. It "
        "pulls the update and restarts with it; on a server that can't restart itself, press Restart "
        "to apply changes next. A failed update rolls itself back. Reload any open dashboard once "
        "afterwards.",
    ),
    AdminTask(
        "plugins",
        re.compile(rf"\b(?:{_YOU}|{_ASK}) (?:install|add|get) (?:a |new |more |some )?plugins?\b"),
        "Yes, on the dashboard: an admin installs plugins from the Plugins page.",
        "On the dashboard's Plugins page: press Install from zip, or use install from GitHub and "
        "press fetch & preview. Read the trust screen before you go on: a plugin runs with full "
        "access to your server, so install only from publishers you trust. Plugins add nothing to "
        "the phone apps.",
    ),
    AdminTask(
        "admin_password",
        re.compile(r"\b(?:change|reset|set|choose|pick) (?:the |my |your |a new |our )?admin password\b"),
        "An admin can, on the server itself: the admin password is reset there, not in the app. "
        "Ask whoever set up Domovoi.",
        "The dashboard has no button for it: the admin password is reset on the server itself. On a "
        "Linux server or a by-hand install, run python -m domovoi.main --reset-admin there. That "
        "signs every admin out and writes a fresh setup code; then open the dashboard, enter the "
        "code and choose the new password. The troubleshooting guide's \"Admin locked out: on the "
        "server\" has the details.",
    ),
    AdminTask(
        "delete_files",
        # Delete, not "remove": taking a song off a queue or a playlist is
        # everyone's, and is no file deletion.
        re.compile(rf"\b(?:{_YOU}|{_ASK}) (?:delete|erase) (?:this|that|the|a|my|some) "
                   r"(?:song|track|file|video|photo|picture|document)s?\b"
                   r"(?! from (?:the |my |this |our )?(?:queue|playlist|list|up next))"),
        "Deleting needs the admin: an admin deletes files on the dashboard, in Files.",
        "On the dashboard, in Files: open the library it's in (Music for a song), tick it, press "
        "Delete and confirm. It's gone from the server, for everyone.",
    ),
    AdminTask(
        "video_downloads",
        re.compile(rf"\b{_YOU} (?:download|fetch|grab) (?:a |the |some |this |that |any )?videos?\b"),
        "I can't download videos here.",
        "Not on this house. An admin can add video downloads from the Plugins page.",
        unless_provided=DOWNLOADER_CAPABILITY,
    ),
)

TASK_BY_KEY: dict[str, AdminTask] = {t.key: t for t in TASKS}


def _norm(question: str) -> str:
    return " ".join((question or "").lower().replace("’", "'").split())


def match(question: str, *, provided: Iterable[str] = ()) -> AdminTask | None:
    """The admin task ``question`` asks about, or None. ``provided``: the
    capabilities this house's plugins provide (:func:`provided_by`), so a
    feature a plugin adds is no admin task once one is installed."""
    q = _norm(question)
    have = set(provided)
    for task in TASKS:
        if task.unless_provided and task.unless_provided in have:
            continue
        if task.pattern.search(q):
            return task
    return None


def answer(task: AdminTask, *, admin: bool) -> str:
    """The fixed answer: the steps for a proven admin, else who and where.
    ``admin`` must come from the server's own check for this turn
    (``Context.proven_admin`` / ``admin_auth.request_is_proven_admin``)."""
    return task.admin if admin else task.member


def provided_by(plugin_rows: Iterable[dict[str, Any]]) -> set[str]:
    """The capabilities installed, running plugins declare
    (``[capabilities] provides`` in the manifest the ``plugins`` row keeps)."""
    out: set[str] = set()
    for r in plugin_rows or ():
        if not r.get("enabled") or r.get("status") not in ("ok", "degraded"):
            continue
        manifest = r.get("manifest") or {}
        caps = manifest.get("capabilities") if isinstance(manifest, dict) else None
        provides = caps.get("provides") if isinstance(caps, dict) else None
        if isinstance(provides, list):
            out.update(str(p) for p in provides)
    return out


__all__ = ["AdminTask", "DOWNLOADER_CAPABILITY", "TASKS", "TASK_BY_KEY", "answer", "match", "provided_by"]
