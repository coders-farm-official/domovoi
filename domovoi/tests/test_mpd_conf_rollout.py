"""Room players stream without song tags, and houses that already have
rooms get the new settings once (2026-11-17).

MPD 0.23.12's httpd output swaps its metadata page at every song change
without its lock while a newly connecting listener copies it (fixed
upstream in October 2026, "httpd: add missing mutex lock"); the rooms'
speakers reconnect after every voice turn. ``tags "no"`` on that output
means MPD never sends it a tag, so the racy code never runs.

mpd.conf is mounted when a room's container is created, so every container
carries a fingerprint of the file it was made with
(``mpd_provisioner.CONF_LABEL``). The core's start makes again a room whose
fingerprint differs while it is not playing; a playing room waits for the
playback sweeper, which makes it again once it stops. Docker is a fake
throughout; no database.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from domovoi import mpd_provisioner
from domovoi.container_guard import holds
from domovoi.container_guard.detect import detect
from domovoi.container_guard.expected import ExpectedSet, mpd_expectations
from domovoi.container_guard.model import DockerView, Listed, Observed

CONF = Path(mpd_provisioner.__file__).resolve().parent / "mpd.conf"
MPD_ID = "sha256:" + "a" * 64
NAME = "domovoi-mpd-office"
MOUNTS = (("/var/lib/docker/volumes/domovoi-mpd-office-data/_data", "/var/lib/mpd"),
          ("/srv/media/music", "/music"), ("/opt/domovoi/domovoi/mpd.conf", "/etc/mpd.conf"))


def _blocks(conf: str) -> list[dict[str, str]]:
    """Each ``audio_output { ... }`` block as {key: value}, comments out."""
    out = []
    for body in re.findall(r"audio_output\s*\{(.*?)\}", conf, re.S):
        kv = {}
        for line in body.splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.match(r'(\w+)\s+"([^"]*)"', line)
            if m:
                assert m.group(1) not in kv, f"{m.group(1)} set twice"
                kv[m.group(1)] = m.group(2)
        out.append(kv)
    return out


# ─── the setting ──────────────────────────────────────────────────────────

def test_the_stream_output_sends_no_tags() -> None:
    (httpd,) = [b for b in _blocks(CONF.read_text(encoding="utf-8")) if b.get("type") == "httpd"]
    assert httpd["tags"] == "no"
    # The lame encoder embeds no tags of its own: with tags on, MPD would
    # take the Icy-Metadata path, whose page swap is the race.
    assert httpd["encoder"] == "lame" and httpd["always_on"] == "yes"


def test_the_fingerprint_follows_the_file_and_ignores_line_endings(monkeypatch, tmp_path) -> None:
    a = tmp_path / "a.conf"
    a.write_bytes(b'tags "no"\n')
    monkeypatch.setattr(mpd_provisioner, "_MPD_CONF", a)
    fp = mpd_provisioner.conf_fingerprint()
    a.write_bytes(b'tags "no"\r\n')
    assert mpd_provisioner.conf_fingerprint() == fp
    a.write_bytes(b'tags "yes"\n')
    assert mpd_provisioner.conf_fingerprint() != fp


# ─── a fake Docker ────────────────────────────────────────────────────────

def player(cid: str = "old", *, label: str | None = None, status: str = "running", **kw) -> Observed:
    base = dict(id=cid, name=NAME, status=status, running=status == "running",
                config_image="domovoi-mpd:latest", image_id=MPD_ID, mounts=MOUNTS,
                restart_policy="unless-stopped",
                labels={} if label is None else {mpd_provisioner.CONF_LABEL: label})
    base.update(kw)
    return Observed(**base)


class FakeDocker:
    """One room's container, every docker call recorded."""

    def __init__(self, monkeypatch, tmp_path, *, label: str | None = "", status: str = "running",
                 playing: bool | None = False) -> None:
        self.obs: Observed | None = player(label=label or None, status=status) \
            if label is not None else None
        self.labels_unreadable = False
        self.calls: list[tuple[str, ...]] = []
        self.playing = playing
        self.after: list[str | None] = []
        self.frozen: str | None = None
        self.trusted = {MPD_ID}
        self.n = 0
        monkeypatch.setattr(mpd_provisioner, "_run_docker", self.run)
        monkeypatch.setattr(mpd_provisioner, "_inspect_room", self.inspect)
        monkeypatch.setattr(mpd_provisioner, "_room_playing", self.is_playing)
        monkeypatch.setattr(mpd_provisioner, "_after_start", self.after_start)
        monkeypatch.setattr(mpd_provisioner, "frozen_by", self.frozen_by)
        monkeypatch.setattr(mpd_provisioner, "_trusted_image_ids", self.trusted_ids)
        monkeypatch.setattr(mpd_provisioner.settings, "podcasts_dir", str(tmp_path / "none1"))
        monkeypatch.setattr(mpd_provisioner.settings, "audiobooks_dir", str(tmp_path / "none2"))

    def verbs(self) -> list[str]:
        return [c[0] if c[0] != "inspect" else "inspect" for c in self.calls
                if c[0] in ("stop", "rm", "run", "start")]

    async def run(self, *args: str, timeout: float = 60.0):
        self.calls.append(args)
        if args[0] == "inspect":
            if self.obs is None:
                return 1, "", "Error: No such object"
            fmt = args[2]
            if fmt == "{{.State.Status}}":
                return 0, self.obs.status + "\n", ""
            if fmt == "{{json .HostConfig.PortBindings}}":
                return 0, '{"6600/tcp":[{"HostIp":"127.0.0.1","HostPort":"6650"}]}\n', ""
            if fmt == "{{json .Config.Labels}}":
                if self.labels_unreadable:
                    return 1, "", "daemon unhappy"
                import json
                return 0, json.dumps(self.obs.labels or None) + "\n", ""
            return 0, "\n", ""
        if args[0] == "stop":
            assert self.obs is not None
            self.obs = player(self.obs.id, label=self.obs.labels.get(mpd_provisioner.CONF_LABEL),
                              status="exited")
            return 0, NAME + "\n", ""
        if args[0] == "rm":
            self.obs = None
            return 0, NAME + "\n", ""
        if args[0] == "run":
            label = args[args.index("--label") + 1].split("=", 1)[1]
            self.n += 1
            self.obs = player(f"new{self.n}", label=label)
            return 0, "id\n", ""
        if args[0] == "start":
            assert self.obs is not None
            self.obs = player(self.obs.id, label=self.obs.labels.get(mpd_provisioner.CONF_LABEL))
            return 0, "", ""
        raise AssertionError(args)

    async def inspect(self, target: str):
        return self.obs

    async def is_playing(self, control_port: int):
        return self.playing

    async def after_start(self, room_id, control_port):
        self.after.append(room_id)

    async def frozen_by(self):
        return self.frozen

    async def trusted_ids(self):
        return set(self.trusted)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    # The quiet period has its own test; elsewhere a quiet look is enough.
    monkeypatch.setattr(mpd_provisioner, "CONF_QUIET_SEC", 0.0)
    mpd_provisioner.reset_self_heal()
    holds.clear()
    yield
    mpd_provisioner.reset_self_heal()
    holds.clear()


