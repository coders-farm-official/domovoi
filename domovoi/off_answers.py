"""How an "off" answer says the feature can be turned on (owner, Q2).

When Domovoi says no because the household switched something off (AI,
the internet, Friend Houses, drop-in, …), the answer ends with where it
is switched back on. One sentence per feature, kept here so every reply
says the same words and the place is named once:

    >>> turn_on_line("ai")
    'An admin can turn it on in Settings → Chat & AI → Models.'

The places are the dashboard's own breadcrumbs (``SETTINGS_GROUPS`` and
``SETTINGS_SECTIONS`` in ``web/static/settings.jsx``: group, then section;
an Advanced field adds the heading it sits under there). A test checks
each against that file, so a moved section fails here first.

Who hears it: today every turn gets the member wording, "an admin can
…": no turn knows yet whether the asker is a proven admin (a dashboard
chat with a live admin session, a phone with admin sign-in; voice never
is). When it does, the admin wording (the exact steps) belongs in
:func:`turn_on_line` too, behind one argument, so no caller changes.

Stdlib only: ``domovoi.egress`` and ``domovoi.ai_features`` import it, and
both load in the web process and in plugins.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OffFeature:
    #: The dashboard path, "Settings → <group> → <section>[ → <heading>]".
    place: str
    #: What the admin does there: "turn it on" for a switch, "change that"
    #: for a choice (the internet answer has three).
    action: str = "turn it on"


AI = "ai"
INTERNET = "internet"
FRIEND_HOUSES = "friend_houses"
DROP_IN = "drop_in"
LYRICS_SEARCH = "lyrics_search"

FEATURES: dict[str, OffFeature] = {
    # ai_features ("AI chat and questions"), its own card on Models.
    AI: OffFeature("Settings → Chat & AI → Models"),
    # INTERNET_ACCESS: always / sometimes / never.
    INTERNET: OffFeature("Settings → Privacy → Internet", "change that"),
    # friend_houses_enabled, the switch at the top of its section.
    FRIEND_HOUSES: OffFeature("Settings → Household → Friend Houses"),
    # dropin_enabled ("Enable drop-in"), config group "Drop-in".
    DROP_IN: OffFeature("Settings → System → Advanced → Drop-in"),
    # lyrics_search_enabled ("Find songs by their words"), group "Library".
    LYRICS_SEARCH: OffFeature("Settings → System → Advanced → Library"),
}


def turn_on_line(feature: str) -> str:
    """The sentence an "off" answer ends with: who can turn ``feature``
    back on, and where. ``feature`` is one of :data:`FEATURES`' keys."""
    f = FEATURES[feature]
    return f"An admin can {f.action} in {f.place}."


def with_turn_on(text: str, feature: str) -> str:
    """``text`` (a complete sentence) followed by :func:`turn_on_line`."""
    return f"{text.rstrip()} {turn_on_line(feature)}"


__all__ = [
    "AI", "DROP_IN", "FEATURES", "FRIEND_HOUSES", "INTERNET", "LYRICS_SEARCH",
    "OffFeature", "turn_on_line", "with_turn_on",
]
