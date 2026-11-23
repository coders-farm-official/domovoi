"""The offline kit's format and checker (domovoi/offline_kit; docs/OFFLINE_KIT.md).

Pure file tests: no DB, no network. Kits are written with a tiny
``part_size`` so splitting is tested with bytes, not gigabytes (the 1 MiB
floor a reader holds kit.json to is lowered here, and tested on its own
below). The release
signature is checked with the throwaway key and manifests ssh-keygen made
for the Windows tray's tests (windows/tests/release-sig/).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi.offline_kit import KIT_FORMAT, Component, Source, reassemble, verify, write_kit
from domovoi.offline_kit import format as fmt
from domovoi.offline_kit.__main__ import main
from domovoi.offline_kit.sshsig import RELEASE_KEY, ed25519_verify, parse_public_key

REPO = Path(__file__).resolve().parents[2]
SIG = REPO / "windows" / "tests" / "release-sig"
TEST_KEY = (SIG / "test-key.pub").read_text(encoding="ascii")
VERSION = "1.0.1"  # the fixture manifest's
COMMIT = "c0ffee0123456789abcdef0123456789abcdef01"
PART = 4000  # above the release fixtures (3000 bytes), which are never split
BLOB = bytes(range(256)) * 39 + b"tail"  # 9988 bytes: parts of 4000, 4000, 1988
BLOB_PATH = "ollama/models/blobs/sha256-" + hashlib.sha256(BLOB).hexdigest()


REAL_MIN_PART_SIZE = fmt.MIN_PART_SIZE


@pytest.fixture(autouse=True)
def _tiny_parts(monkeypatch):
    monkeypatch.setattr(fmt, "MIN_PART_SIZE", 1)


def _components() -> list[Component]:
    return [
        Component("guide", "START-HERE", VERSION, "MIT",
                  [Source("START-HERE.txt", b"Plug in the stick. Open release/DomovoiSetup.exe.\n")]),
        Component("release", "Domovoi Setup", VERSION, "MIT", [
            Source("release/DomovoiSetup.exe", SIG / "setup.bin"),
            Source("release/DomovoiSetup.manifest.json", SIG / "manifest-1.0.1.json"),
            Source("release/DomovoiSetup.manifest.json.sig", SIG / "manifest-1.0.1.json.sig"),
        ], pinned_by="the release's signed manifest"),
        Component("ollama-model", "qwen3:4b", "sha256-abc", "Apache-2.0", [
            Source("ollama/models/manifests/registry.ollama.ai/library/qwen3/4b", b'{"layers": []}\n'),
            Source(BLOB_PATH, BLOB),
        ], pinned_by="scripts/linux/provision-manifest.conf"),
        Component("ollama-model", "llama3.2:3b", "sha256-def", "LicenseRef-Llama-3.2-Community", [
            Source("ollama/models/manifests/registry.ollama.ai/library/llama3.2/3b", b"{}\n"),
            Source(BLOB_PATH, BLOB),  # a blob two models share is stored once
        ], pinned_by="scripts/linux/provision-manifest.conf"),
        Component("wake-word", "domovoi_v1", "1", "CC-BY-NC-SA-4.0",
                  [Source("wake/domovoi_v1.onnx", b"\x08onnx" * 7)], pinned_by="wake model card"),
    ]


def _make(tmp_path: Path, **kw) -> Path:
    root = tmp_path / "DOMOVOIKIT"
    write_kit(root, version=VERSION, commit=COMMIT, components=kw.pop("components", _components()),
              includes=["ai"], android_version_code=261120010,
              created_at="2026-11-22T10:15:00Z", part_size=kw.pop("part_size", PART), **kw)
    return root


def _check(root: Path):
    return verify(root, release_key=TEST_KEY)


def _doc(root: Path) -> dict:
    return json.loads((root / "kit.json").read_text(encoding="utf-8"))


def _rewrite(root: Path, doc: dict) -> None:
    """Change kit.json and keep SHA256SUMS's line for it in step, so only
    the change itself is wrong."""
    raw = (json.dumps(doc, indent=2) + "\n").encode("utf-8")
    (root / "kit.json").write_bytes(raw)
    sums, _ = fmt.parse_sums((root / "SHA256SUMS").read_bytes())
    sums["kit.json"] = hashlib.sha256(raw).hexdigest()
    (root / "SHA256SUMS").write_bytes(fmt.format_sums(sums))


# ── Round trip ──────────────────────────────────────────────────────────


def test_round_trip_write_then_verify(tmp_path: Path) -> None:
    root = _make(tmp_path)
    report = _check(root)
    assert report.errors == [] and report.notes == []
    assert report.ok
    doc = _doc(root)
    assert doc["kit_format"] == KIT_FORMAT == 1
    assert (doc["domovoi_version"], doc["tag"], doc["commit"]) == (VERSION, "v1.0.1", COMMIT)
    assert doc["targets"] == ["pc-x86_64"]  # PC ticked, Mac not, by default
    assert doc["part_size"] == PART
    assert [n["id"] for n in doc["notices"]] == ["do-not-sell", "llama", "wake-word-nc-sa"]
    # The big file is stored as parts only.
    assert not (root / BLOB_PATH).exists()
    assert [(root / p).stat().st_size for p in fmt.part_paths(BLOB_PATH, 3)] == [4000, 4000, 1988]
    # SHA256SUMS is sha256sum's format, sorted, and covers kit.json.
    lines = (root / "SHA256SUMS").read_bytes().decode().splitlines()
    paths = [ln.split("  ", 1)[1] for ln in lines]
    assert paths == sorted(paths) and "kit.json" in paths and BLOB_PATH + ".part02" in paths
    assert BLOB_PATH not in paths
    for ln in lines:
        sha, path = ln.split("  ", 1)
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == sha
    assert b"\r" not in (root / "kit.json").read_bytes()


def test_reassemble_joins_and_checks(tmp_path: Path) -> None:
    root = _make(tmp_path)
    entry = next(fmt.split_files(_doc(root)))
    out = tmp_path / "joined"
    reassemble(root, entry, out)
    assert out.read_bytes() == BLOB
    (root / entry["parts"][1]["path"]).write_bytes(b"x" * PART)
    out.unlink()
    with pytest.raises(ValueError, match="part02 is damaged"):
        reassemble(root, entry, out)
    assert not out.exists() and not (tmp_path / "joined.joining").exists()


def test_cli_verify_ok_and_exit_codes(tmp_path: Path, capsys) -> None:
    root = _make(tmp_path)
    key = tmp_path / "test-key.pub"
    key.write_text(TEST_KEY, encoding="ascii")
    assert main(["verify", str(root), "--release-key", str(key)]) == 0
    assert "The kit is fine: Domovoi 1.0.1" in capsys.readouterr().out
    (root / "wake" / "domovoi_v1.onnx").unlink()
    (root / "wake" / "stray.txt").write_text("hi")
    assert main(["verify", str(root), "--release-key", str(key)]) == 1
    out = capsys.readouterr().out
    assert "wake/domovoi_v1.onnx is missing." in out and "wake/stray.txt is not part of the kit" in out
    assert main(["verify", str(root), "--no-release-check", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and len(data["errors"]) >= 2


def test_checker_runs_with_the_standard_library_alone(tmp_path: Path) -> None:
    """-S: no site-packages, as on a bare box with only Python (its own
    process: the real 1 MiB part floor)."""
    root = _make(tmp_path, part_size=REAL_MIN_PART_SIZE)
    key = tmp_path / "k.pub"
    key.write_text(TEST_KEY, encoding="ascii")
    p = subprocess.run([sys.executable, "-S", "-m", "domovoi.offline_kit", "verify", str(root),
                        "--release-key", str(key)], cwd=REPO, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "The kit is fine" in p.stdout


def test_package_imports_only_the_standard_library() -> None:
    import ast
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    for path in (REPO / "domovoi" / "offline_kit").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert roots <= allowed, f"{path.name} imports {roots - allowed}"


# ── Splitting ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("size,want", [
    (0, []), (99, []), (100, []), (101, [100, 1]), (200, [100, 100]), (250, [100, 100, 50]),
])
def test_part_sizes(size: int, want: list[int]) -> None:
    assert fmt.part_sizes(size, 100) == want


def test_part_names() -> None:
    assert fmt.part_paths("a/b", 2) == ["a/b.part01", "a/b.part02"]
    assert fmt.part_paths("a/b", 100)[0] == "a/b.part001"
    assert fmt.part_paths("a/b", 100)[-1] == "a/b.part100"


def test_default_part_size_fits_fat32() -> None:
    assert fmt.PART_SIZE < fmt.FAT32_MAX_FILE == 4 * 1024**3 - 1


def test_a_file_over_the_limit_is_split_exactly(tmp_path: Path) -> None:
    data = b"z" * (3 * PART)
    comps = _components() + [Component("speaker-model", "eres2net", "1", "Apache-2.0",
                                       [Source("speech/speaker/model.onnx", data)])]
    root = _make(tmp_path, components=comps)
    entry = next(e for e in fmt.split_files(_doc(root)) if e["path"] == "speech/speaker/model.onnx")
    assert [p["size"] for p in entry["parts"]] == [PART] * 3  # no empty fourth part
    assert _check(root).ok


# ── Failure modes: each reported, in plain words ─────────────────────────


def test_missing_file(tmp_path: Path) -> None:
    root = _make(tmp_path)
    (root / "START-HERE.txt").unlink()
    assert _check(root).errors == ["START-HERE.txt is missing."]


def test_wrong_hash(tmp_path: Path) -> None:
    root = _make(tmp_path)
    p = root / "wake" / "domovoi_v1.onnx"
    data = bytearray(p.read_bytes())
    data[0] ^= 1
    p.write_bytes(bytes(data))
    assert _check(root).errors == ["wake/domovoi_v1.onnx is damaged: its contents don't match "
                                   "kit.json. Make the stick again."]


def test_wrong_size(tmp_path: Path) -> None:
    root = _make(tmp_path)
    (root / "wake" / "domovoi_v1.onnx").write_bytes(b"short")
    errors = _check(root).errors
    assert len(errors) == 1 and "wake/domovoi_v1.onnx is the wrong size: 5 bytes" in errors[0]


def test_missing_part(tmp_path: Path) -> None:
    root = _make(tmp_path)
    (root / f"{BLOB_PATH}.part02").unlink()
    assert _check(root).errors == [f"{BLOB_PATH}.part02 is missing.",
                                   f"{BLOB_PATH} can't be put back together from its parts."]


def test_parts_in_the_wrong_order_on_the_stick(tmp_path: Path) -> None:
    root = _make(tmp_path)
    one, two = root / f"{BLOB_PATH}.part01", root / f"{BLOB_PATH}.part02"
    a, b = one.read_bytes(), two.read_bytes()
    one.write_bytes(b)
    two.write_bytes(a)
    errors = _check(root).errors
    assert f"{BLOB_PATH}.part01 is damaged: its contents don't match kit.json. Make the stick again." in errors
    assert f"{BLOB_PATH}.part02 is damaged: its contents don't match kit.json. Make the stick again." in errors
    assert (f"{BLOB_PATH}: its parts, joined in order, are not the file kit.json names. "
            "Make the stick again.") in errors


def test_parts_listed_in_the_wrong_order(tmp_path: Path) -> None:
    root = _make(tmp_path)
    doc = _doc(root)
    entry = doc["components"][2]["files"][1]
    entry["parts"][0], entry["parts"][1] = entry["parts"][1], entry["parts"][0]
    doc["components"][3]["files"][1] = entry
    _rewrite(root, doc)
    errors = _check(root).errors
    assert any("part 1: its name must be " + BLOB_PATH + ".part01" in e for e in errors)
    assert errors[-1] == "The files weren't checked, because kit.json is not right."


def test_joined_parts_not_the_named_file(tmp_path: Path) -> None:
    """Every part matches kit.json, but kit.json's whole-file hash is not
    what they join into."""
    root = _make(tmp_path)
    doc = _doc(root)
    for c in (2, 3):
        doc["components"][c]["files"][1]["sha256"] = "0" * 64
    _rewrite(root, doc)
    assert _check(root).errors == [f"{BLOB_PATH}: its parts, joined in order, are not the file "
                                   "kit.json names. Make the stick again."]


def test_extra_files(tmp_path: Path) -> None:
    root = _make(tmp_path)
    (root / "speech").mkdir()
    (root / "speech" / "extra.onnx").write_bytes(b"x")
    (root / "notes.txt").write_text("hi")
    (root / BLOB_PATH).write_bytes(BLOB)  # whole beside its parts
    # What operating systems leave on a stick is ignored.
    (root / "System Volume Information").mkdir()
    (root / "System Volume Information" / "IndexerVolumeGuid").write_text("x")
    (root / ".DS_Store").write_bytes(b"x")
    (root / "wake" / "._domovoi_v1.onnx").write_bytes(b"x")
    assert sorted(_check(root).errors) == sorted([
        "notes.txt is not part of the kit (kit.json doesn't list it).",
        "speech/extra.onnx is not part of the kit (kit.json doesn't list it).",
        f"{BLOB_PATH} is here whole, but kit.json says it is stored in parts only.",
    ])


def test_an_unlisted_folder_and_a_link_are_reported(tmp_path: Path) -> None:
    """M12 (review 2026-11-23): a folder kit.json lists nothing in (an
    extra apt suite) and a link (a linked suite folder) are named, not
    passed because they hold no unlisted plain file."""
    import os

    root = _make(tmp_path)
    (root / "apt" / "amd64" / "ubuntu" / "dists" / "evil").mkdir(parents=True)
    errors = _check(root).errors
    assert "apt/amd64/ubuntu/dists/evil/ is not part of the kit (kit.json lists nothing in it)." in errors
    try:
        os.symlink(root / "wake", root / "wake-link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system can't make a link here")
    assert "wake-link is a link, which a kit never holds." in _check(root).errors


def test_every_problem_is_reported_not_just_the_first(tmp_path: Path) -> None:
    root = _make(tmp_path)
    (root / "START-HERE.txt").unlink()
    (root / "wake" / "domovoi_v1.onnx").write_bytes(b"\x08onnx" * 6 + b"\x09onnx")
    (root / "wake" / "extra.bin").write_bytes(b"x")
    (root / f"{BLOB_PATH}.part03").unlink()
    errors = _check(root).errors
    assert len(errors) == 5, errors


def test_no_kit_json(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    errors = verify(tmp_path / "x").errors
    assert errors == ["There is no kit.json here, so this is not an offline kit (or the stick is "
                      "damaged)."]


def test_kit_json_not_json(tmp_path: Path) -> None:
    root = _make(tmp_path)
    (root / "kit.json").write_text("{nope", encoding="utf-8")
    errors = _check(root).errors
    assert errors[0].startswith("kit.json is not valid JSON")


def test_newer_kit_format_is_refused(tmp_path: Path) -> None:
    root = _make(tmp_path)
    doc = _doc(root)
    doc["kit_format"] = KIT_FORMAT + 1
    doc["something_new"] = {"x": 1}
    _rewrite(root, doc)
    errors = _check(root).errors
    assert len(errors) == 1
    assert errors[0].startswith(f"This kit was made by a newer Domovoi (kit format {KIT_FORMAT + 1})")


def test_unknown_fields_are_noted_not_errors(tmp_path: Path) -> None:
    """A server reads a newer release's kit to update from it, so new
    fields within one kit_format are ignored."""
    root = _make(tmp_path)
    doc = _doc(root)
    doc["made_by"] = "a newer server"
    doc["components"][0]["colour"] = "blue"
    _rewrite(root, doc)
    report = _check(root)
    assert report.ok
    assert len(report.notes) == 2 and "'made_by'" in report.notes[0]


def _break(doc: dict, how: str) -> None:
    comps = doc["components"]
    if how == "commit":
        doc["commit"] = "abc"
    elif how == "tag":
        doc["tag"] = "v9.9.9"
    elif how == "created_at":
        doc["created_at"] = "yesterday"
    elif how == "target":
        doc["targets"] = ["pc-x86_64", "toaster"]
    elif how == "no_targets":
        doc["targets"] = []
    elif how == "include":
        doc["includes"] = ["everything"]
    elif how == "part_size":
        doc["part_size"] = 5 * 1024**3
    elif how == "kind":
        comps[4]["kind"] = "mystery"
    elif how == "wrong_folder":
        comps[4]["kind"] = "phone-app"
    elif how == "licence":
        comps[4]["licence"] = "WTFPL-ish"
    elif how == "never_licence":
        comps[4]["licence"] = "LicenseRef-Qwen-Research"
    elif how == "household_pinned":
        comps[4]["household_added"] = True
    elif how == "household_kind":
        comps[0]["household_added"] = True
        comps[0]["pinned_by"] = None
    elif how == "traversal":
        comps[4]["files"][0]["path"] = "wake/../../etc/passwd"
    elif how == "backslash":
        comps[4]["files"][0]["path"] = "wake\\domovoi_v1.onnx"
    elif how == "fat32_name":
        comps[4]["files"][0]["path"] = "wake/what?.onnx"
    elif how == "lists_kit_json":
        comps[0]["files"][0]["path"] = "kit.json"
        comps[0]["kind"] = "release"
    elif how == "sha":
        comps[4]["files"][0]["sha256"] = "XYZ"
    elif how == "size":
        comps[4]["files"][0]["size"] = -1
    elif how == "too_big_unsplit":
        comps[4]["files"][0]["size"] = PART + 1
    elif how == "split_when_small":
        comps[4]["files"][0]["parts"] = [{"path": "wake/domovoi_v1.onnx.part01", "size": 35,
                                          "sha256": "0" * 64}]
    elif how == "part_count":
        for c in (2, 3):
            comps[c]["files"][1]["parts"] = comps[c]["files"][1]["parts"][:2]
    elif how == "conflict":
        comps[3]["files"][1] = dict(comps[3]["files"][1], parts=None, size=1, sha256="1" * 64)
    elif how == "case_clash":
        comps[0]["files"].append({"path": "START-HERE.TXT", "size": 1, "sha256": "2" * 64})
    elif how == "missing_notice":
        doc["notices"] = [n for n in doc["notices"] if n["id"] != "llama"]
    elif how == "no_do_not_sell":
        doc["notices"] = [n for n in doc["notices"] if n["id"] != "do-not-sell"]
    elif how == "no_components":
        doc["components"] = []
    elif how == "bool_version_code":
        doc["android_version_code"] = True
    else:
        raise AssertionError(how)


@pytest.mark.parametrize("how,expect", [
    ("commit", "commit must be"),
    ("tag", "tag must be v1.0.1"),
    ("created_at", "created_at must be"),
    ("target", "'toaster' is not one of"),
    ("no_targets", "targets must list"),
    ("include", "'everything' is not one of"),
    ("part_size", "part_size must be"),
    ("kind", "kind 'mystery' is not one of"),
    ("wrong_folder", "a phone-app belongs in phone/"),
    ("licence", "is not on the list of licences a kit may carry"),
    ("never_licence", "doesn't allow passing it on"),
    ("household_pinned", "can't have pinned_by"),
    ("household_kind", "a household can add only"),
    ("traversal", "has an empty, '.' or '..' part"),
    ("backslash", "must be a relative path"),
    ("fat32_name", "a FAT32 stick can't hold"),
    ("lists_kit_json", "is the kit's own index"),
    ("sha", "sha256 must be 64 lowercase hex"),
    ("size", "size must be a whole number"),
    ("too_big_unsplit", "must be stored in parts"),
    ("split_when_small", "only a file bigger than part_size"),
    ("part_count", "it splits into 3 parts, kit.json lists 2"),
    ("conflict", "is listed twice with different contents"),
    ("case_clash", "differ only in capitals"),
    ("missing_notice", "the 'llama' notice"),
    ("no_do_not_sell", "the 'do-not-sell' notice"),
    ("no_components", "components must list"),
    ("bool_version_code", "android_version_code must be"),
])
def test_bad_schema(tmp_path: Path, how: str, expect: str) -> None:
    root = _make(tmp_path)
    doc = _doc(root)
    _break(doc, how)
    _rewrite(root, doc)
    errors = _check(root).errors
    assert any(expect in e for e in errors), errors
    assert errors[-1] == "The files weren't checked, because kit.json is not right."


# ── SHA256SUMS ──────────────────────────────────────────────────────────


def test_sums_disagree(tmp_path: Path) -> None:
    root = _make(tmp_path)
    sums, _ = fmt.parse_sums((root / "SHA256SUMS").read_bytes())
    sums["wake/domovoi_v1.onnx"] = "f" * 64
    del sums["START-HERE.txt"]
    sums["wake/ghost.onnx"] = "e" * 64
    (root / "SHA256SUMS").write_bytes(fmt.format_sums(sums) + b"not a sums line\n")
    errors = _check(root).errors
    assert "SHA256SUMS doesn't list START-HERE.txt." in errors
    assert "SHA256SUMS and kit.json disagree about wake/domovoi_v1.onnx." in errors
    assert "SHA256SUMS lists wake/ghost.onnx, which kit.json doesn't." in errors
    assert any(e.startswith("SHA256SUMS line") for e in errors)
    assert len(errors) == 4


def test_kit_json_changed_after_sums(tmp_path: Path) -> None:
    root = _make(tmp_path)
    doc = _doc(root)
    doc["includes"] = ["ai", "vision"]
    (root / "kit.json").write_text(json.dumps(doc), encoding="utf-8")
    assert _check(root).errors == ["kit.json is not the one SHA256SUMS was made with (it was "
                                   "changed or damaged)."]


def test_missing_sums(tmp_path: Path) -> None:
    root = _make(tmp_path)
    (root / "SHA256SUMS").unlink()
    assert _check(root).errors == ["SHA256SUMS is missing."]


# ── The release's signed manifest ───────────────────────────────────────


def test_release_key_is_the_repos_release_key() -> None:
    pinned = parse_public_key(RELEASE_KEY)
    assert pinned is not None
    assert pinned == parse_public_key((REPO / "windows/installer/release-signing.pub").read_text())


def test_pinned_key_refuses_a_kit_signed_by_another_key(tmp_path: Path) -> None:
    root = _make(tmp_path)
    errors = verify(root).errors  # Domovoi's real release key
    assert errors == ["release/DomovoiSetup.manifest.json can't be trusted: it is signed with a "
                      "key other than Domovoi's release key."]


def test_skipping_the_release_check_says_so(tmp_path: Path) -> None:
    report = verify(_make(tmp_path), release_key=None)
    assert report.ok and report.notes == ["The release signature was not checked (asked not to)."]


def _swap_release_file(root: Path, name: str, data: bytes) -> None:
    """Replace a release file and keep kit.json and SHA256SUMS in step, so
    only the signature check can object."""
    path = f"release/{name}"
    (root / path).write_bytes(data)
    doc = _doc(root)
    for f in doc["components"][1]["files"]:
        if f["path"] == path:
            f["size"], f["sha256"] = len(data), hashlib.sha256(data).hexdigest()
    sums, _ = fmt.parse_sums((root / "SHA256SUMS").read_bytes())
    sums[path] = hashlib.sha256(data).hexdigest()
    (root / "SHA256SUMS").write_bytes(fmt.format_sums(sums))
    _rewrite(root, doc)


def test_tampered_manifest(tmp_path: Path) -> None:
    root = _make(tmp_path)
    m = (SIG / "manifest-1.0.1.json").read_bytes().replace(b'"size": 3000', b'"size": 3001')
    _swap_release_file(root, "DomovoiSetup.manifest.json", m)
    errors = _check(root).errors
    assert errors == ["release/DomovoiSetup.manifest.json can't be trusted: its signature doesn't "
                      "match the file (it was changed, or signed by someone else)."]


def test_wrong_namespace_signature(tmp_path: Path) -> None:
    root = _make(tmp_path)
    _swap_release_file(root, "DomovoiSetup.manifest.json.sig",
                       (SIG / "manifest-1.0.1.json.wrong-namespace.sig").read_bytes())
    assert "its signature is for something else" in _check(root).errors[0]


def test_manifest_of_another_release(tmp_path: Path) -> None:
    root = _make(tmp_path)
    _swap_release_file(root, "DomovoiSetup.manifest.json", (SIG / "manifest-0.9.0.json").read_bytes())
    _swap_release_file(root, "DomovoiSetup.manifest.json.sig",
                       (SIG / "manifest-0.9.0.json.sig").read_bytes())
    errors = _check(root).errors
    assert len(errors) == 1 and "is for Domovoi 0.9.0" in errors[0] and "kit.json says 1.0.1" in errors[0]


def test_installer_not_the_signed_one(tmp_path: Path) -> None:
    root = _make(tmp_path)
    _swap_release_file(root, "DomovoiSetup.exe", b"MZ not the release's build")
    assert _check(root).errors == ["release/DomovoiSetup.exe is not the installer the signed "
                                   "release manifest names."]


def test_mac_target_needs_the_mac_manifest(tmp_path: Path) -> None:
    root = _make(tmp_path, targets=["pc-x86_64", "mac-arm64"])
    errors = _check(root).errors
    assert errors == ["release/DomovoiSetup-mac.manifest.json or its .sig is missing, so nothing "
                      "ties the kit's A Mac with an Apple chip installer to a Domovoi release."]


def test_ed25519_rfc8032_vectors() -> None:
    h = bytes.fromhex
    assert ed25519_verify(
        h("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"), b"",
        h("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
          "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"))
    good = h("92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
             "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")
    key = h("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
    assert ed25519_verify(key, b"\x72", good)
    assert not ed25519_verify(key, b"\x73", good)
    assert not ed25519_verify(key, b"\x72", good[:32] + bytes(32))
    # S at or above the group order is refused (malleability).
    order = 2**252 + 27742317777372353535851937790883648493
    s = int.from_bytes(good[32:], "little") + order
    assert s < 2**256
    assert not ed25519_verify(key, b"\x72", good[:32] + s.to_bytes(32, "little"))


# ── kit.json can't make a reader do unbounded work ──────────────────────


@pytest.mark.parametrize("part_size,size,expect", [
    (1, 10**15, "part_size must be a whole number of bytes from 1048576"),
    (REAL_MIN_PART_SIZE, 1024**4 + 1, "size must be a whole number of bytes, at most"),
    (REAL_MIN_PART_SIZE, 1024**4, "it splits into 1048576 parts, kit.json lists 3"),
])
def test_part_counts_are_bounded_and_arithmetic(tmp_path: Path, monkeypatch, part_size, size, expect) -> None:
    root = _make(tmp_path)
    doc = _doc(root)
    monkeypatch.setattr(fmt, "MIN_PART_SIZE", REAL_MIN_PART_SIZE)
    monkeypatch.setattr(fmt, "part_sizes", lambda *a: pytest.fail("a reader must not list the parts"))
    doc["part_size"] = part_size
    entry = next(e for c in doc["components"] for e in c["files"] if e.get("parts"))
    entry["size"] = size
    problems, _ = fmt.validate(doc)
    assert any(expect in p for p in problems), problems


def test_an_oversized_kit_json_is_refused_unread(tmp_path: Path) -> None:
    root = _make(tmp_path)
    with open(root / "kit.json", "r+b") as f:
        f.truncate(fmt.MAX_KIT_JSON + 1)
    errors = _check(root).errors
    assert errors and "kit.json is bigger than any kit's" in errors[0]


# ── write_kit refuses what it can't write ───────────────────────────────


def test_a_file_that_doesn_t_match_its_expected_sha256_is_refused(tmp_path: Path) -> None:
    comps = _components()
    comps[2] = Component("ollama-model", "qwen3:4b", "sha256-abc", "Apache-2.0", [
        Source(BLOB_PATH, b"X" + BLOB[1:], hashlib.sha256(BLOB).hexdigest())],
        pinned_by="scripts/linux/provision-manifest.conf")
    with pytest.raises(ValueError, match="doesn't match its pinned SHA-256"):
        _make(tmp_path, components=comps)
    assert not (tmp_path / "DOMOVOIKIT" / "kit.json").exists()


def test_write_refuses_a_folder_with_files(tmp_path: Path) -> None:
    root = tmp_path / "k"
    root.mkdir()
    (root / "old.txt").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        write_kit(root, version=VERSION, commit=COMMIT, components=_components(), part_size=PART)


def test_write_checks_licences_before_copying(tmp_path: Path) -> None:
    comps = _components() + [Component("ollama-model", "gemma3:12b", "x", "LicenseRef-Gemma",
                                       [Source("ollama/models/blobs/sha256-g", b"g")])]
    root = tmp_path / "k"
    with pytest.raises(ValueError, match="doesn't allow passing it on"):
        write_kit(root, version=VERSION, commit=COMMIT, components=comps, part_size=PART)
    assert list(root.iterdir()) == []


def test_household_added_component_brings_its_notice(tmp_path: Path) -> None:
    comps = _components() + [Component("ollama-model", "qwen2.5:14b", "sha256-q", "Apache-2.0",
                                       [Source("ollama/models/blobs/sha256-q", b"q" * 10)],
                                       household_added=True)]
    root = _make(tmp_path, components=comps)
    doc = _doc(root)
    assert "household-added" in [n["id"] for n in doc["notices"]]
    assert doc["components"][-1]["household_added"] is True and doc["components"][-1]["pinned_by"] is None
    assert _check(root).ok


def test_layout_folders_match_the_kinds() -> None:
    assert fmt.FOLDERS == ("apt", "images", "licences", "linux", "mac", "ollama", "phone",
                           "python", "release", "rooms", "source", "speech", "wake", "windows")
