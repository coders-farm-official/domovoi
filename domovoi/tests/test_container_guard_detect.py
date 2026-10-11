"""Every detection rule of the container guard (domovoi/container_guard/
detect.py), and the Docker CLI parsing it rests on. Pure: no Docker, no
database."""

from __future__ import annotations

import json

import pytest

from domovoi.container_guard.detect import (
    DOCKER_KEY,
    RESTART_LOOP_COUNT,
    RestartMemory,
    detect,
    image_matches,
)
from domovoi.container_guard.docker_cli import Claimer, parse_inspect, parse_ps
from domovoi.container_guard.expected import ExpectedSet
from domovoi.container_guard.model import (
    FAILURE,
    KNOWN,
    ONESHOT,
    RUNNING,
    STOPPED,
    TAMPER,
    DockerView,
    Expected,
    Listed,
    Observed,
)

D_PG = "sha256:" + "1" * 64
D_OTHER = "sha256:" + "2" * 64
MPD_ID = "sha256:" + "3" * 64
NOW = 1_800_000_000.0
BOOT = NOW - 3600


def pg(**kw) -> Expected:
    base = dict(name="domovoi-postgres", owner="core", role="postgres", label="the database",
                state=RUNNING, required=True, image_ref=f"postgres:16@{D_PG}",
                image_digests=frozenset({D_PG}), compose_service="postgres")
    base.update(kw)
    return Expected(**base)


def obs(name="domovoi-postgres", **kw) -> Observed:
    base = dict(id="c" * 64, name=name, status="running", running=True,
                config_image=f"postgres:16@{D_PG}", image_id=D_PG)
    base.update(kw)
    return Observed(**base)


def view(*observed: Observed, extra_listed=(), **kw) -> DockerView:
    v = DockerView(ok=True, **kw)
    for o in observed:
        v.listed[o.name] = Listed(id=o.id, name=o.name, compose_project=o.labels.get("p", ""),
                                  compose_service=o.labels.get("s", ""))
        v.containers[o.name] = o
    for listed in extra_listed:
        v.listed[listed.name] = listed
    return v


def expected(*items: Expected, **kw) -> ExpectedSet:
    e = ExpectedSet(**kw)
    e.add(items)
    return e


def run(exp, v, memory=None):
    return detect(exp, v, now_wall=NOW, boot_wall=BOOT, memory=memory)


def kinds(det):
    return sorted((f.cls, f.kind, f.container) for f in det.findings)


# ─── healthy ──────────────────────────────────────────────────────────────

def test_a_healthy_set_finds_nothing() -> None:
    det = run(expected(pg()), view(obs(health="healthy")))
    assert det.findings == [] and det.evaluated == {"domovoi-postgres"} and det.daemon_ok


# ─── F1 daemon ────────────────────────────────────────────────────────────

def test_f1_docker_not_answering_is_one_finding() -> None:
    det = run(expected(pg()), DockerView(ok=False, error="Cannot connect to the Docker daemon"))
    assert kinds(det) == [(FAILURE, "daemon", None)]
    (f,) = det.findings
    assert f.key == DOCKER_KEY and f.severity == "error" and not det.evaluated


def test_f1_needs_something_expected_and_docker_installed() -> None:
    known_only = expected(pg(state=KNOWN))
    assert run(known_only, DockerView(ok=False, error="x")).findings == []
    assert run(expected(pg()), DockerView(ok=False, cli_missing=True, error="x")).findings == []


def test_f1_is_amber_when_only_optional_containers_are_expected() -> None:
    det = run(expected(pg(required=False)), DockerView(ok=False, error="x"))
    assert det.findings[0].severity == "warn"


# ─── F2-F5 ────────────────────────────────────────────────────────────────

def test_f2_missing() -> None:
    det = run(expected(pg()), view())
    (f,) = det.findings
    assert (f.cls, f.kind, f.key) == (FAILURE, "missing", "failure:domovoi-postgres")
    assert f.summary == "The database is not running: its container is missing."
    assert f.severity == "error"


def test_f3_stopped_names_the_exit_code() -> None:
    det = run(expected(pg()), view(obs(status="exited", running=False, exit_code=137)))
    (f,) = det.findings
    assert f.kind == "stopped" and "exit code 137" in f.summary
    assert f.found["status"] == "exited"


def test_f3_paused_counts_as_stopped() -> None:
    det = run(expected(pg()), view(obs(status="paused", paused=True)))
    assert det.findings[0].kind == "stopped" and "paused" in det.findings[0].summary


def test_f4_restarting_now() -> None:
    det = run(expected(pg()), view(obs(status="restarting", running=False, restarting=True)))
    assert det.findings[0].kind == "restarting"


