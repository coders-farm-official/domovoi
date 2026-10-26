"""A tiny Yjs client for the co-editing tests (not a test module).

Speaks the y-websocket protocol the way the stock browser provider does,
over pycrdt: answers the server's sync step 1, applies step 2 and updates,
remembers presence and control messages, and sends its own edits as
updates. ``feed()`` takes a server frame and returns the frames the client
answers with.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pycrdt import (
    Doc,
    Text,
    XmlElement,
    XmlFragment,
    XmlText,
    create_sync_message,
    create_update_message,
    write_var_uint,
)

from web.backend.collab.protocol import (
    MSG_AWARENESS,
    MSG_CONTROL,
    MSG_SYNC,
    SYNC_STEP1,
    SYNC_STEP2,
    SYNC_UPDATE,
    AwarenessEntry,
    awareness_message,
    encode_awareness_update,
    parse_awareness_update,
    read_var_bytes,
    read_var_uint,
    var_string,
)
from pycrdt import handle_sync_message


def control(payload: dict[str, Any]) -> bytes:
    return write_var_uint(MSG_CONTROL) + var_string(json.dumps(payload))


class SimClient:
    def __init__(self, client_id: Optional[int] = None) -> None:
        self.doc = Doc(client_id=client_id) if client_id is not None else Doc()
        self.controls: list[dict[str, Any]] = []
        self.presence: dict[int, str] = {}
        self.synced = False
        self.updates_seen = 0

    # ── what the client sends ──
    def hello_frames(self) -> list[bytes]:
        return [create_sync_message(self.doc)]

    def edit_text(self, index: int, value: str, root: str = "text") -> bytes:
        sv = self.doc.get_state()
        self.doc.get(root, type=Text).insert(index, value)
        return create_update_message(self.doc.get_update(sv))

    def append_paragraph(self, value: str) -> bytes:
        sv = self.doc.get_state()
        frag = self.doc.get("default", type=XmlFragment)
        p = frag.children.append(XmlElement("paragraph"))
        t = p.children.append(XmlText())
        t.insert(0, value)
        return create_update_message(self.doc.get_update(sv))

    def presence_frame(self, state: dict[str, Any] | None, clock: int = 1) -> bytes:
        entry = AwarenessEntry(self.doc.client_id, clock,
                               "null" if state is None else json.dumps(state))
        return awareness_message(encode_awareness_update([entry]))

    def text(self, root: str = "text") -> str:
        return str(self.doc.get(root, type=Text))

    def xml(self) -> str:
        return str(self.doc.get("default", type=XmlFragment))

    def last_control(self, t: str) -> Optional[dict[str, Any]]:
        for c in reversed(self.controls):
            if c.get("t") == t:
                return c
        return None

    # ── what the server sends ──
    def feed(self, frame: bytes) -> list[bytes]:
        kind, pos = read_var_uint(frame, 0)
        out: list[bytes] = []
        if kind == MSG_SYNC:
            step, _ = read_var_uint(frame, pos)
            reply = handle_sync_message(frame[pos:], self.doc)
            if reply is not None:
                out.append(reply)
            if step == SYNC_STEP2:
                self.synced = True
            if step == SYNC_UPDATE:
                self.updates_seen += 1
        elif kind == MSG_AWARENESS:
            payload, _ = read_var_bytes(frame, pos)
            for e in parse_awareness_update(payload):
                if e.state == "null":
                    self.presence.pop(e.client_id, None)
                else:
                    self.presence[e.client_id] = e.state
        elif kind == MSG_CONTROL:
            raw, _ = read_var_bytes(frame, pos)
            self.controls.append(json.loads(raw.decode("utf-8")))
        return out
