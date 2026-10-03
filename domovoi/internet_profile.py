"""The runtime half of the internet answer (``INTERNET_ACCESS``).

``domovoi/config.py`` derives the defaults of the online extras from the
answer at BOOT (``PROFILE_DEFAULTS`` and the ``Settings`` validator). This
module is what happens while the core runs:

* :func:`apply_answer` — the answer was just saved (Settings → Internet,
  the first-run step, ``POST /v1/admin/config``): re-derive every setting
  that follows it. Live-tier followers change now; restart-tier followers
  keep their boot value and are listed as needing a restart, like any
  restart-tier save.
* :func:`follow_again` — "follow the answer again" on a setting somebody
  pinned by hand: its ``.env`` line is COMMENTED OUT (never deleted), and
  it re-derives.
* :func:`status` — the document behind ``GET /v1/admin/internet``, which
  renders Settings → Internet.

"Set by hand" at runtime means the key's UPPER name is in the process
environment, or is an uncommented line in ``domovoi/.env``
(:func:`domovoi.config_env_writer.env_file_keys`). That equals
``model_fields_set`` at boot plus every dashboard save since, because a
save writes ``.env``.

The answer the profile derives from is ``settings.internet_access`` — the
core's own copy, kept equal to what :func:`domovoi.egress.policy` reads
(the save path writes both, and refuses to change an answer pinned in the
process environment, so the two processes never disagree).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from domovoi import config_env_writer, egress
from domovoi.config import (
    PROFILE_BY_NAME,
    PROFILE_CONDITIONS,
    PROFILE_DEFAULTS,
    PROFILE_FIELD_NAMES,
    ProfileDefault,
    Settings,
    profile_condition_met,
    profile_value,
    settings,
)

log = logging.getLogger(__name__)

HF_RESTART_NOTE = "the speech models switch their download checks after a restart"
NOT_IN_PROFILE = "not a setting that follows the internet answer"
ANSWER_LOCKED = "set in the server's environment (INTERNET_ACCESS); change it there"

# What Settings → Internet shows for each answer (contract 5.7.4). The
# Windows installer's question uses these labels too
# (windows-installer/INTERNET-PROFILE.md §3).
CHOICES: list[dict[str, str]] = [
    {
        "value": "always",
        "label": "Yes, always",
        "summary": "It's on our home internet.",
        "detail": (
            "Turns on the extras that use the internet: web answers (weather, "
            "scores, news), finding artists by the names people actually say, "
            "podcast downloads. They pause by themselves if the internet drops."
        ),
    },
    {
        "value": "sometimes",
        "label": "Sometimes",
        "summary": "The connection comes and goes, or it's slow or metered.",
        "detail": (
            "Small lookups run whenever the internet is up. Big automatic "
            "downloads stay off until you turn them on."
        ),
    },
    {
        "value": "never",
        "label": "No, keep everything in the house",
        "summary": "There's no internet here, or I don't want Domovoi to use it.",
        "detail": (
            "Domovoi won't contact the internet at all. Online extras are "
            "switched off; music, timers, intercom, voice and everything else "
            "on your network work as normal."
        ),
    },
]

PRIVACY_NOTE = (
    "Your voice and recordings never leave this box, whatever you answer. "
    "With the internet on, some extras send short text out, such as a web "
    "search you asked for or an artist's name."
)

# Strong refs to the fire-and-forget probe re-checks (a bare create_task
# can be garbage-collected mid-flight).
_PENDING: set[asyncio.Task[Any]] = set()


@dataclass
class InternetApplyResult:
    applied: list[str] = field(default_factory=list)
    restart_required: list[str] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)


# ─── Who set what ─────────────────────────────────────────────────────────


def _in_environment(name: str) -> bool:
    key = name.upper()
    return any(k.upper() == key for k in os.environ)


def _environment_value(name: str) -> str | None:
    key = name.upper()
    for k, v in os.environ.items():
        if k.upper() == key:
            return v
    return None


def hand_set_fields() -> dict[str, str]:
    """``name → "environment" | "env_file"`` for every profile field set by
    hand. The environment wins (it shadows ``.env``)."""
    file_keys = config_env_writer.env_file_keys()
    out: dict[str, str] = {}
    for pd in PROFILE_DEFAULTS:
        if _in_environment(pd.name):
            out[pd.name] = "environment"
        elif pd.name.upper() in file_keys:
            out[pd.name] = "env_file"
    return out


def answer_source() -> str:
    """Where the answer comes from: ``environment``, ``env_file`` or
    ``unset``."""
    if _in_environment("internet_access"):
        return "environment"
    if "INTERNET_ACCESS" in config_env_writer.env_file_keys():
        return "env_file"
    return "unset"


def answer_locked() -> bool:
    """An answer pinned in the process environment can't be changed from
    the dashboard: the web process would keep reading the environment's
    value while the core read the saved one."""
    return _in_environment("internet_access")


def _coerce(name: str, raw: object) -> object:
    """``raw`` (a string from the environment or ``.env``) as the field's
    own type, the way pydantic-settings will read it at boot."""
    try:
        annotation = Settings.model_fields[name].annotation
        return TypeAdapter(annotation).validate_python(raw)
    except Exception:
        return raw


def _field_default(name: str) -> object:
    return Settings.model_fields[name].default


class _NextBoot:
    """Attribute view of the settings the NEXT boot will read: a pending
    environment / ``.env`` value where there is one, else the live value.
    Only what ``profile_value`` consults (the AcoustID key)."""

    def __getattr__(self, name: str) -> object:
        return _coerce(name, config_env_writer.next_boot_value(name, getattr(settings, name)))


def _derived(pd: ProfileDefault, answer: str, s: object) -> object:
    value = profile_value(pd, answer, s)
    return _field_default(pd.name) if value is None else value


def next_boot_answer() -> str:
    return egress.normalize_policy(
        config_env_writer.next_boot_value("internet_access", settings.internet_access)
    )


def next_boot_value(name: str) -> object:
    """What profile field ``name`` will be after a restart: its hand-set
    value when there is one, else what the (pending) answer derives, else
    the field's own default."""
    pd = PROFILE_BY_NAME[name]
    hand = _environment_value(name)
    if hand is not None:
        return _coerce(name, hand)
    if name.upper() in config_env_writer.env_file_keys():
        return _coerce(name, config_env_writer.next_boot_value(name, getattr(settings, name)))
    return _derived(pd, next_boot_answer(), _NextBoot())


