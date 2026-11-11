"""Where the media folders really are (domovoi/media_folders.py, 2026-11-11).

The Windows install's folders are Windows folders mounted into the distro;
DomovoiHost reports the mapping into a root-owned file, the core writes
bounded requests for "open on the Domovoi PC" (the tray carries it out) and
"move the media folders" (DomovoiHost carries it out). Covers the report's
trust and field checks, the /etc/fstab fallback for an older host, the
per-host page, both requests' gating and shapes, the tray's loopback hops,
the Linux path check behind Settings, and the MPD refresh after a move.

No DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domovoi import media_folders as mf
from domovoi.config import settings

ROOT = "C:\\Users\\Public\\Domovoi"
FOLDERS = {"music": ROOT + "\\Music", "pictures": ROOT + "\\Pictures", "videos": ROOT + "\\Videos",
           "documents": ROOT + "\\Documents", "audiobooks": ROOT + "\\Audiobooks"}
REPORT = {
    "schema": 1, "host_kind": "windows", "root": ROOT, "mount_base": "/srv/media",
    "folders": FOLDERS, "source": "setup", "can_open": True, "can_change": True,
    "change": {"state": "none", "request_id": None, "root": None, "problem": None},
    "written_at": "2026-11-11T05:00:00Z",
}
FSTAB_TEXT = (
    "LABEL=cloudimg-rootfs / ext4 discard,errors=remount-ro 0 1\n"
    "# BEGIN Domovoi media folders (scripts/linux/provision.sh rewrites this block)\n"
    "C:/Users/Public/Domovoi/Music /srv/media/music drvfs metadata,uid=1000,gid=1000,umask=022,fmask=133,noatime,nofail 0 0\n"
    "D:/My\\040Media/Videos /srv/media/videos drvfs metadata,uid=1000,gid=1000,umask=022,fmask=133,noatime,nofail 0 0\n"
    "# END Domovoi media folders\n"
)


@pytest.fixture
def host(tmp_path, monkeypatch):
    report = tmp_path / "var" / "lib" / "domovoi-windows" / "media.json"
    report.parent.mkdir(parents=True)
    fstab = tmp_path / "etc" / "fstab"
    fstab.parent.mkdir(parents=True)
    monkeypatch.setenv("DOMOVOI_HOST_MEDIA_REPORT", str(report))
    monkeypatch.setenv("DOMOVOI_FSTAB", str(fstab))
    monkeypatch.setenv("DOMOVOI_MEDIA_FOLDERS_DIR", str(tmp_path / "home" / ".domovoi" / "media_folders"))
    monkeypatch.setattr(mf, "_root_only", lambda st: True)
    monkeypatch.setattr(mf, "_tray_running", lambda: True)
    monkeypatch.setattr(mf, "_update_running", lambda: False)
    for _, _, field in mf.KINDS:
        monkeypatch.setattr(settings, field, "/srv/media/" + field[:-4])
    monkeypatch.setattr(settings, "podcasts_dir", "/home/domovoi/.domovoi/podcasts")

    def write(doc=REPORT) -> None:
        report.write_text(json.dumps(doc) if isinstance(doc, dict) else doc, encoding="utf-8")

    write()
    return {"report": report, "write": write, "fstab": fstab, "dir": tmp_path / "home" / ".domovoi" / "media_folders"}


# ─── the report ──────────────────────────────────────────────────────────


def test_the_report_is_read_and_checked(host) -> None:
    r = mf.read_report()
    assert r["host_kind"] == "windows" and r["root"] == ROOT and r["folders"] == FOLDERS
    assert r["can_open"] and r["can_change"] and r["change"]["state"] == "none"


def test_a_report_not_roots_alone_is_not_believed(host, monkeypatch) -> None:
    monkeypatch.setattr(mf, "_root_only", lambda st: False)
    assert mf.read_report() is None
    assert mf.host()["kind"] == "linux"


@pytest.mark.parametrize("change", [
    {"schema": 2},
    {"host_kind": "mac"},
    {"root": "\\\\nas\\share"},
    {"root": "C:\\"},
    {"mount_base": "/mnt"},
    {"folders": {"music": "relative\\Music"}},
    {"folders": {"etc": "C:\\x"}},
    {"folders": {"music": "C:\\a\\..\\Windows"}},
    {"folders": ["C:\\x"]},
])
def test_a_report_in_another_shape_is_ignored(host, change) -> None:
    host["write"]({**REPORT, **change})
    assert mf.read_report() is None


def test_unknown_words_in_a_report_are_dropped(host) -> None:
    host["write"]({**REPORT, "source": "hacked", "can_open": "yes",
                   "change": {"state": "explode", "request_id": "nothex", "problem": "x" * 999}})
    r = mf.read_report()
    assert r["source"] == "setup" and r["can_open"] is False
    assert r["change"]["state"] == "none" and r["change"]["request_id"] is None and r["change"]["problem"] is None


def test_an_older_host_still_shows_the_folders_from_fstab(host) -> None:
    host["report"].unlink()
    host["fstab"].write_text(FSTAB_TEXT, encoding="utf-8")
    h = mf.host()
    assert h["kind"] == "windows" and h["source"] == "fstab"
    assert h["folders"] == {"music": "C:\\Users\\Public\\Domovoi\\Music", "videos": "D:\\My Media\\Videos"}
    assert h["can_open"] is False and h["can_change"] is False
    assert h["root"] is None  # two different parents: no one root
    s = mf.status()
    assert s["open"]["available"] is False and "newer" in s["open"]["why"] or "update" in s["open"]["why"]
    assert s["change"]["available"] is False and "Setup" in s["change"]["why"]


def test_fstab_lines_outside_the_block_or_not_drvfs_are_ignored(host) -> None:
    host["report"].unlink()
    host["fstab"].write_text(
        "C:/x /srv/media/music drvfs defaults 0 0\n" + FSTAB_TEXT.replace("drvfs", "ext4", 1), encoding="utf-8")
    assert mf.read_fstab_folders() == {"videos": "D:\\My Media\\Videos"}


def test_the_common_root() -> None:
    assert mf._common_root(FOLDERS) == ROOT
    assert mf._common_root({"music": "D:\\M\\Songs"}) is None


# ─── the page ────────────────────────────────────────────────────────────


def test_windows_page_shows_the_windows_folders(host) -> None:
    s = mf.status()
    assert s["host_kind"] == "windows" and s["place"] == "the Domovoi PC" and s["root"] == ROOT
    rows = {r["kind"]: r for r in s["kinds"]}
    assert rows["music"]["location"] == ROOT + "\\Music" and rows["music"]["where"] == "host"
    assert rows["music"]["linux_path"] == "/srv/media/music" and rows["music"]["editable"] is False
    assert rows["podcasts"]["location"] is None and rows["podcasts"]["where"] == "internal"
    assert [r["kind"] for r in s["kinds"]] == ["music", "videos", "pictures", "documents", "audiobooks", "podcasts"]
    assert s["open"]["available"] is True and s["change"]["available"] is True


def test_a_subfolder_of_a_mount_maps_inside_its_folder(host, monkeypatch) -> None:
    monkeypatch.setattr(settings, "videos_dir", "/srv/media/videos/films/new")
    rows = {r["kind"]: r for r in mf.status()["kinds"]}
    assert rows["videos"]["location"] == ROOT + "\\Videos\\films\\new"


def test_linux_page_is_the_path_itself(host, tmp_path, monkeypatch) -> None:
    host["report"].unlink()
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr(settings, "music_dir", str(music))
    s = mf.status()
    assert s["host_kind"] == "linux" and s["root"] is None
    rows = {r["kind"]: r for r in s["kinds"]}
    assert rows["music"]["location"] == str(music) and rows["music"]["exists"] and rows["music"]["editable"]
    assert rows["pictures"]["editable"] is False
    assert s["open"]["available"] is False and s["change"]["available"] is False


def test_no_tray_no_open(host, monkeypatch) -> None:
    monkeypatch.setattr(mf, "_tray_running", lambda: False)
    o = mf.status()["open"]
    assert o["available"] is False and "icon" in o["why"]
    with pytest.raises(mf.Refused):
        mf.request_open("music")


# ─── open on the Domovoi PC ─────────────────────────────────────────────


def test_open_names_a_kind_never_a_path(host) -> None:
    r = mf.request_open("music")
    doc = json.loads((host["dir"] / "open.json").read_text())
    assert set(doc) == {"v", "id", "kind", "at"} and doc["kind"] == "music" and doc["id"] == r["id"]
    assert mf.pending_open()["kind"] == "music"


@pytest.mark.parametrize("kind", ["podcasts", "C:\\Windows", "../music", ""])
def test_open_refuses_anything_without_a_host_folder(host, kind) -> None:
    with pytest.raises(mf.Refused):
        mf.request_open(kind)


def test_open_is_refused_on_linux(host) -> None:
    host["report"].unlink()
    with pytest.raises(mf.Refused):
        mf.request_open("music")


def test_one_tray_claims_and_reports(host) -> None:
    rid = mf.request_open("videos")["id"]
    assert mf.claim_open("0" * 32) == "gone"
    assert mf.claim_open(rid) == "ok"
    assert mf.pending_open() is None
    assert mf.claim_open(rid) == "gone"
    with pytest.raises(PermissionError):
        mf.record_open_result("0" * 32, "opened", None)
    with pytest.raises(ValueError):
        mf.record_open_result(rid, "exploded", None)
    mf.record_open_result(rid, "opened", "C:\\Users\\Public\\Domovoi\\Videos")
    assert mf.status()["open"]["last"]["outcome"] == "opened"


def test_a_second_claim_while_one_is_held_loses(host) -> None:
    rid = mf.request_open("music")["id"]
    (host["dir"] / "open-claim.json").write_text("{}")
    assert mf.claim_open(rid) == "claimed"


def test_an_old_open_request_is_dropped(host, monkeypatch) -> None:
    mf.request_open("music")
    real = mf._now
    monkeypatch.setattr(mf, "_now", lambda: real() + mf.OPEN_TTL_SEC + 5)
    assert mf.pending_open() is None
    assert not (host["dir"] / "open.json").exists()


# ─── move the media root ────────────────────────────────────────────────


def test_a_move_writes_the_bounded_request(host) -> None:
    mf.request_change("D:/Media/")
    doc = json.loads((host["dir"] / "request.json").read_text())
    assert set(doc) == {"v", "root", "id", "at"} and doc["root"] == "D:\\Media" and len(doc["id"]) == 32
    st = mf.status()["change"]
    assert st["state"] == "waiting" and st["waiting_for_host"] and st["requested_root"] == "D:\\Media"


@pytest.mark.parametrize("root,words", [
    ("\\\\nas\\media", "network"),
    ("D:\\", "whole drive"),
    ("media", "drive"),
    ("D:\\a\\..\\b", ". or .."),
    ("D:\\a?b", "valid"),
    ("D:\\a\nb", "control"),
    ("D:\\" + "x" * 300, "longer"),
    (ROOT.lower(), "already"),
])
def test_a_move_to_a_bad_folder_is_refused(host, root, words) -> None:
    with pytest.raises(mf.Refused) as e:
        mf.request_change(root)
    assert words in str(e.value)


def test_a_move_is_refused_while_an_update_runs(host, monkeypatch) -> None:
    monkeypatch.setattr(mf, "_update_running", lambda: True)
    with pytest.raises(mf.Busy):
        mf.request_change("D:\\Media")


def test_a_move_is_refused_off_a_host_that_can(host) -> None:
    host["write"]({**REPORT, "can_change": False})
    with pytest.raises(mf.Refused):
        mf.request_change("D:\\Media")
    host["report"].unlink()
    with pytest.raises(mf.Refused):
        mf.request_change("D:\\Media")


def test_the_hosts_answer_is_shown_and_the_request_taken_back(host) -> None:
    mf.request_change("D:\\Media")
    rid = json.loads((host["dir"] / "request.json").read_text())["id"]
    host["write"]({**REPORT, "change": {"state": "applying", "request_id": rid, "root": "D:\\Media"}})
    st = mf.status()["change"]
    assert st["state"] == "applying" and not st["waiting_for_host"]
    with pytest.raises(mf.Refused):
        mf.request_change("E:\\Other")
    with pytest.raises(mf.Refused):
        mf.cancel_change()
    host["write"]({**REPORT, "root": "D:\\Media", "change": {
        "state": "done", "request_id": rid, "root": "D:\\Media", "previous_root": ROOT}})
    st = mf.status()["change"]
    assert st["state"] == "done" and st["previous_root"] == ROOT
    assert not (host["dir"] / "request.json").exists()
    # Read again from the report alone.
    assert mf.status()["change"]["state"] == "done"


def test_a_refused_move_says_why(host) -> None:
    mf.request_change("D:\\Media")
    rid = json.loads((host["dir"] / "request.json").read_text())["id"]
    host["write"]({**REPORT, "change": {"state": "refused", "request_id": rid, "root": "D:\\Media",
                                        "problem": "D: is not an NTFS drive"}})
    st = mf.status()["change"]
    assert st["state"] == "refused" and "NTFS" in st["problem"]


def test_a_waiting_move_can_be_withdrawn_or_replaced(host) -> None:
    mf.request_change("D:\\Media")
    mf.request_change("E:\\Media")
    assert json.loads((host["dir"] / "request.json").read_text())["root"] == "E:\\Media"
    mf.cancel_change()
    assert not (host["dir"] / "request.json").exists()


def test_the_request_is_the_service_users_alone(host) -> None:
    mf.request_change("D:\\Media")
    mode = (host["dir"] / "request.json").stat().st_mode & 0o777
    import os
    if hasattr(os, "getuid"):
        assert mode == 0o600


# ─── Linux paths in Settings ─────────────────────────────────────────────


def test_linux_dir_must_exist_and_be_readable(host, tmp_path, monkeypatch) -> None:
    host["report"].unlink()
    assert "full path" in mf.linux_dir_problem("music")
    assert "no folder" in mf.linux_dir_problem(str(tmp_path / "nope"))
    ok = tmp_path / "ok"
    ok.mkdir()
    assert mf.linux_dir_problem(str(ok)) is None
    monkeypatch.setattr(mf.os, "access", lambda p, m: False)
    assert "can't read" in mf.linux_dir_problem(str(ok))


def test_videos_in_the_home_may_be_made_at_startup(host, tmp_path, monkeypatch) -> None:
    host["report"].unlink()
    monkeypatch.setattr(mf.Path, "home", classmethod(lambda cls: tmp_path))
    assert mf.linux_dir_problem(str(tmp_path / "Videos"), made_in_home=True) is None
    assert mf.linux_dir_problem(str(tmp_path / "Videos")) is not None


def test_settings_refuse_a_linux_path_on_the_windows_install(host) -> None:
    from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate

    with pytest.raises(ValueError) as e:
        coerce_and_validate(FIELD_BY_NAME["music_dir"], "/srv/other")
    assert "Media folders" in str(e.value)
    with pytest.raises(ValueError):
        coerce_and_validate(FIELD_BY_NAME["videos_dir"], "/srv/other")


def test_sounds_is_not_a_household_folder() -> None:
    from domovoi.config_schema import FIELD_BY_NAME

    assert FIELD_BY_NAME["sounds_dir"].group != "Paths"
    assert {f.name for f in FIELD_BY_NAME.values() if f.group == "Paths"} == set(mf.LINUX_EDITABLE)


# ─── after a move ────────────────────────────────────────────────────────


def test_moved_since_last_boot(host) -> None:
    assert mf.moved_since_last_boot() is False      # first time: recorded
    assert mf.moved_since_last_boot() is False
    host["write"]({**REPORT, "root": "D:\\Media",
                   "folders": {k: v.replace(ROOT, "D:\\Media") for k, v in FOLDERS.items()}})
    assert mf.moved_since_last_boot() is True
    assert mf.moved_since_last_boot() is False


def test_never_moved_on_linux(host) -> None:
    host["report"].unlink()
    assert mf.moved_since_last_boot() is False


@pytest.mark.asyncio
async def test_a_player_on_another_music_folder_is_made_again(monkeypatch) -> None:
    from domovoi import mpd_provisioner as mp

    calls: list[tuple] = []

    async def docker(*args, **kw):
        calls.append(args)
        if args[0] == "ps" or (args[0] == "inspect" and "State" in args[2]):
            return 0, "running\n", ""
        if args[0] == "inspect" and ".Mounts" in args[2]:
            return 0, "/srv/old-music\n", ""
        if args[0] == "inspect":
            return 0, "127.0.0.1\n", ""
        return 0, "", ""

    async def state(name):
        return "running"

    async def ip(name):
        return mp._CONTROL_BIND

    created: list[str] = []

    async def create(**kw):
        created.append(kw["music_dir"])

    async def pin(port):
        return None

    monkeypatch.setattr(mp, "_run_docker", docker)
    monkeypatch.setattr(mp, "_container_state", state)
    monkeypatch.setattr(mp, "_control_port_host_ip", ip)
    monkeypatch.setattr(mp, "_create_container", create)
    monkeypatch.setattr(mp, "_pin_startup_volume", pin)
    await mp._ensure_container(name="domovoi-mpd-x", image="img", control_port=6650, http_port=8050,
                               music_dir="/srv/new-music", volume_name="v")
    assert ("rm", "-f", "domovoi-mpd-x") in calls and created == ["/srv/new-music"]
    calls.clear()
    created.clear()
    await mp._ensure_container(name="domovoi-mpd-x", image="img", control_port=6650, http_port=8050,
                               music_dir="/srv/old-music", volume_name="v")
    assert created == [] and not any(c[0] == "rm" for c in calls)


# ─── the HTTP hops ───────────────────────────────────────────────────────


def _deps(app, path, method):
    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return {d.call.__name__ for d in r.dependant.dependencies}
    raise AssertionError(f"no {method} {path}")


def test_the_hops_are_tiered() -> None:
    from domovoi.main import app as core_app
    from web.backend.main import app as web_app

    assert "require_admin_read" in _deps(core_app, "/v1/admin/media-folders", "GET")
    assert "require_admin_mutation" in _deps(core_app, "/v1/admin/media-folders/open", "POST")
    assert "require_admin_security" in _deps(core_app, "/v1/admin/media-folders/location", "POST")
    assert "require_admin_security" in _deps(core_app, "/v1/admin/media-folders/location/cancel", "POST")
    assert "require_admin_read" in _deps(web_app, "/api/config/media-folders", "GET")
    assert "require_admin_mutation" in _deps(web_app, "/api/config/media-folders/open", "POST")
    assert "require_admin_security" in _deps(web_app, "/api/config/media-folders/location", "POST")
    assert "require_admin_security" in _deps(web_app, "/api/config/media-folders/location/cancel", "POST")


@pytest.mark.asyncio
async def test_the_trays_routes_answer_loopback_only(host) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app as core_app

    rid = mf.request_open("music")["id"]
    far = ASGITransport(app=core_app, client=("192.168.1.30", 5000))
    async with AsyncClient(transport=far, base_url="http://test") as c:
        assert (await c.get("/v1/windows/media/open")).status_code == 403
        assert (await c.post("/v1/windows/media/open/claim", json={"request_id": rid})).status_code == 403
    near = ASGITransport(app=core_app, client=("127.0.0.1", 5000))
    async with AsyncClient(transport=near, base_url="http://test") as c:
        r = await c.get("/v1/windows/media/open", headers={"X-Forwarded-For": "192.168.1.30"})
        assert r.status_code == 403
        r = await c.get("/v1/windows/media/open")
        assert r.status_code == 200 and r.json()["request"]["kind"] == "music"
        assert (await c.post("/v1/windows/media/open/claim", json={"request_id": rid})).status_code == 200
        assert (await c.post("/v1/windows/media/open/claim", json={"request_id": rid})).status_code == 404
        r = await c.post("/v1/windows/media/open/result", json={"request_id": rid, "outcome": "opened"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_the_core_move_endpoint(host, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app as core_app
    from domovoi.tests.auth_testkit import bearer, install_fake_db

    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    async with AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                           base_url="http://test") as c:
        monkeypatch.setattr(mf, "_update_running", lambda: True)
        r = await c.post("/v1/admin/media-folders/location", json={"root": "D:\\Media"}, headers=bearer("admin-token"))
        assert r.status_code == 409
        monkeypatch.setattr(mf, "_update_running", lambda: False)
        r = await c.post("/v1/admin/media-folders/location", json={"root": "\\\\nas\\x"}, headers=bearer("admin-token"))
        assert r.status_code == 400 and "network" in r.json()["detail"]
        r = await c.post("/v1/admin/media-folders/location", json={"root": "D:\\Media"}, headers=bearer("admin-token"))
        assert r.status_code == 200 and r.json()["change"]["requested_root"] == "D:\\Media"
        r = await c.post("/v1/admin/media-folders/open", json={"kind": "podcasts"}, headers=bearer("admin-token"))
        assert r.status_code == 400
        r = await c.post("/v1/admin/media-folders/location", json={"root": "E:\\x"})
        assert r.status_code in (401, 403)


# ─── the Windows side reads the same paths ──────────────────────────────


def test_the_windows_side_reads_the_same_paths() -> None:
    cs = (Path(__file__).resolve().parents[2] / "windows" / "common" / "MediaFolders.cs").read_text(encoding="utf-8")
    assert '"/.domovoi/media_folders/request.json"' in cs
    assert 'ReportDir + "/media.json"' in cs and mf.REPORT_FILES[0][1] == "/var/lib/domovoi-windows/media.json"
    assert "MaxRootLength = 200" in cs and mf.MAX_ROOT == 200
    assert 'MountBase = "/srv/media"' in cs and mf.MOUNT_BASE == "/srv/media"
    for state in mf.CHANGE_STATES:
        assert f'"{state}"' in cs, state
    tray = (Path(__file__).resolve().parents[2] / "windows" / "tray" / "MediaOpen.cs").read_text(encoding="utf-8")
    for outcome in mf.OPEN_OUTCOMES:
        assert f'"{outcome}"' in tray, outcome
    assert '"/v1/windows/media/open"' in tray


def test_fstab_text_matches_provision() -> None:
    sh = (Path(__file__).resolve().parents[2] / "scripts" / "linux" / "provision.sh").read_text(encoding="utf-8")
    assert f'FSTAB_BEGIN="{mf.FSTAB_BEGIN}"' in sh and f'FSTAB_END="{mf.FSTAB_END}"' in sh
    assert f"MEDIA_BASE={mf.MOUNT_BASE}" in sh
