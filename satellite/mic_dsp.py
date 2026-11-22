"""Microphone DSP presets on an XVF3800 array (`set_mic_dsp`, admin only).

The XVF3800 runs its own AGC and noise suppression. An admin can switch a
satellite between a few fixed settings to compare them in a noisy room,
without SSH: the dashboard sends a preset NAME, and this module turns it
into ``xvf_host`` writes from :data:`PRESETS`. Nothing else is accepted:

* **Allowlist only.** The server never sends a parameter, a value or a
  command; a name that is not a key of :data:`PRESETS` is refused and
  nothing is written. Every value written is either a literal in the table
  or a value read from the chip at start (``factory``).
* **Runtime only.** ``SAVE_CONFIGURATION`` is never sent, so nothing reaches
  the chip's flash: unplugging the array brings its firmware values back.
* **factory** restores the values read before the first change (kept in
  ``~/.domovoi/mic_dsp.json``, so a service restart while a preset is applied does
  not mistake the preset's values for the factory ones).
* After every change it reads the parameters back and reports what the
  chip actually holds (``mic_dsp_state``), and logs one line.

The same table names live in ``domovoi/mic_dsp.py`` (the core validates
the name before sending it); a test holds the two together. A future
"winner" preset is one more row in both.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger("satellite.mic_dsp")

#: Preset name -> the (parameter, values) rows it writes, in order. Each
#: preset after ``factory`` builds on the one before. ``factory`` writes
#: nothing beyond restoring the start-up values.
PRESETS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "factory": (),
    # AGC maximum gain 31.6 (30 dB) instead of the firmware's maximum.
    "B": (("PP_AGCMAXGAIN", ("31.6",)),),
    # B + noise-suppression gain floors 0.05 (stationary, non-stationary).
    "C": (
        ("PP_AGCMAXGAIN", ("31.6",)),
        ("PP_MIN_NS", ("0.05",)),
        ("PP_MIN_NN", ("0.05",)),
    ),
    # C + AGC off at a fixed gain of 10 (20 dB).
    "D": (
        ("PP_AGCMAXGAIN", ("31.6",)),
        ("PP_MIN_NS", ("0.05",)),
        ("PP_MIN_NN", ("0.05",)),
        ("PP_AGCONOFF", ("0",)),
        ("PP_AGCGAIN", ("10",)),
    ),
}

#: Every parameter any preset writes: read at start (the factory values),
#: restored before each preset, and read back after it.
PARAMS: tuple[str, ...] = (
    "PP_AGCONOFF", "PP_AGCMAXGAIN", "PP_AGCGAIN", "PP_MIN_NS", "PP_MIN_NN",
)

#: The profile whose array this drives.
XVF_PROFILE = "xvf3800_usb"

#: The chosen preset and the factory values, kept beside config.toml
#: (``~/.domovoi/mic_dsp.json``).
STATE_NAME = "mic_dsp.json"

#: A test marker's length cap (characters, after escaping).
MARK_MAX = 120

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

#: How long one ``xvf_host`` call may take before its process group is
#: killed, and how long a change waits for one already running.
CALL_TIMEOUT = 5.0
LOCK_TIMEOUT = 10.0

Runner = Callable[[list[str]], "tuple[int, str]"]


def _kill_group(proc: subprocess.Popen) -> None:
    """End a call that hung, children included. Under ``sudo -n`` the
    tool runs as root in sudo's session: SIGTERM to the group reaches sudo,
    which relays it to the tool; SIGKILL then ends whatever is left that
    this user may signal."""
    if sys.platform == "win32":
        proc.kill()
        return
    for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            pass
        try:
            proc.wait(timeout=1.0)
            return
        except subprocess.TimeoutExpired:
            continue


def _run(argv: list[str]) -> tuple[int, str]:
    # Its own session, so a timeout can end the whole group and not only
    # sudo (subprocess.run's timeout would leave the tool running).
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=CALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise
    return proc.returncode, out


def clean_mark(text: object) -> str:
    """A marker as it may be logged: control characters escaped
    (``\\x1b``), surrounding space dropped, at most :data:`MARK_MAX`
    characters. Empty for anything that isn't text."""
    if not isinstance(text, str):
        return ""
    out = []
    for ch in text.strip():
        code = ord(ch)
        if code < 0x20 or code == 0x7F or 0x80 <= code < 0xA0 or ch in "\u2028\u2029":
            out.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)[:MARK_MAX]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(stdout: str, param: str) -> list[float] | None:
    """The numbers on ``xvf_host <param>``'s last line, or None."""
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1].replace(param, " ")
    nums = _NUM_RE.findall(last)
    if not nums:
        return None
    return [float(n) for n in nums[:8]]


