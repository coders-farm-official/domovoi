"""The tray on D-Bus: a StatusNotifierItem, its dbusmenu, and pop-ups.

The D-Bus half of :mod:`satellite.tray` (which holds the model, the icons
and the actions), on ``dbus-fast`` (MIT) over the user's session bus:

* ``org.kde.StatusNotifierItem`` at ``/StatusNotifierItem``, registered
  with ``org.kde.StatusNotifierWatcher`` (and again whenever a watcher
  appears: a panel restarting, the GNOME extension switched on);
* ``com.canonical.dbusmenu`` at ``/MenuBar`` (protocol version 3);
* ``org.freedesktop.Notifications`` for the pop-ups, with their actions.

No annotations are postponed in this module on purpose: dbus-fast reads
the D-Bus signatures from the annotations as written (``'s'``, ``'ai'``).
"""

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Any

from dbus_fast import Message, MessageType, PropertyAccess, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property, method, signal

from satellite import tray

log = logging.getLogger("satellite.tray")

SNI_INTERFACE = "org.kde.StatusNotifierItem"
SNI_PATH = "/StatusNotifierItem"
MENU_INTERFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"
WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
NOTIFY = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
DBUS = "org.freedesktop.DBus"


class StatusNotifierItem(ServiceInterface):
    def __init__(self, owner: "Tray") -> None:
        super().__init__(SNI_INTERFACE)
        self._t = owner

    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> 's':
        return "Hardware"

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> 's':
        return tray.APP_ID

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> 's':
        return f"Domovoi · {tray.room_name(self._t.status)}"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> 's':
        return self._t.sni_status()

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> 'i':
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> 's':
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def IconPixmap(self) -> 'a(iiay)':
        return [list(p) for p in tray.icon_pixmaps(self._t.state)]

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconName(self) -> 's':
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconPixmap(self) -> 'a(iiay)':
        return []

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconName(self) -> 's':
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconPixmap(self) -> 'a(iiay)':
        return [list(p) for p in tray.icon_pixmaps(self._t.state)]

    @dbus_property(access=PropertyAccess.READ)
    def AttentionMovieName(self) -> 's':
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def ToolTip(self) -> '(sa(iiay)ss)':
        title, text = tray.tooltip(self._t.status, self._t.code)
        return ["", [], title, text]

    @dbus_property(access=PropertyAccess.READ)
    def ItemIsMenu(self) -> 'b':
        return True

    @dbus_property(access=PropertyAccess.READ)
    def Menu(self) -> 'o':
        return MENU_PATH

    @method()
    def ContextMenu(self, x: 'i', y: 'i') -> None:
        return None

    @method()
    def Activate(self, x: 'i', y: 'i') -> None:
        # A host that calls Activate instead of showing the menu: the
        # window has the same controls.
        self._t.open_window()

    @method()
    def SecondaryActivate(self, x: 'i', y: 'i') -> None:
        return None

    @method()
    def Scroll(self, delta: 'i', orientation: 's') -> None:
        if orientation == "vertical" and delta:
            self._t.run_action("volume_up" if delta > 0 else "volume_down")

    @signal()
    def NewTitle(self) -> None:
        return None

    @signal()
    def NewIcon(self) -> None:
        return None

    @signal()
    def NewAttentionIcon(self) -> None:
        return None

    @signal()
    def NewToolTip(self) -> None:
        return None

    @signal()
    def NewStatus(self) -> 's':
        return self._t.sni_status()


def _props(item: tray.MenuItem) -> dict[str, Variant]:
    if item.separator:
        return {"type": Variant("s", "separator"), "visible": Variant("b", item.visible)}
    return {
        "label": Variant("s", item.label.replace("_", "__")),
        "enabled": Variant("b", item.enabled),
        "visible": Variant("b", item.visible),
    }


