#!/usr/bin/env python3
"""Container entrypoint for the portable openWakeWord trainer.

Runs IDENTICALLY whether the host launched us with ``--gpus`` or not: we ask
``torch.cuda.is_available()`` (which honors any ``CUDA_VISIBLE_DEVICES`` the
host wrapper set) and route generation/training to ``cuda`` or ``cpu``
accordingly. Steps:

  1. Detect device (GPU vs CPU) and print it.
  2. Ensure the one-time ~17.5 GB negative/feature dataset is in the NAMED cache
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

``--warm`` (no WAKE_SLUG needed) does step 2 only and exits: the
dashboard's "Turn on wake-word training" runs it once, so the first Train
doesn't start with an 18 GB download. Either way the process lowers its
own CPU/IO priority first (WAKE_NICE, default 10).

NOTHING here assumes a GPU exists or knows any host path — /data/clips,
/data/models, /data/cache are fixed in-container mountpoints the wrapper binds.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

OWW_REPO = Path("/opt/openWakeWord")
TRAIN_PY = OWW_REPO / "openwakeword" / "train.py"
TEMPLATE = Path("/opt/train_config.template.yaml")
TRAIN_RUNNER = Path("/opt/train_runner.py")

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

# Pins (read 2026-10-13): the Hugging Face dataset commits the downloads
# come from, the SHA-256 of the two feature files at that commit (their LFS
# object ids) and of the two background zips. A file that doesn't match is
# refused. The MIT impulse responses stream through `datasets` at their
# pinned commit; their 270 WAVs are not hashed one by one.
FEATURES_REPO = "davidscripka/openwakeword_features"
FEATURES_REVISION = "985bf1b47e7f19c07741af82bfe32d5a9dc56096"
FEATURES_SHA256 = {
    "openwakeword_features_ACAV100M_2000_hrs_16bit.npy":
        "721a66d0682c65a1b5c1da0aa109409cede1d20e28b15235c344b000cbb7654f",
    "validation_set_features.npy":
        "a56a8a0f8e0efb91900acc6de4c0cdf4c564842e8475a7d49b36c039e17a690f",
}
RIRS_REPO = "davidscripka/MIT_environmental_impulse_responses"
RIRS_REVISION = "b824a1ef2821f112fda0b9cb26e4278c62b425bb"
BACKGROUND_ZIP_SHA256 = {
    "fma_sample.zip": "bcf6b79a279ecb02f321dfaf6354f274955c4c770cfe5b6101016604b6bff1a5",
    "fsd50k_sample.zip": "faef06b7d4ebc3e59b083a1158337f75db7465af3cd62ddd3ef19266d20bf036",
}
# Written once the feature files were checked against FEATURES_SHA256 (a
# cache filled before the pins gets checked once).
VERIFIED = CACHE / f".oww_data_verified_{FEATURES_REVISION[:12]}"


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_features() -> None:
    """Every pinned feature file is there and matches its SHA-256 (about a
    minute for the 17.3 GB one, once)."""
    if VERIFIED.is_file():
        return
    log("checking the training data against its pinned SHA-256 (once) ...")
    for name, want in FEATURES_SHA256.items():
        hits = sorted(FEATURES_DIR.rglob(name))
        if not hits:
            die(f"{name} is missing from {FEATURES_DIR}", 4)
        got = sha256_file(hits[0])
        if got != want:
            die(f"{name} has SHA-256 {got}, the pinned commit's is {want}: refused. "
                f"Remove the cache volume (Settings > Wake words > Turn off and free the "
                f"space) and turn training on again.", 4)
    VERIFIED.write_text("ok\n")
    log("training data matches its pins.")


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
    volume, gated by a sentinel so reruns skip the ~17.5 GB pull. Detects
    cached-vs-fresh. The data is NOT baked into the image."""
    CACHE.mkdir(parents=True, exist_ok=True)
    if SENTINEL.is_file():
        log(f"cache HIT: dataset present at {CACHE} (sentinel exists) — "
            f"skipping ~17.5 GB download.")
        verify_features()
        return
    log(f"cache MISS: downloading one-time openWakeWord feature dataset into "
        f"{CACHE} (~17.5 GB; first run only) ...")
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
        # Precomputed ACAV100M negative features + validation set used by
        # openWakeWord training. ~17.5 GB on disk.
        snapshot_download(
            FEATURES_REPO,
            repo_type="dataset",
            revision=FEATURES_REVISION,
            local_dir=str(FEATURES_DIR),
        )
    except Exception as e:
        die(f"feature dataset download failed: {e}. Re-running resumes "
            f"(huggingface_hub caches partial files); check container network "
            f"+ that the named cache volume has room for ~17.5 GB.", 4)
    verify_features()
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
            RIRS_REPO, revision=RIRS_REVISION,
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
            name = url.rsplit("/", 1)[-1]
            got = sha256_file(Path(tmp_path))
            if got != BACKGROUND_ZIP_SHA256.get(name):
                os.unlink(tmp_path)
                die(f"{name} has SHA-256 {got}, pinned {BACKGROUND_ZIP_SHA256.get(name)}: "
                    f"refused.", 4)
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


