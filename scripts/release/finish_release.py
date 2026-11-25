"""Finish a Domovoi release: the Windows Setup, one SHA256SUMS, the notes, then ``stable``.

The release number is Domovoi's own version (``pyproject.toml``; owner
decision 2026-11-14): every release is one tag ``v<that version>`` and one
GitHub release. The Android app keeps its own versionName and versionCode
and is released only when it changes. So there are two paths:

(a) **The app changed.** ``android/scripts/release.ps1`` (or ``.sh``) runs
    first: it builds and signs the APKs, commits ``android/release.json``
    (whose ``tag`` is this release's, ``v`` + pyproject's version) on top of
    the commit they were built from, tags that commit and publishes the
    GitHub release with the APKs. Then this script finishes it.
(b) **Server and Windows only.** This script alone: the release commit is
    pushed to main with ``android/release.json`` unchanged, still naming
    the earlier release that holds the app's APKs. This script tags the
    commit (HEAD, unless the tag exists), and on a real run pushes the tag
    and creates the GitHub release with no APKs.

From a clean checkout at the tag (path b: at the release commit) or after
it (``git pull --ff-only``)::

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\release\\finish-release.ps1 [-Tag v1.0.0] [-NoPublish]
    bash scripts/release/finish-release.sh [--tag v1.0.0] [--no-publish] [--setup FILE]

In order, refusing at the first problem (nothing on GitHub changes until
every check has passed):

1. The checks.
   - ``origin`` is the GitHub repository, every fetch and push URL of it.
   - The checkout is clean, and HEAD is the tag's commit or after it.
   - The tag (default ``v`` + HEAD's ``pyproject.toml`` version; lightweight
     or annotated, peeled to its commit) is on GitHub at that same commit
     (path b: or nowhere yet, then made here at HEAD, which must be on
     origin/main), and its ``pyproject.toml`` says the same version.
   - The tag's ``android/release.json`` names this tag (path a) or another
     one (path b).
     Path a: the tag's commit is ``android/release.json`` on top of the
     commit that file names (the one the APKs were built from), and
     nothing else; the GitHub release exists and holds the APKs the
     manifest pins (size and GitHub's SHA-256 digest). The app's own tag
     ``app-<versionName>`` is here and on GitHub at the commit the APKs
     were built from (release.ps1|.sh push it; this script never makes or
     moves it), the tag holds ``android/play/WHATS-NEW-v<versionName>.txt``,
     and the GitHub release of that tag, if there is one yet, is a full
     (not pre-) release holding nothing but those APKs, each the pinned file.
     Path b: that other tag is an earlier release (here and on GitHub at
     the same commit, before this one), its ``android/release.json`` is
     byte for byte the one at this tag, the app at this tag
     (``versionName``, ``versionCode`` in android/app/build.gradle.kts) is
     the one it names, and its GitHub release holds those APKs. This
     tag's GitHub release, if it exists yet, holds no APKs.
   - The release holds nothing finish-release doesn't know.
   - For a release without a suffix, ``stable`` can fast-forward to the
     tag's commit.
2. ``DomovoiSetup.exe``, built from the tag's own files: a clean, detached
   worktree at the tag's commit, where the installer's own script runs as
   ``windows/build.ps1 -Pin <tag>`` (it moves that worktree's pins to the
   tag's commit, provision.sh fetched from it, then builds). The worktree
   must then differ from the tag in ``windows/installer/pins.iss`` only,
   and those pins must install the tag's commit with the SHA-256 of its
   ``provision.sh`` and nothing bundled; anything else is refused. The
   worktree is removed afterwards; its ``pins.iss`` is kept beside the
   Setup. ``--setup FILE`` takes one built that way elsewhere (on Linux,
   say), and the notes say it was. The same build makes
   ``DomovoiTray.exe`` (``windows/build/bin``), the tray icon Setup
   installs, attached on its own too for Windows installs from before the
   tray (it runs from anywhere and registers itself); with ``--setup``, from
   ``--tray FILE`` or ``bin/DomovoiTray.exe`` beside the Setup. A tag whose
   build makes none (before the tray) is released without it. The same
   build makes ``DomovoiSatelliteSetup.exe`` (``windows/build``, beside the
   Setup; 2026-10-21), the double-click Setup that makes a spare Windows
   PC a room, attached beside ``DomovoiSetup.exe``: Domovoi servers fetch
   it from the release (checked against its ``SHA256SUMS``) and hand it to
   the household's PCs. With ``--setup``, from ``--satellite-setup FILE``
   or the one beside the Setup. A tag whose build makes none is released
   without it.
   Then, when the tag pins Domovoi's release key
   (``windows/installer/release-signing.pub``, which the Setup's programs
   embed), the Windows update's signatures (docs/WINDOWS_HOST.md, "Updates
   and the release channel"): ``DomovoiSetup.manifest.json`` (kind, tag,
   version, the tag's commit, the Setup's size and SHA-256), and
   ``DomovoiSetup.exe.sig`` and ``DomovoiSetup.manifest.json.sig``, detached
   Ed25519 signatures (SSHSIG) made by ``ssh-keygen -Y sign`` with the
   private half the owner names (``--signing-key FILE`` or
   ``DOMOVOI_RELEASE_SIGNING_KEY``; outside the checkout, refused inside it,
   and never read by this script: ssh-keygen reads it, and asks for its
   passphrase itself), each checked with ``ssh-keygen -Y verify`` against
   the tag's pinned public key before anything is published. A tag that
   pins a key is refused without one; a tag that pins none is released
   without signatures, and the Windows side then has no update step.
   ``--mac-pkg FILE`` (absent by default, and then nothing below changes):
   ``DomovoiSetup-mac.pkg``, the macOS installer, which builds on a Mac
   only (the release workflow's ``build-mac`` job, on a macOS runner),
   handed in with its build record (``DomovoiSetup-mac.build.json`` beside
   it, or ``--mac-build-record``: kind, tag, the commit it was built from,
   the ref it installs, the package's size and SHA-256). The record must
   name this tag and describe that very file, and its commit must be the
   tag's, or, when the app changed (path a), the commit the tag's
   ``android/release.json`` commit sits on (the workflow builds the package
   while it waits for approval, before that commit exists; the package
   holds nothing of ``android/``), with the tag as the ref it installs.
   Anything else stops the run. It is attached as ``DomovoiSetup-mac.pkg``,
   and when the Windows Setup is signed, signed the same way (key file or
   agent) under its own namespaces (``domovoi-mac-setup``,
   ``domovoi-mac-setup-manifest``): ``DomovoiSetup-mac.manifest.json`` and
   the two ``.sig`` files.
   Then, with ``--sbom-dir DIR`` (the release workflow's ``sbom`` job makes
   them with scripts/release/sbom.py), the software bills of materials,
   CycloneDX JSON: ``domovoi-server.cdx.json`` and ``domovoi-web.cdx.json``
   always, ``domovoi-android.cdx.json`` exactly when the release carries
   the APKs (path a). Each must name this release (the app's: the
   versionName android/release.json names). Without ``--sbom-dir`` (a
   local run) the release has none.
3. One ``SHA256SUMS`` (LF line endings) for every asset: the APKs (from
   the manifest; path a only), the Setup and its signatures and manifest, the tray,
   the satellite Setup, the Mac package and its signatures when given, and
   the SBOMs.
4. The release notes, in the house order (docs/CONTRIBUTING.md,
   "Releasing"): first a Download section, each file a direct link on
   the release with what it is for (the Linux one-liner copied from the
   tag's docs/LINUX_HOST.md, Google Play and the APKs, the room Setup, how
   an installed Domovoi updates). From 1.5.0, then the release's three
   Highlights (``## Highlights`` of ``docs/release/v<version>.md``: exactly
   3 plain one-line items, the same 3 under ``### Highlights`` in its dated
   section of ``docs/RELEASE_NOTES.md``; refused otherwise, here and in the
   plan job; houses read them with the same parser,
   domovoi/release_highlights.py). Then What's new, with a line linking
   the release's full notes and its RELEASE_NOTES.md section at the tag:
   the ``## At a glance`` section of ``docs/release/v<version>.md`` at the
   tag if there is one (the file is the full notes; see
   :func:`release_doc_problems`; refused otherwise), else a summary of the
   ``docs/RELEASE_NOTES.md`` entries added since the previous tag (a
   release candidate counts from the tag before it; a release from the
   release before it), with links into the full file at the tag. Last,
   how to check a download, then (collapsed) the files and their SHA-256,
   the commit the tag is, how the Setup was built, the app's signing
   certificate. Kept under 20 KB, and never over GitHub's 125,000-character
   cap.
5. Path b, a tag made here: push it (never forced), then create the
   GitHub release with the files (``--prerelease`` marks it one).
   Otherwise: upload the Setup, the tray, the satellite Setup, the SBOMs and ``SHA256SUMS`` (``--clobber``, so a second run
   replaces them), set the title and the notes, and keep a pre-release one
   (only one GitHub already marks so: every release is a full one by
   default, release candidates included, owner decision 2026-10-15) or
   mark it Latest.
6. Check every asset on GitHub against ``SHA256SUMS``.
   Then, path a only: the app's own GitHub release ``app-<versionName>``
   (owner decision 2026-11-20, for F-Droid, which fetches
   ``releases/download/app-<v>/domovoi-<v>-<abi>.apk``), at the existing
   tag (``--verify-tag``), titled "Domovoi for Android <versionName>", its
   notes the tag's WHATS-NEW file and a line linking this release, and
   ``--latest=false`` (never Latest, never a pre-release: installers and
   the update check read this release, which must stay Latest). Its files
   are this release's APKs, downloaded from it and checked against
   ``android/release.json``, under the same names. A run that finds it
   made uploads only the APKs it lacks (never ``--clobber``: a file there
   that isn't the pinned one stops the run). Each APK is then downloaded
   back from it and checked, and for a release that isn't a pre-release,
   GitHub's Latest is checked to be still this one.
7. LAST, only for a release without a suffix that is not a pre-release:
   the tag on GitHub is checked once more, then
   ``git push origin <the tag's commit>:refs/heads/stable`` (the SHA that
   was checked, never the tag's name again), never forced: a ``stable``
   that is not an ancestor of it is refused.

``--no-publish`` runs 1-4 (it fetches and reads the GitHub release, never
changes it), leaves the Setup, its ``pins.iss``, ``SHA256SUMS`` and
``NOTES.md`` in ``windows/build/release/<tag>/``, and prints what a real
run would do. Without a signing key it leaves the signatures out and says
so (a real run refuses). A tag it made here for path b (the Setup's build
pins to it) is deleted again at the end, as it is when a real run stops
before pushing it.

For the release workflow (``.github/workflows/release.yml``) there are two
opt-in flags; the owner's local runs never need them. ``--plan FILE`` is
its gate: it checks HEAD is a release to make or to finish (a new one's
``docs/release/v<version>.md`` in the house style, too) and writes what
to run to FILE as JSON (see :func:`plan`), changing nothing.
``--signing-agent`` signs with the release key an ssh-agent holds
(``SSH_AUTH_SOCK``) instead of a key file: ssh-keygen is handed the tag's
public key and asks the agent, so nothing prompts and no key is on disk.
``--sbom-dir DIR`` attaches the SBOMs (step 2), and ``--provenance`` says
in the notes how to check each file's build provenance, which the
workflow's ``attest`` job records once this script has published them.

git, gh and Windows PowerShell are run by their absolute paths, never from
the checkout (Windows would look in the current folder first). Standard
library only. Never reads a key or a password (ssh-keygen reads the
release key's private half, at the path given); the only writes
outside ``windows/build/`` are a temporary worktree's registration in
``.git``, path b's tag (here, then on GitHub), the GitHub release's assets
and notes, and the ``stable`` branch.
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
import tomllib
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NoReturn

REPO = Path(__file__).resolve().parents[2]


def _load_highlights_parser():
    """domovoi/release_highlights.py, by path: the one parser the house
    uses for the Highlights too (update notices), so the two can't
    disagree. Standard library only, like this script."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "domovoi_release_highlights", Path(__file__).resolve().parents[2] / "domovoi" / "release_highlights.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


