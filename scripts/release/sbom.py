"""The release's software bills of materials (SBOMs), CycloneDX 1.6 JSON.

Three, one per thing a release ships that carries other people's code:

``server``   The Python packages a Linux server (and the Windows install's
             distro) runs: every pin of ``requirements-linux-py314.lock`` and
             ``requirements-linux-py314-no-deps.lock``, the files
             ``scripts/linux/provision.sh`` installs from, each with the
             SHA-256s pip holds it to, and who needs whom (pip-compile's
             ``# via`` lines).
``web``      The browser libraries the dashboard serves from
             ``web/static/vendor/``: each one's version and licence (the
             table below, held to the files by
             ``domovoi/tests/test_release_sbom.py``), the SHA-256 of every
             file shipped, and the packages inside the bundles Domovoi
             builds (their ``THIRD-PARTY.json``, Univer's notices).
``android``  What the release APKs carry: the app's
             ``releaseRuntimeClasspath`` as Gradle resolved it (written by
             ``scripts/release/sbom-android.init.gradle``: each Maven
             component, the SHA-256 of its file, its dependencies), plus the
             speech library built from source
             (``android/native/sherpa-onnx``).

Run from anywhere; paths are the repository's::

    python scripts/release/sbom.py server  --out domovoi-server.cdx.json
    python scripts/release/sbom.py web     --out domovoi-web.cdx.json
    cd android && ./gradlew --no-daemon --no-configuration-cache \\
        --init-script ../scripts/release/sbom-android.init.gradle \\
        -Pdomovoi.sbomDeps="$PWD/deps.json" :app:domovoiSbomDeps
    python scripts/release/sbom.py android --gradle-deps android/deps.json --out domovoi-android.cdx.json

``.github/workflows/release.yml`` (its ``sbom`` job, no secrets) makes all
three for a release; finish-release attaches them beside the other files
and lists them in ``SHA256SUMS``. CI makes them on every change, so a
release never finds them broken.

Standard library only: no SBOM tool is downloaded, so nothing outside this
repository decides what the SBOM says (the generator is this file, MIT
like the rest). ``--tag`` names the release in the SBOM (default ``v`` +
``pyproject.toml``'s version).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
import tomllib
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[2]
REPO_URL = "https://github.com/coders-farm-official/domovoi"
SPEC_VERSION = "1.6"
#: The release-asset names finish-release attaches (scripts/release/finish_release.py SBOM_ASSETS).
ASSET_NAMES = {"server": "domovoi-server.cdx.json", "web": "domovoi-web.cdx.json",
               "android": "domovoi-android.cdx.json"}
SERVER_LOCKS = ("requirements-linux-py314.lock", "requirements-linux-py314-no-deps.lock")
VENDOR = "web/static/vendor"
#: The ABIs of the release APKs (release.yml builds -Pdomovoi.abis=arm64-v8a,armeabi-v7a).
RELEASE_ABIS = ("arm64-v8a", "armeabi-v7a")
SHERPA = "android/native/sherpa-onnx"

#: The dashboard's vendored libraries. ``files`` are globs under
#: web/static/vendor/ (every shipped .js and .css must match one entry: the
#: test checks); ``third_party`` names a bundle's own list of what it holds
#: (``json``: a THIRD-PARTY.json; ``notices``: Univer's notices file);
#: ``inside`` lists what a bundle carries that has no such list
#: (docs/LICENSES.md and each folder's README say the same). A version
#: bump here goes with the files; the test holds each version to the
#: folder's README, docs/LICENSES.md or the file itself.
WEB_LIBRARIES: list[dict[str, Any]] = [
    {"name": "react", "version": "18.3.1", "license": "MIT", "npm": True,
     "files": ["react/react.development.js"]},
    {"name": "react-dom", "version": "18.3.1", "license": "MIT", "npm": True,
     "files": ["react/react-dom.development.js"]},
    {"name": "@babel/standalone", "version": "7.29.0", "license": "MIT", "npm": True,
     "files": ["babel/babel.min.js"]},
    {"name": "lucide", "version": "1.22.0", "license": "ISC", "npm": True,
     "files": ["lucide/lucide.min.js"]},
    {"name": "marked", "version": "12.0.2", "license": "MIT", "npm": True,
     "files": ["marked/marked.min.js"]},
    {"name": "qrcode-generator", "version": "1.4.4", "license": "MIT", "npm": True,
     "files": ["qrcode/qrcode.js"]},
    {"name": "butterchurn", "version": "3.0.0-beta.5", "license": "MIT", "npm": True,
     "files": ["butterchurn/butterchurn.min.js"],
     "modified": "its built-in noise texture replaced by one made for Domovoi (web/static/vendor/butterchurn/README.md)",
     "inside": [{"name": "eel-wasm", "license": "MIT"},
                {"name": "@assemblyscript/loader", "license": "Apache-2.0"},
                {"name": "ecma-proposal-math-extensions", "version": "0.0.2", "license": "MIT"}]},
    {"name": "@excalidraw/excalidraw", "version": "0.17.6", "license": "MIT", "npm": True,
     "files": ["excalidraw/*.js", "excalidraw/excalidraw-assets/*.js", "excalidraw/excalidraw-assets/locales/*.js"],
     "inside": [{"name": "dompurify", "version": "3.0.3", "license": "Apache-2.0 OR MPL-2.0"}]},
    {"name": "domovoi-tiptap-bundle", "version": "2.27.3", "license": "MIT AND BSD-2-Clause",
     "description": "Tiptap and ProseMirror for the Documents editor, built by scripts/vendor/editors/build.mjs",
     "files": ["tiptap/tiptap.min.js"], "third_party": ("json", "tiptap/THIRD-PARTY.json")},
    {"name": "domovoi-yjs-bundle", "version": "13.6.33", "license": "MIT",
     "description": "Yjs and its co-editing helpers, built by scripts/vendor/editors/build.mjs",
     "files": ["yjs/yjs.min.js"], "third_party": ("json", "yjs/THIRD-PARTY.json")},
    {"name": "domovoi-mammoth-bundle", "version": "1.13.0", "license": "BSD-2-Clause AND MIT AND Zlib",
     "description": "mammoth (Word files to HTML), built by scripts/vendor/editors/build.mjs",
     "files": ["mammoth/mammoth.min.js"], "third_party": ("json", "mammoth/THIRD-PARTY.json")},
    {"name": "domovoi-univer-bundle", "version": "1.2.0", "license": "Apache-2.0",
     "description": "Univer's open-source spreadsheet editor, built by scripts/vendor/univer/vendor_univer.py",
     "files": ["univer/univer.js", "univer/univer.css"], "third_party": ("notices", "univer/THIRD-PARTY-NOTICES.txt")},
]


class Refused(Exception):
    """The input isn't what this script reads: the message says what."""


