"""Root's own copy of the satellite code, and the switch that gives a card one.

Security review 2026-11-23, M1: ``domovoi-provisioning.service`` ran
``python -m satellite.provisioning_mode`` as root at every boot from the
service account's venv and ``~/domovoi`` tree, which that account can
write; and sudoers let the account run ``/opt/xvf3800/xvf_host`` with any
arguments. Either was a way from the account to root, so no card could
offer a support session. This module closes both, on new cards and on
cards already in the house.

**The layout.**

* ``/usr/local/lib/domovoi/root-code`` is a link to one release under
  ``/usr/local/lib/domovoi/root-code.d/<name>/satellite/…``: root's copy of
  the code, root:root, writable by nobody else. The provisioning unit runs
  ``/usr/bin/python3 -E -s -m satellite.root_entry`` with that link as its
  working directory, and names the account (``DOMOVOI_SATELLITE_USER``),
  never a path in its home. The account's own synced tree stays where it
  is, for the client.
* ``/usr/local/sbin/domovoi-xvf`` is satellite/xvf_guard.py, root's; the
  sudoers line names it instead of ``xvf_host``. No rule runs ``ALL``.
* The installed setup indicator (``/usr/local/sbin/domovoi-status``) tries
  the wrapper before the tool, so the client still lights the ring.

**Updates.** A code sync is still done by the client, as the account
(satellite/code_sync.py), and it keeps the signed manifest it accepted
(``~/.domovoi/code-manifest.sig.json``). At every boot, before the
provisioning code runs, root (satellite/root_entry.py, running root's copy)
checks that envelope itself: the server's signature against the
root-owned pin (``/etc/domovoi/server-identity.json``), and its serial
against root's own record (``/var/lib/domovoi/root-code-serial.json``) so
an older list can't roll it back. Only then does it read each listed file
out of the account's tree, keep it only when its SHA-256 is the signed one,
and write the bytes it checked into a new release. Nothing the account
wrote is ever run by root unverified. A card without the root-owned pin
can't verify anything, so its copy stays as installed.

**The switch** (:func:`switch`), for a card prepared before this: the
provisioning unit there still runs the account's tree as root, so that
run makes the switch once, at a boot after the update brought this file:
the release is written and import-checked with the system Python, the link
pointed at it, the wrapper installed, the unit and sudoers replaced
(sudoers through ``visudo -cf`` first), then ``systemctl daemon-reload``.
Any failure undoes every step already done, in reverse, so the card is
left as it was: working, and trying again at the next boot. Running it
again on a switched card changes nothing.

Standard library only: the system Python runs it as root.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("satellite.root_code")

LIB_DIR = Path("/usr/local/lib/domovoi")
SYSTEM_PYTHON = "/usr/bin/python3"
ENTRY_MODULE = "satellite.root_entry"
#: The account the provisioning unit works for, by name (the unit names no
#: path in the account's home: the support-session check would refuse it).
USER_ENV = "DOMOVOI_SATELLITE_USER"
#: The template, inside the code tree (relative to ``satellite/``).
UNIT_TEMPLATE = "scripts/domovoi-provisioning.service"
#: The wrapper's source, inside the code tree.
WRAPPER_SOURCE = "xvf_guard.py"
WRAPPER_PATH = "/usr/local/sbin/domovoi-xvf"
#: The client keeps the code channel's signed manifest here, beside
#: config.toml (satellite/code_sync.py writes it).
ENVELOPE_NAME = "code-manifest.sig.json"
#: What a release may hold: the code channel's own allowlist.
CODE_EXT = frozenset({".py", ".toml", ".txt", ".md", ".service", ".sh", ".ps1", ".json",
                      ".example"})
MAX_FILE = 8 * 1024 * 1024
_REL_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.\-]*)*\Z")
_USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_SMOKE = "import satellite.root_entry, satellite.provisioning_mode, satellite.xvf_guard"


class RootCodeError(RuntimeError):
    """The switch or a refresh could not be made; nothing was left half-done."""


@dataclass
class Layout:
    """Every path the switch touches (tests point them at a temp folder)."""

    lib_dir: Path = LIB_DIR
    unit: Path = Path("/etc/systemd/system/domovoi-provisioning.service")
    sudoers: Path = Path("/etc/sudoers.d/domovoi-satellite")
    wrapper: Path = Path(WRAPPER_PATH)
    state_dir: Path = Path("/var/lib/domovoi")
    status: Path = Path("/usr/local/sbin/domovoi-status")

    @property
    def root_code(self) -> Path:
        return self.lib_dir / "root-code"

    @property
    def releases(self) -> Path:
        return self.lib_dir / "root-code.d"

    @property
    def serial_file(self) -> Path:
        return self.state_dir / "root-code-serial.json"

    @property
    def record(self) -> Path:
        return self.state_dir / "root-code.json"


def _chown_root(path: Path) -> None:
    if hasattr(os, "chown") and hasattr(os, "geteuid") and os.geteuid() == 0:
        os.chown(path, 0, 0, follow_symlinks=False)


def _link(target: Path, link: Path) -> None:
    """Point ``link`` at ``target`` in one step (a new link renamed over
    the old one), so a power cut leaves the old release or the new one."""
    tmp = link.with_name(link.name + ".new")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target, tmp, target_is_directory=True)
    os.replace(tmp, link)


def _readlink(link: Path) -> Path | None:
    try:
        return Path(os.readlink(link))
    except OSError:
        return None


def _unlink(link: Path) -> None:
    try:
        link.unlink()
    except FileNotFoundError:
        pass


@dataclass
class Ops:
    """What touches the system beyond plain files: the commands, ownership
    and the release link (tests use fakes)."""

    run: Callable[..., Any] = subprocess.run
    chown_root: Callable[[Path], None] = _chown_root
    link: Callable[[Path, Path], None] = _link
    readlink: Callable[[Path], Path | None] = _readlink
    unlink: Callable[[Path], None] = _unlink
    python: str = SYSTEM_PYTHON
    visudo: str = "/usr/sbin/visudo"
    systemctl: str = "/usr/bin/systemctl"


# ─── what goes into a release ─────────────────────────────────────────────


def _rel_ok(rel: object) -> bool:
    return (isinstance(rel, str) and len(rel) <= 300 and bool(_REL_RE.fullmatch(rel))
            and ".." not in rel.split("/") and Path(rel).suffix in CODE_EXT)


def read_regular(path: Path) -> bytes:
    """A regular file's bytes, never through a final link and never
    blocking on a FIFO the account left in its place; at most MAX_FILE."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) \
        | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise RootCodeError(f"{path} is not a regular file")
        if st.st_size > MAX_FILE:
            raise RootCodeError(f"{path} is too large")
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_FILE:
            raise RootCodeError(f"{path} is too large")
        return data
    finally:
        os.close(fd)


