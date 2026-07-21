"""Install-time contract checks (design §13.2) — run at every load
(install step 13, enable, boot). Failure ⇒ ``load_error``, never a crash.

Checks:

1. Handler contract: ``name == tool_schema["name"]``; band in a
   plugin-usable range; unanchored greedy catch-all ⇒ band ≥ 900;
   ``requires_network != "no"`` ⇒ ``fallback_offline`` overridden;
   ``confirmation_kinds`` all ``<slug>.``-prefixed; ``display`` present;
   ``offline_ok`` only set on degraded handlers' paths.
2. **Utterance-corpus collision test**: the core-owned corpus merged with
   every currently-enabled plugin's manifest-declared ``corpus`` phrases,
   routed through the merged registry dry-run — every utterance must
   still resolve to its owning handler.
3. Manifest/code drift: declared handlers/workers/startup hooks/
   capabilities ⊆ registered and vice versa; **value mismatches are hard
   failures naming both values** (code is authoritative).
4. Config FieldSpecs — validated at registration by
   :mod:`.config_bridge`; re-surfaced here as contract failures.
5. Import-time budget (< 10 s) + best-effort CUDA-init check.
6. Router auth audit — plugin routers mounted through the SDK are gated
   by construction (§4.11); anything mounted around it is warn-flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from domovoi.handlers.base import Handler, as_fast_path, registry_sort_key
from domovoi.plugins_runtime.manifest import (
    PLUGIN_BAND_MAX,
    PLUGIN_BAND_MIN,
    PluginManifest,
)

IMPORT_TIME_BUDGET_SEC = 10.0

# Core capability slugs (design §12) — always satisfy `consumes`.
CORE_CAPABILITY_SLUGS = frozenset(
    {
        "media-acquisition-queue",
        "event-bus",
        "playback",
        "library",
        "sessions",
        "realtime-notify",
        "canned-sounds",
    }
)

# The core-owned collision corpus (design §13.2 check 2): one canonical
# utterance per ordering guarantee. Kept in sync with the §4.2
# band table; test_router.py exercises the same phrases.
CORE_CORPUS: list[tuple[str, str]] = [
    ("i'm good", "dismiss"),
    ("i'm fine", "dismiss"),
    ("never mind", "dismiss"),
    ("false alarm", "dismiss"),
    ("i'm sarah", "voice_profile"),
    ("forget me", "voice_profile"),
    ("who am i", "voice_profile"),
    ("fix the wifi", "wifi"),
    ("what voices do you have", "voice"),
    ("switch to ryan", "voice"),
    ("remind me to feed the cat in 10 minutes", "reminder"),
    ("set a timer for 5 minutes", "timer"),
    ("what's 5 plus 3", "calculator"),
    ("what time is it", "clock"),
    ("say that again", "repeat"),
    ("double check that", "double_check"),
    ("are you sure", "double_check"),
    ("drop in on the kitchen", "dropin"),
    ("hang up", "dropin"),
    ("tell everyone dinner is ready", "intercom"),
    ("let's have a chat", "chat_mode"),
    ("remember that i like jazz", "memory"),
    ("my favorite team is the mariners", "memory"),
    ("homelab status", "homelab"),
    ("what's the news", "news"),
    ("any news about spacex", "news"),
    ("play the latest episode of the daily", "spoken_audio"),
    ("resume my book", "spoken_audio"),
    ("next chapter", "spoken_audio"),
    ("play my favorites", "playlist"),
    ("play the chill playlist", "playlist"),
    ("shuffle the chill playlist", "playlist"),
    ("make a new playlist called chill", "playlist"),
    ("play the beatles", "music"),
    ("play creep by radiohead", "music"),
    ("pause", "music"),
    ("stop the music", "music"),
    ("skip this", "music"),
    ("find creep in my library", "library"),
    ("how many songs do i have", "library"),
    ("what did i add today", "library"),
]


class ContractError(RuntimeError):
    """One or more §13.2 contract checks failed."""

    def __init__(self, slug: str, errors: list[str], warnings: list[str] | None = None):
        self.slug = slug
        self.errors = errors
        self.warnings = warnings or []
        super().__init__(
            f"plugin {slug!r} failed {len(errors)} contract check(s): "
            + " | ".join(errors)
        )


@dataclass
class ContractReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.errors


# ─── routing dry-run (shared with /v1/admin/route-dry-run later) ────────────

def dry_run_winner(transcript: str, handlers: list[Handler]) -> str | None:
    """Replicate the router's normalization + band-ordered fast-path scan
    WITHOUT dispatching. ``handlers`` must be band-sorted (the live
    registry list is)."""
    from domovoi.router import _LEADING_FILLER_RE

    t = transcript.lower().strip().rstrip(".,!?")
    t = _LEADING_FILLER_RE.sub("", t)
    for handler in handlers:
        for entry in handler.fast_paths:
            fp = as_fast_path(entry)
            if fp.pattern.match(t):
                return handler.name
    return None


# ─── greedy-catch-all heuristic (§4.2 registration check) ───────────────────

_CAPTURE_ALL_RE = re.compile(r"\((?:\?P<[^>]+>)?\.\+\)?")
_LITERAL_WORD_RE = re.compile(r"[a-z][a-z']+", re.I)
_REGEX_SYNTAX_STRIP_RE = re.compile(
    r"\\[wsdbWSDB]|\(\?P<[^>]+>|\(\?:|[\^\$\(\)\[\]\{\}\|\?\*\+\\]|\d+,?\d*"
)


def is_greedy_unanchored(pattern: re.Pattern[str]) -> bool:
    """True when a fast-path regex contains an unanchored ``(.+)`` capture
    with fewer than two literal anchor words — the mechanical "prove you
    don't poach ``play X``" rule (§4.2): such a path needs band ≥ 900."""
    src = pattern.pattern
    if not _CAPTURE_ALL_RE.search(src):
        return False
    stripped = _REGEX_SYNTAX_STRIP_RE.sub(" ", src)
    words = _LITERAL_WORD_RE.findall(stripped)
    return len(words) < 2


