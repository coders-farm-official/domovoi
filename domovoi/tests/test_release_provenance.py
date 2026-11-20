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
* The ``build-mac`` job builds DomovoiSetup-mac.pkg on macOS with a
  read-only token, no environment and no secret, from the dispatched
  commit, with the pinned Xcode (the same as ci.yml's mac job); the signing
  job waits for it and hands its package to finish-release (``--mac-pkg``),
  so the Mac files are in SHA256SUMS and so among the attested subjects.
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
    # No folding: at the default width a longer step moves where a line
    # breaks, and a phrase the tests look for can land split in two.
    return yaml.safe_dump(JOBS[name], width=10**9)


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
    assert "github.ref == 'refs/heads/main'" in cond
    assert "needs.plan.result == 'success'" in cond
    (down,) = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact@")]
    assert down["with"] == {"name": "release-sums", "path": "${{ runner.temp }}/attest"}
    assert "if" not in down


def test_there_is_no_provenance_only_run():
    """OPS-21 (round 6): attest_published ran attest alone, with no
    approval (plan, build and signing skipped, so the release environment
    never asked), and attested whatever SHA256SUMS the published release
    held: upload changed bytes and a matching SHA256SUMS with --clobber,
    dispatch, and the result was a genuine attestation for them. 1.3.0's
    backfill is done; the input is gone, and attest runs only after a
    release run's own signing job, from the SHA256SUMS that job made."""
    on = DOC[True] if True in DOC else DOC["on"]
    assert set(on["workflow_dispatch"]["inputs"]) == {"tag"}
    assert "attest_published" not in TEXT.replace("# There is no provenance-only run (OPS-21, round 6): attest_published", "")
    assert "if" not in JOBS["plan"]
    assert JOBS["attest"]["env"]["TAG"] == "${{ needs.plan.outputs.tag }}"
    # Nothing in attest takes SHA256SUMS from the release itself.
    attest = _job_text("attest")
    assert "--pattern SHA256SUMS" not in attest
    for name in ("build-app", "sbom", "release"):
        needs = JOBS[name]["needs"]
        assert "plan" in (needs if isinstance(needs, list) else [needs]), name
        assert "always()" not in str(JOBS[name].get("if", "")), name
    assert "needs.plan.result == 'success'" in JOBS["release"]["if"]
    # The signing job, the one behind the release environment's approval,
    # is the only source of the subjects.
    assert JOBS["release"]["environment"] == "release"


def test_no_other_job_can_mint_an_oidc_token_or_write_attestations():
    assert DOC["permissions"] == {}
    for name, job in JOBS.items():
        perms = job.get("permissions") or {}
        if name != "attest":
            assert "id-token" not in perms and "attestations" not in perms, name
    assert JOBS["release"]["permissions"] == {"contents": "write"}
    assert JOBS["release"]["environment"] == "release"


def test_the_signing_job_hands_on_the_sha256sums_it_published():
    # An artifact, never a job output: GitHub drops an output that holds a
    # secret's value (1.3.0's provenance was lost that way).
    job = JOBS["release"]
    assert "outputs" not in job
    (step,) = [s for s in job["steps"] if s.get("id") == "published"]
    assert "env" not in step, "the step that checks SHA256SUMS sees no secret"
    assert 'windows/build/release/$TAG/SHA256SUMS' in step["run"]
    (up,) = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert up["with"]["name"] == "release-sums" and "env" not in up
    assert up["with"]["path"] == "windows/build/release/${{ env.TAG }}/SHA256SUMS"
    names = [s.get("name") or s.get("uses") for s in job["steps"]]
    finish = next(i for i, n in enumerate(names) if str(n).startswith("Finish the release"))
    assert finish < job["steps"].index(up), "after finish-release published"


def test_the_key_alias_is_a_variable_not_a_secret():
    """A secret alias is masked in every output, and `domovoi` is in every file name."""
    assert "secrets.ANDROID_KEY_ALIAS" not in TEXT
    (step,) = [s for s in JOBS["release"]["steps"] if str(s.get("name", "")).startswith("Sign and publish the app")]
    assert step["env"]["ANDROID_KEY_ALIAS"] == "${{ vars.ANDROID_KEY_ALIAS }}"
    assert '"$env:ANDROID_KEY_ALIAS".Trim()' in step["run"]


def _attest_check() -> str:
    (step,) = [s for s in JOBS["attest"]["steps"] if s.get("name") == "The subjects, held to the release on GitHub"]
    run = step["run"]
    return run.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _every_kind() -> dict[str, bytes]:
    """One file of each kind a release can hold."""
    return {
        "domovoi-1.3.0-arm64-v8a.apk": b"apk 64", "domovoi-1.3.0-armeabi-v7a.apk": b"apk 32",
        fr.SETUP_ASSET: b"MZ setup", **{n: n.encode() for n in fr.SIGNATURE_ASSETS},
        fr.TRAY_ASSET: b"MZ tray", fr.SATELLITE_SETUP_ASSET: b"MZ sat",
        **{n: b'{"bomFormat": "CycloneDX"}' + n.encode() for n in fr.SBOM_ASSETS},
        fr.MAC_SETUP_ASSET: b"xar! mac", **{n: n.encode() for n in fr.MAC_SIGNATURE_ASSETS},
    }


def _sums_for(files: dict[str, bytes]) -> bytes:
    """finish-release's own SHA256SUMS for these files."""
    sha = {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}
    rel = fr.Release(tag="v1.3.0", version="1.3.0", prerelease=False, tag_commit="1" * 40, cert="ab" * 32,
                     apks=[{"file": n, "sha256": sha[n], "abi": "arm64-v8a"} for n in sorted(files) if n.endswith(".apk")],
                     setup_sha256=sha[fr.SETUP_ASSET], tray_sha256=sha[fr.TRAY_ASSET],
                     satellite_setup_sha256=sha[fr.SATELLITE_SETUP_ASSET],
                     signatures={n: sha[n] for n in fr.SIGNATURE_ASSETS},
                     sboms={n: sha[n] for n in fr.SBOM_ASSETS},
                     mac_setup_sha256=sha[fr.MAC_SETUP_ASSET],
                     mac_signatures={n: sha[n] for n in fr.MAC_SIGNATURE_ASSETS})
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
                 fr.SATELLITE_SETUP_ASSET, *fr.SBOM_ASSETS, fr.MAC_SETUP_ASSET, *fr.MAC_SIGNATURE_ASSETS,
                 "SHA256SUMS"):
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