# ─── shared ──────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def license_choice(expr: str) -> list[dict[str, Any]]:
    """CycloneDX's licences for an SPDX expression: one id, or the expression."""
    expr = expr.strip()
    if re.fullmatch(r"[A-Za-z0-9.+-]+", expr):
        return [{"license": {"id": expr}}]
    return [{"expression": expr}]


def npm_purl(name: str, version: str | None) -> str:
    """``pkg:npm/%40scope/name@version`` (the purl spec encodes the scope's @)."""
    base = "pkg:npm/" + quote(name, safe="/")
    return f"{base}@{quote(version, safe='')}" if version else base


def pypi_name(name: str) -> str:
    """PEP 503's normalised name, the one purls use."""
    return re.sub(r"[-_.]+", "-", name).lower()


def project_version(repo: Path) -> str:
    return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def default_tag(repo: Path) -> str:
    """``v`` + pyproject's version, in the tag's spelling (``1.0.0rc2`` -> ``v1.0.0-rc2``)."""
    v = project_version(repo)
    m = re.fullmatch(r"([0-9]+\.[0-9]+\.[0-9]+)(?:(a|b|rc)([0-9]+))?", v)
    if not m:
        raise Refused(f"pyproject.toml's version {v!r} is not X.Y.Z[aN|bN|rcN]")
    suffix = {"a": "-alpha", "b": "-beta", "rc": "-rc"}.get(m.group(2) or "", "")
    return f"v{m.group(1)}{suffix}{m.group(3) or ''}"


