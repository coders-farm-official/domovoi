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

Being worked on.

- **Tests on GitHub Actions.** Every change checked in the open, not only
  on the maintainers' machines.
- **Signed releases with provenance.** Release commits and tags signed,
  release files published with build provenance, and servers on `stable`
  checking the signature before they update (the update verifier already
  checks wherever an `allowed_signers` file is in place:
  [signed updates](LINUX_HOST.md#signed-updates)).
- **Trust you can check.** A [security policy](../SECURITY.md) with a
  promised reply time, a [code of conduct](../CODE_OF_CONDUCT.md), and an
  [inventory of every host Domovoi can contact](INTERNET.md#egress-inventory),
  held to the code by a test, with the commands to
  [check it yourself](INTERNET.md#check-it-yourself).

## Next

Planned, not started or only begun.

- **The iPhone app.** Coming soon: its own track, apart from the server's
  releases.
- **The Android app on F-Droid,** beside Google Play and the GitHub
  releases.
- **A permissive default wake word.** The "Domovoi" word a new install
  wakes on is CC BY-NC-SA 4.0 because of its training data, so it stays
  out of this repository and can't ride in anything sold. One trained on
  permissively licensed data throughout could ship anywhere.
- **The Windows installer out of preview,** code-signed, so one download
  turns the household gaming PC into a Domovoi server.

## Later

Wanted, not yet scheduled.

- **Guided resumes and letters.** The step-by-step flows that ask
  questions to build a resume or letter are built but held back; plain
  "write a cover letter" in Chat already works.
- **The previews out of preview:** the phone's own chat, satellites on
  spare devices, and the USB stick for old computers, once they have been
  tried on more real devices ([hardware validation](HARDWARE_VALIDATION.md)).
- **TLS on the household network,** from the
  [hardening backlog](SECURITY_PRIVACY.md#hardening-backlog--deferred-in-v1-on-purpose)
  that v1 deferred on purpose.
