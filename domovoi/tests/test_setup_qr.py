"""The setup-code QR (1.5.0): domovoi/qr.py, domovoi/setup_qr.py and where
the core prints it.

* the encoder matches an independent one module for module (the same
  reference matrices the Windows tray's encoder is held to);
* the payload is the dashboard's root with the code in the FRAGMENT only:
  ``http://<lan>:6369/#setup-code=<code>``, never a path or a query;
* the QR is printed only where the code already is: under the banner on a
  terminal, never off one;
* no route serves it (the code's readers are the ones there were).

DB-free: the boot hook's "is it claimed?" read is stubbed, as in
test_admin_auth.py.
"""

from __future__ import annotations

import io
import logging
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import domovoi.main as core_main
from domovoi import admin_auth, setup_qr
from domovoi.qr import HIGH, LOW, MEDIUM, QrCode

REPO = Path(__file__).resolve().parents[2]

# Not a real code: a sentinel nothing else in the process would print.
FAKE_CODE = "fake-sentinel-code-never-real-test-only-words-here"
BASE = "http://192.168.1.20:6369"


class _Stdout(io.StringIO):
    """A stdout stand-in: ``isatty`` as told, and an encoding."""

    def __init__(self, tty: bool, encoding: str | None = "utf-8") -> None:
        super().__init__()
        self._tty = tty
        self._enc = encoding

    def isatty(self) -> bool:
        return self._tty

    @property
    def encoding(self):  # type: ignore[override]
        return self._enc


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(setup_qr, "dashboard_base", lambda: BASE)


@pytest.fixture
def _unclaimed_no_db(monkeypatch):
    @asynccontextmanager
    async def _no_db():
        yield None

    async def _no_admin(_session) -> bool:
        return False

    monkeypatch.setattr(admin_auth, "session_scope", _no_db)
    monkeypatch.setattr(admin_auth, "has_admin_auth", _no_admin)
    admin_auth.write_setup_code(FAKE_CODE)


# ── the encoder ──────────────────────────────────────────────────────────

def _reference_cases() -> list[tuple[str, int, int, str]]:
    src = (REPO / "windows" / "tests" / "QrReference.cs").read_text(encoding="utf-8")
    pat = re.compile(r'new\[\] \{ "([^"]*)", "(\d)", "(\d)",\s*"([#.n\\]+)" \}')
    return [(t, int(e), int(m), mat.replace("\\n", "\n")) for t, e, m, mat in pat.findall(src)]


def test_the_encoder_matches_an_independent_one_module_for_module() -> None:
    cases = _reference_cases()
    assert len(cases) >= 10
    for text, ecc, mask, matrix in cases:
        q = QrCode.encode_text(text, ecc, mask, False)
        assert q.text() == matrix, (text[:30], ecc, mask)


def test_the_encoder_picks_a_version_and_boosts_error_correction() -> None:
    q = QrCode.encode_text(BASE, MEDIUM, -1, True)
    assert q.version == 2 and 0 <= q.mask <= 7 and q.size == 25
    assert QrCode.encode_text("a" * 300, LOW, -1, False).version >= 9
    assert QrCode.encode_text("a", MEDIUM, -1, True).ecc == HIGH
    with pytest.raises(ValueError):
        QrCode.encode_text("a" * 3000, LOW, -1, False)


# ── the payload ──────────────────────────────────────────────────────────

def test_the_payload_puts_the_code_in_the_fragment_only() -> None:
    url = setup_qr.claim_url(BASE, FAKE_CODE)
    assert url == f"{BASE}/#setup-code={FAKE_CODE}"
    parts = urlsplit(url)
    assert FAKE_CODE not in parts.path + parts.query + parts.netloc
    assert parts.fragment == f"setup-code={FAKE_CODE}"
    # A trailing slash on the base changes nothing.
    assert setup_qr.claim_url(BASE + "/", FAKE_CODE) == url
    # Spaces (a hand-typed code) are escaped, not a second fragment.
    assert setup_qr.claim_url(BASE, " a b ") == f"{BASE}/#setup-code=a%20b"


@pytest.mark.parametrize("base", [
    "http://192.168.1.20:6369/settings", "http://192.168.1.20:6369/?x=1",
    "http://192.168.1.20:6369/#home", "ftp://192.168.1.20", "192.168.1.20:6369", "",
])
def test_the_payload_refuses_a_base_that_could_carry_the_code_elsewhere(base) -> None:
    with pytest.raises(ValueError):
        setup_qr.claim_url(base, FAKE_CODE)


def test_the_web_port_follows_web_port(monkeypatch) -> None:
    monkeypatch.delenv("WEB_PORT", raising=False)
    assert setup_qr.web_port() == 6369
    monkeypatch.setenv("WEB_PORT", "6390")
    assert setup_qr.web_port() == 6390
    monkeypatch.setenv("WEB_PORT", "nope")
    assert setup_qr.web_port() == 6369


def test_the_terminal_rendering_is_the_symbol_black_on_white() -> None:
    q = QrCode.encode_text(setup_qr.claim_url(BASE, FAKE_CODE))
    lines = setup_qr.render_terminal(q).split("\n")
    quiet = setup_qr.QUIET_ZONE
    side = q.size + 2 * quiet
    assert len(lines) == (side + 1) // 2
    for i, line in enumerate(lines):
        assert line.startswith("\x1b[30;47m") and line.endswith("\x1b[0m")
        body = line[len("\x1b[30;47m"):-len("\x1b[0m")]
        assert len(body) == side
        for j, ch in enumerate(body):
            x, y = j - quiet, 2 * i - quiet
            top, bottom = q.dark(x, y), q.dark(x, y + 1)
            assert ch == {(True, True): "█", (True, False): "▀",
                          (False, True): "▄", (False, False): " "}[(top, bottom)]


