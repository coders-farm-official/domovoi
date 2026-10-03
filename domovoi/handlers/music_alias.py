"""MusicAliasHandler — household "also called" names by voice.

* "when I say gramps I mean Hearth Ensemble" — teach a name (one at a time; an
  artist, album or song can have any number of them). The target can be
  what is playing: "when I say lullaby I mean this song" / "this artist" /
  "this album".
* "forget that name" — undo the last name taught in this conversation;
  "forget the name gramps" — remove one by name.
* "what else is Hearth Ensemble called" / "what names does Hearth Ensemble
  go by" — list them.

The rules (one meaning per name, "replace it?" on a conflict, only an admin
may remove a name someone else added) live in
:mod:`domovoi.db.library_aliases`, shared with the dashboard's "also
called" lists. A name taken by something else is a yes/no question
(``core.alias_replace``); a target the resolver is only fairly sure of is
asked about too (``core.alias_target``, "Do you mean …?") — and that one
takes a correction as well as a yes or no ("no, I mean the Hearth Cats"),
so it is also a choice kind. After every write the resolver's fingerprint
is dropped (:func:`library_match.invalidate`), so "play gramps" works on
the very next turn.

Only about the library. The list phrases have the shape of any question
about names ("what else is the moon called"), and "when I say X, I mean Y"
is said of other things than music: a turn whose subject is not a library
artist, album or song (or a household name for one) is DECLINED — the
fast path returns None and the router routes the turn on, to the tool
model and the Q&A model, as if it had not matched. A tool call the LLM
router makes about something outside the library is declined the same way.

Fully local (``requires_network="no"``). Not exposed to chat mode.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import PurePath
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.clients.mpd import MPDNotProvisioned, get_mpd_client_for
from domovoi.confirmations import request_confirmation
from domovoi.db import library_aliases as repo
from domovoi.db.repositories import SessionRepository
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.shared import library_match
from domovoi.handlers.shared.library_match import Candidate, EntityRef, library_path_for_mpd_file
from domovoi.handlers.shared.spoken_names import alias_key, entity_names, speakable
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)

ALIAS_REPLACE_KIND = "core.alias_replace"
ALIAS_TARGET_KIND = "core.alias_target"
#: Session-context key: the id of the last name taught by voice in this
#: conversation ("forget that name").
LAST_ALIAS_KEY = "last_alias_id"
#: Most names "what else is X called" says.
MAX_LISTED = 5

# ─── Fast paths (anchored; none commits early — every one has an open slot
# or follows one) ─────────────────────────────────────────────────────────

_ADD_RE = re.compile(
    r"^when i say (.+?),? (?:i mean|i want|i[’']m talking about|play|that means|it means) (.+)$"
)
_FORGET_LAST_RE = re.compile(r"^(?:forget|remove|delete) (?:that|this|the last) (?:alias|name|nickname)$")
_FORGET_RE = re.compile(r"^(?:forget|remove|delete) the (?:alias|name|nickname) (.+)$")
# An article-led subject ("what else is a baby goat called", "what other
# names does an owl have") is a question about the world, not about a
# library entity: left to the Q&A model.
_LIST_RE = re.compile(r"^what else is (?!an? )(.+?) called$")
_LIST_NAMES_RE = re.compile(r"^what (?:other )?names (?:does|do) (?!an? )(.+?) (?:have|go by)$")

# The tool is offered to the LLM router ONLY when the words are there —
# never on the default offer, so the cached prompt prefix of an ordinary
# turn is untouched (router.offered_tool_schemas).
_OFFER_RE = re.compile(r"\b(?:when i say|also called|other names?|alias|nickname)\b")
# A request to hear music is music's, whatever name it uses ("I want to hear
# the band also called the kindlers"): the tool is withheld from it — unless
# it is teaching a name ("when I say gramps, play Hearth Ensemble").
_PLAY_REQUEST_RE = re.compile(r"\b(?:play|hear|listen to|put on)\b")

# "this" targets: what is playing in the room.
_NOW_SONG = frozenset({
    "this", "that", "this one", "that one", "this song", "that song",
    "this track", "that track", "this tune", "the song that's playing",
    "what's playing",
})
_NOW_ARTIST_RE = re.compile(r"^(?:this|that) (?:artist|band|singer|group|rapper)$")
_NOW_ALBUM_RE = re.compile(r"^(?:this|that) (?:album|record)$")

_QUOTES = "\"'“”‘’"


def _clean_capture(s: str) -> str:
    return repo.normalize_alias((s or "").strip().strip(_QUOTES).strip())


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _and_join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _ref_for_target(target: repo.Target, folder: str | None = None) -> EntityRef:
    """An EntityRef standing for a target, for :func:`library_match.speak_for`."""
    if target.type == "track":
        return EntityRef(type="track", key=str(target.track_id), label=target.name,
                         artist_label=target.artist_name)
    if target.type == "album":
        return EntityRef(type="album", key=f"{target.key}|{folder or ''}", label=target.name,
                         artist_label=target.artist_name)
    return EntityRef(type="artist", key=target.key or alias_key(target.name), label=target.name)


class MusicAliasHandler(Handler):
    name = "music_alias"
    # band rationale: 295 — after playlist (290) and before music's greedy
    #   ^play catch-all (300). Every path is anchored on its own opener
    #   ("when i say", "forget/remove/delete the name", "what else is …
    #   called", "what names does … have"), none starts with "play", and
    #   the corpus test proves no earlier band takes them.
    priority_band = 295
    display = HandlerDisplay(label="Music names", tone="media")
    requires_network = "no"
    confirmation_kinds = (ALIAS_REPLACE_KIND, ALIAS_TARGET_KIND)
    # "Do you mean …?" about a target takes "no, I mean <another>" too.
    choice_kinds = (ALIAS_TARGET_KIND,)
    chat_exposed = False
    tool_schema = {
        "name": "music_alias",
        "description": (
            "The names THIS HOUSEHOLD has given to an artist, album or song in "
            "ITS OWN MUSIC LIBRARY, so a nickname plays the right music: teach "
            "one ('when I say gramps I mean Hearth Ensemble'), forget one "
            "('forget the name gramps'), or list them ('what else is Hearth "
            "Ensemble called'). Not for playing music, and not for what "
            "anything or anyone in the world is called or nicknamed, or for "
            "aliases of email addresses, people or devices."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "forget", "list"]},
                "alias": {
                    "type": "string",
                    "description": "The other name (add, forget).",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "The artist, album or song it means (add, list); 'this "
                        "song', 'this artist' or 'this album' for what is playing."
                    ),
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        # Unbound functions: the router calls fp.method(handler, m, ctx, session).
        self.fast_paths = [
            FastPath(_ADD_RE, MusicAliasHandler._add_from_match),
            FastPath(_FORGET_LAST_RE, MusicAliasHandler._forget_last_from_match),
            FastPath(_FORGET_RE, MusicAliasHandler._forget_from_match),
            FastPath(_LIST_RE, MusicAliasHandler._list_from_match),
            FastPath(_LIST_NAMES_RE, MusicAliasHandler._list_from_match),
        ]

    def offers_tool(self, transcript: str) -> bool:
        t = transcript or ""
        if not _OFFER_RE.search(t):
            return False
        return "when i say" in t or not _PLAY_REQUEST_RE.search(t)

    def _reply(self, ctx: Context, text_: str, **kw: Any) -> Response:
        return Response(text=text_, session_id=ctx.session_id, matched_handler=self.name, **kw)

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return self._reply(ctx, "Try 'when I say gramps, I mean Hearth Ensemble'.")

    async def execute_from_tool(self, args: dict, ctx: Context, session: AsyncSession) -> Response | None:
        """The LLM router's call. Declined (None — the Q&A model answers
        instead) when it is not about a name in the library: an add with
        no target or with one the library has nothing like ("when I say
        goodnight I mean turn off the lights"), a name to forget that the
        household never taught, a list for something the library does not
        hold."""
        action = (args.get("action") or "").strip().lower()
        alias = str(args.get("alias") or "")
        target = str(args.get("target") or "")
        if action == "add":
            if not _clean_capture(target):
                return None
            return await self._add(alias, target, ctx, session, decline_unknown=True)
        if action == "forget":
            if not alias.strip():
                return await self._forget_last(ctx, session)
            if await repo.find_alias(session, _clean_capture(alias)) is None:
                return None
            return await self._forget(alias, ctx, session)
        if action == "list":
            return await self._list(target or alias, ctx, session)
        return None

    # ─── fast-path entries ─────────────────────────────────────────────

    async def _add_from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response | None:
        return await self._add(m.group(1), m.group(2), ctx, session, decline_unknown=True)

    async def _forget_last_from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response:
        return await self._forget_last(ctx, session)

    async def _forget_from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response:
        return await self._forget(m.group(1), ctx, session)

    async def _list_from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response | None:
        return await self._list(m.group(1), ctx, session)

    # ─── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _actor(ctx: Context) -> repo.Actor:
        return repo.Actor(kind="voice", room_id=ctx.room_id, person_id=ctx.person_id)

    @staticmethod
    async def _speak(session: AsyncSession, ref: EntityRef, avoid: frozenset[str] = frozenset()) -> str:
        try:
            said = await library_match.speak_for(session, ref)
        except Exception as e:  # noqa: BLE001 — a reply never fails on its wording
            log.warning("speak_for(%s) failed: %s", ref, e)
            said = ""
        # speak_for says an entity by a household or MusicBrainz name that
        # sounds like it ("SBTRKT" → "subtract"). A reply ABOUT that very
        # name says the target by its own name instead — never "when you
        # say subtract, I'll play subtract".
        if said and alias_key(said) in avoid:
            said = ""
        return said or speakable(ref.label)

    async def _phrase(self, session: AsyncSession, target: repo.Target, *, folder: str | None = None,
                      ref: EntityRef | None = None, avoid: Iterable[str] = ()) -> str:
        """How to SAY a target in a reply — by none of the names in
        ``avoid`` (the names the reply is about)."""
        keys = frozenset(k for k in (alias_key(a) for a in avoid) if k)
        said = await self._speak(session, ref or _ref_for_target(target, folder), keys)
        if target.type == "track":
            if target.artist_name:
                by = await self._speak(session, EntityRef(
                    type="artist", key=alias_key(target.artist_name), label=target.artist_name), keys)
                return f"{said} by {by}"
            return said
        if target.type == "album":
            phrase = f"the album {said}"
            if target.artist_name:
                phrase += f" by {speakable(target.artist_name)}"
            return phrase
        return said

    async def _row_target(self, session: AsyncSession, row: repo.AliasRow) -> repo.Target:
        """A stored row's target, with the song's artist filled in."""
        target = repo.target_of(row)
        if target.type == "track" and target.track_id is not None:
            r = (await session.execute(
                text("SELECT title, artist, album, file_path FROM library_tracks WHERE id = :id"),
                {"id": target.track_id},
            )).first()
            if r is not None:
                title, artist = repo.track_display(r[0], r[1], r[2], r[3])
                target = repo.Target(type="track", name=title, track_id=target.track_id, artist_name=artist)
        return target

    async def _now_playing_row(self, ctx: Context, session: AsyncSession) -> tuple[Any, str | None]:
        """(library row, refusal) for what this room is playing."""
        try:
            mpd = get_mpd_client_for(ctx.room_id)
            song = await mpd.current_song()
        except MPDNotProvisioned:
            return None, "Nothing is playing right now."
        except Exception as e:  # noqa: BLE001
            log.warning("music_alias: MPD currentsong failed: %s", e)
            return None, "I couldn't tell what's playing right now."
        if not song or not song.get("file"):
            return None, "Nothing is playing right now."
        mpd_file = str(song["file"])
        if mpd_file.startswith("http"):
            return None, ("That's streaming from somewhere else — I can only learn names "
                          "for music in your library.")
        row = (await session.execute(
            text("SELECT id, title, artist, album, file_path FROM library_tracks WHERE file_path = :p"),
            {"p": library_path_for_mpd_file(mpd_file)},
        )).first()
        if row is None:
            return None, "I couldn't find the current song in your library. Try 'rescan my library'."
        return row, None

    async def _target_now_playing(
        self, which: str, ctx: Context, session: AsyncSession
    ) -> tuple[repo.Target | None, str | None, str | None]:
        """(target, folder, refusal) for "this song / artist / album"."""
        row, refusal = await self._now_playing_row(ctx, session)
        if row is None:
            return None, None, refusal
        tid, title, artist, album, file_path = int(row[0]), row[1], row[2], row[3], row[4]
        shown, primary = repo.track_display(title, artist, album, file_path)
        folder = PurePath(file_path or "").parent.as_posix().lower()
        if which == "artist":
            if not primary:
                return None, None, "I don't know who this is by."
            # The credit's first performer (contract §3). A whole band-like
            # credit ("Earth, Wind & Fire") is named from the drawer, or by
            # saying its name.
            return repo.Target(type="artist", name=primary, key=alias_key(primary)), folder, None
        if which == "album":
            names = entity_names(title, artist, album)
            album_name = next((nm for kind, nm in names if kind == "album"), None)
            if not album_name or not alias_key(album_name):
                return None, None, "This song isn't on an album I know."
            return repo.Target(
                type="album", name=album_name, key=alias_key(album_name),
                artist_key=alias_key(primary) if primary else None, artist_name=primary,
            ), folder, None
        return repo.Target(type="track", name=shown, track_id=tid, artist_name=primary), folder, None

    async def _target_from_candidate(self, cand: Candidate, session: AsyncSession) -> repo.Target | None:
        ref = cand.ref
        if ref.type in ("artist", "credit"):
            return repo.Target(type="artist", name=ref.label, key=ref.key)
        if ref.type in ("title", "track"):
            tid: int | None = cand.track_ids[0] if cand.track_ids else None
            if tid is None and ref.type == "track" and ref.key.isdigit():
                tid = int(ref.key)
            if tid is None:
                rows = await library_match.tracks_for(session, ref)
                tid = rows[0].id if rows else None
            if tid is None:
                return None
            return repo.Target(type="track", name=ref.label, track_id=int(tid), artist_name=ref.artist_label)
        if ref.type == "album":
            akey = ref.key.split("|", 1)[0]
            if not akey:
                return None
            return repo.Target(
                type="album", name=ref.label, key=akey,
                artist_key=alias_key(ref.artist_label) if ref.artist_label else None,
                artist_name=ref.artist_label,
            )
        return None

    async def _candidate_phrase(self, session: AsyncSession, cand: Candidate) -> str:
        ref = cand.ref
        said = cand.speak or speakable(ref.label)
        if ref.type in ("title", "track") and ref.artist_label:
            by = await self._speak(session, EntityRef(
                type="artist", key=alias_key(ref.artist_label), label=ref.artist_label))
            return f"{said} by {by}"
        if ref.type == "album":
            phrase = f"the album {said}"
            if ref.artist_label:
                phrase += f" by {speakable(ref.artist_label)}"
            return phrase
        return said

    async def _remember_last(self, ctx: Context, session: AsyncSession, alias_id: int) -> None:
        if ctx.session_id is None:
            return
        await SessionRepository(session).set_context_key(ctx.session_id, LAST_ALIAS_KEY, alias_id)

    # ─── add ───────────────────────────────────────────────────────────

    async def _add(
        self,
        raw_alias: str,
        raw_target: str,
        ctx: Context,
        session: AsyncSession,
        *,
        decline_unknown: bool = False,
    ) -> Response | None:
        """Teach ``raw_alias`` for ``raw_target``. ``decline_unknown``: a
        target the library has nothing like is not this handler's turn
        ("when I say goodnight I mean turn off the lights") — None, and the
        router routes it on; otherwise "I couldn't find …"."""
        alias = _clean_capture(raw_alias)
        said_target = _clean_capture(raw_target)
        if not alias or not alias_key(alias):
            return self._reply(ctx, "I didn't catch the name. Say 'when I say gramps, I mean' and the artist, album or song.")
        if not said_target:
            return self._reply(ctx, f"What should {alias} mean?")
        which = said_target.lower()
        folder: str | None = None
        if which in _NOW_SONG or _NOW_ARTIST_RE.match(which) or _NOW_ALBUM_RE.match(which):
            kind = "artist" if _NOW_ARTIST_RE.match(which) else "album" if _NOW_ALBUM_RE.match(which) else "track"
            target, folder, refusal = await self._target_now_playing(kind, ctx, session)
            if target is None:
                return self._reply(ctx, refusal or "Nothing is playing right now.")
            return await self._commit_add(alias, target, ctx, session, folder=folder)

        res = await library_match.resolve_request(session, {"any": said_target})
        best = res.best
        if res.decision == "play" and best is not None:
            target = await self._target_from_candidate(best, session)
            if target is None:
                return self._reply(ctx, f"I couldn't find {said_target} in your library.")
            return await self._commit_add(alias, target, ctx, session, ref=best.ref)
        if res.decision == "ask" and best is not None and ctx.session_id is not None:
            target = await self._target_from_candidate(best, session)
            if target is not None:
                prompt = f"Do you mean {await self._candidate_phrase(session, best)}?"
                await request_confirmation(
                    session, ctx.session_id, kind=ALIAS_TARGET_KIND, handler=self.name,
                    data={"alias": alias, "target": target.to_dict(), "ref": best.ref.to_dict()},
                    prompt=prompt,
                )
                return self._reply(ctx, prompt, expect_followup=True,
                                   data={"alias": alias, "candidate": best.to_dict()})
        if decline_unknown and res.decision == "none":
            return None
        return self._reply(ctx, f"I couldn't find {said_target} in your library.")

    async def _commit_add(
        self,
        alias: str,
        target: repo.Target,
        ctx: Context,
        session: AsyncSession,
        *,
        replace: bool = False,
        folder: str | None = None,
        ref: EntityRef | None = None,
    ) -> Response:
        outcome = await repo.add_alias(
            session, alias=alias, target=target, actor=self._actor(ctx), source="voice", replace=replace
        )
        phrase = await self._phrase(session, target, folder=folder, ref=ref, avoid=(alias,))
        if outcome.status == "invalid":
            if outcome.code == "already_its_name":
                return self._reply(ctx, f"That's already what {phrase} is called.")
            return self._reply(ctx, "I didn't catch the name. Say 'when I say gramps, I mean' and the artist, album or song.")
        if outcome.status == "exists":
            return self._reply(ctx, f"{_cap(alias)} already means {phrase}.",
                               data={"alias_id": outcome.row.id if outcome.row else None})
        if outcome.status == "taken":
            existing = outcome.existing
            assert existing is not None
            other = await self._phrase(session, await self._row_target(session, existing), avoid=(alias,))
            prompt = f"{_cap(alias)} already means {other}. Replace it?"
            if ctx.session_id is not None:
                await request_confirmation(
                    session, ctx.session_id, kind=ALIAS_REPLACE_KIND, handler=self.name,
                    data={"alias": alias, "target": target.to_dict(), "existing_id": existing.id,
                          "folder": folder},
                    prompt=prompt,
                )
                return self._reply(ctx, prompt, expect_followup=True,
                                   data={"alias": alias, "existing_id": existing.id})
            return self._reply(ctx, f"{_cap(alias)} already means {other}.")
        if outcome.status == "forbidden":
            return self._reply(ctx, "Only an admin can change that one — someone else added it.")
        row = outcome.row
        assert row is not None
        library_match.invalidate()
        await self._remember_last(ctx, session, row.id)
        text_ = f"Got it — when you say {alias}, I'll play {phrase}"
        if outcome.shadows:
            shadow = outcome.shadows[0]
            name = speakable(shadow["name"])
            text_ += f" instead of the album {name}" if shadow["type"] == "album" else f" instead of {name}"
        return self._reply(ctx, text_ + ".", data={
            "alias_id": row.id, "status": outcome.status, "shadows": outcome.shadows,
            "target": target.to_dict(),
        })

    # ─── forget ────────────────────────────────────────────────────────

    async def _forget_row(self, row: repo.AliasRow, ctx: Context, session: AsyncSession) -> Response:
        outcome = await repo.remove_alias(session, row.id, self._actor(ctx))
        if outcome.status == "not_found":
            return self._reply(ctx, f"I don't have a name called {row.alias}.")
        if outcome.status == "forbidden":
            return self._reply(ctx, "Only an admin can remove that one — someone else added it.")
        library_match.invalidate()
        phrase = await self._phrase(session, await self._row_target(session, row), avoid=(row.alias,))
        return self._reply(ctx, f"OK — {row.alias} doesn't mean {phrase} anymore.",
                           data={"alias_id": row.id, "status": outcome.status})

    async def _forget(self, raw_alias: str, ctx: Context, session: AsyncSession) -> Response:
        alias = _clean_capture(raw_alias)
        if not alias:
            return self._reply(ctx, "Which name should I forget?")
        row = await repo.find_alias(session, alias)
        if row is None:
            return self._reply(ctx, f"I don't have a name called {alias}.")
        return await self._forget_row(row, ctx, session)

    async def _forget_last(self, ctx: Context, session: AsyncSession) -> Response:
        alias_id = None
        if ctx.session_id is not None:
            data = await SessionRepository(session).get_context(ctx.session_id) or {}
            alias_id = data.get(LAST_ALIAS_KEY)
        row = await repo.get_alias(session, int(alias_id)) if isinstance(alias_id, int) else None
        if row is None or row.suppressed:
            return self._reply(ctx, "I haven't learned a new name in this conversation — "
                                    "say 'forget the name' and the name.")
        response = await self._forget_row(row, ctx, session)
        if ctx.session_id is not None and response.data.get("status") in ("removed", "suppressed"):
            await SessionRepository(session).set_context_key(ctx.session_id, LAST_ALIAS_KEY, None)
        return response

    # ─── list ──────────────────────────────────────────────────────────

    async def _list(self, raw_target: str, ctx: Context, session: AsyncSession) -> Response | None:
        """The names a library entity is also called — or None (declined:
        the router routes the turn on) when ``raw_target`` is neither a
        household name nor something the resolver is SURE is in the
        library. "What else is the moon called" is a question about the
        moon, and a loose match ("Moon River") is not what was asked
        about."""
        said = _clean_capture(raw_target)
        if not said:
            return self._reply(ctx, "Which artist, album or song?")
        track_ids: tuple[int, ...] = ()
        ref: EntityRef | None = None
        known = await repo.find_alias(session, said)
        if known is not None:
            target = await self._row_target(session, known)
        else:
            res = await library_match.resolve_request(session, {"any": said})
            best = res.best
            if res.decision != "play" or best is None:
                return None
            maybe = await self._target_from_candidate(best, session)
            if maybe is None:
                return None
            target, ref, track_ids = maybe, best.ref, tuple(best.track_ids)
        rows = await repo.aliases_for_target(session, target, track_ids=track_ids)
        names: list[str] = []
        for r in rows:
            if r.alias not in names:
                names.append(r.alias)
        phrase = _cap(await self._phrase(session, target, ref=ref, avoid=names))
        if not rows:
            return self._reply(ctx, f"{phrase} doesn't have any other names yet.",
                               data={"aliases": [], "target": target.to_dict()})
        spoken = names[:MAX_LISTED]
        return self._reply(ctx, f"{phrase} is also called {_and_join(spoken)}.",
                           data={"aliases": names, "target": target.to_dict()})

    # ─── parked questions ─────────────────────────────────────────────

    async def handle_choice_reply(
        self, kind: str, data: dict, transcript: str, ctx: Context, session: AsyncSession
    ) -> Response | None:
        """The reply to "Do you mean <target>?" (``core.alias_target``),
        whole: yes teaches the name for it; a plain no leaves it; "no, I
        mean <another>" / "I meant <another>" teaches it for that one (and
        may ask about it in turn); anything else is not about the question
        — None, and the router routes it as a turn of its own."""
        from domovoi.handlers.music_choice import parse_choice_reply

        if kind != ALIAS_TARGET_KIND:
            return None
        alias = data.get("alias") if isinstance(data.get("alias"), str) else ""
        if not alias:
            return None
        reply = parse_choice_reply(transcript, 1)
        if reply.action == "pick":
            return await self.handle_confirmation(kind, data, True, ctx, session)
        if reply.action == "decline":
            return self._reply(ctx, "OK, I'll leave it.")
        if reply.action == "name":
            # A correction or a request names the target outright; a bare
            # name only when the library has something by it.
            return await self._add(
                alias, reply.name, ctx, session,
                decline_unknown=not (reply.negated or reply.explicit),
            )
        return None

    async def handle_confirmation(
        self, kind: str, data: dict, affirmative: bool, ctx: Context, session: AsyncSession
    ) -> Response:
        alias = data.get("alias") if isinstance(data.get("alias"), str) else ""
        target = repo.Target.from_dict(data.get("target"))
        if not affirmative or not alias or target is None:
            return self._reply(ctx, "OK, I'll leave it.")
        folder = data.get("folder") if isinstance(data.get("folder"), str) else None
        if kind == ALIAS_REPLACE_KIND:
            return await self._commit_add(alias, target, ctx, session, replace=True, folder=folder)
        ref = EntityRef.from_dict(data.get("ref"))
        return await self._commit_add(alias, target, ctx, session, ref=ref)


__all__ = ["ALIAS_REPLACE_KIND", "ALIAS_TARGET_KIND", "LAST_ALIAS_KEY", "MusicAliasHandler"]
