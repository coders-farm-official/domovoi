"""release.yml's SBOMs and build provenance stay what they are.

Pure file checks, no network, no DB:

* The ``attest`` job attests every file of the release: its subjects are
  the SHA256SUMS the signing job made (which finish-release writes for
  every kind of asset: the APKs, the Setup and its signatures, the tray,
  the satellite Setup, the SBOMs) plus SHA256SUMS itself, and only once
  the release on GitHub holds exactly those files. Its inline check is run
  here against a release that matches and ones that don't.
* attest-build-provenance is pinned by commit SHA and runs in that job
  alone, with exactly ``contents: read``, ``id-token: write`` and
  ``attestations: write``; the job has no environment, no secret, no
  checkout. The signing job never gets id-token or attestations.
* The ``sbom`` job makes the three SBOMs with a read-only token and no
  secret, and the signing job hands them to finish-release (``--sbom-dir``,
  ``--provenance``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
DOC = yaml.safe_load(TEXT)
JOBS = DOC["jobs"]
ATTEST_USES = re.compile(r"^actions/attest-build-provenance@[0-9a-f]{40}$")


def _fr():
    spec = importlib.util.spec_from_file_location("finish_release_for_provenance",
                                                  REPO / "scripts" / "release" / "finish_release.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fr = _fr()


def _job_text(name: str) -> str:
    return yaml.safe_dump(JOBS[name])


def _steps_using(prefix: str) -> list[tuple[str, dict]]:
    return [(job, step) for job, spec in JOBS.items() for step in spec.get("steps", [])
            if str(step.get("uses", "")).startswith(prefix)]


# ─── the attest job ──────────────────────────────────────────────────────

def test_attest_build_provenance_is_pinned_and_runs_in_the_attest_job_alone():
    uses = _steps_using("actions/attest")
    assert [job for job, _ in uses] == ["attest"], "provenance is made in the attest job only"
    (_, step), = uses
    assert ATTEST_USES.match(step["uses"])
    line = next(line for line in TEXT.splitlines() if "actions/attest-build-provenance@" in line)
    assert re.search(r"#\s*v\d+\.\d+\.\d+\s*$", line), "the pin names its release"
    assert step["with"] == {"subject-checksums": "${{ runner.temp }}/attest/subjects.txt"}


def test_the_attest_job_has_the_least_it_needs():
    job = JOBS["attest"]
    assert job["permissions"] == {"contents": "read", "id-token": "write", "attestations": "write"}
    assert "environment" not in job
    assert "secrets." not in _job_text("attest")
    assert not _steps_using("actions/checkout@") or all(j != "attest" for j, _ in _steps_using("actions/checkout@"))
    assert set(job["needs"]) == {"plan", "release"}
    # Runs only after a release job that succeeded, even when a job further
    # up (the app's build, on a server-only release) was skipped.
    cond = job["if"]
    assert "!cancelled()" in cond and "needs.release.result == 'success'" in cond
    assert job["env"]["SUMS_B64"] == "${{ needs.release.outputs.sums }}"


def test_no_other_job_can_mint_an_oidc_token_or_write_attestations():
    assert DOC["permissions"] == {}
    for name, job in JOBS.items():
        perms = job.get("permissions") or {}
        if name != "attest":
            assert "id-token" not in perms and "attestations" not in perms, name
    assert JOBS["release"]["permissions"] == {"contents": "write"}
    assert JOBS["release"]["environment"] == "release"


def test_the_signing_job_hands_on_the_sha256sums_it_published():
    job = JOBS["release"]
    assert job["outputs"]["sums"] == "${{ steps.published.outputs.sums }}"
    (step,) = [s for s in job["steps"] if s.get("id") == "published"]
    assert "env" not in step, "the step that hands SHA256SUMS on sees no secret"
    assert 'windows/build/release/$TAG/SHA256SUMS' in step["run"] and "base64 -w0" in step["run"]
    names = [s.get("name") or s.get("uses") for s in job["steps"]]
    finish = next(i for i, n in enumerate(names) if str(n).startswith("Finish the release"))
    assert finish < names.index(step["name"]), "after finish-release published"


def _attest_check() -> str:
    run = JOBS["attest"]["steps"][0]["run"]
    return run.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _every_kind() -> dict[str, bytes]:
    """One file of each kind a release can hold."""
    return {
        "domovoi-1.3.0-arm64-v8a.apk": b"apk 64", "domovoi-1.3.0-armeabi-v7a.apk": b"apk 32",
        fr.SETUP_ASSET: b"MZ setup", **{n: n.encode() for n in fr.SIGNATURE_ASSETS},
        fr.TRAY_ASSET: b"MZ tray", fr.SATELLITE_SETUP_ASSET: b"MZ sat",
        **{n: b'{"bomFormat": "CycloneDX"}' + n.encode() for n in fr.SBOM_ASSETS},
    }


def _sums_for(files: dict[str, bytes]) -> bytes:
    """finish-release's own SHA256SUMS for these files."""
    sha = {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}
    rel = fr.Release(tag="v1.3.0", version="1.3.0", prerelease=False, tag_commit="1" * 40, cert="ab" * 32,
                     apks=[{"file": n, "sha256": sha[n], "abi": "arm64-v8a"} for n in sorted(files) if n.endswith(".apk")],
                     setup_sha256=sha[fr.SETUP_ASSET], tray_sha256=sha[fr.TRAY_ASSET],
                     satellite_setup_sha256=sha[fr.SATELLITE_SETUP_ASSET],
                     signatures={n: sha[n] for n in fr.SIGNATURE_ASSETS},
                     sboms={n: sha[n] for n in fr.SBOM_ASSETS})
    return fr.sums_text(rel).encode("ascii")


