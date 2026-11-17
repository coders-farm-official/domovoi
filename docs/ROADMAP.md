# Roadmap

Where Domovoi is heading, honestly and without dates. We build in public
with a small team, so plans move when we learn something; this page moves
with them. What already works is in the README's
[Status and roadmap](../README.md#status-and-roadmap), and what changed
when is in the [release notes](RELEASE_NOTES.md).

Ideas and votes are welcome on [Discord](https://discord.gg/j3H5XamSxg)
and in [Discussions](https://github.com/coders-farm-official/domovoi/discussions),
or as a [feature request](https://github.com/coders-farm-official/domovoi/issues/new/choose).

## Now

Being worked on: the next release, 1.4.0. It is already on `main`, and its
changes are listed under "1.4.0 (unreleased)" in the
[release notes](RELEASE_NOTES.md).

- **Up next history.** Look back at the songs Up next picked, and why, in
  Settings.
- **Up to five wake words per room,** as many as the room's satellite can run.
- **A room display.** A phone or tablet in room mode shows what is playing
  and the room's timers and alarms, not only listening.
- **Short videos in chat.** Attach a video of up to three minutes; the
  server watches it and answers questions about it.
- **Radio by the name you say,** and played in a room you choose.
- **Satellites that ask every house.** A new satellite asks every Domovoi
  server it finds and joins only the one you approve it on.
- **Admins signed out after an hour idle,** so a tab left open overnight
  is not still an admin in the morning.
- **Fixes from home testing:** the Windows tray no longer sticks on an
  old Setup, printing says plainly that it is for network printers, and a
  song deleted while it plays no longer leaves a room stuck on it.

## Next

Planned, not started or only begun.

- **Rooms with several satellites, and moving a satellite to another
  room.** Today a room has exactly one satellite.
- **The iPhone app.** Coming: its own track, apart from the server's
  releases, with no dates yet.
- **The Android app on F-Droid,** beside Google Play and the GitHub
  releases. The part that makes it possible is done and shipped: the app's
  release builds are reproducible, so F-Droid can check them and publish
  the signed file as it is ([how](../android/README.md#reproducible-builds)).
  The listing itself is not in F-Droid's catalog yet.
- **The Windows installer code-signed**, so one download
  turns the household gaming PC into a Domovoi server.

## Later

Wanted, not yet scheduled.

- **Guided resumes and letters.** The step-by-step flows that ask
  questions to build a resume or letter are built but held back; plain
  "write a cover letter" in Chat already works.
- **The USB stick tried on more real computers,** to take it out of
  preview (so far it has been tried in virtual machines) ([hardware validation](HARDWARE_VALIDATION.md)).
- **Old smart speakers as satellites.** Some early Echo models can be unlocked
  and reflashed with open firmware; "Add a room" could walk you through
  turning one into a Domovoi satellite.
- **An offline kit:** a server that has internet copies its models, the
  phone app and the installer to a USB drive, for an install that never
  goes online.
- **More Linux distributions,** such as Fedora and Arch, if people ask
  for them. Say so in [Discussions](https://github.com/coders-farm-official/domovoi/discussions).
- **TLS on the household network,** from the
  [hardening backlog](SECURITY_PRIVACY.md#hardening-backlog--deferred-in-v1-on-purpose)
  that v1 deferred on purpose.
