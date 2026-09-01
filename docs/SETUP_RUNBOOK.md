# Day-one setup runbook

Bringing up a brand-new Domovoi system, in order, from unboxed hardware to
a house you can talk to.

The [README's First install](../README.md#first-install) is the quickstart —
the commands, nothing else. This is the runbook: the same commands with
the ordering, the verification gates, and the decisions that are painful
to change later. If you're setting up your first system, work through this
page and let it send you into the deeper docs at the right moments.

**Budget roughly an evening for the server and the first satellite, then
20–30 minutes per additional room.** The first satellite is where you'll
learn the quirks of your particular mic board; the rest are repetition.

Related: [Satellite hardware](SATELLITE_HARDWARE.md) (parts and tour) ·
[`satellite/PROVISIONING.md`](../satellite/PROVISIONING.md) (the canonical
per-Pi checklist) · [Running without an NVIDIA GPU](CPU_HOST.md) ·
[Running the server on Linux](LINUX_HOST.md) ·
[Troubleshooting](TROUBLESHOOTING.md)

---

## Table of contents

- [Step 0 — Before you touch anything](#step-0--before-you-touch-anything)
- [Step 1 — Server prerequisites](#step-1--server-prerequisites)
- [Step 2 — Bring up the server](#step-2--bring-up-the-server)
- [Step 3 — Claim admin and configure for your hardware](#step-3--claim-admin-and-configure-for-your-hardware)
- [Step 4 — First voice turn](#step-4--first-voice-turn)
- [Step 5 — The first satellite](#step-5--the-first-satellite)
- [Step 6 — Verify the room end to end](#step-6--verify-the-room-end-to-end)
- [Step 7 — The rest of the fleet](#step-7--the-rest-of-the-fleet)
- [Step 8 — Make it yours](#step-8--make-it-yours)
- [Reusing satellite hardware from a previous system](#reusing-satellite-hardware-from-a-previous-system)
- [Before you call it done](#before-you-call-it-done)

---

## Step 0 — Before you touch anything

Three decisions that are annoying to reverse:

**Room names.** A room *is* a satellite, and its `room_id` becomes the
name of its MPD container, its database row, and the word you say out
loud. Pick short, spoken-friendly, permanent ones now: `kitchen`,
`bedroom`, `office` — not `pi-01`. You will say "play music in the
kitchen" several times a day for years.

**Where the server lives.** It needs to be always-on, on wired Ethernet if
at all possible, and physically somewhere fan noise doesn't matter.
Satellites reconnect by hostname or IP, so give the server a **DHCP
reservation on your router right now** — before anything points at it.
Moving the server's IP later means touching every Pi.

**How much disk your media will want.** Domovoi itself is ~15–20 GB with
all default models (less if you're following [CPU_HOST.md](CPU_HOST.md) and
running smaller ones). Your music, podcasts, and audiobooks sit on top of
that with no ceiling. Work out where that lives before you fill the boot
drive. See the README's [Disk footprint](../README.md#disk-footprint).

Then inventory what you actually have, because it changes which path you
take:

| Check | Why it matters |
|---|---|
| Does the server have an **NVIDIA GPU**? | If not, read [CPU_HOST.md](CPU_HOST.md) before Step 3 — you'll change four settings. |
| Windows or **Linux** server? | The docs are Windows-first. Linux works and is the better host on a non-NVIDIA box — read [LINUX_HOST.md](LINUX_HOST.md) for Steps 1–2 and the systemd units, then rejoin here at Step 3. |
| Which **mic board** is on each satellite? | The 2-Mics HAT and the XVF3800 array are genuinely different builds. The HAT also has a V1/V2.0 codec trap — identify it *before* flashing ([PROVISIONING §0](../satellite/PROVISIONING.md)). |
| **Pi Zero 2 W or a bigger Pi?** | Zero 2 W with an XVF3800 needs a true **OTG adapter**, not a plain cable, and a `cmdline.txt` fix. Pi 3/4/5 need neither. |
| **Stereo powered speakers?** | On the XVF3800, budget a **mono→stereo adapter** per room. Standing rule — see below. |

---

## Step 1 — Server prerequisites

> **On Linux?** [LINUX_HOST.md](LINUX_HOST.md) replaces this step and the
> next one — different packages, Docker Engine instead of Docker Desktop,
> and a virtualenv. Come back at
> [Step 3](#step-3--claim-admin-and-configure-for-your-hardware).

Install these before cloning anything:

- **Python 3.11+**
- **Docker Desktop** — start it and let it finish initializing. Postgres,
  migrations, SearXNG, and every room's music daemon live here.
- **[Ollama](https://ollama.com)**
- **Git**

Pull the language models now, because it's the slowest step and it can run
while you do everything else:

```bash
ollama pull llama3.2:3b
```

```bash
ollama pull qwen2.5:14b
```

> **On a CPU-only server, pull `qwen2.5:7b` instead of the 14B** — it's
> half the size and roughly twice the speed, and it's what
> [CPU_HOST.md](CPU_HOST.md) recommends. You can always pull the 14B later
> and switch with no restart.

---

## Step 2 — Bring up the server

From wherever you keep code:

```bash
git clone https://github.com/coders-farm-official/domovoi
```

Everything below runs from the **repo root** — the directory holding
`pyproject.toml`. This matters; `pip` and `pytest` both resolve packages
relative to it.

```powershell
pip install -e ".[dev,real-clients,voice-profile]"
```

On an **NVIDIA host**, add the CUDA runtime wheels. They live in their own
extra so CPU-only machines don't pull ~2–3 GB they'll never load:

```powershell
pip install -e ".[cuda]"
```

```powershell
pip install --no-deps resemblyzer
```

That last line is a Windows quirk, not a typo — `resemblyzer` (speaker
identification) pins a `webrtcvad` with no Windows wheels. Details in
[`domovoi/README.md`](../domovoi/README.md); on Linux a plain `pip install
resemblyzer` works. Skip the `voice-profile` extra entirely if you don't
want per-person voice profiles; it drops torch and saves a couple of GB.

Now the one-shot bootstrap — Postgres, migrations, and the core service:

```powershell
./domovoi/scripts/dev.ps1
```

(`./domovoi/scripts/dev.sh` under bash or git-bash.)

Leave that running and start the dashboard in a **second terminal**:

```powershell
./web/scripts/dev.ps1
```

(`./web/scripts/dev.sh` under bash.)

These are two separate long-lived processes and both need to stay up. The
core (`:6370`) owns everything real-time; the dashboard (`:6369`) serves
the UI and proxies admin actions to the core.

> **Gate — don't move on until:** `http://<server>:6369` loads in a
> browser, and its health indicator isn't reporting a degraded database.
> If the dashboard loads but reports degraded, the core or Postgres isn't
> up; check the first terminal.

<details>
<summary>What <code>dev.ps1</code> actually does, if you'd rather run it by hand</summary>

```powershell
cd domovoi
docker compose up -d postgres        # Postgres 16, host port 6432
docker compose run --rm flyway       # schema migrations
cd ..
python -m domovoi.main               # core on :6370
```

Two things `dev.ps1` deliberately leaves out:

- `docker compose run --rm flyway-test` — migrates the **test** database.
  You only need it to run `pytest`, so it's not part of a normal boot.
- `docker compose up -d searxng` — the metasearch proxy behind
  "double-check that" claim verification. Bound to `127.0.0.1:6888`, so
  the LAN can't reach it. Start it if you want that feature.

Also note the core builds the `domovoi-mpd:latest` image lazily on first
startup, so your first boot is slower than every subsequent one.
</details>

---

## Step 3 — Claim admin and configure for your hardware

On first boot the core prints an **8-word setup code** to its console and
writes it to `~/.domovoi/setup-code.txt`.

In the dashboard: **Settings → Configuration → Admin → "set up admin"**.
Enter the code, choose a password. The code file is deleted the instant
setup completes.

Day-to-day use doesn't need a login — the password gates the risky
surface: plugin installs, configuration, credentials. Locked out later?

```powershell
python -m domovoi.main --reset-admin
```

That clears the password and prints a fresh setup code.

### Now tune for your server

**If your server has no NVIDIA GPU, do this before anything else.** Open
[CPU_HOST.md](CPU_HOST.md) and apply its settings table — four values in
the dashboard's gear menu. The default `whisper_device = cuda` will fail
STT at startup on a machine without one, and the second-most-common
mistake (setting `device = cpu` but leaving `compute_type = float16`)
produces a system that works and feels broken.

Whisper settings are restart-tier: change them, then restart the core.
Ollama model settings are hot and take effect on the next turn.

---

## Step 4 — First voice turn

**Before you build a single satellite**, confirm the server's brain works.
This isolates the whole class of "is it the Pi or is it the server?"
problems you'd otherwise hit blind.

Post text straight at the core's **`/v1/intent`** endpoint. That is the
same router the satellites drive — it runs fast paths, handlers, and the
tool model, and writes an `intents_log` row per turn.

> **Not the dashboard's chat box.** That surface streams directly to
> Ollama (`chat_stream`) and never enters the router, so it proves Ollama
> is reachable and nothing else — no handler runs, no timer is created,
> and `intents_log` stays empty.

A fast path, no LLM involved — should return instantly and appear on the
Timers screen:

```bash
curl -s -X POST http://localhost:6370/v1/intent -H 'Content-Type: application/json' -d '{"transcript":"set a timer for 2 minutes","room_id":"kitchen"}'
```

A routed turn through the tool model and then the Q&A model — slower, and
the one that proves your model settings are actually working:

```bash
curl -s -X POST http://localhost:6370/v1/intent -H 'Content-Type: application/json' -d '{"transcript":"what is the capital of Mongolia","room_id":"kitchen"}'
```

**Test TTS too, while you're here.** Setting `synthesize` returns WAV
bytes, which exercises router → handler → TTS in one call — the best
smoke test available before a satellite exists:

```bash
curl -s -X POST http://localhost:6370/v1/intent -H 'Content-Type: application/json' -d '{"transcript":"what time is it","room_id":"kitchen","synthesize":true}' -o /tmp/tts-test.wav -D -
```

The response headers carry `X-Response-Text` and `X-Matched-Handler`, and
`/tmp/tts-test.wav` should be a non-trivial size. Copy it to a machine
with speakers if you want to hear it.

Watch the core's log while you do it. At startup it prints the model load
(`loading Whisper model=... device=... compute=...` → `Whisper ready`) —
that gap is boot cost, paid once, and the first one also includes
downloading the model.

**Per-turn latency is not logged.** It's written to the database instead:
one row per routed turn in `intents_log`, with `latency_ms` covering the
whole turn (STT → routing → handler), not STT alone. To read it:

```bash
docker exec -i domovoi-postgres psql -U domovoi domovoi -c "SELECT at, room_id, matched_handler, matched_path, latency_ms, transcript FROM intents_log ORDER BY at DESC LIMIT 10;"
```

**Write down what you see** — on a CPU host that's your baseline for
deciding whether to move up or down a Whisper size. Compare a fast-path
turn (a timer) against a routed one (a question): the difference is what
the language models cost you.

> **Gate:** both commands return sensible answers, and the core log shows
> Whisper loaded on the device you expect.

---

## Step 5 — The first satellite

Build **one** satellite completely and get it working before you touch the
others. Everything you learn here — your speaker's gain, your Wi-Fi's
quirks, your board's device name — applies to the rest.

There are two routes.

### The golden path (dashboard-prepared media)

Flash stock **Raspberry Pi OS Lite (64-bit)** with any tool, no
pre-configuration. Put the card back in the server. Dashboard →
**Satellites → prepare satellite media**. It writes a first-boot overlay
and a fully offline payload (wheels, packages, the satellite code from
this machine, plugin payloads) to the card. Boot the Pi, plug it into the
server's USB port, and adopt it from the Satellites page — name and Wi-Fi,
done.

> ⚠️ **Be aware:** this flow is code-complete but has **not yet been
> validated on real hardware** —
> [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) is the checklist, and
> it's entirely unticked. If you take this route, you're the first to run
> it. Have the manual path ready as a fallback, and consider ticking that
> checklist off as you go — it's exactly the pass the feature is waiting
> on.

### The manual path (proven)

[`satellite/PROVISIONING.md`](../satellite/PROVISIONING.md) is the
canonical checklist — every command, every gotcha, battle-tested. Follow
it start to finish. When it and the friendlier
[SATELLITE_HARDWARE.md](SATELLITE_HARDWARE.md) disagree, PROVISIONING.md
wins.

**Reading order depends on your mic board**, and this trips people up:

- **ReSpeaker 2-Mics HAT** → the main body of PROVISIONING.md, in order.
  Start at §0 and identify V1 vs V2.0 before you do anything else.
- **XVF3800 USB array** → **read [Appendix: XVF3800](../satellite/PROVISIONING.md)
  first**, then follow the main body with its skip-list applied. The
  appendix tells you to skip §0, §4, §4a, and §4b entirely — roughly half
  the HAT-specific work doesn't exist for you. Don't read the main body
  top-to-bottom and wonder why you're compiling device-tree overlays.

### XVF3800 landmines, in the order they'll bite you

Pulled forward from the appendix because each one costs an evening:

1. **The speaker plugs into the array's own 3.5 mm jack — never the Pi's.**
   The on-chip echo canceller uses that output as its echo reference. Wire
   it anywhere else and barge-in false-triggers on Domovoi's own voice.
   This is not optional.
2. **Stereo powered pair? Add a mono→stereo adapter.** The array's output
   is genuinely mono — the right DAC channel isn't wired to the jack. A
   daisy-chained second speaker sits silent otherwise. Standing rule: this
   setup gets the adapter by default.
3. **Pi Zero 2 W: add `dwc_otg.speed=1`** to the single line of
   `/boot/firmware/cmdline.txt`. The Pi's USB controller mis-clocks audio
   transfers at high speed and delivers audio ~8× too fast — the symptom
   is relentless `mic queue overflowing` and a wake word that never fires.
   Verify with `satellite/scripts/mic_probe.py`: you want `frames/sec ≈
   16000`, not ~128000. (Don't add a newline to that file — a malformed
   cmdline can stop the Pi booting.)
4. **The array is line-level and will sound faint** even at 100% volume.
   Set `[playback] gain` around `3.0` to boost Domovoi's voice and local
   clips without touching music.
5. **Install the whole `rpi_64bit/` folder** for `xvf_host`, not just the
   binary — it loads `libcommand_map.so` from its own directory. Then
   point `[leds] xvf_host_path` at it, since it won't be on `PATH`.

And two that apply to any board:

- **Underpowered PSU is the #1 cause of mystery flakiness.** 2.5 A
  minimum. Check `vcgencmd get_throttled` — anything but `0x0` means the
  Pi has browned out.
- **Give every satellite a DHCP reservation** ([PROVISIONING §7](../satellite/PROVISIONING.md)).

### Point it at the server

In the Pi's `~/.domovoi/config.toml`:

```toml
domovoi_url = "ws://<your-server>:6370"
```

Use whatever name resolves reliably on your LAN. On first connection the
core automatically provisions that room's MPD container and the room
appears on the dashboard's Satellites page.

---

## Step 6 — Verify the room end to end

Don't declare victory at "it connected." Walk this list:

| Test | Expected |
|---|---|
| Say the wake word | LED ring reacts; core log shows the connection and the wake event |
| *"What time is it?"* | Spoken answer from the room's speaker |
| *"Set a timer for one minute"* | Spoken confirmation, timer on the dashboard, and it actually fires in the room |
| *"Play [something in your library] in \<room\>"* | Music from that room's speaker |
| *"Turn it up"* / *"Set the volume to 50"* | Volume changes, and the dashboard reflects the new level |
| Talk over Domovoi mid-sentence | Barge-in — it stops. **XVF3800 only:** shouldn't false-trigger on its own voice. If it does, your speaker is on the wrong jack. |
| `vcgencmd get_throttled` on the Pi | `0x0` |
| Unplug the Pi, plug it back in | Boots, joins Wi-Fi, reconnects unattended — no SSH needed |

That last one is the real test. A satellite that needs a human after a
power cut isn't finished. It depends on the systemd unit from
[PROVISIONING §8](../satellite/PROVISIONING.md):

```bash
sudo -E sh ~/domovoi/satellite/scripts/install-service.sh
```

---

## Step 7 — The rest of the fleet

Now repeat. With one satellite proven, each additional room is mechanical:
flash, seat the board, install, set `room_id` and `domovoi_url`, install
the service, verify.

Some fleet-level things worth doing once you have two or more rooms:

- **Test the intercom.** *"Tell the kitchen dinner's ready"* from another
  room. This only becomes testable at two rooms and it's a headline
  feature.
- **Test drop-in** — live two-way audio between rooms.
- **Label the physical hardware** ([PROVISIONING §9](../satellite/PROVISIONING.md)).
  Room name and board type on the case. Future you, holding four identical
  Pis, will be grateful.
- **Watch RAM on Zero 2 Ws.** 512 MB is tight; openWakeWord fits but
  without much room.

---

## Step 8 — Make it yours

Optional, in rough order of payoff:

**Train a custom wake word.** The default is the built-in `hey_jarvis`.
The documented path is to record clips through a satellite's own mic,
train on the server, and push to the room — all from the dashboard, no
cloud, no third-party service. Covered in
[SATELLITE_HARDWARE.md § Custom wake words](SATELLITE_HARDWARE.md).

**Add your media library.** Point the music directory at wherever your
files live (`~/Music` by default).

**Install plugins.** Dashboard → Plugins, admin login required. Upload a
zip or paste a GitHub URL; Domovoi stages, validates, and shows a trust
screen with the permissions requested and the resolved dependency tree
before anything runs. The bundled [radio plugin](../plugins/radio) is the
reference example. Plugins run with real access on your server — install
only what you trust.

**Set up your backup.** See [Before you call it done](#before-you-call-it-done).

---

## Reusing satellite hardware from a previous system

If some of your Pis came from an earlier assistant build, they're just
hardware — there's no import path, and nothing on those cards is worth
keeping. Reflash them.

What *does* carry over is the physical kit: Pis, mic boards, speakers,
PSUs, cases, and the cabling. Check each bundle for:

- **Which mic board is actually on it.** Don't trust your memory of what
  you ordered. If it's a 2-Mics HAT, run [PROVISIONING §0](../satellite/PROVISIONING.md)
  to pin down V1 vs V2.0 — the two look nearly identical and use different
  codecs.
- **Card health.** Old SD cards in always-on devices die quietly. New
  cards are cheap next to an afternoon of debugging phantom failures.
- **PSU adequacy.** Chargers that were fine for an older build may not
  hold up under Wi-Fi plus audio load. 2.5 A minimum.

**Upgrading an existing HAT satellite to an XVF3800?** Don't reflash —
[`satellite/MIGRATION_HAT_TO_XVF3800.md`](../satellite/MIGRATION_HAT_TO_XVF3800.md)
covers the in-place swap: which cords move, what changes in config, what
to test, and how to roll back.

**One gotcha specific to reused hardware:** if a Pi was previously paired
to a Domovoi server, re-flashing invalidates its pairing token. See
[PROVISIONING § Re-pairing after a re-flash or device swap](../satellite/PROVISIONING.md).

---

## Before you call it done

**Know what holds your state.** Three things, and losing any of them hurts:

1. **The Postgres database** — settings, people, voice profiles, timers,
   reminders, library metadata, play history, plugin data, admin
   credentials. Lives in the Docker volume `domovoi-pgdata`.
2. **`~/.domovoi/` on the server** — Piper voices, chime sounds, trained
   wake-word models and their training clips, cover art, podcasts and
   audiobooks.
3. **`domovoi/.env`** — every setting you changed from the dashboard.

Set up a backup of all three *now*, while the system is small and you
remember why each one matters. The README's
[Returning users](../README.md#returning-users-moving-or-reinstalling)
section covers restoring them onto a new machine.

**Decide how the server starts.** `dev.ps1` is a foreground development
script — it dies with the terminal, and it doesn't come back after a
reboot. For a machine that's meant to run your house, the two processes
(`python -m domovoi.main` and `python -m web.backend.main`) need to start
unattended. On Windows that's a scheduled task at boot or a service
wrapper. On Linux it's systemd, and
[LINUX_HOST.md](LINUX_HOST.md#make-it-an-appliance) has the three units
ready to paste. Whichever you choose, test it by rebooting the server and
confirming the house comes back without you logging in.

**Test the power cut.** Pull power from the server. Bring it back. Does
everything return — Docker, Postgres, both processes, every satellite —
without a human? That's the difference between a demo and an appliance.

*May your stove stay warm and your wake word never misfire.* 🐈
