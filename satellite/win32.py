"""The Windows calls a spare-PC satellite needs, through ctypes (no pywin32).

Spare-device satellites on Windows (2026-10-13). Everything here is a thin,
typed wrapper over one Win32 call or a small group of them, so the modules
that use it (the control channel's named pipe, the supervisor, the tray, the
installer's power questions, the health sample's battery, the volume) read
as plain Python:

* **Who:** :func:`current_user_sid`, :func:`process_user_sid` — the SID of
  this process's user and of another process's, the Windows counterpart of
  the Unix socket's ``SO_PEERCRED`` check.
* **The control pipe:** :func:`control_pipe_name`, :class:`PipeSecurity`
  (a DACL that lets only this user in), :func:`create_pipe_instance`
  (byte mode, remote clients refused, the first instance claimed with
  ``FILE_FLAG_FIRST_PIPE_INSTANCE`` so nobody can squat the name), and
  :func:`pipe_client_pid` / :func:`pipe_server_pid` for the peer checks.
* **Named events and a mutex** in the session's ``Local\\`` namespace: how
  the tray and the uninstaller ask the supervisor and the engine to stop
  (:class:`NamedEvent`), and how the supervisor and the tray stay single
  (:class:`SingleInstance`).
* **Battery:** :func:`power_status` — ``GetSystemPowerStatus`` in the
  health sample's ``battery`` shape.
* **Volume:** :class:`EndpointVolume` — the default speakers' master volume
  through Windows Core Audio (``IAudioEndpointVolume``), the same control
  the taskbar's volume slider moves.

Importable on any OS (the tests import it everywhere); every function that
needs Windows raises :class:`NotWindows` elsewhere. Stdlib only.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import uuid
from ctypes import wintypes
from typing import Any

log = logging.getLogger("satellite.win32")

IS_WINDOWS = sys.platform == "win32"

#: The control pipe's name stem; the user's SID follows it.
PIPE_STEM = r"\\.\pipe\domovoi-satellite-"
#: Named objects live in the signed-in session's namespace.
LOCAL = "Local\\"

# Win32 constants (winbase.h, winnt.h, winerror.h).
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
ERROR_FILE_NOT_FOUND = 2
ERROR_PIPE_BUSY = 231
ERROR_BROKEN_PIPE = 109
ERROR_IO_PENDING = 997
ERROR_OPERATION_ABORTED = 995
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
INFINITE = 0xFFFFFFFF
PIPE_ACCESS_DUPLEX = 0x00000003
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
PIPE_UNLIMITED_INSTANCES = 255
PIPE_BUFFER = 65536
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SDDL_REVISION_1 = 1
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000


class NotWindows(OSError):
    """A Windows-only call on another OS."""


def _need_windows() -> None:
    if not IS_WINDOWS:
        raise NotWindows("this needs Windows")


def _kernel32():
    _need_windows()
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.OpenProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.CloseHandle.restype = wintypes.BOOL
    k.LocalFree.argtypes = (wintypes.HLOCAL,)
    k.LocalFree.restype = wintypes.HLOCAL
    k.GetNamedPipeClientProcessId.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
    k.GetNamedPipeClientProcessId.restype = wintypes.BOOL
    k.GetNamedPipeServerProcessId.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
    k.GetNamedPipeServerProcessId.restype = wintypes.BOOL
    k.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    k.CreateEventW.restype = wintypes.HANDLE
    k.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    k.OpenEventW.restype = wintypes.HANDLE
    k.SetEvent.argtypes = (wintypes.HANDLE,)
    k.SetEvent.restype = wintypes.BOOL
    k.ResetEvent.argtypes = (wintypes.HANDLE,)
    k.ResetEvent.restype = wintypes.BOOL
    k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.GetSystemPowerStatus.argtypes = (ctypes.POINTER(SYSTEM_POWER_STATUS),)
    k.GetSystemPowerStatus.restype = wintypes.BOOL
    return k


def _advapi32():
    _need_windows()
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    a.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    a.OpenProcessToken.restype = wintypes.BOOL
    a.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    a.GetTokenInformation.restype = wintypes.BOOL
    a.ConvertSidToStringSidW.argtypes = (wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR))
    a.ConvertSidToStringSidW.restype = wintypes.BOOL
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.ULONG))
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    return a


def _winerror(what: str) -> OSError:
    err = ctypes.get_last_error()
    return OSError(0, f"{what} failed: {ctypes.FormatError(err).strip()}", None, err)


# ─── who: the SID of this process's user, and of another process's ────────

def _token_user_sid(token: wintypes.HANDLE) -> str:
    a, k = _advapi32(), _kernel32()
    need = wintypes.DWORD(0)
    a.GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(need))
    if not need.value:
        raise _winerror("GetTokenInformation(size)")
    buf = ctypes.create_string_buffer(need.value)
    if not a.GetTokenInformation(token, TOKEN_USER_CLASS, buf, need, ctypes.byref(need)):
        raise _winerror("GetTokenInformation")
    # TOKEN_USER { SID_AND_ATTRIBUTES User { PSID Sid; DWORD Attributes } }
    psid = ctypes.cast(buf, ctypes.POINTER(wintypes.LPVOID))[0]
    text = wintypes.LPWSTR()
    if not a.ConvertSidToStringSidW(psid, ctypes.byref(text)):
        raise _winerror("ConvertSidToStringSidW")
    try:
        return str(text.value)
    finally:
        k.LocalFree(ctypes.cast(text, wintypes.HLOCAL))


def current_user_sid() -> str:
    """This process's user, as a string SID (``S-1-5-21-…``)."""
    k, a = _kernel32(), _advapi32()
    token = wintypes.HANDLE()
    if not a.OpenProcessToken(k.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        raise _winerror("OpenProcessToken")
    try:
        return _token_user_sid(token)
    finally:
        k.CloseHandle(token)


def process_user_sid(pid: int) -> str | None:
    """The user another process runs as, or None when Windows won't say
    (it exited, or it belongs to someone this account may not query —
    either way, not this user's)."""
    if not IS_WINDOWS:
        raise NotWindows("this needs Windows")
    k, a = _kernel32(), _advapi32()
    proc = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not proc:
        return None
    try:
        token = wintypes.HANDLE()
        if not a.OpenProcessToken(proc, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            return _token_user_sid(token)
        except OSError:
            return None
        finally:
            k.CloseHandle(token)
    finally:
        k.CloseHandle(proc)


# ─── the control pipe ─────────────────────────────────────────────────────

def control_pipe_name(sid: str | None = None) -> str:
    r"""``\\.\pipe\domovoi-satellite-<SID>``: one per Windows user, so two
    people signed in on one PC never meet on it."""
    return PIPE_STEM + (sid or current_user_sid())


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD),
                ("lpSecurityDescriptor", wintypes.LPVOID),
                ("bInheritHandle", wintypes.BOOL)]


