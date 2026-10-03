"""The track drawer's "also called" lists and the Stats tab's names card
(web/static/music.jsx), driven through domovoi/tests/jsx_interact_harness.js.

Owner decision 2026-10-02: a band can have MANY names, so the drawer's
field is an editable LIST per target — the song, each of its artists, its
album — of chips (an x on the ones this caller may remove, MusicBrainz ones
muted) plus an "add a name…" input, never a single text box. A name means
one thing household-wide: a 409 asks "Replace it?" when the caller may
(then POSTs again with ``replace: true``), else says only an admin can; a
name that now plays something instead of a library name says so.

DB-free (the data layer is scripted by the harness). Names are synthetic
(checked clear of the spoken-match gate).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/music.jsx"]

TRACK = {"id": 6, "title": "Warm Stones", "artist": "Hearth Ensemble", "album": "By The Hearth",
         "duration_sec": 200, "file_path": "/music/hearth_06.mp3", "added_at": "2026-09-01T00:00:00Z",
         "added_via": "manual", "favorited": False, "source": None, "source_id": None, "enriched_at": None}


def _alias(i, alias, *, target_type="artist", target_name="Hearth Ensemble", source="manual",
           can_remove=True, added_by="Kitchen tablet"):
    return {"id": i, "alias": alias, "alias_key": alias.replace(" ", ""), "target_type": target_type,
            "target_key": None if target_type == "track" else target_name.lower().replace(" ", ""),
            "target_name": target_name, "target_artist_key": None,
            "target_track_id": 6 if target_type == "track" else None, "source": source,
            "created_by_kind": "system" if source == "musicbrainz" else "device", "added_by": added_by,
            "created_at": "2026-10-03T00:00:00Z", "suppressed": False, "can_remove": can_remove}


VIEW = {
    "track": {"id": 6, "title": "Warm Stones",
              "aliases": [_alias(11, "the warm one", target_type="track", target_name="Warm Stones")]},
    "artists": [{"name": "Hearth Ensemble", "key": "hearthensemble", "credit": False, "aliases": [
        _alias(12, "gramps", source="voice", can_remove=False, added_by="voice in den"),
        _alias(13, "hearth band", source="musicbrainz", added_by="MusicBrainz"),
    ]}],
    "album": {"name": "By The Hearth", "key": "bythehearth", "artist_key": "hearthensemble",
              "artist_name": "Hearth Ensemble", "aliases": []},
}
FOR_TRACK = "GET /api/music/aliases/for-track/6"
NEW = _alias(21, "the kindlers")
DRAWER = {"files": FILES, "component": "Drawer", "props": {"track": TRACK, "rooms": ["office"]},
          "fnProps": ["onClose", "onDelete", "onPlayInRoom", "onBrowserPlay", "onQueueTrack", "fire"]}

OPEN = "h.render(); await h.settle(); h.rerender();"
CHIPS = r"""
  const chips = () => h.findAll((el) => el.type === 'span' && el.props && typeof el.props.title === 'string'
      && (el.props.title === 'from MusicBrainz' || el.props.title.indexOf('added by ') === 0))
    .map((el) => ({ text: el.text, title: el.props.title, background: el.props.style.background }));
  const removable = () => h.findAll((el) => el.type === 'button' && el.props && typeof el.props.title === 'string'
      && el.props.title.indexOf('remove ') === 0).map((el) => el.props.title);
  const toasts = () => h.fnCalls.filter((c) => c.name === 'fire').map((c) => c.args[0]);
"""

TAKEN_REPLACEABLE = {"__error": {"status": 409, "message": "409 Conflict", "detail": {"detail": {
    "code": "alias_taken", "can_replace": True, "message": "'gramps' already means Ember Choir.",
    "existing": _alias(31, "gramps", target_name="Ember Choir")}}}}
TAKEN_NOT_YOURS = {"__error": {"status": 409, "message": "409 Conflict", "detail": {"detail": {
    "code": "alias_taken", "can_replace": False, "message": "'gramps' already means Ember Choir.",
    "existing": _alias(31, "gramps", target_name="Ember Choir", can_remove=False)}}}}
# The harness answers every POST to one path alike; a replace (replace:
# true) gets the moved row instead of the 409 — the call is still recorded.
REPLACE_SETUP = r"""
  const _post = apiPost;
  apiPost = (p, body) => {
    const r = _post(p, body);
    if (!(body && body.replace)) return r;
    return r.catch(() => ({ status: 'replaced', shadows: [],
      alias: Object.assign({}, %s, { target_name: 'Hearth Ensemble', target_key: 'hearthensemble' }) }));
  };
