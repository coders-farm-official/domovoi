"""Every dashboard script shares one global scope — pin that no two collide.

index.html loads each page file as ``<script type="text/babel">``. The
in-browser Babel compiles it with its default script-tag plugins (object
rest/spread among them) and runs the result as a classic script, so every
top-level name, including helpers the COMPILER adds such as ``const
_excluded`` for ``{ a, ...rest }``, lands in the one global scope.

A ``const``/``let``/``class`` declared by two scripts is a SyntaxError that
stops the second script outright. On 2026-09-27 that is exactly what
happened: data.js gained ``const { quiet, noPrompt, ...init } = opts``, the
compiler emitted ``const _excluded`` for it, components.jsx already had one
for Button, and components.jsx never ran, so the dashboard rendered blank.
jsxcheck and dupglobals both passed, because they compile or scan each file
with presets only and never see compiler helpers.

This compiles every script exactly as the page does
(domovoi/tests/script_scope_check.js) and fails on any lexical name declared
by more than one script. ``var`` and ``function`` duplicates are legal in
classic scripts (the compiler's ``_extends`` helper is one) and are allowed.

No DB — never skips. Needs ``node``, like the render harnesses.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK = Path(__file__).with_name("script_scope_check.js")


def _declarations() -> dict:
    node = shutil.which("node")
    assert node, "node is required to compile web/static the way the browser does"
    out = subprocess.run(
        [node, str(CHECK), str(REPO_ROOT)],
        capture_output=True, text=True, timeout=300, check=True,
    )
    return json.loads(out.stdout)


def test_no_two_dashboard_scripts_declare_the_same_lexical_global():
    report = _declarations()
    assert "data.js" in " ".join(report["scripts"]), report["scripts"]
    owners: dict[str, list[tuple[str, str]]] = {}
    for script, d in report["decls"].items():
        for name in d["lexical"]:
            owners.setdefault(name, []).append((script, "lexical"))
        for name in d["other"]:
            owners.setdefault(name, []).append((script, "var/function"))
    clashes = {
        name: where for name, where in owners.items()
        if len({s for s, _ in where}) > 1 and any(kind == "lexical" for _, kind in where)
    }
    assert not clashes, (
        "top-level names declared by more than one dashboard script "
        "(a SyntaxError in the browser; compiler helpers count too): "
        f"{json.dumps(clashes, indent=1)}"
    )


def test_the_check_sees_compiler_helpers():
    """Guard the guard: the compile must include the script-tag plugins,
    or a compiler-emitted helper like _excluded is invisible again."""
    report = _declarations()
    comps = next(d for s, d in report["decls"].items() if s.startswith("components.jsx"))
    assert "_excluded" in comps["lexical"], (
        "components.jsx's Button uses `...rest`; if _excluded no longer shows "
        "up, the check stopped compiling like the browser"
    )