# ─── Hugging Face (D10) ───────────────────────────────────────────────────

_TRUTHY = ("1", "true", "yes", "on")


def hf_hub_offline_now() -> bool:
    """Whether huggingface_hub in this process runs offline (it read
    HF_HUB_OFFLINE when it was imported)."""
    return os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in _TRUTHY


def hf_hub_offline_next_boot(answer: str) -> bool:
    """What the next boot will set: a value set by hand wins; otherwise
    offline exactly when the answer is ``never``."""
    from domovoi import config as config_mod

    hand = None if config_mod.HF_HUB_OFFLINE_SET_BY_PROFILE else os.environ.get("HF_HUB_OFFLINE")
    if hand:
        return hand.strip().lower() in _TRUTHY
    return egress.normalize_policy(answer) == "never"


# ─── Re-deriving ──────────────────────────────────────────────────────────


def _rederive(names: list[str], answer: str, result: InternetApplyResult) -> None:
    """Re-derive the given profile fields under ``answer``. Live-tier ones
    go onto the singleton now (object.__setattr__ — the filled value is
    not "set"); restart-tier ones are listed when their next-boot value
    differs from the live one."""
    hand = hand_set_fields()
    for name in names:
        pd = PROFILE_BY_NAME[name]
        if name in hand:
            continue
        target = _derived(pd, answer, settings)
        live = getattr(settings, name)
        if pd.applies == "live":
            if live != target:
                object.__setattr__(settings, name, target)
                result.applied.append(name)
        elif live != target and name not in result.restart_required:
            result.restart_required.append(name)


