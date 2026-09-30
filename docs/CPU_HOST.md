# Running Domovoi without an NVIDIA GPU

The default configuration assumes a machine with an NVIDIA GPU: Whisper
runs on CUDA at `float16`, and Ollama keeps a 14B tool-routing model in
VRAM. Plenty of good server hardware isn't shaped like that — AMD mini
PCs, Intel NUCs, older desktops, anything with an integrated GPU.

Domovoi runs on those. It just needs different settings, and you should
know in advance which parts get slower and which don't change at all.

Related: [FAQ — Can it run without a GPU?](FAQ.md#can-it-run-without-a-gpu) ·
[Setup runbook](SETUP_RUNBOOK.md) ·
[Running the server on Linux](LINUX_HOST.md) ·
[Troubleshooting](TROUBLESHOOTING.md)

---

## The short version

| Setting | Default (NVIDIA) | CPU host |
|---|---|---|
| `whisper_device` | `cuda` | **`cpu`** |
| `whisper_compute_type` | `auto` (runs `float16`) | `auto` (runs **`int8`**) — or `int8` explicitly |
| `whisper_model` | `large-v3` | **`small.en`**, or `medium` if you need the accuracy |
| `whisper_cpu_threads` | `0` (one per physical core; ignored on `cuda`) | `0` — unchanged |
| `ollama_tool_model` | `qwen2.5:14b` | **`qwen2.5:7b`** |
| `ollama_model` | `llama3.2:3b` | `llama3.2:3b` — unchanged, already small |
| `tts_engine` | `piper` | `piper` — unchanged, but read the note below |
| Chat mode (Letta) | opt-in | **leave off** |

All of these are dashboard settings — gear → **Advanced** for the Whisper
ones, **Models** for the Ollama three. The Whisper settings are
restart-tier (the model loads once at boot). The Ollama model settings are
hot — they take effect on the next turn with no restart, which makes them
cheap to experiment with. Try a model, say something, try another.

**Forgot to change them?** The core still starts. When the configured
Whisper can't load — `cuda` on a machine with no NVIDIA GPU is the usual
reason — it retries with `whisper_cpu_fallback_model` (default `small.en`)
on `cpu` at `int8`, which is this page's recommended setup anyway, and the
dashboard's **Models** page shows a banner saying so. When no CUDA device
is visible at all it goes straight to the fallback, without first
downloading the configured `cuda` model. If even that fails,
the core runs without speech recognition (satellites answer "I can't
understand speech right now") and the banner shows the load error. Either
way the first-run setup code is written first, so you can always sign in
and fix the settings.

---

## Why these values

### `whisper_compute_type = int8`, not `float16`

`float16` is a GPU compute type. CTranslate2 (the engine under
faster-whisper) refuses it on a CPU. `int8` is the CPU quantization, and on
any recent AMD or Intel core with AVX2 — AVX-512 better still — it's
genuinely fast.

The default, `auto`, follows the device: `int8` on `cpu`, `float16` on
`cuda`. So setting `whisper_device = cpu` is enough. The dashboard also
refuses `cpu` together with a GPU type, and saving `cpu` over a saved
`float16` resets the compute type to `auto` (the save says so). Only a
hand-edited `.env` can still pair them, and then startup falls back as
described above.

### A smaller Whisper model

STT sits in the path of *every single turn*. It is the latency you feel
most, so buy it down first.

- **`small.en`** (~0.5 GB) — start here on an English-only household. On
  8 modern cores it transcribes a typical 2–4 second command in well under
  a second.
- **`medium`** (~0.8 GB) — noticeably better on accented speech, mumbling,
  and mic-at-the-other-end-of-the-room audio. Costs roughly 2–3× the time.
- **`large-v3`** — not viable on CPU. Multiple seconds per utterance.

Drop the `.en` suffix if anyone in the house talks to Domovoi in another
language; the English-only models are meaningfully faster but will
mistranscribe rather than switch.

> These are ballparks, not promises — core count, memory bandwidth, and
> what else the box is doing all move them. Measure yours rather than
> trusting the table: see [Measuring turn latency](#measuring-turn-latency)
> below.

### `whisper_cpu_threads = 0`: every core

faster-whisper on its own runs 4 CPU threads, whatever the machine has.
On an 8-core server that leaves half the cores idle while you wait for
every transcript, and the encoder — most of a transcription's time — is
exactly the work that spreads across cores. `whisper_cpu_threads` (gear →
**Advanced** → Speech-to-text, restart tier) defaults to `0`, which means
one thread per **physical** core; hyperthreads add little to dense matrix
work. Set a number to pin it, for example to leave cores free for Ollama
while it answers. A non-zero value also overrides `OMP_NUM_THREADS`. The
setting is ignored on `cuda`. The boot log says what was used:

```bash
journalctl -u domovoi-core | grep "loading Whisper"
# loading Whisper model=small.en device=cpu compute=int8 cpu_threads=8
```

### `qwen2.5:7b` instead of `qwen2.5:14b`

The 14B tool-router is chosen for schema adherence — it reliably emits the
structured tool calls the router wants. On CPU it's roughly 9 GB resident
and slow enough to be felt on every routed turn.

`qwen2.5:7b` is about 4.7 GB and roughly twice the throughput. Its schema
adherence is weaker but generally acceptable. Because the setting is hot,
run both for a day and see whether routing actually degrades for the
things your household says.

If you notice the router picking wrong handlers, measure before you
guess: `scripts/eval_routing.py` replays a fixed corpus of commands and
questions and reports which handler each one landed on —

```bash
python scripts/eval_routing.py --core http://localhost:6370 --room bench
```

or, to A/B tool models without touching the running core,
`--ollama http://localhost:11434 --model qwen3:8b`. The classic CPU-host
failure is a small router treating a plain question as a tool call ("who
painted the mona lisa" → double_check, → calculator, → news, depending
on the model family); the corpus's `qa` cases catch exactly that. On the
reference all-CPU AMD box, `qwen3:8b` (with thinking off, below) has been
the better router of the two small models — run the corpus on yours
rather than taking that on faith. Your remaining options are the same
as always: go back to 14B and accept the latency, or add a fast path
(see below).

### Thinking models: keep thinking off

Most current models have a **thinking mode** — they emit reasoning tokens
before answering. That's usually an upgrade, and on a CPU host it is
exactly the wrong trade for *this* call: routing sits in the latency path
of every non-fast-path turn, so a router that reasons first adds that cost
to every single command.

`ollama_tool_think` (dashboard → **Models**) controls it, and it defaults
to **off**. Leave it off here. It's a hot setting, so you can A/B it in
seconds if you're curious whether a given model routes better with it.

This is what makes newer tool models usable on a CPU box at all — without
it, swapping in a thinking-capable router makes every command feel slow
and the model gets blamed for it.

The flag degrades safely in both directions: Domovoi omits it entirely
when the installed `ollama` client predates the kwarg, and if a server
rejects it for a model with no thinking mode, the turn is retried once
without it and the flag latches off for the process. Neither case costs
you a failed route.

The Q&A model has its own switch, `ollama_qa_think` (dashboard →
**Models** → *Q&A model thinks first*). Its default, `default`, sends no
flag at all, which is how Q&A calls have always gone out, so a hybrid
model such as `qwen3` reasons before every answer. If your Q&A model has a
thinking mode, set it to `false` here. It degrades the same way as the
router's flag. It covers the voice pipeline's Q&A calls; the dashboard's
text chat still sends no `think` flag.

### Context window

Domovoi sends no `num_ctx` unless you set one, so every call gets the
Ollama server's default window: 4096 tokens on most hosts, or whatever
`OLLAMA_CONTEXT_LENGTH` says in Ollama's unit. The routing prompt alone is
roughly 3.4k tokens before plugins and grows with every plugin, so a
router that starts missing commands as plugins are added may be running
out of room. `ollama_tool_num_ctx` (the router) and `ollama_num_ctx` (Q&A
and the dashboard's text chat) set it per role, under dashboard →
**Models**; 8192 is a sensible first step. Voice picks a change up at
once; the text chat runs in the dashboard's process and picks it up when
the dashboard restarts. A blank value or 0 sends nothing. A bigger window
costs memory for as long as the model stays loaded, and Ollama reloads a
model whenever its window changes, so if one model serves both roles,
give both the same value.

### Leave chat mode off

Open-mic conversational mode routes every turn through Letta and a 14B
model. It's opt-in behind a Docker Compose profile precisely so a
deployment that doesn't want it never runs it. On a CPU host, don't.

Command mode never touches Letta.

---

## The thing that saves you

Most of what a household actually says never reaches a language model at
all.

Handlers declare regex **fast paths** (`domovoi/handlers/base.py`). When
an utterance matches one, it dispatches directly — no LLM, no routing
model, no network. Timers, clock, calculator, music transport, volume,
reminders, intercom, and drop-in all work this way, and all declare
`requires_network = "no"`.

So on a CPU host:

- *"Set a timer for ten minutes"* → STT + regex. Fast. Feels the same as
  it would on a 4090.
- *"Play the Beatles in the kitchen"* → STT + regex. Fast.
- *"What's the capital of Mongolia?"* → STT + tool router + Q&A model.
  This is where you wait.

The system doesn't feel uniformly slower. It feels instant for the
hundred things you say daily and thoughtful for the ones you say
occasionally. That is a much better trade than the raw numbers suggest,
and it's worth setting expectations with the household on exactly this
split.

### TTS: the one place local-first costs you something

`tts_engine` defaults to `piper`, which renders every spoken response
locally — on this box, that means on the same CPU already doing Whisper
and the language models. Edge, by contrast, arrives over the network
already rendered and costs zero local compute.

So this is the one setting where the local-first default and the
CPU-host advice genuinely pull in opposite directions, and it's worth
being clear-eyed rather than pretending otherwise.

**Start on Piper anyway.** It's fast — a `medium` voice renders a
typical one- or two-sentence reply in well under a second on eight modern
cores — and it runs after the answer is already decided, so it lands at
the tail of the turn rather than in the middle of it. For most households
it simply isn't the bottleneck.

Reach for `edge` if you measure otherwise, or if you want the nicer
voices badly enough. Just do it knowingly: it sends **the text of every
spoken response** to Microsoft, which is the single thing the default
config is built to avoid. See
[SECURITY_PRIVACY.md](SECURITY_PRIVACY.md).

Piper's voice model downloads from Hugging Face once, on first render.
After that it is fully offline — which also makes it the rung that keeps
working when your internet doesn't.

### Measuring turn latency

Two different numbers, often confused:

**Model load** is logged, and is boot cost only:

```bash
journalctl -u domovoi-core | grep -iE "loading Whisper|Whisper ready"
```

The gap between those lines is how long Whisper took to load. The *first*
one also downloads the model from Hugging Face, so restart once and time
the second for a true figure.

Ollama's model loads are the exception: they are boot cost only if the
models *stay* loaded, which is what [Memory budget](#memory-budget) is
about. A first question that takes most of a minute after a quiet
afternoon is that, not STT.

**Per-turn latency, stage by stage.** Every spoken turn logs one line,
with no transcript in it:

```bash
journalctl -u domovoi-core | grep 'turn timings'
# turn timings room=kitchen trigger=wake_word path=fast capture_audio_ms=2130
#   endpoint_silence_ms=1200 stt_ms=640 stt_wait_ms=0 identify_ms=41 route_ms=22
#   tts_first_ms=95 total_ms=190 speech_to_reply_ms=1390 stt=reused/1spec
#   whisper=small.en/cpu/int8/8t
```

| Stage | What it measures |
|---|---|
| `capture_audio_ms` | how much audio the satellite sent (the length of the capture) |
| `endpoint_silence_ms` | the silence the satellite waited out after your last word before it stopped listening — `listen.silence_timeout`, give or take a frame |
| `stt_ms` | the Whisper call whose transcript the turn used — the number this page is mostly about |
| `stt_wait_ms` | how long the turn actually waited for that transcript after the satellite stopped listening (see below) |
| `identify_ms` | voice identification (which household member spoke); with a reused speculative transcript (one at least `voice_profile_min_utterance_sec` long) the voice embedding already ran alongside that decode, so this is only the lookup |
| `route_ms` | the routing transaction: fast path or language model, the handler, the audit writes |
| `tts_first_ms` | the first sentence of the reply, synthesized and on its way to the satellite |
| `total_ms` | end of speech (the satellite's `utterance_end`) to the first reply audio |
| `speech_to_reply_ms` | your last word to the first reply audio: `endpoint_silence_ms + total_ms`. The number you feel. |

`total_ms` starts when the satellite decides you have stopped talking,
which is `listen.silence_timeout` (1.2 s by default) after your last word.
`endpoint_silence_ms` is that wait, when the core can know it: satellites
from this release report their last voiced frame, and for an older one it
is worked out from the timeout it reported on connect; otherwise the two
stages that need it are missing from the line.

**Speculative transcription** (`speculative_stt_enabled`, on by default)
is why `stt_wait_ms` is usually far below `stt_ms`. The satellite streams
its audio as you speak, so at the first quarter-second pause the core
starts Whisper on what it has, while the satellite is still counting its
silence; at the end it uses that transcript only when nothing was said
after it (it counts frames — it never guesses). `stt=reused/1spec` on the
log line means it was used; `stt=full/…` means speech came after the copy
and the whole capture was transcribed after all. On this hardware that
takes the Whisper time out of every turn's wait up to the silence timeout:
with a 0.6-1.0 s decode and the 1.2 s default, the transcript is usually
ready when the satellite stops listening. Each pause somebody talks past
costs one extra decode of CPU (at most three per utterance); the summary's
`speculative` block counts how often the early transcript was used.

**Early commit** (`early_commit_enabled`, on by default; satellites from
this release only) goes one step further for simple commands: when the
early transcript is a whole closed command, the core stops the satellite
listening without waiting out its silence timeout and answers. The core
logs each one:

```bash
journalctl -u domovoi-core | grep 'early commit'
# early commit room=kitchen trigger=wake_word tier=A hold_ms=350 silence_ms=840 frames=71
```

`silence_ms` is how long after your last word it stopped listening, which
becomes the turn's `endpoint_silence_ms`; the turn's log line carries
`early_commit=A/350ms`. Tier A (closed phrases like "pause the music",
"volume up", "what's playing") needs 350 ms of silence, tier B (a timer or
reminder with a duration, "volume 40", the clock, one-word commands) 650
ms (`early_commit_hold_a_ms`, `early_commit_hold_b_ms`). On a CPU-only
server the decode itself usually takes longer than either hold, so in
practice the capture ends when the transcript is ready: about
`240 ms + stt_ms` after your last word instead of `silence_timeout +
stt_ms` before this release. For "set a timer for ten minutes" on an
8-core server with small.en that is roughly 0.8-1.2 s to the end of
listening plus 0.2-0.4 s to the first reply audio (`speech_to_reply_ms`);
getting under a second needs a faster decode (base.en, a GPU, or a
dedicated fast recognizer for simple commands).

Whatever is said after the hold is lost ("set a timer for ten minutes …
for the pasta" gets no label). When the satellite heard speech after the
core stopped listening, the core logs `early commit … cut in on speech`
and records `post_commit_voiced_ms` on the turn; the summary's
`early_commit` block counts them (`cut_in`). If a room sees those, turn
the room's **Stop listening early on a whole command** off (satellite
Listening settings), or `early_commit_tier_b` off for the whole house.

The same stages are stored on each turn's `intents_log` row
(`timings`, a JSON column; migration V015), and the dashboard's **Models**
page shows the recent medians under speech-to-text. For the whole
breakdown, per stage p50 / p95 / max:

```bash
curl -s http://localhost:6370/v1/stats/latency | python3 -m json.tool
curl -s "http://localhost:6370/v1/stats/latency?since=2026-09-28T18:00:00Z&room=kitchen"
```

Pass `since` as the time of your last restart when you are comparing
Whisper settings, so the numbers are all from the settings now running
(`whisper_seen` in the answer lists the settings the window covers).
`speculative` in the answer is `{turns, reused, decodes}`: turns that had
an early transcript, how many used it, and how many speculative decodes
were started; `early_commit` is `{turns, A, B, cut_in}` for the captures
the core ended early.

`intents_log.latency_ms` is **not** the whole turn. It is the router's
share only: its clock starts after speech-to-text has finished and stops
before any reply audio exists. It is still the useful number next to
`matched_path`, which tells you whether a turn took a regex fast path or
went through the language models:

```bash
docker exec -i domovoi-postgres psql -U domovoi domovoi -c "SELECT at, room_id, matched_path, latency_ms, timings FROM intents_log ORDER BY at DESC LIMIT 10;"
```

Compare the two paths and you can see exactly what the LLM costs on your
hardware — which is the number that should drive your model choices, not
the table above.

### The streaming fast lane

Every command still waits for the satellite's `listen.silence_timeout`
(1.2 s) and then for one Whisper decode, however simple it was. The fast
lane is a second, much smaller recognizer that follows each capture while
you are still talking (sherpa-onnx running a streaming NeMo FastConformer,
`domovoi/fast_lane.py`). When what it has heard so far is a complete
simple command, such as "pause the music", "volume up" or "set a timer
for ten minutes", and the room has then been quiet for a moment, it knows
the command well before the satellite stops listening.

For now it only runs in **shadow mode**: it writes down what it would
have done and whether Whisper agreed, and every reply is exactly what it
would have been without it. Those records decide whether it may ever act.

Turn it on:

```bash
pip install -e ".[fastlane]"              # sherpa-onnx; not part of any other extra
python -m domovoi.fast_lane fetch         # optional: the 103 MB model now
```

then `FASTLANE_MODE=shadow` in `domovoi/.env` and a restart, or
Settings → Speech-to-text → *Fast lane* → **shadow**, which applies at
once. The model downloads on first enable into
`~/.domovoi/models/fastlane/` from the sherpa-onnx project's GitHub
releases, checked against a pinned SHA-256 (and its files again on every
load). It is NVIDIA NeMo's
`stt_en_fastconformer_hybrid_large_streaming_80ms` (CC-BY-4.0) in
sherpa-onnx's int8 export. Without the extra, shadow mode logs once that
the lane is unavailable and nothing else changes.

What it commits on, and after how much quiet:

| Hold | Commands |
|---|---|
| 350 ms | Closed phrases: pause / resume / stop the music, next / previous song, volume up / down, what's playing, how long is left on the timer, hang up, next / previous chapter, the wifi, server and voice questions, play / shuffle my favorites |
| 650 ms | A number, a duration or a label: set a timer, cancel or stop the timer ("... for the pasta"), set the volume to N, skip N seconds, a calculation; the clock ("what time is it" can become "... in Tokyo"); phrases that can start a question ("what was that ... song", "what are my reminders ... for tomorrow", "how many albums ... does Adele have", "what's this book ... about"); a bare word ("stop", "pause", "next") and "go back", which often start a longer command |
| never | Open slots (`play ...`, `remember ...`, `announce ...`), a reminder's message, a reminder with no task yet ("set a reminder for 10 minutes", which "... to call mom" can follow), plugin commands, anything for the language model |

What you see: one line per turn it would have acted on,

```bash
journalctl -u domovoi-core | grep fastlane
# fastlane would commit music._pause_from_match at +391ms (806 ms before
#   utterance_end, hold 350 ms) in room=kitchen; lane heard 'pause the
#   music'; whisper later said 'Pause the music.'; agree=True
```

plus a "fastlane missed ..." line when Whisper heard a command the lane
could have taken but didn't (or "fastlane had not decided ..." when the
core's own early commit stopped listening first: `fastlane_preempted`, not
a miss). The turn's `intents_log.timings` carries
`fastlane_seen`, `fastlane_ms` (your last voiced frame to the lane's
decision), `fastlane_lead_ms` (how long before the satellite's
`utterance_end` it came), `fastlane_agree`, `fastlane_after_ms` (speech
that came after the decision: a real commit would have cut you off),
`fastlane_cpu_ms`, and the lane's own text and path. The latency summary
counts them without the text:

```bash
curl -s http://localhost:6370/v1/stats/latency | python3 -m json.tool   # the "fastlane" block
docker exec -i domovoi-postgres psql -U domovoi domovoi -c "SELECT at, room_id, transcript, timings->>'fastlane_text' AS lane FROM intents_log WHERE timings->>'fastlane_agree' = 'false' ORDER BY at DESC LIMIT 20;"
```

**Measured, on the dev box** (i9-14900KF, 333 Piper-TTS command clips in
nine voices shaped like a satellite capture; not yet on the Beelink, and
TTS voices are not a household):

| | |
|---|---|
| Last word to the lane's decision, 350 ms hold | p50 0.39 s, p95 0.63 s (real-time, one room); 0.41 / 0.67 s with three rooms talking at once |
| Last word to the lane's decision, 650 ms hold | p50 0.66 s, p95 0.69 s |
| CPU, one worker thread | real-time factor 0.16 (0.13 with 2 threads): about a sixth of one core per room while someone talks, nothing when nobody does; ~0.4 s of CPU per command |
| The rest of the core | sherpa-onnx lets go of Python's lock while it decodes, so the core's event loop keeps running: its longest gap stayed under 1.5 ms with the lane following one or three rooms, apart from a single 10-34 ms gap per run. The lane also stops decoding a room at its `utterance_end`, the moment Whisper starts on that room's audio |
| Memory | +200 MB for the model, +7 MB per room talking; loads in about 1 s |
| Commits right | 181 of 182 on the single commands (the one miss: "what's *by* plus three") |
| Commands caught | 181 of 252 single commands (72%; the 252 include 9 reminders it never takes). The rest were misheard and would simply have waited for Whisper. For scale: Whisper tiny got 211 of the 252 right, large-v3 77 of 84 on three of the voices |
| Mid-command pauses | "set a timer for ten minutes ... for the pasta" with a 700 ms pause committed early in 7 of 9 clips, which is what a 650 ms hold does; "stop ... the timer", "next ... chapter" and "what time is it ... in Tokyo" waited correctly |

So if it were allowed to act, a command it catches would start answering
about 0.6-0.8 s after the last word on the 350 ms hold and 0.9-1.1 s on
the 650 ms one (its decision plus roughly 0.25-0.45 s for voice
identification, routing, the first sentence of speech and the satellite
starting to play), against 1.2 s plus a Whisper decode plus the same
0.25-0.45 s today.
The lane uses one worker thread for every room; `fastlane_cpu_threads`
(default 1) is what it may use.

---

## Memory budget

Ollama only holds a model resident for as long as the request's
`keep_alive` says, and Ollama's own default is **5 minutes**. On a CPU host
that is the difference between a quick answer and a painful one: after a
quiet spell the next question pays a full cold start — reloading the
model and re-prefilling the tool schema for every registered handler
(~3k tokens with a couple of dozen handlers). Measured on an all-CPU AMD
mini PC, same question both times:

| "what is the capital of mongolia" | |
|---|---:|
| Cold — models unloaded | 53.6 s |
| Warm — models resident | 3.7 s |
| Any fast-path turn (regex, no LLM) | 0.02 s either way |

Domovoi therefore sends its own `keep_alive` on every Ollama call.
`ollama_keep_alive` (dashboard → **Models** → *Keep models loaded for*)
controls it and defaults to `24h`. Use `-1` to keep the models loaded
until Ollama itself restarts, or `0` to unload after every reply. It's a
hot setting: the next turn uses the new value. On a box with a GPU that
also does other work, a shorter value hands the VRAM back sooner.
*Keep tool-routing model loaded for* (`ollama_tool_keep_alive`) gives the
router its own value; blank uses the one above. The dashboard's text chat
sends the same value when it talks to one of the two voice models, so a
chat can't hand them back to Ollama's 5-minute default; any other model
(the vision one) unloads on Ollama's schedule.

`keep_alive` keeps a model loaded once it is loaded. Loading it is the
other half: Domovoi loads both voice models itself, in the background, when
the core starts and again after a model, keep-alive or context-window
change (`ollama_warmup`, dashboard → **Models** → *Load models ahead of
time*), router prompt included, so the first question after a restart is a
warm one. When a question does find a model unloaded anyway (Ollama
restarted, or another model pushed it out of memory), the satellite says
"Just a moment, I'm waking up my language model." (`ollama_cold_start_notice`)
and that call gets `ollama_load_timeout_sec` (300 s) instead of the ordinary
`ollama_timeout_sec`, so a slow load never ends in "my language model isn't
answering".

If you'd rather manage this on the Ollama side, blank out
`ollama_keep_alive` so Domovoi sends nothing (a per-request value
overrides the server's default, so the two would otherwise fight) and
set the default in Ollama's systemd unit instead:

```bash
sudo systemctl edit ollama
```

```ini
[Service]
Environment="OLLAMA_KEEP_ALIVE=-1"
```

Either way, the budget below assumes the models stay resident — that is
the point. On a 24 GB machine, a rough accounting:

| | Approx. |
|---|---:|
| The OS — Windows 4–6 GB, headless Linux 1–2 GB | 1–6 GB |
| Containers — Postgres, SearXNG, one MPD per room (add ~1 GB for Docker Desktop itself on Windows) | 1–3 GB |
| Whisper `small.en` int8 | ~1 GB |
| `llama3.2:3b` | ~2.5 GB |
| `qwen2.5:7b` | ~5 GB |
| or `qwen3:8b` (4–8k context) | ~6 GB |

That leaves real headroom, and a headless Linux host hands you several GB
more of it than Windows does. Swap in `qwen2.5:14b` (~9 GB) and it gets tight
once you have several rooms, each with its own MPD container — another
argument for the 7B.

The context window is part of each model's footprint while it is loaded:
at 32k tokens `llama3.2:3b` alone is ~6 GB and `qwen3:8b` ~10 GB, so leave
`ollama_num_ctx` / `ollama_tool_num_ctx` (and the server's
`OLLAMA_CONTEXT_LENGTH`) at 4–8k on a 24 GB box. And a third model — the
dashboard's vision model, or Letta's — needs its own room: loading it is
what makes Ollama unload a voice model whatever `keep_alive` says.

If the box has less than 16 GB, run one Ollama model: point
`ollama_model` and `ollama_tool_model` at the *same* small model and
accept weaker routing.

---

## What about the integrated GPU?

Tempting, and mostly not worth it yet.

- **Whisper / faster-whisper** goes through CTranslate2, which supports
  CUDA and CPU. Not ROCm, not Vulkan, not Intel Arc. Your iGPU cannot run
  Whisper here regardless of how capable it is.
- **Ollama** can use some AMD and Intel integrated graphics via ROCm or
  Vulkan backends, with real caveats: many iGPUs aren't on the supported
  list and need environment-variable overrides, support varies sharply by
  driver and OS, and an iGPU shares the same system RAM anyway — so you
  gain compute, not capacity. Support is meaningfully better on Linux than
  on Windows, if that's your host — see [LINUX_HOST.md](LINUX_HOST.md).

If you want to try it, `ollama_url` is a dashboard setting
(**Connections**), so nothing stops you pointing Domovoi at any Ollama
that works, on this box or another. Just don't make it a prerequisite for
your install going live. Get the system running on CPU first, then
experiment.

---

## Splitting the load across two machines

If there's another machine on the network with a real GPU — a gaming PC,
say — you don't have to move Domovoi onto it. Point the CPU-host install
at its Ollama:

1. On the GPU machine, make Ollama listen beyond localhost
   (`OLLAMA_HOST=0.0.0.0:11434`) and pull the models there.
2. On the Domovoi server: dashboard → **Connections** → `ollama_url` →
   `http://<gpu-machine>:11434`.

Now LLM work runs on the GPU while STT, TTS, satellite WebSockets, and
everything real-time stay on the always-on box. Whisper stays on the
Domovoi server's CPU either way — there's no setting to send STT
elsewhere.

The obvious cost: Domovoi's language answers now depend on a second
machine being awake. Fast-path commands don't, so the house degrades
gracefully rather than going dark.

---

## When to reconsider

Go find a GPU if, after tuning:

- Transcription of normal room-distance speech takes more than ~1.5 s and
  you've already tried `small.en`.
- Routing accuracy on 7B is bad enough that Domovoi does the wrong thing
  often, and 14B is too slow to be the fix.
- You want chat mode, which is not a CPU feature.

Otherwise the CPU host is a legitimate deployment, not a compromise you're
tolerating.