class DbusMenu(ServiceInterface):
    def __init__(self, owner: "Tray") -> None:
        super().__init__(MENU_INTERFACE)
        self._t = owner
        self.revision = 1

    def _items(self) -> list[tray.MenuItem]:
        return self._t.items

    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> 'u':
        return 3

    @dbus_property(access=PropertyAccess.READ)
    def TextDirection(self) -> 's':
        return "ltr"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> 's':
        return "normal"

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> 'as':
        return []

    @method()
    def GetLayout(self, parent_id: 'i', recursion_depth: 'i', property_names: 'as') -> 'u(ia{sv}av)':
        children = []
        if parent_id == 0 and recursion_depth != 0:
            children = [Variant("(ia{sv}av)", [it.id, _props(it), []]) for it in self._items()]
        if parent_id == 0:
            root = [0, {"children-display": Variant("s", "submenu")}, children]
        else:
            item = next((it for it in self._items() if it.id == parent_id), None)
            root = [parent_id, _props(item) if item else {}, []]
        return [self.revision, root]

    @method()
    def GetGroupProperties(self, ids: 'ai', property_names: 'as') -> 'a(ia{sv})':
        wanted = set(ids)
        return [[it.id, _props(it)] for it in self._items() if not wanted or it.id in wanted]

    @method()
    def GetProperty(self, id_: 'i', name: 's') -> 'v':
        item = next((it for it in self._items() if it.id == id_), None)
        return (_props(item).get(name) if item else None) or Variant("s", "")

    @method()
    def Event(self, id_: 'i', event_id: 's', data: 'v', timestamp: 'u') -> None:
        if event_id == "clicked":
            self._t.menu_clicked(id_)

    @method()
    def EventGroup(self, events: 'a(isvu)') -> 'ai':
        for id_, event_id, _data, _ts in events:
            if event_id == "clicked":
                self._t.menu_clicked(id_)
        return []

    @method()
    def AboutToShow(self, id_: 'i') -> 'b':
        return False

    @method()
    def AboutToShowGroup(self, ids: 'ai') -> 'aiai':
        return [[], []]

    @signal()
    def LayoutUpdated(self) -> 'ui':
        return [self.revision, 0]

    @signal()
    def ItemsPropertiesUpdated(self) -> 'a(ia{sv})a(ias)':
        return [[], []]


