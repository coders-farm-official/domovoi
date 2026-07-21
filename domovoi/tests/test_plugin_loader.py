"""Loader + §13.2 contract checks: collision corpus, greedy-band rule,
manifest/code drift, DLL-bootstrap assertion, deterministic load order."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from domovoi import bootstrap
from domovoi.handlers import HANDLER_BY_NAME, HANDLERS
from domovoi.plugins_runtime import registry as reg
from domovoi.plugins_runtime.contracts import ContractError, is_greedy_unanchored
from domovoi.plugins_runtime.loader import LOADER
from domovoi.plugins_runtime.manifest import parse_manifest
from domovoi.tests.conftest import requires_db

pytestmark = pytest.mark.asyncio

FIXTURE = Path(__file__).parent / "fixtures" / "compliments"


@pytest.fixture(autouse=True)
def _dlls_registered():
    bootstrap.register_nvidia_dlls()
    yield


def make_plugin(
    parent: Path,
    slug: str,
    *,
    band: int = 400,
    pattern: str | None = None,
    manifest_band: int | None = None,
    corpus: tuple[str, ...] = (),
) -> Path:
    """Generate a tiny importable plugin dir."""
    root = parent / slug
    pkg = root / f"domovoi_plugin_{slug}"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    regex = pattern or f"^{slug} ping$"
    corpus_toml = ", ".join(f'"{c}"' for c in corpus)
    (root / "domovoi-plugin.toml").write_text(
        textwrap.dedent(
            f"""
            [plugin]
            slug = "{slug}"
            name = "{slug}"
            version = "1.0.0"
            publisher = "tests"
            license = "MIT"
            description = "generated test plugin"
            domovoi_api = ">=1.0,<2.0"

            [entry_points]
            core = "domovoi_plugin_{slug}.core"

            [[handlers]]
            name = "{slug}"
            band = {manifest_band if manifest_band is not None else band}
            requires_network = "no"
            label = "{slug}"
            corpus = [{corpus_toml}]
            """
        ),
        encoding="utf-8",
    )
    (pkg / "core.py").write_text(
        textwrap.dedent(
            f"""
            import re

            from domovoi.sdk import FastPath, Handler, HandlerDisplay, Response

            LOADS = []


            class TestHandler(Handler):
                name = "{slug}"
                priority_band = {band}
                display = HandlerDisplay(label="{slug}")
                requires_network = "no"
                tool_schema = {{
                    "name": "{slug}",
                    "description": "test",
                    "parameters": {{"type": "object", "properties": {{}},
                                    "required": []}},
                }}

                def __init__(self):
                    self.fast_paths = [
                        FastPath(re.compile(r"{regex}"), TestHandler._go)
                    ]

                async def _go(self, m, ctx, session) -> Response:
                    return Response(text="ok")

                async def execute(self, intent, ctx, session) -> Response:
                    return Response(text="ok")


            def register(ctx):
                LOADS.append(ctx.slug)
                ctx.add_handler(TestHandler())
            """
        ),
        encoding="utf-8",
    )
    return root


async def _load(root: Path, slug: str):
    manifest = parse_manifest(
        (root / "domovoi-plugin.toml").read_text(encoding="utf-8")
    )
    return await LOADER.load_plugin(
        slug=slug, install_dir=root, manifest=manifest,
        foreign_corpus=[], update_registry_status=False,
    )


# ─── happy path ─────────────────────────────────────────────────────────────

async def test_fixture_plugin_loads_and_unloads() -> None:
    manifest = parse_manifest(
        (FIXTURE / "domovoi-plugin.toml").read_text(encoding="utf-8")
    )
    lp = await LOADER.load_plugin(
        slug="compliments", install_dir=FIXTURE, manifest=manifest,
        foreign_corpus=[], update_registry_status=False,
    )
    try:
        assert "compliments" in HANDLER_BY_NAME
        h = HANDLER_BY_NAME["compliments"]
        assert h.plugin_slug == "compliments"        # stamped by the loader
        # Registry stays band-sorted with the plugin slotted in.
        bands = [x.priority_band for x in HANDLERS]
        assert bands == sorted(bands)
        # register() actually ran.
        from domovoi_plugin_compliments.core import REGISTER_CALLS

        assert "compliments" in REGISTER_CALLS
        assert lp.degraded_reasons == []
    finally:
        await LOADER.unload_plugin("compliments")
    assert "compliments" not in HANDLER_BY_NAME


async def test_loader_asserts_dll_bootstrap_flag(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_plugin(tmp_path, "flagtest")
    monkeypatch.setattr(bootstrap, "dlls_registered", False)
    with pytest.raises(RuntimeError, match="register_nvidia_dlls"):
        await _load(root, "flagtest")


# ─── §13.2 check 1: greedy catch-all needs band ≥ 900 ───────────────────────

def test_greedy_unanchored_heuristic() -> None:
    import re

    assert is_greedy_unanchored(re.compile(r"^play (.+)$"))
    assert is_greedy_unanchored(re.compile(r"^find (?P<q>.+)$"))
    assert not is_greedy_unanchored(re.compile(r"^find (.+) in my library$"))
    assert not is_greedy_unanchored(re.compile(r"^say something nice$"))
    assert not is_greedy_unanchored(
        re.compile(r"^tune to (.+) on the radio$")
    )


async def test_greedy_catchall_below_900_rejected(tmp_path: Path) -> None:
    root = make_plugin(tmp_path, "greedy", band=400, pattern=r"^fetch (.+)$")
    with pytest.raises(ContractError) as exc:
        await _load(root, "greedy")
    assert any(">= 900" in e for e in exc.value.errors)
    assert "greedy" not in HANDLER_BY_NAME     # torn back down


async def test_greedy_catchall_at_900_allowed(tmp_path: Path) -> None:
    root = make_plugin(tmp_path, "greedyok", band=900, pattern=r"^fetch (.+)$")
    lp = await _load(root, "greedyok")
    try:
        assert "greedyok" in HANDLER_BY_NAME
    finally:
        await LOADER.unload_plugin("greedyok")


# ─── §13.2 check 2: the utterance-corpus collision test ─────────────────────

async def test_corpus_poacher_rejected(tmp_path: Path) -> None:
    # Band 280 (anchored media) + a pattern that steals a MUSIC-owned corpus
    # phrase: install must fail naming the utterance and both handlers.
    root = make_plugin(
        tmp_path, "poacher", band=280, pattern=r"^play the beatles$"
    )
    with pytest.raises(ContractError) as exc:
        await _load(root, "poacher")
    joined = " ".join(exc.value.errors)
    assert "play the beatles" in joined
    assert "poacher" in joined and "music" in joined
    assert "poacher" not in HANDLER_BY_NAME


async def test_plugin_own_corpus_must_win(tmp_path: Path) -> None:
    # Declares a corpus phrase its own regex does NOT match ⇒ fails.
    root = make_plugin(
        tmp_path, "corpusmiss", band=400, pattern=r"^corpusmiss ping$",
        corpus=("some phrase nothing matches",),
    )
    with pytest.raises(ContractError) as exc:
        await _load(root, "corpusmiss")
    assert any("some phrase nothing matches" in e for e in exc.value.errors)


async def test_foreign_plugin_corpus_protected(tmp_path: Path) -> None:
    # An EARLIER-installed plugin's declared corpus phrase can't be poached
    # by a later plugin in a lower band (§13.2 check 2, plugin-vs-plugin).
    first = make_plugin(
        tmp_path, "earlier", band=400, pattern=r"^zap the widget$",
        corpus=("zap the widget",),
    )
    lp = await _load(first, "earlier")
    try:
        poacher = make_plugin(
            tmp_path, "latecomer", band=280, pattern=r"^zap the widget$"
        )
        manifest = parse_manifest(
            (poacher / "domovoi-plugin.toml").read_text(encoding="utf-8")
        )
        with pytest.raises(ContractError) as exc:
            await LOADER.load_plugin(
                slug="latecomer", install_dir=poacher, manifest=manifest,
                foreign_corpus=[("zap the widget", "earlier")],
                update_registry_status=False,
            )
        joined = " ".join(exc.value.errors)
        assert "zap the widget" in joined and "earlier" in joined
    finally:
        await LOADER.unload_plugin("earlier")


# ─── §13.2 check 3: manifest/code drift ─────────────────────────────────────

async def test_manifest_band_drift_is_hard_failure(tmp_path: Path) -> None:
    root = make_plugin(tmp_path, "drift", band=405, manifest_band=280)
    with pytest.raises(ContractError) as exc:
        await _load(root, "drift")
    joined = " ".join(exc.value.errors)
    assert "280" in joined and "405" in joined   # names BOTH values


async def test_unsatisfied_consumes_lists_available(tmp_path: Path) -> None:
    root = make_plugin(tmp_path, "needystub")
    mf = root / "domovoi-plugin.toml"
    mf.write_text(
        mf.read_text(encoding="utf-8").replace(
            "[entry_points]",
            '[capabilities]\nconsumes = ["quantum-flux-provider"]\n\n[entry_points]',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContractError) as exc:
        await _load(root, "needystub")
    joined = " ".join(exc.value.errors)
    assert "quantum-flux-provider" in joined
    assert "media-acquisition-queue" in joined   # the available list


# ─── §3.7: deterministic load order + tombstone respect ─────────────────────

@requires_db
async def test_discovery_heals_stale_bundled_install_dir(
    tmp_path: Path, monkeypatch,
) -> None:
    """A bundled row whose stored absolute install_dir points at a moved or
    deleted checkout (e.g. a git worktree that no longer exists) must be
    re-resolved against the current bundled root and load normally."""
    from domovoi.plugins_runtime import loader as loader_mod

    installed = tmp_path / "installed"
    installed.mkdir()
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    monkeypatch.setattr(loader_mod, "installed_root", lambda: installed)
    monkeypatch.setattr(loader_mod, "bundled_root", lambda: bundled)

    slug = "healme"
    root = make_plugin(bundled, slug)
    manifest = parse_manifest(
        (root / "domovoi-plugin.toml").read_text(encoding="utf-8")
    )
    stale = tmp_path / "deleted-worktree" / "plugins" / slug  # never exists
    try:
        await reg.insert_plugin(
            slug=slug, name=slug, version="1.0.0", publisher="tests",
            license="MIT", domovoi_api=">=1.0,<2.0", enabled=True,
            bundled=True, install_source="bundled", source_ref=None,
            install_dir=str(stale), manifest=manifest.raw,
        )
        await LOADER.discover_and_load_all()

        assert slug in LOADER.loaded
        row = await reg.get_plugin(slug)
        assert row is not None
        assert row.install_dir == str(root.resolve())
        assert row.status in ("ok", "degraded")
    finally:
        await LOADER.shutdown()
        await reg.delete_plugin(slug)


async def test_discovery_loads_in_slug_order_and_honors_tombstone(
    tmp_path: Path, monkeypatch,
) -> None:
    from domovoi.plugins_runtime import loader as loader_mod

    installed = tmp_path / "installed"
    installed.mkdir()
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    monkeypatch.setattr(loader_mod, "installed_root", lambda: installed)
    monkeypatch.setattr(loader_mod, "bundled_root", lambda: bundled)

    slugs = ["zzz_last", "aaa_first", "mmm_mid"]
    try:
        for slug in slugs:
            root = make_plugin(installed, slug)
            manifest = parse_manifest(
                (root / "domovoi-plugin.toml").read_text(encoding="utf-8")
            )
            await reg.insert_plugin(
                slug=slug, name=slug, version="1.0.0", publisher="tests",
                license="MIT", domovoi_api=">=1.0,<2.0", enabled=True,
                bundled=False, install_source="zip", source_ref=None,
                install_dir=str(root), manifest=manifest.raw,
            )
        # Tombstone one of them — discovery must NOT resurrect it.
        await reg.tombstone_plugin("mmm_mid")

        await LOADER.discover_and_load_all()
        loaded = [s for s in LOADER.loaded if s in slugs]
        assert loaded == ["aaa_first", "zzz_last"]   # slug order, no tombstone
        assert "mmm_mid" not in LOADER.loaded

        row = await reg.get_plugin("mmm_mid")
        assert row is not None and row.status == "uninstalled"

        # An unregistered hand-copied dir is flagged, never loaded.
        stray = make_plugin(installed, "straycopy")
        await LOADER.discover_and_load_all()
        assert any("straycopy" in d for d in LOADER.unregistered_dirs)
        assert "straycopy" not in LOADER.loaded
    finally:
        # discover_and_load_all() also hot-loads any REAL plugin with an
        # enabled registry row (the bundled radio plugin lands one the
        # first time a lifespan test boots) — unload everything, not
        # just this test's synthetic slugs, so no handler leaks into the
        # registry-shape tests downstream.
        await LOADER.shutdown()
        for slug in slugs:
            await reg.delete_plugin(slug)
