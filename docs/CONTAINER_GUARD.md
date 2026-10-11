# The container guard

Domovoi runs part of itself in Docker containers: its database, the music
player for each room, the web search helper, chat mode's server, and
whatever containers its plugins run. The **container guard** is the part
of the core that knows which of those containers should be running, checks
them every 30 seconds, and says so loudly when one fails or when something
about them looks tampered with.

Two kinds of problem, two responses (the owner's choice, 2026-10-11):

* **A failure** — a container is missing, stopped, keeps restarting or is
  unhealthy, or Docker itself doesn't answer. The guard alerts, and
  everything that still works keeps working: timers, voice, music in the
  other rooms. The incident resolves by itself when the container comes
  back.
* **Signs of tampering** — a container runs a different image than the one
  Domovoi pinned or built, an unknown container is running under
  Domovoi's names, or one of Domovoi's containers runs with privileged or
  host-level settings Domovoi never gives it. The guard alerts the same
  way **and freezes the risky operations** (plugin installs and updates,
  satellite upgrades, updates of Domovoi itself, below) until an admin
  reviews the incident and clears it.

Alerts go to a **banner on every dashboard page** and to an **Android
notification** on every paired phone. Nothing is spoken in the rooms and
the satellite LEDs don't change.

Code: `domovoi/container_guard/` (one module per part, listed in its
`__init__.py`), the `container_guard` core worker
(`domovoi/workers/container_guard.py`), the V024 tables, the web routes in
`web/backend/api/containers.py`, the dashboard banner and page
(`web/static/components.jsx`, `web/static/containers.jsx`) and the Android
alerts (`android/app/src/main/java/com/domovoi/app/alerts/`).

## What is watched

The guard builds an **expected set** at every check
(`domovoi/container_guard/expected.py`): which containers should exist,
in what state, running which image.

### The compose stack

When this install runs `domovoi/docker-compose.yml` — by default, when
`DATABASE_URL` points at the compose Postgres's published address
(loopback, port 6432), which is how every documented install runs;
`CONTAINER_GUARD_COMPOSE=on|off` says so outright:

| Service | Container | Expected | Alert colour |
|---|---|---|---|
| `postgres` | `domovoi-postgres` | always running, healthy | red |
| `flyway` | `domovoi-flyway` | a one-shot: exited 0, or gone. A non-zero exit since shortly before the core started is a failure | amber |
| `flyway-test` | `domovoi-flyway-test` | nothing (the test database's migrator) | — |
| `searxng` | `domovoi-searxng` | running while the internet answer is Yes or Sometimes (docs/INTERNET.md) and the core manages it (not `DOMOVOI_MANAGE_SEARXNG=0`); otherwise nothing | amber |
| `letta` | `domovoi-letta` | running while chat mode is on; otherwise nothing | amber |

Each must run the image its `image:` line pins by digest. A pull that moves
a pin changes the file before the update unit recreates the container, so
a digest from either the file the core started with or the file on disk
now is accepted.

A service that isn't expected right now (SearXNG under No, Letta with chat
mode off, the Flyway containers) is still **Domovoi's**: it is never
reported missing and never "unknown". It is still checked for the
security settings below if it runs.

### The music players

One container per room in `mpd_rooms`, named with
`MPD_CONTAINER_PREFIX` (`domovoi-mpd-<room>`), expected to run — the core
starts every known room at boot and the containers restart by themselves.
Each must run the music player image **the core built**: when the core
builds `domovoi-mpd:latest` it records the image ID
(`container_guard_baselines`), and compares every room's container — and
the tag itself — to that ID. The first time the guard sees an image it
didn't build (the first boot with the guard), it adopts it; when the
update unit rebuilt it (its last run says `mpd_changed`), it adopts the
new one. A room being started, warmed or retired is held (below).

### Plugins

Every **loaded** plugin's declared containers (`[[containers]]` in its
manifest, SDK 1.5 — see docs/PLUGIN_DEVELOPMENT.md), as the plugin
reports them right now: their name and image, and whether each should be
running, is stopped on purpose, or isn't managed at all (the household
runs that server itself). A **disabled** plugin's containers are known,
not expected — disabling Jellyfin leaves its server running on purpose.

A plugin that pins its image by digest gets the digest check. A plugin
that pins only a tag (`jellyfin/jellyfin:latest`) gets a weaker one: the
container must have been created from that repository and tag. A
different image retagged under the same name isn't detected — pin a
digest to have that checked.

A plugin that needs Docker but declares no containers (older versions of
Jellyfin, RomM and Kiwix) keeps working and **isn't watched**; its card on
the Plugins page says "containers not watched".

### Not watched

