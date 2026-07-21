# Custom Wake Word Training ("Hey Domovoi")

End-to-end recipe for training a custom openWakeWord model. The Pi
satellites can ship `hey_jarvis` indefinitely as the dev wake word — this
is only needed when you actually want to say "Hey Domovoi." Plan ~1
evening of work; the recording session is the slowest part.

## What you'll end up with

- `hey_domovoi.onnx` — drop into `~/.domovoi/` on each Pi
- One config edit per Pi (`wake_word_model_path` in `~/.domovoi/config.toml`)
- The Pi restarts and now responds to "Hey Domovoi"

## Prerequisites

This pipeline runs **on Domovoi** (or any beefier Linux/Windows box with a
microphone), not on the Pi. The Pi just consumes the trained `.onnx`.

```bash
pip install openwakeword onnxruntime numpy sounddevice
```

The training tool itself comes from openWakeWord's repo — clone it:

```bash
git clone https://github.com/dscripka/openWakeWord.git
cd openWakeWord/notebooks
# automatic_model_training.ipynb is the supported entrypoint
```

## 1. Record positive clips

From the domovoi repo root:

```bash
python scripts/record_wake_word_clips.py \
    --count 30 \
    --out ~/.domovoi/wake_clips/hey_domovoi \
    --phrase "Hey Domovoi"
```

The script counts down for each clip, records ~2 seconds, and reports a
peak level. Aim for 30 clips total. Vary:

- **Distance**: 10 of arm's-length, 10 of about a meter, 10 from across the room
- **Tone**: calm conversational, slightly raised, half-asked (rising intonation)
- **Background**: some quiet, some with normal household noise

If `peak < 2000` shows up, the gain or distance is too low; redo the clip
(delete the file and re-run — the script resumes).

The same script lives at `scripts/record_wake_word_clips.py` if you'd
rather run it on a Pi (with the same mic board that satellite uses — the
ReSpeaker 2-Mics HAT or the XVF3800 USB array) for clips that match the
deployment microphone — recommended for the best detection accuracy. This
matters more for an XVF3800 target: its on-chip beamforming/AGC/noise
suppression reshapes the captured signal, so clips recorded on a HAT (or
on Domovoi) won't match what that satellite actually hears.

## 2. Train the model

Easiest path: openWakeWord's `automatic_model_training.ipynb` notebook.

```bash
cd openWakeWord/notebooks
jupyter notebook  # or use VSCode's notebook UI
```

Open `automatic_model_training.ipynb` and edit the configuration cell:

- `target_phrase` → `"Hey Domovoi"` (used for synthetic positive augmentation)
- `target_language` → `"en"`
- `output_path` → `hey_domovoi.onnx`
- `train_clips_path` → `/path/to/your/.domovoi/wake_clips/hey_domovoi`

Run all cells. The notebook downloads ~7 GB of negative training data
(speech, music, noise) the first time, then trains for 30–60 minutes on
CPU (faster on GPU).

If you don't want to deal with the notebook, the CLI equivalent is in
the openWakeWord repo: `python -m openwakeword.train_model --help`.

## 3. Test before deployment

```python
import time
import numpy as np
import sounddevice as sd
from openwakeword.model import Model

# openWakeWord's predict() requires chunks that are multiples of 80 ms
# (1280 samples at 16 kHz). Smaller chunks make it silently return
# zero-prediction every call. Accumulate mic frames in a remainder buffer
# and feed predict() one full chunk at a time.
OWW_CHUNK = 1280  # 80 ms @ 16 kHz

m = Model(wakeword_models=["hey_domovoi.onnx"], inference_framework="onnx")
print("Say the wake phrase a few times — Ctrl+C to stop.")

pending = np.empty(0, dtype=np.int16)

def cb(indata, frames, t, status):
    global pending
    pcm = (indata[:, 0] * 32767).astype(np.int16)
    pending = np.concatenate([pending, pcm])
    while pending.size >= OWW_CHUNK:
        chunk, pending = pending[:OWW_CHUNK], pending[OWW_CHUNK:]
        pred = m.predict(chunk)
        score = pred.get("hey_domovoi", 0.0)
        if score > 0.5:
            print(f"  detected (score={score:.2f})")

with sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=480, callback=cb):
    while True:
        time.sleep(0.1)
```