def collect_tree(src: Path) -> dict[str, bytes]:
    """Every code file of the ``satellite`` package at ``src`` (tests and
    caches left out). Used for the code root is already running, never
    for a tree root has not been running."""
    files: dict[str, bytes] = {}
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src).as_posix()
        parts = rel.split("/")
        if "__pycache__" in parts or parts[0] == "tests" or path.is_symlink():
            continue
        if path.is_file() and _rel_ok(rel):
            files[rel] = read_regular(path)
    return files


def collect_verified(src: Path, manifest: dict[str, str]) -> dict[str, bytes]:
    """The files a signed manifest lists, read from ``src`` and each kept
    only when its SHA-256 is the signed one: what is written is the bytes
    that were checked. Any file missing or different: RootCodeError."""
    files: dict[str, bytes] = {}
    for rel, sha in sorted(manifest.items()):
        if not _rel_ok(rel) or rel.split("/")[0] == "tests":
            continue
        try:
            data = read_regular(src / rel)
        except OSError as e:
            raise RootCodeError(f"{rel}: {e}") from e
        if hashlib.sha256(data).hexdigest() != str(sha).lower():
            raise RootCodeError(f"{rel} is not the file the server signed")
        files[rel] = data
    return files


def release_name(digests: dict[str, str]) -> str:
    """A release's name: a digest over its file list and their SHA-256s,
    so the same code is the same release, whichever way it arrived."""
    h = hashlib.sha256()
    for rel in sorted(digests):
        h.update(f"{rel}\0{digests[rel]}\n".encode())
    return h.hexdigest()[:16]


def _digests(files: dict[str, bytes]) -> dict[str, str]:
    return {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}


# ─── the unit, the wrapper, sudoers ───────────────────────────────────────


def valid_user(user: object) -> bool:
    return isinstance(user, str) and bool(_USER_RE.fullmatch(user)) and user != "root"