def bom(root: dict[str, Any], components: list[dict[str, Any]], dependencies: list[dict[str, Any]],
        *, repo: Path, now: datetime.datetime | None = None) -> dict[str, Any]:
    """The CycloneDX document around a root component."""
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": stamp,
            "tools": {"components": [{
                "type": "application", "name": "domovoi-sbom", "version": project_version(repo),
                "description": "scripts/release/sbom.py, Domovoi's own generator (standard library only)",
                "externalReferences": [{"type": "vcs", "url": REPO_URL}],
            }]},
            "manufacturer": {"name": "Coders Farm", "url": ["https://github.com/coders-farm-official"]},
            "component": root,
        },
        "components": components,
        "dependencies": dependencies,
    }


def domovoi_root(repo: Path, tag: str, description: str) -> dict[str, Any]:
    return {
        "type": "application", "bom-ref": "domovoi", "name": "domovoi", "version": tag.removeprefix("v"),
        "description": description, "licenses": license_choice("MIT"),
        "purl": f"pkg:github/coders-farm-official/domovoi@{quote(tag, safe='')}",
        "externalReferences": [{"type": "vcs", "url": REPO_URL},
                               {"type": "distribution", "url": f"{REPO_URL}/releases/tag/{tag}"}],
    }


# ─── server: the hash-pinned locks ───────────────────────────────────────

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s\\;]+)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def parse_lock(text: str) -> list[dict[str, Any]]:
    """Each pin of a pip-compile lock: name, version, its SHA-256s, and what
    needs it (``via``: package names; ``"root"`` for this project or the
    lock's own ``.in`` file)."""
    pins: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    in_via = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        m = _PIN.match(line)
        if m:
            cur = {"name": m.group(1), "version": m.group(2), "hashes": [], "via": []}
            pins.append(cur)
            in_via = False
            cur["hashes"] += _HASH.findall(line)
            continue
        if cur is None:
            continue
        stripped = line.strip()
        if stripped.startswith("--hash="):
            cur["hashes"] += _HASH.findall(stripped)
            continue
        v = re.match(r"^#\s+via(?:\s+(.*))?$", stripped)
        if v:
            in_via = True
            if v.group(1):
                cur["via"].append(_via(v.group(1)))
                in_via = False
            continue
        if in_via:
            item = re.match(r"^#\s{3,}(\S.*)$", stripped)
            if item:
                cur["via"].append(_via(item.group(1)))
                continue
            in_via = False
        if not line.startswith(" "):
            cur = None
    for p in pins:
        if not p["hashes"]:
            raise Refused(f"{p['name']}=={p['version']} has no --hash: the lock must be hash-pinned")
    return pins


def _via(text: str) -> str:
    text = text.strip()
    if text.startswith("-r ") or text.startswith("-c ") or "(pyproject.toml)" in text:
        return "root"
    return pypi_name(re.sub(r"\[.*?\]", "", text.split()[0]))


