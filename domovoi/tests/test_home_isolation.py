"""The home isolation and the real-home guard, asserted rather than assumed
(domovoi/tests/home_isolation.py, wired in by the repo-root conftest.py).

Tests ran with the developer's real home until 2026-10-15, and wrote into
``~/.domovoi`` on every run: a test core's ``run/core-ready.json`` and
``logs/core-faulthandler.log``, the satellite's ``log-spool.txt``, a
``logs/plugin_<slug>.log`` per fixture plugin. These pin that the home is a
temp folder before anything computes a path from it, and that a write that
reaches the real one anyway fails the run, serial or under ``-n``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi.tests import home_isolation

REPO = Path(__file__).resolve().parents[2]
PLANT = Path(__file__).resolve().parent / "fixtures" / "home_guard" / "plant_write.py"


def _inside(path: Path | str, root: Path) -> bool:
    return Path(os.path.normcase(os.path.abspath(path))).is_relative_to(
        Path(os.path.normcase(os.path.abspath(root))))


# ─── the home is a temp folder ──────────────────────────────────────────────

def test_the_home_is_a_temp_folder_of_this_process():
    home = home_isolation.session_home()
    real = home_isolation.real_home()
    assert home is not None and home.is_dir()
    assert Path.home() == home != real
    assert Path(os.path.expanduser("~")) == home
    assert Path("~/.domovoi").expanduser() == home / ".domovoi"
    for var in home_isolation.HOME_VARS:
        assert os.environ[var] == str(home)
    for var in home_isolation.FALLBACK_VARS:
        assert var not in os.environ, f"{var} would let expanduser find the real home"
    assert os.environ["DOMOVOI_HOME"] == str(home / ".domovoi")
    assert not _inside(home, real / ".domovoi")
    # Under -n each worker has its own (it inherits the controller's
    # environment, and makes a new one rather than share that).
    assert os.environ[home_isolation.OWNER_ENV] == str(os.getpid())
    assert home.name.startswith(f"domovoi-test-home-{os.environ.get('PYTEST_XDIST_WORKER') or 'main'}-")


def test_every_settings_folder_under_dot_domovoi_is_in_the_temp_home():
    from domovoi.config import Settings, settings

    home = home_isolation.session_home()
    seen = 0
    for name, field in Settings.model_fields.items():
        for value in (field.default, getattr(settings, name)):
            if isinstance(value, str) and ".domovoi" in value:
                seen += 1
                assert _inside(Path(value).expanduser(), home), f"{name} = {value}"
    assert seen >= 10  # voice_models_dir, sounds_dir, wake_models_dir, ...
    # And the ones outside .domovoi that tests index or write (lyrics sidecars).
    for name in ("music_dir", "videos_dir", "pictures_dir", "documents_dir"):
        assert _inside(Path(getattr(Settings.model_fields[name], "default")).expanduser(), home), name


def test_module_constants_built_at_import_are_in_the_temp_home():
    from domovoi import fast_lane
    from domovoi.plugins_runtime import config_bridge, installer, loader
    from domovoi.satellite_media import builder, cache
    from domovoi.sdk import observability

    home = home_isolation.session_home()
    for path in (
        observability._LOG_DIR,          # logs/plugin_<slug>.log
        fast_lane.MODELS_ROOT,
        cache.CACHE_ROOT,
        builder.BUILDS_ROOT,
        loader.installed_root(),         # computed per call: these follow HOME
        installer.staging_root(),
        installer.data_root(),
        config_bridge.plugin_env_file("radio"),
    ):
        assert _inside(path, home), path


def test_a_settings_path_into_the_real_home_is_moved_to_the_temp_one():
    real = home_isolation.real_config_dir()
    home = home_isolation.session_home()
    assert home_isolation.relocate(str(real / "piper_voices")) == str(home / ".domovoi" / "piper_voices")
    assert home_isolation.relocate(str(real)) == str(home / ".domovoi")
    assert home_isolation.relocate(str(home / ".domovoi" / "x")) is None
    assert home_isolation.relocate("relative/.domovoi") is None
    assert home_isolation.relocate(42) is None


def test_an_exported_path_into_the_real_home_is_dropped(tmp_path):
    """A child process, so the scrub runs on an environment of its own."""
    fake_real = tmp_path / "real-home"
    (fake_real / ".domovoi").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_XDIST", "DOMOVOI_TEST_"))}
    env.update({
        home_isolation.REAL_HOME_ENV: str(fake_real),
        "VOICE_MODELS_DIR": str(fake_real / ".domovoi" / "piper_voices"),
        "KEEP_ME": str(fake_real / "Music"),
    })
    # And the cleanup removes the home even with a log file still open in it
    # (a plugin's plugin_<slug>.log handler; Windows won't delete an open file).
    code = (
        "import os, logging; from domovoi.tests import home_isolation as h; home = h.isolate(); "
        "print(os.environ.get('VOICE_MODELS_DIR')); print(os.environ.get('KEEP_ME')); "
        "print(os.environ['HOME'] == str(home)); "
        "(home / '.domovoi' / 'logs').mkdir(); "
        "logging.getLogger('plugin.x').addHandler(logging.FileHandler(home / '.domovoi' / 'logs' / 'plugin_x.log')); "
        "logging.getLogger('plugin.x').warning('hi'); "
        "h.cleanup(); print(home.exists())"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["None", str(fake_real / "Music"), "True", "False"]


# ─── the guard ──────────────────────────────────────────────────────────────

def test_the_snapshot_sees_a_file_created_changed_or_removed(tmp_path):
    root = tmp_path / ".domovoi"
    (root / "logs").mkdir(parents=True)
    (root / "keep.txt").write_text("same")
    (root / "edit.txt").write_text("before")
    (root / "gone.txt").write_text("x")
    before = home_isolation.snapshot(root)

    (root / "logs" / "core-faulthandler.log").write_text("")
    (root / "edit.txt").write_text("after, and longer")
    (root / "gone.txt").unlink()
    (root / "run").mkdir()
    after = home_isolation.snapshot(root)

    found = {(kind, path) for kind, path, _ in home_isolation.changes(before, after)}
    assert found == {
        ("created", "logs/core-faulthandler.log"),
        ("modified", "edit.txt"),
        ("removed", "gone.txt"),
        ("created", "run"),
    }
    assert home_isolation.changes(after, home_isolation.snapshot(root)) == []


def test_a_missing_folder_has_nothing_to_compare_until_it_appears(tmp_path):
    root = tmp_path / ".domovoi"
    assert home_isolation.snapshot(root) is None
    assert home_isolation.changes(None, None) == []
    root.mkdir()
    (root / "device-token.txt").write_text("t")
    kinds = {(kind, path) for kind, path, _ in home_isolation.changes(None, home_isolation.snapshot(root))}
    assert kinds == {("created", "."), ("created", "device-token.txt")}


def _child_env(fake_real: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_XDIST", "DOMOVOI_TEST_", "PYTEST_ADDOPTS"))}
    env[home_isolation.REAL_HOME_ENV] = str(fake_real)
    # No database for the child: a dead port, so it neither probes the
    # lane nor makes worker copies of it.
    env["TEST_DATABASE_URL"] = "postgresql+asyncpg://domovoi:domovoi@127.0.0.1:9/domovoi_home_guard_test"
    return env


@pytest.mark.parametrize("workers", [None, 2], ids=["serial", "n2"])
def test_a_planted_write_fails_the_run_and_names_the_file(tmp_path, workers):
    fake_real = tmp_path / "real-home"
    (fake_real / ".domovoi").mkdir(parents=True)
    (fake_real / ".domovoi" / "device-token.txt").write_text("t")
    args = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(PLANT)]
    if workers:
        args += ["-n", str(workers)]
    out = subprocess.run(args, cwd=REPO, env=_child_env(fake_real),
                         capture_output=True, text=True, timeout=300)
    text = out.stdout + out.stderr
    assert "2 passed" in text, text           # the tests themselves pass...
    assert out.returncode == 1, text           # ...and the run fails anyway
    assert "real ~/.domovoi written during the run" in text
    assert f"created  {fake_real / '.domovoi' / 'planted.txt'}" in text
    assert f"modified {fake_real / '.domovoi' / 'device-token.txt'}" in text


def test_the_guard_can_be_switched_off(tmp_path):
    fake_real = tmp_path / "real-home"
    (fake_real / ".domovoi").mkdir(parents=True)
    (fake_real / ".domovoi" / "device-token.txt").write_text("t")
    env = _child_env(fake_real) | {home_isolation.GUARD_ENV: "off"}
    out = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(PLANT)],
                         cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "real ~/.domovoi written" not in out.stdout
