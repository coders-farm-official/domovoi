# Portable openWakeWord training image — runs IDENTICALLY on GPU and CPU hosts.
#
# Base: pytorch/pytorch CUDA runtime. The PyTorch image bundles its OWN CUDA +
# cuDNN userspace, so the container needs only the host GPU *driver* (supplied
# via WSL2 on Windows, or the nvidia-container-toolkit on native Linux) — and
# when you run it WITHOUT `--gpus`, torch.cuda.is_available() returns False and
# everything runs on CPU. ONE image, both modes. No host paths are baked in.
#
# >>> CPU-ONLY / APPLE-SILICON TOGGLE <<<
# A CUDA image still *runs* on a CPU-only x86 host (it just won't see a GPU).
# But it will NOT run on arm64 (Apple Silicon) — there is no arm64 CUDA image.
# For a Mac/arm host, override the base at build time with a CPU/arm wheel:
#     docker build --build-arg BASE=python:3.11-slim \
#                  --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu \
#                  -t domovoi-wake-trainer:cpu scripts/wake_word/docker
# (The default below is the GPU-capable image, which is what the shared Domovoi
#  box and most contributors want.)
ARG BASE=pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime
FROM ${BASE}

# Pin third-party repos by commit so the build doesn't drift as upstream master
# moves (openWakeWord/piper notebooks have known breakage churn). Override at
# build time if you need a newer/forked SHA.
ARG OWW_REF=main
ARG PSG_REF=master
# When BASE is a non-torch image (e.g. python:3.11-slim for CPU/arm), set this
# to a pip index so torch gets installed. Empty => assume torch is already in
# the base (the pytorch/pytorch case) and DON'T reinstall it (reinstalling can
# pull a CPU-only wheel that fights the image's bundled CUDA torch).
ARG TORCH_INDEX=""

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OWW_CACHE=/data/cache

# System deps:
#   espeak-ng + libespeak-ng1  -> espeak-phonemizer (piper-sample-generator)
#   build-essential            -> webrtcvad C extension on some platforms
#   ffmpeg, libsndfile1        -> audio decode/resample (audiomentations, soundfile)
#   git, wget, ca-certificates -> clone repos + fetch the piper .pt model over HTTPS
#                                 (slim images ship without CA certs -> HTTPS
#                                  silently fails, so install them explicitly)
RUN apt-get update && apt-get install -y --no-install-recommends \
        espeak-ng libespeak-ng1 \
        build-essential \
        ffmpeg libsndfile1 \
        git wget ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Optionally install torch for non-torch bases (CPU/arm path). No-op for the
# pytorch/pytorch base where torch is already present.
RUN if [ -n "$TORCH_INDEX" ]; then \
        pip install --index-url "$TORCH_INDEX" torch torchaudio ; \
    fi

WORKDIR /opt

# ── piper-sample-generator (dscripka FORK — REQUIRED, not rhasspy upstream) ──
# Cloned to a path the training YAML points at via `piper_sample_generator_path`.
# openWakeWord's train.py drives generation INTERNALLY: it does
#   sys.path.insert(0, piper_sample_generator_path)
#   from generate_samples import generate_samples
#   generate_samples(text=..., max_samples=..., batch_size=...,
#                    noise_scales=..., length_scales=..., output_dir=...,
#                    auto_reduce_batch_size=True, file_names=...)
# That import + that exact call signature only exist in the dscripka FORK:
#   * a TOP-LEVEL generate_samples.py (rhasspy master moved it into a
#     piper_sample_generator/ package -> `No module named 'generate_samples'`),
#   * an `auto_reduce_batch_size` kwarg, AND
#   * a DEFAULT `model=` (rhasspy master made `model` a required positional, and
#     train.py never passes one -> TypeError).
# So we MUST use dscripka/piper-sample-generator, whose default model is the
# v1.0.0 en-us-libritts-high.pt. The v2.0.0 en_US-libritts_r-medium.pt is a
# DIFFERENT state_dict that does NOT load in this fork's generator class.
RUN git clone https://github.com/dscripka/piper-sample-generator.git /opt/piper-sample-generator && \
    cd /opt/piper-sample-generator && git checkout "$PSG_REF"

# Bake the ~63 MB v1.0.0 VITS generator checkpoint at BUILD time (offline-capable,
# no GitHub-Releases rate-limit hit per run). This is the en-us-libritts-high.pt
# that the dscripka fork's generate_samples() defaults to (models/ dir, relative
# to the script) — the matching state_dict for THIS fork's generator.
RUN mkdir -p /opt/piper-sample-generator/models && \
    wget -q -O /opt/piper-sample-generator/models/en-us-libritts-high.pt \
      'https://github.com/rhasspy/piper-sample-generator/releases/download/v1.0.0/en-us-libritts-high.pt' && \
    test "$(stat -c%s /opt/piper-sample-generator/models/en-us-libritts-high.pt)" -gt 50000000

# ── openWakeWord ─────────────────────────────────────────────────────────────
RUN git clone https://github.com/dscripka/openWakeWord.git /opt/openWakeWord && \
    cd /opt/openWakeWord && git checkout "$OWW_REF"

