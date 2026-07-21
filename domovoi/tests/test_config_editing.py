"""Web config editing (Phase A) — registry integrity, value coercion,
and the atomic .env writer. Pure units (no DB / no app), so they run
without the core lifespan."""

from __future__ import annotations

import pytest

from domovoi.config import Settings
from domovoi.config_schema import (
    EDITABLE_FIELDS,
    FieldSpec,
    coerce_and_validate,
)
from domovoi.config_env_writer import write_env_values


# ─── Registry integrity ──────────────────────────────────────────────────


def test_every_editable_field_is_a_real_setting() -> None:
    valid = set(Settings.model_fields)
    for spec in EDITABLE_FIELDS:
        assert spec.name in valid, f"{spec.name} is not a Settings field"


def test_tts_voice_fields_are_excluded() -> None:
    # They come from the Voices registry table, not settings — editing them
    # here would be a silent no-op, so they must not be exposed.
    names = {f.name for f in EDITABLE_FIELDS}
    assert "tts_edge_voice" not in names
    assert "tts_piper_voice" not in names


def test_field_specs_are_well_formed() -> None:
    seen = set()
    for spec in EDITABLE_FIELDS:
        assert spec.name not in seen, f"duplicate field {spec.name}"
        seen.add(spec.name)
        assert spec.help.strip(), f"{spec.name} has no tooltip"
        assert spec.section in ("common", "advanced")
        assert spec.tier in ("hot", "reapply", "restart")
        if spec.type == "choice":
            assert spec.choices, f"{spec.name} is a choice with no choices"
        if spec.min is not None and spec.max is not None:
            assert spec.min <= spec.max, f"{spec.name} has min > max"


# ─── Coercion + validation ───────────────────────────────────────────────


def _spec(**kw) -> FieldSpec:
    base = dict(name="x", label="X", group="G", help="h", type="float")
    base.update(kw)
    return FieldSpec(**base)


def test_coerce_int_rejects_non_integer() -> None:
    spec = _spec(type="int", min=0, max=10)
    assert coerce_and_validate(spec, 3) == 3
    assert coerce_and_validate(spec, "4") == 4
    with pytest.raises(ValueError):
        coerce_and_validate(spec, 3.5)
    with pytest.raises(ValueError):
        coerce_and_validate(spec, "abc")


def test_coerce_float_and_bounds() -> None:
    spec = _spec(type="float", min=0.5, max=2.0)
    assert coerce_and_validate(spec, 1.25) == 1.25
    assert coerce_and_validate(spec, "0.75") == 0.75
    with pytest.raises(ValueError):
        coerce_and_validate(spec, 2.5)        # above max
    with pytest.raises(ValueError):
        coerce_and_validate(spec, 0.1)        # below min


def test_coerce_bool_forms() -> None:
    spec = _spec(type="bool")
    assert coerce_and_validate(spec, True) is True
    assert coerce_and_validate(spec, "false") is False
    assert coerce_and_validate(spec, "on") is True
    assert coerce_and_validate(spec, 0) is False
    with pytest.raises(ValueError):
        coerce_and_validate(spec, "maybe")


def test_coerce_choice() -> None:
    spec = _spec(type="choice", choices=["edge", "piper", "system"])
    assert coerce_and_validate(spec, "piper") == "piper"
    with pytest.raises(ValueError):
        coerce_and_validate(spec, "festival")


# ─── .env writer ─────────────────────────────────────────────────────────


def _parse_env(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip().upper()] = v.strip().strip('"')
    return out


def test_env_writer_creates_file_when_absent(tmp_path) -> None:
    env = tmp_path / ".env"
    assert not env.exists()
    write_env_values({"bot_name": "Jeeves", "tts_speed": 1.2}, env_path=env)
    assert env.exists()
    parsed = _parse_env(env.read_text(encoding="utf-8"))
    assert parsed["BOT_NAME"] == "Jeeves"
    assert parsed["TTS_SPEED"] == "1.2"


def test_env_writer_merges_and_preserves(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# operator note\n"
        "DATABASE_URL=postgresql://keep/me\n"
        "BOT_NAME=Domovoi\n",
        encoding="utf-8",
    )
    write_env_values({"bot_name": "Alfred", "log_level": "DEBUG"}, env_path=env)
    text = env.read_text(encoding="utf-8")
    parsed = _parse_env(text)
    # Edited key rewritten in place; untouched key + comment preserved;
    # new key appended.
    assert parsed["BOT_NAME"] == "Alfred"
    assert parsed["DATABASE_URL"] == "postgresql://keep/me"
    assert parsed["LOG_LEVEL"] == "DEBUG"
    assert "# operator note" in text


def test_env_writer_quotes_values_with_spaces(tmp_path) -> None:
    env = tmp_path / ".env"
    write_env_values({"music_dir": "C:/Users/Kamron/My Music"}, env_path=env)
    parsed = _parse_env(env.read_text(encoding="utf-8"))
    assert parsed["MUSIC_DIR"] == "C:/Users/Kamron/My Music"
