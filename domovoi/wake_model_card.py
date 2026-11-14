"""A trained wake word, packed to share: the model, its checksums, a model
card and the licence notice (Settings → Voice → Wake words → Download).

``build_bundle(row)`` returns the zip the dashboard's Download button
saves. Everything in it comes from what this server knows:

* the ``wake_words`` row (phrase, slug, threshold, synthetic_only, the
  number of recordings the model was trained on);
* the model file itself (size, SHA-256) and its ``.onnx.json`` companion;
* the training record the trainer worker writes when a training succeeds
  (:func:`record_training`: when, which Domovoi, which trainer build, the
  trainer's knobs as the run got them). A model trained before that record
  existed falls back to the clips folder's training marker for the date;
* the last "Score clips" result for THIS model file (:func:`record_score`,
  matched by SHA-256, so a later retrain never inherits an older score);
* the trainer's own configuration in ``scripts/wake_word/docker`` (the
  datasets, their pinned revisions and checksums, the pinned openWakeWord
  and piper-sample-generator commits, the training config's settings),
  read from the files, never assumed. A dataset the config names that the
  licence table below doesn't know is listed with "licence not recorded".

What the server doesn't know is written "not recorded", with how to
measure it. Nothing private goes in: no recordings, no clip names, no room,
no person's name (the word's display name is left out; its phrase and slug
are what the model is), no paths, addresses or host names. The training
record and score live in ``~/.domovoi/wake_trainer/models/<slug>.json``,
outside ``wake_models_dir`` so the satellites' model manifest never serves
them.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import logging
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from domovoi.config import settings

log = logging.getLogger(__name__)

MODEL_LICENCE = "CC BY-NC-SA 4.0"
MODEL_LICENCE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
NOT_RECORDED = "not recorded"

_SLUG_RE = re.compile(r"^[a-z0-9_]{1,80}$")


# ── where things are ─────────────────────────────────────────────────────

def models_dir() -> Path:
    return Path(settings.wake_models_dir)


def record_dir() -> Path:
    """``~/.domovoi/wake_trainer/models``: beside wake_models, not in it."""
    return models_dir().parent / "wake_trainer" / "models"


def record_path(slug: str) -> Path | None:
    if not _SLUG_RE.match(slug or ""):
        return None
    return record_dir() / f"{slug}.json"


def trainer_docker_dir() -> Path:
    return Path(settings.repo_dir) / "scripts" / "wake_word" / "docker"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ── the per-model record (training, score) ────────────────────────────────

def read_record(slug: str) -> dict[str, Any]:
    path = record_path(slug)
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_record(slug: str, data: dict[str, Any]) -> None:
    path = record_path(slug)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), "utf-8")
        tmp.replace(path)
    except OSError as e:
        log.warning("wake model card: could not save the record for %s: %s", slug, e)


# The trainer knobs as the run's environment had them ("" = the image's
# default, which the card reads from the trainer's entrypoint).
TRAINING_KNOBS = (
    "WAKE_TRAINER_N_SAMPLES", "WAKE_TRAINER_N_SAMPLES_VAL", "WAKE_TRAINER_STEPS",
    "WAKE_TRAINER_REAL_TARGET_FRACTION",
)


def record_training(slug: str, *, synthetic_only: bool, recordings: int,
                    env: dict[str, str], gpu: bool | None,
                    trainer_source: str | None, domovoi_version: str | None) -> None:
    """Blocking. A training of ``slug`` succeeded: keep what the card needs
    about it. The model file's SHA-256 ties the record to that file, so a
    model replaced by hand never shows another run's settings. A new
    training drops the old score (it scored a different model)."""
    model = models_dir() / f"{slug}.onnx"
    try:
        digest = sha256_file(model)
    except OSError:
        digest = None
    _write_record(slug, {
        "training": {
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model_sha256": digest,
            "synthetic_only": bool(synthetic_only),
            "recordings": int(recordings),
            "knobs": {k: env.get(k, "") for k in TRAINING_KNOBS},
            "device": None if gpu is None else ("gpu" if gpu else "cpu"),
            "trainer_source": trainer_source,
            "domovoi_version": domovoi_version,
        },
    })


def record_score(slug: str, summary: dict[str, Any]) -> None:
    """Blocking. "Score clips" ran on ``slug``'s model: keep its summary
    (counts and ratios only, no clip names) with the model's SHA-256."""
    model = models_dir() / f"{slug}.onnx"
    try:
        digest = sha256_file(model)
    except OSError:
        return
    keep = {k: summary.get(k) for k in (
        "threshold", "n_clips", "raw_recall", "raw_max", "trimmed_recall",
        "trimmed_max", "silence_score")}
    data = read_record(slug)
    data["score"] = {
        "model_sha256": digest,
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **keep,
    }
    _write_record(slug, data)


