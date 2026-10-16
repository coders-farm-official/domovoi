# Portable Docker Wake-Word Trainer

A **capability-sensing**, **portable** openWakeWord training pipeline behind
the core's `wake_word_train_command` contract. Nothing is hardcoded to one
host (no fixed GPU index, no `~/.domovoi` path baked into the image, no
assumption a GPU even exists), so the same image trains on a Linux server's
Docker Engine, inside the Windows install's WSL distro, on a CPU-only box,
and in Docker Desktop on a development PC.

On a Domovoi server nobody runs any of this by hand: **Settings → Wake
Words → Turn on wake-word training** builds the image, checks the GPU,
downloads the training data and writes the command
([README.md, section 1](README.md#1-turn-training-on-once)). This page is
how the pieces work, and the hand route for development PCs.

openWakeWord automatic training is **Linux-only** (its `piper-sample-generator`
dependency needs `espeak-ng` and a C toolchain), so a Linux **Docker** container
is the universal substrate. The pipeline degrades GPU -> CPU at two layers:
the **host wrapper** passes `--gpus` only when it will actually work, and the
**container** routes to `cuda`/`cpu` via `torch.cuda.is_available()`.

```
domovoi worker  --shells out-->  train_wake_word.py  --docker run-->  container
(wake_word_trainer)   (4 placeholders)  (host, stdlib-only,    (GPU or CPU)   (entrypoint.py:
                                          senses docker+GPU)                   gen + train + onnx)
```

---

## Files

| File | Role |
|---|---|
| `scripts/wake_word/train_wake_word.py` | **Host wrapper**: what `wake_word_train_command` runs. Stdlib-only (the host may not have torch). Senses Docker and the GPU, removes a leftover training container, builds the `docker run` with its resource caps, streams the output, stops the container at its deadline or when told to stop, and returns the container's exit code unchanged. `--stop` removes every training container. |
| `scripts/wake_word/docker/Dockerfile` | **One image**, GPU or CPU. Base pinned by digest, the two upstream repositories by commit, every pip install held by `constraints.txt`; bakes the Piper generator checkpoint and the two feature models (SHA-256 checked). The ~18 GB training data is **not** baked: it lives in a named cache volume. |
| `scripts/wake_word/docker/constraints.txt` | The version of every Python package the image's pip installs bring in (`PIP_CONSTRAINT`). |
| `scripts/wake_word/docker/entrypoint.py` | **Container entrypoint**: lowers its own priority, detects GPU/CPU, downloads the data once, generates synthetic positives, injects the recorded clips, trains, writes `/data/models/<slug>.onnx`, exits non-zero on any failure. `--warm` downloads the data and exits. |
| `scripts/wake_word/docker/train_runner.py` | Runs `train.py` after giving every word of the phrase a pronunciation (the core's first, espeak-ng's for the rest). |
| `scripts/wake_word/docker/train_config.template.yaml` | openWakeWord training config, rendered per run from env. |
| `scripts/wake_word/docker/requirements.txt` | Readable list of what the Dockerfile installs and why (the versions are in `constraints.txt`). |

---

## How it senses GPU vs CPU

**Host wrapper (`train_wake_word.py`):**
1. `docker info`: is the daemon up? (clear error + exit 3 if not). Then the
   image must exist (exit 3, "image ... not found", if not), and any
   training container left over from an earlier run is removed.
2. **Real** GPU probe: `docker run --rm --gpus all --entrypoint python <image>
   -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)"`.
   This is the only reliable test: it works the same on Docker Desktop
   (toolkit bundled) and Docker Engine with the NVIDIA Container Toolkit. If
   it exits non-zero, `--gpus` is **left out** entirely so a GPU-less host
   never errors on the flag, and the log says `backend = CPU`.
3. If GPUs exist and there are several, pick the **least-used** via
   `nvidia-smi --query-gpu=index,uuid,memory.used` and pass `--gpus "device=<N>"`
   **plus `-e CUDA_VISIBLE_DEVICES=<that card's UUID>`**. Under WSL2 (Docker
   Desktop, or Docker Engine in a distro) `--gpus device=N` alone does not
   isolate a card: every GPU is reached through `/dev/dxg`, the container sees
   them all and torch takes `cuda:0`, so a run "on GPU 1" landed on GPU 0
   (measured 2026-10-13, both engines); the environment variable is what
   holds. nvidia-smi is looked up on PATH and then at `/usr/lib/wsl/lib/`,
   which a systemd unit's PATH lacks. Single GPU -> `--gpus all`. The chosen
   backend is logged (`backend = GPU device 1 GPU-… (of 3, least-used)` or
   `backend = CPU`).
4. `docker info` again for the Docker host's CPUs and memory, to size the
   resource caps ([below](#resource-caps-live-voice-first)).

**Container (`entrypoint.py`):** asks `torch.cuda.is_available()` (which honors
the `CUDA_VISIBLE_DEVICES` the runtime sets) and routes generation + training to
`cuda` or `cpu`. The **same image** does both: a CUDA image still runs on CPU
when launched without `--gpus`.

---

## Build OR pull

### What the dashboard does

**Turn on wake-word training** runs, as the core's service user, from the
checkout:

```bash
docker build --progress=plain \
  --label org.domovoi.wake-trainer.src=$(git rev-parse HEAD:scripts/wake_word/docker) \
  -t domovoi-wake-trainer:latest scripts/wake_word/docker
```

The label is the git tree id of the `docker` folder. On a Linux server or
the Windows install, the update unit (`scripts/linux/apply-update.sh`)
compares it with the checkout's after every update or Restart and rebuilds
the image, as the service user, when they differ: only where the image
exists (a household that turned training on), not when internet access is
turned off, and under systemd in a transient unit
(`domovoi-wake-trainer-build`, `journalctl -u domovoi-wake-trainer-build`)
the update does not wait for
([LINUX_HOST.md](../../docs/LINUX_HOST.md#updates-from-the-dashboard)).
Measured 2026-10-13: 7.8 min fresh inside WSL (the 3.2 GB base pull
included), about 3 min with the base image cached; 12.4 GB on disk. The
whole "Turn on" job (build, GPU check, data download) took 12.8 min in the
end-to-end test, and about 22.5 GB came over the network: the dashboard
says "about 22 GB, once".

### Build by hand (a development PC)

```bash
# from the repo root; the label keeps the update unit from rebuilding it needlessly
docker build --label org.domovoi.wake-trainer.src=$(git rev-parse HEAD:scripts/wake_word/docker) \
  -t domovoi-wake-trainer:latest scripts/wake_word/docker
```
GPU and CPU x86 hosts use the default base. **Apple Silicon / CPU-only arm**:
override the base (there is no arm64 CUDA image; the constraints were
resolved on the default base, so expect to adjust them):
```bash
docker build \
  --build-arg BASE=python:3.11-slim \
  --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu \
  -t domovoi-wake-trainer:latest scripts/wake_word/docker
```

### What the build pins

| Input | Pinned how |
|---|---|
| Base image `pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime` (Docker Hub) | By digest, `sha256:0a3b9fedefe1f61ac4d5a9de9015c0863db27ca0fde2d4e37e6268147980b726` (`ARG BASE`). |
| openWakeWord (`github.com/dscripka/openWakeWord`) | By commit, `OWW_REF` = `368c037` (the full SHA is in the Dockerfile). |
| piper-sample-generator (`github.com/dscripka/piper-sample-generator`) | By commit, `PSG_REF` = `f1988a4`. |
| Every Python package the pip installs bring in (PyPI) | `constraints.txt` through `PIP_CONSTRAINT`: the `pip freeze` of the image the 2026-10-13 spike trained with, less what the base ships; a rebuild's freeze is identical. |
| `en-us-libritts-high.pt` (Piper generator v1.0.0, GitHub release asset, ~255 MB) | SHA-256 checked at build. |
| `melspectrogram.onnx`, `embedding_model.onnx` (openWakeWord v0.5.1 release assets) | SHA-256 checked at build. |
| The apt packages (`espeak-ng`, `ffmpeg`, `build-essential`, ...) | Not pinned: whatever the base's Ubuntu archive serves that day, checked by apt against the archive's signature. |

The training data (downloaded at run time, not at build) is pinned in
`docker/entrypoint.py`:

| Input | Pinned how |
|---|---|
| `davidscripka/openwakeword_features` (Hugging Face) | `FEATURES_REVISION` = `985bf1b` (the dataset commit); both `.npy` files checked against `FEATURES_SHA256` (their LFS ids at that commit) before the first training uses them. A cache filled before the pins is checked once (about a minute for the 17.3 GB file), then `.oww_data_verified_<rev>` marks it. A mismatch refuses the run (exit 4). |
| `davidscripka/MIT_environmental_impulse_responses` (Hugging Face) | `RIRS_REVISION` = `b824a1e`. Its 270 WAVs are not hashed one by one: the commit holds them. |
| `fma_sample.zip`, `fsd50k_sample.zip` (Backblaze B2) | `BACKGROUND_ZIP_SHA256`, checked before unpacking; a mismatch refuses the run. |

To move a pin: change it, rebuild, train once, and check
`train_config.template.yaml` against the new commit's `train.py`.

### Publish once, pull everywhere

Building installs the full torch + training stack and bakes a ~255 MB TTS
model: 12.4 GB on disk. A group of development PCs can share one build:
```bash
docker tag  domovoi-wake-trainer:latest ghcr.io/<you>/domovoi-wake-trainer:1
docker push ghcr.io/<you>/domovoi-wake-trainer:1
# on each PC:
docker pull ghcr.io/<you>/domovoi-wake-trainer:1
# and in domovoi/.env (or the core's process environment):
WAKE_TRAINER_IMAGE=ghcr.io/<you>/domovoi-wake-trainer:1
```
The update unit never rebuilds an image whose name starts with a registry
host: pull a newer one yourself when `scripts/wake_word/docker` changes.
The dashboard's **Turn on wake-word training** always builds from the
checkout, under the configured name, so with a published image set the two
`.env` lines by hand instead ([Wire it into the Domovoi server](#wire-it-into-the-domovoi-server)).

---

## Build internals — the dependency set & upstream patches

openWakeWord's *training* path (`openwakeword/train.py` + `openwakeword/data.py`)
pulls a much larger stack than the inference install, and the upstream notebook
pins **2022-era versions that do not install on py3.11 / torch 2.4**. The image
installs these **modern, verified-working** versions instead (in a late
Dockerfile layer so the slow base layers stay cached; `constraints.txt`
holds the rest of the tree):

| Package | Version | Needed by |
|---|---|---|
| `torchinfo` | 1.8.0 | `train.py` |
| `torchmetrics` | 1.9.0 | `train.py` (Recall/Accuracy metrics) |
| `pronouncing` | 0.3.0 | `data.py` (adversarial-text generation) |
| `mutagen` | 1.48.1 | `data.py` |
| `speechbrain` | 1.1.0 | `data.py` (`read_audio`, `reverberate` — 1.1.0 keeps the `.dataio`/`.processing` import paths the trainer uses) |
| `torch-audiomentations` | 0.12.0 | `data.py` (GPU augmentation) |
| `acoustics` | 0.2.6 | `data.py` (`acoustics.generator.noise`) |
| `datasets` | **3.6.0** | entrypoint RIR download |
| `scipy` | **<1.15** (1.14.x) | pin reason below |

Two non-obvious pins and the patches below make it work — keep these if you bump
anything:

1. **`scipy<1.15`** — `acoustics 0.2.6` (newest on PyPI) imports
   `scipy.special.sph_harm` at module load; scipy **1.15 removed it**. 1.14.x
   still ships it and keeps `librosa`/`numba` happy. (We never use the
   `acoustics.directivity` submodule that needs it — only `generator.noise` —
   but the top-level `import acoustics` fails regardless.)
2. **`datasets==3.6.0`** — `datasets` **4.x/5.x** moved `Audio` decoding to
   `torchcodec` (needs a torch-matched FFmpeg build); streaming the MIT RIRs
   then raises *"please install 'torchcodec'"*. 3.6.0 decodes via `soundfile`
   and co-exists with the image's `huggingface_hub` 1.x.
3. **piper-sample-generator = the `dscripka` FORK, not `rhasspy` upstream.**
   `train.py` does `from generate_samples import generate_samples` and calls it
   with `auto_reduce_batch_size=...` and no `model=`. Only the dscripka fork has
   a **top-level** `generate_samples.py`, the `auto_reduce_batch_size` kwarg, and
   a **default** `model=` (the **v1.0.0** `en-us-libritts-high.pt`). rhasspy
   master moved the function into a package (`No module named 'generate_samples'`)
   and made `model` required. The image bakes the matching **v1.0.0** checkpoint
   (the v2.0.0 medium model is a different state_dict that won't load here).
4. **tflite-guard patch.** Every boolean flag in `train.py` is declared
   `action="store_true", default="False"` — and `"False"` is a *non-empty string*
   (truthy). The generate/augment/train guards dodge this with `is True`, but the
   tflite guard is a bare `if args.convert_to_tflite:`, so it fires on the default
   even when the flag is never passed, imports `onnx_tf`+`tensorflow`, and
   **crashes right after the `.onnx` is already written**. A one-line `sed`
   rewrites it to `is True`. **This is why we need NO tensorflow / onnx_tf** — we
   only want the `.onnx`, and that whole fragile 2022-pinned subtree stays out.
5. **Feature models baked at build.** `AudioFeatures` (used by both `--augment`
   and `--train`) hard-requires `melspectrogram.onnx` + `embedding_model.onnx` in
   `openwakeword/resources/models/`. The build runs
   `openwakeword.utils.download_models([])` to fetch them (v0.5.1 release assets)
   and checks both against SHA-256, so no network is needed mid-train.
6. **Words the CMU dictionary lacks** ("domovoi", "domo", most names).
   `--generate_clips` builds adversarial negatives from each phrase word's CMU
   pronunciation; for a missing word upstream downloads a DeepPhonemizer
   checkpoint from an S3 bucket that answers **403 for every model** (checked
   2026-10-13) and imports a `dp` package the image lacks, so "hey domovoi"
   failed every run with `No module named 'dp'`. The entrypoint runs `train.py`
   through `docker/train_runner.py`, which first gives every phrase word a
   pronunciation and logs where it came from:
   - **the host's first**: `WAKE_PRONUNCIATIONS` (`word=ARPABET;...`), which
     the core works out with Domovoi's own phonemizer (`domovoi/phonemizer/`,
     MIT): the "say it like" list (`domovoi` -> `D OW1 M OW2 V OY2`, from
     the shipped "Domovoi = DOH-moe-voy"), then its own model's guess for a
     word its dictionary lacks. These win, also over a CMU entry. Compared
     on 15 names, they were as good as espeak-ng's;
   - **espeak-ng** (already in the image for Piper) mapped to ARPAbet, only
     for a missing word the host sent nothing for (a hand run, an older core):
     `domovoi` -> `D AA1 M UW0 V OY2`.

   These shape the near-miss negatives.

   **The synthetic clips say it the household's way.** The Piper generator
   (dscripka's fork) phonemizes each phrase with espeak-ng itself and takes
   no phonemes, so espeak-ng said "domovoi" DOM-oo-voy (`dˈɑːmuːvˌɔɪ`). The
   core now also sends `WAKE_SYNTH_IPA` (`domovoi=dˈoʊmoʊvˌɔɪ`): the
   phonemizer's IPA, in the voice's espeak "en-us" symbols, for every
   phrase word the "say it like" list or the lexicon's own fixes cover.
   `train_runner.py` wraps `espeak_phonemizer.Phonemizer.phonemize` in its
   process, so the generator speaks those words from these phonemes, in
   the positives and in any negative containing them; a symbol the voice
   lacks leaves that word to espeak-ng. The run log says so:
   `synthesis: 'domovoi' spoken as dˈoʊmoʊvˌɔɪ (... espeak-ng would say
   dˈɑːmuːvˌɔɪ)`, the first phrases changed with their phonemes, and a
   count at the end (`104 of 135 phrases ...` in a small test run).
7. **Near-miss negatives from the phrase.** `custom_negative_phrases` used
   to be `[]`. The entrypoint now fills it (`train_runner.near_miss_phrases`)
   with the phrase with one word at a time swapped for a dictionary word
   that sounds near it: its start ("hey dome", "hey dom"), words spelled and
   starting like it ("hey domino", "hey dominate"), words a phone away
   ("hello demo"); call words ("hey", "hello") stay. Worked out from the
   CMU dictionary and the household's reading; nothing is listed per wake
   word. The list is in the run log (`near-miss negatives (7): ...`).
8. **Retraining picks up new clips.** `--augment_clips` does nothing when
   `positive_features_train.npy` exists, and the run dir is kept per slug, so a
   retrain after recording more clips used to reuse the first run's features.
   The entrypoint stores a digest of the real clip set the features were
   computed from (`.real_clips.sha256`, written after a successful augment) and
   drops the four feature files when the set changes; the run's `real_*.wav`
   copies are removed at its end (finished, failed or stopped: they are
   recordings of somebody's voice) and made again by the next run, and every
   run starts by removing any an earlier, killed run left. Synthetic clips
   stay cached.

**`--shm-size`.** The training DataLoader's worker processes pass batches through
`/dev/shm`; Docker's 64 MB default overflows mid-train
(*"RuntimeError: No space left on device"* on a `/torch_*` file). The host
wrapper passes `--shm-size 2g` (override `WAKE_TRAINER_SHM_SIZE`); if you ever
run the container by hand, add `--shm-size=2g` yourself.

### Env knobs (host wrapper → container)

| Host env | Container env | Default | Effect |
|---|---|---|---|
| `WAKE_TRAINER_N_SAMPLES` | `WAKE_N_SAMPLES` | image: 5000 | synthetic positives/negatives to generate |
| `WAKE_TRAINER_N_SAMPLES_VAL` | `WAKE_N_SAMPLES_VAL` | image: 1000 | synthetic validation clips |
| `WAKE_TRAINER_STEPS` | `WAKE_STEPS` | image: 50000 | training steps (sequence-1 max; seq 2/3 use steps/10) |
| `WAKE_TRAINER_REAL_TARGET_FRACTION` | `WAKE_REAL_TARGET_FRACTION` | image: 0.2 | share of the positives that are copies of the recorded clips |
| `WAKE_PRONUNCIATIONS` | `WAKE_PRONUNCIATIONS` | set by the core | the phrase words' pronunciations (`word=PHONES;...`, ARPAbet), patch 6 |
| `WAKE_TRAINER_CPUS` | — (`--cpus`) | all but a quarter of the Docker host's CPUs, at least 2 left | CPUs the container may use |
| `WAKE_TRAINER_CPU_SHARES` | — (`--cpu-shares`) | `128` | CPU weight against everything else (Docker's default is 1024): voice wins every contended slice |
| `WAKE_TRAINER_CPUSET` | — (`--cpuset-cpus`) | none | a fixed set of CPUs, e.g. `16-31` |
| `WAKE_TRAINER_MEMORY` | — (`--memory`, `--memory-swap` the same) | `12g` | memory cap, no swap on top; never more than the Docker host minus 6 GB (at least 4 GB). The core sets it from **Memory for training** (`WAKE_TRAINER_MEMORY_GB`) |
| `WAKE_TRAINER_DEADLINE_SEC` | — | set by the core: its time limit minus 60 s | the wrapper removes its container when this runs out (exit 124) |
| `WAKE_TRAINER_REQUIRE_GPU` | — | set by the core (`1`) when the dashboard's setup found the GPU | the wrapper refuses to train on the CPU when its GPU check fails (exit 75, the reason in the log) |
| `WAKE_TRAINER_CONTAINER` | — (`--name`) | `domovoi-wake-train` | the container's name |
| `WAKE_TRAINER_SHM_SIZE` | — (`--shm-size`) | `2g` | shared memory for DataLoader workers |
| `WAKE_TRAINER_IMAGE` | — | `domovoi-wake-trainer:latest` | image to run (a published tag to skip building) |
| `WAKE_TRAINER_CACHE_VOLUME` | — | `domovoi_oww_cache` | named volume for the one-time datasets |
| `WAKE_TRAINER_DOCKER` | — | `docker` | the docker command (e.g. `podman`) |
| — | `WAKE_NICE` | `10` | the entrypoint's nice value (it also asks for the idle I/O class); `0` turns that off. The wrapper doesn't pass it: for a hand `docker run -e` |
| — | `WAKE_TTS_BATCH` | 50 on a GPU, 16 on the CPU | synthetic speech batch size; hand runs only, like `WAKE_NICE` |

**How they reach the wrapper.** The wrapper reads them from its process
environment, and the core's trainer worker builds that environment: the
core's own, plus every `WAKE_TRAINER_*` line in `domovoi/.env`, plus the
values of the settings in **Settings → Configuration → Wake words**
(samples, steps, recorded share, CPUs, memory; 0 means the default here),
plus `WAKE_TRAINER_DEADLINE_SEC`, `WAKE_TRAINER_REQUIRE_GPU` and `WAKE_PRONUNCIATIONS`. A variable in
the core's process environment wins over the `.env` line. So a `.env` line
is enough; no systemd drop-in is needed. `WAKE_TRAINER_IMAGE`,
`WAKE_TRAINER_CACHE_VOLUME` and `WAKE_TRAINER_DOCKER` are deliberately not
in the dashboard: what they name runs as the service user.

### Fast iteration vs. full quality

A **smoke run** (verify the pipeline produces a loadable `.onnx`) — minutes on GPU:
```
WAKE_TRAINER_N_SAMPLES=200 WAKE_TRAINER_N_SAMPLES_VAL=50 WAKE_TRAINER_STEPS=2000
```
A **production-quality** model needs far more data + steps (per upstream's own
guidance): `WAKE_TRAINER_N_SAMPLES` **≥ 10000–50000** (20k+ recommended, 100k+
best), `WAKE_TRAINER_N_SAMPLES_VAL` ~2000, `WAKE_TRAINER_STEPS` ~**50000** (the
image default). Measured 2026-10-13 at the image defaults (5,000 samples,
50,000 steps, warm cache): about 20 min on one RTX 4070 Ti SUPER (generation
5-6 min, augmentation 4 min, training 10 min) and about 75 min on 8 CPU
cores, where generating the 12,000 synthetic clips takes about an hour
(training itself runs at ~110 steps/s either way: the model is tiny). The
first run adds the dataset download (5.6 min at ~90 MB/s) unless the
dashboard's setup already warmed it. Synthetic generation grows linearly
with `WAKE_TRAINER_N_SAMPLES`, so 20k+ samples need a longer **Training
time limit** (Settings → Configuration → Wake words) than the GPU's
automatic 3600 s. Generation is resumable (the run dir keeps its clips), so
a run cut off by the limit continues where it stopped on the next Train.
Recording **more real positive clips** than the minimum also helps
materially.

---

## Wire it into the Domovoi server

**On a Domovoi server the dashboard does this**: the last step of **Turn on
wake-word training** writes both keys to `domovoi/.env` through the repo's
`.env` writer and applies them live. On a Linux host or the Windows
install's distro (checkout `/opt/domovoi`) the result is:
```
WAKE_WORD_TRAINER_ENABLED=true
WAKE_WORD_TRAIN_COMMAND="/opt/domovoi/.venv/bin/python" "/opt/domovoi/scripts/wake_word/train_wake_word.py" --clips "{clips_dir}" --phrase "{phrase}" --slug "{slug}" --out "{out}"
```
(the writer stores it quoted and escaped; that is the value). A command that
isn't the repo's wrapper is replaced by it when the card runs.

**By hand, on a development PC** with Docker Desktop, after building the
image, in `domovoi/.env`:
```
WAKE_WORD_TRAINER_ENABLED=true
WAKE_WORD_TRAIN_COMMAND=python "C:\path\to\domovoi\scripts\wake_word\train_wake_word.py" --clips "{clips_dir}" --phrase "{phrase}" --slug "{slug}" --out "{out}"
```
Turning `WAKE_WORD_TRAINER_ENABLED` on in Settings → Configuration starts the
trainer without a restart; a hand edit of `.env` needs one.
Notes:
- **Every placeholder is double-quoted.** The worker tokenizes with
  `shlex.split(posix=False)` on Windows, which keeps backslashes literal and
  honors the double quotes as grouping — so a path keeps its spaces/backslashes
  as one argv token — and strips the quotes. Each value goes into one token,
  so it is always exactly one argument.
- `python` must be the Domovoi server's interpreter. The worker runs with **no
  shell and no cwd**, on the core's PATH, so `python` (or a full path to it) must
  resolve there. If you run the Domovoi server from a venv, use that venv's
  `python` (or its absolute path) here.
- On Linux/Mac hosts use forward-slash paths and `python3` if that's what's on
  PATH:
  `WAKE_WORD_TRAIN_COMMAND=python3 "/srv/domovoi/scripts/wake_word/train_wake_word.py" --clips "{clips_dir}" --phrase "{phrase}" --slug "{slug}" --out "{out}"`
- **Time limit:** **Training time limit** in Settings → Configuration → Wake
  words (`WAKE_WORD_TRAIN_TIMEOUT_SEC`). 0, the default, sizes it to the
  server: 3600 s when the dashboard's setup found a GPU, otherwise twice the
  CPU estimate and at least 7200 s (7200 s for a trainer set up by hand,
  which the setup never measured).
- The worker recognises the repo's wrapper in the command and runs its
  `--stop` on a timeout, a shutdown and its first check after a start. A
  command of your own is yours to stop.

### Placeholder mapping (host -> container)
| Placeholder | Host value (example) | In container |
|---|---|---|
| `{clips_dir}` | `C:\Users\alex\.domovoi\wake_clips\hey_domovoi\.training` | `/data/clips` (read-only) |
| `dirname({out})` | `C:\Users\alex\.domovoi\wake_models` | `/data/models` |
| `{phrase}` | `hey domovoi` | `WAKE_PHRASE` env |
| `{slug}` | `hey_domovoi` | `WAKE_SLUG` env -> writes `/data/models/hey_domovoi.onnx` |
| `{out}` | `C:\...\wake_models\hey_domovoi.onnx` | (the host path the worker stat()s; appears via the `/data/models` bind) |

The container writes `/data/models/<slug>.onnx`; the bind mount makes it appear
at the host `{out}` the worker then checks. **You never pass the raw Windows
`{out}` as the in-container path** — the wrapper derives `/data/models` from
`dirname(out)` itself.

---

## One training at a time, nothing left running

`docker run` alone keeps the container running when the docker client is
killed, so the wrapper and the worker take care of it:

- The container is named `domovoi-wake-train` (Docker refuses a second
  container by that name) and labelled `org.domovoi.wake-train=<slug>`.
  Before a run, the wrapper removes every container with that label: the
  core trains one word at a time, so one still there is left over from a
  run that was cut off. The GPU probe carries the label too.
- The wrapper removes its container when `WAKE_TRAINER_DEADLINE_SEC` runs
  out (exit 124) and when it gets SIGTERM, SIGINT or SIGHUP (exit 128 plus
  the signal). systemd stopping `domovoi-core` reaches it: the unit's
  `KillMode=control-group` signals every process in it.
- Removing means `docker stop -t 10` first, then `docker rm -f`. The
  container runs under `--init`, so the entrypoint gets the SIGTERM (as PID
  1 it would ignore it) and unwinds through the `finally` that removes the
  run's copies of the recorded clips. Then a throwaway container of the
  image scrubs any `runs/*/*/positive_train/real_*.wav` left in the cache
  volume (a run killed with SIGKILL can't clean up after itself); nothing
  when the volume doesn't exist.
- `python train_wake_word.py --stop` removes every container with the
  label and scrubs those copies, and says "no training container to stop"
  when there is none. `--forget SLUG` removes a word's folder from the
  cache volume (the core runs it when a wake word is deleted), `--keep-only
  A,B` every folder but those words' (the worker's first check after a
  start, with the words that exist).
- The core's worker trains words one after another, and nothing while
  training is off or while the dashboard's setup job runs (the image may be
  rebuilding). At its limit or at a shutdown it asks the command to stop
  (SIGTERM to its process group; on Windows it removes the container first,
  which ends `docker run`), kills it after 30 s, then runs `--stop`. At its
  first check after a start it runs `--stop --keep-only <the words that
  exist>` once, for a container or copies a crashed core left behind and
  the files of words deleted while it was down. **Stop training** on a word
  that trains does the same as the limit (the word goes back to *recording*
  with its clips, not queued again); on a queued word it puts it back at
  once. A word cut off by a shutdown stays queued and trains
  again after the restart; one that ran out of time is failed, the message
  naming the time limit.
- The whole output of each word's last run is in
  `~/.domovoi/wake_trainer/logs/<slug>.log`; the word's row shows the end of
  it.

## Resource caps (live voice first)

On a CPU-only server training competes with Whisper and Ollama for every
core, so every run is capped:

- `--cpus`: all but a quarter of the Docker host's CPUs, at least two left
  free (`WAKE_TRAINER_CPUS`, or **CPUs for training**), never more than a
  `WAKE_TRAINER_CPUSET` holds;
- `--cpu-shares 128`: the core, Whisper and Ollama win every contended
  slice;
- `--memory 12g --memory-swap 12g`: no swap on top, so a run that outgrows
  it stops with an out-of-memory error instead of swapping the box to a
  crawl. It peaked at 9.3 GB of its own memory at the defaults (14.7 GB with
  page cache). Never more than the Docker host minus 6 GB;
- inside the container the entrypoint nices itself (`WAKE_NICE`, default 10)
  and asks for the idle I/O class (`ionice -c 3`);
- on a GPU host, the least-used card, so generation (15.9 of 16 GB at its
  peak) stays off the card holding the chat model.

---

## The cache volume (one-time downloads)

Three datasets live in the **named** Docker volume (`domovoi_oww_cache` by
default) mounted at `/data/cache`, each **not** baked into the image and each
**sentinel-gated** by the entrypoint so it downloads **once** and every later
run is a cache hit. A named volume (not a `C:\` bind mount) is deliberate:
Windows bind mounts are dramatically slower for the I/O-heavy feature reads.

| Cached under `/data/cache/` | Source | Size | Sentinel | Used by |
|---|---|---|---|---|
| `openwakeword_features/` | HF `davidscripka/openwakeword_features` (`openwakeword_features_ACAV100M_2000_hrs_16bit.npy` 17.3 GB + `validation_set_features.npy` 0.18 GB) | **17.5 GB** (measured 2026-10-13) | `.oww_data_complete` | `feature_data_files` + FP-validation in `--train_model` |
| `mit_rirs/` | HF `davidscripka/MIT_environmental_impulse_responses` (streamed → 270 16 kHz 16-bit WAVs) | 6 MB | `.oww_rirs_complete` | `rir_paths` in `--augment_clips` |
| `background_clips/` | openWakeWord-resources `fma_sample.zip` + `fsd50k_sample.zip` (Backblaze B2, extracted WAVs) | 1,200 clips, 0.45 GB | `.oww_background_complete` | `background_paths` in `--augment_clips` |

17.9 GB in all. The RIR + background WAVs are **separate** from the feature
`.npy`'s: the augment stage `os.scandir()`s `rir_paths`/`background_paths`
for *real WAV files*, so they cannot point at the features dir. Per-run
synthetic clips + computed features live under `/data/cache/runs/<slug>/`
(about 0.5 GB per wake word at the defaults) and are resumable: re-running
tops up to `n_samples`, and augmentation is skipped only while the real clip
set is the one the features were computed from (patch 8 above). The copies
of the word's recorded clips a run puts in `positive_train/real_*.wav` are
removed at the end of the run, however it ends (above); deleting the wake
word in the dashboard removes its folder too. **Turn off and free the
space** (Settings → Wake Words) removes the whole volume, the image and
Docker's build cache, after showing their size.

The dashboard's setup warms it. By hand (the first Train otherwise spends
~6 min downloading on a fast line):
```bash
docker run --rm -v domovoi_oww_cache:/data/cache domovoi-wake-trainer:latest --warm
```
It prints the cache's size every 15 s and `DONE: the training data cache is
ready.` at the end; run it again after a failure and it carries on.
Inspect / reset:
```bash
docker volume inspect domovoi_oww_cache
docker volume rm domovoi_oww_cache    # forces a fresh ~18 GB download next run
```

---

## Per-OS prerequisites

### GPU box (Windows 11 + NVIDIA, Docker Desktop — a development PC)
- Recent NVIDIA **Windows** driver (R495+; any current Game-Ready/Studio is fine
  for RTX 40-series).
- `wsl --update`; Docker Desktop with the **WSL2 engine** enabled.
- **Do NOT** install an NVIDIA Linux driver inside WSL2 — the Windows driver is
  mapped in as `libcuda.so`; a Linux driver breaks it.
- The NVIDIA Container Toolkit is **bundled** in Docker Desktop — nothing extra
  to install for `--gpus all`.
- Verify: `docker run --rm --gpus all --entrypoint python domovoi-wake-trainer:latest -c "import torch;print(torch.cuda.is_available())"` -> `True`.

### GPU box (the Windows install: Docker Engine inside its WSL2 distro)
Docker Engine has no NVIDIA runtime of its own: without the NVIDIA Container
Toolkit `--gpus` fails with *"failed to discover GPU vendor from CDI: no known
GPU vendor found"*. `scripts/linux/gpu-runtime.sh` adds it, only where WSL
hands the distro an NVIDIA GPU (`/dev/dxg` and
`/usr/lib/wsl/lib/libcuda.so.1`): NVIDIA's apt repository with its key
checked against the pinned fingerprint, the toolkit at the versions pinned in
`scripts/linux/provision-manifest.conf` (1.20.1-1), `nvidia-ctk runtime
configure --runtime=docker`, and a Docker restart. A fresh install runs it as
`provision.sh`'s `gpu-runtime` step; an existing one at its next update or
**Restart Domovoi**. It never installs a Linux NVIDIA driver (it refuses if
apt would bring one). Measured 2026-10-13 on Ubuntu 26.04.1 in WSL 2,
docker-ce 29.8.1, toolkit 1.20.1: about 15 s, then `--gpus all` sees every
card. Details: [docs/WINDOWS_HOST.md](../../docs/WINDOWS_HOST.md#training-wake-words).

### GPU box (native Linux + NVIDIA)
- The NVIDIA driver, installed by you (Domovoi never installs one).
- With the driver's `nvidia-smi` listing a GPU, `provision.sh` and the update
  unit set up the NVIDIA Container Toolkit the same way
  ([docs/LINUX_HOST.md](../../docs/LINUX_HOST.md#wake-word-training));
  `DOMOVOI_MANAGE_GPU_RUNTIME=0` in `/etc/default/domovoi-update` leaves that
  to you (install `nvidia-container-toolkit` and run
  `nvidia-ctk runtime configure --runtime=docker`).
- Verify with the same `--gpus all` one-liner.

### CPU-only (Windows / Linux)
- Just Docker (Desktop on Windows, docker-ce on Linux). No GPU bits.
- The wrapper's GPU probe fails cleanly -> it omits `--gpus` and the container
  trains on CPU: about 75 min a word on 8 cores, with the voice pipeline
  slower meanwhile. The dashboard asks before turning that on and before each
  Train.

### Apple Silicon Mac
- Docker Desktop for Mac. **No CUDA** — build the CPU/arm image variant (see
  *Build*). Training runs on CPU inside the linux/arm64 container. (Docker on
  Apple Silicon does not pass through the Metal GPU.)

---

## Troubleshooting

- **`ERROR: Docker is not available` (exit 3)** — the daemon isn't running or
  `docker` isn't on the Domovoi server's PATH. Start Docker Desktop / the docker
  service. Remember the worker runs with the Domovoi server's PATH/env.
- **`image ... not found locally` (exit 3)** — turn training on in Settings →
  Wake Words (it builds the image), or build it by hand
  ([Build OR pull](#build-or-pull)), or `docker pull` a published tag and set
  `WAKE_TRAINER_IMAGE`. Turning `WAKE_WORD_TRAINER_ENABLED` on in Settings →
  Configuration alone gives exactly this.
- **Wrapper logs `backend = CPU` but you have a GPU** — run the verify one-liner
  above. If `nvidia-smi` works on the host but the container probe fails,
  Docker's GPU runtime isn't set up (WSL2 engine off in Docker Desktop, or the
  NVIDIA Container Toolkit missing on Docker Engine: an update or **Restart
  Domovoi** runs `gpu-runtime.sh`, then **Check the GPU and trainer again**).
  If `nvidia-smi` inside the container works but torch says `False`, it's a
  torch/CUDA build mismatch in the image, not passthrough.
- **`train command timed out after Ns and was stopped`**, or **`exited 124`**
  with "stopped after N s: the training time limit ... ran out" — the run
  took longer than **Training time limit** (Settings → Configuration → Wake
  words). Raise it, or lower **Synthetic clips per wake word**. The container
  was removed either way. The synthetic clips generated so far stay in the
  cache volume, so the next run for the same word continues from them.
- **`exited 143`** (or 130, 129) — the wrapper was told to stop by a signal
  from outside the trainer (someone stopped it by hand). When the core itself
  stops, the word normally stays queued and trains again after the restart.
- **`exited 137`**, or the log shows a stage that `exited -9` — the kernel
  stopped the training for running out of memory inside the container's
  cap: raise **Memory for training**, or lower the sample counts.
- **`exited 0 but <slug>.onnx was not produced`** — the container exited clean
  but no model reached `/data/models`. Check the log
  (`~/.domovoi/wake_trainer/logs/<slug>.log`); usually an ONNX-export error or an
  openWakeWord API drift (see [Build internals](#build-internals--the-dependency-set--upstream-patches)).
  The container also fails with exit 5 in that case, so you'll normally see a
  non-zero exit instead.
- **A training container still running** (`docker ps --filter
  label=org.domovoi.wake-train`) with no word training: `python
  scripts/wake_word/train_wake_word.py --stop` removes it; the core also does
  at its next start.
- **Re-running a crashed generation** — generation is resumable: the entrypoint
  output dir is on the persistent cache volume and `--generate_clips` tops up to
  `n_samples` when the same word trains again.
- **Mount points at an empty dir** — if a bind-mount **source** doesn't exist,
  Docker silently creates it empty. The wrapper guards `{clips_dir}` (exits 2 if
  missing), but double-check the path if you see "no training clips found."

---

## What you get

`<slug>.onnx` at the host `{out}` (e.g.
`C:\Users\alex\.domovoi\wake_models\hey_domovoi.onnx`), about 205 KB. The
worker marks the row `ready`, the model serves over `/v1/wake-models/<slug>.onnx`,
and the dashboard's **Push to …** control pushes it to satellites. The Pi's
effective wake word, the served filename, and the openWakeWord prediction-dict
key all equal `<slug>` — the container writes exactly `<slug>.onnx`, never a
renamed file. The model is for this household only: its training data's
licences don't allow sharing it ([README.md, section 5](README.md#5-licence-a-model-trained-here-is-for-this-household)).
