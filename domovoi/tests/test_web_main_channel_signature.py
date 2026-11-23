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


def test_the_stable_nudge_offers_to_keep_main_and_records_it():
    """A house set up for stable before 1.0 sits on main; once a release is
    out it is nudged to switch. A household that wants main can say so:
    the button posts main to the channel route, which records the choice
    (git config domovoi.intendedChannel), so the nudge stops."""
    src = SETTINGS.read_text(encoding="utf-8")
    keep = src[src.index("const keepMain"):src.index("};", src.index("const keepMain"))]
    assert "apiPost('/api/config/version/channel', { channel: 'main' })" in keep
    nudge = src[src.index("{stableWaiting && ("):src.index("{aheadOfRelease &&")]
    assert "onClick={keepMain}>Keep following main</Button>" in nudge


def test_switching_to_the_channel_already_followed_records_the_choice():
    from pathlib import Path as _P
    gv = (_P(__file__).resolve().parents[1] / "git_version.py").read_text(encoding="utf-8")
    early = gv[gv.index('if track["branch"] == channel and track["channel"] == channel:'):]
    assert early.splitlines()[1].strip() == '_run("config", "domovoi.intendedChannel", channel)'
