"""Manifest validation corpus (design §2.2 / §13 — good + bad manifests),
directory-layout validation, and the web-entry import-hygiene tripwire."""

from __future__ import annotations

from pathlib import Path

import pytest

from domovoi.plugins_runtime.manifest import (
    ManifestError,
    api_range_satisfied,
    check_web_import_hygiene,
    parse_manifest,
    validate_plugin_dir,
)

FIXTURE = Path(__file__).parent / "fixtures" / "compliments"


GOOD = """
[plugin]
slug = "radiotest"
name = "Radio Test"
version = "1.2.3"
publisher = "Coders Farm"
license = "MIT"
description = "A test manifest."
domovoi_api = ">=1.0,<2.0"

[entry_points]
core = "domovoi_plugin_radiotest.core"

[capabilities]
provides = ["now-playing-source:radiotest"]
consumes = ["media-acquisition-queue"]

[requirements]
python = ["httpx==0.28.1"]
lockfile = "requirements.lock"
system = [ { tool = "ffmpeg", required = true, help = "Needed." } ]

[[handlers]]
name = "radiotest"
band = 280
requires_network = "degraded"
chat_exposed = true
label = "Radio Test"
tone = "media"
corpus = ["play 97.5 fm"]

[[workers]]
name = "sampler"
kind = "poll"
[[workers]]
name = "importer"
kind = "startup"

[permissions]
network = true
warnings = ["Talks to the internet."]

[web]
scripts = ["web/static/stations.jsx"]
[[web.pages]]
route = "stations"
page = "StationsPage"
nav_label = "Stations"
nav_order = 50

[[realtime]]
notify_channel = "plugin_radiotest_stations_changed"
realtime_channel = "radiotest.stations"
snapshot = "snapshot_stations"

[[media_libraries]]
id = "roms"
label = "ROM library"
icon = "puzzle"
base = "config"
path = "library_dir"
read_only = false

[android]
capabilities = ["stations"]
"""


def _mutate(text: str, old: str, new: str) -> str:
    assert old in text, f"fixture drift: {old!r} not in manifest"
    return text.replace(old, new)


def test_good_manifest_parses_fully() -> None:
    m = parse_manifest(GOOD)
    assert m.slug == "radiotest"
    assert m.version == "1.2.3"
    assert m.entry_core == "domovoi_plugin_radiotest.core"
    assert m.entry_web is None
    assert m.provides == ("now-playing-source:radiotest",)
    assert m.consumes == ("media-acquisition-queue",)
    assert m.python_requirements == ("httpx==0.28.1",)
    assert m.lockfile == "requirements.lock"
    assert m.system_requirements[0].tool == "ffmpeg"
    assert m.system_requirements[0].required is True
    h = m.handlers[0]
    assert (h.name, h.band, h.requires_network, h.chat_exposed) == (
        "radiotest", 280, "degraded", True,
    )
    assert h.label == "Radio Test" and h.tone == "media"
    assert h.corpus == ("play 97.5 fm",)
    assert {w.name: w.kind for w in m.workers} == {
        "sampler": "poll", "importer": "startup",
    }
    assert m.env_prefix == "RADIOTEST_"          # default <SLUG_UPPER>_
    assert m.permissions["network"] is True
    assert m.permissions["subprocess"] is False  # defaults false
    assert m.warnings == ("Talks to the internet.",)
    assert m.web_pages[0].route == "stations"
    assert m.realtime[0].notify_channel == "plugin_radiotest_stations_changed"
    ml = m.media_libraries[0]
    assert (ml.id, ml.label, ml.base, ml.path) == (
        "roms", "ROM library", "config", "library_dir",
    )
    assert ml.icon == "puzzle" and ml.read_only is False and ml.separator is None
    # The whole table rides raw → plugins.manifest JSONB unchanged.
    assert m.raw["media_libraries"][0]["id"] == "roms"
    assert m.android_capabilities == ("stations",)
    assert m.migrations_dir == "migrations"
    assert m.package_name == "domovoi_plugin_radiotest"
    assert m.raw["plugin"]["slug"] == "radiotest"  # JSONB payload intact