# ─── the checks ─────────────────────────────────────────────────────────────

def check_handlers(
    slug: str, handlers: list[Handler], report: ContractReport
) -> None:
    prefix = f"{slug}."
    for h in handlers:
        name = getattr(h, "name", None)
        schema = getattr(h, "tool_schema", None) or {}
        if not name:
            report.errors.append("a registered handler has no name")
            continue
        if schema.get("name") != name:
            report.errors.append(
                f"handler {name!r}: tool_schema name {schema.get('name')!r} "
                f"!= Handler.name {name!r}"
            )
        band = getattr(h, "priority_band", None)
        if not isinstance(band, int):
            report.errors.append(f"handler {name!r}: priority_band missing")
        elif not (PLUGIN_BAND_MIN <= band <= PLUGIN_BAND_MAX):
            report.errors.append(
                f"handler {name!r}: band {band} outside the plugin-usable "
                f"ranges ({PLUGIN_BAND_MIN}-{PLUGIN_BAND_MAX})"
            )
        display = getattr(h, "display", None)
        if display is None:
            report.errors.append(f"handler {name!r}: display metadata missing")
        for kind in getattr(h, "confirmation_kinds", ()):
            if not kind.startswith(prefix):
                report.errors.append(
                    f"handler {name!r}: confirmation kind {kind!r} must be "
                    f"namespaced {prefix}<kind>"
                )
        rn = getattr(h, "requires_network", "no")
        if rn != "no" and type(h).fallback_offline is Handler.fallback_offline:
            report.errors.append(
                f"handler {name!r}: requires_network={rn!r} but "
                f"fallback_offline() is not overridden (local-first contract)"
            )
        for entry in getattr(h, "fast_paths", []):
            fp = as_fast_path(entry)
            if fp.offline_ok is not None and rn != "degraded":
                report.errors.append(
                    f"handler {name!r}: fast path {fp.pattern.pattern!r} sets "
                    f"offline_ok but requires_network={rn!r} (only 'degraded' "
                    f"handlers may)"
                )
            if is_greedy_unanchored(fp.pattern) and (band or 0) < 900:
                report.errors.append(
                    f"handler {name!r}: fast path {fp.pattern.pattern!r} is an "
                    f"unanchored greedy catch-all — band must be >= 900, got "
                    f"{band}"
                )