def test_no_qr_where_it_cannot_be_drawn_or_reached(monkeypatch) -> None:
    assert setup_qr.terminal_block(FAKE_CODE, stream=_Stdout(True, "cp1252")) is None
    assert setup_qr.terminal_block(FAKE_CODE, stream=_Stdout(True, None)) is None
    monkeypatch.setattr(setup_qr, "dashboard_base", lambda: None)
    assert setup_qr.terminal_block(FAKE_CODE, stream=_Stdout(True)) is None


# ── shown only where the code already is ─────────────────────────────────

@pytest.mark.asyncio
async def test_the_banner_on_a_terminal_carries_the_qr(_unclaimed_no_db, monkeypatch) -> None:
    out = _Stdout(tty=True)
    monkeypatch.setattr(sys, "stdout", out)
    assert await admin_auth.ensure_setup_code_if_unclaimed() == FAKE_CODE
    said = out.getvalue()
    assert f"Setup code: {FAKE_CODE}" in said
    q = QrCode.encode_text(setup_qr.claim_url(BASE, FAKE_CODE))
    assert setup_qr.render_terminal(q) in said
    assert f"dashboard at {BASE} with the setup code filled in" in said


@pytest.mark.asyncio
async def test_off_a_terminal_there_is_no_qr_and_no_code(_unclaimed_no_db, monkeypatch, caplog, capfd) -> None:
    out = _Stdout(tty=False)
    monkeypatch.setattr(sys, "stdout", out)
    with caplog.at_level(logging.DEBUG):
        assert await admin_auth.ensure_setup_code_if_unclaimed() == FAKE_CODE
    assert out.getvalue() == ""
    assert FAKE_CODE not in caplog.text and "█" not in caplog.text
    fd_out, fd_err = capfd.readouterr()
    assert FAKE_CODE not in fd_out + fd_err and "█" not in fd_out + fd_err


def test_reset_admin_prints_the_qr_on_a_terminal_only(monkeypatch) -> None:
    async def _fake_reset() -> str:
        return FAKE_CODE

    monkeypatch.setattr(admin_auth, "reset_admin", _fake_reset)
    on_tty = _Stdout(tty=True)
    monkeypatch.setattr(sys, "stdout", on_tty)
    core_main._reset_admin_cli()
    assert "█" in on_tty.getvalue() and f"new setup code: {FAKE_CODE}" in on_tty.getvalue()
    redirected = _Stdout(tty=False)
    monkeypatch.setattr(sys, "stdout", redirected)
    core_main._reset_admin_cli()
    assert FAKE_CODE not in redirected.getvalue() and "█" not in redirected.getvalue()


def test_the_command_shows_it_on_a_terminal_only(monkeypatch) -> None:
    admin_auth.write_setup_code(FAKE_CODE)
    redirected = _Stdout(tty=False)
    monkeypatch.setattr(sys, "stdout", redirected)
    assert setup_qr.main([]) == 2
    assert FAKE_CODE not in redirected.getvalue() and "█" not in redirected.getvalue()
    on_tty = _Stdout(tty=True)
    monkeypatch.setattr(sys, "stdout", on_tty)
    assert setup_qr.main([]) == 0
    assert f"Setup code: {FAKE_CODE}" in on_tty.getvalue() and "█" in on_tty.getvalue()


def test_the_command_says_so_when_there_is_no_code(monkeypatch) -> None:
    admin_auth.delete_setup_code()
    on_tty = _Stdout(tty=True)
    monkeypatch.setattr(sys, "stdout", on_tty)
    assert setup_qr.main([]) == 1
    assert "there is no setup code" in on_tty.getvalue()


# ── no route serves it ───────────────────────────────────────────────────

def test_no_route_serves_the_code_or_its_qr() -> None:
    """The QR module is used by the core's terminal output only, never by a
    route module of either process; and the code's readers are the ones
    there were before the QR (the claim's verify, the boot banner, and the
    two redactors that blank it)."""
    importers = sorted(
        str(p.relative_to(REPO)).replace("\\", "/")
        for root in ("domovoi", "web")
        for p in (REPO / root).rglob("*.py")
        if "tests" not in p.parts and re.search(
            r"^\s*(from domovoi import .*\bsetup_qr\b|import domovoi\.setup_qr\b|from domovoi\.setup_qr import)",
            p.read_text(encoding="utf-8", errors="replace"), re.M)
    )
    assert importers == ["domovoi/admin_auth.py"], importers
    readers = sorted(
        str(p.relative_to(REPO)).replace("\\", "/")
        for root in ("domovoi", "web")
        for p in (REPO / root).rglob("*.py")
        if "tests" not in p.parts and "read_setup_code" in p.read_text(encoding="utf-8", errors="replace")
    )
    assert readers == ["domovoi/admin_auth.py", "domovoi/help_desk/known.py",
                       "domovoi/setup_qr.py", "domovoi/support_sessions.py"], readers
    # And no route of either app is about it.
    from domovoi.main import app as core_app
    from web.backend.main import app as web_app

    for app in (core_app, web_app):
        for r in app.routes:
            path = getattr(r, "path", "")
            assert "qr" not in path.lower() or "setup" not in path.lower(), path
