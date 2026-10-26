# The container guard

Domovoi runs part of itself in Docker containers: its database, the music
player for each room, the web search helper, chat mode's server, and
whatever containers its plugins run. The **container guard** is the part
of the core that knows which of those containers should be running, checks
them every 30 seconds, and says so loudly when one fails or when something
about them looks tampered with.

Two kinds of problem, two responses (the owner's choice, 2026-10-11):

* **A failure** — a container is missing, stopped, keeps restarting or is
  unhealthy, runs an older image of its own repository than the one
  Domovoi pins now (it needs recreating), or Docker itself doesn't
  answer. The guard alerts, and
  everything that still works keeps working: timers, voice, music in the
  other rooms. The incident resolves by itself when the container comes
  back.
* **Signs of tampering** — a container runs an image from another
  repository than the one Domovoi pinned, an image that didn't come from
  the registry, or a music player image Domovoi didn't build; an unknown
  container is running under
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

The pins come from the **committed** compose file — `git show
HEAD:domovoi/docker-compose.yml` in the checkout — as the guard first read
it after the core started and at HEAD now (a pull moves HEAD before the
update unit recreates the containers, so a digest from either is
accepted). An uncommitted edit of the file is ignored: changing a pin on
disk and running `docker compose up` doesn't make another image look
right. A checkout that isn't a git clone (or a host without `git`) has
only the file on disk, and that is what is read.

Every running compose container is compared to its service's pin:

* **the pinned digest** — fine;
* **an older image of the same repository** that came from the registry
  (Docker holds a registry digest of that repository for it) — a
  **failure**, amber whatever the service: "… is running an older
  postgres image than the one Domovoi pins: recreate it", with the command
  (`docker compose up -d <service>` from `domovoi/`). This is what a pin
  the container hasn't followed yet looks like: a host without the update
  unit after a `git pull`, Letta from before the last pin bump. It
  resolves by itself once the container runs the pin;
* **another repository**, or an image with **no registry provenance**
  (built or retagged locally under the pinned name) — **tampering**.

A service that isn't expected right now (SearXNG under No, Letta with chat
mode off, the Flyway containers) is still **Domovoi's**: it is never
reported missing and never "unknown" — but if it runs, its image is
compared to its pin the same way, and it is checked for the security
settings below. So is a one-off `docker compose run` of a service (it
carries the service's label under another name). While the update unit's
`domovoi-searxng-start` step runs (it may be pulling the pinned image),
SearXNG is held.

### The music players

One container per room in `mpd_rooms`, named with
`MPD_CONTAINER_PREFIX` (`domovoi-mpd-<room>`), expected to run — the core
starts every known room at boot and the containers restart by themselves.
Each must run the music player image **Domovoi built**: when the core
builds `domovoi-mpd:latest` it records the image ID
(`container_guard_baselines`), and compares every room's container — and
the tag itself — to that ID on every check. The first time the guard sees
an image (the first boot with the guard), it adopts it.

Three things build that image, and each one says which image it built,
in the same operation, before any room's player can be made from it:

| Who builds it | When | What vouches for the image |
|---|---|---|
| **The core** | at its start when the tag names no image, and the admin's **rebuild the music player image** on the Containers page | the core records the new ID as the baseline itself, right after the build and before any room's player is created or moved onto it |
| **Domovoi's installer** (`scripts/linux/provision.sh`: the Windows Setup, a repair included, or a Linux install) | when the tag names no image, or when it built the image the tag names and `Dockerfile.mpd` changed since | `/var/lib/domovoi-provision/mpd-image.json` (`by: provision`, `image_id`, `tag`, `dockerfile_sha256`, `built_at`), written right after the build, before the rooms' players still on the old image are removed and a running core is restarted; a build it can't verify (below) leaves no record, with a warning |
| **The update unit** (`scripts/linux/apply-update.sh`, step 5 `rebuild-mpd`, and the same step in a rollback) | when an update changes `Dockerfile.mpd` or `mpd.conf` | `/var/lib/domovoi-update/mpd-image.json` (`by: update`, `image_id`, `tag`, `built_at`, `run_started_at`, `to_sha`), removed before each build and written right after it while the core and web are stopped, before the rooms' players are removed and the core starts again (since 2026-10-25); a build it can't verify leaves none, and the run's `last-result.json` says which (`mpd_record`: `recorded`, `unconfirmed`, or `null` for no build) |

The two records count only while the file and its directory are root's
alone (root-owned, writable by nobody else, not a link), and each vouches
for exactly the image it names, built after the recorded one was
recorded. Neither path is a setting: `domovoi/.env` belongs to the
service user. The build runs as the service user, its progress in the
journal (the update) or Setup's log (the installer), with `--iidfile` in a
directory the service user makes for itself; root reads the ID from it
(a regular file, not a link, at most 100 bytes) and records it only when
root's own `docker image inspect` of the tag (an empty `DOCKER_CONFIG`,
no `DOCKER_HOST` or `DOCKER_CONTEXT`) agrees: the tag's image ID, or on
Docker's containerd image store its config digest (what an iidfile holds
there). The ID recorded is the tag's image ID, what the guard compares.