class Tray:
    """One tray: the item, the menu, the pop-ups, fed by the engine's
    ``watch``. ``run_action`` and the pop-up actions go through
    :class:`satellite.tray.TrayActions`."""

    def __init__(self, bus: MessageBus, socket_path: Path, *, actions: tray.TrayActions | None = None,
                 window_opener=None) -> None:
        from satellite import control

        self.bus = bus
        self.socket_path = Path(socket_path)
        self.actions = actions or tray.TrayActions(control.ControlClient(self.socket_path))
        self.status: dict[str, Any] | None = None
        self.state = "offline"
        self.code: str | None = None
        self.items = tray.menu(None)
        self.planner = tray.NotePlanner()
        self.sni = StatusNotifierItem(self)
        self.dbusmenu = DbusMenu(self)
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.registered = False
        self.notes: dict[str, int] = {}          # note key -> notification id
        self.note_keys: dict[int, str] = {}      # notification id -> note key
        self.done = asyncio.Event()
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._window_opener = window_opener
        self._tasks: set[asyncio.Task] = set()

    # ── lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.bus.request_name(self.bus_name)
        self.bus.export(SNI_PATH, self.sni)
        self.bus.export(MENU_PATH, self.dbusmenu)
        self.bus.add_message_handler(self._on_message)
        for rule in (
            f"type='signal',interface='{NOTIFY}'",
            f"type='signal',sender='{DBUS}',member='NameOwnerChanged',arg0='{WATCHER}'",
        ):
            await self._call(DBUS, "/org/freedesktop/DBus", DBUS, "AddMatch", "s", [rule])
        await self.register()
        if not self.registered:
            log.info("no tray on this desktop (no %s): pop-ups only; the status "
                     "window is in the app menu (python -m satellite.tray --window)", WATCHER)
        threading.Thread(target=tray.watch_forever, args=(self.socket_path, self._deliver, self._stop),
                         daemon=True, name="tray-watch").start()

    async def stop(self) -> None:
        self._stop.set()
        self.done.set()

    async def register(self) -> bool:
        owner = await self._call(DBUS, "/org/freedesktop/DBus", DBUS, "NameHasOwner", "s", [WATCHER])
        if owner is None or not owner.body or not owner.body[0]:
            self.registered = False
            return False
        reply = await self._call(WATCHER, WATCHER_PATH, WATCHER, "RegisterStatusNotifierItem",
                                 "s", [self.bus_name])
        self.registered = reply is not None
        if self.registered:
            log.info("tray icon registered with %s", WATCHER)
        return self.registered

    async def _call(self, dest, path, iface, member, sig="", body=None) -> Message | None:
        reply = await self.bus.call(Message(destination=dest, path=path, interface=iface,
                                            member=member, signature=sig, body=body or []))
        if reply is None or reply.message_type == MessageType.ERROR:
            log.debug("%s.%s failed: %s", iface, member, reply.body if reply else None)
            return None
        return reply

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── the status feed ────────────────────────────────────────────────

    def _deliver(self, st: dict[str, Any] | None) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self.on_status, st)

    def sni_status(self) -> str:
        if self.state == "awaiting" or (self.status or {}).get("pending"):
            return "NeedsAttention"
        return "Active"

    def on_status(self, st: dict[str, Any] | None) -> None:
        old_state, old_tip, old_sni = self.state, tray.tooltip(self.status, self.code), self.sni_status()
        self.status = st
        self.actions.last_status = st
        self.state = tray.icon_state(st)
        if self.state != "awaiting":
            self.code = None
        items = tray.menu(st)
        if items != self.items:
            self.items = items
            self.dbusmenu.revision += 1
            self.dbusmenu.LayoutUpdated()
        if self.state != old_state:
            self.sni.NewIcon()
            self.sni.NewAttentionIcon()
            self.sni.NewTitle()
        if tray.tooltip(st, self.code) != old_tip:
            self.sni.NewToolTip()
        if self.sni_status() != old_sni:
            self.sni.NewStatus()
        self._spawn(self._notes_for(st))

    async def _notes_for(self, st: dict[str, Any] | None) -> None:
        code = None
        if tray.icon_state(st) == "awaiting":
            code = await asyncio.to_thread(self.actions.code)
            if code != self.code:
                self.code = code
                self.sni.NewToolTip()
        for note in self.planner.plan(st, code):
            await self.show(note)

    # ── pop-ups ────────────────────────────────────────────────────────

    async def show(self, note: tray.Note) -> int | None:
        actions: list[str] = []
        for key, label in note.actions:
            actions += [key, label]
        hints = {"desktop-entry": Variant("s", tray.APP_ID)}
        if note.persistent:
            hints["resident"] = Variant("b", True)
            hints["urgency"] = Variant("y", 2)
        reply = await self._call(NOTIFY, NOTIFY_PATH, NOTIFY, "Notify", "susssasa{sv}i", [
            tray.APP_NAME, self.notes.get(note.key, 0), "audio-input-microphone",
            note.summary, note.body, actions, hints, 0 if note.persistent else 10000,
        ])
        if reply is None:
            return None
        nid = int(reply.body[0])
        self.notes[note.key] = nid
        self.note_keys[nid] = note.key
        return nid

    def _on_message(self, msg: Message) -> None:
        if msg.message_type != MessageType.SIGNAL:
            return None
        if msg.interface == NOTIFY and msg.member == "ActionInvoked":
            nid, action = msg.body
            if nid in self.note_keys and action not in ("default", "cancel"):
                self.run_action(action)
            elif nid in self.note_keys and action == "default" and self.note_keys[nid] == "approval":
                self.run_action("dashboard")
        elif msg.interface == NOTIFY and msg.member == "NotificationClosed":
            nid = msg.body[0]
            key = self.note_keys.pop(nid, None)
            if key is not None and self.notes.get(key) == nid:
                self.notes.pop(key, None)
        elif msg.member == "NameOwnerChanged" and msg.body and msg.body[0] == WATCHER and msg.body[2]:
            self._spawn(self.register())
        return None

    # ── actions ────────────────────────────────────────────────────────

    def menu_clicked(self, item_id: int) -> None:
        item = next((it for it in self.items if it.id == item_id), None)
        if item is not None and item.action and item.enabled:
            self.run_action(item.action)

    def run_action(self, action: str) -> None:
        if action == "quit":
            log.info("tray quit (the satellite keeps running)")
            self._spawn(self.stop())
            return
        self._spawn(self._act(action))

    async def _act(self, action: str) -> None:
        note = await asyncio.to_thread(self.actions.do, action)
        if note is not None:
            await self.show(note)

    def open_window(self) -> None:
        if self._window_opener is not None:
            self._window_opener()
            return
        import subprocess
        import sys

        try:
            subprocess.Popen([sys.executable, "-m", "satellite.tray", "--window",
                              "--control-socket", str(self.socket_path)],
                             stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            log.warning("could not open the status window: %s", e)


async def run_tray(socket_path: Path) -> int:
    try:
        bus = await MessageBus().connect()
    except Exception as e:  # noqa: BLE001 — no session bus: nothing to show it on
        log.error("no session D-Bus (%s); the tray needs a desktop session", e)
        return 2
    t = Tray(bus, socket_path)
    await t.start()
    await t.done.wait()
    bus.disconnect()
    return 0
