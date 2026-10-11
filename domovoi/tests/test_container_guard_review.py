"""The independent review's findings on the container guard (2026-10-11,
design-notes/container-guard-review/REVIEW.md), one test per finding.

Each was a reproducing scratch test; here each asserts the fixed
behaviour. DB-backed ones run on the test lane like the rest of the
guard's tests.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from domovoi import egress, git_version, self_restart
from domovoi.container_guard import engine as cg_engine
from domovoi.container_guard import freeze, holds, store
from domovoi.container_guard.compose import ComposeService
from domovoi.container_guard.detect import RestartMemory, detect
from domovoi.container_guard.docker_cli import DockerCli
from domovoi.container_guard.engine import GuardConfig, gather_expected
from domovoi.container_guard.expected import (
    PLUGIN_BUSY_MAX_SEC,
    PLUGIN_PULL_MAX_SEC,
    RESOLVER_FAIL_MAX_SEC,
    ExpectedSet,
    compose_expectations,
    declared_containers,
    failing_resolvers,
    mpd_expectations,
    plugin_expectation,
    plugin_expectations,
)
from domovoi.container_guard.model import FAILURE, KNOWN, TAMPER, DockerView, Finding, Listed, Observed
from domovoi.db.session import session_scope
from domovoi.sdk import ContainerExpectation
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_container_guard_engine import (  # noqa: F401 (fixture)
    D_OTHER,
    guard_env,
    running_db,
)

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
OLD = "sha256:" + "0" * 64
NEW = "sha256:" + "9" * 64


def obs(name, image_id, config_image, **kw):
    base = dict(id=(name + "x" * 64)[:64], name=name, status="running", running=True,
                image_id=image_id, config_image=config_image)
    base.update(kw)
    return Observed(**base)


def view_of(*items, repo=None, tags=None, listed_extra=(), unknown=()):
    v = DockerView(ok=True)
    for o in items:
        v.listed[o.name] = Listed(id=o.id, name=o.name)
        v.containers[o.name] = o
    for lst in listed_extra:
        v.listed[lst.name] = lst
    v.repo_digests.update(repo or {})
    v.tag_ids.update(tags or {})
    v.unknown_images.update(unknown)
    return v


def findings(exp, v, **kw):
    det = detect(exp, v, now_wall=2e9, boot_wall=2e9, **kw)
    return sorted((f.cls, f.kind, f.container, f.severity) for f in det.findings)


@pytest.fixture(autouse=True)
def _clean_holds():
    holds.clear()
    yield
    holds.clear()


# ─── H1: the update unit's music player rebuild is adopted ────────────────

@requires_db
async def test_h1_an_in_flight_update_s_rebuild_is_adopted_and_nothing_freezes(guard_env, monkeypatch):
    guard, docker, clock, expected = guard_env
    cg_engine.reset_guard(guard)
    try:
        assert await cg_engine.note_mpd_image(A, built=False) == "adopted"
        monkeypatch.setattr(git_version, "boot_sha", lambda: "abc1234")
        full = "abc1234" + "1" * 33
        # What apply-update.sh has written when it starts the core: running,
        # before rebuild-mpd and start-services finish.
        inflight = {"status": "running", "mode": "update", "mpd_changed": True,
                    "to_sha": full, "from_sha": "f" * 40, "started_at": "2026-10-11T12:00:00Z",
                    "finished_at": None}
        monkeypatch.setattr(git_version, "load_last_update", lambda: (inflight, None))
        assert await cg_engine.note_mpd_image(B, built=False) == "update"
        # The later plain restart through the unit (mpd_changed false): same image.
        plain = dict(inflight, mode="restart", mpd_changed=False)
        monkeypatch.setattr(git_version, "load_last_update", lambda: (plain, None))
        assert await cg_engine.note_mpd_image(B, built=False) == "same"
        async with session_scope() as s:
            b = await store.get_baseline(s, cg_engine.MPD_BASELINE_KEY)
        assert (b["value"], b["source"]) == (B, "update-unit:in-flight")

        exp = ExpectedSet(mpd_prefix="domovoi-mpd-", mpd_tag_ref="domovoi-mpd:latest", mpd_image_id=B)
        exp.add(mpd_expectations([("kitchen", "domovoi-mpd-kitchen")],
                                 tag_ref="domovoi-mpd:latest", image_id=B, warm_done=True))
        expected["set"] = exp
        docker.view = view_of(obs("domovoi-mpd-kitchen", B, "domovoi-mpd:latest"),
                              tags={"domovoi-mpd:latest": B})
        for _ in range(3):
            clock.t += 30
            await guard.check(force=True)
        async with session_scope() as s:
            assert await store.open_incidents(s) == []
        freeze.forget()
        assert not await freeze.is_frozen()
    finally:
        cg_engine.reset_guard(None)


@requires_db
async def test_h1_a_rollback_rebuild_is_adopted_too(guard_env, monkeypatch):
    guard, *_ = guard_env
    cg_engine.reset_guard(guard)
    try:
        await cg_engine.note_mpd_image(A, built=False)
        monkeypatch.setattr(git_version, "boot_sha", lambda: "0ld5ha1")
        rollback = {"status": "running", "mpd_changed": True, "to_sha": "n" * 40,
                    "from_sha": "0ld5ha1" + "2" * 33}
        monkeypatch.setattr(git_version, "load_last_update", lambda: (rollback, None))
        assert await cg_engine.note_mpd_image(B, built=False) == "update"
    finally:
        cg_engine.reset_guard(None)


@requires_db
async def test_h1_an_unreadable_record_on_an_update_unit_host_adopts_without_paging(guard_env, monkeypatch):
    guard, *_ = guard_env
    cg_engine.reset_guard(guard)
    try:
        await cg_engine.note_mpd_image(A, built=False)
        monkeypatch.setattr(git_version, "load_last_update", lambda: (None, "unreadable"))
        monkeypatch.setattr(self_restart, "update_unit_installed", lambda: True)
        monkeypatch.setattr(self_restart, "update_unit_state", lambda: "inactive")
        assert await cg_engine.note_mpd_image(B, built=False) == "unverified"
        assert "adopted unverified" in guard.mpd_note
        async with session_scope() as s:
            b = await store.get_baseline(s, cg_engine.MPD_BASELINE_KEY)
        assert (b["value"], b["source"]) == (B, "unverified:record-missing:unreadable")
        # A host with no update unit has nobody else who rebuilds: reported.
        monkeypatch.setattr(self_restart, "update_unit_installed", lambda: False)
        assert await cg_engine.note_mpd_image(A, built=False) == "changed"
        # A readable record that doesn't vouch for a rebuild is no evidence.
        monkeypatch.setattr(self_restart, "update_unit_installed", lambda: True)
        quiet = {"status": "ok", "mpd_changed": False, "finished_at": "2026-10-11T12:00:00Z"}
        monkeypatch.setattr(git_version, "load_last_update", lambda: (quiet, None))
        assert await cg_engine.note_mpd_image(A, built=False) == "changed"
    finally:
        cg_engine.reset_guard(None)


# ─── H2: an image from an earlier pin is "outdated", not tampering ────────

def _compose(image, *, chat=True):
    boot = {"letta": ComposeService("letta", image, "domovoi-letta", ("chat",)),
            "postgres": ComposeService("postgres", f"postgres:16.15@{NEW}", "domovoi-postgres")}
    exp = ExpectedSet(compose_project="domovoi", compose_services=frozenset(boot))
    exp.add(compose_expectations(boot, boot, project="domovoi", internet_policy="",
                                 manage_searxng=True, chat_mode=chat))
    return exp


def test_h2_letta_on_the_previous_pin_is_outdated_amber():
    exp = _compose(f"letta/letta:0.34.4@{NEW}")
    v = view_of(obs("domovoi-letta", OLD, "letta/letta:latest"),
                obs("domovoi-postgres", NEW, "postgres:16.15"),
                repo={OLD: ("letta/letta@" + OLD,)})
    assert findings(exp, v) == [(FAILURE, "outdated", "domovoi-letta", "warn")]
    (f,) = detect(exp, v, now_wall=2e9, boot_wall=2e9).findings
    assert "recreate it" in f.summary and "docker compose up -d letta" in f.expected["fix"]


def test_h2_postgres_after_a_pull_on_a_host_without_the_update_unit():
    exp = _compose(f"letta/letta:0.34.4@{NEW}", chat=False)
    v = view_of(obs("domovoi-postgres", OLD, "postgres:16"), repo={OLD: ("postgres@" + OLD,)})
    assert findings(exp, v) == [(FAILURE, "outdated", "domovoi-postgres", "warn")]


def test_h2_a_foreign_repository_or_no_registry_provenance_is_tampering():
    exp = _compose(f"letta/letta:0.34.4@{NEW}", chat=False)
    foreign = view_of(obs("domovoi-postgres", OLD, "evil/pg:1"), repo={OLD: ("evil/pg@" + OLD,)})
    assert findings(exp, foreign) == [(TAMPER, "image", "domovoi-postgres", "error")]
    # Same name, built locally (no repo digest): not from the registry.
    local = view_of(obs("domovoi-postgres", OLD, "postgres:16.15"), repo={OLD: ()})
    assert findings(exp, local) == [(TAMPER, "image", "domovoi-postgres", "error")]


async def test_h2_searxng_waits_while_the_update_s_transient_unit_runs(monkeypatch):
    monkeypatch.setattr(cg_engine, "_transient_unit_active", lambda unit: unit == cg_engine.SEARXNG_START_UNIT)
    monkeypatch.delenv("DOMOVOI_MANAGE_SEARXNG", raising=False)
    guard = cg_engine.ContainerGuard(config=lambda: GuardConfig(compose="on"))
    with egress.override_policy("always"):
        exp = await gather_expected(guard, GuardConfig(compose="on"), with_db=False)
    assert exp.containers["domovoi-searxng"].hold == "the update is starting the web search helper"


# ─── M1: only "/" or the system drive is "the host's whole disk" ──────────

@pytest.mark.parametrize(("source", "root"), [
    ("E:/", False), ("E:\\", False), ("/run/desktop/mnt/host/e", False), ("/home/me/Music", False),
    ("/", True), ("C:\\", True), ("/run/desktop/mnt/host/c", True),
])
def test_m1_a_data_drive_root_is_not_a_root_mount(source, root):
    exp = ExpectedSet(mpd_prefix="domovoi-mpd-", mpd_tag_ref="domovoi-mpd:latest", mpd_image_id=A)
    exp.add(mpd_expectations([("kitchen", "domovoi-mpd-kitchen")], tag_ref="domovoi-mpd:latest",
                             image_id=A, warm_done=True))
    v = view_of(obs("domovoi-mpd-kitchen", A, "domovoi-mpd:latest", mounts=((source, "/music"),)))
    want = [(TAMPER, "config", "domovoi-mpd-kitchen", "error")] if root else []
    assert findings(exp, v) == want


# ─── M2: a staged upgrade waits for its restart ───────────────────────────

_JF = {"containers": [{"id": "server", "name": "domovoi-jellyfin", "image": "jellyfin/jellyfin:latest"}]}


async def test_m2_a_plugin_whose_upgrade_waits_for_a_restart_is_held():
    row = SimpleNamespace(slug="jellyfin", name="Jellyfin", enabled=True, status="ok", manifest=_JF)
    old_code = SimpleNamespace(context=SimpleNamespace(containers={}))
    items, _, _ = await plugin_expectations([row], {"jellyfin": old_code}, pending_restart={"jellyfin"})
    (e,) = items
    assert e.hold == "the Jellyfin plugin's upgrade waits for a restart"
    exp = ExpectedSet()
    exp.add(items)
    assert findings(exp, DockerView(ok=True)) == []
    assert findings(exp, view_of(obs("domovoi-jellyfin", OLD, "jellyfin/jellyfin:10.10.7"))) == []


# ─── M3: a clear is durable until the guard sees the finding gone ─────────

@requires_db
async def test_m3_a_clear_survives_downtime_and_ends_when_the_finding_is_gone(guard_env):
    guard, docker, clock, expected = guard_env
    docker.show(running_db(image_id=D_OTHER, config_image="postgres:other"))
    for _ in range(2):
        clock.t += 30
        await guard.check(force=True)
    async with session_scope() as s:
        (r,) = await store.open_incidents(s)
        await store.clear(s, r["id"], "admin via test", "upgraded by hand, keep it")
    freeze.forget()
    async with session_scope() as s:
        # The box was off overnight: nothing touched the record for hours.
        await s.execute(text("UPDATE container_incidents SET last_seen_at = now() - interval '2 days'"))
    for _ in range(3):
        clock.t += 30
        await guard.check(force=True)
    async with session_scope() as s:
        assert await store.open_incidents(s) == []
        assert [k for k, _ in await store.accepted(s)] == [r["incident_key"]]
    # Put right: the guard sees the finding gone and the acceptance ends...
    docker.show(running_db())
    clock.t += 30
    await guard.check(force=True)
    async with session_scope() as s:
        assert await store.accepted(s) == []
    # ...so the same swap again is news.
    docker.show(running_db(image_id=D_OTHER, config_image="postgres:other"))
    for _ in range(2):
        clock.t += 30
        await guard.check(force=True)
    async with session_scope() as s:
        (again,) = await store.open_incidents(s)
    assert again["class"] == TAMPER and again["id"] != r["id"]


# ─── M4: Domovoi's not-currently-expected containers are image-checked ────

def test_m4_known_compose_containers_run_the_pin_or_raise():
    svc = {"letta": ComposeService("letta", f"letta/letta:0.34.4@{NEW}", "domovoi-letta", ("chat",)),
           "searxng": ComposeService("searxng", f"searxng/searxng:x@{NEW}", "domovoi-searxng")}
    exp = ExpectedSet(compose_project="domovoi", compose_services=frozenset(svc))
    exp.add(compose_expectations(svc, svc, project="domovoi", internet_policy="",
                                 manage_searxng=True, chat_mode=False))
    v = view_of(obs("domovoi-letta", OLD, "evil/miner:latest"),
                obs("domovoi-searxng", A, "searxng/searxng:old"),
                repo={A: ("searxng/searxng@" + A,)})
    assert findings(exp, v) == [
        (FAILURE, "outdated", "domovoi-searxng", "warn"),
        (TAMPER, "image", "domovoi-letta", "error"),
    ]


async def test_m4_a_disabled_plugin_s_container_runs_its_repository():
    row = SimpleNamespace(slug="jellyfin", name="Jellyfin", enabled=False, status="ok", manifest=_JF)
    items, names, _ = await plugin_expectations([row], {})
    exp = ExpectedSet(plugin_names=names)
    exp.add(items)
    assert items[0].state == KNOWN and items[0].repo_only
    evil = view_of(obs("domovoi-jellyfin", OLD, "evil/miner:latest"), repo={OLD: ("evil/miner@" + OLD,)})
    assert findings(exp, evil) == [(TAMPER, "image", "domovoi-jellyfin", "error")]
    # Its own repository at any tag (the plugin's choice while it ran): fine.
    own = view_of(obs("domovoi-jellyfin", OLD, "jellyfin/jellyfin:10.10.7"),
                  repo={OLD: ("jellyfin/jellyfin@" + OLD,)})
    assert findings(exp, own) == []


# ─── M5: compose pins come from the committed file ────────────────────────

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_m5_an_uncommitted_edit_of_the_compose_file_is_ignored(tmp_path, monkeypatch):
    repo = tmp_path / "checkout"
    (repo / "domovoi").mkdir(parents=True)
    compose = repo / "domovoi" / "docker-compose.yml"
    compose.write_text(f"services:\n  postgres:\n    image: postgres:16@{OLD}\n"
                       f"    container_name: domovoi-postgres\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "pin")
    compose.write_text(f"services:\n  postgres:\n    image: evil/pg:1@{NEW}\n"
                       f"    container_name: domovoi-postgres\n", encoding="utf-8")
    guard = cg_engine.ContainerGuard()
    monkeypatch.setattr(guard, "compose_file", lambda: compose)
    boot, now = guard.compose_services()
    assert boot["postgres"].image == now["postgres"].image == f"postgres:16@{OLD}"
    exp = ExpectedSet(compose_project="domovoi", compose_services=frozenset(boot))
    exp.add(compose_expectations(boot, now, project="domovoi", internet_policy="",
                                 manage_searxng=True, chat_mode=False))
    v = view_of(obs("domovoi-postgres", NEW, f"evil/pg:1@{NEW}"), repo={NEW: ("evil/pg@" + NEW,)})
    assert findings(exp, v) == [(TAMPER, "image", "domovoi-postgres", "error")]


def test_m5_without_git_the_file_on_disk_is_read(tmp_path, monkeypatch):
    compose = tmp_path / "plain" / "domovoi" / "docker-compose.yml"
    compose.parent.mkdir(parents=True)
    compose.write_text(f"services:\n  postgres:\n    image: postgres:16@{OLD}\n", encoding="utf-8")
    guard = cg_engine.ContainerGuard()
    monkeypatch.setattr(guard, "compose_file", lambda: compose)
    assert guard.compose_services()[0]["postgres"].image == f"postgres:16@{OLD}"


# ─── L1: the Containers page is the guest line on a shared screen ─────────

def test_l1_the_web_page_shows_a_shared_screen_only_the_guest_line():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "web" / "static" / "containers.jsx").read_text(encoding="utf-8")
    assert "const shared = useSharedScreen();" in src
    assert "const household = !shared &&" in src
    assert "useAdminSignedIn() && !shared" in src


# ─── L2: a one-off run under the compose label runs its service's image ───

def test_l2_a_one_off_run_is_held_to_its_service_s_pin():
    svc = {"flyway": ComposeService("flyway", f"flyway/flyway:10@{NEW}", "domovoi-flyway")}
    exp = ExpectedSet(compose_project="domovoi", compose_services=frozenset(svc))
    exp.add(compose_expectations(svc, svc, project="domovoi", internet_policy="",
                                 manage_searxng=True, chat_mode=False))
    evil = obs("whatever", OLD, "evil/miner:latest")
    v = view_of(listed_extra=[Listed(id=evil.id, name=evil.name, compose_project="domovoi",
                                     compose_service="flyway", state="running")])
    v.containers[evil.name] = evil
    assert findings(exp, v) == [(TAMPER, "image", "whatever", "error")]
    ok = obs("domovoi-flyway-run-1", NEW, f"flyway/flyway:10@{NEW}")
    v = view_of(listed_extra=[Listed(id=ok.id, name=ok.name, compose_project="domovoi",
                                     compose_service="flyway", state="running")])
    v.containers[ok.name] = ok
    assert findings(exp, v) == []


# ─── L3: a failed image lookup is no data ─────────────────────────────────

def test_l3_an_image_whose_digests_couldn_t_be_read_is_not_judged():
    exp = _compose(f"letta/letta:0.34.4@{NEW}", chat=False)
    v = view_of(obs("domovoi-postgres", OLD, "postgres:16"), unknown={OLD})
    det = detect(exp, v, now_wall=2e9, boot_wall=2e9)
    assert det.findings == [] and "domovoi-postgres" not in det.image_evaluated


async def test_l3_the_cli_marks_a_failed_lookup_unknown(monkeypatch):
    cli = DockerCli()
    calls = []

    async def fake_call(args):
        calls.append(args)
        if args[0] == "ps":
            return 0, "c1\tdomovoi-postgres\tdomovoi\tpostgres\trunning\n", ""
        if args[0] == "inspect":
            return 0, ('[{"Id":"c1","Name":"/domovoi-postgres","Image":"%s",'
                       '"State":{"Status":"running","Running":true},"Config":{"Image":"postgres:16"}}]' % OLD), ""
        return 1, "", "Error response from daemon: context deadline exceeded"

    monkeypatch.setattr(cli, "_call", fake_call)
    monkeypatch.setattr("shutil.which", lambda exe: "docker")
    from domovoi.container_guard import docker_cli

    docker_cli._DIGEST_CACHE.pop(OLD, None)
    v = await cli.observe(claim=lambda c: True)
    assert OLD in v.unknown_images and OLD not in v.repo_digests


# ─── L4: a busy plugin is believed past the cap while its image is pulled ─

def test_l4_the_busy_cap_waits_for_an_active_pull():
    (decl,) = declared_containers(_JF)
    busy = ContainerExpectation(busy=True, reason="pulling jellyfin")
    plugin_expectation("jf", "Jellyfin", decl, busy, now=1000.0)
    later = 1000.0 + PLUGIN_BUSY_MAX_SEC + 60
    assert plugin_expectation("jf", "Jellyfin", decl, busy, now=later, image_pulling=True).hold == "pulling jellyfin"
    assert plugin_expectation("jf", "Jellyfin", decl, busy, now=later, image_pulling=False).hold is None
    # A pull that never finishes doesn't hold forever.
    never = 1000.0 + PLUGIN_PULL_MAX_SEC + 1
    assert plugin_expectation("jf", "Jellyfin", decl, busy, now=never, image_pulling=True).hold is None
    plugin_expectation("jf", "Jellyfin", decl, ContainerExpectation(), now=later)


async def test_l4_the_guard_asks_about_the_image_the_plugin_is_moving_to(monkeypatch):
    import time

    from domovoi.container_guard import expected as cg_expected

    reported = ContainerExpectation(image="jellyfin/jellyfin:10.10.6", busy=True, reason="recreating",
                                    pulling="jellyfin/jellyfin:10.10.7")
    lp = SimpleNamespace(context=SimpleNamespace(containers={"server": lambda: reported}))
    row = SimpleNamespace(slug="jfpull", name="Jellyfin", enabled=True, status="ok", manifest=_JF)
    monkeypatch.setitem(cg_expected._BUSY_SINCE, ("jfpull", "server"),
                        time.monotonic() - PLUGIN_BUSY_MAX_SEC - 60)
    asked: list[str] = []

    async def absent(ref):
        asked.append(ref)
        return False

    async def present(ref):
        return True

    (e,), _, _ = await plugin_expectations([row], {"jfpull": lp}, image_present=absent)
    assert asked == ["jellyfin/jellyfin:10.10.7"] and e.hold == "recreating"
    (e,), _, _ = await plugin_expectations([row], {"jfpull": lp}, image_present=present)
    assert e.hold is None


# ─── L5: a resolver that keeps failing ends up "not watched", visibly ─────

def test_l5_a_failing_resolver_holds_then_becomes_not_watched():
    (decl,) = declared_containers(_JF)
    first = plugin_expectation("jf5", "Jellyfin", decl, None, resolver_failed=True, now=10.0)
    assert first.hold and "couldn't say" in first.hold
    late = plugin_expectation("jf5", "Jellyfin", decl, None, resolver_failed=True,
                              now=10.0 + RESOLVER_FAIL_MAX_SEC + 1)
    assert late.state == KNOWN and late.hold is None and late.repo_only
    assert {"slug": "jf5", "container": "server"} == {
        k: v for k, v in failing_resolvers(now=10.0 + RESOLVER_FAIL_MAX_SEC + 1)[0].items() if k != "for_sec"}
    plugin_expectation("jf5", "Jellyfin", decl, ContainerExpectation(), now=99999.0)
    assert failing_resolvers(now=99999.0) == []


# ─── L6: retention, and a flapping container is one incident ──────────────

def _failure_finding(kind="stopped"):
    return Finding(key="failure:domovoi-mpd-kitchen", cls=FAILURE, kind=kind, severity="error",
                   container="domovoi-mpd-kitchen", owner="core", label="the music player for kitchen",
                   summary="The music player for kitchen stopped.")


@requires_db
async def test_l6_a_failure_back_within_15_minutes_reopens_its_incident(guard_env):
    async with session_scope() as s:
        first, created = await store.open_incident(s, _failure_finding())
        assert created
        await store.resolve_incident(s, first, "recovered")
        again, created_again = await store.open_incident(s, _failure_finding("missing"))
    assert again == first and created_again is False
    async with session_scope() as s:
        (row,) = await store.open_incidents(s)
        assert row["kind"] == "missing" and row["resolved_at"] is None
        await store.resolve_incident(s, first, "recovered")
        await s.execute(text("UPDATE container_incidents SET resolved_at = now() - interval '20 minutes'"))
        later, created_later = await store.open_incident(s, _failure_finding())
    assert later != first and created_later


@requires_db
async def test_l6_resolved_incidents_older_than_90_days_are_pruned(guard_env):
    async with session_scope() as s:
        old, _ = await store.open_incident(s, _failure_finding())
        await store.resolve_incident(s, old, "recovered")
        accepted, _ = await store.open_incident(s, Finding(
            key="tamper:image:x:1", cls=TAMPER, kind="image", severity="error", container="x",
            owner="core", label="x", summary="x"))
        await store.clear(s, accepted, "admin", None)
        still_open, _ = await store.open_incident(s, Finding(
            key="tamper:image:y:1", cls=TAMPER, kind="image", severity="error", container="y",
            owner="core", label="y", summary="y"))
        await s.execute(text("UPDATE container_incidents SET opened_at = now() - interval '200 days', "
                             "resolved_at = CASE WHEN resolved_at IS NULL THEN NULL "
                             "ELSE now() - interval '120 days' END"))
        assert await store.prune(s) == 1
        left = {r["id"] for r in await store.recent_incidents(s, hours=24 * 365)}
    assert left == {accepted, still_open}


# ─── L7: the crash-loop window runs on the monotonic clock ────────────────

def test_l7_a_wall_clock_stepped_back_doesn_t_make_a_crash_loop():
    from domovoi.container_guard.model import RUNNING, Expected

    e = Expected(name="domovoi-mpd-kitchen", owner="core", role="mpd", label="the music player",
                 state=RUNNING, image_id=A)
    exp = ExpectedSet()
    exp.add([e])
    memory = RestartMemory()
    # One crash every ~5 real minutes; NTP steps the wall clock back 15 min.
    for mono, wall, count in ((0.0, 10_000.0, 0), (300.0, 10_300.0, 1),
                              (600.0, 9_400.0, 2), (900.0, 9_700.0, 3)):
        det = detect(exp, view_of(obs("domovoi-mpd-kitchen", A, "domovoi-mpd:latest", restart_count=count)),
                     now_wall=wall, boot_wall=0.0, memory=memory, now_mono=mono)
    assert det.findings == []


# ─── The suite's own lane: the guard's store writes where conftest points ─

def test_the_guard_s_store_runs_on_the_test_database_conftest_chose():
    import os

    from domovoi.db.session import engine

    wanted = os.environ["DATABASE_URL"].rsplit("/", 1)[-1].partition("?")[0]
    assert engine.url.database == wanted and wanted.endswith("_test")