* Containers of other projects on the same Docker host.
* Containers that merely look like Domovoi's (`domovoi-…`) but that
  nothing claims — a test harness's, an old plugin's, a retired service's.
  The guard lists them on the Containers page as "unrecognized" and never
  opens an incident for them: on a shared or development host there are
  always some, and an alarm that always rings is no alarm.
* Anything at all while `CONTAINER_GUARD_ENABLED=false`.

## The rules

Each rule is a function in `domovoi/container_guard/detect.py`; the names
are the ones its tests use.

Failures:

* **F1 daemon** — Docker doesn't answer (`docker ps` fails or times out)
  while something is expected to run. One incident, not one per container;
  while Docker is away no other rule runs. Not raised when the `docker`
  command isn't installed at all (the Containers page says so).
* **F2 missing** — an expected container doesn't exist.
* **F3 stopped** — it exists but isn't running (exited, created, dead,
  paused), with its exit code.
* **F4 restarting** — Docker is restarting it, or restarted it three or
  more times in the last ten minutes (a crash loop).
* **F5 unhealthy** — it runs and its health check says unhealthy.
* **F6 one-shot failed** — a one-shot (Flyway) exited non-zero.

Tampering:

* **T1 image** — an expected container runs a different image (above), or
  the music player image tag points at an image the core didn't build.
* **T2 unknown** — a **running** container in Domovoi's own namespace that
  nothing claims: one carrying the compose project's label
  (`com.docker.compose.project=domovoi`) with a service the compose file
  doesn't have, or one named with the music player prefix that no room
  owns. (A removed room's player left running on the right image is only
  listed as unrecognized.)
* **T3 config** — one of Domovoi's containers running privileged, on the
  host's network or process namespace, or with the Docker socket or the
  host's whole filesystem mounted, when its declaration allows none of
  that (no core container does).

## No false alarms

False alarms would make people ignore the banner, so the guard is
conservative about what counts and loud about what does
(`domovoi/container_guard/engine.py`):

* **Boot.** Nothing is judged in the first 3 minutes after the core starts
  (`CONTAINER_GUARD_BOOT_GRACE_SEC`): a reboot or the update unit starts
  everything at once.
* **Updates and restarts.** Nothing is judged while a restart or an update
  is under way (the update unit stops and starts every service).
* **Docker coming back.** For 2 minutes after Docker answers again its
  containers are left to come back up by themselves.
* **Work in progress** (`domovoi/container_guard/holds.py`): a room's music
  player while it is started, warmed or removed, and a minute after; a
  plugin while it is installed, upgraded, enabled, disabled or uninstalled,
  and 5 minutes after (its own watchdog recreates its containers then); a
  plugin's container while the plugin says it is recreating it (believed
  for 30 minutes at most); the web search helper while it is started or
  stopped, and 2 minutes after.
* **A failure** opens only after it was seen on **two checks in a row and
  has lasted 2 minutes** (`CONTAINER_GUARD_FAILURE_GRACE_SEC`): a `docker
  restart`, or a crash that `restart: unless-stopped` heals in seconds,
  never pages anyone. Docker itself not answering needs **5 minutes**
  (`CONTAINER_GUARD_DAEMON_GRACE_SEC`): Docker Desktop restarting is not an
  outage.
* **Tampering** opens after two checks in a row at least 25 seconds apart
  (`CONTAINER_GUARD_TAMPER_CONFIRM_SEC`).
* A failure **resolves** after two clean checks in a row, or at once when
  the container stops being expected (its plugin was disabled, its room
  removed, chat mode turned off).

When Docker doesn't answer the guard backs off (doubling, at most five
minutes between looks). A check costs at most four short `docker` calls
whatever else runs on the host, so it is cheap on a Pi-class box.

## Incidents

Each distinct problem is one row in `container_incidents` (V024): its
class, kind, container, what was expected and what was found, when it
opened, was last seen and was resolved, and who acknowledged or cleared it
and when. One open incident per problem: a failure is one per container
(if a stopped container is then removed, the same incident says so, and
an acknowledgement of the old problem no longer hides it); a tamper
finding is one per container **and** image or setting.

* **Acknowledge** (a failure, admin): the banner hides it until something
  new happens. It still resolves by itself.
* **Clear** (tampering, admin, after review, with an optional note): the
  incident closes, and when no tamper incident is open the freeze lifts.
  Both are recorded. If the condition is still there it stays **accepted**
  and doesn't reopen; anything different — another image, another
  container — opens a new incident. A tamper incident never resolves by
  itself, even when the container is put right: someone has to look.

## The freeze

While any tamper incident is open (`domovoi/container_guard/freeze.py`),
these refuse with **HTTP 423** and a message naming the incident:

