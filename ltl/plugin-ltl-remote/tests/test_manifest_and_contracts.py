"""Manifest, contract checks, and the loader.

Green here means the plugin survives the checks the installer runs — the
manifest-versus-code cross-check, the lockfile rules, and the web
process's import guard. These need a Domovoi environment; without one
they skip rather than failing collection.
"""

from __future__ import annotations

import tomllib

import pytest

from conftest import PLUGIN_DIR, needs_domovoi

MANIFEST_PATH = PLUGIN_DIR / "domovoi-plugin.toml"


@pytest.fixture(scope="module")
def raw_manifest() -> dict:
    with MANIFEST_PATH.open("rb") as fh:
        return tomllib.load(fh)


# ─── things that hold without Domovoi installed ──────────────────────────


def test_slug_is_a_legal_python_and_sql_identifier(raw_manifest):
    """The slug becomes both a Python package suffix
    (``domovoi_plugin_<slug>``) and a Postgres schema
    (``plugin_<slug>``), so hyphens are not an option."""
    slug = raw_manifest["plugin"]["slug"]
    assert slug == "ltl_remote"
    assert slug.replace("_", "").isalnum()
    assert (PLUGIN_DIR / f"domovoi_plugin_{slug}").is_dir()


def test_manifest_declares_exactly_one_python_package(raw_manifest):
    packages = [
        p.name for p in PLUGIN_DIR.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    ]
    assert packages == ["domovoi_plugin_ltl_remote"]


def test_handler_band_sits_in_device_control_and_above_library(raw_manifest):
    """Band 265 is the device-control and comms range (200-269), which is
    where a system-control surface belongs.

    The upper bound is load-bearing rather than stylistic: ``library``
    at 310 has a greedy ``^is (.+)$`` fast path that would swallow "is
    remote access on". Raising this band above 310 breaks routing, and
    the contract test below is what would catch it — this assertion says
    why."""
    band = raw_manifest["handlers"][0]["band"]
    assert 200 <= band <= 269


def test_declared_workers_match_the_code():
    declared = {
        (w["name"], w["kind"])
        for w in tomllib.loads(MANIFEST_PATH.read_text())["workers"]
    }
    assert declared == {
        ("ltl_relay_link", "longrun"),
        ("ltl_remote_reaper", "poll"),
        ("publish_identity", "startup"),
    }


def test_pinned_requirements_appear_in_the_lockfile(raw_manifest):
    """The installer refuses a direct dependency whose pinned version is
    not in the hashed lockfile; catching that here beats catching it at
    install time on someone's server."""
    lock = (PLUGIN_DIR / "requirements.lock").read_text()
    assert "--hash=sha256:" in lock
    for pin in raw_manifest["requirements"]["python"]:
        assert f"{pin} \\" in lock, f"{pin} is missing from requirements.lock"


