# Running the Domovoi server on Windows with the installer (preview)

`DomovoiSetup.exe` puts the whole Linux server from
[LINUX_HOST.md](LINUX_HOST.md) on a Windows 11 PC: the same systemd units,
Postgres, Docker Engine, Ollama and git checkout, inside a private WSL2
distro that belongs to a hidden Windows account. Windows supplies only the
installer, one service that keeps the distro running, firewall rules, and
Domovoi's icon in the notification area. After setup the dashboard at
`http://localhost:6369` is the interface; the icon opens it, says whether
Domovoi is running, and prepares satellite cards
([The tray icon](#the-tray-icon)).

**This is a preview.** It ships with Domovoi 1.0 but isn't yet held to its promise. It is not code-signed (see [Unsigned](#unsigned)),
it installs a pinned commit and then follows a release channel (see
[Updates and the release channel](#updates-and-the-release-channel)), and
some of the finished product is not in it yet (see
[Not in this build](#not-in-this-build)). The native
Windows install in the
[README](../README.md) and [SETUP_RUNBOOK.md](SETUP_RUNBOOK.md) is a
different thing and is unchanged.

Read alongside: [Running the server on Linux](LINUX_HOST.md) (what runs
inside the distro) · [Internet or not](INTERNET.md) (the question Setup
asks) · [Security & privacy](SECURITY_PRIVACY.md)

**A Windows laptop as a room** (a satellite, not the server) is a different
thing: [Satellites on spare devices → Windows](SPARE_DEVICE_SATELLITES.md#windows-laptops-and-pcs).
Its installer refuses to run on the PC where this server is installed;
using the server PC as a room is planned for the server's tray icon (it
doesn't do that yet).

---

## Which PCs

| | Supported | Notes |
|---|---|---|
| Windows | 11 22H2 or later (build 22621+), Home or Pro, x64 | Not Windows 10, not Arm64: mirrored networking and the Hyper-V firewall need 22H2. |
| Memory | 16 GB or more | Below 24 GB the distro is capped at 10 GB and gets the smaller models. From about 32 GB it gets the standard models (as on a Linux box with that much memory), and can train wake words ([Training wake words](#training-wake-words)). |
| Processor | Firmware virtualization on (Intel VT-x / AMD-V) | AVX2 is checked and only warned about. |
| Disk | 40 GB free on the system drive | An SSD is strongly recommended; Setup warns on a spinning disk. Wake-word training, if you turn it on, takes about 32 GB more. |
| GPU | Optional | Ollama uses an NVIDIA GPU that WSL passes through; without one it runs on the CPU. Wake-word training uses it too, through the NVIDIA Container Toolkit Setup adds to the distro. Whisper always runs on the CPU. |
| Internet | Needed during setup (about 10.5-13.5 GB, most of it the AI models) | Afterwards Domovoi works with or without it, as you answer in Setup. |
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
   If an earlier Domovoi left its account's profile folder behind
   (`C:\Users\domovoi-svc` with no account), Setup stops and gives the
   one line that removes it (after a restart, from an administrator
   PowerShell: `Get-CimInstance Win32_UserProfile -Filter "SID='<its SID>'" |
   Remove-CimInstance`); **Check again** carries on.
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
5. **Downloads** Ubuntu 26.04.1's official WSL image, and `provision.sh`
   from the pinned commit (GitHub's raw file at `DomovoiCommit`; a release
   build pins its release tag's commit), each checked against a pinned
   SHA-256 (`windows/installer/pins.iss`). A build whose `provision.sh` is
   ahead of the pinned commit (`ProvisionBundled` in `pins.iss`) carries
   the script inside `DomovoiSetup.exe` instead; it is checked against its
   pin the same way, it still installs the pinned commit's code, and every
   file it reads from that checkout must exist at the pinned commit (the
   installer's tests check both). The commit, its `provision.sh` pin, the
   channel and the dashboard's tab addresses move together:
   `windows\build.ps1 -Pin` sets all of them from one commit
   ([Building it](#building-it)).
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
   `--stop-before`, as the current pin's has; with an older one the start
   fails instead, and Setup then shows the same pop-ups and carries on by
   itself after **Retry**.)
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
8. **Shows your setup code** once, with a **Copy** button, **Open
   Domovoi**, and the steps to the admin account in the dashboard's own
   words. The code works for 24 hours (a restart of the service writes a
   fresh one). **Open Domovoi** copies the code too, then opens
   `http://localhost:6369/?installed=<commit>-<build time>#settings/configuration/setup`:
   Settings > Configuration, where the **first-run admin setup** opens by
   itself on a box nobody has claimed yet (if it doesn't, **set up admin**
   in the Admin box opens it). Paste the code into **setup code**, choose
   an admin password of at least 10 characters, type it again in
   **confirm password**, and click **set password**. A build whose pinned
   commit predates the dashboard's tab addresses opens `#settings` instead
   (`DashboardTabRoutes` in `pins.iss`), and its steps say to click the
   Configuration tab first. The `?installed=` part is there because a
   browser that ran an earlier Domovoi on this PC may still have that
   dashboard's service worker, and the old ones answered from their cache
   first, by exact address, so an address no earlier build opened always
   reaches today's dashboard (whose worker then takes over at once). The
   page also says where the AI models run, from what Setup found: the
   chat and voice-command models on the NVIDIA cards it lists (or on the
   processor when there is none), speech recognition always on the
   processor. Domovoi's icon appears beside the clock for the account that
   ran Setup, and starts at every account's sign-in from then on
   ([The tray icon](#the-tray-icon)).

## Every change on the PC, and how uninstall undoes it

| Change | Uninstall |
|---|---|
| Setup's install record: value `Install` under **`HKLM\SOFTWARE\Domovoi`** (what Setup changed, the account's SID; no secrets). Written before each change it records. | Deleted last, when an uninstall run finishes (see [Uninstalling](#uninstalling)); until then a second run still knows what to undo. |
| Local account **`domovoi-svc`**: standard user (member of Users only), a random 40-character password generated in memory and never shown, logged or stored outside Windows' own account and service databases, password never expires, user can't change it. Its comment marks it as Setup's. | Deleted (only if it carries Setup's mark or Setup recorded making it). |
| Its sign-in screen entry: DWORD `domovoi-svc = 0` in `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList` (Setup makes the `SpecialAccounts` and `UserList` keys if they are missing). | The value is deleted; the keys too if Setup made them and they are empty. |
| Account rights through LSA: `SeServiceLogonRight`, and `SeDenyInteractiveLogonRight`, `SeDenyRemoteInteractiveLogonRight`, `SeDenyNetworkLogonRight`. Run `DomovoiSetup.exe /nodenylogon=1` to leave the three deny rights out, on a first install or over an existing one (it takes back ones granted before). | Every right of the account's SID is removed, with its entry in the policy. |
| Its profile, made by Windows when the service first signs in: `C:\Users\domovoi-svc`, or `C:\Users\domovoi-svc.<PC name>` if a folder of that name is already there (the service writes its `.wslconfig` into whichever it is, and the uninstaller finds it by the account's SID); it holds the account's `.wslconfig` (mirrored networking, `instanceIdleTimeout=-1`, memory cap, swap in `C:\ProgramData\Domovoi\wsl`) and its WSL registration. | Deleted. Right after unregistering the distro the service ends that account's own WSL session (`wsl --shutdown` run as `domovoi-svc`, see below), then the uninstaller waits up to two minutes for Windows to let go of the profile. This comes after the account is deleted; if Windows still holds the profile then, a one-time startup task, **`DomovoiRemoveProfile`** (runs as SYSTEM), deletes it at the next restart and then deletes itself. It touches only that profile, only once its account is gone, and if it can't tell (or the profile is in use) it waits for the next boot. |
| Service **`DomovoiHost`** (Automatic, runs as `.\domovoi-svc`, depends on `WSLService`, restarts after 10, 30 and 60 s on failure), binary `C:\Program Files\Domovoi\DomovoiHost.exe`. | Stopped (it stops Domovoi's units in the distro first), then deleted. |
| WSL distro **`domovoi`** of `domovoi-svc` (Ubuntu 26.04.1 plus everything `provision.sh` installs), disk in `C:\ProgramData\Domovoi\distro`. Once imported, the service never deletes or re-imports over that disk, even if WSL stops listing it. Your own distros and `.wslconfig` are not touched. | Unregistered by the service, as its owner, first. Until that has worked (or the disk is gone), nothing else that could strand it is touched: not the service, the account, its profile or rights, nor ProgramData. If it fails, the uninstaller stops, says why, and stays installed for another try. A disk WSL no longer lists is deleted or kept aside, see [Uninstalling](#uninstalling). |
| Hyper-V firewall rules for WSL's VM creator (`{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}`), inbound TCP from your subnet only: `Domovoi-Dashboard-6369`, `Domovoi-Core-6370`, `Domovoi-MPD-8050-8099`, `Domovoi-Jellyfin-8096`, `Domovoi-RomM-7676`, `Domovoi-Kiwix-9467`. They apply to **every WSL VM on the PC**, your own WSL distros and Docker Desktop's included: anything of yours that listens on those ports in a mirrored-mode distro becomes reachable from your home network too. None if the PC has no private (RFC 1918) address. | Every `Domovoi-` rule for WSL's VM creator is removed (and listing them again must work, or the uninstaller says so). |
| `C:\ProgramData\Domovoi` (`distro`, `wsl`, `backups`, `logs`, `state`, `host`): SYSTEM and Administrators only, with `domovoi-svc` allowed where it needs it. Household accounts can't open it. Setup only uses this folder if it made it. | Made administrators-only again, then deleted, only if Setup made it (files still in use are deleted at the next restart). |
| The media folder and its `Music`, `Pictures`, `Videos`, `Documents`, `Audiobooks`: made if missing; `domovoi-svc` may read and write them (an inherited rule); a root folder Setup makes also lets every user of the PC (Users) read and write. An existing folder keeps all its own rules. | Setup's rule for `domovoi-svc` comes off. A folder Setup made is removed **only if it is empty**. No file in a media folder is ever deleted. |
| `C:\Program Files\Domovoi` (`DomovoiHost.exe`, `DomovoiSetupHelper.exe`, `DomovoiTray.exe`, `domovoi.ico`, the uninstaller) and its Apps entry. | Deleted, after everything above is gone. |
| The tray icon's start at every account's sign-in: value `DomovoiTray` under `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run` (`"C:\Program Files\Domovoi\DomovoiTray.exe" --autostart`), and **Domovoi** in the Start menu (opens the dashboard, and brings a hidden icon back). Setup starts the icon for the account that ran it once the install has worked. | Every account's running icon is ended first (before the uninstall's other steps, and before Setup replaces the file on a repair); then the value and the Start menu entry are removed. |
| Written by the icon itself, per account (not by Setup): `HKCU\Software\Domovoi\Tray` (`KeepAwake`, `StartWithWindows`) and its log, `%LOCALAPPDATA%\Domovoi\tray.log`. | The uninstalling account's settings are removed; other accounts' two values and the logs stay (they hold nothing of Domovoi's data). |
| Only when missing (not on a PC that already runs WSL 2.7.14 or later): the Windows feature **Virtual Machine Platform**, and **WSL 2.7.14**. Only when they need a restart: a copy of Setup in `C:\Program Files\Domovoi\resume` and a `DomovoiSetupResume` value under `HKLM\...\CurrentVersion\RunOnce` that runs it (Windows deletes the value when it runs). | Virtual Machine Platform and WSL are left in place: they are shared with anything else that uses WSL. The copy and a leftover `DomovoiSetupResume` value are deleted. |

Not changed: power and sleep settings (the icon's **Keep this PC awake**
only asks Windows not to sleep while it runs), Windows Update, `AutoUpgradeViaMsix`,
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
  `DomovoiRemoveProfile` task). The uninstall finishes, says so in plain
  words ("A leftover folder from Domovoi's hidden account will be removed
  automatically the next time Windows restarts. There's nothing for you
  to do."), and removes the program. The paths, error codes and the
  task's name are in the window's list of steps and the uninstall's log,
  not in its messages.
- **If something else is left**, it depends on what:
  - Left over but harmless (nothing of Domovoi's data in it, no access
    granted once the account is gone): the service marked for deletion,
    an access rule for the deleted account's SID on a media folder, the
    sign-in screen entry, the resume entry, or the profile or files above
    when even their removal at restart could not be set up. The uninstall
    finishes, names each one in plain words, says they hold none of your
    data, and removes the program; the command or setting that removes
    each by hand is in its log (and in the list of steps). The service
    goes at the next restart by itself.
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

## The tray icon

`DomovoiTray.exe`: Domovoi's cat beside the clock (it may be under the
**^** arrow; drag it out to keep it in sight). It runs as whoever is signed
in, never as an administrator, and starts at every account's sign-in. The
dot on the cat says how Domovoi is: green running, amber starting or
updating, grey stopped, red a problem; hovering says it in words, and
Windows shows a notification when Domovoi stops or comes back (not while it
restarts for an update). It reads only what needs no password: the
`DomovoiHost` service's state, the dashboard's `/api/health` and the
core's `/v1/alive`; nothing new is opened on the server for it.

Right-click it for the menu; double-click opens Domovoi.

| Item | What it does |
|---|---|
| **Open Domovoi** | `http://localhost:6369`. |
| **Open on this network** | The dashboard at this PC's address on your home network (the address of the adapter that leads to the router; with several, one entry each). |
| **Copy address** | That address, to paste into a message. |
| **Show a code for your phone** | That address as a QR code: point the phone's camera at it. |
| **User manual** | The dashboard's manual (`#manual`). |
| **Prepare a satellite card...** | The card helper, below. |
| **Keep this PC awake** | On by default: Windows doesn't put the PC to sleep while the icon runs (the screen may still turn off). The line under it says, from the power plan (never changed), whether Windows would still sleep it when nobody is signed in. |
| **Start with Windows** | This account's sign-in start, on by default. |
| **Save logs for support...** | Domovoi's logs are administrators-only: Windows asks for permission, then Setup's helper copies them to `Documents\Domovoi logs`, blanked as **Copy logs** does, with the icon's own log beside them. Without Setup's helper on the PC only the icon's log is copied, and it says so. |
| **Restart Domovoi...** | Asks first, then Windows asks for an administrator, then `Restart-Service DomovoiHost`: for when the dashboard itself can't be reached. |
| **Hide this icon** | Hides it until the next sign-in; Domovoi keeps running, and so does **Keep this PC awake**. **Domovoi** in the Start menu brings it back. |

### Prepare a satellite card

Domovoi's Linux system can't see SD cards here, so the dashboard builds the
card's files as a zip and the icon's card helper puts them on the card:

1. Flash a microSD card with **Raspberry Pi OS Lite (64-bit)** in
   Raspberry Pi Imager. When Imager offers to apply OS customisation
   settings, choose **No**.
2. In the dashboard, **Settings > Satellites > prepare satellite media**:
   pick the board and the microphone, **Prepare**, then **download for the
   card helper**. Keep the setup network password it shows on screen.
3. **Prepare a satellite card...** in the icon's menu. It picks the newest
   `domovoi-satellite-overlay*.zip` in Downloads (or **Browse**), checks it
   (its shape, and the payload against its SHA-256), lists only removable
   drives that look like a freshly flashed Raspberry Pi card (never a hard
   disk or the Windows drive), asks for the setup network password, writes
   the card, reads everything back to check it, and offers **Eject**.
4. Put the card in the Pi and switch it on. Join its setup Wi-Fi from your
   phone, then approve it in **Settings > Satellites**.

It writes exactly what a server that can see the card writes
(`domovoi/satellite_media/overlay.py`, checked byte for byte by the tests):
the zip's `domovoi` folder and `userconf.txt`, `domovoi/ap.json` with the
password, Domovoi's block at the end of `config.txt` and its words at the
end of `cmdline.txt`'s one line, each added once (a card prepared before
keeps its bytes). `config.txt` and `cmdline.txt` are written last, so a
card pulled out halfway doesn't start Domovoi's setup without its files.
Nothing is formatted or deleted. A card that already runs another
first-boot script (Imager's customisation adds one) is refused: Domovoi's
setup would never start on it.

By hand instead, the zip's `README.txt` has the steps.

### On an install from before the icon

An install made by a Setup without the icon (1.0.0-rc1, rc2) gets it in
one of three ways:

- **Run `DomovoiTray.exe` on its own** (keeps everything): download it
  from the release page (its line is in `SHA256SUMS`), put it in any
  folder and run it. It starts for your account at once and at each
  sign-in from then on (value `DomovoiTray` under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`; **Start with
  Windows** turns it off). Each account that wants the icon runs it once.
  Once a Setup with the icon is installed, its copy takes over and the
  per-account start is removed.
- **Run the newer Setup over the install** (a repair): Setup stops
  Domovoi, replaces its programs (the icon included), and runs the newer
  `provision.sh` over the existing distro. The database, settings and
  Domovoi's code stay as they are (an existing checkout is never moved;
  the dashboard's updates move it). This is the same repair run as "Setup
  stopped partway" below, with a newer `provision.sh` than the one that
  installed the distro.
- **Uninstall, then install the newer Setup**: this build has no
  keep-data uninstall, so it deletes the database, history, voice
  profiles and the server's identity, and satellites must be paired
  again.

### For developers

`DomovoiTray.exe --web-url URL --core-url URL --service NAME` (and
`--profile`, `--downloads`, `--fake-card DIR`, `--grace SECONDS`,
`--snapshot DIR`; `windows/tray/Options.cs` lists them) points the icon at
a throwaway Domovoi. Any of them makes it a developer run: its own
settings key (`HKCU\Software\Domovoi\TrayDev\<profile>`), its own
single-instance name, no sign-in registration, and no real drive listed by
the card helper (only `--fake-card` folders). Never point one at a
household's install, and never use **Restart Domovoi** with a real
service name in a test.

## Give the PC a fixed address (DHCP reservation)

Satellites remember the server's address once they are approved and don't
look for it again. If your router hands this PC a different address
later, every satellite goes offline. Setup's last page shows the PC's
address and MAC address: in your router's settings, add a **DHCP
reservation** for that MAC address, so the PC always gets the same
address. Do the same on a Linux server; it is not specific to Windows.

## Training wake words

**Settings > Wake Words** trains a wake word of your own ("Hey Domovoi")
on this PC, inside the distro, once an admin turns training on there. The
steps, what it costs and the licence of what it makes are in
[`scripts/wake_word/README.md`](../scripts/wake_word/README.md). What this
PC needs for it:

- **Docker's way to the GPU.** WSL hands the distro the PC's NVIDIA cards
  (`/dev/dxg`, and Windows' driver in `/usr/lib/wsl/lib`), but Docker
  Engine in the distro can't use them without the NVIDIA Container
  Toolkit. `scripts/linux/gpu-runtime.sh` adds it, only when the distro
  sees an NVIDIA card: NVIDIA's apt repository with its key checked
  against a pinned fingerprint, the toolkit at pinned versions, the
  `nvidia` runtime in Docker's configuration, and a Docker restart
  (Postgres and the rooms' players stop for a moment and come back by
  themselves). It never installs a Linux NVIDIA driver: the driver is
  Windows'. A **new install** gets it during Setup (`provision.sh`'s
  `gpu-runtime` step, since the installer pinned to `4d6b414`); an
  **existing install** from an earlier Setup gets it at its first update
  from the dashboard (**Pull the latest**, then **Restart to apply
  changes**). After that every **Restart Domovoi** checks it is
  still in place. A try that failed is not repeated at every Restart: the
  next one is a day later, or at the next Restart after you press **Turn
  on wake-word training** or **Check the GPU and trainer again**. With the
  internet answer **No**, the update downloads nothing, so the toolkit
  isn't installed then. More:
  [LINUX_HOST.md, Wake-word training](LINUX_HOST.md#wake-word-training).
- **Memory.** Training needs the distro to see 24 GB, the line where it
  gets the standard models. The distro gets the PC's memory minus 8 GB,
  so that means a PC with about 32 GB or more; below that the card says
  training can't be turned on here, in the PC's terms ("this PC has about
  24 GB").
- **Disk and download.** About 22 GB downloaded, once, and about 32 GB
  more disk the first time (the trainer and its training data), then
  about 0.5 GB per wake word, in the distro's disk
  (`C:\ProgramData\Domovoi\distro\ext4.vhdx`), which grows on the system
  drive. Domovoi can't see how much of C: is free (the distro's own disk
  is a 1 TB virtual one, whatever C: holds), so the card shows no number
  here: it asks you to confirm that C: has at least 40 GB free before it
  turns training on. Look in File Explorer, **This PC**.
- **Time.** About 20 minutes a wake word on the GPU (26 the first time;
  measured on an RTX 4070 Ti SUPER). Generating the synthetic speech fills
  a 16 GB card, so the trainer takes the least busy card, away from the one
  holding the chat model.

Then, as the admin: **Settings > Wake Words > Turn on wake-word training**,
confirm, and wait (about 13 minutes on a fast line: the trainer's build,
then the training data download; you can leave the page). Create a wake word, record at
least 15 clips on a satellite (hundreds for an XVF3800), press **Train**,
and **Push to** a room when it is ready. "Hello Domo", "Hey Domovoi" and
"Domovoi" are three wake words, three models; a satellite listens for one
at a time.

On a PC with an NVIDIA card and an install from an earlier Setup, update
first: until the toolkit is there the
card says Docker can't use the card yet, and offers only the processor.
With the toolkit in place after the update, **Check the GPU and trainer
again** (or **Try again**) finds the GPU. Once training is set up for the
GPU, a **Train** while Docker can't reach it (the toolkit lost, the card
gone) fails saying so instead of running for over an hour on the
processor. Without an NVIDIA card, training
runs on the processor: about 75 minutes a word on 8 cores, and voice
answers more slowly while it runs. The card says so and asks before
turning training on and again before each **Train**.

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
- **The dashboard doesn't open:** the tray icon's dot and tooltip say
  what Domovoi is doing (starting, stopped, or which part isn't
  answering). **Restart Domovoi...** in its menu restarts it, and **Save
  logs for support...** copies the logs, both with an administrator's
  permission.
- **The browser shows an old dashboard, or claiming the admin account
  fails** ("X-Requested-With header required"): an earlier Domovoi's
  service worker is answering from its cache. Use **Open Domovoi** on
  Setup's last page: its address gets past that cache, today's dashboard
  loads, and its worker replaces the old one at once (any later visit to
  `http://localhost:6369` is current too).
- **The setup code expired, or you lost it before claiming the admin
  account:** run Setup again. It restarts Domovoi, which writes a fresh
  code, and shows it on its last page. Nothing else changes.
- **Everything offline, the dashboard up** (the satellites and the phone
  app can't reach Domovoi, the dashboard says the server isn't
  answering): the core inside the distro has frozen. It notices by itself
  and restarts within a minute or so; if it hasn't, an admin gets
  **Restart the core** on Settings > Version, the Satellites page and Home
  ([TROUBLESHOOTING.md](TROUBLESHOOTING.md#everything-offline-dashboard-up)).
  The distro runs the same units and the same loop watchdog as a Linux
  server ([LINUX_HOST.md, The loop watchdog](LINUX_HOST.md#the-loop-watchdog)):
  `provision.sh` installs the `Type=notify` core unit with `WatchdogSec=`,
  and keeps the distro's journal on disk, so the record of a freeze
  survives a restart of the PC. A distro installed before 2026-10-15 gets
  the journal setting, and `Restart=on-failure` if its core unit had no
  restart policy at all, from the
  next update run (Settings > Version, **Restart Domovoi**); the
  `Type=notify` unit itself comes with the next run of Setup. By hand, from
  an administrator PowerShell, `Restart-Service DomovoiHost` stops
  Domovoi's Linux system and starts it again, core included (the distro
  is the hidden account's, so your own `wsl` doesn't list it).

## Not in this build

- The always-on supervisor (health checks and recovery, firewall sync when
  the network changes, the port watch, the media watcher, backups). Until
  then nothing re-checks the subnet in the firewall rules if the PC moves
  to another network.
- Power settings: Setup doesn't change them. The tray icon's **Keep this
  PC awake** keeps the PC up while someone is signed in; with nobody signed
  in Windows sleeps it as its power plan says (the icon's menu says when),
  and a sleeping PC takes Domovoi offline.
- The server PC as a room (a satellite on the same PC).
- Whether everything starts before anyone signs in after a reboot, and
  whether the GPU is used then, is still being proven.
- Code signing (on hold): see [Unsigned](#unsigned).
- Keep-data uninstall and reinstall onto kept data.
- The optional details page after the internet question (town, state,
  AcoustID key); set them in the dashboard.

## Updates and the release channel

Setup installs the pinned commit (`DomovoiCommit`), and the dashboard's
updates then follow a release channel (`DomovoiChannel` in `pins.iss`,
handed to `provision.sh` as `--track-branch`; the channels are described in
[LINUX_HOST.md, Release channels](LINUX_HOST.md#release-channels)):

- **`stable`**, on a release build: Domovoi's releases only. Settings →
  Configuration → Version says when a newer release is out; nothing
  updates until an admin pulls it there.
- **`main`**, on a build pinned to a commit that is not a release: every
  change, as it lands.

The repository has a `stable` branch only from the first release (v1.0.0)
on. A release candidate's Setup (v1.0.0-rc2) that asks for stable before
then follows `main`, and its log says so; the Version card says when the
first release is out, and an admin switches to stable there. Nothing
switches by itself. An install from before the channels follows `main`, as
it always did, until someone switches it.

Before it reports success, DomovoiHost checks that the commit the first
install checked out is one Setup pinned (`provision.sh` also refuses a
`--ref` that resolves anywhere else), and fails the install closed
otherwise. Repairs of an install that passed that check keep whatever the
dashboard's updates moved it to.

Inside the distro, updates run as root exactly as on a Linux server, and
so does what keeps them honest: the update unit's root-owned verifier
copies each commit out of a mirror only root can write, verifies its
signature there once signed updates are switched on, and the update runs
from that copy, never from the checkout the service user (`domovoi`, the
core, the dashboard, the plugins) can write ([LINUX_HOST.md, The update
verifier](LINUX_HOST.md#the-update-verifier)). `provision.sh` reads the
checkout the same way on every run, a repair by Setup included. A distro
installed before 2026-10-16 moves over by itself at its next **Restart
Domovoi** after pulling this change; until Setup is rebuilt on a commit
that has it, a fresh install's first update does the same.

## Building it

From a Windows PowerShell 5.1 prompt in the repository:

```powershell
.\windows\build.ps1                  # exes (DomovoiTray.exe too), unit tests, then DomovoiSetup.exe
.\windows\build.ps1 -SkipInstaller   # exes and unit tests only
.\windows\build.ps1 -Pin v1.0.0      # move the pins to a release tag's commit, then build
.\windows\build.ps1 -Pin <sha> -Channel main -PinOnly   # a test pin, nothing built
```

`-Pin` takes a release tag (`refs/tags` only, never a branch) or a full
commit SHA that is in this clone and on `origin/main`'s history, and
rewrites `pins.iss` in one go: `DomovoiCommit`, `DomovoiChannel` (`stable`
unless `-Channel main`; `stable` needs a commit a `v*` tag names),
`ProvisionSha256` (that commit's `provision.sh`, its exact bytes),
`ProvisionBundled` off, and `DashboardTabRoutes`. Commit the changed
`pins.iss` after the tag: the tag is the commit households run.

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
| `windows/tray/` | `DomovoiTray.exe`: the notification-area icon and its card helper (WinForms, the same compiler). |
| `windows/common/` | Logic both share: progress-line parsing, `wsl --list` answers, port owners, `.wslconfig` and firewall rules, media-folder rules, the uninstall plan and its runner. |
| `windows/tests/` | The unit tests, compiled with `common/`, `helper/`, `host/` and `tray/`. They run Firewall's cleanup script against stand-in cmdlets, the helper's folder code on throwaway junctions under `%TEMP%`, and the card helper on folders that mimic a flashed card, against `card-goldens.json` (the server's own `config.txt`/`cmdline.txt` edits, made and kept current by `domovoi/tests/test_windows_card_parity.py`); nothing else on the PC is touched. |

`build.ps1 -Sign` is the place code signing goes back in; while signing is
on hold it refuses, and every build is unsigned.

`pytest domovoi/tests/test_windows_installer.py` runs the build and the
unit tests on Windows, and the file checks everywhere.
