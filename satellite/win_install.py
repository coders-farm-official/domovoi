"""The Windows satellite's installer, after the bootstrap: everything with a
rule in it, in Python where it is tested.

Spare-device satellites on Windows (2026-10-13). The dashboard's one-line
PowerShell command checks the satellite bundle's SHA-256 and runs
``satellite/scripts/install-windows.ps1``, which refuses a PC that runs the
Domovoi server, fetches the embeddable Python the lock pins
(``satellite/windows-runtime.json``, inside that same bundle), checks it,
and runs this module with it:

    python -m satellite.win_install install --payload 'domovoi-room:1?…' \
        --bundle <dir holding satellite\\> --staged-python <dir> --root <install folder>

In order, and nothing written before step 2 has passed:

1. read the enrolment payload (``satellite/enrolment.py``: the core on the
   home network, its fingerprint, the room);
2. make the core prove it holds that fingerprint — the address-bound
   ``/v1/health`` challenge every satellite runs — or stop (exit 3);
3. ask what the server may do on its own: "automatic" or "ask me";
4. stop a satellite already installed in that folder (only now, after the
   proof), and install, per user, into
   ``%LOCALAPPDATA%\\Domovoi\\Satellite`` — or another empty folder inside
   the account's own folder, never through a link, with a DACL of its own
   (this user, SYSTEM, Administrators; nothing inherited): the
   Python (``python\\``), every wheel the lock names — from the core's cache,
   or from python.org / PyPI when the core hasn't fetched them, each held to
   its SHA-256 and size either way — installed by pip with
   ``--require-hashes --no-index --no-deps``; the satellite code
   (``app\\satellite``); the wake-word models (checked against their pins);
   the config with the pinned core (``.domovoi\\config.toml``); the
   uninstaller (``uninstall.ps1``, a copy taken now from the verified
   bundle, outside the tree code updates rewrite);
5. register it for this user only: a value under ``HKCU\\…\\Run`` (starts it
   at sign-in; Settings > Apps > Startup can turn it off), an entry in
   Installed apps (``HKCU\\…\\Uninstall``), and "Domovoi Satellite" in the
   Start menu — no administrator rights, nothing for other accounts;
6. ask, one at a time, before any power change (``powercfg``, plugged in
   only), each defaulting to No, and record what was there before;
7. start it, and wait for the approval code to be typed into the
   dashboard, showing the code here (never in a log).

Exit status: 0 done; 2 a usage error or a refused payload; 3 the core did
not prove its identity; 4 not approved before ``--wait`` ran out (it keeps
waiting in the background); 1 anything else. Stdlib only: it runs on the
embeddable Python before any wheel is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from satellite import enrolment

APP_KEY = "DomovoiSatellite"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall" + "\\" + APP_KEY
SHORTCUT_NAME = "Domovoi Satellite.lnk"
STATE_FILE = "install-windows.json"
ROOT_MARKER = ".domovoi-satellite-root"
LOCK_NAME = "windows-runtime.json"
PUBLISHER = "Coders Farm"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
#: The plugged-in power settings the installer may change, by powercfg alias.
POWER_SETTINGS = {
    "standby": ("SUB_SLEEP", "STANDBYIDLE", "Never sleep while plugged in (the screen can still turn off)?"),
    "hibernate": ("SUB_SLEEP", "HIBERNATEIDLE", None),     # goes with "standby"
    "lid": ("SUB_BUTTONS", "LIDACTION", "Keep running with the lid closed while plugged in?"),
}
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX_RE = re.compile(r"0x([0-9a-fA-F]{1,8})")


class InstallError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def say(line: str = "") -> None:
    print(line, flush=True)


# ─── the lock and pinned downloads ────────────────────────────────────────

def load_lock(bundle: Path) -> dict[str, Any]:
    """The lock from the bundle, shape-checked like the core checks it."""
    path = bundle / "satellite" / LOCK_NAME
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InstallError(f"the bundle's {LOCK_NAME} is unreadable: {e}") from e
    if not isinstance(doc, dict) or doc.get("lock_version") != 1:
        raise InstallError(f"{LOCK_NAME} is not version 1")
    for entry in lock_entries(doc):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,200}\.(whl|zip)", str(entry.get("file"))):
            raise InstallError(f"bad file name in {LOCK_NAME}: {entry.get('file')!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256"))):
            raise InstallError(f"{entry.get('file')}: bad sha256 in {LOCK_NAME}")
        if not str(entry.get("url", "")).startswith(("https://www.python.org/ftp/python/",
                                                      "https://files.pythonhosted.org/packages/")):
            raise InstallError(f"{entry.get('file')}: not a python.org or PyPI URL")
    return doc


VC_RUNTIME_FILES = ("msvcp140.dll", "msvcp140_1.dll")


def vc_runtime_dlls(lock: dict[str, Any]) -> list[dict[str, Any]]:
    """The lock's Microsoft C++ runtime DLLs (``vc_runtime.dlls``): each
    one of the two onnxruntime needs, a member of the pinned msvc-runtime
    wheel, with its own SHA-256 and size."""
    vc = lock.get("vc_runtime")
    if not isinstance(vc, dict):
        raise InstallError(f"{LOCK_NAME} pins no Microsoft C++ runtime (vc_runtime)")
    dlls = vc.get("dlls")
    prefix = f"{str(vc.get('name', '')).replace('-', '_')}-{vc.get('version')}.data/data/"
    if not isinstance(dlls, list) or sorted(d.get("file") for d in dlls if isinstance(d, dict)) != \
            sorted(VC_RUNTIME_FILES):
        raise InstallError(f"{LOCK_NAME}: vc_runtime must name {', '.join(VC_RUNTIME_FILES)}")
    for d in dlls:
        ok = (d.get("member") == prefix + d["file"]
              and re.fullmatch(r"[0-9a-f]{64}", str(d.get("sha256")))
              and isinstance(d.get("size"), int) and 0 < d["size"] < 10_000_000)
        if not ok:
            raise InstallError(f"{LOCK_NAME}: a vc_runtime DLL is malformed: {d!r}"[:300])
    return dlls


def place_vc_runtime(root: Path, lock: dict[str, Any], wheels_dir: Path) -> None:
    """Microsoft's C++ runtime DLLs beside the satellite's python.exe (the
    wake word's onnxruntime imports msvcp140.dll and msvcp140_1.dll, 14.44+,
    and carries neither; Windows loads the copy beside python.exe before
    System32's). Each is read from the pinned msvc-runtime wheel (already
    fetched and checked) and written only if it is the pinned file."""
    import hashlib
    import zipfile

    wheel = wheels_dir / lock["vc_runtime"]["file"]
    with zipfile.ZipFile(wheel) as z:
        for d in vc_runtime_dlls(lock):
            body = z.read(d["member"])
            if len(body) != d["size"] or hashlib.sha256(body).hexdigest() != d["sha256"]:
                raise InstallError(f"{d['file']} in {wheel.name} is not the pinned file; nothing placed")
            dest = root / "python" / d["file"]
            part = dest.with_name(dest.name + ".part")
            part.write_bytes(body)
            os.replace(part, dest)
    say("  Microsoft's C++ runtime (" + ", ".join(f"{d['file']} {d.get('version', '')}".strip()
                                            for d in vc_runtime_dlls(lock)) + ") beside the satellite's Python")


def lock_entries(lock: dict[str, Any]) -> list[dict[str, Any]]:
    """Every file the lock names, as the core lists them
    (domovoi/satellite_windows.py entries): the Python, pip, the wheels,
    and the wheel the C++ runtime DLLs come from."""
    out = [lock.get("python") or {}, lock.get("pip") or {}, *(lock.get("wheels") or [])]
    if isinstance(lock.get("vc_runtime"), dict):
        out.append(lock["vc_runtime"])
    return out


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, size: int, opener: Callable[..., Any], timeout: float) -> None:
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        with opener(url, timeout=timeout) as r, open(part, "wb") as f:
            if getattr(r, "status", 200) != 200:
                raise OSError(f"HTTP {getattr(r, 'status', '?')}")
            while chunk := r.read(1 << 20):
                written += len(chunk)
                if written > size:
                    raise OSError(f"larger than the pinned {size} bytes")
                f.write(chunk)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


def fetch_pinned(entry: dict[str, Any], dest_dir: Path, *, core: str | None,
                 opener: Callable[..., Any] | None = None, timeout: float = 600.0,
                 log: Callable[[str], None] = say) -> str:
    """Put the lock entry's file in ``dest_dir``: the copy there if it
    already matches, else the core's cached copy, else upstream's — each
    kept only when its size and SHA-256 are the lock's. Returns where it
    came from ("present", "core", "upstream"); raises InstallError."""
    opener = opener or urllib.request.urlopen
    name, sha, size = entry["file"], entry["sha256"], int(entry["size"])
    dest = dest_dir / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size == size and sha256_file(dest) == sha:
        return "present"
    sources = []
    if core:
        sources.append(("core", f"{core.rstrip('/')}/v1/satellite-windows/files/{name}"))
    sources.append(("upstream", entry["url"]))
    problems = []
    for where, url in sources:
        try:
            _download(url, dest, size, opener, timeout)
        except (OSError, urllib.error.URLError, ValueError) as e:
            problems.append(f"{where}: {e}")
            continue
        if dest.stat().st_size == size and sha256_file(dest) == sha:
            return where
        dest.unlink(missing_ok=True)
        problems.append(f"{where}: not the pinned file")
    raise InstallError(f"could not get {name} ({'; '.join(problems)})")


# ─── the install folder ───────────────────────────────────────────────────

def pth_name(python_dir: Path) -> str:
    found = sorted(python_dir.glob("python3*._pth"))
    if not found:
        raise InstallError(f"no ._pth file in {python_dir}: not an embeddable Python")
    return found[0].name


def write_pth(python_dir: Path, extra: list[str]) -> None:
    """The embeddable Python's path file: its own zip, its folder, ``extra``
    (relative to the Python folder) and its own ``Lib\\site-packages`` —
    and no ``import site``: with it, Python would also load this account's
    user site-packages (``%APPDATA%\\Python\\Python314\\site-packages``),
    its ``.pth`` files and ``usercustomize``, into every satellite process
    (security review of wf/sat-windows). None of the pinned wheels ships a
    ``.pth`` file, so nothing needs ``site``. UTF-8 without a BOM, which is
    how Python reads a ``._pth`` (a BOM breaks its first line)."""
    zipname = pth_name(python_dir).replace("._pth", ".zip")
    lines = [zipname, ".", *extra, "Lib\\site-packages"]
    (python_dir / pth_name(python_dir)).write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))


def replace_dir(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def site_packages(root: Path) -> Path:
    return root / "python" / "Lib" / "site-packages"


def pip_install(root: Path, wheels_dir: Path, lock: dict[str, Any],
                run: Callable[..., Any] = subprocess.run) -> None:
    """Every wheel in the lock, by its hash, from ``wheels_dir`` only."""
    reqs = wheels_dir / "requirements-locked.txt"
    lines = [f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}" for w in lock["wheels"]]
    reqs.write_text("\n".join(lines) + "\n", encoding="utf-8")
    pip_wheel = wheels_dir / lock["pip"]["file"]
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from pip._internal.cli.main import main; sys.exit(main(sys.argv[2:]))")
    argv = [str(root / "python" / "python.exe"), "-c", code, str(pip_wheel), "install",
            "--no-index", "--find-links", str(wheels_dir), "--require-hashes", "--no-deps",
            "--only-binary=:all:", "--disable-pip-version-check", "--no-warn-script-location",
            "--no-cache-dir", "--quiet", "-r", str(reqs)]
    # Nothing from this person's PATH (a compiler, a toolchain, another
    # Python) takes part: pip installs pinned wheels and runs nothing else.
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PATH", "PYTHONPATH", "PIP_CONFIG_FILE")}
    env["PATH"] = os.pathsep.join([str(root / "python"),
                                   str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")])
    env["PIP_CONFIG_FILE"] = os.devnull
    r = run(argv, capture_output=True, text=True, timeout=1800, creationflags=CREATE_NO_WINDOW, env=env)
    if r.returncode != 0:
        raise InstallError(f"pip could not install the satellite's libraries: {(r.stderr or r.stdout)[-800:]}")
    for rel in lock.get("remove_after_install") or []:
        target = site_packages(root) / rel
        if ".." not in Path(rel).parts:
            target.unlink(missing_ok=True)


# ─── registry, Start menu ─────────────────────────────────────────────────

class Registry:
    """The few HKCU values the installer writes. ``stub`` (tests): a JSON
    file stands in for the registry and the Start-menu shortcut, so a test
    on a real account writes nothing there."""

    def __init__(self, stub: Path | None = None) -> None:
        self.stub = stub

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.stub.read_text(encoding="utf-8-sig")) if self.stub else {}
        except (OSError, ValueError):
            return {}

    def _save(self, doc: dict[str, Any]) -> None:
        assert self.stub is not None
        self.stub.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    def set_run(self, command: str) -> None:
        if self.stub:
            doc = self._load()
            doc.setdefault("run", {})[APP_KEY] = command
            return self._save(doc)
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, APP_KEY, 0, winreg.REG_SZ, command)

    def set_uninstall(self, values: dict[str, Any]) -> None:
        if self.stub:
            doc = self._load()
            doc.setdefault("uninstall", {})[APP_KEY] = values
            return self._save(doc)
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_SET_VALUE) as k:
            for name, value in values.items():
                kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                winreg.SetValueEx(k, name, 0, kind, value)

    def set_shortcut(self, lnk: Path, target: str, args: str, workdir: str, icon: str) -> None:
        if self.stub:
            doc = self._load()
            doc["shortcut"] = {"path": str(lnk), "target": target, "args": args, "workdir": workdir}
            return self._save(doc)
        q = ps_quote
        script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(lnk)}); "
                  f"$s.TargetPath={q(target)}; $s.Arguments={q(args)}; $s.WorkingDirectory={q(workdir)}; "
                  f"$s.IconLocation={q(icon)}; $s.Description='Domovoi room satellite'; $s.Save()")
        lnk.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, timeout=60, check=True, creationflags=CREATE_NO_WINDOW)


#: Every character Windows PowerShell takes for a single quote: the ASCII
#: one and the typographic ones (U+2018 to U+201B). An account named
#: O’Brien, or a room typed on a phone, has the second kind.
PS_SINGLE_QUOTES = "'\u2018\u2019\u201a\u201b"


def ps_quote(value: Any) -> str:
    """A PowerShell single-quoted string literal of ``value``: every
    character PowerShell reads as a single quote is doubled (one of a pair
    is a literal), so no value can end the string early."""
    return "'" + "".join(c + c if c in PS_SINGLE_QUOTES else c for c in str(value)) + "'"


def powershell() -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
               / "powershell.exe")


def start_menu_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / \
        "Microsoft" / "Windows" / "Start Menu" / "Programs"


def folder_size_kb(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total // 1024


def register(root: Path, room: str, version: str, reg: Registry) -> None:
    pythonw = root / "python" / "pythonw.exe"
    command = f'"{pythonw}" -m satellite.win_service'
    reg.set_run(command)
    uninstall = (f'"{powershell()}" -NoProfile -ExecutionPolicy Bypass -File "{root / "uninstall.ps1"}"')
    reg.set_uninstall({
        "DisplayName": f"Domovoi Satellite ({room})"[:120],
        "DisplayVersion": version,
        "Publisher": PUBLISHER,
        "InstallLocation": str(root),
        "DisplayIcon": str(pythonw),
        "UninstallString": uninstall,
        "QuietUninstallString": uninstall + " -Quiet",
        "NoModify": 1,
        "NoRepair": 1,
        "EstimatedSize": folder_size_kb(root),
    })
    reg.set_shortcut(start_menu_dir() / SHORTCUT_NAME, str(pythonw), "-m satellite.win_service",
                     str(root / "app"), str(pythonw))


# ─── power (asked, plugged in only, default No) ───────────────────────────

def powercfg_exe() -> str:
    """Windows' own powercfg, by its full path: a bare name is looked for in
    the current folder and on PATH first."""
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "powercfg.exe")


def _powercfg(run: Callable[..., Any], *args: str) -> str:
    r = run([powercfg_exe(), *args], capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout or "powercfg failed").strip()[:200])
    return r.stdout or ""


def active_scheme(run: Callable[..., Any]) -> str:
    m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
                  _powercfg(run, "/getactivescheme"))
    if not m:
        raise OSError("powercfg did not name the active power plan")
    return m.group(1).lower()


def current_ac(run: Callable[..., Any], scheme: str, sub: str, setting: str) -> int:
    """The plugged-in value: of the two current values powercfg prints (AC
    then DC, in hex, whatever the display language), the first."""
    values = _HEX_RE.findall(_powercfg(run, "/query", scheme, sub, setting))
    if len(values) < 2:
        raise OSError(f"powercfg did not show {setting}")
    return int(values[-2], 16)


def change_power(answer: Callable[[str], bool], run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Ask about each change, make the ones agreed to, and return what was
    there before (for the uninstaller), ``{}`` when nothing changed."""
    wanted = [k for k in ("standby", "lid") if answer(POWER_SETTINGS[k][2])]
    if not wanted:
        return {}
    try:
        scheme = active_scheme(run)
    except OSError as e:
        say(f"  power settings left alone: {e}")
        return {}
    changed: dict[str, Any] = {"scheme": scheme}
    keys = ["standby", "hibernate"] if "standby" in wanted else []
    keys += ["lid"] if "lid" in wanted else []
    for key in keys:
        sub, setting, _q = POWER_SETTINGS[key]
        try:
            before = current_ac(run, scheme, sub, setting)
            _powercfg(run, "/setacvalueindex", scheme, sub, setting, "0")
        except OSError as e:
            say(f"  could not change {setting} ({e}); a standard account may not be allowed to")
            continue
        changed[key] = before
    try:
        _powercfg(run, "/setactive", scheme)
    except OSError:
        pass
    return changed if len(changed) > 1 else {}


