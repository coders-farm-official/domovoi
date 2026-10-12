; What this test build installs and downloads (PLAN 4.13; BRIEF rule 6: URL,
; version and SHA-256 for everything fetched at run time). One file, so a
; new pin is one edit; domovoi/tests/test_windows_installer.py holds
; provision.sh's pin to the file it names (the one shipped inside Setup, or
; the one at the pinned commit).
;
; Linux-side pins (docker-ce, models, Whisper, Ollama, images, the Python
; lock) are not here: provision.sh reads them from the checkout at the
; commit below (scripts/linux/provision-manifest.conf and the repo's own
; files), so they always belong to the code being installed.

; The commit Setup installs: on main's history, so updates fast-forward
; from it. A full SHA, never a branch; release tags and their signers come
; with code signing (PLAN 10, section 14 item 29).
#define DomovoiRepoUrl "https://github.com/coders-farm-official/domovoi"
#define DomovoiCommit "fb62fa42e5914c34ced39a102cef453df0daa2dc"

; provision.sh, the one script Setup runs inside the distro (as root); it
; then clones the repository at DomovoiCommit and reads every other pin
; from that checkout. Two ways to get it:
; * ProvisionBundled defined: the repository's scripts/linux/provision.sh as
;   this build's tree has it is packed inside DomovoiSetup.exe (nothing to
;   download). For a test build whose provision.sh is ahead of the pinned
;   commit: here fb62fa4's script plus wf/wininst-exe's --stop-before and
;   start diagnostics (130,385 bytes), which nothing else in fb62fa4's
;   checkout depends on. Take it out once the commit pinned has them.
; * not defined: downloaded from the pinned commit (ProvisionUrl).
; Either way ProvisionSha256 is checked by the helper and by DomovoiHost.
#define ProvisionBundled
#define ProvisionSource "..\..\scripts\linux\provision.sh"
#define ProvisionUrl "https://raw.githubusercontent.com/coders-farm-official/domovoi/" + DomovoiCommit + "/scripts/linux/provision.sh"
#define ProvisionSha256 "d07eed17ca486aac5da4b321ce89cfd3732614b5e48f2eb3d9309853e4e3a156"

; Ubuntu 26.04.1's official WSL image, 418,495,746 bytes ([verified]
; SHA256SUMS 2026-09-24, PLAN 4.13; matched again 2026-10-11).
#define UbuntuName "ubuntu-26.04.1-wsl-amd64.wsl"
#define UbuntuUrl "https://releases.ubuntu.com/26.04.1/" + UbuntuName
#define UbuntuSha256 "48d56724b5c8e60f24893e83e73bbb58c60b3ca22fba3da977075420acd54104"

; WSL 2.7.14 (x64), 258,990,080 bytes, only when C:\Program Files\WSL\wsl.exe
; is missing or older ([verified] GitHub API asset digest 2026-09-24,
; PLAN 4.4/4.13). Newer builds are kept unless on the deny-list in
; DomovoiSetupHelper (2.9.3).
#define WslMsiName "wsl.2.7.14.0.x64.msi"
#define WslMsiUrl "https://github.com/microsoft/WSL/releases/download/2.7.14/" + WslMsiName
#define WslMsiSha256 "db084e536279a59e90a26ec598d8aa8a4dff8309f41d078fd06242953ac1ebcd"
