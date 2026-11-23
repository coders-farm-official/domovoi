# Room audio protocol (`/v1/room-audio`), version 1

Every sound in a room (music, spoken answers, timers, alarms, announcements)
plays from every speaker in the room at the same moment. The Domovoi server
runs one **room streamer** per playing room. It stamps each 20 ms chunk of
audio with the server-clock time its first sample must leave the speaker.
Each speaker device (a satellite: a Pi, a PC, a phone in a room) keeps a
model of the server clock and plays every sample at its stamped time.

This document is the contract for timed players: the satellite player
(`satellite/room_player.py`), the Android and iPhone apps. The server side
lives in `domovoi/room_playout/`; a reference client used by the tests is
`domovoi/room_playout/ref_client.py`.

Targets at the speakers: within 2 ms between Pis and PCs, within 5 ms with
phones.

## 1. Connection

```
GET ws://<server>:6370/v1/room-audio/{satellite_id}
```

* `satellite_id` is the device's id, the same id its `/v1/stream/{id}`
  socket uses (today: the room id it paired with). It must match
  `^[a-z][a-z0-9_]{0,31}$`; any other id is refused (`bad_hello`, close
  1008) before the hello is read.
* The socket is an addition to `/v1/stream`, not a replacement: the device
  keeps its stream socket for wake words, commands and control, and opens
  this one when it declares `room_audio` in its stream hello: in its
  `supports` list, or, from a Pi (whose hello carries no `supports`), as
  the boolean key `"room_audio": true`.
* Browser `Origin` headers are checked as on `/v1/stream`; devices send none.
* Text frames carry JSON control messages (UTF-8, one object per frame,
  `type` names it). Binary frames carry audio.
* Readers ignore unknown message types, unknown JSON fields and unknown
  binary kinds. That is how version 1 grows.

## 2. Units and clocks

* **Server clock**: microseconds, integer, monotonic, arbitrary epoch (it
  restarts with the server). Never wall-clock time.
* **Client clock**: the device's own monotonic clock, in microseconds (for
  example `CLOCK_MONOTONIC`, `QueryPerformanceCounter`,
  `SystemClock.elapsedRealtimeNanos`, `mach_continuous_time`). Use the same
  clock the audio API reports its output timestamps in, so no second
  conversion is needed.
* **Audio**: 48 000 Hz, 2 channels (interleaved L, R), signed 16-bit
  little-endian. One **chunk** is 960 frames (20 ms, 3 840 bytes). A mono
  speaker plays `(L + R) / 2`.
* **Timeline**: sample index `n` of a timeline plays at server time
  `t0_us + n * 1 000 000 / 48 000`. Indices only grow; a gap (nothing to
  play) is simply indices with no chunk. Compute play times from `t0_us` and
  the index in floating point, not from `play_at_us` (which is rounded).
  Every room's timeline on one server has the same `t0_us` (the server
  picks it once), so index `n` is the same moment in every room; indices
  can therefore be large from the first chunk (64-bit).

## 3. Handshake

The client sends `hello` as its first frame, within 10 s of the upgrade.

```json
{"type": "hello", "proto": 1, "satellite_id": "kitchen_2",
 "pairing_token": "<the device's pairing token>",
 "codecs": ["pcm_s16le"], "channels": 2,
 "output_latency_us": 0, "supports": ["calibrate"],
 "client": "room_player/1.5.0"}
```

| Field | Required | Meaning |
|---|---|---|
| `proto` | yes | Protocol major version. This document is `1`. |
| `satellite_id` | no | Defaults to the path segment; must equal it if sent. |
| `pairing_token` | see §4 | The same token the device presents on `/v1/stream`. |
| `codecs` | no | Codecs the client decodes, in preference order. Default `["pcm_s16le"]`. |
| `channels` | no | 1 or 2, what the device plays. Informational in v1. |
| `output_latency_us` | no | The device's own estimate of its output path beyond what its audio API reports. Informational: the value the device plays by comes from the server (`latency`, §5.5). |
| `supports` | no | Optional features. `"calibrate"`: the device takes part in speaker calibration (§11). |
| `client` | no | Free text for logs. |

The server answers `ready`, then a `timeline` (and, if the room is playing,
the chunks of the next ~500 ms at once so a late joiner is in step at once):

