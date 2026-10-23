# Troubleshooting

Symptom → cause → fix, grouped by area. Start with [Which logs to check](#which-logs-to-check) if you're not sure where the problem lives. Terms are defined in the [Glossary](GLOSSARY.md); setup steps live in the [README](../README.md), [WINDOWS_HOST.md](WINDOWS_HOST.md) (the Windows installer) and `satellite/PROVISIONING.md`. Running without internet on purpose, or only sometimes? [INTERNET.md](INTERNET.md) says what each answer to the internet question switches, what is expected not to work, and what to download first.

- [On the Windows installer](#on-the-windows-installer)
- [Satellite won't connect](#satellite-wont-connect)
- [No TTS audio / Domovoi is silent](#no-tts-audio--domovoi-is-silent)
- [Wake word not triggering](#wake-word-not-triggering)
- [Wake-word training](#wake-word-training)
- [Music won't play](#music-wont-play)
- [Acquisitions / downloads stuck](#acquisitions--downloads-stuck)
- [Dashboard can't reach the core](#dashboard-cant-reach-the-core)
- [Everything offline, dashboard up](#everything-offline-dashboard-up)
- [Chat can't answer](#chat-cant-answer)
- [Printing and scanning](#printing-and-scanning)
- [Admin locked out](#admin-locked-out)
- [The app or dashboard says "too many wrong tokens"](#the-app-or-dashboard-says-too-many-wrong-tokens)
- [GPU / CUDA issues](#gpu--cuda-issues)
- [Docker issues](#docker-issues)
- [Which logs to check](#which-logs-to-check)

---

## On the Windows installer

The installer runs the server inside a private Linux (WSL2) that belongs to a hidden Windows account, and nobody has a shell in it. So the `journalctl`, `systemctl`, `docker` and `~/.domovoi` commands in the rest of this page are for Linux servers and by-hand installs, not for you. What you have instead:

| What you need | What to do |
|---|---|
| To see whether Domovoi is running | The icon beside the clock: its dot and tooltip say starting, stopped, or which part isn't answering |
| To restart it | **Restart Domovoi...** in the icon's menu; or **Restart Domovoi** (and, for a frozen core, **Restart the core**) in the dashboard under Settings → System → About & updates; or, from an administrator PowerShell, `Restart-Service DomovoiHost` |
| Logs to send when you ask for help | **Save logs for support...** in the icon's menu. Setup's own logs are in `C:\ProgramData\Domovoi\logs` (administrators only), and **Copy logs** on a Setup error copies them to `Documents\Domovoi logs` |
| Setup stopped halfway | Run Setup again: it carries on where it stopped |
| The setup code expired, or you lost it before claiming the admin account | Run Setup again: it restarts Domovoi, which writes a fresh code, and shows it on its last page |
| The browser shows an old dashboard, or claiming the admin account says "X-Requested-With header required" | Use **Open Domovoi** on Setup's last page: its address gets past the old dashboard's cached copy |

More: [When something goes wrong](WINDOWS_HOST.md#when-something-goes-wrong).

## Satellite won't connect

First stop on the Pi: `systemctl status domovoi-satellite` and `journalctl -u domovoi-satellite -f`.

| Symptom | Likely cause | Fix |
|---|---|---|
| Room never appears in Settings → Rooms & devices → Rooms | Wrong `domovoi_url` in the Pi's `~/.domovoi/config.toml` | It must be a WebSocket URL to the **core**, port **6370**: `ws://<server>.local:6370` — `ws://`, not `http://`, and not the dashboard's 6369 |
| `.local` hostname doesn't resolve from the Pi | mDNS flakiness | Use the server's LAN IP in `domovoi_url`, or fix mDNS; conversely, from Windows, install Bonjour to resolve the Pi's `.local` name |
| Connection refused | Core not running, or Windows firewall blocking inbound 6370 | Confirm the core is up (`http://<server>:6370/v1/health` from another machine); add an inbound allow rule for the port in Windows Defender Firewall |
| Service inactive after reboot | systemd unit not enabled | `sudo systemctl enable --now domovoi-satellite` (PROVISIONING.md §8) |
| Room shows connected, then drops ~15 s after Wi-Fi blips | Working as intended | The server pings each WebSocket every 10 s (5 s timeout); a dead socket is evicted within ~15 s and the Pi's client reconnects with backoff — no action needed |
| Dashboard **Restart satellite** button reports failure | Missing self-restart sudoers entry on the Pi | Add the one-line entry from PROVISIONING.md §8.1 — exactly `<user> ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-satellite.service` (sudo matches the whole argument list; `--no-block` is load-bearing) |
| Voice "fix the wifi" / the Wi-Fi watcher does nothing | Missing sudoers entry, or the recovery refused to guess | Add the entry from PROVISIONING.md §6.7 (`nmcli connection up id domovoi-home` on a NetworkManager unit, `wpa_cli … reassociate` otherwise). A log line `wifi recovery not run: …` says why it held back: the house network was not in the last scan while the link was up, or the setup hotspot profile is still on the unit (reboot it after the satellite upgrade, §8.4) |
| The router keeps listing the satellites as new devices; they drop off the server and come back | Before 2026-10-12 every server restart made every satellite rejoin the Wi-Fi, power saving was on, and every card was named `raspberrypi` | Update the server, upgrade each satellite, then reboot each satellite once (docs/RELEASE_NOTES.md, 2026-10-12). The room's retained log shows what the reboot changed (`network settings (applied as root at boot): changed: …`) |
| The setup page says **"Couldn't join that network. Check the password"** with `Hint: use 'journalctl -xe NM_CONNECTION=…'` in brackets, and the password is right | A card prepared before this fix left the house network's PMF setting to NetworkManager, which then offers WPA3 (SAE) to a WPA2/WPA3 mixed router whenever the Pi's driver claims it; the Pi Zero 2 W cannot finish SAE. The page also showed nmcli's hint line instead of its reason | Update the server and prepare the card again: the join now uses WPA2 with PMF off on a mixed router, and says the real reason. A satellite joined by a card with the fix keeps working when the router is in mixed mode. Without the fix, a router set to WPA2-PSK (AES) only lets it join, but it must stay that way: back in mixed mode, the satellite drops off at its next reconnect. See [satellite/PROVISIONING.md §8.5](../satellite/PROVISIONING.md#85-which-home-wi-fi-networks-a-satellite-can-join) |
| A phone says **"Couldn't connect to network"** to a new satellite's `Domovoi-Setup-<id>` network, with the right key | Cards prepared before 2026-10-17 left the setup network's security to NetworkManager, which offered WPA3 and Protected Management Frames whenever the Pi's Wi-Fi driver claimed support; some phones (Samsung) take WPA3 when offered, and the Pi's driver breaks it in access-point mode. Other phones joined fine | Update the server, prepare the card again and rewrite it: the setup network is now plain WPA2 (AES) with PMF off, on 2.4 GHz channel 1, 6 or 11. Meanwhile, set up from another phone. To see what the satellite did, put its card in any PC and open `domovoi/device-info.json`: `setup_network.line` says whether the network came up, on which channel, and how many phones joined it; `error` holds the last failure. A card prepared since then also takes `"channel": 1\|6\|11` and `"pmf": "optional"` in `domovoi/ap.json` on the same partition (read at every boot) |
| Satellite card shows **setup hotspot** / the Health card says **setup hotspot on** | The satellite is hosting its setup network instead of being on the house Wi-Fi | It recovers by itself (and reboots after 15 min if it cannot); after the satellite upgrade and one reboot the hotspot profile is gone for good (PROVISIONING.md §8.4) |
| Wi-Fi on a satellite card is amber or red | The signal at the satellite: amber -60 to -70 dBm, red below -70 | Move the satellite or the router closer, or away from metal and appliances. The Mbit/s next to it is the last frame's rate and says nothing on its own: an idle link reads 1 Mbit/s |
| Health card says **reboot needed** | The satellite was upgraded; three settings are applied at boot. Or it says **system packages missing (mpg123 …)**: a package its setup did not install, which the boot step installs online | Unplug it for a few seconds and plug it back in, with the Wi-Fi up. For packages, the log then says `base packages (installed as root at boot): …`; if it says `no_network` or `failed`, reboot again once the network is good |
| A satellite on a weak signal (around -75 dBm) drops off the Wi-Fi and stays red for minutes | The link itself went down (NetworkManager not connected, no route) | Since 2026-10-17 it tries to rejoin after 2 minutes, again 2.5 minutes after a failed try (turning the radio off and on first, on cards prepared since then), and reboots after about 10 minutes, waiting longer after each recent reboot. Move it or the router closer: below -70 dBm the link will keep dropping |
| The router still lists a satellite as `raspberrypi` | The router learns the name from the satellite's DHCP request. Before the 2026-10-12 follow-up, the rename came after that request, so a satellite needed a **second** reboot | Upgrade the satellite and reboot it once. Then check the Health card's **boot network step** and **host name**. *skipped* gives the reason, e.g. a host name someone chose, which is left alone. *not installed* means nothing runs the step at boot (PROVISIONING.md §8.4) |
| TTS chops mid-word for hours at a time | AP rate-control wedge (rx bitrate stuck at 1 Mbit/s) | The satellite's Wi-Fi watcher auto-reassociates once it has lost the server and the server stops answering; say "fix the wifi" to trigger it immediately, or tune `[wifi]` in the satellite config |
| Both satellites go to the orange "no server" ring together while the server is up | Before 2026-09-15 the watcher reassociated any link whose rx rate read under 5 Mbit/s, and an idle link reports the 1 Mbit/s beacon rate | Update the satellite code (dashboard upgrade); the watcher now leaves a link alone while a session is live and probes the server before blaming the Wi-Fi |
| The satellite's clock or time zone differs from the server's (log lines in the wrong zone, or dated months ago) | A Pi has no battery clock and Pi OS ships in Europe/London; a hand-built unit has no `domovoi-sync-time` helper, or the sudoers line for it is missing | A prepared card syncs both from the server at stage 1, stage 2 and on every connect (look for `time sync:` in the satellite log). On a hand-built unit run `sudo timedatectl set-timezone <zone>` once, or install the helper per PROVISIONING.md §8.2 |

## A satellite died while nobody was using it (the Health card and Last outage)

Two units did this in October 2026: fine for days, then the room offline and the ring orange, and only a power cycle brought them back — with nothing left to read, because the Pi's log ring is RAM. Since 2026-10-09 the satellite explains itself. Open the room in Settings → Rooms & devices → Rooms; the **Overview** tab has a **Health** card and, under it, a **Last outage** block; the **Logs** tab has a **retained on the server** view beside the live ring.

**Reading the Health card.** One sample a minute while the satellite is connected, and the last one is kept when it goes offline (the pill says *kept from before it went offline*). What to look at:

| Field | Healthy | What a bad value means |
|---|---|---|
| memory | 60–120 MB, peak not far above | Climbing towards 300 MB: a leak; the client exits and restarts itself at `[health] rss_limit_mb` while idle (the Last outage block then says `RSS … above the limit`) |
| cpu | a few % idle, system under 30 % | Pinned at 100 % with nothing to do: a reconnect storm (see *reconnects* and the retained log for `reconnecting in`) or a predict loop stuck behind throttling |
| temperature | under 70 °C, *never throttled this boot* | A red pill names the live `vcgencmd` flag (`under voltage`, `throttled`, `soft temp limit`): the power supply or the case. *throttled earlier this boot* means it happened and recovered |
| uptime | days, with few reconnects | A short uptime you did not cause is a reboot (power cut, the watchdog, the reboot helper); many reconnects on a long uptime is the link |
| wi-fi | 20–70 Mbit/s, NetworkManager *connected* | 1 Mbit/s on an IDLE link is the beacon rate and normal; *disconnected* or no link data while the room is offline is the radio |
| microphone | about 33 fps, wake loop a fraction of a second ago | 0 fps while the mic is on: the USB array stopped delivering (after two checks a minute apart the client reopens the stream, then restarts itself if it is still silent); a wake loop *ago* value that grows while nobody is talking: the mic thread is stuck (the client restarts itself once it has been idle 60 s with no tick, seen twice). Neither counts while a turn, chat, drop-in call or recording is running, nor while capture is stopped on purpose (a refused approval) |
| reachability | *gateway answers*, core reachable, ws up | *gateway silent* (no answer on TCP 53/80/443 nor to a ping, or no default route) with the core unreachable is the network, not Domovoi: after 5 min the client runs the Wi-Fi recovery (sharing one cooldown with the Wi-Fi watcher), after 15 min it reboots through `domovoi-reboot` (new cards; PROVISIONING §8.3 for existing ones), at most once an hour. Nothing is touched while the gateway answers, or while the core's own machine answers (a refused port is an answer): that is the core being down |
| handles | a few dozen fds | Hundreds and climbing: a leak; the client exits at 512 |

**Reading Last outage.** It is what the satellite's previous process life wrote to its SD card before it ended, delivered in its next hello:

* **exit reason** — `self-check: …` names the check that restarted it (the table above); `shutdown: requested` is a restart someone asked for (a config save, the Restart button, an upgrade); *unknown* means the process never got to write one: a power cut, the kernel's OOM killer, or a kernel/firmware wedge (then look at *uptime* — a reboot — and the offline table).
* **the offline table** — one row per failed connect while it was disconnected: the failure (`tcp_refused` = the core's port is closed, the core is down; `tcp_timeout` / `dns` = nothing answers, the network; `closed_before_ready` = the core took the socket and refused the session: pairing, approval — check Settings → Rooms & devices → Rooms for a pending approval), whether the **gateway** answered (silent = the Pi's own link or the router), whether the **core** answered, the link state, and the action the client took.
* **self-checks** — the last actions it took and why. An action ending in `_held` is one the **restart budget** stopped: a satellite restarts or reboots itself at most 3 times in a rolling hour (at most 1 of them a reboot), counted across restarts in `~/.domovoi/self-restarts.json`; past that the check only logs `HELD BACK` (every 10 minutes) and `last-health.json` shows `restart_budget` with `spent: true`. Every check also has to see its condition twice in a row before it acts. A unit that keeps hitting the budget has a real fault the restarts are not fixing — read the retained log for the `self-check:` lines.

**The retained log** (Logs tab → *retained on the server*) is the Pi's own log as it pushed it, including the lines marked `[offline]` that it wrote while it could not reach the core — the part that used to be lost. It survives the Pi dying and the core restarting; the last 1 MB per room is kept.

A room that shows **no health sample yet** runs satellite code from before 2026-10-09: upgrade it from the Overview tab, then it reports within a minute.

## No TTS audio / Domovoi is silent

The TTS engine chain is **piper → system** (every voice is local; the Microsoft Edge online voices were dropped in October 2026): a per-engine failure (network drop, missing voice, or an engine "succeeding" with a zero-length WAV) falls through to the next, so total silence is usually playback-side, not synthesis-side.

| Symptom | Likely cause | Fix |
|---|---|---|
| No speech at all, LEDs show "speaking" | Pi playback path, not the server | Check speaker power/cable; on the HAT verify the codec/overlay smoke test (`aplay`) from PROVISIONING.md §5; check `journalctl -u domovoi-satellite` for PortAudio device errors |
| No speech on an XVF3800 satellite | `output_device` not pinned to the array | Set `input_device` / `output_device` (device-name substrings are reboot-proof) in `[audio]`; the client logs a startup warning when the xvf profile runs with output unset. Find IDs with `python -m satellite.client --list-devices` |
| A word or name comes out wrong | The converter guessed (it reads unknown words with a small model) | Settings → Voice → Voices → *Say it like*: the word and how it sounds in plain letters, stressed syllable in capitals (`Siobhan` = `shiv-AWN`); the play button tries it before you save |
| A different voice after the October 2026 updates | The old defaults (lessac, then Norman) aren't the default any more, and the Microsoft Edge voices are gone: the server moved to Kristin, and rooms set to an Edge voice speak in the default | Settings → Voice → Voices → *Domovoi's voice*: Norman or Linda, with a sample of each |
| Robotic voice instead of the Piper voice, on a server answered **No** | The Piper voice isn't on disk, and with the internet off it can't be downloaded | Copy the `.onnx` + `.onnx.json` into `~/.domovoi/piper_voices/`, or switch the answer to **Sometimes** once and say something (the voice downloads), then back to **No**; while it is Sometimes the search helper starts and artist names are looked up ([what a temporary Sometimes does](INTERNET.md#updating-a-box-answered-no)) |
| Wake word answers with a canned "trouble reaching the network" clip | The Pi hasn't reached the server for 30+ s (`degraded_after_disconnect_sec`) | Fix the satellite↔server link (see the section above); the clip is the designed offline behavior, not a bug |
| Domovoi is much quieter than music | TTS engines normalize well below full scale | Raise `[playback] gain` on the satellite (2.0–4.0 typical); it scales TTS and greeting clips but leaves music untouched |
| Speech is choppy ("It's Wednes…day…") | WS delivery jitter draining the audio buffer, or the Wi-Fi rate wedge | Confirm `[playback] tts_prebuffer_sec` isn't set to 0; check the Wi-Fi watcher rows above |
| A specific uploaded voice produces silence | Bad voice model rendering zero-frame WAVs | The router already treats zero-frame output as failure and falls through; delete or re-upload the voice on the Voices page |

## Wake word not triggering

| Symptom | Likely cause | Fix |
|---|---|---|
| Never triggers, or only when shouting | Threshold too high, or mic level too low | Lower `[wake] threshold` (default 0.5; lower = more sensitive) in the Pi's config or via the dashboard's satellite Settings; check mic capture with `arecord` and let the boot-time mic-gain auto-tune run (HAT boards) |
| Triggers on random speech/TV | Threshold too low | Raise the threshold; if false triggers happen during playback, check that `[barge_in] require_wake_word` is still `true` (the default) for that room |
| Custom wake word never fires after "push to room" | Model/sidecar mismatch on the Pi | The push writes the slug to `~/.domovoi/wake` and the model must exist as `~/.domovoi/wake_models/<slug>.onnx` (synced from the server's `/v1/wake-models` channel). Check the satellite log for sync errors; delete the `~/.domovoi/wake` sidecar to revert to the configured word |
| Custom word fires poorly vs. the built-in | Too few / low-quality training clips | Server refuses training below 15 clips, but good models want far more — record hundreds on the actual satellite mic (especially XVF3800), then retrain; use the clip-quality scores shown while recording |
| Word sits in "training" forever | Training is off: nothing trains while it is, and a word queued then waits | Settings → Voice → Wake words → **Turn on wake-word training** (an admin, once; see [Wake-word training](#wake-word-training) below). With training on and no command set (`WAKE_WORD_TRAIN_COMMAND` empty), a queued word fails with a pointer to that card instead |
| The first words of a command are missing | Spoken over the wake acknowledgement, which the satellite does not listen to, by design | Wait for the greeting or chime to finish. If that wait is too long for the room, set **Wake acknowledgement** (dashboard → satellite → Settings, `[wake] ack_mode`) to "Short chime, then listen" (about 0.5 s) or "Light only, listen right away" |
| A long pause after the wake word before it listens | The spoken greeting plays to its end first — about 1.2 s with a Piper voice | The satellite log's `wake ack:` line gives the exact wait. Use the chime or light-only acknowledgement for the room |
| No greeting or chime after the wake word, only the light | `[wake] ack_mode = "none"` (or, with ack_mode unset, `[greeting] enabled = false`); or music was playing (the spoken greeting is skipped then); or `mpg123`/`aplay` is missing, or can't open the output device | Set the acknowledgement in the satellite's Settings; the log says `wake ack: greeting skipped (music was playing)`, `could not run …`, or `… exited with status N …` — for the last, check `[music] alsa_device`. Without `mpg123` (`wake ack: mpg123 is not installed`) the satellite plays the chime instead of the greeting until the boot step installs it: reboot it with the network up |
| (Developers) model scores never move when feeding audio manually | openWakeWord chunk-size quirk | `predict()` needs chunks in multiples of 80 ms (1280 samples @ 16 kHz); sub-minimum chunks make the model silently return nothing. The client accumulates 30 ms mic frames into 1280-sample slices — keep that buffering if you touch the audio loop |

## Wake-word training

Training runs on the server, in a Docker container, once an admin turns it
on in Settings → Voice → Wake words ([`scripts/wake_word/README.md`](../scripts/wake_word/README.md)).
A word's row shows the end of the trainer's output when it fails; the whole
output of its last run is in `~/.domovoi/wake_trainer/logs/<slug>.log` on
the server. A failed word trains again on the clips it has: press
**Train** on it again (or record a new take first).

| Symptom | Likely cause | Fix |
|---|---|---|
| "Training can't be turned on here: Internet access is turned off for this server" | The internet answer is **No**: turning training on downloads the trainer and its data, about 22 GB, once | Answer **Sometimes** or **Yes** in Settings → Privacy → Internet while you turn training on and train; the downloaded data stays. Every training is refused under **No** |
| "... training needs a 24 GB server" | The server has less memory than the standard model tier's line (`MemTotal` under 22,528 MiB); training uses about 10 GB beside the voice pipeline | A server with 24 GB or more |
| "Training needs a PC with about 32 GB of memory or more, and this PC has about N GB" (the Windows install) | The distro gets the PC's memory minus 8 GB, and training needs it to see 24 GB | A PC with about 32 GB or more ([WINDOWS_HOST.md](WINDOWS_HOST.md#training-wake-words)) |
| "Only N GB of disk is free; training needs about M GB plus 5 GB to spare" | Docker's data folder (`/var/lib/docker`, or the home drive under Docker Desktop) lacks the room: about 32 GB the first time | Free space there, then reload the page |
| "Domovoi can't see how much space is free on the Windows drive (C:)"; **Turn it on** stays grey until a box is ticked (the Windows install) | The distro's disk is a 1 TB virtual one inside C:, whatever C: holds, and the distro can't see C: itself | Check that C: has at least 40 GB free (File Explorer, **This PC**), then tick the box |
| "Docker is not running" / "Docker is not installed on this server" | Training runs in a Docker container | Start Docker (`sudo systemctl start docker`; Docker Desktop on a development PC). The provisioning installs Docker Engine on a Linux server and the Windows install |
| The card says the server has an NVIDIA card but Docker can't use it yet; it offers only the CPU | Docker Engine has no NVIDIA Container Toolkit yet (an install from before 2026-10-13) | Update from the dashboard (**Pull the latest**, then **Restart to apply changes**): the update unit installs it ([LINUX_HOST.md](LINUX_HOST.md#wake-word-training)). Then turn training on. Already on: **Check the GPU and trainer again** |
| The setup stops at "Check the GPU": "Docker can't reach the GPU (...)" with **Use the CPU** | `docker run --gpus all` failed: the toolkit is missing or Docker wasn't restarted after it, or (bare metal) the NVIDIA driver isn't working | Update, or **Restart Domovoi** (which reruns `scripts/linux/gpu-runtime.sh`), then **Try again**. Settings → System → About & updates's last update shows a `gpu-runtime` warning with the reason when the helper failed. After a failure the helper waits a day before it tries again at a Restart; pressing **Turn on** or **Check the GPU and trainer again** first makes the next Restart try at once (`/var/lib/domovoi-gpu-runtime/last-failure` says when and why it last failed). **Use the CPU** trains without it: about 75 minutes a word on 8 cores, voice slower meanwhile |
| "Interrupted: Domovoi restarted while training was being set up" | The core stopped (a restart, an update) during the setup job | **Try again**: it reuses what was done (the build cache, the data already downloaded) |
| The setup fails at "Build the trainer" or "Download the training data" | The download or build failed (a network drop, a full disk); the card shows what it printed last | **Try again**: the build cache and a partial data download are reused |
| Every training fails with "image ... not found locally" | `wake_word_trainer_enabled` was switched on in Settings → System → Advanced without the card's setup, or the image was removed | Settings → Voice → Wake words → **Turn on wake-word training** (or, when on, **Check the GPU and trainer again**), which builds it |
| "train command timed out after N s and was stopped", or "exited 124" with "the training time limit ... ran out" | The word took longer than **Training time limit** (Settings → System → Advanced → Wake words): sized to the server by default (an hour on a GPU, at least two on the CPU), too short for raised sample counts | Raise the time limit, or lower **Synthetic clips per wake word**. The container was removed; the synthetic clips made so far are kept and reused when the word trains again |
| "exited 137", or the log shows a stage that "exited -9" | The kernel stopped the training for running out of memory inside its cap (**Memory for training**, 12 GB) | Raise **Memory for training**, or lower the sample counts |
| A word fails with "not trained: training was set up for the GPU, and Docker can't reach it now" | Training was set up on the GPU, and this run's GPU check failed (the toolkit removed, Docker not restarted after it, the card gone or its driver broken). It is refused rather than run for over an hour on the CPU under the GPU's time limit | **Restart Domovoi** (it sets the toolkit up again), then **Check the GPU and trainer again**, and **Train**. Or turn training on again and **Use the CPU** |
| "Stopped: internet access was turned off (Settings > Privacy > Internet)" on the setup card | The internet answer became **No** while the setup was building or downloading; it stopped there | Answer **Sometimes** or **Yes**, then **Try again**: what was downloaded is reused |
| A word went back to *recording* with "Training stopped from the dashboard." | Someone pressed **Stop training** | **Train** again when you want it: its clips are all there |
| A word was training when the server restarted | Working as intended | The training container is removed and the word stays queued; it trains again once the core is back |
| The log says `backend = CPU` on a box with an NVIDIA GPU | The trainer's own GPU check failed at the start of that run | As for "Docker can't reach the GPU" above; [DOCKER_TRAINER.md](../scripts/wake_word/DOCKER_TRAINER.md#troubleshooting) has the one-line check |
| Voice is slow while a word trains | A CPU-only server: training takes most of the processor for about 75 minutes a word | Expected, and the dashboard said so before the Train. Lower **CPUs for training** to leave voice more, at the cost of a longer training |
| `docker ps` shows a `domovoi-wake-train` container while no word trains | Left over from a run the core could not stop (killed hard) | `python scripts/wake_word/train_wake_word.py --stop` from the checkout as the service user, or restart the core, which removes it at its first check |

## Domovoi answers itself / a turn nobody spoke

The satellite hears its own voice, transcribes it, and routes it as a new
command. The tell is a turn whose `user_text` is the *previous* turn's
`assistant_text` — in the 2026-09-10 office log, "Stopped." came back 3.9
seconds later as the question "stopped.".

Barge-in is the usual door in: it arms for the whole of playback and fires on
250 ms of anything VAD calls speech, so residual echo that the AEC didn't
fully cancel can trip it — and the frames that tripped it are carried into the
next capture as its opening audio. The core now drops a turn it recognizes as
its own reply verbatim (look for `self-echo: dropping barge-triggered turn`),
but that is a net under the problem, not a fix for it.

| Symptom | Likely cause | Fix |
|---|---|---|
| A turn's `user_text` is the previous turn's `assistant_text` | Barge-in firing on speaker echo | Set `[barge_in] require_wake_word` back to `true` for the room (dashboard → satellite → Settings); it is the default, so someone turned it off. Only the wake word then interrupts, and the echoed frames are not carried into the capture |
| Same, but you want to keep talk-over interruption | Detector tuned below the residual echo floor | Raise `[barge_in] min_speech_ms` (250 → 500–700), raise `vad_aggressiveness_during_tts` (→ 3), raise `[noise_gate] dbfs` (the XVF3800 profile ships a deliberately permissive −60 dB) |
| Turns tagged `barge-in` in the Conversations tab that you didn't start | Same root cause; the pill is the diagnostic | The Logs tab shows the matching `barge-in detected` line with its timestamp |
| The wake greeting appears at the start of transcripts, or a turn is dropped as `greeting echo` | A satellite from before the wake acknowledgement moved ahead of the capture: it plays the greeting over the capture | Upgrade the satellite (Settings → Rooms & devices → Rooms → the satellite → **Upgrade satellite**). A current one plays the greeting to the end, then listens, and its log shows `wake ack: … listening N ms after the wake word`. Until then, check `[music] alsa_device` — on an XVF3800 it must be the array (`plughw:CARD=Array,DEV=0`), not `default` |
| Long recordings of room conversation as one "command" | A barge opened a capture with no pre-speech timeout | It runs to `[listen] max_record_seconds` (default 30 s). Lower it, or stop the barge from misfiring above |

## Music won't play

Per-room playback = an MPD container per room on the server + `mpg123` on the Pi consuming its HTTP stream. Check both ends.

| Symptom | Likely cause | Fix |
|---|---|---|
| "Playing X" but no sound on the Pi | `mpg123` missing on the Pi (the Health card says **system packages missing**) | A card from media prep: reboot it with the network up, and the boot step installs it. A hand-built unit: `sudo apt install mpg123` (PROVISIONING.md §3). The server side succeeds end-to-end without it |
| No sound, `mpg123` installed | Wrong ALSA device for music | Set `[music] alsa_device` on the satellite (HAT default `plughw:0,0`; on the XVF3800 pin it explicitly to the array so audio routes through its speaker and AEC) |
| Room's music never starts, other rooms fine | That room's MPD container unhealthy | `docker ps -a | findstr domovoi-mpd-` — the container is named `domovoi-mpd-<room>`; check `docker logs` on it, `docker start` it, or delete it and reconnect the satellite (the provisioner recreates it; port assignments persist in the `mpd_rooms` table) |
| First room ever takes ages / warning about provision timeout | First MPD boot scans the whole library | Normal on a big library — the provisioner waits 30 s (`MPD_PROVISION_TIMEOUT_SEC`) then logs a warning; the container keeps scanning in the background |
| Track "plays" but library files aren't found | `MUSIC_DIR` mismatch | MPD containers mount `MUSIC_DIR` (default `~/Music`) read-only as `/music`. If you changed `MUSIC_DIR`, existing containers still mount the old path — remove the `domovoi-mpd-*` containers so they're recreated with the new mount |
| First second of every song stutters, or playback starts after a ~5 s pause | `music_ready` handshake not completing | The Pi primes its stream buffer (`[music] prime_sec`, default 1.0) then sends `music_ready`; if the frame never arrives, the server resumes anyway after `MUSIC_PREPARE_FALLBACK_SEC` (5 s). An old satellite build or a dropped WS causes the fallback path — update/restart the satellite |
| First play in a room after its MPD restarted is silent (music LEDs on, no sound); the Pi log says `mpg123 exited rc=1` with `Connection refused` or `no data at all from network resource`; the next play works | The room's stream wasn't open yet — MPD opens its http output only once it actually plays, and every start leaves it paused first | Fixed on both ends: the core opens a closed stream (`pause 0` + `pause 1`, nothing audible) and probes for HTTP 200 before `music_start` (`MUSIC_STREAM_READY_TIMEOUT_SEC`, 3 s), and the Pi retries a refused stream for ~8 s. If it still fails, the Pi logs `music: giving up`, turns its music LEDs off and (with a current core) sends `music_failed`, and the core pauses that room's MPD. Probe from the server with `curl -sS -m 3 -o /dev/null -D - http://127.0.0.1:<port>` — a serving stream prints `HTTP/1.1 200 OK` headers (then the command times out on the endless stream, which is expected); a bare TCP connect proves nothing (Docker's port proxy accepts it even while MPD refuses), and on Windows `curl -m 2` reports a refused port as a timeout |
| Music suddenly very quiet in one room | Stale MPD volume state | Shouldn't persist: the provisioner pins every (re)started MPD to 100% and the satellite's hardware mixer is the one true volume. Restart the room's container, then use the normal voice volume command |
| Uploaded files don't appear in the library | Index hasn't caught up | Uploads land in `MUSIC_DIR/uploads/` and a reindex is triggered after upload; give the indexer a moment, then check the core log |

## Acquisitions / downloads stuck

| Symptom | Likely cause | Fix |
|---|---|---|
| Requests pile up as `pending`; Domovoi says it can't fetch media right now | **No fulfiller installed — this is the designed state, not an error** | Core ships with no external media provider. Install a provider plugin that registers as an acquisition fulfiller; the moment one is enabled it drains the whole backlog (including detections queued while you were offline) |
| Row stuck in `claimed` | The fulfiller crashed mid-job | The row stays marked as claimed by that plugin until it finishes or fails it — check the plugin's log in `~/.domovoi/logs/plugin_<slug>.log`, fix what broke it (network, toolchain), and restart the core so the fulfiller's worker comes back up |
| Row flips to `failed` | The fulfiller retried and gave up | Terminal after 3 attempts (`ACQUISITION_MAX_ATTEMPTS`); the row's `error` field (dashboard detail / `GET /v1/acquisitions`) says why — commonly a dead URL or provider-side failure |
| Row marked `unfulfillable` | The fulfiller determined it can never satisfy this request | Re-request with better terms (different query text or a direct URL) |
| Same song requested twice, second request vanishes | Dedup, working as intended | Three layers: fuzzy match against the library at enqueue, a live-queue identity key, and provider-side re-dedup — an already-owned or already-queued item won't queue again |

## Dashboard can't reach the core

The dashboard (port 6369) is a separate process that reads the shared Postgres and calls the core's admin API on port 6370. When the core is unreachable, pages still load from the database, but live state degrades.

| Symptom | Likely cause | Fix |
|---|---|---|
| Rooms show status `unknown`; live cards empty; action buttons fail | Core not running or unreachable from the web process | Check `http://localhost:6370/v1/health`; start the core (`sudo systemctl restart domovoi-core` on a Linux server, `python -m domovoi.main` on a by-hand install; on the Windows installer, restart Domovoi from its icon). The web backend deliberately degrades instead of erroring |
| Both on the same box but still unreachable | Core URL override wrong | The web process finds the core via `DOMOVOI_URL` (default `http://localhost:6370`) — unset it or point it at the right host |
| Dashboard unreachable from another device on the LAN | Bind/firewall | The web backend binds `0.0.0.0:6369` by default (`WEB_HOST` / `WEB_PORT`); allow inbound 6369 in Windows Defender Firewall |
| Dashboard up but every page errors | Postgres down | See [Docker issues](#docker-issues) — both processes need the `domovoi-postgres` container |

## Everything offline, dashboard up

Every satellite offline at once, the phone app saying the core isn't answering, voice commands dead, and yet the dashboard on port 6369 loads: the **core** (port 6370) has stopped answering. Its port may still take connections; nothing answers on it. The dashboard is a separate process, which is why it is still up, and it says so: Home has "the Domovoi server isn't answering", Settings → Rooms & devices → Rooms says "The server's core isn't answering, so satellites can't connect" (the cards say *can't connect* and when each room was last heard from, not *offline*), and Settings → System → About & updates says the same.

That is what happened on 2026-10-14, for a day and a half, until a power cycle. Since 2026-10-15:

1. **The core restarts itself.** Its loop watchdog notices a stuck event loop after 30 s, or a database it can't reach after three probes, writes every thread's stack to the journal and to `~/.domovoi/logs/core-watchdog.log`, and exits; systemd starts it again 5 s later. Satellites and the app reconnect by themselves. Settings → System → About & updates then shows **last freeze**: when, and why. ([LINUX_HOST.md, The loop watchdog](LINUX_HOST.md#the-loop-watchdog).)
2. **If it hasn't come back after a minute or two**, sign in as admin and press **Restart the core** (Settings → System → About & updates, Settings → Rooms & devices → Rooms, or Home's row). The dashboard first makes sure the core is really stuck: no answer at all for about 45 s, longer than the core waits before ending itself (the button says *making sure it's stuck* meanwhile; a page left open counts that time already). Then it ends the core and systemd starts it again; the button waits until the core answers. A core that answers at all is never ended: busy for a few seconds, still starting, or waiting on its database. Where the dashboard can't do it (a core not run by systemd, a hand-written unit with `Restart=no`, a core under another account), it says why and shows the command to run instead. The next update adds `Restart=on-failure` to a unit with no restart policy at all.
3. **Over SSH**, when nothing else works: `sudo systemctl restart domovoi-core`. On the Windows install: `Restart-Service DomovoiHost` from an administrator PowerShell.

| Symptom | Likely cause | Fix |
|---|---|---|
| The core keeps restarting (Version: **last freeze** keeps changing) | Something freezes it again each time | Read `~/.domovoi/logs/core-watchdog.log`: each report names the stuck thread and line (`(the event loop)` marks the loop's). A plugin's worker there points at that plugin: disable it and tell its author. Report anything in the core with that file attached |
| **last freeze** says *database probes ... got no answer* | Postgres was stuck (not down: a down database is not counted) | `docker logs domovoi-postgres`, and the box's disk and memory |
| **Restart the core** missing, with *Restart=no* | A hand-written core unit | Press **Restart Domovoi** once the core answers (the update run adds the drop-in), or add `Restart=on-failure` yourself |
| **Restart the core** missing, with *Restart=on-abnormal* (or `on-watchdog`, `on-success`) | A restart policy of your own, which doesn't start a core that exited with 70 | The update run leaves it (its last update warns); set `Restart=on-failure` in a drop-in if you want the watchdog's restarts |
| Satellites: *The database isn't answering, so satellites can't connect* | Postgres is down: the core can't serve without it, and the dashboard's own check fails too | Restarting the core won't help, so no button. `docker ps -a --filter name=domovoi-postgres` (Windows) or `systemctl status domovoi-db` (Linux) |
| Satellites: *The server's core is running but can't answer* (Home: *running but can't answer · most likely its database*) | The core's event loop answers (`/v1/alive`), its health check doesn't: almost always its database | As above; once the database is back the core answers by itself |
| **Restart the core** says *busy, not frozen* | The core answered while the dashboard waited | Nothing ended. If it keeps going silent, its watchdog ends it when it stays stuck |
| **Restart the core** says *a restart is already under way* | Another press (another admin, another tab) is waiting or restarting | Wait for that one |
| After a power cycle, `journalctl -u domovoi-core` shows nothing before the reboot | The journal lived in RAM | Updates keep it on disk from now on ([LINUX_HOST.md](LINUX_HOST.md#the-loop-watchdog)); `core-watchdog.log` is on disk either way |

## Chat can't answer

The Chat page (dashboard and Android app) talks to Ollama from the web process. When there is no reply, the message under it says why in plain words; the exception itself, with Ollama's address, is in the web backend log (`chat stream failed (thread …, model …, <code>)`). The codes are listed in [API_REFERENCE.md](API_REFERENCE.md), §3.18.

| Symptom | Likely cause | Fix |
|---|---|---|
| "Pictures need the vision model qwen3-vl:2b-instruct-q4_K_M, which isn't installed on this Domovoi." (also shown by the paperclip) | The vision model isn't pulled; it isn't by default (except by `scripts/linux/provision.sh`) | Settings → Chat & AI → Models: install it, or switch the Vision row to a vision-capable model you have. See [FAQ: pictures in chat](FAQ.md#can-i-send-pictures-in-chat) |
| "This photo is in HEIC format, which the chat can't read. Send it from the Domovoi app…" (dashboard) | A phone camera's HEIC photo; a browser can't convert it | Send it from the Android app, which converts it to a JPEG, or save it as a JPEG first. See [FAQ: pictures in chat](FAQ.md#can-i-send-pictures-in-chat) |
| "This photo is in HEIC format, and this phone can't convert HEIC photos (that needs Android 9 or newer)." (app) | Android 8 has no HEIC decoder | Save the photo as a JPEG (or set the camera to JPEG) and send that |
| "The vision model … can't read pictures." | The Vision row names a text-only model | Pick a vision-capable model for the Vision row in Settings → Chat & AI → Models |
| "The model … isn't installed on this Domovoi." | `OLLAMA_MODEL` (the chat model) isn't pulled | Install it, or pick an installed one, in Settings → Chat & AI → Models |
| "… needs more memory than this Domovoi has free right now." | Other models are loaded (the voice models stay loaded for `OLLAMA_KEEP_ALIVE`) | Try again once they unload, shorten the keep-alive, or pick a smaller model; see [CPU_HOST.md](CPU_HOST.md) |
| "Domovoi couldn't reach its language model." | Ollama is stopped, restarting, or `OLLAMA_URL` points elsewhere | Check `ollama ps` / `systemctl status ollama` on the server, and `OLLAMA_URL` (default `http://localhost:11434`) |
| An older reply shows a raw "Client error '404 Not Found' for url …" line | The server is older than 2026-10-11 and kept the exception's text | Update the server; older replies are then read out in plain words as well (the stored row is left as it was) |
| "I can't answer that one — I'm set up for home commands. Say 'what can you do' to hear them.", or Chat is missing from the dashboard | AI chat and questions are turned off (by the household, or because setup was told to leave them out) | Expected. An admin turns them on under **AI chat and questions** in Settings → Chat & AI → Models, which shows the download size first. See [FAQ: Is AI optional?](FAQ.md#is-ai-optional) |
| "That document couldn't be read" | The file is damaged, password-protected, scanned pages with no text, or a format chat doesn't take: old .doc and .rtf files are refused | Save a Word file as .docx or PDF and attach that; for scanned pages, use a PDF with text in it. The message says which |
| "Domovoi couldn't find that in the document" | The passages found don't answer the question, so it says so instead of guessing | Ask with words the document uses, or attach the right file. A file longer than about 1,000 pages is read only as far as its first 4 million characters; the attachment says how far it got |
| A document in a phone chat is refused as a PDF | The phone can't read PDFs itself (that needs Android 15, or Android 12 to 14 with the PDF update) | Tap **ask the home server**: it reads the text and keeps nothing ([FAQ](FAQ.md#can-i-ask-a-chat-about-a-document)) |
| A long summary says "this summary was interrupted — carry on?" | The server restarted while it read | Press **carry on**: it reads only the rest. Two summaries at most read in the house at once; a third chat is asked to try again when one finishes |
| A lookup is refused, or says it can't go online | The internet answer is **No**, or you pressed **Not now**, or the lookup took too long (the search helper, Wikipedia or the page was slow), or too many were made in a minute (a limit per device and for the house) | Settings → Privacy → Internet to allow it; **Allow once** or **Allow in this chat** on the card; or wait a minute and ask again. With **No**, only the manual and an offline library answer ([FAQ](FAQ.md#does-domovoi-look-things-up-online)) |

## Printing and scanning

Domovoi prints to network printers (AirPrint, IPP Everywhere or Mopria) and scans from eSCL ("AirScan") scanners, with no drivers. USB printers and printers that need a maker's driver are not supported. Everything stays on your network, so it keeps working when the internet answer is **No**.

| Symptom | Likely cause | Fix |
|---|---|---|
| Settings → Rooms & devices → Printers & scanners finds no printer | Domovoi looks once, for about 3 seconds, when the page opens. The printer is asleep, on another network (a guest Wi-Fi, a second router), or only on USB | Wake it and press **Look again** (it also tries the addresses on your network, at most once a minute), or press **Add by address** and type the printer's address or name. Domovoi only takes addresses on your home network, and saves one only after it answers like a printer. On the Windows installer, the line under the list ("by multicast", "by unicast", "by sweep") says how the last look found printers and whether multicast reaches the server |
| "the printer's security certificate changed" | Domovoi remembers each printer's certificate when you add it; a firmware update or a factory reset changes it, and so would someone in between | If you did one of those, an admin presses **Trust the new certificate** on the printer's row. If you didn't, leave it |
| A Word or OpenDocument file says "save it as PDF first" | Print takes PDFs, pictures, text and markdown files and spreadsheets (printed as a plain table), not Word or OpenDocument files | Save it as PDF in Word or Docs, then print the PDF |
| A print is refused for its size | Limits: 100 pages and 20 copies a job, 500 sheets a day for the house | Print fewer pages, or an admin raises the limits in Settings → Rooms & devices → Printers & scanners |

## I deployed, and the dashboard still shows the old thing

The dashboard is static files, and a browser is allowed to reuse a file it
already has without asking. Before 2026-09-25 it was allowed to do that for
days: the static mount sent no `Cache-Control` at all, so freshness fell to
the RFC 9111 heuristic. That is fixed, but the fix arrives the same way
every other front-end change does, so it takes one reload to land.

| Symptom | Likely cause | Fix |
|---|---|---|
| You pulled and restarted, opened the dashboard from a bookmark or a pinned tab, and it is unchanged | That browser's copy of the page predates 2026-09-25, so it is still heuristically fresh and the browser did not ask the box anything | **Reload once** — plain Ctrl-R (Cmd-R, or pull-to-refresh on a phone). No hard refresh, no clearing site data. Do it once per browser and phone; every release after that arrives on an ordinary open |
| A tab you left open all day is running yesterday's dashboard | Nothing re-fetches a page that has already loaded, and no cache header can change that | The dashboard notices this itself now and shows "Domovoi has been updated…" with a reload button. If it is a pre-2026-09-25 page it cannot notice — reload it |
| A plugin's panel is still the old one after an upgrade | Same disease, second door: plugin scripts have no version in their URL | Reload once. Both the route and the page's loader ask past the cache from 2026-09-25 |
| Only ONE browser is stale and it has a service worker (a `localhost` or `https://` origin) | A worker registered under the old cache-first rules answers before the network, and it is already in the browser | Reload twice, once. `http://<host>:6369` is not a secure context, so no worker registers there and this cannot happen on the LAN install |
| Everything is stale on every device and a reload does not help | The box did not actually take the release | `GET /v1/admin/version` on the core, with the household token as `X-Device-Token`: compare `running_sha` with `checkout_sha`. A pull without a restart leaves them different |
| You edited `domovoi/.env` by hand (an API key, say), or saved a setting marked **restart**, and nothing changed | The services read their settings when they start | Settings → System → About & updates → **Restart Domovoi** (admin sign-in). It is there whenever nothing else is waiting to load; when something is, **Restart to apply changes** restarts too. With the update unit it is a plain restart (no backup, no dependency sync; the database is only restarted and migrated if Postgres doesn't answer or a migration is missing) unless the checkout isn't the commit the unit last applied (code pulled after the page read the version, or loaded by a hand restart): then the press says so before anything happens and runs the full update. A host without the self-restart grant shows the command to run instead ([LINUX_HOST.md](LINUX_HOST.md)). Before 2026-10-05 the card offered no restart unless code or a plugin upgrade was waiting |

To check what a given tab is running rather than guessing: its console has
`window.__DOMOVOI_BUNDLE__`, and `GET /api/bundle` on the box says what it
would serve now. Same value = that tab is current.

The mechanism, and what it does and does not promise:
[API_REFERENCE §3.22](API_REFERENCE.md#322-the-static-mount-and-how-a-front-end-change-reaches-a-browser).
The one-time action after the 2026-09-25 upgrade:
[RELEASE_NOTES](RELEASE_NOTES.md).

## Admin locked out

| Symptom | Likely cause | Fix |
|---|---|---|
| Forgot the admin password | — | On a Linux server or a by-hand install, on the server: `python -m domovoi.main --reset-admin`. This clears the credential and all sessions and writes a fresh 8-word setup code to `~/.domovoi/setup-code.txt` (also printed when you run it in a terminal). Open the dashboard, enter the code, choose a new password |
| Login rejected even with the right password | Failed-login backoff | Wrong attempts back off exponentially per source (1 s doubling, capped at 5 min). Wait it out, then log in |
| First-run setup asks for a code you don't have | Proof-of-possession by design | Read it from `~/.domovoi/setup-code.txt` on the server, as the user the core runs as (on a box set up by `scripts/linux/provision.sh`, that is `domovoi`: `sudo cat /home/domovoi/.domovoi/setup-code.txt`). The core prints it only when it runs in a terminal; it is never in `journalctl -u domovoi-core`, which shows only where the file is. If the file is gone, `--reset-admin` mints a new one |
| Logged in on the dashboard but core admin endpoints 401 | Not using the bearer token | Mutating endpoints accept only `Authorization: Bearer <token>`; the cookie exists solely for page loads. Tokens are shared across both processes — one login covers dashboard and core. See [Security & Privacy](SECURITY_PRIVACY.md) |
| Settings → System → Advanced says "Advanced settings … need an admin sign-in in this tab" and the Admin card says **signed in (view only after reload)** | The page was reloaded more than the reload window (an hour by default) after you entered the password. The admin sign-in lives only in the tab's memory (by design); inside the window a reload takes it back from the session cookie by itself, but past it the cookie only renders pages and changes nothing, and the server withholds the Advanced section and secret values from it. Also seen when the window is set to 0, and behind a reverse proxy that shows the browser another scheme or port than the dashboard's own (the resume is same-origin only) | Press **sign in** on that note, or **sign in again** on the Admin card. Advanced appears straight away, no reload. The window is Settings → System → Advanced → Security → **Stay signed in after a reload**. Until 2026-09-30 the page did not re-read after a sign-in, so Advanced stayed hidden until you switched Settings tabs |
| **sign out** on a reloaded page asks for the password | The page was reloaded after the reload window ended. The server ends a session only for a tab that proves it holds one — the cookie alone cannot sign anyone out, or a page on another site could. Inside the window the page takes its sign-in back and signs out without asking | Enter it once; the browser is then signed out for real, reloads included. Until 2026-09-30 that sign-out quietly did nothing and the next reload was signed in again |
| Right after updating, a browser that was signed in shows **signed out** | The 2026-10-14 update made the sign-in cookie a secret of its own instead of the admin token (other services on the server could see the old one); old cookies stop working | Sign in once. A tab that wasn't reloaded keeps working until you reload it |
| The Admin card says **Not signed out — the Domovoi server did not answer** (or **answered 502**) | You pressed **sign out** while the server was down or restarting, so the server never ended the session; the browser is still signed in, and says so | Press **try again** once the server is back (Settings → System → About & updates shows when). Until then the session is live |
| A reload, or a new tab, is still signed in as the admin without the password | Working as intended since 2026-10-14: for an hour (by default) after you enter the admin password in a browser, that browser's reloads and new tabs stay signed in. Only that browser — another device needs the password | To end it now, **sign out** (Settings → System → Advanced → Admin). To change how long it lasts, or turn it off (0), Settings → System → Advanced → Security → **Stay signed in after a reload** |
| The admin login said **Failed to fetch** while a restart you had just signed in for went ahead (or popped up again during the restart) | The sign-in had worked: the password was accepted and the restart replayed. Up to and including the 2026-10-01 release, the login prompt stayed up through the rest of the sign-in, any read the server refused while it was coming back opened it again, and a password sent while the server was away showed the browser's own "Failed to fetch" | Nothing to do: wait for Settings → System → About & updates to show the new version. With the fix that followed that release, the prompt closes the moment the password is accepted, the Version card says **Updating —** / **Restarting —** until the server answers, nothing asks for a sign-in while it is away, and a sign-in that cannot reach the server says so in words |

## The app or dashboard says "too many wrong tokens"

The household token is what a phone or a browser presents so the server
knows it belongs to the household (Settings → Rooms & devices → Phones & browsers → Household token on
an admin's dashboard). The server counts wrong tokens **per address**. It
allows five, then makes that address wait: 1 s, doubling, up to 5 minutes.
The answer is `429` with `Retry-After`. While the wait runs, every device
at that address is turned away, even one holding the right token. A device
that keeps sending an old token restarts the wait each time it ends.

| Symptom | Likely cause | Fix |
|---|---|---|
| The app shows **couldn't load rooms**, **calendar unavailable** and **offline**, and Videos says `429 Too Many Requests … too many wrong X-Device-Token values`. Or a browser shows the same 429 on every panel | Something at that address is sending a household token this server no longer accepts. Usually it is a **browser tab or an app that was paired before the server was reinstalled, moved to a new machine at the same address, or had its household token changed**. On 2026-10-17 it was a dashboard tab, left open in the phone's own browser, holding the old install's token: it locked out the app on that same phone | 1. Find the old holder on that device: an open dashboard tab on the server's address (close it, or pair it again), another app, or an old kiosk link with `device_token=` in it. 2. Wait up to 5 minutes. 3. Pair again with the current token: the app's card or Settings → Rooms & devices → Phones & browsers → Household token → **replace**; in a browser, **pair again** or **sign in as admin**, which fetches the token by itself. In the app you can also forget the server (tap the server name at the top, then the **✕** on its row) and add it again |
| The app shows **Domovoi doesn't recognise this phone any more** | The server refused the household token this phone holds. Since 2026-10-17 the app stops sending it at once instead of retrying | Paste the current household token into the card and press **pair again**. If the card counts a wait down, the new token is tried when the wait is over. If it was a passing fault on the server (its database not answering refuses every token), **try the old token again** |
| The app shows local media and **This looks like a different Domovoi, or it was reinstalled** | The server at the saved address proved a different identity from the one this phone trusted. The app sends it nothing until you decide | If you reinstalled or replaced your Domovoi, compare the new fingerprint with the dashboard's Settings → System → About & updates, then press **it's my Domovoi — trust it again**, and paste that server's household token on the pairing screen that follows (the old token is forgotten with the old identity). Away from home, don't |
| Pasting the household token says **That token doesn't match this server** | The token is another server's, or an old one: after a reinstall the server has a fresh token, and a custom one you chose before is gone | Copy it from **this** server's dashboard: Settings → Rooms & devices → Phones & browsers → Household token. Each paste is checked once with the server, so a few wrong ones make it ask you to wait (it counts down) |
| A page or the app says **auth check unavailable**, or **Couldn't check it with the server** | The server could not reach its database for a moment (a restart, too many connections) | Try again in a few seconds. Nothing is dropped: since 2026-10-17 this is a `503`, not the wrong-token answer |
| The dashboard says **This Domovoi doesn't recognise this browser any more** | The server refused the household token this browser stored. Since 2026-10-17 the browser stops sending it: not as a header, not on the live socket, not in picture or video links | **pair again** (paste the current token) or **sign in as admin**. Try **try the old token again** only if the server had a passing fault |
| The dashboard says **This Domovoi asked this address to wait** | Too many wrong tokens came from this address, often from another tab or device | Wait out the countdown. The browser sends nothing meanwhile and tries again by itself. Then find whatever still holds the old token (first row) |


## GPU / CUDA issues

| Symptom | Likely cause | Fix |
|---|---|---|
| STT fails at startup: cuBLAS/cuDNN DLLs not found (Windows) | NVIDIA DLL preload didn't run or wheels missing | `domovoi/bootstrap.py` must preload `nvidia/cublas`, `cudnn`, and `cuda_nvrtc` DLLs from pip wheels **before** anything imports `ctranslate2`/`faster-whisper` — don't reorder imports in `domovoi/main.py`. If you have no system CUDA install, make sure the NVIDIA pip wheels are present in the venv |
| Whisper model won't load / out of VRAM | Model too big for the GPU | The core starts anyway on `WHISPER_CPU_FALLBACK_MODEL` (default `small.en`, cpu, int8) and the Models page shows a banner with the load error. Set a smaller `WHISPER_MODEL` (Settings → System → Advanced → Speech-to-text; default is `large-v3`), restart |
| `cuda` configured on a machine with no NVIDIA GPU | — | Same fallback and banner. `WHISPER_DEVICE=cpu` (Settings → System → Advanced → Speech-to-text; `WHISPER_COMPUTE_TYPE=auto` then runs int8), pick a small model, restart. Slower but functional — see the [FAQ](FAQ.md#can-it-run-without-a-gpu) |
| Satellites answer "I can't understand speech right now" | Whisper failed to load at startup, and so did the CPU fallback | The Models page banner shows the error (`GET /v1/admin/hardware` → `stt`). Fix the Speech-to-text settings or install the real clients (`pip install -e ".[real-clients]"`), restart the core |
| Responses stall for up to two minutes then fail gracefully | Ollama wedged or still loading a model | The per-request timeout (`OLLAMA_TIMEOUT_SEC`, 120 s) bounds the turn. Check `ollama ps`, GPU memory pressure from Whisper + both Ollama models, and consider a smaller tool model |
| The first question after a quiet spell takes most of a minute, the next ones are quick | Ollama unloaded the models (its own default is 5 min) and reloaded them cold | Domovoi sends `OLLAMA_KEEP_ALIVE` (default `24h`, Settings → System → Advanced → Models → *Keep models loaded for*) on every call; check it isn't blank or `0`, and that `ollama ps` shows the models between turns. See [CPU_HOST.md](CPU_HOST.md#memory-budget) |
| The satellite says "Just a moment, I'm waking up my language model." | The question needed a model Ollama didn't have loaded; it is loading it now | Normal once after Ollama restarts. If it happens often, something is pushing the voice models out of memory — a third model (vision, Letta) or a big context window; see [CPU_HOST.md](CPU_HOST.md#memory-budget). The core log line `cold start in room=…` names the model |
| Driver just updated, everything broke | Driver/toolkit mismatch | Reboot first (Windows driver updates half-apply until then); then verify `nvidia-smi` works before blaming Domovoi |

## Docker issues

Compose commands run from the `domovoi/` directory (where `docker-compose.yml` lives). MPD containers are **not** in compose — they're created lazily per room.

| Symptom | Likely cause | Fix |
|---|---|---|
| Core can't reach Postgres | Container down | `cd domovoi && docker compose up -d postgres`; health-check with `docker ps` (container `domovoi-postgres`) |
| Port conflict on 6432 | Another service (e.g. pgBouncer, another stack) already bound 6432 | Domovoi publishes Postgres on 6432 precisely to avoid the default 5432 — if 6432 itself is taken, change the published port in `docker-compose.yml` and update `DATABASE_URL` to match |
| `psql -h <server> -p 6432` from another machine is refused | By design: Postgres, Letta, SearXNG and the MPD control ports are published on `127.0.0.1` only | Run `psql` (or anything else that needs the database) on the server itself, e.g. `docker exec -it domovoi-postgres psql -U domovoi -d domovoi` |
| Core logs `password authentication failed for user "domovoi"` on first boot | `docker compose up` ran before `python -m domovoi.env_bootstrap`, so the volume was initialised with the template password while `.env` carries a random one (or the other way round) | Make the two agree: either rotate the database to the `.env` value ([LINUX_HOST.md](LINUX_HOST.md#two-more-linux-notes)) or, on a brand-new install with nothing in it, `docker compose down -v` and start again with `env_bootstrap` first |
| Music in one room stopped once right after upgrading the core; log says `recreating it bound to 127.0.0.1` | That room's MPD container was created before the control port moved to loopback; port bindings can't be edited, so the core recreated it once | Nothing to do — the data volume carried over and the new container never trips this again. Say "play" to resume |
| Tables missing after a fresh setup | Migrations never ran | `docker compose run --rm flyway` (prod DB) and `docker compose run --rm flyway-test` (test DB) — they exit after migrating; that's normal |
| `pytest` refuses to run / can't find `domovoi_test` | Test DB missing on a pre-existing volume | The `domovoi_test` DB is only auto-created on a *fresh* Postgres volume; on an existing one create it manually, then `docker compose run --rm flyway-test`. The test suite hard-refuses any non-`_test` database by design |
| Web answers ("check that online", the weather, "double-check that") answer "I can't search the web right now — my search helper isn't running" | The SearXNG container isn't running. The core's log says `SearXNG isn't answering at http://localhost:6888` once | Saving **Yes** or **Sometimes** in Settings → Privacy → Internet starts it, and so do `dev.sh` / `dev.ps1` and the Linux update unit. By hand, from `domovoi/`: `docker compose up -d searxng` (the first start downloads the pinned image); Docker restarts it after reboots. Check with `docker ps --filter name=domovoi-searxng` and `docker logs domovoi-searxng`. It's localhost-only on port 6888, reachable solely from the server itself |
| Web answers say "I couldn't reach my search helper just now" | SearXNG answered with an error, or something that isn't its JSON (a wrong `SEARXNG_URL`, the container still starting) | Try again in a moment; if it persists, `docker logs domovoi-searxng`, and check `SEARXNG_URL` (default `http://localhost:6888`) |
| Web answers say "I'm set to stay off the internet, so I can't check that" | The server is answered **No** to the internet question | Expected. To allow web answers, switch to **Yes** or **Sometimes** in Settings → Privacy → Internet ([INTERNET.md](INTERNET.md)) |
| A button is greyed with "needs internet · Settings → Privacy → Internet", or an action fails with "internet access is turned off for this box (Settings → Privacy → Internet)" (HTTP 409) | The server is answered **No** | Expected: it would reach the internet. Change the answer in Settings → Privacy → Internet if you want it ([what each answer changes](INTERNET.md#what-each-answer-changes)) |
| "I checked online but couldn't find a clear answer to that" | The search ran and found nothing usable | Rephrase, or ask more specifically; this reply now only comes after a search that really ran |
| MPD containers won't start after changing `MUSIC_DIR` or Docker Desktop file-sharing | Stale mounts / unshared drive | Share the drive in Docker Desktop settings; remove the `domovoi-mpd-*` containers so the provisioner recreates them with current paths |
| Chat mode won't start | Letta container not up | Chat mode is off by default; enabling `CHAT_MODE_ENABLED` assumes `docker compose up letta` and the required Ollama models (including the embedding model) are pulled |

## Questions land on the wrong handler

**Symptom:** a plain question gets a tool's answer — "who wrote the odyssey"
comes back as "I'm not sure what to calculate", "who painted the mona lisa"
gets "Yes, that checks out", or a trivia question turns into a news digest.

**Cause:** the utterance missed every fast path and the LLM tool router
picked a tool for it. Small tool models on a CPU host do this: shown a
"verify a claim" or "look up a subject" schema, they read a factual
question as that job. The router prompt, the handlers' tool descriptions
and the per-handler tool-offer gate (`Handler.offers_tool`) all exist to
prevent it, and they are only as good as the model reading them.

**Check:** replay the routing corpus at the running core and read which
handler each case landed on — every `qa` case must come back with no
handler:

```bash
python scripts/eval_routing.py --core http://localhost:6370 --room bench
```

A single utterance is just `curl` at `/v1/intent`, with the household
device token as `X-Device-Token` (see
[SETUP_RUNBOOK.md](SETUP_RUNBOOK.md)) — look at `matched_handler` and
`matched_path`, not the dashboard chat box, which never enters the router.
A bare `401` from that curl means the header is missing or stale, not that
routing is broken. The quoting is **not** the suspect:
`-H "X-Device-Token: $TOKEN"` carries spaces, `$`, backticks and double
quotes through unchanged, because the shell never re-scans the result of an
expansion for quoting. Three things do bite a custom household token:

* **A stray CR in the mirror file.** An older Windows build wrote
  `~/.domovoi/device-token.txt` with CRLF endings, and `$(cat …)` strips the
  trailing newline but not the carriage return, so the header never matches.
  `od -c ~/.domovoi/device-token.txt | tail -2` shows it. The first boot
  on the current build rewrites the file with a bare `\n`, so if it is
  still CRLF the process has not restarted since the update.
* **An unquoted YAML scalar** (Home Assistant) — quote the value, see
  [HOME_ASSISTANT.md](HOME_ASSISTANT.md).
* **A process that has not been restarted since the update.** A token an
  admin *chose* is only understood by a build that has the two-form match;
  an older core still canonicalises the candidate first, so it refuses every
  device-tier call — the dashboard's proxied writes, `/v1/intent`, the
  satellites and the phone all at once. The dashboard can set the token
  before the core has restarted, so restart **both** units and then set it.
  If a device tier has already gone dead this way, restart rather than
  rotate; rotating to a generated phrase also works, because a phrase is
  canonical and an old process accepts it.

**Fix:** try the other small tool model (`ollama_tool_model`, hot setting —
see [CPU_HOST.md](CPU_HOST.md)); if one phrasing keeps misrouting, an
anchored fast path or a tighter `offers_tool` gate on the handler that
poaches it is the deterministic fix. Re-run the corpus after either.

## Which logs to check

| Log | Where | What's in it |
|---|---|---|
| Core log | `journalctl -u domovoi-core` on a Linux server; the `python -m domovoi.main` console on a by-hand install (level via `LOG_LEVEL`) | The whole voice pipeline: routing, handlers, MPD provisioning, workers. Millisecond timestamps; snapshot/health polling spam is filtered out for you |
| Core freezes | `~/.domovoi/logs/core-watchdog.log` (1 MB, one older copy); `core-faulthandler.log` beside it until the next start moves it in | Every time the core was ended for freezing: the reason, every thread's stack, the event loop's tasks. On disk, so it survives a power cycle ([Everything offline, dashboard up](#everything-offline-dashboard-up)) |
| Per-plugin logs | `~/.domovoi/logs/plugin_<slug>.log` (5 MB × 3 rotation) | Everything a given plugin does. Also tailed live on that plugin's dashboard detail page. Crank one plugin to DEBUG without drowning the core: `LOG_LEVEL_PLUGIN_<SLUG>=DEBUG` |
| Web backend log | `journalctl -u domovoi-web` on a Linux server; the `python -m web.backend.main` console on a by-hand install (`LOG_LEVEL`) | Dashboard API, realtime listeners, plugin web pages |
| Satellite log | `journalctl -u domovoi-satellite -f` on the Pi (level via `[log]` in its config) | Wake word, capture, playback, Wi-Fi watcher, sync channels, and one `health:` line every ten minutes |
| Satellite log, retained on the server | Settings → Rooms & devices → Rooms → room → Logs → *retained on the server*; the file is `~/.domovoi/satellite-logs/<room>.log` (1 MB, one rotation) | The same lines, as the Pi pushed them — including `[offline]` lines written while it could not reach the core. Survives the Pi dying |
| Satellite health | Settings → Rooms & devices → Rooms → room → Overview → Health; `GET /api/satellites/<room>/health`; the core log's `satellite health room=…` lines (one per ten samples) and `reports an outage from its previous life` warnings | Memory, CPU, temperature and throttling, uptime, mic rate, link, reachability — one sample a minute, 24 h kept; and what the previous process life left behind |
| MPD (per room) | `docker logs domovoi-mpd-<room>` | Library scan, stream/decoder errors for that room |
| Postgres / SearXNG / Letta | `docker logs domovoi-postgres` etc. | Infrastructure containers |
| The Windows installer | `C:\ProgramData\Domovoi\logs` (administrators only): `install.log`, `host.log`, `provision.log`; the tray icon's **Save logs for support...** copies them | What Setup and the DomovoiHost service did, and what `provision.sh` printed, with the failing unit's last journal lines when the start fails. Passwords, tokens and setup codes are blanked as far as Setup can recognise them: look through a copy before you share it |

---

*Not covered here? Check the [FAQ](FAQ.md) for behavior that looks broken but is by design, or open an issue — see [Contributing](CONTRIBUTING.md).*
