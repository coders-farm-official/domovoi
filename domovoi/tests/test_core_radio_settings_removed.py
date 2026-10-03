"""The dead core ``radio_*`` settings are gone (fix B7).

The radio feature moved into the bundled radio plugin, whose settings
live in ``~/.domovoi/plugins/radio.env`` (``RADIO_*``). The core kept a
copy of every one of them that nothing read, and the dashboard showed
three of them ("Radio sampler worker", "Default sample interval",
"Re-download cooldown") as switches that did nothing. Existing ``.env``
files with ``RADIO_*`` lines keep working: Settings ignores unknown keys.

DB-free.
"""

from __future__ import annotations

import re
from pathlib import Path

from domovoi.config import Settings
from domovoi.config_schema import EDITABLE_FIELDS

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def test_settings_has_no_radio_fields() -> None:
    assert [f for f in Settings.model_fields if f.startswith("radio_")] == []


def test_no_radio_fieldspecs_and_no_radio_group() -> None:
    assert [s.name for s in EDITABLE_FIELDS if s.name.startswith("radio_")] == []
    assert [s.name for s in EDITABLE_FIELDS if s.group == "Radio"] == []


def test_workers_group_has_no_radio_row() -> None:
    workers = [s for s in EDITABLE_FIELDS if s.group == "Workers"]
    assert workers, "the Workers group itself stays"
    assert not any("radio" in (s.name + s.label).lower() for s in workers)


def test_env_example_has_no_radio_keys_and_points_at_the_plugin() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    keys = re.findall(r"^\s*#?\s*(RADIO_[A-Z0-9_]+)\s*=", text, flags=re.MULTILINE)
    assert keys == []
    assert "~/.domovoi/plugins/radio.env" in text


def test_an_old_env_with_radio_lines_still_loads(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("RADIO_SAMPLER_ENABLED=false\nRADIO_MARKET_STATE=CO\n", encoding="utf-8")
    s = Settings(_env_file=str(env))
    assert not hasattr(s, "radio_sampler_enabled")