```json
{"type": "ready", "proto": 1, "satellite_id": "kitchen_2",
 "room_id": "kitchen", "codec": "pcm_s16le", "rate": 48000,
 "channels": 2, "chunk_frames": 960, "playout_ms": 500,
 "server_now_us": 123456789}
```

or an `error` and closes the socket:

```json
{"type": "error", "reason": "pairing_rejected" | "proto_unsupported" |
 "not_in_room" | "bad_hello", "supported": [1]}
```

`supported` is sent with `proto_unsupported`. A client that receives
`proto_unsupported` falls back to the plain HTTP stream (it cannot play in
sync; the room then uses it only when it is the room's only speaker).

## 4. Authentication

The pairing token from `/v1/stream` authenticates this socket too. The
server compares `sha256(pairing_token)` with the device's pairing row:

* token present and matching: accepted;
* token present but wrong, or no pairing row for the device: refused
  (`pairing_rejected`, close 1008);
* no token: refused (`pairing_rejected`), whatever the pairing mode. Unlike
  `/v1/stream`, this socket does not admit unpaired devices when strict
  pairing is off (`SATELLITE_PAIRING_STRICT=false`);
* this socket never pairs a device or parks it for approval. A device that
  is not paired yet pairs on `/v1/stream` first, then connects here.

The device must also belong to a room (`not_in_room` otherwise).

## 5. Server to client

### 5.1 `timeline`

```json
{"type": "timeline", "timeline_id": 7, "t0_us": 123400000,
 "rate": 48000, "channels": 2, "codec": "pcm_s16le", "chunk_frames": 960}
```

Sent after `ready`, and again whenever the room's timeline changes (the
device moved room, the streamer restarted). A new `timeline_id` replaces the
old one at once: drop buffered audio of other timelines.

### 5.2 Audio (binary)

One binary frame per chunk: a 24-byte little-endian header, then the
payload.

| Offset | Type | Field |
|---|---|---|
| 0 | u8 | `kind` = 1 (audio) |
| 1 | u8 | `codec`: 0 = `pcm_s16le`; 1 = `opus` (reserved, not sent in v1) |
| 2 | u16 | `frames` in this chunk (960 in v1) |
| 4 | u32 | `timeline_id` |
| 8 | i64 | `first_sample`: timeline index of the chunk's first frame (a multiple of 960) |
| 16 | i64 | `play_at_us`: server time the first frame plays, rounded down (redundant, for logs) |
| 24 | bytes | payload: `frames * channels * 2` bytes of PCM |

Chunks arrive about `playout_ms` (500 ms by default) before their play time,
in index order. A chunk whose play time has already passed when it arrives
is dropped and counted as `late`. Missing indices play as silence.

The server has already mixed every sound into the chunks (music ducked under
speech, timers, announcements), so the client plays what it receives.

### 5.3 Timed controls

Each control takes effect at a server time `at_us`, normally about 80 ms in
the future so every speaker acts together. A control that arrives after its
`at_us` takes effect at once.

```json
{"type": "flush", "at_us": 123999000}
```

Stop or pause: fade out over 5 ms starting at `at_us`, then discard every
buffered sample whose play time is at or after `at_us`. Chunks the server
sends later (with later play times) play normally.

```json
{"type": "gain", "at_us": 123999000, "gain": 0.25, "ramp_ms": 50}
```

Room gain (ducking): from `at_us`, ramp linearly from the current gain to
`gain` (0.0 to 1.0) over `ramp_ms`. It multiplies the device's own volume.
The gain starts at 1.0 on connect and on every new timeline.

### 5.4 `speech`

```json
{"type": "speech", "start_us": 124100000, "end_us": 126350000}
```

