"""Read a plugin's route-tier markers off its SOURCE (design §4.11 / §5.1,
PLG-1): which route functions carry ``@device_endpoint`` (any paired
household device) or ``@open_endpoint`` (no credential at all).

Two callers, one walk, so they cannot disagree:

* **the install preview** — :mod:`domovoi.plugins_runtime.open_endpoints`
  runs THIS file as a script in a throwaway subprocess (``python -I
  route_markers.py <package dir>``) against the STAGED package, before
  anything of it has been imported, and the trust screen lists what it
  finds;
* **the load** — both processes, once the plugin is imported, hold every
  route that is off the admin tier at RUNTIME against the same walk of
  the installed package (``domovoi.webkit.unlisted_tier_routes``) and
  refuse to serve one the walk does not list. A marker applied any way
  the walk cannot see (a ``setattr`` of the marker attribute, a call
  instead of a decorator, a helper defined outside the package) therefore
  never serves a route the admin was not shown.

This module imports only the stdlib: the subprocess needs no domovoi on
its path, and the web dashboard process — which must never import
``domovoi.plugins_runtime`` — can use it through ``domovoi.webkit``.

What the walk recognises, per ``def``: a decorator naming a marker —
``@device_endpoint``, ``@webkit.device_endpoint``, or a local alias bound
in the same file (``from domovoi.sdk import device_endpoint as household``
or ``household = device_endpoint``) — together with the router decorators
on the same ``def``. A router decorator whose path is not a string literal
reports ``path: None``. A function carrying BOTH markers is reported as a
conflict, which the installer refuses (the runtime refuses to load it too).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

__all__ = ["MARKERS", "scan_marked_endpoints"]

# Decorator name → the tier it puts a route on.
MARKERS = {"open_endpoint": "open", "device_endpoint": "device"}

_ROUTE_METHODS = {
    "get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH",
    "delete": "DELETE", "head": "HEAD", "options": "OPTIONS",
    "api_route": None, "route": None, "websocket": "WS",
}


def _name_of(node: ast.expr) -> str | None:
    """``x`` → ``"x"``; ``a.b.x`` → ``"x"``; anything else → None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _decorator_name(node: ast.expr) -> str | None:
    """``open_endpoint`` / ``sdk.open_endpoint`` / ``open_endpoint()`` →
    ``"open_endpoint"``; anything else → its trailing attribute name."""
    if isinstance(node, ast.Call):
        node = node.func
    return _name_of(node)


def _marker_aliases(tree: ast.AST) -> dict[str, str]:
    """Local names this file binds to a marker, → the marker's tier:
    ``from … import device_endpoint as household`` and
    ``household = device_endpoint`` (or ``= webkit.device_endpoint``, or
    ``= <an alias bound above>``). Without these, an aliased marker would
    put a route on a tier the trust screen never showed."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in MARKERS and alias.asname:
                    aliases[alias.asname] = MARKERS[alias.name]
    assigns: list[tuple[str, ast.expr]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            assigns.append((node.targets[0].id, node.value))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assigns.append((node.target.id, node.value))
    # Chains (``a = device_endpoint``; ``b = a``) settle in a few rounds.
    for _ in range(4):
        changed = False
        for target, value in assigns:
            name = _name_of(value)
            tier = MARKERS.get(name or "") or aliases.get(name or "")
            if tier and aliases.get(target) != tier:
                aliases[target] = tier
                changed = True
        if not changed:
            break
    return aliases


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


def scan_marked_endpoints(package_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Walk every ``.py`` under ``package_dir`` (the plugin's
    ``domovoi_plugin_<slug>`` package) and return
    ``{"open": [...], "device": [...], "conflicts": [...]}`` — one record
    per route of every function decorated ``@open_endpoint``,
    ``@device_endpoint``, or (a conflict) both:
    ``{method, path, module, function, process, line}``. ``process`` is
    ``"web"`` for the package's ``web`` module (and modules under a
    ``web`` subpackage), else ``"core"`` — the two mount prefixes the
    trust screen renders. Raises ``SyntaxError`` (propagated) for a file
    the parser cannot read."""
    package_dir = Path(package_dir)
    found: dict[str, list[dict[str, Any]]] = {"open": [], "device": [], "conflicts": []}
    for file in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"), str(file))
        aliases = _marker_aliases(tree)
        module = _module_name(package_dir, file)
        tail = module.split(".", 1)[1] if "." in module else ""
        process = "web" if tail == "web" or tail.startswith("web.") else "core"
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {_decorator_name(d) for d in node.decorator_list}
            tiers = {
                MARKERS.get(n or "") or aliases.get(n or "")
                for n in names
            } - {None}
            if not tiers:
                continue
            bucket = "conflicts" if len(tiers) > 1 else tiers.pop()
            routes = [r for r in (_route_decorator(d) for d in node.decorator_list) if r]
            if not routes:
                routes = [(None, None)]
            for method, path in routes:
                found[bucket].append(
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


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"error": "usage: route_markers <package dir>"}))
        return 2
    package_dir = Path(argv[1])
    if not package_dir.is_dir():
        print(json.dumps({"error": f"{package_dir} is not a directory"}))
        return 1
    try:
        marked = scan_marked_endpoints(package_dir)
    except SyntaxError as e:
        print(json.dumps({"error": f"cannot parse {e.filename} line {e.lineno}: {e.msg}"}))
        return 1
    except RecursionError:
        print(json.dumps({"error": "source too deeply nested to parse"}))
        return 1
    # ``endpoints`` keeps its name (the open list); the device tier and
    # any conflicts ride beside it.
    print(json.dumps({
        "endpoints": marked["open"],
        "device_endpoints": marked["device"],
        "conflicts": marked["conflicts"],
    }))
    return 0


if __name__ == "__main__":  # pragma: no cover — the subprocess entry
    sys.setrecursionlimit(3000)
    raise SystemExit(_main(sys.argv))
