"""The internet answer as DEFAULTS: ``config.PROFILE_DEFAULTS``, the
``Settings`` validator that applies it at boot, and the runtime half in
``domovoi/internet_profile.py`` (re-derive on a change, "follow the answer
again", the Settings → Internet document), plus the ``.env`` helpers they
stand on.

DB-free. Every test builds its own ``Settings`` or points
``config_env_writer._ENV_FILE`` at a tmp ``.env``; the live singleton is
only touched through ``monkeypatch`` so it is restored afterwards.
"""

from __future__ import annotations

import importlib.util
from datetime import date

import pytest

from domovoi import config as config_mod
from domovoi import config_env_writer, egress, internet_profile
from domovoi.config import (
    PROFILE_DEFAULTS,
    PROFILE_FIELD_NAMES,
    Settings,
    profile_value,
    settings,
)

ANSWERS = ("always", "sometimes", "never")
NAMES = [pd.name for pd in PROFILE_DEFAULTS]

# The owner's table (contract 5.4 / D22), spelled out so a change to
# PROFILE_DEFAULTS has to change this too.
EXPECTED = {
    "news_enabled": {"always": True, "sometimes": True, "never": False},
    "music_alias_fetch_enabled": {"always": True, "sometimes": True, "never": False},
    "podcast_feed_poller_enabled": {"always": True, "sometimes": False, "never": False},
    "library_enricher_enabled": {"always": True, "sometimes": True, "never": False},
    "seed_voice_catalog": {"always": False, "sometimes": False, "never": False},
    # Lyrics (V021, owner decision 2026-10-03): LRCLIB follows the answer.
    "lyrics_lrclib_enabled": {"always": True, "sometimes": True, "never": False},
    "lyrics_write_lrc": {"always": True, "sometimes": True, "never": False},
}
# Read per tick / per call: these apply the moment the answer changes.
LIVE = {"music_alias_fetch_enabled", "lyrics_lrclib_enabled", "lyrics_write_lrc"}


@pytest.fixture(autouse=True)
def _no_hand_set_exports(monkeypatch):
    """Nothing in the developer's environment pins a profile field or the
    answer for these tests."""
    for name in [*NAMES, "internet_access", "acoustid_api_key", "HF_HUB_OFFLINE"]:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def with_shazam(monkeypatch):
    def set_(present: bool) -> None:
        real = importlib.util.find_spec

        def fake(name, *a, **k):
            if name == "shazamio":
                return object() if present else None
            return real(name, *a, **k)

        monkeypatch.setattr(importlib.util, "find_spec", fake)

    return set_


# ─── The table ────────────────────────────────────────────────────────────


def test_the_table_is_the_owners_table() -> None:
    assert set(NAMES) == set(EXPECTED) == PROFILE_FIELD_NAMES
    for pd in PROFILE_DEFAULTS:
        for a in ANSWERS:
            assert getattr(pd, a) == EXPECTED[pd.name][a], (pd.name, a)
    # topic news stays a personal opt-in; Edge voices never a default
    assert "news_auto_fetch" not in PROFILE_FIELD_NAMES
    assert "tts_engine" not in PROFILE_FIELD_NAMES
    applies = {pd.name: pd.applies for pd in PROFILE_DEFAULTS}
    assert all(applies[k] == "live" for k in LIVE)
    assert all(v == "restart" for k, v in applies.items() if k not in LIVE)
    # every row names a real Settings field
    assert PROFILE_FIELD_NAMES <= set(Settings.model_fields)


@pytest.mark.parametrize("answer", ANSWERS)
@pytest.mark.parametrize("name", NAMES)
def test_the_matrix(answer, name, with_shazam) -> None:
    with_shazam(True)   # the enricher's condition met, so its row reads plainly
    s = Settings(_env_file=None, internet_access=answer)
    assert getattr(s, name) == EXPECTED[name][answer]
    # filled values are NOT marked as set (only the answer itself is)
    assert name not in s.model_fields_set


def test_unset_keeps_every_field_default() -> None:
    s = Settings(_env_file=None)
    assert s.internet_access == ""
    for name in NAMES:
        assert getattr(s, name) == Settings.model_fields[name].default, name
    # …which is today's behaviour, spelled out
    assert s.news_enabled is True and s.seed_voice_catalog is True
    assert s.library_enricher_enabled is True
    assert s.music_alias_fetch_enabled is False and s.podcast_feed_poller_enabled is False
    # LRCLIB stays off while the question is unanswered; saving .lrc files
    # is on, and does something only once LRCLIB is.
    assert s.lyrics_lrclib_enabled is False and s.lyrics_write_lrc is True


