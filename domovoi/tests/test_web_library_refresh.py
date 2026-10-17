"""The dashboard's library pages follow changes made elsewhere (2026-10-17).

The owner uploaded videos from his phone and the Videos page kept its old
grid, with no way to look again short of leaving the page. Videos,
Pictures and Documents have no database index, so no NOTIFY ever said they
changed: the web now pushes ``files.changed`` (``listing_cache.changes``,
the ``files`` realtime channel) and the pages re-read on it.

Driven for real: ``web/static/data.js`` (``usePagedList``, ``stateBus``) and
each page file, inside ``jsx_interact_harness.js``, on the scripted server
of ``test_web_paged_lists``. Pinned, per page:

* Videos: a ``files.changed`` event re-reads the loaded grid quietly; the
  new "Rescan" button re-walks the folders (``refresh=true``);
* Files (Pictures and Documents are libraries there): ``files.changed``
  re-reads the folder on screen; its "Rescan" re-walks it;
* Audiobooks and Podcasts already follow ``podcasts.changed`` (their
  indexer and the feed poller notify) and keep their server rescans
  ("reindex", "poll now"); pinned so a refactor cannot drop them.

DB-free; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from domovoi.tests.test_web_paged_lists import BASE_FILES, HARNESS, PRELUDE, REPO_ROOT, SETUP

EMIT = r"""
  const bus = h.global('__bus');
  const emit = async (type) => { bus._notifyAll({ type, data: {} }); await step(); await step(); };
"""


def _scenario(page_files, component, script) -> dict:
    return {
        "files": BASE_FILES + page_files,
        "component": f"(globalThis.__bus = stateBus, {component})",
        "setup": SETUP,
        "refs": True,
        "script": PRELUDE + EMIT + script,
    }


SCENARIOS = {
    "videos": _scenario(["web/static/videos.jsx"], "VideosPage", r"""
      const before = reqs('/api/videos/list').length;
      await emit('files.changed');
      const afterEvent = reqs('/api/videos/list').slice(before);
      await emit('library.indexer.changed');
      const afterOther = reqs('/api/videos/list').length;
      const n = afterOther;
      await h.click({ type: 'button', text: 'Rescan' }); await step(); await step();
      return { before, afterEvent, afterOther, rescan: reqs('/api/videos/list').slice(n),
               footer: footer() };
    """),
    "files": _scenario(["web/static/files.jsx"], "FilesPage", r"""
      await step();
      const before = reqs('/api/files/browse').length;
      await emit('files.changed');
      const afterEvent = reqs('/api/files/browse').slice(before);
      const n = reqs('/api/files/browse').length;
      await h.click({ type: 'button', text: 'Rescan' }); await step(); await step();
      return { before, afterEvent, rescan: reqs('/api/files/browse').slice(n), footer: footer() };
    """),
    "audiobooks": _scenario(["web/static/audiobooks.jsx"], "AudiobooksPage", r"""
      const before = reqs('/api/audiobooks').length;
      await emit('podcasts.changed');
      const after = reqs('/api/audiobooks').length;
      const reindex = h.findAll({ type: 'button', text: 'reindex' }).length;
      return { before, after, reindex };
    """),
    "podcasts": _scenario(["web/static/podcasts.jsx"], "PodcastsPage", r"""
      const before = reqs('/api/podcasts/subscriptions').length;
      await emit('podcasts.changed');
      const after = reqs('/api/podcasts/subscriptions').length;
      const poll = h.findAll({ type: 'button', text: 'poll now' }).length;
      return { before, after, poll };
    """),
}


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    f = tmp_path_factory.mktemp("library-refresh") / "scenarios.json"
    f.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{f}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, res in out.items():
        assert "__harness_error" not in res, f"{name}: {res.get('__harness_error')}"
    return out


def test_videos_rereads_when_a_library_changed(outcomes) -> None:
    o = outcomes["videos"]
    assert o["before"] == 1
    # The loaded window again, from the top, without a cursor.
    assert len(o["afterEvent"]) == 1 and "cursor" not in o["afterEvent"][0]
    assert o["afterEvent"][0]["limit"] == "50"
    assert "refresh" not in o["afterEvent"][0]
    # Another library's event (music's index) is not a reason to re-read.
    assert o["afterOther"] == 2
    assert o["footer"] == ["showing 50 of 75 videos"]


def test_videos_rescan_rewalks_the_folders(outcomes) -> None:
    o = outcomes["videos"]
    assert len(o["rescan"]) == 1 and o["rescan"][0].get("refresh") == "true"


def test_files_rereads_the_folder_on_screen_when_a_library_changed(outcomes) -> None:
    o = outcomes["files"]
    assert o["before"] >= 1
    assert len(o["afterEvent"]) == 1 and "cursor" not in o["afterEvent"][0]
    assert o["afterEvent"][0]["path"] == ""
    assert len(o["rescan"]) == 1 and o["rescan"][0].get("refresh") == "true"
    assert o["footer"] == ["showing 50 of 90 items"]


def test_audiobooks_and_podcasts_keep_following_their_indexer(outcomes) -> None:
    a = outcomes["audiobooks"]
    assert a["after"] == a["before"] + 1 and a["reindex"] >= 1
    p = outcomes["podcasts"]
    assert p["after"] == p["before"] + 1 and p["poll"] >= 1
