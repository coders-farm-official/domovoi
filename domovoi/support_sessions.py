"""Satellite support sessions (1.5.0): an admin's shell on one satellite.

docs/SECURITY_PRIVACY.md, "Support sessions", is the plain-words version;
docs/uml/satellite-protocol.md has the frames. In short:

* **Who.** An admin on the web dashboard, at the security tier, whose
  session proved the password within five minutes AND who types the
  password again in the Open dialog. A session minted from a phone grant
  is never "freshly signed in", so it can never open one.
* **Where.** A satellite whose hello declared ``support_shell`` (a Pi on a
  prepared card whose service account is not in sudo) and whose "Allow
  support sessions" switch (``satellites.support_allowed``, off by
  default) is on. One session per satellite.
* **How.** The browser opens the dashboard's socket with a single-use
  30-second ticket (this module's :class:`TicketBook`); the dashboard
  relays it to the core's socket; the core sends ``support_*`` text frames
  over the satellite's existing WebSocket. The satellite runs a PTY as its
  own service account.
* **Limits** (both ends): 30 minutes (warnings at 5 and 1 left), 10 minutes
  without input (warning at 9), 16 KB per output frame and 64 KB/s, 4 KB
  per input frame, an 8 MB transcript (then "log full"). It ends on any
  disconnect or restart and within :data:`ADMIN_RECHECK_SEC` of the
  admin session ending.
* **Transcript.** asciicast v2 under ``~/.domovoi/support-sessions/<id>.cast``
  (0600), written here: input as received from the browser (typed while
  the PTY's echo was off: ``[hidden input: N bytes]`` for each frame),
  output as the satellite sent it, with known secrets and secret-looking
  lines blanked, matched a whole line at a time with escape codes taken
  out. What is blanked always leaves a marker and its bytes count against
  the 8 MB cap, so no string the shell prints can stop the log. Best
  effort; the transcript is an admin-only read for that reason. Kept 30
  days and 200 MB in all, oldest first.
* **Signed, both ways.** The satellite acts on ``support_allow``,
  ``support_open`` and each ``support_input`` only with this server's
  identity signature (domovoi/server_identity.py, ``SUPPORT_CONTEXT``) over
  the satellite's id, its connection's nonce, the issue time and the
  satellite's pinned support key (satellite/support_shell.py). Every frame
  the satellite sends about a session (opened, refused, output, echo,
  closed) carries the satellite's own signature with its support key,
  under the same context with the ``satellite-to-server`` label, over its
  id, the connection's nonce, the session id and a sequence number that
  rises on every frame of the connection; only the signed values are used.
  The key is pinned (``satellites.support_key``, V082/V083) when an admin
  APPROVES the satellite's pairing: a device that proves its approval
  binds the key into that exchange (satellite/pairing_proof.py, version
  3), one approved by its code had it in the parked hello. A re-pair
  re-pins. A satellite presenting any other key is not offered sessions,
  and the dashboard's room drawer says its key changed. A satellite
  paired before this has none pinned: turning the switch on pins the key
  it presents then (trust at that moment, which the confirm dialog says). A frame
  that fails the check is dropped and logged without its content, and
  counts for nothing: a forged close or a run of forged frames ends no
  session (a missing close runs into the session's own limits).

The satellite never gets new power over the server: its ``support_*``
frames are accepted only for the session open on that same connection,
capped, stored as data and never run. A run of frames for no open session
closes its socket.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import codecs
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi import admin_auth
from domovoi.db.session import session_scope

log = logging.getLogger(__name__)

ABILITY = "support_shell"

#: What an admin is told when the connected satellite's key is not the
#: pinned one (the route's 409; the drawer shows the same words).
KEY_CHANGED_DETAIL = (
    "this satellite's support key changed; re-pair it to use support sessions"
)

# ─── Limits (module constants; tests monkeypatch these) ───────────────────

MAX_SEC = 30 * 60
#: The satellite refuses a `support_open` asking for more than this.
SATELLITE_MAX_SEC = 60 * 60
WARN_LEFT_SEC = (300, 60)
IDLE_SEC = 10 * 60
IDLE_WARN_SEC = 9 * 60
OUTPUT_FRAME_MAX = 16 * 1024
OUTPUT_RATE_MAX = 64 * 1024
#: A satellite may run this far over the rate in one second (frame timing
#: on a busy link) before its frames are dropped.
OUTPUT_RATE_SLACK = 2.0
INPUT_FRAME_MAX = 4 * 1024
TRANSCRIPT_MAX_BYTES = 8 * 1024 * 1024
TRANSCRIPTS_TOTAL_MAX_BYTES = 200 * 1024 * 1024
KEEP_DAYS = 30
TICKET_TTL_SEC = 30.0
ADMIN_RECHECK_SEC = 5.0
OPEN_ANSWER_SEC = 10.0
#: Frames for no open session (or over the caps) before the satellite's
#: socket is closed.
STRAY_FRAME_LIMIT = 20
#: Shell input is an admin action for the idle hour, stamped at most this often.
ACTIVITY_STAMP_EVERY_SEC = 60.0
TICK_SEC = 1.0
COLS_RANGE = (20, 400)
ROWS_RANGE = (5, 200)

START_LINE = "A support session has started in this room."
END_LINE = "The support session has ended."
VOICE_END_REPLY = "Support session ended."

#: End reasons: the code stored in ``support_sessions.end_reason`` and
#: the words the dashboard shows.
REASONS: dict[str, str] = {
    "time_limit": "the 30-minute limit was reached",
    "idle": "10 minutes without input",
    "admin_ended": "ended by the admin",
    "household_ended": "ended from a household device",
    "voice": "ended by voice in the room",
    "switch_off": "support sessions were turned off for this satellite",
    "unpaired": "the satellite was unpaired",
    "exit": "the shell exited",
    "log_full": "log full",
    "admin_session_ended": "the admin sign-in ended",
    "browser_closed": "the terminal was closed",
    "satellite_disconnected": "the satellite disconnected",
    "server_restarted": "server restarted",
    "refused": "the satellite refused",
    "no_answer": "the satellite didn't answer",
    "never_attached": "the terminal never connected",
    "limits_broken": "the satellite broke the output limits",
    "satellite_ended": "the satellite ended it",
    "input_stalled": "the shell stopped reading its input",
    "not_signed": "the server couldn't sign the open",
    "untrusted_key": "the satellite's support key is not the one pinned for it",
    "removed": "the satellite was removed",
}

#: The satellite's own end reasons it may report in `support_closed`.
SATELLITE_REASONS = frozenset({"exit", "time_limit", "idle", "satellite_ended",
                               "input_stalled"})

_ID_RE = re.compile(r"^[0-9a-f]{32}\Z")
#: Refused satellite frames logged per connection before only every
#: hundredth is (the rest are counted).
REFUSED_LOG_FIRST = 5


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def transcript_dir() -> Path:
    return Path.home() / ".domovoi" / "support-sessions"


def transcript_path(session_id: str) -> Path:
    if not valid_id(session_id):
        raise ValueError("bad session id")
    return transcript_dir() / f"{session_id}.cast"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ─── Tickets: single use, 30 seconds, bound to the admin and the satellite ─


@dataclass
class Ticket:
    session_id: str
    admin_hash: str
    satellite_id: str
    expires: float


class TicketBook:
    """Browser socket tickets. Only the sha256 is kept; a ticket is removed
    the moment it is presented, whether or not it was good, so it can be
    used once at most."""

    def __init__(self) -> None:
        self._by_hash: dict[str, Ticket] = {}

    def issue(self, session_id: str, admin_hash: str, satellite_id: str) -> str:
        self._prune()
        raw = secrets.token_hex(32)
        self._by_hash[_sha256(raw)] = Ticket(
            session_id, admin_hash, satellite_id, time.monotonic() + TICKET_TTL_SEC
        )
        return raw

    def redeem(self, raw: Any, session_id: str) -> Ticket | None:
        if not isinstance(raw, str) or not 16 <= len(raw) <= 128:
            return None
        ticket = self._by_hash.pop(_sha256(raw), None)
        if ticket is None or ticket.expires < time.monotonic():
            return None
        if not hmac.compare_digest(ticket.session_id, session_id):
            return None
        return ticket

    def _prune(self) -> None:
        now = time.monotonic()
        for k in [k for k, t in self._by_hash.items() if t.expires < now]:
            self._by_hash.pop(k, None)


# ─── Redaction (best effort, at write time) ───────────────────────────────

REMOVED = "[removed]"
HIDDEN_INPUT = "[hidden input]"
KEY_REMOVED = "[private key removed]"
#: A line is matched whole; one longer than this is matched in pieces.
LINE_MAX = 4096
#: Inside a private key, at most this much is held back before the
#: redactor gives the key up (and says so): a BEGIN line with no END can't
#: silence the transcript.
PEM_MAX = 16 * 1024

_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY-----")
#: What a line inside a key looks like: base64, or a ``Name: value`` header
#: (old encrypted PEM). Any other line ends the key early, and is logged.
_PEM_BODY = re.compile(r"[A-Za-z0-9+/=]{0,128}|[A-Za-z][A-Za-z-]{0,40}: [^\r\n]{0,128}")
_EOL = re.compile(r"\r\n?|\n")
_SECRET_NAME = re.compile(r"(?i)password|passwd|token|secret|key")
#: ``name = value`` / ``export NAME=value`` / ``name: value``. The name is
#: one bounded run that starts at a word, so the work is linear in the
#: line (the unbounded form took about a second per 16 KB frame).
_ASSIGNMENT = re.compile(
    r"(?<![\w.\-])((?:export[ \t]{1,8})?)([\w.\-]{1,128})([ \t]{0,8}[=:][ \t]{0,8})([^\r\n]+)")
_LINE_PATTERNS: tuple[tuple[re.Pattern[str], Any], ...] = (
    # wpa_supplicant / NetworkManager: psk=..., psk: ...
    (re.compile(r"(?i)(\bpsk[ \t]{0,8}[=:][ \t]{0,8})[^\s]+"), r"\1" + REMOVED),
    # A pairing token printed anywhere: any 32+ character token after it.
    (re.compile(r"(?i)(pairing_token\W{0,12})[A-Za-z0-9_\-]{32,}"), r"\1" + REMOVED),
)


def _assignment(m: re.Match[str]) -> str:
    if not _SECRET_NAME.search(m.group(2)) or m.group(4).startswith(REMOVED):
        return m.group(0)
    return m.group(1) + m.group(2) + m.group(3) + REMOVED


def _patterns(line: str) -> str:
    out = line
    for pattern, repl in _LINE_PATTERNS:
        out = pattern.sub(repl, out)
    return _ASSIGNMENT.sub(_assignment, out)


class Redactor:
    """Blanks what the core knows is secret and what looks like a secret,
    for one direction of one session.

    It works a whole line at a time: bytes wait in a per-direction buffer
    until their line ends (or :data:`LINE_MAX`), so a secret typed one key
    per frame, or split over two output frames, is still one line here.
    Each line is also matched with its escape codes taken out; when only
    that copy shows a secret, the plain copy is what gets written.

    Nothing it holds back goes silently: a private key leaves
    :data:`KEY_REMOVED`, and :attr:`dropped` counts every character held
    back, which the transcript charges against its cap. A key with no END
    line within :data:`PEM_MAX` is given up, with a marker, and logging
    resumes."""

    def __init__(self, known: tuple[str, ...] = ()) -> None:
        self._known = tuple(k for k in known if k and len(k) >= 6)
        self._in_pem = False
        self._pem_bytes = 0
        self._buf = ""
        self.dropped = 0

    def __call__(self, chunk: str) -> str:
        """The redacted text of every line ``chunk`` completes."""
        self._buf += chunk
        out: list[str] = []
        while True:
            m = _EOL.search(self._buf)
            if m is None:
                break
            line, self._buf = self._buf[: m.end()], self._buf[m.end():]
            out.append(self._line(line))
        while len(self._buf) > LINE_MAX:
            line, self._buf = self._buf[:LINE_MAX], self._buf[LINE_MAX:]
            out.append(self._line(line))
        return "".join(out)

    def flush(self) -> str:
        """Whatever is left in the buffer (the session ended mid-line)."""
        line, self._buf = self._buf, ""
        return self._line(line) if line else ""

    def _line(self, line: str) -> str:
        plain = _ANSI_RE.sub("", line)
        if self._in_pem:
            if _PEM_END.search(plain) is not None:
                self._in_pem = False
                self.dropped += len(line)
                return ""
            self._pem_bytes += len(line)
            if not _PEM_BODY.fullmatch(plain.strip()):
                # Not a key's line (a command, a prompt): the "key" was a
                # marker, and this line and the rest are logged.
                self._in_pem = False
                return ("[private key: ended without its END line; logging resumed]\r\n"
                        + self._clean(line, plain))
            if self._pem_bytes > PEM_MAX:
                self._in_pem = False
                return ("[private key: no END line within 16 KB; logging resumed]\r\n"
                        + self._clean(line, plain))
            self.dropped += len(line)
            return ""
        begin = _PEM_BEGIN.search(plain)
        if begin is not None:
            head = plain[: begin.start()]
            before = self._clean(head, head)
            rest = plain[begin.end():]
            self.dropped += len(line) - len(head)
            if _PEM_END.search(rest) is None:
                self._in_pem = True
                self._pem_bytes = len(rest)
            return before + KEY_REMOVED + "\r\n"
        return self._clean(line, plain)

    def _clean(self, line: str, plain: str) -> str:
        raw = line
        stripped = plain
        for secret in self._known:
            raw = raw.replace(secret, REMOVED)
            stripped = stripped.replace(secret, REMOVED)
        raw = _patterns(raw)
        stripped = _patterns(stripped)
        if stripped == plain or _ANSI_RE.sub("", raw) == stripped:
            return raw
        # Only the copy without escape codes shows the secret: write that.
        return stripped


# ─── The transcript: asciicast v2 ─────────────────────────────────────────


class LogFull(Exception):
    """The transcript reached its cap; the session must end."""


class Transcript:
    """One session's asciicast v2 file. Every event goes through
    :meth:`write` BEFORE its bytes go anywhere, so nothing reaches the
    satellite or the browser unlogged; past the cap :class:`LogFull`."""

    def __init__(self, session_id: str, *, cols: int, rows: int, title: str,
                 known_secrets: tuple[str, ...] = ()) -> None:
        directory = transcript_dir()
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        self.path = transcript_path(session_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        self._fd = os.open(self.path, flags, 0o600)
        self._start = time.monotonic()
        self.bytes = 0
        self.truncated = False
        self._closed = False
        self._redact_in = Redactor(known_secrets)
        self._redact_out = Redactor(known_secrets)
        self._dec_in = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._dec_out = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._hidden_lines = 0
        header = {
            "version": 2, "width": cols, "height": rows,
            "timestamp": int(time.time()), "title": title[:120],
            "env": {"TERM": "xterm-256color"},
        }
        self._append(json.dumps(header))

    def _append(self, line: str) -> None:
        data = (line + "\n").encode("utf-8")
        os.write(self._fd, data)
        self.bytes += len(data)

    def _charge(self, redactor: Redactor) -> None:
        """What the redactor held back counts against the cap as if it had
        been written: a flood of blanked text still fills the log."""
        if redactor.dropped:
            self.bytes += redactor.dropped
            redactor.dropped = 0
            if self.bytes > TRANSCRIPT_MAX_BYTES - 256:
                self._full()

    def _full(self) -> None:
        self.truncated = True
        self._append(json.dumps([round(time.monotonic() - self._start, 3), "o",
                                 "\r\n[log full: the session ended]\r\n"]))
        self.close()
        raise LogFull()

    def _event(self, kind: str, value: str) -> None:
        if self._closed:
            raise LogFull()
        line = json.dumps([round(time.monotonic() - self._start, 3), kind, value])
        # Room for the closing marker is kept back.
        if self.bytes + len(line) + 1 > TRANSCRIPT_MAX_BYTES - 256:
            self._full()
        self._append(line)

    def input(self, data: bytes, *, echo_on: bool) -> None:
        """Input as the browser sent it. While the PTY's echo is off (a
        password prompt) each frame is logged as a count, never dropped;
        echo-off past the first line (Enter) is no longer a password
        prompt, so input after it is logged as typed."""
        if self._closed:
            raise LogFull()
        if not echo_on and self._hidden_lines < 1:
            self._event("i", f"[hidden input: {len(data)} bytes]")
            if b"\r" in data or b"\n" in data:
                self._hidden_lines += 1
            return
        if echo_on:
            self._hidden_lines = 0
        value = self._redact_in(self._dec_in.decode(data))
        self._charge(self._redact_in)
        if value:
            self._event("i", value)

    def output(self, data: bytes) -> None:
        if self._closed:
            raise LogFull()
        value = self._redact_out(self._dec_out.decode(data))
        self._charge(self._redact_out)
        if value:
            self._event("o", value)

    def flush(self) -> None:
        """Write what the line buffers still hold (the session is ending)."""
        for kind, redactor in (("i", self._redact_in), ("o", self._redact_out)):
            try:
                value = redactor.flush()
                self._charge(redactor)
                if value:
                    self._event(kind, value)
            except LogFull:
                return

    def note(self, words: str) -> None:
        """A line of the session's own (its end), never typed or shown."""
        if not self._closed:
            self.flush()
        if not self._closed:
            try:
                self._event("o", f"\r\n[{words}]\r\n")
            except LogFull:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self._fd)
        except OSError:
            pass


