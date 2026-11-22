"""``python -m domovoi.offline_kit verify DIR``: check an offline kit.

Exit 0 when the kit is fine, 1 when anything is wrong (every problem is
listed), 2 when the command itself is wrong.

  --release-key FILE    check the release signature with this ssh-ed25519
                        public key instead of Domovoi's (for tests)
  --no-release-check    skip the release signature (says so)
  --json                print the findings as JSON
"""

from __future__ import annotations

import argparse
import json
import sys

from .checker import verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m domovoi.offline_kit",
                                     description="Check a Domovoi offline kit.")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify", help="check every file on a kit")
    v.add_argument("dir", help="the kit's folder (the stick)")
    key = v.add_mutually_exclusive_group()
    key.add_argument("--release-key", metavar="FILE",
                     help="check the release signature with this ssh-ed25519 public key")
    key.add_argument("--no-release-check", action="store_true",
                     help="don't check the release signature")
    v.add_argument("--json", action="store_true", help="print the findings as JSON")
    args = parser.parse_args(argv)

    kwargs = {}
    if args.no_release_check:
        kwargs["release_key"] = None
    elif args.release_key:
        try:
            with open(args.release_key, encoding="ascii") as f:
                kwargs["release_key"] = f.read()
        except (OSError, UnicodeDecodeError) as e:
            print(f"Can't read the release key {args.release_key}: {e}", file=sys.stderr)
            return 2
    report = verify(args.dir, **kwargs)
    if args.json:
        print(json.dumps({"ok": report.ok, "errors": report.errors, "notes": report.notes,
                          "files_checked": report.files_checked,
                          "bytes_checked": report.bytes_checked}, indent=2))
        return 0 if report.ok else 1
    for note in report.notes:
        print(f"Note: {note}")
    if report.ok:
        version = report.doc["domovoi_version"] if report.doc else "?"
        print(f"The kit is fine: Domovoi {version}, {report.files_checked} files, "
              f"{report.bytes_checked / 1e9:.2f} GB checked.")
        return 0
    errors = report.errors
    print(f"The kit has {len(errors)} problem{'s' if len(errors) != 1 else ''}:")
    for e in errors:
        print(f"  - {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
