"""``GET /api/health`` passes the core's identity through, challenge and all.

The Android app has to tell the server it paired with from anything else
answering at its saved address after a network change (security round 3,
A6-03). The core already signs a caller's nonce on ``/v1/health?challenge=``
(domovoi/server_identity.py); the web's health probe fetched that same URL
and kept only ``stt``. It now forwards the ``identity`` block verbatim and
forwards a ``?challenge=`` of its own caller to the core, so a phone that
can only see :6369 gets the core's signature over ITS nonce.

Open, like the rest of the answer: every field is public, and the phone
asking has deliberately not sent its token yet.

The core hop is replaced by a scripted client (the handler builds its own
``httpx.AsyncClient``); no core process, no ``requires_db``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from web.backend.main import app as web_app
from web.backend.main import core_identity_block, health_challenge_ok

IDENTITY = {
    "algorithm": "ed25519",
    "fingerprint": "SHA256:If4x36FUomFia/hUBG/SJxt77UtqvkWqWId+9H+XIbk",
    "public_key": "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=",
}


class _Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _scripted_core(monkeypatch, answer) -> list[str]:
    """Replace the handler's client with one that answers ``answer`` (a
    ``_Resp``, a callable of the URL returning one, or an exception)."""
    seen: list[str] = []

    class _Client:
        def __init__(self, **_kw) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            seen.append(url)
            if isinstance(answer, Exception):
                raise answer
            if callable(answer):
                return answer(url)
            return answer

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return seen


async def _health(monkeypatch, answer, query: str = "") -> tuple[int, Any, list[str]]:
    client = AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test")
    seen = _scripted_core(monkeypatch, answer)   # after the test's own client exists
    async with client:
        r = await client.get("/api/health" + query)
    return r.status_code, r.json(), seen


@pytest.mark.asyncio
async def test_the_core_identity_is_passed_through_verbatim(monkeypatch) -> None:
    status, body, seen = await _health(
        monkeypatch, _Resp(200, {"status": "ok", "stt": "ok", "identity": IDENTITY})
    )
    assert status == 200
    assert body["identity"] == IDENTITY
    assert body["stt"] == "ok"
    assert seen == [seen[0]] and seen[0].endswith("/v1/health")


@pytest.mark.asyncio
async def test_a_challenge_is_forwarded_to_the_core_and_its_signature_comes_back(monkeypatch) -> None:
    challenge = "0123456789abcdef0123456789abcdef"

    def core(url: str) -> _Resp:
        assert url.endswith(f"/v1/health?challenge={challenge}"), url
        return _Resp(200, {
            "status": "ok",
            "identity": {**IDENTITY, "challenge": challenge, "signature": "c2ln"},
        })

    status, body, seen = await _health(monkeypatch, core, f"?challenge={challenge}")
    assert status == 200
    assert body["identity"]["challenge"] == challenge
    assert body["identity"]["signature"] == "c2ln"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_challenge_reaches_the_core_verbatim(monkeypatch) -> None:
    # Every character the alphabet allows, once: the hop's URL carries the
    # nonce as sent, so the signature the core returns is over that nonce.
    challenge = "aZ09._~-"
    seen_urls: list[str] = []

    def core(url: str) -> _Resp:
        seen_urls.append(url)
        return _Resp(200, {"status": "ok"})

    status, _, _ = await _health(monkeypatch, core, f"?challenge={challenge}")
    assert status == 200
    assert seen_urls == [seen_urls[0]] and seen_urls[0].endswith(f"/v1/health?challenge={challenge}")


@pytest.mark.parametrize(
    "challenge",
    ["x" * 129, "has space", "café", "", "a&b=c", "a/b", "a\nb"],
    ids=["too-long", "space", "non-ascii", "empty", "query-chars", "slash", "newline"],
)
@pytest.mark.asyncio
async def test_a_challenge_the_core_would_refuse_is_refused_here_first(monkeypatch, challenge) -> None:
    from urllib.parse import quote

    status, body, seen = await _health(
        monkeypatch, _Resp(200, {"status": "ok"}), f"?challenge={quote(challenge, safe='')}"
    )
    assert status == 400, body
    assert seen == [], "nothing was asked of the core"


def test_the_challenge_rule_matches_the_core_cap_and_alphabet() -> None:
    # The core signs only URL-safe characters (domovoi.server_identity's
    # challenge rule after security round 3): the hop refuses the rest
    # before asking, so a refusal never looks like "the core is down".
    assert health_challenge_ok("0123456789abcdef0123456789abcdef")
    assert health_challenge_ok("aZ09._~-")
    assert health_challenge_ok("x" * 128)
    assert not health_challenge_ok("x" * 129)
    assert not health_challenge_ok("")
    assert not health_challenge_ok("a b")
    assert not health_challenge_ok("a\tb")
    assert not health_challenge_ok("a\nb")
    assert not health_challenge_ok("a&b")
    assert not health_challenge_ok("a/b")
    assert not health_challenge_ok("a%20b")


@pytest.mark.parametrize(
    "answer",
    [
        _Resp(200, {"status": "ok", "stt": "ok"}),            # a core from before identity
        _Resp(200, {"status": "ok", "identity": "SHA256:x"}),  # not a block
        _Resp(200, {"status": "ok", "identity": {}}),          # an empty block
        _Resp(200, {"status": "ok", "identity": {"n": 3}}),    # nothing a client can use
        _Resp(503, {"detail": "db unreachable"}),               # core up, not healthy
        httpx.ConnectError("refused"),                          # core down
    ],
    ids=["older-core", "not-a-dict", "empty", "no-strings", "core-503", "core-down"],
)
@pytest.mark.asyncio
async def test_no_identity_is_null_never_a_guess(monkeypatch, answer) -> None:
    status, body, _ = await _health(monkeypatch, answer)
    assert status == 200
    assert "identity" in body
    assert body["identity"] is None


def test_only_the_string_fields_of_the_block_are_forwarded() -> None:
    assert core_identity_block({**IDENTITY, "extra": 1, 2: "x"}) == IDENTITY
    assert core_identity_block(None) is None
    assert core_identity_block([IDENTITY]) is None
    assert core_identity_block({}) is None
