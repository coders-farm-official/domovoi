"""A fake spoken-name resolver for the "did you mean …?" dialog's tests.

The dialog codes against the resolver's API (``library_match``:
``resolve_request``, ``match_reply``, ``candidate_from_ref``) and against
``MusicHandler.play_candidate`` — all owned by the resolver builder, so
these tests swap them for small fakes over an invented three-name library
(every name here is made up, and was checked to occur nowhere in the
held-out spoken-match gate):

* "velvet kites" (any spelling the fake folds to it) resolves to PLAY the
  artist The Velvet Kites;
* "glass harber" resolves to ASK: the artist Glass Harbor, or the album
  Moonlit Static by Glass Harbor — and, said as a reply, names Glass
  Harbor fairly (0.8; its exact name scores 0.95);
* anything else resolves to "none" (today's search).

``MusicHandler._play`` gets the CONTRACT's hook (resolve → play / ask /
today's search) only while the resolver's own ``_play`` is not on the
branch (it adds ``allow_choice``); once it is, the real one runs and these
fakes feed it. A "none" then goes on to today's MPD search, whose stub
always finds something — so tests assert what the resolver was ASKED, not
what that search answered.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

import pytest

from domovoi import confirmations
from domovoi.handlers.music import MusicHandler
from domovoi.handlers.shared import library_match
from domovoi.handlers.shared.library_match import Candidate, EntityRef, Resolution
from domovoi.models import Response

NEW_URL = "http://192.0.2.20:8099"

GLASS = EntityRef(type="artist", key="glassharbor", label="Glass Harbor")
MOONLIT = EntityRef(
    type="album", key="moonlitstatic|music/glass harbor/moonlit static",
    label="Moonlit Static", artist_label="Glass Harbor",
)
KITES = EntityRef(type="artist", key="velvetkites", label="The Velvet Kites")
LANTERN = EntityRef(
    type="title", key="paperlanternparade|velvetkites",
    label="Paper Lantern Parade", artist_label="The Velvet Kites",
)


def fold(text: str) -> str:
    """The fake's whole notion of "sounds the same": letters only, no
    leading "the"."""
    t = re.sub(r"[^a-z ]+", "", (text or "").lower()).strip()
    t = re.sub(r"^the ", "", t)
    return t.replace(" ", "")


def cand(ref: EntityRef, score: float = 0.8, speak: str | None = None) -> Candidate:
    return Candidate(ref=ref, score=score, via="fuzzy", speak=speak or ref.label, track_ids=(1, 2))


ASK = Resolution(
    decision="ask", candidates=(cand(GLASS, 0.83), cand(MOONLIT, 0.78)),
    heard="glass harber", reason="fuzzy",
)


@dataclass
class FakeResolver:
    resolved: list[dict] = field(default_factory=list)   # queries resolve_request saw
    replies: list[tuple[str, list[str]]] = field(default_factory=list)  # match_reply calls
    played: list[Candidate] = field(default_factory=list)
    gone: set[str] = field(default_factory=set)          # labels no longer in the library
    asks: dict[str, Resolution] = field(default_factory=lambda: {"glassharber": ASK})
    plays: dict[str, EntityRef] = field(default_factory=lambda: {"velvetkites": KITES})
    # A reply that names a parked candidate fairly, not exactly: folded
    # text → (that candidate's label, score).
    near: dict[str, tuple[str, float]] = field(
        default_factory=lambda: {"glassharber": ("Glass Harbor", 0.8)}
    )
    raise_on_play: BaseException | None = None

    async def resolve_request(self, session, query: Mapping[str, str], *, kinds=None) -> Resolution:
        self.resolved.append(dict(query))
        heard = (query.get("any") or query.get("title") or query.get("artist") or "").strip()
        key = fold(heard)
        if key in self.plays:
            return Resolution("play", (cand(self.plays[key], 1.0),), heard=heard, reason="exact")
        if key in self.asks:
            return self.asks[key]
        return Resolution.none("below_ask", heard=heard)

    async def match_reply(self, session, text: str, refs) -> tuple[int, float] | None:
        self.replies.append((text, [r.label for r in refs]))
        for i, ref in enumerate(refs):
            if fold(ref.label) == fold(text):
                return i, 0.95
        hit = self.near.get(fold(text))
        if hit is not None:
            for i, ref in enumerate(refs):
                if ref.label == hit[0]:
                    return i, hit[1]
        return None

    async def candidate_from_ref(self, session, ref: Any) -> Candidate | None:
        if not isinstance(ref, EntityRef):
            ref = EntityRef.from_dict(ref)
        if ref is None or ref.label in self.gone:
            return None
        return Candidate(ref=ref, score=1.0, via="ref", speak=ref.label, track_ids=(1,))

    async def play_candidate(self, handler, candidate: Candidate, ctx, session, *, heard: str = "") -> Response:
        if self.raise_on_play is not None:
            raise self.raise_on_play
        self.played.append(candidate)
        return Response(
            text=f"Playing {candidate.speak}.",
            session_id=ctx.session_id,
            matched_handler="music",
            music_action="start",
            music_stream_url=NEW_URL,
            data={"resolved": candidate.to_dict()},
        )

    @property
    def played_labels(self) -> list[str]:
        return [c.ref.label for c in self.played]


def resolver_hook_on_branch() -> bool:
    """Whether MusicHandler._play already has the resolver's hook."""
    return "allow_choice" in inspect.signature(MusicHandler._play).parameters


async def contract_play(self, query, ctx, session, *, allow_choice: bool = True) -> Response:
    """The CONTRACT's ``_play`` hook (§1), with "today's search" reduced to
    its not-found reply — used only while the resolver's own is absent."""
    res = await library_match.resolve_request(session, query)
    if res.decision == "play":
        return await self.play_candidate(res.best, ctx, session, heard=res.heard)
    if res.decision == "ask" and allow_choice:
        ask = getattr(self, "ask_did_you_mean", None)
        if ask is not None:
            reply = await ask(res, query, ctx, session)
            if reply is not None:
                return reply
    q = " by ".join(v for v in (query.get("title"), query.get("artist")) if v) or query.get("any", "")
    return Response(text=f"I couldn't find anything called {q}.", session_id=ctx.session_id,
                    matched_handler=self.name)


def install(monkeypatch) -> FakeResolver:
    """Swap the resolver API and play_candidate for a FakeResolver."""
    fake = FakeResolver()
    monkeypatch.setattr(library_match, "resolve_request", fake.resolve_request)
    monkeypatch.setattr(library_match, "match_reply", fake.match_reply)
    monkeypatch.setattr(library_match, "candidate_from_ref", fake.candidate_from_ref)

    async def _play_candidate(self, candidate, ctx, session, *, heard: str = ""):
        return await fake.play_candidate(self, candidate, ctx, session, heard=heard)

    monkeypatch.setattr(MusicHandler, "play_candidate", _play_candidate, raising=False)
    if not resolver_hook_on_branch():
        monkeypatch.setattr(MusicHandler, "_play", contract_play)
    return fake


def clear_choice_parked() -> None:
    from domovoi.handlers import music_choice

    confirmations.CHOICE_PARKED.clear()
    confirmations._CHOICE_PARKED_AT.clear()
    music_choice.forget_declined()


@pytest.fixture
def fake_resolver(monkeypatch):
    clear_choice_parked()
    yield install(monkeypatch)
    clear_choice_parked()