# ─── the state record, start, wait ────────────────────────────────────────

def read_state(root: Path) -> dict[str, Any]:
    try:
        doc = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def merge_power(old: Any, new: dict[str, Any]) -> dict[str, Any]:
    """What the uninstaller puts back: the value from BEFORE the first
    install that changed a setting. Installing again over an install that
    already changed it records "0" (its own value) as "before"; that must
    not replace the person's original."""
    if not isinstance(old, dict) or old.get("scheme") != new.get("scheme"):
        return new
    merged = dict(new)
    for key in POWER_SETTINGS:
        if isinstance(old.get(key), int):
            merged[key] = old[key]
    return merged


def write_state(root: Path, **values: Any) -> None:
    path = root / STATE_FILE
    doc = read_state(root)
    doc.update(values)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")


def start(root: Path, *, test_hooks: bool = False) -> None:
    """The supervisor (and through it the tray and the engine), detached.
    ``test_hooks`` (tests only): its --test-audio-hooks."""
    subprocess.Popen(
        [str(root / "python" / "pythonw.exe"), "-m", "satellite.win_service",
         *(["--test-audio-hooks"] if test_hooks else [])],
        cwd=str(root / "app"), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )


def ask_choice(prompt: str, interactive: bool) -> bool:
    if not interactive:
        return False
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# ─── install ──────────────────────────────────────────────────────────────

