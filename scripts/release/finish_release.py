"""Finish a Domovoi release: the Windows Setup, one SHA256SUMS, the notes, then ``stable``.

Run by the owner, after ``android/scripts/release.ps1`` (or ``.sh``) has
published ``v<version>`` with the two APKs, tagged at the commit that adds
``android/release.json``, and after the Windows Setup's pins
(``windows/installer/pins.iss``) have been moved to that commit and pushed.
From a clean checkout at that pins commit (``git pull --ff-only``)::

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\release\\finish-release.ps1 [-Tag v1.0.0] [-NoPublish]
    bash scripts/release/finish-release.sh [--tag v1.0.0] [--no-publish] [--setup FILE]

In order, refusing at the first problem (nothing on GitHub changes until
every check has passed):

1. The checks. The checkout is clean. The tag (default ``v`` + the
   Android versionName) is on GitHub, and HEAD is that tag or comes after
   it. The tag's ``android/release.json`` names this release. The pins at
   HEAD install the tag's commit, with ``provision.sh``'s SHA-256 right.
   The GitHub release exists, holds the APKs the manifest pins (size and
   GitHub's SHA-256 digest), and nothing finish-release doesn't know. For
   a release without a suffix, ``stable`` can fast-forward to the tag.
2. ``DomovoiSetup.exe``, built by the installer's own script,
   ``windows/build.ps1`` (``--setup FILE`` takes one built from this same
   commit instead, e.g. when this runs on Linux).
3. One ``SHA256SUMS`` (LF line endings) for every asset: the APKs (from
   the manifest) and the Setup.
4. The release notes: ``docs/release/v<version>.md`` at the tag if there is
   one, otherwise a summary of the ``docs/RELEASE_NOTES.md`` entries added
   since the previous tag (a pre-release counts from the tag before it; a
   release from the release before it), with links into the full file at
   the tag. Then the files, their SHA-256, the commit the tag is, the
   commit Setup was built from, the app's signing certificate. Kept under
   20 KB, and never over GitHub's 125,000-character cap.
5. Upload the Setup and ``SHA256SUMS`` (``--clobber``, so a second run
   replaces them), set the title, the notes, and pre-release (a version
   with a suffix, or one GitHub already marks so) or Latest.
6. Check every asset on GitHub against ``SHA256SUMS``.
7. LAST, only for a release without a suffix: fast-forward ``stable`` to
   the tag's commit, ``git push origin "v<version>^{commit}:refs/heads/stable"``,
   never forced: a ``stable`` that is not an ancestor of the tag is refused.

``--no-publish`` runs 1-4 (it fetches and reads the GitHub release, never
changes it), leaves the Setup, ``SHA256SUMS`` and ``NOTES.md`` in
``windows/build/release/<tag>/``, and prints what a real run would do.

Standard library only. Never touches a keystore or a password; the only
writes outside ``windows/build/`` are the GitHub release's assets and
notes, and the ``stable`` branch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NoReturn

REPO = Path(__file__).resolve().parents[2]
REPO_SLUG = "coders-farm-official/domovoi"
REPO_URL = "https://github.com/" + REPO_SLUG
SETUP_ASSET = "DomovoiSetup.exe"
SUMS_ASSET = "SHA256SUMS"
#: GitHub refuses a release body over this many characters.
BODY_LIMIT = 125_000
#: What the notes aim for: the summary shrinks to fit.
BODY_BUDGET = 20_000
#: A first paragraph longer than this is left out of the summary.
PARAGRAPH_MAX = 400
TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)(-[0-9A-Za-z.]+)?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class Refused(Exception):
    """A check failed: the message says what and what to do."""


def die(message: str) -> NoReturn:
    raise Refused(message)


# ─── the release notes ───────────────────────────────────────────────────

@dataclass
class Entry:
    heading: str
    body: str
    anchor: str = ""


def _headings(text: str) -> list[tuple[int, int, str]]:
    """(line index, level, text) of every ATX heading outside code fences."""
    out = []
    fence = None
    for i, line in enumerate(text.split("\n")):
        stripped = line.strip()
        m = re.match(r"^(`{3,}|~{3,})", stripped)
        if m:
            if fence is None:
                fence = m.group(1)[0]
            elif stripped.startswith(fence * 3):
                fence = None
            continue
        if fence is not None:
            continue
        h = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if h:
            out.append((i, len(h.group(1)), h.group(2)))
    return out


def _heading_text(heading: str) -> str:
    """The text GitHub renders for a heading's inline Markdown."""
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"(\*\*|__|\*)", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    return s