def render_unit(template: str, user: str) -> str:
    if not valid_user(user):
        raise RootCodeError(f"not a service account name: {user!r}")
    text = template.replace("@USER@", user)
    if "@HOME@" in text or "@USER@" in text:
        raise RootCodeError("the provisioning unit template still names a placeholder")
    return text


def wrapper_line(user: str) -> str:
    return f"{user} ALL=(root) NOPASSWD: {WRAPPER_PATH}"


_TAG_RE = re.compile(r"\s*[A-Z_]+:")


def _commands(line: str) -> list[str]:
    """The commands of one sudoers rule (``user host=(runas) TAGS: a, b``)."""
    spec = line.split("=", 1)[1].strip()
    if spec.startswith("("):
        spec = spec.split(")", 1)[1] if ")" in spec else ""
    while True:
        m = _TAG_RE.match(spec)
        if not m:
            break
        spec = spec[m.end():]
    return [c.strip() for c in spec.split(",") if c.strip()]


def loose_rule(line: str, user: str) -> bool:
    """Whether one sudoers line gives ``user`` ``ALL`` or the XVF3800 tool."""
    code = line.split("#", 1)[0].strip()
    if not code or code.split()[0] != user or "=" not in code:
        return False
    for cmd in _commands(code):
        argv = cmd.split()
        if cmd == "ALL" or (argv and argv[0].endswith("/xvf_host")):
            return True
    return False


def desired_sudoers(current: str, user: str) -> str:
    """``current`` with every rule that gives the account ``ALL`` or
    ``xvf_host`` taken out, and the wrapper's line added once. Every other
    line (the helpers, the comments) is kept as it is."""
    out: list[str] = []
    have = False
    for line in current.splitlines():
        if loose_rule(line, user):
            continue
        if line.strip() == wrapper_line(user):
            have = True
        out.append(line)
    if not have:
        out += ["# The XVF3800's LED ring and DSP, only through the root-owned wrapper",
                "# that accepts the exact operations the satellite makes",
                "# (satellite/xvf_guard.py), never the tool with free arguments.",
                wrapper_line(user)]
    return "\n".join(out) + "\n"


#: The setup indicator's one sudo line on a card prepared before 1.5.0, and
#: what the switch puts in its place: the wrapper first, so the ring still
#: lights for the client once sudoers no longer names the tool.
_STATUS_OLD = '  sudo -n "$XVF" "$@" >/dev/null 2>&1\n'
_STATUS_NEW = (f'  sudo -n {WRAPPER_PATH} "$@" >/dev/null 2>&1 && return 0\n'
               + _STATUS_OLD)


def desired_status(text: str) -> str:
    """The installed domovoi-status with the wrapper tried before the tool
    (a helper from 1.5.0 on already does; one without the line is left)."""
    if WRAPPER_PATH in text or _STATUS_OLD not in text:
        return text
    return text.replace(_STATUS_OLD, _STATUS_NEW, 1)


# ─── one transaction ──────────────────────────────────────────────────────


class _Undo:
    def __init__(self) -> None:
        self.steps: list[tuple[str, Callable[[], None]]] = []

    def add(self, what: str, fn: Callable[[], None]) -> None:
        self.steps.append((what, fn))

    def run(self) -> list[str]:
        failed = []
        for what, fn in reversed(self.steps):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 — undo the rest regardless
                log.error("root code: could not undo %s: %s", what, e)
                failed.append(what)
        self.steps.clear()
        return failed


def _write_file(path: Path, data: bytes, mode: int, ops: Ops) -> Path:
    """``data`` into a new file beside ``path`` (a name with a dot, which
    sudo's includedir skips), root's, at ``mode``. Returns the new file."""
    tmp = path.with_name(path.name + ".domovoi-new")
    _unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    if os.name == "posix":      # on Windows (tests) a mode without w is read-only
        os.chmod(tmp, mode)
    ops.chown_root(tmp)
    return tmp


def _replace(path: Path, data: bytes, mode: int, ops: Ops, undo: _Undo,
             check: Callable[[Path], None] | None = None) -> bool:
    """Put ``data`` at ``path`` (checked first by ``check``), remembering how
    to put the old file back. False when it already held exactly that."""
    try:
        old: bytes | None = path.read_bytes()
        old_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        old, old_mode = None, mode
    if old == data:
        return False
    tmp = _write_file(path, data, mode, ops)
    try:
        if check is not None:
            check(tmp)
    except Exception:
        _unlink(tmp)
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, path)

    def back() -> None:
        if old is None:
            _unlink(path)
        else:
            os.replace(_write_file(path, old, old_mode, ops), path)

    undo.add(str(path), back)
    return True


