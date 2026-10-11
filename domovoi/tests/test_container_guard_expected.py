"""The container guard's expected set (domovoi/container_guard/expected.py)
and the plugin declarations it reads (SDK 1.5 ``[[containers]]``).

DB-free and Docker-free: the builders take plain values.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from domovoi.container_guard import holds
from domovoi.container_guard.compose import parse_services, project_name, read_services
from domovoi.container_guard.expected import (
    PLUGIN_BUSY_MAX_SEC,
    ExpectedSet,
    compose_expectations,
    compose_is_ours,
    declared_containers,
    mpd_expectations,
    needs_docker,
    plugin_expectation,
    plugin_expectations,
)
from domovoi.container_guard.model import KNOWN, ONESHOT, RUNNING, STOPPED, normalize_ref, ref_digest
from domovoi.plugins_runtime.contracts import ContractReport, check_containers
from domovoi.plugins_runtime.manifest import ManifestError, parse_manifest
from domovoi.sdk import ContainerExpectation

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "domovoi" / "docker-compose.yml"


@pytest.fixture(autouse=True)
def _no_holds():
    holds.clear()
    yield
    holds.clear()


# ─── the compose file ─────────────────────────────────────────────────────

def test_the_compose_reader_agrees_with_yaml_on_the_real_file() -> None:
    mine = read_services(COMPOSE)
    ref = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert set(mine) == set(ref)
    for name, svc in ref.items():
        assert mine[name].image == svc.get("image"), name
        assert mine[name].container_name == svc.get("container_name"), name
        assert mine[name].profiles == tuple(svc.get("profiles") or ()), name


def test_the_compose_reader_handles_comments_quotes_and_block_lists() -> None:
    text = textwrap.dedent(
        """\
        # top comment
        services:
          # a comment
          a:
            image: "repo/a:1@sha256:%s"  # trailing comment
            container_name: 'domovoi-a'
            profiles:
              - chat
              - extra
          b:
            image: repo/b:2 # note
            environment:
              image: not-this-one
        volumes:
          x:
            name: y
        """ % ("0" * 64)
    )
    services = parse_services(text)
    assert set(services) == {"a", "b"}
    assert services["a"].image == f"repo/a:1@sha256:{'0' * 64}"
    assert services["a"].container_name == "domovoi-a"
    assert services["a"].profiles == ("chat", "extra")
    assert services["b"].image == "repo/b:2"


def test_project_name_is_the_directory() -> None:
    assert project_name(COMPOSE) == "domovoi"


@pytest.mark.parametrize(
    ("setting", "url", "ours"),
    [
        ("auto", "postgresql+asyncpg://domovoi:x@127.0.0.1:6432/domovoi", True),
        ("auto", "postgresql+asyncpg://domovoi:x@localhost:6432/domovoi", True),
        # A throwaway test core on its own Postgres must not claim the real
        # install's containers.
        ("auto", "postgresql+asyncpg://domovoi:x@127.0.0.1:6433/domovoi", False),
        # A native Postgres.
        ("auto", "postgresql+asyncpg://domovoi:x@127.0.0.1:5432/domovoi", False),
        ("auto", "postgresql+asyncpg://domovoi:x@db.lan:6432/domovoi", False),
        ("on", "postgresql+asyncpg://domovoi:x@127.0.0.1:5432/domovoi", True),
        ("off", "postgresql+asyncpg://domovoi:x@127.0.0.1:6432/domovoi", False),
    ],
)
def test_compose_is_ours(setting, url, ours) -> None:
    assert compose_is_ours(setting, url) is ours


def _core(**kw):
    boot = read_services(COMPOSE)
    base = dict(project="domovoi", internet_policy="", manage_searxng=True, chat_mode=False)
    base.update(kw)
    return {e.compose_service: e for e in compose_expectations(boot, **base)}


def test_postgres_must_run_and_is_required() -> None:
    pg = _core()["postgres"]
    assert (pg.name, pg.state, pg.required) == ("domovoi-postgres", RUNNING, True)
    assert pg.pin == "digest" and len(pg.image_digests) == 1
    assert pg.label == "the database"


def test_flyway_is_a_one_shot_and_flyway_test_is_only_known() -> None:
    core = _core()
    assert core["flyway"].state == ONESHOT and core["flyway"].required is False
    assert core["flyway-test"].state == KNOWN


@pytest.mark.parametrize(
    ("policy", "managed", "state"),
    [
        ("always", True, RUNNING),
        ("sometimes", True, RUNNING),
        # Answered No: the core stops it; nothing is expected of it.
        ("never", True, KNOWN),
        # Unanswered: today's behaviour, whatever the household did by hand.
        ("", True, KNOWN),
        # DOMOVOI_MANAGE_SEARXNG=0: the operator runs it some other way.
        ("always", False, KNOWN),
    ],
)
def test_searxng_follows_the_internet_answer(policy, managed, state) -> None:
    sx = _core(internet_policy=policy, manage_searxng=managed)["searxng"]
    assert sx.state == state
    assert sx.required is False  # only web answers need it: amber


def test_letta_runs_only_in_chat_mode() -> None:
    assert _core(chat_mode=False)["letta"].state == KNOWN
    letta = _core(chat_mode=True)["letta"]
    assert letta.state == RUNNING and letta.required is False


def test_the_search_helper_waits_while_it_is_started_or_stopped() -> None:
    sx = _core(internet_policy="always", searxng_hold="being started")["searxng"]
    assert sx.hold == "being started"


def test_a_pull_that_moves_a_pin_accepts_both_digests() -> None:
    boot = read_services(COMPOSE)
    moved = {k: v for k, v in boot.items()}
    new_digest = "sha256:" + "a" * 64
    pg = boot["postgres"]
    moved["postgres"] = type(pg)(pg.service, f"postgres:17@{new_digest}", pg.container_name, pg.profiles)
    exp = {
        e.compose_service: e
        for e in compose_expectations(boot, moved, project="domovoi", internet_policy="",
                                      manage_searxng=True, chat_mode=False)
    }
    assert exp["postgres"].image_digests == {ref_digest(pg.image), new_digest}


# ─── the music players ────────────────────────────────────────────────────

def test_each_room_has_a_running_music_player_on_the_built_image() -> None:
    rows = [("kitchen", "domovoi-mpd-kitchen"), ("garage", "domovoi-mpd-garage")]
    exp = mpd_expectations(rows, tag_ref="domovoi-mpd:latest", image_id="sha256:" + "b" * 64,
                           warm_done=True)
    assert [e.name for e in exp] == ["domovoi-mpd-kitchen", "domovoi-mpd-garage"]
    for e in exp:
        assert e.state == RUNNING and e.required and e.role == "mpd"
        assert e.pin == "id" and e.hold is None
    assert exp[0].label == "the music player for kitchen"


def test_a_room_being_started_is_held() -> None:
    holds.hold("room:kitchen", 60, "the music player for kitchen is starting")
    exp = mpd_expectations([("kitchen", "domovoi-mpd-kitchen")], tag_ref="t", image_id=None,
                           warm_done=True)
    assert exp[0].hold == "the music player for kitchen is starting"
    assert exp[0].pin == "tag"  # no recorded image: the tag is compared


def test_rooms_wait_until_the_boot_warm_is_done() -> None:
    exp = mpd_expectations([("kitchen", "domovoi-mpd-kitchen")], tag_ref="t", image_id=None,
                           warm_done=False)
    assert exp[0].hold


def test_a_hold_ends_after_the_block_and_its_settle_time() -> None:
    with holds.holding("room:x", "starting", settle_sec=0.0):
        assert holds.held("room:x") == "starting"
        with holds.holding("room:x", "starting", settle_sec=0.0):
            pass
        assert holds.held("room:x") == "starting"  # the outer step still runs
    assert holds.held("room:x") is None


# ─── plugin declarations ──────────────────────────────────────────────────

_MANIFEST = textwrap.dedent(
    """\
    [plugin]
    slug = "vidsrv"
    name = "Video Server"
    version = "1.0.0"
    publisher = "Coders Farm"
    license = "MIT"
    description = "test"
    domovoi_api = ">=1.5,<2.0"

    [entry_points]
    core = "domovoi_plugin_vidsrv.core"

    [requirements]
    system = [{ tool = "docker", required = true }]
    {extra}
    """
)


def _manifest(extra: str):
    return parse_manifest(_MANIFEST.replace("{extra}", extra))


def test_the_manifest_declares_containers() -> None:
    m = _manifest(
        """
