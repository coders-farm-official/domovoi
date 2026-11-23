# The offline kit

An online Domovoi server can write an **offline kit** to a USB stick. A
house that has never been online sets up Domovoi from that stick, and
later updates from a newer one. The stick holds everything an install
would otherwise download: the installers, the models, the phone app, the
packages.

> **Coming in 1.5.0, being built.** This page describes the kit's format,
> how to check a stick, how a server makes a kit and how a Linux computer
> (Ubuntu 26.04) and a Windows 11 PC install from one, and how a server
> updates from a newer one. The Mac installing
> from a kit arrives in a later step of 1.5.0; this page grows with it.

## Making a kit

On a Domovoi server that is on the stable channel and runs a release:
**Settings → System → Offline kit** (admins only).

1. Tick what the stick is for: **A Windows or Linux PC** (ticked) and
   **A Mac with an Apple chip** (not ticked; it adds about 2.4 GB, and
   Ollama for macOS with AI).
2. Tick what to include: **AI chat and questions**, **Pictures in chat**,
   **Rooms: Raspberry Pis and spare computers**. Models and wake words the
   house added itself are listed too, unticked and marked "not checked
   against a release". Models whose licence doesn't allow passing them on
   (Gemma, Qwen2.5 3B, Llama 3.2 Vision, any model of unknown licence) are
   listed as never going on a kit.
3. Pick your stick's size. The card says the kit's size, the smallest
   stick that holds it and, when yours is too small, by how much and what
   to leave out. A "16 GB" stick holds about 14.9 GB of kit.
4. Press **Make the kit**. One bar shows the progress: first what the
   server has to download, then the kit itself, then a check of the
   image. It goes on if you close the page; **stop** throws away what it
   made.
5. When it is ready: **Open in Raspberry Pi Imager** (or download the
   image and write it with Imager or USBImager). Everything on the stick
   is replaced. Then **delete the kit** on the server. It goes by itself
   after 3 days, and when the next kit starts. A kit with household items
   ticked downloads only from the card, signed in as admin (its link
   works once, for 5 minutes): save the image, then choose it in Imager
   under **Use custom**.

The card's reasons when it can't make a kit:

| The card says | Why |
|---|---|
| Offline kits are made from a release. This server follows main. | A kit is always the release the server runs. Switch to stable in About & updates. |
| Domovoi 1.5.1 is out. Update this server first… | A warning only: **make it anyway** puts the server's own release on the stick. |
| This server has no internet, so it can't fetch: … | Internet access is "No" and the server lacks those parts. Untick them, or allow internet while the kit is made. |
| Needs 15 GB free on the server. | The image and the downloads need that much disk. |
| …needs the dosfstools and mtools packages | The image is made without root by `mkfs.fat` and `mcopy`: `sudo apt install dosfstools mtools`. |
| …Docker uses its older image store… | See "Images" below. |

On a Windows server the WSL disk grows by the image's size, and on a Mac
the VM's disk does, and neither shrinks by itself: delete the kit once
the stick is written.

Sizes measured on 2026-11-22 for 1.4.0's pins (the card's numbers are
exact once the server has fetched each part once):

| Kit | Size | Stick |
|---|---|---|
| PC, no AI | about 3.3 GB | 8 GB |
| PC, AI + pictures + rooms | about 12.0 GB | 16 GB |
| PC + Mac, AI + pictures + rooms | about 14.4 GB | 16 GB |

The biggest parts: the floor tier's models (qwen3 4B 2.5 GB, llama3.2 3B
2.0 GB, qwen3-vl 2B 1.9 GB), Ollama for Linux (1.4 GB), Debian's
installer for the room stick (0.8 GB), the Ubuntu and Docker packages
(0.53 GB), Whisper (0.48 GB). The standard tier's model (qwen3 8B, 5.2 GB)
goes on only when the server has it.

### Where each part comes from

The server copies what it already has and downloads the rest, each file
held to the pin in the release's own commit (read with `git show`), and
every download goes through the internet setting (Settings → Privacy →
Internet). Downloads land in `~/.domovoi/offline_kit/cache/` and the next
kit reuses them; the kit images are in `~/.domovoi/offline_kit/builds/`.

- **AI models:** from this server's Ollama folder when the core can read
  it, else from Ollama's registry: the manifest held to the digest in
  `provision-manifest.conf`, each blob to the digest the manifest names.
  On a Mac server the core reads the Mac's models through a read-only
  mount named in `DOMOVOI_KIT_OLLAMA_MODELS` (and whisper.cpp's model in
  `DOMOVOI_KIT_MAC_STT`) when the Mac's setup provides one, else it
  downloads them again by pin.