release_highlights = _load_highlights_parser()
REPO_SLUG = "coders-farm-official/domovoi"
REPO_URL = "https://github.com/" + REPO_SLUG
SETUP_ASSET = "DomovoiSetup.exe"
#: The tray icon (windows/tray), built beside the Setup in windows/build/bin.
TRAY_ASSET = "DomovoiTray.exe"
#: The Windows satellite Setup (windows/installer/DomovoiSatelliteSetup.iss),
#: built beside the Setup in windows/build. Its name is the one Domovoi
#: servers fetch (domovoi/satellite_setup.py SETUP_ASSET).
SATELLITE_SETUP_ASSET = "DomovoiSatelliteSetup.exe"
SUMS_ASSET = "SHA256SUMS"
#: The Windows update's signatures (windows/common/ReleaseSignature.cs reads
#: them): the Setup's, its manifest, and the manifest's.
SETUP_SIG_ASSET = "DomovoiSetup.exe.sig"
MANIFEST_ASSET = "DomovoiSetup.manifest.json"
MANIFEST_SIG_ASSET = "DomovoiSetup.manifest.json.sig"
SIGNATURE_ASSETS = (SETUP_SIG_ASSET, MANIFEST_ASSET, MANIFEST_SIG_ASSET)
#: The macOS installer (mac-installer PLAN 4.11-4.12), built on a Mac from
#: the tag and handed in with --mac-pkg; a release without it is unchanged.
MAC_SETUP_ASSET = "DomovoiSetup-mac.pkg"
MAC_SETUP_SIG_ASSET = "DomovoiSetup-mac.pkg.sig"
MAC_MANIFEST_ASSET = "DomovoiSetup-mac.manifest.json"
MAC_MANIFEST_SIG_ASSET = "DomovoiSetup-mac.manifest.json.sig"
MAC_SIGNATURE_ASSETS = (MAC_SETUP_SIG_ASSET, MAC_MANIFEST_ASSET, MAC_MANIFEST_SIG_ASSET)
MAC_SETUP_NAMESPACE = "domovoi-mac-setup"
MAC_MANIFEST_NAMESPACE = "domovoi-mac-setup-manifest"
MAC_MANIFEST_KIND = "domovoi-mac-setup"
#: What the Mac build writes beside the package (and its "kind").
MAC_BUILD_RECORD = "DomovoiSetup-mac.build.json"
MAC_BUILD_KIND = "domovoi-mac-setup-build"
#: The software bills of materials (scripts/release/sbom.py ASSET_NAMES),
#: CycloneDX JSON, attached by --sbom-dir; the app's only with its APKs.
SERVER_SBOM_ASSET = "domovoi-server.cdx.json"
WEB_SBOM_ASSET = "domovoi-web.cdx.json"
ANDROID_SBOM_ASSET = "domovoi-android.cdx.json"
SBOM_ASSETS = (SERVER_SBOM_ASSET, WEB_SBOM_ASSET, ANDROID_SBOM_ASSET)
#: The public half of Domovoi's release key, at the tag (build.ps1 embeds it).
RELEASE_KEY = "windows/installer/release-signing.pub"
SETUP_NAMESPACE = "domovoi-windows-setup"
MANIFEST_NAMESPACE = "domovoi-windows-setup-manifest"
MANIFEST_KIND = "domovoi-windows-setup"
#: The name the checks give the key in their allowed-signers line.
SIGNER = "domovoi-release"
#: GitHub refuses a release body over this many characters.
BODY_LIMIT = 125_000
#: What the notes aim for: the summary shrinks to fit.
BODY_BUDGET = 20_000
#: A first paragraph longer than this is left out of the summary.
PARAGRAPH_MAX = 400
#: A release tag, whole (use with fullmatch): ASCII digits only.
TAG_RE = re.compile(r"v([0-9]+\.[0-9]+\.[0-9]+)(-[0-9A-Za-z.]+)?", re.ASCII)
#: origin's URLs that are the GitHub repository (or DOMOVOI_RELEASE_ORIGIN_URL, for the tests).
ORIGIN_RE = re.compile(r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
                       r"coders-farm-official/domovoi(?:\.git)?/?", re.ASCII | re.IGNORECASE)
SHA_RE = re.compile(r"^[0-9a-f]{40}\Z")
PINS = "windows/installer/pins.iss"
STABLE_STAYS = "only a release without a suffix, not marked a pre-release, moves it"

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
    prerelease: bool         # GitHub marks it a pre-release
    tag_commit: str
    cert: str
    apks: list[dict[str, Any]]
    setup_sha256: str
    setup_built: bool = True  # built here from the tag (False: --setup)
    previous_tag: str | None = None
    notes: str = ""          # the summary part, from docs/release or RELEASE_NOTES.md
    candidate_of: str | None = None   # X.Y.Z, when the version has a suffix
    tray_sha256: str | None = None    # DomovoiTray.exe, when the tag's build makes one
    satellite_setup_sha256: str | None = None  # DomovoiSatelliteSetup.exe, likewise
    signatures: dict[str, str] | None = None  # the Windows update's files -> their SHA-256
    #: Path b: the app this release hands phones, released earlier
    #: (version_name, version_code, tag); its APKs are not in ``apks``.
    app_from: dict[str, Any] | None = None
    sboms: dict[str, str] | None = None  # the SBOMs attached -> their SHA-256
    provenance: bool = False  # the release workflow attests every file (--provenance)
    #: The one-line Linux install, exactly as the tag's docs/LINUX_HOST.md has
    #: it (:func:`linux_install_line`); None when it has none.
    linux_install: str | None = None
    full_notes: bool = False          # docs/release/<tag>.md at the tag: the full notes
    notes_anchor: str | None = None   # this release's section of RELEASE_NOTES.md (:func:`notes_anchor`)
    mac_setup_sha256: str | None = None       # DomovoiSetup-mac.pkg, when --mac-pkg gave one
    mac_signatures: dict[str, str] | None = None  # its signature files -> their SHA-256
    #: The release's three Highlights (docs/release/<tag>.md), from 1.5.0 on:
    #: right after Download on the page. None or [] = no section.
    highlights: list[str] | None = None


def _who(abi: str) -> str:
    return "the Android app, most phones (64-bit)" if abi == "arm64-v8a" else "the Android app, older or cheaper phones (32-bit)"


#: The app on Google Play (README.md links the same page).
PLAY_URL = "https://play.google.com/store/apps/details?id=com.domovoi.app"
#: Where the release body sends people, as (file at the tag, heading). The
#: tests hold each heading to the file, so a renamed one fails there.
LINUX_DOC = ("docs/LINUX_HOST.md", "One command")
PI_ROOM_DOC = ("scripts/manual/manual.md", "A Raspberry Pi room")
#: How an installed Domovoi updates (Settings → System → About & updates).
#: The Mac package is signed ad hoc (no Apple Developer ID yet), so
#: Gatekeeper stops it the first time; Apple's steps for macOS 26 ("Open a
#: Mac app from an unknown developer"): System Settings, Privacy & Security,
#: Security, Open Anyway, for about an hour after the try.
MAC_GATEKEEPER_LINE = ("The Mac package is not from an identified developer yet, so the first time you open it, "
                       "macOS won't. Open System Settings → Privacy & Security, go to Security and click "
                       "**Open Anyway** (it is there for about an hour after you tried), then enter your "
                       "login password.")
UPDATE_LINE = ("**Already installed?** Update from the dashboard: "
               "Settings → System → About & updates → **Update Domovoi**.")
#: The one-line install in docs/LINUX_HOST.md, whole.
LINUX_INSTALL_RE = re.compile(r"^curl -fsSL https://\S+/scripts/linux/install\.sh \| sudo bash$", re.M)
#: The section of docs/release/v<version>.md (the release's FULL notes)
#: that goes on the release page: the highlights, then short sections.
GLANCE = "At a glance"
#: The release's three Highlights (from 1.5.0): ``## Highlights`` right
#: above ``## At a glance`` in docs/release/v<version>.md, ``### Highlights``
#: in its dated section of RELEASE_NOTES.md, ``## Highlights`` on the page
#: right after Download. Houses show them in About & updates before they
#: update (domovoi/update_notice.py), read with the same parser.
HIGHLIGHTS = "Highlights"
#: The first release whose notes must carry them (the v1.4.x tags can't
#: change, so their files have none).
HIGHLIGHTS_FROM = (1, 5, 0)


def highlights_required(version: str | None) -> bool:
    m = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)(?:-.*)?", version or "")
    return bool(m) and tuple(int(x) for x in m.groups()[:3]) >= HIGHLIGHTS_FROM


def release_highlights_of(text: str) -> list[str]:
    """The three Highlights of a docs/release/v<version>.md, or []."""
    return release_highlights.parse_highlights(text.encode("utf-8"))


def _highlights_problems(text: str) -> list[str]:
    """The Highlights rules (docs/CONTRIBUTING.md, "The release page")."""
    text = text.replace("\r\n", "\n")
    items = release_highlights_of(text)
    if not items:
        return [f"it needs exactly one \"## {HIGHLIGHTS}\" section of exactly 3 items (\"- text\", each 8 to 80 "
                "characters (code points: an emoji counts once), plain words, no nesting): houses show them before they update"]
    problems = release_highlights.markdown_problems(items)
    heads = [(i, lvl, h) for i, lvl, h in _headings(text) if lvl <= 2]
    at = [k for k, (_, _, h) in enumerate(heads) if _heading_text(h).strip() == HIGHLIGHTS]
    if at and (at[0] + 1 >= len(heads) or _heading_text(heads[at[0] + 1][2]).strip() != GLANCE):
        problems.append(f"\"## {HIGHLIGHTS}\" must come right above \"## {GLANCE}\"")
    return problems


def notes_highlights(release_notes: str, version: str) -> list[str] | None:
    """The ``### Highlights`` of RELEASE_NOTES.md's dated section for this
    release: its items, [] when the section has none valid, None when
    there is no dated section."""
    for e in entries(release_notes):
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} — " + re.escape(version), _heading_text(e.heading).strip()):
            # The heading, then its list: the lines up to the first blank
            # line after it (the section's other bullets follow).
            lines = e.body.split("\n")
            at = [n for n, line in enumerate(lines) if line.rstrip() == f"### {HIGHLIGHTS}"]
            if len(at) != 1:
                return []
            block, started = [f"## {HIGHLIGHTS}"], False
            for line in lines[at[0] + 1:]:
                if not line.strip():
                    if started:
                        break
                    continue
                started = True
                block.append(line)
            return release_highlights.parse_highlights("\n".join(block).encode("utf-8"))
    return None


def highlights_match_problems(doc_text: str, release_notes: str, version: str) -> list[str]:
    """RELEASE_NOTES.md's dated section must carry the same three
    Highlights as docs/release/v<version>.md."""
    want = release_highlights_of(doc_text.replace("\r\n", "\n"))
    got = notes_highlights(release_notes, version)
    if got is None:
        return [f"docs/RELEASE_NOTES.md has no dated section for {version} (\"## YYYY-MM-DD — {version}\") to "
                f"carry its \"### {HIGHLIGHTS}\""]
    if got != want:
        return [f"the \"### {HIGHLIGHTS}\" in docs/RELEASE_NOTES.md's {version} section differ from "
                f"docs/release/v{version}.md's"]
    return []
#: Headings the script writes on the page itself: never in the page part.
GENERATED_HEADINGS = ("Download", "What's new", "Files", "Files and checksums", "Install or update")


def linux_install_line(linux_host: str | None) -> str | None:
    """The one-line install command, copied from docs/LINUX_HOST.md (never
    retyped): its first ``curl … install.sh | sudo bash`` line."""
    m = LINUX_INSTALL_RE.search((linux_host or "").replace("\r\n", "\n"))
    return m.group(0) if m else None


def _glance(text: str) -> list[Entry]:
    return [e for e in entries(text) if _heading_text(e.heading).strip() == GLANCE]


def release_doc_problems(text: str, version: str | None = None) -> list[str]:
    """What keeps a ``docs/release/v<version>.md`` from the house style
    (docs/CONTRIBUTING.md, "The release page"). The file is the release's
    full notes, title, intro and all; only its one ``## At a glance``
    section goes on the page, under the What's new heading the script
    writes after Download, so Download stays first. That section starts
    with the highlights (a list), and its own headings (``###``, shown as
    ``##`` on the page) are none of the script's."""
    text = text.replace("\r\n", "\n")
    found = _glance(text)
    if len(found) != 1:
        return [f"it needs exactly one \"## {GLANCE}\" section (it has {len(found)}): the page carries that "
                "section, the file the full notes"]
    problems = []
    lines = [line for line in found[0].body.split("\n") if line.strip()]
    if not lines or not re.match(r"^[*+-]\s", lines[0].strip()):
        problems.append(f"\"## {GLANCE}\" must start with the highlights, a list of one-line items")
    for _, _level, heading in _headings(found[0].body):
        if _heading_text(heading).strip() in GENERATED_HEADINGS or heading.startswith("What's new"):
            problems.append(f"\"## {GLANCE}\" has a \"{heading}\" heading, which finish_release.py writes itself")
    # From 1.5.0, exactly one valid ``## Highlights`` above the glance.
    if highlights_required(version):
        problems += _highlights_problems(text)
    return problems


