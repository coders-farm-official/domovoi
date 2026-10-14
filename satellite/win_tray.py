"""The Windows satellite's tray icon: the mic-live indicator, mute, the
approval code and the "ask me" questions, in the notification area.

Spare-device satellites on Windows (2026-10-13). The same tray as on Linux
(satellite/tray.py holds the model: which icon a status shows, the tooltip,
the menu, the pop-ups, the icon pixels), drawn with the Windows shell's own
notification-area API through ctypes — ``Shell_NotifyIconW``, a popup menu,
and its balloon notifications (shown as toasts on Windows 10 and 11). No
extra library and no build step: it runs under the Python the installer put
in place, whose ``pythonw.exe`` is signed by the Python Software Foundation.

* **Icon:** outline mic while listening for the wake word; filled red while
  the microphone is live; struck through when muted; a speaker badge while
  it speaks or plays; amber while it waits for approval; grey when it is
  offline, refused, unpinned or not running (the tooltip says why).
* **Pop-ups:** the approval code (and in the tooltip and the menu header
  while it waits — never in a log), "Approved", a change waiting for your
  OK, About.
* **Allow** on a waiting change first shows everything it will change, in
  full, and asks once more: a menu line has room for a summary only.
* **Stop satellite…** asks, then sets the user's stop event: the supervisor
  and the engine end (satellite/win_service.py). **Start satellite** comes
  back in its place. **Quit tray** leaves the satellite running.

The tray talks to the engine only through the control pipe
(satellite/control_win.py), and only to a pipe served by this user's own
process. One per user (a named mutex). Windows-only.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

from satellite import control, tray, win32

log = logging.getLogger("satellite.win_tray")

APP_NAME = tray.APP_NAME
TIP_MAX = 127
INFO_TITLE_MAX = 63
INFO_MAX = 255

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_CONTEXTMENU = 0x007B
WM_USER = 0x0400
WM_APP = 0x8000
WM_TRAY = WM_APP + 1          # the icon's callback
WM_STATUS = WM_APP + 2        # a new status from the watcher thread
WM_NOTE = WM_APP + 3          # a pop-up from an action
NIN_SELECT = WM_USER + 0
NIN_KEYSELECT = WM_USER + 1
NIN_BALLOONUSERCLICK = WM_USER + 5
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO, NIF_SHOWTIP = 0x1, 0x2, 0x4, 0x10, 0x80
NIIF_INFO = 0x1
NIIF_NOSOUND = 0x10
NOTIFYICON_VERSION_4 = 4
MF_STRING, MF_GRAYED, MF_SEPARATOR = 0x0, 0x1, 0x800
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD, TPM_BOTTOMALIGN = 0x2, 0x80, 0x100, 0x20
MB_YESNO, MB_OK, MB_ICONQUESTION, MB_ICONINFORMATION = 0x4, 0x0, 0x20, 0x40
MB_SETFOREGROUND, MB_TOPMOST = 0x10000, 0x40000
IDYES = 6
SM_CXSMICON = 49
BI_RGB = 0
DIB_RGB_COLORS = 0
ICON_ID = 1

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", win32.GUID), ("hBalloonIcon", wintypes.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", POINT)]


def _api() -> tuple[Any, Any, Any, Any]:
    win32._need_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassExW.argtypes = (ctypes.POINTER(WNDCLASSEXW),)
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = (wintypes.LPCWSTR, wintypes.HINSTANCE)
    user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.GetMessageW.argtypes = (ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    user32.TranslateMessage.argtypes = (ctypes.POINTER(MSG),)
    user32.DispatchMessageW.argtypes = (ctypes.POINTER(MSG),)
    user32.DispatchMessageW.restype = LRESULT
    user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR)
    user32.TrackPopupMenu.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, wintypes.HWND, wintypes.LPVOID)
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.DestroyMenu.argtypes = (wintypes.HMENU,)
    user32.GetMenuItemCount.argtypes = (wintypes.HMENU,)
    user32.GetMenuStringW.argtypes = (wintypes.HMENU, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int, wintypes.UINT)
    user32.GetMenuState.argtypes = (wintypes.HMENU, wintypes.UINT, wintypes.UINT)
    user32.GetMenuState.restype = wintypes.UINT
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.PostQuitMessage.argtypes = (ctypes.c_int,)
    user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
    user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.CreateIconIndirect.argtypes = (ctypes.POINTER(ICONINFO),)
    user32.CreateIconIndirect.restype = wintypes.HICON
    user32.DestroyIcon.argtypes = (wintypes.HICON,)
    shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
                                       ctypes.POINTER(wintypes.LPVOID), wintypes.HANDLE, wintypes.DWORD)
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateBitmap.argtypes = (ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, wintypes.LPVOID)
    gdi32.CreateBitmap.restype = wintypes.HBITMAP
    gdi32.DeleteObject.argtypes = (wintypes.HANDLE,)
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    return user32, shell32, gdi32, kernel32


def argb_to_bgra(argb: bytes) -> bytes:
    """tray.render_icon's ARGB (network order) as a 32-bit DIB's BGRA."""
    out = bytearray(len(argb))
    out[0::4], out[1::4], out[2::4], out[3::4] = argb[3::4], argb[2::4], argb[1::4], argb[0::4]
    return bytes(out)


def install_root() -> Path:
    return Path(sys.executable).resolve().parent.parent


def supervisor_reason(root: Path) -> str | None:
    """Why the supervisor stopped the satellite, when it says."""
    import json

    try:
        doc = json.loads((root / "logs" / "supervisor-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("state") == "running":
        return None
    reason = doc.get("reason")
    return str(reason)[:120] if reason else None


def _open(target: str) -> bool:
    if not target:
        return False
    try:
        os.startfile(target)  # noqa: S606 — a folder or the dashboard URL the engine named
        return True
    except OSError:
        return False


class WinTray:
    """The notification-area icon and its menu. ``show_balloon`` and
    ``ask`` are attributes so a test can watch them instead of a person."""

    def __init__(self, actions: tray.TrayActions, *, root: Path | None = None) -> None:
        self.user32, self.shell32, self.gdi32, self.kernel32 = _api()
        self.actions = actions
        self.root = root or install_root()
        self.status: dict[str, Any] | None = None
        self.code: str | None = None
        self.planner = tray.NotePlanner()
        self.state = "offline"
        self.last_note: tray.Note | None = None
        self._icons: dict[str, int] = {}
        self._incoming: tuple[dict[str, Any] | None, str | None] | None = None
        self._notes: list[tray.Note] = []
        self._lock = threading.Lock()
        self._added = False
        self.hwnd: int | None = None
        self._class = f"DomovoiSatelliteTray{os.getpid()}"
        self._wndproc = WNDPROC(self._proc)
        self._hinst = self.kernel32.GetModuleHandleW(None)
        self._taskbar_created = self.user32.RegisterWindowMessageW("TaskbarCreated")
        self.show_balloon: Callable[[tray.Note], None] = self._show_balloon
        self.ask: Callable[[str, str], bool] = self._ask

    # ── window and icon ────────────────────────────────────────────────

    def create(self) -> None:
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self._hinst
        wc.lpszClassName = self._class
        if not self.user32.RegisterClassExW(ctypes.byref(wc)):
            raise win32._winerror("RegisterClassExW")
        self.hwnd = self.user32.CreateWindowExW(0, self._class, APP_NAME, 0, 0, 0, 0, 0,
                                                None, None, self._hinst, None)
        if not self.hwnd:
            raise win32._winerror("CreateWindowExW")
        self._add_icon()

    def _nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = ICON_ID
        return nid

    def icon(self, state: str) -> int:
        if state not in self._icons:
            size = max(16, min(64, self.user32.GetSystemMetrics(SM_CXSMICON) or 16))
            self._icons[state] = self._make_icon(tray.render_icon(state, size), size)
        return self._icons[state]

    def _make_icon(self, argb: bytes, size: int) -> int:
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = size, -size          # top-down
        bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, BI_RGB
        bits = wintypes.LPVOID()
        hdc = self.user32.GetDC(None)
        color = self.gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        self.user32.ReleaseDC(None, hdc)
        if not color:
            raise win32._winerror("CreateDIBSection")
        data = argb_to_bgra(argb)
        ctypes.memmove(bits, data, len(data))
        mask_bytes = ctypes.create_string_buffer(((size + 15) // 16) * 2 * size)
        mask = self.gdi32.CreateBitmap(size, size, 1, 1, mask_bytes)
        info = ICONINFO(True, 0, 0, mask, color)
        hicon = self.user32.CreateIconIndirect(ctypes.byref(info))
        self.gdi32.DeleteObject(color)
        self.gdi32.DeleteObject(mask)
        if not hicon:
            raise win32._winerror("CreateIconIndirect")
        return hicon

    def tip(self) -> str:
        title, text = tray.tooltip(self.status, self.code)
        if self.status is None:
            reason = supervisor_reason(self.root)
            if reason:
                text = f"Not running: {reason}"
        return f"{title}\n{text}"[:TIP_MAX]

    def _add_icon(self) -> None:
        nid = self._nid()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.icon(self.state)
        nid.szTip = self.tip()
        self._added = bool(self.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        nid.uVersion = NOTIFYICON_VERSION_4
        self.shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))

    def _update_icon(self) -> None:
        nid = self._nid()
        nid.uFlags = NIF_ICON | NIF_TIP | NIF_SHOWTIP
        nid.hIcon = self.icon(self.state)
        nid.szTip = self.tip()
        self.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _show_balloon(self, note: tray.Note) -> None:
        nid = self._nid()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = note.summary[:INFO_TITLE_MAX]
        nid.szInfo = (note.body or " ")[:INFO_MAX]
        nid.dwInfoFlags = NIIF_INFO
        self.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _ask(self, title: str, text: str) -> bool:
        return self.user32.MessageBoxW(None, text, title,
                                       MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST) == IDYES

    def _tell(self, title: str, text: str) -> None:
        self.user32.MessageBoxW(None, text, title, MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND)

    def remove(self) -> None:
        if self._added and self.hwnd:
            self.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid()))
            self._added = False
        for h in self._icons.values():
            self.user32.DestroyIcon(h)
        self._icons.clear()
        if self.hwnd:
            self.user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        self.user32.UnregisterClassW(self._class, self._hinst)

    # ── status (from the watcher thread) ───────────────────────────────

    def deliver(self, st: dict[str, Any] | None) -> None:
        """Called on the watcher thread with each status (None while the
        engine is not running): fetches the code while it waits for
        approval, then hands both to the window thread."""
        code = self.actions.code() if st and st.get("connection") == "awaiting_approval" else None
        with self._lock:
            self._incoming = (st, code)
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, WM_STATUS, 0, 0)

    def apply_status(self) -> None:
        with self._lock:
            incoming, self._incoming = self._incoming, None
        if incoming is None:
            return
        self.status, self.code = incoming
        self.actions.last_status = self.status
        self.state = tray.icon_state(self.status)
        self._update_icon()
        for note in self.planner.plan(self.status, self.code):
            self.last_note = note
            self.show_balloon(note)

    # ── the menu ───────────────────────────────────────────────────────

    def menu_items(self) -> list[tray.MenuItem]:
        items = tray.menu(self.status, can_start=True)
        if self.status and self.status.get("connection") == "awaiting_approval" and self.code:
            head = items[0]
            items[0] = tray.MenuItem(head.id, f"{tray.room_name(self.status)} · approval code {self.code}",
                                     enabled=False)
        return items

    def build_menu(self) -> tuple[int, dict[int, str]]:
        hmenu = self.user32.CreatePopupMenu()
        actions: dict[int, str] = {}
        for item in self.menu_items():
            if not item.visible:
                continue
            if item.separator:
                self.user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                continue
            flags = MF_STRING | (0 if item.enabled and item.action else MF_GRAYED)
            self.user32.AppendMenuW(hmenu, flags, item.id, item.label.replace("&", "&&"))
            if item.action:
                actions[item.id] = item.action
        return hmenu, actions

    def show_menu(self) -> None:
        hmenu, actions = self.build_menu()
        try:
            pt = POINT()
            self.user32.GetCursorPos(ctypes.byref(pt))
            self.user32.SetForegroundWindow(self.hwnd)
            chosen = self.user32.TrackPopupMenu(
                hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY | TPM_BOTTOMALIGN,
                pt.x, pt.y, 0, self.hwnd, None)
            self.user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            self.user32.DestroyMenu(hmenu)
        action = actions.get(chosen)
        if action:
            self.run_action(action)

    # ── actions ────────────────────────────────────────────────────────

    def confirm_text(self, action: str) -> tuple[str, str] | None:
        """For Allow: everything the waiting change will do, in full."""
        verb, _, pid = action.partition(":")
        if verb != "approve":
            return None
        pending = next((p for p in (self.status or {}).get("pending") or [] if str(p.get("id")) == pid), None)
        if pending is None:
            return None
        what = tray.PENDING_LABELS.get(pending.get("kind"), "this change")
        lines = [f"The Domovoi server asks to apply {what}:", "", str(pending.get("summary") or "")]
        keys = pending.get("keys")
        if isinstance(keys, list) and keys:
            lines += ["", "Every setting it changes:"] + [f"  • {k}" for k in keys]
        lines += ["", "Allow it now? (It may restart the satellite.)"]
        return f"{APP_NAME} · {tray.room_name(self.status)}", "\n".join(lines)

    def run_action(self, action: str) -> None:
        if action == "quit":
            self.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            return
        threading.Thread(target=self._do, args=(action,), name=f"tray-{action}", daemon=True).start()

    def _do(self, action: str) -> None:
        if action == "about":
            note = tray.about_note(self.status)
            self._tell(note.summary, note.body)
            return
        if action == "stop_ask":
            if not self.ask(APP_NAME, "Stop the satellite?\n\nIt stops listening and leaves the room "
                                      "until you start it again (Start menu: Domovoi Satellite, or "
                                      "this menu), or sign in again."):
                return
            action = "stop"
        confirm = self.confirm_text(action)
        if confirm is not None and not self.ask(*confirm):
            return
        note = self.actions.do(action)
        if note is not None:
            with self._lock:
                self._notes.append(note)
            if self.hwnd:
                self.user32.PostMessageW(self.hwnd, WM_NOTE, 0, 0)

    def _flush_notes(self) -> None:
        with self._lock:
            notes, self._notes = self._notes, []
        for note in notes:
            self.last_note = note
            self.show_balloon(note)

    def _balloon_clicked(self) -> None:
        note = self.last_note
        if note is None:
            return
        if note.key.startswith("pending:"):
            self.show_menu()
        elif any(a == "dashboard" for a, _ in note.actions):
            self.run_action("dashboard")

    # ── the window procedure ───────────────────────────────────────────

    def _proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event in (WM_CONTEXTMENU, NIN_SELECT, NIN_KEYSELECT):
                    self.show_menu()
                elif event == NIN_BALLOONUSERCLICK:
                    self._balloon_clicked()
                return 0
            if msg == WM_STATUS:
                self.apply_status()
                return 0
            if msg == WM_NOTE:
                self._flush_notes()
                return 0
            if msg == self._taskbar_created and msg:
                self._add_icon()          # Explorer restarted
                return 0
            if msg == WM_CLOSE:
                self.remove()
                return 0
            if msg == WM_DESTROY:
                self.user32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001 — never let an exception cross into the shell
            log.exception("tray: message %#x failed", msg)
            return 0
        return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def loop(self) -> None:
        msg = MSG()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))


# ─── start and stop, the Windows way ──────────────────────────────────────

def stop_satellite() -> tray.Note:
    """Set the user's stop event: the supervisor and the engine end."""
    event = win32.NamedEvent(win32.object_name(win32.STOP_EVENT))
    event.set()
    event.close()
    return tray.Note("stop", "Satellite stopped",
                     "It is not listening. Start it again from this menu or the Start menu "
                     "(Domovoi Satellite).")


