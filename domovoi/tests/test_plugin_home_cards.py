"""``[[web.home_cards]]`` (SDK 1.13): a plugin card on the dashboard's Home.

Validation in the manifest loader (which ``domovoi plugin pack`` runs too),
and the web host handing the cards to the browser. No DB.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from domovoi.plugins_runtime.cli import _validate_dir
from domovoi.plugins_runtime.manifest import ManifestError, parse_manifest
from web.backend.plugin_host import _home_cards_out

BASE = textwrap.dedent(
    """
    [plugin]
    slug = "cardy"
    name = "Cardy"
    version = "1.0.0"
    publisher = "tests"
    license = "MIT"
    description = "generated test plugin"
    domovoi_api = ">=1.13,<2.0"

    [entry_points]
    core = "domovoi_plugin_cardy.core"

    [web]
    scripts = ["web/static/cardy.jsx"]
    """
)


def _m(cards: str):
    return parse_manifest(BASE + textwrap.dedent(cards))


def test_a_card_parses_with_defaults() -> None:
    m = _m('''
        [[web.home_cards]]
        id = "today"
        title = "Today"
        component = "TodayCard"
    ''')
    (c,) = m.home_cards
    assert (c.id, c.title, c.component, c.default_on, c.order) == ("today", "Today", "TodayCard", True, 50)


def test_a_card_keeps_its_switch_and_order() -> None:
    m = _m('''
        [[web.home_cards]]
        id = "a"
        title = "A"
        component = "A"
        default_on = false
        order = 5
    ''')
    assert m.home_cards[0].default_on is False and m.home_cards[0].order == 5


def test_no_cards_is_the_default() -> None:
    assert parse_manifest(BASE).home_cards == ()


@pytest.mark.parametrize("body", [
    'title = "T"\ncomponent = "C"',                              # no id
    'id = "a"\ncomponent = "C"',                                 # no title
    'id = "a"\ntitle = "T"',                                     # no component
    'id = "Bad Id"\ntitle = "T"\ncomponent = "C"',               # id shape
    'id = "a"\ntitle = "T"\ncomponent = "not a name"',           # component shape
    'id = "a"\ntitle = "T"\ncomponent = "C"\ndefault_on = "yes"',
    'id = "a"\ntitle = "T"\ncomponent = "C"\norder = "1"',
    'id = "a"\ntitle = "T"\ncomponent = "C"\norder = true',
    'id = "a"\ntitle = "' + "x" * 61 + '"\ncomponent = "C"',
])
def test_a_bad_card_refuses_the_load(body: str) -> None:
    with pytest.raises(ManifestError, match="home_cards"):
        _m("[[web.home_cards]]\n" + body + "\n")


def test_a_duplicate_id_refuses_the_load() -> None:
    with pytest.raises(ManifestError, match="twice"):
        _m('''
            [[web.home_cards]]
            id = "a"
            title = "A"
            component = "A"
            [[web.home_cards]]
            id = "a"
            title = "B"
            component = "B"
        ''')


def test_pack_validation_refuses_a_bad_card(tmp_path: Path) -> None:
    (tmp_path / "domovoi-plugin.toml").write_text(
        BASE + '[[web.home_cards]]\nid = "A"\ntitle = "T"\ncomponent = "C"\n', encoding="utf-8")
    (tmp_path / "domovoi_plugin_cardy").mkdir()
    with pytest.raises((ManifestError, SystemExit)):
        _validate_dir(tmp_path)


def test_the_web_host_sends_cards_and_skips_malformed_rows() -> None:
    out = _home_cards_out([
        {"id": "a", "title": "A", "component": "CA", "default_on": False, "order": 3},
        {"id": "b", "title": "B", "component": "CB"},
        {"id": 7, "title": "bad", "component": "X"},
        "junk",
    ])
    assert out == [
        {"id": "a", "title": "A", "component": "CA", "default_on": False, "order": 3},
        {"id": "b", "title": "B", "component": "CB", "default_on": True, "order": 50},
    ]
    assert _home_cards_out(None) == []
