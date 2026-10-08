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

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_hidden_python_scripts_at_the_repo_root() -> None:
    hidden = sorted(p.name for p in REPO_ROOT.glob(".*.py"))
    assert hidden == [], f"hidden scripts at the repo root: {hidden}"


def test_the_security_integration_migrator_is_gone() -> None:
    assert not (REPO_ROOT / ".secint_migrate.py").exists()
