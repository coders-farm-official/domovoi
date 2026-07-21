# Portable Docker Wake-Word Trainer

A **capability-sensing**, **portable** openWakeWord training pipeline that plugs
into the core service's `wake_word_train_command` contract. It is designed for
the fact that this Domovoi server is **shared** across very different machines:
multi-GPU Windows boxes, modest CPU-only laptops, Linux servers, and Apple
Silicon Macs. Nothing is hardcoded to one host — no fixed GPU index, no
`~/.domovoi` path baked into the image, no assumption a GPU even exists.

openWakeWord automatic training is **Linux-only** (its `piper-sample-generator`
dependency needs `espeak-ng` and a C toolchain), so a Linux **Docker** container
is the universal substrate. The pipeline degrades GPU -> CPU gracefully at two
layers: the **host wrapper** picks `--gpus` only when it will actually work, and
the **container** routes to `cuda`/`cpu` via `torch.cuda.is_available()`.

```
domovoi worker  --shells out-->  train_wake_word.py  --docker run-->  container
(wake_word_trainer)   (4 placeholders)  (host, stdlib-only,    (GPU or CPU)   (entrypoint.py:
                                          senses docker+GPU)                   gen + train + onnx)
```

---

## Files

| File | Role |
|---|---|
| `scripts/wake_word/train_wake_word.py` | **Host wrapper** — IS the `wake_word_train_command`. Stdlib-only (host may not have torch). Senses Docker + GPU, builds the right `docker run`, streams output, returns the container exit code unchanged. |
| `scripts/wake_word/docker/Dockerfile` | **One image**, GPU or CPU. Bakes piper model; the ~7 GB feature data is **not** baked — it lives in a named cache volume. |
| `scripts/wake_word/docker/entrypoint.py` | **Container entrypoint** — auto-detects GPU/CPU, downloads cache once, generates synthetic positives, injects real clips, trains, writes `/data/models/<slug>.onnx`, exits non-zero on any failure. |
| `scripts/wake_word/docker/train_config.template.yaml` | openWakeWord training config, rendered per run from env. |
| `scripts/wake_word/docker/requirements.txt` | Extra Python deps installed into the image (reference; the Dockerfile installs them). |

---

## How it senses GPU vs CPU

**Host wrapper (`train_wake_word.py`):**
1. `docker info` — is the daemon up? (clear error + exit 3 if not).
2. **Real** GPU probe: `docker run --rm --gpus all <image> true`. This is the
   only reliable cross-platform test — it works the same on Docker Desktop
   (toolkit bundled) and native Linux. If it exits non-zero, we **omit
   `--gpus`** entirely so a GPU-less host never errors on the flag.
3. If GPUs exist and there are several, pick the **least-used** via
   `nvidia-smi --query-gpu=index,memory.used` and pass `--gpus "device=<N>"`.
   Single GPU -> `--gpus all`. The chosen backend is logged
   (`backend = GPU device 1 (of 3, least-used)` or `backend = CPU`).

**Container (`entrypoint.py`):** asks `torch.cuda.is_available()` (which honors
the `CUDA_VISIBLE_DEVICES` the runtime sets) and routes generation + training to
`cuda` or `cpu`. The **same image** does both — a CUDA image still runs on CPU
when launched without `--gpus`.

---

## Build OR pull

### Build locally (any contributor)
```bash
# from the repo root
docker build -t domovoi-wake-trainer:latest scripts/wake_word/docker
```
GPU and CPU x86 hosts use the default base. **Apple Silicon / CPU-only arm** —
override the base (there is no arm64 CUDA image):
```bash
docker build \
  --build-arg BASE=python:3.11-slim \
  --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu \
  -t domovoi-wake-trainer:latest scripts/wake_word/docker
```