- **Ubuntu and Docker packages:** apt works out what an install needs, in
  a clean `ubuntu:26.04` container (an ordinary `docker run`, not
  privileged): `APT_PACKAGES`, the pinned docker-ce, nftables, dosfstools,
  mtools and the NVIDIA container toolkit, recommends included. A real
  Ubuntu has more installed than the container, so the set errs on the
  side of more. The server keeps the mirror for 14 days.
- **Python packages:** for each package in both locks, the file PyPI lists
  whose SHA-256 the lock names, for an x86_64 PC (and an aarch64 Mac VM).
- **Images:** `docker save` of the compose images (not SearXNG) and this
  server's music player image.
- **The rest** (installers, Ollama, Whisper, the voices, the wake words,
  the phone app, WSL, Ubuntu's images, the Mac parts): the release's own
  files (its signed manifests checked with the release key) and the
  pinned upstream files.

Never on a kit: anything of the household's (database, `.env`, setup
code, identity, names, media, voice profiles, room codes), SearXNG,
CUDA wheels, the wake-word trainer's data, plugins.

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

## Installing from a kit

**Linux (Ubuntu 26.04).** Install Ubuntu 26.04 first, plug in the kit and
run `sudo bash /media/alex/DOMOVOIKIT/linux/install.sh --kit`. It checks
the stick (checksums, the release signature, the source at the signed
commit, then every file with the checker from that commit) before it runs
anything from it, and the install then checks every file against the same
pins as an online install. Nothing is downloaded.
[LINUX_HOST.md, Installing without internet](LINUX_HOST.md#installing-without-internet)
has the steps and what each check proves. Other Linux systems install
with internet.

**Windows 11.** Open the stick and double-click `release\DomovoiSetup.exe`.
Setup sees the kit beside it and installs from it, with no internet: it
checks the stick (checksums, the release signature, that the kit is the
Setup's own release, the parts and the disk) before it changes anything,
takes WSL and Ubuntu's image from `windows\` and `provision.sh` from
`linux\`, each against Setup's own pins, and opens the stick read-only
inside Domovoi's Linux system for `provision.sh --offline-kit`. Keep the
stick in until Setup finishes.
[WINDOWS_HOST.md, Installing without internet](WINDOWS_HOST.md#installing-without-internet)
has the steps, the checks and the refusals. The Mac follows in a later
step of 1.5.0.

## Updating from a kit

A house with no internet updates from a newer kit. On the server's
dashboard, as the admin: **Settings → System → About & updates → Update
from an offline kit**.

1. Plug the newer kit's stick into the computer the browser runs on, and
   press **choose the kit folder**. Choose the stick itself (`DOMOVOIKIT`,
   the folder with `kit.json` in it).
2. The server reads `kit.json` first and says plainly when it can't use
   the kit:

   | The card says | Why |
   |---|---|
   | This kit is 1.5.0; this server already runs 1.5.0. | A kit only updates to a newer release; it never downgrades. |
   | This kit is for a Mac with an Apple chip; this server is a Windows or Linux PC… | The kit wasn't made for this server's machine. Make one with that box ticked. |
   | This update needs 3.1 GB free on the server. It has 1.2 GB. | The files to come, the copy the update makes of what it installs, and a copy of the Python packages to roll back to. |
   | Updates from a kit follow the stable channel… | Switch to stable in About & updates. |
   | …its update service isn't installed. | Linux: `sudo bash scripts/linux/install-update-unit.sh`. |
   | This kit is not a Domovoi release… | The release's signed manifest doesn't verify with Domovoi's release key. Nothing changes; what was sent is removed. |
   | …doesn't match the kit… thrown away | A file's SHA-256 isn't the one `kit.json` names: a bad copy or a failing stick. Send it again, or make the stick again. |

3. The page sends only what the server lacks: the release's signed
   manifest and source first, then the Python packages it doesn't have at
   the kit's versions, the container images Docker doesn't hold by their
   pinned digests, the music player image, Ubuntu's packages when one the
   update installs is missing, the phone app when the kit's is newer,
   and, from a kit made with Rooms ticked, the rooms' files (`rooms/`,
   with the release's `SHA256SUMS` for the Windows room Setup) the server
   doesn't already hold. A typical update sends 0.5 to 1.5 GB. If the page is closed or the
   connection drops, choose the folder again: it picks up where it
   stopped.
4. Press **update now**. The server checks the release signature again,
   takes the kit's release out of `source/domovoi.bundle` (it must be the
   signed manifest's commit, built on the release the server runs, and
   signed by an allowed signer while signed updates are enforced), moves
   the checkout to it and starts the update, as **Restart** does after a
   pull. The update installs the Python packages from the hash-pinned
   lock with no package index, loads the images (each held to the digest
   the compose file pins) and installs Ubuntu's packages from the kit's
   copy of Ubuntu's signed archive. Nothing comes from the internet, so it
   works with internet access set to **No**.
5. After a good update the rooms' files go where the server serves them,
   each held to the new release's pins as at install ("Adding rooms with
   no internet" below; a kit without `rooms/` leaves them as they were,
   and the update's log says so), the phone app from the kit goes in
   (checked like an admin's upload of the APKs) and the uploaded copy is
   removed from the server. If the update fails it is rolled back as usual, including the
   Python packages (from a copy made before the update) and the music
   player image; the uploaded files stay for another try, and **cancel**
   removes them.

Models, voices and the other files an install copies are not part of an
update: an update with internet doesn't download them either.

The upload lives in `~/.domovoi/offline_kit/update/` of the service user
(`files/` in the kit's layout, `session.json`, and `request`, which names
the commit for the update unit). The update unit copies what it installs
into its own folder before using it, and removes that copy when it ends.
An administrator who moved the checkout to the kit's release by hand can
run one update from a stick: `sudo systemctl set-environment
DOMOVOI_OFFLINE_SOURCE=/media/alex/DOMOVOIKIT`, `sudo systemctl start
domovoi-update.service`, then `sudo systemctl unset-environment
DOMOVOI_OFFLINE_SOURCE`. A split file on the stick is not joined that
way; the dashboard's upload joins them.

## Adding rooms with no internet

Tick **Rooms: Raspberry Pis and spare computers** when you make the kit.
The installer then puts the rooms' files on the new server by itself
(you'll see "Copying the phone app and the rooms' files from the offline
kit"), each checked against the pins of the release, and you add rooms as
usual: **Settings → Household → Rooms**. With internet access set to
"No", every room below installs from the server alone.

| Room | What it takes from the server | No internet? |
|---|---|---|
| A Raspberry Pi | its card, prepared on the dashboard: the Pi's Python packages, its system packages and every wake-word model | Yes |
| A Windows PC | `DomovoiSatelliteSetup.exe`, then its Python and packages | Yes |
| A Linux computer | its packages (and, on older Linux, its own Python) | Yes, when it already has its sound packages (PortAudio, mpg123, the ALSA utilities; most desktops do) |
| An old computer, from the room stick | Debian's installer image (the server builds the stick from it) | No: Debian installs over the network, so that room needs internet while it sets up |
| A phone in room mode | the app, from the join page | Yes |

What the stick carries for rooms (`rooms/`), and what holds each file:

| On the stick | Held to |
|---|---|
| `rooms/DomovoiSatelliteSetup.exe` | the release's `SHA256SUMS` (`release/SHA256SUMS`, as online: the Setup isn't code-signed yet) |
| `rooms/windows/<python version>/` | `satellite/windows-runtime.json` |
| `rooms/linux-python/` | `satellite/locks/linux-python.txt` |
| `rooms/linux-wheels/` | a hash `satellite/locks/pc-*.txt` lists for that package |
| `rooms/satellite/oww_models/` (and `wake/`) | `OWW_MODEL_FILES` in `domovoi/satellite_media/fetchers.py` |
| `rooms/<Debian's netinst>.iso` | `domovoi/usb_stick/debian-image.json` |
| `rooms/satellite/wheels/`, `debs/`, `dtbo/`, `xvf_host/` | nothing: Pi OS's packages and the satellite's libraries have no pin in a release, so they are "from the house that made this kit" |

The server that makes the kit fetches what it lacks first (the Windows
files, the Linux Python, the Linux packages from PyPI by the locks'
hashes, the Pi's packages the way preparing a card fetches them). Under
internet access "No" the card says "This server has no internet, so it
can't fetch: the rooms' parts" instead.

A server installed from a kit **without** Rooms can't add a Windows,
Linux or Pi room until it has those files. Where the dashboard would
otherwise say only "internet access is off", it says so: "This server was
set up from an offline kit (Domovoi 1.5.0) made without Rooms ticked, so
it has no …". Update it from a kit made with Rooms ticked, or allow
internet for a while. A file on the stick that doesn't match its pin is
refused and named in the install's log; the rest still go in.

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

### The folders, in detail (for installers)

- `apt/<arch>/ubuntu/`, `apt/<arch>/docker/`, `apt/amd64/nvidia/`: real
  repositories laid out from apt's own download. Ubuntu's
  `dists/<suite>/InRelease` and
  `dists/<suite>/<component>/binary-<arch>/Packages` for `resolute`,
  `resolute-updates`, `resolute-security` and `resolute-backports` (all
  four components), Docker's `dists/resolute/InRelease` and
  `stable/binary-<arch>/Packages`, NVIDIA's flat `InRelease` and
  `Packages`, and each `.deb` at the `Filename` its index gives. The
  `Packages` files are uncompressed (each `InRelease` lists that hash
  too). `<arch>` is `amd64`, and `arm64` (from ports.ubuntu.com) on a kit
  for a Mac. A source such as `deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] file:/media/alex/DOMOVOIKIT/apt/amd64/ubuntu resolute main universe`
  works; apt warns once per index that it found no `.xz` and then uses the
  uncompressed file. Tried on 2026-11-22: an `ubuntu:26.04` container with
  no network installed `APT_PACKAGES`, the pinned docker-ce and the NVIDIA
  toolkit from such a mirror. The install also needs Docker's key file as
  Docker serves it (`docker_key_url`), at `apt/<arch>/docker/gpg`: it
  checks its fingerprint against `provision-manifest.conf`, as online, and
  only then lets apt trust Docker's `InRelease` (NVIDIA's likewise at
  `apt/amd64/nvidia/gpg`, when the GPU runtime comes from a kit).
- `python/cp314-x86_64/`, `python/cp314-aarch64/`: one file per locked
  package (both locks), for `pip install --no-index --find-links …
  --require-hashes`. A package with no wheel for that machine is its
  sdist (today only Metaphone).
- `images/<repo with / as _>_<tag>.tar`: `docker save` of each image under
  its compose name (for example `images/postgres_16.15-trixie.tar`); a kit
  for a Mac holds the arm64 blobs in the same archive. The music player
  image is `images/<MPD_IMAGE_TAG as a name>.tar`, not pinned.
- `ollama/ollama-linux-amd64.tar.zst`; `ollama/models/` in Ollama's own
  layout (`manifests/registry.ollama.ai/library/<name>/<tag>`,
  `blobs/sha256-<hex>`, split when bigger than `part_size`).
- `speech/whisper-small.en/` (the pinned snapshot's files),
  `speech/piper/<voice>.onnx` and `.onnx.json`, `speech/speaker/`.
- `wake/domovoi_v1.onnx` and its notice, openWakeWord's
  `embedding_model.onnx`, `melspectrogram.onnx`, `hey_jarvis_v0.1.onnx`,
  `silero_vad.onnx`; household words in `wake/household/`.
- `phone/<apk>`s and `phone/release.json` (`android/release.json` of the
  release).
- `linux/install.sh` and `linux/provision.sh` (both at the release
  commit; Windows Setup checks `provision.sh` against its own pin and runs
  it in the distro), `source/domovoi.bundle`
  (refs: the release tag and HEAD, both at the release commit).
- `windows/` (the WSL MSI and Ubuntu's WSL image, each under its name in
  the release's `windows/installer/pins.iss`, which is where Windows Setup
  looks), `mac/` (Ollama for macOS,
  Ubuntu's arm64 cloud image, whisper.cpp's model).
- `rooms/`: `DomovoiSatelliteSetup.exe`, Debian's netinst,
  `windows/<python version>/` (every file of
  `satellite/windows-runtime.json`), `linux-python/` (every file of
  `satellite/locks/linux-python.txt`), `linux-wheels/` (one wheel per
  package of each `satellite/locks/pc-*.txt`, the oldest manylinux build
  first; a file two locks share once), and `satellite/` in the layout of
  the server's `~/.domovoi/satellite_media/cache/`: `oww_models/` (the
  pinned openWakeWord files `wake/` doesn't already carry), `wheels/3.13/`,
  `debs/trixie/`, `dtbo/`, `xvf_host/`. An install puts each into that
  cache with `python -m domovoi.kit_rooms install` (provision.sh does it)
  and records it in `~/.domovoi/offline_kit/rooms-from-kit.json`.
- `licences/`: Domovoi's licence, `LICENSES.md`, the Apache-2.0 and
  CC BY-NC-SA 4.0 texts, the release's SBOMs and a README tying them
  together (with "Built with Llama" when a Llama model is on the stick).

### Images

Compose pins each image by digest, and an offline install must find it by
that digest after `docker load`. Measured on 2026-11-22 with the pinned
Docker Engine (29.8.1):

- With Docker's **containerd image store** (the default on a new Docker
  29 install, which is what provision.sh makes), an image saved **under
  its name** (`docker save postgres:16.15-trixie`) loads back with its
  index digest: `postgres:16.15-trixie@sha256:…` is found and runs with
  `--pull=never`.
- Saved by its digest alone, it loads as a nameless image and the digest
  reference doesn't find it. Saved with `--platform`, the archive holds
  the platform's manifest instead of the index, and the digest is lost
  too.
- With the **older image store** (overlay2 without the containerd
  snapshotter: an install upgraded from an older Docker), the digest is
  lost whatever you do, and a containerd store that loads that archive
  doesn't get it back either.

So the server saves each image under its compose name, without
`--platform`, checks that the archive's `index.json` names the pinned
digest, and refuses to make a kit on a server whose Docker uses the older
store.

`scripts/dev/make_test_kit.py` makes a kit in this layout from the real
pinned files, signed with a throwaway key, for testing the install
(`scripts/dev/linux-install-vm.sh --offline-kit` runs one in a VM with no
internet).
