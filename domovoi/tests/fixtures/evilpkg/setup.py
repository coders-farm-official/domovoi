"""Security fixture — a deliberately hostile sdist.

This package's build backend (this very ``setup.py``, executed by pip when
a source distribution is built) writes a MARKER FILE the moment it runs.
It exists so the plugin-installer test can prove that a poisoned lockfile
(one that smuggles ``--no-binary :all:`` past the installer's
``--only-binary=:all:`` guard) is REJECTED at validation and this build
backend therefore never executes: if the marker file is absent after a
rejected install, the arbitrary code below did not run.

Nothing here is provider-specific; ``evilpkg`` is a generic stand-in for
"any attacker-published dependency".
"""

import os
import pathlib

# Arbitrary code that would run at sdist-build time under a naive installer.
_marker = os.environ.get("EVILPKG_MARKER")
if _marker:
    pathlib.Path(_marker).write_text("build backend executed\n", encoding="utf-8")

from setuptools import setup  # noqa: E402

setup(
    name="evilpkg",
    version="0.0.0",
    py_modules=["evilpkg"],
    description="Security test fixture; never install for real.",
)
