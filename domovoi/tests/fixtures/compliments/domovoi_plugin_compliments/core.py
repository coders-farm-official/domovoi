"""Core entry point for the compliments fixture plugin (design §4.3.1's
minimal worked example, adapted as the installer-pipeline test fixture)."""

from __future__ import annotations

import re

from domovoi.sdk import FastPath, Handler, HandlerDisplay, Response, Worker

_NICE_RE = re.compile(r"^say something nice(?: about (?P<topic>.+))?$")

# Mutated by the test suite to prove the loader ran register().
REGISTER_CALLS: list[str] = []


class ComplimentsHandler(Handler):
    name = "compliments"
    priority_band = 400                      # general plugin space (§4.2)
    # band rationale: anchored phrase, nothing to poach — default home.
    display = HandlerDisplay(label="Compliments", tone="info")
    requires_network = "no"
    tool_schema = {
        "name": "compliments",
        "description": "Pay the user a compliment. Example: 'say something nice'.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "what to compliment"},
            },
            "required": [],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [FastPath(_NICE_RE, ComplimentsHandler._nice)]

    async def _nice(self, m, ctx, session) -> Response:
        topic = m.group("topic") or "you"
        return Response(text=f"{topic} — genuinely impressive today.")

    async def execute(self, intent, ctx, session) -> Response:
        return Response(text="You're doing great.")


class ComplimentTicker(Worker):
    name = "compliment_ticker"
    interval_setting = "media_plays_pruner_interval_sec"  # any core cadence field
    stub_suppressed = True

    def __init__(self) -> None:
        self.ticks = 0

    async def tick(self) -> None:
        self.ticks += 1


def register(ctx) -> None:
    REGISTER_CALLS.append(ctx.slug)
    ctx.add_handler(ComplimentsHandler())
    ctx.add_worker(ComplimentTicker())