Aim for **clean detections at conversational distance, no false fires
during normal background TV/music**. If the model has too many false
positives, retrain with more clips that include household-noise samples,
or raise `wake.threshold` in the satellite config (try 0.6 or 0.7).

## 4. Deploy

For each Pi:

```bash
scp hey_domovoi.onnx domovoi-kitchen.local:~/.domovoi/
ssh domovoi-kitchen.local
nano ~/.domovoi/config.toml
# Edit:
#   wake_word = "hey_domovoi"
#   wake_word_model_path = "~/.domovoi/hey_domovoi.onnx"
sudo systemctl restart domovoi-satellite   # or kill + restart manually
```

## 5. Automating from the dashboard (Feature 5 — `wake_word_train_command`)

The Wake Words tab (Settings → Wake Words) can record positive clips on a
satellite and queue training on Domovoi. Because openWakeWord's automatic
training is **Linux-only** (the `piper-sample-generator` dependency does not
run on native Windows) and Domovoi runs the core service on Windows 11, the
`wake_word_trainer` worker never trains in-process — it shells out to an
**operator-supplied command** you configure, gated off by default:

```
# domovoi/.env  (or the gear-modal config)
WAKE_WORD_TRAINER_ENABLED=true
WAKE_WORD_TRAIN_COMMAND=wsl bash -lc 'cd ~/oww && python train.py --positives "{clips_dir}" --phrase "{phrase}" --out "{out}"'
```

The four placeholders are substituted before the command runs:

| Placeholder   | Value                                                       |
|---------------|-------------------------------------------------------------|
| `{clips_dir}` | the recorded positive-clip dir (`<wake_clips_dir>/<slug>/`) |
| `{phrase}`    | the target phrase (e.g. `hey domovoi`)                      |
| `{slug}`      | the model stem — the worker expects `<out>` to be `<slug>.onnx` |
| `{out}`       | the absolute output path the worker then registers          |

**Quote every placeholder in the template** (`"{clips_dir}"`, `"{phrase}"`,
`"{out}"`) — the worker tokenizes the command with `shlex`, so a phrase or
path with spaces (or shell-significant characters) must be quoted or it will
split into extra argv elements. The command runs in a background thread with a
1-hour timeout; the worker marks the row `ready` only if it exits 0 **and**
`<slug>.onnx` materialized at `{out}`. Point it at a WSL2 + CUDA env, a
`docker run --gpus all …` Linux image, or any host that can run the recipe in
§2; on success the model is served back to satellites over `/v1/wake-models`
and pushed from the dashboard's "Push to …" control. With the command empty
(the default), a queued row is failed with a pointer back to this section
rather than hanging.

## Troubleshooting

- **No detections at all**: confirm `wake_word` matches the model file's
  stem (`hey_domovoi.onnx` → `wake_word = "hey_domovoi"`). The
  openWakeWord prediction dict is keyed on the stem.
- **Triggers on "okay" or "thanks"**: too few clips and/or no
  hard-negative samples. Retrain with more variety.
- **Quieter than `hey_jarvis`**: openWakeWord normalizes input, so this
  is usually a confidence threshold issue — lower `wake.threshold` to 0.4.
- **Works on Domovoi but not the Pi**: re-record the clips *on the Pi*
  through the board that satellite uses (HAT or XVF3800) and retrain.
  Codec frequency response and noise floor differ — and the XVF3800's
  on-chip beamforming/AGC/noise suppression differs more still; matching
  the deployment mic helps a lot.