_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"            # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[PX^_][^\x1b]*\x1b\\"           # DCS / SOS / PM / APC
    r"|\x1b[@-Z\\-_]"                      # other two-byte escapes
    r"|\x1b[()][0-9A-Za-z]"
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def plain_text(cast: str) -> str:
    """The transcript's output as plain text: escape codes and control
    characters taken out. For a viewer that sets it as ``textContent``."""
    parts: list[str] = []
    for i, line in enumerate(cast.splitlines()):
        if i == 0 or not line.strip():
            continue
        try:
            t, kind, value = json.loads(line)
        except (ValueError, TypeError):
            continue
        if kind == "o" and isinstance(value, str):
            parts.append(value)
        elif kind == "i" and isinstance(value, str) and value.startswith(HIDDEN_INPUT[:-1]):
            parts.append(HIDDEN_INPUT)
    joined = _ANSI_RE.sub("", "".join(parts)).replace("\r\n", "\n").replace("\r", "")
    return _CTRL_RE.sub("", joined)


# ─── Signed control frames ────────────────────────────────────────────────


def clean_key(value: Any) -> str | None:
    """A support key from a hello: the base64 of 32 bytes, in canonical
    form, or None."""
    if not isinstance(value, str) or not 40 <= len(value) <= 48:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != value:
        return None
    return value