# Patch an upstream train.py bug: ALL the boolean flags are declared
# `action="store_true", default="False"` — and "False" is a NON-EMPTY STRING,
# i.e. truthy. The generate/augment/train guards dodge this by comparing
# `is True`, but the tflite guard is a bare `if args.convert_to_tflite:`, so it
# fires on the truthy-string DEFAULT even when the flag is never passed — pulling
# in tensorflow + onnx_tf and crashing AFTER the .onnx is already written
# ("ModuleNotFoundError: No module named 'onnx_tf'"). We only want the .onnx, so
# make that one guard match the others' `is True` pattern. This keeps us off the
# entire fragile tensorflow/onnx_tf 2022-pinned subtree.
# (train.py ships with CRLF line endings, so match the substring, not an
#  end-of-line-anchored pattern.)
RUN sed -i 's/if args\.convert_to_tflite:/if args.convert_to_tflite is True:/' \
        /opt/openWakeWord/openwakeword/train.py && \
    grep -q "if args.convert_to_tflite is True:" /opt/openWakeWord/openwakeword/train.py

# piper-sample-generator deps (audiomentations, espeak-phonemizer, numpy,
# webrtcvad). We DELIBERATELY do not let it reinstall torch on the CUDA base.
RUN pip install --no-cache-dir \
        audiomentations espeak-phonemizer numpy webrtcvad soundfile && \
    pip install --no-cache-dir -e /opt/openWakeWord && \
    pip install --no-cache-dir "huggingface_hub>=0.23" onnx onnxruntime pyyaml tqdm scipy scikit-learn

# ── FULL openWakeWord *training* dependency set ──────────────────────────────
# `pip install -e .` above only pulls openWakeWord's *inference* deps. The
# trainer (openwakeword/train.py + openwakeword/data.py) imports a much larger
# stack that the upstream notebook pins at 2022-era versions which DO NOT
# install on py3.11 / torch 2.4. The versions below are the modern ones that
# resolve cleanly on this base (verified import of openwakeword.{train,data,
# utils} + AudioFeatures). Mapping to what imports them:
#   torchinfo, torchmetrics         -> train.py
#   pronouncing, mutagen, acoustics -> data.py (adversarial texts, tags, noise)
#   speechbrain                     -> data.py (read_audio + reverberate; 1.1.0
#                                       keeps the .dataio / .processing paths the
#                                       trainer imports)
#   torch-audiomentations           -> data.py (GPU-side augmentation)
#   datasets                        -> entrypoint RIR download (MIT IRs)
# datasets is PINNED ==3.6.0: 4.x/5.x moved Audio decoding to `torchcodec`
# (needs FFmpeg libs + a torch-matched build), so streaming the MIT RIR audio
# raises "please install 'torchcodec'". 3.6.0 still decodes via soundfile AND
# co-exists with the image's huggingface_hub 1.x — no torchcodec needed.
# scipy is PINNED <1.15: acoustics 0.2.6 (newest on PyPI) imports the removed
# scipy.special.sph_harm at module load; scipy 1.14.x still ships it. 1.14 keeps
# librosa/numba happy too. NO tensorflow / onnx_tf: train.py only needs them for
# the OPTIONAL --convert_to_tflite path, which we never invoke (we want .onnx
# only), so we skip that whole fragile 2022-pinned subtree entirely.
RUN pip install --no-cache-dir \
        "torchinfo==1.8.0" \
        "torchmetrics==1.9.0" \
        "pronouncing==0.3.0" \
        "mutagen==1.48.1" \
        "speechbrain==1.1.0" \
        "torch-audiomentations==0.12.0" \
        "acoustics==0.2.6" \
        "datasets==3.6.0" \
        "scipy<1.15"

# Bake openWakeWord's FEATURE models (melspectrogram.onnx + embedding_model.onnx,
# plus the bundled wake models) into resources/models/ at BUILD time. AudioFeatures
# (used by both --augment_clips and --train_model) hard-requires these and would
# otherwise try to fetch them at run time (network dependency mid-train). The
# v0.5.1 GitHub-release assets that download_models() pulls are exactly what the
# notebook fetches by hand.
RUN cd /opt/openWakeWord && \
    python -c "import openwakeword.utils as u; u.download_models([])" && \
    test -f /opt/openWakeWord/openwakeword/resources/models/melspectrogram.onnx && \
    test -f /opt/openWakeWord/openwakeword/resources/models/embedding_model.onnx

# Our entrypoint + YAML template (rendered per-run from env by the entrypoint).
COPY entrypoint.py /opt/entrypoint.py
COPY train_config.template.yaml /opt/train_config.template.yaml

# The cache (negative/feature dataset + downloaded extras) lives on a NAMED
# volume mounted at /data/cache at run time — declared so an un-mounted run
# still works (just re-downloads each time).
VOLUME ["/data/cache"]

# WORKDIR is the openWakeWord repo so train.py's relative asset paths resolve.
WORKDIR /opt/openWakeWord
ENTRYPOINT ["python", "/opt/entrypoint.py"]