def github_slug(heading: str, seen: dict[str, int]) -> str:
    """The anchor GitHub gives a heading (github-slugger): lower case, every
    character but letters, digits, marks, ``-``, ``_`` and spaces dropped,
    each space a ``-``; a repeat gets ``-1``, ``-2``. ``seen`` carries the
    counts across one document."""
    text = _heading_text(heading).lower()
    kept = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch in "-_ " or cat[0] in "LNM":
            kept.append(ch)
    base = "".join(kept).replace(" ", "-")
    slug = base
    n = seen.get(base, 0)
    if n:
        slug = f"{base}-{n}"
    seen[base] = n + 1
    return slug


def entries(text: str) -> list[Entry]:
    """The ``## `` sections of a release-notes file, in order, each with the
    anchor GitHub gives it."""
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    heads = _headings(text)
    seen: dict[str, int] = {}
    anchors = {i: github_slug(t, seen) for i, _, t in heads}
    out = []
    level2 = [(i, t) for i, lvl, t in heads if lvl == 2]
    for n, (i, t) in enumerate(level2):
        # The body runs to the next heading of level 1 or 2.
        end = len(lines)
        for j, lvl, _ in heads:
            if j > i and lvl <= 2:
                end = j
                break
        body = "\n".join(lines[i + 1:end]).strip("\n")
        out.append(Entry(heading=t, body=body, anchor=anchors[i]))
    return out


def new_entries(current: str, previous: str | None) -> list[Entry]:
    """The entries in ``current`` whose heading ``previous`` lacks (all of
    them without a previous file), newest first as written."""
    known = {e.heading for e in entries(previous)} if previous else set()
    return [e for e in entries(current) if e.heading not in known]


def first_paragraph(body: str) -> str:
    """The entry's opening paragraph on one line, or "" when it starts with
    a list, a table, a heading or a fence, or is too long to quote."""
    para: list[str] = []
    for line in body.split("\n"):
        s = line.strip()
        if not s:
            if para:
                break
            continue
        if not para and re.match(r"^([*+-]\s|\d+\.\s|[#|<>]|```|~~~)", s):
            return ""
        para.append(s)
    text = " ".join(para)
    return text if len(text) <= PARAGRAPH_MAX else ""


def summary(new: list[Entry], *, tag: str, since: str | None, budget: int) -> str:
    """The "what's new" part of the release body: one line per entry with a
    link into RELEASE_NOTES.md at the tag, and its opening paragraph while
    that fits ``budget``; titles only, then fewer of them, when not."""
    notes_url = f"{REPO_URL}/blob/{tag}/docs/RELEASE_NOTES.md"
    head = f"## What's new since {since}" if since else "## What's new"
    if not new:
        return f"{head}\n\nNo new entries in [docs/RELEASE_NOTES.md]({notes_url}) since {since or 'the start'}.\n"

    def render(with_text: bool, count: int) -> str:
        lines = [head, ""]
        for e in new[:count]:
            lines.append(f"- [{e.heading}]({notes_url}#{e.anchor})")
            para = first_paragraph(e.body) if with_text else ""
            if para:
                lines.append(f"  {para}")
        rest = len(new) - count
        if rest > 0:
            lines.append(f"- ...and {rest} more, in the full notes.")
        lines += ["", f"The full notes, with what to do after updating: [docs/RELEASE_NOTES.md at {tag}]({notes_url})."]
        return "\n".join(lines) + "\n"

    for with_text in (True, False):
        text = render(with_text, len(new))
        if len(text) <= budget:
            return text
    count = len(new)
    while count > 1 and len(render(False, count)) > budget:
        count -= 1
    return render(False, count)


# ─── the release body ────────────────────────────────────────────────────

@dataclass
class Release:
    tag: str
    version: str
    prerelease: bool
    tag_commit: str
    setup_commit: str
    cert: str
    apks: list[dict[str, Any]]
    setup_sha256: str
    previous_tag: str | None = None
    notes: str = ""          # the summary part, from docs/release or RELEASE_NOTES.md


def _who(abi: str) -> str:
    return "the Android app, most phones (64-bit)" if abi == "arm64-v8a" else "the Android app, older or cheaper phones (32-bit)"


