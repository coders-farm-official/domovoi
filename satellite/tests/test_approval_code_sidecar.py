"""Which approval code the device says while it waits (CORE-3 / CORE-9).

The customer is asked to type this code into the dashboard, so the device
has to say the one that is actually on the pending request. The server
sends it with the ``awaiting_approval`` frame; the sidecar is what
survives a reboot. The server's copy wins:

* a satellite that never ran the setup portal has no code of its own —
  in strict pairing mode the core mints one for it;
* a retry must not say a code the request has since moved on from.
"""

from __future__ import annotations

import pytest

from satellite.tests._client_import import import_client

client = import_client()


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    path = tmp_path / "approval_code"
    monkeypatch.setattr(client, "APPROVAL_CODE_SIDECAR", path)
    return path


def test_a_device_with_no_code_takes_the_one_the_server_sent(sidecar) -> None:
    assert client._remember_approval_code("481502") == "481502"
    assert sidecar.read_text(encoding="utf-8").strip() == "481502"


def test_the_servers_code_replaces_a_stale_one(sidecar) -> None:
    sidecar.write_text("111111\n", encoding="utf-8")
    assert client._remember_approval_code("930071") == "930071"
    assert sidecar.read_text(encoding="utf-8").strip() == "930071"


def test_without_a_code_from_the_server_the_sidecar_still_speaks(sidecar) -> None:
    """An older server sends no code with the frame; the portal's own code
    is still the right thing to say."""
    sidecar.write_text("481502\n", encoding="utf-8")
    for offered in (None, "", "   ", 12345):
        assert client._remember_approval_code(offered) == "481502"


def test_nothing_anywhere_says_nothing(sidecar) -> None:
    assert client._remember_approval_code(None) is None
    assert not sidecar.exists()


def test_an_unwritable_config_dir_still_says_the_code(sidecar, monkeypatch) -> None:
    """Persisting is a convenience for the next boot — failing to do it
    must not cost the customer the code they are being asked for."""
    def _boom(*a, **kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(type(sidecar), "write_text", _boom)
    assert client._remember_approval_code("481502") == "481502"