### Publish once, pull everywhere (recommended for the shared box)
Building bakes a ~255 MB TTS model + feature models and installs the full torch +
training stack — several GB. Publish it once so the other people sharing this
domovoi just `docker pull`:
```bash
docker tag  domovoi-wake-trainer:latest ghcr.io/kamronk/domovoi-wake-trainer:1
docker push ghcr.io/kamronk/domovoi-wake-trainer:1
# on each sharer's host:
docker pull ghcr.io/kamronk/domovoi-wake-trainer:1
# point the wrapper at it (so it won't try to build):
export WAKE_TRAINER_IMAGE=ghcr.io/kamronk/domovoi-wake-trainer:1   # or set in .env
```

---

## Build internals — the dependency set & upstream patches

openWakeWord's *training* path (`openwakeword/train.py` + `openwakeword/data.py`)
pulls a much larger stack than the inference install, and the upstream notebook
pins **2022-era versions that do not install on py3.11 / torch 2.4**. The image
installs these **modern, verified-working** versions instead (in a late
Dockerfile layer so the slow base layers stay cached):

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

Two non-obvious pins and three patches make it work — keep these if you bump
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
   so no network is needed mid-train.

**`--shm-size`.** The training DataLoader's worker processes pass batches through
`/dev/shm`; Docker's 64 MB default overflows mid-train
(*"RuntimeError: No space left on device"* on a `/torch_*` file). The host
wrapper passes `--shm-size 2g` (override `WAKE_TRAINER_SHM_SIZE`); if you ever
run the container by hand, add `--shm-size=2g` yourself.

### Env knobs (host wrapper → container)

| Host env (on the Domovoi server process) | Container env | Default | Effect |
|---|---|---|---|
| `WAKE_TRAINER_N_SAMPLES` | `WAKE_N_SAMPLES` | image: 5000 | synthetic positives/negatives to generate |
| `WAKE_TRAINER_N_SAMPLES_VAL` | `WAKE_N_SAMPLES_VAL` | image: 1000 | synthetic validation clips |
| `WAKE_TRAINER_STEPS` | `WAKE_STEPS` | image: 50000 | training steps (sequence-1 max; seq 2/3 use steps/10) |
| `WAKE_TRAINER_SHM_SIZE` | — (docker `--shm-size`) | `2g` | shared-memory for DataLoader workers |
| `WAKE_TRAINER_IMAGE` | — | `domovoi-wake-trainer:latest` | image to run (set to a published tag to skip building) |
| `WAKE_TRAINER_CACHE_VOLUME` | — | `domovoi_oww_cache` | named volume for the one-time datasets |

### Fast iteration vs. full quality

A **smoke run** (verify the pipeline produces a loadable `.onnx`) — minutes on GPU:
```
WAKE_TRAINER_N_SAMPLES=200 WAKE_TRAINER_N_SAMPLES_VAL=50 WAKE_TRAINER_STEPS=2000
```
A **production-quality** model needs far more data + steps (per upstream's own
guidance): `WAKE_TRAINER_N_SAMPLES` **≥ 10000–50000** (20k+ recommended, 100k+
best), `WAKE_TRAINER_N_SAMPLES_VAL` ~2000, `WAKE_TRAINER_STEPS` ~**50000** (the
image default). Generation + training at those sizes runs ~1–2 h on the
RTX 4070 Ti Super and will exceed the Domovoi server worker's **3600 s** timeout —
warm the synthetic clips/features first (the run dir is resumable) or raise the
worker timeout. Recording **more real positive clips** than the 30 in
`~/.domovoi/wake_clips/<slug>/` also helps materially.

---

## Wire it into the Domovoi server

In `domovoi/.env` (or the gear-modal config):
```
WAKE_WORD_TRAINER_ENABLED=true
WAKE_WORD_TRAIN_COMMAND=python "C:\Users\Kamron\claude-exp\domovoi-voice-search\scripts\wake_word\train_wake_word.py" --clips "{clips_dir}" --phrase "{phrase}" --slug "{slug}" --out "{out}"
```
Notes:
- **Every placeholder is double-quoted.** The worker tokenizes with
  `shlex.split(posix=False)` on Windows, which keeps backslashes literal and
  honors the double quotes as grouping — so a path keeps its spaces/backslashes
  as one argv token.
