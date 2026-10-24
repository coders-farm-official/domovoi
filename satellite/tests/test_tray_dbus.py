"""The tray on a real session D-Bus (satellite/tray_dbus.py).

Run under ``dbus-run-session`` in the Linux test container: a fake
StatusNotifierWatcher and a fake notification server own their well-known
names on a private session bus, a fake engine answers the control socket,
and the real tray process logic connects to all three. The test then does
what a panel and a person do: reads the item's properties, opens the menu,
clicks items, invokes a pop-up's action, and watches the icon follow the
engine's state (the mic-live indicator above all).

Skipped where there is no session bus (Windows, a bare CI shell).
"""

import asyncio
import os
import sys

import pytest

dbus_fast = pytest.importorskip("dbus_fast")

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
    reason="needs a session D-Bus (dbus-run-session in the Linux container)",
)

from dbus_fast import Message, MessageType, PropertyAccess, Variant  # noqa: E402
from dbus_fast.aio import MessageBus  # noqa: E402
from dbus_fast.service import ServiceInterface, dbus_property, method, signal  # noqa: E402

from satellite import control, tray  # noqa: E402
from satellite import tray_dbus  # noqa: E402
from satellite.tests.test_control_protocol import FakeEngine  # noqa: E402


class FakeWatcher(ServiceInterface):
    def __init__(self):
        super().__init__("org.kde.StatusNotifierWatcher")
        self.registered = []

    @method()
    def RegisterStatusNotifierItem(self, service: 's') -> None:
        self.registered.append(service)

    @method()
    def RegisterStatusNotifierHost(self, service: 's') -> None:
        return None

    @dbus_property(access=PropertyAccess.READ)
    def IsStatusNotifierHostRegistered(self) -> 'b':
        return True

    @dbus_property(access=PropertyAccess.READ)
    def ProtocolVersion(self) -> 'i':
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def RegisteredStatusNotifierItems(self) -> 'as':
        return list(self.registered)


class FakeNotifications(ServiceInterface):
    def __init__(self):
        super().__init__("org.freedesktop.Notifications")
        self.shown = []
        self.next_id = 0
        self._invoke = (0, "")

    @method()
    def Notify(self, app: 's', replaces: 'u', icon: 's', summary: 's', body: 's',
               actions: 'as', hints: 'a{sv}', timeout: 'i') -> 'u':
        if replaces:
            nid = replaces
        else:
            self.next_id += 1
            nid = self.next_id
        self.shown.append({"id": nid, "replaces": replaces, "summary": summary, "body": body,
                           "actions": list(actions), "hints": {k: v.value for k, v in hints.items()},
                           "timeout": timeout})
        return nid

    @method()
    def CloseNotification(self, nid: 'u') -> None:
        return None

    @method()
    def GetCapabilities(self) -> 'as':
        return ["actions", "body", "persistence"]

    @method()
    def GetServerInformation(self) -> 'ssss':
        return ["fake", "test", "1", "1.2"]

    @signal()
    def ActionInvoked(self) -> 'us':
        return list(self._invoke)

    def invoke(self, nid, key):
        self._invoke = (nid, key)
        self.ActionInvoked()


