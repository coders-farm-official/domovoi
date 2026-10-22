; What this build installs and downloads (PLAN 4.13; BRIEF rule 6: URL,
; version and SHA-256 for everything fetched at run time). One file, so a
; new pin is one edit, and the pins that belong together move together:
; `windows\build.ps1 -Pin <tag or SHA>` rewrites DomovoiCommit,
; DomovoiChannel, ProvisionSha256, ProvisionBundled (off),
; DashboardTabRoutes and ProvisionAiChoice from that commit in one go, and
; domovoi/tests/test_windows_installer.py fails while they disagree.
;
; Linux-side pins (docker-ce, models, Whisper, Ollama, images, the Python
; lock) are not here: provision.sh reads them from the checkout at the
; commit below (scripts/linux/provision-manifest.conf and the repo's own
; files), so they always belong to the code being installed.

; The commit Setup installs: on main's history, so updates fast-forward
; from it. A full SHA, never a branch or a tag (provision.sh reads a SHA as
; that commit only, and DomovoiHost checks the HEAD it got against it). A
; release build pins its release tag's commit (design-notes
; V1-RELEASE-PLAN 1.5: the tag is the commit households run).
#define DomovoiRepoUrl "https://github.com/coders-farm-official/domovoi"
#define DomovoiCommit "f9b754b0f6d111abad7ea79f26a467700c23df9b"

; The release channel the install then follows for updates (provision.sh
; --track-branch; docs/LINUX_HOST.md, Release channels): "stable" for a
; release build, so the household gets releases only and can switch to
; main in Settings > About & updates. "main" only while DomovoiCommit is not a
; release's commit, as here (a commit on main that no v* tag names yet, so
; the stable channel would not hold it); the test holds "stable" to a
; commit a v* tag names. build.ps1 -Pin sets stable unless given -Channel
; main.
#define DomovoiChannel "main"

; Does the dashboard at that commit give each Settings tab its own address
; (SETUP_ROUTE in web/static/components.jsx: settings/advanced/setup, before
; Settings' regroup of 2026-10-21 settings/configuration/setup, which still
; works)? Then "Open Domovoi" opens #settings/configuration/setup, Settings >
; Advanced with the first-run admin setup open, and the last page's steps
; start there. Without them it opens #settings, Settings on its first tab,
; and the steps say to click Configuration, that dashboard's last tab.
; build.ps1 -Pin sets it from the commit; domovoi/tests/test_windows_installer.py
; checks it.
#define DashboardTabRoutes

; Does provision.sh at that commit take --ai-models / --no-ai-models (AI
; chat and questions optional, 2026-10-21; ProvisionScript.SupportsAiChoice
; in windows\common\Provision.cs)? Then Setup asks "Include AI chat and
; questions?". Without them it doesn't ask and installs everything, as it
; did before the question: that provision.sh always pulls the AI models,
; and a No would only fail once the account, the firewall rules and the
; Ubuntu download were done. build.ps1 -Pin sets it from the commit's
; provision.sh (the bundled one, with ProvisionBundled: set it by hand);
; domovoi/tests/test_windows_installer.py checks it.
#define ProvisionAiChoice

; provision.sh, the one script Setup runs inside the distro (as root); it
; then clones the repository at DomovoiCommit and reads every other pin
; from that checkout. Two ways to get it:
; * not defined (the way a release builds): downloaded from the pinned
;   commit (ProvisionUrl), so the script and the checkout it installs are
;   always the same commit's.
; * ProvisionBundled defined: the repository's scripts/linux/provision.sh as
;   this build's tree has it is packed inside DomovoiSetup.exe, for a test
;   build whose provision.sh is ahead of the pinned commit. The test then
;   holds ProvisionSha256 to this tree's file, and every file that script
;   needs from the checkout (scripts/linux/units/..., the helpers) to exist
;   at DomovoiCommit.
; Either way ProvisionSha256 is checked by the helper and by DomovoiHost.
; #define ProvisionBundled
#define ProvisionSource "..\..\scripts\linux\provision.sh"
#define ProvisionUrl "https://raw.githubusercontent.com/coders-farm-official/domovoi/" + DomovoiCommit + "/scripts/linux/provision.sh"
#define ProvisionSha256 "4fdb688e4a5f401ddca67e46ddb3a4f9f46ccb568d79d5d80b33be9319917a84"

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