**While the core runs**, every check first looks at the tag: when it
names another image than the recorded one and a record names exactly that
image, the guard takes it on as the baseline there and then, before any
rule runs ("rebuilt by Domovoi's installer" or "rebuilt by the update
unit" on Settings → Health). So a room set up right after a rebuild — its
player created from the new tag before anything restarts the core — is
not reported. The image it replaced is kept as Domovoi's **previous
build** (`mpd_image_id_previous`) while a room's player still runs it:
such a player is waiting to be recreated, not tampering (the tag itself
must name the current one). Once no room's player runs it, the guard
forgets it, and a room on it is reported like any other image. An
incident already open about exactly a vouched image (one that opened
before the record could be read) is **not** closed: anyone in the
`docker` group can write a root-owned file through a container
(docs/SECURITY_PRIVACY.md), so lifting the freeze stays the admin's call.
It gets a sentence instead — "Domovoi's update recorded building this
image (…) in its root-owned record; check that nobody built it by hand,
then clear." — and stays open until the admin clears it.

**At the core's start**, when the tag names another image than the one
recorded, the same two records are asked first, then the update unit's
record of its last run (`UPDATE_RESULT_FILE`,
`/var/lib/domovoi-update/last-result.json`, written by root):

* it says the run rebuilt the music player (`mpd_changed`) and either
  **finished** after the recorded image was recorded, or is **the run
  under way that started this core** (status `running`, and its `to_sha`
  is the code now running — or its `from_sha`, for a rollback), and that
  run recorded no image of its own (an update script from before
  2026-10-25, or one that couldn't read the ID it built, which it says in
  its log): the new image is adopted quietly. A run that recorded the image
  it built vouches for that image alone;
* the update unit is installed but its record is **missing or can't be
  read**: the new image is adopted **unverified** — no incident, a
  warning in the log, the line "music player image at this start: …
  adopted unverified" on Settings → Health, and the reason in
  `container_guard_baselines.source`;
* anything else — a host without the update unit (nothing but the core
  builds the image there), a readable update record that doesn't vouch for
  a rebuild, or a record that names another image (a `docker build` by
  hand after Domovoi's) or an older build: reported as tampering (T1).

**Anything else stays tampering.** Someone with Docker access running
`docker build -t domovoi-mpd:latest` (or `docker tag`) makes an image no
record names: the tag and every room's player made from it are reported,
whether the core is running or not, and whatever the update unit's last
run said.

**Clearing the tag's incident keeps that image.** When an admin clears,
after review, the music player tag's own incident ("The music player
image was replaced outside Domovoi") and the tag still names that image
at that moment, the clear makes it the baseline ("kept by an admin", the
clear's answer says `kept_image`), so the next room set up from it isn't
reported. Once, at the clear, and nowhere else: clearing a room's
incident covers only that room's player, and an image the tag no longer
names is not kept. Any other image is news again.

**What the incident says.** When a music player image incident opens and
the update unit's own root-owned result says its last run rebuilt the
music player (`mpd_changed: true`) without recording which image it built
(an update script from before 2026-10-25), finishing after the image the
guard expected was recorded (or still going), and no record names another
image built since, the incident adds: "Domovoi's update at <time> rebuilt
the music player image without recording which image it built, so this
may be that rebuild: check that nobody built or tagged domovoi-mpd:latest
by hand …, then clear." When the run withheld its record because the ID
its build wrote and root's look at the tag didn't agree (its result says
`mpd_record: "unconfirmed"`), the sentence starts "Domovoi's update at
<time> couldn't confirm which image it built", with the same advice. It
never says the image is safe, and it never relies on what
Docker says about when the image was made (`.Created` is whatever the
builder chose). Any other incident gets only the plain words. To check:
`journalctl -u domovoi-update` shows what the update unit ran and when,
and the shell history and `docker events` of the people with Docker
access show anything else.

Until 2026-10-21 the installer rebuilt the image on every re-run of its
images step (a repair by Setup after any change to the compose file) and
recorded nothing, so a repair followed by an update that didn't touch the
music player opened "The music player image was replaced outside
Domovoi". A rebuild that misses Docker's build cache gets a new image ID
even from an unchanged `Dockerfile.mpd`: the base image is pinned by
digest, but the apt layer is not reproducible. Until 2026-10-25 the
update unit recorded only *that* it rebuilt the image (`mpd_changed`), the
guard asked only at the core's start, and a vouched image found while the
core ran was held until the next restart: a room set up in between had its
player reported as "a different image" (2026-10-25, incident #5 on a
Windows install), and clearing that incident left the next new room to be
reported again.

The unverified case opens no hole the guard claimed to close: the record
sits in a root-owned directory, so only root can remove or spoil it, and
replacing the image takes Docker access, which is root-equivalent — the
host's administrator, whom the guard never claims to stop
(docs/SECURITY_PRIVACY.md). It covers only the image found at start; from
then on the tag and every room's container are compared to it on every
check, so a swap while the core runs is still tampering. A room being
started, warmed or retired is held (below).

### Plugins

Every **loaded** plugin's declared containers (`[[containers]]` in its
manifest, SDK 1.5 — see docs/PLUGIN_DEVELOPMENT.md), as the plugin
reports them right now: their name and image, and whether each should be
running, is stopped on purpose, or isn't managed at all (the household
runs that server itself). A **disabled** plugin's containers (or one that
failed to load) are known, not expected — disabling Jellyfin leaves its
server running on purpose — but one that runs must run the declared
repository, at any tag (the plugin chose the tag while it was enabled):
another repository is tampering.

