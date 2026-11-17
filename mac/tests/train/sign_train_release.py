#!/usr/bin/env python3
"""Sign a test DomovoiSetup-mac.pkg for the Mac update train (mac-installer
PLAN 4.12, Phase 6): the four release files a Mac side's update downloads,
made exactly as ``finish-release --mac-pkg`` makes them (its own
``check_mac_build_record`` and ``sign_setup``), but with a THROWAWAY key.

    python3 mac/tests/train/sign_train_release.py --pkg DIR/DomovoiSetup-mac.pkg \\
        --tag v1.90.1 --key ~/train-keys/train-key --out DIR/release-v1.90.1

* ``--pkg``: a package ``mac/build.sh --tag TAG --release-key KEY.pub
  --release-repo OWNER/FORK`` made; its build record
  (``DomovoiSetup-mac.build.json``) beside it, or ``--record``.
* ``--key``: the throwaway key's private half (``ssh-keygen -t ed25519``),
  outside the checkout; ``KEY.pub`` beside it. The project's release key is
  refused: it never enters a session, and this is for a fork only.
* ``--commit``: the tag's commit, when this checkout doesn't have the tag.

It writes, into ``--out``: DomovoiSetup-mac.pkg, DomovoiSetup-mac.pkg.sig,
DomovoiSetup-mac.manifest.json, DomovoiSetup-mac.manifest.json.sig and
SHA256SUMS, then prints the ``gh`` command that would publish them on the
fork. It never runs it: creating a release, even on a fork, is the owner's
call. Nothing here talks to GitHub.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PROJECT_REPO = "coders-farm-official/domovoi"


def load_finish_release():
    path = REPO / "scripts" / "release" / "finish_release.py"
    spec = importlib.util.spec_from_file_location("finish_release", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["finish_release"] = mod
    spec.loader.exec_module(mod)
    return mod


def key_blob(text: str) -> str | None:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "ssh-ed25519":
            return parts[1]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sign_train_release", description=__doc__.split("\n\n")[0])
    ap.add_argument("--pkg", required=True)
    ap.add_argument("--record", help="the build record (default: DomovoiSetup-mac.build.json beside the package)")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--commit", help="the tag's commit (default: this checkout's)")
    ap.add_argument("--key", required=True, help="the THROWAWAY key's private half; KEY.pub beside it")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fork", default="OWNER/FORK", help="for the printed gh command only")
    a = ap.parse_args(argv)
    fr = load_finish_release()
    try:
        tag = a.tag
        if not fr.re.match(r"^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]{1,40})?$", tag):
            fr.die(f"{tag} is not a release tag")
        key = Path(a.key).expanduser().resolve()
        pub_path = key.with_name(key.name + ".pub")
        if not key.is_file() or not pub_path.is_file():
            fr.die(f"no throwaway key at {key} with {pub_path.name} beside it")
        pub = pub_path.read_bytes()
        project = key_blob((REPO / fr.RELEASE_KEY).read_text(encoding="ascii"))
        if key_blob(pub.decode("ascii", "replace")) in (None, project):
            fr.die("that is not a throwaway ed25519 key: the project's release key is never used for the train")
        if a.fork == PROJECT_REPO:
            fr.die("the train publishes on a throwaway fork, never on the project's own repository")
        commit = a.commit
        if commit is None:
            p = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"],
                               capture_output=True, text=True)
            commit = p.stdout.strip() if p.returncode == 0 else None
        if not commit or not fr.re.match(r"^[0-9a-f]{40}$", commit):
            fr.die(f"no commit for {tag} in this checkout: pass --commit")
        pkg = Path(a.pkg).resolve()
        record = Path(a.record).resolve() if a.record else pkg.parent / fr.MAC_BUILD_RECORD
        out = Path(a.out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        staged = out / fr.MAC_SETUP_ASSET
        if staged.resolve() != pkg:
            shutil.copyfile(pkg, staged)
        fr.check_mac_build_record(record, staged, tag=tag, tag_commit=commit)
        sums = fr.sign_setup(out, staged, tag=tag, version=tag[1:], tag_commit=commit, key=key, public=pub, repo=REPO, mac=True)
        lines = [f"{fr.sha256_file(staged)}  {fr.MAC_SETUP_ASSET}"] + [f"{sums[n]}  {n}" for n in fr.MAC_SIGNATURE_ASSETS]
        (out / "SHA256SUMS").write_bytes(("\n".join(lines) + "\n").encode("ascii"))
    except fr.Refused as e:
        print(f"sign_train_release: {e}", file=sys.stderr)
        return 2
    names = [fr.MAC_SETUP_ASSET, *fr.MAC_SIGNATURE_ASSETS, "SHA256SUMS"]
    print(f"sign_train_release: {tag} at {commit[:12]}, signed with the throwaway key {pub_path.name}; in {out}:")
    for n in names:
        print(f"  {n}")
    print("To publish them on the fork (the owner's call; a published prerelease, not a draft: "
          "a draft's files need a sign-in to download):")
    print(f"  gh release create {tag} --repo {a.fork} --prerelease --title 'Mac update train {tag}' "
          f"--notes 'Throwaway test release for the Mac update train.' " + " ".join(f"'{out / n}'" for n in names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