def glance_notes(text: str, *, since: str | None) -> str:
    """The page's part of the full notes: ``## What's new since <since>``,
    then the ``## At a glance`` section with its ``###`` headings as ``##``."""
    body = _glance(text.replace("\r\n", "\n"))[0].body
    out, fence = [], None
    for line in body.split("\n"):
        m = re.match(r"^\s*(`{3,}|~{3,})", line)
        if m:
            fence = None if fence else m.group(1)[0]
        elif fence is None and line.startswith("### "):
            line = line[1:]
        out.append(line)
    head = "## What's new"
    if since:
        head += f" since {since[1:] if since.startswith('v') else since}"
    return head + "\n\n" + "\n".join(out).strip("\n") + "\n"


def notes_anchor(release_notes: str, version: str) -> str | None:
    """GitHub's anchor of RELEASE_NOTES.md's dated heading for this
    release (``## 2026-11-16 — 1.3.0`` is ``2026-11-16--130``)."""
    for e in entries(release_notes):
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} — " + re.escape(version), _heading_text(e.heading).strip()):
            return e.anchor
    return None


def more_detail(rel: Release) -> str:
    """The line under What's new: the release's full notes, and its section
    of RELEASE_NOTES.md, both at the tag."""
    links = []
    if rel.full_notes:
        links.append(f"[the full {rel.version} release notes]({REPO_URL}/blob/{rel.tag}/docs/release/{rel.tag}.md)")
    changes = f"{REPO_URL}/blob/{rel.tag}/docs/RELEASE_NOTES.md" + (f"#{rel.notes_anchor}" if rel.notes_anchor else "")
    links.append(f"[every change in {rel.version}]({changes})")
    return "**More detail:** " + " and ".join(links) + "."


def _with_more_detail(notes: str, line: str) -> str:
    """``line`` right under the notes' What's new heading (on top without one)."""
    rows = notes.strip("\n").split("\n")
    for i, row in enumerate(rows):
        if row.startswith("## What's new"):
            return "\n".join(rows[:i + 1] + ["", line] + rows[i + 1:])
    return "\n".join([line, "", *rows])


def _asset_url(tag: str, name: str) -> str:
    return f"{REPO_URL}/releases/download/{tag}/{name}"


def _doc_url(tag: str, doc: tuple[str, str]) -> str:
    return f"{REPO_URL}/blob/{tag}/{doc[0]}#{github_slug(doc[1], {})}"


def download(rel: Release) -> list[str]:
    """The Download section, first on the page: each item a direct link to
    its file on this release (the app's APKs: on the release that holds
    them), and one plain line on what it is for."""
    out = ["## Download", ""]
    out.append(f"- **Windows 11 PC — the server:** [{SETUP_ASSET}]({_asset_url(rel.tag, SETUP_ASSET)})")
    if rel.mac_setup_sha256:
        out.append(f"- **Mac (Apple silicon, macOS 26 or later) — the server:** "
                   f"[{MAC_SETUP_ASSET}]({_asset_url(rel.tag, MAC_SETUP_ASSET)})")
    linux_doc = _doc_url(rel.tag, LINUX_DOC)
    if rel.linux_install:
        out += [f"- **Linux — the server:** run this in a terminal ([what it does]({linux_doc}))", "",
                "  ```bash", f"  {rel.linux_install}", "  ```", ""]
    else:
        out.append(f"- **Linux — the server:** [docs/LINUX_HOST.md]({linux_doc})")
    apk_tag = rel.app_from["tag"] if rel.app_from else rel.tag
    apks = (rel.app_from or {}).get("apks") or rel.apks
    by_abi = {e["abi"]: e["file"] for e in apks}
    links = [f"[{by_abi[abi]}]({_asset_url(apk_tag, by_abi[abi])})" for abi in ("arm64-v8a", "armeabi-v7a")
             if abi in by_abi]
    app = f"- **Android phones:** [Google Play]({PLAY_URL})"
    if links:
        app += f", or the APK: {' or '.join(links)}"
        app += (". Which one? 64-bit (arm64-v8a) for almost every phone; 32-bit (armeabi-v7a) for an old or "
                "budget one.")
    else:
        app += "."
    out.append(app + " Your own server's join page offers the app too.")
    pi_doc = _doc_url(rel.tag, PI_ROOM_DOC)
    if rel.satellite_setup_sha256:
        out.append(f"- **Rooms:** [{SATELLITE_SETUP_ASSET}]({_asset_url(rel.tag, SATELLITE_SETUP_ASSET)}) makes a "
                   f"spare Windows PC a room. A Raspberry Pi room is set up from your dashboard: "
                   f"[how]({pi_doc}).")
    else:
        out.append(f"- **Rooms:** a Raspberry Pi room is set up from your dashboard: [how]({pi_doc}).")
    out.append(f"- {UPDATE_LINE}")
    setups = "The Windows Setups are" if rel.satellite_setup_sha256 else "The Windows Setup is"
    out += ["", f"{setups} not code-signed yet: when Windows says \"Windows protected your PC\", choose "
                "More info, then Run anyway."]
    if rel.mac_setup_sha256:
        out += ["", MAC_GATEKEEPER_LINE]
    return out


def body(rel: Release) -> str:
    """The whole release body: Download first, then the summary (what's
    new and the rest, hand-written or from RELEASE_NOTES.md), then every
    file with its SHA-256 and how to check one, last."""
    if rel.candidate_of:
        lead = (f"**Domovoi {rel.version}** is a candidate for {rel.candidate_of}, for testing before it. "
                "Servers that follow `stable` don't get it.")
    elif rel.prerelease:
        lead = (f"**Domovoi {rel.version}** is a pre-release. "
                "Servers that follow `stable` don't get it.")
    else:
        lead = ""
    files = ["<details>", "<summary>Every file, with its SHA-256</summary>", "",
             "| file | what | SHA-256 |", "|---|---|---|"]
    for e in rel.apks:
        files.append(f"| {e['file']} | {_who(e['abi'])} | `{e['sha256']}` |")
    files.append(f"| {SETUP_ASSET} | the Windows installer (preview, not code-signed) | `{rel.setup_sha256}` |")
    for name, what in ((SETUP_SIG_ASSET, "the Setup's Ed25519 signature by Domovoi's release key, for the Windows update"),
                       (MANIFEST_ASSET, "what the Setup is (version, commit, size, SHA-256), for the Windows update"),
                       (MANIFEST_SIG_ASSET, "the manifest's Ed25519 signature by Domovoi's release key")):
        if rel.signatures and name in rel.signatures:
            files.append(f"| {name} | {what} | `{rel.signatures[name]}` |")
    if rel.tray_sha256:
        files.append(f"| {TRAY_ASSET} | the Windows tray icon, for an install from before it (Setup installs it) | `{rel.tray_sha256}` |")
    if rel.satellite_setup_sha256:
        files.append(f"| {SATELLITE_SETUP_ASSET} | makes a spare Windows PC a room (preview, not code-signed) | "
                     f"`{rel.satellite_setup_sha256}` |")
    if rel.mac_setup_sha256:
        files.append(f"| {MAC_SETUP_ASSET} | the Mac installer (Apple silicon, macOS 26 or later; signed ad hoc, "
                     f"not by an identified developer) | `{rel.mac_setup_sha256}` |")
        for name, what in ((MAC_SETUP_SIG_ASSET, "the Mac package's Ed25519 signature by Domovoi's release key, for the Mac update"),
                           (MAC_MANIFEST_ASSET, "what the Mac package is (version, commit, size, SHA-256), for the Mac update"),
                           (MAC_MANIFEST_SIG_ASSET, "the Mac manifest's Ed25519 signature by Domovoi's release key")):
            if rel.mac_signatures and name in rel.mac_signatures:
                files.append(f"| {name} | {what} | `{rel.mac_signatures[name]}` |")
    for name, what in ((SERVER_SBOM_ASSET, "the Python packages a server installs (SBOM, CycloneDX)"),
                       (WEB_SBOM_ASSET, "the dashboard's bundled browser libraries (SBOM, CycloneDX)"),
                       (ANDROID_SBOM_ASSET, "the libraries inside the Android app (SBOM, CycloneDX)")):
        if rel.sboms and name in rel.sboms:
            files.append(f"| {name} | {what} | `{rel.sboms[name]}` |")
    if rel.setup_built:
        how = (f"It was built from the tag's own files by `windows/build.ps1 -Pin {rel.tag}`, which sets its pins "
               "(`windows/installer/pins.iss`) to that commit; nothing else differs from the tag.")
    else:
        how = (f"It was built outside this script and handed to it (`--setup`); `windows/build.ps1 -Pin {rel.tag}` "
               "on the tag's own files builds the same installer.")
    if rel.app_from:
        app = rel.app_from
        app_line = (f"Its `android/release.json` is unchanged: the Android app is still {app['version_name']} "
                    f"(versionCode {app['version_code']}), whose APKs are on the release "
                    f"[{app['tag']}]({REPO_URL}/releases/tag/{app['tag']}); a server fetches them from there and "
                    "hands them to the household's phones.")
    else:
        app_line = ("Its `android/release.json` names the two APKs above, which a server hands to the household's "
                    "phones.")
    files += ["", "</details>"]
    facts = [
        f"- The tag `{rel.tag}` is commit `{rel.tag_commit}`: the code a server installed from this release runs. "
        + app_line,
        f"- `{SETUP_ASSET}` installs that commit. {how} It is not code-signed, so Windows says \"Unknown publisher\": "
        f"[docs/WINDOWS_HOST.md]({REPO_URL}/blob/{rel.tag}/docs/WINDOWS_HOST.md).",
        *([f"- A Windows install updates its Windows side from `{SETUP_ASSET}` once its signatures check out against the "
             "release key its own programs pin (`ssh-keygen -Y verify`, namespace "
             f"`{SETUP_NAMESPACE}`; [docs/WINDOWS_HOST.md]({REPO_URL}/blob/{rel.tag}/docs/WINDOWS_HOST.md))."]
          if rel.signatures else []),
        *([f"- A Windows install from before the tray icon: run `{TRAY_ASSET}` once, from any folder: "
           "Domovoi's icon appears beside the clock and starts at every sign-in from then on "
           f"([docs/WINDOWS_HOST.md]({REPO_URL}/blob/{rel.tag}/docs/WINDOWS_HOST.md#the-tray-icon))."]
          if rel.tray_sha256 else []),
        *([f"- `{MAC_SETUP_ASSET}` installs that commit on a Mac. The release workflow built it on a macOS runner "
             "from the tag's own files (for a release that changes the app: from the commit the tag adds "
             "`android/release.json` to, which the package doesn't hold), with no secrets, and handed it to this "
             "script with its build record. It is signed ad hoc, not with an Apple Developer ID."
             + (f" Its signatures are made the same way, namespace `{MAC_SETUP_NAMESPACE}`." if rel.mac_signatures else "")]
          if rel.mac_setup_sha256 else []),
        f"- The Android app's signing certificate SHA-256: `{rel.cert}` (check with `apksigner verify --print-certs <file>`).",
    ]
    check = ["## Files and checksums", "",
             "Check a download: `sha256sum -c SHA256SUMS --ignore-missing` (Linux, macOS, Git Bash), "
             "or `Get-FileHash -Algorithm SHA256 <file>` (PowerShell) against the line in `SHA256SUMS`."]
    if rel.provenance:
        # OPS-22 (round 6): the strict form only. Without --signer-workflow and
        # --source-ref an attestation from ANY workflow in the repository
        # passes, which a branch pushed by anyone with write access can make.
        check += ["", f"Check that a file was built by Domovoi's release workflow on main: "
                      f"`gh attestation verify <file> -R {REPO_SLUG} "
                      f"--signer-workflow {REPO_SLUG}/.github/workflows/release.yml "
                      f"--source-ref refs/heads/main` "
                      f"([SECURITY.md, Verifying a release]({REPO_URL}/blob/{rel.tag}/SECURITY.md#verifying-a-release))."]
    where = ["<details>", "<summary>Where these files come from</summary>", "", *facts, "", "</details>"]
    # The Highlights right after Download, before What's new (1.5.0 on).
    highlights = ([f"## {HIGHLIGHTS}", "", *(f"- {h}" for h in rel.highlights), ""] if rel.highlights else [])
    parts = [*([lead, ""] if lead else []), *download(rel), "", *highlights,
             _with_more_detail(rel.notes, more_detail(rel)), "",
             *check, "", *files, "", *where]
    return "\n".join(parts).rstrip("\n") + "\n"


