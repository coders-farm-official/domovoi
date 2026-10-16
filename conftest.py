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


@pytest.hookimpl(trylast=True)  # after xdist has taken its workers down
def pytest_sessionfinish(session):
    home_isolation.check_guard(session)


def pytest_terminal_summary(terminalreporter):
    home_isolation.write_report(terminalreporter)


def pytest_unconfigure(config):
    if home_isolation.report_lines() and config.pluginmanager.get_plugin("terminalreporter") is None:
        home_isolation.write_report(None)
    home_isolation.cleanup()
