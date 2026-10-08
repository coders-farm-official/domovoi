"""What the checkout must never carry (OPS-14, OPS-20).

Pure file and ``git check-ignore`` checks: no DB, no network.

* No throwaway harness at the repo root. ``.secint_migrate.py`` dropped
  and recreated both databases with the default credential and applied
  migrations outside Flyway; it reached main once from a security
  integration branch. Session tooling lives outside the product repo.
* ``.gitignore`` covers the credential files the core writes
  (``~/.domovoi/device-token.txt``, ``setup-code.txt``,
  ``server-identity.json``), the satellite's real config, Android signing
  keystores, TLS and SSH keys, and the update unit's allowed-signers file,
  so a copy made for a test can't ride along on ``git add -A``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT = shutil.which("git")


def test_no_hidden_python_scripts_at_the_repo_root() -> None:
    hidden = sorted(p.name for p in REPO_ROOT.glob(".*.py"))
    assert hidden == [], f"hidden scripts at the repo root: {hidden}"


def test_the_security_integration_migrator_is_gone() -> None:
    assert not (REPO_ROOT / ".secint_migrate.py").exists()


# Paths that must be ignored, as a contributor would create them.
MUST_IGNORE = [
    "device-token.txt",
    "domovoi/device-token.txt",
    # Any copy, not just the .txt the core writes (a backup, a renamed one).
    "device-token",
    "device-token.bak",
    "setup-code.txt",
    "setup-code",
    "setup-code.old",
    "server-identity.json",
    "satellite/config.toml",
    "android/app/release.jks",
    "android/release.keystore",
    "server.pem",
    "tls/server.key",
    "signing.p12",
    "signing.pfx",
    "id_rsa",
    "id_rsa.pub",
    "id_ed25519",
    "allowed_signers",
]

# Tracked files the new rules must leave alone.
MUST_TRACK = [
    "satellite/config.toml.example",
    "domovoi/.env.example",
]


def _ignored(path: str) -> bool:
    # --no-index: answer from the ignore rules alone, tracked or not.
    proc = subprocess.run(
        [GIT, "-C", str(REPO_ROOT), "check-ignore", "-q", "--no-index", path],
        capture_output=True,
        text=True,
    )
    assert proc.returncode in (0, 1), proc.stderr
    return proc.returncode == 0


@pytest.mark.skipif(GIT is None, reason="git is not on PATH")
@pytest.mark.parametrize("path", MUST_IGNORE)
def test_credential_files_are_ignored(path: str) -> None:
    assert _ignored(path), f"{path} is not covered by .gitignore"


@pytest.mark.skipif(GIT is None, reason="git is not on PATH")
@pytest.mark.parametrize("path", MUST_TRACK)
def test_examples_stay_trackable(path: str) -> None:
    assert (REPO_ROOT / path).exists(), f"{path} is missing"
    assert not _ignored(path), f"{path} is ignored by the new rules"
