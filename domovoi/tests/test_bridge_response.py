"""The web→core response bridge.

Lists used to be stringified into ``{"detail": "[]"}`` because only dicts
passed through. That is silent in the worst way: the endpoint returns 200,
the dashboard's list hook gets an object instead of an array, and the page
renders nothing at all — which looks exactly like "there is nothing to
show". Caught on a live box, with an empty approvals list.

No DB and no network, so these run everywhere.
"""

from __future__ import annotations

import json

from web.backend.domovoi_client import bridge_response


def _body(payload, status=200):
    r = bridge_response(status, payload)
    return r.status_code, json.loads(r.body.decode())


def test_an_empty_list_stays_a_list():
    assert _body([]) == (200, [])


def test_a_populated_list_survives_intact():
    rows = [{"room_id": "kitchen", "code": "4821"}]
    assert _body(rows) == (200, rows)


def test_dicts_still_pass_through():
    assert _body({"ok": True}) == (200, {"ok": True})


def test_text_bodies_are_still_wrapped():
    """The str() fallback exists for error/text bodies — a core that
    answers with plain text must not break the caller."""
    assert _body("upstream exploded") == (200, {"detail": "upstream exploded"})


def test_none_is_still_wrapped():
    assert _body(None) == (200, {"detail": "None"})


def test_status_codes_pass_through_verbatim():
    """404 when a room isn't connected, 409 when nothing is pending — the
    web caller must see the core's own answer."""
    assert _body({"detail": "no"}, status=409)[0] == 409


def test_connection_failure_becomes_a_gateway_error():
    status, body = _body(None, status=0)
    assert status == 502
    assert "unreachable" in body["detail"]
