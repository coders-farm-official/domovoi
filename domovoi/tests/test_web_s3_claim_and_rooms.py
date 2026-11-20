"""Two dashboard details from the Mac installer sitting (1.4.0), read from
the no-build sources the way the other static checks are.

* S3-3: every field a setup code is typed into turns off a phone's
  autocapitalise, autocorrect and spellcheck (the first letter came out
  capitalised; the server forgives that too, test_auth_hygiene.py).
* S3-5: the Rooms page's empty state says to build the card's files
  "below", where the builder is a collapsed card a few sections down: it
  carries a button that opens that card and scrolls to it.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "web" / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_every_setup_code_field_is_plain_text_on_a_phone() -> None:
    src = _read("components.jsx")
    fields = re.findall(r"<input[^<]*?value=\{code\}.*?/>", src, flags=re.S)
    assert len(fields) >= 2, "the setup-code fields moved; find them again"
    for field in fields:
        for attr in ('autoCapitalize="none"', 'autoCorrect="off"', "spellCheck={false}",
                     'autoComplete="off"'):
            assert attr in field, (attr, field)


def test_the_rooms_empty_state_opens_the_sd_card_builder() -> None:
    rooms = _read("satellites.jsx")
    empty = rooms[rooms.index('title="no rooms set up yet"'):]
    empty = empty[:empty.index("</Card>")]
    assert "action={<Button" in empty
    assert "window.openPrepareMediaCard()" in empty

    card = _read("satellite_media.jsx")
    assert "window.openPrepareMediaCard = openPrepareMediaCard;" in card
    # The card listens for it, opens itself and scrolls into view.
    listener = card[card.index("window.addEventListener(PREPARE_MEDIA_CARD_OPEN_EVENT"):]
    body = card[:card.index("window.addEventListener(PREPARE_MEDIA_CARD_OPEN_EVENT")]
    body = body[body.rindex("React.useEffect"):]
    assert "setOpen(true)" in body and "scrollIntoView" in body
    assert "removeEventListener(PREPARE_MEDIA_CARD_OPEN_EVENT" in listener
