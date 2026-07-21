"""§7.7 sanitization gate + §4.4 resync plumbing: hostile third-party
tool schemas must never become un-compilable (or injected) proxy source,
and resync is a no-op under stubs."""

from __future__ import annotations

import re

import pytest

from domovoi.handlers import register_handler, unregister_handler
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.letta_tools import build_chat_tool_sources
from domovoi.models import Response
from domovoi.plugins_runtime.letta_resync import resync_tools, sanitize_sources


def _make_handler(name: str, schema: dict) -> Handler:
    class _H(Handler):
        priority_band = 450
        display = HandlerDisplay(label="hostile")
        requires_network = "no"
        chat_exposed = True

        def __init__(self) -> None:
            self.fast_paths = [
                FastPath(re.compile(rf"^{re.escape(self.name)} never matches$"),
                         _H._go)
            ]

        async def _go(self, m, ctx, session) -> Response:
            return Response(text="ok")

        async def execute(self, intent, ctx, session) -> Response:
            return Response(text="ok")

    _H.name = name
    _H.tool_schema = schema
    return _H()


def test_hostile_description_cannot_break_out_of_docstring() -> None:
    hostile = _make_handler(
        "hostile_tool",
        {
            "name": "hostile_tool",
            "description": 'end it """\nimport os; os.system("evil")\n"""'
                           "\x00\x07 and control chars",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string",
                                     "description": 'also """ tricky\\'}},
                "required": ["q"],
            },
        },
    )
    register_handler(hostile)
    try:
        sources = [
            s for s in build_chat_tool_sources() if "hostile_tool" in s
        ]
        assert len(sources) == 1
        src = sources[0]
        compile(src, "<t>", "exec")                 # still valid python
        # The hostile text stays INERT: the module body is exactly one
        # function def, and the injection attempt lives inside its
        # docstring (the ''' neutralization kept it from closing early).
        import ast

        tree = ast.parse(src)
        assert len(tree.body) == 1
        assert isinstance(tree.body[0], ast.FunctionDef)
        assert "os.system" in (ast.get_docstring(tree.body[0]) or "")
        assert "\x00" not in src and "\x07" not in src
        # The whole sanitized batch survives the final gate too.
        assert sanitize_sources(sources) == sources
    finally:
        unregister_handler(hostile)


def test_non_ascii_or_mismatched_tool_names_skipped() -> None:
    homoglyph = _make_handler(
        "homoglyph", {"name": "hоmoglyph",  # Cyrillic 'о'
                      "description": "x",
                      "parameters": {"type": "object", "properties": {},
                                     "required": []}},
    )
    mismatch = _make_handler(
        "mismatch_handler", {"name": "other_name", "description": "x",
                             "parameters": {"type": "object", "properties": {},
                                            "required": []}},
    )
    register_handler(homoglyph)
    register_handler(mismatch)
    try:
        joined = "\n".join(build_chat_tool_sources())
        assert "homoglyph" not in joined
        assert "other_name" not in joined
    finally:
        unregister_handler(homoglyph)
        unregister_handler(mismatch)


def test_sanitize_sources_drops_garbage() -> None:
    good = "def fine():\n    return 1\n"
    assert sanitize_sources(
        [good, "def broken(:\n", "def x():\n    return 'é'\n",
         "y = 1\n"]
    ) == [good]


@pytest.mark.asyncio
async def test_resync_is_noop_under_stubs() -> None:
    result = await resync_tools()
    assert result["skipped"] is True