def server_bom(repo: Path, tag: str, now: datetime.datetime | None = None) -> dict[str, Any]:
    pins: list[dict[str, Any]] = []
    for name in SERVER_LOCKS:
        path = repo / name
        if not path.is_file():
            raise Refused(f"no {name}")
        pins += parse_lock(path.read_text(encoding="utf-8"))
    by_name: dict[str, dict[str, Any]] = {}
    for p in pins:
        key = pypi_name(p["name"])
        if key in by_name:
            raise Refused(f"{p['name']} is pinned twice across {', '.join(SERVER_LOCKS)}")
        by_name[key] = p
    components = []
    needs: dict[str, set[str]] = {"domovoi": set()}
    for key in sorted(by_name):
        p = by_name[key]
        purl = f"pkg:pypi/{key}@{quote(p['version'], safe='')}"
        components.append({
            "type": "library", "bom-ref": purl, "name": key, "version": p["version"], "purl": purl,
            "externalReferences": [{
                "type": "distribution", "url": f"https://pypi.org/project/{key}/{p['version']}/",
                "comment": "the files pip may install for this pin, by SHA-256 (any other is refused)",
                "hashes": [{"alg": "SHA-256", "content": h} for h in sorted(set(p["hashes"]))],
            }],
        })
        needs.setdefault(purl, set())
    ref = {k: f"pkg:pypi/{k}@{quote(p['version'], safe='')}" for k, p in by_name.items()}
    for key, p in by_name.items():
        for via in p["via"] or ["root"]:
            parent = "domovoi" if via == "root" else ref.get(via)
            if parent is None:
                raise Refused(f"{p['name']} is needed by {via}, which no lock pins")
            needs.setdefault(parent, set()).add(ref[key])
    deps = [{"ref": r, "dependsOn": sorted(needs[r])} for r in ["domovoi", *sorted(k for k in needs if k != "domovoi")]]
    root = domovoi_root(repo, tag, "The Domovoi server (core and web dashboard): the Python packages a Linux server "
                                   "installs, from " + " and ".join(SERVER_LOCKS))
    return bom(root, components, deps, repo=repo, now=now)


# ─── web: the vendored browser libraries ─────────────────────────────────

def _notices(text: str) -> list[dict[str, str]]:
    """Univer's THIRD-PARTY-NOTICES.txt: ``<name> <version>  (<licence>)``
    on the line after each rule of ``=``."""
    out = []
    lines = text.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines[:-1]):
        if line.startswith("=====") and i + 2 < len(lines) and lines[i + 2].startswith("====="):
            m = re.fullmatch(r"(\S+) (\S+)  \((.+)\)", lines[i + 1].strip())
            if not m:
                raise Refused(f"a notices heading this script can't read: {lines[i + 1]!r}")
            out.append({"name": m.group(1), "version": m.group(2), "license": m.group(3)})
    return out


def _inner(entry: dict[str, Any], vendor: Path) -> list[dict[str, Any]]:
    """The packages inside a bundle, as library components."""
    items: list[dict[str, Any]] = list(entry.get("inside") or [])
    kind_path = entry.get("third_party")
    if kind_path:
        kind, rel = kind_path
        text = (vendor / rel).read_text(encoding="utf-8")
        items += json.loads(text) if kind == "json" else _notices(text)
    out = []
    seen = set()
    for it in items:
        purl = npm_purl(it["name"], it.get("version"))
        if purl in seen:
            continue
        seen.add(purl)
        c: dict[str, Any] = {"type": "library", "bom-ref": f"{entry['name']}/{purl}", "name": it["name"]}
        if it.get("version"):
            c["version"] = it["version"]
        c["purl"] = purl
        if it.get("license"):
            c["licenses"] = license_choice(it["license"])
        out.append(c)
    return out


