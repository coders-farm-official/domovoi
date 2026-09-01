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
| `whisper_compute_type` | `float16` | **`int8`** |
| `whisper_model` | `large-v3` | **`small.en`**, or `medium` if you need the accuracy |
| `ollama_tool_model` | `qwen2.5:14b` | **`qwen2.5:7b`** |
| `ollama_model` | `llama3.2:3b` | `llama3.2:3b` — unchanged, already small |
| `tts_engine` | `piper` | `piper` — unchanged, but read the note below |
| Chat mode (Letta) | opt-in | **leave off** |

All of these are dashboard settings — gear → **Advanced** for the Whisper
three, **Models** for the Ollama three. The Whisper settings are
restart-tier (the model loads once at boot). The Ollama model settings are
hot — they take effect on the next turn with no restart, which makes them
cheap to experiment with. Try a model, say something, try another.

---

## Why these values

### `whisper_compute_type = int8`, not `float16`

`float16` is a GPU compute type. CTranslate2 (the engine under
faster-whisper) will either fall back or crawl if you ask a CPU for it.
`int8` is the CPU quantization, and on any recent AMD or Intel core with
AVX2 — AVX-512 better still — it's genuinely fast.

**This is the single most common CPU-host misconfiguration.** Setting
`whisper_device = cpu` and leaving `compute_type` at `float16` produces a
system that technically works and feels broken.

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

### `qwen2.5:7b` instead of `qwen2.5:14b`

The 14B tool-router is chosen for schema adherence — it reliably emits the
structured tool calls the router wants. On CPU it's roughly 9 GB resident
and slow enough to be felt on every routed turn.

`qwen2.5:7b` is about 4.7 GB and roughly twice the throughput. Its schema
adherence is weaker but generally acceptable. Because the setting is hot,
run both for a day and see whether routing actually degrades for the
things your household says.

If you notice the router picking wrong handlers, that's your signal to go
back to 14B and accept the latency — or to add a fast path (see below).

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

**Per-turn latency is not logged.** It goes to the database — one row per
routed turn in `intents_log`, where `latency_ms` covers the whole turn
(STT → routing → handler), not STT in isolation:

```bash
docker exec -i domovoi-postgres psql -U domovoi domovoi -c "SELECT at, room_id, matched_handler, matched_path, latency_ms, transcript FROM intents_log ORDER BY at DESC LIMIT 10;"
```

`matched_path` is the useful column next to it: it tells you whether a
turn took a regex fast path or went through the language models. Compare
the two and you can see exactly what the LLM costs on your hardware —
which is the number that should drive your model choices, not the table
above.

---

## Memory budget

Ollama holds models resident between turns (`keep_alive`). On a 24 GB
machine, a rough accounting:

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