def test_f4_a_crash_loop_counted_over_ten_minutes() -> None:
    memory = RestartMemory()
    exp = expected(pg())
    for i, count in enumerate([0, 1, 2]):
        det = detect(exp, view(obs(restart_count=count)), now_wall=NOW + i * 30, boot_wall=BOOT,
                     memory=memory)
        assert det.findings == []
    det = detect(exp, view(obs(restart_count=RESTART_LOOP_COUNT)), now_wall=NOW + 90,
                 boot_wall=BOOT, memory=memory)
    assert det.findings[0].kind == "restarting"
    # Ten quiet minutes later the loop has aged out of the window.
    det = detect(exp, view(obs(restart_count=RESTART_LOOP_COUNT)), now_wall=NOW + 90 + 601,
                 boot_wall=BOOT, memory=memory)
    assert det.findings == []


def test_f4_a_recreated_container_starts_a_fresh_count() -> None:
    memory = RestartMemory()
    exp = expected(pg())
    detect(exp, view(obs(restart_count=0)), now_wall=NOW, boot_wall=BOOT, memory=memory)
    det = detect(exp, view(obs(id="d" * 64, restart_count=5)), now_wall=NOW + 30, boot_wall=BOOT,
                 memory=memory)
    assert det.findings == []


def test_f5_unhealthy_but_not_starting() -> None:
    assert run(expected(pg()), view(obs(health="unhealthy"))).findings[0].kind == "unhealthy"
    assert run(expected(pg()), view(obs(health="starting"))).findings == []


def test_failure_rules_skip_stopped_and_known_expectations() -> None:
    for state in (STOPPED, KNOWN):
        assert run(expected(pg(state=state)), view()).findings == []


# ─── F6 one-shot ──────────────────────────────────────────────────────────

def flyway(**kw) -> Expected:
    return pg(name="domovoi-flyway", role="flyway", label="the database migrations",
              state=ONESHOT, required=False, compose_service="flyway", **kw)


