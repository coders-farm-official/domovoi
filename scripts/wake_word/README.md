# Custom wake words ("Hey Domovoi")

A satellite wakes on one openWakeWord model. The shipped default is
`hey_jarvis`. A household can train its own ("Hello Domo", "Hey Domovoi")
on the Domovoi server, from the dashboard, with no shell. This page covers
turning training on, training a word, the settings, what a training does to
the server while it runs, and the licence of what it makes. How the trainer
image is built and run: [DOCKER_TRAINER.md](DOCKER_TRAINER.md).

## 1. Turn training on (once)

Training runs openWakeWord's recipe in a Linux container on the server:
Docker Engine on a Linux server and inside the Windows install's WSL
distro, Docker Desktop on a development PC. It needs a 12 GB image and
18 GB of training data, so it is off until an admin asks for it:
**Settings → Voice → Wake words → Wake-word training → Turn on wake-word training**.

Before it starts, the card says what training needs and what this server
has:

| | What training needs | Measured (2026-10-13) |
|---|---|---|
| **Disk** | About 32 GB the first time (the trainer image and its training data), then about 0.5 GB more per wake word. Refused unless that much plus 5 GB to spare is free where Docker keeps its images. | Image 12.4 GB on disk (4.1 GB compressed); training data 17.9 GB (17.3 GB of it one file); per-word run folder 0.5 GB; the model itself 205 KB. |
| **Download** | About 22 GB, once: the trainer's base image, the packages and models its build fetches, and its training data. | About 22.5 GB came over the network in a test install: the 3.2 GB base image, the build's packages and models, the 17.9 GB of training data (about 6 min on a ~90 MB/s line). |
| **Memory** | A 24 GB server: the line where the install picks the standard models (`MemTotal` of at least 22,528 MiB). On the Windows install that is a PC with about 32 GB, since its distro gets the PC's memory minus 8 GB. | Peak 14.7 GiB with page cache, 9.3 GiB of its own. |
| **Time per word** | About 20 minutes on an NVIDIA GPU (26 the first time). On the CPU, about 75 minutes on 8 cores; the card scales that to this server's cores. | RTX 4070 Ti SUPER; 8 CPU cores. Generating the synthetic speech is the slow part. |
| **While it runs** | On a GPU: the least busy card and some CPU; voice keeps working. On the CPU: most of the processor, so voice answers more slowly until the word is done. | |

The card refuses with the reason, instead of offering a button that can't
work, when:

- **internet access is turned off** (Settings → Privacy → Internet → No): turning
  it on downloads about 22 GB, once;
- **too little disk** is free (the need above plus 5 GB). On the Windows
  install Domovoi can't see the Windows drive, so the card shows no free
  space there and the confirm dialog asks you to tick that C: has at least
  40 GB free;
