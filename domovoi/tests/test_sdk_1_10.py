"""SDK 1.10 (2026-11, the radio-by-voice batch): what plugins gained.

* ``domovoi.sdk.spoken_names`` / ``domovoi.sdk.choices`` — the core's
  spoken-name rules and its "did you mean …?" reply reader;
* ``Handler.choice_kinds`` for plugin handlers — contract-checked at load
  (a subset of ``confirmation_kinds``, ``handle_choice_reply`` overridden),
  and a plugin reader that raises costs its question, not the turn;
* ``sdk.playback.ensure_room`` — a room the house has, or 404;
* ``[[web.player_sources]].room_play_template`` — one of the plugin's own
  routes, with ``{id}``.

DB only for ensure_room."""

from __future__ import annotations

import pytest

from domovoi.handlers.base import Handler, HandlerDisplay
from domovoi.models import Response
from domovoi.plugins_runtime.contracts import ContractReport, check_handlers
from domovoi.tests.conftest import requires_db


def test_the_sdk_is_1_10_and_loads_the_new_modules_on_first_use() -> None:
    import domovoi.sdk as sdk

    assert tuple(int(p) for p in sdk.API_VERSION.split(".")) >= (1, 10, 0)
    assert sdk.spoken_names.same_name("3 Doors Down", "Three Doors Down")
    reply = sdk.choices.parse_choice_reply("the second one", 2)
    assert (reply.action, reply.index) == ("pick", 1)
    assert sdk.choices.parse_choice_reply("no", 1).action == "decline"
    assert isinstance(reply, sdk.choices.ChoiceReply)
    with pytest.raises(AttributeError):
        sdk.not_a_thing  # noqa: B018


class _Base(Handler):
    priority_band = 280
    display = HandlerDisplay(label="Demo", tone="media")
    fast_paths: list = []

    async def execute(self, intent, ctx, session):  # pragma: no cover
        return Response(text="")


def _handler(name: str, *, kinds=(), choices=(), reader=True) -> Handler:
    attrs = {"name": name, "tool_schema": {"name": name},
             "confirmation_kinds": kinds, "choice_kinds": choices}
    if reader:
        async def handle_choice_reply(self, kind, data, transcript, ctx, session):
            return None
        attrs["handle_choice_reply"] = handle_choice_reply
    return type(f"H_{name}", (_Base,), attrs)()


def test_a_plugin_choice_kind_must_be_a_confirmation_kind_with_a_reader() -> None:
    good = ContractReport()
    check_handlers("demo", [_handler("ok", kinds=("demo.pick",), choices=("demo.pick",))], good)
    assert good.errors == []

    stray = ContractReport()
    check_handlers("demo", [_handler("stray", kinds=("demo.a",), choices=("demo.b",))], stray)
    assert any("not in its confirmation_kinds" in e for e in stray.errors)

    unread = ContractReport()
    check_handlers("demo", [_handler("unread", kinds=("demo.a",), choices=("demo.a",), reader=False)], unread)
    assert any("does not override handle_choice_reply" in e for e in unread.errors)


@requires_db
async def test_ensure_room_knows_only_the_houses_rooms(db_session) -> None:
    from sqlalchemy import text

    from domovoi.sdk.playback import PlaybackAPI

    await db_session.execute(text(
        "INSERT INTO satellites (room_id) VALUES ('kitchen') ON CONFLICT DO NOTHING"))
    await db_session.commit()
    api = PlaybackAPI("demo")
    assert await api.ensure_room("kitchen") is True
    assert await api.ensure_room("attic") is False
    assert await api.ensure_room("  ") is False


def test_room_play_template_must_be_the_plugins_own(tmp_path) -> None:
    from domovoi.plugins_runtime import manifest as m

    assert m._room_play_template({}, "demo") is None
    ok = {"room_play_template": "/api/plugins/demo/items/{id}/play-in-room"}
    assert m._room_play_template(ok, "demo") == ok["room_play_template"]
    for bad in ("/api/plugins/other/{id}", "/api/plugins/demo/items", "https://x/{id}",
                "/api/plugins/demo/../music/{id}", "/api/plugins/demo/{id}?x=1"):
        with pytest.raises(m.ManifestError):
            m._room_play_template({"room_play_template": bad}, "demo")