def pipe_sddl(sid: str) -> str:
    """A protected DACL with one entry: full access for ``sid``. No
    Everyone, no Administrators, no SYSTEM — and no inheritance from
    anything. Objects carry the default medium integrity label, so a
    low-integrity or app-container process of the same user can't write to
    the pipe either."""
    if not sid.startswith("S-1-") or any(c not in "S0123456789-" for c in sid):
        raise ValueError(f"not a SID: {sid!r}")
    return f"D:P(A;;GA;;;{sid})"


class PipeSecurity:
    """The security attributes for the control pipe's instances; kept alive
    for as long as instances are being created, then :meth:`close`."""

    def __init__(self, sid: str | None = None) -> None:
        self.sid = sid or current_user_sid()
        self._psd = wintypes.LPVOID()
        if not _advapi32().ConvertStringSecurityDescriptorToSecurityDescriptorW(
                pipe_sddl(self.sid), SDDL_REVISION_1, ctypes.byref(self._psd), None):
            raise _winerror("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        self._sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), self._psd, False)

    @property
    def address(self) -> int:
        return ctypes.addressof(self._sa)

    def close(self) -> None:
        if self._psd:
            _kernel32().LocalFree(ctypes.cast(self._psd, wintypes.HLOCAL))
            self._psd = wintypes.LPVOID()


def create_pipe_instance(name: str, security: PipeSecurity, *, first: bool) -> int:
    """One overlapped, byte-mode instance of the control pipe, as an int
    handle. ``first`` claims the name: if anything already holds a pipe of
    that name (left over, or someone squatting it) this raises rather than
    joining it. Remote clients are refused by the pipe itself."""
    _need_windows()
    import _winapi

    open_mode = PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED
    if first:
        open_mode |= FILE_FLAG_FIRST_PIPE_INSTANCE
    return _winapi.CreateNamedPipe(
        name, open_mode,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        PIPE_UNLIMITED_INSTANCES, PIPE_BUFFER, PIPE_BUFFER, 0, security.address,
    )


def _pipe_pid(handle: int, which: str) -> int | None:
    k = _kernel32()
    pid = wintypes.ULONG(0)
    fn = k.GetNamedPipeClientProcessId if which == "client" else k.GetNamedPipeServerProcessId
    if not fn(wintypes.HANDLE(int(handle)), ctypes.byref(pid)):
        return None
    return int(pid.value) or None


def pipe_client_pid(handle: int) -> int | None:
    """The process at the other end of a server-side pipe instance."""
    return _pipe_pid(handle, "client")


def pipe_server_pid(handle: int) -> int | None:
    """The process that created the pipe a client handle is connected to."""
    return _pipe_pid(handle, "server")


def peer_is_this_user(pid: int | None, own_sid: str | None = None) -> bool:
    """Whether ``pid`` runs as this process's user."""
    if not pid:
        return False
    theirs = process_user_sid(pid)
    return theirs is not None and theirs == (own_sid or current_user_sid())


# ─── named events and a single-instance mutex ─────────────────────────────

def object_name(kind: str, sid: str | None = None) -> str:
    r"""``Local\domovoi-satellite-<kind>-<SID>`` — in the signed-in
    session's namespace, per user."""
    if not kind.replace("-", "").isalnum():
        raise ValueError(kind)
    return f"{LOCAL}domovoi-satellite-{kind}-{sid or current_user_sid()}"


#: The supervisor and the engine stop when this is set (the tray's "Stop
#: satellite", the uninstaller).
STOP_EVENT = "stop"
#: The tray quits when this is set (the uninstaller).
TRAY_QUIT_EVENT = "tray-quit"
SUPERVISOR_MUTEX = "supervisor"
TRAY_MUTEX = "tray"


class NamedEvent:
    """A manual-reset event that other processes of this user can set."""

    def __init__(self, name: str, *, create: bool = True) -> None:
        k = _kernel32()
        self.name = name
        if create:
            self._h = k.CreateEventW(None, True, False, name)
        else:
            self._h = k.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, name)
        if not self._h:
            raise _winerror(f"{'CreateEventW' if create else 'OpenEventW'}({name})")

    @classmethod
    def open_existing(cls, name: str) -> "NamedEvent | None":
        """The event if some process made it, else None (nothing is running)."""
        try:
            return cls(name, create=False)
        except OSError:
            return None

    def set(self) -> None:
        _kernel32().SetEvent(self._h)

    def reset(self) -> None:
        _kernel32().ResetEvent(self._h)

    def wait(self, timeout: float | None = None) -> bool:
        ms = INFINITE if timeout is None else max(0, int(timeout * 1000))
        return _kernel32().WaitForSingleObject(self._h, ms) == WAIT_OBJECT_0

    def is_set(self) -> bool:
        return self.wait(0)

    def close(self) -> None:
        if self._h:
            _kernel32().CloseHandle(self._h)
            self._h = None

    def __del__(self) -> None:  # pragma: no cover — best effort
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