def test_the_answer_texts_name_what_lyrics_send() -> None:
    always = next(c for c in internet_profile.CHOICES if c["value"] == "always")
    assert "synced lyrics" in always["detail"]
    assert "a song's title" in internet_profile.PRIVACY_NOTE
    assert "never leave this box" in internet_profile.PRIVACY_NOTE


def test_an_unknown_answer_is_unset() -> None:
    s = Settings(_env_file=None, internet_access="perhaps")
    assert s.internet_access == ""
    assert s.news_enabled is True
    assert Settings(_env_file=None, internet_access="OFFLINE").internet_access == "never"


def test_hand_set_wins_by_kwarg() -> None:
    s = Settings(_env_file=None, internet_access="never", news_enabled=True, seed_voice_catalog=True)
    assert s.news_enabled is True and s.seed_voice_catalog is True
    assert s.music_alias_fetch_enabled is False


def test_hand_set_wins_by_environment(monkeypatch) -> None:
    monkeypatch.setenv("NEWS_ENABLED", "true")
    monkeypatch.setenv("INTERNET_ACCESS", "never")
    s = Settings(_env_file=None)
    assert s.internet_access == "never"
    assert s.news_enabled is True
    assert s.podcast_feed_poller_enabled is False


def test_hand_set_wins_by_env_file(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "INTERNET_ACCESS=always\n"
        "PODCAST_FEED_POLLER_ENABLED=false\n"
        "# MUSIC_ALIAS_FETCH_ENABLED=false\n",     # commented: follows
        encoding="utf-8",
    )
    s = Settings(_env_file=str(env))
    assert s.internet_access == "always"
    assert s.podcast_feed_poller_enabled is False          # pinned
    assert s.music_alias_fetch_enabled is True             # follows


@pytest.mark.parametrize("answer", ("always", "sometimes"))
def test_the_enricher_needs_a_key_or_shazam(answer, with_shazam) -> None:
    with_shazam(False)
    assert Settings(_env_file=None, internet_access=answer).library_enricher_enabled is False
    assert Settings(
        _env_file=None, internet_access=answer, acoustid_api_key="app-key"
    ).library_enricher_enabled is True
    with_shazam(True)
    assert Settings(_env_file=None, internet_access=answer).library_enricher_enabled is True
    with_shazam(False)
    assert Settings(_env_file=None, internet_access="never", acoustid_api_key="k").library_enricher_enabled is False
    assert config_mod.shazam_installed() is False


def test_profile_value_for_unset_is_none() -> None:
    pd = PROFILE_DEFAULTS[0]
    assert profile_value(pd, "", settings) is None
    assert profile_value(pd, "bogus", settings) is None
    assert profile_value(pd, "never", settings) is False


# ─── .env helpers ─────────────────────────────────────────────────────────


def test_env_file_keys(tmp_path) -> None:
    env = tmp_path / ".env"
    assert config_env_writer.env_file_keys(env) == set()
    env.write_text(
        "# NEWS_ENABLED=false\n"
        "music_alias_fetch_enabled=true\n"
        "  BOT_NAME = Jeeves\n"
        "export SEED_VOICE_CATALOG=false\n"
        "garbage line\n\n",
        encoding="utf-8",
    )
    assert config_env_writer.env_file_keys(env) == {
        "MUSIC_ALIAS_FETCH_ENABLED", "BOT_NAME", "SEED_VOICE_CATALOG",
    }


