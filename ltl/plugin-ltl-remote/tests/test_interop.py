"""Run the JS ↔ Python handshake for real, from pytest.

The unit tests on either side prove each implementation is
self-consistent. That is not the same as proving they agree, and "the
two halves of the protocol drifted" is precisely the bug that would be
invisible until a customer could not connect.

Skips when Node is unavailable, since the browser half needs a WebCrypto
runtime.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from conftest import PLUGIN_DIR

CLIENT = PLUGIN_DIR.parent / "interop" / "client.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node 18+ for WebCrypto")
def test_javascript_and_python_interoperate():
    result = subprocess.run(
        ["node", str(CLIENT)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=CLIENT.parent,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout
    assert "handshake: OK" in output
    assert "client -> home: OK" in output
    assert "home -> client: OK" in output
    assert "replay rejected: OK" in output
