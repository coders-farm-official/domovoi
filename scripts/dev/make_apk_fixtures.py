"""Make the signed test APKs that domovoi/tests/test_apk_signature.py and
test_android_app.py check (domovoi/tests/fixtures/android_app/).

Each fixture is a tiny ZIP laid out like an APK (a manifest placeholder, a
dex placeholder and a native library under lib/<abi>/), signed by the real
``apksigner`` from the Android SDK's build-tools with keys made up for this
run: an RSA-4096 "release" key (apksigner signs it with SHA-512), an
RSA-2048 "other" key (SHA-256) and an EC P-256 key. The keystores live in a
temporary folder and are deleted at the end; no key is ever written into
the repo, and none of them is the project's release key.

``fixtures.json`` beside the APKs records, for each file, its size and
SHA-256 and what ``apksigner verify --print-certs`` says about it (the
certificate SHA-256, the schemes), so the tests compare the core's own
verifier with Google's.

Run from the repo root with JAVA_HOME set and the SDK at ANDROID_HOME or
%LOCALAPPDATA%\\Android\\Sdk:

    python scripts/dev/make_apk_fixtures.py

Re-running makes new keys, so every file (and fixtures.json) changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "domovoi" / "tests" / "fixtures" / "android_app"
BUILD_TOOLS = "35.0.0"


def sdk_dir() -> Path:
    env = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if env:
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"


def java_bin(name: str) -> str:
    home = os.environ.get("JAVA_HOME")
    if home:
        exe = Path(home) / "bin" / (name + (".exe" if sys.platform == "win32" else ""))
        if exe.is_file():
            return str(exe)
    found = shutil.which(name)
    if not found:
        sys.exit(f"{name} not found: set JAVA_HOME")
    return found


APKSIGNER = sdk_dir() / "build-tools" / BUILD_TOOLS / "lib" / "apksigner.jar"


def apksigner(*args: str) -> str:
    r = subprocess.run([java_bin("java"), "-jar", str(APKSIGNER), *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"apksigner {args[0]} failed: {r.stdout}{r.stderr}")
    return r.stdout


def make_key(dest: Path, alg: str, size: int, password: str) -> None:
    subprocess.run(
        [java_bin("keytool"), "-genkeypair", "-keystore", str(dest), "-storetype", "PKCS12",
         "-alias", "test", "-keyalg", alg, "-keysize", str(size), "-validity", "36500",
         "-dname", f"CN=Domovoi test fixture {alg}-{size}, O=Not a release key",
         "-storepass", password, "-keypass", password],
        check=True, capture_output=True,
    )


def unsigned_apk(dest: Path, abis: list[str]) -> None:
    with zipfile.ZipFile(dest, "w") as z:
        z.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00 test fixture, not a real manifest")
        z.writestr(zipfile.ZipInfo("classes.dex"), b"dex\n035\x00" + b"\x00" * 64)
        for abi in abis:
            # Stored, like the real app's libraries; apksigner page-aligns them.
            z.writestr(zipfile.ZipInfo(f"lib/{abi}/libfixture.so"), b"\x7fELF" + abi.encode() * 40)
        z.writestr("res/raw/notice.txt", "Domovoi test fixture: signed with a throwaway key.\n",
                   compress_type=zipfile.ZIP_DEFLATED)


def sign(src: Path, dest: Path, ks: Path, password: str, *, v1: bool = False, v2: bool = True,
         v3: bool = True) -> None:
    apksigner("sign", "--ks", str(ks), "--ks-pass", f"pass:{password}", "--min-sdk-version", "26",
              "--v1-signing-enabled", str(v1).lower(), "--v2-signing-enabled", str(v2).lower(),
              "--v3-signing-enabled", str(v3).lower(), "--v4-signing-enabled", "false",
              "--out", str(dest), str(src))


def describe(apk: Path) -> dict:
    r = subprocess.run([java_bin("java"), "-jar", str(APKSIGNER), "verify", "--verbose", "--print-certs",
                        "--min-sdk-version", "26", str(apk)], capture_output=True, text=True)
    out = r.stdout
    cert = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})", out)
    return {
        "apksigner_verifies": r.returncode == 0,
        "apksigner_cert_sha256": cert.group(1) if cert else None,
        "apksigner_v2": "Verified using v2 scheme (APK Signature Scheme v2): true" in out,
        "apksigner_v3": "Verified using v3 scheme (APK Signature Scheme v3): true" in out,
    }


def main() -> int:
    if not APKSIGNER.is_file():
        sys.exit(f"no apksigner at {APKSIGNER}")
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.apk"):
        old.unlink()
    tmp = Path(tempfile.mkdtemp(prefix="domovoi-apk-fixtures-"))
    try:
        pw = secrets.token_urlsafe(18)
        keys = {"release": tmp / "release.p12", "other": tmp / "other.p12", "ec": tmp / "ec.p12"}
        make_key(keys["release"], "RSA", 4096, pw)
        make_key(keys["other"], "RSA", 2048, pw)
        make_key(keys["ec"], "EC", 256, pw)
        plan = [
            # file, abis in the zip, key, signing schemes, what it is for
            ("release-arm64-v8a.apk", ["arm64-v8a"], "release", {}, "the release key, v2 + v3 (SHA-512)"),
            ("release-armeabi-v7a.apk", ["armeabi-v7a"], "release", {}, "the release key, 32-bit"),
            ("release-v2only-arm64-v8a.apk", ["arm64-v8a"], "release", {"v3": False}, "v2 only"),
            ("release-two-abis.apk", ["arm64-v8a", "armeabi-v7a"], "release", {}, "libraries for two ABIs"),
            ("other-arm64-v8a.apk", ["arm64-v8a"], "other", {}, "another RSA key (SHA-256)"),
            ("ec-arm64-v8a.apk", ["arm64-v8a"], "ec", {}, "an EC key: refused"),
            ("v1only-arm64-v8a.apk", ["arm64-v8a"], "other", {"v1": True, "v2": False, "v3": False},
             "JAR signing only: refused"),
        ]
        manifest: dict = {
            "about": "Made by scripts/dev/make_apk_fixtures.py with throwaway keys (deleted after); "
                     "not the release key. apksigner_* fields are what apksigner verify printed.",
            "files": {},
        }
        for name, abis, key, schemes, about in plan:
            raw = tmp / ("unsigned-" + name)
            unsigned_apk(raw, abis)
            sign(raw, OUT / name, keys[key], pw, **schemes)
        raw = OUT / "unsigned-arm64-v8a.apk"
        unsigned_apk(raw, ["arm64-v8a"])
        plan.append(("unsigned-arm64-v8a.apk", ["arm64-v8a"], None, {}, "not signed at all: refused"))
        for name, abis, key, schemes, about in plan:
            data = (OUT / name).read_bytes()
            manifest["files"][name] = {
                "about": about, "key": key, "abis": abis,
                "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                **describe(OUT / name),
            }
        (OUT / "fixtures.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {len(plan)} APKs and fixtures.json to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
