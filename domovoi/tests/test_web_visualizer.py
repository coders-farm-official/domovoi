"""The Now Playing visualizer: the vendored engine, the standard presets, the
rules for an added preset, the ``.milk`` reader and the routes.

* The engine (``web/static/vendor/butterchurn``) is the npm file unchanged
  (sha256 pinned), with its licence texts beside it and a row in
  docs/LICENSES.md.
* The standard set (``web/static/visualizer/presets``) is Domovoi's own:
  every file the index names exists, passes the same rules as an upload,
  runs EEL only and carries no JavaScript equations.
* An added preset (``web.backend.visualizer_presets``): Butterchurn 3 JSON
  is kept with only the fields the engine reads; the older JavaScript
  format is refused; bounds on size, names and numbers; a ``.milk`` file's
  settings and equations come across, its shaders are dropped with a note.
* The routes (``web/backend/api/visualizer.py``): the list and one preset
  are household reads; adding and removing are admin only (Bearer); a
  refused call never touches the store; ids can't reach outside the
  folder.

DB-free: the store is a folder (``visualizer_presets_dir`` -> tmp_path).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from domovoi.config import settings as core_settings
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db, web_client
from web.backend import visualizer_presets as vp

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "web" / "static"
VENDOR = STATIC / "vendor" / "butterchurn"
PRESETS = STATIC / "visualizer" / "presets"
ADMIN = "admin-session-token"
DEVICE = "lantern-river-kettle-harbor"
ENGINE_SHA256 = "b5353af542b95cb5264069a0a4f52a23174e45745b58c48e245a3557d229b640"

MILK = """[preset00]
fRating=3.000000
fGammaAdj=1.700000
fDecay=0.950000
fVideoEchoZoom=1.200000
nWaveMode=2
bAdditiveWaves=1
fWaveAlpha=0.800000
zoom=1.010000
rot=0.020000
nMotionVectorsX=12.000000
wavecode_0_enabled=1
wavecode_0_samples=256
wavecode_0_bSpectrum=1
wave_0_per_point1=x = sample;
wave_0_per_point2=y = 0.5 + value1*0.2;
shapecode_1_enabled=1
shapecode_1_sides=6
shapecode_1_thick=1
shapecode_1_num_inst=3
shape_1_per_frame1=ang = time;
per_frame_init_1=q1 = 0;
per_frame_1=zoom = 1.0 + 0.05*bass;
per_frame_2=rot = rot + 0.01*treb;
per_pixel_1=dx = 0.01*sin(y*10);
"""

MILK_SHADERS = MILK + """MILKDROP_PRESET_VERSION=201
warp_1=`shader_body {
warp_2=`  ret = tex2D(sampler_main, uv).xyz;
warp_3=`}
comp_1=`shader_body { ret = tex2D(sampler_main, uv).xyz; }
"""


def _bc3(**over):
    body = {
        "version": 2,
        "baseVals": {"decay": 0.9, "zoom": 1.01},
        "shapes": [{"baseVals": {"enabled": 1, "sides": 5}, "init_eqs_eel": "", "frame_eqs_eel": "ang = time;"}],
        "waves": [],
        "init_eqs_eel": "",
        "frame_eqs_eel": "zoom = 1 + 0.1*bass;",
        "pixel_eqs_eel": "",
        "warp": "",
        "comp": "shader_body { ret = texture(sampler_main, uv).rgb; }",
    }
    body.update(over)
    return body


# ─── the engine ──────────────────────────────────────────────────────────


def test_the_engine_is_the_npm_file_unchanged_with_its_licences():
    data = (VENDOR / "butterchurn.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == ENGINE_SHA256
    assert (VENDOR / "LICENSE.txt").read_text(encoding="utf-8").startswith("MIT License")
    assert "Apache License" in (VENDOR / "LICENSE-assemblyscript-loader.txt").read_text(encoding="utf-8")
    assert (VENDOR / "NOTICE-assemblyscript.txt").is_file()
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    assert ENGINE_SHA256 in readme and "3.0.0-beta.5" in readme


def test_licences_doc_lists_the_engine_and_the_presets():
    doc = (REPO / "docs" / "LICENSES.md").read_text(encoding="utf-8")
    assert "Butterchurn" in doc and "3.0.0-beta.5" in doc
    assert "eel-wasm" in doc and "@assemblyscript/loader" in doc
    assert "web/static/visualizer/presets" in doc


def test_the_page_loads_the_visualizer_and_never_a_cdn():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'src="visualizer.jsx"' in html
    src = (STATIC / "visualizer.jsx").read_text(encoding="utf-8")
    assert "'/vendor/butterchurn/butterchurn.min.js'" in src
    assert "onlyUseWASM: true" in src
    assert "http://" not in src.replace("http://192.168.1.20:6369", "") and "https://" not in src


# ─── the standard set ────────────────────────────────────────────────────


def _index():
    return json.loads((PRESETS / "index.json").read_text(encoding="utf-8"))


def test_the_standard_set_is_eight_to_fifteen_presets_all_present():
    idx = _index()
    ids = [p["id"] for p in idx["presets"]]
    assert 8 <= len(ids) <= 15
    assert len(set(ids)) == len(ids)
    assert idx["default"] in ids
    on_disk = {p.name for p in PRESETS.glob("*.json")} - {"index.json"}
    assert on_disk == {p["file"] for p in idx["presets"]}


@pytest.mark.parametrize("entry", _index()["presets"], ids=lambda e: e["id"])
def test_every_standard_preset_passes_the_upload_rules_and_runs_eel_only(entry):
    body = json.loads((PRESETS / entry["file"]).read_text(encoding="utf-8"))
    assert vp.validate_preset(body) == body, "a standard preset should already be in stored form"
    text = json.dumps(body)
    assert "_eqs_str" not in text
    assert body["frame_eqs_eel"].strip(), "every standard preset reacts per frame"
    assert any(w in text for w in ("bass", "mid", "treb", "value1")), "every standard preset follows the music"


def test_the_standard_set_says_where_it_comes_from():
    readme = (PRESETS / "README.md").read_text(encoding="utf-8")
    assert "written for Domovoi" in readme and "MIT" in readme


# ─── the rules for an added preset ───────────────────────────────────────


def test_a_butterchurn_3_preset_is_kept_with_only_what_the_engine_reads():
    out = vp.validate_preset(_bc3(extraField="x", rating=5))
    assert set(out) == {"version", "baseVals", "shapes", "waves", "init_eqs_eel", "frame_eqs_eel",
                        "pixel_eqs_eel", "warp", "comp"}
    assert len(out["shapes"]) == 4 and len(out["waves"]) == 4
    assert out["shapes"][1] == {"baseVals": {"enabled": 0}, "init_eqs_eel": "", "frame_eqs_eel": ""}
    assert out["waves"][0]["point_eqs_eel"] == ""


def test_javascript_equations_are_refused_and_dropped_beside_eel():
    old = {"baseVals": {}, "shapes": [], "waves": [], "init_eqs_str": "a.q1 = 0;",
           "frame_eqs_str": "fetch('/api/x');", "pixel_eqs_str": "", "warp": "", "comp": ""}
    with pytest.raises(vp.PresetError, match="JavaScript"):
        vp.validate_preset(old)
    both = vp.validate_preset(_bc3(frame_eqs_str="alert(1)"))
    assert "frame_eqs_str" not in json.dumps(both)
    with pytest.raises(vp.PresetError, match="JavaScript"):
        vp.validate_preset(_bc3(shapes=[{"baseVals": {}, "frame_eqs_str": "alert(1)"}]))


@pytest.mark.parametrize("bad, why", [
    ([], "object"),
    ({"hello": 1}, "isn't a Butterchurn preset"),
    (_bc3(version=3), "version"),
    (_bc3(baseVals={"zoom": "1"}), "number"),
    (_bc3(baseVals={"zoom": float("nan")}), "number"),
    (_bc3(baseVals={"zoom": True}), "number"),
    (_bc3(baseVals={"a b": 1}), "setting name"),
    (_bc3(shapes=[{}] * 5), "at most 4"),
    (_bc3(waves="x"), "list"),
    (_bc3(frame_eqs_eel=1), "text"),
    (_bc3(frame_eqs_eel="x" * (vp.MAX_CODE_CHARS + 1)), "too long"),
    (_bc3(comp="a\x00b"), "character"),
])
def test_what_is_refused(bad, why):
    with pytest.raises(vp.PresetError, match=why):
        vp.validate_preset(bad)


@pytest.mark.parametrize("raw, name", [
    ("Night drive.milk", "Night drive"),
    ("C:\\Users\\alex\\presets\\a/b.json", "b"),
    ("../../etc/passwd.milk", "passwd"),
    ("tab\there\u202e.json", "tab here"),
    ("x" * 300 + ".milk", "x" * vp.NAME_MAX),
], ids=["plain", "windows-path", "traversal", "control-chars", "long"])
def test_names_come_from_the_file_name_only(raw, name):
    assert vp.clean_name(raw) == name


def test_an_empty_name_is_refused():
    with pytest.raises(vp.PresetError):
        vp.clean_name(".milk")


# ─── .milk ───────────────────────────────────────────────────────────────


def test_a_milk_file_comes_across_settings_and_equations():
    preset, notes = vp.parse_milk(MILK)
    assert notes == []
    assert preset["version"] == 1   # no MILKDROP_PRESET_VERSION: a MilkDrop 1 preset
    bv = preset["baseVals"]
    assert bv["gammaadj"] == 1.7 and bv["decay"] == 0.95 and bv["echo_zoom"] == 1.2
    assert bv["wave_mode"] == 2 and bv["additivewave"] == 1 and bv["mv_x"] == 12 and bv["zoom"] == 1.01
    assert preset["frame_eqs_eel"] == "zoom = 1.0 + 0.05*bass;\nrot = rot + 0.01*treb;"
    assert preset["init_eqs_eel"] == "q1 = 0;"
    assert preset["pixel_eqs_eel"] == "dx = 0.01*sin(y*10);"
    w0 = preset["waves"][0]
    assert w0["baseVals"] == {"enabled": 1, "samples": 256, "spectrum": 1}
    assert w0["point_eqs_eel"] == "x = sample;\ny = 0.5 + value1*0.2;"
    s1 = preset["shapes"][1]
    assert s1["baseVals"] == {"enabled": 1, "sides": 6, "thickoutline": 1, "num_inst": 3}
    assert s1["frame_eqs_eel"] == "ang = time;"
    assert preset["warp"] == "" and preset["comp"] == ""


def test_a_milk_files_shaders_are_dropped_with_a_note():
    preset, notes = vp.parse_milk(MILK_SHADERS)
    assert preset["version"] == 2
    assert preset["warp"] == "" and preset["comp"] == ""
    assert notes == [vp.SHADERS_DROPPED]
    assert "tex2D" not in json.dumps(preset)


def test_crlf_and_a_byte_order_mark_are_read():
    name, source, preset, _ = vp.convert_upload("x.milk", "\ufeff" + MILK.replace("\n", "\r\n"))
    assert source == "milk" and preset["frame_eqs_eel"].startswith("zoom = 1.0")


@pytest.mark.parametrize("filename, content, why", [
    ("x.milk", "just some words", "doesn't look like"),
    ("x.json", "{not json", "valid JSON"),
    ("x.txt", MILK, ".milk file or"),
    ("x.milk", "a" * (vp.MAX_UPLOAD_BYTES + 1), "too large"),
], ids=["not-milk", "not-json", "txt", "oversize"])
def test_uploads_that_are_refused(filename, content, why):
    with pytest.raises(vp.PresetError, match=why):
        vp.convert_upload(filename, content)


# ─── the store ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../x", "..", "a/b", "A", "x.json", "", "-x", "x" * 64, "con\x00"])
def test_ids_never_build_a_path(tmp_path, bad):
    assert not vp.valid_id(bad)
    with pytest.raises(vp.PresetError):
        vp._path(tmp_path, bad)


def test_save_list_read_delete(tmp_path):
    name, source, preset, notes = vp.convert_upload("Night drive.json", json.dumps(_bc3()))
    s = vp.save_added(name, source, preset, notes, root=tmp_path)
    assert s["name"] == "Night drive" and s["source"] == "json" and vp.valid_id(s["id"])
    with pytest.raises(vp.PresetError, match="already"):
        vp.save_added(name, source, preset, notes, root=tmp_path)
    assert [x["id"] for x in vp.list_added(tmp_path)] == [s["id"]]
    assert vp.read_added(s["id"], root=tmp_path)["preset"] == preset
    assert not list(tmp_path.glob(".upload-*"))
    assert vp.delete_added(s["id"], root=tmp_path)
    assert vp.list_added(tmp_path) == [] and not vp.delete_added(s["id"], root=tmp_path)


def test_a_stray_or_broken_file_is_not_listed(tmp_path):
    (tmp_path / "broken-1.json").write_text("{", encoding="utf-8")
    (tmp_path / "other-2.json").write_text(json.dumps({"id": "not-this-name", "preset": {}}), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert vp.list_added(tmp_path) == []
    assert vp.read_added("broken-1", root=tmp_path) is None


def test_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(vp, "MAX_PRESETS", 1)
    vp.save_added("a", "json", vp.validate_preset(_bc3()), [], root=tmp_path)
    with pytest.raises(vp.PresetError, match="remove some"):
        vp.save_added("b", "json", vp.validate_preset(_bc3(comp="")), [], root=tmp_path)


# ─── the routes ──────────────────────────────────────────────────────────


@pytest.fixture
def auth(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN}, device_token=DEVICE)


@pytest.fixture
def store(monkeypatch, tmp_path):
    root = tmp_path / "presets"
    monkeypatch.setattr(core_settings, "visualizer_presets_dir", str(root), raising=False)
    return root


async def test_reads_are_household_reads(auth, store):
    vp.save_added("Seed", "json", vp.validate_preset(_bc3()), [], root=store)
    sid = vp.list_added(store)[0]["id"]
    async with web_client() as c:
        assert (await c.get("/api/visualizer/presets")).status_code == 401
        assert (await c.get(f"/api/visualizer/presets/{sid}")).status_code == 401
        listed = await c.get("/api/visualizer/presets", headers={HEADER: DEVICE})
        one = await c.get(f"/api/visualizer/presets/{sid}", headers={HEADER: DEVICE})
        missing = await c.get("/api/visualizer/presets/nope-1", headers={HEADER: DEVICE})
        odd = await c.get("/api/visualizer/presets/..%2F..%2Fx", headers={HEADER: DEVICE})
    assert listed.status_code == 200
    body = listed.json()
    assert [p["name"] for p in body["presets"]] == ["Seed"] and body["limits"]["accepts"] == [".json", ".milk"]
    assert one.status_code == 200 and one.json()["preset"]["frame_eqs_eel"] == "zoom = 1 + 0.1*bass;"
    assert missing.status_code == 404 and odd.status_code == 404


async def test_the_standard_set_is_served_statically(auth):
    async with web_client() as c:
        idx = await c.get("/visualizer/presets/index.json")
        one = await c.get("/visualizer/presets/hearth.json")
        engine = await c.get("/vendor/butterchurn/butterchurn.min.js")
    assert idx.status_code == 200 and idx.json()["default"] == "hearth"
    assert one.status_code == 200 and "frame_eqs_eel" in one.json()
    assert engine.status_code == 200
    # An ES module only runs when served as JavaScript.
    assert "javascript" in engine.headers["content-type"]


@pytest.mark.parametrize("method, path, body", [
    ("POST", "/api/visualizer/presets", {"filename": "x.milk", "content": MILK}),
    ("DELETE", "/api/visualizer/presets/seed-0123456789", None),
])
async def test_writes_are_admin_bearer_only(auth, store, method, path, body):
    vp.save_added("seed", "json", vp.validate_preset(_bc3()), [], root=store)
    before = sorted(p.name for p in store.iterdir())
    kw = {"json": body} if body else {}
    async with web_client() as c:
        nobody = await c.request(method, path, **kw)
        device = await c.request(method, path, headers={HEADER: DEVICE, "X-Requested-With": "t"}, **kw)
    async with web_client(cookies={COOKIE: ADMIN}) as c:
        cookie = await c.request(method, path, **kw)
    assert nobody.status_code == 401
    assert device.status_code in (401, 403)
    assert cookie.status_code == 403
    assert sorted(p.name for p in store.iterdir()) == before, "a refused call changed the store"


async def test_an_admin_adds_lists_and_removes(auth, store):
    async with web_client() as c:
        add = await c.post("/api/visualizer/presets", headers=bearer(ADMIN) | {"X-Requested-With": "t"},
                           json={"filename": "Night drive.milk", "content": MILK_SHADERS})
        again = await c.post("/api/visualizer/presets", headers=bearer(ADMIN) | {"X-Requested-With": "t"},
                             json={"filename": "Night drive.milk", "content": MILK_SHADERS})
        bad = await c.post("/api/visualizer/presets", headers=bearer(ADMIN) | {"X-Requested-With": "t"},
                           json={"filename": "old.json", "content": json.dumps(
                               {"baseVals": {}, "frame_eqs_str": "a.zoom=1;", "init_eqs_str": ""})})
        listed = await c.get("/api/visualizer/presets", headers={HEADER: DEVICE})
        pid = add.json()["id"]
        gone = await c.delete(f"/api/visualizer/presets/{pid}", headers=bearer(ADMIN) | {"X-Requested-With": "t"})
        twice = await c.delete(f"/api/visualizer/presets/{pid}", headers=bearer(ADMIN) | {"X-Requested-With": "t"})
    assert add.status_code == 201
    assert add.json()["source"] == "milk" and add.json()["notes"] == [vp.SHADERS_DROPPED]
    assert again.status_code == 409
    assert bad.status_code == 400 and "JavaScript" in bad.json()["detail"]
    assert [p["id"] for p in listed.json()["presets"]] == [pid]
    assert gone.status_code == 204 and twice.status_code == 404
    assert [p.name for p in store.iterdir()] == []


async def test_an_oversize_body_is_refused_before_it_is_read(auth, store):
    async with web_client() as c:
        r = await c.post("/api/visualizer/presets", headers=bearer(ADMIN) | {"X-Requested-With": "t"},
                         json={"filename": "big.milk", "content": "a=1\n" * (vp.MAX_UPLOAD_BYTES // 2)})
    assert r.status_code in (400, 413, 422)
    assert not store.exists() or not list(store.iterdir())
