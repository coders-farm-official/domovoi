"""Stage 2 on the satellite holds the wake-word models to the server's pins
(SAT-11, 2026-10 review REV-17).

Before: stage 2 copied the card's cached models into openWakeWord's package
and, when no ``.onnx`` was there, ran ``openwakeword.utils.download_models()``
with no digest check, from the same release the server-side pin protects
against. A card whose cache was short (the server's fetch stopped at the
first bad asset and the build went ahead) therefore re-downloaded the
tampered file on the Pi. Now:

* the pins (``fetchers.OWW_MODEL_FILES``) are rendered into stage 2;
* every model file placed in the package directory, from the card's cache or
  from openWakeWord's download, is checked against them, and anything they
  don't vouch for is deleted;
* the download runs only when the card carries no cached models at all;
* the step's marker goes down only when every pinned model is there.

The 3b block of the RENDERED script runs here under bash (Git Bash on
Windows) with a fake venv Python: no network, no root, no DB.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi.satellite_media import fetchers, overlay


def _find_bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        for up in (Path(git).parent.parent, Path(git).parent.parent.parent):
            candidates.append(up / "bin" / "bash.exe")
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file() and "system32" not in str(c).lower():
            return str(c)
    return None


BASH = _find_bash()
requires_bash = pytest.mark.skipif(BASH is None, reason="no usable bash (Git Bash on Windows)")

A = b"wake-model-a" * 300
B = b"wake-model-b" * 200
C = b"melspec-c" * 250
FILES = {"a_v0.1.onnx": A, "a_v0.1.tflite": B, "melspectrogram.onnx": C}
PINS = "\n".join(
    f"{hashlib.sha256(body).hexdigest()} {len(body)} {name}" for name, body in sorted(FILES.items())
)


# ─── the rendered script ──────────────────────────────────────────────────

def test_the_real_pins_are_rendered_into_stage2() -> None:
    script = overlay.render_stage2("domovoi")
    assert "@OWW_PINS@" not in script
    m = re.search(r'^OWW_PINS="([^"]*)"$', script, re.MULTILINE)
    assert m, "stage 2 assigns no OWW_PINS"
    lines = m.group(1).splitlines()
    assert len(lines) == len(fetchers.OWW_MODEL_FILES)
    for line in lines:
        sha, size, name = line.split(" ")
        assert fetchers.OWW_MODEL_FILES[name] == (sha, int(size))


def test_no_multiline_value_lands_in_a_comment() -> None:
    """A placeholder named in a comment is replaced there too, and only the
    first line of a multi-line value would stay commented: the rest would
    run as commands. The pins appear once, on their assignment."""
    template = (overlay.TEMPLATES_DIR / "stage2.sh.tmpl").read_text(encoding="utf-8")
    uses = [line for line in template.splitlines() if "@OWW_PINS@" in line]
    assert uses == ['OWW_PINS="@OWW_PINS@"']
    script = overlay.render_stage2("domovoi")
    pin_shaped = [l for l in script.splitlines() if re.search(r"[0-9a-f]{64} [0-9]+ \S+", l)]
    assert len(pin_shaped) == len(fetchers.OWW_MODEL_FILES)


def test_the_download_is_no_longer_unconditional_or_unchecked() -> None:
    script = overlay.render_stage2("domovoi")
    block = _block(script)
    assert "download_models()" in block
    # The fallback is guarded by "no cached models on the card", and the
    # check runs after it whichever branch ran.
    assert block.index('ls "$OWW_CACHE"/*.onnx') < block.index("download_models()")
    assert block.index("download_models()") < block.index('oww_verify "$OWW_DIR"')
    assert 'touch "$STEPS/wake-models"' in block.split('if oww_verify "$OWW_DIR"; then', 1)[1].split("elif", 1)[0]


# ─── the block, run ───────────────────────────────────────────────────────

def _block(script: str) -> str:
    start = script.index('OWW_PINS="')
    end = script.index("# 4. Fresh code")
    return script[start:end]


FAKE_PY = r"""#!/usr/bin/env bash
# The satellite venv's python, as far as stage 2's step 3b asks it.
case "$*" in
  *"print(os.path.join"*) printf '%s\n' "$FAKE_PKG_DIR" ;;
  *download_models*)
    echo download >>"$FAKE_LOG"
    cp -r "$FAKE_RELEASE"/. "$FAKE_PKG_DIR"/ ;;
