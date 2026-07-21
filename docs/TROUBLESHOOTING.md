# Troubleshooting

Symptom → cause → fix, grouped by area. Start with [Which logs to check](#which-logs-to-check) if you're not sure where the problem lives. Terms are defined in the [Glossary](GLOSSARY.md); setup steps live in the [README](../README.md) and `satellite/PROVISIONING.md`.

- [Satellite won't connect](#satellite-wont-connect)
- [No TTS audio / Domovoi is silent](#no-tts-audio--domovoi-is-silent)
- [Wake word not triggering](#wake-word-not-triggering)
- [Music won't play](#music-wont-play)
- [Acquisitions / downloads stuck](#acquisitions--downloads-stuck)
- [Dashboard can't reach the core](#dashboard-cant-reach-the-core)
- [Admin locked out](#admin-locked-out)
- [GPU / CUDA issues](#gpu--cuda-issues)
- [Docker issues](#docker-issues)
- [Which logs to check](#which-logs-to-check)

---

## Satellite won't connect

First stop on the Pi: `systemctl status domovoi-satellite` and `journalctl -u domovoi-satellite -f`.

| Symptom | Likely cause | Fix |
|---|---|---|
| Room never appears on the Satellites page | Wrong `domovoi_url` in the Pi's `~/.domovoi/config.toml` | It must be a WebSocket URL to the **core**, port **6370**: `ws://<server>.local:6370` — `ws://`, not `http://`, and not the dashboard's 6369 |
| `.local` hostname doesn't resolve from the Pi | mDNS flakiness | Use the server's LAN IP in `domovoi_url`, or fix mDNS; conversely, from Windows, install Bonjour to resolve the Pi's `.local` name |
| Connection refused | Core not running, or Windows firewall blocking inbound 6370 | Confirm the core is up (`http://<server>:6370/v1/health` from another machine); add an inbound allow rule for the port in Windows Defender Firewall |
| Service inactive after reboot | systemd unit not enabled | `sudo systemctl enable --now domovoi-satellite` (PROVISIONING.md §8) |
| Room shows connected, then drops ~15 s after Wi-Fi blips | Working as intended | The server pings each WebSocket every 10 s (5 s timeout); a dead socket is evicted within ~15 s and the Pi's client reconnects with backoff — no action needed |
| Dashboard **Restart satellite** button reports failure | Missing self-restart sudoers entry on the Pi | Add the one-line entry from PROVISIONING.md §8.1 — exactly `<user> ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-satellite.service` (sudo matches the whole argument list; `--no-block` is load-bearing) |
| Voice "fix the wifi" / the Wi-Fi watcher does nothing | Missing `wpa_cli` sudoers entry | Add the entry from PROVISIONING.md §6.7 so the satellite can run `wpa_cli reassociate` without a password |
| TTS chops mid-word for hours at a time | AP rate-control wedge (rx bitrate stuck at 1 Mbit/s) | The satellite's Wi-Fi watcher auto-reassociates below 5 Mbit/s; say "fix the wifi" to trigger it immediately, or tune `[wifi]` in the satellite config |

## No TTS audio / Domovoi is silent

The TTS engine chain is **edge → piper → system**: a per-engine failure (network drop, missing voice, or an engine "succeeding" with a zero-length WAV) falls through to the next, so total silence is usually playback-side, not synthesis-side.

| Symptom | Likely cause | Fix |
|---|---|---|
| No speech at all, LEDs show "speaking" | Pi playback path, not the server | Check speaker power/cable; on the HAT verify the codec/overlay smoke test (`aplay`) from PROVISIONING.md §5; check `journalctl -u domovoi-satellite` for PortAudio device errors |
| No speech on an XVF3800 satellite | `output_device` not pinned to the array | Set `input_device` / `output_device` (device-name substrings are reboot-proof) in `[audio]`; the client logs a startup warning when the xvf profile runs with output unset. Find IDs with `python -m satellite.client --list-devices` |
| Voice sounds different than usual when internet is down | Expected fallback | Edge (online) failed → Piper (local) spoke instead. Set the engine to `piper` in the settings gear if you want one consistent offline voice |
| Wake word answers with a canned "trouble reaching the network" clip | The Pi hasn't reached the server for 30+ s (`degraded_after_disconnect_sec`) | Fix the satellite↔server link (see the section above); the clip is the designed offline behavior, not a bug |
| Domovoi is much quieter than music | TTS engines normalize well below full scale | Raise `[playback] gain` on the satellite (2.0–4.0 typical); it scales TTS and greeting clips but leaves music untouched |
| Speech is choppy ("It's Wednes…day…") | WS delivery jitter draining the audio buffer, or the Wi-Fi rate wedge | Confirm `[playback] tts_prebuffer_sec` isn't set to 0; check the Wi-Fi watcher rows above |
| A specific uploaded voice produces silence | Bad voice model rendering zero-frame WAVs | The router already treats zero-frame output as failure and falls through; delete or re-upload the voice on the Voices page |

## Wake word not triggering

| Symptom | Likely cause | Fix |
|---|---|---|
| Never triggers, or only when shouting | Threshold too high, or mic level too low | Lower `[wake] threshold` (default 0.5; lower = more sensitive) in the Pi's config or via the dashboard's satellite Settings; check mic capture with `arecord` and let the boot-time mic-gain auto-tune run (HAT boards) |
| Triggers on random speech/TV | Threshold too low | Raise the threshold; also consider `[barge_in] require_wake_word = true` if false triggers happen during playback |
| Custom wake word never fires after "push to room" | Model/sidecar mismatch on the Pi | The push writes the slug to `~/.domovoi/wake` and the model must exist as `~/.domovoi/wake_models/<slug>.onnx` (synced from the server's `/v1/wake-models` channel). Check the satellite log for sync errors; delete the `~/.domovoi/wake` sidecar to revert to the configured word |
| Custom word fires poorly vs. the built-in | Too few / low-quality training clips | Server refuses training below 15 clips, but good models want far more — record hundreds on the actual satellite mic (especially XVF3800), then retrain; use the clip-quality scores shown while recording |
| Word sits in "training" forever | Trainer disabled or unconfigured | Training is Linux-only, so on the Windows server it shells out: set `WAKE_WORD_TRAINER_ENABLED=true` **and** `WAKE_WORD_TRAIN_COMMAND` to your WSL2/Docker pipeline (see `scripts/wake_word/README.md` and `DOCKER_TRAINER.md`). An empty command marks queued words failed with a runbook pointer |
| (Developers) model scores never move when feeding audio manually | openWakeWord chunk-size quirk | `predict()` needs chunks in multiples of 80 ms (1280 samples @ 16 kHz); sub-minimum chunks make the model silently return nothing. The client accumulates 30 ms mic frames into 1280-sample slices — keep that buffering if you touch the audio loop |

## Music won't play

Per-room playback = an MPD container per room on the server + `mpg123` on the Pi consuming its HTTP stream. Check both ends.

| Symptom | Likely cause | Fix |
|---|---|---|
| "Playing X" but no sound on the Pi | `mpg123` missing on the Pi | `sudo apt install mpg123` (PROVISIONING.md §3 — the server side succeeds end-to-end without it) |
| No sound, `mpg123` installed | Wrong ALSA device for music | Set `[music] alsa_device` on the satellite (HAT default `plughw:0,0`; on the XVF3800 pin it explicitly to the array so audio routes through its speaker and AEC) |
| Room's music never starts, other rooms fine | That room's MPD container unhealthy | `docker ps -a | findstr domovoi-mpd-` — the container is named `domovoi-mpd-<room>`; check `docker logs` on it, `docker start` it, or delete it and reconnect the satellite (the provisioner recreates it; port assignments persist in the `mpd_rooms` table) |
| First room ever takes ages / warning about provision timeout | First MPD boot scans the whole library | Normal on a big library — the provisioner waits 30 s (`MPD_PROVISION_TIMEOUT_SEC`) then logs a warning; the container keeps scanning in the background |
| Track "plays" but library files aren't found | `MUSIC_DIR` mismatch | MPD containers mount `MUSIC_DIR` (default `~/Music`) read-only as `/music`. If you changed `MUSIC_DIR`, existing containers still mount the old path — remove the `domovoi-mpd-*` containers so they're recreated with the new mount |
| First second of every song stutters, or playback starts after a ~5 s pause | `music_ready` handshake not completing | The Pi primes its stream buffer (`[music] prime_sec`, default 1.0) then sends `music_ready`; if the frame never arrives, the server resumes anyway after `MUSIC_PREPARE_FALLBACK_SEC` (5 s). An old satellite build or a dropped WS causes the fallback path — update/restart the satellite |
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
| Rooms show status `unknown`; live cards empty; action buttons fail | Core not running or unreachable from the web process | Check `http://localhost:6370/v1/health`; start the core (`python -m domovoi.main`). The web backend deliberately degrades instead of erroring |
| Both on the same box but still unreachable | Core URL override wrong | The web process finds the core via `DOMOVOI_URL` (default `http://localhost:6370`) — unset it or point it at the right host |
| Dashboard unreachable from another device on the LAN | Bind/firewall | The web backend binds `0.0.0.0:6369` by default (`WEB_HOST` / `WEB_PORT`); allow inbound 6369 in Windows Defender Firewall |
| Dashboard up but every page errors | Postgres down | See [Docker issues](#docker-issues) — both processes need the `domovoi-postgres` container |

## Admin locked out

| Symptom | Likely cause | Fix |
|---|---|---|
| Forgot the admin password | — | On the server: `python -m domovoi.main --reset-admin`. This clears the credential and all sessions and writes a fresh 8-word setup code to `~/.domovoi/setup-code.txt` (also printed to the console). Open the dashboard, enter the code, choose a new password |
| Login rejected even with the right password | Failed-login backoff | Wrong attempts back off exponentially per source (1 s doubling, capped at 5 min). Wait it out, then log in |
| First-run setup asks for a code you don't have | Proof-of-possession by design | Read it from `~/.domovoi/setup-code.txt` on the server or from the core's startup console output. If it's gone, `--reset-admin` mints a new one |
| Logged in on the dashboard but core admin endpoints 401 | Not using the bearer token | Mutating endpoints accept only `Authorization: Bearer <token>`; the cookie exists solely for page loads. Tokens are shared across both processes — one login covers dashboard and core. See [Security & Privacy](SECURITY_PRIVACY.md) |

## GPU / CUDA issues

| Symptom | Likely cause | Fix |
|---|---|---|
| STT fails at startup: cuBLAS/cuDNN DLLs not found (Windows) | NVIDIA DLL preload didn't run or wheels missing | `domovoi/bootstrap.py` must preload `nvidia/cublas`, `cudnn`, and `cuda_nvrtc` DLLs from pip wheels **before** anything imports `ctranslate2`/`faster-whisper` — don't reorder imports in `domovoi/main.py`. If you have no system CUDA install, make sure the NVIDIA pip wheels are present in the venv |
| Whisper model won't load / out of VRAM | Model too big for the GPU | Set a smaller `WHISPER_MODEL` (settings gear → advanced → Speech-to-text; default is `large-v3`), restart |
| `cuda` configured on a machine with no NVIDIA GPU | — | `WHISPER_DEVICE=cpu` (settings gear → advanced), pick a small model, restart. Slower but functional — see the [FAQ](FAQ.md#can-it-run-without-a-gpu) |
| Responses stall for up to two minutes then fail gracefully | Ollama wedged or still loading a model | The per-request timeout (`OLLAMA_TIMEOUT_SEC`, 120 s) bounds the turn. Check `ollama ps`, GPU memory pressure from Whisper + both Ollama models, and consider a smaller tool model |
| Driver just updated, everything broke | Driver/toolkit mismatch | Reboot first (Windows driver updates half-apply until then); then verify `nvidia-smi` works before blaming Domovoi |

## Docker issues

Compose commands run from the `domovoi/` directory (where `docker-compose.yml` lives). MPD containers are **not** in compose — they're created lazily per room.

| Symptom | Likely cause | Fix |
|---|---|---|
| Core can't reach Postgres | Container down | `cd domovoi && docker compose up -d postgres`; health-check with `docker ps` (container `domovoi-postgres`) |
| Port conflict on 6432 | Another service (e.g. pgBouncer, another stack) already bound 6432 | Domovoi publishes Postgres on 6432 precisely to avoid the default 5432 — if 6432 itself is taken, change the published port in `docker-compose.yml` and update `DATABASE_URL` to match |
| Tables missing after a fresh setup | Migrations never ran | `docker compose run --rm flyway` (prod DB) and `docker compose run --rm flyway-test` (test DB) — they exit after migrating; that's normal |
| `pytest` refuses to run / can't find `domovoi_test` | Test DB missing on a pre-existing volume | The `domovoi_test` DB is only auto-created on a *fresh* Postgres volume; on an existing one create it manually, then `docker compose run --rm flyway-test`. The test suite hard-refuses any non-`_test` database by design |
| "Double-check" always says it can't search | SearXNG container not running | `docker compose up -d searxng` — it's localhost-only on port 6888, reachable solely from the server itself |
| MPD containers won't start after changing `MUSIC_DIR` or Docker Desktop file-sharing | Stale mounts / unshared drive | Share the drive in Docker Desktop settings; remove the `domovoi-mpd-*` containers so the provisioner recreates them with current paths |
| Chat mode won't start | Letta container not up | Chat mode is off by default; enabling `CHAT_MODE_ENABLED` assumes `docker compose up letta` and the required Ollama models (including the embedding model) are pulled |

## Which logs to check

| Log | Where | What's in it |
|---|---|---|
| Core log | The `python -m domovoi.main` console (level via `LOG_LEVEL`) | The whole voice pipeline: routing, handlers, MPD provisioning, workers. Millisecond timestamps; snapshot/health polling spam is filtered out for you |
| Per-plugin logs | `~/.domovoi/logs/plugin_<slug>.log` (5 MB × 3 rotation) | Everything a given plugin does. Also tailed live on that plugin's dashboard detail page. Crank one plugin to DEBUG without drowning the core: `LOG_LEVEL_PLUGIN_<SLUG>=DEBUG` |
| Web backend log | The `python -m web.backend.main` console (`LOG_LEVEL`) | Dashboard API, realtime listeners, plugin web pages |
| Satellite log | `journalctl -u domovoi-satellite -f` on the Pi (level via `[log]` in its config) | Wake word, capture, playback, Wi-Fi watcher, sync channels |
| MPD (per room) | `docker logs domovoi-mpd-<room>` | Library scan, stream/decoder errors for that room |
| Postgres / SearXNG / Letta | `docker logs domovoi-postgres` etc. | Infrastructure containers |

---

*Not covered here? Check the [FAQ](FAQ.md) for behavior that looks broken but is by design, or open an issue — see [Contributing](CONTRIBUTING.md).*
