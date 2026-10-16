"""Make the room phone's cross-language identity fixture
(android/app/src/test/resources/roommode/identity-fixture.json), which
RoomIdentityTest and RoomModelsTest check.

Signs, with the core's own code (domovoi/server_identity.py) and a key made
up for this run in a temporary folder, (a) a /v1/health answer bound to an
address, (b) the same answer unbound and (c) a manifest envelope with
awkward paths. The Python satellite (satellite/server_identity.py) checks
both before anything is written: the fixture is only useful if the
reference implementation agrees with itself. The key is deleted at the end;
only public data reaches the file.

The address is the documented example network's PC (192.168.1.30), not a
real one. Run from the repo root:

    python scripts/dev/make_identity_fixture.py

Re-running makes a new key, so the fingerprint, the public key and every
signature in the file change; the Kotlin tests read them from the file.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from domovoi import server_identity  # noqa: E402
from satellite import server_identity as sat  # noqa: E402

OUT = REPO / "android" / "app" / "src" / "test" / "resources" / "roommode" / "identity-fixture.json"
CHALLENGE = "3f9a1c0d5e7b2a4c6e8f0a1b2c3d4e5f"
ADDR = "192.168.1.30:6370"
CHANNEL = "satellite-wake-base"
MANIFEST = {
    "melspectrogram.onnx": "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
    "greetings/greet_été \"quoted\".mp3": "00" * 32,
    "a/b\\c\n.mp3": "11" * 32,
    "\U0001f600.mp3": "22" * 32,
}


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    with tempfile.TemporaryDirectory() as d:
        ident = server_identity.load_or_create(Path(d) / "server-identity.json")
        health = {"status": "ok", "identity": ident.health_answer(CHALLENGE, addr=ADDR)}
        unbound = {"status": "ok", "identity": ident.health_answer(CHALLENGE)}
        envelope = ident.signed_manifest(CHANNEL, MANIFEST)
        sat.verify_health_document(health, challenge=CHALLENGE,
                                   expected_fingerprint=ident.fingerprint, addr=ADDR)
        sat.verify_manifest_envelope(envelope, channel=CHANNEL,
                                     expected_fingerprint=ident.fingerprint)
        doc = {
            "about": "Signed by domovoi/server_identity.py with a throwaway key "
                     "(scripts/dev/make_identity_fixture.py); public data only.",
            "fingerprint": ident.fingerprint,
            "challenge": CHALLENGE,
            "addr": ADDR,
            "health": health,
            "health_unbound": unbound,
            "envelope": envelope,
            "manifest_digest": sat.manifest_digest(MANIFEST),
            "canonical_manifest": sat.canonical_json(MANIFEST).decode("utf-8"),
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print("wrote", out, ident.fingerprint)


if __name__ == "__main__":
    main()
