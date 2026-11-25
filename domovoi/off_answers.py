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

Who hears which: a turn's asker is a member unless the server proved
otherwise (``Context.proven_admin``, set only from a live admin sign-in:
a dashboard chat, the dashboard's play box; voice never is). A member
hears "An admin can …"; a proven admin hears the exact steps (``admin=True``):

    >>> turn_on_line("ai", admin=True)
    'Turn it on in Settings → Chat & AI → Models: press turn on on the AI chat and questions card.'

Words only: which sentence is said never decides whether anything may be
done. Every switch named here keeps its own admin gate on its endpoint.

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
    #: The steps once there, for a proven admin: the controls' own words
    #: on the dashboard ("press turn on on the … card").
    steps: str = ""


AI = "ai"
INTERNET = "internet"
FRIEND_HOUSES = "friend_houses"
DROP_IN = "drop_in"
LYRICS_SEARCH = "lyrics_search"

# The steps quote the dashboard's own labels: AiFeaturesCard (models.jsx),
# the internet card's answers and Save (settings.jsx INTERNET_ANSWER_LABEL),
# FhOffCard's "Turn on" (friend_houses.jsx), and an Advanced field's label
# (config_schema.py) behind the folded "show the settings". A test checks
# each label against those files.
FEATURES: dict[str, OffFeature] = {
    # ai_features ("AI chat and questions"), its own card on Models.
    AI: OffFeature("Settings → Chat & AI → Models",
                   steps="press turn on on the AI chat and questions card"),
    # INTERNET_ACCESS: always / sometimes / never.
    INTERNET: OffFeature("Settings → Privacy → Internet", "change that",
                         steps="pick Yes or Sometimes, then press Save"),
    # friend_houses_enabled, the switch at the top of its section.
    FRIEND_HOUSES: OffFeature("Settings → Household → Friend Houses",
                              steps="press Turn on"),
    # dropin_enabled ("Enable drop-in"), config group "Drop-in".
    DROP_IN: OffFeature("Settings → System → Advanced → Drop-in",
                        steps="press show the settings, switch on Enable drop-in, then press Save"),
    # lyrics_search_enabled ("Find songs by their words"), group "Library".
    LYRICS_SEARCH: OffFeature("Settings → System → Advanced → Library",
                              steps="press show the settings, switch on Find songs by their words, "
                                    "then press Save"),
}


def turn_on_line(feature: str, *, admin: bool = False) -> str:
    """The sentence an "off" answer ends with: who can turn ``feature``
    back on, and where. ``feature`` is one of :data:`FEATURES`' keys.

    ``admin`` is the turn's ``Context.proven_admin`` (or, in a dashboard
    chat, the same server check on the request): True gives the steps
    themselves. Never pass anything a client said about itself."""
    f = FEATURES[feature]
    if admin and f.steps:
        return f"{f.action[0].upper()}{f.action[1:]} in {f.place}: {f.steps}."
    return f"An admin can {f.action} in {f.place}."


def with_turn_on(text: str, feature: str, *, admin: bool = False) -> str:
    """``text`` (a complete sentence) followed by :func:`turn_on_line`."""
    return f"{text.rstrip()} {turn_on_line(feature, admin=admin)}"


__all__ = [
    "AI", "DROP_IN", "FEATURES", "FRIEND_HOUSES", "INTERNET", "LYRICS_SEARCH",
    "OffFeature", "turn_on_line", "with_turn_on",
]
