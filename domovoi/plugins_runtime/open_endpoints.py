"""Find a staged plugin's ``@open_endpoint`` routes for the install
preview (design §4.11 / §5.1, PLG-1).

Both processes gate plugin mutations by default; a route author opts a
route out with ``@open_endpoint`` (``domovoi.sdk`` / ``domovoi.webkit``).
The trust screen promises to list every such opt-out, so the admin sees
the plugin's open surface BEFORE confirming. That list has to come from
the staged source without importing it — importing runs plugin code, and
nothing runs pre-confirm — so this module walks the package's Python
files with :mod:`ast` and reports every function that carries the
decorator together with the router decorator on the same ``def``.

The walk runs in a **throwaway subprocess** (``python -I open_endpoints.py
<package dir>`` — this file imports only the stdlib): ``ast.parse`` never
executes code, but a crafted source can still exhaust the parser
(recursion depth, memory), and that must not take the core down. The
subprocess is time-boxed; a failure refuses the install (fail closed —
a package the parser cannot read is one the trust screen cannot
describe).

Static best effort, by construction: a router decorator whose path is
not a string literal reports ``path: None``; a decorator spelled through
an alias other than ``open_endpoint`` is not recognised. A plugin author
follows the documented shape (``@router.post("/x")`` over
``@open_endpoint`` over the ``def``) and gets an accurate list.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

__all__ = ["scan_open_endpoints", "collect_open_endpoints", "OpenEndpointScanError"]

_ROUTE_METHODS = {
    "get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH",
    "delete": "DELETE", "head": "HEAD", "options": "OPTIONS",
    "api_route": None, "route": None, "websocket": "WS",
}
SCAN_TIMEOUT_SEC = 60


class OpenEndpointScanError(RuntimeError):
    """The subprocess scan failed or timed out."""


def _decorator_name(node: ast.expr) -> str | None:
    """``open_endpoint`` / ``sdk.open_endpoint`` / ``open_endpoint()`` →
    ``"open_endpoint"``; anything else → its trailing attribute name."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _route_decorator(node: ast.expr) -> tuple[str | None, str | None] | None:
    """(method, path) for ``@<something>.post("/x", ...)``-shaped
    decorators; None when the decorator is not a router method."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    attr = node.func.attr
    if attr not in _ROUTE_METHODS:
        return None
    method = _ROUTE_METHODS[attr]
    path: str | None = None
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(
        node.args[0].value, str
    ):
        path = node.args[0].value
    else:
        for kw in node.keywords:
            if kw.arg == "path" and isinstance(kw.value, ast.Constant) and isinstance(
                kw.value.value, str
            ):
                path = kw.value.value
    if method is None:  # api_route(..., methods=[...])
        for kw in node.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                names = [
                    e.value.upper() for e in kw.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                method = ",".join(names) or None
    return method, path


def _module_name(package_dir: Path, file: Path) -> str:
    rel = file.relative_to(package_dir.parent).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def scan_open_endpoints(package_dir: Path) -> list[dict[str, Any]]:
    """Walk every ``.py`` under ``package_dir`` (the plugin's
    ``domovoi_plugin_<slug>`` package) and return one record per
    function decorated ``@open_endpoint``:
    ``{method, path, module, function, process, line}``. ``process`` is
    ``"web"`` for the package's ``web`` module (and modules under a
    ``web`` subpackage), else ``"core"`` — the two mount prefixes the
    trust screen renders. Raises ``SyntaxError`` (propagated) for a file
    the parser cannot read."""
    package_dir = Path(package_dir)
    found: list[dict[str, Any]] = []
    for file in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"), str(file))
        module = _module_name(package_dir, file)
        tail = module.split(".", 1)[1] if "." in module else ""
        process = "web" if tail == "web" or tail.startswith("web.") else "core"
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = [_decorator_name(d) for d in node.decorator_list]
            if "open_endpoint" not in names:
                continue
            routes = [r for r in (_route_decorator(d) for d in node.decorator_list) if r]
            if not routes:
                routes = [(None, None)]
            for method, path in routes:
                found.append(
                    {
                        "method": method,
                        "path": path,
                        "module": module,
                        "function": node.name,
                        "process": process,
                        "line": node.lineno,
                    }
                )
    return found


def collect_open_endpoints(
    package_dir: Path, *, timeout: float = SCAN_TIMEOUT_SEC
) -> list[dict[str, Any]]:
    """Run :func:`scan_open_endpoints` in a throwaway subprocess and
    return its records. Raises :class:`OpenEndpointScanError` when the
    scan fails, times out, or reports a file it could not parse."""
    # Run THIS file as a plain script: it imports only the stdlib, so the
    # child needs no domovoi on its path, and -I (isolated: no env vars,
    # no site customisation, no cwd on sys.path) keeps the walk inert.
    proc_args = [
        sys.executable, "-I", "-X", "utf8", str(Path(__file__).resolve()),
        str(package_dir),
    ]
    try:
        proc = subprocess.run(
            proc_args, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise OpenEndpointScanError(
            f"open-endpoint scan of {package_dir.name} exceeded {timeout:.0f}s"
        )
    except OSError as e:  # pragma: no cover — interpreter missing
        raise OpenEndpointScanError(f"open-endpoint scan could not start: {e}")
    try:
        payload = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("error"):
        raise OpenEndpointScanError(f"open-endpoint scan: {payload['error']}")
    if proc.returncode != 0 or not isinstance(payload, dict):
        raise OpenEndpointScanError(
            "open-endpoint scan failed: "
            + (proc.stderr or proc.stdout or "").strip()[-1500:]
        )
    return list(payload.get("endpoints") or [])


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"error": "usage: open_endpoints <package dir>"}))
        return 2
    package_dir = Path(argv[1])
    if not package_dir.is_dir():
        print(json.dumps({"error": f"{package_dir} is not a directory"}))
        return 1
    try:
        endpoints = scan_open_endpoints(package_dir)
    except SyntaxError as e:
        print(json.dumps({"error": f"cannot parse {e.filename} line {e.lineno}: {e.msg}"}))
        return 1
    except RecursionError:
        print(json.dumps({"error": "source too deeply nested to parse"}))
        return 1
    print(json.dumps({"endpoints": endpoints}))
    return 0


if __name__ == "__main__":  # pragma: no cover — the subprocess entry
    sys.setrecursionlimit(3000)
    raise SystemExit(_main(sys.argv))