def sums_text(rel: Release) -> str:
    """``SHA256SUMS``: ``<sha256>  <file>`` per asset, LF line endings."""
    lines = [f"{e['sha256']}  {e['file']}" for e in rel.apks]
    lines.append(f"{rel.setup_sha256}  {SETUP_ASSET}")
    for name in SIGNATURE_ASSETS:
        if rel.signatures and name in rel.signatures:
            lines.append(f"{rel.signatures[name]}  {name}")
    if rel.tray_sha256:
        lines.append(f"{rel.tray_sha256}  {TRAY_ASSET}")
    if rel.satellite_setup_sha256:
        lines.append(f"{rel.satellite_setup_sha256}  {SATELLITE_SETUP_ASSET}")
    if rel.mac_setup_sha256:
        lines.append(f"{rel.mac_setup_sha256}  {MAC_SETUP_ASSET}")
        for name in MAC_SIGNATURE_ASSETS:
            if rel.mac_signatures and name in rel.mac_signatures:
                lines.append(f"{rel.mac_signatures[name]}  {name}")
    for name in SBOM_ASSETS:
        if rel.sboms and name in rel.sboms:
            lines.append(f"{rel.sboms[name]}  {name}")
    return "\n".join(lines) + "\n"


# ─── versions and pins ───────────────────────────────────────────────────

def pep440(version: str) -> str | None:
    """The tag's version as pyproject.toml spells it: ``1.0.0`` stays,
    ``1.0.0-rc2`` is ``1.0.0rc2`` (``-alphaN`` aN, ``-betaN`` bN); None for
    any other suffix."""
    m = re.fullmatch(r"([0-9]+\.[0-9]+\.[0-9]+)(?:-(alpha|beta|rc)\.?([0-9]+))?", version, re.ASCII)
    if not m:
        return None
    base, pre, n = m.groups()
    return base if not pre else f"{base}{ {'alpha': 'a', 'beta': 'b', 'rc': 'rc'}[pre] }{n}"


def read_pins(text: str) -> tuple[dict[str, str], set[str]]:
    """``#define Name "value"`` pairs and bare ``#define Name`` flags of
    pins.iss (expressions, like ProvisionUrl, and commented-out lines are
    left out)."""
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

def find_exe(name: str, repo: Path) -> str | None:
    """A program on PATH by its absolute path, never one inside the
    checkout: Windows looks in the current folder first unless told not to
    (NoDefaultCurrentDirectoryInExePath, which shutil.which and
    CreateProcess both honour)."""
    os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"
    found = shutil.which(name)
    if not found:
        return None
    path = Path(found).resolve()
    if path.is_relative_to(Path(repo).resolve()):
        die(f"{name} resolves to {path}, inside the checkout: refusing to run it")
    return str(path)


class Tools:
    """git, gh and the downloads, in one place (tests replace gh and the
    downloader)."""

    def __init__(self, repo: Path, gh: list[str] | None, say: Callable[[str], None], git: str | None = None):
        self.repo = repo
        self.gh_cmd = gh
        self.say = say
        self.git_exe = git or find_exe("git", repo)
        if not self.git_exe:
            die("no git on PATH")

    def git_run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([self.git_exe, *args], cwd=self.repo, capture_output=True)

    def git(self, *args: str, check: bool = True) -> str:
        p = self.git_run(*args)
        if check and p.returncode != 0:
            why = p.stderr.decode("utf-8", "replace").strip().splitlines()
            die(f"git {' '.join(args)} failed" + (f": {why[-1]}" if why else ""))
        return p.stdout.decode("utf-8", "replace")

    def git_ok(self, *args: str) -> bool:
        return self.git_run(*args).returncode == 0

    def git_bytes(self, *args: str) -> bytes | None:
        p = self.git_run(*args)
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


def default_gh(repo: Path) -> list[str] | None:
    """The gh to run: ``DOMOVOI_RELEASE_GH`` (a path, or a JSON list of the
    command and its first arguments; the script's tests use it), else gh on
    PATH by its absolute path."""
    env = os.environ.get("DOMOVOI_RELEASE_GH")
    if env:
        return json.loads(env) if env.lstrip().startswith("[") else [env]
    found = find_exe("gh", repo)
    return [found] if found else None


# ─── the run ─────────────────────────────────────────────────────────────

def semver(pep: str) -> str | None:
    """pyproject.toml's version as a tag spells it (the inverse of
    :func:`pep440`): ``1.2.1`` stays, ``1.0.0rc2`` is ``1.0.0-rc2``."""
    m = re.fullmatch(r"([0-9]+\.[0-9]+\.[0-9]+)(?:(a|b|rc)([0-9]+))?", pep or "", re.ASCII)
    if not m:
        return None
    base, pre, n = m.groups()
    return base if not pre else f"{base}-{ {'a': 'alpha', 'b': 'beta', 'rc': 'rc'}[pre] }{n}"


def _default_tag(repo: Path) -> str:
    """``v`` + the checkout's pyproject.toml version: the release number is
    Domovoi's own version, never the Android app's."""
    try:
        version = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    except (OSError, ValueError, KeyError):
        version = None
    sv = semver(version) if isinstance(version, str) else None
    if not sv:
        die("pyproject.toml names no X.Y.Z or X.Y.Z(a|b|rc)N version: pass --tag")
    return f"v{sv}"


def _gradle_app(text: str) -> tuple[str | None, int | None]:
    """(versionName, versionCode) android/app/build.gradle.kts sets, each
    None unless it is set exactly once."""
    names = re.findall(r'^\s*versionName = "([^"]+)"\s*$', text, re.M)
    codes = re.findall(r"^\s*versionCode = ([0-9]+)\s*$", text, re.M)
    return (names[0] if len(names) == 1 else None), (int(codes[0]) if len(codes) == 1 else None)


def _released_base(rel: dict[str, Any]) -> int:
    """The base versionCode (build.gradle.kts's ``versionCode``) a manifest's
    release was built from. Since per-ABI versionCodes (2026-11-14) each
    APK entry names its own code, base*10 + its ABI's digit, and the
    release's ``version_code`` is base*10; before, it was the base itself."""
    code = int(rel.get("version_code") or 0)
    apks = rel.get("apks") or []
    per_abi = bool(apks) and all(isinstance(e, dict) and "version_code" in e for e in apks)
    return code // 10 if per_abi else code


def _manifest_at(t: "Tools", rev: str) -> dict[str, Any]:
    """The ``release`` object of ``android/release.json`` at ``rev`` ({} when
    there is none or it is unreadable)."""
    raw = t.git_bytes("show", f"{rev}:android/release.json")
    try:
        doc = json.loads(raw.decode("utf-8")) if raw is not None else {}
    except ValueError:
        doc = {}
    rel = doc.get("release") if isinstance(doc, dict) else None
    return rel if isinstance(rel, dict) else {}


def _check_origin(t: Tools) -> None:
    """Every fetch and push URL of origin is the GitHub repository (git
    pushes stable and reads tags there; gh writes the release on GitHub)."""
    expected = os.environ.get("DOMOVOI_RELEASE_ORIGIN_URL")
    urls: list[str] = []
    for args in (("remote", "get-url", "--all", "origin"), ("remote", "get-url", "--push", "--all", "origin")):
        p = t.git_run(*args)
        if p.returncode != 0:
            die("this checkout has no remote named origin")
        urls += [u.strip() for u in p.stdout.decode("utf-8", "replace").splitlines() if u.strip()]
    for url in urls:
        ok = url.rstrip("/") == expected.rstrip("/") if expected else bool(ORIGIN_RE.fullmatch(url))
        if not ok:
            die(f"origin is {url}, not {expected or 'github.com/' + REPO_SLUG}: the tag and stable live there, "
                "the release on GitHub")


def _previous_tag(t: Tools, tag_commit: str, prerelease: bool) -> str | None:
    """The tag the notes count from: the nearest earlier tag for a
    release candidate or a pre-release; for a release, the nearest earlier
    one without a suffix (stable's last), else any."""
    patterns = [["--exclude", "v*-*"], []] if not prerelease else [[]]
    if not t.git_ok("rev-parse", "--verify", "--quiet", f"{tag_commit}^"):
        return None
    for extra in patterns:
        out = t.git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", *extra, f"{tag_commit}^", check=False).strip()
        if out:
            return out
    return None


def _remote_ref(t: Tools, ref: str) -> str | None:
    """The commit a ref on origin points at, or None. A tag is peeled: an
    annotated (or signed) tag's ``^{}`` line names its commit."""
    out = t.git("ls-remote", "origin", ref, f"{ref}^{{}}")
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
    allowed = ({e["file"] for e in apks} | {SETUP_ASSET, TRAY_ASSET, SATELLITE_SETUP_ASSET, SUMS_ASSET, *SIGNATURE_ASSETS}
               | {MAC_SETUP_ASSET, *MAC_SIGNATURE_ASSETS}
               | {SERVER_SBOM_ASSET, WEB_SBOM_ASSET} | ({ANDROID_SBOM_ASSET} if apks else set()))
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


def _check_app_release(release: dict[str, Any] | None, app_tag: str, apks: list[dict[str, Any]]) -> None:
    """Path b: the earlier release the manifest names holds its APKs (it
    may hold anything else: that is that release's business)."""
    if release is None:
        die(f"no GitHub release {app_tag}, which the tag's android/release.json names for the app's APKs")
    if release.get("isDraft"):
        die(f"the GitHub release {app_tag}, which holds the app's APKs, is a draft")
    assets = {a["name"]: a for a in release.get("assets") or []}
    for e in apks:
        a = assets.get(e["file"])
        if a is None:
            die(f"the release {app_tag} has no {e['file']}, which android/release.json names there")
        if a.get("size") != e["size"]:
            die(f"{e['file']} on {app_tag} is {a.get('size')} bytes, the manifest says {e['size']}")
        d = _digest(a)
        if d is not None and d != e["sha256"]:
            die(f"{e['file']} on {app_tag} is not the file android/release.json pins (SHA-256 {d})")


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


# ─── the app's own release, app-<versionName> (for F-Droid) ──────────────
#
# Owner decision 2026-11-20: a release that carries the APKs (path a) also
# gets a GitHub release for the app's lightweight tag app-<versionName>, which
# release.ps1|.sh pushed at the commit the APKs were built from. It holds the
# very APKs v<server> holds, byte for byte and under the same names: F-Droid
# fetches releases/download/app-<v>/domovoi-<v>-<abi>.apk. Never Latest (the
# installers and the update check read v<server>, which stays Latest), never a
# pre-release. finish-release never makes or moves the app tag.

#: The app tag's versionName: what a tag name and a file name can hold.
APP_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]*", re.ASCII)


def app_tag_of(rel: dict[str, Any]) -> str:
    """``app-<versionName>``, the tag release.ps1|.sh push at the commit the
    APKs were built from (the manifest's ``commit``)."""
    name = rel.get("version_name")
    if not isinstance(name, str) or not APP_VERSION_RE.fullmatch(name):
        die(f"android/release.json names no usable versionName ({name!r})")
    return f"app-{name}"


def whats_new_path(version_name: str) -> str:
    """The app's "What's new" (Google Play's), at the tag: the app release's notes."""
    return f"android/play/WHATS-NEW-v{version_name}.txt"


def app_release_notes(whats_new: str, *, tag: str) -> str:
    """The app release's notes: the app's WHATS-NEW text, then which Domovoi
    release it belongs to, linked."""
    text = whats_new.replace("\r\n", "\n").strip("\n")
    return (f"{text}\n\nPart of [Domovoi {tag[1:]}]({REPO_URL}/releases/tag/{tag}).\n")


