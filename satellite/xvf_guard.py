#!/usr/bin/python3 -I
"""The XVF3800 tool, through a door that only fits what the satellite needs.

The array's LED ring and DSP are driven over USB by ``xvf_host``, which
needs root. Cards prepared before 1.5.0 let the service account run
``/opt/xvf3800/xvf_host`` through sudo with ANY arguments, and the upstream
tool has options that load a caller-chosen library and write the chip's
saved configuration: a way from the account to root, and a way to make a
"runtime only" DSP preset permanent (security review 2026-11-23, M1).

This file is installed root-owned as ``/usr/local/sbin/domovoi-xvf``
(root:root 0755) and run by the system Python with ``-I``; it is
self-contained and standard library only, so it never runs the satellite
account's code. Sudoers names this wrapper instead of the tool. It accepts
exactly the operations the satellite makes, each argument matched in full,
and then runs the tool from its root-owned folder with a clean environment:

* ``led_effect <0-4>``, ``led_brightness <0-255>``, ``led_speed <0-255>``
* ``led_color 0xRRGGBB``, ``led_doa_color 0xRRGGBB 0xRRGGBB``
  (satellite/leds.py and the setup indicator, domovoi-status)
* ``PP_AGCONOFF``, ``PP_AGCMAXGAIN``, ``PP_AGCGAIN``, ``PP_MIN_NS``,
  ``PP_MIN_NN``: alone (a read) or with one to eight plain non-negative
  numbers (a write), the five parameters satellite/mic_dsp.py reads and
  sets. Nothing else: no ``SAVE_CONFIGURATION``, no option, no path.

Anything else is refused (exit 2) before the tool is started. The tool and
its folder must be root's and writable by nobody else, or the wrapper
refuses to run it (exit 3).
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

#: Where stage 1 (and the update that switches an older card) installs this
#: file, and the one path the sudoers line names.
WRAPPER = Path("/usr/local/sbin/domovoi-xvf")
#: The tool it runs: the copy stage 1 installs from the card's payload,
#: with ``libcommand_map.so`` beside it.
XVF_HOST = Path("/opt/xvf3800/xvf_host")

_INT_0_4 = re.compile(r"[0-4]\Z")
_INT_0_255 = re.compile(r"(?:0|[1-9][0-9]{0,2})\Z")
_COLOR = re.compile(r"0x[0-9a-fA-F]{6}\Z")
#: A plain non-negative decimal, optionally with an exponent: what
#: mic_dsp writes (its table's literals, and values it read from the chip
#: and formatted with ``int``/``repr``). No sign, so nothing looks like an
#: option; at most 24 characters.
_NUMBER = re.compile(r"(?=.{1,24}\Z)[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]{1,3})?\Z")
#: The DSP parameters satellite/mic_dsp.PARAMS names (a test holds the two
#: together).
DSP_PARAMS = ("PP_AGCONOFF", "PP_AGCMAXGAIN", "PP_AGCGAIN", "PP_MIN_NS", "PP_MIN_NN")
DSP_MAX_VALUES = 8


class Refused(ValueError):
    """The arguments are not one of the allowed operations."""


def _byte(value: str) -> bool:
    return bool(_INT_0_255.fullmatch(value)) and int(value) <= 255


def check(args: list[str]) -> list[str]:
    """``args`` unchanged when they are one allowed operation, else
    :class:`Refused`. Every argument is matched in full (``fullmatch`` and
    ``\\Z``, so a trailing newline is no match either)."""
    if not args:
        raise Refused("no operation")
    if not all(isinstance(a, str) for a in args):
        raise Refused("arguments must be text")
    op, values = args[0], args[1:]
    if op == "led_effect":
        ok = len(values) == 1 and bool(_INT_0_4.fullmatch(values[0]))
    elif op in ("led_brightness", "led_speed"):
        ok = len(values) == 1 and _byte(values[0])
    elif op == "led_color":
        ok = len(values) == 1 and bool(_COLOR.fullmatch(values[0]))
    elif op == "led_doa_color":
        ok = len(values) == 2 and all(_COLOR.fullmatch(v) for v in values)
    elif op in DSP_PARAMS:
        ok = len(values) <= DSP_MAX_VALUES and all(_NUMBER.fullmatch(v) for v in values)
    else:
        raise Refused(f"operation not allowed: {op[:40]!r}")
    if not ok:
        raise Refused(f"{op}: arguments not allowed")
    return list(args)


def tool_is_roots(tool: Path = XVF_HOST) -> str | None:
    """None when ``tool`` and its folder are root's and writable by nobody
    else (the library it loads lives in that folder); otherwise why not."""
    for path in (tool, tool.parent):
        try:
            st = os.lstat(path)
        except OSError:
            return f"{path} is missing"
        if stat.S_ISLNK(st.st_mode):
            return f"{path} is a link"
        if st.st_uid != 0:
            return f"{path} is not root's"
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return f"{path} is writable by others"
    return None


def sudo_prefixes(path: str, wrapper: Path | None = None) -> list[list[str]]:
    """The ``sudo -n`` ways the satellite account tries, in order: this
    wrapper where it is installed, then the tool itself (a card from before
    1.5.0 not yet switched, or a hand-built one with its own sudoers line).
    Callers try a plain call to the tool first."""
    wrapper = WRAPPER if wrapper is None else wrapper
    out = [["sudo", "-n", str(wrapper)]] if wrapper.is_file() else []
    out.append(["sudo", "-n", path])
    return out


def main(argv: list[str] | None = None, *, tool: Path = XVF_HOST,
         execv=os.execve) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        check(args)
    except Refused as e:
        print(f"domovoi-xvf: refused: {e}", file=sys.stderr)
        return 2
    problem = tool_is_roots(tool)
    if problem:
        print(f"domovoi-xvf: not running the tool: {problem}", file=sys.stderr)
        return 3
    try:
        os.chdir(tool.parent)
    except OSError as e:
        print(f"domovoi-xvf: {e}", file=sys.stderr)
        return 3
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
    execv(str(tool), [str(tool), *args], env)
    return 3    # only when execv is a test's stand-in


if __name__ == "__main__":
    sys.exit(main())