def check_corpus_collisions(
    slug: str,
    plugin_handlers: list[Handler],
    merged_registry: list[Handler],
    foreign_corpus: list[tuple[str, str]],
    report: ContractReport,
) -> None:
    """Check 2 — the merged corpus must keep resolving to its owners with
    the candidate plugin's handlers in the registry."""
    plugin_names = {h.name for h in plugin_handlers}
    corpus = CORE_CORPUS + foreign_corpus
    handlers = sorted(merged_registry, key=registry_sort_key)
    for utterance, owner in corpus:
        winner = dry_run_winner(utterance, handlers)
        if winner != owner and (winner in plugin_names or owner in plugin_names):
            report.errors.append(
                f"utterance-corpus collision: {utterance!r} routes to "
                f"{winner!r} but belongs to {owner!r}"
            )
        elif winner != owner:
            # Pre-existing breakage this plugin didn't cause — surface it,
            # don't fail the newcomer's install for it.
            report.warnings.append(
                f"corpus phrase {utterance!r} resolves to {winner!r} (owner "
                f"{owner!r}) independently of plugin {slug!r}"
            )


def check_manifest_drift(
    slug: str,
    manifest: PluginManifest,
    handlers: list[Handler],
    worker_names: dict[str, str],
    hook_names: list[str],
    capability_names: list[str],
    report: ContractReport,
) -> None:
    """Check 3 — the manifest is a cross-checked declaration; code is
    authoritative and any VALUE mismatch is a hard failure naming both."""
    by_name = {h.name: h for h in handlers}

    for decl in manifest.handlers:
        h = by_name.get(decl.name)
        if h is None:
            report.errors.append(
                f"manifest declares handler {decl.name!r} but register() did "
                f"not register it"
            )
            continue
        if h.priority_band != decl.band:
            report.errors.append(
                f"handler {decl.name!r}: manifest says band={decl.band}, code "
                f"registers {h.priority_band}"
            )
        if h.requires_network != decl.requires_network:
            report.errors.append(
                f"handler {decl.name!r}: manifest says requires_network="
                f"{decl.requires_network!r}, code registers "
                f"{h.requires_network!r}"
            )
        if bool(getattr(h, "chat_exposed", False)) != decl.chat_exposed:
            report.errors.append(
                f"handler {decl.name!r}: manifest says chat_exposed="
                f"{decl.chat_exposed}, code registers "
                f"{bool(getattr(h, 'chat_exposed', False))}"
            )
        display = getattr(h, "display", None)
        if display is not None and decl.label and display.label != decl.label:
            report.errors.append(
                f"handler {decl.name!r}: manifest says label={decl.label!r}, "
                f"code registers {display.label!r}"
            )
    declared_handler_names = {d.name for d in manifest.handlers}
    for h in handlers:
        if h.name not in declared_handler_names:
            report.warnings.append(
                f"handler {h.name!r} registered but not declared in the "
                f"manifest [[handlers]] (declare it — the web process and "
                f"Android read display from the manifest JSONB)"
            )

    declared_workers = {d.name: d.kind for d in manifest.workers}
    code_workers = dict(worker_names)
    for hook in hook_names:
        code_workers.setdefault(hook, "startup")
    for wname, wkind in declared_workers.items():
        actual = code_workers.get(wname)
        if actual is None:
            report.errors.append(
                f"manifest declares worker {wname!r} (kind={wkind!r}) but "
                f"register() did not register it"
            )
        elif actual != wkind:
            report.errors.append(
                f"worker {wname!r}: manifest says kind={wkind!r}, code "
                f"registers {actual!r}"
            )
    for wname in code_workers:
        if wname not in declared_workers:
            report.warnings.append(
                f"worker {wname!r} registered but not declared in the "
                f"manifest [[workers]]"
            )

    registered_caps = set(capability_names)
    for cap in manifest.provides:
        if cap not in registered_caps:
            report.errors.append(
                f"manifest declares capability {cap!r} but register() never "
                f"registered it (nothing registers 'implicitly at first use' "
                f"— design §2.2)"
            )
    for cap in registered_caps:
        if cap not in manifest.provides:
            report.warnings.append(
                f"capability {cap!r} registered but not declared in the "
                f"manifest [capabilities].provides"
            )