@pytest.mark.parametrize(
    "old,new,fragment",
    [
        # slug rules
        ('slug = "radiotest"', 'slug = "Radio-Test"', "slug"),
        ('slug = "radiotest"', 'slug = "core"', "reserved"),
        ('slug = "radiotest"', 'slug = "x" # too short is 1 char', "slug"),
        # strict semver
        ('version = "1.2.3"', 'version = "1.2"', "semver"),
        ('version = "1.2.3"', 'version = "1.2.3-beta"', "semver"),
        # domovoi_api must be satisfiable by the running core
        ('domovoi_api = ">=1.0,<2.0"', 'domovoi_api = ">=9.0"', "not satisfiable"),
        ('domovoi_api = ">=1.0,<2.0"', 'domovoi_api = "banana"', "specifier"),
        # entry point must match the slug package
        (
            'core = "domovoi_plugin_radiotest.core"',
            'core = "domovoi_plugin_other.core"',
            "entry_points.core",
        ),
        # pinned-only requirements
        ('python = ["httpx==0.28.1"]', 'python = ["httpx>=0.28"]', "pin"),
        ('python = ["httpx==0.28.1"]', 'python = ["httpx"]', "pin"),
        # band rules
        ("band = 280", "band = 50", "plugin-usable"),
        ("band = 280", "band = 1000", "plugin-usable"),
        # requires_network vocabulary
        (
            'requires_network = "degraded"',
            'requires_network = "sometimes"',
            "requires_network",
        ),
        # label is required on manifest handlers (§2.2 — web/Android read it)
        ('label = "Radio Test"\n', "", "label"),
        # worker kind vocabulary
        ('kind = "poll"', 'kind = "cron"', "kind"),
        # realtime channel prefix
        (
            'notify_channel = "plugin_radiotest_stations_changed"',
            'notify_channel = "stations_changed"',
            "plugin_radiotest_",
        ),
    ],
)
def test_bad_manifests_rejected(old: str, new: str, fragment: str) -> None:
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_mutate(GOOD, old, new))
    assert fragment.lower() in str(exc.value).lower()


@pytest.mark.parametrize(
    "missing",
    ["name", "version", "publisher", "license", "description", "domovoi_api"],
)
def test_required_plugin_fields(missing: str) -> None:
    lines = [
        line for line in GOOD.splitlines() if not line.startswith(f"{missing} =")
    ]
    with pytest.raises(ManifestError):
        parse_manifest("\n".join(lines))


def test_missing_entry_points_table_rejected() -> None:
    text = GOOD.replace("[entry_points]", "[not_entry_points]").replace(
        'core = "domovoi_plugin_radiotest.core"', 'x = "y"'
    )
    with pytest.raises(ManifestError, match="entry_points"):
        parse_manifest(text)


def test_lockfile_defaults_when_python_reqs_present() -> None:
    text = _mutate(GOOD, 'lockfile = "requirements.lock"\n', "")
    m = parse_manifest(text)
    assert m.lockfile == "requirements.lock"


def test_no_python_reqs_needs_no_lockfile() -> None:
    text = _mutate(GOOD, 'python = ["httpx==0.28.1"]', "python = []")
    text = _mutate(text, 'lockfile = "requirements.lock"\n', "")
    assert parse_manifest(text).lockfile is None


def test_api_range_satisfied() -> None:
    assert api_range_satisfied(">=1.0,<2.0", "1.0.0")
    assert api_range_satisfied(">=1.0", "1.5.2")
    assert not api_range_satisfied(">=1.1", "1.0.9")
    assert not api_range_satisfied("<1.0", "1.0.0")
    assert api_range_satisfied("==1.0.0", "1.0.0")
    assert api_range_satisfied("~=1.0", "1.9.0")
    assert not api_range_satisfied("~=1.0", "2.0.0")
    with pytest.raises(ManifestError):
        api_range_satisfied("about 1.0", "1.0.0")


# ─── directory-level validation ─────────────────────────────────────────────

def test_fixture_plugin_dir_validates_clean() -> None:
    manifest = parse_manifest(
        (FIXTURE / "domovoi-plugin.toml").read_text(encoding="utf-8")
    )
    assert validate_plugin_dir(FIXTURE, manifest) == []


def test_missing_package_dir_flagged(tmp_path: Path) -> None:
    (tmp_path / "domovoi-plugin.toml").write_text(GOOD, encoding="utf-8")
    manifest = parse_manifest(GOOD)
    errors = validate_plugin_dir(tmp_path, manifest)
    assert any("domovoi_plugin_radiotest" in e for e in errors)


def _write_minimal_tree(root: Path, slug: str) -> None:
    pkg = root / f"domovoi_plugin_{slug}"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")


def test_migration_gap_flagged(tmp_path: Path) -> None:
    text = _mutate(GOOD, 'python = ["httpx==0.28.1"]', "python = []")
    manifest = parse_manifest(text)
    (tmp_path / "domovoi-plugin.toml").write_text(text, encoding="utf-8")
    _write_minimal_tree(tmp_path, "radiotest")
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "V001__a.sql").write_text("SELECT 1;", encoding="utf-8")
    (mig / "V003__b.sql").write_text("SELECT 1;", encoding="utf-8")
    errors = validate_plugin_dir(tmp_path, manifest)
    assert any("gapless" in e for e in errors)


def test_bad_migration_filename_flagged(tmp_path: Path) -> None:
    text = _mutate(GOOD, 'python = ["httpx==0.28.1"]', "python = []")
    manifest = parse_manifest(text)
    _write_minimal_tree(tmp_path, "radiotest")
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")
    errors = validate_plugin_dir(tmp_path, manifest)
    assert any("V###__name.sql" in e for e in errors)


def test_lockfile_must_carry_hashes(tmp_path: Path) -> None:
    manifest = parse_manifest(GOOD)
    _write_minimal_tree(tmp_path, "radiotest")
    (tmp_path / "requirements.lock").write_text(
        "httpx==0.28.1\n", encoding="utf-8"
    )
    errors = validate_plugin_dir(tmp_path, manifest)
    assert any("--hash=" in e for e in errors)


