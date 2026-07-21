"""Dump the FastAPI app's OpenAPI spec to ``web/openapi.json``.

Run from the repo root::

    python -m web.scripts.dump_openapi

Used as the input to Claude Design's per-page prompts so the design
process knows the exact contract — field names, types, status codes —
without having to read the route source.
"""

from __future__ import annotations

import json
from pathlib import Path

from web.backend.main import app


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "openapi.json"
    spec = app.openapi()
    out.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