async def ensure(tmp_path, **kw) -> None:
    await mpd_provisioner._ensure_container(
        name=NAME, image="domovoi-mpd:latest", control_port=6650, http_port=8050,
        music_dir=str(tmp_path), volume_name="domovoi-mpd-office-data", room_id="office",
        check_conf=True, **kw)


def _run_args(docker: FakeDocker) -> tuple[str, ...]:
    (run,) = [c for c in docker.calls if c[0] == "run"]
    return run


# ─── the container is labelled with what it was made with ─────────────────

async def test_a_new_room_carries_today_s_fingerprint(monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label=None)
    await ensure(tmp_path)
    run = _run_args(docker)
    assert run[run.index("--label") + 1] == (
        f"{mpd_provisioner.CONF_LABEL}={mpd_provisioner.conf_fingerprint()}")


# ─── an older mpd.conf ────────────────────────────────────────────────────

async def test_an_idle_room_on_an_older_conf_is_made_again_keeping_its_volume(
        monkeypatch, tmp_path, caplog) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="0123456789abcdef")
    with caplog.at_level(logging.WARNING, logger="domovoi.mpd_provisioner"):
        await ensure(tmp_path)
    # Stopped politely first (MPD writes its state file: the queue and the
    # place), then removed, then made again.
    assert docker.verbs() == ["stop", "rm", "run"]
    (stop,) = [c for c in docker.calls if c[0] == "stop"]
    assert stop[1] == "-t" and int(stop[2]) >= 10
    run = _run_args(docker)
    # The same named data volume: state file, database, playlists.
    assert "domovoi-mpd-office-data:/var/lib/mpd" in run
    assert run[run.index("--name") + 1] == NAME
    assert docker.obs.labels[mpd_provisioner.CONF_LABEL] == mpd_provisioner.conf_fingerprint()
    assert docker.after == ["office"]
    assert mpd_provisioner.conf_pending() == {}
    lines = [r.getMessage() for r in caplog.records if "room=office" in r.getMessage()]
    assert len(lines) == 1 and "made again with the new mpd.conf" in lines[0]