A plugin upgraded and **waiting for a restart** is held until the restart:
its new manifest is already recorded while its old code still runs the old
container. A plugin that says it is **busy** (recreating its container) is
believed for 30 minutes, and past that only while the image it is moving
to is still being pulled (not on the host yet), up to 6 hours in all. A plugin whose resolver **keeps
raising** holds its containers for 30 minutes; after that they are shown
as "not watched · resolver failing" on Settings → Health and only their
repository and settings are checked, until the resolver answers again.

A plugin that pins its image by digest gets the digest check. A plugin
that pins only a tag (`jellyfin/jellyfin:latest`) gets a weaker one: the
container must have been created from that repository and tag. A
different image retagged under the same name isn't detected — pin a
digest to have that checked. Either way, a container on another image of
the same repository that came from the registry is "outdated" (a
failure: recreate it), and another repository is tampering — the same
split as the compose services.

A plugin that needs Docker but declares no containers (older versions of
Jellyfin, RomM and Kiwix) keeps working and **isn't watched**; its card on
the Plugins page says "containers not watched".

### Not watched

* Containers of other projects on the same Docker host.
* Containers that merely look like Domovoi's (`domovoi-…`) but that
  nothing claims — a test harness's, an old plugin's, a retired service's.
  The guard lists them on Settings → Health as "unrecognized" and never
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
  command isn't installed at all (Settings → Health says so).
* **F2 missing** — an expected container doesn't exist.
* **F3 stopped** — it exists but isn't running (exited, created, dead,
  paused), with its exit code.
* **F4 restarting** — Docker is restarting it, or restarted it three or
  more times in the last ten minutes (a crash loop).
* **F5 unhealthy** — it runs and its health check says unhealthy.
* **F6 one-shot failed** — a one-shot (Flyway) exited non-zero.
* **F7 outdated** — a compose service's or plugin's container runs
  another image of its own repository, from the registry, than its pin
  (above): amber, "recreate it".

