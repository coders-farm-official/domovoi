"""Walking an app's routes in a way that works on every FastAPI we allow.

``pyproject.toml`` floors FastAPI at 0.115, and the two halves of that
range disagree about what ``app.routes`` contains:

* up to 0.138, ``include_router`` FLATTENS — every included endpoint lands
  in ``app.routes`` as an ``APIRoute``, so ``isinstance(r, APIRoute)``
  finds all of them;
* from 0.139, the included router is kept as one ``_IncludedRouter``
  entry, so that same ``isinstance`` check silently matches only the
  handful of routes declared on the app itself, and the effective path,
  methods and dependencies of everything else are behind
  ``iter_route_contexts``.

"Silently" is the whole problem. A route-tier test that walks the wrong
way does not fail — it finds nothing, asserts nothing about nothing, and
passes. That is exactly how a mounted-and-gated upload route came to look
unmounted (and how the opposite mistake would hide an ungated one), so
every test that inspects the route table imports this helper rather than
picking a walk of its own.

Prefer the real function when it exists, because only FastAPI knows what
a router's prefix, dependencies and overrides resolve to; the fallback is
for the older layout where there is nothing to resolve.
"""

from __future__ import annotations

try:  # FastAPI >= 0.139
    from fastapi.routing import iter_route_contexts
except ImportError:  # pragma: no cover — older FastAPI flattens on include
    from fastapi.routing import APIRoute

    def iter_route_contexts(routes):  # type: ignore[misc]
        return [r for r in routes if isinstance(r, APIRoute)]


__all__ = ["iter_route_contexts"]
