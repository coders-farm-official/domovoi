# Security policy

Domovoi runs in people's homes, next to their microphones, music and
documents, so we take reports seriously and answer them. Thank you for
looking.

## Supported versions

| Version | Supported |
|---|---|
| The latest release (what the `stable` branch points at) | Yes |
| `main` | Yes: fixes land here first |
| Older releases | No: update first (Settings → System → About & updates → **Update Domovoi**), then check whether the problem is still there |

The Android app follows the same rule: the latest version on Google Play
and on the project's GitHub releases.

## How to report a vulnerability

Please don't open a public issue, discussion or Discord post for a
security problem.

1. **GitHub private vulnerability reporting (preferred).** On the
   repository page, open the **Security** tab and choose **Report a
   vulnerability**. The report stays private between you and the
   maintainers, and we can work on the fix together in a private fork.
2. **Email:** [info@codersfarm.org](mailto:info@codersfarm.org), if you
   can't use GitHub or prefer email. Put "Domovoi security" in the subject.

### What to include

- What the problem is, and what an attacker gains (read a file, run code,
  get past the admin password, reach the server from outside the house...).
- Where the attacker has to be: on the household's network, an enrolled
  device, a guest, a plugin, a web page a household member opens, or the
  internet.
- The version (Settings → System → About & updates), the host kind
  (Linux, or the Windows install), and the part involved (core, dashboard,
  a satellite, the Android app, a plugin).
- Steps to reproduce, or a proof of concept. Short is fine.
- Logs, if they help. Look through them first: the dashboard's
  **report a problem** (Help) builds a summary with the values it knows
  blanked out, and on Windows **Save logs for support...** in the tray
  icon's menu collects the logs. Neither catches everything, so please
  remove passwords, tokens, setup codes and addresses before you send.

## What happens next

- **We reply within 7 days** to say we have the report, and tell you
  whether we can reproduce it.
- We fix it on `main`, then ship the fix in a release, and say in the
  release notes what was fixed and who should update.
- We credit you in the release notes and the advisory, by the name or
  handle you give us, unless you'd rather not be named.
- **Coordinated disclosure:** please give us up to **90 days** from your
  report before you publish details. If a fix ships sooner, we agree a
  date with you; if we need longer, we ask you and say why. We publish a
  GitHub security advisory once the fix is out.

## Scope

In scope: everything in this repository that a household runs.

- The **core** (the voice service, port 6370) and the **web dashboard**
  (port 6369).
- **Satellites**: the Raspberry Pi satellite, a spare Linux or Windows PC
  as a room, and a phone or tablet in the Android app's room mode,
  including setup, pairing and the setup codes.
- The **Windows host**: `DomovoiSetup.exe`, the DomovoiHost service, the
  tray icon, and the Windows satellite Setup.
- The **Android app**.
- The **bundled radio plugin** (`plugins/radio/`) and Coders Farm's own
  first-party plugins.
- The **update path**: releases, the update unit, signatures and the
  update verifier.
- The **iPhone app** (`ios/`): in development, not yet released. Reports
  are welcome.

Out of scope:

- **Third-party plugins.** Report those to their publishers. A way for a
  plugin to get past what Domovoi's plugin runtime promises is in scope.
- **The household's own network**: router configuration, Wi-Fi security,
  port forwarding. Domovoi is not designed to be exposed to the internet.
- Problems that need an attacker who already has the admin password, or a
  shell on the server, unless they reach further than that should allow.
- Known limits the threat model already states (below), unless you found a
  way around what it says holds.

## The threat model, briefly

Domovoi trusts the household's network: anything on the LAN can see what
the server is, and the daily features need the household **device token**
that every enrolled client presents. A **setup code** lets a new device or
satellite join. One **admin password** gates anything that runs code,
reaches a credential, or changes what the server or a satellite runs.
There is no TLS on the LAN in v1, and port forwarding 6369 or 6370 is not
supported. What leaves the house is up to the household's internet answer
(Yes, Sometimes or No), and every outbound host is listed in
[docs/INTERNET.md](docs/INTERNET.md#egress-inventory).

The full threat model, what each credential protects, the outbound-fetch
rules and the hardening deliberately deferred in v1 are in
[docs/SECURITY_PRIVACY.md](docs/SECURITY_PRIVACY.md).