def check_consumes(
    manifest: PluginManifest, report: ContractReport
) -> None:
    """Hard `consumes` requirements — install fails if unsatisfied; the
    error lists what IS available so a typo is spottable at a glance."""
    from domovoi.capabilities import CAPABILITIES

    available = set(CORE_CAPABILITY_SLUGS) | set(CAPABILITIES.names())
    for cap in manifest.consumes:
        if cap not in available:
            report.errors.append(
                f"consumes {cap!r} is not provided by core or any enabled "
                f"plugin — available: {sorted(available)}"
            )


def check_import_budget(
    slug: str,
    import_seconds: float,
    cuda_initialized: bool,
    report: ContractReport,
) -> None:
    if import_seconds > IMPORT_TIME_BUDGET_SEC:
        report.errors.append(
            f"importing the core entry module took {import_seconds:.1f}s "
            f"(budget {IMPORT_TIME_BUDGET_SEC:.0f}s) — load heavy libraries "
            f"lazily inside worker/handler bodies (design §4.1)"
        )
    if cuda_initialized:
        report.errors.append(
            "importing the core entry module initialized CUDA — this breaks "
            "the Windows DLL bootstrap order (design §4.1); defer GPU work "
            "into handler/worker bodies"
        )


def run_contract_checks(
    *,
    slug: str,
    manifest: PluginManifest,
    handlers: list[Handler],
    merged_registry: list[Handler],
    worker_names: dict[str, str],
    hook_names: list[str],
    capability_names: list[str],
    foreign_corpus: list[tuple[str, str]] | None = None,
    import_seconds: float = 0.0,
    cuda_initialized: bool = False,
) -> ContractReport:
    """Run every §13.2 check; returns a report (caller raises
    :class:`ContractError` / sets ``load_error`` on ``errors``)."""
    report = ContractReport()
    check_handlers(slug, handlers, report)
    check_consumes(manifest, report)
    check_manifest_drift(
        slug, manifest, handlers, worker_names, hook_names,
        capability_names, report,
    )
    # The plugin's own declared corpus phrases must route to their owning
    # handler too — "canonical utterances this handler must win" (§2.2).
    own_corpus = [
        (utterance, decl.name)
        for decl in manifest.handlers
        for utterance in decl.corpus
    ]
    check_corpus_collisions(
        slug, handlers, merged_registry,
        list(foreign_corpus or []) + own_corpus, report,
    )
    check_import_budget(slug, import_seconds, cuda_initialized, report)
    return report


def media_library_drift_warnings(
    manifest: PluginManifest, install_dir: str | None
) -> list[str]:
    """Optional §7.1 drift check for ``[[media_libraries]]`` — WARN (never
    fail) when a *static*-base root (``install_dir`` / ``data_dir`` /
    ``absolute``) does not exist at install time. ``config``- and
    ``music_dir``-based roots resolve at runtime from user config, so their
    absence is expected and not warned. Kept standalone (no signature change to
    :func:`run_contract_checks`) so installers/loaders can surface these as
    load warnings without importing the web-side resolver."""
    from pathlib import Path

    warnings: list[str] = []
    for decl in manifest.media_libraries:
        if decl.base == "install_dir":
            if not install_dir:
                continue
            root = Path(install_dir) / decl.path
        elif decl.base == "absolute":
            root = Path(decl.path).expanduser()
        elif decl.base == "data_dir":
            root = (
                Path.home() / ".domovoi" / "plugins" / "data" / manifest.slug / decl.path
            )
        else:
            # config / music_dir → resolved from user config at runtime.
            continue
        if not root.exists():
            warnings.append(
                f"media_libraries.{decl.id}: static {decl.base} root {str(root)!r} "
                f"does not exist yet (the library is skipped until it does)"
            )
    return warnings
