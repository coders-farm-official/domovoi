# Running the Domovoi server on Windows with the installer (test build)

`DomovoiSetup.exe` puts the whole Linux server from
[LINUX_HOST.md](LINUX_HOST.md) on a Windows 11 PC: the same systemd units,
Postgres, Docker Engine, Ollama and git checkout, inside a private WSL2
distro that belongs to a hidden Windows account. Windows supplies only the
installer, one service that keeps the distro running, and firewall rules.
After setup the dashboard at `http://localhost:6369` is the only interface.

**This is a test build.** It is unsigned, it installs a pinned commit of
`main`, and some of the finished product is not in it yet (see
[Not in this build](#not-in-this-build)). The native Windows install in the
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
| Internet | Needed during setup (about 8-9 GB) | Afterwards Domovoi works with or without it, as you answer in Setup. |
| Docker Desktop, Ollama for Windows, your own WSL distros | Fine alongside | Setup checks the ports Domovoi needs, not which programs you run. A program holding one of them gets a pop-up naming it, with Retry and Cancel. |

## What Setup does

1. **Checks the PC** and changes nothing: Windows build, memory,
   virtualization, disk, WSL, the network, the internet, and every port
   Domovoi needs (6369, 6370, 6432, 6650-6699, 8050-8099, 8096, 7676, 9467,
   11434). A taken port gets one pop-up per program holding ports (Docker
   Desktop, Ollama, or the program's name), with **Retry** and **Cancel**.
2. **Lists every change it will make** (the table below) before it makes
   any.
3. **Asks whether this PC will have internet after setup** (Yes, always /
   Sometimes / No; nothing is preselected). The answer goes to
   `provision.sh --internet`; see [INTERNET.md](INTERNET.md). You can
   change it later in **Settings > Internet**.
4. **Asks for the media folder** (default `C:\Users\Public\Domovoi`), the
   Windows folder you open in Explorer and copy music, pictures, videos,
   documents and audiobooks into.
5. **Downloads** `provision.sh` from the pinned commit and Ubuntu 26.04.1's
   official WSL image, each checked against a pinned SHA-256
   (`windows/installer/pins.iss`).
6. **Turns on Virtual Machine Platform** and installs WSL 2.7.14 only if
   they are missing. If Windows needs a restart, Setup asks, and starts
   again by itself after you sign in.
7. **Makes the hidden account and the service**, sets the folders' access,
   adds the firewall rules, and hands the rest to the service:
   `DomovoiHost`, running as the hidden account, writes that account's
   `.wslconfig`, imports the distro, and runs `provision.sh` inside it as
   root. Setup's progress bar follows `provision.sh`'s progress lines
   (about 20-40 minutes, most of it the AI models). When `provision.sh` asks
   for a restart of the distro, the service does it and runs the script
   once more to confirm.
8. **Shows your setup code** once, with a **Copy** button, and **Open
   Domovoi** (`http://localhost:6369`). Use the code there to make the
   admin account; it works for 24 hours (a restart of the service writes a
   fresh one).

## Every change on the PC, and how uninstall undoes it

| Change | Uninstall |
|---|---|
| Local account **`domovoi-svc`**: standard user (member of Users only), a random 40-character password generated in memory and never shown, logged or stored outside Windows' own account and service databases, password never expires, user can't change it. Its comment marks it as Setup's. | Deleted (only if it carries Setup's mark or Setup recorded making it). |
| Its sign-in screen entry: DWORD `domovoi-svc = 0` in `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList` (Setup makes the `SpecialAccounts` and `UserList` keys if they are missing). | The value is deleted; the keys too if Setup made them and they are empty. |
| Account rights through LSA: `SeServiceLogonRight`, and `SeDenyInteractiveLogonRight`, `SeDenyRemoteInteractiveLogonRight`, `SeDenyNetworkLogonRight` (run `DomovoiSetup.exe /nodenylogon=1` to leave the three deny rights out). | Every right of the account's SID is removed, with its entry in the policy. |
| Its profile, `C:\Users\domovoi-svc`, made by Windows when the service first signs in; it holds the account's `.wslconfig` (mirrored networking, `instanceIdleTimeout=-1`, memory cap, swap in `C:\ProgramData\Domovoi\wsl`) and its WSL registration. | Deleted (it waits up to three minutes for WSL to let go). |
| Service **`DomovoiHost`** (Automatic, runs as `.\domovoi-svc`, depends on `WSLService`, restarts after 10, 30 and 60 s on failure), binary `C:\Program Files\Domovoi\DomovoiHost.exe`. | Stopped (it stops Domovoi's units in the distro first), then deleted. |
| WSL distro **`domovoi`** of `domovoi-svc` (Ubuntu 26.04.1 plus everything `provision.sh` installs), disk in `C:\ProgramData\Domovoi\distro`. Your own distros and `.wslconfig` are not touched. | Unregistered by the service, as its owner, before the account goes; that deletes its disk. |
| Hyper-V firewall rules for WSL's VM creator (`{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}`), inbound TCP from your subnet only: `Domovoi-Dashboard-6369`, `Domovoi-Core-6370`, `Domovoi-MPD-8050-8099`, `Domovoi-Jellyfin-8096`, `Domovoi-RomM-7676`, `Domovoi-Kiwix-9467`. They apply to every WSL VM on the PC. None if the PC has no private (RFC 1918) address. | Every `Domovoi-` rule for WSL's VM creator is removed. |
| `C:\ProgramData\Domovoi` (`distro`, `wsl`, `backups`, `logs`, `state`, `host`): SYSTEM and Administrators only, with `domovoi-svc` allowed where it needs it. Household accounts can't open it. | Deleted (files still in use at the end are deleted at the next restart). |
| The media folder and its `Music`, `Pictures`, `Videos`, `Documents`, `Audiobooks`: made if missing; `domovoi-svc` may read and write them (an inherited rule); a folder Setup made also lets Users read and write. An existing folder keeps all its own rules. | Setup's rule for `domovoi-svc` comes off. A folder Setup made is removed **only if it is empty**. No file in a media folder is ever deleted. |
| `C:\Program Files\Domovoi` (`DomovoiHost.exe`, `DomovoiSetupHelper.exe`, the uninstaller) and its Apps entry. | Deleted. |
| Only when missing: the Windows feature **Virtual Machine Platform**, and **WSL 2.7.14**. Only after their restart: a `DomovoiSetupResume` value under `HKLM\...\CurrentVersion\RunOnce` (Windows deletes it when it runs). | Left in place: they are shared with anything else that uses WSL. A leftover `DomovoiSetupResume` value is deleted. |

Not changed: power and sleep settings, Windows Update, `AutoUpgradeViaMsix`,
your accounts and files. Setup never runs `wsl --shutdown` and never
touches the household's own WSL distros or Docker Desktop.

## Uninstalling

**Settings > Apps > Installed apps > Domovoi > Uninstall.** It removes
everything in the table above, in order: stop the service, unregister the
distro as `domovoi-svc`, delete the service, the firewall rules, Setup's
media-folder rules and empty folders, the profile, the account rights, the
sign-in entry, the account, `C:\ProgramData\Domovoi`, and the program. Its
record goes to `%TEMP%\domovoi-uninstall-<time>.log`.

It works after a half-finished setup, and running it again is safe: it
looks at what is on the PC as well as what Setup recorded
(`C:\ProgramData\Domovoi\state\install.json`), and skips what is already
gone. If a step can't finish (a file still in use), it says what is left;
those files go at the next restart.

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

Logs are in `C:\ProgramData\Domovoi\logs` (administrators only):

| File | What |
|---|---|
| `install.log` | Every change Setup's helper made. |
| `host.log` | DomovoiHost: the import, the holder (`wsl.exe -d domovoi --exec sleep infinity`), restarts, requests. |
| `provision.log` | Everything `provision.sh` printed, including its `@@DOMOVOI` progress lines. |

None of them holds the account's password or the setup code: the code
travels from the distro to Setup encrypted to a one-time key Setup holds
in memory, and lines shaped like a setup code are blanked.

- **Setup stopped partway:** run Setup again; it carries on, and
  `provision.sh` resumes where it stopped. Or uninstall, which undoes a
  half-finished setup too.
- **A port pop-up:** stop the program or container named, or move it to
  another port, then **Retry**. Docker Desktop can keep running.
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
- Code signing: Windows SmartScreen warns about the unsigned exe.
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
| `windows/helper/` | `DomovoiSetupHelper.exe`: every change to the PC, one verb per call, run elevated from `C:\Program Files\Domovoi`. |
| `windows/host/` | `DomovoiHost.exe`: the service that runs as `domovoi-svc`. |
| `windows/common/` | Logic both share, with unit tests in `windows/tests/`: progress-line parsing, port owners, `.wslconfig` and firewall rules, the uninstall plan. |

`pytest domovoi/tests/test_windows_installer.py` runs the build and the
unit tests on Windows, and the file checks everywhere.