class SingleInstance:
    """Held by the one running supervisor (or tray) of this user: a named
    mutex that a second copy finds already there."""

    def __init__(self, name: str) -> None:
        k = _kernel32()
        self._h = k.CreateMutexW(None, False, name)
        if not self._h:
            raise _winerror(f"CreateMutexW({name})")
        self.already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self._h:
            _kernel32().CloseHandle(self._h)
            self._h = None


# ─── battery ──────────────────────────────────────────────────────────────

class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wintypes.DWORD),
                ("BatteryFullLifeTime", wintypes.DWORD)]


_NO_SYSTEM_BATTERY = 128
_CHARGING = 8
_UNKNOWN = 255


def battery_from_status(ac_line: int, flag: int, percent: int) -> dict[str, Any]:
    """The health sample's ``battery`` for one ``GetSystemPowerStatus``
    answer: ``{"battery": {percent, charging, on_power}}``, or ``{}`` on a
    PC with no battery (a desktop, a mini PC) or one Windows can't read.
    The same shape a Linux laptop and a phone send."""
    if flag == _UNKNOWN and percent == _UNKNOWN:
        return {}
    if flag != _UNKNOWN and flag & _NO_SYSTEM_BATTERY:
        return {}
    if percent > 100:
        return {}
    charging = None if flag == _UNKNOWN else bool(flag & _CHARGING)
    on_power = None if ac_line == _UNKNOWN else ac_line == 1
    return {"battery": {"percent": int(percent), "charging": charging, "on_power": on_power}}


def power_status() -> dict[str, Any]:
    """This PC's battery, from ``GetSystemPowerStatus`` (no privilege)."""
    if not IS_WINDOWS:
        return {}
    status = SYSTEM_POWER_STATUS()
    try:
        if not _kernel32().GetSystemPowerStatus(ctypes.byref(status)):
            return {}
    except OSError:
        return {}
    return battery_from_status(status.ACLineStatus, status.BatteryFlag, status.BatteryLifePercent)