async def until(cond, timeout=6.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return cond()


async def call(bus, dest, path, iface, member, sig="", body=None):
    reply = await bus.call(Message(destination=dest, path=path, interface=iface, member=member,
                                   signature=sig, body=body or []))
    assert reply.message_type == MessageType.METHOD_RETURN, reply.body
    return reply.body


async def prop(bus, dest, name):
    body = await call(bus, dest, tray_dbus.SNI_PATH, "org.freedesktop.DBus.Properties", "Get",
                      "ss", [tray_dbus.SNI_INTERFACE, name])
    return body[0].value


async def layout(bus, dest):
    body = await call(bus, dest, tray_dbus.MENU_PATH, tray_dbus.MENU_INTERFACE, "GetLayout",
                      "iias", [0, -1, []])
    revision, (_root, _props, children) = body
    items = []
    for child in children:
        cid, props, _ = child.value
        items.append((cid, {k: v.value for k, v in props.items()}))
    return revision, items


def item_id(items, label):
    return next(cid for cid, p in items if p.get("label") == label)


@pytest.fixture
async def desktop(tmp_path):
    """A panel (the watcher), a notification server and an engine."""
    host = await MessageBus().connect()
    watcher, notes = FakeWatcher(), FakeNotifications()
    await host.request_name("org.kde.StatusNotifierWatcher")
    await host.request_name("org.freedesktop.Notifications")
    host.export("/StatusNotifierWatcher", watcher)
    host.export("/org/freedesktop/Notifications", notes)
    engine = FakeEngine({"connection": "connected", "mic": "listening"})
    sock = tmp_path / "control.sock"
    server = control.ControlServer(engine, control.UnixSocketTransport(sock))
    engine.server = server
    await server.start()
    yield host, watcher, notes, engine, sock
    await server.close()
    host.disconnect()


async def test_the_tray_registers_shows_the_state_and_its_menu_drives_the_engine(desktop):
    host, watcher, notes, engine, sock = desktop
    panel = await MessageBus().connect()
    signals = []

    def on_signal(msg):
        if msg.message_type == MessageType.SIGNAL and msg.member in (
                "NewIcon", "NewToolTip", "NewStatus", "LayoutUpdated"):
            signals.append(msg.member)
    panel.add_message_handler(on_signal)
    await call(panel, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
               "AddMatch", "s", ["type='signal'"])

    tray_bus = await MessageBus().connect()
    t = tray_dbus.Tray(tray_bus, sock, window_opener=lambda: None)
    await t.start()
    try:
        assert watcher.registered == [t.bus_name], "registered with the panel's watcher"
        assert await until(lambda: t.status is not None)
        name = t.bus_name

        # What a panel reads.
        assert await prop(panel, name, "Status") == "Active"
        assert await prop(panel, name, "Id") == "domovoi-satellite"
        assert await prop(panel, name, "Menu") == "/MenuBar"
        pix = await prop(panel, name, "IconPixmap")
        assert [(w, h) for w, h, _ in pix] == [(s, s) for s in tray.ICON_SIZES]
        assert bytes(pix[1][2]) == tray.icon_pixmaps("idle")[1][2]
        tip = await prop(panel, name, "ToolTip")
        assert tip[2] == "Domovoi · Kid's Room" and "wake word" in tip[3]

        # The menu, as the panel draws it.
        revision, items = await layout(panel, name)
        labels = [p.get("label") for _, p in items if p.get("type") != "separator"]
        assert "Mute listening" in labels and "Stop the room…" in labels
        assert labels[0] == "Kid's Room · connected to 192.168.1.20"

        # Mute from the menu: the engine mutes, and the icon and menu follow.
        await call(panel, name, tray_dbus.MENU_PATH, tray_dbus.MENU_INTERFACE, "Event", "isvu",
                   [item_id(items, "Mute listening"), "clicked", Variant("s", ""), 0])
        assert await until(lambda: engine.status["mic"] == "muted")
        assert await until(lambda: t.state == "muted")
        assert bytes((await prop(panel, name, "IconPixmap"))[1][2]) == tray.icon_pixmaps("muted")[1][2]
        new_revision, items = await layout(panel, name)
        assert new_revision > revision
        assert "Unmute listening" in [p.get("label") for _, p in items]
        await until(lambda: "NewIcon" in signals and "LayoutUpdated" in signals)
        assert "NewIcon" in signals and "LayoutUpdated" in signals

        # The mic-live indicator: a capture starts in the engine.
        engine.status["mic"] = "capturing"
        engine.server.notify()
        assert await until(lambda: t.state == "capturing")
        assert "Microphone live" in (await prop(panel, name, "ToolTip"))[3]
        assert bytes((await prop(panel, name, "IconPixmap"))[0][2]) == tray.icon_pixmaps("capturing")[0][2]

        # Volume up from the menu.
        await call(panel, name, tray_dbus.MENU_PATH, tray_dbus.MENU_INTERFACE, "Event", "isvu",
                   [item_id(items, "Volume up"), "clicked", Variant("s", ""), 0])
        assert await until(lambda: engine.status["volume"] == 50)
    finally:
        await t.stop()
        tray_bus.disconnect()
        panel.disconnect()


async def test_the_approval_code_pops_up_and_approved_replaces_it(desktop):
    host, watcher, notes, engine, sock = desktop
    engine.status.update(connection="awaiting_approval", mic="off")
    tray_bus = await MessageBus().connect()
    t = tray_dbus.Tray(tray_bus, sock, window_opener=lambda: None)
    await t.start()
    try:
        assert await until(lambda: notes.shown)
        first = notes.shown[0]
        assert "482913" in first["summary"] and first["timeout"] == 0
        assert first["hints"].get("resident") is True
        assert first["actions"] == ["dashboard", "Open dashboard"]
        assert t.sni_status() == "NeedsAttention"
        assert "482913" in tray.tooltip(t.status, t.code)[1]

        engine.status.update(connection="connected", mic="listening")
        engine.server.notify()
        assert await until(lambda: len(notes.shown) == 2)
        second = notes.shown[1]
        assert second["replaces"] == first["id"], "the code's pop-up becomes 'Approved'"
        assert second["summary"] == "Approved: this device is now the Kid's Room"
    finally:
        await t.stop()
        tray_bus.disconnect()


async def test_an_update_waiting_for_ok_is_allowed_from_its_pop_up(desktop):
    host, watcher, notes, engine, sock = desktop
    tray_bus = await MessageBus().connect()
    t = tray_dbus.Tray(tray_bus, sock, window_opener=lambda: None)
    await t.start()
    try:
        assert await until(lambda: t.status is not None)
        action = engine.pending.offer("update", "new room software (44f0fff → 9c1d2aa)",
                                      lambda: engine.ran.append("update"))
        assert await until(lambda: any(n["summary"].endswith("the room software update") for n in notes.shown))
        note = next(n for n in notes.shown if n["summary"].endswith("the room software update"))
        assert note["actions"] == [f"approve:{action.id}", "Allow", f"decline:{action.id}", "Not now"]
        assert t.sni_status() == "NeedsAttention"
        notes.invoke(note["id"], f"approve:{action.id}")
        assert await until(lambda: engine.ran == ["update"])
        assert await until(lambda: t.sni_status() == "Active")
    finally:
        await t.stop()
        tray_bus.disconnect()


async def test_quit_tray_leaves_the_engine_running_and_a_desktop_without_a_tray_still_gets_pop_ups(tmp_path):
    host = await MessageBus().connect()
    notes = FakeNotifications()
    await host.request_name("org.freedesktop.Notifications")
    host.export("/org/freedesktop/Notifications", notes)
    engine = FakeEngine({"connection": "awaiting_approval", "mic": "off"})
    sock = tmp_path / "control.sock"
    server = control.ControlServer(engine, control.UnixSocketTransport(sock))
    engine.server = server
    await server.start()
    tray_bus = await MessageBus().connect()
    t = tray_dbus.Tray(tray_bus, sock, window_opener=lambda: None)
    try:
        await t.start()
        assert t.registered is False, "no watcher: vanilla GNOME"
        assert await until(lambda: notes.shown and "482913" in notes.shown[0]["summary"])
        quit_id = next(i.id for i in t.items if i.action == "quit")
        t.menu_clicked(quit_id)
        assert await until(lambda: t.done.is_set())
        got = await asyncio.to_thread(control.ControlClient(sock).call, "status")
        assert got["ok"], "the engine keeps running"
    finally:
        await t.stop()
        tray_bus.disconnect()
        await server.close()
        host.disconnect()