- `python` must be the Domovoi server's interpreter. The worker runs with **no
  shell, no cwd, no env override** — `python` (or a full path to it) must be on
  the Domovoi server process's PATH. If you run the Domovoi server from a venv, use
  that venv's `python` (or its absolute path) here.
- On Linux/Mac hosts use forward-slash paths and `python3` if that's what's on
  PATH:
  `WAKE_WORD_TRAIN_COMMAND=python3 "/srv/domovoi/scripts/wake_word/train_wake_word.py" --clips "{clips_dir}" --phrase "{phrase}" --slug "{slug}" --out "{out}"`
- **Timeout:** the worker kills the command after **3600 s (1 hour)** and marks
  the row `train command timed out`. On CPU, large `n_samples` can blow past an
  hour — see Troubleshooting to lower it.

### Placeholder mapping (host -> container)
| Placeholder | Host value (example) | In container |
|---|---|---|
| `{clips_dir}` | `C:\Users\Kamron\.domovoi\wake_clips\hey_domovoi` | `/data/clips` (read-only) |
| `dirname({out})` | `C:\Users\Kamron\.domovoi\wake_models` | `/data/models` |
| `{phrase}` | `hey domovoi` | `WAKE_PHRASE` env |
| `{slug}` | `hey_domovoi` | `WAKE_SLUG` env -> writes `/data/models/hey_domovoi.onnx` |
| `{out}` | `C:\...\wake_models\hey_domovoi.onnx` | (the host path the worker stat()s; appears via the `/data/models` bind) |

The container writes `/data/models/<slug>.onnx`; the bind mount makes it appear
at the host `{out}` the worker then checks. **You never pass the raw Windows
`{out}` as the in-container path** — the wrapper derives `/data/models` from
`dirname(out)` itself.

---

## The cache volume (one-time downloads)