def schedule_probe_recheck() -> None:
    """Ask the connectivity probe to look again now (the gate already
    applies; the probe's reported state catches up). Best effort: no probe
    or no running loop is fine."""
    from domovoi import connectivity

    probe = connectivity.current_probe()
    if probe is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(probe.check_now(), name="connectivity-recheck")
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


def apply_answer(old: str, new: str) -> InternetApplyResult:
    """The answer changed from ``old`` to ``new`` (already on the settings
    singleton and in ``.env``): re-derive every follower, list what needs a
    restart, and have the probe look again."""
    old = egress.normalize_policy(old)
    new = egress.normalize_policy(new)
    result = InternetApplyResult()
    _rederive([pd.name for pd in PROFILE_DEFAULTS], new, result)
    if (old == "never") != (new == "never") and hf_hub_offline_now() != hf_hub_offline_next_boot(new):
        result.restart_required.append("internet_access")
        result.notes["internet_access"] = HF_RESTART_NOTE
    log.info(
        "internet answer %s → %s: applied %s; restart for %s",
        old or "(not answered)", new or "(not answered)",
        ", ".join(result.applied) or "nothing", ", ".join(result.restart_required) or "nothing",
    )
    schedule_probe_recheck()
    return result


def follow_again(names: list[str]) -> tuple[list[str], dict[str, str], list[str]]:
    """Let hand-set profile fields follow the answer again: comment their
    ``.env`` lines out and re-derive. Returns ``(followed, refused,
    restart_required)``. A name outside the profile, or one set in the
    process environment, is refused (with the reason)."""
    followed: list[str] = []
    refused: dict[str, str] = {}
    for name in names:
        if name in followed or name in refused:
            continue
        if name not in PROFILE_FIELD_NAMES:
            refused[name] = NOT_IN_PROFILE
        elif _in_environment(name):
            refused[name] = f"set in the server's environment ({name.upper()}); change it there"
        else:
            followed.append(name)
    if not followed:
        return [], refused, []
    config_env_writer.remove_env_keys([n.upper() for n in followed])
    result = InternetApplyResult()
    _rederive(followed, settings.internet_access, result)
    log.info("internet answer: %s follow the answer again", ", ".join(followed))
    return followed, refused, result.restart_required


# ─── Settings → Internet ──────────────────────────────────────────────────


def _connectivity(probe: Any) -> dict[str, Any]:
    turned_off = egress.internet_turned_off()
    if probe is None:
        return {
            "online": not turned_off,
            "reason": "turned_off" if turned_off else "connected",
            "target": settings.connectivity_probe_target,
            "last_checked_at": None,
            "last_online_at": None,
        }
    online = bool(probe.online) and not turned_off
    if turned_off:
        reason = "turned_off"
    else:
        own = getattr(probe, "reason", None)
        reason = own if own in ("connected", "offline") else ("connected" if online else "offline")
    iso = lambda v: v.isoformat() if v is not None else None  # noqa: E731
    return {
        "online": online,
        "reason": reason,
        "target": probe.target,
        "last_checked_at": iso(getattr(probe, "last_checked_at", None)),
        "last_online_at": iso(getattr(probe, "last_online_at", None)),
    }


def network_plugins() -> list[dict[str, Any]]:
    """Loaded plugins whose manifest asks for the network
    (``permissions.network = true``). A plugin that uses the SDK's HTTP
    client follows the answer; one with its own HTTP code or a
    ``docker pull`` may not. ``bundled`` marks the plugins shipped in this
    repo (the radio plugin), which follow it."""
    try:
        from domovoi.plugins_runtime.loader import LOADER, bundled_root

        root = bundled_root().resolve()
        out: list[dict[str, Any]] = []
        for slug, lp in sorted(LOADER.loaded.items()):
            perms = getattr(lp.manifest, "permissions", {}) or {}
            if not perms.get("network"):
                continue
            try:
                bundled = Path(lp.install_dir).resolve().is_relative_to(root)
            except (OSError, ValueError):
                bundled = False
            out.append({"slug": slug, "name": lp.manifest.name or slug, "bundled": bundled})
        return out
    except Exception as e:  # noqa: BLE001 — a status page must render
        log.debug("internet status: couldn't list network plugins: %s", e)
        return []