def signed_doc(sat: Any, kind: str, **fields: Any) -> dict[str, Any]:
    """The document the satellite checks: the frame's kind, the satellite's
    id, the nonce its hello sent on THIS connection, the support key it
    presented, the issue time and the frame's own fields. ValueError when
    the connection sent no nonce or no key."""
    nonce = getattr(sat, "support_nonce", None)
    if not isinstance(nonce, str) or not nonce:
        raise ValueError("the satellite sent no support nonce on this connection")
    key = getattr(sat, "support_key", None)
    if not isinstance(key, str) or not key:
        raise ValueError("the satellite sent no support key on this connection")
    return {"kind": kind, "satellite_id": satellite_of(sat), "nonce": nonce,
            "satellite_key": key, "issued_at": int(time.time()), **fields}


async def pinned_key(satellite_id: str) -> str | None:
    """The support key pinned for ``satellite_id`` (None: none)."""
    async with session_scope() as s:
        row = (await s.execute(
            text("SELECT support_key FROM satellites WHERE satellite_id = :id"),
            {"id": satellite_id},
        )).first()
    return row.support_key if row is not None and row.support_key else None


def key_is_pinned(sat: Any, pinned: str | None) -> bool:
    """Whether this connection presented the pinned key."""
    key = getattr(sat, "support_key", None)
    return bool(pinned) and isinstance(key, str) and hmac.compare_digest(key, str(pinned))