def _run_check(tmp_path: Path, sums: bytes, assets: list[dict]) -> subprocess.CompletedProcess:
    d = tmp_path / "attest"
    d.mkdir()
    (d / "SHA256SUMS").write_bytes(sums)
    (d / "release.json").write_text(json.dumps({"assets": assets}), encoding="utf-8")
    script = tmp_path / "check.py"
    script.write_text(_attest_check(), encoding="utf-8")
    env = {**os.environ, "TAG": "v1.3.0", "GITHUB_REPOSITORY": "coders-farm-official/domovoi",
           "PATH": str(tmp_path / "no-gh-here")}  # every asset has a digest: gh is never run
    return subprocess.run([sys.executable, "-I", str(script), str(d)], capture_output=True, text=True, env=env)


def _assets(files: dict[str, bytes], sums: bytes) -> list[dict]:
    return [{"name": n, "digest": "sha256:" + hashlib.sha256(d).hexdigest()}
            for n, d in {**files, fr.SUMS_ASSET: sums}.items()]


def test_every_kind_of_asset_becomes_a_subject(tmp_path):
    files = _every_kind()
    sums = _sums_for(files)
    assert sorted(line.split("  ")[1] for line in sums.decode().splitlines()) == sorted(files), \
        "finish-release's SHA256SUMS lists every kind of asset"
    p = _run_check(tmp_path, sums, _assets(files, sums))
    assert p.returncode == 0, p.stdout + p.stderr
    subjects = (tmp_path / "attest" / "subjects.txt").read_text(encoding="ascii").splitlines()
    want = {**{n: hashlib.sha256(d).hexdigest() for n, d in files.items()}, "SHA256SUMS": hashlib.sha256(sums).hexdigest()}
    assert sorted(subjects) == sorted(f"{sha}  {n}" for n, sha in want.items())
    for kind in (".apk", fr.SETUP_ASSET, fr.SETUP_SIG_ASSET, fr.MANIFEST_ASSET, fr.TRAY_ASSET,
                 fr.SATELLITE_SETUP_ASSET, *fr.SBOM_ASSETS, "SHA256SUMS"):
        assert any(s.endswith(kind) for s in subjects), kind


