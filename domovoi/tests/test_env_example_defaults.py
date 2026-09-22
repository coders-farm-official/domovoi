"""`.env.example` must agree with `Settings` on the local-first defaults.

A fresh box is set up by copying `.env.example` to `.env`, so the example
file IS the effective default — whatever `Settings` says. Until 2026-09-11
the two disagreed on the one value that matters most: `Settings.tts_engine`
defaulted to `piper` (local) with a paragraph explaining why, docs/FAQ.md
promised "Edge TTS — opt-in, not the default", and `.env.example` shipped
`TTS_ENGINE=edge`. Every new box sent its response text to Microsoft while
the code and the docs both said it wouldn't.

It was sticky, too: on first boot the voice registry promotes the
configured engine's voice as the default and then preserves it forever, so
even flipping `.env` back to `piper` afterwards changed nothing.

Pure file checks, no DB, no `requires_db` — this must never skip.
"""

from __future__ import annotations

from pathlib import Path

from domovoi.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / "domovoi" / ".env.example"


def _example_values() -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def test_env_example_exists() -> None:
    assert ENV_EXAMPLE.is_file(), f"missing {ENV_EXAMPLE}"


def test_settings_default_engine_is_local() -> None:
    """The product promise itself — guard it independently of the example."""
    assert Settings.model_fields["tts_engine"].default == "piper"


def test_env_example_tts_engine_matches_settings_default() -> None:
    """The example is what a new box actually runs. It must not quietly
    opt the household into a cloud engine the code says is opt-in."""
    env = _example_values()
    assert "TTS_ENGINE" in env, ".env.example no longer sets TTS_ENGINE"
    assert env["TTS_ENGINE"] == Settings.model_fields["tts_engine"].default


def test_env_example_does_not_default_to_a_cloud_engine() -> None:
    """Belt and braces: even if Settings changes, the shipped example must
    not send every spoken response off-box by default."""
    assert _example_values().get("TTS_ENGINE") != "edge"


# ─── Where the example deliberately DISAGREES with Settings (CORE-9) ──────
#
# Strict satellite pairing is the one setting where "what a fresh box
# runs" and "what the code defaults to" are meant to differ. A fresh box
# has nothing paired, so it can start closed. A box that upgrades into
# this code has satellites in the house already, and a field default that
# flipped under it would park the whole fleet on the next restart.

SCRIPTS = REPO_ROOT / "domovoi" / "scripts"


def test_a_fresh_install_starts_with_strict_satellite_pairing() -> None:
    env = _example_values()
    assert env.get("SATELLITE_PAIRING_STRICT") == "true", (
        ".env.example no longer starts a fresh install with strict pairing"
    )


def test_the_field_default_stays_lenient_for_installs_that_upgrade() -> None:
    assert Settings.model_fields["satellite_pairing_strict"].default is False


def test_the_dev_scripts_create_an_env_only_when_there_is_none() -> None:
    """The scripts may bootstrap a missing .env from the example. They must
    never rewrite one that exists — that file is the household's posture,
    including the satellites it has already let in.

    Both scripts delegate to ``python -m domovoi.env_bootstrap`` (OPS-1),
    which copies the example with a random Postgres password and opens the
    target ``O_EXCL`` so an existing file is never truncated; see
    ``test_env_bootstrap.py``. What is checked here is that the scripts do
    not ALSO copy the file themselves — an unguarded ``cp`` / ``Copy-Item``
    beside the bootstrap would be exactly the rewrite this forbids.
    """
    sh = (SCRIPTS / "dev.sh").read_text(encoding="utf-8")
    assert "python -m domovoi.env_bootstrap" in sh, (
        "dev.sh no longer bootstraps .env through domovoi.env_bootstrap"
    )
    assert not [
        l for l in sh.splitlines()
        if l.strip().startswith("cp ") and ".env" in l
    ], "dev.sh copies .env itself instead of going through env_bootstrap"

    ps1 = (SCRIPTS / "dev.ps1").read_text(encoding="utf-8")
    assert "python -m domovoi.env_bootstrap" in ps1, (
        "dev.ps1 no longer bootstraps .env through domovoi.env_bootstrap"
    )
    assert not [
        l for l in ps1.splitlines() if "Copy-Item" in l and ".env" in l
    ], "dev.ps1 copies .env itself instead of going through env_bootstrap"


def test_a_bootstrapped_env_carries_the_examples_strict_pairing() -> None:
    """The composition that matters: the bootstrap rewrites only the
    Postgres credential, so the .env a fresh install actually gets still
    carries the example's `SATELLITE_PAIRING_STRICT=true`."""
    from domovoi.env_bootstrap import render_fresh_env

    rendered = render_fresh_env(
        ENV_EXAMPLE.read_text(encoding="utf-8"), "a-random-password"
    )
    values = {}
    for raw in rendered.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    assert values.get("SATELLITE_PAIRING_STRICT") == "true"
