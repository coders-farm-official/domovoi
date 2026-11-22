"""A server that follows the main channel runs merge commits, not signed
releases: the Version card says so calmly instead of warning, unless signed
updates are enforced (then an update would refuse it, so it stays an error)."""

from pathlib import Path

SETTINGS = Path(__file__).resolve().parents[2] / "web" / "static" / "settings.jsx"


def test_main_channel_unsigned_commit_reads_as_expected_not_a_warning():
    src = SETTINGS.read_text(encoding="utf-8")
    badge = src[src.index("const sigBadge"):src.index("return (", src.index("const sigBadge"))]
    assert "channel === 'main' && !sig.enforced" in badge
    assert "main channel: not a signed release (expected)" in badge
    assert "tone: 'idle'" in badge
    # Enforced signing still reads as an error.
    assert "tone: sig.enforced ? 'err' : 'warn'" in badge