A spoken sound (an answer, a timer or an alarm, an announcement, a
plugin's speech) is mixed into the timeline from `start_us` to `end_us`
(server clock). Sent when the sound is queued (normally `playout_ms` before
it starts), to a speaker that joins while one is still to end, and again
after a `flush` that cut only the music (the flush itself cuts every span
at its `at_us`; the span that plays on is told again from there).

A client keeps the spans that have not ended. While one is playing or still
to come, a wake word heard by the device is a **barge-in**, exactly as over
its own reply: it sends `wake_claim` with `barge_in: true` on `/v1/stream`,
and the server stops the room's speech on every speaker (a timed `flush`),
while the music plays on. A client that ignores this message loses only
that: the server still treats a wake during the room's answer as a
barge-in.

### 5.5 `pong`

```json
{"type": "pong", "t0": 5551000, "t1": 123456001, "t2": 123456040}
```

The answer to a `ping` (§7): `t0` echoed, `t1` when the server received the
ping, `t2` when it sent the pong, both server clock.

### 5.5 `latency`

```json
{"type": "latency", "output_latency_us": 1830}
```

The device's calibrated output latency (§11): how much later than its audio
API's reported time its sound actually leaves the speaker (DAC, amplifier,
USB, OS buffers the API does not count). A device starts every connection
at 0; the server sends `latency` right after `ready` when the device has a
calibrated value in its room, whenever a calibration changes it, and with 0
when the device moves room. The device adds it in
§8 step 1. Values are relative within the room (§11.4): only the
differences between the room's speakers matter for sync.

## 6. Client to server

```json
{"type": "ping", "t0": 5551000}
```

`t0` is the client clock when the ping is sent.

```json
{"type": "stats", "offset_us": -1234.5, "rate_ppm": 12.1, "rtt_us": 2100,
 "err_us": 180, "underruns": 0, "late": 0, "corrections": 12, "jumps": 0}
```

Every 5 s while playing, all fields optional: the clock fit (§7), the mean
absolute playout error over the period, and counters since connect. The
server keeps the latest per device (the room drawer's sync state; automatic
`playout_ms` raises on underruns).

```json
{"type": "calibrate_result", "run_id": "k3x9", "step": 1, "played": false,
 "heard": true, "arrival_us": 124003210.5, "snr_db": 24.1}
```

A device's answer to a `calibrate` step (§11.2).

## 7. Clock sync

The client estimates the server clock from four-timestamp exchanges.

1. **Ping rate**: 20 per second for the first second after `ready`, then 4
   per second. The server ignores pings beyond 50 per second, and keeps
   at most 50 unsent pongs (the oldest are dropped).
2. **Timestamps**: `t0` taken just before the ping is written; `t3` taken
   the moment the pong's bytes are read from the socket, **in the socket's
   reader thread, before parsing or queueing** (a timestamp taken after an
   event loop hop carried a 0.5 ms bias in the spike).
3. **Per sample**: `rtt = (t3 - t0) - (t2 - t1)`,
   `offset = ((t1 - t0) + (t2 - t3)) / 2`, `mid = (t0 + t3) / 2`.
4. **Window**: samples whose `mid` is within the last 60 s.
5. **Keep**: the lowest-`rtt` 10% of the window (at least 4 samples), plus
   every sample whose `rtt` is within 100 us of the window's lowest.
6. **Rate**: when the kept samples span at least 10 s, fit
   `offset = a + b * (mid - tref)` by least squares (`tref` = the newest
   `mid`), clamp `b` to ±500 ppm. Otherwise `b = 0`, and step 7 uses only
   the kept samples from the last 2 s (all kept samples if none are that new).
7. **Offset**: `a` = the median over kept samples of
   `offset_i - b * (mid_i - tref)`.
8. **Map**: `server(local) = local + a + b * (local - tref)`.
9. **Converged**: after 16 samples. **Do not start playout before the fit
   has converged.** At 20 Hz this takes under a second.

Windows clients call `timeBeginPeriod(1)` while connected, or timers round to
about 15.6 ms.

## 8. Playout

The client plays timeline sample `n` so that it leaves the speaker at server
time `T(n) = t0_us + n * 1e6 / 48000`.

1. For each output buffer, take the audio API's time for its first frame
   reaching the hardware (PortAudio `outputBufferDacTime`, ALSA
   `snd_pcm_status` htstamp plus delay, `AudioTrack.getTimestamp`,
   `AVAudioTime` host time). Smooth it by fitting it against frames written
   over the last few seconds; reported times jitter by about 1.5 ms on
   WASAPI. Add `output_latency_us` (§5.5): the buffer is played as if it
   reached the hardware at `dac_time + output_latency_us`, so a device with a
   longer acoustic path plays its samples that much earlier. A calibration
   chirp (§11) is the exception: it is timed by the API's time alone.
