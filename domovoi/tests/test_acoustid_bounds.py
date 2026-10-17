"""AcoustID's answers are bounded (review of wf/acoustid-key, 2026-10-17,
notes N-A1 and N-A2).

* Every AcoustID request — the sweep's and "test the key" — goes through
  ``library_enricher._bounded_api_request`` (pyacoustid's own
  ``_api_request`` replaced, behind its rate limiter): a deadline for the
  whole answer, at most 1 MiB of it once un-gzipped, no redirect followed
  (a 307 would post the key again, elsewhere). The sweep's lookup now has
  a timeout too: before, one hung lookup held pyacoustid's rate-limit lock
  and "test the key" with it.
* A typed key over 256 characters is refused without being quoted back
  (pydantic's 422 carried it in ``input``).

A local stand-in plays api.acoustid.org: nothing leaves the machine.
"""

from __future__ import annotations

import gzip
import http.server
import json
import threading
import time
from unittest.mock import patch

import pytest

from domovoi.config import settings
from domovoi.tests.auth_testkit import bearer
from domovoi.tests.test_acoustid_key_setting import (  # noqa: F401 — fixtures
    KEY,
    OK,
    REFUSED,
    TOKEN,
    Lookups,
    _keep_acoustid_address,
    _test_key,
    _web,
    box,
)
from domovoi.workers import library_enricher as le


class _StandIn:
    """api.acoustid.org on loopback: one path per behaviour; records the
    paths it was posted to."""

    def __init__(self) -> None:
        hits: list[str] = []
        self.hits = hits

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _send(self, code, body, encoding=None):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                if encoding:
                    self.send_header("Content-Encoding", encoding)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                hits.append(self.path)
                kind = self.path.strip("/").split("/")[0]
                if kind == "redirect":
                    self.send_response(307)
                    self.send_header("Location", "/target/lookup")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif kind == "big":
                    self._send(200, b'{"status": "ok", "pad": "' + b"x" * (2 * 1024 * 1024) + b'"}')
                elif kind == "bomb":
                    self._send(200, gzip.compress(b" " * (8 * 1024 * 1024)), encoding="gzip")
                elif kind == "drip":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    try:
                        for _ in range(100):
                            self.wfile.write(b" ")
                            self.wfile.flush()
                            time.sleep(0.1)
                    except OSError:
                        pass
                elif kind == "error":
                    self._send(400, json.dumps(REFUSED).encode())
                else:
                    self._send(200, json.dumps(OK).encode())

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stand_in():
    s = _StandIn()
    yield s
    s.close()


def _post(stand_in, kind: str, timeout: float = 5.0):
    return le._bounded_api_request(f"{stand_in.base}/{kind}/lookup",
                                   {"client": KEY, "meta": ["recordingids"]}, timeout)


def test_an_answer_is_read_and_an_error_answer_keeps_its_code(stand_in) -> None:
    assert _post(stand_in, "ok") == OK
    assert _post(stand_in, "error") == REFUSED     # AcoustID's 400 comes back as data, as in pyacoustid


def test_a_redirect_is_not_followed(stand_in) -> None:
    import acoustid

    with pytest.raises(acoustid.WebServiceError) as e:
        _post(stand_in, "redirect")
    assert "redirect" in str(e.value) and KEY not in str(e.value)
    assert stand_in.hits == ["/redirect/lookup"]   # the key was not posted again


@pytest.mark.parametrize("kind", ["big", "bomb"])
def test_an_answer_over_a_mebibyte_is_cut_off(stand_in, kind) -> None:
    import acoustid

    with pytest.raises(acoustid.WebServiceError, match="too large"):
        _post(stand_in, kind)


def test_a_slow_drip_ends_at_the_deadline(stand_in) -> None:
    import acoustid

    started = time.monotonic()
    with pytest.raises(acoustid.WebServiceError, match="to answer"):
        _post(stand_in, "drip", timeout=1.0)
    assert time.monotonic() - started < 3.0


def test_pyacoustid_goes_through_the_bounds_behind_its_rate_limit(stand_in, monkeypatch) -> None:
    import acoustid as module

    monkeypatch.setattr(module, "_api_request", module._api_request)   # put back afterwards
    acoustid = le._acoustid()
    wrapped = acoustid._api_request
    assert getattr(wrapped, "_domovoi_bounded", False) is True
    assert isinstance(wrapped, module._rate_limit)                     # pyacoustid's limiter kept
    assert le._acoustid()._api_request is wrapped                       # wrapped once
    monkeypatch.setattr(acoustid, "API_BASE_URL", f"{stand_in.base}/redirect/")
    with pytest.raises(acoustid.WebServiceError, match="redirect"):
        acoustid.lookup(KEY, le.KEY_CHECK_FINGERPRINT, 30, ["recordingids"], 5.0)
    monkeypatch.setattr(acoustid, "API_BASE_URL", f"{stand_in.base}/error/")
    assert acoustid.lookup(KEY, le.KEY_CHECK_FINGERPRINT, 30, ["recordingids"], 5.0) == REFUSED


@pytest.mark.asyncio
async def test_the_sweep_lookup_has_a_timeout(box, monkeypatch, tmp_path) -> None:  # noqa: F811
    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    seen: list[dict] = []

    def match(_key, _path, **kw):
        seen.append(kw)
        return OK

    with patch("acoustid.match", match):
        await le._enrich_via_acoustid(tmp_path / "x.mp3")
    assert seen == [{"parse": False, "timeout": le.ACOUSTID_TIMEOUT_SEC}]
    assert le.KEY_CHECK_TIMEOUT_SEC == le.ACOUSTID_TIMEOUT_SEC < 30    # under the web hop's 30 s


@pytest.mark.asyncio
async def test_a_typed_key_too_long_is_refused_without_quoting_it(box, monkeypatch) -> None:  # noqa: F811
    import web.backend.api.config as web_config

    long_key = "Q7" * 150                                  # 300 characters
    sent: list[str] = []

    async def to_core(path, body=None, timeout=30.0, headers=None):
        sent.append(path)
        return 200, {}

    monkeypatch.setattr(web_config, "post_admin", to_core)
    fake = Lookups(OK)
    with patch("acoustid.lookup", fake):
        core = await _test_key({"key": long_key})
        async with _web() as c:
            web = await c.post("/api/config/acoustid-test", json={"key": long_key},
                               headers=bearer(TOKEN))
    for r in (core, web):
        assert r.status_code == 422
        assert "Q7Q7Q7" not in r.text
        assert "256 characters" in r.text
    assert fake.keys == [] and sent == []
