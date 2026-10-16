"""S5 (delta security review 2026-10-16): the server's journal, kept on disk,
must hold neither the household token nor what people said.

* Credentials: both processes install :func:`domovoi.log_privacy.install`
  at import, so every record has ``…token=`` values (and a Bearer) blanked
  as it is made. The review's proof is repeated through a real uvicorn
  server with the web's own logging: ``?device_token=`` used to reach the
  access log verbatim.
* Spoken words: a log line at INFO or above names what somebody said only
  through :func:`domovoi.log_privacy.said`, which shows the words at DEBUG
  and a word count otherwise. A source scan keeps new lines from logging a
  transcript bare.
"""

from __future__ import annotations

import ast
import io
import logging
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from domovoi import log_privacy
from domovoi.log_privacy import MASK, said, scrub

REPO = Path(__file__).resolve().parents[2]
TOKEN = "hh-SECRET-TOKEN-1234"


# ─── scrub(): what is blanked, what is left alone ─────────────────────────


@pytest.mark.parametrize(
    ("line", "kept"),
    [
        (f'"GET /api/files/raw?library_id=core:documents&path=a.pdf&device_token={TOKEN} HTTP/1.1" 401',
         '"GET /api/files/raw?library_id=core:documents&path=a.pdf&device_token=*** HTTP/1.1" 401'),
        (f'127.0.0.1:5 - "WebSocket /ws/state?token={TOKEN}" [accepted]',
         '127.0.0.1:5 - "WebSocket /ws/state?token=***" [accepted]'),
        (f"GET /api/videos/stream/7?device_token={TOKEN}&start=10",
         "GET /api/videos/stream/7?device_token=***&start=10"),
        (f"HTTP Request: GET http://127.0.0.1:6370/v1/x?access_token={TOKEN}#frag",
         "HTTP Request: GET http://127.0.0.1:6370/v1/x?access_token=***#frag"),
        (f"auth: Authorization: Bearer {TOKEN}", "auth: Authorization: Bearer ***"),
        (f"pairing_token={TOKEN}, room=kitchen", "pairing_token=***, room=kitchen"),
        (f"client_secret={TOKEN} password=hunter22 api_key=abc123 sig=deadbeef",
         "client_secret=*** password=*** api_key=*** sig=***"),
    ],
)
def test_credential_values_are_blanked_and_names_kept(line: str, kept: str) -> None:
    assert scrub(line) == kept
    assert TOKEN not in scrub(line)


@pytest.mark.parametrize(
    "line",
    [
        "exit returncode=1 tokens_used=512 key=OLLAMA_MODEL",
        "GET /api/music/library?limit=1&cursor=abc HTTP/1.1",
        "the bearer of bad news",
        "no equals sign here at all",
        "",
    ],
)
def test_ordinary_lines_are_left_alone(line: str) -> None:
    assert scrub(line) == line


def _record(msg, args, *, name="t", exc_info=None) -> logging.LogRecord:
    log_privacy.install()
    return logging.getLogger(name).makeRecord(name, logging.INFO, __file__, 1, msg, args, exc_info)


def test_the_record_factory_scrubs_arguments_and_keeps_their_shape() -> None:
    """uvicorn's AccessFormatter unpacks record.args into five fields, so
    the scrub replaces strings with strings and leaves the rest alone."""
    log_privacy.install()
    rec = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5", "GET", f"/api/files/raw?device_token={TOKEN}", "1.1", 401), None,
    )
    # LogRecord() itself does not run the factory; logging.makeRecord does.
    made = logging.getLogRecordFactory()(
        "uvicorn.access", logging.INFO, __file__, 1, rec.msg, rec.args, None,
    )
    assert isinstance(made.args, tuple) and len(made.args) == 5
    assert made.args[4] == 401
    assert TOKEN not in made.getMessage()
    assert "device_token=***" in made.getMessage()

    from uvicorn.logging import AccessFormatter

    out = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(made)
    assert TOKEN not in out and "device_token=***" in out


def test_url_objects_exceptions_and_tracebacks_are_scrubbed() -> None:
    import httpx

    url = httpx.URL(f"http://127.0.0.1:6370/v1/a?device_token={TOKEN}")
    rec = _record("HTTP Request: %s %s", ("GET", url))
    assert TOKEN not in rec.getMessage()

    err = RuntimeError(f"failed for url 'http://x/y?token={TOKEN}'")
    rec = _record("hop failed: %s", (err,))
    assert TOKEN not in rec.getMessage()

    try:
        raise ValueError(f"bad url http://x/y?device_token={TOKEN}")
    except ValueError:
        import sys

        rec = _record("boom", None, exc_info=sys.exc_info())
    text = logging.Formatter().format(rec)
    assert TOKEN not in text and "device_token=***" in text