def web_files(entry: dict[str, Any], vendor: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in entry["files"]:
        found = sorted(p for p in vendor.glob(pattern) if p.is_file())
        if not found:
            raise Refused(f"{VENDOR}/{pattern} ({entry['name']}) matches no file")
        files += found
    return files


def web_bom(repo: Path, tag: str, now: datetime.datetime | None = None) -> dict[str, Any]:
    vendor = repo / VENDOR
    components = []
    for entry in WEB_LIBRARIES:
        ref = npm_purl(entry["name"], entry["version"]) if entry.get("npm") else f"{entry['name']}@{entry['version']}"
        c: dict[str, Any] = {"type": "library", "bom-ref": ref, "name": entry["name"], "version": entry["version"]}
        if entry.get("npm"):
            c["purl"] = ref
        if entry.get("description"):
            c["description"] = entry["description"]
        c["licenses"] = license_choice(entry["license"])
        if entry.get("modified"):
            c["pedigree"] = {"notes": "Modified: " + entry["modified"]}
        files = [{"type": "file", "bom-ref": f"file:{p.relative_to(repo).as_posix()}",
                  "name": p.relative_to(repo).as_posix(),
                  "hashes": [{"alg": "SHA-256", "content": sha256_file(p)}]}
                 for p in web_files(entry, vendor)]
        c["components"] = files + _inner(entry, vendor)
        components.append(c)
    deps = [{"ref": "domovoi", "dependsOn": [c["bom-ref"] for c in components]}]
    root = domovoi_root(repo, tag, f"The Domovoi web dashboard's vendored browser libraries ({VENDOR}/), "
                                   "served by the server itself")
    return bom(root, components, deps, repo=repo, now=now)


# ─── android: Gradle's resolved release classpath ────────────────────────

def app_version(repo: Path) -> tuple[str, int]:
    text = (repo / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    names = re.findall(r'^\s*versionName\s*=\s*"([^"]+)"', text, re.M)
    codes = re.findall(r"^\s*versionCode\s*=\s*([0-9]+)", text, re.M)
    if len(names) != 1 or len(codes) != 1:
        raise Refused("android/app/build.gradle.kts doesn't set versionName and versionCode exactly once each")
    return names[0], int(codes[0])


def maven_purl(group: str, module: str, version: str) -> str:
    return f"pkg:maven/{quote(group, safe='')}/{quote(module, safe='')}@{quote(version, safe='')}"


def sherpa_component(repo: Path) -> dict[str, Any]:
    """The speech library built from source (not a Maven dependency): its
    release, commit and the files the release APKs carry."""
    d = repo / SHERPA
    info = (d / "BUILD-INFO.txt").read_text(encoding="utf-8")
    m = re.match(r"sherpa-onnx v([0-9][0-9A-Za-z.-]*), commit ([0-9a-f]{40})", info)
    if not m:
        raise Refused(f"{SHERPA}/BUILD-INFO.txt doesn't start with 'sherpa-onnx v<version>, commit <sha>'")
    sums = {}
    for line in (d / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if line.strip():
            sha, name = line.split("  ", 1)
            sums[name] = sha
    files = []
    for abi in RELEASE_ABIS:
        name = f"jniLibs/{abi}/libsherpa-onnx-jni.so"
        if name not in sums:
            raise Refused(f"{SHERPA}/SHA256SUMS has no {name}")
        files.append({"type": "file", "bom-ref": f"file:{SHERPA}/{name}", "name": f"{SHERPA}/{name}",
                      "hashes": [{"alg": "SHA-256", "content": sums[name]}]})
    purl = f"pkg:github/k2-fsa/sherpa-onnx@v{m.group(1)}"
    return {
        "type": "library", "bom-ref": purl, "name": "sherpa-onnx", "version": m.group(1), "purl": purl,
        "licenses": license_choice("Apache-2.0"),
        "description": f"Built from source by {SHERPA}/build.sh (text to speech left out); BUILD-INFO.txt there "
                       "names the toolchain and what its build fetched",
        "pedigree": {"commits": [{"uid": m.group(2), "url": f"https://github.com/k2-fsa/sherpa-onnx/commit/{m.group(2)}"}]},
        "components": files,
    }


def android_bom(repo: Path, tag: str, gradle_deps: Path, now: datetime.datetime | None = None) -> dict[str, Any]:
    doc = json.loads(gradle_deps.read_text(encoding="utf-8"))
    if doc.get("format") != 1 or doc.get("configuration") != "releaseRuntimeClasspath":
        raise Refused(f"{gradle_deps} is not sbom-android.init.gradle's output (format 1, releaseRuntimeClasspath)")
    comps = doc.get("components") or []
    if not comps:
        raise Refused(f"{gradle_deps} lists no components")
    key_ref = {}
    components = []
    for c in sorted(comps, key=lambda c: (c["group"], c["module"], c["version"])):
        purl = maven_purl(c["group"], c["module"], c["version"])
        key_ref[f"{c['group']}:{c['module']}:{c['version']}"] = purl
        comp: dict[str, Any] = {"type": "library", "bom-ref": purl, "group": c["group"], "name": c["module"],
                                "version": c["version"], "purl": purl}
        arts = c.get("artifacts") or []
        if len(arts) == 1:
            comp["hashes"] = [{"alg": "SHA-256", "content": arts[0]["sha256"]}]
        elif arts:
            comp["components"] = [{"type": "file", "bom-ref": f"{purl}#{a['file']}", "name": a["file"],
                                   "hashes": [{"alg": "SHA-256", "content": a["sha256"]}]} for a in arts]
        components.append(comp)
    sherpa = sherpa_component(repo)
    components.append(sherpa)
    name, code = app_version(repo)
    root = {
        "type": "application", "bom-ref": "com.domovoi.app", "name": "com.domovoi.app", "version": name,
        "description": f"Domovoi for Android (versionCode {code}): the release APKs ({', '.join(RELEASE_ABIS)}) "
                       f"published with Domovoi {tag}",
        "licenses": license_choice("MIT"),
        "externalReferences": [{"type": "vcs", "url": REPO_URL},
                               {"type": "distribution", "url": f"{REPO_URL}/releases/tag/{tag}"}],
        "properties": [{"name": "android:versionCode", "value": str(code)}],
    }

    def refs(keys: list[str], where: str) -> list[str]:
        out = []
        for k in keys:
            if k not in key_ref:
                raise Refused(f"{where} depends on {k}, which {gradle_deps.name} doesn't list")
            out.append(key_ref[k])
        return sorted(set(out))

    deps = [{"ref": "com.domovoi.app", "dependsOn": sorted({*refs(doc.get("roots") or [], "the app"), sherpa["bom-ref"]})}]
    for c in sorted(comps, key=lambda c: (c["group"], c["module"], c["version"])):
        k = f"{c['group']}:{c['module']}:{c['version']}"
        deps.append({"ref": key_ref[k], "dependsOn": refs(c.get("dependencies") or [], k)})
    deps.append({"ref": sherpa["bom-ref"], "dependsOn": []})
    return bom(root, components, deps, repo=repo, now=now)


# ─── the command line ────────────────────────────────────────────────────

def main(argv: list[str] | None = None, repo: Path | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sbom", description="Write one of the release's CycloneDX SBOMs.")
    ap.add_argument("kind", choices=sorted(ASSET_NAMES))
    ap.add_argument("--out", required=True, help="the JSON file to write")
    ap.add_argument("--tag", help="the release, v<version> (default: v + pyproject.toml's version)")
    ap.add_argument("--gradle-deps", help="android: the JSON sbom-android.init.gradle wrote")
    args = ap.parse_args(argv)
    repo = Path(repo or REPO)
    try:
        tag = args.tag or default_tag(repo)
        if args.kind == "server":
            doc = server_bom(repo, tag)
        elif args.kind == "web":
            doc = web_bom(repo, tag)
        else:
            if not args.gradle_deps:
                raise Refused("android needs --gradle-deps (sbom-android.init.gradle's output)")
            doc = android_bom(repo, tag, Path(args.gradle_deps))
    except Refused as e:
        print(f"sbom: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"sbom: {out.name}: {args.kind}, {len(doc['components'])} components, {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