def _is_cloud_model(name: object) -> bool:
    text = str(name or "").strip().lower()
    return text.endswith("-cloud") or text.endswith(":cloud")


def never_warnings(s: Any = None) -> list[str]:
    """Things a box answered No still sends out, which the gate can't stop
    because the core treats them as local: a language-model server
    (``OLLAMA_URL``) that is not on this network, and an Ollama cloud
    model (local Ollama forwards those to ollama.com). Empty unless the
    answer is ``never``."""
    s = settings if s is None else s
    if egress.normalize_policy(getattr(s, "internet_access", "")) != "never":
        return []
    out: list[str] = []
    url = str(getattr(s, "ollama_url", "") or "")
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    if host and not egress.is_local_host(host):
        out.append(
            f"The language model server ({url}) is not on this network, so every "
            "question you ask Domovoi goes to it. Point OLLAMA_URL at an Ollama in "
            "the house to keep them here."
        )
    for field_name in ("ollama_model", "ollama_tool_model", "ollama_vision_model"):
        model = getattr(s, field_name, "")
        if _is_cloud_model(model):
            out.append(
                f"The model {model} ({field_name.upper()}) runs in Ollama's cloud, so "
                "what you ask it leaves the house. Pick a model that runs on this box."
            )
    return out


def status(s: Any = None, probe: Any = None) -> dict[str, Any]:
    """The Settings → Internet document (``GET /v1/admin/internet``)."""
    s = settings if s is None else s
    answer = egress.normalize_policy(getattr(s, "internet_access", ""))
    hand = hand_set_fields()
    boot_answer = next_boot_answer()
    features: list[dict[str, Any]] = []
    restart_required: list[str] = []
    for pd in PROFILE_DEFAULTS:
        live = getattr(s, pd.name)
        nxt = next_boot_value(pd.name)
        set_by = hand.get(pd.name) or ("answer" if answer else "default")
        met = profile_condition_met(pd, s)
        features.append({
            "name": pd.name,
            "label": pd.label,
            "value": live,
            "next_boot_value": nxt,
            "answer_values": {a: profile_value(pd, a, s) for a in egress.POLICIES},
            "follows": bool(answer) and pd.name not in hand,
            "set_by": set_by,
            "applies": pd.applies,
            "condition": PROFILE_CONDITIONS.get(pd.requires) if pd.requires else None,
            "condition_met": met,
        })
        if pd.applies == "restart" and nxt != live:
            restart_required.append(pd.name)
    hf_now = hf_hub_offline_now()
    if hf_now != hf_hub_offline_next_boot(boot_answer):
        restart_required.append("internet_access")
    from domovoi import searxng_service

    return {
        "answer": answer,
        "answer_locked": answer_locked(),
        "answer_source": answer_source(),
        "choices": [dict(c) for c in CHOICES],
        "privacy_note": PRIVACY_NOTE,
        "connectivity": _connectivity(probe),
        "hf_hub_offline": hf_now,
        "features": features,
        "restart_required": restart_required,
        # The local search helper behind web answers (SearXNG): what the
        # last start/stop in this process did, and whether one runs now.
        "search_helper": searxng_service.status(),
        # Add-ons that may reach the internet with their own code.
        "network_plugins": network_plugins(),
        # Under never: what still leaves the house that the gate can't see.
        "warnings": never_warnings(s),
    }


__all__ = [
    "ANSWER_LOCKED",
    "CHOICES",
    "HF_RESTART_NOTE",
    "InternetApplyResult",
    "NOT_IN_PROFILE",
    "PRIVACY_NOTE",
    "answer_locked",
    "answer_source",
    "apply_answer",
    "follow_again",
    "hand_set_fields",
    "next_boot_value",
    "schedule_probe_recheck",
    "status",
]
