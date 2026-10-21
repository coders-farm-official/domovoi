"""The spare computer's tray: the satellite's state where a person can see it.

Spare-device satellites (S1, 2026-10-13). A laptop has no LED ring, so the
tray is how its owner sees that the microphone is live, mutes it, finds
the approval code, and answers the "ask me" questions. It is a separate,
optional process: the engine runs headless and the tray only talks to it
over the local control channel (:mod:`satellite.control`), so quitting the
tray never stops the satellite.

* **Tray icon:** a StatusNotifierItem over D-Bus (:mod:`satellite.tray_dbus`,
  ``dbus-fast``, MIT) with its menu over ``com.canonical.dbusmenu``; shown by
  KDE, Xfce, Cinnamon, MATE and Ubuntu's GNOME (its AppIndicator extension
  is on by default). No pystray, PyGObject, AppIndicator or Qt.
* **Pop-ups:** ``org.freedesktop.Notifications`` on the same connection: the
  approval code (persistent, replaced by "Approved" once it is), an update
  or restart waiting for the person's OK, "About".
* **No tray at all** (vanilla GNOME, Debian's default): the pop-ups still
  come, and ``python -m satellite.tray --window`` (the app-menu launcher
  "Domovoi Satellite") opens a small Tkinter status window with the same
  controls. Tkinter is the standard library's (Tcl/Tk, BSD-style).

Icon states (the mic-live indicator first): outline mic while listening
for the wake word; **filled red mic while the microphone is live** (a
command, a call, a chat, a wake-word recording); struck-through when
muted; a speaker badge while it speaks or plays music; amber while it
waits for approval; grey, with the reason in the tooltip, when it is
offline, its server failed the pinned identity check, or the engine is not
running.

This module is the part that needs no D-Bus: the model (status → icon,
tooltip, menu, pop-ups), the icon pixels, the actions, the Tk window and
``main``. Everything here is testable on any OS.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from satellite import control

log = logging.getLogger("satellite.tray")

APP_ID = "domovoi-satellite"
APP_NAME = "Domovoi Satellite"
SERVICE_UNIT = "domovoi-satellite.service"
DEFAULT_SOCKET = Path.home() / ".domovoi" / control.SOCKET_NAME

ICON_STATES = ("idle", "capturing", "muted", "speaking", "awaiting", "offline")
NEWLINE = chr(10)

# ─── the model: what a status looks like ─────────────────────────────────


def icon_state(st: dict[str, Any] | None) -> str:
    """Which icon a status shows. The live microphone wins over anything
    the speaker is doing; waiting for approval and being offline win over
    both (the microphone is closed then anyway)."""
    if not st:
        return "offline"
    conn = st.get("connection")
    if conn == "awaiting_approval":
        return "awaiting"
    if conn in ("offline", "unpinned", "refused", "connecting"):
        return "offline"
    mic = st.get("mic")
    if mic == "muted":
        return "muted"
    if mic == "capturing":
        return "capturing"
    if st.get("output") in ("speaking", "music"):
        return "speaking"
    return "idle"


def room_name(st: dict[str, Any] | None) -> str:
    room = (st or {}).get("room") or {}
    return room.get("name") or room.get("id") or "this room"


def server_host(st: dict[str, Any] | None) -> str:
    server = (st or {}).get("server") or {}
    return server.get("host") or server.get("url") or "the Domovoi server"


def connection_text(st: dict[str, Any] | None) -> str:
    if not st:
        from satellite import not_started

        why = not_started.read()
        return "the satellite isn't running" + (f": {why}" if why else "")
    conn = st.get("connection")
    reason = st.get("reason")
    if conn == "connected":
        return f"connected to {server_host(st)}"
    if conn == "awaiting_approval":
        return "waiting for approval"
    if conn == "connecting":
        return f"connecting to {server_host(st)}"
    if conn == "unpinned":
        return f"{server_host(st)} failed the identity check" + (f": {reason}" if reason else "")
    if conn == "refused":
        return "the Domovoi server refused this device" + (f": {reason}" if reason else "")
    return f"offline" + (f": {reason}" if reason else "")


def tooltip(st: dict[str, Any] | None, code: str | None = None) -> tuple[str, str]:
    """``(title, text)`` for the icon's tooltip."""
    title = f"Domovoi · {room_name(st)}"
    state = icon_state(st)
    if state == "awaiting":
        text = (f"Waiting for approval, code {code}. Approve it in Settings → Rooms."
                if code else "Waiting for approval in Settings → Rooms.")
    elif state == "offline":
        text = connection_text(st)
    elif state == "muted":
        text = "Muted: the microphone is closed."
    elif state == "capturing":
        text = "Microphone live: sending what it hears to the Domovoi server."
    elif state == "speaking":
        text = "Playing music." if (st or {}).get("output") == "music" else "Speaking."
    else:
        text = "Listening for the wake word (nothing leaves this computer until it hears it)."
    pending = (st or {}).get("pending") or []
    if pending:
        text += f" {len(pending)} waiting for your OK."
    return title, text


