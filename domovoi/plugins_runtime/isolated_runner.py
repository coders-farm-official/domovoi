"""The runner inside a plugin's decode-only container (core 1.5.0).

The core copies this file into every decode-only image
(:mod:`domovoi.plugins_runtime.isolated`) as
``/opt/domovoi-isolated/domovoi_isolated_runner.py``, next to
``decoder.json`` (the plugin's ``[isolated] decoder``, written by the core),
the core's image helper and the plugin's decoder package. It uses the
standard library only (the image helper imports Pillow when it is used).

It reads requests from stdin and writes one reply per request to stdout,
each a frame: a 4-byte big-endian length, then the body.

* request body: ``id u32 ‖ kind u8 ‖ meta_len u16 ‖ meta (JSON object) ‖
  payload``. Kind 0 asks the plugin's decoder; kind 1 is an image request
  the core makes itself, answered by the core's helper
  (``domovoi_isolated.decode_pixels``), never by the plugin's code;
* reply body: ``id u32 ‖ status u8 ‖ data``: status 0 with the decoder's
  answer as JSON, status 1 with a short reason (ASCII), or status 2 (an
  image request only) with ``width u16 ‖ height u16 ‖ channels u8 ‖
  pixels``: raw 8-bit RGB or RGBA, never an encoded file. The core
  encodes the PNG itself.

The decoder is ``function(payload: bytes, meta: dict) -> value``, a JSON
value. What it prints goes to stderr, which the core sends nowhere, never
into the reply stream. The core checks every reply itself: nothing here
is trusted.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import struct
import sys

# Kept equal to the core's (isolated.py): a request or reply past these is
# never sent or accepted.
REQUEST_MAX = 256 * 1024 + 1024 + 7
REPLY_MAX = 64 * 1024
REASON_MAX = 120

KIND_DECODE, KIND_IMAGE = 0, 1
OK, REFUSED, PIXELS = 0, 1, 2

_UNSAFE = re.compile(r"[^A-Za-z0-9 .,:;_()'/-]")


def _read_exactly(stream, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _write(out, rid: int, status: int, data: bytes) -> None:
    body = struct.pack(">IB", rid, status) + data
    out.write(struct.pack(">I", len(body)) + body)
    out.flush()


def _refuse(out, rid: int, reason: str) -> None:
    _write(out, rid, REFUSED, _UNSAFE.sub("?", reason)[:REASON_MAX].encode("ascii"))


def serve(decode, stdin, out) -> None:
    """Answer requests until stdin closes."""
    while True:
        head = _read_exactly(stdin, 4)
        if head is None:
            return
        (size,) = struct.unpack(">I", head)
        if size < 7 or size > REQUEST_MAX:
            return  # the core never sends this: stop, and it starts a new runner
        body = _read_exactly(stdin, size)
        if body is None:
            return
        rid, kind, meta_len = struct.unpack(">IBH", body[:7])
        try:
            meta = json.loads(body[7:7 + meta_len].decode("utf-8")) if meta_len else {}
            if not isinstance(meta, dict) or kind not in (KIND_DECODE, KIND_IMAGE):
                raise ValueError("bad request")
        except ValueError:
            _refuse(out, rid, "bad request")
            continue
        payload = body[7 + meta_len:]
        try:
            if kind == KIND_IMAGE:
                import domovoi_isolated as helper

                width, height, channels, pixels = helper.decode_pixels(payload)
                _write(out, rid, PIXELS, struct.pack(">HHB", width, height, channels) + pixels)
                continue
            value = decode(payload, meta)
            data = json.dumps(value, allow_nan=False, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
        except Exception as e:  # noqa: BLE001 — a refusal, never the end of the runner
            _refuse(out, rid, f"{type(e).__name__}: {e}")
            continue
        if len(data) > REPLY_MAX:
            _refuse(out, rid, "the decoded value is too large")
            continue
        _write(out, rid, OK, data)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "decoder.json"), encoding="utf-8") as f:
        spec = json.load(f)
    if here not in sys.path:
        sys.path.insert(0, here)
    # The reply stream is a private copy of fd 1; fd 1 itself now points at
    # stderr, so a print() in the decoder can't corrupt a reply.
    out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    decode = getattr(importlib.import_module(spec["module"]), spec["function"])
    serve(decode, sys.stdin.buffer, out)


if __name__ == "__main__":
    main()
