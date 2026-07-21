"""Voice sample line (Voices page play button)."""

from __future__ import annotations

from domovoi.voice_sample import FUN_FACTS, build_sample_text


def test_build_sample_text_includes_voice_name_and_fact():
    text = build_sample_text("Aria", fact="cats have five toes on their front paws.")
    assert text.startswith("This is the voice of Aria.")
    assert "cats have five toes" in text
    assert text.endswith("front paws.")


def test_build_sample_text_random_fact_is_from_bank():
    # The random path always pulls a known fact and names the voice.
    text = build_sample_text("Ryan")
    assert "This is the voice of Ryan." in text
    assert any(fact in text for fact in FUN_FACTS)


def test_facts_are_clean_for_tts():
    # No empty facts; each ends with sentence punctuation so the line reads
    # naturally aloud.
    for fact in FUN_FACTS:
        assert fact.strip()
        assert fact.rstrip()[-1] in ".!?"