def near_misses(phrase: str) -> list[str]:
    """The phrase's near misses for custom_negative_phrases (train_runner.py
    works them out from the phrase and the CMU dictionary); [] if that
    fails, which costs the extra negatives and nothing else."""
    import importlib.util

    try:
        spec = importlib.util.spec_from_file_location("_train_runner", TRAIN_RUNNER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        phrases = mod.phrase_near_misses(phrase)
    except Exception as e:  # noqa: BLE001
        log(f"no near-miss negatives: {e}")
        return []
    log(f"near-miss negatives ({len(phrases)}): {', '.join(phrases)}")
    return phrases


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
        "__CUSTOM_NEGATIVES__": json.dumps(near_misses(phrase), ensure_ascii=False),
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    cfg = work / "train_config.yaml"
    cfg.write_text(text, encoding="utf-8")
    log(f"rendered training config -> {cfg}")
    return cfg


# train.py's --augment_clips does nothing when positive_features_train.npy
# already exists (it never sees --overwrite), and the run dir under the cache
# volume is kept per slug. So a retrain after recording more (or different)
# clips reused the first run's features: the new clips never reached the
# model. These four are written together by that stage.
FEATURE_FILES = (
    "positive_features_train.npy", "negative_features_train.npy",
    "positive_features_test.npy", "negative_features_test.npy",
)
REAL_FINGERPRINT = ".real_clips.sha256"


def real_clip_fingerprint(wavs: list[Path]) -> str:
    """A digest of the real clips' names and contents (order-independent)."""
    import hashlib

    h = hashlib.sha256()
    for w in sorted(wavs, key=lambda p: p.name):
        h.update(w.name.encode("utf-8") + b"\0")
        h.update(hashlib.sha256(w.read_bytes()).digest())
    return h.hexdigest()


def real_wavs() -> list[Path]:
    return sorted(CLIPS_IN.glob("*.wav")) if CLIPS_IN.is_dir() else []


def reset_for_real_clips(model_dir: Path, wavs: list[Path]) -> bool:
    """Before injecting this run's real clips into <model_dir>/positive_train:
    always drop the previous run's injected copies (real_*.wav), and unless
    the stored features are recorded as computed from exactly this real clip
    set (mark_features_current, after a successful --augment_clips), delete
    them so --augment_clips computes them again. Synthetic clips are kept
    either way. Returns True when features were dropped."""
    pos_dir = model_dir / "positive_train"
    if pos_dir.is_dir():
        for old in pos_dir.glob("real_*.wav"):
            old.unlink()
    stamp = model_dir / REAL_FINGERPRINT
    before = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else None
    if before == real_clip_fingerprint(wavs):
        return False
    dropped = False
    for name in FEATURE_FILES:
        f = model_dir / name
        if f.is_file():
            f.unlink()
            dropped = True
    if stamp.is_file():
        stamp.unlink()
    return dropped


def scrub_real_copies(runs: Path) -> int:
    """Remove every run's copies of recorded clips (``real_*.wav``) under
    ``runs``: what a stopped or killed run left. The next run of a word
    copies its originals again."""
    n = 0
    if runs.is_dir():
        for f in runs.glob("*/*/positive_train/real_*.wav"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    if n:
        log(f"removed {n} copies of recorded clips an earlier run left in the cache volume")
    return n


class Stopped(SystemExit):
    """SIGTERM/SIGINT/SIGHUP: unwind, so the ``finally`` that removes the
    run's copies of the recorded clips runs (docker stop, or the docker
    client's signal proxy; the container runs under --init so the signal
    reaches this process)."""


def _on_stop_signal(signum, _frame) -> None:
    raise Stopped(128 + signum)


def install_stop_handlers() -> None:
    import signal

    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _on_stop_signal)
            except (OSError, ValueError):
                pass


def drop_real_copies(model_dir: Path) -> int:
    """Remove this run's copies of the household's recorded clips from the
    cache volume (they are recordings of somebody's voice; the server keeps
    the originals under ~/.domovoi/wake_clips and the next run copies them
    again). The features computed from them stay, which is what a retrain
    with the same clips reuses. Runs at the end of every run, failed or not."""
    pos_dir = model_dir / "positive_train"
    n = 0
    if pos_dir.is_dir():
        for f in pos_dir.glob("real_*.wav"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    if n:
        log(f"removed {n} copies of the recorded clips from the cache volume")
    return n


def mark_features_current(model_dir: Path, wavs: list[Path]) -> None:
    """Record which real clip set the features on disk were computed from."""
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / REAL_FINGERPRINT).write_text(
        real_clip_fingerprint(wavs) + "\n", encoding="utf-8")


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
    wavs = real_wavs()
    if reset_for_real_clips(work / slug, wavs):
        log("real clip set changed since the last run; dropped the old copies "
            "and computed features so augmentation runs again.")
    if not CLIPS_IN.is_dir():
        log("no /data/clips mount; training on synthetic positives only.")
        return 0
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
    # Through train_runner.py: phrase words the CMU dictionary lacks get an
    # espeak-ng pronunciation first (train.py's own fallback downloads a
    # DeepPhonemizer model that is no longer served).
    argv = [sys.executable, str(TRAIN_RUNNER), str(TRAIN_PY),
            "--training_config", str(cfg), flag]
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


def lower_priority() -> None:
    """Run (and let every stage this starts run) at a low CPU and disk
    priority inside the container: nice 10, and the idle I/O class where
    the kernel's scheduler honours one. The host wrapper's cgroup limits
    (--cpus, --cpu-shares, --memory) are what keep live voice responsive
    against this container; this only orders the work inside it. Best
    effort: WAKE_NICE=0 turns it off."""
    try:
        n = int(os.environ.get("WAKE_NICE", "10") or "0")
    except ValueError:
        n = 10
    if n <= 0 or not hasattr(os, "nice"):
        return
    try:
        os.nice(n)
    except OSError as e:
        log(f"could not lower the CPU priority: {e}")
    if shutil.which("ionice"):
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())],
                       capture_output=True, check=False)


