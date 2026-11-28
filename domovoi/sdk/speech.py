"""SpeechAPI — out-of-turn announcements to satellite rooms (SDK 1.1),
and rendering text to audio in a room's voice (SDK 1.9, ``render``).

Plugins sometimes finish work AFTER their turn's Response has been
spoken — a background summarization, a long download, a print
completing. This API lets them push a spoken announcement to a room
through the exact fan-out the core timers and ``/v1/admin/announce``
use: ``StreamSession.announce``, which skips a Pi mid-response (its
in-flight TTS would clip the announcement) and auto-restores resumable
music afterwards.

The active-session map lives on the FastAPI app state; the core binds a
provider at startup (``bind_active_sessions`` in main.py's lifespan).
Before binding — or in the web process, where this API must never be
used — ``announce`` reports zero rooms reached rather than raising.

Words from a friend house (SDK 1.19): ``announce`` and ``render`` take a
:class:`~domovoi.sdk.friend_houses.FriendText` or a ``friend_sentence(...)``
as well as a ``str`` and filter the friend's words with the wake-phrase
filter people's messages get. For a plugin with the ``friend_houses``
permission every line is filtered, a plain ``str`` too.

A line inside the turn (SDK 1.20): ``interim(ctx, text)`` says a short line
in the room now, as the opening of the turn's reply, while the handler is
still working — the hook the core's cold-start notice uses
(``Context.speak_interim``), once per turn.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

#: The longest line ``SpeechAPI.interim`` says (SDK 1.20): a few seconds
#: of speech, so the reply it opens isn't held up behind it.
INTERIM_MAX_CHARS = 120


def _short_line(text: Any) -> str:
    """``text`` as one short line: whitespace collapsed, cut at a word
    boundary to :data:`INTERIM_MAX_CHARS` with an ellipsis. "" for none."""
    line = " ".join(str(text or "").split())
    if len(line) <= INTERIM_MAX_CHARS:
        return line
    cut = line[: INTERIM_MAX_CHARS - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.-") + "…"


_active_sessions_provider: Callable[[], dict[str, Any]] | None = None
_room_voices_provider: Callable[[], dict[str, str]] | None = None


def bind_active_sessions(provider: Callable[[], dict[str, Any]]) -> None:
    """Called once by core startup with a live view of
    ``app.state.active_sessions`` (room_id → StreamSession)."""
    global _active_sessions_provider
    _active_sessions_provider = provider


def bind_room_voices(provider: Callable[[], dict[str, str]]) -> None:
    """Called once by core startup with a live view of
    ``app.state.satellite_voice`` (room_id -> the voice the room speaks
    in), which ``render`` reads (SDK 1.9)."""
    global _room_voices_provider
    _room_voices_provider = provider


async def _room_voice_name(room_id: str | None) -> str | None:
    """The voice ``room_id`` speaks in, the way the room's own replies
    pick it: what its connected device reports (``satellite_voice``),
    else what the server keeps for it (V031), else None (the server's
    voice). None for no room. Never raises."""
    if not room_id:
        return None
    if _room_voices_provider is not None:
        name = (_room_voices_provider() or {}).get(room_id)
        if name:
            return name
    try:
        from domovoi.db.repositories import SatellitesRepository
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            return await SatellitesRepository(s).room_voice(room_id)
    except Exception as e:  # noqa: BLE001 - speak in the server's voice
        log.debug("speech.render: could not read %s's voice: %s", room_id, e)
        return None


class SpeechAPI:
    """Injected as ``sdk.speech`` — never constructed by plugins."""

    def __init__(self, slug: str = "core", *, friend_houses: bool = False) -> None:
        self.slug = slug
        self.friend_houses = friend_houses

    async def _said(self, text: Any) -> str:
        from domovoi.sdk.friend_houses import speakable_for

        return await speakable_for(text, always=self.friend_houses)

    async def render(self, text: Any, *, room_id: str | None = None) -> bytes:
        """Render ``text`` to audio and return it as one mono 16-bit WAV
        (SDK 1.9). Nothing is played: the plugin decides where the audio
        goes (a file a room's player queues, a stream to a browser).

        The voice is the one ``room_id`` speaks in, picked the way the
        room's own replies pick it; with no room (a browser playing the
        audio itself) it is the server's voice, Settings -> Voices. Long
        text is rendered a sentence at a time and joined, so there is no
        length cap. Raises ``ValueError`` for empty text; an engine
        failure raises like the TTS client does. The core process only."""
        text = await self._said(text)
        if not (text or "").strip():
            raise ValueError("speech.render: nothing to say")
        import io
        import wave

        from domovoi.clients.tts import get_tts_client
        from domovoi.streaming import (
            _resample_pcm,
            _split_sentences,
            _wav_to_pcm,
            resolve_voice,
        )

        engine, voice = await resolve_voice(await _room_voice_name(room_id))
        tts = get_tts_client()
        rate: int | None = None
        parts: list[bytes] = []
        for sentence in _split_sentences(text):
            pcm, sr = _wav_to_pcm(
                await tts.synthesize(sentence, engine=engine, voice=voice)
            )
            if not pcm:
                continue
            if rate is None:
                rate = sr
            elif sr != rate:
                pcm = _resample_pcm(pcm, sr, rate)
            parts.append(pcm)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate or 22050)
            wf.writeframes(b"".join(parts))
        return buf.getvalue()

    def _handler_label(self) -> str:
        """The name this plugin's line is labelled with in the turn's
        response_start frame (``matched_handler``): its handler's name
        when it has exactly one, else its slug. Only a label for logs and
        the dashboard; the spoken words never carry it."""
        try:
            from domovoi.handlers import HANDLERS

            names = [h.name for h in HANDLERS if getattr(h, "plugin_slug", None) == self.slug]
        except Exception:  # noqa: BLE001 - the slug is a fine label
            names = []
        return names[0] if len(names) == 1 else self.slug

    async def interim(self, ctx: Any, text: Any) -> bool:
        """Say a short line now, as the opening of this turn's reply, while
        the handler keeps working (SDK 1.20) — "Still looking…" before a
        slow search finishes. At most once per turn; it shares the slot
        with the core's cold-start notice, so a turn that already said
        "I'm waking up my language model" says nothing more. True when it
        went out (the line is then listed in the room's history before the
        reply); False when it was not said.

        Only a voice turn can say it: typed chat, the dashboard's play box
        and ``/v1/intent`` have no room speaker, and get False. Empty text
        is refused, and the line is cut to :data:`INTERIM_MAX_CHARS`. For a
        plugin with the ``friend_houses`` permission the line goes through
        the wake-phrase filter ``announce`` uses. Never raises: a TTS
        failure is False too."""
        try:
            speak = getattr(ctx, "speak_interim", None)
            if not callable(speak):
                return False
            line = _short_line(await self._said(text))
            if not line:
                return False
            spoke = await speak(line, handler=self._handler_label())
            if spoke is not True:
                return False
            said = getattr(ctx, "interim_said", None)
            if isinstance(said, list):
                said.append(line)
            return True
        except Exception as e:  # noqa: BLE001 - an interim line is best-effort
            log.warning("speech.interim (plugin %s) failed: %s", self.slug, e)
            return False

    async def announce(self, room_id: str | None, text: Any) -> list[str]:
        """Speak ``text`` in ``room_id`` (or every connected room when
        None). Returns the room_ids actually reached — empty when the
        target isn't connected. Never raises for delivery problems:
        an announcement is best-effort by nature (the Pi may drop WiFi
        between the check and the play), so callers branch on the
        returned list, not on exceptions. A room in its quiet hours
        (domovoi/quiet_hours.py) is not reached: nothing is spoken there,
        then or later."""
        text = await self._said(text)
        if not (text or "").strip():
            return []
        if _active_sessions_provider is None:
            log.debug("speech.announce before binding — no satellites reachable")
            return []
        sessions = _active_sessions_provider() or {}
        if room_id is None:
            targets = list(sessions.values())
        else:
            target = sessions.get(room_id)
            targets = [target] if target is not None else []

        reached: list[str] = []
        for sess in targets:
            try:
                # Always a notice: quiet hours (domovoi/quiet_hours.py) hold
                # a plugin's announcement back, and no plugin can say
                # otherwise.
                await sess.announce(text, kind="notice")
                reached.append(sess.room_id)
            except Exception as e:  # noqa: BLE001 — per-room isolation
                log.warning(
                    "speech.announce (plugin %s) failed for room=%s: %s",
                    self.slug, getattr(sess, "room_id", "?"), e,
                )
        return reached
