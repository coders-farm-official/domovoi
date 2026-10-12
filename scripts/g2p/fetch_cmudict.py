"""Fetch CMUdict at the pinned commit, check it, and write Domovoi's copy.

    python scripts/g2p/fetch_cmudict.py              # -> domovoi/phonemizer/data/cmudict/
    python scripts/g2p/fetch_cmudict.py --upstream DIR   # the unchanged files (training input)

Every file is checked against the SHA-256 below before anything is written.
Domovoi's copy drops the 17 entries whose word contains one of the repo's
reserved names (AGENTS.md, "Permanent conventions"); nothing else changes.
The out-of-dictionary model was trained on the unchanged upstream file
(train-in-container.sh fetches it with --upstream).
"""
import hashlib
import os
import re
import sys
import urllib.request

COMMIT = "74790861f652b15e4ac49015a90074ad62a27690"
BASE = f"https://raw.githubusercontent.com/cmusphinx/cmudict/{COMMIT}/"
UPSTREAM_SHA256 = {
    "cmudict.dict": "81917843c7f44ce2b094ac63873c2c7a4cf802040792c455ba3ca406891c3d22",
    "LICENSE": "bd4ce8e44170a5f9f481310ca85c51de3c4f851a65e679b40e603b143bd3542a",
    "README": "00c34e7564f1f6a68de02e12c123d801471da92bc3091f7d89b605f238bf8554",
}
# The filtered cmudict.dict Domovoi ships (also in data/SHA256SUMS).
FILTERED_SHA256 = "bd7b76b677381e8ea6ffced3dd18b310d903a6a3cd36aa718f8d79be1ffdcf5a"
# The reserved names, written so they don't match themselves.
RESERVED = re.compile(r"har[l]ey|ric[h]ard|orche[s]trator|yout[u]be|yt[-_]?d[l]p", re.IGNORECASE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEST = os.path.join(REPO, "domovoi", "phonemizer", "data", "cmudict")


def fetch(name):
    with urllib.request.urlopen(BASE + name, timeout=60) as r:
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != UPSTREAM_SHA256[name]:
        raise SystemExit(f"{name}: SHA-256 {got}, expected {UPSTREAM_SHA256[name]}")
    return data


def filtered(data):
    keep, dropped = [], []
    for line in data.decode("utf-8").splitlines(keepends=True):
        word = line.split(" ", 1)[0]
        (dropped if RESERVED.search(word) else keep).append(line)
    return "".join(keep).encode("utf-8"), len(dropped)


def main(argv):
    upstream = "--upstream" in argv
    dest = argv[argv.index("--upstream") + 1] if upstream else DEST
    os.makedirs(dest, exist_ok=True)
    for name in UPSTREAM_SHA256:
        data = fetch(name)
        if name == "cmudict.dict" and not upstream:
            data, n = filtered(data)
            got = hashlib.sha256(data).hexdigest()
            if got != FILTERED_SHA256:
                raise SystemExit(f"filtered cmudict.dict: SHA-256 {got}, expected {FILTERED_SHA256}")
            print(f"cmudict.dict: dropped {n} reserved-name entries")
        with open(os.path.join(dest, name), "wb") as f:
            f.write(data)
        print(f"{hashlib.sha256(data).hexdigest()}  {name}")


if __name__ == "__main__":
    main(sys.argv[1:])
