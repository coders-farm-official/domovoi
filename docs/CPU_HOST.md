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
| `identify_ms` | voice identification (which household member spoke) |
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
were started.

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

That leaves real headroom, and a headless Linux host hands you several GB
more of it than Windows does. Swap in `qwen2.5:14b` (~9 GB) and it gets tight
once you have several rooms, each with its own MPD container — another
argument for the 7B.

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
