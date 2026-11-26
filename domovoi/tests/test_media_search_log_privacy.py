"""Photo and video search words never land in a log (1.5.0, design
MEDIA-UNDERSTANDING §8: "query words are not logged").

* the access log's request line for ``/api/media/search`` has ``q``
  blanked at EVERY level, DEBUG included (domovoi/log_privacy.py);
* a failed search, in the web route, the chat and the voice handler, logs
  only the kind of error, never the database error's text (which carries
  the words as its parameters);
* the voice turn itself is still recorded in conversation history, as
  every turn is (router._persist_turn): that is not a log line.
"""

from __future__ import annotations

import logging

import pytest

from domovoi import log_privacy
from domovoi.log_privacy import WORDS_MASK, scrub
from domovoi.media_understanding import ask

SECRET = "grandmas-surprise-party"


def _access_record(path: str) -> logging.LogRecord:
    log_privacy.install()
    return logging.getLogRecordFactory()(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5", "GET", path, "1.1", 200), None,
    )


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_the_access_log_never_carries_the_search_words(level: int) -> None:
    root = logging.getLogger()
    old = root.level
    root.setLevel(level)
    try:
        for path in (f"/api/media/search?q={SECRET}&limit=20",
                     f"/api/media/search?kind=image&q={SECRET}&cursor=40",
                     f"/api/media/search?q={SECRET}"):
            made = _access_record(path)
            from uvicorn.logging import AccessFormatter

            out = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s',
                                  use_colors=False).format(made)
            assert SECRET not in out and f"q={WORDS_MASK}" in out, out
            assert "/api/media/search?" in out
        # Other parameters stay; other routes keep the DEBUG escape hatch.
        assert "kind=image" in scrub(f"/api/media/search?kind=image&q={SECRET}")
        other = scrub(f"/api/lookups?q={SECRET}")
        assert (SECRET in other) == (level == logging.DEBUG)
    finally:
        root.setLevel(old)


class _Boom(Exception):
    pass


class _BadSession:
    """A session whose every query fails with the words in its text, as a
    database error's does."""

    async def execute(self, *a, **kw):
        raise _Boom(f"[SQL: SELECT ...] [parameters: {{'q': '{SECRET}'}}]")

    async def rollback(self):
        return None


async def test_a_failed_search_raises_without_the_words(caplog) -> None:
    with pytest.raises(ask.SearchFailed) as e:
        await ask.find(_BadSession(), f"photos of {SECRET}", people=True)
    assert SECRET not in str(e.value) and e.value.__suppress_context__
    import traceback

    assert SECRET not in "".join(traceback.format_exception(e.value))


async def test_the_voice_handler_says_so_and_logs_no_words(caplog) -> None:
    from domovoi.handlers import HANDLER_BY_NAME
    from domovoi.models import Context

    caplog.set_level(logging.DEBUG)
    h = HANDLER_BY_NAME["media_search"]
    r = await h.execute_from_tool({"query": f"photos of {SECRET}"}, Context(room_id="kitchen"), _BadSession())
    assert r.text == ask.FAILED_TEXT
    assert all(SECRET not in rec.getMessage() and SECRET not in (rec.exc_text or "") for rec in caplog.records)


async def test_the_web_route_and_the_chat_log_no_words(caplog, monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from web.backend.api import chat_media, media_search

    @asynccontextmanager
    async def bad_scope():
        yield _BadSession()

    monkeypatch.setattr(media_search, "session_scope", bad_scope)
    monkeypatch.setattr(chat_media, "session_scope", bad_scope)

    async def no_shared(request):
        return False

    monkeypatch.setattr(chat_media, "shared_screen", no_shared)

    async def no_shared_route(request, s):
        return False

    monkeypatch.setattr(media_search, "_shared_screen", no_shared_route)
    caplog.set_level(logging.DEBUG)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        await media_search.media_search(request=None, q=f"photos of {SECRET}", kind=None, date_from=None,
                                        date_to=None, limit=20, offset=0, cursor=None)
    assert e.value.status_code == 500 and SECRET not in str(e.value.detail)
    turn = await chat_media.answer(f"photos of {SECRET}", None)
    assert turn.text == ask.FAILED_TEXT and turn.media["items"] == []
    assert caplog.records, "the failures are logged (by kind)"
    assert all(SECRET not in rec.getMessage() and SECRET not in (rec.exc_text or "") for rec in caplog.records)