2. Convert that local time to server time with the fit, then to the due
   index `due = (server_time - t0_us) * 48000 / 1e6`.
3. The first buffer after convergence starts at `pos = round(due)`.
4. Each buffer: `err = pos - due`, smoothed with an EMA (alpha 0.02).
   * `|err| > 50 ms` (2 400 samples): jump, `pos = round(due)`, reset the EMA.
   * EMA `> +1` sample (ahead): repeat one sample in this buffer.
   * EMA `< -1` sample (behind): drop one sample in this buffer.
   * At most one correction per buffer.
5. Missing samples play as silence. Audio that resumes less than 500 ms
   after it last played (since the last `flush` or `timeline`) counts one
   `underrun`; a longer gap is the room going quiet between sounds.
6. Apply the room gain (§5.3), then the device volume.

The spike held two clients within 1.1 ms (p95) for 6 minutes with this
scheme, a 500 ms buffer, ±50 ppm clocks and Wi-Fi-like jitter.

## 9. What plays here

Which room sounds take this stream (the server's
`domovoi/room_sound.py`):

| Sound | Path |
|---|---|
| Music, radio, podcasts, audiobooks, Sleep sounds (the room's MPD stream) | This stream, whenever a device of the room is connected here, a room with one speaker included. Devices that never connect here get today's `music_start` on `/v1/stream` (their own stream player) only when no device of the room plays room audio. |
| Spoken answers, timers and alarms, announcements, intercom, Friend House messages, plugin speech | This stream in a room with more than one live device; `/v1/stream` (no playout delay) in a room with one. |
| Wake chime or greeting | The device that heard the wake word, played by itself. |
| Drop-in call | `/v1/stream` of the one device the call is on; the room's music here stops for the call and comes back after it. |

Timing of the room's controls:

* **Pause, stop and skip** (by voice, the dashboard, a phone, a room
  screen): a `flush` at one `at_us` on every speaker; after a skip the
  timeline carries on with the next song from there. A sound mixed over the
  music (an answer) plays on: the flush cuts its `speech` span, which is
  told again.
* **A voice turn**: from the wake word the room's music is held silent with
  `gain` 0 (30 ms ramp) on every speaker while the timeline runs on (the
  song carries on, as it always did while a turn listened). It comes back
  with `gain` 1 timed at the first sample of a synced answer (the streamer
  mixes the music down under speech), or at the turn's end.
* **Rooms sharing a queue, or a group**: the server decodes the host
  room's stream once and every following room's timeline carries the same
  samples at the same indices, so every speaker of every room in the group
  plays the same sample at the same server time. Pause, skip and stop on
  the host's player are one `flush` time in all of them; a resume starts
  them at one index. A room joining mid-song starts at the song's current
  place (about 40 ms after the join, not a full playout buffer later); a
  room joining or leaving while its own music plays gets a `flush` and
  carries on with the new music from there. Each room still has its own
  timeline (`timeline_id`): an answer, a timer, a wake word's `gain` hold
  in one room are mixed into or sent to that room's speakers only. A
  grouped room whose devices don't play room audio keeps today's
  `music_start` (its own stream player, not in step with the others).
* **Volume**: the `/v1/stream` frame `set_volume` goes to every device of
  the room, with `at_us` (server clock) for devices that play room audio;
  they apply it when their clock model reads that time (at most one second
  ahead), so the speakers change together.

## 10. Codec

Version 1 sends PCM only: 1.5 Mbit/s per speaker. That fits home Wi-Fi for a
room's few speakers, needs no native decoder on any device, and adds no codec
delay. `opus` (libopus, BSD-3; one 20 ms packet of 48 kHz stereo per audio
frame, `codec` = 1) is reserved for when measurements on real Wi-Fi call for
it; a server sends it only to clients that list `"opus"` in `codecs`.

## 11. Closing

| Code | Meaning |
|---|---|
| 1000 | Normal close. |
| 1001 | The server is going away, or the device moved room: reconnect. |
| 1008 | Refused (`error` frame first): pairing, protocol or hello. |
| 1011 | The client was too slow to take its audio (send queue full): reconnect. |

A client reconnects with backoff (1 s, doubling to 30 s) and plays the plain
HTTP stream meanwhile only if it is the room's only speaker.

## 11. Speaker calibration

Audio APIs report their own buffer, not the whole path to the air. The
room measures the rest with its microphones: one speaker plays a chirp at a
stamped time while every device in the room records, then the next speaker
plays, and so on. The server solves each device's output latency and sends
it back as `latency` (§5.5). It also stores it
(`satellites.output_latency_us`, `calibrated_at`), and a move clears it.

Only devices that list `"calibrate"` in their hello's `supports` take part.
A room with one such device has nothing to calibrate against and is skipped.

### 11.1 `calibrate` (server to client)

```json
{"type": "calibrate", "run_id": "k3x9", "step": 1, "at_us": 123999000,
 "play": true, "listen_ms": 400,
 "chirp": {"f0_hz": 1000, "f1_hz": 6000, "ms": 80, "level": 0.3}}
```

Every taking-part device of the room gets each step; `play` is true for
exactly one of them. Steps come one at a time, at least `listen_ms` plus
1 s apart, with `at_us` about 1 s ahead.

* **Play** (`play: true`): the chirp's first sample reaches the hardware at
  server time `at_us`, by the audio API's own time **without**
  `output_latency_us` (the measurement must not include the value it
  measures). It is mixed over whatever plays; the server only calibrates a
  room that is quiet.
* **The chirp**: a linear sweep from `f0_hz` to `f1_hz` over `ms`
  milliseconds, `x(t) = level * sin(2 pi (f0 t + (f1 - f0) t^2 / (2 T)))`
  for `0 <= t < T`, with 5 ms raised-cosine fades at both ends, the same on
  both channels. Each device builds it itself at its own rate: nothing
  but these numbers travels.
* **Record** (every device, the player too): the microphone from
  `at_us - 100 ms` to `at_us + listen_ms`, each captured sample stamped
  with the server time it reached the microphone (the API's capture time
  through the clock fit, §7, fitted against samples captured like §8's
  output times).
* **Find**: cross-correlate the recording with the chirp, take the
  envelope (the magnitude of the analytic signal), and pick the earliest
  peak within 6 dB of the highest (a reflection can be louder than the
  direct path, never earlier). Refine to a fraction of a sample with a
  parabola through the peak and its neighbours.
* **Heard**: the peak must stand at least 12 dB over the recording's
  median envelope; otherwise the chirp was not heard.

### 11.2 `calibrate_result` (client to server)

One per step, within 2 s of the end of the listening window:

| Field | Meaning |
|---|---|
| `run_id`, `step` | Echoed. |
| `played` | This device played the chirp (only when asked to). |
| `heard` | The chirp was found in this device's recording. |
| `arrival_us` | With `heard`: server time the chirp's first sample reached this device's microphone, by its capture API's time. |
| `snr_db` | With `heard`: the peak over the median envelope. |
| `reason` | Without `heard`: `not_heard` (too far, too noisy), `no_mic`, `no_clock` (the fit has not converged), `busy` (the speaker or mic could not be opened). |

A device that does not answer in time counts as not heard.

### 11.3 The solve

For player `i` and listener `j`, the server takes
`m(i, j) = arrival_us(j) - at_us`. With `L` a device's output latency, `M`
its input latency (the microphone path its capture API does not count) and
`d(i, j)` the time sound takes between the two:

```
m(i, j) = L(i) + d(i, j) + M(j)        m(j, i) = L(j) + d(i, j) + M(i)
m(i, i) = L(i) + M(i)                  (the device hearing itself, d ~ 0)
```

Played both ways, the distance cancels; with the self-recordings, so does
the input latency: `L(i) - L(j) = ((m(i,i) + m(i,j)) - (m(j,j) + m(j,i))) / 2`.
The server solves every measurement at once by least squares (unknowns: each
`L`, each `M`, each pair's `d`). A device that cannot hear itself (an
echo-cancelling array removes its own playback from its microphone) is
solved by assuming its input latency equals the room's others' (a weak prior
that only decides what the measurements leave open).

A device is solved when it is linked to the room's largest group by a pair
heard both ways. Others keep their previous value. A solve whose residuals
exceed 2 ms (the measurements disagree) stores nothing.

### 11.4 Values

Microphones alone cannot tell a delay all speakers share from none, so the
values are relative: the room's lowest is 0. That is all sync needs; a
constant added to every speaker of a room moves none against another.