async def test_a_room_from_before_the_label_is_made_again(monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="")       # no label at all
    await ensure(tmp_path)
    assert docker.verbs() == ["stop", "rm", "run"]


async def test_a_paused_room_counts_as_idle(monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="old", playing=False)
    await ensure(tmp_path)
    assert docker.verbs() == ["stop", "rm", "run"]


async def test_a_playing_room_is_left_alone_and_done_once_it_stops(monkeypatch, tmp_path, caplog) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="old", playing=True)
    with caplog.at_level(logging.WARNING, logger="domovoi.mpd_provisioner"):
        await ensure(tmp_path)
        assert docker.verbs() == []                       # the music plays on
        assert mpd_provisioner.conf_pending() == {"office": (6650, 8050, NAME)}
        # The sweeper's ticks while it still plays: nothing.
        assert await mpd_provisioner.retry_conf_pending() is None
        assert await mpd_provisioner.retry_conf_pending() is None
        assert docker.verbs() == []
        docker.playing = False                             # the queue ran out
        assert await mpd_provisioner.retry_conf_pending() == "office"
    assert docker.verbs() == ["stop", "rm", "run"]
    assert mpd_provisioner.conf_pending() == {}
    assert await mpd_provisioner.retry_conf_pending() is None
    msgs = [r.getMessage() for r in caplog.records if "room=office" in r.getMessage()]
    assert len(msgs) == 2                       # "made again once ...", "made again"
    assert "once it stops playing" in msgs[0]


async def test_the_sweeper_waits_for_a_minute_of_quiet(monkeypatch, tmp_path) -> None:
    """A play starts paused until the speaker is ready, and a pause may be
    resumed: a room is made again only once it has been quiet a while."""
    monkeypatch.setattr(mpd_provisioner, "CONF_QUIET_SEC", 60.0)
    docker = FakeDocker(monkeypatch, tmp_path, label="old", playing=True)
    await ensure(tmp_path)
    docker.playing = False
    assert await mpd_provisioner.retry_conf_pending() is None      # quiet starts now
    mpd_provisioner._conf_quiet_since["office"] -= 30
    assert await mpd_provisioner.retry_conf_pending() is None      # 30 s: not yet
    docker.playing = True                                          # resumed
    assert await mpd_provisioner.retry_conf_pending() is None
    assert "office" not in mpd_provisioner._conf_quiet_since       # the clock starts over
    docker.playing = False
    assert await mpd_provisioner.retry_conf_pending() is None
    mpd_provisioner._conf_quiet_since["office"] -= 61
    assert await mpd_provisioner.retry_conf_pending() == "office"
    assert docker.verbs() == ["stop", "rm", "run"]
    assert mpd_provisioner._conf_quiet_since == {}


async def test_a_room_whose_player_can_t_say_is_left_for_later(monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="old", playing=None)
    await ensure(tmp_path)
    assert docker.verbs() == [] and "office" in mpd_provisioner.conf_pending()


async def test_a_stopped_container_on_an_older_conf_is_made_again_without_asking(
        monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="old", status="exited", playing=None)
    await ensure(tmp_path)
    assert docker.verbs() == ["stop", "rm", "run"]


async def test_today_s_fingerprint_changes_nothing(monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label=mpd_provisioner.conf_fingerprint(), playing=False)
    await ensure(tmp_path)
    assert docker.verbs() == [] and docker.after == []
    assert mpd_provisioner.conf_pending() == {}


async def test_labels_docker_can_t_read_change_nothing(monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="old")
    docker.labels_unreadable = True
    await ensure(tmp_path)
    assert docker.verbs() == [] and mpd_provisioner.conf_pending() == {}


async def test_without_check_conf_a_running_room_is_never_touched(monkeypatch, tmp_path) -> None:
    """The on-demand paths (a satellite connecting, a heal) leave it be."""
    docker = FakeDocker(monkeypatch, tmp_path, label="old")
    await mpd_provisioner._ensure_container(
        name=NAME, image="domovoi-mpd:latest", control_port=6650, http_port=8050,
        music_dir=str(tmp_path), volume_name="domovoi-mpd-office-data", room_id="office")
    assert docker.verbs() == []