@pytest.mark.parametrize("change, why", [
    (lambda a: a + [{"name": "extra.bin", "digest": "sha256:" + "0" * 64}], "the release holds"),
    (lambda a: [x for x in a if x["name"] != fr.TRAY_ASSET], "the release holds"),
    (lambda a: [{**x, "digest": "sha256:" + "0" * 64} if x["name"] == fr.SETUP_ASSET else x for x in a],
     "not the .* the release job made"),
    (lambda a: [{**x, "digest": "sha256:" + "0" * 64} if x["name"] == "SHA256SUMS" else x for x in a],
     "not the .* the release job made"),
])
def test_a_release_that_is_not_what_the_signing_job_made_is_not_attested(tmp_path, change, why):
    files = _every_kind()
    sums = _sums_for(files)
    p = _run_check(tmp_path, sums, change(_assets(files, sums)))
    assert p.returncode != 0 and re.search(why, p.stdout + p.stderr), p.stdout + p.stderr
    assert not (tmp_path / "attest" / "subjects.txt").exists()


def test_a_sha256sums_without_the_sboms_is_refused(tmp_path):
    files = _every_kind()
    tail = ("  " + fr.WEB_SBOM_ASSET + "\n").encode()
    sums = b"".join(line for line in _sums_for(files).splitlines(keepends=True) if not line.endswith(tail))
    del files[fr.WEB_SBOM_ASSET]
    p = _run_check(tmp_path, sums, _assets(files, sums))
    assert p.returncode != 0 and "names no domovoi-web.cdx.json" in p.stdout + p.stderr


# ─── the sbom job and its hand-over ──────────────────────────────────────

def test_the_sbom_job_is_read_only_and_secret_free():
    job = JOBS["sbom"]
    assert job["permissions"] == {"contents": "read"}
    assert "environment" not in job and "secrets." not in _job_text("sbom")
    assert job["needs"] == "plan"
    text = _job_text("sbom")
    for kind, name in (("server", fr.SERVER_SBOM_ASSET), ("web", fr.WEB_SBOM_ASSET), ("android", fr.ANDROID_SBOM_ASSET)):
        assert f"sbom.py {kind} " in text and f"/sboms/{name}" in text, kind
    (app,) = [s for s in job["steps"] if str(s.get("name", "")).startswith("The app's SBOM")]
    assert app["if"] == "needs.plan.outputs.path == 'app'", "the app's SBOM only with the APKs"
    (up,) = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert up["with"]["name"] == "release-sboms"


def test_the_signing_job_attaches_the_sboms_through_finish_release():
    job = JOBS["release"]
    assert "sbom" in job["needs"]
    (down,) = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact@")
               and (s.get("with") or {}).get("name") == "release-sboms"]
    assert down["with"] == {"name": "release-sboms", "path": "${{ runner.temp }}/sboms"}
    assert "env" not in down, "the download step is handed no secret"
    (finish,) = [s for s in job["steps"] if str(s.get("name", "")).startswith("Finish the release")]
    assert '--sbom-dir "$RUNNER_TEMP/sboms" --provenance' in finish["run"]


def test_every_artifact_action_is_pinned_by_sha():
    for job, step in _steps_using("actions/"):
        assert re.fullmatch(r"actions/[\w-]+@[0-9a-f]{40}", step["uses"]), (job, step["uses"])


def test_ci_makes_the_sboms_the_same_way():
    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = ci["jobs"]["sbom"]
    text = yaml.safe_dump(job)
    assert "secrets." not in text and "permissions" not in job
    for kind in ("server", "web", "android"):
        assert f"sbom.py {kind} " in text
    assert "--init-script ../scripts/release/sbom-android.init.gradle" in text


def test_security_md_says_how_to_verify_a_release():
    text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    assert "## Verifying a release" in text
    assert "gh attestation verify <file> -R coders-farm-official/domovoi" in text