def _ok(result: Any) -> bool:
    return getattr(result, "returncode", 1) == 0


def _make_dir(path: Path, ops: Ops) -> None:
    missing = []
    p = path
    while not p.exists():
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        d.mkdir(mode=0o755)
        os.chmod(d, 0o755)
        ops.chown_root(d)


def write_release(files: dict[str, bytes], layout: Layout, ops: Ops) -> Path:
    """A release holding ``files`` under ``satellite/``, root's, checked by
    importing the provisioning code with the system Python from it."""
    for need in (UNIT_TEMPLATE, WRAPPER_SOURCE, "root_entry.py", "provisioning_mode.py"):
        if need not in files:
            raise RootCodeError(f"the code has no {need}")
    name = release_name(_digests(files))
    release = layout.releases / name
    _make_dir(layout.releases, ops)
    if release.exists():
        shutil.rmtree(release)
    tmp = layout.releases / (name + ".new")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        for rel, data in files.items():
            dest = tmp / "satellite" / rel
            _make_dir(dest.parent, ops)
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                         0o644)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            os.chmod(dest, 0o644)
            ops.chown_root(dest)
        if not (tmp / "satellite" / "__init__.py").exists():
            raise RootCodeError("the code has no satellite/__init__.py")
        try:
            smoke = ops.run([ops.python, "-E", "-s", "-c", _SMOKE], cwd=str(tmp),
                            capture_output=True, text=True, timeout=300,
                            stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            raise RootCodeError(f"the system Python could not check the copy: {e}") from e
        if not _ok(smoke):
            err = (getattr(smoke, "stderr", "") or "").strip().splitlines()[-1:]
            raise RootCodeError(f"the system Python cannot import the copy: {err}")
        os.replace(tmp, release)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return release


def _prune(layout: Layout, keep: set[str]) -> None:
    try:
        entries = list(layout.releases.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name not in keep:
            shutil.rmtree(entry, ignore_errors=True)


def current_release(layout: Layout, ops: Ops) -> str | None:
    target = ops.readlink(layout.root_code)
    return target.name if target is not None else None


def pending(files: dict[str, bytes], user: str, layout: Layout, ops: Ops) -> list[str]:
    """What still differs from the switched layout for this code (empty: nothing)."""
    out: list[str] = []
    if current_release(layout, ops) != release_name(_digests(files)):
        out.append("root's code copy")
    try:
        if read_regular(layout.wrapper) != files.get(WRAPPER_SOURCE):
            out.append("the XVF3800 wrapper")
    except OSError:
        out.append("the XVF3800 wrapper")
    try:
        unit = layout.unit.read_text(encoding="utf-8")
    except OSError:
        unit = ""
    want = files.get(UNIT_TEMPLATE, b"").decode("utf-8", "replace")
    if unit != render_unit(want, user):
        out.append("the provisioning unit")
    if layout.sudoers.exists():
        cur = layout.sudoers.read_text(encoding="utf-8", errors="replace")
        if desired_sudoers(cur, user) != cur:
            out.append("sudoers")
    if layout.status.exists():
        cur = layout.status.read_text(encoding="utf-8", errors="replace")
        if desired_status(cur) != cur:
            out.append("the setup indicator")
    return out


def switch(files: dict[str, bytes], user: str, *, layout: Layout | None = None,
           ops: Ops | None = None, source: str = "") -> dict[str, Any]:
    """Make this card run root's copy of ``files``, all or nothing.

    Returns ``{"changed": [...], "release": name}``; raises RootCodeError
    after undoing every step it made. Running it again with the same files
    changes nothing."""
    layout = layout or Layout()
    ops = ops or Ops()
    if not valid_user(user):
        raise RootCodeError(f"not a service account name: {user!r}")
    for need in (UNIT_TEMPLATE, WRAPPER_SOURCE):
        if need not in files:
            raise RootCodeError(f"the code has no {need}")
    todo = pending(files, user, layout, ops)
    name = release_name(_digests(files))
    if not todo:
        return {"changed": [], "release": name}
    undo = _Undo()
    previous = ops.readlink(layout.root_code)
    try:
        if "root's code copy" in todo:
            release = write_release(files, layout, ops)
            undo.add("the new release", lambda: shutil.rmtree(release, ignore_errors=True))
            ops.link(release, layout.root_code)
            undo.add("the release link", lambda: (ops.link(previous, layout.root_code)
                                                  if previous is not None
                                                  else ops.unlink(layout.root_code)))
        _make_dir(layout.wrapper.parent, ops)
        _replace(layout.wrapper, files[WRAPPER_SOURCE], 0o755, ops, undo)
        unit = render_unit(files[UNIT_TEMPLATE].decode("utf-8"), user)
        _replace(layout.unit, unit.encode("utf-8"), 0o644, ops, undo)
        if layout.sudoers.exists():
            cur = layout.sudoers.read_text(encoding="utf-8")

            def visudo(tmp: Path) -> None:
                if not _ok(ops.run([ops.visudo, "-cf", str(tmp)], capture_output=True,
                                   text=True, timeout=30, stdin=subprocess.DEVNULL)):
                    raise RootCodeError("visudo refused the new sudoers file")

            _replace(layout.sudoers, desired_sudoers(cur, user).encode("utf-8"), 0o440, ops,
                     undo, check=visudo)
        if layout.status.exists():
            cur = layout.status.read_text(encoding="utf-8")
            _replace(layout.status, desired_status(cur).encode("utf-8"), 0o755, ops, undo)
        if not _ok(ops.run([ops.systemctl, "daemon-reload"], capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)):
            raise RootCodeError("systemctl daemon-reload failed")
    except Exception as e:
        failed = undo.run()
        try:
            ops.run([ops.systemctl, "daemon-reload"], capture_output=True, text=True,
                    timeout=60, stdin=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001 — the files are back; a reload is a nicety
            pass
        detail = f"{e}" + (f" (could not undo: {', '.join(failed)})" if failed else "")
        raise RootCodeError(f"root code switch undone: {detail}") from e
    keep = {name}
    if previous is not None:
        keep.add(previous.name)
    _prune(layout, keep)
    try:
        _make_dir(layout.state_dir, ops)
        layout.record.write_text(json.dumps({"release": name, "source": source,
                                             "changed": todo}, indent=2) + "\n",
                                 encoding="utf-8")
    except OSError as e:
        log.warning("root code: could not write %s: %s", layout.record, e)
    return {"changed": todo, "release": name}


# ─── where the code comes from ────────────────────────────────────────────


def home_of(user: str) -> Path | None:
    if not valid_user(user):
        return None
    try:
        import pwd

        return Path(pwd.getpwnam(user).pw_dir)
    except (ImportError, KeyError):
        return None


def user_of_home(home: str | None) -> str | None:
    """The account whose home is ``home`` (the old unit only set HOME)."""
    if not home:
        return None
    try:
        import pwd

        for entry in pwd.getpwall():
            if entry.pw_dir.rstrip("/") == home.rstrip("/") and valid_user(entry.pw_name):
                return entry.pw_name
    except ImportError:
        pass
    return None


def verified_manifest(home: Path, layout: Layout,
                      pin: Callable[[], str | None] | None = None) -> dict[str, str] | None:
    """The code manifest the client last accepted, checked again by root:
    the server's signature against the root-owned pin and the serial
    against root's own record. None when it can't be (no pin, no envelope,
    or it does not verify), with the reason logged."""
    from satellite import server_identity as si

    if pin is None:
        def pin() -> str | None:
            fingerprint, source = si.pinned_fingerprint(None)
            return fingerprint if source == "image" else None
    fingerprint = pin()
    if not fingerprint:
        log.info("root code: no root-owned server pin, so no update to root's copy can be "
                 "checked; it stays as installed")
        return None
    path = home / ".domovoi" / ENVELOPE_NAME
    try:
        doc = json.loads(read_regular(path).decode("utf-8"))
    except FileNotFoundError:
        log.info("root code: no signed code manifest from the client yet (%s)", path)
        return None
    except (OSError, ValueError, RootCodeError) as e:
        log.warning("root code: the signed code manifest could not be read: %s", e)
        return None
    try:
        manifest = si.accept_manifest_envelope(doc, channel=si.CODE_CHANNEL,
                                               expected_fingerprint=fingerprint,
                                               path=layout.serial_file)
    except si.IdentityError as e:
        log.warning("root code: the signed code manifest was refused: %s", e)
        return None
    if not isinstance(manifest, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in manifest.items()):
        log.warning("root code: the signed code manifest is not a path -> sha256 map")
        return None
    return manifest


def running_from_root_copy(layout: Layout | None = None) -> bool:
    layout = layout or Layout()
    here = Path(__file__).resolve()
    try:
        return here.is_relative_to(layout.releases.resolve())
    except OSError:
        return False


def refresh(user: str, *, layout: Layout | None = None, ops: Ops | None = None,
            pin: Callable[[], str | None] | None = None) -> dict[str, Any] | None:
    """Root's boot step, from root's copy (satellite/root_entry.py): bring
    the copy up to the code the client synced, verified as above, and keep
    the layout switched. Never raises."""
    layout = layout or Layout()
    ops = ops or Ops()
    try:
        home = home_of(user)
        if home is None:
            log.error("root code: no account %r to read updates from", user)
            return None
        manifest = verified_manifest(home, layout, pin)
        files = None
        source = "verified update"
        if manifest is not None:
            want = release_name({r: s.lower() for r, s in manifest.items()
                                 if _rel_ok(r) and r.split("/")[0] != "tests"})
            if want != current_release(layout, ops):
                try:
                    files = collect_verified(home / "domovoi" / "satellite", manifest)
                except RootCodeError as e:
                    log.warning("root code: the account's tree is not the signed code (%s); "
                                "root's copy is unchanged", e)
        if files is None:
            # Root's own copy, re-applied: keeps the wrapper, unit and sudoers
            # switched. Root-owned, so nothing unverified comes in this way.
            files = collect_tree(layout.root_code / "satellite")
            source = "root's copy"
        result = switch(files, user, layout=layout, ops=ops, source=source)
        if result["changed"]:
            log.info("root code: %s (%s, release %s)", ", ".join(result["changed"]), source,
                     result["release"])
        return result
    except Exception as e:  # noqa: BLE001 — the boot goes on with what is there
        log.warning("root code: %s", e)
        return None


def switch_running_card(*, layout: Layout | None = None, ops: Ops | None = None,
                        home: str | None = None) -> dict[str, Any] | None:
    """The one-time switch on a card whose provisioning unit still runs the
    account's tree as root (called from that run, satellite/
    provisioning_mode.main). The copy is the code this run IS: the signed
    list where root can check it, else the running tree, which root is
    executing already. Never raises; a failure is undone and retried at the
    next boot."""
    layout = layout or Layout()
    ops = ops or Ops()
    try:
        if running_from_root_copy(layout) or not layout.unit.exists():
            return None
        user = user_of_home(home if home is not None else os.environ.get("HOME"))
        if user is None:
            log.warning("root code: cannot tell which account this card serves; not switched")
            return None
        src = Path(__file__).resolve().parent
        files = None
        source = "running tree"
        acct_home = home_of(user)
        manifest = verified_manifest(acct_home, layout) if acct_home else None
        if manifest is not None:
            try:
                files = collect_verified(src, manifest)
                source = "verified update"
            except RootCodeError as e:
                log.info("root code: the running tree is not the signed list (%s); "
                         "copying the running tree", e)
        if files is None:
            files = collect_tree(src)
        result = switch(files, user, layout=layout, ops=ops, source=source)
        if result["changed"]:
            log.warning("root code: switched this card to root's own copy: %s",
                        ", ".join(result["changed"]))
        return result
    except Exception as e:  # noqa: BLE001 — never take the boot down
        log.warning("root code: %s", e)
        return None


def main(argv: list[str] | None = None) -> int:
    """``sudo /usr/bin/python3 -E -s -m satellite.root_code switch <user>``
    from a checkout's root, for a hand-built satellite: the same switch,
    from the code in that checkout."""
    args = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    if len(args) != 2 or args[0] != "switch":
        print("usage: python3 -E -s -m satellite.root_code switch <service-account>",
              file=sys.stderr)
        return 2
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print("run it as root", file=sys.stderr)
        return 2
    try:
        result = switch(collect_tree(Path(__file__).resolve().parent), args[1],
                        source="installed by hand")
    except RootCodeError as e:
        print(e, file=sys.stderr)
        return 1
    print("changed: " + (", ".join(result["changed"]) or "nothing") +
          f" (release {result['release']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