def note_key_changed(sat: Any, pinned: str | None) -> bool:
    """Mark the connection (``support_key_changed``) when a key is pinned
    and it presents a different one; clear it otherwise. Only the support
    side reads it: the room drawer's note and nothing about voice use."""
    key = getattr(sat, "support_key", None)
    changed = bool(pinned) and bool(key) and not key_is_pinned(sat, pinned)
    try:
        sat.support_key_changed = changed
    except AttributeError:  # pragma: no cover - a frozen stand-in
        pass
    return changed


class FrameRefused(Exception):
    """A satellite's support frame failed its signature check."""


def verify_satellite_frame(sat: Any, ctrl: dict[str, Any]) -> dict[str, Any]:
    """The signed document of a ``support_*`` frame the satellite sent, or
    :class:`FrameRefused`: unsigned, malformed, a bad signature (a changed
    field included), signed for another kind of frame, another satellite,
    another connection (nonce) or another session, or a sequence number not
    past the last one on this connection (replayed or out of order). The
    key is the one this connection presented, which a session only runs on
    when it is the pinned one."""
    from domovoi import server_identity

    key = getattr(sat, "support_key", None)
    nonce = getattr(sat, "support_nonce", None)
    if not key or not nonce:
        raise FrameRefused("this connection has no support key")
    envelope = ctrl.get("signed")
    if not isinstance(envelope, dict):
        raise FrameRefused("unsigned")
    doc = envelope.get("doc")
    signature = _b64(envelope.get("signature"), 64)
    if envelope.get("algorithm") != server_identity.ALGORITHM or not isinstance(doc, dict) \
            or signature is None or len(signature) != 64:
        raise FrameRefused("malformed signature")
    if not server_identity.verify(base64.b64decode(key), server_identity.support_reply_message(doc),
                                  signature):
        raise FrameRefused("bad signature")
    if doc.get("kind") != ctrl.get("type"):
        raise FrameRefused("signed for another kind of frame")
    if doc.get("satellite_id") != satellite_of(sat):
        raise FrameRefused("signed for another satellite")
    if doc.get("nonce") != nonce:
        raise FrameRefused("signed for another connection")
    if doc.get("session_id") != ctrl.get("session_id"):
        raise FrameRefused("signed for another session")
    seq = doc.get("seq")
    last = getattr(sat, "support_rx_seq", 0) or 0
    if not isinstance(seq, int) or isinstance(seq, bool) or seq <= last:
        raise FrameRefused("replayed or out of order")
    sat.support_rx_seq = seq
    return doc


def sign(doc: dict[str, Any]) -> dict[str, Any]:
    """``doc`` signed with the server identity under its support context
    (domovoi/server_identity.py :data:`SUPPORT_CONTEXT`)."""
    from domovoi import server_identity

    return server_identity.load_or_create().signed_support(doc)


async def send_allow_state(app: Any, sat: Any, allowed: bool | None = None) -> bool:
    """Tell a connected satellite, signed, whether support sessions are
    allowed on it (``satellites.support_allowed`` unless ``allowed`` is
    given). The satellite refuses every open until it holds a signed
    "allowed" for its current connection. False when nothing was sent."""
    try:
        async with session_scope() as s:
            row = (await s.execute(
                text("SELECT support_allowed, support_key FROM satellites "
                     "WHERE satellite_id = :id"),
                {"id": satellite_of(sat)},
            )).first()
    except Exception as e:  # noqa: BLE001 - the satellite stays disarmed
        log.info("support: couldn't read the allow switch for %s: %s", satellite_of(sat), e)
        return False
    if allowed is None:
        allowed = bool(row and row.support_allowed)
    pinned = row.support_key if row is not None else None
    # The room drawer's note (main.py's snapshot): a key is pinned and this
    # connection presents a different one. Voice use is not affected.
    note_key_changed(sat, pinned)
    if allowed and not key_is_pinned(sat, pinned):
        # Only the pinned key is trusted (pinned at the pairing's approval,
        # or by the switch for a satellite paired before that): another one
        # (a reflashed card, or one put in the hello by a host in the
        # middle) is not armed. A re-pair is what pins a new one.
        log.warning("support: %s presented a support key other than the pinned one; "
                    "not allowing sessions (re-pair it to pin its key)",
                    satellite_of(sat))
        allowed = False
    try:
        frame = {"type": "support_allow",
                 "signed": sign(signed_doc(sat, "support_allow", allowed=bool(allowed)))}
        await sat.send_support(frame)
    except Exception as e:  # noqa: BLE001 - undeclared, no nonce, no key, a dead socket
        log.info("support: couldn't send the allow state to %s: %s", satellite_of(sat) or "?", e)
        return False
    return True


# ─── A live session ───────────────────────────────────────────────────────


@dataclass
class Session:
    id: str
    satellite_id: str
    room_id: str | None
    admin_hash: str
    opener_label: str | None
    cols: int
    rows: int
    max_sec: int = MAX_SEC
    idle_sec: int = IDLE_SEC
    state: str = "pending"          # pending | opening | open | ended
    sat: Any = None                 # the StreamSession it runs on
    writer: Transcript | None = None
    deadline: float = 0.0           # monotonic
    ends_at: float = 0.0            # wall clock, for the banner
    last_input: float = 0.0
    echo_on: bool = True
    bytes_in: int = 0
    bytes_out: int = 0
    reason: str | None = None
    warned: set[str] = field(default_factory=set)
    out_window: float = 0.0
    out_window_bytes: int = 0
    last_admin_check: float = 0.0
    last_stamp: float = 0.0
    in_seq: int = 0                 # the last signed input frame's number
    key: str | None = None          # the satellite's pinned support key
    ack: asyncio.Future[Any] | None = None
    outbox: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    expiry_task: asyncio.Task[None] | None = None

    def as_active(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "satellite_id": self.satellite_id,
            "room_id": self.room_id,
            "ends_at": self.ends_at or None,
            "state": self.state,
        }