@dataclass(frozen=True)
class MenuItem:
    id: int
    label: str
    action: str = ""
    enabled: bool = True
    visible: bool = True
    separator: bool = False


# Stable ids, so a click on an item still means the same action after the
# menu was rebuilt for a new status.
_IDS = {
    "header": 1, "sep1": 2, "mute": 3, "volume_up": 4, "volume_down": 5, "identify": 6,
    "sep2": 7, "dashboard": 8, "logs": 9, "about": 10, "sep3": 11, "quit": 12, "stop": 13,
    "sep4": 14, "start": 15,
}
_PENDING_BASE = 100
MAX_PENDING_ITEMS = 4

PENDING_LABELS = {
    "update": "the satellite update",
    "restart": "the restart",
    "config": "the new settings",
    "wake_word": "the new wake word",
}


def menu(st: dict[str, Any] | None, *, can_start: bool = False) -> list[MenuItem]:
    """The tray menu for a status (assessment §4.1): who and where, mute,
    volume, which room, the waiting actions, the dashboard, the logs,
    About, Quit tray and the separate, explicit Stop satellite.
    ``can_start`` (Windows): while the engine isn't running, Stop
    satellite becomes Start satellite."""
    up = st is not None
    muted = (st or {}).get("mic") == "muted"
    items = [
        MenuItem(_IDS["header"], f"{room_name(st)} · {connection_text(st)}", enabled=False),
        MenuItem(_IDS["sep1"], "", separator=True),
        MenuItem(_IDS["mute"], "Unmute listening" if muted else "Mute listening",
                 action="unmute" if muted else "mute", enabled=up),
        MenuItem(_IDS["volume_up"], "Volume up", action="volume_up",
                 enabled=up and (st or {}).get("volume") is not None),
        MenuItem(_IDS["volume_down"], "Volume down", action="volume_down",
                 enabled=up and (st or {}).get("volume") is not None),
        MenuItem(_IDS["identify"], "Which room is this? (plays a chime)", action="identify", enabled=up),
    ]
    pending = ((st or {}).get("pending") or [])[:MAX_PENDING_ITEMS]
    if pending:
        items.append(MenuItem(_IDS["sep2"], "", separator=True))
        for i, p in enumerate(pending):
            what = PENDING_LABELS.get(p.get("kind"), p.get("kind", "it"))
            items.append(MenuItem(_PENDING_BASE + 2 * i, f"Allow {what}: {p.get('summary', '')}"[:120],
                                  action=f"approve:{p.get('id')}"))
            items.append(MenuItem(_PENDING_BASE + 2 * i + 1, f"Not now ({what})",
                                  action=f"decline:{p.get('id')}"))
    items += [
        MenuItem(_IDS["sep3"], "", separator=True),
        MenuItem(_IDS["dashboard"], "Open dashboard", action="dashboard",
                 enabled=bool((st or {}).get("dashboard"))),
        MenuItem(_IDS["logs"], "Show logs folder", action="logs", enabled=up),
        MenuItem(_IDS["about"], "About", action="about"),
        MenuItem(_IDS["sep4"], "", separator=True),
        MenuItem(_IDS["quit"], "Quit tray (the satellite keeps running)", action="quit"),
        (MenuItem(_IDS["start"], "Start satellite", action="start")
         if can_start and not up else
         MenuItem(_IDS["stop"], "Stop satellite…", action="stop_ask", enabled=up)),
    ]
    return items