""" % json.dumps(_alias(31, "gramps", target_name="Ember Choir"))

SCENARIOS = {
    "lists": {**DRAWER, "api": {FOR_TRACK: VIEW}, "script": OPEN + CHIPS + r"""
      const inputs = h.findAll({ placeholder: 'add a name…' }).map((el) => h.plain(el).props);
      return { texts: h.text(), chips: chips(), removable: removable(), inputs,
               fetched: h.calls.filter((c) => c.method === 'GET').map((c) => c.path) };
    """},
    "add": {**DRAWER, "api": {FOR_TRACK: VIEW, "POST /api/music/aliases": {"status": "added", "alias": NEW, "shadows": []}},
            "script": OPEN + CHIPS + r"""
      await h.type({ name: 'also-called-artist:hearthensemble' }, '  the kindlers ');
      await h.key({ name: 'also-called-artist:hearthensemble' }, 'Enter');
      return { posts: h.calls.filter((c) => c.method === 'POST'), chips: chips(), toasts: toasts(),
               draft: h.plain(h.find({ name: 'also-called-artist:hearthensemble' })).props.value };
    """},
    "add_album": {**DRAWER, "api": {FOR_TRACK: VIEW, "POST /api/music/aliases": {
                      "status": "added", "alias": _alias(22, "hearth record", target_type="album",
                                                         target_name="By The Hearth"), "shadows": []}},
                  "script": OPEN + r"""
      await h.type({ name: 'also-called-album' }, 'hearth record');
      await h.key({ name: 'also-called-album' }, 'Enter');
      await h.type({ name: 'also-called-song' }, 'warmies');
      await h.key({ name: 'also-called-song' }, 'Enter');
      return { posts: h.calls.filter((c) => c.method === 'POST').map((c) => c.body) };
    """},
    "shadow": {**DRAWER, "api": {FOR_TRACK: VIEW, "POST /api/music/aliases": {
                   "status": "added", "alias": _alias(23, "kindling"),
                   "shadows": [{"type": "title", "name": "Kindling"}]}},
               "script": OPEN + CHIPS + r"""
      await h.type({ name: 'also-called-artist:hearthensemble' }, 'kindling');
      await h.key({ name: 'also-called-artist:hearthensemble' }, 'Enter');
      return { toasts: toasts() };
    """},
    "exists": {**DRAWER, "api": {FOR_TRACK: VIEW, "POST /api/music/aliases": {
                   "status": "exists", "alias": VIEW["artists"][0]["aliases"][0], "shadows": []}},
               "script": OPEN + CHIPS + r"""
      await h.type({ name: 'also-called-artist:hearthensemble' }, 'Gramps');
      await h.key({ name: 'also-called-artist:hearthensemble' }, 'Enter');
      return { toasts: toasts(), chips: chips() };
    """},
    "replace": {**DRAWER, "setup": REPLACE_SETUP,
                "api": {FOR_TRACK: VIEW, "POST /api/music/aliases": TAKEN_REPLACEABLE},
                "script": OPEN + CHIPS + r"""
      await h.type({ name: 'also-called-artist:hearthensemble' }, 'gramps');
      await h.key({ name: 'also-called-artist:hearthensemble' }, 'Enter');
      const dialog = h.find({ type: 'div', text: 'already means' });
      const question = dialog ? dialog.text : null;
      await h.click({ type: 'button', text: 'Replace' });
      return { question, posts: h.calls.filter((c) => c.method === 'POST').map((c) => c.body),
               chips: chips(), toasts: toasts(), dialogAfter: !!h.find({ type: 'button', text: 'Replace' }) };
    """},
    "replace_cancel": {**DRAWER, "api": {FOR_TRACK: VIEW, "POST /api/music/aliases": TAKEN_REPLACEABLE},
                       "script": OPEN + r"""
      await h.type({ name: 'also-called-artist:hearthensemble' }, 'gramps');
      await h.key({ name: 'also-called-artist:hearthensemble' }, 'Enter');
      const shown = !!h.find({ type: 'button', text: 'Replace' });
      await h.click({ type: 'button', text: 'Cancel' });
      return { shown, gone: !h.find({ type: 'button', text: 'Replace' }),
               posts: h.calls.filter((c) => c.method === 'POST').length };
    """},
    "not_yours": {**DRAWER, "api": {FOR_TRACK: VIEW, "POST /api/music/aliases": TAKEN_NOT_YOURS},
                  "script": OPEN + CHIPS + r"""
      await h.type({ name: 'also-called-artist:hearthensemble' }, 'gramps');
      await h.key({ name: 'also-called-artist:hearthensemble' }, 'Enter');
      return { toasts: toasts(), dialog: !!h.find({ type: 'button', text: 'Replace' }),
               posts: h.calls.filter((c) => c.method === 'POST').length };
    """},
    "refused_name": {**DRAWER, "api": {FOR_TRACK: VIEW, "POST /api/music/aliases": {"__error": {
                         "status": 422, "message": "422", "detail": {"detail": {
                             "code": "already_its_name", "message": "That's already what Hearth Ensemble is called."}}}}},
                     "script": OPEN + CHIPS + r"""
      await h.type({ name: 'also-called-artist:hearthensemble' }, 'hearth-ensemble');
      await h.key({ name: 'also-called-artist:hearthensemble' }, 'Enter');
      return { toasts: toasts() };
    """},
    "remove": {**DRAWER, "api": {FOR_TRACK: VIEW, "DELETE /api/music/aliases/13": None},
               "script": OPEN + CHIPS + r"""
      await h.click({ type: 'button', title: "remove 'hearth band'" });
      return { deletes: h.calls.filter((c) => c.method === 'DELETE').map((c) => c.path),
               chips: chips().map((c) => c.text), toasts: toasts() };
    """},
    "escape": {**DRAWER, "api": {FOR_TRACK: VIEW}, "script": OPEN + r"""
      await h.type({ name: 'also-called-song' }, 'half typed');
      const before = h.plain(h.find({ name: 'also-called-song' })).props.value;
      await h.key({ name: 'also-called-song' }, 'Escape');
      await h.key({ name: 'also-called-song' }, 'Enter');
      return { before, after: h.plain(h.find({ name: 'also-called-song' })).props.value,
               posts: h.calls.filter((c) => c.method === 'POST').length };
    """},
    "band_credit": {**DRAWER, "api": {FOR_TRACK: {
                        "track": {"id": 6, "title": "Ash Avenue", "aliases": []},
                        "artists": [{"name": "Earth, Wind & Fire", "key": "earthwindandfire", "credit": True, "aliases": []},
                                    {"name": "Earth", "key": "earth", "credit": False, "aliases": []}],
                        "album": None}},
                    "script": OPEN + r"""
      return { texts: h.text(), inputs: h.findAll({ placeholder: 'add a name…' }).length };
    """},
    "old_server": {**DRAWER, "api": {}, "script": OPEN + r"""
      return { texts: h.text(), inputs: h.findAll({ placeholder: 'add a name…' }).length };
    """},
}


def _stats(fetch):
    return {"household": 4, "musicbrainz": 2, "suppressed": 1, "fetch": fetch}


STATS = {"total_tracks": 1, "total_duration_sec": 200, "by_added_via": {"manual": 1},
         "by_source": {"library": 1}, "enriched_count": 0}
for name, fetch in {
    "stats_off": {"enabled": False, "state": "off", "artists_total": 2319, "checked": 0},
    "stats_running": {"enabled": True, "state": "running", "artists_total": 2319, "checked": 1204},
    "stats_offline": {"enabled": True, "state": "offline", "artists_total": 2319, "checked": 10},
    "stats_done": {"enabled": True, "state": "idle", "artists_total": 12, "checked": 12},
    "stats_unknown": {"enabled": None, "state": "unknown", "artists_total": 3, "checked": 0},
}.items():
    SCENARIOS[name] = {"files": FILES, "component": "StatsTab", "props": {"stats": STATS, "loading": False},
                       "api": {"GET /api/music/aliases/status": _stats(fetch)},
                       "script": "h.render(); return { texts: h.text() };"}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("also-called") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def test_one_editable_list_per_target(driven) -> None:
    r = driven["lists"]
    assert r["fetched"] == ["/api/music/aliases/for-track/6"]
    assert "also called" in r["texts"]
    for label in ("song", "artist", "album"):
        assert label in r["texts"]
    # a list per target (song, artist, album) — never one text box
    assert [i["name"] for i in r["inputs"]] == ["also-called-song", "also-called-artist:hearthensemble",
                                                "also-called-album"]
    assert all(i["maxLength"] == 120 and i["placeholder"] == "add a name…" for i in r["inputs"])
    assert [c["text"] for c in r["chips"]] == ["the warm one", "gramps", "hearth band"]


def test_musicbrainz_chips_are_muted_and_only_removable_ones_have_an_x(driven) -> None:
    r = driven["lists"]
    mb = [c for c in r["chips"] if c["title"] == "from MusicBrainz"]
    assert [c["text"] for c in mb] == ["hearth band"] and mb[0]["background"] == "transparent"
    household = [c for c in r["chips"] if c["title"] != "from MusicBrainz"]
    assert all(c["background"] == "var(--brand-soft)" for c in household)
    assert {c["title"] for c in household} == {"added by Kitchen tablet", "added by voice in den"}
    # "gramps" was added by someone else: no x for this caller
    assert r["removable"] == ["remove 'the warm one'", "remove 'hearth band'"]


def test_enter_adds_one_trimmed_name_to_that_target(driven) -> None:
    r = driven["add"]
    assert r["posts"] == [{"method": "POST", "path": "/api/music/aliases", "body": {
        "target_type": "artist", "target_name": "Hearth Ensemble", "alias": "the kindlers", "replace": False}}]
    assert "the kindlers" in [c["text"] for c in r["chips"]]
    assert r["draft"] == ""
    assert r["toasts"] == ["'the kindlers' now also means Hearth Ensemble"]


def test_albums_pin_their_artist_and_songs_go_by_id(driven) -> None:
    album, song = driven["add_album"]["posts"]
    assert album == {"target_type": "album", "target_name": "By The Hearth",
                     "target_artist_name": "Hearth Ensemble", "alias": "hearth record", "replace": False}
    assert song == {"target_type": "track", "target_track_id": 6, "alias": "warmies", "replace": False}


def test_a_name_taking_over_a_library_name_says_so(driven) -> None:
    assert driven["shadow"]["toasts"] == ["'kindling' will now play Hearth Ensemble instead of Kindling."]
    assert driven["exists"]["toasts"] == ["'gramps' already means Hearth Ensemble"]
    assert [c["text"] for c in driven["exists"]["chips"]].count("gramps") == 1


def test_a_taken_name_asks_to_replace_it_then_posts_replace(driven) -> None:
    r = driven["replace"]
    assert r["question"] == "'gramps' already means Ember Choir (artist). Replace it?"
    bodies = r["posts"]
    assert [b["replace"] for b in bodies] == [False, True]
    assert bodies[1] == {"target_type": "artist", "target_name": "Hearth Ensemble", "alias": "gramps", "replace": True}
    assert r["dialogAfter"] is False
    # the moved row lands on this target (and only once)
    assert [c["text"] for c in r["chips"]].count("gramps") == 2      # the voice one + the replaced one
    c = driven["replace_cancel"]
    assert c["shown"] and c["gone"] and c["posts"] == 1


def test_a_name_someone_else_holds_is_not_offered_for_replacing(driven) -> None:
    r = driven["not_yours"]
    assert r["dialog"] is False and r["posts"] == 1
    assert r["toasts"] == ["Only an admin can change a name someone else added."]
    assert driven["refused_name"]["toasts"] == ["That's already what Hearth Ensemble is called."]


def test_x_removes_one_name(driven) -> None:
    r = driven["remove"]
    assert r["deletes"] == ["/api/music/aliases/13"]
    assert r["chips"] == ["the warm one", "gramps"]
    assert r["toasts"] == ["removed 'hearth band'"]


def test_escape_clears_and_an_empty_enter_sends_nothing(driven) -> None:
    r = driven["escape"]
    assert (r["before"], r["after"], r["posts"]) == ("half typed", "", 0)


def test_a_band_credit_is_offered_whole(driven) -> None:
    r = driven["band_credit"]
    assert "whole credit" in r["texts"] and "Earth, Wind & Fire" in r["texts"]
    assert r["inputs"] == 3           # song, the whole credit, one part; no album


def test_an_older_server_shows_no_section(driven) -> None:
    r = driven["old_server"]
    assert "also called" not in r["texts"] and r["inputs"] == 0


@pytest.mark.parametrize(("scenario", "line"), [
    ("stats_off", "MusicBrainz lookup: off — Settings → Configuration → Library"),
    ("stats_running", "MusicBrainz lookup: checking — 1,204 of 2,319 artists"),
    ("stats_offline", "MusicBrainz lookup: paused — offline"),
    ("stats_done", "MusicBrainz lookup: done"),
    ("stats_unknown", "MusicBrainz lookup: status unknown"),
])
def test_the_stats_card_says_where_the_lookup_is(driven, scenario: str, line: str) -> None:
    texts = driven[scenario]["texts"]
    assert "also-called names" in texts and "6" in texts
    assert f"4 household · 2 from MusicBrainz · {line}" in texts