esac
exit 0
"""


def _posix(p: Path) -> str:
    s = str(p)
    if sys.platform == "win32":
        drive, rest = os.path.splitdrive(s)
        return "/" + drive.rstrip(":").lower() + rest.replace("\\", "/")
    return s


@pytest.fixture
def sat(tmp_path):
    class Sat:
        home = tmp_path / "home"
        cache = tmp_path / "home" / ".domovoi" / "oww_models"
        pkg = tmp_path / "venv" / "openwakeword" / "resources" / "models"
        release = tmp_path / "release"
        steps = tmp_path / "steps"
        log = tmp_path / "calls.log"

        def run(self) -> subprocess.CompletedProcess:
            script = overlay.render_template(
                "stage2.sh.tmpl",
                {"SAT_USER": "domovoi", "CODE_EXT_ALLOW": "{}", "SDIST_ONLY": "",
                 "APT_PACKAGES": "", "OWW_PINS": PINS},
            )
            fake = tmp_path / "python"
            fake.write_bytes(FAKE_PY.encode("utf-8"))
            fake.chmod(0o755)
            runner = tmp_path / "run.sh"
            runner.write_bytes((
                "set -u\n"
                f'HOME_DIR="{_posix(self.home)}"\n'
                f'STEPS="{_posix(self.steps)}"\n'
                f'VENVPY="{_posix(fake)}"\n'
                'SAT_USER="$(id -un)"\n'
                'as_user() { "$@"; }\n'
                "chown() { :; }\n"
                + _block(script)
            ).encode("utf-8"))
            env = {**os.environ, "FAKE_PKG_DIR": _posix(self.pkg), "FAKE_RELEASE": _posix(self.release),
                   "FAKE_LOG": _posix(self.log)}
            return subprocess.run([BASH, str(runner)], capture_output=True, text=True, env=env, timeout=120)

        def downloads(self) -> int:
            return self.log.read_text(encoding="utf-8").count("download") if self.log.exists() else 0

        def names(self) -> list[str]:
            return sorted(p.name for p in self.pkg.iterdir()) if self.pkg.is_dir() else []

        def done(self) -> bool:
            return (self.steps / "wake-models").exists()

    for d in (Sat.home, Sat.release, Sat.steps):
        d.mkdir(parents=True, exist_ok=True)
    return Sat()


def _fill(d: Path, files: dict[str, bytes]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (d / name).write_bytes(body)


@requires_bash
def test_a_card_with_its_verified_cache_needs_no_download(sat) -> None:
    _fill(sat.cache, FILES)
    proc = sat.run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sat.names() == sorted(FILES)
    assert sat.downloads() == 0
    assert sat.done()
    assert "present and verified" in proc.stdout


@requires_bash
def test_a_tampered_cached_model_is_deleted_and_not_downloaded_over(sat) -> None:
    _fill(sat.cache, {**FILES, "a_v0.1.onnx": A[::-1]})
    proc = sat.run()
    assert proc.returncode == 0, proc.stderr
    assert "a_v0.1.onnx" not in sat.names()
    assert sat.downloads() == 0, "the cached bucket is there: no download"
    assert not sat.done(), "an incomplete set must be retried, not marked done"
    assert "does not match its pinned SHA-256" in proc.stdout


@requires_bash
def test_without_a_cache_the_download_runs_and_is_checked(sat) -> None:
    _fill(sat.release, FILES)
    proc = sat.run()
    assert proc.returncode == 0, proc.stderr
    assert sat.downloads() == 1
    assert sat.names() == sorted(FILES)
    assert sat.done()


@requires_bash
def test_a_tampered_or_unpinned_download_is_deleted(sat) -> None:
    """The REV-17 path: a replaced release asset arrives through
    openWakeWord's own download. It is removed, never loaded."""
    _fill(sat.release, {**FILES, "melspectrogram.onnx": C + b"evil", "surprise_v9.onnx": b"x" * 100})
    proc = sat.run()
    assert proc.returncode == 0, proc.stderr
    assert sat.downloads() == 1
    assert sat.names() == ["a_v0.1.onnx", "a_v0.1.tflite"]
    assert not sat.done()
    assert "melspectrogram.onnx: it does not match its pinned SHA-256" in proc.stdout
    assert "surprise_v9.onnx: not one of the pinned models" in proc.stdout
    assert "melspectrogram.onnx is missing" in proc.stdout


@requires_bash
def test_a_short_file_with_the_right_prefix_is_refused(sat) -> None:
    _fill(sat.cache, {**FILES, "a_v0.1.tflite": B[:-1]})
    proc = sat.run()
    assert "a_v0.1.tflite" not in sat.names()
    assert not sat.done()


@requires_bash
def test_a_done_step_is_not_run_again(sat) -> None:
    (sat.steps / "wake-models").write_text("", encoding="utf-8")
    proc = sat.run()
    assert proc.returncode == 0
    assert sat.names() == [] and sat.downloads() == 0
