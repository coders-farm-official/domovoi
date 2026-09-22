"""How a wake-word phrase reaches the operator's training command
(CORE-10).

``wake_word_train_command`` is a TEMPLATE the operator writes — the
README suggests a ``docker run …`` or ``wsl …`` wrapper — and the phrase
is substituted into it. The invariant this module holds is narrow and
load-bearing: **a substituted value is exactly one element of argv**, and
before that, a phrase is words someone could say out loud.

DB-free: ``build_train_argv`` and ``phrase_is_speakable`` are pure, and
the route check is a pydantic model. The queue-draining branches of
``tick()`` are covered against a real DB in test_wake_word_trainer.
"""

from __future__ import annotations

import pytest

from domovoi.models import WAKE_PHRASE_PATTERN
from domovoi.workers.wake_word_trainer import build_train_argv, phrase_is_speakable

TEMPLATE = (
    "docker run --rm -v {clips_dir}:/clips -v {out}:/out "
    "openwakeword train --phrase {phrase} --slug {slug}"
)


def _argv(phrase: str) -> list[str]:
    return build_train_argv(
        TEMPLATE,
        clips_dir="/home/k/.domovoi/wake_clips/hey_domovoi",
        phrase=phrase,
        slug="hey_domovoi",
        out="/home/k/.domovoi/wake_models/hey_domovoi.onnx",
    )


# ─── argv is a list, and a value is one element of it ─────────────────────


def test_a_plain_phrase_lands_as_one_argument():
    argv = _argv("hey domovoi")
    assert isinstance(argv, list)
    assert "hey domovoi" in argv
    assert argv[argv.index("--phrase") + 1] == "hey domovoi"


def test_a_phrase_with_quotes_and_spaces_is_still_one_argument():
    """The whole point. Whatever is in the phrase, the command receives
    the same number of arguments in the same order — the phrase cannot
    become several of them, and cannot land ahead of the image name."""
    baseline = _argv("hey domovoi")
    awkward = 'hey" -v /:/host --entrypoint sh "there'
    argv = _argv(awkward)

    assert len(argv) == len(baseline)
    assert argv[argv.index("--phrase") + 1] == awkward
    # Nothing new appeared as its own token.
    assert "-v" not in argv[argv.index("--phrase"):]
    assert "--entrypoint" not in argv
    assert "/:/host" not in argv
    # And the command still starts where it did.
    assert argv[:3] == baseline[:3] == ["docker", "run", "--rm"]


def test_every_placeholder_is_one_element():
    argv = _argv("hey domovoi")
    for value in (
        "/home/k/.domovoi/wake_clips/hey_domovoi",
        "hey domovoi",
        "hey_domovoi",
        "/home/k/.domovoi/wake_models/hey_domovoi.onnx",
    ):
        assert sum(1 for token in argv if value in token) >= 1


def test_a_path_with_spaces_stays_one_element():
    argv = build_train_argv(
        "trainer --clips {clips_dir}",
        clips_dir=r"C:\Users\Someone Else\.domovoi\wake_clips",
        phrase="x", slug="x", out="x",
    )
    assert argv[-1] == r"C:\Users\Someone Else\.domovoi\wake_clips"
    assert len(argv) == 3


def test_an_unknown_placeholder_is_an_error_not_a_silent_gap():
    with pytest.raises(KeyError):
        build_train_argv("trainer {nope}", clips_dir="a", phrase="b", slug="c", out="d")


def test_an_unparseable_template_raises():
    with pytest.raises(ValueError):
        build_train_argv("trainer 'unterminated", clips_dir="a", phrase="b",
                         slug="c", out="d")


# ─── what counts as a phrase ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase",
    ["hey domovoi", "Hey, Jarvis", "o'brien", "wake-up", "domovoi 2", "Okay Google"],
)
def test_speakable_phrases_pass(phrase):
    assert phrase_is_speakable(phrase) is True


@pytest.mark.parametrize(
    "phrase",
    [
        "",
        None,
        'hey" -v /:/host --entrypoint sh "x',
        "hey; rm -rf /",
        "hey $(whoami)",
        "hey `id`",
        "hey\nthere",
        "hey|tee /tmp/x",
        "hey&whoami",
        "hey>/tmp/out",
        "hey\\there",
    ],
)
def test_unspeakable_phrases_are_refused(phrase):
    assert phrase_is_speakable(phrase) is False


def test_the_route_and_the_worker_use_the_same_rule():
    """The web route bounds the phrase on the way in and the worker
    re-checks before it runs anything. Two checks, one rule."""
    from web.backend.api.wake_words import WakeWordCreate

    field = WakeWordCreate.model_fields["phrase"]
    patterns = [
        getattr(m, "pattern", None) for m in field.metadata
    ]
    assert WAKE_PHRASE_PATTERN in patterns


def test_the_route_refuses_a_phrase_the_trainer_would_refuse():
    import pydantic

    from web.backend.api.wake_words import WakeWordCreate

    WakeWordCreate(name="Hey Domovoi", phrase="hey domovoi")
    with pytest.raises(pydantic.ValidationError):
        WakeWordCreate(name="Hey Domovoi", phrase='hey" --entrypoint sh "x')
    with pytest.raises(pydantic.ValidationError):
        WakeWordCreate(name="Hey Domovoi", phrase="hey; rm -rf /")