@dataclass
class Note:
    """A pop-up to show. ``key`` names its slot: a later note with the same
    key replaces it on screen (the approval code becomes "Approved")."""

    key: str
    summary: str
    body: str
    actions: list[tuple[str, str]] = field(default_factory=list)   # (action, label)
    persistent: bool = False


class NotePlanner:
    """Which pop-ups a new status calls for: the approval code (once per
    code, persistent), "Approved" when that wait ends, each new waiting
    action once."""

    def __init__(self) -> None:
        self.code_shown: str | None = None
        self.waiting = False
        self.pending_seen: set[str] = set()

    def plan(self, st: dict[str, Any] | None, code: str | None) -> list[Note]:
        notes: list[Note] = []
        conn = (st or {}).get("connection")
        if conn == "awaiting_approval":
            self.waiting = True
            if code and code != self.code_shown:
                self.code_shown = code
                notes.append(Note(
                    "approval",
                    f"{room_name(st)}: waiting for approval, code {code}",
                    f"Approve it in Settings → Rooms on the Domovoi dashboard, "
                    f"with the code {code}.",
                    actions=[("dashboard", "Open dashboard")],
                    persistent=True,
                ))
        elif conn == "connected" and self.waiting:
            self.waiting = False
            self.code_shown = None
            notes.append(Note("approval", f"Approved: this device is now the {room_name(st)}",
                              "It answers to the wake word from now on.",
                              actions=[("dashboard", "Open dashboard")]))
        for p in (st or {}).get("pending") or []:
            pid = str(p.get("id"))
            if pid in self.pending_seen:
                continue
            self.pending_seen.add(pid)
            what = PENDING_LABELS.get(p.get("kind"), "a change")
            notes.append(Note(
                f"pending:{pid}", f"{room_name(st)}: the Domovoi server asks to apply {what}",
                NEWLINE.join([f"{p.get('summary', '')}. It waits for your OK (your choice: \"ask me\")."]
                             + [f"  {line}" for line in p.get("details") or []]),
                actions=[(f"approve:{pid}", "Allow"), (f"decline:{pid}", "Not now")],
                persistent=True,
            ))
        return notes


def about_note(st: dict[str, Any] | None) -> Note:
    server = (st or {}).get("server") or {}
    lines = [
        f"Room: {room_name(st)} ({((st or {}).get('room') or {}).get('id', '?')})",
        f"Server: {server.get('url') or '?'}",
        f"Pinned identity: {server.get('fingerprint') or 'none'}",
        f"Satellite code: {(st or {}).get('version') or 'as installed'}",
        f"Updates: {(st or {}).get('permissions') or '?'}",
        f"Python {sys.version.split()[0]}",
    ]
    return Note("about", f"{APP_NAME} · {room_name(st)}", "\n".join(lines))


STOP_NOTE = Note(
    "stop", "Stop the satellite?",
    "It stops listening and leaves the room until you start it again "
    "(systemctl --user start domovoi-satellite) or restart the computer.",
    actions=[("stop", "Stop it"), ("cancel", "Keep it running")],
    persistent=True,
)


# ─── icons, drawn here (no image files, no image library) ────────────────

_COLOURS = {
    "idle": (0xFF, 0x4C, 0xAF, 0x50),       # green outline: listening for the wake word
    "capturing": (0xFF, 0xE5, 0x39, 0x35),  # filled red: the microphone is live
    "muted": (0xFF, 0x9E, 0x9E, 0x9E),      # grey mic, red strike
    "speaking": (0xFF, 0x4C, 0xAF, 0x50),
    "awaiting": (0xFF, 0xFF, 0xB3, 0x00),   # amber
    "offline": (0xFF, 0x80, 0x80, 0x80),    # grey
}
_STRIKE = (0xFF, 0xE5, 0x39, 0x35)
_BADGE = (0xFF, 0x42, 0xA5, 0xF5)
ICON_SIZES = (22, 32, 48)


