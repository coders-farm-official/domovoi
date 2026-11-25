"""A plugin installed or enabled while the dashboard is open adds its page and
nav item with no restart and no reload (1.4.1).

The web process mounts a new plugin live (plugin_host.resync) and the shell
used to answer a plugins.changed push with only a "reload" toast, so the nav
stayed as it was until the owner reloaded. The shell now refreshes the
manifest in place: scripts of plugins this tab has not run are loaded, the
nav re-reads the manifest, and only an upgrade of a plugin the tab already
runs still asks for a reload.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "web/static/index.html").read_text(encoding="utf-8")


def _between(start: str, end: str) -> str:
    i = HTML.index(start)
    return HTML[i:HTML.index(end, i)]


def test_a_plugins_changed_push_refreshes_the_nav_not_just_a_toast() -> None:
    app = _between("const [pluginsStale", "// Re-render Lucide icons")
    assert "refreshPlugins()" in app
    assert "setManifestTick" in app
    # The reload toast is the exception (an upgrade), not the answer.
    assert "if (needsReload) setPluginsStale(true);" in app


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_refresh_loads_a_new_plugin_and_flags_only_an_upgrade(tmp_path: Path) -> None:
    src = _between("const loadPluginScripts", "const bootstrap = async")
    harness = tmp_path / "h.js"
    harness.write_text(
        """
const calls = [];
const window = { DomovoiPluginErrors: { record() {} }, DomovoiPluginRefreshRetryMs: [1, 1] };
const ServerStore = { isTrusted: () => true, current: () => '' };
const API_BASE = '';
const Babel = { transform: (c) => ({ code: c }) };
let manifest = { plugins: [] };
let down = 0;
const apiGet = async () => { if (down > 0) { down--; throw new Error('restarting'); } return JSON.parse(JSON.stringify(manifest)); };
globalThis.fetch = async (u) => { calls.push(u); return { ok: true, text: async () => 'window.ran.push(' + JSON.stringify(u) + ');' }; };
window.ran = [];
globalThis.window = window;
""" + src + """
(async () => {
  const mk = (slug, version) => ({ slug, version, scripts: ['/plugins/' + slug + '/static/p.js'],
                                   pages: [{ route: slug, page: 'P', nav_label: slug }] });
  const out = {};
  manifest = { plugins: [mk('alpha', '1.0.0')] };
  out.first = await refreshPlugins();
  // A second plugin installed while the tab is open.
  manifest = { plugins: [mk('alpha', '1.0.0'), mk('beta', '1.0.0')] };
  out.install = await refreshPlugins();
  out.nav = window.DomovoiPluginManifest.plugins.map((p) => p.slug);
  out.ran = window.ran.slice();
  // Alpha upgraded: its running code is old, so a reload is asked for.
  manifest = { plugins: [mk('alpha', '1.1.0'), mk('beta', '1.0.0')] };
  out.upgrade = await refreshPlugins();
  // A restart: same plugins. The read fails while the server is down (and
  // is retried), or answers the very same set. Neither is news.
  manifest = { plugins: [mk('alpha', '1.0.0'), mk('beta', '1.0.0')] };
  down = 1;
  out.restartRetried = await refreshPlugins();
  down = 99;
  out.restartDown = await refreshPlugins();
  down = 0;
  out.same = await refreshPlugins();
  console.log(JSON.stringify(out));
})();
""",
        encoding="utf-8",
    )
    r = subprocess.run(["node", str(harness)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["install"] is False            # no reload, no restart
    assert out["nav"] == ["alpha", "beta"]    # the new item is in the nav data
    assert out["ran"] == ["/plugins/alpha/static/p.js", "/plugins/beta/static/p.js"]
    assert out["upgrade"] is True
    assert out["restartRetried"] is False     # a read that recovered, same set
    assert out["restartDown"] is False        # a server still down: quiet
    assert out["same"] is False               # identical set: no toast
