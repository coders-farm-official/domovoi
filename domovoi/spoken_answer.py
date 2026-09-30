"""A spoken Q&A answer, sentence by sentence, while the model writes it.

The voice Q&A fallthrough used to wait for llama3.2:3b's whole reply before
the first word was synthesized: on a CPU host 0.7-2.4 s for a joke or a
short explanation, 5-6 s for "tell me about the history of Rome", all of it
before anything was heard. :class:`SpokenAnswer` reads the reply as it
streams and hands each sentence over as soon as it is complete, so the
streaming layer can speak the first one while the rest is still being
written (``StreamSession._speak_streamed_answer``).

It keeps every rule the whole-reply path (``qa_with_uncertainty``) applies,
in the only form that works once words have been spoken:

* Nothing usable yet — empty, only a placeholder ("None."), or cut on a
  contraction stem ("I don") — means one more try, exactly as before. Only
  while nothing has been said: a retry after the first sentence would say
  it twice.
* A reply that ends cut keeps its complete sentences: the broken tail is
  never spoken.
* A first sentence that is only a placeholder is held back until the next
  one shows it isn't the whole answer.
* ``unreachable`` is set only when the Ollama calls themselves failed and
  nothing was said; the router then speaks its outage line, as before.

The full text (``answer``) is what the router persists and what the
online-check / memory offer is appended to, once the reply has ended.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from domovoi.clients.ollama import (
    _PLACEHOLDER_ANSWER_RE,
    _clean_spoken_answer,
    _is_timeout,
    _last_complete_sentences,
)

log = logging.getLogger(__name__)

# The same split the streaming layer synthesizes by (streaming._split_sentences):
# after a sentence end and the whitespace that follows it.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str) -> list[str]:
    return [p for p in _SENTENCE_SPLIT.split((text or "").strip()) if p]


class SpokenAnswer:
    """``sentences()`` yields the answer's sentences as the model completes
    them; afterwards ``answer`` holds everything that was handed over and
    ``unreachable`` says whether the model never answered at all.

    ``open_stream`` starts one streamed Q&A call (an async iterator of text
    deltas); it is called again for the retry."""

    def __init__(self, open_stream: Callable[[], AsyncIterator[str]], *, transcript: str = "") -> None:
        self._open = open_stream
        self._transcript = transcript
        self.spoken: list[str] = []
        self.unreachable = False
        self.first_sentence_at: float | None = None   # time.monotonic()
        self._stream: Any = None

    @property
    def answer(self) -> str:
        return " ".join(self.spoken).strip()

    def _hand_over(self, sentence: str) -> str:
        if self.first_sentence_at is None:
            self.first_sentence_at = time.monotonic()
        self.spoken.append(sentence)
        return sentence

    async def close(self) -> None:
        """Stop the model mid-answer (a barge-in, or the answer is done):
        closing the stream closes its connection, and Ollama stops
        generating."""
        stream, self._stream = self._stream, None
        close = getattr(stream, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception as e:  # noqa: BLE001
                log.debug("closing the answer stream: %s", e)

    async def sentences(self) -> AsyncIterator[str]:
        try:
            for attempt in (1, 2):
                buffer = ""
                held: list[str] = []
                try:
                    self._stream = self._open()
                    async for delta in self._stream:
                        buffer += delta or ""
                        parts = _SENTENCE_SPLIT.split(buffer.lstrip())
                        if len(parts) < 2:
                            continue
                        *complete, buffer = parts
                        for sentence in complete:
                            sentence = sentence.strip()
                            if not sentence:
                                continue
                            if not self.spoken and not held and _PLACEHOLDER_ANSWER_RE.match(sentence):
                                # "None." alone is no answer; followed by
                                # more, it is just an odd first sentence.
                                held.append(sentence)
                                continue
                            for waiting in held:
                                yield self._hand_over(waiting)
                            held = []
                            yield self._hand_over(sentence)
                except Exception as e:  # noqa: BLE001 — the Ollama call failed
                    await self.close()
                    if self.spoken:
                        # Part of the answer was already said: keep it, stop.
                        log.warning(
                            "spoken answer: stream failed after %d sentence(s) for %r: %s",
                            len(self.spoken), self._transcript, e,
                        )
                        return
                    log.warning(
                        "spoken answer: ollama call failed (attempt %d) for %r: %s",
                        attempt, self._transcript, e,
                    )
                    if attempt == 2 or _is_timeout(e):
                        # A timeout already waited the full read limit.
                        self.unreachable = True
                        return
                    continue
                await self.close()

                tail = buffer.strip()
                if self.spoken:
                    # The last words: spoken unless they stop on a
                    # contraction stem with no end ("…because I don").
                    if tail:
                        if _clean_spoken_answer(tail)[1] != "cut":
                            yield self._hand_over(tail)
                        else:
                            log.warning(
                                "spoken answer: dropped a cut tail for %r: %r",
                                self._transcript, tail,
                            )
                    return

                text, problem = _clean_spoken_answer(" ".join(held + [tail]))
                if problem is None:
                    for sentence in _sentences(text):
                        yield self._hand_over(sentence)
                    return
                log.warning(
                    "spoken answer: %s answer for %r (attempt %d): %r",
                    problem, self._transcript, attempt, text,
                )
                if attempt == 2 and problem == "cut":
                    for sentence in _sentences(_last_complete_sentences(text)):
                        yield self._hand_over(sentence)
        finally:
            # Closed by the consumer (a barge-in) or done: never leave the
            # model writing an answer nobody will hear.
            await self.close()


@dataclass
class StreamedQA:
    """What route() hands a voice turn in place of a finished Q&A reply
    (``Response.qa_stream``): the answer to speak as it is written, and
    ``finish(session)`` — called once it has been said, or cut off —
    which parks any offer, records the turn with the full text, and
    returns ``(response, text still to say)``."""

    answer: SpokenAnswer
    finish: Callable[[Any], Awaitable[tuple[Any, str]]]
