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
| **Wake-word training** | openWakeWord's training pipeline (piper-sample-generator) is Linux-only, so `wake_word_train_command` has to shell out to WSL2, Docker, or Colab. | Native. Point the setting at the pipeline directly — no bridge. See [`scripts/wake_word/README.md`](../scripts/wake_word/README.md). |
| **External binaries** (`ffmpeg`, `fpcalc`/Chromaprint, `mpg123`) | Download, unzip, put on PATH by hand. | `apt install`. |
| **`resemblyzer` install** | Needs `pip install --no-deps resemblyzer` because its `webrtcvad` pin has no Windows wheels and would need MSVC to build. | `webrtcvad` builds from source with `gcc` + `python3-dev`, so try the plain install first. |
| **Docker** | Docker Desktop — a whole application, its own RAM overhead, its own licensing question. | Native Docker Engine. The core drives containers through the `docker` CLI (`mpd_provisioner.py`), so nothing else changes. |
| **Autostart** | Scheduled task at boot, or a service wrapper. Fiddly. | systemd. Units below. |
| **Ollama on an AMD/Intel iGPU** | ROCm support on Windows is thin. | Better supported, if you ever want to experiment. Still optional — see [CPU_HOST.md](CPU_HOST.md#what-about-the-integrated-gpu). |

## What to know going in

Four differences. Two need something from you; two are already handled
in-repo and are listed so you know why you don't have to think about
them.

### 1. The system-voice fallback needs a package installed

TTS falls back through `piper → system` (`edge → piper → system` if you
chose Edge; Edge is never a fallback for Piper). The `system` rung is the
OS's own synthesizer —
pyttsx3/SAPI on Windows, and **`espeak-ng` on Linux**. It's
robotic, and that's the point: it's the floor that still talks when the
network is down *and* the Piper voice is missing or broken.

`espeak-ng` isn't installed by default on a server image, and without it
the chain ends at Piper with nothing beneath it. One package:

```bash
sudo apt install -y espeak-ng
```

(It's already in the [prerequisites](#install) below. `espeak` works too if
that's what your distro carries.) Either way, download a Piper voice and
confirm it renders — `espeak-ng` is a safety net, not a voice you want the
house to hear day to day.

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
long poles are `numba` (pulled in by `librosa`), `torch`, and
`ctranslate2` (under `faster-whisper`) — `numba` is reliably the last to
land, so it's the one to watch.

```bash
python3 -c "import json,urllib.request as u,re; [print(p, sorted({m.group(1) for f in json.load(u.urlopen(f'https://pypi.org/pypi/{p}/json'))['urls'] for m in [re.search(r'-(cp3\d+)-',f['filename'])] if m and 'manylinux' in f['filename'] and 'x86_64' in f['filename']})) for p in ('numba','torch','ctranslate2','shazamio-core')]"
```

If your target Python's tag is in all three lists, you're clear.

### Where that stood in August 2026

Every compiled dependency had cp314 manylinux x86_64 wheels: `torch`
2.13.0, `ctranslate2` 4.8.2, `numba` 0.67.0, `numpy` 2.5.2, `scipy`
1.18.1, `asyncpg` 0.31.0, plus `pillow`, `psutil`, `sqlalchemy`,
`miniaudio`, `lameenc`, `soxr`, `llvmlite`. `piper-tts` ships `cp39-abi3`,
which is forward-compatible with every CPython from 3.9 on. The NVIDIA
CUDA wheels are `py3-none-manylinux` — no Python tag at all.

Two dependencies still compile from source, and both are worth knowing
about because **neither shows up if you only check the packages named in
`pyproject.toml`** — they are transitive:

- **`webrtcvad`** (via `resemblyzer`) — sdist-only on *every* Python
  version, so not a 3.14 issue. See the `resemblyzer` note under
  [Install](#install).
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

Prerequisites:

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-dev build-essential pkg-config libasound2-dev git ffmpeg mpg123 libchromaprint-tools espeak-ng
```

`libchromaprint-tools` provides `fpcalc` for library enrichment,
`espeak-ng` is the last-resort TTS voice, and `python3-dev` +
`build-essential` are what let `webrtcvad` compile.

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
`real-clients` and `voice-profile` extras, `resemblyzer`, and CPU-only
`torch` from PyTorch's CPU index), every package at an exact version with
its SHA-256s, so a substituted or tampered download fails the install
instead of running. It was compiled for CPython 3.14 on x86_64, which is
what Ubuntu 26.04's `python3` is (`python3 --version`). Three commands, in
this order:

```bash
pip install --require-hashes -c requirements-linux-py314.lock setuptools wheel
pip install --require-hashes --no-build-isolation -r requirements-linux-py314.lock
pip install --no-deps --no-build-isolation -e .
```

The first installs the build tools, checked against the lock's hashes.
The second installs everything else; `metaphone` and `webrtcvad` publish
no wheels and are built here, with those checked tools rather than a
fresh, unchecked `setuptools` that pip would otherwise download for each
build (that is what `--no-build-isolation` is for, and why the
`python3-dev` and `build-essential` prerequisites are there). The third
installs Domovoi itself and nothing else. No `dev` extra: a server doesn't
run the test suite.

PyPI's default Linux `torch` build bundles its own CUDA runtime — cuBLAS,
cuDNN, NCCL, cuFFT, cuSOLVER, Triton and friends, about **2.7 GB** of it.
The lock pins the `+cpu` build from PyTorch's CPU index (it names that
index itself), so none of that arrives. That is entirely separate from the
`cuda` extra below, which only covers the wheels `ctranslate2` wants.

Check the voice encoder loads:

```bash
python -c "from resemblyzer import VoiceEncoder; VoiceEncoder()"
```

That should print `Loaded the voice encoder model on cpu in <N> seconds`.

**No lock for your Python?** On anything but CPython 3.14 (the
[`uv venv --python 3.13`](#if-the-wheels-arent-there-yet) route, an arm64
box the lock wasn't checked on), fall back to pip's resolver, which checks
no hashes and installs whatever the index serves that day within the
floors in `pyproject.toml`:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[real-clients,voice-profile]"
pip install resemblyzer      # if webrtcvad fails to build: pip install --no-deps resemblyzer
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
> Nothing about it installs unless you ask:
>
> ```bash
> pip install -e ".[fastlane]"
> python -m domovoi.fast_lane fetch      # optional: the 103 MB model now, not on first enable
> ```
>
> `sherpa-onnx` ships binary wheels for CPython 3.14 on x86_64 and
> aarch64 (and carries its own onnxruntime), so there is nothing to
> build. Then set `FASTLANE_MODE=shadow` in `domovoi/.env`, or pick
> **shadow** under Settings → Speech-to-text → *Fast lane*, which applies
> without a restart. If the [update unit](#updates-from-the-dashboard)
> manages this box, add `fastlane` to `DOMOVOI_PIP_EXTRAS` so a venv
> re-sync keeps it installed. Like `cuda`, it is outside the lock: pip
> resolves it from the index and checks no hashes, and the update unit's
> step says so.

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
  your town for local news, an AcoustID **application** key in
  `domovoi/.env` for song recognition, your radio market.
- **No.** While the box is still online, work through
  [Before you disconnect](INTERNET.md#before-you-disconnect): the Whisper
  fallback model, the Ollama models, the satellite media cache, and the
  time zone (`timedatectl` should show yours, not UTC; without internet,
  point `systemd-timesyncd` at a LAN time server if you have one). Then
  answer **No**: the server stops contacting anything outside your
  network, including the internet check, and sets `HF_HUB_OFFLINE=1` for
  itself. No drop-in is needed.
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
tell you so.

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
  caches and the default `~/Music`, `~/Pictures` and `~/Documents`.
  `domovoi-db` sees homes read-only and writes only its own.
- **Private `/tmp`** per unit, files the services create are not readable
  by other accounts (`UMask=0027`), and the kernel's tunables, modules,
  logs, control groups and hostname are off limits.
- **No set-uid files, no realtime scheduling, no new namespaces, native
  system calls only,** and sockets limited to local, IPv4, IPv6 and
  netlink.
- **`domovoi-web` and `domovoi-db`** also run with `NoNewPrivileges=yes`
  and no capabilities at all; `domovoi-db`, which only drives the `docker`
  CLI, gives up devices, the clock and writable-executable memory too.

What is deliberately left out, and why:

- **`NoNewPrivileges` on the core.** The version panel's restart runs
  `sudo -n systemctl ...` from the core ([2b](#2b-restart-from-the-dashboard-needs-one-sudoers-line)),
  and `NoNewPrivileges` stops sudo working. The web never runs sudo (its
  Restart button asks the core), so it keeps the setting.
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
scored 9.0 "UNSAFE" each; the shipped ones score about 5.9 for the core,
3.5 for the web and 1.8 for `domovoi-db`), Settings → Version still offers
**Restart Domovoi** (its `sudo -n -l` probe runs inside the sandbox), a
satellite turn answers, and a room's music starts (the core runs
`docker`).

**Already running units from an older copy of this page?** If they match
this page's layout, install the shipped files over them as above, then
`sudo systemctl daemon-reload` and restart the three. If you changed
paths or the user, copy the sandboxing block from the shipped file into a
drop-in instead (`sudo systemctl edit domovoi-core`, and the same for the
other two), with your paths in `ReadWritePaths=` and `BindPaths=`.

`domovoi-update.service` is not sandboxed: it runs as root to stop and
start the other units, back up the database, rebuild the room image and
roll everything back ([Updates from the dashboard](#updates-from-the-dashboard)).

### Internet or not

The internet answer needs nothing from the units. Saving it in Settings →
Internet starts or stops the search helper at once, the
[update unit](#updates-from-the-dashboard) keeps it in step after every
update, and under **No** the core sets `HF_HUB_OFFLINE=1` for itself (the
`HF_HUB_OFFLINE` drop-in older versions of this guide suggested is no
longer needed; leaving one in place does no harm). Whisper loads its model
from the local cache first on every answer.

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

## Updates from the dashboard

The version panel's **Pull the latest** fetches, verifies the fetched tip's
signature (see [Signed updates](#signed-updates)), and fast-forwards to
it. A pull can bring new Python dependencies, a new Flyway migration or a
new MPD image, and a bare restart of core and web applies none of them:
`domovoi-db` runs Flyway only when *it* starts. A migration that never ran
looks like a broken release (the V013 device-token table was the first
time this bit).

A fourth unit closes that gap. `domovoi-update.service` is a root oneshot
that runs [`scripts/linux/apply-update.sh`](../scripts/linux/apply-update.sh).
On a box that already runs the three units, one command installs it: see
[One-time upgrade for existing installs](#one-time-upgrade-for-existing-installs).
Once it's installed, the panel's **Restart to apply changes** and
**Restart Domovoi** both start it instead of bouncing core and web, and each
run does this:

1. Work out what changed since the last SHA it applied and saw healthy.
   If nothing did, it's a plain restart, which is what **Restart Domovoi**
   is when nothing is waiting: stop web and core, start them, and
   health-check them, usually in a few seconds. No backup, no dependency
   sync, no MPD rebuild, and `domovoi-db` is left alone, unless Postgres
   doesn't answer or its Flyway history is missing a migration the checkout
   ships: then `domovoi-db` is restarted first (compose up plus Flyway),
   as every plain restart did before 2026-10-05.
   If something did, it's the full update below, whichever button started
   it: code pulled since the panel last read the version, or code loaded
   outside the unit (pulled, then `domovoi-core` restarted by hand, so the
   checkout isn't the SHA the unit last applied). **Restart Domovoi** says
   so before anything happens: its confirm asks for the full update (a
   second confirm, when only the read taken at the press shows it), and
   the card says **Updating…** and waits up to 15 minutes, not seconds.
2. Refuse, touching nothing, if the checkout's `origin` or tracking branch
   is not the pinned upstream, or if signed updates are enforced and HEAD
   does not verify against the root-owned allowed-signers file (both in
   [Signed updates](#signed-updates); the first check runs before step 1,
   on the plain restart too). Refuse, touching nothing, if tracked files
   have uncommitted changes. A rollback could not restore that tree.
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
   changed: re-sync the venv. By default that is pip's resolver, as it
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
   a usable lock, the step says `WARNING` and uses the resolver.
6. If `domovoi/Dockerfile.mpd` or `domovoi/mpd.conf` changed: rebuild
   `domovoi-mpd:latest` exactly as the core does, and remove the room
   containers. The core recreates each one at startup from its `mpd_rooms`
   row, with the same data volume, so playlists and queues survive.
7. `systemctl restart domovoi-db`: compose up plus Flyway.
8. Start core and web. Both must answer `/v1/health` (core, :6370) and
   `/api/health` (web, :6369) within 120 s.
   On a healthy plain restart (step 1), the search-helper step below runs
   too; a plain restart that fails its health check is recorded as
   `failed` and changes nothing else (there is nothing to roll back).
9. Every plugin that loaded before the update must still load: none that
   was enabled and not at `load_error` may be at `load_error` now. The core
   never lets a failing plugin take it down, so its health check stays
   green through one, and without this step a plugin the new code breaks
   would drop out of the house under an `ok` update. The script reads each
   plugin's status from the `plugins` table through the `domovoi-postgres`
   container, as it does the migration counts; the core has recorded every
   plugin's load before it first answers `/v1/health`. A plugin that was
   already failing, or that you switched off, doesn't count, and neither
   does one that is new in the update.
10. Keep the search helper (SearXNG) in step with the internet answer,
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

Under `INTERNET_ACCESS=never` (read once, before anything is touched) an
update that would download is refused with status `aborted`, nothing
stopped or changed: one whose Python dependencies changed (pip), whose
`Dockerfile.mpd` changed (docker build pulls the base image and runs apt),
or whose `domovoi/docker-compose.yml` moves an `image:` pin (every image
there is pinned by digest, so `domovoi-db` would pull the new one).
An `mpd.conf`-only change keeps the image and just recreates the rooms.
To take such an update, follow
[INTERNET.md → Updating a box answered No](INTERNET.md#updating-a-box-answered-no).

If any of 4-9 fails, it rolls back: `git reset --keep` to the previous SHA
(never `--hard`), the venv re-synced and every package put back at its
exact pre-update version, the old MPD image rebuilt, and, if
`flyway_schema_history` or any plugin's migration ledger
(`plugin_<slug>.schema_history`) grew, the pre-update dump restored.
Plugin migrations count because Flyway never sees them: the core applies a
plugin's pending migrations when it starts, so a pull that adds one to a
bundled plugin changes the database in step 8, not step 7. Rolling the code
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
EnvironmentFile=-/etc/default/domovoi-update
ExecStart=/bin/bash /opt/domovoi/scripts/linux/apply-update.sh
TimeoutStartSec=30min
```

No `User=`: it runs as root, because it has to stop and start the other
units and read the database dump. It has no `[Install]` section either:
nothing starts it at boot, only the button or you.

Root runs the script straight from the checkout, which the service user can
write. That doesn't give the service user anything new: it is already in
the `docker` group, which is root-equivalent. The script also keeps root
away from the tree: git and pip run as the service user through
`runuser`, and root's own files live in `/var/lib/domovoi-update`, which the
service user can read but not write.

What that does not bound is upstream. The script root runs is whatever
the pull brought, so a compromise of the repository's `main` (a stolen
token, a merged pull request) would be root on this box at its next
**Restart to apply changes**, with no LAN access or credential needed.
The bound is a signature: see [Signed updates](#signed-updates). Until
you set it up, every pull and every update says so.

**`/etc/default/domovoi-update`** is optional. Every setting defaults to the
layout on this page, so you only need the file to change one:

```bash
# Service user. Default: User= of domovoi-core.service, else the owner of the checkout.
# DOMOVOI_USER=domovoi
# Default: the checkout the script lives in.
# DOMOVOI_REPO_DIR=/opt/domovoi
# Default: the venv domovoi-core.service's ExecStart runs from, else <checkout>/.venv.
# DOMOVOI_VENV=/opt/domovoi/.venv
# Extras for the venv re-sync. The lock covers real-clients and voice-profile; any other
# (an NVIDIA host's cuda, fastlane) is resolved from the index after it, without hash checks.
# DOMOVOI_PIP_EXTRAS=real-clients,voice-profile
# 1: the re-sync installs from the hash-pinned lock. Default: off, pip's resolver as before.
# Re-seed the lock from this box's pip freeze first (The hash-pinned lock, below).
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
# DOMOVOI_UPSTREAM_BRANCH=main
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

### The hash-pinned lock

The update unit can re-sync the venv from `requirements-linux-py314.lock`
instead of pip's resolver, so that a substituted or tampered download
fails the update and it rolls back. That is opt-in: until
`/etc/default/domovoi-update` sets `DOMOVOI_USE_LOCK=1`, the re-sync
resolves from the index exactly as it did before the lock existed, and
its step says the lock is there. The reason is the first locked re-sync:
it moves every package to the lock's version, and the lock in the
repository is seeded from the test suite's set, not from what your box
runs (torch, librosa, faster-whisper and the rest of the speech stack are
the newest that resolved). Re-seed it from the box first:

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
   sudo bash /opt/domovoi/scripts/linux/install-update-unit.sh --dry-run
   ```

   It reports `dependency lock: on` when an update would install from
   the lock, and warns when it could not (no lock in the checkout, or a
   lock for another Python than the venv's). Without the setting it
   reports `dependency lock: off`.

From then on an update that changes `pyproject.toml` or a lock installs
from the lock, every package hash-checked ([step 5](#updates-from-the-dashboard)).
A lock change is a dependency change, so under the **No** internet answer
that update is refused like any other. Remove the line (or set it to `0`)
to go back to the resolver.

### Signed updates

What bounds an update is a signature. Without one, a compromise of the
repository's `main` on GitHub (a stolen token, a compromised laptop, a
merged pull request that edits `scripts/linux/apply-update.sh`,
`domovoi/Dockerfile.mpd` or `pyproject.toml`) is root on every Linux
install at its next **Restart to apply changes**: the update unit runs the
pulled checkout's own script as root, and the attacker needs no LAN access
and no credential to get there. Signing closes that. Every commit on
`main` carries an SSH signature by one of your keys, the box holds the
list of those keys in a file only root can write, and nothing from a
checkout that does not verify runs as root.

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
- **Before anything runs as root** (`apply-update.sh`, every run, the
  plain restart as much as the full update): root runs `git verify-commit
  HEAD` itself, not through `runuser` and not taking the core's word for
  it, with the signers file and every program git could be told to run
  pinned on the command line (the checkout's `.git/config` belongs to the
  service user, and a `gpg.ssh.program` in it would otherwise choose what
  root executes). A HEAD that does not verify is `refused`, nothing
  stopped or changed, and the result says what to do. The signers file
  must be a plain file owned by root that no one else can write, or it is
  refused as well.

What this does not cover: a tree already moved to an unverified commit by
a pull that skipped the core (`git pull` by hand as the service user) has
already replaced the script root runs, and a check inside that script is
no check at all. Pull through the dashboard, or by hand with the same
check git offers:

```bash
sudo -u domovoi git -C /opt/domovoi -c gpg.ssh.allowedSignersFile=/etc/domovoi/allowed_signers pull --ff-only --verify-signatures
```

The service user is in the `docker` group and so root-equivalent already;
signing bounds *upstream*, not the service user.

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
printf 'kamron namespaces="git" %s\n' "$(cut -d' ' -f1,2 ~/.ssh/domovoi_signing.pub)"

sudo install -d -m 0755 -o root -g root /etc/domovoi
echo 'kamron namespaces="git" ssh-ed25519 AAAA...' | sudo tee /etc/domovoi/allowed_signers >/dev/null
sudo chmod 0644 /etc/domovoi/allowed_signers
```

Then check that HEAD verifies the way the unit will, before the next
update has to:

```bash
sudo git -C /opt/domovoi -c safe.directory=/opt/domovoi -c gpg.ssh.allowedSignersFile=/etc/domovoi/allowed_signers verify-commit HEAD
sudo bash /opt/domovoi/scripts/linux/install-update-unit.sh --dry-run
```

The first prints `Good "git" signature for kamron with ED25519 key ...`.
The second reports `signed updates enforced: HEAD ... is signed by kamron`,
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
step saying which. All four settings are optional and read from that
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

2. Run the installer. `--dry-run` says what it would do and changes
   nothing; `--apply` goes on to run the update once, the way the button
   will:

   ```bash
   sudo bash /opt/domovoi/scripts/linux/install-update-unit.sh --dry-run
   sudo bash /opt/domovoi/scripts/linux/install-update-unit.sh --apply
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
  with a `pyvenv.cfg`), `piper-tts` older than 1.3 in the venv, and signed
  updates not set up yet. It also says whether an update re-syncs the venv
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
- **Installs the unit** exactly as shown above, and runs
  `systemctl daemon-reload`. The unit goes in after the grant is verified,
  because the core offers the full update as soon as it sees the unit file,
  and without the grant the button would stop working instead of falling
  back to the plain bounce.
- With `--apply`, **starts the update** (`systemctl start` waits for it)
  and prints the status from `last-result.json`. Step 4 below says what
  each status means.

If it stops after it has put the grant or the unit in place (a failed
check, a failed write, Ctrl-C), it takes both back, so the Restart button
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

3. Install the unit and the grant:

   ```bash
   sudo tee /etc/systemd/system/domovoi-update.service >/dev/null <<'EOF'
   [Unit]
   Description=Domovoi update (back up, sync, migrate, restart, roll back on failure)
   After=docker.service network-online.target
   Wants=network-online.target

   [Service]
   Type=oneshot
   EnvironmentFile=-/etc/default/domovoi-update
   ExecStart=/bin/bash /opt/domovoi/scripts/linux/apply-update.sh
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
   (commit or stash them as `domovoi`), or HEAD does not verify against
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
with, so the two always match. A fresh `.env` gets random values from
`python -m domovoi.env_bootstrap`; an older `.env` without them falls back
to the historical values, which were the same on every install. Both
containers listen on `127.0.0.1` only, so on a dedicated box only the box's
own processes reach them, but give them their own values anyway:

```bash
cd /opt/domovoi/domovoi
gen() { python3 -c 'import secrets; print(secrets.token_urlsafe(24))'; }
printf 'LETTA_TOKEN=%s
SEARXNG_SECRET=%s
' "$(gen)" "$(gen)" >>.env
# Recreate whichever of the two runs, so it starts with the new value:
docker compose up -d --no-deps searxng            # if the search helper runs
docker compose --profile chat up -d letta         # if chat mode is on
sudo systemctl restart domovoi-core domovoi-web   # the core reads LETTA_TOKEN at start
```

Letta reads its password from the environment when it starts, so that is
the whole rotation. Skip the `searxng` line on a box answered No: the
helper stays stopped there.

**Don't suspend.** Desktop-oriented installs sometimes ship with sleep
targets enabled, which is fatal for a machine satellites reconnect to:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```