def body(rel: Release) -> str:
    """The whole release body: the summary, how to install, the files."""
    if rel.prerelease:
        lead = (f"**Domovoi {rel.version}** is a pre-release: for testing before the release it leads to. "
                "Servers that follow `stable` don't get it.")
    else:
        lead = f"**Domovoi {rel.version}**."
    files = ["## Files", "", "| file | what | SHA-256 |", "|---|---|---|"]
    for e in rel.apks:
        files.append(f"| {e['file']} | {_who(e['abi'])} | `{e['sha256']}` |")
    files.append(f"| {SETUP_ASSET} | the Windows installer (preview, not code-signed) | `{rel.setup_sha256}` |")
    facts = [
        "",
        f"- The tag `{rel.tag}` is commit `{rel.tag_commit}`: the code a server installed from this release runs. "
        "Its `android/release.json` names the two APKs above, which a server hands to the household's phones.",
        f"- `{SETUP_ASSET}` installs that commit. It was built from `{rel.setup_commit}`, the tag plus the commit "
        "that pins it (`windows/installer/pins.iss`). It is not code-signed, so Windows says \"Unknown publisher\": "
        f"[docs/WINDOWS_HOST.md]({REPO_URL}/blob/{rel.tag}/docs/WINDOWS_HOST.md).",
        f"- The Android app's signing certificate SHA-256: `{rel.cert}` (check with `apksigner verify --print-certs <file>`).",
        "- Check a download: `sha256sum -c SHA256SUMS --ignore-missing` (Linux, macOS, Git Bash), "
        "or `Get-FileHash -Algorithm SHA256 <file>` (PowerShell) against the line in `SHA256SUMS`.",
    ]
    install = [
        "## Install or update",
        "",
        f"- **A Domovoi server** updates from its dashboard: Settings, Configuration, Version, then check for updates. "
        f"A new Linux server: [docs/LINUX_HOST.md]({REPO_URL}/blob/{rel.tag}/docs/LINUX_HOST.md).",
        "- **Phones** get the app from their own server (the join page, or Settings, Devices), "
        "once an admin presses \"Download the app to this server\" there.",
        f"- **Windows (preview):** `{SETUP_ASSET}` puts the server on a Windows 11 PC.",
    ]
    parts = [lead, "", rel.notes.strip(), "", *install, "", *files, *facts]
    return "\n".join(parts).rstrip("\n") + "\n"


def sums_text(rel: Release) -> str:
    """``SHA256SUMS``: ``<sha256>  <file>`` per asset, LF line endings."""
    lines = [f"{e['sha256']}  {e['file']}" for e in rel.apks]
    lines.append(f"{rel.setup_sha256}  {SETUP_ASSET}")
    return "\n".join(lines) + "\n"


# ─── the Setup's pins ────────────────────────────────────────────────────

def read_pins(text: str) -> tuple[dict[str, str], set[str]]:
    """``#define Name "value"`` pairs and bare ``#define Name`` flags of
    pins.iss (expressions, like ProvisionUrl, are left out)."""
    values = dict(re.findall(r'^#define\s+(\w+)\s+"([^"]*)"\s*$', text, re.M))
    flags = set(re.findall(r"^#define\s+(\w+)\s*$", text, re.M))
    return values, flags


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ─── git and gh ──────────────────────────────────────────────────────────