Three datasets live in the **named** Docker volume (`domovoi_oww_cache` by
default) mounted at `/data/cache`, each **not** baked into the image and each
**sentinel-gated** by the entrypoint so it downloads **once** and every later
run is a cache hit. A named volume (not a `C:\` bind mount) is deliberate:
Windows bind mounts are dramatically slower for the I/O-heavy feature reads.

| Cached under `/data/cache/` | Source | Size | Sentinel | Used by |
|---|---|---|---|---|
| `openwakeword_features/` | HF `davidscripka/openwakeword_features` (ACAV100M negative features `.npy` + `validation_set_features.npy`) | ~7 GB | `.oww_data_complete` | `feature_data_files` + FP-validation in `--train_model` |
| `mit_rirs/` | HF `davidscripka/MIT_environmental_impulse_responses` (streamed → 270 16 kHz 16-bit WAVs) | ~25 MB | `.oww_rirs_complete` | `rir_paths` in `--augment_clips` |
| `background_clips/` | openWakeWord-resources `fma_sample.zip` + `fsd50k_sample.zip` (extracted WAVs) | ~1.2 k clips, ~1 GB | `.oww_background_complete` | `background_paths` in `--augment_clips` |

The RIR + background WAVs are **separate** from the feature `.npy`'s: the augment
stage `os.scandir()`s `rir_paths`/`background_paths` for *real WAV files*, so they
cannot point at the features dir. Per-run synthetic clips + computed features
live under `/data/cache/runs/<slug>/` and are resumable (re-running tops up to
`n_samples` and skips augmentation if features already exist).

Warm it up once before the first real training run (optional but nice):
```bash
docker volume create domovoi_oww_cache
docker run --rm -v domovoi_oww_cache:/data/cache \
  -e WAKE_SLUG=warmup -e WAKE_PHRASE="warm up" \
  domovoi-wake-trainer:latest python -c \
  "import importlib.util; s=importlib.util.spec_from_file_location('e','/opt/entrypoint.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.ensure_dataset()"
```
Inspect / reset:
```bash
docker volume inspect domovoi_oww_cache
docker volume rm domovoi_oww_cache    # forces a fresh ~7 GB download next run
```

---

## Per-OS prerequisites

### GPU box (Windows 11 + NVIDIA — e.g. Domovoi)
- Recent NVIDIA **Windows** driver (R495+; any current Game-Ready/Studio is fine
  for RTX 40-series).
- `wsl --update`; Docker Desktop with the **WSL2 engine** enabled.
- **Do NOT** install an NVIDIA Linux driver inside WSL2 — the Windows driver is
  mapped in as `libcuda.so`; a Linux driver breaks it.
- The NVIDIA Container Toolkit is **bundled** in Docker Desktop — nothing extra
  to install for `--gpus all`.
- Verify: `docker run --rm --gpus all domovoi-wake-trainer:latest python -c "import torch;print(torch.cuda.is_available())"` -> `True`.

### GPU box (native Linux + NVIDIA)
- NVIDIA driver + `nvidia-container-toolkit` installed and the daemon
  configured (`nvidia-ctk runtime configure`).
- Verify with the same `--gpus all` one-liner.

### CPU-only (Windows / Linux laptop)
- Just Docker (Desktop on Windows, docker-ce on Linux). No GPU bits.
- The wrapper's GPU probe fails cleanly -> it omits `--gpus` and the container
  trains on CPU (slower; see times below). Nothing else to do.

### Apple Silicon Mac
- Docker Desktop for Mac. **No CUDA** — build the CPU/arm image variant (see
  *Build*). Training runs on CPU inside the linux/arm64 container. (Docker on
  Apple Silicon does not pass through the Metal GPU.)

---

## Troubleshooting

- **`ERROR: Docker is not available` (exit 3)** — the daemon isn't running or
  `docker` isn't on the Domovoi server's PATH. Start Docker Desktop / the docker
  service. Remember the worker runs with the Domovoi server's PATH/env.
- **`image ... not found locally` (exit 3)** — build it
  (`docker build -t domovoi-wake-trainer:latest scripts/wake_word/docker`) or
  `docker pull` a published tag and set `WAKE_TRAINER_IMAGE`.
- **Wrapper logs `backend = CPU` but you have a GPU** — run the verify one-liner
  above. If `nvidia-smi` works on the host but the container probe fails, your
  Docker GPU passthrough isn't set up (WSL2 engine off, or toolkit missing on
  Linux). If `nvidia-smi` inside the container works but torch says `False`,
  it's a torch/CUDA build mismatch in the image, not passthrough.
- **`train command timed out after 3600s`** — CPU training of many samples
  exceeds the worker's 1-hour cap. Lower the synthetic-clip count:
  `export WAKE_TRAINER_N_SAMPLES=2000 WAKE_TRAINER_N_SAMPLES_VAL=500` (or set
  them in the Domovoi server's env). On a GPU box this rarely triggers.
- **`exited 0 but <slug>.onnx was not produced`** — the container exited clean
  but no model reached `/data/models`. Check the captured log tail on the Wake
  Words tab; usually an ONNX-export error or an openWakeWord API drift (see
  Risks). The container also fails with exit 5 in that case, so you'll normally
  see a non-zero exit instead.
- **Re-running a crashed generation** — generation is resumable: the entrypoint
  output dir is on the persistent cache volume and `--generate_clips` tops up to
  `n_samples`. Just re-queue/retrain.
- **Mount points at an empty dir** — if a bind-mount **source** doesn't exist,
  Docker silently creates it empty. The wrapper guards `{clips_dir}` (exits 2 if
  missing), but double-check the path if you see "no training clips found."

---

## What you get

`<slug>.onnx` at the host `{out}` (e.g.
`C:\Users\Kamron\.domovoi\wake_models\hey_domovoi.onnx`). The worker marks the
row `ready`, the model serves over `/v1/wake-models/<slug>.onnx`, and the
dashboard's **Push to …** control pushes it to satellites. The Pi's effective
wake word, the served filename, and the openWakeWord prediction-dict key all
equal `<slug>` — the container writes exactly `<slug>.onnx`, never a renamed
file.