def forget(slug: str) -> None:
    path = record_path(slug)
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# ── the trainer's own configuration ──────────────────────────────────────

_ENTRYPOINT_NAMES = (
    "FEATURES_REPO", "FEATURES_REVISION", "FEATURES_SHA256", "RIRS_REPO",
    "RIRS_REVISION", "BACKGROUND_ZIP_URLS", "BACKGROUND_ZIP_SHA256",
)
_ENV_DEFAULT_RE = re.compile(r"""os\.environ\.get\(\s*["'](WAKE_[A-Z_]+)["']\s*,\s*["']([^"']*)["']\s*\)""")
_ARG_RE = re.compile(r"^ARG\s+([A-Z_]+)=(\S+)\s*$", re.M)
_VOICE_RE = re.compile(r"""(https://\S+/([\w.-]+\.pt))'?\s*&&""")
_VOICE_SHA_RE = re.compile(r"""echo\s+"([0-9a-f]{64})\s+\S+/models/([\w.-]+\.pt)""")
_TEMPLATE_KEYS = ("model_type", "layer_size", "augmentation_rounds", "max_negative_weight",
                  "target_false_positives_per_hour")


def trainer_config(docker_dir: Path | None = None) -> dict[str, Any]:
    """Blocking. What ``scripts/wake_word/docker`` says the trainer uses.
    Parsed, never imported or run; a file that can't be read leaves its
    keys out (the card then says "not recorded")."""
    d = docker_dir or trainer_docker_dir()
    out: dict[str, Any] = {}
    try:
        src = (d / "entrypoint.py").read_text("utf-8")
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id in _ENTRYPOINT_NAMES:
                try:
                    out[node.targets[0].id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
        out["env_defaults"] = {k: v for k, v in _ENV_DEFAULT_RE.findall(src)}
    except (OSError, SyntaxError, ValueError):
        pass
    try:
        df = (d / "Dockerfile").read_text("utf-8")
        args = dict(_ARG_RE.findall(df))
        for k in ("BASE", "OWW_REF", "PSG_REF"):
            if args.get(k):
                out[k] = args[k]
        m = _VOICE_RE.search(df)
        if m:
            out["VOICE_URL"], out["VOICE_FILE"] = m.group(1), m.group(2)
        m = _VOICE_SHA_RE.search(df)
        if m and m.group(2) == out.get("VOICE_FILE"):
            out["VOICE_SHA256"] = m.group(1)
    except OSError:
        pass
    try:
        tpl = (d / "train_config.template.yaml").read_text("utf-8")
        cfg = {}
        for key in _TEMPLATE_KEYS:
            m = re.search(rf"^{key}:\s*([^\s#]+)", tpl, re.M)
            if m:
                cfg[key] = m.group(1)
        if cfg:
            out["train_config"] = cfg
    except OSError:
        pass
    return out


# Licences and attribution, keyed by what the trainer's config names. The
# config gives the identifiers, revisions and checksums; this table gives
# what their publishers say about them (docs/LICENSES.md, "The wake-word
# trainer"; scripts/wake_word/README.md section 5).
DATASETS: dict[str, dict[str, str]] = {
    "davidscripka/openwakeword_features#negatives": {
        "what": "Negative features: ACAV100M, about 2,000 hours of general audio, as openWakeWord features",
        "role": "training (the sounds that are not the wake word)",
        "licence": "CC BY-NC-SA 4.0 (non-commercial, share-alike)",
        "attribution": (
            "Negative training features from openWakeWord (David Scripka), "
            "https://huggingface.co/datasets/davidscripka/openwakeword_features, "
            "CC BY-NC-SA 4.0. Computed from ACAV100M: Lee et al., \"ACAV100M: Automatic "
            "Curation of Large-Scale Datasets for Audio-Visual Video Representation "
            "Learning\", ICCV 2021."),
    },
    "davidscripka/openwakeword_features#validation": {
        "what": "False-wake validation features",
        "role": "checking the model while it trains (choosing the checkpoint)",
        "licence": "includes MUSDB18, licensed for educational use only",
        "attribution": (
            "Validation features from openWakeWord (David Scripka), "
            "https://huggingface.co/datasets/davidscripka/openwakeword_features. Includes "
            "MUSDB18: Rafii et al., \"The MUSDB18 corpus for music separation\", 2017, "
            "https://sigsep.github.io/datasets/musdb.html (educational use only)."),
    },
    "davidscripka/MIT_environmental_impulse_responses": {
        "what": "Room impulse responses (room echo)",
        "role": "training (augmentation: makes the clips sound like rooms)",
        "licence": "no licence stated",
        "attribution": (
            "Room impulse responses from the MIT McDermott lab's IR Survey "
            "(Traer and McDermott, \"Statistics of natural reverberation enable perceptual "
            "separation of sound and space\", PNAS 2016), "
            "https://mcdermottlab.mit.edu/Reverb/IR_Survey.html, as packaged at "
            "https://huggingface.co/datasets/davidscripka/MIT_environmental_impulse_responses. "
            "No licence is stated for them."),
    },
    "fma_sample.zip": {
        "what": "Background music: a sample of the Free Music Archive (FMA)",
        "role": "training (augmentation: background noise)",
        "licence": "per track, Creative Commons licences, some non-commercial",
        "attribution": (
            "Background clips from FMA: Defferrard et al., \"FMA: A Dataset for Music "
            "Analysis\", ISMIR 2017, https://github.com/mdeff/fma, sampled by openWakeWord. "
            "Each track keeps its artist's licence."),
    },
    "fsd50k_sample.zip": {
        "what": "Background sounds: a sample of FSD50K",
        "role": "training (augmentation: background noise)",
        "licence": "per clip CC0, CC BY or CC BY-NC (some non-commercial)",
        "attribution": (
            "Background clips from FSD50K: Fonseca et al., \"FSD50K: An Open Dataset of "
            "Human-Labeled Sound Events\", IEEE/ACM TASLP 2022, "
            "https://zenodo.org/records/4060432, sampled by openWakeWord. Each clip keeps "
            "its uploader's Freesound licence."),
    },
    "voice": {
        "what": "Synthetic voices: piper-sample-generator's LibriTTS-R voice model",
        "role": "making the spoken wake-word clips",
        "licence": "the generator's code is MIT; the voice model states no licence of its own "
                   "and was trained on LibriTTS-R (CC BY 4.0)",
        "attribution": (
            "Synthetic clips made with piper-sample-generator (MIT), "
            "https://github.com/dscripka/piper-sample-generator, voice model trained on "
            "LibriTTS-R: Koizumi et al., \"LibriTTS-R: A Restored Multi-Speaker "
            "Text-to-Speech Corpus\", Interspeech 2023, https://www.openslr.org/141/ "
            "(CC BY 4.0)."),
    },
    "openwakeword": {
        "what": "openWakeWord's training code",
        "role": "the training itself",
        "licence": "Apache-2.0",
        "attribution": "Trained with openWakeWord (David Scripka), "
                       "https://github.com/dscripka/openWakeWord, Apache-2.0.",
    },
}


def datasets_in_use(cfg: dict[str, Any]) -> list[dict[str, str]]:
    """The inputs the trainer's config names, each with its licence entry
    and the pin the config holds for it. One the table doesn't know keeps
    its name with "licence not recorded"."""
    rows: list[dict[str, str]] = []

    def add(key: str, source: str, pin: str) -> None:
        entry = DATASETS.get(key)
        if entry is None:
            entry = {"what": source, "role": "training", "licence": "licence not recorded here: "
                     "check its publisher's page", "attribution": f"{source} (licence not recorded)."}
        rows.append({**entry, "source": source, "pin": pin})

    repo = cfg.get("FEATURES_REPO")
    if repo:
        rev = cfg.get("FEATURES_REVISION") or ""
        for fname, digest in sorted((cfg.get("FEATURES_SHA256") or {}).items()):
            part = "validation" if "validation" in fname else "negatives"
            key = f"{repo}#{part}" if f"{repo}#{part}" in DATASETS else repo
            add(key, f"huggingface.co/datasets/{repo}: {fname}",
                f"revision {rev}, sha256 {digest}")
    rirs = cfg.get("RIRS_REPO")
    if rirs:
        add(rirs, f"huggingface.co/datasets/{rirs}", f"revision {cfg.get('RIRS_REVISION') or '?'}")
    shas = cfg.get("BACKGROUND_ZIP_SHA256") or {}
    for url in cfg.get("BACKGROUND_ZIP_URLS") or []:
        name = str(url).rsplit("/", 1)[-1]
        add(name, str(url), f"sha256 {shas.get(name, '?')}")
    if cfg.get("VOICE_FILE"):
        add("voice", f"{cfg['VOICE_FILE']} ({cfg.get('VOICE_URL', '')})",
            f"sha256 {cfg.get('VOICE_SHA256', '?')}; generator commit {cfg.get('PSG_REF', '?')}")
    if cfg.get("OWW_REF"):
        add("openwakeword", "github.com/dscripka/openWakeWord", f"commit {cfg['OWW_REF']}")
    return rows


# ── the card ─────────────────────────────────────────────────────────────

def _knob(training: dict[str, Any] | None, cfg: dict[str, Any], knob: str,
          image_key: str) -> str:
    """A trainer setting as the run had it: the run's own value, the
    image's default when the run left it to the image, else not recorded."""
    if not training:
        return NOT_RECORDED
    value = ((training.get("knobs") or {}).get(knob) or "").strip()
    if value:
        return value
    default = (cfg.get("env_defaults") or {}).get(image_key)
    return f"{default} (the trainer's default)" if default else "the trainer's default"


def _md_escape(text: str) -> str:
    return re.sub(r"([\\`*_|<>\[\]])", r"\\\1", str(text))


def build_card(row: dict[str, Any], *, model: Path, companion: Path | None,
               cfg: dict[str, Any], record: dict[str, Any],
               trained_marker: dict[str, Any] | None) -> str:
    slug = row["slug"]
    phrase = _md_escape(row["phrase"])
    synthetic = bool(row.get("synthetic_only"))
    size = model.stat().st_size
    digest = sha256_file(model)
    training = record.get("training") if isinstance(record.get("training"), dict) else None
    if training and training.get("model_sha256") not in (None, digest):
        training = None   # that record describes another model file
    score = record.get("score") if isinstance(record.get("score"), dict) else None
    if score and score.get("model_sha256") != digest:
        score = None

    if training and training.get("trained_at"):
        trained_at = training["trained_at"]
    elif trained_marker and trained_marker.get("trained_at") and not synthetic:
        trained_at = f"{trained_marker['trained_at']} (from the clips folder's training note)"
    else:
        mtime = datetime.fromtimestamp(model.stat().st_mtime, timezone.utc)
        trained_at = f"{NOT_RECORDED}; the model file was written {mtime.isoformat(timespec='seconds')}"

    recordings = row.get("trained_clips")
    if recordings is None and training:
        recordings = training.get("recordings")
    if recordings is None and trained_marker and not synthetic:
        try:
            recordings = int(trained_marker.get("clips") or 0) or None
        except (TypeError, ValueError):
            recordings = None
    if synthetic:
        recorded_line = "0 (trained on synthetic voices only)"
    elif recordings is None:
        recorded_line = f"{NOT_RECORDED} (it may include the household's recordings)"
    else:
        recorded_line = str(int(recordings))
    used_recordings = (not synthetic) and (recordings is None or int(recordings) > 0)

    n_samples = _knob(training, cfg, "WAKE_TRAINER_N_SAMPLES", "WAKE_N_SAMPLES")
    n_val = _knob(training, cfg, "WAKE_TRAINER_N_SAMPLES_VAL", "WAKE_N_SAMPLES_VAL")
    steps = _knob(training, cfg, "WAKE_TRAINER_STEPS", "WAKE_STEPS")
    frac = (_knob(training, cfg, "WAKE_TRAINER_REAL_TARGET_FRACTION", "WAKE_REAL_TARGET_FRACTION")
            if not synthetic else "not used (no recordings)")
    tcfg = cfg.get("train_config") or {}

    L: list[str] = []
    a = L.append
    a(f"# Wake word model: \"{phrase}\"")
    a("")
    a("Made by a Domovoi server's wake-word trainer. Everything below is what "
      "that server knows about this model; what it doesn't know says "
      f"\"{NOT_RECORDED}\".")
    a("")
    a("## The model")
    a("")
    a("| | |")
    a("|---|---|")
    a(f"| Phrase | {phrase} |")
    a(f"| File | `{slug}.onnx` (openWakeWord, ONNX) |")
    a(f"| Model name (the openWakeWord key) | `{slug}` |")
    a(f"| Recommended threshold | {float(row['threshold']):g} (the wake sensitivity this server uses for it) |")
    a(f"| Size | {size} bytes |")
    a(f"| SHA-256 | `{digest}` |")
    if companion is not None:
        a(f"| Companion file | `{companion.name}`, SHA-256 `{sha256_file(companion)}` |")
    a(f"| Trained | {trained_at} |")
    a(f"| Licence when shared | {MODEL_LICENCE} ({MODEL_LICENCE_URL}); see LICENSE-NOTICE.md |")
    a("")
    a("## How it was trained")
    a("")
    a("| | |")
    a("|---|---|")
    a(f"| Domovoi version | {(training or {}).get('domovoi_version') or NOT_RECORDED} |")
    src = (training or {}).get("trainer_source")
    a(f"| Trainer build | {('built from scripts/wake_word/docker at git tree ' + src) if src and src != 'unknown' else NOT_RECORDED} |")
    dev = (training or {}).get("device")
    a(f"| Trained on | {dev.upper() if dev else NOT_RECORDED} |")
    a(f"| Synthetic voices only | {'yes' if synthetic else 'no'} |")
    a(f"| The household's recordings used | {recorded_line} |")
    a(f"| Synthetic clips | {n_samples} |")
    a(f"| Synthetic validation clips | {n_val} |")
    a(f"| Training steps | {steps} |")
    a(f"| Share of positives copied from recordings | {frac} |")
    for key, label in (("model_type", "Model type"), ("layer_size", "Layer size"),
                       ("augmentation_rounds", "Augmentation rounds"),
                       ("max_negative_weight", "Max negative weight"),
                       ("target_false_positives_per_hour", "Target false wakes per hour (training goal)")):
        a(f"| {label} | {tcfg.get(key, NOT_RECORDED)} |")
    a("")
    if not training:
        a("This run's own settings (version, build, clip counts, steps) were not "
          "recorded: the model was trained before Domovoi kept them. Train it again "
          "to record them. The model type and training goals above are the "
          "trainer's as it is set up now.")
        a("")
    a("Augmentation: each clip is mixed with background sounds and room echo "
      "from the datasets below, then turned into openWakeWord features. Near "
      "misses of the phrase (words that sound close to it) are spoken by the "
      "same synthetic voices as negatives.")
    a("")
    a("## Training data")
    a("")
    a("Read from the trainer's configuration on the server that made this "
      "model (`scripts/wake_word/docker`)"
      + (". The model predates Domovoi's record of its training, so this is "
         "the trainer as it is set up now." if not training else "."))
    a("")
    rows = datasets_in_use(cfg)
    if rows:
        a("| Input | Used for | Licence | Pinned |")
        a("|---|---|---|---|")
        for r in rows:
            a(f"| {_md_escape(r['what'])} | {r['role']} | {r['licence']} | {_md_escape(r['pin'])} |")
    else:
        a(f"The trainer's configuration could not be read: {NOT_RECORDED}.")
    if used_recordings:
        a("")
        a("Positive clips also include recordings of the household saying the "
          "phrase (not included here).")
    a("")
    a("## Measured")
    a("")
    if score:
        a(f"\"Score clips\" on {score.get('scored_at') or 'an unrecorded date'}, against "
          f"{score.get('n_clips')} of the household's recordings at threshold "
          f"{score.get('threshold')}. These are clips the model may have been "
          "trained on, so they show it works on its own data, not how it does "
          "for other voices.")
        a("")
        a("| | |")
        a("|---|---|")
        a(f"| Recordings that woke it (raw) | {score.get('raw_recall')} |")
        a(f"| Recordings that woke it (trimmed) | {score.get('trimmed_recall') if score.get('trimmed_recall') is not None else NOT_RECORDED} |")
        a(f"| Highest score (raw) | {score.get('raw_max')} |")
        a(f"| Score on silence | {score.get('silence_score') if score.get('silence_score') is not None else NOT_RECORDED} |")
    else:
        a(f"Score on the household's recordings: {NOT_RECORDED}. On the server, "
          "open the wake word's clips and use Score clips, then Download again.")
    a("")
    a(f"- False wakes per hour: {NOT_RECORDED}. To measure: run the model at the "
      "recommended threshold over several hours of speech, TV and music that never "
      "says the phrase, and count the wakes.")
    a(f"- Missed wakes on voices it never heard: {NOT_RECORDED}. To measure: record "
      "the phrase from people (and rooms) not in the training, then score them at the "
      "recommended threshold.")
    a("")
    a("## Before sharing")
    a("")
    if synthetic:
        a("This model was trained on synthetic voices only: nobody's recordings went into it.")
    elif recordings is None:
        a("This server can't tell whether this model was trained on recordings of the "
          "household. Treat it as if it was.")
    elif int(recordings) > 0:
        a(f"This model was trained on {int(recordings)} recordings of the household "
          "saying the phrase, as well as synthetic voices.")
    else:
        a("This model was trained on synthetic voices only: nobody's recordings went into it.")
    if used_recordings:
        a("")
        a("The `.onnx` file holds learned weights, not the recordings: they can't be "
          "played back from it. But those voices shaped it, and it may work better for "
          "them than for others. Whether to share it is the household's decision. A "
          "model trained on synthetic voices only (Train on synthetic voices only) "
          "carries nobody's voice and usually works better for other homes.")
    a("")
    a("The training data's licences bind anyone who shares this model: the negative "
      "features are CC BY-NC-SA 4.0 (non-commercial, share-alike), some background "
      "clips are non-commercial, the validation set includes MUSDB18 (educational use "
      "only) and the room echoes state no licence. So it is offered under CC BY-NC-SA "
      "4.0, with the attribution in LICENSE-NOTICE.md, and never commercially. "
      "Domovoi's own code stays MIT.")
    a("")
    return "\n".join(L)


def build_notice(row: dict[str, Any], cfg: dict[str, Any]) -> str:
    phrase = _md_escape(row["phrase"])
    L: list[str] = []
    a = L.append
    a(f"# Licence notice: the \"{phrase}\" wake word model")
    a("")
    a(f"The model (`{row['slug']}.onnx`) is offered under the Creative Commons "
      f"Attribution-NonCommercial-ShareAlike 4.0 International licence ({MODEL_LICENCE}): "
      f"{MODEL_LICENCE_URL}. Domovoi's code is MIT; that licence does not cover this model.")
    a("")
    a("Why NC-SA: the model was trained on data under that licence, and on data under "
      "other terms listed below. Whoever passes it on must give the attribution below, "
      "may not use it commercially, and must share anything built from it under the "
      "same licence.")
    a("")
    a("Two inputs are weaker than the label: the validation set includes MUSDB18, "
      "licensed for educational use only, and the room impulse responses state no "
      "licence at all. openWakeWord publishes its own models with the same inputs "
      "under CC BY-NC-SA 4.0. This notice is a summary, not legal advice.")
    a("")
    a("## Attribution")
    a("")
    seen: set[str] = set()
    for r in datasets_in_use(cfg):
        if r["attribution"] in seen:
            continue
        seen.add(r["attribution"])
        a(f"- {r['attribution']}")
    if not seen:
        a(f"- The trainer's configuration could not be read: datasets {NOT_RECORDED}. "
          "See Domovoi's docs/LICENSES.md, \"The wake-word trainer\".")
    a("- Model trained with Domovoi's wake-word trainer "
      "(https://github.com/coders-farm-official/domovoi, MIT).")
    a("")
    return "\n".join(L)


def bundle_name(slug: str) -> str:
    return f"{slug}-wake-word.zip"


def build_bundle(row: dict[str, Any]) -> bytes:
    """Blocking. The zip for one trained word. Raises FileNotFoundError
    when its model file is missing."""
    slug = row["slug"]
    if not _SLUG_RE.match(slug or ""):
        raise FileNotFoundError("not a model name")
    mdir = models_dir().resolve()
    model = (mdir / f"{slug}.onnx").resolve()
    model.relative_to(mdir)
    if not model.is_file():
        raise FileNotFoundError(f"{slug}.onnx")
    companion = mdir / f"{slug}.onnx.json"
    companion = companion if companion.is_file() and not companion.is_symlink() else None

    from domovoi import wake_clip_quality as wq

    marker = wq.read_trained(Path(settings.wake_clips_dir) / slug)
    cfg = trainer_config()
    card = build_card(row, model=model, companion=companion, cfg=cfg,
                      record=read_record(slug), trained_marker=marker)
    notice = build_notice(row, cfg)

    files: list[tuple[str, bytes]] = [(f"{slug}.onnx", model.read_bytes())]
    if companion is not None:
        files.append((companion.name, companion.read_bytes()))
    files += [("MODEL_CARD.md", card.encode("utf-8")),
              ("LICENSE-NOTICE.md", notice.encode("utf-8"))]
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in files)
    files.append(("SHA256SUMS", sums.encode("ascii")))

    buf = io.BytesIO()
    stamp = time.gmtime()[:6]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            info = zipfile.ZipInfo(f"{slug}/{name}", date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()