class Tools:
    """git, gh and the downloads, in one place (tests replace gh and the
    downloader)."""

    def __init__(self, repo: Path, gh: list[str] | None, say: Callable[[str], None]):
        self.repo = repo
        self.gh_cmd = gh
        self.say = say

    def git(self, *args: str, check: bool = True) -> str:
        p = subprocess.run(["git", *args], cwd=self.repo, capture_output=True)
        if check and p.returncode != 0:
            why = p.stderr.decode("utf-8", "replace").strip().splitlines()
            die(f"git {' '.join(args)} failed" + (f": {why[-1]}" if why else ""))
        return p.stdout.decode("utf-8", "replace")

    def git_run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True)

    def git_ok(self, *args: str) -> bool:
        return self.git_run(*args).returncode == 0

    def git_bytes(self, *args: str) -> bytes | None:
        p = subprocess.run(["git", *args], cwd=self.repo, capture_output=True)
        return p.stdout if p.returncode == 0 else None

    def gh(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        if not self.gh_cmd:
            die("no gh (GitHub CLI): install it and run gh auth login")
        p = subprocess.run([*self.gh_cmd, *args], cwd=self.repo, capture_output=True)
        if check and p.returncode != 0:
            why = (p.stderr or p.stdout).decode("utf-8", "replace").strip().splitlines()
            die(f"gh {' '.join(args[:2])} failed" + (f": {why[-1]}" if why else ""))
        return p

    def download_sha256(self, url: str) -> str:
        h = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 - https GitHub asset URL
            for chunk in iter(lambda: r.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()


def default_gh() -> list[str] | None:
    """The gh to run: ``DOMOVOI_RELEASE_GH`` (a path, or a JSON list of the
    command and its first arguments; the script's tests use it), else gh on
    PATH."""
    env = os.environ.get("DOMOVOI_RELEASE_GH")
    if env:
        return json.loads(env) if env.lstrip().startswith("[") else [env]
    found = shutil.which("gh")
    return [found] if found else None


# ─── the run ─────────────────────────────────────────────────────────────

def _version_name(text: str) -> str:
    found = re.findall(r'^\s*versionName = "([^"]+)"\s*$', text, re.M)
    if len(found) != 1:
        die("android/app/build.gradle.kts names no single versionName: pass --tag")
    return found[0]


def _previous_tag(t: Tools, tag_commit: str, prerelease: bool) -> str | None:
    """The tag the notes count from: the nearest earlier tag for a
    pre-release; for a release, the nearest earlier one without a suffix
    (stable's last), else any."""
    patterns = [["--exclude", "v*-*"], []] if not prerelease else [[]]
    if not t.git_ok("rev-parse", "--verify", "--quiet", f"{tag_commit}^"):
        return None
    for extra in patterns:
        out = t.git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", *extra, f"{tag_commit}^", check=False).strip()
        if out:
            return out
    return None


def _remote_ref(t: Tools, ref: str) -> str | None:
    """The commit a ref on origin points at (a tag peeled), or None."""
    out = t.git("ls-remote", "origin", ref)
    found: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            found[parts[1]] = parts[0]
    return found.get(f"{ref}^{{}}") or found.get(ref)


def _stable_plan(t: Tools, tag_commit: str) -> tuple[str, str | None]:
    """("already" | "create" | "forward" | "refused", stable's commit)."""
    current = _remote_ref(t, "refs/heads/stable")
    if current is None:
        return "create", None
    if current == tag_commit:
        return "already", current
    if not t.git_ok("cat-file", "-e", f"{current}^{{commit}}"):
        t.git("fetch", "--quiet", "origin", "refs/heads/stable")
    if t.git_ok("merge-base", "--is-ancestor", current, tag_commit):
        return "forward", current
    return "refused", current


def _release_json(t: Tools, tag: str) -> dict[str, Any] | None:
    p = t.gh("release", "view", tag, "--repo", REPO_SLUG, "--json",
             "tagName,name,isPrerelease,isDraft,assets,url", check=False)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout.decode("utf-8"))
    except ValueError:
        die("gh release view printed something that is not JSON")


def _digest(asset: dict[str, Any]) -> str | None:
    d = asset.get("digest") or ""
    return d[len("sha256:"):] if d.startswith("sha256:") else None


def _check_published_apks(release: dict[str, Any], apks: list[dict[str, Any]]) -> None:
    assets = {a["name"]: a for a in release.get("assets") or []}
    allowed = {e["file"] for e in apks} | {SETUP_ASSET, SUMS_ASSET}
    unknown = sorted(set(assets) - allowed)
    if unknown:
        die(f"the release has files finish-release doesn't know: {', '.join(unknown)} "
            "(remove them on GitHub, or add them to finish_release.py, first)")
    for e in apks:
        a = assets.get(e["file"])
        if a is None:
            die(f"the release has no {e['file']}: release.ps1 uploads it")
        if a.get("size") != e["size"]:
            die(f"{e['file']} on GitHub is {a.get('size')} bytes, the manifest says {e['size']}")
        d = _digest(a)
        if d is not None and d != e["sha256"]:
            die(f"{e['file']} on GitHub is not the file android/release.json pins (SHA-256 {d})")


def _verify_published(t: Tools, tag: str, sums: str) -> None:
    """Every asset on GitHub against SHA256SUMS (GitHub's own digest; a
    download where it gives none), SHA256SUMS itself included, and no
    other files."""
    release = _release_json(t, tag)
    if release is None:
        die(f"can't read the release {tag} back")
    assets = {a["name"]: a for a in release.get("assets") or []}
    want = {}
    for line in sums.splitlines():
        sha, name = line.split("  ", 1)
        want[name] = sha
    want[SUMS_ASSET] = sha256_bytes(sums.encode("ascii"))
    extra = sorted(set(assets) - set(want))
    if extra:
        die(f"the release holds files SHA256SUMS doesn't cover: {', '.join(extra)}")
    for name, sha in want.items():
        a = assets.get(name)
        if a is None:
            die(f"{name} is missing from the release on GitHub")
        got = _digest(a)
        if got is None:
            got = t.download_sha256(a["url"])
        if got != sha:
            die(f"{name} on GitHub is not the file finish-release made (SHA-256 {got}, not {sha})")


def run(argv: list[str], *, tools: Tools | None = None, repo: Path | None = None,
        build_setup: Callable[[Path], Path] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="finish-release", description=(
        "Finish a Domovoi release after android/scripts/release: build and attach the Windows Setup, "
        "write one SHA256SUMS, set the notes, then (a release without a suffix) fast-forward stable."))
    ap.add_argument("--tag", help="the release, v<version> (default: v + the Android versionName)")
    ap.add_argument("--no-publish", action="store_true", help="check, build and write everything; change nothing on GitHub")
    ap.add_argument("--setup", help="a DomovoiSetup.exe built by windows/build.ps1 from this commit, instead of building it")
    ap.add_argument("--since", help="the tag the notes' summary counts from (default: the previous one)")
    args = ap.parse_args(argv)

    repo = Path(repo or REPO)
    say = (tools.say if tools else (lambda m: print(f"finish-release: {m}", flush=True)))
    t = tools or Tools(repo, default_gh(), say)
    publish = not args.no_publish

    # 1. The checks. --------------------------------------------------------
    if t.git("status", "--porcelain").strip():
        die("the checkout has uncommitted changes: commit or stash them first")
    head = t.git("rev-parse", "HEAD").strip()
    gradle = (repo / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    tag = args.tag or f"v{_version_name(gradle)}"
    m = TAG_RE.match(tag)
    if not m:
        die(f"{tag} is not v<X.Y.Z> or v<X.Y.Z>-<suffix>")
    version = tag[1:]
    by_name = bool(m.group(2))

    fetch = t.git_run("fetch", "--quiet", "--tags", "origin")
    fetched = fetch.returncode == 0
    if not fetched:
        err = fetch.stderr.decode("utf-8", "replace").strip()
        if "would clobber" in err or "rejected" in err:
            die(f"git fetch --tags would move a tag here (a published tag never moves): {err.splitlines()[-1]}")
        if publish:
            die("git fetch origin failed")
        say("warning: git fetch origin failed; checking against what this checkout has")
    if not t.git_ok("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"):
        die(f"no tag {tag}: android/scripts/release.ps1 publishes the release and makes it")
    tag_commit = t.git("rev-parse", f"{tag}^{{commit}}").strip()
    on_github = _remote_ref(t, f"refs/tags/{tag}") if fetched else None
    if fetched and on_github != tag_commit:
        if on_github is None and not publish:
            say(f"warning: the tag {tag} is not on GitHub")
        else:
            die(f"the tag {tag} here is {tag_commit[:12]}, on GitHub {on_github or 'missing'}")
    if not t.git_ok("merge-base", "--is-ancestor", tag_commit, head):
        die(f"HEAD {head[:12]} does not come after the tag {tag} ({tag_commit[:12]}): git switch to the commit that pins Setup to it")
    if publish and head != tag_commit and not t.git("branch", "-r", "--contains", head).strip():
        die(f"HEAD {head[:12]} is not on GitHub yet: push it first (the notes name it as the commit Setup was built from)")
    between = t.git("log", "--format=%h %s", f"{tag_commit}..{head}").strip()
    say(f"Domovoi {version}: the tag is {tag_commit[:12]}; Setup is built from {head[:12]}"
        + (f", the tag plus:\n    " + "\n    ".join(between.splitlines()) if between else ""))

    raw = t.git_bytes("show", f"{tag_commit}:android/release.json")
    try:
        manifest = json.loads(raw.decode("utf-8")) if raw is not None else {}
    except ValueError:
        manifest = {}
    rel = manifest.get("release") or {}
    if rel.get("version_name") != version or rel.get("tag") != tag:
        die(f"the tag's android/release.json names {rel.get('version_name') or 'no release'}, not {version}: "
            "android/scripts/release.ps1 commits the manifest and tags that commit (an older one tagged before it)")
    apks = list(rel.get("apks") or [])
    cert = str(rel.get("cert_sha256") or "")
    if not apks or not cert:
        die("the tag's android/release.json names no APKs or no certificate")

    pins_path = repo / "windows" / "installer" / "pins.iss"
    values, flags = read_pins(pins_path.read_text(encoding="utf-8"))
    pinned = values.get("DomovoiCommit")
    if pinned != tag_commit:
        die(f"windows/installer/pins.iss installs {pinned or 'nothing'}, not the tag's commit {tag_commit}: "
            "move the Setup's pins to it (DomovoiCommit, ProvisionSha256 and what moves with them), commit, push, "
            "git pull, and run again")
    provision_pin = values.get("ProvisionSha256")
    if not provision_pin:
        die("windows/installer/pins.iss names no ProvisionSha256")
    tag_provision = t.git_bytes("show", f"{tag_commit}:scripts/linux/provision.sh")
    if tag_provision is None:
        die(f"the tag {tag} has no scripts/linux/provision.sh")
    if "ProvisionBundled" in flags:
        src = values.get("ProvisionSource", "").replace("\\", "/")
        bundled = (pins_path.parent / src).resolve()
        if not src or not bundled.is_file():
            die(f"pins.iss bundles provision.sh from {src or '(nothing)'}, which is not here")
        data = bundled.read_bytes()
        if data != tag_provision:
            say("warning: Setup carries a provision.sh that differs from the tag's (ProvisionBundled)")
    else:
        data = tag_provision
    if sha256_bytes(data) != provision_pin:
        die(f"pins.iss's ProvisionSha256 is not the SHA-256 of the provision.sh Setup runs ({sha256_bytes(data)})")

    release = _release_json(t, tag) if (publish or t.gh_cmd) else None
    if release is None:
        if publish:
            die(f"no GitHub release {tag} (or gh can't read it): android/scripts/release.ps1 publishes it first")
        say(f"warning: no GitHub release {tag} to check (or no gh): the run goes on with the manifest's files")
    else:
        if release.get("isDraft"):
            die(f"the GitHub release {tag} is a draft: publish it first")
        _check_published_apks(release, apks)
    prerelease = by_name or bool(release and release.get("isPrerelease"))

    stable = ("skip", None)
    if not prerelease:
        stable = _stable_plan(t, tag_commit) if fetched else ("unknown", None)
        if stable[0] == "refused":
            die(f"stable on GitHub is {stable[1]}, which the tag's commit doesn't come after: refusing "
                "(stable only ever fast-forwards to a release; it is never rewound or forced)")

    # 2. The Setup. ---------------------------------------------------------
    stage = repo / "windows" / "build" / "release" / tag
    if args.setup:
        setup = Path(args.setup).resolve()
        if not setup.is_file():
            die(f"no Setup at {args.setup}")
        say(f"using {setup} (built by windows/build.ps1 from {head[:12]}, it is taken to be)")
    else:
        setup = (build_setup or _build_setup)(repo)
        if t.git("status", "--porcelain").strip():
            die("building the Setup changed tracked files: look at git status")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    staged_setup = stage / SETUP_ASSET
    shutil.copyfile(setup, staged_setup)

    # 3 and 4. SHA256SUMS and the notes. --------------------------------------
    if args.since and not t.git_ok("rev-parse", "--verify", "--quiet", f"refs/tags/{args.since}"):
        die(f"no tag {args.since} (--since)")
    since = args.since or _previous_tag(t, tag_commit, prerelease)
    hand = t.git_bytes("show", f"{tag_commit}:docs/release/{tag}.md")
    info = Release(tag=tag, version=version, prerelease=prerelease, tag_commit=tag_commit, setup_commit=head,
                   cert=cert, apks=apks, setup_sha256=sha256_file(staged_setup), previous_tag=since)
    if hand is not None:
        info.notes = hand.decode("utf-8")
        say(f"the notes: docs/release/{tag}.md at the tag")
    else:
        current = (t.git_bytes("show", f"{tag_commit}:docs/RELEASE_NOTES.md") or b"").decode("utf-8")
        previous = t.git_bytes("show", f"{since}:docs/RELEASE_NOTES.md") if since else None
        new = new_entries(current, previous.decode("utf-8") if previous is not None else None)
        info.notes = ""
        frame = len(body(info))
        info.notes = summary(new, tag=tag, since=since, budget=max(2000, BODY_BUDGET - frame))
        say(f"the notes: {len(new)} entries of docs/RELEASE_NOTES.md since {since or 'the start'}")
    text = body(info)
    if len(text) > BODY_LIMIT:
        die(f"the notes are {len(text)} characters; GitHub takes {BODY_LIMIT} at most")
    sums = sums_text(info)
    (stage / SUMS_ASSET).write_bytes(sums.encode("ascii"))
    notes_file = stage / "NOTES.md"
    notes_file.write_bytes(text.encode("utf-8"))
    kind = "a pre-release" if prerelease else "the Latest release"
    say(f"staged in {stage}: {SETUP_ASSET} ({staged_setup.stat().st_size} bytes), {SUMS_ASSET}, NOTES.md ({len(text)} characters)")

    stable_push = f"{tag}^{{commit}}:refs/heads/stable"
    if not publish:
        say("not published (--no-publish). A real run then:")
        print(f"    gh release upload {tag} {SETUP_ASSET} {SUMS_ASSET} --repo {REPO_SLUG} --clobber")
        print(f"    gh release edit {tag} --repo {REPO_SLUG} --title \"Domovoi v{version}\" --notes-file NOTES.md "
              + ("--prerelease" if prerelease else "--latest") + f"    ({kind})")
        print("    (checks every file on GitHub against SHA256SUMS)")
        if prerelease:
            print("    stable stays where it is: a pre-release never moves it")
        elif stable[0] == "already":
            print(f"    stable is already at {tag}")
        elif stable[0] == "unknown":
            print("    stable: unknown (no fetch)")
        else:
            frm = f" (a fast-forward from {stable[1][:12]})" if stable[1] else " (stable is made)"
            print(f"    git push origin \"{stable_push}\"{frm}")
        return 0

    # 5. Upload and edit. -----------------------------------------------------
    say(f"uploading {SETUP_ASSET} and {SUMS_ASSET} to {tag}...")
    t.gh("release", "upload", tag, str(staged_setup), str(stage / SUMS_ASSET), "--repo", REPO_SLUG, "--clobber")
    say(f"setting the title and notes, as {kind}...")
    t.gh("release", "edit", tag, "--repo", REPO_SLUG, "--title", f"Domovoi v{version}", "--notes-file", str(notes_file),
         "--prerelease" if prerelease else "--latest")

    # 6. Check what GitHub has. -----------------------------------------------
    _verify_published(t, tag, sums)
    say("every file on GitHub matches SHA256SUMS")

    # 7. LAST: stable. --------------------------------------------------------
    if prerelease:
        say("stable stays where it is: a pre-release never moves it")
    else:
        stable = _stable_plan(t, tag_commit)
        if stable[0] == "refused":
            die(f"stable on GitHub moved to {stable[1]}, which the tag doesn't come after: refusing")
        if stable[0] == "already":
            say(f"stable is already at {tag}")
        else:
            say(f"moving stable to {tag} ({tag_commit[:12]})" + (f", a fast-forward from {stable[1][:12]}" if stable[1] else ", the first time"))
            t.git("push", "origin", stable_push)
            if _remote_ref(t, "refs/heads/stable") != tag_commit:
                die("stable on GitHub is not the tag's commit after the push")
    say(f"done: {tag} has the Setup, SHA256SUMS and its notes" + ("" if prerelease else "; stable is at it") + ".")
    return 0


def _build_setup(repo: Path) -> Path:
    """DomovoiSetup.exe, from the installer's own build script."""
    if os.name != "nt":
        die("the Windows Setup builds on Windows only (windows/build.ps1): run finish-release.ps1 there, "
            "or pass --setup with a DomovoiSetup.exe built from this commit")
    shell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not shell:
        die("no Windows PowerShell to run windows/build.ps1")
    print("finish-release: building the Windows Setup (windows/build.ps1)...", flush=True)
    p = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(repo / "windows" / "build.ps1")], cwd=repo)
    if p.returncode != 0:
        die("windows/build.ps1 failed")
    exe = repo / "windows" / "build" / SETUP_ASSET
    if not exe.is_file():
        die(f"windows/build.ps1 left no {exe}")
    return exe


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")  # a Windows console's code page
        except (AttributeError, ValueError):
            pass
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except Refused as e:
        print(f"finish-release: {e}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