class Refused(Exception):
    """Opening was refused: ``status`` and ``detail`` for the HTTP answer."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class SupportSessions:
    """Every support session this core has open (at most one per satellite)."""

    def __init__(self, app: Any = None) -> None:
        self.app = app
        self.tickets = TicketBook()
        self.by_id: dict[str, Session] = {}
        self.by_satellite: dict[str, Session] = {}

    # ── opening ──────────────────────────────────────────────────────

    async def create(self, *, satellite_id: str, room_id: str | None, admin_hash: str,
                     cols: int, rows: int) -> tuple[Session, str]:
        """Record a pending session and issue its browser ticket. The
        satellite hears nothing until the browser's socket attaches."""
        if satellite_id in self.by_satellite:
            raise Refused(409, "a support session is already open on this satellite")
        await prune()
        sid = secrets.token_hex(16)
        async with session_scope() as s:
            label = await admin_auth.session_label(s, admin_hash)
            try:
                await s.execute(
                    text(
                        "INSERT INTO support_sessions (id, satellite_id, room_id, opener_label, "
                        "opener_hash_prefix) VALUES (:id, :sat, :room, :label, :prefix)"
                    ),
                    {"id": sid, "sat": satellite_id, "room": room_id,
                     "label": (label or "")[:120] or None, "prefix": admin_hash[:12]},
                )
            except Exception as e:  # the one-open index
                if "support_sessions_one_open" in str(e):
                    raise Refused(409, "a support session is already open on this satellite") from None
                raise
        sess = Session(
            id=sid, satellite_id=satellite_id, room_id=room_id, admin_hash=admin_hash,
            opener_label=label, cols=_clamp(cols, COLS_RANGE), rows=_clamp(rows, ROWS_RANGE),
            max_sec=MAX_SEC, idle_sec=IDLE_SEC,
        )
        self.by_id[sid] = sess
        self.by_satellite[satellite_id] = sess
        ticket = self.tickets.issue(sid, admin_hash, satellite_id)
        sess.expiry_task = asyncio.create_task(self._expire_pending(sess))
        log.info("support session %s created for %s by %r", sid[:8], satellite_id, label)
        return sess, ticket

    async def _expire_pending(self, sess: Session) -> None:
        await asyncio.sleep(TICKET_TTL_SEC + 5.0)
        if sess.state == "pending":
            await self.end(sess, "never_attached", announce=False)

    def redeem(self, raw_ticket: Any, session_id: str) -> Session | None:
        ticket = self.tickets.redeem(raw_ticket, session_id)
        if ticket is None:
            return None
        sess = self.by_id.get(session_id)
        if sess is None or sess.state != "pending" or sess.admin_hash != ticket.admin_hash \
                or sess.satellite_id != ticket.satellite_id:
            return None
        return sess

    # ── the terminal ─────────────────────────────────────────────────

    async def run(self, ws: Any, sess: Session) -> None:
        """Serve the relayed browser socket ``ws`` (accepted) for ``sess``
        until the session ends."""
        sess.state = "opening"
        if sess.expiry_task is not None:
            sess.expiry_task.cancel()
        sender = asyncio.create_task(self._send_loop(ws, sess))
        try:
            if not await self._admin_live(sess, force=True):
                await self.end(sess, "admin_session_ended", announce=False)
                return
            sat = live_satellite(self.app, sess.satellite_id)
            if sat is None:
                await self.end(sess, "satellite_disconnected", announce=False)
                return
            sess.sat = sat
            try:
                sess.key = await pinned_key(sess.satellite_id)
            except Exception as e:  # noqa: BLE001 - fail closed
                log.warning("support session %s: couldn't read the pinned key: %s", sess.id[:8], e)
                sess.key = None
            if not key_is_pinned(sat, sess.key):
                await self.end(sess, "untrusted_key", announce=False)
                return
            known = await _known_secrets()
            sess.writer = Transcript(
                sess.id, cols=sess.cols, rows=sess.rows,
                title=f"Domovoi support session on {sess.satellite_id}", known_secrets=known,
            )
            sess.ack = asyncio.get_running_loop().create_future()
            try:
                opening = sign(signed_doc(
                    sat, "support_open", session_id=sess.id, cols=sess.cols, rows=sess.rows,
                    max_sec=sess.max_sec, idle_sec=sess.idle_sec))
            except Exception as e:  # noqa: BLE001 - no nonce, no key: nothing is sent
                log.warning("support session %s: couldn't sign the open: %s", sess.id[:8], e)
                await self.end(sess, "not_signed", announce=False)
                return
            try:
                await sat.send_support({"type": "support_open", "session_id": sess.id,
                                        "signed": opening})
                answer = await asyncio.wait_for(asyncio.shield(sess.ack), OPEN_ANSWER_SEC)
            except (TimeoutError, asyncio.TimeoutError):
                await self.end(sess, "no_answer", announce=False)
                return
            except Exception as e:  # noqa: BLE001 - a dead socket, an undeclared ability
                log.info("support session %s: open failed: %s", sess.id[:8], e)
                await self.end(sess, "satellite_disconnected", announce=False)
                return
            if answer is not True:
                await self.end(sess, "refused", announce=False, detail=str(answer or "")[:120])
                return
            now = time.monotonic()
            sess.state = "open"
            sess.deadline = now + sess.max_sec
            sess.ends_at = time.time() + sess.max_sec
            sess.last_input = now
            sess.outbox.put_nowait({"t": "opened", "max_sec": sess.max_sec,
                                    "idle_sec": sess.idle_sec, "ends_in_sec": sess.max_sec})
            log.info("support session %s opened on %s by %r", sess.id[:8], sess.satellite_id,
                     sess.opener_label)
            # The room hears it before the first byte of input reaches the
            # shell (the browser's frames are read only after), retried: a
            # reply may be playing.
            await _say(sat, START_LINE, attempts=3)
            receiver = asyncio.create_task(self._receive_loop(ws, sess))
            ticker = asyncio.create_task(self._tick_loop(sess))
            try:
                await sess.ended.wait()
            finally:
                for task in (receiver, ticker):
                    task.cancel()
        finally:
            if sess.state != "ended":
                await self.end(sess, "browser_closed")
            try:
                await asyncio.wait_for(sender, 2.0)
            except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError):
                sender.cancel()

    async def _send_loop(self, ws: Any, sess: Session) -> None:
        while True:
            msg = await sess.outbox.get()
            if msg is None:
                return
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:  # noqa: BLE001 - the browser left
                if sess.state != "ended":
                    asyncio.create_task(self.end(sess, "browser_closed"))
                return

    async def _receive_loop(self, ws: Any, sess: Session) -> None:
        try:
            while sess.state == "open":
                raw = await ws.receive_text()
                await self.browser_message(sess, raw)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - disconnect
            pass
        if sess.state != "ended":
            await self.end(sess, "browser_closed")

    async def browser_message(self, sess: Session, raw: Any) -> None:
        """One frame from the browser: ``{"t":"in","d":b64,"k":bool}`` or
        ``{"t":"resize","cols":n,"rows":n}``. Anything else is ignored.
        ``k`` says the bytes came from a key press or a paste: only those
        count as activity for the idle limit, so a terminal's automatic
        answers to a program's queries can't keep a session alive."""
        if sess.state != "open" or not isinstance(raw, str) or len(raw) > INPUT_FRAME_MAX * 2:
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("t")
        if kind == "in":
            data = _b64(msg.get("d"), INPUT_FRAME_MAX)
            if not data:
                return
            # Before each input: the admin sign-in must still be live.
            if not await self._admin_live(sess, force=True):
                await self.end(sess, "admin_session_ended")
                return
            try:
                assert sess.writer is not None
                sess.writer.input(data, echo_on=sess.echo_on)
            except LogFull:
                await self.end(sess, "log_full")
                return
            sess.bytes_in += len(data)
            active = msg.get("k") is True
            if active:
                sess.last_input = time.monotonic()
                sess.warned.discard("idle")
            sess.in_seq += 1
            try:
                signed = sign(signed_doc(
                    sess.sat, "support_input", session_id=sess.id, seq=sess.in_seq,
                    active=active, data=base64.b64encode(data).decode("ascii")))
            except Exception as e:  # noqa: BLE001
                log.warning("support session %s: couldn't sign input: %s", sess.id[:8], e)
                await self.end(sess, "not_signed")
                return
            await self._to_sat(sess, {"type": "support_input", "session_id": sess.id,
                                      "signed": signed})
            if active and time.monotonic() - sess.last_stamp >= ACTIVITY_STAMP_EVERY_SEC:
                sess.last_stamp = time.monotonic()
                try:
                    await admin_auth.stamp_admin_activity_by_hash(sess.admin_hash)
                except Exception as e:  # noqa: BLE001
                    log.debug("support session: activity stamp failed: %s", e)
        elif kind == "resize":
            cols, rows = msg.get("cols"), msg.get("rows")
            if isinstance(cols, int) and isinstance(rows, int):
                sess.cols, sess.rows = _clamp(cols, COLS_RANGE), _clamp(rows, ROWS_RANGE)
                await self._to_sat(sess, {"type": "support_resize", "session_id": sess.id,
                                          "cols": sess.cols, "rows": sess.rows})

    async def _to_sat(self, sess: Session, frame: dict[str, Any]) -> None:
        try:
            await sess.sat.send_support(frame)
        except Exception as e:  # noqa: BLE001
            log.info("support session %s: satellite send failed: %s", sess.id[:8], e)
            await self.end(sess, "satellite_disconnected", announce=False)

    async def _tick_loop(self, sess: Session) -> None:
        while sess.state == "open":
            await asyncio.sleep(TICK_SEC)
            await self.tick(sess)

    async def tick(self, sess: Session, now: float | None = None) -> None:
        """The session's clocks: the time limit and its warnings, the idle
        limit and its warning, the admin re-check."""
        if sess.state != "open":
            return
        now = time.monotonic() if now is None else now
        left = sess.deadline - now
        if left <= 0:
            await self.end(sess, "time_limit")
            return
        for mark in WARN_LEFT_SEC:
            key = f"left{mark}"
            if left <= mark and key not in sess.warned:
                sess.warned.add(key)
                minutes = max(1, round(mark / 60))
                sess.outbox.put_nowait({"t": "warn", "text": (
                    f"{minutes} minute{'s' if minutes != 1 else ''} left in this support session")})
        idle = now - sess.last_input
        if idle >= sess.idle_sec:
            await self.end(sess, "idle")
            return
        if idle >= IDLE_WARN_SEC and "idle" not in sess.warned:
            sess.warned.add("idle")
            sess.outbox.put_nowait({"t": "warn", "text": (
                "No input for 9 minutes: the session closes after 10")})
        if not await self._admin_live(sess, now=now):
            await self.end(sess, "admin_session_ended")

    async def _admin_live(self, sess: Session, *, force: bool = False, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if not force and now - sess.last_admin_check < ADMIN_RECHECK_SEC:
            return True
        sess.last_admin_check = now
        try:
            async with session_scope() as s:
                return await admin_auth.session_live_by_hash(s, sess.admin_hash)
        except Exception as e:  # noqa: BLE001 - fail closed
            log.warning("support session %s: admin check failed: %s", sess.id[:8], e)
            return False

    # ── frames from the satellite ────────────────────────────────────

    async def satellite_frame(self, sat: Any, ctrl: dict[str, Any]) -> None:
        """A ``support_*`` frame from a satellite connection. Its signature
        is checked first (:func:`verify_satellite_frame`): a frame that
        fails is dropped and logged without its content, and counts for
        nothing, so forged frames can't end a session or close the socket.
        A signed one is accepted only for the session open on THAT
        connection (and on the pinned key); anything else is dropped and
        counted, and a run of them closes the socket. Only the signed
        values are used."""
        kind = ctrl.get("type")
        try:
            doc = verify_satellite_frame(sat, ctrl)
        except FrameRefused as e:
            self._refused(sat, kind, ctrl.get("session_id"), str(e))
            return
        sid = doc.get("session_id")
        sess = self.by_id.get(sid) if isinstance(sid, str) else None
        if sess is None or sess.sat is not sat or sess.state not in ("opening", "open") \
                or not key_is_pinned(sat, sess.key):
            await self._stray(sat, kind)
            return
        if kind == "support_opened":
            if sess.ack is not None and not sess.ack.done():
                sess.ack.set_result(True)
            return
        if kind == "support_refused":
            if sess.ack is not None and not sess.ack.done():
                sess.ack.set_result(str(doc.get("reason") or "refused"))
            return
        if sess.state != "open":
            await self._stray(sat, kind)
            return
        if kind == "support_output":
            data = _b64(doc.get("data"), OUTPUT_FRAME_MAX)
            if data is None or not self._within_rate(sess, len(data)):
                await self._stray(sat, kind)
                return
            try:
                assert sess.writer is not None
                sess.writer.output(data)
            except LogFull:
                await self.end(sess, "log_full")
                return
            sess.bytes_out += len(data)
            sess.outbox.put_nowait({"t": "out", "d": base64.b64encode(data).decode("ascii")})
        elif kind == "support_echo":
            sess.echo_on = doc.get("on") is not False
        elif kind == "support_closed":
            reason = doc.get("reason")
            await self.end(sess, reason if reason in SATELLITE_REASONS else "satellite_ended")
        else:
            await self._stray(sat, kind)

    def _within_rate(self, sess: Session, size: int) -> bool:
        now = time.monotonic()
        if now - sess.out_window >= 1.0:
            sess.out_window, sess.out_window_bytes = now, 0
        sess.out_window_bytes += size
        return sess.out_window_bytes <= OUTPUT_RATE_MAX * OUTPUT_RATE_SLACK

    def _refused(self, sat: Any, kind: Any, sid: Any, why: str) -> None:
        """Log a frame that failed its signature check: the kind, the
        satellite, the session's first digits and why, never its content.
        The first few per connection, then every hundredth."""
        count = getattr(sat, "support_refused_frames", 0) + 1
        try:
            sat.support_refused_frames = count
        except AttributeError:
            pass
        if count <= REFUSED_LOG_FIRST or count % 100 == 0:
            short = sid[:8] if isinstance(sid, str) else "?"
            log.warning("support: refused a %s frame from %s for session %s: %s (%d refused)",
                        str(kind)[:32], satellite_of(sat) or "?", short, why, count)

    async def _stray(self, sat: Any, kind: Any) -> None:
        count = getattr(sat, "support_strays", 0) + 1
        try:
            sat.support_strays = count
        except AttributeError:
            pass
        log.info("support: dropped %r from %s (%d)", kind, satellite_of(sat) or "?", count)
        if count >= STRAY_FRAME_LIMIT:
            sess = self.by_satellite.get(satellite_of(sat) or "")
            if sess is not None and sess.sat is sat:
                await self.end(sess, "limits_broken", announce=False)
            try:
                await sat.ws.close(code=1008)
            except Exception:  # noqa: BLE001
                pass

    # ── ending ───────────────────────────────────────────────────────

    async def end(self, sess: Session, reason: str, *, announce: bool = True,
                  detail: str | None = None) -> None:
        """End ``sess`` (idempotent): tell the satellite, close the
        transcript, record the row, say the end line in the room."""
        if sess.state == "ended":
            return
        was_open = sess.state == "open"
        sess.state = "ended"
        sess.reason = reason
        if sess.expiry_task is not None and sess.expiry_task is not asyncio.current_task():
            sess.expiry_task.cancel()
        if sess.ack is not None and not sess.ack.done():
            sess.ack.set_result("ended")
        self.by_id.pop(sess.id, None)
        if self.by_satellite.get(sess.satellite_id) is sess:
            self.by_satellite.pop(sess.satellite_id, None)
        words = REASONS.get(reason, reason)
        if sess.writer is not None:
            sess.writer.note(f"session ended: {words}")
            sess.writer.close()
        try:
            async with session_scope() as s:
                await s.execute(
                    text(
                        "UPDATE support_sessions SET closed_at = now(), end_reason = :r, "
                        "bytes_in = :bi, bytes_out = :bo, truncated = :tr, transcript_bytes = :tb "
                        "WHERE id = :id AND closed_at IS NULL"
                    ),
                    {"r": reason, "bi": sess.bytes_in, "bo": sess.bytes_out, "id": sess.id,
                     "tr": bool(sess.writer and sess.writer.truncated),
                     "tb": sess.writer.bytes if sess.writer else 0},
                )
        except Exception as e:  # noqa: BLE001 - startup cleanup catches what is left
            log.warning("support session %s: couldn't record its end: %s", sess.id[:8], e)
        # Recorded first, then the satellite is told: a send that hangs on
        # a stalled socket can't keep the session looking open. Bounded.
        if sess.sat is not None and reason not in ("satellite_disconnected",):
            try:
                await asyncio.wait_for(
                    sess.sat.send_support({"type": "support_close", "session_id": sess.id,
                                           "reason": reason}), 5.0)
            except Exception:  # noqa: BLE001
                pass
        log.info("support session %s on %s ended: %s%s", sess.id[:8], sess.satellite_id, reason,
                 f" ({detail})" if detail else "")
        if was_open and announce and sess.sat is not None and reason != "satellite_disconnected":
            asyncio.create_task(_say(sess.sat, END_LINE))
        sess.outbox.put_nowait({"t": "end", "reason": reason, "text": words,
                                **({"detail": detail} if detail else {})})
        sess.outbox.put_nowait(None)
        sess.ended.set()

    async def end_by_id(self, session_id: str, reason: str) -> bool:
        sess = self.by_id.get(session_id)
        if sess is None:
            return False
        await self.end(sess, reason)
        return True

    async def end_for_satellite(self, satellite_id: str, reason: str, *, announce: bool = True) -> bool:
        sess = self.by_satellite.get(satellite_id)
        if sess is None:
            return False
        await self.end(sess, reason, announce=announce)
        return True

    async def end_in_room(self, room_id: str | None, reason: str, *, announce: bool = True) -> int:
        """End every session on a satellite in ``room_id`` (by voice): the
        room's devices from the registry (domovoi/room_devices.py), and any
        session recorded in that room."""
        if not room_id:
            return 0
        devices: set[str] = set()
        if self.app is not None:
            from domovoi import room_devices

            try:
                devices = set(room_devices.registry(self.app).devices_in(room_id))
            except Exception as e:  # noqa: BLE001 - fall back to the recorded room
                log.debug("support: room registry unavailable: %s", e)
        hits = [s for s in list(self.by_id.values())
                if s.satellite_id in devices or s.room_id == room_id]
        for sess in hits:
            await self.end(sess, reason, announce=announce)
        return len(hits)

    async def connection_dropped(self, sat: Any) -> None:
        """The satellite's socket closed: its session ends, never resumes."""
        sess = self.by_satellite.get(satellite_of(sat) or "")
        if sess is not None and (sess.sat is sat or sess.sat is None):
            await self.end(sess, "satellite_disconnected", announce=False)

    def open_on(self, *sats: Any) -> bool:
        """Whether a session is open (or opening) on any of these live
        satellite connections (a drop-in call is refused then)."""
        for sat in sats:
            sess = self.by_satellite.get(satellite_of(sat) or "")
            if sess is not None and sess.state in ("pending", "opening", "open"):
                return True
        return False

    def active(self) -> list[dict[str, Any]]:
        return [s.as_active() for s in self.by_id.values() if s.state in ("opening", "open")]


def get_manager(app: Any) -> SupportSessions:
    state = app.state
    mgr = getattr(state, "support_sessions", None)
    if not isinstance(mgr, SupportSessions):
        mgr = SupportSessions(app)
        state.support_sessions = mgr
    return mgr


# ─── helpers ──────────────────────────────────────────────────────────────


def satellite_of(sat: Any) -> str | None:
    """A live session's satellite id (1.5.0 rooms: a device connects by its
    own id, ``StreamSession.satellite_id``; its ``room_id`` is the room)."""
    return getattr(sat, "satellite_id", None) or getattr(sat, "room_id", None)


def live_satellite(app: Any, satellite_id: str) -> Any | None:
    """The live session of the satellite ``satellite_id`` (never another
    device of a room that happens to have that id)."""
    from domovoi import room_devices

    found = room_devices.registry(app).session_for(satellite_id)
    if found is None or satellite_of(found) != satellite_id:
        return None
    return found


def _clamp(value: Any, bounds: tuple[int, int]) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = bounds[0]
    return max(bounds[0], min(bounds[1], v))


def _b64(value: Any, max_bytes: int) -> bytes | None:
    if not isinstance(value, str) or len(value) > (max_bytes * 4) // 3 + 8:
        return None
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data or len(data) > max_bytes:
        return None
    return data


async def _known_secrets() -> tuple[str, ...]:
    found: list[str] = []
    try:
        async with session_scope() as s:
            token = await admin_auth.get_device_token(s)
        if token:
            found.append(token)
    except Exception as e:  # noqa: BLE001
        log.debug("support: couldn't read the device token for redaction: %s", e)
    code = admin_auth.read_setup_code()
    if code:
        found.append(code)
    return tuple(found)


async def _say(sat: Any, words: str, attempts: int = 1) -> bool:
    """One short line in the room, tried up to ``attempts`` times a second
    apart; best effort (a reply may be playing)."""
    for attempt in range(max(1, attempts)):
        try:
            await sat.announce(words, kind="user")
            return True
        except Exception as e:  # noqa: BLE001
            log.info("support: couldn't say %r in %s: %s", words, satellite_of(sat) or "?", e)
            if attempt + 1 < attempts:
                await asyncio.sleep(1.0)
    return False


# ─── the audit list and the files ─────────────────────────────────────────


async def mark_left_open() -> int:
    """At core start: a row still open belongs to a session this process
    never ran, so it ends as "server restarted"."""
    async with session_scope() as s:
        rows = (await s.execute(text(
            "UPDATE support_sessions SET closed_at = now(), end_reason = 'server_restarted' "
            "WHERE closed_at IS NULL RETURNING id"
        ))).all()
    if rows:
        log.info("support sessions: %d left open by the last run marked 'server restarted'", len(rows))
    return len(rows)


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": r.id, "satellite_id": r.satellite_id, "room_id": r.room_id,
        "opener_label": r.opener_label, "opener_hash_prefix": r.opener_hash_prefix,
        "opened_at": r.opened_at.isoformat() if r.opened_at else None,
        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        "end_reason": r.end_reason,
        "end_text": REASONS.get(r.end_reason or "", r.end_reason) if r.end_reason else None,
        "bytes_in": int(r.bytes_in or 0), "bytes_out": int(r.bytes_out or 0),
        "truncated": bool(r.truncated), "transcript_bytes": int(r.transcript_bytes or 0),
        "has_transcript": transcript_path(r.id).exists(),
    }


