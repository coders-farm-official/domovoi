"""The offline kit end to end, for real but small (release 1.5.0, P1).

Builds a tiny kit image with the server's own job (domovoi/kit_builder/
jobs.py): a real FAT32 image made with mkfs.fat and mcopy (no root), a
file split into parts, the guide and the notices; then copies everything
back out of the image and runs P0's checker on it, the way an installer
would see the stick.

Gated: set ``DOMOVOI_KIT_E2E=1`` on a machine with dosfstools and mtools
(any Linux server; on Windows, run it in a Linux container). Nothing is
downloaded.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from domovoi import egress
from domovoi.kit_builder import fatimage, host, jobs, plan
from domovoi.kit_builder.parts import Part, Release
from domovoi.offline_kit import Source, verify

pytestmark = [
    pytest.mark.skipif(os.environ.get("DOMOVOI_KIT_E2E") != "1", reason="set DOMOVOI_KIT_E2E=1 to build a real kit image"),
    pytest.mark.skipif(fatimage.tools() is None, reason="needs mkfs.fat and mcopy (dosfstools, mtools)"),
]

COMMIT = "c" * 40


def test_a_tiny_kit_image_is_written_and_passes_the_checker(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "kit_root", lambda: tmp_path / "offline_kit")
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    big = tmp_path / "model.bin"
    big.write_bytes(os.urandom(2_500_000))
    parts = [
        Part(id="installer", group="base", kind="release", name="installer", version="1.5.0", licence="MIT",
             size=9, exact=True, download=0, acquire=lambda ctx: [Source("release/DomovoiSetup.exe", b"installer")]),
        Part(id="model", group="ai", kind="ollama-model", name="qwen3:4b", version="d", licence="Apache-2.0",
             size=big.stat().st_size, exact=True, download=0, pinned_by="scripts/linux/provision-manifest.conf",
             acquire=lambda ctx: [Source("ollama/models/blobs/sha256-" + "d" * 64, big),
                                  Source("ollama/models/manifests/registry.ollama.ai/library/qwen3/4b", b"{}")]),
        Part(id="wake", group="base", kind="wake-word", name="domovoi_v1", version="1", licence="CC-BY-NC-SA-4.0",
             size=4, exact=True, download=0, acquire=lambda ctx: [Source("wake/domovoi_v1.onnx", b"wake")]),
    ]
    rel = Release("1.5.0", "v1.5.0", COMMIT)
    doc = jobs.build(rel, plan.Selection(), parts, core="http://192.168.1.20:6370", part_size=1_048_576)
    img = jobs.image_file(doc["id"])
    assert img is not None and img.stat().st_size == doc["size"]

    # The disk: an MBR and one FAT32 partition labelled DOMOVOIKIT at 1 MiB.
    raw = img.read_bytes()[:1024 * 1024 + 512]
    assert raw[510:512] == b"\x55\xaa" and raw[446 + 4] == 0x0C
    boot = raw[1024 * 1024:]
    assert boot[82:90] == b"FAT32   " and boot[71:82] == b"DOMOVOIKIT "

    out = tmp_path / "stick"
    fatimage.extract(img, out)
    assert sorted(p.name for p in (out / "ollama/models/blobs").iterdir()) == [
        f"sha256-{'d' * 64}.part0{i}" for i in (1, 2, 3)]
    report = verify(out, release_key=None)
    assert report.ok, report.errors
    kit = json.loads((out / "kit.json").read_text())
    assert kit["domovoi_version"] == "1.5.0" and kit["part_size"] == 1_048_576
    assert {n["id"] for n in kit["notices"]} == {"do-not-sell", "wake-word-nc-sa"}
    # The checker an installer runs, as a program.
    r = subprocess.run([sys.executable, "-m", "domovoi.offline_kit", "verify", "--no-release-check", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "The kit is fine" in r.stdout
