"""The setup-code QR (1.5.0): the dashboard's claim address with the setup
code in the URL **fragment**, as a QR code a phone's camera opens.

``http://192.168.1.20:6369/#setup-code=<the eight words>``

The fragment rule: the code goes after ``#`` and nowhere else. A browser
never sends the fragment to the server (not in the request line, not in a
``Referer``), so the code cannot land in an access log, a proxy's log or
another site's referrer. The dashboard's ``web/static/setup_fragment.js``
reads it before anything else runs, takes it out of the address bar
(``history.replaceState``) and pre-fills the claim form, which posts it
exactly as typing it does (``POST /api/auth/setup``: the same backoff,
the same checks, the same single use).

The QR is shown only where the code itself is already shown: the core's
first-run banner and ``--reset-admin`` (both only when stdout is a
terminal), ``python -m domovoi.setup_qr`` (the same file the Linux
installer's last message tells you to ``cat``; a terminal only), and the
Windows installer's last page (``windows/helper/Steps.cs``, the same
format). No endpoint returns it.

Stdlib only, plus :mod:`domovoi.qr` and :mod:`domovoi.lan_address`.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO
from urllib.parse import quote, urlsplit

from domovoi.qr import MEDIUM, QrCode

# The fragment's key. web/static/setup_fragment.js and
# windows/helper/Steps.cs (SetupQr) use the same.
FRAGMENT_KEY = "setup-code"

DEFAULT_WEB_PORT = 6369

# Light border around the symbol, in modules (the standard asks for 4).
QUIET_ZONE = 4

# Black on white whatever the terminal's own colours: a scanner wants dark
# modules on a light ground, and many terminals are light-on-dark.
_ANSI_ON = "\x1b[30;47m"
_ANSI_OFF = "\x1b[0m"
_BLOCKS = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}


def claim_url(base_url: str, code: str) -> str:
    """The dashboard's claim address for ``code``: ``base_url`` (scheme,
    host and port only) with the code in the fragment. Refuses a base
    with a path, query or fragment of its own, so the code can only ever
    sit after the one ``#``."""
    parts = urlsplit(base_url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("the dashboard address must be http(s)://host[:port]")
    if parts.path not in ("", "/") or parts.query or parts.fragment or "#" in base_url or "?" in base_url:
        raise ValueError("the dashboard address must have no path, query or fragment")
    if not code or not code.strip():
        raise ValueError("no setup code")
    return f"{parts.scheme}://{parts.netloc}/#{FRAGMENT_KEY}={quote(code.strip(), safe='-')}"


def web_port() -> int:
    try:
        port = int(os.environ.get("WEB_PORT", "") or DEFAULT_WEB_PORT)
    except ValueError:
        return DEFAULT_WEB_PORT
    return port if 0 < port < 65536 else DEFAULT_WEB_PORT


def dashboard_base() -> str | None:
    """``http://<this server's LAN address>:<dashboard port>``, or None
    when there is no LAN address (a phone can't reach ``localhost``)."""
    from domovoi import lan_address

    try:
        ip = lan_address.lan_ipv4()
    except Exception:  # pragma: no cover — the lookup never should raise
        ip = None
    if not ip:
        return None
    return f"http://{ip}:{web_port()}"


def render_terminal(qr: QrCode, *, quiet: int = QUIET_ZONE) -> str:
    """The symbol as text: two modules per character (half blocks), black
    on white by ANSI colour, one line per two rows."""
    lo, hi = -quiet, qr.size + quiet
    lines = []
    for y in range(lo, hi, 2):
        row = "".join(_BLOCKS[(qr.dark(x, y), qr.dark(x, y + 1))] for x in range(lo, hi))
        lines.append(_ANSI_ON + row + _ANSI_OFF)
    return "\n".join(lines)


def can_render(stream: TextIO | None = None) -> bool:
    """True when ``stream`` (stdout) can print the half blocks. A Windows
    console on cp1252 can't; there the QR is left out, never mangled."""
    out = stream if stream is not None else sys.stdout
    enc = getattr(out, "encoding", None) or ""
    try:
        "".join(_BLOCKS.values()).encode(enc)
    except (LookupError, UnicodeEncodeError):
        return False
    return bool(enc)


def terminal_block(code: str, *, base_url: str | None = None, stream: TextIO | None = None) -> str | None:
    """What a terminal that already shows ``code`` prints under it: the
    QR and one line saying what it is. None when there is no LAN address
    or the terminal can't draw it. The caller decides that the stream is
    a terminal (:func:`domovoi.admin_auth.stdout_is_terminal`): this
    never prints anything itself."""
    base = base_url or dashboard_base()
    if not base or not can_render(stream):
        return None
    qr = QrCode.encode_text(claim_url(base, code), MEDIUM, -1, True)
    return (
        render_terminal(qr)
        + "\n Or scan this with a phone on the same network: it opens the\n"
        f" dashboard at {base} with the setup code filled in.\n"
    )


def main(argv: list[str] | None = None) -> int:
    """``python -m domovoi.setup_qr``, as the user the core runs as: the
    setup code and its QR, for the terminal you are sitting at. Prints
    nothing secret anywhere else: with stdout redirected it says where
    the file is and stops."""
    from domovoi import admin_auth

    if not admin_auth.stdout_is_terminal():
        print("not shown: stdout is not a terminal. " + admin_auth.setup_code_whereabouts() + ".")
        return 2
    code = admin_auth.read_setup_code()
    if code is None:
        print(f"there is no setup code at {admin_auth.setup_code_path()}: the admin is already "
              "set up, or the core has not started yet (run this as the user the core runs as).")
        return 1
    if admin_auth.setup_code_expired():
        print("the setup code is more than a day old; restart the core to get a fresh one.")
        return 1
    print(f"Setup code: {code}")
    block = terminal_block(code)
    if block is None:
        print("(no QR code: this server has no LAN address, or this terminal can't draw one)")
    else:
        print(block)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
