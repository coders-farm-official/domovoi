"""The offline kit's builder (release 1.5.0, phase P1; domovoi/kit_builder/,
docs/OFFLINE_KIT.md "Making a kit").

Fakes stand in for Docker, apt, Ollama, PyPI and the network: no test here
downloads anything or needs mtools. The pins are the repository's own, so
a pin file that stops parsing fails here. The real FAT32 image is built by
test_offline_kit_e2e.py (gated: it needs mkfs.fat and mcopy).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

from domovoi import egress
from domovoi.kit_builder import (
    apt_mirror, fatimage, fetch, guide, host, images, jobs, licences, ollama_store, parts, pins, plan,
    wheels,
)
from domovoi.kit_builder.parts import Part, Release
from domovoi.offline_kit import FolderSink, Source, verify

REPO = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40


def repo_read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


@pytest.fixture
def kit_home(tmp_path, monkeypatch):
    root = tmp_path / "offline_kit"
    monkeypatch.setattr(host, "kit_root", lambda: root)
    monkeypatch.setattr(egress, "internet_turned_off", lambda: False)
    monkeypatch.setenv("DOMOVOI_KIT_OLLAMA_MODELS", "")
    monkeypatch.setattr(host, "LINUX_OLLAMA_DIRS", ())
    jobs._cancel.clear()
    jobs._job.update(running=False, work=None)
    plan.invalidate()
    return root


# ─── pins ────────────────────────────────────────────────────────────────

def test_the_pins_are_read_from_the_release_commit_s_own_files():
    p = pins.load(repo_read, COMMIT)
    assert p.ollama_linux.name == "ollama-linux-amd64.tar.zst" and len(p.ollama_linux.sha256) == 64
    assert p.ollama_linux.size and p.ollama_linux.url.startswith("https://github.com/ollama/ollama/releases/")
    tags = {m.tag for m in p.models}
    assert any(t.startswith("qwen3:4b") for t in tags) and any(t.startswith("llama3.2:3b") for t in tags)
    assert {w.name.rsplit(".", 1)[-1] for w in p.windows} == {"msi", "wsl"}
    assert all(i.digest and i.tag for i in p.images)
    assert "python3" in p.apt_packages and "docker_ce" in p.manifest
    assert p.whisper["whisper_model_bin_bytes"] > 400_000_000


def test_searxng_never_goes_on_a_kit_and_every_image_is_pinned_by_digest():
    imgs = pins.compose_images(repo_read(pins.COMPOSE))
    assert imgs and not any("searxng" in i.repo for i in imgs)
    with pytest.raises(pins.PinError):
        pins.compose_images("services:\n  x:\n    image: postgres:16\n")


def test_pins_iss_defines_resolve_their_concatenations():
    got = pins.parse_iss('#define A "x"\n#define B "https://h/" + A + "/f"\n')
    assert got["B"] == "https://h/x/f"


# ─── licences (design section 9) ─────────────────────────────────────────

@pytest.mark.parametrize("model,licence", [
    ("qwen3:4b-instruct-2507-q4_K_M", "Apache-2.0"),
    ("qwen3-vl:2b-instruct-q4_K_M", "Apache-2.0"),
    ("llama3.2:3b-instruct-q4_K_M", "LicenseRef-Llama-3.2-Community"),
    ("qwen2.5:14b", "Apache-2.0"),
])
def test_allowed_models_carry_their_licence(model, licence):
    assert licences.model_licence(model) == licence and licences.refusal(model) is None


@pytest.mark.parametrize("model", ["qwen2.5:3b", "qwen2.5:3b-instruct", "gemma2:9b", "gemma3:12b",
                                   "llama3.2-vision:11b", "mistral:7b", "somebody/thing:latest"])
def test_models_that_can_t_be_passed_on_are_refused_with_a_reason(model):
    why = licences.refusal(model)
    assert why and model in why


# ─── fetch ───────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self.status_code, self._body = status, body

    def iter_bytes(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass


def test_a_download_is_kept_only_at_its_pin(tmp_path, monkeypatch, kit_home):
    body = b"x" * 1000
    monkeypatch.setattr(fetch.net_safety, "open_stream_sync", lambda c, url, headers=None: _Resp(body))
    monkeypatch.setattr(fetch, "http_client", lambda: _Ctx())
    dest = tmp_path / "f.bin"
    fetch.fetch_file("https://example.org/f", dest, sha256=hashlib.sha256(body).hexdigest(), size=1000)
    assert dest.read_bytes() == body
    with pytest.raises(fetch.FetchError):
        fetch.fetch_file("https://example.org/f", tmp_path / "g.bin", sha256="0" * 64, size=1000)
    with pytest.raises(fetch.FetchError):
        fetch.fetch_file("https://example.org/f", tmp_path / "h.bin", sha256=hashlib.sha256(body).hexdigest(),
                         size=999)
    assert not (tmp_path / "g.bin").exists() and not list(tmp_path.glob(".*.part"))


def test_under_never_nothing_is_requested(tmp_path, monkeypatch):
    monkeypatch.setattr(egress, "policy", lambda: "never")
    called = []
    monkeypatch.setattr(fetch.net_safety, "open_stream_sync", lambda *a, **k: called.append(a))
    with pytest.raises(Exception):
        fetch.fetch_file("https://example.org/f", tmp_path / "f", sha256="0" * 64, size=1)
    assert not called


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ─── Ollama models ───────────────────────────────────────────────────────

def _model_folder(root: Path, name: str, tag: str, blobs: list[bytes]) -> tuple[str, bytes]:
    layers = []
    for b in blobs:
        d = hashlib.sha256(b).hexdigest()
        (root / "blobs").mkdir(parents=True, exist_ok=True)
        (root / "blobs" / f"sha256-{d}").write_bytes(b)
        layers.append({"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{d}", "size": len(b)})
    manifest = json.dumps({"config": layers[0], "layers": layers[1:]}).encode()
    mf = root / "manifests" / "registry.ollama.ai" / "library" / name / tag
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_bytes(manifest)
    return hashlib.sha256(manifest).hexdigest(), manifest


def test_a_local_model_is_found_by_its_pinned_manifest_digest(tmp_path):
    folder = tmp_path / "models"
    digest, _ = _model_folder(folder, "qwen3", "4b", [b"config", b"weights" * 100])
    m = ollama_store.local_model("qwen3:4b", digest, [folder])
    assert m and m.size == len(m.manifest) + 6 + 700
    assert m.manifest_path() == "manifests/registry.ollama.ai/library/qwen3/4b"
    assert ollama_store.local_model("qwen3:4b", "f" * 64, [folder]) is None
    (folder / "blobs" / f"sha256-{hashlib.sha256(b'config').hexdigest()}").unlink()
    assert ollama_store.local_model("qwen3:4b", digest, [folder]) is None


def test_a_missing_model_comes_from_the_registry_held_to_its_pin(tmp_path, kit_home, monkeypatch):
    src = tmp_path / "upstream"
    digest, manifest = _model_folder(src, "qwen3", "4b", [b"c", b"w" * 50])
    asked = []

    def fetch_bytes(url, **kw):
        asked.append(url)
        return manifest

    def fetch_file(url, dest, *, sha256, size, progress=None, headers=None):
        asked.append(url)
        data = (src / "blobs" / f"sha256-{sha256}").read_bytes()
        assert len(data) == size
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    monkeypatch.setattr(fetch, "fetch_bytes", fetch_bytes)
    monkeypatch.setattr(fetch, "fetch_file", fetch_file)
    m = ollama_store.fetch_model("qwen3:4b", digest)
    assert asked[0] == "https://registry.ollama.ai/v2/library/qwen3/manifests/4b"
    assert all(ollama_store.blob_source(m, b) for b in m.blobs)
    with pytest.raises(ollama_store.ModelProblem):
        ollama_store.registry_manifest("qwen3:8b", "e" * 64)


# ─── apt ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,want", [
    ("archive.ubuntu.com_ubuntu_dists_resolute_InRelease", ("ubuntu", "dists/resolute/InRelease")),
    ("security.ubuntu.com_ubuntu_dists_resolute-security_main_binary-amd64_Packages",
     ("ubuntu", "dists/resolute-security/main/binary-amd64/Packages")),
    ("ports.ubuntu.com_ubuntu-ports_dists_resolute_universe_binary-arm64_Packages",
     ("ubuntu", "dists/resolute/universe/binary-arm64/Packages")),
    ("download.docker.com_linux_ubuntu_dists_resolute_stable_binary-amd64_Packages",
     ("docker", "dists/resolute/stable/binary-amd64/Packages")),
    ("nvidia.github.io_libnvidia-container_stable_deb_amd64_InRelease", ("nvidia", "InRelease")),
    ("nvidia.github.io_libnvidia-container_stable_deb_amd64_._Packages", ("nvidia", "Packages")),
    ("archive.ubuntu.com_ubuntu_dists_resolute_main_i18n_Translation-en", None),
    ("evil.example.com_ubuntu_dists_resolute_InRelease", None),
])
def test_apt_s_lists_map_onto_repositories(name, want):
    got = apt_mirror.classify(name)
    assert (got and (got.repo, got.rel)) == want if want else got is None


def _raw(tmp: Path, debs: dict[str, bytes], filename: str = "pool/main/h/hello/{n}") -> Path:
    raw = tmp / "raw"
    (raw / "lists").mkdir(parents=True)
    (raw / "debs").mkdir()
    stanzas = []
    for n, data in debs.items():
        (raw / "debs" / n).write_bytes(data)
        stanzas.append(f"Package: {n}\nFilename: {filename.format(n=n)}\nSHA256: {hashlib.sha256(data).hexdigest()}\n")
    (raw / "lists" / "archive.ubuntu.com_ubuntu_dists_resolute_main_binary-amd64_Packages").write_text("\n".join(stanzas))
    (raw / "lists" / "archive.ubuntu.com_ubuntu_dists_resolute_InRelease").write_text("signed")
    return raw


def test_the_debs_are_laid_out_at_the_paths_their_index_names(tmp_path):
    raw = _raw(tmp_path, {"hello_1_amd64.deb": b"one", "bye_2_amd64.deb": b"two"})
    files = apt_mirror.assemble(raw, tmp_path / "out")
    assert "ubuntu/pool/main/h/hello/hello_1_amd64.deb" in files
    assert "ubuntu/dists/resolute/InRelease" in files
    assert (tmp_path / "out/ubuntu/dists/resolute/main/binary-amd64/Packages").is_file()


def test_a_deb_no_index_names_stops_the_mirror(tmp_path):
    raw = _raw(tmp_path, {"a_1_amd64.deb": b"x"})
    (raw / "debs" / "stray_1_amd64.deb").write_bytes(b"not listed")
    with pytest.raises(apt_mirror.AptError, match="stray"):
        apt_mirror.assemble(raw, tmp_path / "out")


def test_an_index_naming_a_path_outside_the_repo_is_refused(tmp_path):
    raw = _raw(tmp_path, {"a_1_amd64.deb": b"x"}, filename="../../etc/{n}")
    with pytest.raises(apt_mirror.AptError, match="outside"):
        apt_mirror.assemble(raw, tmp_path / "out")


def test_the_container_script_holds_only_pins_and_checks_the_keys():
    p = pins.load(repo_read, COMMIT)
    s = apt_mirror.script(p, "amd64", "1000:1000")
    assert "--download-only" in s and "status.clean" in s and p.manifest["docker_key_fpr"] in s
    assert f"docker-ce={p.manifest['docker_ce']}" in s and "nvidia-container-toolkit=" in s
    assert "nftables" in s and "mtools" in s and "chown -R 1000:1000 /out" in s
    assert "nvidia" not in apt_mirror.script(p, "arm64")
    cmd = apt_mirror.docker_command(p, "amd64", Path("/x"))
    assert "--privileged" not in cmd and "no-new-privileges" in cmd and apt_mirror.IMAGE in cmd
    bad = pins.load(repo_read, COMMIT)
    bad.manifest = {**bad.manifest, "docker_ce": "1; rm -rf /"}
    with pytest.raises(apt_mirror.AptError):
        apt_mirror.script(bad, "amd64")


def test_a_mirror_is_built_once_and_reused_for_the_same_pins(kit_home, monkeypatch):
    p = pins.load(repo_read, COMMIT)
    runs = []

    def fake_run(cmd, **kw):
        runs.append(cmd)
        out = Path(cmd[cmd.index("-v") + 1].rsplit(":/out", 1)[0])
        tmp = out.parent / "src"
        tmp.mkdir(exist_ok=True)
        raw = _raw(tmp, {"hello_1_amd64.deb": b"deb!"})
        for sub in ("lists", "debs"):
            (out / sub).mkdir(parents=True, exist_ok=True)
            for f in (raw / sub).iterdir():
                (out / sub / f.name).write_bytes(f.read_bytes())
        return subprocess.CompletedProcess(cmd, 0, "", "")

    doc = apt_mirror.build(p, "amd64", run=fake_run)
    assert len(runs) == 1 and apt_mirror.cached(p, "amd64") == doc and apt_mirror.fresh(doc)
    assert not apt_mirror.fresh(doc, now=time.time() + apt_mirror.MAX_AGE_SEC + 1)
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    with pytest.raises(egress.InternetTurnedOff):
        apt_mirror.build(p, "amd64", run=fake_run)
    assert len(runs) == 1


# ─── wheels ──────────────────────────────────────────────────────────────

def test_the_lock_is_read_with_every_hash():
    locked = wheels.parse_lock(repo_read(pins.LOCK))
    assert len(locked) > 50 and all(lk.hashes for lk in locked)
    assert any(lk.name == "setuptools" for lk in locked)


@pytest.mark.parametrize("name,arch,good", [
    ("numpy-2.3.0-cp314-cp314-manylinux_2_28_x86_64.whl", "x86_64", True),
    ("numpy-2.3.0-cp314-cp314-manylinux_2_28_aarch64.whl", "x86_64", False),
    ("numpy-2.3.0-cp314-cp314t-manylinux_2_28_x86_64.whl", "x86_64", False),
    ("numpy-2.3.0-cp313-cp313-manylinux_2_28_x86_64.whl", "x86_64", False),
    ("cffi-2.0-cp39-abi3-manylinux2014_aarch64.whl", "aarch64", True),
    ("x-1.0-py3-none-any.whl", "aarch64", True),
    ("x-1.0-cp314-cp314-musllinux_1_2_x86_64.whl", "x86_64", False),
    ("x-1.0-cp314-cp314-manylinux_2_99_x86_64.whl", "x86_64", False),
    ("x-1.0-cp314-cp314-win_amd64.whl", "x86_64", False),
])
def test_a_wheel_is_taken_only_for_a_machine_it_installs_on(name, arch, good):
    assert (wheels.rank(name, arch) > 1) is good


def test_the_best_file_the_lock_allows_is_picked_per_machine():
    files = [
        {"filename": "m-1-py3-none-any.whl", "digests": {"sha256": "1" * 64}, "size": 10, "url": "u1"},
        {"filename": "m-1-cp314-cp314-manylinux_2_28_x86_64.whl", "digests": {"sha256": "2" * 64}, "size": 20, "url": "u2"},
        {"filename": "m-1-cp314-cp314-manylinux_2_28_aarch64.whl", "digests": {"sha256": "3" * 64}, "size": 30, "url": "u3"},
        {"filename": "m-1.tar.gz", "digests": {"sha256": "4" * 64}, "size": 40, "url": "u4"},
    ]
    lk = wheels.Locked("m", "1", frozenset({"1" * 64, "2" * 64, "4" * 64}))
    assert wheels.choose(lk, files, "x86_64").filename.endswith("x86_64.whl")
    # The aarch64 wheel isn't in the lock's hashes: the pure-Python one.
    assert wheels.choose(lk, files, "aarch64").filename == "m-1-py3-none-any.whl"
    only_sdist = wheels.Locked("m", "1", frozenset({"4" * 64}))
    assert wheels.choose(only_sdist, files, "aarch64").filename == "m-1.tar.gz"
    with pytest.raises(wheels.WheelError):
        wheels.choose(wheels.Locked("m", "1", frozenset({"9" * 64})), files, "x86_64")


def test_the_picks_are_resolved_once_and_cached(kit_home):
    lock = "m==1 \\\n    --hash=sha256:" + "2" * 64 + "\n"
    calls = []

    def files_of(lk):
        calls.append(lk.name)
        return [{"filename": "m-1-py3-none-any.whl", "digests": {"sha256": "2" * 64}, "size": 7, "url": "u"}]

    picks = wheels.resolve(lock, "", files_of)
    assert [p.filename for p in picks["x86_64"]] == ["m-1-py3-none-any.whl"]
    assert wheels.resolve(lock, "", files_of) == picks and calls == ["m"]


# ─── images (design risk 2) ──────────────────────────────────────────────

def _oci_tar(path: Path, digest: str, name: str | None) -> None:
    ann = {"io.containerd.image.name": name} if name else {}
    index = json.dumps({"schemaVersion": 2, "manifests": [{"digest": f"sha256:{digest}", "annotations": ann}]}).encode()
    with tarfile.open(path, "w") as tf:
        info = tarfile.TarInfo("index.json")
        info.size = len(index)
        tf.addfile(info, io.BytesIO(index))


def test_only_an_archive_naming_the_pinned_digest_and_its_tag_is_kept(tmp_path):
    pin = pins.ImagePin(ref="postgres:16@sha256:" + "a" * 64, repo="postgres", tag="16", digest="a" * 64,
                        licence="PostgreSQL")
    good, by_digest, other = tmp_path / "g.tar", tmp_path / "d.tar", tmp_path / "o.tar"
    _oci_tar(good, "a" * 64, "docker.io/library/postgres:16")
    _oci_tar(by_digest, "a" * 64, None)              # saved by digest alone: not found after load
    _oci_tar(other, "b" * 64, "docker.io/library/postgres:16")   # saved with --platform
    assert images.archive_ok(good, pin)
    assert not images.archive_ok(by_digest, pin) and not images.archive_ok(other, pin)


def test_the_legacy_image_store_is_refused_with_the_reason():
    def run(info):
        return lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(info), "")

    assert images.store_problem(run({"DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]})) is None
    why = images.store_problem(run({"Driver": "overlay2", "DriverStatus": [["Backing Filesystem", "extfs"]]}))
    assert why and "older image store" in why
    assert "isn't answering" in images.store_problem(
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "no daemon"))


def test_an_image_is_saved_under_its_tag_without_platform(kit_home, monkeypatch):
    pin = pins.ImagePin(ref="flyway/flyway:10@sha256:" + "c" * 64, repo="flyway/flyway", tag="10",
                        digest="c" * 64, licence="Apache-2.0")
    cmds = []

    def run(cmd, **kw):
        cmds.append(cmd)
        if "save" in cmd:
            _oci_tar(Path(cmd[cmd.index("-o") + 1]), "c" * 64, "docker.io/flyway/flyway:10")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    out = images.save(pin, ("amd64",), run=run)
    assert out.is_file() and images.kit_path("flyway/flyway", "10") == "images/flyway_flyway_10.tar"
    save = next(c for c in cmds if "save" in c)
    assert "--platform" not in save and save[-1] == "flyway/flyway:10"
    assert not any("pull" in c for c in cmds)          # docker image inspect said it is here
    # A Mac's kit pulls arm64 first, through the outbound gate.
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    with pytest.raises(egress.InternetTurnedOff):
        images.save(pin, ("amd64", "arm64"), run=run)


# ─── the FAT32 image's size ──────────────────────────────────────────────

def test_the_image_size_covers_every_file_and_its_slack():
    files = [(f"d{i % 7}/f{i}", 1000 + i) for i in range(500)]
    size = fatimage.image_size(files)
    assert size >= sum(s for _, s in files) + 500 * 512 and size % (1024 * 1024) == 0
    big = fatimage.image_size([("ollama/models/blobs/x.part01", 3_900_000_000)])
    assert 3_900_000_000 < big < 4_100_000_000
    assert fatimage.cluster_size(20 * 10 ** 9) == 32 * 1024 and fatimage.cluster_size(40 * 10 ** 6) == 512


def test_the_disk_has_one_fat32_partition_at_one_mib():
    mbr = fatimage._mbr(64 * 1024 * 1024)
    assert mbr[510:] == b"\x55\xaa" and mbr[446 + 4] == 0x0C
    assert int.from_bytes(mbr[446 + 8:446 + 12], "little") == 2048


# ─── the plan: defaults, sizes, fit, refusals ────────────────────────────

def _part(pid, group="base", size=1_000_000, download=0, targets=(), kind="release", licence="MIT", **kw):
    return Part(id=pid, group=group, kind=kind, name=kw.pop("name", pid), version="1", licence=licence, size=size,
                exact=True, download=download, acquire=lambda ctx: [], targets=targets, **kw)


def _cat(*ps):
    return plan.Catalogue(release=Release("1.5.0", "v1.5.0", COMMIT), parts=list(ps), made=time.time())


def test_the_owner_s_defaults_pc_ticked_mac_not_household_off():
    sel = plan.Selection.from_body(None)
    assert sel.targets == ("pc-x86_64",) and set(sel.includes) == {"ai", "vision", "rooms"} and not sel.household
    cat = _cat(_part("rel"), _part("mac", targets=("mac-arm64",)),
               _part("model:x", group="household", kind="ollama-model", household_added=True,
                     note="not checked against a release"))
    ids = {p.id for p in plan.selected(cat, sel)}
    assert ids == {"rel"}
    view = plan.plan(cat, sel, tools_ok=lambda: True, store_problem=lambda: None)
    assert view["household"][0]["note"] == "not checked against a release" and not view["household"][0]["ticked"]
    assert [t["ticked"] for t in view["targets"]] == [True, False]
    sel2 = plan.Selection.from_body({"household": ["model:x"]})
    assert {p.id for p in plan.selected(cat, sel2)} == {"rel", "model:x"}


def test_a_stick_too_small_says_by_how_much_and_what_to_leave_out():
    cat = _cat(_part("base", size=5_000_000_000), _part("ai", group="ai", size=3_000_000_000),
               _part("pic", group="vision", size=1_900_000_000), _part("rooms", group="rooms", size=1_200_000_000))
    sel = plan.Selection(stick_gb=8)
    total = plan.image_bytes(plan.selected(cat, sel))
    f = plan.fit(cat, sel, total)
    assert f["fits"] is False and f["needed_gb"] == 16
    assert f["message"].startswith("Too big for an 8 GB stick by ")
    assert "Leave out AI chat and questions (3.0 GB)" in f["message"]
    assert plan.fit(cat, plan.Selection(stick_gb=16), total)["fits"] is True
    # Marketed GB: a 16 GB stick holds less than 16 GiB.
    assert plan.stick_capacity(16) < 16 * 1024 ** 3


def test_refusals_say_one_plain_reason(monkeypatch, kit_home):
    cat = _cat(_part("rel", download=10, label="the Windows parts"), _part("mac", download=5, targets=("mac-arm64",),
                                                                        label="the Mac parts"))
    base = dict(tools_ok=lambda: True, store_problem=lambda: None)
    sel = plan.Selection(targets=("pc-x86_64", "mac-arm64"))
    assert plan.plan(cat, sel, busy=True, **base)["refusal"]["message"] == "A kit is already being made."
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    why = plan.plan(cat, sel, **base)["refusal"]
    assert why["reason"] == "internet_off"
    assert why["message"] == ("This server has no internet, so it can't fetch: the Windows parts, the Mac "
                              "parts. Untick them, or allow internet for a while.")
    monkeypatch.setattr(egress, "internet_turned_off", lambda: False)
    monkeypatch.setattr(plan, "free_bytes", lambda p: 10)
    assert plan.plan(cat, sel, **base)["refusal"]["message"].startswith("Needs ")
    monkeypatch.setattr(plan, "free_bytes", lambda p: 10 ** 13)
    assert plan.plan(cat, sel, tools_ok=lambda: False, store_problem=lambda: None)["refusal"]["reason"] == "no_tools"
    assert plan.plan(cat, sel, tools_ok=lambda: True, store_problem=lambda: "old store")["refusal"]["message"] == "old store"
    assert plan.plan(cat, sel, **base)["refusal"] is None


def test_a_server_not_on_a_release_is_refused_and_one_behind_is_warned(monkeypatch):
    from domovoi import git_run, git_version
    from domovoi.kit_builder import service

    def run(tag):
        def r(cmd, **kw):
            out = tag if "describe" in cmd else COMMIT
            return subprocess.CompletedProcess(cmd, 0 if out else 128, out + "\n", "")
        return r

    monkeypatch.setattr(git_version, "tracking", lambda: {"channel": "main"})
    monkeypatch.setattr(git_run, "run", run("v1.5.0"))
    st = plan.release_state(".")
    assert st.release is None and st.refusal["message"].startswith("Offline kits are made from a release. "
                                                                    "This server follows main.")
    monkeypatch.setattr(git_version, "tracking", lambda: {"channel": "stable"})
    monkeypatch.setattr(git_version, "latest_release", lambda: "1.6.0")
    st = plan.release_state(".")
    assert st.release.version == "1.5.0" and st.release.commit == COMMIT and st.behind
    assert service._behind_warning(st)["message"] == ("Domovoi 1.6.0 is out. Update this server first to put "
                                                      "1.6.0 on the stick.")
    monkeypatch.setattr(git_run, "run", run(""))
    assert plan.release_state(".").refusal["reason"] == "not_release"


# ─── the build job ───────────────────────────────────────────────────────

class _FolderImage(FolderSink):
    """FatImage's place in tests: the same sink, into a folder (a short
    one: Windows' path limit and Ollama's 71-character blob names)."""

    last: Path | None = None

    def __init__(self, path, total, label="DOMOVOIKIT"):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix="k"))
        super().__init__(folder)
        _FolderImage.last = folder
        Path(path).write_bytes(b"image")   # the job hashes the image file


def _kit_parts(tmp: Path) -> list[Part]:
    blob = tmp / "blob.bin"
    blob.write_bytes(os.urandom(5000))
    return [
        Part(
            id="installer", group="base", kind="release", name="installer", version="1.5.0", licence="MIT", size=3,
            exact=True, download=0, acquire=lambda ctx: [Source("release/DomovoiSetup.exe", b"exe")]),
        Part(id="model", group="ai", kind="ollama-model", name="llama3.2:3b", version="d", size=5000,
             licence="LicenseRef-Llama-3.2-Community", exact=True, download=0, pinned_by="provision-manifest.conf",
             acquire=lambda ctx: [Source("ollama/models/blobs/sha256-" + "b" * 64, blob)]),
        Part(id="wake", group="base", kind="wake-word", name="domovoi_v1", version="1", size=4,
             licence="CC-BY-NC-SA-4.0", exact=True, download=0, acquire=lambda ctx: [Source("wake/domovoi_v1.onnx", b"wake")]),
    ]


def test_a_kit_is_made_kept_listed_and_passes_the_checker(kit_home, tmp_path, monkeypatch):
    monkeypatch.setattr(fatimage, "FatImage", _FolderImage)
    monkeypatch.setattr(plan, "free_bytes", lambda p: 10 ** 13)
    rel = Release("1.5.0", "v1.5.0", COMMIT, assets={"DomovoiSetup.exe": {"size": 3, "sha256": "e" * 64}})
    doc = jobs.build(rel, plan.Selection(), _kit_parts(tmp_path), core="http://192.168.1.20:6370")
    folder = _FolderImage.last
    report = verify(folder, release_key=None)
    assert report.ok, report.errors
    kit = json.loads((folder / "kit.json").read_text())
    assert {n["id"] for n in kit["notices"]} >= {"do-not-sell", "wake-word-nc-sa", "llama"}
    start = (folder / "START-HERE.txt").read_text()
    assert "App Store" in start and "Don't sell this stick" in start and "Built with Llama" in start
    assert jobs.build_info(doc["id"])["sha256"] == hashlib.sha256(b"image").hexdigest()
    lst = jobs.os_list(doc)
    assert lst["os_list"][0]["url"] == f"http://192.168.1.20:6370/v1/offline-kit/{doc['id']}/domovoi-offline-kit-1.5.0.img"
    assert lst["os_list"][0]["extract_sha256"] == doc["sha256"] and "init_format" not in lst["os_list"][0]


def test_cancel_stops_the_job_and_keeps_nothing(kit_home, tmp_path, monkeypatch):
    monkeypatch.setattr(fatimage, "FatImage", _FolderImage)
    monkeypatch.setattr(plan, "free_bytes", lambda p: 10 ** 13)
    jobs._cancel.set()
    with pytest.raises(jobs.Cancelled):
        jobs.build(Release("1.5.0", "v1.5.0", COMMIT), plan.Selection(), _kit_parts(tmp_path))
    jobs._cancel.clear()
    assert jobs.list_builds() == []
    assert not jobs.builds_root().exists() or not list(jobs.builds_root().glob(".*"))


def test_old_kits_are_deleted_after_three_days_and_only_the_newest_is_kept(kit_home):
    root = jobs.builds_root()
    now = time.time()
    ids = []
    for i, age in enumerate((10, 100, jobs.KEEP_SEC + 5)):
        bid = f"{i}" * 32
        (root / bid).mkdir(parents=True)
        (root / bid / jobs.BUILD_JSON).write_text(json.dumps({"format": 1, "id": bid, "made": now - age, "size": 1,
                                                              "sha256": "0" * 64, "domovoi_version": "1.5.0"}))
        ids.append(bid)
    (root / ".half-made").mkdir()
    assert sorted(jobs.prune(now)) == sorted(ids[1:])
    assert [b["id"] for b in jobs.list_builds()] == [ids[0]] and not (root / ".half-made").exists()
    assert jobs.prune(now + jobs.KEEP_SEC) == [ids[0]]


@pytest.mark.parametrize("bad", ["", "../" * 11, "a" * 31, "a" * 33, "a/" * 16])
def test_only_a_well_formed_id_names_a_kit(bad):
    assert not jobs.valid_id(bad) and jobs.build_info(bad) is None


def test_the_guide_names_the_release_the_installers_and_the_notices():
    g = guide.GuideInfo(version="1.5.0", tag="v1.5.0", commit=COMMIT, targets=("pc-x86_64", "mac-arm64"),
                        includes=("rooms",), installers={"DomovoiSetup.exe": "f" * 64},
                        notices=("do-not-sell", "wake-word-nc-sa"), household=("qwen2.5:14b",))
    t = guide.text(g).decode()
    assert "Domovoi 1.5.0" in t and "f" * 64 in t and "DomovoiSetup-mac.pkg" in t and "install.sh --kit" in t
    assert "qwen2.5:14b" in t and "not checked against a release" in t and "CC BY-NC-SA" in t
    page = guide.page(g).decode()
    assert page.startswith("<!doctype html>") and "<script" not in page


# ─── what a kit never carries (design section 7) ─────────────────────────

def test_a_kit_carries_nothing_from_the_household(kit_home, tmp_path, monkeypatch):
    """A seeded server's secrets and names never reach the stick: only
    files the parts name (pinned upstream files, caches, the release) and
    the generated guide and licence notes, which come from the release."""
    secrets_ = ["SECRET-SETUP-CODE-4711", "Kid's Room", "alex-admin-password", "kitchen-tablet"]
    home = tmp_path / "home" / ".domovoi"
    home.mkdir(parents=True)
    (home / ".env").write_text("SETUP_CODE=SECRET-SETUP-CODE-4711\nADMIN_PASSWORD=alex-admin-password\n")
    (home / "rooms.json").write_text(json.dumps({"rooms": ["Kid's Room", "kitchen-tablet"]}))
    monkeypatch.setattr(fatimage, "FatImage", _FolderImage)
    monkeypatch.setattr(plan, "free_bytes", lambda p: 10 ** 13)
    ps = _kit_parts(tmp_path) + [Part(
        id="lic", group="base", kind="licence", name="licences", version="1", licence="MIT", size=1, exact=True,
        download=0, acquire=lambda ctx: [Source("licences/README.txt", parts.readme([]).encode())])]
    doc = jobs.build(Release("1.5.0", "v1.5.0", COMMIT), plan.Selection(), ps)
    folder = _FolderImage.last
    blob = b"".join(f.read_bytes() for f in folder.rglob("*") if f.is_file())
    blob += (jobs.builds_root() / doc["id"] / jobs.BUILD_JSON).read_bytes()
    for s in secrets_:
        assert s.encode() not in blob


# ─── the dashboard card (JSX) ────────────────────────────────────────────

HARNESS = Path(__file__).with_name("jsx_render_harness.js")

PLAN = {
    "release": {"version": "1.5.0", "tag": "v1.5.0", "commit": COMMIT},
    "targets": [{"id": "pc-x86_64", "label": "A Windows or Linux PC", "ticked": True, "default": True},
                {"id": "mac-arm64", "label": "A Mac with an Apple chip", "ticked": False, "default": False}],
    "includes": [{"id": "ai", "label": "AI chat and questions", "bytes": 6_400_000_000, "ticked": True},
                 {"id": "vision", "label": "Pictures in chat", "bytes": 1_900_000_000, "ticked": True},
                 {"id": "rooms", "label": "Rooms: Raspberry Pis and spare computers", "bytes": 1_200_000_000, "ticked": True}],
    "household": [{"id": "model:qwen2.5:14b", "name": "qwen2.5:14b", "kind": "ollama-model", "bytes": 9_000_000_000,
                   "note": "not checked against a release", "ticked": False}],
    "refused_models": [{"name": "gemma3:12b", "reason": "gemma3:12b: its licence doesn't allow passing it on"}],
    "total_bytes": 14_100_000_000, "exact": True, "download_bytes": 2_400_000_000,
    "download_labels": ["the Windows parts"],
    "stick": {"needed_gb": 16, "chosen_gb": 8, "fits": False, "over_by": 6_100_000_000,
              "message": "Too big for an 8 GB stick by 6.1 GB. Leave out AI chat and questions (6.4 GB) to fit."},
    "stick_sizes": [8, 16, 32],
}
IDLE = {"running": False, "ok": None}
SEL = {"targets": ["pc-x86_64"], "includes": ["ai", "vision", "rooms"], "household": [], "stick_gb": 8}
BUILD = {"id": "A" * 32, "domovoi_version": "1.5.0", "size": 14_100_000_000, "expires": 4_000_000_000,
         "image_url": "http://192.168.1.20:6370/v1/offline-kit/x/i.img", "os_list_url": "http://192.168.1.20:6370/x/os-list.json",
         "manifest_url": "http://192.168.1.20:6370/x/m", "installers": {"DomovoiSetup.exe": "f" * 64}, "household": []}
FN = ["onToggle", "onStick", "onMake", "onStop", "onDelete"]
SCENARIOS = {
    "plan": {"file": "web/static/offline_kit.jsx", "component": "OfflineKitView", "fnProps": FN,
             "props": {"st": {"plan": PLAN, "refusal": None, "warning": None, "job": IDLE, "builds": []}, "sel": SEL}},
    "behind": {"file": "web/static/offline_kit.jsx", "component": "OfflineKitView", "fnProps": FN,
               "props": {"st": {"plan": PLAN, "refusal": None, "job": IDLE, "builds": [],
                                "warning": {"reason": "behind", "message": "Domovoi 1.6.0 is out. Update this server first to put 1.6.0 on the stick."}},
                         "sel": SEL}},
    "running": {"file": "web/static/offline_kit.jsx", "component": "OfflineKitView", "fnProps": FN,
                "props": {"st": {"plan": PLAN, "job": {"running": True, "stage": "write", "bytes_done": 8_400_000_000,
                                                       "bytes_todo": 14_100_000_000}, "builds": []}, "sel": SEL}},
    "ready": {"file": "web/static/offline_kit.jsx", "component": "OfflineKitView", "fnProps": FN,
              "props": {"st": {"plan": PLAN, "job": IDLE, "builds": [BUILD]}, "sel": SEL}},
}


@pytest.fixture(scope="module")
def rendered() -> dict:
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to render web/static JSX")
    proc = subprocess.run([node, str(HARNESS), str(REPO), json.dumps(SCENARIOS)], capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _text(els) -> str:
    return " ".join(str(e.get("text") or "") for e in els)


def test_the_card_shows_the_boxes_the_size_the_fit_and_the_notice(rendered):
    t = _text(rendered["plan"])
    assert "A Windows or Linux PC" in t and "A Mac with an Apple chip" in t and "Pictures in chat" in t
    assert "14.1 GB" in t and "Needs a" in t and "16 GB" in t and "Too big for an 8 GB stick by 6.1 GB" in t
    assert "2.4 GB will be downloaded first (the Windows parts)" in t
    assert "not checked against a release" in t and "gemma3:12b" in t
    assert "Don’t sell this stick" in t and "make the kit" in t


def test_a_server_behind_the_latest_release_is_warned_but_may_go_on(rendered):
    t = _text(rendered["behind"])
    assert "Domovoi 1.6.0 is out" in t and "make it anyway" in t and "update first" in t


def test_while_it_runs_one_bar_and_a_stop(rendered):
    els = rendered["running"]
    bar = [e for e in els if e["props"].get("role") == "progressbar"]
    assert bar and bar[0]["props"]["aria-valuenow"] == 60
    t = _text(els)
    assert "Making the kit" in t and "8.4 GB of 14.1 GB" in t and "stop" in t


def test_when_done_imager_download_and_delete(rendered):
    els = rendered["ready"]
    t = _text(els)
    assert "Your offline kit is ready." in t and "Everything on the stick will be replaced." in t
    assert any(str(e["props"].get("href", "")).startswith("rpi-imager://?repo=") for e in els)
    assert "delete the kit" in t and "deleted by itself" in t
