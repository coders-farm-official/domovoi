"""A .env value never carries a control character (review of 2026-10-13).

A Configuration value with a newline (``bot_name`` has no pattern) was
written double-quoted: one value to the core's python-dotenv, but every
line-based reader saw the text after the newline as a setting of its own:
``WAKE_TRAINER_IMAGE=domovoi-mpd:latest``, which root's
scripts/linux/apply-update.sh reads to name the image it rebuilds. The
dashboard now refuses such a value, the writer refuses it whatever the key,
and ``python -m domovoi.config_env_writer --get KEY`` lets the shell
scripts read a value the way the core does.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from domovoi import config_env_writer as w
from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate

SMUGGLED = "Domo\nWAKE_TRAINER_IMAGE=domovoi-mpd:latest"


def test_configuration_refuses_a_value_with_a_newline():
    with pytest.raises(ValueError, match="control character"):
        coerce_and_validate(FIELD_BY_NAME["bot_name"], SMUGGLED)
    for bad in ("a\rb", "a\x00b", "a\tb", "a\x1bb"):
        with pytest.raises(ValueError):
            coerce_and_validate(FIELD_BY_NAME["bot_name"], bad)
    assert coerce_and_validate(FIELD_BY_NAME["bot_name"], "Domo, the cat") == "Domo, the cat"


@pytest.mark.parametrize("key", ["bot_name", "wake_word_train_command", "internet_access"])
def test_the_writer_refuses_it_for_every_key_and_writes_nothing(tmp_path, key):
    env = tmp_path / ".env"
    env.write_text("BOT_NAME=Domo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="control character"):
        w.write_env_values({key: SMUGGLED}, env)
    assert env.read_text(encoding="utf-8") == "BOT_NAME=Domo\n"
    assert "WAKE_TRAINER_IMAGE" not in dotenv_values(env)


def test_get_reads_a_value_the_way_the_core_does(tmp_path):
    env = tmp_path / ".env"
    # What an older writer could leave: a quoted value spanning two lines.
    env.write_text('BOT_NAME="Domo\nWAKE_TRAINER_IMAGE=domovoi-mpd:latest"\n'
                   'MPD_IMAGE_TAG=domovoi-mpd:latest\nMPD_IMAGE_TAG="x y"\n', encoding="utf-8")
    assert w.get_value("WAKE_TRAINER_IMAGE", env) is None
    assert w.get_value("bot_name", env) == SMUGGLED
    assert w.get_value("MPD_IMAGE_TAG", env) == "x y"      # the last line wins


def test_get_cli(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('WAKE_TRAINER_IMAGE="domovoi-wake-trainer:latest"\n', encoding="utf-8")
    code = (
        "import sys; from pathlib import Path; import domovoi.config_env_writer as w; "
        f"w._ENV_FILE = Path({str(env)!r}); sys.exit(w.main(sys.argv[1:]))"
    )
    run = lambda *a: subprocess.run([sys.executable, "-c", code, *a],  # noqa: E731
                                    capture_output=True, text=True,
                                    cwd=str(Path(__file__).resolve().parents[2]))
    r = run("--get", "WAKE_TRAINER_IMAGE")
    assert r.returncode == 0 and r.stdout.strip() == "domovoi-wake-trainer:latest"
    r = run("--get", "NOT_SET")
    assert r.returncode == 0 and r.stdout == ""
    assert run("--bogus").returncode == 2
