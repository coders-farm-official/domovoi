"""A slow core-entry import is a soft note, never a load failure.

Live report (1.4.0): Radio showed "load error" and was disabled after one
slow boot on a busy box. A slow import now keeps the plugin enabled and
flags a warning (judged on CPU time when measured); real violations such
as CUDA initialized at import still fail. DB-free.
"""

from __future__ import annotations

from domovoi.plugins_runtime.contracts import (
    IMPORT_TIME_BUDGET_SEC,
    ContractReport,
    check_import_budget,
)

SLOW = IMPORT_TIME_BUDGET_SEC * 4
FAST = 0.5


def _run(wall, cpu=None, cuda=False) -> ContractReport:
    report = ContractReport()
    check_import_budget("radio", wall, cuda, report, cpu)
    return report


def test_over_budget_import_is_a_warning_and_keeps_the_plugin_enabled():
    report = _run(SLOW)
    assert report.ok() and not report.errors
    assert report.slow_import and "loaded slowly" in report.slow_import
    assert report.slow_import in report.warnings


def test_busy_box_wall_time_alone_does_not_flag_when_cpu_was_small():
    report = _run(SLOW, cpu=FAST)
    assert report.ok() and report.slow_import is None and not report.warnings


def test_heavy_cpu_import_is_flagged_softly_with_both_numbers():
    report = _run(SLOW, cpu=SLOW)
    assert report.ok()
    assert "of CPU" in report.slow_import


def test_fast_import_has_no_warning():
    report = _run(FAST, cpu=FAST)
    assert report.ok() and not report.warnings and report.slow_import is None


def test_a_real_violation_still_disables():
    report = _run(SLOW, cuda=True)
    assert not report.ok()
    assert any("initialized CUDA" in e for e in report.errors)
    assert report.slow_import  # the slow note rides along, soft