def test_a_credential_passed_as_an_argument_after_its_name_is_blanked_too() -> None:
    """``log.info("pairing_token=%s", tok)``: the format string's
    placeholder is not blanked (that would break the formatting and lose
    the line); the formatted line is."""
    rec = _record("pairing_token=%s room=%s", (TOKEN, "kitchen"))
    assert rec.getMessage() == "pairing_token=*** room=kitchen"
    rec = _record("GET %s", (f"/a?device_token={TOKEN}&b=%2F",))
    assert rec.getMessage() == "GET /a?device_token=***&b=%2F"


def test_install_is_idempotent() -> None:
    log_privacy.install()
    first = logging.getLogRecordFactory()
    log_privacy.install()
    assert logging.getLogRecordFactory() is first


@pytest.mark.parametrize("module", ["domovoi.main", "web.backend.main"])
def test_both_processes_install_it_at_import(module: str) -> None:
    import importlib

    importlib.import_module(module)
    assert getattr(logging.getLogRecordFactory(), "_domovoi_log_scrub", False)
    rec = logging.getLogger("uvicorn.error").makeRecord(
        "uvicorn.error", logging.INFO, __file__, 1, '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:5", f"/ws/state?token={TOKEN}"), None,
    )
    assert TOKEN not in rec.getMessage()


def test_the_reviewers_proof_a_real_uvicorn_access_log_carries_no_token() -> None:
    """proof_access_log_token.py, as a regression: uvicorn's own logging
    config, the web's filters and factory, a request carrying the household
    token. It used to print ``TOKEN IN LOG: True``."""
    import logging.config

    import uvicorn
    from uvicorn.config import LOGGING_CONFIG

    logging.config.dictConfig(LOGGING_CONFIG)   # what uvicorn.run() installs
    import web.backend.main  # noqa: F401 — the web's filters and the factory

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    access = logging.getLogger("uvicorn.access")
    access.addHandler(handler)

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b"no"})

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=True))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/files/raw?library_id=core:documents&path=a.pdf"
                f"&device_token={TOKEN}",
                timeout=5,
            )
        except Exception:
            pass   # 401 is the answer; the log line is the point
        for _ in range(40):
            if "/api/files/raw" in buf.getvalue():
                break
            time.sleep(0.05)
    finally:
        server.should_exit = True
        t.join(timeout=5)
        access.removeHandler(handler)
    out = buf.getvalue()
    assert "/api/files/raw" in out, out
    assert TOKEN not in out
    assert f"device_token={MASK}" in out


# ─── said(): words at DEBUG only ──────────────────────────────────────────


def test_said_shows_a_word_count_at_info_and_the_words_at_debug(caplog) -> None:
    caplog.set_level(logging.INFO)
    log = logging.getLogger("domovoi.router")
    log.info("fast path %s declined %r; routing on", "music", said("what else is the moon called"))
    assert caplog.records[-1].getMessage() == "fast path music declined <6 words>; routing on"
    assert repr(said("one")) == "<1 word>" and str(said(None)) == "<0 words>"
    caplog.set_level(logging.DEBUG)
    log.info("fast path %s declined %r; routing on", "music", said("what else is the moon called"))
    assert caplog.records[-1].getMessage() == "fast path music declined 'what else is the moon called'; routing on"


#: Names that hold what somebody said, or what they were answered.
_SAID_NAMES = frozenset({
    "transcript", "_transcript", "heard", "cleaned", "lane_text", "candidate_text",
    "user_text", "assistant_text", "spoken", "_last_spoken_text", "q_display",
})
_LEVELS = {"info", "warning", "error", "exception", "critical"}


def _bare_said_names(node: ast.AST) -> list[str]:
    """Names in ``_SAID_NAMES`` used in a log argument outside a said() call."""
    found: list[str] = []

    def walk(n: ast.AST) -> None:
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("said", "_heard_for_log", "len"):
            return
        if isinstance(n, ast.Compare):
            return   # `x if request != transcript else ""`: compared, not written
        if isinstance(n, ast.Name) and n.id in _SAID_NAMES:
            found.append(n.id)
        elif isinstance(n, ast.Attribute) and n.attr in _SAID_NAMES:
            found.append(n.attr)
            return
        for child in ast.iter_child_nodes(n):
            walk(child)

    walk(node)
    return found


def test_no_log_line_at_info_or_above_writes_a_transcript_bare() -> None:
    """The review named router.py:797/:857, streaming.py:3770,
    music_rooms.py:320/:520, music_choice.py:638 and searxng.py:171/:174;
    the sweep found the rest. A transcript-shaped argument of a log call at
    INFO or above must go through said()."""
    offenders: list[str] = []
    roots = [REPO / "domovoi", REPO / "web" / "backend"]
    for root in roots:
        for path in root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr not in _LEVELS:
                    continue
                base = node.func.value
                if not (isinstance(base, ast.Name) and base.id in ("log", "logger", "_log")):
                    continue
                bare = [n for a in node.args[1:] for n in _bare_said_names(a)]
                fmt = node.args[0] if node.args else None
                if isinstance(fmt, ast.JoinedStr):
                    bare += _bare_said_names(fmt)
                if bare:
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno} {sorted(set(bare))}")
    assert offenders == [], "log what was said through log_privacy.said():\n" + "\n".join(offenders)
