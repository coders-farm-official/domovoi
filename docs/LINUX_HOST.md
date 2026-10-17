# Running the Domovoi server on Linux

Linux and Windows are both supported server hosts. Windows earned its
first-class status honestly — for many households the gaming PC is the best
AI hardware in the house — but that case rests almost entirely on having an
NVIDIA GPU in it.

On a dedicated always-on box, especially one without NVIDIA, **Linux is the
better host** and it's where the project is heading. This page covers what
actually differs. Nothing here is a workaround for a Windows assumption;
the platform-specific paths are guarded and both are maintained.

Read alongside: [Setup runbook](SETUP_RUNBOOK.md) (the ordered bring-up) ·
[Running without an NVIDIA GPU](CPU_HOST.md) (model and device settings)

---

## What gets better

The Windows-first case rests almost entirely on CUDA. Take CUDA away and
the ledger flips:

| | On Windows | On Linux |
|---|---|---|
| **CUDA DLL preloading** | `domovoi/bootstrap.py` preloads cuBLAS/cuDNN by hand because the Windows loader ignores `add_dll_directory()` for dependent DLLs. The plugin loader asserts it ran. | The whole module is `if sys.platform != "win32": return ()`. Dead code. It's the single biggest piece of Windows-specific engineering in the repo, and you skip it. |
| **Wake-word training** | openWakeWord's training pipeline (piper-sample-generator) is Linux-only, so it runs in a Linux container under Docker Desktop. | On the server itself, in a container on its own Docker Engine, switched on from the dashboard (Settings → Wake Words); an NVIDIA GPU gets its container runtime from the install and the update unit. See [Wake-word training](#wake-word-training). |
| **External binaries** (`ffmpeg`, `fpcalc`/Chromaprint, `mpg123`) | Download, unzip, put on PATH by hand. | `apt install`. |
| **`faster-whisper` and `resemblyzer` install** | `pip install --no-deps -r requirements-no-deps.in`: without the dependencies they declare (PyAV, librosa, webrtcvad), which Domovoi doesn't use. | The same, from the hash-pinned `requirements-linux-py314-no-deps.lock`. |
| **Docker** | Docker Desktop — a whole application, its own RAM overhead, its own licensing question. | Native Docker Engine. The core drives containers through the `docker` CLI (`mpd_provisioner.py`), so nothing else changes. |
| **Autostart** | Scheduled task at boot, or a service wrapper. Fiddly. | systemd. Units below. |
| **Ollama on an AMD/Intel iGPU** | ROCm support on Windows is thin. | Better supported, if you ever want to experiment. Still optional — see [CPU_HOST.md](CPU_HOST.md#what-about-the-integrated-gpu). |

## What to know going in

Four differences. Two need something from you; two are already handled
in-repo and are listed so you know why you don't have to think about
them.

### 1. The system-voice fallback is optional on Linux

TTS falls back through `piper → system`. The `system` rung is the
OS's own synthesizer — pyttsx3/SAPI on Windows, `say` on macOS, and on
Linux **`espeak-ng` if you install it**. It's robotic, and it is only the
floor beneath the Piper voice for when that voice is missing or broken.

Domovoi no longer installs `espeak-ng` (since 2026-10): its voices are
spoken by its own text-to-phoneme converter on ONNX Runtime, with no
espeak-ng anywhere, and espeak-ng is GPL. Without a system synthesizer
the chain simply ends at Piper. If you want the floor anyway:

```bash
sudo apt install -y espeak-ng
```

(`espeak` works too if that's what your distro carries.) What matters
more is that the Piper voices are on disk: the provisioning script fetches
all three, and the core fetches a missing one on first use.

### 2. USB satellite adoption needs auto-mounting

The dashboard's adopt-a-satellite-over-USB flow scans for removable
drives. There *is* a real Linux implementation —
`_removable_linux()` in `web/backend/api/files_security.py` parses
`/proc/mounts` and checks `/sys/block/<dev>/removable` — but it only sees
volumes that are **already mounted**. Windows assigns a drive letter
automatically; a headless Ubuntu Server does not mount USB storage at all.

Two smaller degradations in the same flow: the FAT volume-label
pre-filter is skipped (`_volume_label()` returns `None`, and the check is
written so that falls through safely rather than rejecting the drive), and
the Windows raw-volume flush after writing `provision.json` no-ops — but
the write does `fsync` + atomic `os.replace` + a directory `fsync`, so the
rename is durable on POSIX even if the card is yanked immediately.

If you want to try this flow, install `udisks2` and an auto-mount helper,
or point the scanner at a fixed mountpoint:

```bash
export SATELLITE_ADOPTION_SCAN_DIRS=/media/domovoi/setup
```

**My advice: don't start here.** This flow is [already unvalidated on real
hardware](HARDWARE_VALIDATION.md) on its best-supported platform. On Linux
you'd be debugging two unknowns at once. Use the manual path in
[`satellite/PROVISIONING.md`](../satellite/PROVISIONING.md) — it's proven,
and it's the same amount of work the second time you do it.

### 2b. Restart-from-the-dashboard needs one sudoers line

The version panel's **Restart to apply changes** button bounces both
services so pulled code actually loads, and **Restart Domovoi** (an admin's,
whenever nothing is waiting to load) bounces them so a hand edit of
`domovoi/.env` or a restart-tier setting takes effect. The service user
can't do that unaided, so grant exactly that one command — the same
single-command pattern the satellite uses for its own self-restart
([`PROVISIONING.md`](../satellite/PROVISIONING.md) §8.1). Without the grant
the panel shows the command to run instead.

> **Prefer the update unit.** A bare bounce loads new code but does not
> install new dependencies, run new migrations or rebuild the MPD image.
> With `domovoi-update.service` installed ([Updates from the
> dashboard](#updates-from-the-dashboard)) the same button does all of
> that, health-checks the result and rolls back if it fails. It needs its
> own one-line grant, shown there. What follows is the plain bounce, which
> is what the button does on a host without the unit.

```bash
sudo visudo -f /etc/sudoers.d/domovoi-restart
```

```
domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-core.service domovoi-web.service
```

Replace `domovoi` with the `User=` from your unit files. Verify without
actually restarting anything:

```bash
sudo -n -l /usr/bin/systemctl --no-block restart domovoi-core.service domovoi-web.service
```

That's the same probe the server runs to decide whether to offer the
button; `domovoi/self_restart.py` reports `restart_capable` from it. Skip
this and nothing breaks — the panel shows the manual `systemctl` command
instead of a button, which is exactly the pre-existing behaviour.

> Both units are bounced together on purpose: a pull moves the whole
> checkout, and core and web import from the same tree, so restarting one
> would leave the other serving stale code — the confusion the version
> panel exists to end.

### 3. `host.docker.internal` doesn't exist (handled)

Chat mode's Letta container reaches the host's Ollama at
`host.docker.internal`, which Docker Desktop provides and native Docker
Engine does not. Affects `OLLAMA_BASE_URL` in the `letta` service and the
`letta_tool_callback_url` setting.

**This is now handled in-repo** — the `letta` service in
`domovoi/docker-compose.yml` maps the name explicitly:

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Docker Desktop provides the name natively and ignores the mapping, so the
one compose file works on both platforms. Nothing to do; noted here because
any *new* container that needs to reach the host wants the same two lines.

### 4. Cosmetic: no GPU readout (nothing to do)

The dashboard's Models page reads GPU stats from `nvidia-smi`. It's
guarded by `shutil.which()`, so it degrades cleanly to blank rather than
erroring. CPU, RAM, and disk still come through via `psutil`.

---

## Which distro

**Ubuntu Server 26.04 LTS** (verified against this stack, August 2026).
Any current LTS works; the section below is really about *how to check*,
because the answer changes with time.

### The thing that actually decides it

Ubuntu's system Python moves with the release — 24.04 ships 3.12, 26.04
ships **3.14** (`python3 3.14.3-0ubuntu2`). That matters because this stack
leans on compiled packages that ship wheels *per Python version*, and a
brand-new CPython can go months without them. When a wheel is missing, pip
falls back to building from source and you lose an evening to a compiler
error that has nothing to do with Domovoi.

So the rule is: **before committing to a release, check that this repo's
compiled dependencies publish wheels for the Python it ships.** The
long poles are `torch` and `ctranslate2` (the engine under
`faster-whisper`); `numba`, the slowest of all to land, left with
`librosa` on 2026-10-15, and the check below still lists it for older
checkouts.

```bash
python3 -c "import json,urllib.request as u,re; [print(p, sorted({m.group(1) for f in json.load(u.urlopen(f'https://pypi.org/pypi/{p}/json'))['urls'] for m in [re.search(r'-(cp3\d+)-',f['filename'])] if m and 'manylinux' in f['filename'] and 'x86_64' in f['filename']})) for p in ('numba','torch','ctranslate2','shazamio-core')]"
```

If your target Python's tag is in all three lists, you're clear.

### Where that stood in August 2026

Every compiled dependency had cp314 manylinux x86_64 wheels: `torch`
2.13.0, `ctranslate2` 4.8.2, `numba` 0.67.0, `numpy` 2.5.2, `scipy`
1.18.1, `asyncpg` 0.31.0, plus `pillow`, `psutil`, `sqlalchemy`,
`miniaudio` and `lameenc` (until 2026-10), `soxr`, `llvmlite`. `piper-tts` (the voice engine
until 2026-10) shipped `cp39-abi3`; its replacement, `onnxruntime`, has
cp314 wheels. The NVIDIA
CUDA wheels are `py3-none-manylinux` — no Python tag at all.

Two dependencies still compile from source, and both are worth knowing
about because **neither shows up if you only check the packages named in
`pyproject.toml`** — they are transitive:

- **`webrtcvad`** (via `resemblyzer`) — sdist-only on *every* Python
  version. Since 2026-10-15 it isn't installed: `resemblyzer` goes in
  without its dependencies (`requirements-no-deps.in`).
- **`shazamio-core`** (via `shazamio`, the library-enricher fallback) — a
  Rust/pyo3 extension binding ALSA. `shazamio` itself is pure Python, which
  makes this easy to miss. When its prebuilt wheel is absent for your
  Python, pip compiles it, and that needs `pkg-config` + `libasound2-dev`
  from the prerequisites above plus a Rust toolchain (pip fetches one
  automatically).

The lesson generalizes: when checking wheel coverage for a new Python,
walk the *transitive* compiled dependencies, not just the direct ones. A
pure-Python wrapper can hide a Rust extension.

Docker publishes native `resolute` packages, and Ollama's installer
branches on kernel and architecture rather than Ubuntu release.

### If the wheels aren't there yet

Don't fight the system Python — sidestep it:

```bash
uv venv --python 3.13 .venv
```

`uv` (or deadsnakes) installs its own interpreter, so the distro's version
stops mattering and the systemd units below work unchanged — they already
point at the venv's `python`. This is also the escape hatch if a future
release jumps to a Python the ecosystem hasn't caught up with.

Picking an older LTS isn't permanent either: LTS-to-LTS
`do-release-upgrade` opens once the newer release reaches its `.1` point
release.

Use **Server, not Desktop.** A desktop environment costs you 1–2 GB of RAM
that models want, and the server has no screen. (The one thing Desktop
would buy you is USB auto-mounting for the adoption flow — see above, and
you're not using that.)

---

## Install

### Or let the script do it

On a fresh Ubuntu 26.04 box, `scripts/linux/provision.sh` runs this
section, [Make it an appliance](#make-it-an-appliance) and the
[update unit](#one-time-upgrade-for-existing-installs) for you,
unattended. It is the same script the Windows installer runs inside its
WSL distro. Read it, or ask it what it would do, before it changes
anything:

```bash
git clone https://github.com/coders-farm-official/domovoi /tmp/domovoi
sudo bash /tmp/domovoi/scripts/linux/provision.sh --dry-run --internet always --timezone America/New_York
sudo bash /tmp/domovoi/scripts/linux/provision.sh --internet always --timezone America/New_York
```

It clones the repository again into `/opt/domovoi` as the `domovoi` user
(an account it creates with no password login; an existing `--user` is
used as it is, and one in `sudo`, `admin` or `wheel` is refused), at the
latest release, and the box then follows the `stable` channel: releases
only ([Release channels](#release-channels)). Before the repository's first
release there is no `stable` branch yet; the script then installs `main`,
follows it, and says so, and the Version card says when the first release
is out. `--ref` picks something else: a branch (`--ref main`), a release
tag (`--ref v1.0.0`, read as a tag only, never a branch of that name; any
other name is the tag of that name when there is one, `stable` and `main`
always the branches) or a full commit SHA (that commit only); with a tag or a SHA, `--track-branch`
names the channel it follows (default `stable`). After the checkout, HEAD
must be the commit `--ref` named, or nothing is installed. On top
of the manual steps it pre-downloads what the core would otherwise fetch
at its first boot: the Ollama models for the box's memory (`--tier`), the
Whisper model, the Piper voice and the room image. The models include the
vision model that reads pictures in chat (`qwen3-vl:2b-instruct-q4_K_M` on
both tiers, about 1.9 GB more to download); `--no-vision-model` leaves it
out, and chat then says it isn't installed until an admin installs it from
Settings → Models. What it checks:

- **Against a pin, stopping on a mismatch:** every Python package (the
  lock's hashes), the Ollama release pinned under [By hand](#by-hand)
  below (SHA-256), Docker's apt key (the fingerprint in
  `scripts/linux/provision-manifest.conf`; that key alone is installed,
  and apt checks the docker-ce packages against the index it signs),
  NVIDIA's apt key on a box with an NVIDIA GPU (checked the same way, for
  the NVIDIA Container Toolkit), the compose images and the room image's
  base (digests), each Ollama model (the manifest's digests), Whisper's
  `model.bin` and the Piper voice (SHA-256).
- **Not hashed:** Ubuntu's own packages (apt checks them against Ubuntu's
  signed archive, at whatever version it serves that day), what
  `domovoi/Dockerfile.mpd` installs with apt inside the room image, and
  Whisper's small files beside `model.bin` (`config.json`,
  `tokenizer.json`, `vocabulary.*`), which come from the pinned Hugging
  Face revision.
- **Later:** docker-ce goes in at the manifest's versions on a box that
  has none yet and is not held, so `apt upgrade` moves it like any other
  package; so does the NVIDIA Container Toolkit. The box's updates install
  from the lock too: the script puts
  `DOMOVOI_USE_LOCK=1` in `/etc/default/domovoi-update` (unless that file
  already sets it), so an update that changes the dependencies re-syncs
  the venv hash-checked, the way it was installed
  ([The hash-pinned lock](#the-hash-pinned-lock)).

What else it settles, beyond the manual steps:

- **GPU.** Ollama runs on every GPU it finds, and on the CPU without one;
  the script pins nothing (no `CUDA_VISIBLE_DEVICES`). Inside the Windows
  installer's WSL distro that includes the PC's NVIDIA cards, which WSL
  hands through. The model tier still follows the box's memory alone, the
  vision model's included: on a box with an NVIDIA GPU, pick `qwen2.5vl:7b`
  for the Vision row in Settings → Models afterwards.
  The script's last progress line lists what Ollama found (`"gpu"`, empty
  on a CPU-only box), and its log says it in words.
  Where an NVIDIA GPU is visible, the `gpu-runtime` step gives Docker its
  way to it, the NVIDIA Container Toolkit, for the wake-word trainer's
  container ([Wake-word training](#wake-word-training)); a box without one
  skips the step.
- **Time zone.** On bare metal, `--timezone` sets it (`timedatectl`, and
  `/etc/timezone`, which the core reads first). In WSL the distro's zone is
  Windows': WSL rewrites `/etc/localtime` and `/etc/timezone` from it at
  every distro start, so `--timezone` is ignored there with a warning; the
  script only checks that the two files name the same zone, and if they
  don't (someone ran `timedatectl` inside the distro) rewrites
  `/etc/timezone` to match and restarts core and web. Change the zone in
  Windows' settings; it applies at the distro's next start.
- **Ubuntu's own background jobs** follow the internet answer: see
  [Internet or not](#internet-or-not). On WSL, `wsl-pro.service` and snapd
  are turned off whatever the answer (Domovoi uses neither); on bare metal
  they are left as they are.
- **Media folders.** `--media music=...` (and `pictures`, `documents`,
  `audiobooks`, `podcasts`, `videos`) sets the matching `MUSIC_DIR`, ...,
  `VIDEOS_DIR`.

Run it again at any time: finished steps are skipped, a killed run picks
up where it stopped (a clone cut short is made again), and nothing is
restarted that didn't change. A re-run never undoes your own settings: in
`domovoi/.env` it sets a key only if it is missing or still holds what
the script last wrote there, and it warns about each one it leaves alone.
A media folder it mounted before that is missing now (an unplugged drive)
is a warning, not a failure. `--help` lists the options (`--media`,
`--tier`, `--skip-models`, ...) and the exit codes.

The steps below are the same install by hand, without the pre-downloads.
Follow them instead if you prefer, or to see what a step changed.

### By hand

Prerequisites:

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-dev build-essential pkg-config libasound2-dev git ffmpeg mpg123 libchromaprint-tools xorriso
```

`xorriso` is what the core runs to make an old computer's room stick
(Settings → Satellites → add a spare device → old computer; a GPL program
run on its own, never bundled with Domovoi). `libchromaprint-tools`
provides `fpcalc` for library enrichment, and
`python3-dev` + `build-essential` are what let the sdist-only pins
(`metaphone`) compile. (No
`espeak-ng`: see [the system-voice fallback](#1-the-system-voice-fallback-is-optional-on-linux).)

`pkg-config` and `libasound2-dev` are there for **`shazamio-core`** — a
Rust/pyo3 extension that binds ALSA. It's a transitive dependency of
`shazamio` (the library-enricher fallback), and on any Python new enough
to be missing its prebuilt wheel, pip compiles it from source. Without
these two the build dies with *"The pkg-config command could not be
found"* and takes the whole install down with it.

Docker Engine — follow Docker's official apt-repository instructions for
your release, then:

```bash
sudo usermod -aG docker $USER
```

**This one matters.** The core shells out to the `docker` CLI to build and
run each room's MPD container. Without group membership it would need
sudo, which it won't do. Log out and back in, then confirm with `docker ps`
that you get output rather than a permission error.

> **The `docker` group is root.** Anyone in it can start a container that
> mounts the host's `/` and change anything on the box as root; Docker's
> own documentation says the same. So the account the services run as is,
> in effect, root: a bug that lets someone run code inside the core is a
> root compromise of the server, whatever the [unit
> sandboxing](#sandboxing-the-units) below holds back. Make that account
> one that does nothing else: the `domovoi` service user under [Make it an
> appliance](#make-it-an-appliance) (`sudo usermod -aG docker domovoi`),
> not a login you also browse or read mail with, and add no one else to
> the group.

Ollama, from a pinned release checked against its SHA-256. (Ollama's
`curl -fsSL https://ollama.com/install.sh | sh` fetches whatever is newest
and checks no hash; the steps below are what that script does on a
CPU-only x86_64 box, with the check added.)

```bash
OLLAMA_VERSION=0.34.4
# ollama-linux-amd64.tar.zst of that release (on arm64: ollama-linux-arm64.tar.zst,
# 96f50a1192133028cf4e010d8c333f8af14b1505db6be7b2034c11487e7fd7e6)
OLLAMA_SHA256=c238986e61d40c0cc5f4a9b9e40b9eea104350b77efa34741fc134e105cb9533
sudo apt install -y zstd
curl -fL -o /tmp/ollama.tar.zst   "https://github.com/ollama/ollama/releases/download/v$OLLAMA_VERSION/ollama-linux-amd64.tar.zst"
echo "$OLLAMA_SHA256  /tmp/ollama.tar.zst" | sha256sum -c -   # must say OK; stop if not
sudo rm -rf /usr/local/lib/ollama
sudo tar --zstd -xf /tmp/ollama.tar.zst -C /usr/local       # bin/ollama and lib/ollama
rm /tmp/ollama.tar.zst
sudo useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama
```

**`/etc/systemd/system/ollama.service`** (the unit `install.sh` writes):

```ini
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now ollama
```

To move to another release, take its digest from that release's
`sha256sum.txt` (or `gh release view v<version> -R ollama/ollama --json
assets`) and repeat the steps with both values changed. The Windows
installer pins the same release.

Then clone and install. A virtualenv is worth it here — it keeps Domovoi's
dependency tree away from the system Python that apt manages:

```bash
git clone https://github.com/coders-farm-official/domovoi && cd domovoi
```

```bash
python3 -m venv .venv && source .venv/bin/activate
```

**Install from the hash-pinned lock.** `requirements-linux-py314.lock` at
the repo root is the whole production set (the core, the dashboard, the
`real-clients` and `voice-profile` extras, and CPU-only `torch` from
PyTorch's CPU index), every package at an exact version with its
SHA-256s, so a substituted or tampered download fails the install instead
of running. Beside it, `requirements-linux-py314-no-deps.lock` pins and
hashes `faster-whisper` and `resemblyzer`, which go in without the
dependencies they declare (`requirements-no-deps.in` says why: PyAV and
librosa carry LGPL libraries Domovoi doesn't use). They were compiled for
CPython 3.14 on x86_64, which is what Ubuntu 26.04's `python3` is
(`python3 --version`). Four installs, in this order:

```bash
awk '/^(packaging|setuptools|wheel)==/ {p = 1} p {print} p && !/\\$/ {p = 0}' requirements-linux-py314.lock >/tmp/build-tools.txt
pip install --require-hashes -r /tmp/build-tools.txt
pip install --require-hashes --no-build-isolation -r requirements-linux-py314.lock
pip install --require-hashes --no-deps -r requirements-linux-py314-no-deps.lock
pip install --no-deps --no-build-isolation -e .
```

The first installs the build tools, checked against the lock's hashes:
the `awk` line copies the lock's own entries for `setuptools`, `wheel`
and `packaging` (which `wheel` needs), each `name==version` with all its
`--hash` lines, into a requirements file for it. (The shorter
`pip install --require-hashes -c requirements-linux-py314.lock setuptools wheel`
is refused by the pip that Ubuntu 26.04's venv ships, 25.1: it takes no
hashes from a constraints file. `provision.sh` and the update unit's
locked re-sync both install the build tools the `awk` way.) If pip
stops with *Hashes are required* for another package, a build tool
gained a dependency in the lock: add its name to the `awk` pattern.
The second installs everything else; `metaphone` publishes no wheels and
is built here, with those checked tools rather than a fresh, unchecked
`setuptools` that pip would otherwise download for each build (that is
what `--no-build-isolation` is for, and why the `python3-dev` and
`build-essential` prerequisites are there). The third installs
`faster-whisper` and `resemblyzer` alone, hash-checked; everything they
use is already in from the second. The fourth installs Domovoi itself and
nothing else. No `dev` extra: a server doesn't run the test suite.

PyPI's default Linux `torch` build bundles its own CUDA runtime — cuBLAS,
cuDNN, NCCL, cuFFT, cuSOLVER, Triton and friends, about **2.7 GB** of it.
The lock pins the `+cpu` build from PyTorch's CPU index (it names that
index itself), so none of that arrives. That is entirely separate from the
`cuda` extra below, which only covers the wheels `ctranslate2` wants.

Check speech recognition and the voice encoder load:

```bash
python -c "from domovoi.clients.whisper import import_faster_whisper; import_faster_whisper()"
python -c "from domovoi.clients.speaker_encoder import SpeakerEncoder; SpeakerEncoder()"
```

Both print nothing when they work. (Not `from resemblyzer import
VoiceEncoder`: that package imports librosa, which isn't installed;
Domovoi runs Resemblyzer's network itself, `domovoi/clients/speaker_encoder.py`.)

**No lock for your Python?** On anything but CPython 3.14 (the
[`uv venv --python 3.13`](#if-the-wheels-arent-there-yet) route, an arm64
box the lock wasn't checked on), fall back to pip's resolver, which checks
no hashes and installs whatever the index serves that day within the
floors in `pyproject.toml`:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[real-clients,voice-profile]"
pip install --no-deps -r requirements-no-deps.in    # faster-whisper, resemblyzer
```

The [update unit](#updates-from-the-dashboard) re-syncs this way too
until you opt it into the lock ([The hash-pinned lock](#the-hash-pinned-lock));
once opted in, it falls back to this, with a warning in its step,
whenever the venv's Python isn't the lock's.
Maintainers regenerate the lock with `DOMOVOI_LOCK_SEED=requirements.lock
bash scripts/linux/compile-linux-lock.sh` (pip-compile in a pinned
`python:3.14-slim` container, so it resolves for this platform from any
machine with Docker, starting from the suite's tested versions) after any
change to `pyproject.toml`, and commit both.

> **CUDA wheels are opt-in.** They used to ride along with `real-clients`;
> they now live in a separate `cuda` extra, so the lock pulls nothing
> NVIDIA. On a machine that *does* have an NVIDIA GPU, add
> `pip install -e ".[cuda]"` (resolved from the index, not hash-checked:
> the lock is the CPU set).

> **The streaming fast lane is opt-in as well.** It is a small second
> recognizer that follows each command while it is spoken; today it only
> runs in *shadow* mode, logging what it would have done next to what
> Whisper heard ([CPU_HOST.md](CPU_HOST.md#the-streaming-fast-lane)).
> Domovoi does not install its recognizer, `sherpa-onnx`, any more
> (2026-10): the `sherpa-onnx` wheels on PyPI compile in espeak-ng
> (GPL-3.0) for their own text to speech. To try the lane, install a
> sherpa-onnx built with `SHERPA_ONNX_ENABLE_TTS=OFF`, or the PyPI wheel
> knowing it carries that GPL code ([CPU_HOST.md](CPU_HOST.md#the-streaming-fast-lane)):
>
> ```bash
> pip install "sherpa-onnx>=1.13.8,<2"     # the PyPI wheel: includes espeak-ng (GPL-3.0)
> python -m domovoi.fast_lane fetch      # optional: the 103 MB model now, not on first enable
> ```
>
> Then set `FASTLANE_MODE=shadow` in `domovoi/.env`, or pick **shadow**
> under Settings → Speech-to-text → *Fast lane*, which applies without a
> restart. It is outside the lock and outside pyproject: an update's venv
> re-sync leaves it installed but does not install it.

Bring it up — `dev.sh` is the bash twin of `dev.ps1`:

```bash
./domovoi/scripts/dev.sh
```

The first thing it does on a fresh checkout is write `domovoi/.env` from
`.env.example` with a **random Postgres password** (`python -m
domovoi.env_bootstrap`; both `DATABASE_URL` and the `POSTGRES_PASSWORD`
line that `docker-compose.yml` reads carry it). Run the stack by hand
instead? Run that command first, before the first `docker compose up`, so
the database is initialised with the password your `.env` holds. An
existing `.env` is never touched. Already know whether this box will have
internet? Answer in the same step: `python -m domovoi.env_bootstrap
--internet always` (or `sometimes`, `never`) writes `INTERNET_ACCESS` into
the fresh `.env`; run it before `dev.sh`. After the migrations, `dev.sh`
starts the search helper (SearXNG) when the answer is `always` or
`sometimes`.

and in a second terminal, from the repo root:

```bash
python -m web.backend.main
```

From here, answer [the internet question](#will-your-domovoi-have-internet)
below, then rejoin the [setup runbook at Step 3](SETUP_RUNBOOK.md#step-3--claim-admin-and-configure-for-your-hardware)
to claim admin and apply your [CPU host settings](CPU_HOST.md).

### Will your Domovoi have internet?

Installing needed it. After this point it's your choice. The dashboard
asks right after you claim the admin account — **Yes, always**,
**Sometimes**, or **No, keep everything in the house** — and you can
change it later in **Settings → Internet**. [INTERNET.md](INTERNET.md)
says what each answer switches; on Linux it comes down to this:

- **Yes, always** (or **Sometimes**). Saving the answer starts the search
  helper (SearXNG) behind web answers — the weather, scores, "check that
  online", news feed discovery — and the [update unit](#updates-from-the-dashboard)
  keeps it in step after every update; Docker brings it back after a
  reboot. Then do [what stays yours](INTERNET.md#if-your-domovoi-will-have-internet):
  your town for local news, an AcoustID **application** key for song
  recognition (Settings → Configuration → Library → **AcoustID key**, or
  `domovoi/.env`), your radio market.
- **No.** While the box is still online, work through
  [Before you disconnect](INTERNET.md#before-you-disconnect): the Whisper
  fallback model, the Ollama models, the satellite media cache, and the
  time zone (`timedatectl` should show yours, not UTC; without internet,
  point `systemd-timesyncd` at a LAN time server if you have one). Then
  answer **No**: the server stops contacting anything outside your
  network, including the internet check, and sets `HF_HUB_OFFLINE=1` for
  itself. No drop-in is needed. With the [update unit](#updates-from-the-dashboard)
  installed, the next **Restart Domovoi** also quiets Ubuntu's own jobs
  that go online (package updates, login news, crash reports): see
  [Internet or not](#internet-or-not).
- Whatever the answer, leave `CONNECTIVITY_PROBE_TARGET` on an internet
  address: pointed at the router, the probe tells Domovoi it is online
  when it isn't.

---

## Make it an appliance

This is where Linux earns its keep. The runbook warns that `dev.ps1` is a
foreground development script that dies with its terminal — on Linux you
replace it properly with three systemd units.

The three units ship with the checkout, in
[`scripts/linux/units/`](../scripts/linux/units/). They assume the repo at
`/opt/domovoi`, a venv at `/opt/domovoi/.venv`, and a service user named
`domovoi` with its home at `/home/domovoi`, in the `docker` group (which
[makes it root](#install), in effect). Install them as they are:

```bash
sudo install -m 0644 /opt/domovoi/scripts/linux/units/domovoi-db.service   /opt/domovoi/scripts/linux/units/domovoi-core.service   /opt/domovoi/scripts/linux/units/domovoi-web.service /etc/systemd/system/
```

A different user, checkout or home? Change `User=`, `WorkingDirectory=`,
`ExecStart=`, `Environment=HOME=` and the `ReadWritePaths=` / `BindPaths=`
lines of the copies in `/etc/systemd/system/` to match; [Sandboxing the
units](#sandboxing-the-units) says what each of those lines must cover.

- **`domovoi-db.service`** brings up Postgres and runs the migrations
  before anything connects, exactly what `dev.sh` does first
  (`docker compose up -d postgres`, then `docker compose run --rm flyway`).
- **`domovoi-core.service`** is the voice service on :6370
  (`/opt/domovoi/.venv/bin/python -m domovoi.main`).
- **`domovoi-web.service`** is the dashboard on :6369
  (`/opt/domovoi/.venv/bin/python -m web.backend.main`).

Note `WorkingDirectory` differs: the two Python services run from the
**repo root** so `python -m` resolves the packages, while the database
unit runs from `domovoi/` where the compose file lives. Same split as the
dev scripts. No venv activation is needed — `ExecStart` names the venv's
interpreter directly, which is equivalent.

Three details worth not skipping:

- **`After=ollama.service`** keeps the core from starting before Ollama is
  listening. The core survives it either way, but the first routed turn
  after a reboot shouldn't have to fail first.
- **`Environment=HOME=...`** — `~/.domovoi/` holds your Piper voices,
  trained wake-word models, the sounds cache, and the first-boot setup
  code, all resolved through `Path.home()`. It would very likely resolve
  correctly without this, but a service quietly writing its state into the
  wrong home directory is a miserable thing to diagnose later. Set it and
  point it at the service user's real home.
- **`TimeoutStopSec=30`** on both Python units, **`KillMode=control-group`**
  on the core. A core stop takes well under a second: SIGTERM starts
  uvicorn's own shutdown (every
  satellite gets a 1012 close and reconnects to the next process), then
  the teardown unloads plugins and stops the workers. The core bounds that
  itself — `SHUTDOWN_GRACE_SEC` (5) for open sockets and requests,
  `SHUTDOWN_TEARDOWN_SEC` (10) for the teardown, and at
  `SHUTDOWN_DEADLINE_SEC` (20) after the signal it logs every thread's stack
  and exits — so `TimeoutStopSec` is only systemd's backstop, and it has to
  stay above that deadline or the journal gets a bare SIGKILL instead of
  the stacks. Without it systemd waits 90 s. (Until 2026-09-30 the core
  swallowed SIGTERM and every update and restart waited out exactly that.)

  `control-group` is systemd's default, written out because `mixed` looks
  tempting and is worse here. The core's one long-lived child is Python's
  multiprocessing resource tracker — Python 3.14 starts one the first time
  anything creates a multiprocessing lock, and loading the Whisper model
  does (through its download progress bar). The tracker ignores SIGTERM by
  design and exits by itself the moment the core does, after removing the
  semaphore the core leaves in `/dev/shm`. `mixed` SIGKILLs it right then,
  in the middle of that clean-up, on every stop; `control-group` lets it
  finish in a few milliseconds, and the core's short-lived children
  (ffmpeg, `rtl_fm`, git, docker, pip) take the SIGTERM as they should.

  Already running units written before these lines? Add them without
  touching the unit file; the next stop uses them, no restart needed:

  ```bash
  sudo mkdir -p /etc/systemd/system/domovoi-core.service.d
  printf '[Service]\nTimeoutStopSec=30\nKillMode=control-group\n' \
    | sudo tee /etc/systemd/system/domovoi-core.service.d/stop.conf
  sudo systemctl daemon-reload
  systemctl show domovoi-core -p TimeoutStopUSec -p KillMode
  ```

Enable and start:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now domovoi-db domovoi-core domovoi-web
```

Watch them:

```bash
journalctl -u domovoi-core -f
```

`domovoi-web` uses `Wants=` rather than `Requires=` on the core
deliberately — if the core is wedged, you still want the dashboard up to
tell you so. For the same reason it is ordered after `domovoi-db` only,
not after the core: the core is `Type=notify` and counts as starting
until it says it is ready ([The loop watchdog](#the-loop-watchdog)), and
the dashboard must not wait on that.

**Then test the power cut.** Pull the plug, bring it back, and confirm the
whole house returns — Docker, Postgres, both services, every satellite —
without you logging in. That's the difference between a demo and an
appliance, and it's the thing Linux makes genuinely easy.

### Sandboxing the units

The shipped units carry systemd's sandboxing. None of it changes what
Domovoi does; it bounds what a process gone wrong can reach directly:

- **Read-only system.** `ProtectSystem=strict` mounts everything read-only
  except what `ReadWritePaths=` names: the checkout and its venv
  (`/opt/domovoi`: pulls, plugin installs, `domovoi/.env`), the usual
  mount points for media disks and cards (`/mnt`, `/media`, `/run/media`,
  `/srv`; a missing one is skipped), and the service user's home, the only
  home the core and web can see at all (`ProtectHome=tmpfs` with
  `BindPaths=/home/domovoi`). The home holds `~/.domovoi/`, the model
  caches and the default `~/Music`, `~/Pictures`, `~/Videos` and
  `~/Documents`.
  `domovoi-db` sees homes read-only and writes only its own.
- **Private `/tmp`** per unit, files the services create are not readable
  by other accounts (`UMask=0027`), and the control groups are read-only.
- **`domovoi-web` and `domovoi-db`** also have the kernel's tunables,
  modules, logs and hostname off limits; no set-uid files, no realtime
  scheduling, no new namespaces, native system calls only, and sockets
  limited to local, IPv4, IPv6 and netlink. They run with
  `NoNewPrivileges=yes` and no capabilities at all; `domovoi-db`, which
  only drives the `docker` CLI, gives up devices, the clock and
  writable-executable memory too.

What is deliberately left out, and why:

- **Everything that implies `NoNewPrivileges` on the core.** The version
  panel's Restart runs `sudo -n systemctl ...` from the core
  ([2b](#2b-restart-from-the-dashboard-needs-one-sudoers-line)), and on a
  box with the [update unit](#updates-from-the-dashboard) that is
  `sudo -n systemctl start domovoi-update.service`, the box's only update
  path. sudo works only while its set-uid bit is honoured, and the kernel's
  no-new-privileges flag turns that off ("The no new privileges flag is
  set"). systemd sets that flag for `NoNewPrivileges=yes`, and it also
  implies it, for a unit that runs as a non-root `User=`, for every option
  it enforces with seccomp: `LockPersonality`, `MemoryDenyWriteExecute`,
  `PrivateDevices`, `ProtectClock`, `ProtectHostname`, `ProtectKernelLogs`,
  `ProtectKernelModules`, `ProtectKernelTunables`,
  `RestrictAddressFamilies`, `RestrictNamespaces`, `RestrictRealtime`,
  `RestrictSUIDSGID`, `SystemCallArchitectures`, `SystemCallFilter`,
  `SystemCallLog` and `DynamicUser` (`man systemd.exec`, under
  `NoNewPrivileges=`). So the core unit keeps only the file-system part
  above (read-only system, private `/tmp`, hidden homes, read-only control
  groups, the umask), and its comment lists what it leaves out. Adding any
  of those options to the core, in the unit or a drop-in, silently breaks
  the Restart button: the panel then reports no sudoers grant. The web
  never runs sudo (its Restart button asks the core), so it keeps
  `NoNewPrivileges` and every one of those the hardware allows (all but the
  device, clock and W+X ones below). Its **Restart the core**, for a core
  that doesn't answer, needs no privilege either: it signals the core's
  main process, which runs as the same user, and the core unit's
  `Restart=` starts it again ([The loop watchdog](#the-loop-watchdog)).
- **`PrivateDevices`, `ProtectClock`, `DevicePolicy` on core and web.**
  They hide device nodes: a GPU, an SDR stick for the radio, the block
  devices the dashboard reads a satellite card's label from.
- **`MemoryDenyWriteExecute` on core and web.** The speech and audio stack
  (numba, torch, onnxruntime) compiles code at run time.
- **The Docker socket.** No unit setting can narrow it: the core needs it
  for the room players, and through it the service user can still do
  anything as root ([Install](#install)). The sandbox narrows the direct
  paths only.

**Media somewhere else**, say `/data/music`? Add it with a drop-in for the
core and the web (`sudo systemctl edit domovoi-core`, then the same for
`domovoi-web`):

```ini
[Service]
ReadWritePaths=/data/music
```

**Check it** after `daemon-reload` and a restart: `systemd-analyze security
domovoi-core` scores the exposure (the units this page listed before
scored 9.0 "UNSAFE" each; the shipped ones score about 3.5 for the web and
1.8 for `domovoi-db`, and the core, which keeps only the file-system part,
scores higher than both), Settings → Version still offers
**Restart Domovoi** (its `sudo -n -l` probe runs inside the sandbox), a
satellite turn answers, and a room's music starts (the core runs
`docker`). The quick check for the sudo path on its own, as root:
`systemd-run -p User=domovoi -p ProtectSystem=strict -p PrivateTmp=yes --wait --pipe sudo -n -l`
lists the grant; add `-p RestrictSUIDSGID=yes` and it fails, which is the
trap described above.

**Already running units from an older copy of this page?** If they match
this page's layout, install the shipped files over them as above, then
`sudo systemctl daemon-reload` and restart the three. If you changed
paths or the user, copy the sandboxing block from the shipped file into a
drop-in instead (`sudo systemctl edit domovoi-core`, and the same for the
other two), with your paths in `ReadWritePaths=` and `BindPaths=`. Copy
each unit's own block: the web's or the database's block in the core's
drop-in stops its sudo.

`domovoi-update.service` is not sandboxed: it runs as root to stop and
start the other units, back up the database, rebuild the room image and
roll everything back ([Updates from the dashboard](#updates-from-the-dashboard)).

### The loop watchdog

On 2026-10-14 the core froze: port 6370 still took connections, nothing
answered, every satellite went offline, and the dashboard's Restart could
not help, because it asks the core to restart itself. It stayed that way
until a power cycle, which also wiped the journal (that box kept it in RAM
only). Two causes were found and fixed (the radio plugin decoding audio on
the core's event loop, and database connects queuing behind a full thread
pool); these pieces are for the next one.

**The core watches itself.** A thread outside the event loop
(`domovoi/loop_watchdog.py`) checks a heartbeat the loop beats every
2 s, and every 15 s sends a real `SELECT 1` through the normal database
path. If the loop has not run for `LOOP_WATCHDOG_STALL_SEC` (30), or
`LOOP_WATCHDOG_PROBE_FAILURES` (3) probes in a row got no answer within
`LOOP_WATCHDOG_PROBE_TIMEOUT_SEC` (10), it writes the reason and every
thread's stack to the journal and to `~/.domovoi/logs/core-watchdog.log`
(1 MB, one older copy, flushed to disk before it goes on), and exits with
status **70**. A database that *refuses* (down, restarting) is logged, not
counted: restarting the core can't fix that. A startup still unfinished
after `LOOP_WATCHDOG_STARTUP_SEC` (900) counts too. For a freeze so deep
the thread can't run either (C code holding the interpreter), Python's
faulthandler dumps every stack and exits (status 1) 30 s after the stall
time; what it writes goes to `~/.domovoi/logs/core-faulthandler.log`, and
the next start moves it into `core-watchdog.log` and repeats it in the
journal. Settings → Version shows the last such restart (**last freeze**,
when and why). `LOOP_WATCHDOG_ENABLED=false` turns the checks off (the
core then never ends itself; under the shipped unit it still says it is
ready and keeps systemd's watchdog fed).

**systemd restarts it.** Every one of those exits needs the unit to start
the core again. The shipped `domovoi-core.service` has what it takes:

```ini
Type=notify          # READY=1 when startup is done
NotifyAccess=main
Restart=on-failure   # after the watchdog's 70, faulthandler's 1, a kill
RestartSec=5
TimeoutStartSec=15min
WatchdogSec=90       # fed only while the loop beats and the database answers
```

`WatchdogSec` is the last backstop: the core feeds it (`WATCHDOG=1`) only
while healthy, so if even its watchdog thread stops (the whole process
stuck), systemd sends SIGABRT (faulthandler prints the stacks) and then
SIGKILL, and `Restart=` starts it again. The dashboard is ordered after
`domovoi-db`, not after the core, so it stays up while the core starts or
is stuck.

**A unit you wrote yourself** (from an older copy of this page) with no
restart policy at all (`Restart=no`, systemd's default) leaves the core
down after the watchdog ends it. You don't have to fix that: every run of
the [update unit](#updates-from-the-dashboard) checks `systemctl show -p
Restart domovoi-core` and, only when it is `no`, writes
`/etc/systemd/system/domovoi-core.service.d/50-domovoi-restart.conf`
(`Restart=on-failure`, plus `RestartSec=5` unless the unit sets a
`RestartSec=` of its own) and reloads systemd; the Version card's last
update says so. A policy of your own (`on-abnormal`, `on-watchdog`,
`on-success`) is never replaced: the run leaves it and its last update
carries a warning that it won't start a core that exited with 70. Neither
is `always` or `on-failure` touched. `Type=notify` and `WatchdogSec=` are
added only by installing the shipped unit (above, or
`scripts/linux/provision.sh`), never into a unit you wrote: a different
`ExecStart` could leave systemd waiting for a READY that never comes.
To decide `Restart=` yourself, put it in the unit or in a drop-in that
sorts after `50-domovoi-restart.conf`.

**The dashboard's Restart the core.** When the core doesn't answer, an
admin gets **Restart the core** on Settings → Version, on the Satellites
page and on Home's "isn't answering" row. It ends only a core that is
truly stuck, never one that is busy, slow or missing its database:

- **Liveness, not health.** The web asks `GET /v1/alive`, which the
  core's event loop answers by itself, with no database. Any answer at all,
  a `503` included, means the core runs: nothing is ended, and the button
  isn't offered. A core whose database is down says so (the Satellites
  banner reads *the database isn't answering*): restarting it can't help.
- **Silent for longer than the core's own watchdog waits.** A press ends
  the core only after `/v1/alive` has given no answer, asked every second
  (3 s each), for `LOOP_WATCHDOG_STALL_SEC` + 15 s (45 s by default; never
  under 45): a loop busy for a few seconds answers again within that, and
  a core truly frozen has usually ended itself first. The page reads the
  status every 5 s while the core is silent, and that silence counts, so a
  press after a minute of watching acts at once; otherwise the button says
  *making sure it's stuck* while it waits. Only silence somebody watched
  counts: more than 12 s between two silent reads (the page was closed;
  the core may have answered meanwhile) starts the count again.
- **Not while it starts.** A core systemd reports as starting, one that
  restarted meanwhile (a new main PID) and, under a unit changed to
  `Type=simple`, one that hasn't said it is ready
  (`~/.domovoi/run/core-ready.json`) and started less than
  `LOOP_WATCHDOG_STARTUP_SEC` (900 s) ago are left alone. The note counts
  only when it names that PID and was written after that process started
  (the core removes it at each start), so an earlier boot's note can't
  vouch for a core that is starting now.
- **Every guard again, right before the kill.** The unit restarts after a
  kill, the main PID is still that process, it is `python -m domovoi.main`
  and this account's: read fresh after the wait, before SIGUSR1 and again
  before SIGKILL. One press at a time; a second (another admin) is told one
  is under way.

The web checks the admin session itself (the shared `admin_sessions`
table; the core can't), then signals the core's main process: SIGUSR1
(every stack into the faulthandler log), then SIGKILL, and systemd starts
it again. It needs no sudoers line: core and web run as the same user.
Where it can't work (a unit systemd wouldn't restart, a core under another
account, no systemd), the dashboard shows why and the command to run by
hand. When the process can't be signalled but the web may use the core's
[restart grant](#2b-restart-from-the-dashboard-needs-one-sudoers-line)
(a web unit you wrote without `NoNewPrivileges`), it runs that instead.

**The journal is kept on disk.** Without that, the record of a freeze is
lost at the power cycle that ends it. `provision.sh` and every update run
install
[`scripts/linux/units/journald-domovoi.conf`](../scripts/linux/units/journald-domovoi.conf)
as `/etc/systemd/journald.conf.d/50-domovoi.conf`, which sets
`Storage=persistent` and nothing else, and restart `systemd-journald`
once. Its size stays journald's to decide (by default at most 10% of the
file system, capped at 4 GB) or yours: a `SystemMaxUse=` in
`/etc/systemd/journald.conf` or a drop-in of your own still applies. To
keep the journal your own way, set `DOMOVOI_MANAGE_JOURNAL=0` in
`/etc/default/domovoi-update` and delete the file, or put your own
`Storage=` in a drop-in after it. What the journal holds, for how long,
and what "forget me" doesn't reach:
[SECURITY_PRIVACY.md, Data at rest](SECURITY_PRIVACY.md#data-at-rest).

**By hand, over SSH** (nothing else answering):

```bash
sudo systemctl restart domovoi-core                 # what the button does, the slow way
journalctl -u domovoi-core -b -1 --no-pager | tail -200   # the boot before, if the box was power-cycled
cat ~domovoi/.domovoi/logs/core-watchdog.log        # every stall report, newest last
sudo kill -USR1 "$(systemctl show -p MainPID --value domovoi-core)"   # every stack into ~/.domovoi/logs/core-faulthandler.log; it carries on
```

### Internet or not

The internet answer needs nothing from the units. Saving it in Settings →
Internet starts or stops the search helper at once, the
[update unit](#updates-from-the-dashboard) keeps it in step after every
update, and under **No** the core sets `HF_HUB_OFFLINE=1` for itself (the
`HF_HUB_OFFLINE` drop-in older versions of this guide suggested is no
longer needed; leaving one in place does no harm). Whisper loads its model
from the local cache first on every answer.

**Ubuntu's own background jobs follow the answer too.** A stock Ubuntu
box goes online by itself on timers that have nothing to do with Domovoi.
Under **No**, [`scripts/linux/os-jobs.sh`](../scripts/linux/os-jobs.sh)
masks them (`systemctl mask`, and stops the timers):

| Unit | What it fetches |
|---|---|
| `apt-daily.timer`, `apt-daily-upgrade.timer`, `unattended-upgrades.service` | package lists and security updates |
| `motd-news.timer` | the login banner's news |
| `ua-timer.timer` | Ubuntu Pro status |
| `apport-autoreport.path`, `apport-autoreport.timer` | crash reports |
| `snapd.snap-repair.timer` | snap repair assertions |

Only units that are installed and not masked already are touched, and
each one it masks is written down in `/var/lib/domovoi-os-jobs/masked`.
The timers are stopped before systemd reloads and every unit it changes
has its failed mark cleared, so `systemctl --failed` stays empty and the
box doesn't report "degraded" over it. `unattended-upgrades.service` is
only the helper that keeps a shutdown from cutting an upgrade short:
masked, it keeps running until the next boot, because stopping it would
stop an upgrade already under way.
Under **Yes, always** or **Sometimes** it unmasks exactly those (a mask you
made yourself stays) and starts the enabled ones again, so they run as
Ubuntu ships them. Not answered yet: left as they are. A unit file of your
own in `/etc/systemd/system` under one of those names is never replaced
(the helper says it left it alone).

When it takes effect: at once when `provision.sh` runs, and otherwise at
the **next healthy restart or update the update unit runs**
([Updates from the dashboard](#updates-from-the-dashboard), step 12):
**Restart Domovoi** after you change the answer in Settings → Internet.
Saving the answer alone doesn't change them: the core runs as the service
user, which can't change systemd units. Without the update unit, run it
yourself: `sudo bash /opt/domovoi/scripts/linux/os-jobs.sh --internet
never` (or `always`, `sometimes`); `--check` and `--dry-run` change
nothing. To manage those timers yourself, put
`DOMOVOI_MANAGE_OS_JOBS=0` in `/etc/default/domovoi-update`: the update
unit and `provision.sh` both leave them alone then.

The helper writes a unit down before it masks it, and crosses it off when
the mask fails, so a run killed half-way never leaves a mask it has not
written down (one written down but not masked is masked by the next
**No** run, or crossed off by the next Yes/Sometimes run).

Taking an update that downloads on a box answered **No** is one restart
longer: switch to **Sometimes**, restart, switch back to **No** and
**restart once more**; the Sometimes restart unmasked the timers, the
last one masks them again.

In the Windows installer's WSL distro the same helper also disables and
masks `wsl-pro.service` and snapd (`snapd.service`, `snapd.socket` and the
other `snapd.*` services and timers) whatever the answer: Domovoi uses
neither. On bare metal it leaves them alone, since your server may use
snaps.

**Pinning the answer in the unit**, so the dashboard can't change it (it
then shows "set in the server's environment"): a drop-in for
`domovoi-core.service` **and** `domovoi-web.service`, with the same value
in both, then `daemon-reload` and restart them:

```bash
for u in domovoi-core domovoi-web; do
  sudo mkdir -p /etc/systemd/system/$u.service.d
  printf '[Service]\nEnvironment=INTERNET_ACCESS=never\n' \
    | sudo tee /etc/systemd/system/$u.service.d/internet.conf
done
```

For the update unit's search-helper step to read the same answer, put
`INTERNET_ACCESS=never` in `/etc/default/domovoi-update` too, or keep the
answer in `domovoi/.env`, which all three read.

**An alternative for the search helper, for Yes and Sometimes only:** if
you'd rather the database unit (re)create the `searxng` container as well,
add it in a drop-in, with the leading `-`:

```bash
sudo mkdir -p /etc/systemd/system/domovoi-db.service.d
printf '[Service]\nExecStart=-/usr/bin/docker compose up -d searxng\n' \
  | sudo tee /etc/systemd/system/domovoi-db.service.d/searxng.conf
```

The `-` lets `domovoi-db` succeed when that line fails, for instance when
the image can't be pulled because the line is down. Without it the failure
fails `domovoi-db`, and the core, which `Requires=` it, doesn't start.

Either way, `sudo systemctl daemon-reload` and a restart of that unit pick
it up.

**Remove that drop-in before you answer No.** It starts the container on
every boot whatever the answer says, and SearXNG fetches its own rule
lists and server lists from the internet each time it starts, before
anyone searches ([SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#what-leaves-your-network--and-how-to-turn-each-thing-off)).

---

## Wake-word training

Training a wake word of your own runs on the server, in a container on its
Docker Engine, once an admin turns it on: **Settings → Wake Words → Turn on
wake-word training**. That builds the trainer image (12.4 GB), downloads its
training data (17.9 GB, into the Docker volume `domovoi_oww_cache`) and
switches training on, with no shell and no restart: about 22 GB downloaded,
once, and about 13 minutes on a fast line. Plan for about 32 GB of
disk the first time, then about 0.5 GB per wake word, where Docker keeps its
data (`/var/lib/docker`). The card refuses, saying why, on a box with less
than 24 GB of memory (the standard model tier's line), less free disk than
that plus 5 GB, no running Docker, or the internet answer **No**. How to
train a word, the settings and the licence of what it makes:
[`scripts/wake_word/README.md`](../scripts/wake_word/README.md).

**On a box with an NVIDIA GPU,** Docker needs the NVIDIA Container Toolkit to
hand the GPU to a container. [`scripts/linux/gpu-runtime.sh`](../scripts/linux/gpu-runtime.sh)
sets it up, only where an NVIDIA GPU is visible: in WSL, `/dev/dxg` and
`/usr/lib/wsl/lib/libcuda.so.1` (Windows' driver); on bare metal, the NVIDIA
driver's `nvidia-smi -L` listing a GPU. Installing the driver stays yours:
the script never installs one, and stops before installing anything if apt
would bring a driver, kernel or compute package along. It:

- adds NVIDIA's apt repository,
  `https://nvidia.github.io/libnvidia-container/stable/deb/amd64`
  (`/etc/apt/sources.list.d/nvidia-container-toolkit.sources`), with its key
  checked the way Docker's is: the downloaded key file must hold exactly one
  primary key, with the fingerprint `provision-manifest.conf` pins
  (`C95B321B61E88C1809C4F759DDCAE044F796ECB0`), and only that key is
  exported to `/etc/apt/keyrings/nvidia-container-toolkit.asc`;
- installs `nvidia-container-toolkit`, `nvidia-container-toolkit-base`,
  `libnvidia-container-tools` and `libnvidia-container1`, those not
  installed yet, at the manifest's versions (1.20.1-1), not held: like
  docker-ce, `apt upgrade` moves them later;
- runs `nvidia-ctk runtime configure --runtime=docker` (the
  `/etc/docker/daemon.json` it found is kept in
  `/var/lib/domovoi-gpu-runtime/daemon.json.before`) and restarts Docker
  when that changed the file. If Docker doesn't answer within 60 s, the file
  goes back as it was and Docker is restarted again.

A box already set up is left alone. `provision.sh` runs it as its
`gpu-runtime` step, right after Docker; a helper that fails or refuses
there (a key that isn't the pinned one, apt that would bring a driver,
Docker that won't start with the runtime) is a warning, not the end of the
install: training then runs on the CPU, and the next run or update tries
again. The update unit runs it on every
update and every Restart, while core and web are stopped, so a box installed
before it existed gets it at its next one ([Updates from the
dashboard](#updates-from-the-dashboard), step 7); there it never fails the
run, and under **No** it downloads nothing. A try that failed or refused is
recorded (`/var/lib/domovoi-gpu-runtime/last-failure`: the pins, when, why)
and the update unit doesn't repeat it for a day, so a box where it can't be
set up doesn't run `apt-get update` and restart Docker at every Restart. It
tries at once when the pins change, when `provision.sh` runs (it passes
`--force`), and at the next update or Restart after an admin presses **Turn
on wake-word training** or **Check the GPU and trainer again** on a box
whose card Docker can't use (the core leaves
`~/.domovoi/wake_trainer/gpu-runtime-retry`). A Docker restart stops every
container for a moment: Postgres and the rooms' players come back by
themselves (`restart: unless-stopped`), and the update unit restarts
`domovoi-db` after it. To keep the toolkit in your own hands, put
`DOMOVOI_MANAGE_GPU_RUNTIME=0` in `/etc/default/domovoi-update`; both
scripts leave it alone then. By hand: `sudo bash
/opt/domovoi/scripts/linux/gpu-runtime.sh` (`--check` and `--dry-run`
change nothing, `--detect` only says whether a GPU is visible). A word then
trains in about 20 minutes (26 the first time), on the least busy card, so
generating the synthetic speech stays off the card holding the chat model.
Once training is set up for the GPU, a training whose GPU Docker can't reach
(the toolkit removed, the card gone) fails saying so (the wrapper's exit 75)
instead of running for over an hour on the CPU under a limit sized for the
GPU: Restart, then **Check the GPU and trainer again**, or turn training on
again with the CPU.

**On a CPU-only box** (no NVIDIA GPU) training works, slower: about 75
minutes a word on 8 cores at the defaults (measured; the card scales that
to the box's cores), and the voice pipeline answers more slowly while a word
trains. The dashboard says so, and asks before turning training on and again
before each **Train**. The container is capped so voice keeps working: all
but a quarter of the CPUs (at least two left), a low CPU weight
(`--cpu-shares 128`), 12 GB of memory with no swap, and a lowered priority
inside; the time limit sizes itself to the box (twice the estimate, at
least two hours).

**After an update** that changes `scripts/wake_word/docker`, the update unit
rebuilds the trainer image (step 13), only on a box that built it. Answering
**No** in Settings → Internet while that rebuild, or a "Turn on" job's build
or download, is running stops it (the core stops the rebuild unit's `docker
build`, and the job's container), and the core checks again when it starts.

**Stopping and turning off.** A word that is training has a **Stop
training** button: its container is stopped (it removes its copies of the
recordings first) and the word goes back to *recording* with its clips. **Turn
off and free the space** shows what the trainer image and the cache volume
take, then switches training off and removes both, with Docker's build cache;
trained models and recordings stay. By hand, the same:
`docker image rm domovoi-wake-trainer:latest`, `docker volume rm
domovoi_oww_cache`, `docker builder prune`.

---

## Updates from the dashboard

The version panel's **Pull the latest** fetches, verifies the fetched tip's
signature (see [Signed updates](#signed-updates)), and fast-forwards to
it. A pull can bring new Python dependencies, a new Flyway migration or a
new MPD image, and a bare restart of core and web applies none of them:
`domovoi-db` runs Flyway only when *it* starts. A migration that never ran
looks like a broken release (the V013 device-token table was the first
time this bit).

A fourth unit closes that gap. `domovoi-update.service` is a root oneshot
that runs [`scripts/linux/apply-update.sh`](../scripts/linux/apply-update.sh),
never from the checkout itself: its root-owned verifier first copies the
commit HEAD names out of a mirror only root can write, and the update runs
from that copy ([The update verifier](#the-update-verifier)).
On a box that already runs the three units, one command installs it: see
[One-time upgrade for existing installs](#one-time-upgrade-for-existing-installs).
Once it's installed, the panel's **Restart to apply changes** and
**Restart Domovoi** both start it instead of bouncing core and web. One run
at a time: while it runs, however it was started (the dashboard, another
tab, `sudo systemctl start domovoi-update` over ssh), the core refuses
another restart and a pull, and the panel, reloaded or not, shows only a
greyed **Updating…** until the run ends, then what it did. The core asks
systemd whether the unit is running (`systemctl show`, no sudo). Each run
does this:

1. Work out what changed since the last SHA it applied and saw healthy.
   If nothing did, it's a plain restart, which is what **Restart Domovoi**
   is when nothing is waiting: stop web and core, start them, and
   health-check them, usually in a few seconds. No backup, no dependency
   sync, no MPD rebuild, and `domovoi-db` is left alone, unless Postgres
   doesn't answer or its Flyway history is missing a migration the checkout
   ships: then `domovoi-db` is restarted first (compose up plus Flyway),
   as every plain restart did before 2026-10-05. While core and web are
   stopped, Docker's GPU runtime is checked as in step 7 (and when that
   restarted Docker, `domovoi-db` is restarted too); a healthy plain
   restart ends with steps 11-13.
   If something did, it's the full update below, whichever button started
   it: code pulled since the panel last read the version, or code loaded
   outside the unit (pulled, then `domovoi-core` restarted by hand, so the
   checkout isn't the SHA the unit last applied). **Restart Domovoi** says
   so before anything happens: its confirm asks for the full update (a
   second confirm, when only the read taken at the press shows it), and
   the card says **Updating…** and waits up to 15 minutes, not seconds.
2. Refuse, touching nothing, if the checkout's `origin` or tracking branch
   is not the pinned upstream (with a branch pin, HEAD must also be on one
   of the pinned branches as last fetched), or if signed updates are enforced and HEAD
   does not verify against the root-owned allowed-signers file (both in
   [Signed updates](#signed-updates); the first check runs before step 1,
   on the plain restart too). Refuse, touching nothing, if tracked files
   have uncommitted changes, or are hidden from `git status` by a
   `skip-worktree` or `assume-unchanged` flag (`git update-index`), whose
   changes it can't see. A rollback could not restore that tree.
   Untracked files are fine. Stop, touching nothing, if the dependencies
   changed but the service user can't write the venv (step 5 would fail,
   and so would its rollback).
3. `pg_dump -Fc` the database through the `domovoi-postgres` container into
   `/var/lib/domovoi-update/backups/`, and `domovoi_test` next to it
   (`pre-<sha>-<time>.test.dump`) when that database exists: plugin
   migrations are applied to both. The newest 5 of each are kept. If either
   backup fails, the update stops there and nothing has been stopped.
4. Stop `domovoi-web` and `domovoi-core`, waiting at most 40 s
   (`DOMOVOI_UPDATE_STOP_TIMEOUT`). A unit still stopping then is
   SIGKILLed; one still up 10 s after that fails the step. The step's
   detail says how long each unit took and how it ended, so a stop that
   needed a kill — systemd's after `TimeoutStopSec`, or this one — shows
   in the version panel instead of hiding in a slow step.
5. If `pyproject.toml`, a `requirements*.lock` or a bundled plugin's lock
   changed: re-sync the venv. A change to `pyproject.toml` that only moves
   `[project]`'s `version` line (every release's bump) is not a dependency
   change: no re-sync, and a box answered **No** still takes the release. By default that is pip's resolver, as it
   was before the lock existed: CPU torch first, then
   `pip install -e ".[real-clients,voice-profile]"` from the index; when
   the checkout has `requirements-linux-py314.lock`, the step's detail
   says it is there to opt into. With `DOMOVOI_USE_LOCK=1`
   ([The hash-pinned lock](#the-hash-pinned-lock)), and when the venv runs
   the Python the lock was compiled for, it is the three hash-checked
   installs from the lock that [Install](#install) gives, and a package
   whose download doesn't match its hash fails the step, so the update
   rolls back; extras beyond the lock's own (`cuda`, `fastlane`) then go
   through the resolver, and the step's detail says so. Opted in without
   a usable lock, it uses the resolver and the step is a `warn`: the
   version panel shows **lock not applied: <reason>** under the last
   update.
6. If `domovoi/Dockerfile.mpd` or `domovoi/mpd.conf` changed: rebuild
   `domovoi-mpd:latest` exactly as the core does, and remove the room
   containers. The core recreates each one at startup from its `mpd_rooms`
   row, with the same data volume, so playlists and queues survive.
7. Docker's GPU runtime, where an NVIDIA GPU is visible
   (`scripts/linux/gpu-runtime.sh`, [Wake-word training](#wake-word-training)):
   the NVIDIA Container Toolkit installed when it is missing, and the
   `nvidia` runtime put in `/etc/docker/daemon.json`, restarting Docker
   when that changed. It runs here, with core and web stopped, because a
   Docker restart bounces Postgres and the rooms' players; step 8 restarts
   `domovoi-db` after it. It can't fail the update and is never rolled
   back: a `gpu-runtime` step is recorded only when it changed something
   (`ok`) or failed (`warn`, with the helper's reason). No GPU, or nothing
   to do: nothing is recorded. Under `never` nothing is downloaded. A failed
   try is not repeated for a day ("not retried", nothing recorded), unless
   the pins changed or the dashboard left its retry flag
   (`~/.domovoi/wake_trainer/gpu-runtime-retry`, removed once read).
   `DOMOVOI_MANAGE_GPU_RUNTIME=0` skips it.
8. `systemctl restart domovoi-db`: compose up plus Flyway.
9. Start core and web. Both must answer `/v1/health` (core, :6370) and
   `/api/health` (web, :6369) within 120 s.
   On a healthy plain restart (step 1), the search-helper step below runs
   too; a plain restart that fails its health check is recorded as
   `failed` and changes nothing else (there is nothing to roll back).
10. Every plugin that loaded before the update must still load: none that
   was enabled and not at `load_error` may be at `load_error` now. The core
   never lets a failing plugin take it down, so its health check stays
   green through one, and without this step a plugin the new code breaks
   would drop out of the house under an `ok` update. The script reads each
   plugin's status from the `plugins` table through the `domovoi-postgres`
   container, as it does the migration counts; the core has recorded every
   plugin's load before it first answers `/v1/health`. A plugin that was
   already failing, or that you switched off, doesn't count, and neither
   does one that is new in the update.
11. Keep the search helper (SearXNG) in step with the internet answer,
   read the way the core reads it (`python -m domovoi.egress
   --print-policy`, as the service user): `always` or `sometimes` →
   `docker compose up -d --no-deps searxng` (the first time, that downloads
   the pinned image); `never` → `docker stop domovoi-searxng` if it runs;
   not answered → left as it is. The update is recorded as applied
   (`applied_sha`) before this step, and the start is handed to a
   transient unit of its own (`domovoi-searxng-start`, see
   `journalctl -u domovoi-searxng-start`), so a first download on a slow
   line never holds the update open or turns it into a failure. Without
   systemd (`DOMOVOI_UPDATE_SEARXNG_DETACH=0`) it runs in the foreground,
   bounded by `DOMOVOI_UPDATE_SEARXNG_TIMEOUT` (300 s). This step can't
   fail the update: a failure is recorded as a `warn` step with its
   output, and the result stays `ok`. `DOMOVOI_MANAGE_SEARXNG=0` in
   `/etc/default/domovoi-update` skips it.
12. Keep Ubuntu's own background jobs in step with the same answer
   (`scripts/linux/os-jobs.sh`, [Internet or not](#internet-or-not)):
   `never` masks the updaters, news and crash-report timers, `always` or
   `sometimes` unmasks what an earlier `never` masked, and in the Windows
   installer's WSL distro `wsl-pro` and snapd stay off. This runs after a
   healthy plain restart too, so a changed answer takes effect at the next
   **Restart Domovoi**. It can't fail the update either: an `os-jobs` step
   is recorded only when it changed something (`ok`; what it changed is
   in the journal and in `last-result.json`) or failed (`warn`; Settings
   → Version shows the reason under the last update, such as "could not
   mask apt-daily.timer"). `DOMOVOI_MANAGE_OS_JOBS=0` skips it.
13. Rebuild the wake-word trainer image when it is out of date. Only where
   it exists (a household turned training on; the name is
   `DOMOVOI_WAKE_TRAINER_IMAGE`, else `WAKE_TRAINER_IMAGE` in
   `domovoi/.env`, else `domovoi-wake-trainer:latest`) and when its
   `org.domovoi.wake-trainer.src` label isn't the git tree id of the
   checkout's `scripts/wake_word/docker` (the core labels it the same way
   when it builds it). `docker build` of that folder, as the service user:
   under systemd in a transient unit (`domovoi-wake-trainer-build`, see
   `journalctl -u domovoi-wake-trainer-build`) the update doesn't wait for,
   since a build takes 3 to 8 minutes; without systemd in the foreground,
   bounded by `DOMOVOI_UPDATE_TRAINER_TIMEOUT` (1800 s). Under `never`
   nothing is built and a `warn` step says the image is out of date. An
   image from a registry (a name whose first part is a host, such as
   `ghcr.io/...`) is left alone. It can't fail the update; a `wake-trainer`
   step is recorded only when it built, started a build or could not. A
   healthy plain restart ends with it too. `DOMOVOI_MANAGE_WAKE_TRAINER=0`
   skips it.

Under `INTERNET_ACCESS=never` (read once, before anything is touched) an
update that would download is refused with status `aborted`, nothing
stopped or changed: one whose Python dependencies changed (pip), whose
`Dockerfile.mpd` changed (docker build pulls the base image and runs apt),
or whose `domovoi/docker-compose.yml` moves an `image:` pin (every image
there is pinned by digest, so `domovoi-db` would pull the new one).
An `mpd.conf`-only change keeps the image and just recreates the rooms.
To take such an update, follow
[INTERNET.md → Updating a box answered No](INTERNET.md#updating-a-box-answered-no).
Steps 7 and 13 never refuse an update: under `never` they download nothing
and say so.

If any of 4-10 fails (step 7 never does), it rolls back:
`git reset --keep` to the previous SHA (never `--hard`), the venv
re-synced and every package put back at its exact pre-update version,
the old MPD image rebuilt, and, if
`flyway_schema_history` or any plugin's migration ledger
(`plugin_<slug>.schema_history`) grew, the pre-update dump restored.
Plugin migrations count because Flyway never sees them: the core applies a
plugin's pending migrations when it starts, so a pull that adds one to a
bundled plugin changes the database in step 9, not step 8. Rolling the code
back without them would leave that plugin's schema ahead of its code, and a
corrected release of the same migration would then be refused as an edited
file. `domovoi_test` gets the same treatment on its own evidence: its dump
is restored if a plugin ledger in it grew. The restore goes into a fresh
database that is then renamed to `domovoi` (or `domovoi_test`); the
replaced one is kept as `domovoi_failed_<timestamp>` (or
`domovoi_test_failed_<timestamp>`) for inspection. It is a full copy of
the database, so only the newest one of each is kept: the next restore
drops older ones (`DOMOVOI_UPDATE_KEEP_FAILED_DBS`, default 1). Drop the
last one by hand (`docker exec domovoi-postgres dropdb -U domovoi
domovoi_failed_<timestamp>`) once it's no longer interesting. The loader switches off a plugin whose import,
`register()` or contract check fails, and a boot skips switched-off
plugins, so every plugin that loaded before the update and that the new
code's load errors switched off is switched back on before the previous SHA
starts (a plugin you switched off yourself stays off). Then it restarts,
health-checks and checks the plugins again, and records the commit it
rolled back as `bad_sha`. If a plugin still won't load on the previous SHA,
the result is `rollback_failed` and names it. The panel stops offering a
pull while upstream still points at that commit, and offers the next one.

Every run writes `/var/lib/domovoi-update/last-result.json` (status,
from/to SHA, each step with its timing, the signature verdict, the error).
It carries the tail of each failed command's output, so it is mode 0640,
group the service user's: the core reads it, other local accounts don't. The dumps in
`backups/` hold everything the database holds, the household token
included; that directory is 0700 and each dump 0600, root only
([SECURITY_PRIVACY.md → Data at rest](SECURITY_PRIVACY.md#data-at-rest)). The version panel shows
it as **last update**, and `GET /v1/admin/version` serves it as
`last_update`. When the core can't use the file, `last_update` is `null`
and `last_update_problem` says why (`unreadable`, `invalid`, ...); the
core's journal has the detail. Everything the script prints goes to the
journal:

```bash
journalctl -u domovoi-update -n 200
```

**`/etc/systemd/system/domovoi-update.service`**:

```ini
[Unit]
Description=Domovoi update (back up, sync, migrate, restart, roll back on failure)
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=DOMOVOI_REPO_DIR=/opt/domovoi
EnvironmentFile=-/etc/default/domovoi-update
RuntimeDirectory=domovoi-update
ExecStartPre=/bin/bash /usr/local/lib/domovoi-update/verify-update.sh prepare
ExecStart=/bin/bash /usr/local/lib/domovoi-update/verify-update.sh run
TimeoutStartSec=30min
```

No `User=`: it runs as root, because it has to stop and start the other
units and read the database dump. It has no `[Install]` section either:
nothing starts it at boot, only the button or you. Nothing in it names a
file of the checkout: `ExecStartPre` makes the verified copy of HEAD in
`/run/domovoi-update/<sha>` (`RuntimeDirectory=`, gone when the run ends)
and `ExecStart` runs that copy's `apply-update.sh` ([The update
verifier](#the-update-verifier)). The script keeps root away from the tree
too: git, pip and the room image's `docker build` run as the service user
through `runuser`, and root's own files live in `/var/lib/domovoi-update`,
which the service user can read but not write.

What that does not bound is upstream. The copy root runs is whatever
commit the pull brought, so a compromise of the repository's `main` (a
stolen token, a merged pull request) would be root on this box at its next
**Restart to apply changes**, with no LAN access or credential needed.
The bound is a signature: see [Signed updates](#signed-updates). Until
you set it up, every pull and every update says so.

### The update verifier

[`scripts/linux/update-verifier.sh`](../scripts/linux/update-verifier.sh),
installed root-owned as `/usr/local/lib/domovoi-update/verify-update.sh`,
is the only way root takes code from the checkout (delta security review
2026-10-16, S4). The checkout belongs to the service user, and so does
everything running as that user: the core, the dashboard, every plugin. Code
running there could edit the checkout, commit or not (an edit hidden from
`git status` by `git update-index --skip-worktree` included), swap a file
for a symbolic link, or plant a `refs/replace`, and before the verifier
root ran `apply-update.sh`, `os-jobs.sh` and `gpu-runtime.sh` and copied
the journald drop-in straight from that tree at the next Restart, signed
updates or not. Now, before every run (`ExecStartPre`):

1. HEAD is fetched into a bare mirror only root can write,
   `/var/lib/domovoi-update/mirror.git`, with `transfer.fsckObjects`: every
   object is checked as it comes in. The other end of the fetch, `git
   upload-pack`, runs as the service user; root reads nothing of the
   checkout itself, not its files, its object store or its config, and its
   git never follows a replacement object or a graft. The first run copies
   the whole history (about 30 MB, ten seconds or so); later ones only
   what's new.
2. While signed updates are enforced, the commit must verify there
   ([Signed updates](#signed-updates)), or the run is `refused` before any
   of it runs, and **last update** says why.
3. `scripts/linux` (the verifier itself, `apply-update.sh`, `os-jobs.sh`,
   `gpu-runtime.sh`, `install-update-unit.sh`, the units and drop-ins, the
   pin manifest; not its tests) and `docs/LINUX_HOST.md` are written out of
   the mirror with `git archive` into `/run/domovoi-update/<sha>`: plain
   files only (a symbolic link or a submodule there refuses the run),
   root's, `0755` directories.

`ExecStart` then runs that copy's `apply-update.sh`, which refuses if HEAD
moved since the copy was made (restart again to apply what is checked out
now) and runs its signature and branch checks in the mirror. Everything
root runs or installs after that (the helpers, the journald drop-in, the
verifier and the unit themselves) is the copy's.

What it bounds and what it doesn't: with signed updates enforced, root
runs only commits you signed, whatever happens to the working tree. Without
them, the copy is simply the commit HEAD names: an uncommitted or hidden
edit changes nothing root runs, but code running as the service user can
still make a commit and move HEAD to it, and that runs as root at the next
Restart. That is what signing is for. And code running as the service user
still runs the core, the dashboard and the plugins it can edit: the
verifier keeps it from root through the update, not from them. (The
service user is also in the `docker` group, which is root-equivalent on
its own; see [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#signed-updates-what-they-bound).)

**Existing installs** need nothing done by hand: the first **Restart to
apply changes** after pulling this runs the old unit's `apply-update.sh`
from the checkout one last time, and that script hands over to the
verifier beside it, which makes the copy and goes on from it; the copy's
`apply-update.sh` then puts the verifier in place and replaces the unit
with the one above (a `update-unit` step in **last update** says so). A
unit someone changed by hand is replaced the same way, after a copy of it
is kept beside it as `domovoi-update.service.bak-<time>` (a name systemd
ignores; the `warn` step says where): put any change of yours back as a
drop-in in `/etc/systemd/system/domovoi-update.service.d/`.

The search helper is started from the copy's `domovoi/docker-compose.yml`
(kept for it as `/var/lib/domovoi-update/searxng-compose.yml`), by `docker
compose` running as the service user, in the foreground or in its
transient unit; the project directory (`domovoi/.env`, `domovoi/searxng`)
is the checkout's, read by that user.

The fetch into the mirror is bounded: `DOMOVOI_VERIFY_FETCH_TIMEOUT`
seconds (default 900) and `DOMOVOI_VERIFY_FETCH_MAX_KB` per file it
writes (default 2097152, 2 GB), in `/etc/default/domovoi-update`; past
either, the run fails at `verify` with nothing changed.

`provision.sh` does the same on every run, re-runs included: right after
its checkout step it makes the verified copy (the same code, carried in
`provision.sh` itself) in `/run/domovoi-provision/<sha>` and reads the pin
manifest, the Ollama pin, the units, the drop-ins and its helpers only from
there; the files it only hashes or reads a line of (the locks, the compose
file) it reads as the service user.

**By hand, run root's scripts from a verified copy, never from the
checkout.** The installed verifier makes one (`export`); before the first
install there is none yet, and the checkout's own `update-verifier.sh`
makes it, as trusted as the checkout itself at that moment. Every command
on this page that runs `install-update-unit.sh` or `provision.sh` by hand
takes this form:

```bash
V=/usr/local/lib/domovoi-update/verify-update.sh
sudo test -f "$V" || V=/opt/domovoi/scripts/linux/update-verifier.sh   # before the first install only
COPY=$(sudo DOMOVOI_REPO_DIR=/opt/domovoi bash "$V" export /run/domovoi-update-install)
sudo bash "$COPY/scripts/linux/install-update-unit.sh" --dry-run
```

Run from inside the checkout on a box that has the verifier,
`install-update-unit.sh` does this itself: it says so, makes the copy with
the installed verifier and runs that copy's installer with the same
options. Only the very first install runs it from the checkout as it is.

**`/etc/default/domovoi-update`** is optional. Every setting defaults to the
layout on this page, so you only need the file to change one:

```bash
# Service user. Default: User= of domovoi-core.service, else the owner of the checkout.
# DOMOVOI_USER=domovoi
# The checkout. The unit sets it (Environment=); if set here too, it must name the same one.
# DOMOVOI_REPO_DIR=/opt/domovoi
# Default: the venv domovoi-core.service's ExecStart runs from, else <checkout>/.venv.
# DOMOVOI_VENV=/opt/domovoi/.venv
# Extras for the venv re-sync. The lock covers real-clients and voice-profile; any other
# (an NVIDIA host's cuda, fastlane) is resolved from the index after it, without hash checks.
# DOMOVOI_PIP_EXTRAS=real-clients,voice-profile
# 1: the re-sync installs from the hash-pinned lock. Default: off, pip's resolver as before.
# Re-seed the lock from this box's pip freeze first (The hash-pinned lock, below).
# provision.sh sets it to 1 on the boxes it installs (they were installed from the lock).
# DOMOVOI_USE_LOCK=1
# The hash-pinned lock the re-sync installs from, relative to the checkout. Empty: never use one.
# DOMOVOI_DEPS_LOCK=requirements-linux-py314.lock
# CPU torch index, for the resolver path only (the lock names its own). Empty: skip that step.
# DOMOVOI_TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
# DOMOVOI_UPDATE_DIR=/var/lib/domovoi-update
# DOMOVOI_UPDATE_KEEP_BACKUPS=5
# Replaced databases (<db>_failed_<time>) a rollback keeps for inspection, newest first.
# DOMOVOI_UPDATE_KEEP_FAILED_DBS=1
# 0 lets an update go ahead when the pre-update backup fails (then no restore is possible).
# DOMOVOI_UPDATE_REQUIRE_BACKUP=1
# DOMOVOI_UPDATE_HEALTH_TIMEOUT=120
# How long the stop step waits for web and core before SIGKILLing what is still stopping.
# DOMOVOI_UPDATE_STOP_TIMEOUT=40
# DOMOVOI_CORE_HEALTH_URL=http://127.0.0.1:6370/v1/health
# DOMOVOI_WEB_HEALTH_URL=http://127.0.0.1:6369/api/health
# DOMOVOI_PG_CONTAINER=domovoi-postgres
# DOMOVOI_PG_USER=domovoi
# Its <db>_test twin is backed up and restored with it whenever that exists.
# DOMOVOI_PG_DB=domovoi
# Where the core records the pre-pull SHA (its UPDATE_STATE_DIR). Default: ~<service user>/.domovoi/update
# DOMOVOI_CORE_STATE_DIR=/home/domovoi/.domovoi/update
# Default: MPD_IMAGE_TAG / MPD_CONTAINER_PREFIX from domovoi/.env, else these.
# DOMOVOI_MPD_IMAGE_TAG=domovoi-mpd:latest
# DOMOVOI_MPD_CONTAINER_PREFIX=domovoi-mpd-
# Signed updates (below). Enforcement is this file's presence; the core reads the path from here too.
# DOMOVOI_ALLOWED_SIGNERS=/etc/domovoi/allowed_signers
# A retired key's revocation file (ssh-keygen -Y verify -r). Default: none.
# DOMOVOI_REVOKED_SIGNERS=/etc/domovoi/revoked_signers
# The upstream the checkout must follow; a checkout on another remote or branch is refused. Default: not pinned.
# DOMOVOI_UPSTREAM_URL=https://github.com/coders-farm-official/domovoi
# One branch, or several separated by spaces or commas. provision.sh writes the two release channels:
# DOMOVOI_UPSTREAM_BRANCH="stable main"
# The core reads this file as the service user (the pin, the signers path): keep it readable (0644).
# 0: leave Ubuntu's background jobs alone (step 12, Internet or not). Default: they follow the answer.
# DOMOVOI_MANAGE_OS_JOBS=1
# 0: leave Docker's GPU runtime (the NVIDIA Container Toolkit) alone (step 7, Wake-word training).
# provision.sh reads it too. Default: set up where an NVIDIA GPU is visible.
# DOMOVOI_MANAGE_GPU_RUNTIME=1
# 0: never rebuild the wake-word trainer image (step 13). Default: rebuilt when scripts/wake_word/docker changed.
# DOMOVOI_MANAGE_WAKE_TRAINER=1
# The trainer image's name. Default: WAKE_TRAINER_IMAGE from domovoi/.env, else this.
# DOMOVOI_WAKE_TRAINER_IMAGE=domovoi-wake-trainer:latest
# Its rebuild in a transient unit: auto (when systemd runs the box), 1, or 0 (in the foreground, bounded below).
# DOMOVOI_UPDATE_TRAINER_DETACH=auto
# DOMOVOI_UPDATE_TRAINER_TIMEOUT=1800
# 0: leave the journal where it is (The loop watchdog). Default: kept on disk (journald's own size limits).
# DOMOVOI_MANAGE_JOURNAL=1
# The update verifier's fetch into root's mirror (The update verifier): seconds, and KB a file.
# DOMOVOI_VERIFY_FETCH_TIMEOUT=900
# DOMOVOI_VERIFY_FETCH_MAX_KB=2097152
```

**The sudoers grant.** The button needs one more single-command rule,
alongside (not instead of) the restart rule in [2b](#2b-restart-from-the-dashboard-needs-one-sudoers-line):

```bash
sudo visudo -f /etc/sudoers.d/domovoi-update
```

```
domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block start domovoi-update.service
```

Check it as the service user, without starting anything:

```bash
sudo -u domovoi sudo -n -l /usr/bin/systemctl --no-block start domovoi-update.service
```

How the core decides which restart to run: it looks for
`domovoi-update.service` in the systemd unit directories (a plain file
check, no sudo). Found, the button starts the unit, and only if the rule
above exists. Without that rule the panel says so and shows the manual
command; it does not fall back to a plain bounce, which would skip the
migrations. Not found, the button bounces core and web exactly as before.
`systemctl mask domovoi-update.service` switches back to the plain bounce.

### Release channels

One version for the whole project, one tag per release (`v1.0.0`,
`v1.0.1`, `v1.1.0-rc1`), and two branches a box can follow for updates:

| Channel | What it holds | Who follows it |
|---|---|---|
| `stable` | The latest release's commit, nothing else. It only ever fast-forwards, once a release is complete (`git push origin "v1.0.1^{commit}:refs/heads/stable"`), and never gets a release candidate. | New installs: `provision.sh`'s default, and a release build of the Windows installer. |
| `main` | Every change, as it lands. Release candidates are tags on it. | Boxes installed before the channels, until someone switches them; whoever wants changes before they are released. |

The channel is the branch the checkout tracks, so the check, **Pull the
latest**, the rolled-back `bad_sha` and the update unit's pin all work as
before. Settings → Configuration → Version shows:

- the release label of the code that runs: `1.0.0` on a release, `1.0.0 +
  14 (abc1234)` past one (`git describe --tags --match "v[0-9]*"`);
- **updates**: the channel, and for an admin a choice between the two;
- after **Check for updates**, on `stable`: "Domovoi 1.0.1 is available",
  "the latest release", or, on a box that runs code newer than the latest
  release (switched from `main`), that updates resume with the next
  release that includes it; on `main`: how many commits behind, and the
  latest release.

**Switching** (an admin, security tier: `POST /api/config/version/channel`)
makes the local branch of that name at the commit the box already runs and
tracks `origin/<channel>`. It never moves HEAD or a file, so it needs no
restart and leaves the update unit's `applied_sha` and `bad_sha` as they
were; the next check says what is new there, and pulling stays its own
step. `main` → `stable` never takes a box backwards: past the latest
release, it waits for the next one. `stable` → `main` is a fast-forward
away. A switch is refused, changing nothing, while an update runs, under
**No** (it fetches), for a channel the update unit's pin doesn't allow, for
a box whose code is on neither branch's line, and before the first release
(`stable` doesn't exist yet). Nothing switches by itself: an install that
asked for `stable` before there was one follows `main` (it remembers what
was asked for, `git config domovoi.intendedChannel stable`), and the
Version card says so once the first release is out.

The branches are fetched first, then the tags on their own (never
forced). A published tag never moves; if one upstream ever did, git keeps
this box's copy ("would clobber existing tag"), and the card shows a
warning that a version label may be off. The check, the pull and the
switch go on: one moved tag never stops a box from updating.

To follow a channel by hand, on a box without the dashboard:

```bash
sudo -u domovoi git -C /opt/domovoi fetch --tags origin
sudo -u domovoi git -C /opt/domovoi checkout -B stable          # at HEAD: nothing moves
sudo -u domovoi git -C /opt/domovoi branch --set-upstream-to origin/stable stable
```

### The hash-pinned lock

The update unit can re-sync the venv from `requirements-linux-py314.lock`
instead of pip's resolver, so that a substituted or tampered download
fails the update and it rolls back. That is opt-in: until
`/etc/default/domovoi-update` sets `DOMOVOI_USE_LOCK=1`, the re-sync
resolves from the index exactly as it did before the lock existed, and
its step says the lock is there. The reason is the first locked re-sync:
it moves every package to the lock's version, and the lock in the
repository is seeded from the test suite's set, not from what your box
runs (torch, faster-whisper and the rest of the speech stack are the
newest that resolved).

**A box `provision.sh` installed is opted in already.** It was installed
from the lock (the installs under [By hand](#by-hand)), so its venv
holds the lock's versions and there is nothing to re-seed: the script
writes `DOMOVOI_USE_LOCK=1` into `/etc/default/domovoi-update` (a file
that already sets `DOMOVOI_USE_LOCK`, to anything, is left as it is, with
a warning when that is not `1`), and its updates install the way it was
installed. Set it to `0` to go back to the resolver. A box set up by hand
from the resolver, like the first Linux servers, keeps the default, off,
until you opt it in.

The locked re-sync installs the build tools the way the manual install
does: the lock's own entries for `setuptools`, `wheel` and what they need
in the lock (found through its `# via` lines; today `packaging`), with
their hashes, as a requirements file (`pip install --require-hashes -r`).
Not `pip install --require-hashes -c <lock> setuptools wheel`: the pip in
Ubuntu 26.04's venv (25.1) takes no hashes from a constraints file and
refuses that, so before 2026-10-11 an opted-in Ubuntu 26.04 box rolled
back every update that changed its dependencies.

To opt in a box you installed by hand from the resolver, re-seed the lock
from it first:

1. On the box, write down what its venv runs:

   ```bash
   sudo -u domovoi /opt/domovoi/.venv/bin/python -m pip freeze --exclude-editable >domovoi-freeze.txt
   ```

2. On a machine with Docker, in a checkout of the same commit, compile
   the lock from that freeze, check it, and commit it. Every version that
   still satisfies `pyproject.toml` is kept, so the first locked re-sync
   moves only what the floors require:

   ```bash
   DOMOVOI_LOCK_SEED=domovoi-freeze.txt bash scripts/linux/compile-linux-lock.sh
   USE_STUBS=true PYTHONPATH=. python -m pytest domovoi/tests/test_ops_linux_lock.py
   ```

   Where the freeze and `requirements.lock` disagree on a package both
   carry, decide which wins (the suite's tested version is the safer
   default); the seed is one file, so merge the two first if you want
   some of each.

3. Pull that commit to the box, opt in, and let the installer's
   pre-flight say what an update will do:

   ```bash
   echo 'DOMOVOI_USE_LOCK=1' | sudo tee -a /etc/default/domovoi-update
   V=/usr/local/lib/domovoi-update/verify-update.sh
   sudo test -f "$V" || V=/opt/domovoi/scripts/linux/update-verifier.sh   # before the first install only
   COPY=$(sudo DOMOVOI_REPO_DIR=/opt/domovoi bash "$V" export /run/domovoi-update-install)
   sudo bash "$COPY/scripts/linux/install-update-unit.sh" --dry-run
   ```

   It reports `dependency lock: on` when an update would install from
   the lock, and warns when it could not (no lock in the checkout, or a
   lock for another Python than the venv's). Without the setting it
   reports `dependency lock: off`.

From then on an update that changes `pyproject.toml` or a lock installs
from the lock, every package hash-checked ([step 5](#updates-from-the-dashboard)).
A package that does not match its hash fails the update, and it rolls
back. If a later update can't use the lock at all (the venv moved to a
newer Python, the lock was renamed, `DOMOVOI_DEPS_LOCK` was emptied), it
does not stop: it resolves from the index as before, but its `sync-deps`
step is a `warn`, the journal says WARNING, and Settings → Version shows
**lock not applied: <reason>** under the last update until the next run.
Re-seed and commit a lock for the new Python to go back to hash-checked
installs. A lock change is a dependency change, so under the **No**
internet answer that update is refused like any other. Remove the line
(or set it to `0`) to go back to the resolver.

Turning `dev` out of the default extras stops a re-sync adding the test
runner; it does not remove what an older install already has. Of the
`dev` extra (`pyproject.toml`, `[project.optional-dependencies]`) only
pytest and pytest-asyncio are not also production dependencies. On a box
that does not run the test suite, take them out once while you re-seed
(after step 1's freeze, so the freeze still names what the box ran):

```bash
sudo -u domovoi /opt/domovoi/.venv/bin/python -m pip uninstall -y pytest pytest-asyncio
```

### Signed updates

What bounds an update is a signature. Without one, a compromise of the
repository's `main` on GitHub (a stolen token, a compromised laptop, a
merged pull request that edits `scripts/linux/apply-update.sh`,
`domovoi/Dockerfile.mpd` or `pyproject.toml`) is root on every Linux
install at its next **Restart to apply changes**: the update unit runs the
pulled commit's own scripts as root, and the attacker needs no LAN access
and no credential to get there. Signing closes that. Every commit on
`main` carries an SSH signature by one of your keys, the box holds the
list of those keys in a file only root can write, and nothing of a commit
that does not verify runs as root ([The update verifier](#the-update-verifier)
makes sure root never runs the working tree instead).

**Enforcement is the file.** While `/etc/domovoi/allowed_signers` exists,
both halves of the pipeline check against it and refuse otherwise. While it
doesn't, both go on and say so, loudly: the core's log on every pull, the
update's journal and a `warn` step in **last update** on every run, and an
**unsigned** badge beside the version in Settings. So an install that
hasn't set signing up keeps updating, and removing the file turns
enforcement off again. Two checks, in this order:

- **Before the pull** (`POST /v1/admin/version/pull`; the core, as the
  service user): `git fetch`, then `git verify-commit` on the fetched tip,
  then `git merge --ff-only` to that exact SHA. A tip that does not verify
  is refused with `pulled: false` and the tree never moves. This is the
  gate for the dashboard path: the code doing the checking is the code
  already loaded, which the pull cannot change.
- **Before anything runs as root** (the update verifier, every run, the
  plain restart as much as the full update): root fetches HEAD into its own
  mirror and runs `git verify-commit` there itself, not through `runuser`
  and not taking the core's word for it, with the signers file and every
  program git could be told to run pinned on the command line, and no
  replacement object or graft followed. A HEAD that does not verify is
  `refused`, nothing of it run, nothing stopped or changed, and the result
  says what to do. The signers file must be a plain file owned by root that
  no one else can write, or it is refused as well. The copy's
  `apply-update.sh` checks again, in the same mirror, and records the
  verdict as the `signature` step.

So a tree moved to an unverified commit by a pull that skipped the core
(`git pull` by hand as the service user) is refused at the next Restart
too, before anything of it runs. Pull through the dashboard, or by hand
with the same check git offers, so the pull itself is refused instead:

```bash
sudo -u domovoi git -C /opt/domovoi -c gpg.ssh.allowedSignersFile=/etc/domovoi/allowed_signers pull --ff-only --verify-signatures
```

(After a pull by hand, run `env_bootstrap --repair` before you restart
anything yourself; see [Helper-container secrets](#two-more-linux-notes).)

With signing enforced and the verifier in the unit, that bound holds
against code running as the service user too, as far as the update goes:
whatever it does to the working tree, root runs only signed commits. It
still runs the core, the dashboard and the plugins it can edit, and the
service user is in the `docker` group, which is root-equivalent on its own
([SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#signed-updates-what-they-bound)).

**Set it up.** On the machine you commit from (once per machine):

```bash
ssh-keygen -t ed25519 -C "domovoi commit signing" -f ~/.ssh/domovoi_signing
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/domovoi_signing.pub
git config --global commit.gpgsign true
git config --global tag.gpgsign true
```

Every commit you make from then on is signed, merges included. **Merges
made by tools must be signed too.** A merge an agent, a script or GitHub's
merge button makes with an identity that doesn't sign is unsigned, and the
box refuses it (the signature is on the tip; a signed tip vouches for
everything under it, so signed merge commits of unsigned work are fine).
Merge locally with the configuration above in force, or sign a merge after
the fact with `git commit --amend -S --no-edit`, and turn on GitHub's
**Require signed commits** branch protection on `main` so an unsigned
commit can't land there in the first place. `git log --show-signature -1`
checks any commit. Only SSH signatures are checked on the box: an
OpenPGP-signed commit reads as unverified there (no keyring).

On the box, install the allowed-signers file: one line per key, a
principal (any name; it's what the version panel shows as the signer),
the `git` namespace, then the public key as its `.pub` file has it. The
first command, on the machine you commit from, prints the line; the rest,
as your admin user on the box, put it in place owned by root:

```bash
printf 'alex namespaces="git" %s\n' "$(cut -d' ' -f1,2 ~/.ssh/domovoi_signing.pub)"

sudo install -d -m 0755 -o root -g root /etc/domovoi
echo 'alex namespaces="git" ssh-ed25519 AAAA...' | sudo tee /etc/domovoi/allowed_signers >/dev/null
sudo chmod 0644 /etc/domovoi/allowed_signers
```

Then check that HEAD verifies the way the unit will, before the next
update has to: the verifier itself, which makes a copy and changes nothing
else, then the installer's pre-flight from that copy:

```bash
V=/usr/local/lib/domovoi-update/verify-update.sh
sudo test -f "$V" || V=/opt/domovoi/scripts/linux/update-verifier.sh   # before the first install only
COPY=$(sudo DOMOVOI_REPO_DIR=/opt/domovoi bash "$V" export /run/domovoi-update-install)
sudo bash "$COPY/scripts/linux/install-update-unit.sh" --dry-run
```

The `export` says `HEAD is signed by alex (SHA256:...), listed in /etc/domovoi/allowed_signers`
(or `refused:` and why) and prints the copy's directory.
The installer's pre-flight reports `signed updates enforced: HEAD ... is signed by alex`,
or stops, changing nothing, if HEAD would be refused. If the checkout is
still on a commit from before you started signing, pull a signed one first
(**Pull the latest** verifies it), then restart. Several people can sign:
one line each. A key is retired by removing its line; to retire it while
its old commits stay verifiable, list it in a revocation file instead
(`ssh-keygen -Y verify -r`) and set `DOMOVOI_REVOKED_SIGNERS`.

**Pin the upstream** as well, so a checkout pointed at another remote or
branch is refused before it is even verified. In
`/etc/default/domovoi-update`:

```bash
DOMOVOI_UPSTREAM_URL=https://github.com/coders-farm-official/domovoi
DOMOVOI_UPSTREAM_BRANCH=main
```

`origin` must have that URL (a trailing `.git` or `/` doesn't matter) and
HEAD must track `origin/main`, or the run is `refused` with an `upstream`
step saying which. `DOMOVOI_UPSTREAM_BRANCH` may name several branches,
separated by spaces or commas: `provision.sh` writes `"stable main"`, the
two [release channels](#release-channels), so the dashboard's channel
switch works and any other branch is refused. With a branch pin, root also
checks that HEAD itself is on one of the pinned branches as this box last
fetched them (the tracking branch is the service user's config), on the
plain restart too, and the core's check and pull compare with, and merge,
`refs/remotes/origin/<branch>` by name rather than the checkout's `@{u}`.
If a pinned branch was rewritten upstream past this box's HEAD, the commit
the box last applied still restarts (with an `upstream` warning on the
Version card); an update to anything else is refused until the branch
holds it again. The pin catches mistakes, not an adversary: those refs
live in the service user's own `.git`, which that account can rewrite, so
only [signed updates](#signed-updates) bound what root runs. Root's git
calls (here, in `provision.sh` and in the installer) ignore `refs/replace`
and grafts (`GIT_NO_REPLACE_OBJECTS=1`, `GIT_GRAFT_FILE=/dev/null`), so a
signature check always sees the commit HEAD names.
Until signed updates are switched on, the pin catches a mistake (a
checkout pointed at the wrong branch), not code on the box: the branches
it checks HEAD against are the ones the checkout last fetched, copied into
root's mirror from the checkout, and the service user can rewrite those
(the review of the root verifier, case R4). With signing enforced it is
the signature that bounds what runs. What signing doesn't block yet
(planned with 1.1's signed releases): moving HEAD back to an older commit
that was signed too.
Keep the file readable by the service user (`0644`; it holds no secrets):
the core reads the pin and the signers path from it, and while it can't,
the dashboard offers no channel switch and says why. All four settings are optional and read from that
root-owned file: `DOMOVOI_ALLOWED_SIGNERS` (default
`/etc/domovoi/allowed_signers`; the core reads the path from the same file,
so the two halves always look at one file), `DOMOVOI_REVOKED_SIGNERS`,
`DOMOVOI_UPSTREAM_URL`, `DOMOVOI_UPSTREAM_BRANCH`.

**What you see.** `GET /v1/admin/version` carries `signature: {status,
signer, key, enforced, allowed_signers, detail}`: `status` is `verified`,
`unsigned`, `unverified` (signed, but not by a listed key, or no file to
check against) or `unknown`; `signer` and `key` name a verified
signature's principal and fingerprint. The Settings → Version card shows
it as a badge beside the version: **signed**, **unsigned** or **signature
not verified**, red when enforcement would refuse an update and amber when
signing just isn't set up. `POST /v1/admin/version/pull` answers with the
same `signature` object, and a refusal as `pulled: false` with the reason
in `error`. Every update result carries it as `signature` too, plus a
`signature` step (`ok`, `warn` while not enforced, `refused`) and, when
pinned, an `upstream` step; a refused run has status `refused`, like a
dirty tree.

### One-time upgrade for existing installs

For a box already running the three units above, such as the household's
first server. One command sets it up. Over SSH, as your admin user:

1. Pull, but don't press Restart yet. Use **Pull the latest** in the
   dashboard, or:

   ```bash
   sudo -u domovoi git -C /opt/domovoi pull --ff-only
   ```

   The pull brings the installer. The core keeps running the old code until
   something restarts it, and that old SHA is the one the installer records
   as the rollback baseline.

2. Run the installer, from a verified copy of the checkout
   ([The update verifier](#the-update-verifier)). `--dry-run` says what it
   would do and changes nothing (the copy and the mirror it comes from are
   root's own cache); `--apply` goes on to run the update once, the way the
   button will:

   ```bash
   V=/usr/local/lib/domovoi-update/verify-update.sh
   sudo test -f "$V" || V=/opt/domovoi/scripts/linux/update-verifier.sh   # before the first install only
   COPY=$(sudo DOMOVOI_REPO_DIR=/opt/domovoi bash "$V" export /run/domovoi-update-install)
   sudo bash "$COPY/scripts/linux/install-update-unit.sh" --dry-run
   sudo bash "$COPY/scripts/linux/install-update-unit.sh" --apply
   ```

It does what the manual steps below do, after checking that the box can
take them:

- **Checks first, and changes nothing while it does.** systemd is running;
  `domovoi-db`, `domovoi-core` and `domovoi-web` are installed; git works
  in the checkout as the service user; `docker compose` works for that
  user; only root can write `/var/lib/domovoi-update` (or whatever
  `DOMOVOI_UPDATE_DIR` names), the directories above it and
  `/etc/default/domovoi-update`. With the allowed-signers file in place,
  HEAD verifies against it as root the way every update run will, and the
  pinned upstream (if any) is the checkout's; otherwise it stops rather
  than leave a unit that refuses every run ([Signed updates](#signed-updates)).
  If one of these fails it stops and says why. Three things only warn: a
  venv the service user doesn't wholly own (it prints the `chown` that
  fixes it, and `--fix-ownership` runs it, last, and only on a directory
  with a `pyvenv.cfg`), the voice's runtime (`onnxruntime` missing or older
  than 1.28, or `piper-tts` still in the venv), and signed updates not set
  up yet. It also says whether an update re-syncs the venv
  from the hash-pinned lock (`dependency lock: on`) or through pip's
  resolver (`off`, the default; [The hash-pinned lock](#the-hash-pinned-lock)),
  and warns when the lock is opted into but an update could not use it.
- **Records the rollback baseline** in `/var/lib/domovoi-update/applied_sha`
  when that file doesn't exist yet: the `running_sha` the core reports on
  `/v1/admin/version`, `-dirty` stripped, checked as a commit of the
  checkout by the service user. That read takes the household token, which
  the installer reads as the service user from its
  `~/.domovoi/device-token.txt` and hands curl on stdin. If the core isn't
  answering or refuses the read, or the SHA
  isn't in the checkout, it stops instead of guessing. HEAD is no stand-in,
  because a pull moves it past the running code.
- **Installs the grant** in `/etc/sudoers.d/domovoi-update` (mode 0440),
  only after `visudo -cf` has accepted it on its own. Then `visudo -c`
  checks the whole configuration, and if that fails the previous state is
  put back. Then it asks sudo, as the service user, exactly what the core
  asks before it offers the button.
- **Installs the update verifier**, `scripts/linux/update-verifier.sh`
  from beside the installer, as `/usr/local/lib/domovoi-update/verify-update.sh`
  (0755, root's; it stops if anyone but root can write that directory or
  one above it), then **the unit** exactly as shown above, and runs
  `systemctl daemon-reload`. The unit goes in after the grant is verified,
  because the core offers the full update as soon as it sees the unit file,
  and without the grant the button would stop working instead of falling
  back to the plain bounce.
- With `--apply`, **starts the update** (`systemctl start` waits for it)
  and prints the status from `last-result.json`. Step 4 below says what
  each status means.

If it stops after it has put the grant, the verifier or the unit in place
(a failed check, a failed write, Ctrl-C), it takes them back, so the Restart button
keeps doing what it did before. The recorded baseline stays: it is still
the SHA the core runs, and a later run couldn't learn it once something
restarts the core.

A second run changes nothing and says so. A file it replaces is kept
beside it as `<name>.bak-<timestamp>`, a name sudo and systemd both ignore.
Its defaults are this page's layout: the checkout is `domovoi-core.service`'s
`WorkingDirectory` (else `/opt/domovoi`), the service user is the
checkout's owner and must be the core unit's `User=`, and the core answers
on `http://127.0.0.1:6370`. `--repo`, `--user` and `--core-url` change
them; `--core-url` has to be this box (`127.0.0.1`, `localhost` or
`[::1]`), because the SHA it reports must be the one this checkout runs.
It reads `/etc/default/domovoi-update` the way the unit will. `--help`
lists the options.

If something restarted the core onto the pulled code before the installer
ran, that code is what it records. Flyway still runs on every update, but
new dependencies or a new MPD image from that pull won't be installed:
re-sync the venv as in [Install](#install) if `pyproject.toml` changed.

From then on, **Pull the latest** followed by **Restart to apply changes**
runs the whole pipeline, and **last update** in the version panel shows how
it went. The old restart rule in `/etc/sudoers.d/domovoi-restart` can stay.
It's used only if the unit is ever removed or masked.

A box whose update unit predates the verifier (installed before
2026-10-16) needs none of this again: its next **Restart to apply changes**
moves it over by itself ([The update verifier](#the-update-verifier),
"Existing installs").

#### By hand

The same, without the installer. Run these over SSH, as your admin user,
in this order.

1. Record the SHA the box is **running** now as the last known-good one.
   Do this first, before you pull. The `/v1/admin/version` endpoint takes
   the household token, which the core keeps in the service user's
   `~/.domovoi/device-token.txt`, so no admin login is needed. The token
   goes to curl on stdin (`-H @-`) rather than on its command line:

   ```bash
   sudo install -d -m 0755 /var/lib/domovoi-update
   RUNNING=$(sudo -u domovoi sed 's/^/X-Device-Token: /' ~domovoi/.domovoi/device-token.txt \
     | curl -s -H @- http://127.0.0.1:6370/v1/admin/version \
     | python3 -c 'import json,sys; print(json.load(sys.stdin)["running_sha"].removesuffix("-dirty"))')
   sudo -u domovoi git -C /opt/domovoi rev-parse --verify "$RUNNING^{commit}" | sudo tee /var/lib/domovoi-update/applied_sha
   ```

   `tee` must print a 40-character SHA. If it prints an error, stop there.

2. Pull, but don't press Restart yet. Use **Pull the latest** in the
   dashboard, or:

   ```bash
   sudo -u domovoi git -C /opt/domovoi pull --ff-only
   ```

3. Install the verifier, the unit and the grant. The verifier comes from
   the checkout this once (look at it first if anything but you could have
   written there); from then on every update replaces it with the verified
   copy's:

   ```bash
   sudo install -d -m 0755 -o root -g root /usr/local/lib/domovoi-update
   sudo install -m 0755 -o root -g root /opt/domovoi/scripts/linux/update-verifier.sh /usr/local/lib/domovoi-update/verify-update.sh
   sudo tee /etc/systemd/system/domovoi-update.service >/dev/null <<'EOF'
   [Unit]
   Description=Domovoi update (back up, sync, migrate, restart, roll back on failure)
   After=docker.service network-online.target
   Wants=network-online.target

   [Service]
   Type=oneshot
   Environment=DOMOVOI_REPO_DIR=/opt/domovoi
   EnvironmentFile=-/etc/default/domovoi-update
   RuntimeDirectory=domovoi-update
   ExecStartPre=/bin/bash /usr/local/lib/domovoi-update/verify-update.sh prepare
   ExecStart=/bin/bash /usr/local/lib/domovoi-update/verify-update.sh run
   TimeoutStartSec=30min
   EOF
   echo 'domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block start domovoi-update.service' >/tmp/domovoi-update.sudoers
   sudo visudo -cf /tmp/domovoi-update.sudoers && sudo install -m 0440 -o root -g root /tmp/domovoi-update.sudoers /etc/sudoers.d/domovoi-update
   sudo visudo -c
   sudo systemctl daemon-reload
   ```

   The rule is checked before it goes into place, because a broken file
   under `/etc/sudoers.d` can lock you out of `sudo`. `visudo -c` must then
   report every file `parsed OK`.

4. Apply the pull with the new pipeline. Without `--no-block`,
   `systemctl start` waits until the run finishes:

   ```bash
   sudo systemctl start domovoi-update.service
   cat /var/lib/domovoi-update/last-result.json
   ```

   Expect `"status": "ok"` and `"mode": "update"`. If it says `rolled_back`,
   the box is back on the old SHA and the `error` field says why.
   `journalctl -u domovoi-update -n 200` has the detail. That old SHA
   predates the script, so its Restart button is still the plain bounce:
   once the cause is fixed, pull again (step 2) and repeat this step by
   hand. `refused` means nothing was changed because the tree is not one
   the unit will apply: tracked files in `/opt/domovoi` have local changes
   (commit or stash them as `domovoi`), or some are hidden from `git
   status` (`git update-index --no-skip-worktree --no-assume-unchanged
   FILE` as `domovoi` shows them again), or HEAD does not verify against
   the allowed-signers file, or the checkout is not the pinned upstream
   ([Signed updates](#signed-updates)); the `error` says which, and you
   repeat this step once it's fixed.
   `aborted` means nothing was changed: the backup failed, or the
   dependencies changed and `domovoi` can't write the venv (`sudo chown -R
   domovoi: /opt/domovoi/.venv` fixes that one). This first run also
   recreates the `domovoi-postgres` container once, to pick up its new
   `restart: unless-stopped` policy. The data volume is untouched.

5. Check the grant the button will use:

   ```bash
   sudo -u domovoi sudo -n -l /usr/bin/systemctl --no-block start domovoi-update.service
   ```

---

## Two more Linux notes

**Firewall.** If `ufw` is on, satellites need `6370` and browsers need
`6369` from the LAN:

```bash
sudo ufw allow from 192.168.0.0/16 to any port 6369,6370 proto tcp
```

Postgres (`6432`), Letta (`6283`), SearXNG (`6888`) and every room's MPD
*control* port (`6650`, `6651`, …) are published on `127.0.0.1` only, so
no firewall rule is needed to keep them off the LAN — Docker never binds
them to a LAN interface in the first place. The per-room MPD *stream*
ports (`8050`, `8051`, …) are the exception: satellites pull the audio
from them, so they stay LAN-published, and `ufw` must allow them alongside
`6369`/`6370`:

```bash
sudo ufw allow from 192.168.0.0/16 to any port 8050:8099 proto tcp
```

**Rotating the Postgres password.** Postgres reads `POSTGRES_PASSWORD`
only when it initialises the `domovoi-pgdata` volume; after that the
credential lives in the database, so rotating is three steps:

```bash
# 1. change it in Postgres (the container is running)
docker exec -it domovoi-postgres psql -U domovoi -d domovoi \
  -c "ALTER USER domovoi WITH PASSWORD 'new-secret-here';"
# 2. put the same value in domovoi/.env, in BOTH lines:
#      DATABASE_URL=postgresql+asyncpg://domovoi:new-secret-here@localhost:6432/domovoi
#      POSTGRES_PASSWORD=new-secret-here
# 3. restart the core and the web backend (systemctl restart domovoi-core domovoi-web)
```

Flyway picks the new value up from `.env` on its next run (the compose
file interpolates it). Rotate right away if your install predates the
random-password bootstrap — its `.env` carries the template default, and
`python -m domovoi.env_bootstrap` deliberately leaves an existing `.env`
alone.

**Helper-container secrets.** `docker-compose.yml` takes the Letta server
password from `LETTA_TOKEN` and SearXNG's signing key from `SEARXNG_SECRET`
in `domovoi/.env`. `LETTA_TOKEN` is also what the core signs in to Letta
with, so the two always match. There is no fallback: until October 2026
an `.env` without them got values that were the same on every install,
so compose now refuses every command (`domovoi-db`'s `compose up postgres`
included) while either is missing, and says how to add them. A fresh
`.env` gets random values from `python -m domovoi.env_bootstrap`. An older
one gets them from

```bash
cd /opt/domovoi
sudo -u domovoi .venv/bin/python -m domovoi.env_bootstrap --repair
```

which appends a generated value for each one the file lacks (or leaves
empty, or sets to the old shared value) and changes nothing else; run
again, it writes nothing. You rarely type it: the
[update unit](#updates-from-the-dashboard) runs it as the service user
before anything uses compose, on every update and every Restart (a value
it adds is an `env-secrets` step in **last update**), and `dev.sh` /
`dev.ps1` run it too. **Updating by hand** (`git pull`, then restarting the
units yourself)? Run it once after the pull and before the restart, or
`domovoi-db` refuses to start and takes the core with it.

The search helper takes its new value the next time it is started (the
update unit starts it under Yes or Sometimes). Letta, if chat mode runs it,
keeps its old password until it is recreated, while the core signs in
with the new one after its restart: recreate it (the update unit records
this as a `warn` with the same line):

```bash
cd /opt/domovoi/domovoi
docker compose --profile chat up -d letta         # if chat mode is on
sudo systemctl restart domovoi-core domovoi-web   # the core reads LETTA_TOKEN at start
```

Letta reads its password from the environment when it starts, so that is
the whole rotation. To rotate later, delete the line from `.env`, run the
repair, and do the same two steps.

**Don't suspend.** Desktop-oriented installs sometimes ship with sleep
targets enabled, which is fatal for a machine satellites reconnect to:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```