[[containers]]
id = "server"
name = "domovoi-vidsrv"
image = "vid/server:1.2@sha256:%s"
label = "the video server"

[[containers]]
id = "db"
name = "domovoi-vidsrv-db"
image = "mariadb:11"
required = false
""" % ("c" * 64)
    )
    assert [c.id for c in m.containers] == ["server", "db"]
    server, db = m.containers
    assert server.pinned and server.required and server.label == "the video server"
    assert not db.pinned and db.required is False
    assert m.raw["containers"][0]["name"] == "domovoi-vidsrv"


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ('id = "Bad"\nname = "x"\nimage = "a:1"', "must match"),
        ('id = "a"\nname = "domovoi-postgres"\nimage = "a:1"', "core's own"),
        ('id = "a"\nname = "domovoi-mpd-kitchen"\nimage = "a:1"', "core's own"),
        ('id = "a"\nname = "bad name"\nimage = "a:1"', "not a Docker container name"),
        ('id = "a"\nname = "x"\nimage = "a:1@sha256:abc"', "not an image reference"),
        ('id = "a"\nname = "x"\nimage = "a:1"\nrequired = "yes"', "boolean"),
        ('id = "a"\nname = "x"\nimage = "a:1"\nallow = ["root"]', "allowed values"),
        # Letting a container run privileged needs a stated reason.
        ('id = "a"\nname = "x"\nimage = "a:1"\nallow = ["privileged"]', "permissions.warnings"),
    ],
)
def test_bad_container_entries_are_refused(entry, message) -> None:
    with pytest.raises(ManifestError, match=message):
        _manifest("\n[[containers]]\n" + entry + "\n")


def test_ids_and_names_are_unique() -> None:
    with pytest.raises(ManifestError, match="more than once"):
        _manifest('\n[[containers]]\nid = "a"\nname = "x"\nimage = "a:1"\n'
                  '[[containers]]\nid = "a"\nname = "y"\nimage = "a:1"\n')
    with pytest.raises(ManifestError, match="more than once"):
        _manifest('\n[[containers]]\nid = "a"\nname = "x"\nimage = "a:1"\n'
                  '[[containers]]\nid = "b"\nname = "x"\nimage = "a:1"\n')


def test_an_older_manifest_declares_nothing_and_still_parses() -> None:
    m = _manifest("")
    assert m.containers == ()
    assert needs_docker(m.raw) is True


def test_contract_check_8_code_must_match_the_manifest() -> None:
    m = _manifest('\n[[containers]]\nid = "server"\nname = "domovoi-vidsrv"\nimage = "a:1"\n')
    report = ContractReport()
    check_containers("vidsrv", m, ["server"], [], report)
    assert report.ok()
    report = ContractReport()
    check_containers("vidsrv", m, ["server", "sidecar"], [], report)
    assert any("'sidecar'" in e for e in report.errors)
    report = ContractReport()
    check_containers("vidsrv", m, [], [("domovoi-vidsrv", "other")], report)
    assert any("already declared by the enabled plugin 'other'" in e for e in report.errors)


def _row(slug="vidsrv", *, enabled=True, status="ok", containers=None, docker=True):
    manifest = {"plugin": {"slug": slug}}
    if docker:
        manifest["requirements"] = {"system": [{"tool": "docker", "required": True}]}
    if containers is not None:
        manifest["containers"] = containers
    return SimpleNamespace(slug=slug, name="Video Server", enabled=enabled, status=status,
                           manifest=manifest)


_DECL = [{"id": "server", "name": "domovoi-vidsrv", "image": "vid/server:1.2", "label": "the video server"}]


def _loaded(resolvers):
    return SimpleNamespace(context=SimpleNamespace(containers=resolvers))


async def test_a_loaded_plugin_s_resolver_drives_the_expectation() -> None:
    async def resolve():
        return ContainerExpectation(name="my-vid", image="vid/server:1.3")

    exp, names, unwatched = await plugin_expectations([_row(containers=_DECL)],
                                                      {"vidsrv": _loaded({"server": resolve})})
    (e,) = exp
    assert (e.name, e.image_ref, e.state, e.owner) == ("my-vid", "vid/server:1.3", RUNNING, "plugin:vidsrv")
    assert e.pin == "tag" and e.label == "the video server"
    assert "my-vid" in names and unwatched == ()


async def test_no_resolver_means_the_manifest_values_running() -> None:
    exp, _, _ = await plugin_expectations([_row(containers=_DECL)], {"vidsrv": _loaded({})})
    assert exp[0].name == "domovoi-vidsrv" and exp[0].state == RUNNING


async def test_a_stopped_container_is_not_expected_to_run() -> None:
    exp, _, _ = await plugin_expectations(
        [_row(containers=_DECL)],
        {"vidsrv": _loaded({"server": lambda: ContainerExpectation(state="stopped")})},
    )
    assert exp[0].state == STOPPED


async def test_an_unmanaged_container_is_not_watched_at_all() -> None:
    exp, names, _ = await plugin_expectations(
        [_row(containers=_DECL)],
        {"vidsrv": _loaded({"server": lambda: ContainerExpectation.unmanaged("external server")})},
    )
    assert exp == [] and "domovoi-vidsrv" not in names


async def test_a_disabled_plugin_s_containers_are_known_not_expected() -> None:
    exp, names, _ = await plugin_expectations([_row(enabled=False, containers=_DECL)], {})
    assert exp[0].state == KNOWN and "domovoi-vidsrv" in names


async def test_a_plugin_that_needs_docker_and_declares_nothing_is_unwatched() -> None:
    _, _, unwatched = await plugin_expectations([_row(containers=None)], {})
    assert unwatched == ("vidsrv",)
    _, _, unwatched = await plugin_expectations([_row(containers=None, docker=False)], {})
    assert unwatched == ()
    _, _, unwatched = await plugin_expectations([_row(enabled=False, containers=None)], {})
    assert unwatched == ()


async def test_a_failing_resolver_holds_instead_of_alarming() -> None:
    def boom():
        raise RuntimeError("bug")

    exp, _, _ = await plugin_expectations([_row(containers=_DECL)],
                                          {"vidsrv": _loaded({"server": boom})})
    assert exp[0].hold and "couldn't say" in exp[0].hold


async def test_a_plugin_being_upgraded_is_held() -> None:
    holds.hold("plugin:vidsrv", 300, "the vidsrv plugin is being upgraded")
    exp, _, _ = await plugin_expectations([_row(containers=_DECL)], {"vidsrv": _loaded({})})
    assert exp[0].hold == "the vidsrv plugin is being upgraded"


def test_busy_is_believed_for_thirty_minutes_only() -> None:
    (decl,) = declared_containers({"containers": _DECL})
    busy = ContainerExpectation(busy=True, reason="pulling the image")
    first = plugin_expectation("vidsrv", "Video Server", decl, busy, now=1000.0)
    assert first.hold == "pulling the image"
    later = plugin_expectation("vidsrv", "Video Server", decl, busy,
                               now=1000.0 + PLUGIN_BUSY_MAX_SEC + 1)
    assert later.hold is None
    plugin_expectation("vidsrv", "Video Server", decl, ContainerExpectation(), now=5000.0)


def test_container_expectation_validates_its_state() -> None:
    with pytest.raises(ValueError):
        ContainerExpectation(state="sleeping")


def test_expected_set_claims_and_prefixes() -> None:
    exp = ExpectedSet(mpd_prefix="domovoi-mpd-", mpd_tag_ref="domovoi-mpd:latest",
                      plugin_names=frozenset({"domovoi-vidsrv"}))
    exp.add(mpd_expectations([("k", "domovoi-mpd-k")], tag_ref="t", image_id=None, warm_done=True))
    assert exp.claimed_names() == {"domovoi-mpd-k", "domovoi-vidsrv"}
    assert exp.prefixes() == ("domovoi-mpd-",)
    assert exp.image_refs() == ("domovoi-mpd:latest",)
    assert exp.anything_expected()


@pytest.mark.parametrize(
    ("ref", "norm"),
    [
        ("postgres:16@sha256:" + "0" * 64, "postgres:16"),
        ("docker.io/library/postgres:16", "postgres:16"),
        ("jellyfin/jellyfin", "jellyfin/jellyfin:latest"),
        ("ghcr.io/kiwix/kiwix-serve:3.7", "ghcr.io/kiwix/kiwix-serve:3.7"),
        ("localhost:5000/x", "localhost:5000/x:latest"),
    ],
)
def test_normalize_ref(ref, norm) -> None:
    assert normalize_ref(ref) == norm