def _shapes(state: str):
    """Coverage functions in unit coordinates (0..1, y down) with colours."""
    def capsule(x, y, fill: bool):
        cx, top, bottom, r = 0.5, 0.12, 0.58, 0.17
        if top + r <= y <= bottom - r:
            d = abs(x - cx) - r
        else:
            cy = top + r if y < top + r else bottom - r
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 - r
        return d <= 0 if fill else -0.075 <= d <= 0

    def stand(x, y):
        # The U under the capsule, the stem and the base.
        d = ((x - 0.5) ** 2 + (y - 0.45) ** 2) ** 0.5
        u = 0.26 <= d <= 0.33 and y >= 0.45
        stem = abs(x - 0.5) <= 0.035 and 0.78 <= y <= 0.9
        base = 0.32 <= x <= 0.68 and 0.86 <= y <= 0.92
        return u or stem or base

    colour = _COLOURS[state]
    fill = state in ("capturing", "awaiting")
    layers = [(lambda x, y: capsule(x, y, fill) or stand(x, y), colour)]
    if state == "muted":
        layers.append((lambda x, y: abs((y - 0.1) - (x - 0.1)) <= 0.06 and 0.1 <= x <= 0.9, _STRIKE))
    if state == "speaking":
        def badge(x, y):
            body = 0.66 <= x <= 0.76 and 0.68 <= y <= 0.86
            cone = 0.76 <= x <= 0.92 and abs(y - 0.77) <= (x - 0.76) * 0.9 + 0.09
            return body or cone
        layers.append((badge, _BADGE))
    return layers


def render_icon(state: str, size: int, *, samples: int = 3) -> bytes:
    """ARGB32, network byte order, ``size``×``size``: what an SNI
    ``IconPixmap`` carries. Antialiased by supersampling."""
    layers = _shapes(state)
    out = bytearray(size * size * 4)
    n = samples * samples
    for py in range(size):
        for px in range(size):
            a = r = g = b = 0.0
            for colour_fn, colour in layers:
                hits = 0
                for sy in range(samples):
                    for sx in range(samples):
                        x = (px + (sx + 0.5) / samples) / size
                        y = (py + (sy + 0.5) / samples) / size
                        if colour_fn(x, y):
                            hits += 1
                if hits:
                    cov = hits / n
                    _, cr, cg, cb = colour
                    # "over" compositing, straight alpha.
                    a_new = cov + a * (1 - cov)
                    if a_new > 0:
                        r = (cr * cov + r * a * (1 - cov)) / a_new
                        g = (cg * cov + g * a * (1 - cov)) / a_new
                        b = (cb * cov + b * a * (1 - cov)) / a_new
                    a = a_new
            i = (py * size + px) * 4
            out[i:i + 4] = bytes((int(round(a * 255)), int(round(r)), int(round(g)), int(round(b))))
    return bytes(out)


_ICON_CACHE: dict[tuple[str, int], bytes] = {}


def icon_pixmaps(state: str) -> list[tuple[int, int, bytes]]:
    """Every size of one state's icon, as ``a(iiay)``."""
    out = []
    for size in ICON_SIZES:
        key = (state, size)
        if key not in _ICON_CACHE:
            _ICON_CACHE[key] = render_icon(state, size)
        out.append((size, size, _ICON_CACHE[key]))
    return out


# ─── actions (shared by the D-Bus tray and the Tk window) ─────────────────

