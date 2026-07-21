"""Sample line for auditioning a voice (the web Voices page play button).

A complete sentence that introduces the bot and shares a random fun fact —
long enough to actually judge how a voice sounds, and a little fun. Synthed
live on demand in the selected voice (see the /v1/admin/voices/sample
endpoint), so each click picks a fresh fact.
"""

from __future__ import annotations

import random

# Clean, TTS-friendly one-liners (no symbols/abbreviations that read badly).
FUN_FACTS: list[str] = [
    "honey never spoils — archaeologists have found pots of it in ancient "
    "Egyptian tombs, over three thousand years old and still perfectly edible.",
    "a group of flamingos is called a flamboyance.",
    "octopuses have three hearts, and two of them stop beating whenever the "
    "octopus swims.",
    "bananas are berries, but strawberries are not.",
    "the Eiffel Tower can grow more than six inches taller in summer, because "
    "the iron expands in the heat.",
    "a day on Venus is longer than a whole year on Venus.",
    "sea otters hold hands while they sleep so they don't drift apart.",
    "the shortest war in history lasted only about thirty-eight minutes.",
    "sharks have been around longer than trees have.",
    "there are more possible games of chess than there are atoms in the "
    "observable universe.",
    "a single bolt of lightning is about five times hotter than the surface "
    "of the sun.",
    "wombats produce cube-shaped droppings.",
    "the human nose can distinguish over a trillion different smells.",
    "hot water can freeze faster than cold water under the right conditions.",
]


def build_sample_text(voice_name: str, fact: str | None = None) -> str:
    """A full intro + fun-fact line spoken in the voice being auditioned.
    Introduces the voice by name (not the bot) since this is for picking a
    voice. Pass ``fact`` for a deterministic line (tests); otherwise one is
    picked at random."""
    chosen = fact if fact is not None else random.choice(FUN_FACTS)
    return (
        f"This is the voice of {voice_name}. "
        f"Here's a fun fact for you: {chosen}"
    )