def _iso(ts: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def test_f6_a_one_shot_that_exited_zero_or_is_gone_is_fine() -> None:
    assert run(expected(flyway()), view()).findings == []
    done = obs(name="domovoi-flyway", status="exited", running=False, exit_code=0,
               finished_at=_iso(BOOT - 10))
    assert run(expected(flyway()), view(done)).findings == []


def test_f6_a_recent_non_zero_exit_is_a_failure_an_old_one_history() -> None:
    recent = obs(name="domovoi-flyway", status="exited", running=False, exit_code=1,
                 finished_at=_iso(BOOT - 60))
    (f,) = run(expected(flyway()), view(recent)).findings
    assert (f.kind, f.severity) == ("oneshot_failed", "warn")
    old = obs(name="domovoi-flyway", status="exited", running=False, exit_code=1,
              finished_at=_iso(BOOT - 7200))
    assert run(expected(flyway()), view(old)).findings == []


# ─── holds ────────────────────────────────────────────────────────────────

def test_a_held_container_is_not_judged_at_all() -> None:
    swapped = obs(image_id=D_OTHER, config_image="evil:1", privileged=True)
    det = run(expected(pg(hold="the room is starting")), view(swapped))
    assert det.findings == [] and det.held == {"domovoi-postgres": "the room is starting"}
    assert "domovoi-postgres" not in det.evaluated


def test_listed_but_gone_before_inspect_is_no_data() -> None:
    v = view(extra_listed=[Listed(id="x", name="domovoi-postgres")])
    det = run(expected(pg()), v)
    assert det.findings == [] and det.evaluated == set()


# ─── T1 image ─────────────────────────────────────────────────────────────

def test_t1_a_different_image_under_a_digest_pin() -> None:
    det = run(expected(pg()), view(obs(image_id=D_OTHER, config_image="postgres:16")))
    (f,) = det.findings
    assert (f.cls, f.kind) == (TAMPER, "image")
    assert f.key == f"tamper:image:domovoi-postgres:{D_OTHER}"
    assert f.expected["digests"] == [D_PG] and f.found["image_id"] == D_OTHER
    assert f.summary == "The database is running a different image than the one Domovoi expects."


def test_t1_the_pin_matches_through_repo_digests_on_the_classic_store() -> None:
    config_id = "sha256:" + "9" * 64
    v = view(obs(image_id=config_id), repo_digests={config_id: (f"postgres@{D_PG}",)})
    assert run(expected(pg()), v).findings == []


def test_t1_a_stopped_expectation_still_checks_a_running_container() -> None:
    det = run(expected(pg(state=STOPPED)), view(obs(image_id=D_OTHER)))
    assert kinds(det) == [(TAMPER, "image", "domovoi-postgres")]
    # ...and a known one too (M4): nothing expected of it is no licence to
    # run another image.
    assert kinds(run(expected(pg(state=KNOWN)), view(obs(image_id=D_OTHER)))) == [
        (TAMPER, "image", "domovoi-postgres")]


def test_t1_a_stopped_container_s_image_is_not_judged() -> None:
    det = run(expected(pg(state=STOPPED)), view(obs(image_id=D_OTHER, running=False, status="exited")))
    assert det.findings == []


def test_t1_the_music_player_runs_the_image_the_core_built() -> None:
    mpd = Expected(name="domovoi-mpd-kitchen", owner="core", role="mpd", label="the music player for kitchen",
                   state=RUNNING, image_ref="domovoi-mpd:latest", image_id=MPD_ID)
    ok = obs(name="domovoi-mpd-kitchen", image_id=MPD_ID, config_image="domovoi-mpd:latest")
    assert run(expected(mpd), view(ok)).findings == []
    swapped = obs(name="domovoi-mpd-kitchen", image_id=D_OTHER, config_image="domovoi-mpd:latest")
    assert kinds(run(expected(mpd), view(swapped))) == [(TAMPER, "image", "domovoi-mpd-kitchen")]


def test_t1_a_tag_only_pin_compares_the_reference() -> None:
    tag = Expected(name="domovoi-vid", owner="plugin:vid", role="plugin", label="the video server",
                   state=RUNNING, image_ref="jellyfin/jellyfin:latest")
    assert image_matches(tag, obs(name="domovoi-vid", config_image="jellyfin/jellyfin"), DockerView(ok=True))
    assert not image_matches(tag, obs(name="domovoi-vid", config_image="evil/jellyfin:latest"),
                             DockerView(ok=True))


def test_t1_the_music_player_tag_replaced() -> None:
    exp = expected(mpd_prefix="domovoi-mpd-", mpd_tag_ref="domovoi-mpd:latest", mpd_image_id=MPD_ID)
    det = run(exp, view(tag_ids={"domovoi-mpd:latest": D_OTHER}))
    (f,) = det.findings
    assert (f.kind, f.key) == ("image", f"tamper:mpd-image:{D_OTHER}")
    assert run(exp, view(tag_ids={"domovoi-mpd:latest": MPD_ID})).findings == []
    # A missing tag (removed to force a rebuild) is not tampering.
    assert run(exp, view(tag_ids={"domovoi-mpd:latest": None})).findings == []
    exp.mpd_tag_hold = "being built"
    assert run(exp, view(tag_ids={"domovoi-mpd:latest": D_OTHER})).findings == []


# ─── T3 config ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("kw", "aspect"),
    [
        ({"privileged": True}, "privileged"),
        ({"network_mode": "host"}, "host_network"),
        ({"pid_mode": "host"}, "host_pid"),
        ({"mounts": (("/var/run/docker.sock", "/var/run/docker.sock"),)}, "docker_socket"),
        ({"mounts": (("\\\\.\\pipe\\docker_engine", "/sock"),)}, "docker_socket"),
        ({"mounts": (("/", "/host"),)}, "root_mount"),
        ({"mounts": (("C:\\", "/host"),)}, "root_mount"),
    ],
)
def test_t3_security_settings_domovoi_never_gives(kw, aspect) -> None:
    det = run(expected(pg()), view(obs(**kw)))
    (f,) = det.findings
    assert (f.cls, f.kind) == (TAMPER, "config") and f.found["settings"] == [aspect]


def test_t3_a_declared_allowance_is_respected() -> None:
    det = run(expected(pg(allow=frozenset({"host_network"}))), view(obs(network_mode="host")))
    assert det.findings == []


def test_t3_applies_to_known_containers_too() -> None:
    det = run(expected(pg(state=KNOWN)), view(obs(privileged=True)))
    assert kinds(det) == [(TAMPER, "config", "domovoi-postgres")]


def test_t3_ordinary_mounts_are_fine() -> None:
    det = run(expected(pg()), view(obs(mounts=(("/home/u/Music", "/music"), ("vol", "/var/lib/mpd")))))
    assert det.findings == []


# ─── T2 unknown ───────────────────────────────────────────────────────────

def _compose_set(**kw) -> ExpectedSet:
    return expected(pg(), compose_project="domovoi",
                    compose_services=frozenset({"postgres", "flyway", "flyway-test", "searxng", "letta"}),
                    mpd_prefix="domovoi-mpd-", mpd_tag_ref="domovoi-mpd:latest", mpd_image_id=MPD_ID, **kw)


def test_t2_an_unknown_compose_service_running() -> None:
    stranger = obs(name="domovoi-helper", id="e" * 64, config_image="evil/helper:1", image_id=D_OTHER,
                   labels={"p": "domovoi", "s": "helper"})
    det = run(_compose_set(), view(obs(), stranger))
    (f,) = det.findings
    assert (f.cls, f.kind, f.container) == (TAMPER, "unknown", "domovoi-helper")
    assert f.key == f"tamper:unknown:domovoi-helper:{D_OTHER}"