def _fmt(values: list[float]) -> list[str]:
    return [str(int(v)) if v.is_integer() else repr(v) for v in values]


class XvfDsp:
    """One XVF3800's DSP presets. Thread-safe; every call blocks on
    ``xvf_host`` (run it off the event loop)."""

    def __init__(self, path: str, *, runner: Runner = _run,
                 state_file: Path | None = None) -> None:
        self._path = path
        self._runner = runner
        self._state_file = state_file or (Path.home() / ".domovoi" / STATE_NAME)
        self._lock = threading.Lock()
        self._prefix: list[str] | None = None
        self._factory: dict[str, list[str]] | None = None
        self._preset = "factory"
        # Whether the chip may hold values other than the factory ones:
        # restoring is skipped while it can't, so `factory` on an untouched
        # chip writes nothing.
        self._dirty = False
        self.last_state: dict | None = None
        self._load()

    @classmethod
    def create(cls, cfg) -> "XvfDsp | None":
        """An instance for an XVF3800 satellite whose ``xvf_host`` is
        installed, else None (the satellite then doesn't declare mic_dsp)."""
        try:
            if getattr(cfg.device, "name", None) != XVF_PROFILE:
                return None
            path = str(cfg.leds_xvf_host_path or "")
        except AttributeError:
            return None
        found = shutil.which(path) or (path if os.path.isfile(path) else None)
        return cls(found) if found else None

    @property
    def preset(self) -> str:
        return self._preset

    # ── the chip ─────────────────────────────────────────────────────

    def _call(self, *args: str) -> tuple[int, str]:
        if self._prefix is None:
            # As the LED driver: a plain call first (a udev rule may grant
            # USB access), then `sudo -n` (the sudoers entry in PROVISIONING).
            for prefix in ([self._path], ["sudo", "-n", self._path]):
                try:
                    rc, out = self._runner([*prefix, PARAMS[0]])
                except (OSError, subprocess.SubprocessError):
                    continue
                if rc == 0:
                    self._prefix = prefix
                    break
            else:
                raise RuntimeError(f"xvf_host at {self._path!r} did not answer")
        try:
            return self._runner([*self._prefix, *args])
        except (OSError, subprocess.SubprocessError) as e:
            return 1, str(e)

    def _read_all(self) -> dict[str, list[float] | None]:
        out: dict[str, list[float] | None] = {}
        for p in PARAMS:
            rc, stdout = self._call(p)
            out[p] = _parse(stdout, p) if rc == 0 else None
        return out

    def _write(self, param: str, values: tuple[str, ...] | list[str]) -> bool:
        rc, _ = self._call(param, *values)
        return rc == 0

    # ── the saved state ──────────────────────────────────────────────

    def _load(self) -> None:
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        preset = data.get("preset")
        factory = data.get("factory")
        if isinstance(factory, dict) and all(
            p in factory and isinstance(factory[p], list)
            and all(isinstance(v, str) and _NUM_RE.fullmatch(v) for v in factory[p])
            for p in PARAMS
        ):
            self._factory = {p: list(factory[p]) for p in PARAMS}
        if isinstance(preset, str) and preset in PRESETS and self._factory is not None:
            self._preset = preset
            # A preset kept from an earlier run, or a restore that never
            # finished, may still be on the chip.
            dirty = data.get("dirty")
            self._dirty = dirty if isinstance(dirty, bool) else preset != "factory"

    def _save(self) -> bool:
        """Write the preset and the factory values; False when it failed."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"preset": self._preset, "factory": self._factory,
                                       "dirty": self._dirty}), encoding="utf-8")
            os.replace(tmp, self._state_file)
        except OSError as e:
            log.warning("mic_dsp: could not save %s: %s", self._state_file, e)
            return False
        return True

    # ── presets ──────────────────────────────────────────────────────

    def _capture_factory(self) -> None:
        """Read the factory values, once, before anything of ours was ever
        written. Once known (or loaded from the saved file) they are never
        read again: the chip may hold a preset's values by then."""
        if self._factory is not None:
            return
        read = self._read_all()
        missing = [p for p, v in read.items() if not v]
        if missing:
            raise RuntimeError(f"could not read {', '.join(missing)} from the array")
        self._factory = {p: _fmt(v) for p, v in read.items() if v}
        self._save()

    def apply(self, name: str | None, *, reason: str = "change") -> dict | None:
        """Apply preset ``name`` (None: the current one, read under the
        lock so a change in flight wins) and return the state to report;
        None when another change held the array too long. Raises ValueError (nothing written) for a name not in
        :data:`PRESETS`."""
        if name is not None and (not isinstance(name, str) or name not in PRESETS):
            raise ValueError(f"unknown mic DSP preset {str(name)[:40]!r}")
        # Bounded: a call that hangs must not pile up every later
        # reconnect behind it.
        if not self._lock.acquire(timeout=LOCK_TIMEOUT):
            log.warning("mic_dsp: %s skipped: another change still holds the array", reason)
            return None
        try:
            if name is None:
                name = self._preset
                if name == "factory" and not self._dirty:
                    # Nothing of ours is on the chip: report, touch nothing.
                    state = self._state(applied={}, readback={}, error=None)
                    return state
            error = None
            applied: dict[str, list[str]] = {}
            try:
                self._capture_factory()
                assert self._factory is not None
                # Kept BEFORE the chip is touched: a restart after a crash
                # mid-write (or a later one) then knows a preset may be on
                # the chip and keeps the saved factory values. A preset
                # that can't be kept is refused, so the chip never holds
                # values a restart could read in as factory.
                # Marked dirty while anything is written: a crash or a
                # failed write mid-way leaves the file saying so, and the
                # next ready or apply restores again.
                previous, was_dirty = self._preset, self._dirty
                self._preset = name
                self._dirty = was_dirty or bool(PRESETS[name])
                if not self._save() and name != "factory":
                    self._preset, self._dirty = previous, was_dirty
                    raise RuntimeError(
                        f"could not save {self._state_file.name}; not changing the array")
                # Back to the factory values first, so presets don't stack.
                restored = True
                if was_dirty:
                    for p in PARAMS:
                        if not self._write(p, self._factory[p]):
                            error = f"xvf_host refused {p}"
                            restored = False
                for param, values in PRESETS[name]:
                    if self._write(param, values):
                        applied[param] = list(values)
                    else:
                        error = f"xvf_host refused {param}"
                readback = self._read_all()
                # Clean only once every restore write took and the chip
                # reads back the factory values.
                if name == "factory" and was_dirty:
                    if restored and self._matches_factory(readback):
                        self._dirty = False
                        self._save()
                    elif error is None:
                        error = "the array did not read back its factory values"
            except RuntimeError as e:
                error = str(e)
                readback = {}
            state = self._state(applied=applied, readback=readback, error=error)
        finally:
            self._lock.release()
        log.info(
            "mic_dsp: at=%s reason=%s preset=%s applied=%s readback=%s%s",
            state["at"], reason, name,
            " ".join(f"{p}={','.join(v)}" for p, v in applied.items()) or "-",
            " ".join(f"{p}={','.join(_fmt(v)) if v else '?'}" for p, v in readback.items()) or "-",
            f" error={error}" if error else "",
        )
        return state

    def _state(self, *, applied: dict, readback: dict, error: str | None) -> dict:
        state = {
            "type": "mic_dsp_state",
            "preset": self._preset,
            "presets": list(PRESETS),
            "applied": applied,
            "factory": dict(self._factory or {}),
            "readback": readback,
            "ok": error is None,
            "error": error,
            "at": _now(),
        }
        self.last_state = state
        return state

    def _matches_factory(self, readback: dict) -> bool:
        assert self._factory is not None
        for p in PARAMS:
            got = readback.get(p)
            want = [float(v) for v in self._factory[p]]
            if not got or len(got) != len(want) or not all(
                    math.isclose(g, w, rel_tol=1e-5, abs_tol=1e-6) for g, w in zip(got, want)):
                return False
        return True

    def read_current(self) -> dict | None:
        """The active preset and the five values the chip holds now: the
        same ``xvf_host`` reads :meth:`apply` makes after a change, nothing
        written. None when a change held the array too long (busy). For the
        read-only diagnostics probe (satellite/diagnostics.py)."""
        if not self._lock.acquire(timeout=LOCK_TIMEOUT):
            return None
        try:
            return {"preset": self._preset, "readback": self._read_all()}
        finally:
            self._lock.release()

    @property
    def active(self) -> bool:
        """A preset of ours is (or may be) on the chip."""
        return self._preset != "factory" or self._dirty

    def reapply(self, reason: str) -> dict | None:
        """The current preset again: after a reconnect, or when the array
        came back from a USB replug (it then holds its firmware values).
        At factory with nothing of ours on the chip it only reports: no
        ``xvf_host`` call, no file write. None when skipped (busy)."""
        return self.apply(None, reason=reason)

    def mark(self, text: object) -> str:
        """Log a test marker; returns it as logged ("" when empty)."""
        cleaned = clean_mark(text)
        if cleaned:
            log.info("mic_dsp mark: at=%s preset=%s text=%s", _now(), self._preset, cleaned)
        return cleaned