def test_remove_env_keys_comments_out_and_never_deletes(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_bytes(
        b"# keep me\r\n"
        b"NEWS_ENABLED=false\r\n"
        b"bot_name=Jeeves\r\n"
        b"# SEED_VOICE_CATALOG=true\r\n"
        b"news_enabled=true\r\n"
    )
    removed = config_env_writer.remove_env_keys(["news_enabled", "seed_voice_catalog", "nothing"], env)
    assert removed == ["NEWS_ENABLED"]
    text = env.read_bytes().decode("utf-8")
    today = date.today().isoformat()
    assert text == (
        "# keep me\r\n"
        f"# NEWS_ENABLED=false  (follows INTERNET_ACCESS since {today})\r\n"
        "bot_name=Jeeves\r\n"
        "# SEED_VOICE_CATALOG=true\r\n"
        f"# news_enabled=true  (follows INTERNET_ACCESS since {today})\r\n"
    )
    assert "NEWS_ENABLED" not in config_env_writer.env_file_keys(env)
    assert config_env_writer.remove_env_keys(["news_enabled"], env) == []
    assert config_env_writer.remove_env_keys(["x"], tmp_path / "missing.env") == []
    assert not (tmp_path / "missing.env").exists()
    # no temp files left behind
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


# ─── Runtime: apply, follow again, status ─────────────────────────────────


@pytest.fixture
def live(tmp_path, monkeypatch, with_shazam):
    """The live singleton, as a box that booted UNANSWERED with no .env:
    every profile field at its default, restored afterwards; .env in tmp."""
    env = tmp_path / ".env"
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", env)
    monkeypatch.setattr(settings, "internet_access", "")
    monkeypatch.setattr(settings, "acoustid_api_key", "")
    for name in NAMES:
        monkeypatch.setattr(settings, name, Settings.model_fields[name].default)
    monkeypatch.setattr(config_mod, "HF_HUB_OFFLINE_SET_BY_PROFILE", False)
    with_shazam(True)
    return env


def _save_answer(env, answer: str) -> None:
    """What the save path does before apply_answer: the singleton and .env."""
    settings.internet_access = answer
    config_env_writer.write_env_values({"internet_access": answer}, env)


def test_apply_answer_sets_live_followers_and_lists_restart_ones(live) -> None:
    _save_answer(live, "always")
    result = internet_profile.apply_answer("", "always")
    # lyrics_write_lrc is already on (its own default), so only these move
    assert result.applied == ["music_alias_fetch_enabled", "lyrics_lrclib_enabled"]
    assert settings.music_alias_fetch_enabled is True
    assert settings.lyrics_lrclib_enabled is True and settings.lyrics_write_lrc is True
    # restart-tier: live value untouched, listed when the next boot differs
    assert settings.podcast_feed_poller_enabled is False
    assert sorted(result.restart_required) == ["podcast_feed_poller_enabled", "seed_voice_catalog"]
    assert "internet_access" not in result.restart_required       # not across never


def test_apply_answer_lists_internet_access_across_the_never_boundary(live, monkeypatch) -> None:
    _save_answer(live, "never")
    result = internet_profile.apply_answer("always", "never")
    assert "internet_access" in result.restart_required
    assert result.notes["internet_access"] == internet_profile.HF_RESTART_NOTE
    assert settings.music_alias_fetch_enabled is False
    # both lyrics switches are off at once under never (LRCLIB already was:
    # this box booted unanswered)
    assert settings.lyrics_lrclib_enabled is False and settings.lyrics_write_lrc is False
    assert "lyrics_write_lrc" in result.applied
    assert set(result.restart_required) >= {"news_enabled", "library_enricher_enabled", "seed_voice_catalog"}

    # and back: a process that booted under never has HF_HUB_OFFLINE=1 from
    # the profile; leaving never needs a restart to drop it
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(config_mod, "HF_HUB_OFFLINE_SET_BY_PROFILE", True)
    _save_answer(live, "sometimes")
    back = internet_profile.apply_answer("never", "sometimes")
    assert "internet_access" in back.restart_required


def test_apply_answer_leaves_hand_set_followers_alone(live, monkeypatch) -> None:
    live.write_text("MUSIC_ALIAS_FETCH_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setenv("NEWS_ENABLED", "true")
    _save_answer(live, "always")
    result = internet_profile.apply_answer("", "always")
    assert settings.music_alias_fetch_enabled is False
    assert "music_alias_fetch_enabled" not in result.applied
    _save_answer(live, "never")
    result = internet_profile.apply_answer("always", "never")
    assert "news_enabled" not in result.restart_required           # pinned in the environment
    assert internet_profile.hand_set_fields() == {
        "music_alias_fetch_enabled": "env_file", "news_enabled": "environment",
    }


def test_follow_again_comments_the_line_and_rederives(live, monkeypatch) -> None:
    _save_answer(live, "always")
    config_env_writer.write_env_values({"music_alias_fetch_enabled": False, "news_enabled": False}, live)
    settings.music_alias_fetch_enabled = False
    monkeypatch.setenv("PODCAST_FEED_POLLER_ENABLED", "false")
    followed, refused, restart = internet_profile.follow_again(
        ["music_alias_fetch_enabled", "news_enabled", "podcast_feed_poller_enabled", "bot_name"]
    )
    assert followed == ["music_alias_fetch_enabled", "news_enabled"]
    assert refused == {
        "podcast_feed_poller_enabled": "set in the server's environment (PODCAST_FEED_POLLER_ENABLED); change it there",
        "bot_name": internet_profile.NOT_IN_PROFILE,
    }
    assert settings.music_alias_fetch_enabled is True                 # live: re-derived now
    assert restart == []                                              # news: live True already = derived True
    text = live.read_text(encoding="utf-8")
    assert "# MUSIC_ALIAS_FETCH_ENABLED=false  (follows INTERNET_ACCESS since" in text
    assert "# NEWS_ENABLED=false  (follows INTERNET_ACCESS since" in text
    assert "INTERNET_ACCESS=always" in text


def test_next_boot_value(live, monkeypatch, with_shazam) -> None:
    assert internet_profile.next_boot_value("news_enabled") is True       # unset: the default
    live.write_text("INTERNET_ACCESS=never\n", encoding="utf-8")
    assert internet_profile.next_boot_value("news_enabled") is False      # pending answer
    live.write_text("INTERNET_ACCESS=never\nNEWS_ENABLED=true\n", encoding="utf-8")
    assert internet_profile.next_boot_value("news_enabled") is True       # pinned in .env
    monkeypatch.setenv("NEWS_ENABLED", "0")
    assert internet_profile.next_boot_value("news_enabled") is False      # environment wins
    with_shazam(False)
    live.write_text("INTERNET_ACCESS=always\n", encoding="utf-8")
    assert internet_profile.next_boot_value("library_enricher_enabled") is False
    live.write_text("INTERNET_ACCESS=always\nACOUSTID_API_KEY=k\n", encoding="utf-8")
    assert internet_profile.next_boot_value("library_enricher_enabled") is True


def test_status_document(live, monkeypatch, with_shazam) -> None:
    with_shazam(False)
    doc = internet_profile.status(settings, None)
    assert doc["answer"] == "" and doc["answer_source"] == "unset" and doc["answer_locked"] is False
    assert [c["value"] for c in doc["choices"]] == ["always", "sometimes", "never"]
    assert [c["label"] for c in doc["choices"]] == ["Yes, always", "Sometimes", "No, keep everything in the house"]
    assert "never leave this box" in doc["privacy_note"]
    assert doc["connectivity"]["reason"] == "connected"
    assert doc["restart_required"] == []
    by = {f["name"]: f for f in doc["features"]}
    assert set(by) == PROFILE_FIELD_NAMES
    assert by["news_enabled"]["set_by"] == "default" and by["news_enabled"]["follows"] is False
    enr = by["library_enricher_enabled"]
    assert enr["condition"] == "needs an AcoustID key or the Shazam add-on"
    assert enr["condition_met"] is False
    assert enr["answer_values"] == {"always": False, "sometimes": False, "never": False}
    assert by["news_enabled"]["condition"] is None and by["news_enabled"]["condition_met"] is None
    # never the key itself
    monkeypatch.setattr(settings, "acoustid_api_key", "SECRET-APP-KEY-123")
    doc = internet_profile.status(settings, None)
    assert "SECRET-APP-KEY-123" not in repr(doc)
    assert {f["name"]: f for f in doc["features"]}["library_enricher_enabled"]["condition_met"] is True
    monkeypatch.setattr(settings, "acoustid_api_key", "")

    # answered never, the probe never dialled
    _save_answer(live, "never")
    internet_profile.apply_answer("", "never")
    live.write_text(live.read_text(encoding="utf-8") + "NEWS_ENABLED=true\n", encoding="utf-8")

    class Probe:
        online, reason, target = False, "turned_off", "1.1.1.1:443"
        last_checked_at = last_online_at = None

    with egress.override_policy("never"):
        doc = internet_profile.status(settings, Probe())
    assert doc["answer"] == "never" and doc["answer_source"] == "env_file"
    assert doc["connectivity"] == {"online": False, "reason": "turned_off", "target": "1.1.1.1:443",
                                   "last_checked_at": None, "last_online_at": None}
    by = {f["name"]: f for f in doc["features"]}
    assert by["news_enabled"]["set_by"] == "env_file" and by["news_enabled"]["follows"] is False
    assert by["seed_voice_catalog"]["set_by"] == "answer" and by["seed_voice_catalog"]["follows"] is True
    assert by["seed_voice_catalog"]["value"] is True and by["seed_voice_catalog"]["next_boot_value"] is False
    assert "seed_voice_catalog" in doc["restart_required"]
    assert "news_enabled" not in doc["restart_required"]          # pinned true, live true
    assert "internet_access" in doc["restart_required"]           # HF_HUB_OFFLINE follows at boot
    assert doc["hf_hub_offline"] is False


def test_an_answer_pinned_in_the_environment_is_locked(live, monkeypatch) -> None:
    monkeypatch.setenv("INTERNET_ACCESS", "sometimes")
    assert internet_profile.answer_locked() is True
    assert internet_profile.answer_source() == "environment"
