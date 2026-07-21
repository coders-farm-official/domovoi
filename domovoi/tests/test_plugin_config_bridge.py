"""Plugin config contribution (design §4.6): FieldSpec validation,
kind=secret masking, per-plugin .env persistence, and reapply hooks."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

from domovoi.plugins_runtime.config_bridge import (
    FieldSpec,
    PluginConfigRegistry,
)

SLUG = "cfgtest"


class CfgTestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CFGTEST_", extra="ignore")

    sample_interval_sec: float = 30.0
    station_limit: int = 5
    api_token: str = ""


SPECS = [
    FieldSpec(
        name="sample_interval_sec", label="Sample interval",
        help="Seconds between samples.", group="Sampling", tier="reapply",
        kind="float",
    ),
    FieldSpec(
        name="station_limit", label="Station limit",
        help="Max stations.", group="Sampling", tier="hot", kind="int",
    ),
    FieldSpec(
        name="api_token", label="API token",
        help="Long-lived upstream token.", group="Auth", kind="secret",
    ),
]


@pytest.fixture
def registry(tmp_path: Path) -> PluginConfigRegistry:
    r = PluginConfigRegistry()
    r.register(SLUG, CfgTestSettings, SPECS, env_file=tmp_path / f"{SLUG}.env")
    return r


def test_fieldspec_names_must_be_model_fields(tmp_path: Path) -> None:
    r = PluginConfigRegistry()
    bad = [FieldSpec(name="nope", label="x", help="x", group="x")]
    with pytest.raises(ValueError, match="nope"):
        r.register(SLUG, CfgTestSettings, bad, env_file=tmp_path / "x.env")


def test_fieldspec_cannot_target_satellite_config(tmp_path: Path) -> None:
    from domovoi.satellite_config_schema import EDITABLE_FIELDS

    sat_field = EDITABLE_FIELDS[0].name

    class Colliding(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="CFGTEST_", extra="ignore")
        pass

    Colliding.model_fields[sat_field] = CfgTestSettings.model_fields[
        "station_limit"
    ]
    r = PluginConfigRegistry()
    spec = [FieldSpec(name=sat_field, label="x", help="x", group="x")]
    try:
        with pytest.raises(ValueError, match="satellite"):
            r.register(SLUG, Colliding, spec, env_file=tmp_path / "x.env")
    finally:
        Colliding.model_fields.pop(sat_field, None)


def test_select_kind_requires_choices(tmp_path: Path) -> None:
    r = PluginConfigRegistry()
    spec = [
        FieldSpec(name="station_limit", label="x", help="x", group="x",
                  kind="select")
    ]
    with pytest.raises(ValueError, match="choices"):
        r.register(SLUG, CfgTestSettings, spec, env_file=tmp_path / "x.env")


def test_secret_masked_in_dashboard_rows(registry: PluginConfigRegistry) -> None:
    rows = {row["name"]: row for row in registry.dashboard_group(SLUG)}
    assert rows["api_token"]["value"] == "not set"
    registry.write_values(SLUG, {"api_token": "super-secret-token"})
    rows = {row["name"]: row for row in registry.dashboard_group(SLUG)}
    assert rows["api_token"]["value"] == "•••• set"
    assert "super-secret-token" not in str(rows)
    # Non-secret values render plainly, tagged with the plugin slug.
    assert rows["station_limit"]["value"] == 5
    assert rows["station_limit"]["plugin"] == SLUG


def test_write_values_persists_and_reapplies(
    registry: PluginConfigRegistry, tmp_path: Path
) -> None:
    fired: list[str] = []
    registry.on_reapply(SLUG, "sample_interval_sec", lambda: fired.append("hook"))

    restart = registry.write_values(
        SLUG, {"sample_interval_sec": 12.5, "station_limit": 9}
    )
    assert restart == []                        # no restart-tier fields here
    assert fired == ["hook"]                    # reapply tier ran its hook
    inst = registry.get(SLUG)
    assert inst.sample_interval_sec == 12.5     # live apply
    assert inst.station_limit == 9

    env = (tmp_path / f"{SLUG}.env").read_text(encoding="utf-8")
    assert "CFGTEST_SAMPLE_INTERVAL_SEC=12.5" in env
    assert "CFGTEST_STATION_LIMIT=9" in env


def test_write_values_rejects_unknown_and_invalid(
    registry: PluginConfigRegistry,
) -> None:
    with pytest.raises(ValueError, match="unknown"):
        registry.write_values(SLUG, {"not_a_field": 1})
    with pytest.raises(ValueError):
        registry.write_values(SLUG, {"station_limit": "not-an-int"})


def test_secret_value_never_echoes_in_errors(
    registry: PluginConfigRegistry,
) -> None:
    # station_limit invalid AND a secret in the same write: the raised
    # message must not carry the secret text.
    with pytest.raises(ValueError) as exc:
        registry.write_values(
            SLUG, {"station_limit": "bogus", "api_token": "hunter2-secret"}
        )
    assert "hunter2-secret" not in str(exc.value)


def test_on_reapply_requires_declared_field(
    registry: PluginConfigRegistry,
) -> None:
    with pytest.raises(ValueError, match="api_missing"):
        registry.on_reapply(SLUG, "api_missing", lambda: None)


def test_unregister_teardown(registry: PluginConfigRegistry) -> None:
    registry.unregister(SLUG)
    assert registry.get(SLUG) is None
    assert registry.dashboard_group(SLUG) == []
