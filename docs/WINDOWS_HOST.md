# Running the Domovoi server on Windows with the installer (test build)

`DomovoiSetup.exe` puts the whole Linux server from
[LINUX_HOST.md](LINUX_HOST.md) on a Windows 11 PC: the same systemd units,
Postgres, Docker Engine, Ollama and git checkout, inside a private WSL2
distro that belongs to a hidden Windows account. Windows supplies only the
installer, one service that keeps the distro running, and firewall rules.
After setup the dashboard at `http://localhost:6369` is the only interface.

**This is a test build.** It is not code-signed (see [Unsigned](#unsigned)),
it installs a pinned commit of `main`, and some of the finished product is
not in it yet (see [Not in this build](#not-in-this-build)). The native
Windows install in the
[README](../README.md) and [SETUP_RUNBOOK.md](SETUP_RUNBOOK.md) is a
different thing and is unchanged.

Read alongside: [Running the server on Linux](LINUX_HOST.md) (what runs
inside the distro) · [Internet or not](INTERNET.md) (the question Setup
asks) · [Security & privacy](SECURITY_PRIVACY.md)

---

## Which PCs

| | Supported | Notes |
|---|---|---|
| Windows | 11 22H2 or later (build 22621+), Home or Pro, x64 | Not Windows 10, not Arm64: mirrored networking and the Hyper-V firewall need 22H2. |
| Memory | 16 GB or more | Below 24 GB the distro is capped at 10 GB and gets the smaller models. From about 32 GB it gets the standard models (as on a Linux box with that much memory). |
| Processor | Firmware virtualization on (Intel VT-x / AMD-V) | AVX2 is checked and only warned about. |
| Disk | 40 GB free on the system drive | An SSD is strongly recommended; Setup warns on a spinning disk. |
| GPU | Optional | Ollama uses an NVIDIA GPU that WSL passes through; without one it runs on the CPU. Whisper always runs on the CPU. |
| Internet | Needed during setup (about 10-13 GB, most of it the AI models) | Afterwards Domovoi works with or without it, as you answer in Setup. |
| Docker Desktop, Ollama for Windows, your own WSL distros | Fine alongside | Setup checks the ports Domovoi needs, not which programs you run. A program holding one of them gets a pop-up naming it, with Retry and Cancel. |
| Smart App Control | Must be off | It blocks unsigned programs, and this build is unsigned. See [Unsigned](#unsigned). |

## Unsigned

Code signing is on hold, so `DomovoiSetup.exe`, `DomovoiSetupHelper.exe`
and `DomovoiHost.exe` carry no signature. Two Windows features react to
that:

- **SmartScreen** warns when you start Setup with a blue window,
  "Windows protected your PC". Click **More info**, check the publisher line says
  "Unknown publisher" and the file is the `DomovoiSetup.exe` you meant to
  run, then **Run anyway**. Run it only if you got it from someone you
  trust, or built it yourself (see [Building it](#building-it)).
- **Smart App Control** blocks unsigned programs outright, with no "run
  anyway". With it on, Windows won't start Setup at all; if it is in
  evaluation mode, Setup runs, but Windows may turn it on later and then
  block the Domovoi service at every start, so Domovoi stops starting. Setup
  reads its state (`HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy`,
  `VerifiedAndReputablePolicyState`: 0 off, 1 on, 2 evaluation) before
  anything else: **on**, it stops and says why; **evaluation**, it warns on
  its first page. To use this build, turn it off in **Windows Security >
  App & browser control > Smart App Control settings**. Turning it off
  means Windows no longer blocks untrusted and unsigned apps by itself
  (Microsoft Defender Antivirus and SmartScreen still run), and on many
  Windows 11 builds it can only be turned back on by resetting or
  reinstalling Windows: read what Windows Security says before you switch
  it off.

## What Setup does

1. **Checks the PC** and changes nothing: Smart App Control, Windows build,
   memory, virtualization, disk, WSL, the network, the internet, and every port
   Domovoi needs (6369, 6370, 6432, 6650-6699, 8050-8099, 8096, 7676, 9467,
   11434). A taken port gets one pop-up per program holding ports (Docker
   Desktop, Ollama, or the program's name), with **Retry** and **Cancel**.
   If an earlier uninstall left work for Windows to finish at the next
   restart (files still in use in `C:\ProgramData\Domovoi` or
   `C:\Program Files\Domovoi`, or the `DomovoiRemoveProfile` task), Setup
   asks you to restart first: Windows deletes those by path, and would take
   a new install's files of the same name with them.
2. **Lists every change it will make** (the table below) before it makes
   any.
3. **Asks whether this PC will have internet after setup** (Yes, always /
   Sometimes / No; nothing is preselected). The answer goes to
   `provision.sh --internet`; see [INTERNET.md](INTERNET.md). You can
   change it later in **Settings > Internet**.
4. **Asks for the media folder** (default `C:\Users\Public\Domovoi`), the
   Windows folder you open in Explorer and copy music, pictures, videos,
   documents and audiobooks into. It must be a folder of your own on a
   drive of this PC: Windows, Program Files, ProgramData, the Users folder,
   a profile folder itself (a folder inside it, like your Music, is fine;
   its AppData isn't), anything holding one of those, and links (junctions)
   are refused, because Domovoi's account and its Linux system can write
   there.
5. **Downloads** `provision.sh` from the pinned commit and Ubuntu 26.04.1's
   official WSL image, each checked against a pinned SHA-256
   (`windows/installer/pins.iss`).
6. **Turns on Virtual Machine Platform** and installs WSL 2.7.14 only if
   they are missing. If Windows needs a restart, Setup asks, and starts
   again by itself after you sign in.
7. **Makes the hidden account and the service**, sets the folders' access,
   adds the firewall rules, and hands the rest to the service:
   `DomovoiHost`, running as the hidden account, writes that account's
   `.wslconfig`, imports the distro, switches the distro's interop with
   Windows off (and restarts it) before anything else runs in it, and runs
   `provision.sh` inside it as root. Setup's progress bar follows
   `provision.sh`'s progress lines (about 20-40 minutes, most of it the AI
   models); **Stop Setup** stops waiting, and after four hours Setup asks
   whether to keep waiting. When `provision.sh` asks for a restart of the
   distro, the service does it and runs the script once more to confirm.
   If you stop Setup and don't remove what it did, the service is set to
   Manual and left stopped, so it doesn't carry on at every boot; running
   Setup again picks up where it stopped.

   **The ports are checked again** where it matters: the downloads take
   most of the install, and a program can take a port meanwhile (a Docker
   container that restarts, say). Right before `provision.sh` starts
   Domovoi, the service stops it there (`provision.sh --stop-before
   start`) and waits while a port Domovoi needs is taken, and Setup shows
   the same pop-up per program as its first check; once the port is free
   Domovoi starts by itself. (That needs a pinned `provision.sh` with
   `--stop-before`; with an older one the start fails instead, and Setup
   then shows the same pop-ups and carries on by itself after **Retry**.)
   Domovoi's own Linux services never show up in Windows' port table, so
   only Windows programs are ever named.

   **Every error offers Copy logs.** The logs are in
   `C:\ProgramData\Domovoi\logs`, which only administrators can open, so
   **Copy logs** (on Setup's error pop-ups, its last page after a failure,
   and the uninstaller's messages) copies them to a new folder in
   `Documents\Domovoi logs`. Setup opens that folder for you; the
   uninstaller, which runs as administrator, only names it (opening it from
   there would run as administrator too). Every line is filtered on the
   way: passwords, tokens and setup codes are blanked as far as Setup can
   recognise them.
8. **Shows your setup code** once, with a **Copy** button, and **Open
   Domovoi**. Use the code there to make the admin account; it works for
   24 hours (a restart of the service writes a fresh one). Open Domovoi
   opens `http://localhost:6369/?installed=<commit>-<build time>`: a
   browser that ran an earlier Domovoi on this PC may still have that
   dashboard's service worker, and the old ones answered from their cache
   first, by exact address, so an address no earlier build opened always
   reaches today's dashboard (whose worker then takes over at once).

## Every change on the PC, and how uninstall undoes it

| Change | Uninstall |
|---|---|
| Setup's install record: value `Install` under **`HKLM\SOFTWARE\Domovoi`** (what Setup changed, the account's SID; no secrets). Written before each change it records. | Deleted last, when an uninstall run finishes (see [Uninstalling](#uninstalling)); until then a second run still knows what to undo. |
| Local account **`domovoi-svc`**: standard user (member of Users only), a random 40-character password generated in memory and never shown, logged or stored outside Windows' own account and service databases, password never expires, user can't change it. Its comment marks it as Setup's. | Deleted (only if it carries Setup's mark or Setup recorded making it). |
| Its sign-in screen entry: DWORD `domovoi-svc = 0` in `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList` (Setup makes the `SpecialAccounts` and `UserList` keys if they are missing). | The value is deleted; the keys too if Setup made them and they are empty. |
| Account rights through LSA: `SeServiceLogonRight`, and `SeDenyInteractiveLogonRight`, `SeDenyRemoteInteractiveLogonRight`, `SeDenyNetworkLogonRight`. Run `DomovoiSetup.exe /nodenylogon=1` to leave the three deny rights out, on a first install or over an existing one (it takes back ones granted before). | Every right of the account's SID is removed, with its entry in the policy. |
| Its profile, `C:\Users\domovoi-svc`, made by Windows when the service first signs in; it holds the account's `.wslconfig` (mirrored networking, `instanceIdleTimeout=-1`, memory cap, swap in `C:\ProgramData\Domovoi\wsl`) and its WSL registration. | Deleted. Right after unregistering the distro the service ends that account's own WSL session (`wsl --shutdown` run as `domovoi-svc`, see below), then the uninstaller waits up to two minutes for Windows to let go of the profile. This comes after the account is deleted; if Windows still holds the profile then, a one-time startup task, **`DomovoiRemoveProfile`** (runs as SYSTEM), deletes it at the next restart and then deletes itself. It touches only that profile, only once its account is gone, and if it can't tell (or the profile is in use) it waits for the next boot. |
| Service **`DomovoiHost`** (Automatic, runs as `.\domovoi-svc`, depends on `WSLService`, restarts after 10, 30 and 60 s on failure), binary `C:\Program Files\Domovoi\DomovoiHost.exe`. | Stopped (it stops Domovoi's units in the distro first), then deleted. |
| WSL distro **`domovoi`** of `domovoi-svc` (Ubuntu 26.04.1 plus everything `provision.sh` installs), disk in `C:\ProgramData\Domovoi\distro`. Once imported, the service never deletes or re-imports over that disk, even if WSL stops listing it. Your own distros and `.wslconfig` are not touched. | Unregistered by the service, as its owner, first. Until that has worked (or the disk is gone), nothing else that could strand it is touched: not the service, the account, its profile or rights, nor ProgramData. If it fails, the uninstaller stops, says why, and stays installed for another try. A disk WSL no longer lists is deleted or kept aside, see [Uninstalling](#uninstalling). |
| Hyper-V firewall rules for WSL's VM creator (`{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}`), inbound TCP from your subnet only: `Domovoi-Dashboard-6369`, `Domovoi-Core-6370`, `Domovoi-MPD-8050-8099`, `Domovoi-Jellyfin-8096`, `Domovoi-RomM-7676`, `Domovoi-Kiwix-9467`. They apply to **every WSL VM on the PC**, your own WSL distros and Docker Desktop's included: anything of yours that listens on those ports in a mirrored-mode distro becomes reachable from your home network too. None if the PC has no private (RFC 1918) address. | Every `Domovoi-` rule for WSL's VM creator is removed (and listing them again must work, or the uninstaller says so). |
| `C:\ProgramData\Domovoi` (`distro`, `wsl`, `backups`, `logs`, `state`, `host`): SYSTEM and Administrators only, with `domovoi-svc` allowed where it needs it. Household accounts can't open it. Setup only uses this folder if it made it. | Made administrators-only again, then deleted, only if Setup made it (files still in use are deleted at the next restart). |
| The media folder and its `Music`, `Pictures`, `Videos`, `Documents`, `Audiobooks`: made if missing; `domovoi-svc` may read and write them (an inherited rule); a root folder Setup makes also lets every user of the PC (Users) read and write. An existing folder keeps all its own rules. | Setup's rule for `domovoi-svc` comes off. A folder Setup made is removed **only if it is empty**. No file in a media folder is ever deleted. |
| `C:\Program Files\Domovoi` (`DomovoiHost.exe`, `DomovoiSetupHelper.exe`, `domovoi.ico`, the uninstaller) and its Apps entry. | Deleted, after everything above is gone. |
| Only when missing (not on a PC that already runs WSL 2.7.14 or later): the Windows feature **Virtual Machine Platform**, and **WSL 2.7.14**. Only when they need a restart: a copy of Setup in `C:\Program Files\Domovoi\resume` and a `DomovoiSetupResume` value under `HKLM\...\CurrentVersion\RunOnce` that runs it (Windows deletes the value when it runs). | Virtual Machine Platform and WSL are left in place: they are shared with anything else that uses WSL. The copy and a leftover `DomovoiSetupResume` value are deleted. |

Not changed: power and sleep settings, Windows Update, `AutoUpgradeViaMsix`,
Smart App Control, your accounts and files. Setup never touches the
household's own WSL distros or Docker Desktop, and never runs
`wsl --shutdown` as you or any other account. The uninstaller has
DomovoiHost run it once, as `domovoi-svc`, after the distro is gone: WSL
keeps one session, with its own VM, per Windows user and ends only the
caller's (WSL's source, `LxssUserSessionFactory.cpp` and
`LxssUserSession.cpp`), so it stops that account's VM alone and your own
WSL and Docker Desktop keep running.

## Uninstalling

**Settings > Apps > Installed apps > Domovoi > Uninstall.** It removes
everything in the table above, in order: stop the service, unregister the
distro as `domovoi-svc`, delete the service, end anything still running as
`domovoi-svc`, the firewall rules, Setup's media-folder rules and empty
folders, the profile, the account rights, the sign-in entry, the account,
`C:\ProgramData\Domovoi`, the install record, and the program. The window
shows each step as it happens (the status line, the bar, and a list of
what was done). Its record goes to `%TEMP%\domovoi-uninstall-<time>.log`
(that one file stays, on purpose); **Copy logs** on its messages copies it,
with Domovoi's own logs while they are still there, to
`Documents\Domovoi logs`.

It works after a half-finished setup, and running it again is safe: it
looks at what is on the PC as well as Setup's install record, and skips
what is already gone.

- **If stopping the service or unregistering the distro fails**, it stops
  before touching the account, the service or any data, says why, and
  Domovoi stays in Installed apps for another try.
- **What Windows still has open** goes at the next restart by itself,
  with nothing for you to do: files in `C:\ProgramData\Domovoi` (Windows'
  delete-at-restart list) and the account's profile (the one-time
  `DomovoiRemoveProfile` task). The uninstall finishes, says so, and
  removes the program.
- **If something else is left**, it depends on what:
  - Left over but harmless (nothing of Domovoi's data in it, no access
    granted once the account is gone): the service marked for deletion,
    an access rule for the deleted account's SID on a media folder, the
    sign-in screen entry, the resume entry, or the profile or files above
    when even their removal at restart could not be set up. The uninstall
    finishes, lists each one with the command or setting that removes it
    by hand, and removes the program. The service goes at the next
    restart by itself.
  - Left over and still active (the account itself, its rights, a
    program running as it, a firewall rule): the uninstall keeps Domovoi
    in Installed apps and asks you to restart Windows and uninstall again.
    The **third** run finishes whatever is left and lists it the same way,
    so Domovoi never stays in Installed apps for good.
- **The distro's disk, when WSL no longer lists the distro**
  (`C:\ProgramData\Domovoi\distro\ext4.vhdx` is there, but the service says
  there is nothing to unregister): if DomovoiHost never recorded an import
  as finished, it is a half-finished import and is deleted with
  `C:\ProgramData\Domovoi`. If it did record one, the disk holds your
  database and the server's identity, so it is neither deleted nor left in
  the way: it is moved to `C:\ProgramData\Domovoi-kept-<date>-<time>\ext4.vhdx`
  (administrators only), the uninstall carries on, and its last message
  names that folder. Delete it when you no longer need it.

This build has no "keep my data" uninstall: uninstalling deletes the
database, history, voice profiles and the server's identity, so satellites
must be paired again after a reinstall. Keep-data uninstall comes later.

To see what uninstall would do without doing it, from an administrator
prompt: `"C:\Program Files\Domovoi\DomovoiSetupHelper.exe" uninstall --dry-run`.

## Give the PC a fixed address (DHCP reservation)

Satellites remember the server's address once they are approved and don't
look for it again. If your router hands this PC a different address
later, every satellite goes offline. Setup's last page shows the PC's
address and MAC address: in your router's settings, add a **DHCP
reservation** for that MAC address, so the PC always gets the same
address. Do the same on a Linux server; it is not specific to Windows.

## The trust boundary on a shared PC

On a dedicated Linux box, "this computer" means the server alone. Here it
means everyone who signs in to this PC. With WSL's mirrored networking,
**every program of every Windows account on the PC** can reach the
services the distro keeps to itself: Postgres on 6432 (password-protected),
Ollama on 11434, and each room's MPD control port from 6650 (neither
needs a password), and the core trusts requests from `127.0.0.1` more than
requests from the network (the login throttle).

Until that is closed (planned before a Windows release: the core stops
trusting loopback on a WSL host, and Ollama and MPD move to an address
Windows can't reach), treat **every account on this PC as inside your
household's trust boundary**. Don't install this build on a PC that
people outside the household sign in to.

## When something goes wrong

Logs are in `C:\ProgramData\Domovoi\logs` (administrators only). The
**Copy logs** button on Setup's error pop-ups, on its last page after a
failure, and on the uninstaller's messages copies them to a new folder in
`Documents\Domovoi logs` (of the account that ran Setup); Setup opens it,
the uninstaller names it:

| File | What |
|---|---|
| `install.log` | Every change Setup's helper made. |
| `host.log` | DomovoiHost: the import, the holder (`wsl.exe -d domovoi --exec sleep infinity`), restarts, requests, the ports it waited for. |
| `provision.log` | Everything `provision.sh` printed, including its `@@DOMOVOI` progress lines. When the start fails it also holds `systemctl status` of `domovoi-db`, `domovoi-core` and `domovoi-web` and their last 200 journal lines, so it names the real cause (a port already taken, a migration that failed). |
| `host-status.json` | (in the copy) What DomovoiHost last reported to Setup. |

Setup's own record of the changes it made is `C:\ProgramData\Domovoi\state\install.log`
(administrators only; `domovoi-svc` can't write there).

None of them is given the account's password or the setup code: the code
travels from the distro to Setup encrypted to a one-time key Setup holds
in memory. Beyond that, passwords, tokens and setup codes are blanked as
far as Setup can recognise them, once when a line is written and again in
the copies: anything shaped like a setup code, what comes before the `@` of
an address (`scheme://user:password@host`), the value of a key that names a
password, token, secret or API key, and `Authorization` / `Bearer` values.
That is a pattern match, not a guarantee: look through the copies before
you share them.

- **Setup stopped partway:** run Setup again; it carries on, and
  `provision.sh` resumes where it stopped. Or uninstall, which undoes a
  half-finished setup too.
- **The uninstaller stopped:** read its message and the log it names. The
  most likely cause is WSL itself (being updated, or not answering): wait,
  or restart Windows, then uninstall again. If it says the service is
  missing, run Setup again to repair it first.
- **A port pop-up:** stop the program or container named, or move it to
  another port, then **Retry**. Docker Desktop can keep running. It can
  come during the install too, right before Domovoi starts: then Setup
  carries on by itself once the port is free.
- **The browser shows an old dashboard, or claiming the admin account
  fails** ("X-Requested-With header required"): an earlier Domovoi's
  service worker is answering from its cache. Use **Open Domovoi** on
  Setup's last page: its address gets past that cache, today's dashboard
  loads, and its worker replaces the old one at once (any later visit to
  `http://localhost:6369` is current too).
- **The setup code expired, or you lost it before claiming the admin
  account:** run Setup again. It restarts Domovoi, which writes a fresh
  code, and shows it on its last page. Nothing else changes.

## Not in this build

- The always-on supervisor (health checks and recovery, firewall sync when
  the network changes, the port watch, the media watcher, backups) and the
  tray icon. Until then nothing re-checks the subnet in the firewall rules
  if the PC moves to another network.
- Power settings: Setup doesn't ask to keep the PC awake. A sleeping PC
  takes Domovoi offline.
- Whether everything starts before anyone signs in after a reboot, and
  whether the GPU is used then, is still being proven.
- Code signing (on hold): see [Unsigned](#unsigned).
- Keep-data uninstall and reinstall onto kept data.
- The optional details page after the internet question (town, state,
  AcoustID key); set them in the dashboard.

## Building it

From a Windows PowerShell 5.1 prompt in the repository:

```powershell
.\windows\build.ps1                  # exes, unit tests, then DomovoiSetup.exe
.\windows\build.ps1 -SkipInstaller   # exes and unit tests only
```

The exes are C# for .NET Framework 4.8.1, built with the compiler that
ships in Windows 11 (`%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`);
the installer needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)
(`winget install JRSoftware.InnoSetup --version 6.7.3`). Output goes to
`windows\build\`. Layout:

| Path | What |
|---|---|
| `windows/installer/DomovoiSetup.iss` | The wizard (Inno Setup Pascal script); the UI only. |
| `windows/installer/pins.iss` | The commit and every pinned download (URL and SHA-256). |
| `windows/installer/art/` | Domovoi's icon (16-256 px) and the wizard's pictures at every size Inno Setup uses, made from `docs/assets/domovoi-icon.png` by `make_art.py` (Pillow 12.3.0 from `requirements.txt` in a private venv; `--check` compares them pixel for pixel). Don't edit them by hand: change the source or the script and run it again. |
| `windows/helper/` | `DomovoiSetupHelper.exe`: every change to the PC, one verb per call, run elevated from `C:\Program Files\Domovoi`. |
| `windows/host/` | `DomovoiHost.exe`: the service that runs as `domovoi-svc`. |
| `windows/common/` | Logic both share: progress-line parsing, `wsl --list` answers, port owners, `.wslconfig` and firewall rules, media-folder rules, the uninstall plan and its runner. |
| `windows/tests/` | The unit tests, compiled with `common/`, `helper/` and `host/`. They run Firewall's cleanup script against stand-in cmdlets and the helper's folder code on throwaway junctions under `%TEMP%`; nothing else on the PC is touched. |

`build.ps1 -Sign` is the place code signing goes back in; while signing is
on hold it refuses, and every build is unsigned.

`pytest domovoi/tests/test_windows_installer.py` runs the build and the
unit tests on Windows, and the file checks everywhere.
