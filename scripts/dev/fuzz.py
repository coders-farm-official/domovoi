"""Run one of Domovoi's fuzz targets under Atheris (coverage-guided, libFuzzer).

    python scripts/dev/fuzz.py --list          # the targets (--names: names only)
    python scripts/dev/fuzz.py ics -max_total_time=60
    python scripts/dev/fuzz.py media_tags -max_total_time=60 -timeout=20 corpus/
    python scripts/dev/fuzz.py --seeds media_tags corpus/   # copy the seed files there, then exit

The targets are domovoi/tests/fuzz_targets.py's: the parsers of outside
input (audio tags, ICS feeds, the APK signature check, chat documents and
videos, upload paths, chat drafts, the user-text patterns). Everything
after the target name goes to libFuzzer: ``-max_total_time=N`` seconds,
``-timeout=N`` per input, ``-runs=N``, corpus folders (the first one is
written to). A finding stops the run, prints the traceback and saves the
input as ``crash-<sha1>`` in the current folder; replay it with
``python scripts/dev/fuzz.py <target> crash-<sha1>``.

Atheris (Apache-2.0) is a CI and developer tool, never a dependency of
Domovoi: its wheels are Linux x86_64 only (``pip install --require-hashes
-r .github/ci-fuzz-requirements.txt``). On other systems the same targets
run under Hypothesis in the ordinary suite (domovoi/tests/test_fuzz_parsers.py).
docs/CONTRIBUTING.md, "Fuzz and property tests".
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


#: Modules a target reaches only past a check a one-byte input fails.
WARM = (
    "domovoi.doc_extract.word", "domovoi.doc_extract.odf", "domovoi.doc_extract.sheets",
    "domovoi.doc_extract.plain", "domovoi.doc_extract.structure", "domovoi.richdoc",
)


def _setup_home() -> None:
    """A throwaway HOME, as the test suite has: nothing here may touch the
    real ~/.domovoi (several modules derive paths from it on import)."""
    import tempfile

    home = tempfile.mkdtemp(prefix="domovoi-fuzz-home-")
    os.environ["HOME"] = os.environ["USERPROFILE"] = home
    os.environ.setdefault("USE_STUBS", "true")


def main(argv: list[str]) -> int:
    _setup_home()
    from domovoi.tests import fuzz_targets as ft

    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--names":
        for name in ft.TARGETS:
            print(name)
        return 0
    if argv[0] == "--list":
        for name, t in ft.TARGETS.items():
            print(f"{name:14} {t.about} ({len(t.seeds())} seed files, {len(t.samples())} samples)")
        return 0
    if argv[0] == "--seeds":
        name, dest = argv[1], Path(argv[2])
        dest.mkdir(parents=True, exist_ok=True)
        t = ft.TARGETS[name]
        for p in t.seeds():
            # Each seed file with the first few lead bytes the target
            # branches on, so the fuzzer starts from each variant.
            for lead in range(4):
                (dest / f"{p.stem}-{lead}{p.suffix}").write_bytes(bytes([lead]) + p.read_bytes())
        for i, data in enumerate(t.samples() + ft.found(name)):
            (dest / f"sample-{i}").write_bytes(data)
        return 0
    name, rest = argv[0], argv[1:]
    if name not in ft.TARGETS:
        print(f"no target {name!r}; --list names them", file=sys.stderr)
        return 2

    import atheris

    # Coverage feedback comes from the code loaded when instrument_all()
    # runs. The targets import lazily, so a warm-up call per lead byte, and
    # the document readers a refused input never reaches, load it first.
    import importlib

    for mod in WARM:
        importlib.import_module(mod)
    target = ft.TARGETS[name].run
    for lead in range(16):
        try:
            target(bytes([lead]))
        except Exception:  # noqa: BLE001 - only the imports matter here
            pass
    atheris.instrument_all()
    atheris.Setup([sys.argv[0], *rest], target)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