def test_the_app_tag_release_comes_from_finish_release_after_release_ps1_with_no_new_permission():
    """The app tag's own release (app-<versionName>, for F-Droid) is made by
    finish-release in the signing job, with the token that job already has:
    only after release.ps1 (which pushes the tag app-<versionName> at the
    commit the APKs were built from) on an app release, and on a run that
    finishes one (sign_app false: the tag was pushed by the earlier attempt).
    No step makes a release of the app tag or a tag on its own."""
    job = JOBS["release"]
    assert job["permissions"] == {"contents": "write"}
    names = [str(s.get("name", "")) for s in job["steps"]]
    sign = names.index("Sign and publish the app (release.ps1)")
    finish = next(i for i, n in enumerate(names) if n.startswith("Finish the release"))
    assert sign < finish
    step = job["steps"][finish]
    assert "if" not in step, "finish-release runs on every release; it decides from the tag's android/release.json"
    assert set(step["env"]) == {"RELEASE_SIGNING_KEY", "RELEASE_SIGNING_KEY_PASSPHRASE", "GH_TOKEN"}
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "scripts/release/finish_release.py --tag" in step["run"]
    for s in job["steps"]:
        run = str(s.get("run", ""))
        assert "app-" not in run or "release create" not in run
        assert "git tag" not in run
    # finish-release's own app release: never Latest, at the existing tag.
    src = (REPO / "scripts" / "release" / "finish_release.py").read_text(encoding="utf-8")
    call = src[src.index('t.gh("release", "create", app_tag'):]
    call = call[:call.index(")\n")]
    assert '"--verify-tag"' in call and '"--latest=false"' in call
    assert "--prerelease" not in call and "--clobber" not in call and "--target" not in call


# ─── the Mac package ─────────────────────────────────────────────────────

XCODE = "/Applications/Xcode_16.4.app"