def test_requirements_in_mirrors_the_manifest(raw_manifest):
    declared = set(raw_manifest["requirements"]["python"])
    from_file = {
        line.strip()
        for line in (PLUGIN_DIR / "requirements.in").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert declared == from_file


def test_permission_warnings_name_the_real_risks(raw_manifest):
    """These strings are what an admin reads on the install preview. The
    two that matter — that an approved device reaches the admin tier,
    and that LTL sees metadata — must actually be in there."""
    warnings = " ".join(raw_manifest["permissions"]["warnings"]).lower()
    assert "admin" in warnings and "plugin" in warnings
    assert "cannot read" in warnings
    assert raw_manifest["permissions"]["network"] is True
    assert raw_manifest["permissions"]["subprocess"] is False


def test_realtime_channels_match_the_notify_suffixes(raw_manifest):
    """The plugin fires ``plugin_ltl_remote_<suffix>``; the manifest maps
    those names to dashboard channels. A mismatch is silent at runtime —
    the page just never updates — so it is pinned here."""
    pytest.importorskip("sqlalchemy")
    from domovoi_plugin_ltl_remote import store

    declared = {r["notify_channel"] for r in raw_manifest["realtime"]}
    fired = {f"plugin_ltl_remote_{s}" for s in (store.CH_LINK, store.CH_DEVICES)}
    assert declared == fired


def test_declared_snapshots_exist_in_the_web_module(raw_manifest):
    pytest.importorskip("fastapi")
    from domovoi_plugin_ltl_remote import web

    for entry in raw_manifest["realtime"]:
        assert entry["snapshot"] in web.SNAPSHOTS


def test_migrations_are_gapless_and_start_at_one():
    names = sorted(p.name for p in (PLUGIN_DIR / "migrations").glob("V*.sql"))
    assert names
    for index, name in enumerate(names, start=1):
        assert name.startswith(f"V{index:03d}__"), names


def test_migrations_never_touch_core_tables():
    """Plugins own their schema and nothing else. A stray reference to a
    public table here would be a hard rule violation."""
    sql = " ".join(
        p.read_text().lower() for p in (PLUGIN_DIR / "migrations").glob("V*.sql")
    )
    for forbidden in ("public.", "references intents_log", "references sessions"):
        assert forbidden not in sql


def test_no_private_key_material_is_ever_stored_in_postgres():
    """The security doc promises private keys live only on disk at 0600.
    This is that promise, asserted."""
    sql = " ".join(
        p.read_text().lower() for p in (PLUGIN_DIR / "migrations").glob("V*.sql")
    )
    assert "private_key" not in sql
    assert "dh_public_key" in sql and "sig_public_key" in sql


# ─── things that need Domovoi ────────────────────────────────────────────


@needs_domovoi
def test_manifest_parses_under_the_real_parser():
    from domovoi.plugins_runtime.manifest import parse_manifest_dir

    manifest = parse_manifest_dir(PLUGIN_DIR)
    assert manifest.slug == "ltl_remote"
    assert [h.name for h in manifest.handlers] == ["ltl_remote"]


@needs_domovoi
def test_manifest_passes_the_install_time_checks():
    from domovoi.plugins_runtime.manifest import (
        check_web_import_hygiene,
        parse_manifest_dir,
        validate_plugin_dir,
    )

    manifest = parse_manifest_dir(PLUGIN_DIR)
    assert validate_plugin_dir(PLUGIN_DIR, manifest) == []
    assert check_web_import_hygiene(PLUGIN_DIR, manifest) == []


@needs_domovoi
def test_the_handler_declaration_matches_the_class():
    from domovoi_plugin_ltl_remote.handlers import RemoteAccessHandler

    declared = tomllib.loads(MANIFEST_PATH.read_text())["handlers"][0]
    assert RemoteAccessHandler.name == declared["name"]
    assert RemoteAccessHandler.priority_band == declared["band"]
    assert RemoteAccessHandler.requires_network == declared["requires_network"]
    assert RemoteAccessHandler.display.label == declared["label"]
    assert RemoteAccessHandler.display.tone == declared["tone"]
    assert RemoteAccessHandler.tool_schema["name"] == RemoteAccessHandler.name


@needs_domovoi
def test_a_no_network_handler_needs_no_offline_fallback():
    """``requires_network="no"`` is the claim that every answer is
    local. If that ever stops being true, the registry test in core
    starts demanding a ``fallback_offline`` — so the claim is worth
    pinning to the reason for it."""
    from domovoi_plugin_ltl_remote.handlers import RemoteAccessHandler

    assert RemoteAccessHandler.requires_network == "no"
    assert all(fp.offline_ok is None for fp in RemoteAccessHandler(None).fast_paths)


@needs_domovoi
def test_the_plugin_passes_the_loaders_contract_checks(stub_sdk):
    """The §13.2 checks the loader runs at enable time, against the REAL
    merged registry — so a band collision with a core handler, or a
    corpus phrase another handler would poach, fails here rather than on
    a user's server.

    This is the single most valuable test in the file: it is the same
    code path that decides whether the plugin loads at all.
    """
    from domovoi.handlers import HANDLERS
    from domovoi.handlers.base import registry_sort_key
    from domovoi.plugins_runtime.contracts import run_contract_checks
    from domovoi.plugins_runtime.manifest import parse_manifest_dir

    from domovoi_plugin_ltl_remote.handlers import RemoteAccessHandler

    handler = RemoteAccessHandler(stub_sdk)
    handler.plugin_slug = "ltl_remote"
    # normalize_fast_paths runs at registration; do it by hand since we
    # are not going through the registry here.
    from domovoi.handlers.base import normalize_fast_paths

    normalize_fast_paths(handler)

    report = run_contract_checks(
        slug="ltl_remote",
        manifest=parse_manifest_dir(PLUGIN_DIR),
        handlers=[handler],
        # Band-sorted: dry_run_winner scans in list order and trusts the
        # caller to have sorted, exactly as the live registry is.
        merged_registry=sorted([*HANDLERS, handler], key=registry_sort_key),
        worker_names={"ltl_relay_link": "longrun", "ltl_remote_reaper": "poll"},
        hook_names=["publish_identity"],
        capability_names=[],
    )
    assert report.errors == [], report.errors


@needs_domovoi
def test_the_handlers_corpus_phrases_route_to_it(stub_sdk):
    """Every canonical utterance in the manifest must actually win
    against the full core registry. A phrase that loses is a promise the
    manifest makes and the code does not keep."""
    from domovoi.handlers import HANDLERS
    from domovoi.handlers.base import normalize_fast_paths, registry_sort_key
    from domovoi.plugins_runtime.contracts import dry_run_winner
    from domovoi.plugins_runtime.manifest import parse_manifest_dir

    from domovoi_plugin_ltl_remote.handlers import RemoteAccessHandler

    handler = RemoteAccessHandler(stub_sdk)
    handler.plugin_slug = "ltl_remote"
    normalize_fast_paths(handler)
    registry = sorted([*HANDLERS, handler], key=registry_sort_key)

    for decl in parse_manifest_dir(PLUGIN_DIR).handlers:
        for utterance in decl.corpus:
            assert dry_run_winner(utterance, registry) == "ltl_remote", (
                f"{utterance!r} was poached by another handler"
            )
