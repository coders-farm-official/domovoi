"""What the security doc claims about plugins stays what the code does
(A7-03, A2-03).

The plugin-trust section of docs/SECURITY_PRIVACY.md used to promise that a
migration "cannot COPY to a file or program, alter the server, or create
roles, whatever it says". The code cannot keep that promise (the lint is
lexical and the role is entered on the application's own connection), and
docs/PLUGIN_DEVELOPMENT.md §6.3 already said so. These checks pin the two
documents to the honest wording, and the outbound-fetch paragraph to how
the radio sampler really drives ffmpeg, so a later edit that drifts either
way fails here.

DB-free (file reads and one constant).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SECURITY_DOC = REPO_ROOT / "docs" / "SECURITY_PRIVACY.md"
PLUGIN_DOC = REPO_ROOT / "docs" / "PLUGIN_DEVELOPMENT.md"


def _paragraph(text: str, starts_with: str) -> str:
    """The bullet that starts with ``starts_with`` up to the next bullet."""
    start = text.index(starts_with)
    nxt = re.search(r"\n- \*\*", text[start + len(starts_with):])
    end = start + len(starts_with) + (nxt.start() if nxt else len(text))
    return " ".join(text[start:end].split())


def test_the_migration_containment_paragraph_does_not_promise_a_wall() -> None:
    doc = SECURITY_DOC.read_text(encoding="utf-8")
    para = _paragraph(doc, "- **Database containment")
    assert "whatever it says" not in para
    assert "not a wall" in para
    assert "None of that stops a hostile migration" in para
    # It points at the developer guide's section that says the same.
    assert "PLUGIN_DEVELOPMENT.md#63-per-schema-db-only" in para


def test_the_developer_guide_still_says_the_same_thing() -> None:
    guide = " ".join(PLUGIN_DOC.read_text(encoding="utf-8").split())
    assert "they are not a wall against a hostile one" in guide
    assert "### 6.3 Per-schema DB only" in PLUGIN_DOC.read_text(encoding="utf-8")


def test_the_outbound_paragraph_describes_the_sampler_as_built() -> None:
    plugin_dir = REPO_ROOT / "plugins" / "radio"
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    from domovoi_plugin_radio.clients import shazam_stream

    doc = " ".join(SECURITY_DOC.read_text(encoding="utf-8").split())
    guide = " ".join(PLUGIN_DOC.read_text(encoding="utf-8").split())
    assert shazam_stream.FFMPEG_PROTOCOL_WHITELIST == "pipe"
    assert shazam_stream.FFMPEG_INPUT == "pipe:0"
    for text in (doc, guide):
        assert "-protocol_whitelist http,https,tcp,tls" not in text
        assert "`-protocol_whitelist pipe`" in text
