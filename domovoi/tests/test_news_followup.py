"""Spoken news a listener can follow, and "tell me more about the second one".

The owner, after using it live (2026-11-17): the headlines ran together as
"one long continued ramble", and he wanted to ask more about one story at
the end. So:

* every read-out says each headline as a sentence of its own, numbered
  straight through, one paragraph per story (a blank line, which the speech
  says as a longer silence: tts.PARAGRAPH_SILENCE_SEC), the scope labels
  sentences of their own;
* the stories read are kept in order in the session (``news_read``), and for
  ten minutes "tell me more about the second one" / "the last one" /
  "number three" / "the one about …" / "more on that" says that story.

Non-DB: the reference parsing, the speech pipeline's paragraph gap.
DB-gated (@requires_db): the read-outs and the follow-ups through route().
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import text

from domovoi import egress
from domovoi.config import settings
from domovoi.handlers.news import (
    _FOLLOWUP_RE,
    _as_sentence,
    _match_title,
    _ordinal_word,
    _parse_ref,
)
from domovoi.models import Context, Intent
from domovoi.router import route
from domovoi.tests.conftest import requires_db


# ─── Reference parsing ──────────────────────────────────────────────────


def _ref(said: str) -> str:
    m = _FOLLOWUP_RE.match(said)
    assert m is not None, said
    return next((g for g in m.groups() if g is not None), "")


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("tell me more about the second one", ("index", 2)),
        ("tell me more about number three", ("index", 3)),
        ("number three", ("index", 3)),
        ("the second one", ("index", 2)),
        ("what's the second one about", ("index", 2)),
        ("what about the 3rd story", ("index", 3)),
        ("more about story 2", ("index", 2)),
        ("tell me more about the last one", ("last", None)),
        ("the last one", ("last", None)),
        ("more on that", ("current", None)),
        ("tell me more", ("current", None)),
        ("tell me more about it", ("current", None)),
        ("tell me a bit more about that story", ("current", None)),
        ("the one about the hot tub recall", ("words", ("the hot tub recall", True))),
        ("tell me more about the story about the bridge", ("words", ("the bridge", True))),
        ("tell me more about the hot tub recall", ("words", ("the hot tub recall", False))),
    ],
)
def test_followup_phrasings_parse(said: str, expected) -> None:
    assert _parse_ref(_ref(said)) == expected


@pytest.mark.parametrize(
    "said",
    ["tell me about the beatles", "tell me a story", "play the second one",
     "more", "what's the news", "tell me about rome"],
)
def test_followup_does_not_take_other_phrasings(said: str) -> None:
    assert _FOLLOWUP_RE.match(said) is None


def test_match_title_by_words() -> None:
    titles = ["County bridge closes for repairs", "Hot tub maker recalls heaters",
              "Storm moves up the coast"]
    assert _match_title("the hot tub recall", titles) == 1
    assert _match_title("the storm", titles) == 2
    assert _match_title("the price of eggs", titles) is None


def test_sentences_and_ordinals() -> None:
    assert _as_sentence("Council votes on budget") == "Council votes on budget."
    assert _as_sentence("Will it rain?") == "Will it rain?"
    assert _as_sentence("Bridge closes:") == "Bridge closes."
    assert _ordinal_word(1) == "First" and _ordinal_word(9) == "Ninth"
    assert _ordinal_word(25) == "Number 25"


# ─── The paragraph gap in the speech ────────────────────────────────────


def test_sanitizer_keeps_paragraph_breaks_only() -> None:
    from domovoi.speech_sanitize import sanitize_for_speech

    said = sanitize_for_speech("Here's the news.\n\nFirst, **A**.\n  \nSecond,\nB.")
    assert said == "Here's the news.\n\nFirst, A.\n\nSecond, B."


def test_piper_puts_a_longer_silence_between_paragraphs() -> None:
    from domovoi.clients import tts

    sr = 1000
    fake = SimpleNamespace(
        np=np, sample_rate=sr,
        sentence_audio=lambda ph, speed: np.ones(10, dtype=np.float32),
    )
    pcm = tts.PiperVoice.synthesize_pcm(fake, ["a", "b", "c"], 1.0, paragraph_starts=frozenset({2}))
    samples = np.frombuffer(pcm, dtype=np.int16)
    gap = int(tts.SENTENCE_SILENCE_SEC * sr)
    long_gap = int(tts.PARAGRAPH_SILENCE_SEC * sr)
    assert len(samples) == 30 + gap + long_gap
    assert tts.PARAGRAPH_SILENCE_SEC > tts.SENTENCE_SILENCE_SEC
    # Without paragraphs: the old shape, unchanged.
    assert len(tts.PiperVoice.synthesize_pcm(fake, ["a", "b"], 1.0)) == 2 * (20 + gap)


def test_satellite_speech_marks_paragraph_ends() -> None:
    from domovoi.streaming import _paragraph_silence, _spoken_parts

    sentences, ends = _spoken_parts("Here's the news.\n\nLocally. First, A.\n\nSecond, B.")
    assert sentences == ["Here's the news.", "Locally.", "First, A.", "Second, B."]
    assert ends == {0, 2}
    assert _spoken_parts("One. Two.") == (["One.", "Two."], set())
    assert len(_paragraph_silence(22050)) == 2 * int(0.6 * 22050)


# ─── DB: read-outs and follow-ups ───────────────────────────────────────


async def _feed(session, key: str) -> int:
    return int((await session.execute(
        text("INSERT INTO news_feeds (url, discovered_via) VALUES (:u, 'manual') RETURNING id"),
        {"u": f"https://feed.example/{key}"},
    )).scalar_one())


async def _seed(session, scope: str, title: str, *, n: int, summary: str | None = None,
                url: str | None = None, source: str | None = "Example News") -> int:
    fid = await _feed(session, f"{scope}-{n}")
    return int((await session.execute(
        text(
            """
            INSERT INTO news_items (scope, feed_id, guid, title, source, summary, url,
                                    published_at)
            VALUES (:scope, :fid, :guid, :title, :source, :summary, :url,
                    now() - make_interval(mins => :n))
            RETURNING id
            """
        ),
        {"scope": scope, "fid": fid, "guid": f"{scope}-{n}", "title": title,
         "source": source, "summary": summary, "url": url, "n": n},
    )).scalar_one())


_LONG = (
    "The county closed the river bridge on Monday after inspectors found cracks in two "
    "supports. Repairs will take until spring. A detour runs through the north side, and "
    "buses will add ten minutes to the morning routes. The county will hold a meeting "
    "on Thursday to explain the plan."
)


async def _briefing(db_session, monkeypatch, *, online: bool = True):
    """National: 2 stories, world: 1 (no location set, so no local)."""
    monkeypatch.setattr(settings, "news_items_per_ask", 3)
    ids = [
        await _seed(db_session, "national", "County bridge closes for repairs", n=1,
                    summary=_LONG, url="https://news.example/bridge"),
        await _seed(db_session, "national", "Hot tub maker recalls heaters", n=2,
                    summary="Heaters can overheat.", url="https://news.example/hot-tub"),
        await _seed(db_session, "global", "Storm moves up the coast", n=3,
                    summary=None, url=None),
    ]
    await db_session.commit()
    ctx = Context(room_id="kitchen", online=online)
    r = await route(Intent(transcript="what's the news", room_id="kitchen"), ctx, db_session)
    await db_session.commit()
    return r, ctx.model_copy(update={"session_id": r.session_id}), ids


async def _context(db_session, session_id) -> dict:
    return (await db_session.execute(
        text("SELECT context FROM sessions WHERE id = :sid"), {"sid": session_id},
    )).scalar() or {}


async def _say(db_session, ctx, said: str):
    r = await route(Intent(transcript=said, room_id="kitchen"), ctx, db_session)
    await db_session.commit()
    return r


@requires_db
async def test_briefing_reads_each_headline_as_its_own_numbered_paragraph(
    db_session, monkeypatch
) -> None:
    r, ctx, ids = await _briefing(db_session, monkeypatch)
    assert r.matched_handler == "news"
    assert r.text.split("\n\n") == [
        "Here's the news.",
        "Nationally. First, County bridge closes for repairs.",
        "Second, Hot tub maker recalls heaters.",
        "Around the world. Third, Storm moves up the coast.",
        "Do you want more on any of them?",
    ]
    assert ";" not in r.text
    assert r.expect_followup  # the satellite listens on for "tell me more"
    state = (await _context(db_session, r.session_id))["news_read"]
    assert [e["id"] for e in state["items"]] == ids
    assert (await _context(db_session, r.session_id))["news_last_item_id"] == ids[-1]


@requires_db
async def test_local_scope_label_is_its_own_sentence(db_session, monkeypatch) -> None:
    from domovoi import home_location

    await home_location.save(db_session, {"name": "Springfield"})
    await _seed(db_session, "local", "Library opens a new wing", n=1)
    await db_session.commit()
    r = await route(Intent(transcript="what's the news", room_id="kitchen"),
                    Context(room_id="kitchen", online=True), db_session)
    assert "Locally. First, Library opens a new wing." in r.text.split("\n\n")


@requires_db
@pytest.mark.parametrize(
    ("said", "index"),
    [
        ("tell me more about the second one", 1),
        ("number three", 2),
        ("tell me more about the last one", 2),
        ("tell me more about the first one", 0),
        ("tell me more about the one about the hot tub recall", 1),
        ("tell me more about the bridge", 0),
        ("more on that", 2),
    ],
)
async def test_followup_says_the_right_story(db_session, monkeypatch, said, index) -> None:
    _, ctx, ids = await _briefing(db_session, monkeypatch, online=False)
    r = await _say(db_session, ctx, said)
    assert r.matched_handler == "news", r.text
    titles = ["County bridge closes for repairs.", "Hot tub maker recalls heaters.",
              "Storm moves up the coast."]
    assert r.text.startswith(titles[index]), r.text
    assert (await _context(db_session, r.session_id))["news_last_item_id"] == ids[index]


@requires_db
async def test_full_summary_is_said_with_its_source(db_session, monkeypatch) -> None:
    _, ctx, _ = await _briefing(db_session, monkeypatch)
    r = await _say(db_session, ctx, "tell me more about the first one")
    assert "inspectors found cracks" in r.text
    assert r.text.endswith("That's from Example News.")
    assert not r.expect_followup  # a full summary: nothing to ask


@requires_db
async def test_out_of_range_says_how_many_were_read(db_session, monkeypatch) -> None:
    _, ctx, _ = await _briefing(db_session, monkeypatch)
    r = await _say(db_session, ctx, "tell me more about the tenth one")
    assert r.matched_handler == "news"
    assert r.text == "I only read 3 stories, so there's no tenth one."


@requires_db
async def test_one_about_something_not_read_says_so(db_session, monkeypatch) -> None:
    _, ctx, _ = await _briefing(db_session, monkeypatch)
    r = await _say(db_session, ctx, "the one about the price of eggs")
    assert r.matched_handler == "news"
    assert "None of the stories I just read" in r.text


@requires_db
async def test_unmatched_words_go_on_to_qa(db_session, monkeypatch) -> None:
    _, ctx, _ = await _briefing(db_session, monkeypatch)
    r = await _say(db_session, ctx, "tell me more about the roman empire")
    assert r.matched_handler != "news"


@requires_db
async def test_no_news_list_falls_through(db_session) -> None:
    ctx = Context(room_id="kitchen", online=True)
    r = await route(Intent(transcript="tell me more about the second one", room_id="kitchen"),
                    ctx, db_session)
    assert r.matched_handler != "news"


@requires_db
async def test_a_stale_news_list_falls_through(db_session, monkeypatch) -> None:
    r, ctx, _ = await _briefing(db_session, monkeypatch)
    data = await _context(db_session, r.session_id)
    data["news_read"]["at"] = time.time() - 11 * 60
    import json

    await db_session.execute(
        text("UPDATE sessions SET context = CAST(:c AS JSONB) WHERE id = :sid"),
        {"c": json.dumps(data, default=str), "sid": r.session_id},
    )
    await db_session.commit()
    r2 = await _say(db_session, ctx, "tell me more about the second one")
    assert r2.matched_handler != "news"


@requires_db
async def test_more_on_that_after_something_else_was_said_is_not_news(
    db_session, monkeypatch
) -> None:
    _, ctx, _ = await _briefing(db_session, monkeypatch)
    other = await _say(db_session, ctx, "what time is it")
    assert other.matched_handler != "news"
    r = await _say(db_session, ctx, "more on that")
    assert r.matched_handler != "news"
    # An explicit reference still means the list.
    r = await _say(db_session, ctx, "tell me more about the second one")
    assert r.matched_handler == "news"


@requires_db
async def test_thin_summary_offline_says_it_cannot_read_the_article(
    db_session, monkeypatch
) -> None:
    _, ctx, _ = await _briefing(db_session, monkeypatch, online=False)
    r = await _say(db_session, ctx, "tell me more about the hot tub recall")
    assert r.text == (
        "Hot tub maker recalls heaters. Heaters can overheat. That's from Example News. "
        "I can't reach the internet right now, so I can't read the full article."
    )
    assert not r.expect_followup


@requires_db
async def test_thin_summary_internet_off_says_so(db_session, monkeypatch) -> None:
    _, ctx, _ = await _briefing(db_session, monkeypatch)
    with egress.override_policy("never"):
        r = await _say(db_session, ctx, "tell me more about the second one")
    assert "can't read the full article" in r.text
    assert "Heaters can overheat." in r.text


@requires_db
async def test_headline_only_says_so_plainly(db_session, monkeypatch) -> None:
    _, ctx, _ = await _briefing(db_session, monkeypatch)
    r = await _say(db_session, ctx, "tell me more about the storm")
    assert r.text == (
        "Storm moves up the coast. That's from Example News. "
        "That's all I have on it: just the headline."
    )


@requires_db
async def test_thin_summary_online_asks_then_reads_the_article(db_session, monkeypatch) -> None:
    from domovoi import lookup_web

    monkeypatch.setattr(settings, "lookup_always_allow", False)
    _, ctx, ids = await _briefing(db_session, monkeypatch)
    r = await _say(db_session, ctx, "tell me more about the second one")
    assert r.expect_followup
    assert r.text.endswith("Want me to read the full article online?")
    pending = (await _context(db_session, r.session_id))["pending_confirmation"]
    assert pending["kind"] == "core.news_read_article"
    assert pending["item"]["id"] == ids[1]

    read: list[str] = []

    async def fake_read_page(url, *, query=""):
        read.append(url)
        out = lookup_web.LookupOutcome(kind="page", query=url, internet_used=True)
        out.results.append(lookup_web.LookupResult(
            title="Hot tub maker recalls heaters", url=url,
            snippet="The maker recalled 40,000 heaters.", source="page",
            source_name="the page you named", source_class="web", site="news.example",
            extract="The maker recalled 40,000 heaters after reports of overheating.",
        ))
        return out

    monkeypatch.setattr(lookup_web, "read_page", fake_read_page)
    r2 = await _say(db_session, ctx, "yes")
    assert read == ["https://news.example/hot-tub"]
    assert r2.matched_handler == "news"
    assert r2.text.startswith("Hot tub maker recalls heaters. (stub qa)"), r2.text
    assert r2.text.endswith("That's from Example News.")


@requires_db
async def test_article_that_cannot_be_read_says_why(db_session, monkeypatch) -> None:
    from domovoi import lookup_web

    monkeypatch.setattr(settings, "lookup_always_allow", True)

    async def refuse(url, *, query=""):
        raise lookup_web.LookupRefused("unreachable", "That page couldn't be reached.")

    monkeypatch.setattr(lookup_web, "read_page", refuse)
    _, ctx, _ = await _briefing(db_session, monkeypatch)
    r = await _say(db_session, ctx, "tell me more about the second one")
    assert r.text.endswith(
        "I couldn't read the full article. That page couldn't be reached."
    ), r.text
    assert "Heaters can overheat." in r.text


@requires_db
async def test_favorite_that_after_expanding_saves_that_story(db_session, monkeypatch) -> None:
    _, ctx, ids = await _briefing(db_session, monkeypatch, online=False)
    await _say(db_session, ctx, "tell me more about the first one")
    r = await _say(db_session, ctx, "favorite that")
    assert r.text == "Saved to your favorites."
    fav = (await db_session.execute(
        text("SELECT id FROM news_items WHERE favorited ORDER BY id"))).scalars().all()
    assert fav == [ids[0]]


@requires_db
async def test_favorite_that_after_a_briefing_saves_the_last_story(db_session, monkeypatch) -> None:
    _, ctx, ids = await _briefing(db_session, monkeypatch)
    await _say(db_session, ctx, "favorite that")
    fav = (await db_session.execute(
        text("SELECT id FROM news_items WHERE favorited ORDER BY id"))).scalars().all()
    assert fav == [ids[-1]]


@requires_db
async def test_subject_readout_is_numbered_with_sources(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "news_items_per_ask", 3)
    await _seed(db_session, "national", "Bridge vote set", n=1)
    await _seed(db_session, "national", "Bridge crews start work", n=2)
    await db_session.commit()
    r = await route(Intent(transcript="give me the news about bridge", room_id="kitchen"),
                    Context(room_id="kitchen", online=True), db_session)
    assert r.text.split("\n\n") == [
        "Here's what I have on bridge.",
        "First, Bridge vote set, from Example News.",
        "Second, Bridge crews start work, from Example News.",
        "Do you want more on any of them?",
    ]