# ─── volume: Windows Core Audio, the default speakers ─────────────────────

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        u = uuid.UUID(text)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        return g


CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
IID_IAudioEndpointVolume = "5CDF2C82-841E-4546-9722-0CF74078229A"
CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0x0
E_RENDER = 0
E_MULTIMEDIA = 1
RPC_E_CHANGED_MODE = 0x80010106 - (1 << 32)

# vtable slots (IUnknown takes 0-2).
_ENUM_GET_DEFAULT_ENDPOINT = 4
_DEVICE_ACTIVATE = 3
_VOL_SET_MASTER_SCALAR = 7
_VOL_GET_MASTER_SCALAR = 9
_VOL_SET_MUTE = 14
_VOL_GET_MUTE = 15
_RELEASE = 2


def _method(obj: ctypes.c_void_p, slot: int, restype, *argtypes):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtable[slot])


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(0, f"{what} failed (HRESULT 0x{hr & 0xFFFFFFFF:08X})")


class EndpointVolume:
    """The default output device's master volume and mute, as the
    taskbar's slider shows them (the scalar 0.0-1.0 is the percentage
    Windows displays). Opens and releases the COM objects on every call,
    on whichever thread calls: the engine asks from worker threads."""

    def _with_endpoint(self, fn):
        _need_windows()
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.CoInitializeEx.argtypes = (wintypes.LPVOID, wintypes.DWORD)
        ole32.CoInitializeEx.restype = ctypes.c_long
        ole32.CoCreateInstance.argtypes = (ctypes.POINTER(GUID), wintypes.LPVOID, wintypes.DWORD,
                                           ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        ole32.CoCreateInstance.restype = ctypes.c_long
        hr = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        initialized = hr >= 0          # S_OK or S_FALSE; RPC_E_CHANGED_MODE: an STA thread, fine
        enum = ctypes.c_void_p()
        device = ctypes.c_void_p()
        volume = ctypes.c_void_p()
        try:
            _check(ole32.CoCreateInstance(
                ctypes.byref(GUID.parse(CLSID_MMDeviceEnumerator)), None, CLSCTX_ALL,
                ctypes.byref(GUID.parse(IID_IMMDeviceEnumerator)), ctypes.byref(enum)), "CoCreateInstance")
            _check(_method(enum, _ENUM_GET_DEFAULT_ENDPOINT, ctypes.c_long, ctypes.c_int, ctypes.c_int,
                           ctypes.POINTER(ctypes.c_void_p))(enum, E_RENDER, E_MULTIMEDIA, ctypes.byref(device)),
                   "GetDefaultAudioEndpoint")
            _check(_method(device, _DEVICE_ACTIVATE, ctypes.c_long, ctypes.POINTER(GUID), wintypes.DWORD,
                           wintypes.LPVOID, ctypes.POINTER(ctypes.c_void_p))(
                device, ctypes.byref(GUID.parse(IID_IAudioEndpointVolume)), CLSCTX_ALL, None,
                ctypes.byref(volume)), "IMMDevice::Activate")
            return fn(volume)
        finally:
            for obj in (volume, device, enum):
                if obj:
                    _method(obj, _RELEASE, wintypes.ULONG)(obj)
            if initialized:
                ole32.CoUninitialize()

    def get_scalar(self) -> float:
        def read(vol):
            level = ctypes.c_float()
            _check(_method(vol, _VOL_GET_MASTER_SCALAR, ctypes.c_long, ctypes.POINTER(ctypes.c_float))(
                vol, ctypes.byref(level)), "GetMasterVolumeLevelScalar")
            return float(level.value)
        return self._with_endpoint(read)

    def get_mute(self) -> bool:
        def read(vol):
            muted = wintypes.BOOL()
            _check(_method(vol, _VOL_GET_MUTE, ctypes.c_long, ctypes.POINTER(wintypes.BOOL))(
                vol, ctypes.byref(muted)), "GetMute")
            return bool(muted.value)
        return self._with_endpoint(read)

    def set_scalar(self, level: float, *, unmute: bool = True) -> None:
        level = max(0.0, min(1.0, float(level)))

        def write(vol):
            _check(_method(vol, _VOL_SET_MASTER_SCALAR, ctypes.c_long, ctypes.c_float, wintypes.LPVOID)(
                vol, level, None), "SetMasterVolumeLevelScalar")
            if unmute:
                _check(_method(vol, _VOL_SET_MUTE, ctypes.c_long, wintypes.BOOL, wintypes.LPVOID)(
                    vol, False, None), "SetMute")
        self._with_endpoint(write)
