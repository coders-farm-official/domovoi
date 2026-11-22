# The offline kit

An online Domovoi server can write an **offline kit** to a USB stick. A
house that has never been online sets up Domovoi from that stick, and
later updates from a newer one. The stick holds everything an install
would otherwise download: the installers, the models, the phone app, the
packages.

> **Coming in 1.5.0, being built.** This page describes the kit's format
> and how to check a stick. Making a kit from the dashboard and installing
> from one arrive in later steps of 1.5.0; this page grows with them.

## What's on the stick

The stick is one FAT32 partition labelled `DOMOVOIKIT`:

```
DOMOVOIKIT/
  START-HERE.html, START-HERE.txt   plain steps for each computer
  kit.json        what is on this stick
  SHA256SUMS      a checksum for every file on the stick
  release/        the GitHub release's own installers and signed manifests
  linux/          install.sh
  source/         Domovoi's source, as a git bundle
  apt/            the Ubuntu and Docker packages the install needs
  python/         Python packages
  images/         Docker images
  ollama/         Ollama and its AI models
  speech/         speech-to-text, the voices, voice ID's model
  wake/           wake words and their notices
  phone/          the Android app
  windows/        WSL and Ubuntu for Windows
  mac/            the Mac parts (only when Mac was ticked)
  rooms/          what an offline server needs to add rooms
  licences/       every licence and notice
```

A kit is made for **a Windows or Linux PC** (ticked by default), **a Mac
with an Apple chip** (unticked by default), or both.

Nothing from your household is on it: no database, no passwords, no
names, no media, no voice profiles. A kit is not a backup.

## Checking a stick

Every installer checks the stick before it uses it. To check one
yourself, from a computer with Domovoi's source (or on a Domovoi server):

```
python3 -m domovoi.offline_kit verify /media/alex/DOMOVOIKIT
```

On Windows the stick is a drive letter, for example `E:\`. The check needs
nothing but Python. It lists every problem it finds, not only the first,
and ends with "The kit is fine" or the list. Any problem means: make the
stick again.

On any Linux computer, `cd /media/alex/DOMOVOIKIT && sha256sum -c
SHA256SUMS` checks every file too.

### What the checks prove

- **The copy is good.** Every file is there, the right size, with the
  right SHA-256; split files join back correctly; nothing else is on the
  stick. This catches a bad write or a failing stick. It is not proof of
  where the stick came from.
- **It is a Domovoi release.** The installers in `release/` come with the
  release's signed manifest. The check verifies that signature with
  Domovoi's release key (the same key the Linux, Windows and Mac
  installers already hold) and that it names the same version and commit
  as `kit.json`. Every model and package is then checked against the pins
  in that release's code, as an online install checks them.
- **Files the house added itself**, such as a model it chose or a wake
  word it trained, are marked "not checked against a release". So is the
  MPD image, which the server builds itself.

A stick someone changed could also carry a changed checker or installer.
Use a kit from a house you trust. The dashboard and START-HERE show each
installer's SHA-256, so you can compare it with the release page from any
phone.

## Don't sell the stick

Some files on a kit, such as the Domovoi wake word (CC BY-NC-SA 4.0), may
be shared only for free. Making a kit for yourself or giving one away is
fine; selling one is not. `kit.json` and `licences/` carry the notices,
including "Built with Llama" when a Llama model is on the stick.

## The format, for developers

The single source of truth is `domovoi/offline_kit/format.py`; this is a
summary.

**kit.json** names the kit and every file on it:

| Field | What it is |
|---|---|
| `kit_format` | `1`. It changes only when an older Domovoi would read a kit wrong; a newer number is refused. New fields don't change it: unknown fields are ignored (and noted), so a server can read a newer release's kit to update from it. |
| `domovoi_version`, `tag`, `commit` | The release on the stick: always the release the making server runs. |
| `created_at` | When the kit was made, in UTC (`2026-11-22T10:15:00Z`). |
| `targets` | `pc-x86_64` (Windows or Linux PC), `mac-arm64` (Mac). |
| `includes` | The card's tick boxes: `ai`, `vision`, `rooms`. |
| `android_version_code` | The Android app on the stick, or null. |
| `part_size` | Where big files are split (below). |
| `notices` | `{id, text}`: `do-not-sell` always; `wake-word-nc-sa`, `llama` and `household-added` when the files call for them. |
| `components` | What is on the stick (below). |

Each **component** has a `kind` (which fixes its folder, for example
`ollama-model` lives in `ollama/models/`), a `name`, a `version`, a
`licence` (from an allow-list in the code; licences that forbid passing
files on are refused), `household_added` (true for a model or wake word
the house added itself; it then has no pin), `pinned_by` (which pin in the
release checks it, or null) and its `files`. Each file has a `path`,
`size` and `sha256`. Paths use `/`, are relative to the stick, and only
use names FAT32 can hold. A file two components share (an Ollama blob two
models use) is stored once.

**Split files.** FAT32 can't hold a file of 4 GiB or more. A file bigger
than `part_size` (3,900,000,000 bytes) is stored only as parts:
`<path>.part01`, `<path>.part02`, and so on (three digits from 100 parts
up). Every part but the last is exactly `part_size` bytes. Joined in
order, the parts are the file `kit.json` names, with its size and SHA-256;
each part has its own size and SHA-256 too, so a damaged part is named.

**SHA256SUMS** is `sha256sum`'s format (`<sha256>  <path>`), sorted by
path: every file as stored (parts, not joined files) and `kit.json`
itself.

`domovoi.offline_kit.write_kit` writes a kit folder from a list of
components (it splits files, fills in the notices and writes `kit.json`
and `SHA256SUMS`); `reassemble` joins a split file back, checking it as
it goes.