def test_the_mac_build_job_is_read_only_and_secret_free():
    job = JOBS["build-mac"]
    assert job["permissions"] == {"contents": "read"}
    assert "environment" not in job and "secrets." not in _job_text("build-mac")
    assert job["needs"] == "plan" and job["runs-on"] == "macos-15"
    assert job["if"] == "github.ref == 'refs/heads/main'"
    (co,) = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert co["with"] == {"ref": "${{ github.sha }}", "fetch-depth": 0, "persist-credentials": False}
    for step in job["steps"]:
        assert "env" not in step, "no step of the Mac build is handed anything"
    text = _job_text("build-mac")
    assert XCODE in text and "xcode-select -s" in text and "sort -V" not in text, "Xcode by its path, never the newest"
    (build,) = [s for s in job["steps"] if "mac/build.sh" in str(s.get("run", ""))]
    assert '--tag "$TAG" --ref "$TAG" --cmake pinned' in build["run"]
    (up,) = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert up["with"]["name"] == "release-mac-pkg" and up["with"]["if-no-files-found"] == "error"
    assert "DomovoiSetup-mac.pkg" in up["with"]["path"] and fr.MAC_BUILD_RECORD in up["with"]["path"]


def test_the_mac_build_names_the_tag_only_on_the_dispatched_commit_or_under_its_app_commit():
    (step,) = [s for s in JOBS["build-mac"]["steps"] if s.get("name") == "The tag, named here on the dispatched commit"]
    run = step["run"]
    assert 'git tag -f "$TAG" "$GITHUB_SHA"' in run
    assert '[ "$(git rev-parse "$tagged^")" = "$GITHUB_SHA" ]' in run
    assert '= android/release.json' in run


def test_the_signing_job_waits_for_the_mac_build_and_hands_it_to_finish_release():
    job = JOBS["release"]
    assert "build-mac" in job["needs"] and "needs.build-mac.result == 'success'" in job["if"]
    (down,) = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact@")
               and (s.get("with") or {}).get("name") == "release-mac-pkg"]
    assert down["with"] == {"name": "release-mac-pkg", "path": "${{ runner.temp }}/mac"}
    assert "env" not in down and "if" not in down, "every release carries it; the download sees no secret"
    (finish,) = [s for s in job["steps"] if str(s.get("name", "")).startswith("Finish the release")]
    assert '--mac-pkg "$RUNNER_TEMP/mac/DomovoiSetup-mac.pkg"' in finish["run"]


def test_only_the_signing_job_runs_in_the_release_environment():
    assert [n for n, j in JOBS.items() if "environment" in j] == ["release"]


def test_ci_builds_the_mac_package_with_the_same_xcode():
    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = ci["jobs"]["mac"]
    text = yaml.safe_dump(job, width=10**9)
    assert job["runs-on"] == "macos-15" and job["if"] == "needs.changes.outputs.mac == 'true'"
    assert XCODE in text and "sort -V" not in text and "secrets." not in text
    (step,) = [s for s in job["steps"] if s.get("working-directory") == "mac"]
    assert step["run"] == "swift build && swift test"
    assert "mac" in ci["jobs"]["ci-ok"]["needs"]
    assert ci["jobs"]["changes"]["outputs"]["mac"] == "${{ steps.filter.outputs.mac }}"
    (flt,) = [s for s in ci["jobs"]["changes"]["steps"] if s.get("id") == "filter"]
    assert "mac=$(has '^(mac/|" in flt["run"] and "/workflows/)')" in flt["run"]
    assert "windows ios mac; do" in flt["run"], "a run started by hand runs it too"


def test_every_artifact_action_is_pinned_by_sha():
    for job, step in _steps_using("actions/"):
        assert re.fullmatch(r"actions/[\w-]+@[0-9a-f]{40}", step["uses"]), (job, step["uses"])


def test_ci_makes_the_sboms_the_same_way():
    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = ci["jobs"]["sbom"]
    text = yaml.safe_dump(job, width=10**9)  # unfolded, as _job_text
    assert "secrets." not in text and "permissions" not in job
    for kind in ("server", "web", "android"):
        assert f"sbom.py {kind} " in text
    assert "--init-script ../scripts/release/sbom-android.init.gradle" in text


def test_security_md_says_how_to_verify_a_release():
    text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    assert "## Verifying a release" in text
    # OPS-22: the command people copy is the strict one.
    assert ("gh attestation verify <file> -R coders-farm-official/domovoi "
            "--signer-workflow coders-farm-official/domovoi/.github/workflows/release.yml "
            "--source-ref refs/heads/main") in text
