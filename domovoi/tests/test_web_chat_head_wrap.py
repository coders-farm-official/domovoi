"""The chat's header row on a phone (owner report, 1.5.0 house testing).

At ~412 px the row held the chats toggle, the title, the privacy chip,
the "Share with household" switch, the internet chip and the model name on
one line. The privacy group was allowed to shrink below its own text
(flex-shrink with min-width: 0) while the switch's label refused to wrap,
so the label was drawn underneath the internet chip and both were
unreadable, and the title shrank to nothing.

Now (styles.css .chat-head, chat.jsx ChatPrivacy):

* the row wraps onto more lines instead of squeezing;
* the privacy group and the internet chip never shrink under their text;
* the title keeps a real share of its line; the model name is what gives
  way (flex-basis 0, a small minimum, truncating);
* on a narrow page the switch reads "Share" (the checkbox still carries
  "Share with household" for screen readers).

Checked by hand in a browser from 320 to 768 px, light and dark, with
every internet-chip label and a long model name; these pins keep the
rules that made it work. CSS and JSX read as text. No DB.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = (REPO_ROOT / "web" / "static" / "styles.css").read_text(encoding="utf-8")
CHAT = (REPO_ROOT / "web" / "static" / "chat.jsx").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    """The declarations of the first rule whose selector is exactly `selector`."""
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"styles.css has no `{selector} {{...}}` rule"
    return re.sub(r"\s+", " ", m.group(1))


def test_header_row_wraps_instead_of_squeezing():
    assert "flex-wrap: wrap" in _rule(".chat-head")


def test_privacy_group_and_internet_chip_never_shrink_under_their_text():
    assert "flex: 0 0 auto" in _rule(".chat-privacy")
    assert "white-space: nowrap" in _rule(".chat-privacy-switch")
    assert "flex: 0 0 auto" in _rule(".chat-internet-chip")


def test_title_keeps_its_line_and_the_model_gives_way():
    title = _rule(".chat-head-title")
    assert re.search(r"flex: 1 1 \d+(\.\d+)?em", title), title
    model = _rule(".chat-head-model")
    assert "flex: 1 1 0" in model, model
    assert re.search(r"min-width: \d+(\.\d+)?em", model), model
    assert "text-overflow: ellipsis" in model and "white-space: nowrap" in model


def test_narrow_page_shortens_the_share_label():
    assert "display: none" in _rule(".chat-privacy-switch-short")
    assert "display: none" in _rule(".chat-page.narrow .chat-privacy-switch-long")
    assert "display: inline" in _rule(".chat-page.narrow .chat-privacy-switch-short")
    privacy = re.search(r"const ChatPrivacy = .*?^\};", CHAT, re.M | re.S)
    assert privacy, "ChatPrivacy is gone from chat.jsx"
    body = privacy.group(0)
    assert 'aria-label="Share with household"' in body
    assert '<span className="chat-privacy-switch-long">Share with household</span>' in body
    assert '<span className="chat-privacy-switch-short" aria-hidden="true">Share</span>' in body
