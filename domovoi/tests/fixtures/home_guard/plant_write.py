"""A deliberate write into the "real" home, for the guard to catch.

Not collected by the suite (no ``test_`` prefix): test_home_isolation.py
runs it in a child pytest whose real home (``DOMOVOI_TEST_REAL_HOME``) is
a tmp dir, never the developer's.
"""

from __future__ import annotations

import os
from pathlib import Path


def _real_config_dir() -> Path:
    return Path(os.environ["DOMOVOI_TEST_REAL_HOME"]) / ".domovoi"


def test_the_home_is_isolated_but_this_test_reaches_past_it():
    real = _real_config_dir()
    assert Path.home() / ".domovoi" != real
    (real / "planted.txt").write_text("a test wrote me", encoding="utf-8")


def test_a_second_writer_changes_an_existing_file():
    (_real_config_dir() / "device-token.txt").write_text("changed", encoding="utf-8")