def test_lockfile_must_pin_direct_deps_at_same_version(tmp_path: Path) -> None:
    manifest = parse_manifest(GOOD)
    _write_minimal_tree(tmp_path, "radiotest")
    (tmp_path / "requirements.lock").write_text(
        "httpx==0.27.0 --hash=sha256:deadbeef\n", encoding="utf-8"
    )
    errors = validate_plugin_dir(tmp_path, manifest)
    assert any("httpx==0.28.1" in e for e in errors)


# ─── web-entry import hygiene (§3.2 step 5) ────────────────────────────────

def _web_plugin(tmp_path: Path, web_source: str) -> tuple[Path, object]:
    slug = "webtest"
    text = GOOD.replace("radiotest", slug)
    text = text.replace(
        f'core = "domovoi_plugin_{slug}.core"',
        f'core = "domovoi_plugin_{slug}.core"\nweb = "domovoi_plugin_{slug}.web"',
    )
    manifest = parse_manifest(text)
    pkg = tmp_path / f"domovoi_plugin_{slug}"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
    (pkg / "web.py").write_text(web_source, encoding="utf-8")
    return tmp_path, manifest


def test_web_module_may_import_webkit(tmp_path: Path) -> None:
    root, manifest = _web_plugin(
        tmp_path, "import json\nfrom domovoi.webkit import pagination\n"
    )
    assert check_web_import_hygiene(root, manifest) == []


def test_web_module_importing_core_runtime_rejected(tmp_path: Path) -> None:
    root, manifest = _web_plugin(tmp_path, "from domovoi.db.session import engine\n")
    errors = check_web_import_hygiene(root, manifest)
    assert errors and "domovoi.db" in errors[0]


def test_web_module_importing_plugin_core_entry_rejected(tmp_path: Path) -> None:
    root, manifest = _web_plugin(
        tmp_path, "from domovoi_plugin_webtest.core import register\n"
    )
    errors = check_web_import_hygiene(root, manifest)
    assert errors and "core entry module" in errors[0]


def test_web_hygiene_walks_plugin_internal_imports(tmp_path: Path) -> None:
    root, manifest = _web_plugin(
        tmp_path, "from domovoi_plugin_webtest import helpers\n"
    )
    (root / "domovoi_plugin_webtest" / "helpers.py").write_text(
        "import domovoi.streaming\n", encoding="utf-8"
    )
    errors = check_web_import_hygiene(root, manifest)
    assert errors and "domovoi.streaming" in errors[0]


# ─── [[media_libraries]] table (design §7.1) ───────────────────────────────


def test_media_library_defaults_and_multi_base() -> None:
    """A minimal decl defaults icon/separator/read_only; a second decl with a
    static base + separator + extensions parses cleanly."""
    text = _mutate(
        GOOD,
        'read_only = false\n',
        'read_only = false\n\n'
        '[[media_libraries]]\n'
        'id = "videos"\n'
        'label = "Videos"\n'
        'base = "install_dir"\n'
        'path = "media"\n'
        'separator = ";"\n'
        'extensions = [".mkv", ".mp4"]\n',
    )
    m = parse_manifest(text)
    assert [ml.id for ml in m.media_libraries] == ["roms", "videos"]
    videos = m.media_libraries[1]
    assert videos.base == "install_dir"
    assert videos.read_only is True  # defaulted
    assert videos.separator == ";"
    assert videos.extensions == (".mkv", ".mp4")


@pytest.mark.parametrize(
    "old,new,fragment",
    [
        # id must match the slug-like regex
        ('id = "roms"', 'id = "ROMs"', "media_libraries.id"),
        ('id = "roms"', 'id = "1roms"', "media_libraries.id"),
        # base vocabulary is fixed
        ('base = "config"', 'base = "somewhere"', "base"),
        # label / path are required
        ('label = "ROM library"\n', "", "label"),
        ('path = "library_dir"\n', "", "path"),
        # read_only must be boolean
        ("read_only = false", 'read_only = "no"', "read_only"),
    ],
)
def test_bad_media_library_rejected(old: str, new: str, fragment: str) -> None:
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_mutate(GOOD, old, new))
    assert fragment.lower() in str(exc.value).lower()


def test_media_library_duplicate_id_rejected() -> None:
    dup = _mutate(
        GOOD,
        'read_only = false\n',
        'read_only = false\n\n'
        '[[media_libraries]]\n'
        'id = "roms"\n'
        'label = "Dup"\n'
        'base = "absolute"\n'
        'path = "/tmp/x"\n',
    )
    with pytest.raises(ManifestError, match="unique per plugin"):
        parse_manifest(dup)


def test_no_media_libraries_is_fine() -> None:
    text = _mutate(
        GOOD,
        '[[media_libraries]]\n'
        'id = "roms"\n'
        'label = "ROM library"\n'
        'icon = "puzzle"\n'
        'base = "config"\n'
        'path = "library_dir"\n'
        'read_only = false\n\n',
        "",
    )
    m = parse_manifest(text)
    assert m.media_libraries == ()
