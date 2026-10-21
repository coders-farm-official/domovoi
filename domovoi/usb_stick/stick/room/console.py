"""Domovoi room stick: the room's screen (domovoi/usb_stick/).

Runs on tty1 as the satellite's own account (domovoi-sat), with the
system's ``python3 -I``. It asks the satellite engine for its state over
the engine's control socket (``~/.domovoi/control.sock``, the same
one-JSON-object-per-line protocol the tray uses, ``satellite/control.py``)
and shows, in plain words and large type:

* while the device waits to be approved: the room name and the six-digit
  approval code, drawn in big block digits, and where to type it;
* when it can't connect: why (the server is off, it did not prove its
  identity, it refused this device);
* once approved: "this computer is the room ...", then the screen blanks.

The digits are drawn with reverse-video spaces, so they need no special
font; the big console font (Terminus 16x32) is set by console-setup.
Stdlib only. Nothing here reaches the network. The pure parts
(:func:`big`, :func:`screen_for`) are tested in
domovoi/tests/test_usb_stick_room.py.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time

STICK = "/usr/local/lib/domovoi-stick/stick.json"
POLL = 2.0

# 5x7 digits, "#" is a lit pixel.
DIGITS = {
    "0": [" ### ", "#   #", "#  ##", "# # #", "##  #", "#   #", " ### "],
    "1": ["  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
    "2": [" ### ", "#   #", "    #", "   # ", "  #  ", " #   ", "#####"],
    "3": ["#####", "   # ", "  #  ", "   # ", "    #", "#   #", " ### "],
    "4": ["   # ", "  ## ", " # # ", "#  # ", "#####", "   # ", "   # "],
    "5": ["#####", "#    ", "#### ", "    #", "    #", "#   #", " ### "],
    "6": ["  ## ", " #   ", "#    ", "#### ", "#   #", "#   #", " ### "],
    "7": ["#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "],
    "8": [" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### "],
    "9": [" ### ", "#   #", "#   #", " ####", "    #", "   # ", " ##  "],
}
ON, OFF = "\033[7m", "\033[0m"


def stick() -> dict:
    try:
        with open(STICK, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def ask(path: str, verb: str) -> dict | None:
    """One request on the engine's control socket, or None."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5.0)
            s.connect(path)
            s.sendall((json.dumps({"verb": verb}) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        doc = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        return doc if isinstance(doc, dict) and doc.get("ok") else None
    except (OSError, ValueError):
        return None


def big(code: str, cols: int) -> list[str]:
    """``code`` in block digits that fit ``cols`` columns: two columns per
    pixel when there is room (square-ish in a 16x32 font), else one."""
    digits = [c for c in code if c in DIGITS]
    if not digits:
        return []
    for px in (2, 1):
        gap = " " * (2 * px)
        width = len(digits) * 5 * px + (len(digits) - 1) * len(gap) + px * 2
        if width <= cols - 4 or px == 1:
            break
    rows = []
    for r in range(7):
        line = []
        for i, d in enumerate(digits):
            if i:
                line.append(gap + (" " * px if i == 3 else ""))  # 123 456
            for ch in DIGITS[d][r]:
                line.append(ON + " " * px + OFF if ch == "#" else " " * px)
        rows.append("".join(line))
    return rows


def render(lines: list[str]) -> str:
    return "\033[2J\033[H\033[?25l\n" + "\n".join("  " + ln for ln in lines) + "\n"


def screen_for(st: dict | None, code: str | None, info: dict, cols: int) -> tuple[list[str], bool]:
    """(lines, approved) for one state."""
    room = (st or {}).get("room", {}).get("name") or info.get("room") or "this room"
    core = info.get("core") or ((st or {}).get("server") or {}).get("url") or "your Domovoi server"
    fp = info.get("fp") or ""
    head = ["Domovoi", "", f"Room: {room}", ""]
    foot = ["", f"Server: {core}", f"        {fp[:24]}..." if fp else ""]
    if st is None:
        return head + ["Starting the room's satellite..."] + foot, False
    state = st.get("connection")
    reason = st.get("reason") or ""
    if state == "awaiting_approval" and code:
        return head + [
            "To finish, type this code in the Domovoi dashboard:",
            "Settings -> Rooms -> Waiting for approval",
            "",
        ] + big(code, cols) + ["", f"(the code is {code[:3]} {code[3:]})"] + foot, False
    if state == "connected":
        return head + [
            f"This computer is now the room \"{room}\".",
            "",
            "You can leave it here. The screen turns off in a minute.",
        ] + foot, True
    if state == "unpinned":
        msg = ["The server at that address did not prove it is your",
               "Domovoi server, so nothing is sent to it.", "", reason[:70]]
    elif state == "refused":
        msg = ["Your Domovoi server turned this computer away:", reason[:70], "",
               "Check Settings -> Rooms in the dashboard."]
    elif state == "offline":
        msg = ["Can't reach your Domovoi server right now. Is it on?", reason[:70], "",
               "It keeps trying by itself."]
    else:
        msg = ["Looking for your Domovoi server..."]
    return head + msg + foot, False


def main() -> int:
    import pwd

    home = pwd.getpwuid(os.getuid()).pw_dir
    sock = os.path.join(home, ".domovoi", "control.sock")
    info = stick()
    out = sys.stdout
    shown = None
    blank_set = None
    last_state = None
    while True:
        st = ask(sock, "status")
        st = {k: v for k, v in st.items() if k not in ("ok", "verb", "req")} if st else None
        code = None
        if st and st.get("connection") == "awaiting_approval":
            got = ask(sock, "code")
            code = str(got.get("code")) if got and got.get("code") else None
        try:
            cols = os.get_terminal_size(out.fileno()).columns
        except OSError:
            cols = 80
        lines, approved = screen_for(st, code, info, cols)
        state = (st or {}).get("connection", "no-engine")
        if state != last_state:
            # The journal gets the state, never the code.
            print(f"DOMOVOI-STICK: CONSOLE {state}" + (" (code on screen)" if code else ""),
                  file=sys.stderr, flush=True)
            last_state = state
        page = render(lines)
        if page != shown:
            out.write(page)
            shown = page
        # Linux console: blank after 1 minute once approved, never while a
        # code is up (ESC [ 9 ; n ] is setterm --blank n).
        want = 1 if approved else 0
        if want != blank_set:
            out.write(f"\033[9;{want}]")
            blank_set = want
        out.flush()
        time.sleep(POLL)


if __name__ == "__main__":
    raise SystemExit(main())