Tampering:

* **T1 image** — a Domovoi container runs an image from another
  repository than its pin, or one without registry provenance, or the
  music player image tag points at an image Domovoi didn't build (or
  vouch for: above). Every
  running Domovoi container is checked, expected right now or not: the
  compose services (above), a disabled plugin's (its repository only), a
  one-off run of a compose service (its service's pin). An image whose
  details Docker can't give this check (`docker image inspect` failed) is
  not judged that check.
* **T2 unknown** — a **running** container in Domovoi's own namespace that
  nothing claims: one carrying the compose project's label
  (`com.docker.compose.project=domovoi`) with a service the compose file
  doesn't have, or one named with the music player prefix that no room
  owns. (A removed room's player left running on the right image is only
  listed as unrecognized.)
* **T3 config** — one of Domovoi's containers running privileged, on the
  host's network or process namespace, or with the Docker socket or the
  host's whole filesystem mounted, when its declaration allows none of
  that (no core container does). "The whole filesystem" is `/`, or the
  system drive's root on Windows (`C:\`, as Docker Desktop shows it):
  another drive's root (`MUSIC_DIR=E:\`) is a music folder like any
  other.

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
  and 5 minutes after (its own watchdog recreates its containers then),
  and while its upgrade waits for a restart; a plugin's container while
  the plugin says it is recreating it (believed for 30 minutes, longer
  only while its new image is still being pulled, 6 hours at most); the
  web search helper while
  it is started or stopped, and 2 minutes after, and while the update
  unit's `domovoi-searxng-start` runs.
* **A failure** opens only after it was seen on **two checks in a row and
  has lasted 2 minutes** (`CONTAINER_GUARD_FAILURE_GRACE_SEC`): a `docker
  restart`, or a crash that `restart: unless-stopped` heals in seconds,
  never pages anyone. Docker itself not answering needs **5 minutes**
  (`CONTAINER_GUARD_DAEMON_GRACE_SEC`): Docker restarting (Docker Desktop, or
  the engine in the Windows install's distro) is not an outage.
* **Tampering** opens after two checks in a row at least 25 seconds apart
  (`CONTAINER_GUARD_TAMPER_CONFIRM_SEC`).
* A failure **resolves** after two clean checks in a row, or at once when
  the container stops being expected (its plugin was disabled, its room
  removed, chat mode turned off). An "outdated" failure resolves only
  when the guard sees the container on the pin.
* **Flapping.** A failure that comes back within 15 minutes of resolving
  reopens its own incident: one incident and one notification for a
  container that keeps falling over, not one per cycle.
* The crash-loop count (F4) runs on the monotonic clock, so the wall
  clock being stepped (NTP) neither makes nor hides a loop.

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
* **Clear** (tampering, admin, after review, with an optional note; it
  waits for first-run setup, `501` before an admin password exists, since
  lifting the freeze is a security decision): the incident closes, and when no tamper incident is open the freeze lifts.
  Both are recorded. The cleared finding stays **accepted** — it doesn't
  reopen while it persists, however long the box was off in between —
  until the guard itself looks and sees it gone; if it comes back after
  that, it is news again. Anything different — another image, another
  container — opens a new incident. A tamper incident never resolves by
  itself, even when the container is put right: someone has to look.

**Retention.** Open incidents and accepted clears are kept whatever their
age; resolved incidents are kept 90 days, then pruned (once a day).

## The freeze

While any tamper incident is open (`domovoi/container_guard/freeze.py`),
these refuse with **HTTP 423** and a message naming the incident:

* plugin installs (staging and confirming), upgrades (staging and
  confirming), and enabling a plugin;
* satellite code upgrades (`/v1/admin/satellite/upgrade`), and the
  satellite code and plugin payload channels the upgrades download from
  (so an upgrade started any other way gets nothing to install);
* satellite media preparation, and its download cache refresh;
* handing out the Android app: the APKs phones download from the join page
  (`/v1/android-app/files/*`), and the admin's fetch and upload that fill
  that cache;
* pulling a Domovoi update, switching the update channel (stable or main:
  what every later pull follows), and a restart that would load code that isn't
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

## When Docker or the database is down

On a compose install the incidents live in `domovoi-postgres` — a
container of the same Docker. So when Docker stops, or the database
container does, the guard sees it but **can't record it**: no incident,
no banner, no phone notification. What shows instead is Home's problem
row "the database isn't answering · nothing new can be saved" (the
dashboard and the Android app, from the open health read, for everyone).
Once the database answers again, a failure still there opens its incident
as usual. **F1 (Docker down) and the database container's own failure
rows can fire only where the database doesn't live in that Docker** (a
host Postgres with the music players in Docker, say).

## Where it shows

* **The dashboard banner**, on every page while an incident is open and
  not hidden: red for tampering and for a required container's failure,
  amber for an optional one's (SearXNG, Letta, Flyway, a plugin container
  declared `required = false`). It says what failed in plain words and
  links to the details. An admin can acknowledge the failures it shows
  from the banner itself. A browser with no household credential, and a
  shared screen, see only "Something needs an admin's attention."
