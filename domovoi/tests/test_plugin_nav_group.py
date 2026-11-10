"""A plugin page's sidebar group: ``[[web.pages]] nav_group`` (2026-11-09).

* optional; ``"media"`` or ``"tools"`` (manifest.NAV_GROUPS, the same ids
  as web/static/components.jsx NAV_GROUPS: test_web_nav_groups.py holds
  the two together); absent = a top-level row, as before;
* anything else refuses the load with a message that lists the choices;
* the web host sends it with the page in the frontend manifest, and a
  value it doesn't know (a row written by a newer core) is sent as None,
  never an error;
* the bundled radio plugin, the reference example, puts Stations in Media;
* the scaffold (``domovoi plugin new``) shows the key, commented out.
No DB.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from domovoi.plugins_runtime.cli import _SCAFFOLD_MANIFEST
from domovoi.plugins_runtime.manifest import NAV_GROUPS, ManifestError, parse_manifest
from web.backend import plugin_host

REPO_ROOT = Path(__file__).resolve().parents[2]


def _manifest(page_lines: str = "") -> str:
    return textwrap.dedent(
        """
        [plugin]
        slug = "groupy"
        name = "Groupy"
        version = "1.0.0"
        publisher = "tests"
        license = "MIT"
        description = "generated test plugin"
        domovoi_api = ">=1.0,<2.0"

        [entry_points]
        core = "domovoi_plugin_groupy.core"

        [web]
        scripts = ["web/static/groupy.jsx"]

        [[web.pages]]
        route = "groupy"
        page = "GroupyPage"
        nav_label = "Groupy"
        """
    ) + textwrap.dedent(page_lines)


def test_the_groups_a_page_may_join() -> None:
    assert NAV_GROUPS == ("media", "tools")


@pytest.mark.parametrize("group", ["media", "tools"])
def test_a_known_group_is_kept(group: str) -> None:
    (page,) = parse_manifest(_manifest(f'nav_group = "{group}"\n')).web_pages
    assert page.nav_group == group


def test_no_group_is_a_top_level_row() -> None:
    (page,) = parse_manifest(_manifest()).web_pages
    assert page.nav_group is None


@pytest.mark.parametrize("value", ['"listen"', '"Media"', '""', "3", "true", '["media"]'])
def test_anything_else_is_refused(value: str) -> None:
    with pytest.raises(ManifestError, match="nav_group must be one of 'media', 'tools'"):
        parse_manifest(_manifest(f"nav_group = {value}\n"))


def _host(page: dict) -> dict:
    host = plugin_host.PluginHost()
    host.rows = {"groupy": {
        "slug": "groupy", "name": "groupy", "version": "1.0.0", "publisher": "t", "license": "MIT",
        "enabled": True, "bundled": False, "install_source": "zip", "source_ref": None,
        "install_dir": "/nowhere/groupy", "status": "ok", "last_error": None,
        "installed_at": None, "updated_at": None,
        "manifest": {"web": {"scripts": ["web/static/groupy.jsx"],
                             "pages": [{"route": "groupy", "page": "GroupyPage",
                                        "nav_label": "Groupy", **page}]}},
    }}
    (entry,) = host.frontend_manifest()["plugins"]
    (pg,) = entry["pages"]
    return pg


def test_the_dashboard_gets_the_group() -> None:
    assert _host({"nav_group": "media"})["nav_group"] == "media"
    assert _host({"nav_group": "tools"})["nav_group"] == "tools"
    assert _host({})["nav_group"] is None


def test_a_group_this_dashboard_does_not_know_is_top_level() -> None:
    assert _host({"nav_group": "games"})["nav_group"] is None
    assert _host({"nav_group": ["media"]})["nav_group"] is None


def test_the_radio_plugin_puts_stations_in_media() -> None:
    text = (REPO_ROOT / "plugins" / "radio" / "domovoi-plugin.toml").read_text(encoding="utf-8")
    (page,) = parse_manifest(text).web_pages
    assert page.route == "stations" and page.nav_group == "media"


def test_the_scaffold_and_the_docs_show_the_key() -> None:
    assert '# nav_group = "tools"' in _SCAFFOLD_MANIFEST
    doc = (REPO_ROOT / "docs" / "PLUGIN_DEVELOPMENT.md").read_text(encoding="utf-8")
    assert "* `nav_group` — optional" in doc