def cache_bytes() -> int:
    total = 0
    for root, _dirs, files in os.walk(CACHE):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def warm() -> int:
    """``--warm``: download the datasets into the cache volume and stop
    (the dashboard's "Turn on wake-word training" runs this once, so the
    first Train doesn't wait on ~18 GB). Prints a progress line with the
    cache's size every 15 s while it works, and nothing is trained."""
    import threading

    log(f"warming the training data cache at {CACHE}")
    stop = threading.Event()

    def report() -> None:
        while not stop.wait(15):
            print(f"[entrypoint] warm: {cache_bytes()} bytes in the cache", flush=True)

    t = threading.Thread(target=report, daemon=True)
    t.start()
    try:
        ensure_dataset()
        ensure_rirs()
        ensure_background()
    finally:
        stop.set()
    print(f"[entrypoint] warm: {cache_bytes()} bytes in the cache", flush=True)
    log("DONE: the training data cache is ready.")
    return 0


def main() -> int:
    install_stop_handlers()
    lower_priority()
    if "--warm" in sys.argv[1:]:
        return warm()
    slug = os.environ.get("WAKE_SLUG", "").strip()
    phrase = os.environ.get("WAKE_PHRASE", "").strip()
    if not slug:
        die("WAKE_SLUG not set (host wrapper passes -e WAKE_SLUG).", 2)
    if not phrase:
        die("WAKE_PHRASE not set (host wrapper passes -e WAKE_PHRASE).", 2)

    log(f"training wake word: slug={slug!r} phrase={phrase!r}")
    device = detect_device()
    ensure_dataset()       # ~17.5 GB precomputed feature .npy's (negatives + FP val)
    ensure_rirs()          # MIT impulse-response WAVs for augmentation
    ensure_background()    # background-noise WAVs for augmentation

    # Copies a stopped or killed earlier run (of any word) left behind.
    scrub_real_copies(CACHE / "runs")
    work = CACHE / "runs" / slug
    work.mkdir(parents=True, exist_ok=True)

    cfg = render_config(slug, phrase, device, work)

    try:
        # 1) synthetic positives (piper-sample-generator, internal). Resumable.
        run_stage(cfg, "--generate_clips", device)
        # 2) inject real clips into <slug>/positive_train BEFORE augment.
        inject_real_clips(work, slug)
        # 3) augment (varies acoustics, resamples to 16 kHz training set).
        run_stage(cfg, "--augment_clips", device)
        mark_features_current(work / slug, real_wavs())
        # 4) train + ONNX export.
        run_stage(cfg, "--train_model", device)
        # 5) place <slug>.onnx where the host worker will find it.
        export_model(work, slug)
    finally:
        drop_real_copies(work / slug)

    log(f"DONE: /data/models/{slug}.onnx ready.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # any uncaught error -> non-zero exit
        die(f"unhandled error: {e}", 1)
