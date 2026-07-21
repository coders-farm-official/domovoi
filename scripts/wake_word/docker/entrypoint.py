#!/usr/bin/env python3
"""Container entrypoint for the portable openWakeWord trainer.

Runs IDENTICALLY whether the host launched us with ``--gpus`` or not: we ask
``torch.cuda.is_available()`` (which honors any ``CUDA_VISIBLE_DEVICES`` the
host wrapper set) and route generation/training to ``cuda`` or ``cpu``
accordingly. Steps:

  1. Detect device (GPU vs CPU) and print it.
  2. Ensure the one-time ~7 GB negative/feature dataset is in the NAMED cache
     volume at /data/cache — download once, gated by a sentinel file so reruns
     skip it. (Detects cached-vs-fresh; NOT baked into the image.)
  3. Render the training YAML from env (WAKE_PHRASE/WAKE_SLUG/...).
  4. Generate synthetic positives via piper-sample-generator (driven internally
     by openWakeWord's train.py --generate_clips), then augment.
  5. Inject the real positive clips from /data/clips (resampled to 16 kHz) into
     the positive set before/at augmentation.
  6. Train -> export ONNX -> copy to /data/models/<slug>.onnx.
  7. Exit non-zero on ANY failure (the host worker's success gate is exit 0 +
     <slug>.onnx present at the host {out}, which is /data/models/<slug>.onnx
     via the bind mount).

NOTHING here assumes a GPU exists or knows any host path — /data/clips,
/data/models, /data/cache are fixed in-container mountpoints the wrapper binds.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

OWW_REPO = Path("/opt/openWakeWord")
TRAIN_PY = OWW_REPO / "openwakeword" / "train.py"
TEMPLATE = Path("/opt/train_config.template.yaml")

CACHE = Path(os.environ.get("OWW_CACHE", "/data/cache"))
CLIPS_IN = Path("/data/clips")          # read-only real positives (host bind)
MODELS_OUT = Path("/data/models")       # host bind; we write <slug>.onnx here

SENTINEL = CACHE / ".oww_data_complete"
FEATURES_DIR = CACHE / "openwakeword_features"

# Augmentation AUDIO (real WAVs the --augment_clips stage os.scandir()s). These
# are SEPARATE from the precomputed feature .npy's in FEATURES_DIR — the augment
# stage needs actual room-impulse-response and background WAV files, not feature
# tensors. Each is downloaded once into the named cache volume and sentinel-gated.
RIR_DIR = CACHE / "mit_rirs"
RIR_SENTINEL = CACHE / ".oww_rirs_complete"
BACKGROUND_DIR = CACHE / "background_clips"
BACKGROUND_SENTINEL = CACHE / ".oww_background_complete"

# openWakeWord's curated background-noise sample packs (music + general audio).
# Sufficient for a working model; full-quality training would add AudioSet /
# Common Voice (see DOCKER_TRAINER.md).
BACKGROUND_ZIP_URLS = [
    "https://f002.backblazeb2.com/file/openwakeword-resources/data/fma_sample.zip",
    "https://f002.backblazeb2.com/file/openwakeword-resources/data/fsd50k_sample.zip",
]


def log(msg: str) -> None:
    print(f"[entrypoint] {msg}", flush=True)


def die(msg: str, code: int = 1) -> "None":
    print(f"[entrypoint] FATAL: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def detect_device() -> str:
    """'cuda' if torch sees a usable GPU (honoring CUDA_VISIBLE_DEVICES), else
    'cpu'. We also print nvidia-smi's view so a torch-says-False-but-smi-works
    mismatch is diagnosable from the captured log."""
    try:
        import torch
    except Exception as e:  # torch should always be present, but be defensive
        die(f"torch import failed inside the image: {e}", 1)
    avail = torch.cuda.is_available()
    if avail:
        try:
            name = torch.cuda.get_device_name(0)
        except Exception:
            name = "unknown"
        log(f"device = GPU (torch.cuda.is_available()=True, {name}, "
            f"CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES', '<unset>')})")
        return "cuda"
    log("device = CPU (torch.cuda.is_available()=False — host omitted --gpus or "
        "no GPU runtime). Training will be slower but is fully supported.")
    return "cpu"


def ensure_dataset() -> None:
    """Download the one-time negative/feature dataset into the NAMED cache
    volume, gated by a sentinel so reruns skip the ~7 GB pull. Detects
    cached-vs-fresh. The data is NOT baked into the image."""
    CACHE.mkdir(parents=True, exist_ok=True)
    if SENTINEL.is_file():
        log(f"cache HIT: dataset present at {CACHE} (sentinel exists) — "
            f"skipping ~7 GB download.")
        return
    log(f"cache MISS: downloading one-time openWakeWord feature dataset into "
        f"{CACHE} (~7 GB; first run only) ...")
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
        # Precomputed ACAV100M negative features + validation set used by
        # openWakeWord training. ~7 GB on disk.
        snapshot_download(
            "davidscripka/openwakeword_features",
            repo_type="dataset",
            local_dir=str(FEATURES_DIR),
        )
    except Exception as e:
        die(f"feature dataset download failed: {e}. Re-running resumes "
            f"(huggingface_hub caches partial files); check container network "
            f"+ that the named cache volume has room for ~7 GB.", 4)
    SENTINEL.write_text("ok\n")
    log("cache download complete; sentinel written.")


def ensure_rirs() -> None:
    """Download the MIT environmental impulse responses into <cache>/mit_rirs as
    16 kHz 16-bit PCM WAVs (verbatim from the notebook's RIR cell). The
    --augment_clips stage os.scandir()s rir_paths for real WAVs; pointing it at
    the feature .npy dir would crash it. Sentinel-gated so reruns skip."""
    if RIR_SENTINEL.is_file() and any(RIR_DIR.glob("*.wav")):
        log(f"RIR cache HIT: {RIR_DIR} (sentinel exists) — skipping download.")
        return
    log(f"RIR cache MISS: downloading MIT impulse responses -> {RIR_DIR} ...")
    RIR_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import datasets
        import numpy as np
        import scipy.io.wavfile
        rir = datasets.load_dataset(
            "davidscripka/MIT_environmental_impulse_responses",
            split="train", streaming=True,
        )
        n = 0
        for row in rir:
            name = row["audio"]["path"].split("/")[-1]
            scipy.io.wavfile.write(
                str(RIR_DIR / name), 16000,
                (row["audio"]["array"] * 32767).astype(np.int16),
            )
            n += 1
    except Exception as e:
        die(f"RIR download failed: {e}. The augment stage needs real WAVs under "
            f"{RIR_DIR}; check container network access to huggingface.co.", 4)
    if n == 0:
        die(f"RIR download produced 0 files in {RIR_DIR}.", 4)
    RIR_SENTINEL.write_text("ok\n")
    log(f"RIR download complete: {n} impulse responses; sentinel written.")


def ensure_background() -> None:
    """Download openWakeWord's curated background-noise sample packs (fma_sample +
    fsd50k_sample zips from the openwakeword-resources bucket), extract their
    WAVs into <cache>/background_clips. Sentinel-gated so reruns skip."""
    if BACKGROUND_SENTINEL.is_file() and any(BACKGROUND_DIR.glob("*.wav")):
        log(f"background cache HIT: {BACKGROUND_DIR} (sentinel exists) — skipping.")
        return
    log(f"background cache MISS: downloading noise sample packs -> {BACKGROUND_DIR} ...")
    BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    import urllib.request
    import zipfile
    import tempfile
    total = 0
    for url in BACKGROUND_ZIP_URLS:
        try:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp_path = tmp.name
            log(f"  fetching {url}")
            urllib.request.urlretrieve(url, tmp_path)
            with zipfile.ZipFile(tmp_path) as zf:
                for member in zf.namelist():
                    if member.lower().endswith(".wav"):
                        # Flatten into BACKGROUND_DIR (scandir is non-recursive).
                        data = zf.read(member)
                        name = Path(member).name
                        if not name:
                            continue
                        (BACKGROUND_DIR / name).write_bytes(data)
                        total += 1
            os.unlink(tmp_path)
        except Exception as e:
            die(f"background download/extract failed for {url}: {e}. The augment "
                f"stage needs real WAVs under {BACKGROUND_DIR}.", 4)
    if total == 0:
        die(f"background download produced 0 WAVs in {BACKGROUND_DIR}.", 4)
    BACKGROUND_SENTINEL.write_text("ok\n")
    log(f"background download complete: {total} clips; sentinel written.")


def discover(glob_root: Path, *patterns: str) -> str:
    """Return the first file under glob_root matching any pattern, or ''.
    The exact filenames in the feature dataset have shifted across openWakeWord
    releases, so we discover rather than hardcode."""
    for pat in patterns:
        hits = sorted(glob_root.rglob(pat))
        if hits:
            return str(hits[0])
    return ""


def discover_dir(glob_root: Path, *patterns: str) -> str:
    for pat in patterns:
        for p in sorted(glob_root.rglob(pat)):
            if p.is_dir():
                return str(p)
    return ""


def _first_npy_excluding(glob_root: Path, exclude: str) -> str:
    """First .npy under glob_root that isn't `exclude` (the FP-validation file).
    Fallback for the ACAV100M negative-features file if naming shifts."""
    for p in sorted(glob_root.rglob("*.npy")):
        if str(p) != exclude:
            return str(p)
    return ""


def render_config(slug: str, phrase: str, device: str, work: Path) -> Path:
    n_samples = os.environ.get("WAKE_N_SAMPLES", "5000")
    n_samples_val = os.environ.get("WAKE_N_SAMPLES_VAL", "1000")
    tts_batch = os.environ.get("WAKE_TTS_BATCH", "50" if device == "cuda" else "16")
    steps = os.environ.get("WAKE_STEPS", "50000")

    # FP-validation + negative ACAV100M features come from the precomputed
    # feature dataset (.npy's in FEATURES_DIR). The neg-features file is the big
    # ACAV100M one — exclude the validation file so we don't accidentally pick it.
    fp_val = discover(FEATURES_DIR, "validation_set_features.npy", "*validation*features*.npy")
    neg_feat = (discover(FEATURES_DIR, "*ACAV*.npy", "*negative*features*.npy")
                or _first_npy_excluding(FEATURES_DIR, fp_val))
    # RIR + background point at the REAL-WAV dirs we downloaded (NOT FEATURES_DIR).
    rir = str(RIR_DIR)
    background = str(BACKGROUND_DIR)

    text = TEMPLATE.read_text(encoding="utf-8")
    repl = {
        "__PHRASE__": phrase,
        "__SLUG__": slug,
        "__N_SAMPLES__": n_samples,
        "__N_SAMPLES_VAL__": n_samples_val,
        "__TTS_BATCH__": tts_batch,
        "__STEPS__": steps,
        "__OUTPUT_DIR__": str(work),
        "__FP_VAL_NPY__": fp_val,
        "__NEG_FEATURES_NPY__": neg_feat,
        "__BACKGROUND_DIR__": background,
        "__RIR_DIR__": rir,
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    cfg = work / "train_config.yaml"
    cfg.write_text(text, encoding="utf-8")
    log(f"rendered training config -> {cfg}")
    return cfg


def inject_real_clips(work: Path, slug: str) -> int:
    """Copy real positive WAVs from /data/clips into the generated-positives dir,
    resampled to 16 kHz mono, OVERSAMPLING each so the real clips reach a target
    fraction of the synthetic positive set. Real clips are NOT a config key — they
    must be dropped into positive_train before augment_clips. Returns WAVs written.

    Why oversample: openWakeWord otherwise swamps a handful of real clips among
    tens of thousands of synthetic ones (e.g. 30/20030 = 0.15% → statistically
    invisible), so the model never learns the deployment mic's signature — which
    is fatal for the XVF3800's heavy on-chip DSP. We duplicate each unique real
    clip N times so they make up ~WAKE_REAL_TARGET_FRACTION of the positives, and
    --augment_clips then mixes DIFFERENT background/RIR into every copy, so it's
    added variety rather than identical dupes. Caveat: with very few UNIQUE real
    clips this can overfit those exact recordings — more unique clips beat a bigger
    oversample factor. train.py nests dirs as output_dir/<model_name>/positive_train,
    so we write into work/<slug>/positive_train."""
    pos_dir = work / slug / "positive_train"
    pos_dir.mkdir(parents=True, exist_ok=True)
    if not CLIPS_IN.is_dir():
        log("no /data/clips mount; training on synthetic positives only.")
        return 0
    wavs = sorted(CLIPS_IN.glob("*.wav"))
    if not wavs:
        log("clips dir present but empty; synthetic positives only.")
        return 0
    try:
        import soundfile as sf
        from scipy.signal import resample_poly
    except Exception as e:
        die(f"audio libs missing for clip injection: {e}", 1)

    # Oversample factor to hit the target real fraction of the positive set.
    try:
        frac = float(os.environ.get("WAKE_REAL_TARGET_FRACTION", "0.2"))
    except ValueError:
        frac = 0.2
    frac = min(max(frac, 0.0), 0.9)
    n_synth = int(os.environ.get("WAKE_N_SAMPLES", "5000") or "5000")
    n_real = len(wavs)
    if frac > 0.0 and n_real > 0:
        # real*k / (real*k + synth) ≈ frac  ->  k ≈ frac/(1-frac) * synth/real
        oversample = max(1, round((frac / (1.0 - frac)) * n_synth / n_real))
    else:
        oversample = 1

    written = 0
    for i, w in enumerate(wavs):
        try:
            data, sr = sf.read(str(w))
            if getattr(data, "ndim", 1) > 1:  # downmix to mono
                data = data.mean(axis=1)
            if sr != 16000:
                from math import gcd
                g = gcd(int(sr), 16000)
                data = resample_poly(data, 16000 // g, int(sr) // g)
            for k in range(oversample):
                out = pos_dir / f"real_{i:04d}_{k:03d}.wav"
                sf.write(str(out), data, 16000, subtype="PCM_16")
                written += 1
        except Exception as e:
            log(f"  skip {w.name}: {e}")
    got = written / (written + n_synth) if (written + n_synth) else 0.0
    log(f"injected {n_real} unique real clip(s) ×{oversample} = {written} WAVs "
        f"(~{got:.0%} of positives; target {frac:.0%}) -> {pos_dir}")
    return written


def run_stage(cfg: Path, flag: str, device: str) -> None:
    env = dict(os.environ)
    # Belt-and-suspenders: force CPU visibility off if device==cpu so a stray
    # CUDA context isn't created.
    if device == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = "-1"
    argv = [sys.executable, str(TRAIN_PY), "--training_config", str(cfg), flag]
    log(f"$ {' '.join(argv)}")
    r = subprocess.run(argv, cwd=str(OWW_REPO), env=env)
    if r.returncode != 0:
        die(f"stage {flag} exited {r.returncode}", r.returncode or 1)


def export_model(work: Path, slug: str) -> None:
    """Locate the produced <slug>.onnx and copy it to /data/models/<slug>.onnx.
    train.py writes the onnx somewhere under its output/model dir; find it."""
    MODELS_OUT.mkdir(parents=True, exist_ok=True)
    # Search common locations: cwd, work dir, oww 'models' dir.
    candidates: list[Path] = []
    for root in (work, OWW_REPO, Path.cwd()):
        candidates += list(root.rglob(f"{slug}.onnx"))
    candidates = [c for c in candidates if c.is_file()]
    if not candidates:
        die(f"training finished but no {slug}.onnx was produced anywhere under "
            f"{work} / {OWW_REPO}. The host worker requires <slug>.onnx at "
            f"{{out}} — failing.", 5)
    # Prefer the most recently modified.
    src = max(candidates, key=lambda p: p.stat().st_mtime)
    dst = MODELS_OUT / f"{slug}.onnx"
    shutil.copy2(src, dst)
    # Copy the companion .json metadata if present (some Pi loaders want it).
    js = src.with_suffix(".onnx.json")
    if js.is_file():
        shutil.copy2(js, dst.with_suffix(".onnx.json"))
    log(f"exported model: {src} -> {dst}")


def main() -> int:
    slug = os.environ.get("WAKE_SLUG", "").strip()
    phrase = os.environ.get("WAKE_PHRASE", "").strip()
    if not slug:
        die("WAKE_SLUG not set (host wrapper passes -e WAKE_SLUG).", 2)
    if not phrase:
        die("WAKE_PHRASE not set (host wrapper passes -e WAKE_PHRASE).", 2)

    log(f"training wake word: slug={slug!r} phrase={phrase!r}")
    device = detect_device()
    ensure_dataset()       # ~7 GB precomputed feature .npy's (negatives + FP val)
    ensure_rirs()          # MIT impulse-response WAVs for augmentation
    ensure_background()    # background-noise WAVs for augmentation

    work = CACHE / "runs" / slug
    work.mkdir(parents=True, exist_ok=True)

    cfg = render_config(slug, phrase, device, work)

    # 1) synthetic positives (piper-sample-generator, internal). Resumable.
    run_stage(cfg, "--generate_clips", device)
    # 2) inject real clips into <slug>/positive_train BEFORE augment.
    inject_real_clips(work, slug)
    # 3) augment (varies acoustics, resamples to 16 kHz training set).
    run_stage(cfg, "--augment_clips", device)
    # 4) train + ONNX export.
    run_stage(cfg, "--train_model", device)
    # 5) place <slug>.onnx where the host worker will find it.
    export_model(work, slug)

    log(f"DONE: /data/models/{slug}.onnx ready.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # any uncaught error -> non-zero exit
        die(f"unhandled error: {e}", 1)
