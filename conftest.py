"""Loaded before every other conftest in the repo, so first: the home.

Every pytest run in this repo (any package, serial or ``-n``) starts here,
and from here on ``HOME``/``USERPROFILE`` are a temp folder of the
process's own: nothing a test does lands in the developer's real
``~/.domovoi``, and the run fails, naming the paths, if something does
anyway. Both halves are in domovoi/tests/home_isolation.py.
"""

from __future__ import annotations

from domovoi.tests import home_isolation  # stdlib only; imports nothing of domovoi

home_isolation.isolate()
home_isolation.start_guard()

import pytest  # noqa: E402

from domovoi.tests import live_port_guard  # noqa: E402 — stdlib only


@pytest.fixture(autouse=True)
def no_live_domovoi_ports():
    """No test reaches the Domovoi installed on this machine: a connection to
    loopback at 6369/6370 is refused, and the test fails even if the code
    under test swallowed the refusal (domovoi/tests/live_port_guard.py).

    Its own MonkeyPatch, not the ``monkeypatch`` fixture: requesting that
    here would set it up before every other fixture and so tear it down
    after them, which changes the order other fixtures undo their patches
    in (domovoi/tests/test_egress.py's ``live_policy`` relies on it)."""
    patcher = pytest.MonkeyPatch()
    guard = live_port_guard.install(patcher)
    try:
        yield guard
    finally:
        patcher.undo()
    reached = guard.clear()
    if reached:
        pytest.fail(
            "this test tried to reach the Domovoi installed on this machine at "
            + ", ".join(reached) + " — see domovoi/tests/live_port_guard.py"
        )


@pytest.hookimpl(trylast=True)  # after xdist has taken its workers down
def pytest_sessionfinish(session):
    home_isolation.check_guard(session)


def pytest_terminal_summary(terminalreporter):
    home_isolation.write_report(terminalreporter)


def pytest_unconfigure(config):
    if home_isolation.report_lines() and config.pluginmanager.get_plugin("terminalreporter") is None:
        home_isolation.write_report(None)
    home_isolation.cleanup()
