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

TTS routes `edge → piper → system`. The `system` rung is the OS's own
synthesizer — pyttsx3/SAPI on Windows, and **`espeak-ng` on Linux**. It's
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

Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Then clone and install. A virtualenv is worth it here — it keeps Domovoi's
dependency tree away from the system Python that apt manages:

```bash
git clone https://github.com/coders-farm-official/domovoi && cd domovoi
```

```bash
python3 -m venv .venv && source .venv/bin/activate
```

**Install CPU-only torch first.** This step is not optional on a machine
without an NVIDIA GPU:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

PyPI's default Linux `torch` build bundles its own CUDA runtime — cuBLAS,
cuDNN, NCCL, cuFFT, cuSOLVER, Triton and friends, about **2.7 GB** of it —
and `torch` arrives via the `voice-profile` extra. That is entirely
separate from the `cuda` extra below, which only covers the wheels
`ctranslate2` wants. Installing the `+cpu` build first means the next
command sees `torch>=2.0` already satisfied and never pulls the CUDA one.

```bash
pip install -e ".[dev,real-clients,voice-profile]"
```

Then try `resemblyzer` the normal way — the `--no-deps` dance in the
README is a Windows workaround:

```bash
pip install resemblyzer
```

```bash
python -c "from resemblyzer import VoiceEncoder; VoiceEncoder()"
```

That should print `Loaded the voice encoder model on cpu in <N> seconds`.
If the install fails building `webrtcvad`, fall back to `pip install
--no-deps resemblyzer` — Domovoi doesn't use the code path that calls it.

> **CUDA wheels are opt-in.** They used to ride along with `real-clients`;
> they now live in a separate `cuda` extra, so the command above pulls
> nothing NVIDIA. On a machine that *does* have an NVIDIA GPU, add
> `pip install -e ".[cuda]"`.

Bring it up — `dev.sh` is the bash twin of `dev.ps1`:

```bash
./domovoi/scripts/dev.sh
```

and in a second terminal, from the repo root:

```bash
python -m web.backend.main
```

From here, rejoin the [setup runbook at Step 3](SETUP_RUNBOOK.md#step-3--claim-admin-and-configure-for-your-hardware)
to claim admin and apply your [CPU host settings](CPU_HOST.md).

---

## Make it an appliance

This is where Linux earns its keep. The runbook warns that `dev.ps1` is a
foreground development script that dies with its terminal — on Linux you
replace it properly with three systemd units.

Assumes the repo at `/opt/domovoi`, a venv at `/opt/domovoi/.venv`, and a
service user named `domovoi` who is in the `docker` group. Adjust to taste.

**`/etc/systemd/system/domovoi-db.service`** — brings up Postgres and runs
migrations before anything connects. Exactly what `dev.sh` does first:

```ini
[Unit]
Description=Domovoi database (Postgres + migrations)
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=domovoi
WorkingDirectory=/opt/domovoi/domovoi
ExecStart=/usr/bin/docker compose up -d postgres
ExecStart=/usr/bin/docker compose run --rm flyway

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/domovoi-core.service`** — the voice service on
:6370:

```ini
[Unit]
Description=Domovoi core voice service
After=domovoi-db.service ollama.service
Requires=domovoi-db.service
Wants=ollama.service

[Service]
Type=simple
User=domovoi
WorkingDirectory=/opt/domovoi
Environment=PYTHONUNBUFFERED=1
Environment=HOME=/home/domovoi
ExecStart=/opt/domovoi/.venv/bin/python -m domovoi.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/domovoi-web.service`** — the dashboard on :6369:

```ini
[Unit]
Description=Domovoi web dashboard
After=domovoi-core.service
Wants=domovoi-core.service

[Service]
Type=simple
User=domovoi
WorkingDirectory=/opt/domovoi
Environment=PYTHONUNBUFFERED=1
Environment=HOME=/home/domovoi
ExecStart=/opt/domovoi/.venv/bin/python -m web.backend.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Note `WorkingDirectory` differs: the two Python services run from the
**repo root** so `python -m` resolves the packages, while the database
unit runs from `domovoi/` where the compose file lives. Same split as the
dev scripts. No venv activation is needed — `ExecStart` names the venv's
interpreter directly, which is equivalent.

Two details worth not skipping:

- **`After=ollama.service`** keeps the core from starting before Ollama is
  listening. The core survives it either way, but the first routed turn
  after a reboot shouldn't have to fail first.
- **`Environment=HOME=...`** — `~/.domovoi/` holds your Piper voices,
  trained wake-word models, the sounds cache, and the first-boot setup
  code, all resolved through `Path.home()`. It would very likely resolve
  correctly without this, but a service quietly writing its state into the
  wrong home directory is a miserable thing to diagnose later. Set it and
  point it at the service user's real home.

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

---

## Two more Linux notes

**Firewall.** If `ufw` is on, satellites need `6370` and browsers need
`6369` from the LAN:

```bash
sudo ufw allow from 192.168.0.0/16 to any port 6369,6370 proto tcp
```

Leave Postgres (`6432`) closed to the LAN — nothing outside the server
needs it. SearXNG already binds to `127.0.0.1` only.

**Don't suspend.** Desktop-oriented installs sometimes ship with sleep
targets enabled, which is fatal for a machine satellites reconnect to:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```