async def test_nothing_is_made_again_while_frozen(monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="old")
    docker.frozen = "incident #3"
    await ensure(tmp_path)
    assert docker.verbs() == [] and "office" in mpd_provisioner.conf_pending()
    docker.frozen = None
    assert await mpd_provisioner.retry_conf_pending() == "office"


async def test_a_container_that_isn_t_domovoi_s_player_is_never_touched(monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="old")
    docker.trusted = {"sha256:" + "b" * 64}           # another image under the room's name
    await ensure(tmp_path)
    assert docker.verbs() == [] and mpd_provisioner.conf_pending() == {}


async def test_a_held_room_waits(monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="old", playing=True)
    await ensure(tmp_path)
    docker.playing = False
    with holds.holding("room:office", "being removed", settle_sec=0):
        assert await mpd_provisioner.retry_conf_pending() is None
    assert docker.verbs() == []


async def test_the_sweeper_makes_one_room_again_per_tick(monkeypatch, tmp_path) -> None:
    docker = FakeDocker(monkeypatch, tmp_path, label="old", playing=False)
    done: list[str] = []

    async def fake_ensure(**kw):
        mpd_provisioner._conf_pending.pop(kw["room_id"], None)
        done.append(kw["room_id"])

    monkeypatch.setattr(mpd_provisioner, "_ensure_container", fake_ensure)
    mpd_provisioner._conf_pending.update({"a": (6650, 8050, NAME), "b": (6651, 8051, NAME)})
    assert await mpd_provisioner.retry_conf_pending() == "a"
    assert done == ["a"] and list(mpd_provisioner.conf_pending()) == ["b"]
    assert await mpd_provisioner.retry_conf_pending() == "b"


async def test_the_sweeper_tick_asks_for_the_retry(monkeypatch) -> None:
    from domovoi import music_groups
    from domovoi.workers.playback_state_sweeper import PlaybackStateSweeper

    asked: list[bool] = []

    async def retry():
        asked.append(True)

    async def upkeep(_app):
        return None

    monkeypatch.setattr(mpd_provisioner, "retry_conf_pending", retry)
    monkeypatch.setattr(music_groups, "upkeep", upkeep)
    sweeper = PlaybackStateSweeper(app=None)

    async def nothing(*_a, **_k):
        return 0

    monkeypatch.setattr(sweeper, "_sweep_stamps", nothing)
    monkeypatch.setattr(sweeper, "_sweep_pi_music", nothing)
    await sweeper.tick()
    assert asked == [True]


async def test_retiring_a_room_forgets_its_pending_recreate(monkeypatch) -> None:
    mpd_provisioner._conf_pending["office"] = (6650, 8050, NAME)

    async def gone(_name):
        return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *_a, **_k):
            class R:
                rowcount = 1
            return R()

    import domovoi.db.session as db_session

    monkeypatch.setattr(mpd_provisioner, "_container_state", gone)
    monkeypatch.setattr(db_session, "session_scope", lambda: Session())
    await mpd_provisioner.remove_room("office")
    assert mpd_provisioner.conf_pending() == {}


# ─── the container guard ──────────────────────────────────────────────────

def _view(*observed: Observed) -> DockerView:
    view = DockerView(ok=True)
    for o in observed:
        view.listed[o.name] = Listed(id=o.id, name=o.name)
        view.containers[o.name] = o
    return view


def _expected() -> ExpectedSet:
    exp = ExpectedSet()
    exp.add(mpd_expectations([("office", NAME)], tag_ref="domovoi-mpd:latest", image_id=MPD_ID,
                             warm_done=True))
    return exp


def test_the_guard_holds_the_room_while_it_is_made_again() -> None:
    with holds.holding("room:office", "being made again with the new mpd.conf", settle_sec=60.0):
        det = detect(_expected(), _view(), now_wall=1.8e9, boot_wall=0.0)
    assert det.findings == [] and NAME in det.held


def test_the_guard_accepts_the_container_made_again() -> None:
    """A new ID, the new label, the same name, image, mounts and restart
    policy: the room's player, nothing to report; and the self-heal would
    still start it (room_player_problem)."""
    made_again = player("new1", label=mpd_provisioner.conf_fingerprint())
    det = detect(_expected(), _view(made_again), now_wall=1.8e9, boot_wall=0.0)
    assert det.findings == [] and NAME in det.evaluated
    assert mpd_provisioner.room_player_problem(made_again, {MPD_ID}) is None