* **The Containers page** (`#settings/health/containers`): what the freeze holds, the open
  incidents and the last day's resolved ones — open first, at most 50 rows
  in all; for an admin also what was expected and found, acknowledge,
  clear-after-review, and the guard's last check — what it watches, what
  it is waiting for and why, what it couldn't place, which plugins aren't
  watched, and "check now". A browser with no household credential and a
  shared screen (an admin signed in on it included) get only the guest
  line, as on the banner.
* **Home's problems row**, linking to Settings → Health.
* **Android**: a notification on every paired phone when an incident
  opens — one per incident, on its own "Container problems" channel,
  taken back when the incident resolves or is cleared — the same banner in
  the app, and a row in the app's Home problems; tapping any of them opens
  the details. **How soon:** while the app is on screen, at once (the live
  connection carries `containers.changed`). Off screen Android freezes the
  app and blocks its network, so the notification comes with the app's
  background sync — the same one that keeps timers ringing — about every
  15 minutes, and only on Wi-Fi or Ethernet. There is no foreground
  service. The app has no admin sign-in, so acknowledging and clearing
  happen on the dashboard; a shared screen shows only the guest line.

Updates are live: every incident change fires the `containers.changed`
event on the dashboard's state socket.

## Reviewing and clearing a tamper incident

Open Settings → Health, signed in as the admin. Each incident shows what
Domovoi expected and what it found. Then, on the Domovoi server (on the
Windows install, inside its private WSL2 distro: see
[WINDOWS_HOST.md](WINDOWS_HOST.md)):

1. **A different image.** `docker inspect <container>` shows the image it
   runs (`.Image`, `.Config.Image`). If you changed it yourself (an
   upgrade by hand), put the container back on the pinned image —
   `docker compose up -d <service>` from `domovoi/` recreates a compose
   service on the pin. For the music player, remove every room's
   container first (`docker rm -f domovoi-mpd-<room>` for each — the image
   can't be removed while a container uses it, and a room left running
   keeps the old image), then `docker rmi domovoi-mpd:latest`, then
   restart the core: it rebuilds the image, records it, and recreates each
   room's player from its data volume. The Containers page does all of
   that in one step: **rebuild the music player image** (admin, shown
   unless internet access is turned off, since the build downloads
   Debian's packages; refused while an update or restart is under way)
   builds it as the core does, makes it the image the guard expects, and
   restarts every room's player on it; what a room is playing stops,
   playlists and queues stay. Every room is held for the guard until the
   last one is done. The incident stays open until you clear it, and
   while it is open the freeze also blocks updates: if an update should
   come first, clear the incident, update, then rebuild (so the image the
   guard expects is the one the rooms run). Or, if you mean to keep the image,
   clear the incident with a note saying so: clearing the tag's own
   incident ("The music player image was replaced outside Domovoi")
   while the tag still names that image makes it the image the guard
   expects, and the next room set up from it isn't reported; clear each
   room's incident too. An incident that says Domovoi's update may have
   rebuilt the image is that case, once you have checked (above, "What
   the incident says"). If you didn't change it,
   treat the box as compromised: the container's image came from
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