def _is_link(path: Path) -> bool:
    """A symlink or a junction (``Path.is_symlink`` misses junctions)."""
    if sys.platform == "win32":
        from satellite import win32

        return win32.is_reparse_point(str(path))
    return path.is_symlink()


def check_install_root(root: Path, profile: Path | None = None) -> None:
    """Refuse a folder outside the account's own folder (``profile``: one
    elsewhere, under ``C:\\`` say, inherits "Authenticated Users: Modify",
    and a folder above it that others may rename can be swapped), one
    reached through a link or junction, one that already holds something
    other than an earlier install of this, and one where a Domovoi server
    keeps its files. The folder itself then gets a DACL of its own
    (:func:`protect_root`)."""
    from satellite import pc_engine

    shared = pc_engine.core_account(root / ".domovoi")
    if shared:
        raise InstallError(shared, 2)
    root = Path(os.path.abspath(root))
    if profile is not None:
        home = Path(os.path.abspath(profile))
        if root == home or not root.is_relative_to(home):
            raise InstallError(f"{root} is not inside your own user folder ({home}); the satellite installs "
                               "only there, where no other account can change it", 2)
        chain = [root, *[a for a in root.parents if a != home and a.is_relative_to(home)]]
    else:
        chain = [root]
    for folder in chain:
        if _is_link(folder):
            raise InstallError(f"{folder} is a link or junction; not installing through it", 2)
    if root.is_dir() and any(root.iterdir()) and not (root / ROOT_MARKER).is_file():
        raise InstallError(f"{root} is not empty and is not an earlier Domovoi Satellite install; "
                           "choose an empty folder inside your user folder (-InstallRoot)", 2)


