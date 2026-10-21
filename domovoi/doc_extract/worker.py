"""The document-reading worker: ``python -m domovoi.doc_extract.worker DIR``.

Started by :mod:`domovoi.doc_extract.runner` in a short-lived process. It
caps itself first (:mod:`domovoi.doc_extract.limits`; it refuses to read
when it can't), reads ``DIR/request.json`` (``{"path", "kind"}``), reads
the file (:func:`domovoi.doc_extract.extract`), and writes
``DIR/result.json``: the extraction, or ``{"ok": false, "error":
sentence}``. It opens no network connection: nothing in the readers
follows a link, and an XML entity that would name one is refused.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    d = Path(argv[1])
    req = json.loads((d / "request.json").read_text(encoding="utf-8"))
    from domovoi.doc_extract import limits

    def done(result: dict) -> int:
        (d / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return 0

    try:
        limits.apply(int(req.get("memory_bytes") or limits.worker_memory_bytes()),
                     int(req.get("cpu_seconds") or 120))
    except limits.LimitError as e:
        return done({"ok": False, "error": "That document couldn't be read.", "detail": type(e).__name__})
    from domovoi.doc_extract import extract
    from domovoi.doc_extract.safe import Refused

    try:
        result = extract(Path(req["path"]), str(req["kind"]), single_pass=req.get("single_pass"))
        result["ok"] = True
    except Refused as e:
        result = {"ok": False, "error": str(e)}
    except MemoryError:
        result = {"ok": False, "error": "That document needs more memory to read than Domovoi allows."}
    except RecursionError:
        result = {"ok": False, "error": "That document is nested too deeply to read safely."}
    except Exception as e:  # noqa: BLE001 — a bad document is a failed read
        # Only the exception's kind for the log: its text can quote the
        # document (review round 1), and the server keeps no document text.
        result = {"ok": False, "error": "That document is damaged and couldn't be read.",
                  "detail": type(e).__name__}
    return done(result)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
