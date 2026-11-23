"""OCR with Tesseract: one page picture in, its text and how sure it was.

Tesseract (Apache-2.0, its English data ``tesseract-ocr-eng`` Apache-2.0
too) is an OS package (``scripts/linux/provision.sh``), run as a separate
program and never bundled: ``tesseract page.png stdout -l eng --psm 3 tsv``
at the lowest CPU priority, on one thread (``OMP_THREAD_LIMIT=1``, so a
page never takes every core from a voice turn), held still while a voice
turn runs (:mod:`domovoi.voice_first`).

The TSV output gives every word with its confidence (0-100).
:func:`parse_tsv` rebuilds the text line by line and paragraph by
paragraph, and :func:`readable` decides from the confidences whether the
page came out well enough to trust (otherwise the picture model is asked,
when the household's AI features are on).

**Languages**: Domovoi has no household language setting yet (speech
recognition is English), so pages are read in English (:func:`languages`).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

#: How long one page may take (a dense page at 300 dpi takes a few seconds).
PAGE_TIMEOUT_SEC = 180.0
#: A page is readable with at least this many words ...
MIN_WORDS = 3
#: ... a mean word confidence (weighted by length) of at least this ...
MIN_MEAN_CONF = 70.0
#: ... and at most this share of its words below :data:`LOW_WORD_CONF`.
MAX_LOW_SHARE = 0.35
LOW_WORD_CONF = 50.0


@dataclass
class PageOcr:
    text: str
    confidence: float | None
    words: int
    low_share: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {"text": self.text, "confidence": self.confidence, "words": self.words, "low_share": self.low_share}


def find() -> str | None:
    """The tesseract program, or None when it isn't installed."""
    return shutil.which("tesseract")


def languages() -> list[str]:
    """The Tesseract languages asked: English (no household language
    setting exists to pick others from)."""
    return ["eng"]


def argv(png: Path, langs: list[str]) -> list[str]:
    return [find() or "tesseract", str(png), "stdout", "-l", "+".join(langs), "--psm", "3", "tsv"]


def parse_tsv(tsv: str) -> PageOcr:
    """The page's text and confidence from tesseract's TSV output."""
    lines: dict[tuple[int, int, int], list[str]] = {}
    order: list[tuple[int, int, int]] = []
    weighted = 0.0
    weight = 0
    words = 0
    low = 0
    for i, row in enumerate(tsv.splitlines()):
        cols = row.split("\t")
        if i == 0 and cols and cols[0] == "level":
            continue
        if len(cols) < 12 or cols[0] != "5":
            continue
        word = cols[11].strip()
        try:
            conf = float(cols[10])
            key = (int(cols[2]), int(cols[3]), int(cols[4]))
        except ValueError:
            continue
        if not word or conf < 0:
            continue
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(word)
        words += 1
        weighted += conf * len(word)
        weight += len(word)
        if conf < LOW_WORD_CONF:
            low += 1
    out: list[str] = []
    prev: tuple[int, int] | None = None
    for key in order:
        para = key[:2]
        if prev is not None and para != prev:
            out.append("")
        out.append(" ".join(lines[key]))
        prev = para
    conf = round(weighted / weight, 1) if weight else None
    return PageOcr(text="\n".join(out).strip(), confidence=conf, words=words,
                   low_share=round(low / words, 3) if words else 0.0)


def readable(page: PageOcr) -> bool:
    """Whether OCR read the page well enough to keep its text as the
    page's own: enough words, confident on the whole, few unsure ones.
    Handwriting, photos and smudged print fail this and go to the
    picture model (or are said to be unreadable with AI off)."""
    if page.words < MIN_WORDS or page.confidence is None:
        return False
    return page.confidence >= MIN_MEAN_CONF and page.low_share <= MAX_LOW_SHARE


Hold = Callable[[Any], Awaitable[None]]


class OcrMissing(Exception):
    """tesseract isn't installed on this server."""


class OcrFailed(Exception):
    """tesseract ran and failed on the page."""


async def read_page(png: Path, *, hold: Hold | None = None, langs: list[str] | None = None,
                    timeout: float = PAGE_TIMEOUT_SEC) -> PageOcr:
    """Run tesseract on ``png``. Raises :class:`OcrMissing` or
    :class:`OcrFailed`."""
    from domovoi.chat_video import media

    if find() is None:
        raise OcrMissing()
    try:
        rc, out, _err = await media.run_low(argv(png, langs or languages()), timeout=timeout, hold=hold,
                                            stdout=True, env={"OMP_THREAD_LIMIT": "1"})
    except FileNotFoundError:
        raise OcrMissing() from None
    except TimeoutError:
        raise OcrFailed("timeout") from None
    except media.OutputTooLarge:
        raise OcrFailed("output too large") from None
    if rc != 0:
        raise OcrFailed(f"exit {rc}")
    return parse_tsv(out.decode("utf-8", "replace"))