def protect_root(root: Path) -> None:
    """The install folder belongs to this user (refused otherwise: its owner
    could change its permissions back), and its DACL is this user, SYSTEM
    and Administrators, inherited by everything inside, nothing from above."""
    if sys.platform != "win32":
        return
    from satellite import win32

    owner, me = win32.path_owner_sid(str(root)), win32.current_user_sid()
    if owner != me:
        raise InstallError(f"{root} belongs to another account ({owner}); choose a folder of your own", 2)
    win32.protect_folder(str(root), me)


STOP_WAIT_SEC = 20.0


def stop_running(root: Path) -> None:
    """Stop a satellite installed in ``root`` (its supervisor, engine and
    tray, through this user's stop events), then end whatever of it is
    left after :data:`STOP_WAIT_SEC`. Called only after the core has proven
    its fingerprint: a refused install leaves the running one alone."""
    if sys.platform != "win32" or not (root / "python").is_dir():
        return
    from satellite import win32

    say("Stopping the satellite that is installed here, to install it again...")
    for kind in (win32.STOP_EVENT, win32.TRAY_QUIT_EVENT):
        event = win32.NamedEvent.open_existing(win32.object_name(kind))
        if event is not None:
            event.set()
            event.close()
    deadline = time.monotonic() + STOP_WAIT_SEC
    while True:
        left = win32.processes_under(str(root / "python"))
        if not left:
            return
        if time.monotonic() > deadline:
            for pid in left:
                win32.terminate(pid)
            return
        time.sleep(0.5)


