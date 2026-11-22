"""scripts/release/check_plugin_locks.py: a first-party plugin's lock must pin
every package the core also pins at the core's version."""
from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release" / "check_plugin_locks.py"
H = "--hash=sha256:" + "0" * 64


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("check_plugin_locks", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["check_plugin_locks"] = m
    spec.loader.exec_module(m)
    return m


def _core(tmp_path: Path) -> list[str]:
    a = tmp_path / "requirements-linux-py314.lock"
    b = tmp_path / "requirements.lock"
    a.write_text(f"certifi==2026.10.23 \\n    {H}\nhttpx==0.30.0 \\n    {H}\n", encoding="utf-8")
    b.write_text(f"certifi==2026.10.23 \\n    {H}\nNumPy_x==2.0 \\n    {H}\n", encoding="utf-8")
    return ["--core-lock", str(a), "--core-lock", str(b)]


def _zip(path: Path, lock: str, manifest_extra: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("media/domovoi-plugin.toml",
                   f'[plugin]\nslug = "media"\n[requirements]\npython = ["x==1"]\n{manifest_extra}')
        z.writestr("media/requirements.lock", lock)
    return path


def test_matching_lock_passes(mod, tmp_path, capsys) -> None:
    lock = tmp_path / "ok.lock"
    lock.write_text(f"certifi==2026.10.23 \\n    {H}\nother==1.0 \\n    {H}\n", encoding="utf-8")
    assert mod.main([str(lock), *_core(tmp_path)]) == 0
    assert "ok" in capsys.readouterr().out


def test_drift_fails_with_a_table(mod, tmp_path, capsys) -> None:
    lock = tmp_path / "bad.lock"
    lock.write_text(f"certifi==2026.6.17 \\n    {H}\nnumpy-x==2.0 \\n    {H}\n", encoding="utf-8")
    assert mod.main([str(lock), *_core(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "certifi" in out and "2026.6.17" in out and "2026.10.23" in out
    # the same pin in both core locks is reported against each
    assert out.count("certifi") == 2
    assert "numpy" not in out.lower()      # equal after name normalisation


def test_a_zip_is_read_through_its_manifest(mod, tmp_path, capsys) -> None:
    bad = _zip(tmp_path / "bad.zip", f"httpx==0.1.0 \\n    {H}\n")
    assert mod.main([str(bad), *_core(tmp_path)]) == 1
    assert "httpx" in capsys.readouterr().out
    good = _zip(tmp_path / "good.zip", f"httpx==0.30.0 \\n    {H}\n")
    assert mod.main([str(good), *_core(tmp_path)]) == 0


def test_a_named_lockfile_is_used(mod, tmp_path) -> None:
    p = tmp_path / "named.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("domovoi-plugin.toml",
                   '[requirements]\npython = ["x==1"]\nlockfile = "locks/py.lock"\n')
        z.writestr("locks/py.lock", f"certifi==1.0 \\n    {H}\n")
    assert mod.main([str(p), *_core(tmp_path)]) == 1


def test_unreadable_input_exits_2(mod, tmp_path) -> None:
    assert mod.main([str(tmp_path / "missing.lock"), *_core(tmp_path)]) == 2


def test_the_repos_own_core_locks_parse(mod) -> None:
    for name in mod.CORE_LOCKS:
        pins = mod.parse_pins((REPO / name).read_text(encoding="utf-8"))
        assert "certifi" in pins, name