- **the server has less than 24 GB of memory** (on the Windows install, the
  card says it in the PC's terms: a PC with about 32 GB or more);
- **Docker is not installed or not running**.

On **Turn on wake-word training** a confirm dialog repeats the needs. A
server with no GPU that training can use asks once more: **Train wake words
on the CPU?** with the estimate and "voice answers more slowly". On yes the
core runs one background job, as its own service user:

1. **Build the trainer.** `docker build` of `scripts/wake_word/docker`,
   labelled `org.domovoi.wake-trainer.src=<git tree id of that folder>`.
   About 8 minutes the first time with the base image download, about 3
   with the base image already there.
2. **Check the GPU**, only when the server has an NVIDIA card (an AMD or
   Intel graphics chip is not used; such a server trains on the CPU): a short
   torch check in the image with `--gpus all`. When Docker can't reach the
   card, the job stops and says so, with the CPU estimate and a **Use the
   CPU** button, instead of quietly training on the CPU. On a Linux server
   and in the Windows install, what gives Docker the card is the NVIDIA
   Container Toolkit, which Domovoi's provisioning and update unit set up
   ([LINUX_HOST.md](../../docs/LINUX_HOST.md#wake-word-training),
   [WINDOWS_HOST.md](../../docs/WINDOWS_HOST.md#training-wake-words)).
3. **Download the training data** into the Docker volume
   `domovoi_oww_cache` (the image's `--warm` mode; the progress bar counts
   the gigabytes), so the first Train doesn't start with it.
4. **Switch training on.** `WAKE_WORD_TRAINER_ENABLED=true` and
   `WAKE_WORD_TRAIN_COMMAND` (this server's Python running
   `scripts/wake_word/train_wake_word.py`) go into `domovoi/.env` through
   the repo's `.env` writer and onto the running settings. The trainer
   starts without a restart.

The whole job took 12.8 minutes in the 2026-10-13 test install (the build,
with the base image download and the GPU check, 6.4 minutes; the data about
6). The page can be left; the job carries on. Its progress, and a failure with
the last lines it printed, are kept in `~/.domovoi/wake_trainer/setup.json`,
so a reload, another browser or a restart still shows them. A core restart
in the middle of the job reads "Interrupted: Domovoi restarted while
training was being set up. Turn it on again to finish." The next run
reuses what was done (Docker's build cache, the data already downloaded).

- **Try again** runs the job again after a failure.
- **Use the CPU** appears when the GPU check failed.
- **Check the GPU and trainer again** (once training is on) runs the job
  again: a quick rebuild when nothing changed, the GPU check, the data
  check. Use it after the NVIDIA Container Toolkit arrives. On a box whose
  NVIDIA card Docker can't use yet, it (and **Turn on**) also asks the next
  update or Restart to set the toolkit up at once, even within a day of a
  failed try.
- **Turn off and free the space** (once training is on) shows what the
  trainer image and its data take, then switches training off and removes
  them (the image, Docker's build cache for it, the `domovoi_oww_cache`
  volume with every word's training files). Trained wake words and their
  recordings stay; turning training on again downloads it all again.
- Answering **No** in Settings → Privacy → Internet while the job builds or
  downloads stops it ("Stopped: internet access was turned off"), and stops
  a rebuild an update left running too.

On a Linux server or the Windows install, an update that changes
`scripts/wake_word/docker` rebuilds the image by itself
([LINUX_HOST.md, Updates from the dashboard](../../docs/LINUX_HOST.md#updates-from-the-dashboard)).

## 2. Train a wake word

1. **Create a wake word** (Settings → Voice → Wake words): a name and the phrase,
   e.g. "Hey Domovoi".
2. **Record clips on a satellite**, through the board that satellite
   listens with. Training needs at least 15 selected clips. For an
   XVF3800, record hundreds: its on-chip beamforming, gain control and
   noise suppression reshape the sound, and the model has to learn that.
   Vary distance (arm's length, a metre, across the room), tone (calm,
   raised, half-asked) and background (quiet, normal household noise).
   Use the clip-quality scores to deselect bad takes.
3. **Train.** The button is greyed with "turn on training above first"
   until section 1 is done. On a CPU-only server it asks first, every
   time: "about N minutes, and voice answers more slowly until it
   finishes". About 20 minutes on the GPU.
4. **Push to a room.** The satellite downloads the model and wakes on it.

Three wake words ("Hello Domo", "Hey Domovoi", "Domovoi") are three
models. A satellite runs one at a time: each room wakes on the one pushed
to it. The recipe trains the parts of a phrase as near misses, so a "Hey
Domovoi" model is meant not to wake on "Domovoi" alone; train "Domovoi" as
its own word for that.

A word that fails shows the end of the trainer's output on its row. The
whole output of the last run of each word is in
`~/.domovoi/wake_trainer/logs/<slug>.log` on the server. A failed word
trains again on the clips it has: press **Train** on it again (or **Record**
a new take first, which replaces its clips). A word whose training was cut off
by a restart is not failed: it stays queued and trains again once the core
is back.

When a word trains again on a different set of clips (a new take, or the
word deleted and created again under the same name), the trainer
recomputes its features from them; the synthetic clips it generated before
are kept and reused.

## 3. Settings

**Settings → System → Advanced → Wake words** (admin only).
Each one applies to the next training; none needs a restart.

| Setting (`.env` key) | Default | What it does |
|---|---|---|
| Wake-word training (`WAKE_WORD_TRAINER_ENABLED`) | off | Train queued words. Applies at once. Turned on here without section 1's setup, every training fails with "image not found": use the card. Off: no new training starts. |
| Training time limit (`WAKE_WORD_TRAIN_TIMEOUT_SEC`) | 0 | 0 sizes it to the server: 3600 s on a GPU; on the CPU twice the estimate, at least 7200 s. A word that runs past it is stopped and failed, the message naming this setting. |
| Synthetic clips per wake word (`WAKE_TRAINER_N_SAMPLES`) | 0 = 5000 | 20000 or more cuts near-miss false wakes ("hey dominic" for "hey domovoi") and adds about 15 minutes on a GPU, much more on the CPU: raise the time limit with it. |
| Synthetic validation clips (`WAKE_TRAINER_N_SAMPLES_VAL`) | 0 = 1000 | Held back to check the model while it trains. |
| Training steps (`WAKE_TRAINER_STEPS`) | 0 = 50000 | Fewer is faster and usually worse. |
| Share of recorded clips (`WAKE_TRAINER_REAL_TARGET_FRACTION`) | 0 = 0.2 | How much of the positive set is copies of the recorded clips (each copy gets different room echo and noise). More only helps with many varied recordings. |
| CPUs for training (`WAKE_TRAINER_CPUS`) | 0 | 0 = all but a quarter of the Docker host's CPUs, with at least two left for the voice pipeline. |
| Memory for training (`WAKE_TRAINER_MEMORY_GB`) | 12 | The container's memory cap, no swap on top. A run that needs more stops with an out-of-memory error instead of slowing the server down. |

**Not in the dashboard, on purpose:** `WAKE_WORD_TRAIN_COMMAND`,
`WAKE_TRAINER_IMAGE`, `WAKE_TRAINER_CACHE_VOLUME` and `WAKE_TRAINER_DOCKER`.
What they name (a program, a Docker image, the docker binary) is run by the
core as its service user, so letting an HTTP request write them would be
code execution. They are server configuration: `domovoi/.env`, or the
core's process environment. The card writes the command itself, always the
repo's own wrapper.

**How the knobs reach the trainer.** The core runs the command with an
environment built from the settings above plus every other
`WAKE_TRAINER_*` line in `domovoi/.env` (`WAKE_TRAINER_CPUSET`,
`WAKE_TRAINER_SHM_SIZE`, ...). A variable in the core's own process
environment wins over the `.env` line, as it does for every setting. A
systemd drop-in is no longer needed. Every knob:
[DOCKER_TRAINER.md, Env knobs](DOCKER_TRAINER.md#env-knobs-host-wrapper--container).

## 4. What a training does to the server

- **One at a time.** Words train one after another, never two at once,
  and nothing trains while training is off or while the card's job runs.
  The container has a fixed name, `domovoi-wake-train`, and the label
  `org.domovoi.wake-train=<slug>`; any container with that label still
  there when a run starts is left over from a run that was cut off, and is
  removed first.
- **Nothing left running.** At the time limit the wrapper removes its own
  container a minute before the core gives up (exit code 124). When the
  core stops (systemd stopping `domovoi-core`, a Restart, an update) the
  wrapper is told to stop and removes the container (exit code 128 plus
  the signal); the word stays queued and trains again after the restart.
  The core also asks the command to stop, kills it after 30 s, and runs
  `train_wake_word.py --stop`, which removes every training container. At
  its first check after a start it does that once more, for a container a
  crashed core left behind. A word that ran out of time is failed, the
  message naming the time limit.
- **Stop training.** A word that is training has a **Stop training**
  button: the run stops (its container, and the copies of the recordings
  with it) and the word goes back to *recording* with its clips, not queued
  again.
- **The GPU it was set up for.** Once the setup found the GPU, a training
  whose GPU check fails (the toolkit lost, the card gone) is refused with
  the reason instead of running for over an hour on the CPU under the GPU's
  time limit.
- **No recordings left behind.** The copies of the recorded clips a run
  puts in the cache volume go at its end however it ends: finished,
  failed, stopped, timed out or cut off by a restart (the container gets a
  stop signal and a 10 s grace first). A run killed outright is scrubbed by
  the next stop, run or start. Deleting a wake word removes its training
  files from the volume too. It also deletes the word's model and its
  recorded clips from the server.
- **Live voice keeps priority.** The container gets `--cpus` (all but a
  quarter of the CPUs, at least two left), `--cpu-shares 128` (voice wins
  every contended slice), `--memory 12g` with no swap (never more than the
  Docker host minus 6 GB) and, when set, `WAKE_TRAINER_CPUSET`. Inside, the
  trainer lowers its own priority (nice 10, the idle I/O class).
- **The GPU it uses** is the least busy card, so generating the synthetic
  speech (which fills a 16 GB card) stays off the card holding the chat
  model. Under WSL the card is chosen by its UUID (`CUDA_VISIBLE_DEVICES`),
  because `--gpus device=N` does not isolate a card there.
- **How the words are said.** The core sends the phrase's pronunciations
  (`WAKE_PRONUNCIATIONS`, ARPAbet) from Domovoi's own phonemizer
  (`domovoi/phonemizer/`, MIT): the "say it like" list first (Settings →
  Voice → Voices → Say it like; the shipped list says Domovoi = DOH-moe-voy), and
  its own guess for a word its dictionary lacks. The synthetic clips are
  spoken from the same pronunciations (`WAKE_SYNTH_IPA`, IPA): "Domovoi"
  as DOH-moe-voy, not espeak-ng's DOM-oo-voy, for every word on the list.
  Change a word's "say it like" entry before training to change how the
  synthetic clips say it. The trainer also adds near misses of the phrase
  as negatives ("hey dome", "hey domino", "hello demo"), worked out from
  the dictionary. espeak-ng is GPL and lives only inside the trainer image,
  as a separate program; it pronounces only the words the core sent
  nothing for.

## 5. Licence: a model trained here is for this household

A model trained at home is for that household. It can't become one of
Domovoi's shipped defaults, and it shouldn't be shared, because the
training data is not licensed for that:

- the negative features the training uses (ACAV100M, from
  `davidscripka/openwakeword_features`) are CC BY-NC-SA 4.0
  (non-commercial, share-alike);
- the background clips (FMA and FSD50K samples) include clips under
  non-commercial licences;
- the validation set includes MUSDB18, licensed for educational use only;
- the MIT room impulse responses state no licence at all.

openWakeWord's own pre-trained models are CC BY-NC-SA 4.0 for the same
reason. Domovoi's code stays MIT, and Domovoi bundles nothing GPL or LGPL.
The trainer image does hold some: espeak-ng (GPL-3.0), the Linux
distribution's own ffmpeg package (Ubuntu's in the default image, Debian's
with a `python:3.11-slim` base; both are built with their GPL parts
turned on), the mutagen library (GPL-2.0-or-later) and libsndfile
(LGPL-2.1-or-later). They live only inside that image, which your own
server builds from the Dockerfile in `scripts/wake_word/docker`; Domovoi
doesn't distribute the image. The NVIDIA Container Toolkit is an
Apache-2.0 operating-system package installed from NVIDIA's repository.
A default Domovoi could ship would need
training data with permissive licences throughout.

## 6. When the model isn't good enough

- **No detections at all**: the satellite's wake word must match the model
  file's stem (`hey_domovoi.onnx` → `hey_domovoi`); openWakeWord keys its
  predictions on it. A push sets it.
- **Wakes on "okay", "thanks", or names that sound alike**: too few clips,
  or too few synthetic samples. Raise **Synthetic clips per wake word**
  (20000+) and the time limit, and train again with a larger, more varied
  take. A trained word can't take a new recording: delete it and create it
  again (or create a second word beside it), then record and train.
- **Quieter than `hey_jarvis`**: usually the threshold. Lower it to 0.4 for
  that word.
- **Works in testing, not on the satellite**: record the clips on that
  satellite's own board (HAT or XVF3800) and train again.
- **Score the recorded clips** against the trained model:
  `python scripts/wake_word/score_clips.py <slug>` from the repo root, or
  the score button on the word's row in Settings → Voice → Wake words.

More: [docs/TROUBLESHOOTING.md, Wake-word training](../../docs/TROUBLESHOOTING.md#wake-word-training).

## 7. By hand (development PCs)

### The command

The trainer worker never trains inside the core. It runs
`WAKE_WORD_TRAIN_COMMAND` with four placeholders filled in, and marks the
word `ready` only when the command exits 0 **and** `<slug>.onnx` is at
`{out}`. The card writes the repo's wrapper; by hand, on a development PC
with Docker Desktop, the same thing looks like this in `domovoi/.env`:

```
WAKE_WORD_TRAINER_ENABLED=true
WAKE_WORD_TRAIN_COMMAND=python "C:\path\to\domovoi\scripts\wake_word\train_wake_word.py" --clips "{clips_dir}" --phrase "{phrase}" --slug "{slug}" --out "{out}"
```

| Placeholder | Value |
|---|---|
| `{clips_dir}` | the selected recorded clips (`<wake_clips_dir>/<slug>/.training/`; the word's own folder when none is selected) |
| `{phrase}` | the phrase (e.g. `hey domovoi`) |
| `{slug}` | the model stem; `{out}` must be `<slug>.onnx` |
| `{out}` | the absolute path of the model the worker then registers |

**Double-quote every placeholder and path.** The template is split into
arguments first (`shlex`, keeping backslashes on Windows) and each value is
put inside one argument, so a phrase or path with spaces stays one
argument either way; the quotes keep the template itself splitting the
same way on every platform. `python` must be the core's own interpreter
(the command runs with no shell, on the core's PATH). The time limit is
the setting above, and the `WAKE_TRAINER_*` knobs reach the wrapper from
`domovoi/.env` or Settings → System → Advanced. Build the image first
([DOCKER_TRAINER.md](DOCKER_TRAINER.md#build-or-pull)), or the first
training fails with "image not found".

openWakeWord's own `automatic_model_training.ipynb` notebook is not a
route any more: it fails on any word its dictionary lacks ("domovoi",
"domo"), because the model it downloads for those is no longer served
([DOCKER_TRAINER.md](DOCKER_TRAINER.md#build-internals--the-dependency-set--upstream-patches),
item 6).

### Recording clips without a satellite

```bash
python scripts/record_wake_word_clips.py \
    --count 30 \
    --out ~/.domovoi/wake_clips/hey_domovoi \
    --phrase "Hey Domovoi"
```

It counts down for each clip, records about 2 seconds, and reports a peak
level; `peak < 2000` means the gain or distance is too low (delete the
file and run it again; it resumes). Clips recorded on a PC microphone
don't match what a satellite hears, so prefer recording on the satellite.

### Testing a model at a microphone

```python
import time
import numpy as np
import sounddevice as sd
from openwakeword.model import Model

# openWakeWord's predict() needs chunks that are multiples of 80 ms
# (1280 samples at 16 kHz); smaller chunks silently score zero. Collect
# mic frames and feed predict() one full chunk at a time.
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

(`pip install openwakeword onnxruntime numpy sounddevice` first.) Aim for
clean detections at conversational distance and no false wakes during TV
or music.

### Putting a model on a satellite by hand

The dashboard's **Push to a room** is the normal way. By hand:

```bash
scp hey_domovoi.onnx domovoi-kitchen.local:~/.domovoi/
ssh domovoi-kitchen.local
nano ~/.domovoi/config.toml
# [wake]
#   wake_word = "hey_domovoi"
#   wake_word_model_path = "~/.domovoi/hey_domovoi.onnx"
sudo systemctl restart domovoi-satellite
```