def install(args: argparse.Namespace, *, run: Callable[..., Any] = subprocess.run,
            opener: Callable[..., Any] | None = None) -> int:
    bundle, staged, root = Path(args.bundle), Path(args.staged_python), Path(args.root)
    interactive = sys.stdin.isatty() and not args.yes

    # 1-2. The payload, and the core proves who it is. Nothing written yet.
    try:
        e = enrolment.parse(args.payload)
    except enrolment.EnrolmentError as err:
        raise InstallError(f"that is not a usable enrolment code: {err}", 2) from err
    say("Checking the Domovoi server named in the code...")
    from satellite import server_identity

    try:
        proven = enrolment.verify(e, opener=opener)
    except server_identity.IdentityError as err:
        raise InstallError(f"the server at that address did not prove it is the one in the code, "
                           f"so nothing was installed: {err}", 3) from err
    say(f"  {e.core} proved it is {proven}")
    say(f"  this PC will be the room \"{e.room}\" ({e.room_id})")

    # 3. What the server may do on its own.
    permissions = args.permissions
    if not permissions:
        if interactive:
            say("")
            say("May the Domovoi server update and restart this satellite on its own?")
            say("  1) automatic - it installs signed satellite updates, applies settings from the")
            say("                 dashboard and restarts the satellite when it needs to, like a Pi")
            say("  2) ask me    - each of those waits for your OK in the tray (or the command line)")
            say("Either way it never needs administrator rights. Change it later in")
            say(f"{root / '.domovoi' / 'config.toml'} ([pc] permissions).")
            try:
                choice = input("Choose 1 or 2 [2]: ").strip()
            except EOFError:
                choice = "2"
            permissions = "automatic" if choice in ("1", "automatic") else "ask"
        elif args.yes:
            raise InstallError("--yes needs --permissions automatic or --permissions ask", 2)
        else:
            permissions = "ask"

    lock = load_lock(bundle)
    say(f"Installing for {os.environ.get('USERNAME', 'this account')} into {root} (updates: {permissions})")

    # 4. Files — only into an empty folder or an earlier install of this
    # (the uninstaller removes what it recognises, nothing else), and never
    # beside a Domovoi server's own files (security review of wf/sat-pc, S2).
    check_install_root(root, Path(args.profile) if getattr(args, "profile", None) else None)
    stop_running(root)
    root.mkdir(parents=True, exist_ok=True)
    protect_root(root)
    (root / ROOT_MARKER).write_text("Domovoi Satellite install folder\n", encoding="utf-8")
    replace_dir(staged, root / "python")
    write_pth(root / "python", ["..\\app"])
    (root / "app").mkdir(exist_ok=True)
    replace_dir(bundle / "satellite", root / "app" / "satellite")
    shutil.copyfile(bundle / "satellite" / "scripts" / "uninstall-windows.ps1", root / "uninstall.ps1")

    say("Getting the satellite's libraries (about 95 MB, each checked against its pinned SHA-256)...")
    wheels_dir = root / "cache" / "wheels"
    sources: dict[str, int] = {}
    vc_runtime_dlls(lock)     # the lock pins the runtime properly before anything is fetched
    for entry in [lock["pip"], *lock["wheels"], lock["vc_runtime"]]:
        where = fetch_pinned(entry, wheels_dir, core=e.core, opener=opener)
        sources[where] = sources.get(where, 0) + 1
    say("  " + ", ".join(f"{n} from {w}" for w, n in sorted(sources.items())))
    place_vc_runtime(root, lock, wheels_dir)
    pip_install(root, wheels_dir, lock, run=run)
    shutil.rmtree(root / "cache", ignore_errors=True)

    say("Fetching the wake-word models (checked against their pinned SHA-256)...")
    from satellite import pc_install

    models = site_packages(root) / "openwakeword" / "resources" / "models"
    try:
        pc_install.fetch_models(models, core=e.core, fp=e.fp, opener=opener,
                                log=lambda line: say(line))
    except OSError as err:
        raise InstallError(f"the wake-word models could not be fetched: {err}") from err

    report = pc_install.write_config(root / ".domovoi" / "config.toml",
                                     root / "app" / "satellite" / "config.toml.example",
                                     e, permissions, profile="windows_pc")
    if report.get("backup"):
        say(f"  your previous config is kept as {report['backup']}")
    say(f"  wrote {root / '.domovoi' / 'config.toml'} (pinned to {e.fp})")
    write_state(root, installed_at=int(time.time()), room_id=e.room_id, permissions=permissions,
                python=lock["python"]["version"])

    # 5. This user's sign-in, Installed apps, Start menu.
    reg = Registry(Path(args.registry_file) if args.registry_file else None)
    register(root, e.room, lock["python"]["version"], reg)
    say("  it starts when you sign in (Settings > Apps > Startup lists it), and is in "
        "Installed apps and the Start menu as Domovoi Satellite")

    # 6. Power: asked, plugged in only, default No.
    if args.power == "no":
        answer = lambda _q: False  # noqa: E731
    elif args.power == "yes":
        answer = lambda _q: True  # noqa: E731
    elif args.power in ("standby", "lid"):
        # The Setup's two tick boxes (DomovoiSatelliteSetup.iss): this one only.
        wanted_q = POWER_SETTINGS[args.power][2]
        answer = lambda q: q == wanted_q  # noqa: E731
    else:
        answer = lambda q: ask_choice(q, interactive)  # noqa: E731
    power = change_power(answer, run=run)
    if power:
        write_state(root, power=merge_power(read_state(root).get("power"), power))

    say("")
    say(f"Installed. This PC is the room \"{e.room}\" ({e.room_id}). It runs while you are signed in.")
    py = root / "python" / "python.exe"
    say(f"  status:    \"{py}\" -m satellite.client --status")
    say(f"  log:       {root / 'logs' / 'satellite.log'}")
    say("  uninstall: Settings > Apps > Installed apps > Domovoi Satellite")
    if args.no_start:
        return 0
    start(root, test_hooks=bool(getattr(args, "test_audio_hooks", False)))
    if args.wait <= 0:
        return 0
    say("")
    say("Now approve it: in the dashboard, Settings -> Satellites, type the code shown below.")
    from satellite import control, pc_engine

    # This process is the staged Python in %TEMP%; the engine runs the
    # install's own (the pipe's client accepts only a server from that
    # folder: control_win.PipeStream).
    final = pc_engine.wait_for_approval(control.default_address(root / ".domovoi"), timeout=args.wait,
                                        image_dir=str(root / "python"))
    if final == "connected":
        return 0
    say("Not approved yet. It keeps waiting in the background; the code is also in the tray,")
    say(f"or: \"{py}\" -m satellite.client --approval-code")
    return 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m satellite.win_install")
    sub = parser.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--payload", required=True)
    i.add_argument("--bundle", required=True, help="the folder holding the verified bundle's satellite\\")
    i.add_argument("--staged-python", required=True, help="the checked embeddable Python, unpacked")
    i.add_argument("--root", required=True, help="the install folder")
    i.add_argument("--permissions", choices=("automatic", "ask"), default=None)
    i.add_argument("--power", choices=("ask", "no", "yes", "standby", "lid"), default="ask")
    i.add_argument("--yes", action="store_true")
    i.add_argument("--no-start", action="store_true")
    i.add_argument("--wait", type=int, default=900)
    i.add_argument("--test-audio-hooks", action="store_true",
                   help="TESTS ONLY: start the satellite with its supervisor's --test-audio-hooks")
    i.add_argument("--registry-file", default=None,
                   help="TESTS ONLY: write the Run/Uninstall values and the shortcut to this JSON file")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    # The account's own folder, before ~ is pointed elsewhere: the install
    # folder must be inside it.
    args.profile = os.environ.get("USERPROFILE") or str(Path.home())
    # Whatever a satellite module resolves as ~ is the install folder, never
    # this person's own profile (a Domovoi server keeps its data there).
    os.environ["USERPROFILE"] = str(Path(args.root))
    try:
        return install(args)
    except InstallError as e:
        print(f"install: {e}", file=sys.stderr, flush=True)
        return e.code
    except KeyboardInterrupt:
        print("install: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
