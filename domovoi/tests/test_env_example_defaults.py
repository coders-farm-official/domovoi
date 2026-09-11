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