def _check_app_tag_release(release: dict[str, Any] | None, app_tag: str,
                           apks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The app release as it is now (None: not made yet): never a draft or a
    pre-release, nothing but the manifest's APKs, and each one there the
    file the manifest pins (size and GitHub's digest). Returns the APKs it
    still lacks. Nothing is ever replaced: a different file stops the run."""
    if release is None:
        return list(apks)
    if release.get("tagName") not in (None, app_tag):
        die(f"gh release view {app_tag} answered for {release.get('tagName')}")
    if release.get("isDraft"):
        die(f"the GitHub release {app_tag} is a draft: publish it or delete it on GitHub first")
    if release.get("isPrerelease"):
        die(f"the GitHub release {app_tag} is marked a pre-release: the app's release is a full one "
            "(untick it on GitHub, never Latest), then run again")
    assets = {a["name"]: a for a in release.get("assets") or []}
    unknown = sorted(set(assets) - {e["file"] for e in apks})
    if unknown:
        die(f"the release {app_tag} has files that are not the app's APKs: {', '.join(unknown)} "
            "(remove them on GitHub first)")
    missing = []
    for e in apks:
        a = assets.get(e["file"])
        if a is None:
            missing.append(e)
            continue
        if a.get("size") != e["size"]:
            die(f"{e['file']} on {app_tag} is {a.get('size')} bytes, android/release.json says {e['size']}: "
                "not replacing it (delete it on GitHub if it is wrong, then run again)")
        d = _digest(a)
        if d is not None and d != e["sha256"]:
            die(f"{e['file']} on {app_tag} is not the file android/release.json pins (SHA-256 {d}): "
                "not replacing it (delete it on GitHub if it is wrong, then run again)")
    return missing


def _app_tag_on_github(t: Tools, app_tag: str, built_from: str, *, fetched: bool, publish: bool) -> None:
    """The app tag is release.ps1|.sh's: here and on GitHub, at the commit
    the APKs were built from. This script never makes or moves it."""
    here = t.git("rev-parse", "--verify", "--quiet", f"refs/tags/{app_tag}^{{commit}}", check=False).strip()
    there = _remote_ref(t, f"refs/tags/{app_tag}") if fetched else None
    for where, sha in (("here", here), ("on GitHub", there if fetched else built_from)):
        if sha == built_from:
            continue
        why = (f"the app tag {app_tag} {where} is {sha[:12] if sha else 'missing'}, not {built_from[:12]}, the commit "
               "android/release.json says the APKs were built from (android/scripts/release.ps1 pushes it there; "
               "this script never makes or moves a tag of the app)")
        if publish:
            die(why)
        t.say(f"warning: {why}")


def publish_app_release(t: Tools, stage: Path, *, tag: str, app_tag: str, apks: list[dict[str, Any]],
                        notes: str, server_latest: bool) -> None:
    """Make (or complete) the GitHub release ``app_tag`` with the manifest's
    APKs, taken from the release ``tag`` and checked against
    ``android/release.json`` before they go up, then downloaded back by the
    URL F-Droid uses and checked again. Never Latest, never a pre-release;
    when ``tag`` is Latest (``server_latest``) it must still be afterwards."""
    release = _release_json(t, app_tag)
    missing = _check_app_tag_release(release, app_tag, apks)
    if missing:
        fetch = stage / "app"
        if fetch.exists():
            shutil.rmtree(fetch)
        fetch.mkdir(parents=True)
        files = []
        for e in missing:
            dest = fetch / e["file"]
            t.gh("release", "download", tag, "--repo", REPO_SLUG, "--pattern", e["file"], "--output", str(dest))
            if not dest.is_file() or sha256_file(dest) != e["sha256"] or dest.stat().st_size != e["size"]:
                die(f"{e['file']} downloaded from {tag} is not the file android/release.json pins")
            files.append(dest)
        if release is None:
            notes_file = stage / "APP-NOTES.md"
            notes_file.write_bytes(notes.encode("utf-8"))
            t.say(f"creating the GitHub release {app_tag} with {', '.join(f.name for f in files)} "
                  "(the same files as on " + tag + "; not Latest)...")
            t.gh("release", "create", app_tag, *(str(f) for f in files), "--repo", REPO_SLUG, "--verify-tag",
                 "--title", f"Domovoi for Android {app_tag[len('app-'):]}", "--notes-file", str(notes_file),
                 "--latest=false")
        else:
            t.say(f"uploading {', '.join(f.name for f in files)} to {app_tag} (missing there)...")
            # No --clobber: a file of that name there already stops the upload.
            t.gh("release", "upload", app_tag, *(str(f) for f in files), "--repo", REPO_SLUG)
    else:
        t.say(f"{app_tag} already has both APKs: checking them")

    # Read it back: the release as it should be, and each file downloaded
    # by the URL F-Droid fetches, against android/release.json.
    back = _release_json(t, app_tag)
    if back is None:
        die(f"can't read the release {app_tag} back")
    if _check_app_tag_release(back, app_tag, apks):
        die(f"the release {app_tag} still lacks an APK after the upload")
    for e in apks:
        got = t.download_sha256(_asset_url(app_tag, e["file"]))
        if got != e["sha256"]:
            die(f"{e['file']} on {app_tag} is not the file android/release.json pins (downloaded: SHA-256 {got})")
    if server_latest:
        p = t.gh("api", f"repos/{REPO_SLUG}/releases/latest", "--jq", ".tag_name")
        latest = p.stdout.decode("utf-8", "replace").strip()
        if latest != tag:
            die(f"the Latest release on GitHub is {latest or 'none'}, not {tag}: mark {tag} Latest again on GitHub "
                "(installers and the update check read it)")
    t.say(f"{app_tag} holds {', '.join(e['file'] for e in apks)}, the files android/release.json pins"
          + (f"; {tag} is still Latest" if server_latest else ""))


def _stage_sboms(src: Path, stage: Path, *, version: str, app: dict[str, Any], with_apks: bool) -> dict[str, str]:
    """--sbom-dir: the server's and the web's SBOM always, the app's exactly
    when the release carries the APKs, each CycloneDX JSON naming this
    release (the app's: the versionName android/release.json names),
    copied into the stage. Name -> SHA-256."""
    if not src.is_dir():
        die(f"no folder {src} (--sbom-dir)")
    want = sorted([SERVER_SBOM_ASSET, WEB_SBOM_ASSET, *([ANDROID_SBOM_ASSET] if with_apks else [])])
    there = sorted(p.name for p in src.iterdir() if p.is_file())
    if want != there:
        die(f"--sbom-dir holds {', '.join(there) or 'nothing'}; this release takes exactly {', '.join(want)}"
            + ("" if with_apks else " (it carries no APKs, so no app SBOM)"))
    out = {}
    for name in [n for n in SBOM_ASSETS if n in want]:
        data = (src / name).read_bytes()
        try:
            doc = json.loads(data.decode("utf-8"))
        except ValueError:
            die(f"{name} is not JSON")
        if not isinstance(doc, dict) or doc.get("bomFormat") != "CycloneDX":
            die(f"{name} is not a CycloneDX SBOM")
        named = ((doc.get("metadata") or {}).get("component") or {}).get("version")
        expect = str(app.get("version_name")) if name == ANDROID_SBOM_ASSET else version
        if named != expect:
            die(f"{name} describes version {named!r}, not {expect!r}: an SBOM from another release?")
        (stage / name).write_bytes(data)
        out[name] = sha256_bytes(data)
    return out


def _check_pinned_worktree(t: Tools, src: Path, tag_commit: str, provision: bytes) -> None:
    """After ``build.ps1 -Pin``: the worktree differs from the tag in
    pins.iss only, and those pins install the tag's commit, with its own
    provision.sh fetched (its SHA-256, nothing bundled)."""
    status = t.git("-C", str(src), "status", "--porcelain", "--untracked-files=all")
    changed = sorted(line[3:] for line in status.splitlines() if line.strip())
    if changed != [PINS]:
        die(f"building the Setup left the tag's files changed beyond {PINS}: {', '.join(changed) or 'nothing pinned'}")
    values, flags = read_pins((src / PINS).read_text(encoding="utf-8"))
    if values.get("DomovoiCommit") != tag_commit:
        die(f"{PINS} installs {values.get('DomovoiCommit') or 'nothing'}, not the tag's commit {tag_commit}")
    if "ProvisionBundled" in flags:
        die(f"{PINS} bundles a provision.sh: the Setup must fetch the tag's own (build.ps1 -Pin turns bundling off)")
    if values.get("ProvisionSha256") != sha256_bytes(provision):
        die(f"{PINS}'s ProvisionSha256 is not the SHA-256 of the tag's provision.sh ({sha256_bytes(provision)})")


def _build_setup(src: Path, tag: str, repo: Path) -> Path:
    """DomovoiSetup.exe, by the installer's own script in the tag's
    worktree: ``windows/build.ps1 -Pin <tag>``."""
    script = src / "windows" / "build.ps1"
    command = f"powershell -NoProfile -ExecutionPolicy Bypass -File windows\\build.ps1 -Pin {tag}"
    if "[string]$Pin" not in script.read_text(encoding="utf-8"):
        die(f"the tag's windows/build.ps1 has no -Pin, so the Setup can't be pinned to the tag and built from it. "
            f"Build it by hand in a clean worktree of the tag ({command}) and pass it with --setup")
    if os.name != "nt":
        die(f"the Windows Setup builds on Windows only: run finish-release.ps1 there, or build it in a clean "
            f"worktree of the tag ({command}) and pass it with --setup")
    shell = find_exe("powershell.exe", repo)
    if not shell:
        die("no Windows PowerShell to run windows/build.ps1")
    print(f"finish-release: building the Windows Setup from the tag ({command})...", flush=True)
    p = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Pin", tag], cwd=src)
    if p.returncode != 0:
        die("windows/build.ps1 -Pin failed")
    exe = src / "windows" / "build" / SETUP_ASSET
    if not exe.is_file():
        die(f"windows/build.ps1 left no {exe}")
    return exe


def setup_manifest(tag: str, version: str, tag_commit: str, setup: Path, *,
                   kind: str = MANIFEST_KIND, asset: str = SETUP_ASSET) -> bytes:
    """``DomovoiSetup.manifest.json``: what the tray checks a downloaded
    Setup against (windows/common/ReleaseSignature.cs SetupManifest), its
    version and commit signed along with it. The Mac package's
    (``kind``/``asset``: the Mac's) has the same fields."""
    doc = {"kind": kind, "schema": 1, "tag": tag, "version": version, "commit": tag_commit,
           "file": asset, "size": setup.stat().st_size, "sha256": sha256_file(setup)}
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("ascii")


def check_mac_build_record(record: Path, pkg: Path, *, tag: str, tag_commit: str,
                           built_on: str | None = None) -> None:
    """The Mac build's record (``DomovoiSetup-mac.build.json``, written
    beside the package by the Mac build) names this tag and its commit, and
    the package it describes is this file (size and SHA-256). The package
    can't be built here, so this is what ties it to the tag.

    ``built_on``: an app release's tag is a commit the signing job makes
    (``android/release.json`` on the dispatched commit; the caller has
    checked it changes that file alone), so the release workflow builds the
    Mac package from the dispatched commit, while it waits for approval.
    That commit is accepted too, but only from a build that installs the
    tag itself (its ``ref`` is the tag): the Mac side's files are the same
    in both commits, and the Linux side it installs is the tag's."""
    try:
        doc = json.loads(record.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        die(f"no readable Mac build record at {record} (--mac-build-record; the Mac build writes "
            f"{MAC_BUILD_RECORD} beside the package)")
    if not isinstance(doc, dict) or doc.get("kind") != MAC_BUILD_KIND or doc.get("schema") != 1:
        die(f"{record} is not a Mac build record (kind {MAC_BUILD_KIND}, schema 1)")
    commit = doc.get("commit")
    on_parent = built_on is not None and commit == built_on and commit != tag_commit
    if doc.get("tag") != tag or (commit != tag_commit and not on_parent):
        also = f" or {built_on[:12]} (the commit the tag's android/release.json commit is on)" if built_on else ""
        die(f"the Mac package was built from {doc.get('tag') or 'no tag'} at {str(commit or 'no commit')[:12]}, "
            f"not {tag} at {tag_commit[:12]}{also}: build it from this tag")
    if on_parent and doc.get("ref") != tag:
        die(f"the Mac package was built from {commit[:12]}, the commit under {tag}, but installs "
            f"{doc.get('ref') or 'that commit'}, not {tag}: build it with --ref {tag}")
    if doc.get("size") != pkg.stat().st_size or doc.get("sha256") != sha256_file(pkg):
        die(f"{record} describes another package than {pkg} (size or SHA-256 differ)")


def sign_setup(stage: Path, setup: Path, *, tag: str, version: str, tag_commit: str, key: Path | None,
               public: bytes, repo: Path, ssh_keygen: str | None = None, mac: bool = False) -> dict[str, str]:
    """The Windows update's three files beside the staged Setup (``mac``:
    the Mac update's, beside the staged package, under the Mac's names and
    namespaces): the manifest, and both signatures by the release key's private half
    ``key`` (``ssh-keygen -Y sign``, which reads it and asks for its
    passphrase itself), each checked against the tag's pinned public key
    (``ssh-keygen -Y verify``). Their SHA-256, by name.

    ``key`` None (``--signing-agent``, the release workflow): the private
    half is held by the ssh-agent ``SSH_AUTH_SOCK`` names, and ssh-keygen is
    handed the tag's own public key, so it signs with the agent's matching
    key (and fails if the agent holds none) and never prompts: no key file
    and no passphrase are on disk or asked for."""
    keygen = ssh_keygen or find_exe("ssh-keygen", repo)
    if not keygen:
        die("no ssh-keygen on PATH (OpenSSH's; Windows has it in C:\\Windows\\System32\\OpenSSH, Git Bash has its own)")
    if key is None:
        if not os.environ.get("SSH_AUTH_SOCK"):
            die("--signing-agent: no SSH_AUTH_SOCK, so no ssh-agent to sign with (start one and ssh-add the release key)")
    else:
        key = Path(key).expanduser().resolve()
        if not key.is_file():
            die(f"no release key at {key} (--signing-key)")
        if key.is_relative_to(Path(repo).resolve()):
            die("the release key is inside the checkout: keep it outside (and backed up)")
    line = public.decode("ascii", "replace").strip().splitlines()[0] if public.strip() else ""
    parts = line.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        die(f"{RELEASE_KEY} at the tag is not one ssh-ed25519 public key line")
    names = (MANIFEST_ASSET, SETUP_NAMESPACE, MANIFEST_NAMESPACE, MANIFEST_KIND, SETUP_ASSET, SIGNATURE_ASSETS)
    if mac:
        names = (MAC_MANIFEST_ASSET, MAC_SETUP_NAMESPACE, MAC_MANIFEST_NAMESPACE, MAC_MANIFEST_KIND, MAC_SETUP_ASSET,
                 MAC_SIGNATURE_ASSETS)
    manifest_name, file_ns, manifest_ns, kind, asset, sig_assets = names
    manifest = stage / manifest_name
    manifest.write_bytes(setup_manifest(tag, version, tag_commit, setup, kind=kind, asset=asset))
    allowed = stage / ".allowed_signers"
    # Bytes, so Windows doesn't turn the newline into CRLF.
    allowed.write_bytes(f'{SIGNER} namespaces="{file_ns},{manifest_ns}" {parts[0]} {parts[1]}\n'
                        .encode("ascii"))
    # With the agent: the tag's public key names which of its keys signs.
    agent_pub = stage / ".release-signing.pub"
    if key is None:
        agent_pub.write_bytes(f"{parts[0]} {parts[1]}\n".encode("ascii"))
    sign_with = str(agent_pub) if key is None else str(key)
    how = "the key in ssh-agent" if key is None else str(key)
    try:
        for file, ns in ((setup, file_ns), (manifest, manifest_ns)):
            sig = file.with_name(file.name + ".sig")
            sig.unlink(missing_ok=True)
            print(f"finish-release: signing {file.name} ({ns}) with {how}...", flush=True)
            # The agent path never reads a terminal: nothing to prompt for.
            p = subprocess.run([keygen, "-Y", "sign", "-f", sign_with, "-n", ns, str(file)],
                               stdin=subprocess.DEVNULL if key is None else None)
            if p.returncode != 0 or not sig.is_file():
                die(f"ssh-keygen -Y sign failed for {file.name}"
                    + (" (does the agent hold the private half of the tag's release key?)" if key is None else ""))
            with open(file, "rb") as data:
                v = subprocess.run([keygen, "-Y", "verify", "-f", str(allowed), "-I", SIGNER, "-n", ns, "-s", str(sig)],
                                   stdin=data, capture_output=True)
            if v.returncode != 0:
                die(f"{sig.name} doesn't verify against the tag's {RELEASE_KEY}: is --signing-key that key's "
                    "private half?")
    finally:
        allowed.unlink(missing_ok=True)
        agent_pub.unlink(missing_ok=True)
    return {name: sha256_file(stage / name) for name in sig_assets}


def _pyproject_version_at(t: Tools, rev: str) -> str | None:
    raw = t.git_bytes("show", f"{rev}:pyproject.toml")
    try:
        version = tomllib.loads(raw.decode("utf-8"))["project"]["version"] if raw is not None else None
    except (ValueError, KeyError):
        return None
    return version if isinstance(version, str) else None


def plan(t: Tools, repo: Path, tag_arg: str | None = None) -> dict[str, Any]:
    """What the release workflow (.github/workflows/release.yml) runs, from
    the commit it was dispatched on (HEAD), before anyone approves it.
    Changes nothing (it fetches). One of:

    - **A new release** (no tag ``v<pyproject version>`` on GitHub): HEAD is
      ``origin/main``'s tip and a release commit (it raises pyproject's
      version and leaves ``android/release.json`` alone). The app changed
      when its versionName or versionCode differ from the ones
      ``android/release.json`` names: ``sign_app`` (release.ps1 first, then
      finish-release); otherwise finish-release alone (it tags HEAD). Its
      ``docs/release/v<version>.md``, if it has one, must be in the house
      style (:func:`release_doc_problems`).
    - **A run to finish** (the tag is on GitHub already, from a run that
      stopped): the tag is HEAD (path b's tag, or path a's release commit
      dispatched again), or HEAD plus ``android/release.json`` alone (path
      a's release commit, made by an earlier attempt of this same run):
      finish-release at the tag; nothing is signed or tagged again.

    Anything else is refused. The finish-release run itself checks the rest
    (the app's release on GitHub, stable, the Setup)."""
    _check_origin(t)
    if t.git("status", "--porcelain").strip():
        die("the checkout has uncommitted changes")
    head = t.git("rev-parse", "HEAD").strip()
    tag = tag_arg or _default_tag(repo)
    if not TAG_RE.fullmatch(tag) or pep440(tag[1:]) is None:
        die(f"{tag!r} is not v<X.Y.Z> or v<X.Y.Z>-(alpha|beta|rc)N")
    if _pyproject_version_at(t, head) != pep440(tag[1:]):
        die(f"HEAD's pyproject.toml says {_pyproject_version_at(t, head) or 'no version'}, not {tag}'s")
    if not t.git_ok("fetch", "--quiet", "--tags", "origin", "+refs/heads/main:refs/remotes/origin/main"):
        die("git fetch origin failed")
    main = t.git("rev-parse", "--verify", "refs/remotes/origin/main").strip()
    on_github = _remote_ref(t, f"refs/tags/{tag}")
    doc: dict[str, Any] = {"tag": tag, "version": tag[1:], "head": head}

    if on_github is None:
        if head != main:
            die(f"HEAD {head[:12]} is not origin/main's tip ({main[:12]}): main moved after the run was started, "
                "or it was started elsewhere. Release main's tip: run the workflow again")
        if t.git_ok("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"):
            die(f"a tag {tag} here that GitHub doesn't have")
        parent = t.git("rev-parse", "--verify", "--quiet", f"{head}^", check=False).strip()
        if not parent or _pyproject_version_at(t, parent) == _pyproject_version_at(t, head):
            die(f"HEAD {head[:12]} is not a release commit: it doesn't raise pyproject.toml's version "
                f"(main's tip must be the release commit, docs/CONTRIBUTING.md, Releasing)")
        changed = t.git("diff", "--name-only", parent, head).split()
        if "android/release.json" in changed:
            die("the release commit changes android/release.json: only android/scripts/release.ps1 writes it")
        rel = _manifest_at(t, head)
        if not rel:
            die("HEAD's android/release.json names no release: the first app release is made by hand "
                "(android/scripts/release.ps1)")
        if rel.get("tag") == tag:
            die(f"HEAD's android/release.json names {tag} already, but GitHub has no tag {tag}")
        gradle = t.git_bytes("show", f"{head}:android/app/build.gradle.kts")
        name, code = _gradle_app(gradle.decode("utf-8") if gradle is not None else "")
        if name is None or code is None:
            die("android/app/build.gradle.kts doesn't set versionName and versionCode exactly once each")
        released = _released_base(rel)
        app_changed = (name, code) != (rel.get("version_name"), released)
        if app_changed and not code > released:
            die(f"the app's versionCode {code} is not above the last release's ({released}): "
                "raise it with versionName, or put both back")
        if app_changed:
            # The app's own release (app-<versionName>, for F-Droid) takes its
            # notes from this file: refused here, before anyone approves.
            app_tag_of({"version_name": name})
            if t.git_bytes("show", f"{head}:{whats_new_path(name)}") is None:
                die(f"the app changed, but HEAD has no {whats_new_path(name)}: the notes of its release app-{name} "
                    "(and Google Play's What's new)")
        hand =t.git_bytes("show", f"{head}:docs/release/{tag}.md")
        problems = release_doc_problems(hand.decode("utf-8"), tag[1:]) if hand is not None else []
        if highlights_required(tag[1:]):
            # Houses show a release's three Highlights before they update: a
            # release without approved ones stops here, before anything is built.
            if hand is None:
                problems.append(f"from {'.'.join(map(str, HIGHLIGHTS_FROM))} on a release needs docs/release/{tag}.md "
                                f"with its three \"## {HIGHLIGHTS}\"")
            else:
                notes = (t.git_bytes("show", f"{head}:docs/RELEASE_NOTES.md") or b"").decode("utf-8")
                problems += highlights_match_problems(hand.decode("utf-8"), notes, tag[1:])
        if problems:
            die(f"docs/release/{tag}.md: " + "; ".join(problems)
                + " (docs/CONTRIBUTING.md, \"The release page\", has the template)")
        doc.update(path="app" if app_changed else "server", sign_app=app_changed, finish_at=head)
        if app_changed:
            doc["app"] = {"version_name": name, "version_code": code}
        return doc

    if on_github == head:
        app_changed = _manifest_at(t, head).get("tag") == tag
        doc.update(path="app" if app_changed else "server", sign_app=False, finish_at=head)
        return doc
    parent = t.git("rev-parse", "--verify", "--quiet", f"{on_github}^", check=False).strip()
    if (parent == head and t.git("diff", "--name-only", head, on_github).split() == ["android/release.json"]
            and _manifest_at(t, on_github).get("tag") == tag):
        doc.update(path="app", sign_app=False, finish_at=on_github)
        return doc
    die(f"the tag {tag} on GitHub is {on_github[:12]}, which is neither HEAD {head[:12]} nor its "
        "android/release.json commit: nothing for this run to make or finish")


def run(argv: list[str], *, tools: Tools | None = None, repo: Path | None = None,
        build_setup: Callable[[Path, str, Path], Path] | None = None) -> int:
    """The release (see the module notes). A tag made here for path b is
    deleted again unless it was pushed: a dry run, or a run that stopped
    first, leaves no tag behind to be taken for a release later."""
    made: dict[str, Any] = {}
    try:
        return _run(argv, tools=tools, repo=repo, build_setup=build_setup, made=made)
    finally:
        if made.get("tag") and not made.get("pushed"):
            t, tag, sha = made["tools"], made["tag"], made["commit"]
            now = t.git_run("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
            if now.returncode == 0 and now.stdout.decode("ascii", "replace").strip() == sha:
                t.git_run("tag", "-d", tag)
                t.say(f"deleted the tag {tag} made here (it was not pushed)")


def _run(argv: list[str], *, tools: Tools | None, repo: Path | None,
         build_setup: Callable[[Path, str, Path], Path] | None, made: dict[str, Any]) -> int:
    ap = argparse.ArgumentParser(prog="finish-release", description=(
        "Finish a Domovoi release (after android/scripts/release when the app changed; alone when it didn't): "
        "build the Windows Setup from the tag and attach it, write one SHA256SUMS, set the notes, then (a release "
        "without a suffix) fast-forward stable."))
    ap.add_argument("--tag", help="the release, v<version> (default: v + pyproject.toml's version)")
    ap.add_argument("--prerelease", action="store_true",
                    help="a release this script creates (the app unchanged): mark it a pre-release on GitHub")
    ap.add_argument("--no-publish", action="store_true", help="check, build and write everything; change nothing on GitHub")
    ap.add_argument("--setup", help="a DomovoiSetup.exe built by windows/build.ps1 -Pin <tag> from the tag, instead of building it")
    ap.add_argument("--tray", help="with --setup: the DomovoiTray.exe of that build (default: bin/DomovoiTray.exe beside the Setup)")
    ap.add_argument("--satellite-setup", help="with --setup: the DomovoiSatelliteSetup.exe of that build "
                    "(default: the one beside the Setup)")
    ap.add_argument("--mac-pkg", help="a DomovoiSetup-mac.pkg built on a Mac from the tag, to attach (default: none, "
                    "and the release has no Mac installer)")
    ap.add_argument("--mac-build-record", help=f"with --mac-pkg: its build record (default: {MAC_BUILD_RECORD} beside it)")
    ap.add_argument("--since", help="the tag the notes' summary counts from (default: the previous one)")
    ap.add_argument("--signing-key", help="the private half of Domovoi's release key (an OpenSSH ed25519 key file, "
                    "outside the checkout; default DOMOVOI_RELEASE_SIGNING_KEY), for the Windows update's signatures")
    ap.add_argument("--signing-agent", action="store_true",
                    help="sign with the release key an ssh-agent holds (SSH_AUTH_SOCK) instead of a key file: "
                    "the release workflow's way, which never prompts")
    ap.add_argument("--sbom-dir", metavar="DIR",
                    help="attach the SBOMs in DIR (scripts/release/sbom.py): the server's and the web's, and the "
                    "app's exactly when this release carries the APKs")
    ap.add_argument("--provenance", action="store_true",
                    help="the release workflow attests every file after this run: say how to check it in the notes")
    ap.add_argument("--plan", metavar="FILE",
                    help="the release workflow's gate: check that HEAD is a release to make (or one to finish), "
                    "write what to run as JSON to FILE, and change nothing")
    args = ap.parse_args(argv)

    repo = Path(repo or REPO)
    say = (tools.say if tools else (lambda m: print(f"finish-release: {m}", flush=True)))
    t = tools or Tools(repo, default_gh(repo), say)
    if args.plan:
        doc = plan(t, repo, args.tag)
        Path(args.plan).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        say(f"plan: {doc['tag']}, " + ("the app changed: sign and publish it, then finish"
                                      if doc["sign_app"] else
                                      "finish (" + ("the app's release is published" if doc["path"] == "app"
                                                    else "server and Windows only") + ")")
            + f", at {doc['finish_at'][:12]}")
        return 0
    if args.signing_agent and args.signing_key:
        die("--signing-agent or --signing-key, not both")
    publish = not args.no_publish

    # 1. The checks. --------------------------------------------------------
    _check_origin(t)
    if t.git("status", "--porcelain").strip():
        die("the checkout has uncommitted changes: commit or stash them first")
    head = t.git("rev-parse", "HEAD").strip()
    tag = args.tag or _default_tag(repo)
    m = TAG_RE.fullmatch(tag)
    if not m:
        die(f"{tag!r} is not v<X.Y.Z> or v<X.Y.Z>-<suffix>")
    version = tag[1:]
    by_name = bool(m.group(2))
    want_py = pep440(version)
    if want_py is None:
        die(f"{tag}: a suffix must be -alphaN, -betaN or -rcN (what pyproject.toml can say)")

    fetch = t.git_run("fetch", "--tags", "origin")
    fetched = fetch.returncode == 0
    if not fetched:
        err = fetch.stderr.decode("utf-8", "replace").strip()
        if "would clobber" in err or "rejected" in err:
            die(f"git fetch --tags would move a tag here (a published tag never moves): {err.splitlines()[-1]}")
        if publish:
            die("git fetch origin failed")
        say("warning: git fetch origin failed; checking against what this checkout has")
    # No tag yet: only a release that doesn't change the app (path b) is
    # tagged here, at HEAD; path a's tag is release.ps1's.
    make_tag = not t.git_ok("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")
    if make_tag:
        if _manifest_at(t, head).get("tag") == tag:
            die(f"no tag {tag}: HEAD's android/release.json names it, so android/scripts/release.ps1 publishes the "
                "release and makes the tag")
        tag_commit = head
    else:
        tag_commit = t.git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").strip()
    rel = _manifest_at(t, tag_commit)
    if not rel:
        die("the tag's android/release.json names no release: android/scripts/release.ps1 makes the first one")
    app_changed = rel.get("tag") == tag   # path a; otherwise path b

    on_github = _remote_ref(t, f"refs/tags/{tag}") if fetched else None
    if fetched and on_github != tag_commit:
        if on_github is not None:
            die(f"the tag {tag} here is {tag_commit[:12]}, on GitHub {on_github} (a published tag never moves)")
        if app_changed and publish:
            die(f"the tag {tag} here is {tag_commit[:12]}, on GitHub missing")
        if app_changed:
            say(f"warning: the tag {tag} is not on GitHub")
    push_tag = not app_changed and on_github is None
    if push_tag:
        # The tag goes on GitHub at a commit GitHub has, on main's history
        # (windows/build.ps1 -Pin refuses anything else, too).
        on_main = fetched and t.git_ok("merge-base", "--is-ancestor", tag_commit, "refs/remotes/origin/main")
        if not on_main:
            if publish:
                die(f"{tag_commit[:12]} is not on origin/main: push the release commit to main first, then run again")
            say(f"warning: {tag_commit[:12]} is not on origin/main (a real run refuses)")
        if not make_tag:
            say(f"the tag {tag} is here only, at {tag_commit[:12]}: a real run pushes it")
    if not t.git_ok("merge-base", "--is-ancestor", tag_commit, head):
        die(f"HEAD {head[:12]} does not come after the tag {tag} ({tag_commit[:12]}): git pull, or check out the tag")

    raw = t.git_bytes("show", f"{tag_commit}:pyproject.toml")
    try:
        py_version = tomllib.loads(raw.decode("utf-8"))["project"]["version"] if raw is not None else None
    except (ValueError, KeyError):
        py_version = None
    if py_version != want_py:
        die(f"the tag's pyproject.toml says {py_version or 'no version'}, not {want_py} ({tag})")

    apks = list(rel.get("apks") or [])
    cert = str(rel.get("cert_sha256") or "")
    if not apks or not cert:
        die("the tag's android/release.json names no APKs or no certificate")
    app_from = None
    app_release = None
    if app_changed:
        parent = t.git("rev-parse", "--verify", "--quiet", f"{tag_commit}^", check=False).strip()
        if not parent or rel.get("commit") != parent:
            die(f"the tag's commit is not android/release.json on top of the commit its APKs were built from: "
                f"its parent is {parent[:12] or 'none'}, the manifest names {str(rel.get('commit'))[:12]}")
        changed = t.git("diff", "--name-only", parent, tag_commit).split()
        if changed != ["android/release.json"]:
            die(f"the tag's commit changes more than android/release.json: {', '.join(changed)}")
        # The app's own release, app-<versionName> (for F-Droid): its tag
        # where release.ps1 put it, its notes at the tag, and what GitHub has
        # of it already, all checked before anything changes.
        own_app_tag = app_tag_of(rel)
        whats_new = t.git_bytes("show", f"{tag_commit}:{whats_new_path(rel['version_name'])}")
        if whats_new is None:
            die(f"the tag has no {whats_new_path(rel['version_name'])}: the notes of the app's release {own_app_tag}")
        own_app_notes = app_release_notes(whats_new.decode("utf-8"), tag=tag)
        _app_tag_on_github(t, own_app_tag, parent, fetched=fetched, publish=publish)
        if publish or t.gh_cmd:
            _check_app_tag_release(_release_json(t, own_app_tag), own_app_tag, apks)
    else:
        # Path b: the app is the one an earlier release holds, unchanged.
        app_tag = rel.get("tag")
        if not isinstance(app_tag, str) or not TAG_RE.fullmatch(app_tag):
            die(f"the tag's android/release.json names no release tag ({app_tag!r})")
        if not t.git_ok("rev-parse", "--verify", "--quiet", f"refs/tags/{app_tag}"):
            die(f"the tag's android/release.json names {app_tag}, which is no tag here (git fetch --tags)")
        app_commit = t.git("rev-parse", "--verify", f"refs/tags/{app_tag}^{{commit}}").strip()
        if app_commit == tag_commit or not t.git_ok("merge-base", "--is-ancestor", app_commit, tag_commit):
            die(f"{app_tag}, which holds the app's APKs, is not a release before {tag}")
        if fetched:
            app_on_github = _remote_ref(t, f"refs/tags/{app_tag}")
            if app_on_github != app_commit:
                die(f"the tag {app_tag} here is {app_commit[:12]}, on GitHub {app_on_github or 'missing'}")
        if t.git_bytes("show", f"{tag_commit}:android/release.json") != t.git_bytes(
                "show", f"{app_commit}:android/release.json"):
            die(f"android/release.json at {tag} is not the one {app_tag} released: a release that changes the app "
                "is made by android/scripts/release.ps1 (path a)")
        gradle = t.git_bytes("show", f"{tag_commit}:android/app/build.gradle.kts")
        name, code = _gradle_app(gradle.decode("utf-8") if gradle is not None else "")
        if (name, code) != (rel.get("version_name"), _released_base(rel)):
            die(f"the app at {tag} is {name or '?'} ({code or '?'}), but android/release.json hands phones "
                f"{rel.get('version_name')} ({_released_base(rel)}) from {app_tag}: the app changed, so release it "
                "first with android/scripts/release.ps1 (path a), or put its versionName and versionCode back")
        app_from = {"version_name": rel.get("version_name"), "version_code": rel.get("version_code"), "tag": app_tag,
                    "apks": [{"abi": e["abi"], "file": e["file"]} for e in apks]}
        app_release = _release_json(t, app_tag) if (publish or t.gh_cmd) else None
        if app_release is None and not publish:
            say(f"warning: can't read the GitHub release {app_tag} (or no gh): its APKs go unchecked")
        else:
            _check_app_release(app_release, app_tag, apks)
        say(f"the app is unchanged: {app_from['version_name']} ({app_from['version_code']}), its APKs on {app_tag}; "
            f"{tag} gets no APKs")
        apks = []
    tag_provision = t.git_bytes("show", f"{tag_commit}:scripts/linux/provision.sh")
    if tag_provision is None:
        die(f"the tag {tag} has no scripts/linux/provision.sh")

    release = _release_json(t, tag) if (publish or t.gh_cmd) else None
    create_release = False
    if release is None:
        if app_changed and publish:
            die(f"no GitHub release {tag} (or gh can't read it): android/scripts/release.ps1 publishes it first")
        if app_changed:
            say(f"warning: no GitHub release {tag} to check (or no gh): the run goes on with the manifest's files")
        else:
            create_release = True
    else:
        if release.get("isDraft"):
            die(f"the GitHub release {tag} is a draft: publish it first")
        _check_published_apks(release, apks)
    # Every release is a full one unless GitHub already marks it a pre-release
    # (release.ps1 -Prerelease, or --prerelease here when this script creates
    # it); only a version without a suffix that is not one moves stable.
    if release is not None:
        prerelease = bool(release.get("isPrerelease"))
        if args.prerelease and not prerelease:
            die(f"--prerelease: the GitHub release {tag} exists already, as a full release")
    else:
        prerelease = bool(args.prerelease)
    moves_stable = not by_name and not prerelease

    stable = ("skip", None)
    if moves_stable:
        stable = _stable_plan(t, tag_commit) if fetched else ("unknown", None)
        if stable[0] == "refused":
            die(f"stable on GitHub is {stable[1]}, which the tag's commit doesn't come after: refusing "
                "(stable only ever fast-forwards to a release; it is never rewound or forced)")

    # Path b: the tag, here only for now (windows/build.ps1 -Pin pins to a
    # tag); pushed in step 5, deleted again if it isn't.
    if make_tag:
        t.git("tag", tag, tag_commit)
        made.update(tag=tag, commit=tag_commit, tools=t)
        say(f"tagged {tag_commit[:12]} {tag} here (the app unchanged, so this script makes the tag)")

    # 2. The Setup, from the tag's own files. ----------------------------------
    stage = repo / "windows" / "build" / "release" / tag
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    staged_setup = stage / SETUP_ASSET
    staged_tray = stage / TRAY_ASSET
    staged_sat = stage / SATELLITE_SETUP_ASSET
    # The Mac package, handed in (it builds on a Mac only), tied to the tag
    # by its build record. Taken first, before the long Windows Setup build,
    # so a wrong package stops the run in seconds; copied into the staging
    # folder, and the record checked against that copy (the very file that
    # gets signed and attached, not the one it was copied from).
    staged_mac = stage / MAC_SETUP_ASSET
    if args.mac_build_record and not args.mac_pkg:
        die("--mac-build-record goes with --mac-pkg")
    if args.mac_pkg:
        mac_pkg = Path(args.mac_pkg).resolve()
        if not mac_pkg.is_file():
            die(f"no Mac package at {args.mac_pkg}")
        record = Path(args.mac_build_record).resolve() if args.mac_build_record else mac_pkg.parent / MAC_BUILD_RECORD
        shutil.copyfile(mac_pkg, staged_mac)
        built_on = t.git("rev-parse", "--verify", "--quiet", f"{tag_commit}^", check=False).strip() if app_changed else None
        check_mac_build_record(record, staged_mac, tag=tag, tag_commit=tag_commit, built_on=built_on or None)
        say(f"using {mac_pkg} (--mac-pkg: its build record names {tag}, built from the tag's files)")

    if args.tray and not args.setup:
        die("--tray goes with --setup")
    if args.satellite_setup and not args.setup:
        die("--satellite-setup goes with --setup")
    if args.setup:
        setup = Path(args.setup).resolve()
        if not setup.is_file():
            die(f"no Setup at {args.setup}")
        say(f"using {setup} (--setup: built by windows/build.ps1 -Pin {tag} from the tag, it is taken to be)")
        shutil.copyfile(setup, staged_setup)
        tray = Path(args.tray).resolve() if args.tray else setup.parent / "bin" / TRAY_ASSET
        if args.tray and not tray.is_file():
            die(f"no tray at {args.tray}")
        if tray.is_file():
            say(f"using {tray} (the tray of that build)")
            shutil.copyfile(tray, staged_tray)
        sat = Path(args.satellite_setup).resolve() if args.satellite_setup else setup.parent / SATELLITE_SETUP_ASSET
        if args.satellite_setup and not sat.is_file():
            die(f"no satellite Setup at {args.satellite_setup}")
        if sat.is_file():
            say(f"using {sat} (the satellite Setup of that build)")
            shutil.copyfile(sat, staged_sat)
    else:
        src = repo / "windows" / "build" / "release" / f"{tag}-source"
        if src.exists():
            t.git_run("worktree", "remove", "--force", str(src))
            shutil.rmtree(src, ignore_errors=True)
        t.git_run("worktree", "prune")
        t.git("worktree", "add", "--quiet", "--detach", str(src), tag_commit)
        try:
            exe = (build_setup or _build_setup)(src, tag, repo)
            _check_pinned_worktree(t, src, tag_commit, tag_provision)
            shutil.copyfile(exe, staged_setup)
            shutil.copyfile(src / PINS, stage / "pins.iss")
            # The tray the same build made (a tag from before it has none).
            built_tray = exe.parent / "bin" / TRAY_ASSET
            if built_tray.is_file():
                shutil.copyfile(built_tray, staged_tray)
            # And its satellite Setup, beside it (a tag from before it has none).
            built_sat = exe.parent / SATELLITE_SETUP_ASSET
            if built_sat.is_file():
                shutil.copyfile(built_sat, staged_sat)
        finally:
            if not t.git_ok("worktree", "remove", "--force", str(src)):
                say(f"warning: couldn't remove the build worktree {src}: git worktree remove --force it later")

    # The Windows update's signatures, when the tag pins the release key.
    release_key = t.git_bytes("show", f"{tag_commit}:{RELEASE_KEY}")
    signing_key = None if args.signing_agent else (args.signing_key or os.environ.get("DOMOVOI_RELEASE_SIGNING_KEY") or None)
    signatures = None
    mac_signatures = None
    if args.signing_agent:
        if release_key is None:
            die(f"--signing-agent given, but the tag pins no release key ({RELEASE_KEY}): its Windows side couldn't "
                "check the signatures anyway")
        signatures = sign_setup(stage, staged_setup, tag=tag, version=version, tag_commit=tag_commit,
                                key=None, public=release_key, repo=repo)
        say(f"signed {SETUP_ASSET} and its manifest with the agent's key; both check out against the tag's {RELEASE_KEY}")
        if staged_mac.is_file():
            mac_signatures = sign_setup(stage, staged_mac, tag=tag, version=version, tag_commit=tag_commit,
                                        key=None, public=release_key, repo=repo, mac=True)
            say(f"signed {MAC_SETUP_ASSET} and its manifest ({MAC_SETUP_NAMESPACE}) with the agent's key; both check out too")
    elif release_key is not None and not signing_key and not publish:
        say(f"warning: no signing key, so the Windows update's signatures are left out of this dry run "
            f"(the tag pins {RELEASE_KEY}; a real run needs --signing-key)")
    elif release_key is not None:
        if not signing_key:
            die(f"the tag pins Domovoi's release key ({RELEASE_KEY}), so its Setup must be signed: pass --signing-key "
                "with the path of the key's private half (or set DOMOVOI_RELEASE_SIGNING_KEY); docs/CONTRIBUTING.md, "
                "Releasing, says how it was made")
        signatures = sign_setup(stage, staged_setup, tag=tag, version=version, tag_commit=tag_commit,
                                key=Path(signing_key), public=release_key, repo=repo)
        say(f"signed {SETUP_ASSET} and its manifest; both check out against the tag's {RELEASE_KEY}")
        if staged_mac.is_file():
            mac_signatures = sign_setup(stage, staged_mac, tag=tag, version=version, tag_commit=tag_commit,
                                        key=Path(signing_key), public=release_key, repo=repo, mac=True)
            say(f"signed {MAC_SETUP_ASSET} and its manifest ({MAC_SETUP_NAMESPACE}); both check out too")
    elif signing_key:
        die(f"--signing-key given, but the tag pins no release key ({RELEASE_KEY}): its Windows side couldn't "
            "check the signatures anyway")
    else:
        say(f"no Windows update signatures: the tag pins no release key ({RELEASE_KEY}), so its Windows side has "
            "no update step")

    # The SBOMs the release workflow made (--sbom-dir), checked to be this release's.
    sboms = (_stage_sboms(Path(args.sbom_dir), stage, version=version, app=rel, with_apks=bool(apks))
             if args.sbom_dir else None)
    if sboms:
        say(f"the SBOMs: {', '.join(sboms)}")

    # 3 and 4. SHA256SUMS and the notes. --------------------------------------
    if args.since and not t.git_ok("rev-parse", "--verify", "--quiet", f"refs/tags/{args.since}"):
        die(f"no tag {args.since} (--since)")
    since = args.since or _previous_tag(t, tag_commit, not moves_stable)
    hand = t.git_bytes("show", f"{tag_commit}:docs/release/{tag}.md")
    info = Release(tag=tag, version=version, prerelease=prerelease, tag_commit=tag_commit, cert=cert, apks=apks,
                   setup_sha256=sha256_file(staged_setup), setup_built=not args.setup, previous_tag=since,
                   tray_sha256=sha256_file(staged_tray) if staged_tray.is_file() else None,
                   satellite_setup_sha256=sha256_file(staged_sat) if staged_sat.is_file() else None,
                   signatures=signatures, candidate_of=m.group(1) if by_name else None, app_from=app_from,
                   sboms=sboms, provenance=args.provenance,
                   linux_install=linux_install_line((t.git_bytes("show", f"{tag_commit}:docs/LINUX_HOST.md")
                                                     or b"").decode("utf-8", "replace")),
                   mac_setup_sha256=sha256_file(staged_mac) if staged_mac.is_file() else None,
                   mac_signatures=mac_signatures)
    current = (t.git_bytes("show", f"{tag_commit}:docs/RELEASE_NOTES.md") or b"").decode("utf-8")
    info.notes_anchor = notes_anchor(current, version)
    if hand is None and highlights_required(version):
        die(f"from {'.'.join(map(str, HIGHLIGHTS_FROM))} on a release needs docs/release/{tag}.md at the tag, with "
            f"its three \"## {HIGHLIGHTS}\" (docs/CONTRIBUTING.md, \"The release page\")")
    if hand is not None:
        problems = release_doc_problems(hand.decode("utf-8"), version)
        if highlights_required(version):
            problems += highlights_match_problems(hand.decode("utf-8"), current, version)
        if problems:
            die(f"docs/release/{tag}.md at the tag: " + "; ".join(problems)
                + " (docs/CONTRIBUTING.md, \"The release page\", has the template)")
        info.full_notes = True
        info.highlights = release_highlights_of(hand.decode("utf-8")) if highlights_required(version) else None
        info.notes = glance_notes(hand.decode("utf-8"), since=since)
        say(f"the notes: docs/release/{tag}.md at the tag (its \"{GLANCE}\" on the page, linked in full)")
    else:
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
    tray_note = f"{TRAY_ASSET} ({staged_tray.stat().st_size} bytes), " if info.tray_sha256 else ""
    say(f"staged in {stage}: {SETUP_ASSET} ({staged_setup.stat().st_size} bytes), {tray_note}{SUMS_ASSET}, NOTES.md ({len(text)} characters)")
    if not info.tray_sha256:
        say(f"no {TRAY_ASSET}: this tag's build makes none (it predates the tray icon)")
    if info.satellite_setup_sha256:
        say(f"and {SATELLITE_SETUP_ASSET} ({staged_sat.stat().st_size} bytes)")
    else:
        say(f"no {SATELLITE_SETUP_ASSET}: this tag's build makes none (it predates the satellite Setup)")
    if info.mac_setup_sha256:
        say(f"and {MAC_SETUP_ASSET} ({staged_mac.stat().st_size} bytes)")
    uploads = [staged_setup, *([stage / n for n in SIGNATURE_ASSETS] if signatures else []),
               *([staged_tray] if info.tray_sha256 else []),
               *([staged_sat] if info.satellite_setup_sha256 else []),
               *([staged_mac] if info.mac_setup_sha256 else []),
               *([stage / n for n in MAC_SIGNATURE_ASSETS] if mac_signatures else []),
               *(stage / n for n in SBOM_ASSETS if sboms and n in sboms), stage / SUMS_ASSET]

    stable_push = f"{tag_commit}:refs/heads/stable"
    if app_changed:
        (stage / "APP-NOTES.md").write_bytes(own_app_notes.encode("utf-8"))
    if not publish:
        say("not published (--no-publish). A real run then:")
        if push_tag:
            print(f"    git push origin {tag_commit}:refs/tags/{tag}    (the tag, never forced)")
        if create_release:
            print(f"    gh release create {tag} {' '.join(u.name for u in uploads)} --repo {REPO_SLUG} --verify-tag "
                  f"--title \"Domovoi v{version}\" --notes-file NOTES.md "
                  + ("--prerelease" if prerelease else "--latest") + f"    ({kind})")
        else:
            print(f"    gh release upload {tag} {' '.join(u.name for u in uploads)} --repo {REPO_SLUG} --clobber")
            print(f"    gh release edit {tag} --repo {REPO_SLUG} --title \"Domovoi v{version}\" --notes-file NOTES.md "
                  + ("--prerelease" if prerelease else "--latest") + f"    ({kind})")
        print("    (checks every file on GitHub against SHA256SUMS)")
        if app_changed:
            names = " ".join(e["file"] for e in apks)
            print(f"    gh release create {own_app_tag} {names} --repo {REPO_SLUG} --verify-tag "
                  f"--title \"Domovoi for Android {rel['version_name']}\" --notes-file APP-NOTES.md --latest=false"
                  f"    (the APKs downloaded from {tag}; only those it lacks, if it exists; never Latest)")
            print(f"    (downloads each APK back from {own_app_tag} and checks it against android/release.json)")
        if not moves_stable:
            print(f"    stable stays where it is: {STABLE_STAYS}")
        elif stable[0] == "already":
            print(f"    stable is already at {tag}")
        elif stable[0] == "unknown":
            print("    stable: unknown (no fetch)")
        else:
            frm = f" (a fast-forward from {stable[1][:12]})" if stable[1] else " (stable is made)"
            print(f"    git push origin {stable_push}    ({tag}){frm}")
        return 0

    # 5. Path b's tag, then upload and edit (or create). ----------------------
    if push_tag:
        say(f"pushing the tag {tag} ({tag_commit[:12]}) to GitHub...")
        # A plain push of a new tag: refused if GitHub has one of that name.
        t.git("push", "--quiet", "origin", f"{tag_commit}:refs/tags/{tag}")
        made["pushed"] = True
        if _remote_ref(t, f"refs/tags/{tag}") != tag_commit:
            die(f"the tag {tag} on GitHub is not {tag_commit[:12]} after the push")
    if create_release:
        say(f"creating the GitHub release {tag} with {', '.join(u.name for u in uploads)}, as {kind}...")
        t.gh("release", "create", tag, *(str(u) for u in uploads), "--repo", REPO_SLUG, "--verify-tag",
             "--title", f"Domovoi v{version}", "--notes-file", str(notes_file),
             "--prerelease" if prerelease else "--latest")
    else:
        say(f"uploading {', '.join(u.name for u in uploads)} to {tag}...")
        t.gh("release", "upload", tag, *(str(u) for u in uploads), "--repo", REPO_SLUG, "--clobber")
        say(f"setting the title and notes, as {kind}...")
        t.gh("release", "edit", tag, "--repo", REPO_SLUG, "--title", f"Domovoi v{version}", "--notes-file",
             str(notes_file), "--prerelease" if prerelease else "--latest")

    # 6. Check what GitHub has. -----------------------------------------------
    _verify_published(t, tag, sums)
    say("every file on GitHub matches SHA256SUMS")

    # 6b. Path a: the app's own release, app-<versionName>, for F-Droid. ----
    if app_changed:
        publish_app_release(t, stage, tag=tag, app_tag=own_app_tag, apks=apks, notes=own_app_notes,
                            server_latest=not prerelease)

    # 7. LAST: stable, to the SHA checked above. --------------------------------
    if not moves_stable:
        say(f"stable stays where it is: {STABLE_STAYS}")
    else:
        if _remote_ref(t, f"refs/tags/{tag}") != tag_commit:
            die(f"the tag {tag} on GitHub no longer names {tag_commit[:12]}: stable stays where it is")
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
    say(f"done: {tag} has the Setup, SHA256SUMS and its notes" + ("; stable is at it" if moves_stable else "")
        + (f"; {own_app_tag} has the APKs" if app_changed else "") + ".")
    return 0


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
