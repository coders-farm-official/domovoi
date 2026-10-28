"""The Word-conversion worker: ``python -m domovoi.richdoc.worker DIR``.

Started by :mod:`domovoi.richdoc.runner` in a short-lived process. It caps
itself first (the document reader's limits, :mod:`domovoi.doc_extract.limits`;
it refuses to convert when it can't), reads ``DIR/input.html`` and
``DIR/request.json`` (``{"title"}``), sanitises and converts, and writes
``DIR/out.docx`` or ``DIR/error.json``. It opens no network connection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    d = Path(argv[1])
    req = json.loads((d / "request.json").read_text(encoding="utf-8"))

    def fail(message: str, detail: str = "") -> int:
        (d / "error.json").write_text(json.dumps({"error": message, "detail": detail}), encoding="utf-8")
        return 0

    from domovoi.doc_extract import limits

    try:
        limits.apply(int(req.get("memory_bytes") or limits.worker_memory_bytes()),
                     int(req.get("cpu_seconds") or 60))
    except limits.LimitError as e:
        return fail("That document couldn't be converted.", type(e).__name__)
    from domovoi.richdoc import sanitize_html
    from domovoi.richdoc.word import html_to_docx

    try:
        markup = (d / "input.html").read_text(encoding="utf-8")
        data = html_to_docx(sanitize_html(markup), str(req.get("title") or "")[:200])
    except MemoryError:
        return fail("That document needs more memory to convert than Domovoi allows.")
    except Exception as e:  # noqa: BLE001 - a bad document is a failed conversion
        return fail("That document couldn't be converted to Word.", type(e).__name__)
    (d / "out.docx").write_bytes(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