def start_satellite(root: Path | None = None) -> tray.Note:
    root = root or install_root()
    from satellite import win_service

    try:
        subprocess.Popen(
            [win_service.interpreter(root, windowed=True), "-m", "satellite.win_service", "--no-tray"],
            cwd=str(root / "app"), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, creationflags=win_service.DETACHED_PROCESS,
        )
    except OSError as e:
        return tray.Note("start", "Could not start the satellite", str(e))
    return tray.Note("start", "Starting the satellite", "It listens again in a few seconds.")


def main(argv: list[str] | None = None) -> int:
    del argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    single = win32.SingleInstance(win32.object_name(win32.TRAY_MUTEX))
    if single.already_running:
        return 0
    try:
        ctypes.WinDLL("user32").SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (OSError, AttributeError):
        pass
    address = control.default_address(Path.home())
    actions = tray.TrayActions(control.ControlClient(address), opener=_open,
                               stopper=stop_satellite, starter=start_satellite)
    icon = WinTray(actions)
    icon.create()
    stop = threading.Event()
    threading.Thread(target=tray.watch_forever, args=(address, icon.deliver, stop),
                     name="tray-watch", daemon=True).start()
    quit_event = win32.NamedEvent(win32.object_name(win32.TRAY_QUIT_EVENT))
    quit_event.reset()

    def quit_when_asked() -> None:
        while not stop.is_set():
            if quit_event.wait(1.0):
                if icon.hwnd:
                    icon.user32.PostMessageW(icon.hwnd, WM_CLOSE, 0, 0)
                return

    threading.Thread(target=quit_when_asked, name="tray-quit", daemon=True).start()
    try:
        icon.loop()
    finally:
        stop.set()
        if icon.hwnd:
            icon.remove()
        single.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