async def list_sessions(satellite_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (await s.execute(
            text(
                "SELECT * FROM support_sessions "
                "WHERE (CAST(:sat AS text) IS NULL OR satellite_id = :sat) "
                "ORDER BY opened_at DESC LIMIT :lim"
            ),
            {"sat": satellite_id, "lim": int(limit)},
        )).all()
    return [_row(r) for r in rows]


async def get_session(session_id: str) -> dict[str, Any] | None:
    if not valid_id(session_id):
        return None
    async with session_scope() as s:
        r = (await s.execute(text("SELECT * FROM support_sessions WHERE id = :id"),
                             {"id": session_id})).first()
    return _row(r) if r is not None else None


def read_transcript(session_id: str) -> str | None:
    path = transcript_path(session_id)
    try:
        with open(path, "rb") as f:
            return f.read(TRANSCRIPT_MAX_BYTES + 1024).decode("utf-8", errors="replace")
    except OSError:
        return None


async def delete_session(session_id: str) -> bool:
    """Delete a CLOSED session's row and transcript."""
    if not valid_id(session_id):
        return False
    async with session_scope() as s:
        r = (await s.execute(text(
            "DELETE FROM support_sessions WHERE id = :id AND closed_at IS NOT NULL RETURNING id"),
            {"id": session_id})).first()
    if r is None:
        return False    # open (its live transcript stays) or no such row
    _unlink(transcript_path(session_id))
    return True


async def delete_all() -> int:
    async with session_scope() as s:
        rows = (await s.execute(text(
            "DELETE FROM support_sessions WHERE closed_at IS NOT NULL RETURNING id"))).all()
    for r in rows:
        _unlink(transcript_path(r.id))
    return len(rows)


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


async def prune(now: float | None = None) -> dict[str, int]:
    """30 days, then 200 MB in all (oldest first, with room for one more
    session). Rows older than the 30 days go too; a newer row whose file
    went for the space keeps its row (the audit list outlives the file)."""
    now = time.time() if now is None else now
    removed_age = removed_space = 0
    directory = transcript_dir()
    files: list[tuple[float, int, Path]] = []
    if directory.is_dir():
        for p in directory.glob("*.cast"):
            try:
                st = p.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
    open_ids: set[str] = set()
    try:
        async with session_scope() as s:
            await s.execute(text(
                "DELETE FROM support_sessions WHERE closed_at IS NOT NULL "
                "AND opened_at < now() - make_interval(days => :d)"), {"d": int(KEEP_DAYS)})
            open_ids = {r.id for r in (await s.execute(text(
                "SELECT id FROM support_sessions WHERE closed_at IS NULL"))).all()}
    except Exception as e:  # noqa: BLE001 - the files are still pruned by age
        log.debug("support prune: database not reachable: %s", e)
    keep: list[tuple[float, int, Path]] = []
    for mtime, size, p in files:
        if p.stem in open_ids:
            continue
        if now - mtime > KEEP_DAYS * 86400:
            _unlink(p)
            removed_age += 1
        else:
            keep.append((mtime, size, p))
    total = sum(size for _, size, _ in keep) + sum(
        size for _, size, p in files if p.stem in open_ids)
    budget = TRANSCRIPTS_TOTAL_MAX_BYTES - TRANSCRIPT_MAX_BYTES
    for mtime, size, p in sorted(keep):
        if total <= budget:
            break
        _unlink(p)
        total -= size
        removed_space += 1
    return {"removed_age": removed_age, "removed_space": removed_space}
