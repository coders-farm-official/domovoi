"""Containers a plugin runs, as the container guard should see them (SDK 1.5).

A plugin that runs Docker containers declares each one twice, the way it
declares handlers and workers:

* in its manifest, a ``[[containers]]`` entry per container — ``id``,
  ``name``, ``image``, ``required``, ``label`` and, rarely, ``allow`` —
  which the install trust screen shows and the guard falls back to;
* in ``register(ctx)``, ``ctx.add_container(id, resolve)``, where
  ``resolve`` returns a :class:`ContainerExpectation` describing that
  container RIGHT NOW, as the plugin's own settings and state drive it:
  the name and image it last created it with, whether it should be
  running, and whether the plugin is busy recreating it.

The core's container guard (docs/CONTAINER_GUARD.md) calls ``resolve``
once per check, so it must be cheap: read settings and the plugin's own
state row, never call Docker. A container declared in the manifest with
no resolver is expected to run whenever the plugin is enabled.

A plugin written for an older core can check ``hasattr(ctx,
"add_container")`` and skip the call there; a core older than SDK 1.5
ignores the manifest table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Union

#: The container should exist and run. Every failure rule applies.
RUNNING = "running"
#: The plugin stopped it on purpose (a "stop the video server"). It may be
#: stopped or gone; if it runs, it must run the declared image.
STOPPED = "stopped"
#: The plugin does not manage this container right now (the household
#: points the plugin at a server it runs itself). Not watched at all.
UNMANAGED = "unmanaged"

CONTAINER_STATES = frozenset({RUNNING, STOPPED, UNMANAGED})


@dataclass(frozen=True)
class ContainerExpectation:
    """What a plugin expects of one of its containers right now.

    ``name`` / ``image``: what the plugin last created the container with
    (None: the manifest's value). Report the image the container was
    CREATED from, not a setting the household just changed — until the
    plugin recreates it, the old container runs the old image, and the
    guard reads a mismatch as tampering. ``busy``: the plugin is creating,
    pulling or recreating the container this minute; the guard waits
    (30 minutes) instead of reporting it missing or swapped. ``pulling``:
    while busy, the image the plugin is moving the container TO; past the
    30 minutes the guard keeps waiting as long as that image isn't on the
    host yet (a long pull), then judges. ``reason`` is shown on the
    guard's status while busy or unmanaged.
    """

    name: str | None = None
    image: str | None = None
    state: str = RUNNING
    busy: bool = False
    reason: str | None = None
    pulling: str | None = None

    def __post_init__(self) -> None:
        if self.state not in CONTAINER_STATES:
            raise ValueError(
                f"ContainerExpectation.state must be one of {sorted(CONTAINER_STATES)}, "
                f"got {self.state!r}"
            )

    @classmethod
    def unmanaged(cls, reason: str | None = None) -> "ContainerExpectation":
        return cls(state=UNMANAGED, reason=reason)


ContainerResolver = Callable[
    [], Union[ContainerExpectation, Awaitable[ContainerExpectation], None]
]
