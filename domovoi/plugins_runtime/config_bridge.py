"""Plugin config contribution — FieldSpec + reapply hooks (design §4.6).

A plugin ships a pydantic ``BaseSettings`` subclass (env prefix
``<SLUG>_`` by convention) and a list of :class:`FieldSpec` rows for the
dashboard. Values persist to ``~/.domovoi/plugins/<slug>.env`` via the
same atomic merge-writer core config uses; OS env vars shadow the file
(documented caveat).

Validation at registration (the "name must be a real Settings
field" check, generalized — §13.2 check 4):

* every FieldSpec name must be a field of the settings model;
* no FieldSpec may target a satellite-config field (v1 lockstep rule);
* ``kind="secret"`` fields are masked (``"•••• set"`` / ``"not set"``)
  in every config read and never echoed in validation errors or logs.

The **reapply-hook registry** replaces hardcoded if/elif dispatch:
``on_reapply(slug, field, cb)`` callbacks run after a ``tier="reapply"``
field is written.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Callable, Literal

from domovoi.config_env_writer import write_env_values

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldSpec:
    """One dashboard-editable plugin setting (design §4.6, normative)."""

    name: str                # MUST be a field of the settings model
    label: str               # dashboard row label
    help: str                # one-sentence help text under the control
    group: str               # grouping header within the plugin's section
    tier: Literal["hot", "reapply", "restart"] = "hot"
    kind: Literal["text", "int", "float", "bool", "select", "secret"] = "text"
    choices: tuple[str, ...] = ()      # kind="select" only


@dataclass
class _PluginConfig:
    slug: str
    model_cls: type
    fieldspecs: list[FieldSpec]
    instance: Any
    env_file: Path
    reapply_hooks: dict[str, list[Callable[[], None]]] = dc_field(
        default_factory=dict
    )


def plugin_env_file(slug: str) -> Path:
    return Path.home() / ".domovoi" / "plugins" / f"{slug}.env"


def _satellite_field_names() -> frozenset[str]:
    from domovoi.satellite_config_schema import FIELD_BY_NAME

    return frozenset(FIELD_BY_NAME)


class PluginConfigRegistry:
    """All registered plugin settings models, keyed by slug."""

    def __init__(self) -> None:
        self._configs: dict[str, _PluginConfig] = {}

    # ── registration (ctx.add_config) ──────────────────────────────────────

    def register(
        self,
        slug: str,
        model_cls: type,
        fieldspecs: list[FieldSpec],
        *,
        env_file: Path | None = None,
    ) -> Any:
        """Validate + instantiate the plugin's settings. Returns the live
        settings instance (also stamped onto the plugin's SDK facade)."""
        model_fields = getattr(model_cls, "model_fields", None)
        if model_fields is None:
            raise TypeError(
                f"plugin {slug!r}: add_config expects a pydantic BaseSettings "
                f"subclass, got {model_cls!r}"
            )
        satellite_fields = _satellite_field_names()
        for spec in fieldspecs:
            if spec.name not in model_fields:
                raise ValueError(
                    f"plugin {slug!r}: FieldSpec {spec.name!r} is not a field "
                    f"of {model_cls.__name__} (fields: "
                    f"{sorted(model_fields)})"
                )
            if spec.name in satellite_fields:
                raise ValueError(
                    f"plugin {slug!r}: FieldSpec {spec.name!r} collides with a "
                    f"satellite config field — plugin FieldSpecs cannot target "
                    f"satellite config in v1 (design §4.6)"
                )
            if spec.kind == "select" and not spec.choices:
                raise ValueError(
                    f"plugin {slug!r}: FieldSpec {spec.name!r} is kind='select' "
                    f"but declares no choices"
                )

        env_path = env_file or plugin_env_file(slug)
        try:
            instance = model_cls(_env_file=str(env_path) if env_path.exists() else None)
        except TypeError:
            instance = model_cls()
        cfg = _PluginConfig(
            slug=slug,
            model_cls=model_cls,
            fieldspecs=list(fieldspecs),
            instance=instance,
            env_file=env_path,
        )
        self._configs[slug] = cfg
        return instance

    def on_reapply(self, slug: str, field_name: str, cb: Callable[[], None]) -> None:
        cfg = self._configs.get(slug)
        if cfg is None:
            raise KeyError(f"plugin {slug!r} has no registered config")
        if all(spec.name != field_name for spec in cfg.fieldspecs):
            raise ValueError(
                f"plugin {slug!r}: on_reapply target {field_name!r} is not a "
                f"declared FieldSpec"
            )
        cfg.reapply_hooks.setdefault(field_name, []).append(cb)

    def unregister(self, slug: str) -> None:
        self._configs.pop(slug, None)

    # ── reads (dashboard) ───────────────────────────────────────────────────

    def get(self, slug: str) -> Any | None:
        cfg = self._configs.get(slug)
        return cfg.instance if cfg else None

    def specs(self, slug: str) -> list[FieldSpec]:
        cfg = self._configs.get(slug)
        return list(cfg.fieldspecs) if cfg else []

    def slugs(self) -> list[str]:
        return sorted(self._configs)

    def dashboard_group(self, slug: str) -> list[dict[str, Any]]:
        """Rows for GET /v1/admin/config (admin-gated — §4.6): every
        FieldSpec joined with the live value; secrets masked."""
        cfg = self._configs.get(slug)
        if cfg is None:
            return []
        rows: list[dict[str, Any]] = []
        for spec in cfg.fieldspecs:
            value = getattr(cfg.instance, spec.name, None)
            if spec.kind == "secret":
                value = "•••• set" if value else "not set"
            rows.append(
                {
                    "name": spec.name,
                    "label": spec.label,
                    "help": spec.help,
                    "group": spec.group,
                    "tier": spec.tier,
                    "kind": spec.kind,
                    "choices": list(spec.choices),
                    "value": value,
                    "plugin": slug,
                }
            )
        return rows

    # ── writes ───────────────────────────────────────────────────────────────

    def write_values(self, slug: str, changes: dict[str, Any]) -> list[str]:
        """Validate + apply + persist changed fields. Returns the names of
        fields whose tier is ``restart`` (dashboard chip). Secret values
        are never echoed in error messages."""
        cfg = self._configs.get(slug)
        if cfg is None:
            raise KeyError(f"plugin {slug!r} has no registered config")
        spec_by_name = {s.name: s for s in cfg.fieldspecs}
        unknown = [k for k in changes if k not in spec_by_name]
        if unknown:
            raise ValueError(
                f"plugin {slug!r}: unknown config field(s) {sorted(unknown)}"
            )

        # Validate through the model: rebuild with merged values so pydantic
        # coercion/validators run; a secret field's value is redacted from
        # any raised message.
        merged = {**cfg.instance.model_dump(), **changes}
        try:
            new_instance = cfg.model_cls.model_validate(merged)
        except Exception as e:
            msg = str(e)
            for name, value in changes.items():
                if spec_by_name[name].kind == "secret" and value:
                    msg = msg.replace(str(value), "••••")
            raise ValueError(f"plugin {slug!r}: config rejected: {msg}") from None

        # Apply live (mutate the existing instance so SDK references hold).
        for name in changes:
            setattr(cfg.instance, name, getattr(new_instance, name))

        # Persist with the plugin's env prefix so BaseSettings re-reads it.
        prefix = self._env_prefix(cfg)
        cfg.env_file.parent.mkdir(parents=True, exist_ok=True)
        write_env_values(
            {f"{prefix}{name}".upper(): getattr(new_instance, name) for name in changes},
            env_path=cfg.env_file,
        )

        restart_needed: list[str] = []
        for name in changes:
            tier = spec_by_name[name].tier
            if tier == "reapply":
                for cb in cfg.reapply_hooks.get(name, []):
                    try:
                        cb()
                    except Exception as e:  # noqa: BLE001 — hook isolation
                        log.warning(
                            "plugin %s reapply hook for %s failed: %s",
                            slug, name, e,
                        )
            elif tier == "restart":
                restart_needed.append(name)
        return restart_needed

    @staticmethod
    def _env_prefix(cfg: _PluginConfig) -> str:
        model_config = getattr(cfg.model_cls, "model_config", {}) or {}
        prefix = ""
        if isinstance(model_config, dict):
            prefix = model_config.get("env_prefix", "") or ""
        else:  # pydantic SettingsConfigDict behaves like a mapping
            prefix = getattr(model_config, "get", lambda *_: "")("env_prefix", "")
        return prefix or f"{cfg.slug.upper()}_"


PLUGIN_CONFIG = PluginConfigRegistry()