* plugin installs (staging and confirming), upgrades (staging and
  confirming), and enabling a plugin;
* satellite code upgrades (`/v1/admin/satellite/upgrade`), and the
  satellite code and plugin payload channels the upgrades download from
  (so an upgrade started any other way gets nothing to install);
* satellite media preparation, and its download cache refresh;
* pulling a Domovoi update, and a restart that would load code that isn't
  running yet (pulled code, or a staged plugin upgrade — on a Linux host
  with the update unit, that restart is the unit's trigger).

Everything else keeps working, including disabling or uninstalling a
plugin and a plain restart with nothing new to load. The refusal comes
after the route's own credential check, so a caller without one learns
nothing from it; the satellite channels, which take no credential, only
say "Paused by Domovoi's container guard until an admin reviews a
problem."

The freeze lives in the database, so it survives a restart, and both the
core and the web process enforce it. A root user on the host can still
run `sudo systemctl start domovoi-update.service` by hand, or `docker` by
hand: the guard can't stop the host's administrator (see
docs/SECURITY_PRIVACY.md).

## Where it shows

* **The dashboard banner**, on every page while an incident is open and
  not hidden: red for tampering and for a required container's failure,
  amber for an optional one's (SearXNG, Letta, Flyway, a plugin container
  declared `required = false`). It says what failed in plain words and
  links to the details. An admin can acknowledge the failures it shows
  from the banner itself. A browser with no household credential, and a
  shared screen, see only "Something needs an admin's attention."
* **The Containers page** (`#containers`): what the freeze holds, every
  open incident and the last day's; for an admin also what was expected
  and found, acknowledge, clear-after-review, and the guard's last check —
  what it watches, what it is waiting for and why, what it couldn't place,
  which plugins aren't watched, and "check now".
* **Home's problems row**, linking to the Containers page.
* **Android**: a notification on every paired phone when an incident
  opens — one per incident, on its own "Container problems" channel,
  taken back when the incident resolves or is cleared — the same banner in
  the app, and a row in the app's Home problems; tapping any of them opens
  the details. The app
  has no admin sign-in, so acknowledging and clearing happen on the
  dashboard.

Updates are live: every incident change fires the `containers.changed`
event on the dashboard's state socket.

## Reviewing and clearing a tamper incident

Open the Containers page, signed in as the admin. Each incident shows what
Domovoi expected and what it found. Then, on the Domovoi server:

1. **A different image.** `docker inspect <container>` shows the image it
   runs (`.Image`, `.Config.Image`). If you changed it yourself (an
   upgrade by hand), put the container back on the pinned image —
   `docker compose up -d <service>` from `domovoi/` recreates a compose
   service on the pin; `docker rmi domovoi-mpd:latest` and a restart of
   the core rebuilds the music player image and records it — or, if you
   mean to keep it, clear the incident with a note saying so. If you
   didn't, treat the box as compromised: the container's image came from
   somewhere you didn't choose.
2. **An unknown container.** `docker inspect <name>` says what it runs and
   who made it. Remove it (`docker rm -f <name>`) if it isn't yours.
3. **Risky settings.** The incident names them (privileged mode, the
   host's network or processes, the Docker socket, the host's disk). No
   Domovoi container needs any of them; recreate the container
   (`docker compose up -d --force-recreate <service>`, or let the plugin
   recreate its own).

Then **clear after review**, with a note of what you checked. The freeze
lifts when no tamper incident is open.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `CONTAINER_GUARD_ENABLED` | `true` | Off: nothing is checked and nothing new opens (open incidents stay until resolved or cleared, and an open tamper incident keeps the freeze on). |
| `CONTAINER_GUARD_INTERVAL_SEC` | `30` | Seconds between checks. |
| `CONTAINER_GUARD_BOOT_GRACE_SEC` | `180` | Nothing is judged this long after the core starts. |
| `CONTAINER_GUARD_FAILURE_GRACE_SEC` | `120` | How long a failure must last (and two checks) before it alerts. |
| `CONTAINER_GUARD_DAEMON_GRACE_SEC` | `300` | The same for Docker itself not answering. |
| `CONTAINER_GUARD_TAMPER_CONFIRM_SEC` | `25` | Tampering must be seen on two checks at least this far apart. |
| `CONTAINER_GUARD_COMPOSE` | `auto` | Whether this install runs the compose stack: `auto` (the database is the compose Postgres on loopback port 6432), `on`, `off`. |

## Plugin developers

Declare every container your plugin runs — docs/PLUGIN_DEVELOPMENT.md,
"Containers the plugin runs". The API surface is in docs/API_REFERENCE.md
(§2.6a, §3.9a), and what the guard can and cannot see in
docs/SECURITY_PRIVACY.md ("The container guard").