def test_t2_a_stopped_unknown_does_nothing_until_it_starts() -> None:
    stranger = obs(name="domovoi-helper", id="e" * 64, running=False, status="exited",
                   labels={"p": "domovoi", "s": "helper"})
    assert run(_compose_set(), view(obs(), stranger)).findings == []


def test_t2_a_one_off_run_and_a_retired_service_are_domovoi_s() -> None:
    run_ = obs(name="domovoi-flyway-test-run-1a2b", id="f" * 64, labels={"p": "domovoi", "s": "flyway-test"})
    office = obs(name="domovoi-onlyoffice", id="a" * 64, config_image="onlyoffice/documentserver",
                 image_id=D_OTHER, labels={"p": "domovoi", "s": "onlyoffice"})
    det = run(_compose_set(), view(obs(), run_, office))
    assert det.findings == []


def test_t2_an_unclaimed_music_player_running_another_image() -> None:
    stray = obs(name="domovoi-mpd-backdoor", id="b" * 64, config_image="alpine:3", image_id=D_OTHER)
    (f,) = run(_compose_set(), view(obs(), stray)).findings
    assert (f.kind, f.container) == ("unknown", "domovoi-mpd-backdoor")


def test_t2_a_removed_room_s_player_left_running_is_only_unrecognized() -> None:
    leftover = obs(name="domovoi-mpd-oldroom", id="b" * 64, config_image="domovoi-mpd:latest", image_id=MPD_ID)
    det = run(_compose_set(), view(obs(), leftover))
    assert det.findings == [] and det.unrecognized == ["domovoi-mpd-oldroom"]


def test_t2_other_installs_and_lookalikes_are_never_incidents() -> None:
    # A test harness's music players, an old plugin's server, another
    # project entirely: listed as unrecognized (when they look like ours),
    # never inspected, never an incident.
    det = run(_compose_set(), view(
        obs(),
        extra_listed=[
            Listed(id="1", name="domovoi-ft-mpd-kitchen"),
            Listed(id="2", name="domovoi-jellyfin"),
            Listed(id="3", name="scooped-pg-funnel-dev"),
            Listed(id="4", name="otherproject-mpd-kitchen", compose_project="otherproject"),
        ],
    ))
    assert det.findings == []
    assert det.unrecognized == ["domovoi-ft-mpd-kitchen", "domovoi-jellyfin"]


def test_t2_needs_the_compose_stack_to_be_ours() -> None:
    stranger = obs(name="domovoi-helper", id="e" * 64, image_id=D_OTHER, labels={"p": "domovoi", "s": "helper"})
    exp = expected(pg(), compose_project=None)
    det = run(exp, view(obs(), stranger))
    assert det.findings == [] and det.unrecognized == ["domovoi-helper"]


# ─── the CLI's output ─────────────────────────────────────────────────────

def test_parse_ps_reads_names_and_compose_labels() -> None:
    out = "abc\tdomovoi-postgres\tdomovoi\tpostgres\trunning\ndef\t/x,y\t\t\texited\n\n"
    listed = parse_ps(out)
    assert listed["domovoi-postgres"].compose_service == "postgres"
    assert listed["x"].state == "exited"


def test_parse_inspect_reads_what_the_rules_need() -> None:
    doc = [{
        "Id": "c" * 64, "Name": "/domovoi-postgres", "RestartCount": 2, "Image": D_PG,
        "State": {"Status": "running", "Running": True, "ExitCode": 0, "StartedAt": "2026-10-11T00:00:00Z",
                  "Health": {"Status": "healthy"}},
        "Config": {"Image": "postgres:16", "Labels": {"com.docker.compose.project": "domovoi"}},
        "HostConfig": {"Privileged": False, "NetworkMode": "bridge", "PidMode": ""},
        "Mounts": [{"Type": "volume", "Source": "/var/lib/docker/volumes/x", "Destination": "/data"}],
    }]
    (o,) = parse_inspect(json.dumps(doc))
    assert (o.name, o.running, o.restart_count, o.health, o.image_id) == (
        "domovoi-postgres", True, 2, "healthy", D_PG)
    assert o.security() == []
    assert parse_inspect("not json") == []


def test_the_claimer_inspects_only_what_is_domovoi_s() -> None:
    claim = Claimer({"domovoi-postgres"}, compose_project="domovoi", prefixes=("domovoi-mpd-",))
    assert claim(Listed(id="1", name="domovoi-postgres"))
    assert claim(Listed(id="2", name="anything", compose_project="domovoi"))
    assert claim(Listed(id="3", name="domovoi-mpd-k"))
    assert not claim(Listed(id="4", name="scooped-pg-funnel-dev"))
    assert not claim(Listed(id="5", name="domovoi-ft-mpd-k"))