def _open(target: str) -> bool:
    opener = shutil.which("xdg-open")
    if not opener or not target:
        return False
    try:
        subprocess.Popen([opener, target], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


class TrayActions:
    """What a menu item, a button or a pop-up action does. Blocking (one
    small request to the engine each); the D-Bus side runs them on a
    worker thread."""

    def __init__(self, client: Any, *, opener: Callable[[str], bool] = _open,
                 run: Callable[..., Any] = subprocess.run,
                 stopper: Callable[[], Note] | None = None,
                 starter: Callable[[], Note] | None = None) -> None:
        self.client = client
        self.opener = opener
        self.run = run
        # How "stop" and "start" are done where systemctl isn't the answer
        # (Windows: satellite/win_tray.py).
        self.stopper = stopper
        self.starter = starter
        self.last_status: dict[str, Any] | None = None

    def status(self) -> dict[str, Any] | None:
        try:
            st = self.client.call("status")
        except ConnectionError:
            st = None
        self.last_status = st if st and st.get("ok", True) else None
        return self.last_status

    def code(self) -> str | None:
        try:
            got = self.client.call("code")
        except ConnectionError:
            return None
        return got.get("code") if got.get("waiting") else None

    def do(self, action: str) -> Note | None:
        """Run ``action``; a pop-up to show in answer, or None."""
        st = self.last_status
        try:
            if action in ("mute", "unmute", "identify"):
                self.client.call(action)
            elif action in ("volume_up", "volume_down"):
                self.client.call("volume", delta=10 if action == "volume_up" else -10)
            elif action.startswith("approve:") or action.startswith("decline:"):
                verb, _, pid = action.partition(":")
                self.client.call(verb, action=pid)
            elif action == "dashboard":
                self.opener((st or {}).get("dashboard") or "")
            elif action == "logs":
                got = self.client.call("logs-path")
                if not self.opener(got.get("path") or ""):
                    return Note("logs", "Satellite logs", got.get("hint") or "")
                return Note("logs", "Satellite logs", f"Recent lines: {got.get('hint')}")
            elif action == "about":
                return about_note(st)
            elif action == "stop_ask":
                return STOP_NOTE
            elif action == "stop":
                if self.stopper is not None:
                    return self.stopper()
                self.run(["systemctl", "--user", "stop", SERVICE_UNIT],
                         capture_output=True, text=True, timeout=30)
                return Note("stop", "Satellite stopped",
                            "Start it again with: systemctl --user start domovoi-satellite")
            elif action == "start" and self.starter is not None:
                return self.starter()
        except ConnectionError as e:
            return Note("error", APP_NAME, f"The satellite isn't answering ({e}).")
        return None


# ─── the window (vanilla GNOME, or anyone who opens it) ──────────────────

_WINDOW_MIC_TEXT = {
    "idle": ("Listening for the wake word", "#2e7d32"),
    "capturing": ("● MICROPHONE LIVE", "#c62828"),
    "muted": ("Muted: the microphone is closed", "#616161"),
    "speaking": ("Speaking / playing", "#1565c0"),
    "awaiting": ("Waiting for approval", "#b26a00"),
    "offline": ("Offline", "#616161"),
}


class StatusWindow:
    """A small Tk window with the tray's controls, for a desktop with no
    tray. Polls the engine twice a second (a local socket, cheap)."""

    POLL_MS = 500

    def __init__(self, root: Any, actions: TrayActions) -> None:
        import tkinter as tk

        self.tk = tk
        self.root = root
        self.actions = actions
        root.title(APP_NAME)
        root.minsize(340, 0)
        self.title = tk.Label(root, font=("TkDefaultFont", 13, "bold"), anchor="w")
        self.title.pack(fill="x", padx=12, pady=(12, 2))
        self.where = tk.Label(root, anchor="w", justify="left", wraplength=320)
        self.where.pack(fill="x", padx=12)
        self.mic = tk.Label(root, font=("TkDefaultFont", 15, "bold"), anchor="w")
        self.mic.pack(fill="x", padx=12, pady=(10, 2))
        self.code = tk.Label(root, font=("TkFixedFont", 22, "bold"), anchor="w")
        self.code.pack(fill="x", padx=12)
        row = tk.Frame(root)
        row.pack(fill="x", padx=12, pady=8)
        self.mute_btn = tk.Button(row, command=lambda: self._do(self._mute_action()))
        self.mute_btn.pack(side="left")
        tk.Button(row, text="−", width=3, command=lambda: self._do("volume_down")).pack(side="left", padx=(8, 0))
        self.volume = tk.Label(row, width=6)
        self.volume.pack(side="left")
        tk.Button(row, text="+", width=3, command=lambda: self._do("volume_up")).pack(side="left")
        tk.Button(row, text="Which room?", command=lambda: self._do("identify")).pack(side="left", padx=(8, 0))
        self.pending = tk.Frame(root)
        self.pending.pack(fill="x", padx=12)
        row2 = tk.Frame(root)
        row2.pack(fill="x", padx=12, pady=(4, 12))
        tk.Button(row2, text="Open dashboard", command=lambda: self._do("dashboard")).pack(side="left")
        tk.Button(row2, text="Logs", command=lambda: self._do("logs")).pack(side="left", padx=8)
        self.message = tk.Label(root, anchor="w", justify="left", wraplength=320, fg="#555555")
        self.message.pack(fill="x", padx=12, pady=(0, 10))
        self._pending_ids: tuple = ()
        self.state = "offline"

    def _mute_action(self) -> str:
        return "unmute" if (self.actions.last_status or {}).get("mic") == "muted" else "mute"

    def _do(self, action: str) -> None:
        note = self.actions.do(action)
        if note is not None:
            self.message.config(text=f"{note.summary}\n{note.body}")
        self.refresh()

    def refresh(self) -> None:
        st = self.actions.status()
        self.state = icon_state(st)
        code = self.actions.code() if self.state == "awaiting" else None
        self.title.config(text=f"Domovoi · {room_name(st)}")
        self.where.config(text=connection_text(st))
        text, colour = _WINDOW_MIC_TEXT[self.state]
        self.mic.config(text=text, fg=colour)
        self.code.config(text=f"code {code}" if code else "")
        self.mute_btn.config(text="Unmute" if (st or {}).get("mic") == "muted" else "Mute",
                             state="normal" if st else "disabled")
        vol = (st or {}).get("volume")
        self.volume.config(text="—" if vol is None else f"{vol}%")
        pending = tuple((p.get("id"), p.get("kind"), p.get("summary"), tuple(p.get("details") or ()))
                        for p in (st or {}).get("pending") or [])
        if pending != self._pending_ids:
            self._pending_ids = pending
            for child in self.pending.winfo_children():
                child.destroy()
            for pid, kind, summary, details in pending:
                line = self.tk.Frame(self.pending)
                line.pack(fill="x", pady=2)
                what = PENDING_LABELS.get(kind, kind)
                # Every change it would make, in full (never cut).
                text = NEWLINE.join([f"{what}: {summary}", *(f"  {d}" for d in details)])
                self.tk.Label(line, text=text, anchor="w", wraplength=200,
                              justify="left").pack(side="left", fill="x", expand=True)
                self.tk.Button(line, text="Allow", command=lambda p=pid: self._do(f"approve:{p}")).pack(side="left")
                self.tk.Button(line, text="Not now", command=lambda p=pid: self._do(f"decline:{p}")).pack(side="left")

    def poll(self) -> None:
        self.refresh()
        self.root.after(self.POLL_MS, self.poll)


def run_window(socket_path: Path) -> int:
    try:
        import tkinter as tk
    except ImportError:
        print("no Tkinter here: install python3-tk", file=sys.stderr)
        return 2
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(f"no display for the status window: {e}", file=sys.stderr)
        return 2
    window = StatusWindow(root, TrayActions(control.ControlClient(socket_path)))
    window.poll()
    root.mainloop()
    return 0


# ─── the status feed: the engine's `watch`, kept alive ───────────────────

def watch_forever(socket_path: Path, deliver: Callable[[dict[str, Any] | None], None],
                  stop: threading.Event, *, retry_sec: float = 3.0) -> None:
    """Feed ``deliver`` every status the engine pushes, and ``None`` while
    the engine isn't running (it is retried). Runs on its own thread."""
    while not stop.is_set():
        try:
            for doc in control.ControlClient(socket_path).watch():
                if stop.is_set():
                    return
                if doc.get("event") == "status" or doc.get("verb") == "watch":
                    deliver({k: v for k, v in doc.items() if k not in ("event", "ok", "verb", "req")})
        except (ConnectionError, OSError, ValueError):
            pass
        if stop.is_set():
            return
        deliver(None)
        stop.wait(retry_sec)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME}: the tray icon and status window.")
    parser.add_argument("--window", action="store_true", help="Open the status window instead of the tray icon.")
    parser.add_argument("--control-socket", type=Path, default=DEFAULT_SOCKET,
                        help=f"The engine's control socket (default: {DEFAULT_SOCKET}).")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.window:
        return run_window(args.control_socket)
    try:
        from satellite import tray_dbus
    except ImportError as e:
        print(f"the tray needs dbus-fast ({e}); the status window works without it: "
              f"python -m satellite.tray --window", file=sys.stderr)
        return 2
    import asyncio

    return asyncio.run(tray_dbus.run_tray(args.control_socket))


if __name__ == "__main__":
    raise SystemExit(main())

