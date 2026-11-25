; DomovoiSetup.exe: the Windows installer (not code-signed yet).
;
; Setup is only the user interface. Every change to the PC is made by
; DomovoiSetupHelper.exe, run elevated from C:\Program Files\Domovoi
; (admin-owned) after Setup copies it there; everything that runs as the
; hidden account is done by DomovoiHost.exe, the service. Setup itself
; only runs Windows' own dism.exe and msiexec.exe (System32) when WSL's
; prerequisites are missing, before the install, taskkill.exe for the tray
; icons, and, before a restart, copies itself into
; C:\Program Files\Domovoi\resume and runs icacls.exe on that folder
; (CreateRunOnceEntry).
;
; Flow (PLAN 4.1-4.12): preflight (ports by owner, Retry/Cancel), the AI
; question (AI chat and questions: Yes on a first install, the household's
; current answer on a re-run; only when the pinned provision.sh takes the
; answer, pins.iss ProvisionAiChoice; the download sizes on the pages after
; it follow the answer), what changes, the internet
; question, the media folder, downloads (pinned,
; SHA-256 checked), Virtual Machine Platform and WSL with restart and
; resume, then the helper's steps and DomovoiHost's progress (the ports
; checked again right before Domovoi starts, and after a failed step),
; then the setup code. Uninstall runs the helper's uninstall, which undoes
; all of it, works after a half-finished install too, and shows each step
; as it goes. Every error page offers "Copy logs": the logs are in an
; administrators-only folder, so the helper copies them, redacted, to a
; new folder in Documents\Domovoi logs and Explorer opens that folder.
;
; Update mode (the Windows update's Windows step, design note
; WINDOWS-ONE-UPDATE-2026-10-22): the tray starts this Setup, after checking
; its signatures, as /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /SP-
; /update=1 [/linuxfirst=1] /LOG=<run>\setup.log /updateresult=<run>\result.txt
; (windows\common\UpdateRun.cs SetupArguments). It is a repair of the
; existing install: the household's answers come from Setup's install record
; (the helper's saved-answers; AI kept as the home has it), no page and no
; question is shown, and a failure never undoes anything (no "Remove what
; Setup has changed", which would uninstall, and the service isn't parked:
; Domovoi keeps running as it is). /linuxfirst=1 (the tray's own "Update
; Domovoi") runs the Linux part first, through the helper's linux-update,
; and stops before anything on Windows changes if that fails. The outcome
; goes to /updateresult (ok=, error=, log=, linux=, version=) and the exit
; code (10: the files were replaced but the repair didn't finish), and the
; tray starts again at the end. The tray's helper starts it from
; C:\ProgramData\Domovoi\updates\<run> (administrators only), and the result
; and the log go there too: /updateresult is refused anywhere else. A
; failure starts DomovoiHost again (it was stopped to replace its file), so
; Domovoi keeps running as it was. The Ubuntu image isn't downloaded again
; when DomovoiHost's folder already holds the pinned one.
;
; Offline kit mode (release 1.5.0; docs/WINDOWS_HOST.md, "Installing
; without internet"): Setup finds kit.json beside itself (the stick's
; release folder, or the kit's own), takes /kit=DIR, or the household picks
; the kit's folder when the internet check fails. Before anything changes,
; the helper's kit-check (windows\common\OfflineKit.cs) checks the stick's
; SHA256SUMS, that nothing on it is a link or unaccounted for by kit.json,
; the signed release manifest against the release key, that the
; kit is this Setup's own release (tag and commit), the parts and the disk.
; Then nothing is downloaded: Ubuntu's image, WSL's MSI and provision.sh
; come off the stick, each checked against this Setup's pins, the helper's
; preflight skips the internet check, every helper call says --offline (its
; network guard), AddDownload and RunDownloads refuse loudly, and DomovoiHost
; mounts the kit in the distro for provision.sh --offline-kit. The kit's
; folder rides across the restart as /kit=.
;
; Build: windows\build.ps1 (Inno Setup 6). Docs: docs/WINDOWS_HOST.md.

#include "pins.iss"
; Where "Open Domovoi" lands (pins.iss, DashboardTabRoutes): Settings >
; Advanced with the first-run admin setup open (the first-run welcome in a
; dashboard that has it: pins.iss DashboardWelcome), in a dashboard that has the
; tab addresses (the address keeps its first spelling, Configuration, which
; the dashboard still accepts: build.ps1 -Pin checks the pinned commit's
; SETUP_ROUTE); Settings on its first tab in one from before them, whose
; last tab was still called Configuration.
#ifdef DashboardTabRoutes
  #define DashboardRoute "settings/configuration/setup"
#else
  #define DashboardRoute "settings"
#endif
; The channel the install follows for updates (pins.iss DomovoiChannel), in
; the words the page that says what Setup changes uses.
#if DomovoiChannel == "stable"
  #define ChannelWords ": Domovoi releases only"
#else
  #define ChannelWords ": every change, as it lands"
#endif
#define AppVersion "1.4.1"
#define Helper "DomovoiSetupHelper.exe"
; Which build this is, for the dashboard link on the last page: a URL no
; earlier build opened, so an old cache-first service worker of the
; dashboard can't answer it from its cache.
#define BuildStamp GetDateTimeString('yyyymmddhhnnss', '', '')

[Setup]
AppId={{8F3C2A61-5D4B-4E7A-9C1F-2B6D0E4A7C35}
AppName=Domovoi
AppVersion={#AppVersion}
AppVerName=Domovoi {#AppVersion} ({#Copy(DomovoiCommit, 1, 7)})
AppPublisher=Coders Farm
AppPublisherURL={#DomovoiRepoUrl}
AppSupportURL={#DomovoiRepoUrl}/blob/main/docs/WINDOWS_HOST.md
DefaultDirName={autopf}\Domovoi
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=admin
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.22621
OutputBaseFilename=DomovoiSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern dynamic
CloseApplications=no
; One Setup at a time on this PC, whoever starts it: a second one (a second
; press of the Windows update, from another account's tray, or by hand)
; refuses while the first runs.
SetupMutex=Global\DomovoiSetup
; Setup's own log, in the temporary folder of the account that ran it
; ("Setup Log <date> #<n>.txt"; /LOG= names another place): the plain
; messages for an error Setup can't get past name it ({log}). Nothing
; secret goes in it (the setup code is never logged).
SetupLogging=yes
RestartIfNeededByRun=no
UsePreviousAppDir=no
Uninstallable=yes
UninstallDisplayName=Domovoi
VersionInfoVersion=1.4.1.0
VersionInfoProductTextVersion={#AppVersion}
VersionInfoCompany=Coders Farm
VersionInfoDescription=Domovoi Setup
VersionInfoProductName=Domovoi
; Domovoi's own mark, made by art\make_art.py from docs/assets/domovoi-icon.png:
; Setup's and the uninstaller's icon, the Installed apps entry, and the
; wizard's pictures at each size Inno Setup 6.6+ uses (100% to 250%).
SetupIconFile=art\domovoi.ico
UninstallDisplayIcon={app}\domovoi.ico
WizardImageFile=art\wizard-202x386.png,art\wizard-269x515.png,art\wizard-336x643.png,art\wizard-403x772.png,art\wizard-430x824.png,art\wizard-498x953.png,art\wizard-534x1022.png
WizardSmallImageFile=art\small-58.png,art\small-77.png,art\small-97.png,art\small-116.png,art\small-124.png,art\small-143.png,art\small-159.png

[Files]
; The helper first: preflight extracts it before anything is installed.
Source: "..\build\bin\{#Helper}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\bin\DomovoiHost.exe"; DestDir: "{app}"; Flags: ignoreversion
; The notification-area icon (windows\tray): every account's, started at
; sign-in by the Run value below and right after a successful install.
Source: "..\build\bin\DomovoiTray.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "art\domovoi.ico"; DestDir: "{app}"; Flags: ignoreversion
; The satellite Setup of this same build (DomovoiSatelliteSetup.iss, built
; first by build.ps1): DomovoiHost places it in Domovoi's cache, so the
; dashboard hands the household's PCs the version that matches, offline.
Source: "..\build\DomovoiSatelliteSetup.exe"; DestDir: "{app}"; Flags: ignoreversion
#ifdef ProvisionBundled
; provision.sh shipped inside Setup (pins.iss), extracted to {tmp} before
; the downloads and handed to DomovoiHost by the helper's job step.
Source: "{#ProvisionSource}"; Flags: dontcopy
#endif

[Registry]
; The tray icon for every account on this PC, at sign-in (machine-wide; the
; uninstaller removes the value). An account that turns "Start with
; Windows" off in the icon's menu keeps that in its own HKCU, and this
; start then does nothing for it.
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DomovoiTray"; ValueData: """{app}\DomovoiTray.exe"" --autostart"; Flags: uninsdeletevalue

[Icons]
; Start menu: "Domovoi" opens the dashboard (and brings the icon back if it
; was hidden). Removed by the uninstaller.
Name: "{autoprograms}\Domovoi"; Filename: "{app}\DomovoiTray.exe"; Parameters: "--open"; Comment: "Open Domovoi's dashboard"

[UninstallDelete]
; The copy Setup runs after a restart (CreateRunOnceEntry), if one was made.
Type: filesandordirs; Name: "{app}\resume"

[Messages]
ConfirmUninstall=Remove Domovoi from this PC now?%n%nIt removes the hidden account domovoi-svc, the DomovoiHost service, Domovoi's firewall rules and C:\ProgramData\Domovoi. Your data is kept or deleted as you just chose.%n%nFiles in your media folders are kept; a media folder is removed only if it is empty.

[Code]
const
  RunOnceKey = 'Software\Microsoft\Windows\CurrentVersion\RunOnce';
  RunOnceName = 'DomovoiSetupResume';
  WM_COPY = $0301;
  WM_VSCROLL = $0115;
  SB_BOTTOM = 7;
  InternetDoc = '{#DomovoiRepoUrl}/blob/main/docs/INTERNET.md#what-goes-out';
  DashboardUrl = 'http://localhost:6369';
  { What "Open Domovoi" opens: the dashboard with this build's mark. A
    browser that ran an earlier Domovoi on this port may still have that
    dashboard's service worker, and the old ones answered from their cache
    first, by exact URL (web/static/sw.js before f4421ab, 2026-09-25); a
    URL no build opened before misses that cache and gets today's page,
    which installs the current worker (network-first; it takes over at
    once: skipWaiting, clients.claim). The hash after it is the page:
    DashboardRoute, above. }
  OpenUrl = 'http://localhost:6369/?installed={#Copy(DomovoiCommit, 1, 7)}-{#BuildStamp}#{#DashboardRoute}';
  InstallPortCheck = 'preflight --ports-only --installing';
  LogsHint = 'Logs: C:\ProgramData\Domovoi\logs (only administrators can open it). Copy logs puts a copy in a new folder in Documents\Domovoi logs and opens that folder.';

var
  Resumed: Boolean;
  NoDenyLogon: Boolean;
  ForceCancel: Boolean;
  Preflight: TArrayOfString;
  PreflightDone: Boolean;
  LanIp, LanMac, LanSubnet, LanPrivate, WslMemGb, VmpState, WslState: String;

  PreflightPage: TOutputMsgMemoWizardPage;
  ConsentPage: TOutputMsgMemoWizardPage;
  AiPage: TWizardPage;
  RadioAiYes, RadioAiNo: TNewRadioButton;
  AiYesText, AiNoText, AiLaterText: TNewStaticText;
  AiModelsGb: String;
  { The helper's saved-ai (a re-run): yes or no, and household, record or
    none; '' before it ran. }
  AiSavedDefault, AiSavedFrom: String;
  { What provision.sh reported once Domovoi was up: yes, no, or ''. }
  AiReported: String;
  { Whether provision.sh put the phone app on the server: yes, no (it
    couldn't download it: the last page says so), or '' (unknown). }
  PhoneAppReported: String;
  { Whether this run found the server's code already checked out and left
    it where it was (DomovoiHost's checkout_kept, common\Provision.cs
    InstalledHead.Kept): yes, no, or '' (unknown). Only yes is said. }
  CheckoutKept: String;
  InternetPage: TWizardPage;
  InternetIntro: TNewStaticText;
  RadioAlways, RadioSometimes, RadioNever: TNewRadioButton;
  MediaPage: TInputDirWizardPage;
  { The house identity (S3-4): kept data, a backup file, or fresh. }
  IdentityPage: TWizardPage;
  RadioIdentityKept, RadioIdentityBackup, RadioIdentityFresh: TNewRadioButton;
  IdentityKeptText: TNewStaticText;
  IdentityEdit: TNewEdit;
  IdentityBrowse: TNewButton;
  KeptDir: String;
  KeptLooked: Boolean;
  { The uninstaller: keep the data (the default) or remove everything. }
  UninstallKeepData: Boolean;
  DownloadPage: TDownloadWizardPage;
  ProgressPage: TOutputProgressWizardPage;
  CheckPage: TOutputMarqueeProgressWizardPage;

  InstallOk: Boolean;
  FailureText: String;
  UndoOnExit: Boolean;
  SetupCodeText: String;
  { The setup-code QR (1.5.0): the helper's qr_size= and qr= (one '1' or
    '0' a module, row by row), for the dashboard's address on the network
    with the code in the URL fragment (windows/helper/SetupQr.cs). Kept in
    memory like the code; empty when the helper had no LAN address. }
  SetupQrSize: Integer;
  SetupQrBits: String;
  GpuRaw, TierText: String;
  { The network name DomovoiHost holds (domovoi.local, or domovoi2.local ...),
    from host-status's lan_name; empty when none (docs/WINDOWS_HOST.md). }
  LanName: String;
  CodeTitle, CodeHint, LanText: TNewStaticText;
  CodeEdit: TNewEdit;
  CopyButton, OpenButton, CopyLogsButton, PhoneCodeButton: TNewButton;
  StopButton: TNewButton;
  StopRequested: Boolean;

  { Update mode (the header): what was asked, the household's saved
    answers, whether the trays were ended (so this Setup starts this
    account's again), the Linux part's outcome, and whether the result file
    is written. }
  UpdateMode, LinuxFirst, TraysStopped, ResultWritten, HostStopped: Boolean;
  SavedInternet, SavedMediaRoot, LinuxOutcome: String;
  { The Ubuntu image the job step hands DomovoiHost: the download, or (an
    update) the pinned copy DomovoiHost's folder already holds. }
  ImagePath: String;
  { provision.sh as the job step hands it to DomovoiHost: the download (or
    the copy packed in this Setup), or the kit's linux\provision.sh. }
  ProvisionPath: String;

  { The offline kit (the header): its folder ('' online), whether its check
    passed this run, what it holds. }
  KitRoot, KitVersion: String;
  KitChecked, KitAi: Boolean;

  { The uninstaller: the helper's output lines as they come, and the list
    of steps under the progress bar. }
  UninstLines: TArrayOfString;
  UninstallDetails: TNewMemo;

function GetTickCount: Cardinal; external 'GetTickCount@kernel32.dll stdcall';

{ -- helper calls ------------------------------------------------------- }

function TmpHelper: String;
begin
  Result := ExpandConstant('{tmp}\{#Helper}');
end;

function AppHelper: String;
begin
  Result := ExpandConstant('{app}\{#Helper}');
end;

{ Ends every running DomovoiTray.exe, every account's (Setup and the
  uninstaller run as an administrator), so its file can be replaced or
  deleted. Each comes back at its account's next sign-in. }
procedure StopTrays;
var
  Code: Integer;
begin
  TraysStopped := True;
  if Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM DomovoiTray.exe', '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Log('taskkill DomovoiTray.exe: exit ' + IntToStr(Code))
  else
    Log('taskkill DomovoiTray.exe did not run');
end;

{ The tray icon for the person running Setup, right away (not elevated:
  as the account that started Setup). Everyone else's starts at sign-in. }
procedure StartTray;
var
  Code: Integer;
begin
  if not ExecAsOriginalUser(ExpandConstant('{app}\DomovoiTray.exe'), '', '', SW_SHOWNORMAL, ewNoWait, Code) then
    Log('Could not start DomovoiTray.exe: ' + SysErrorMessage(Code));
end;

{ Runs the helper; Lines gets its stdout (key=value lines). Never logs the
  output: the setup-code verb's output is the code. }
function RunHelper(const Exe, Params: String; var Lines: TArrayOfString; var Code: Integer): Boolean;
var
  Output: TExecOutput;
begin
  Result := False;
  SetArrayLength(Lines, 0);
  Code := -1;
  try
    Result := ExecAndCaptureOutput(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, Code, Output);
    if Result then
      Lines := Output.StdOut;
  except
    Log('Could not run ' + ExtractFileName(Exe) + ': ' + GetExceptionMessage);
    Result := False;
  end;
end;

function KvValue(const Lines: TArrayOfString; const Key: String): String;
var
  I: Integer;
  P: String;
begin
  Result := '';
  P := Key + '=';
  for I := 0 to GetArrayLength(Lines) - 1 do
    if Copy(Lines[I], 1, Length(P)) = P then
    begin
      Result := Trim(Copy(Lines[I], Length(P) + 1, Length(Lines[I])));
      Exit;
    end;
end;

function KvValues(const Lines: TArrayOfString; const Key: String): TArrayOfString;
var
  I, N: Integer;
  P: String;
begin
  SetArrayLength(Result, 0);
  N := 0;
  P := Key + '=';
  for I := 0 to GetArrayLength(Lines) - 1 do
    if Copy(Lines[I], 1, Length(P)) = P then
    begin
      SetArrayLength(Result, N + 1);
      Result[N] := Trim(Copy(Lines[I], Length(P) + 1, Length(Lines[I])));
      N := N + 1;
    end;
end;

function JoinLines(const A: TArrayOfString; const Prefix: String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 0 to GetArrayLength(A) - 1 do
    Result := Result + Prefix + A[I] + #13#10;
end;

{ Pumps the wizard's messages while waiting (SetProgress repaints). }
procedure Wait(Ms: Integer; Position: Integer);
var
  I: Integer;
begin
  for I := 1 to Ms div 100 do
  begin
    ProgressPage.SetProgress(Position, 100);
    Sleep(100);
  end;
end;

{ -- logs the household can open (owner fixes 2026-10-12, item 4) ------- }

function GetFileAttributesW(lpFileName: String): Cardinal; external 'GetFileAttributesW@kernel32.dll stdcall';

{ A folder that is not a link (no reparse point). }
function IsPlainDir(const Path: String): Boolean;
var
  A: Cardinal;
begin
  A := GetFileAttributesW(Path);
  Result := (A <> $FFFFFFFF) and ((A and $10) <> 0) and ((A and $400) = 0);
end;

{ The logs are in C:\ProgramData\Domovoi, which only administrators can
  open. The helper copies them (redacted: no setup code, no password in a
  URL) to a new folder under Documents\Domovoi logs (install-<time> or
  uninstall-<time>), and Explorer opens that new folder. Kind: install or
  uninstall; UninstallLog: the uninstall's own record in the temp folder,
  or ''. }
procedure CopyLogs(const Exe, Kind, UninstallLog: String);
var
  Lines: TArrayOfString;
  Code, Err: Integer;
  Params, Dest, Folder: String;
begin
  if not FileExists(Exe) then
  begin
    MsgBox('The logs can''t be copied: ' + Exe + ' is missing.', mbError, MB_OK);
    Exit;
  end;
  Dest := ExpandConstant('{userdocs}\Domovoi logs');
  Params := 'copy-logs --kind ' + Kind + ' --dest ' + AddQuotes(Dest);
  if UninstallLog <> '' then
    Params := Params + ' --uninstall-log ' + AddQuotes(UninstallLog);
  if not RunHelper(Exe, Params, Lines, Code) or (Code <> 0) then
  begin
    MsgBox('The logs could not be copied: ' + KvValue(Lines, 'error'), mbError, MB_OK);
    Exit;
  end;
  { The helper's folder= is the new folder; anything but a folder named
    for this copy directly inside Dest shows Dest itself. }
  Folder := KvValue(Lines, 'folder');
  if (CompareText(ExtractFileDir(Folder), Dest) <> 0) or (Pos(Kind + '-', ExtractFileName(Folder)) <> 1) then
    Folder := Dest;
  Log('Copied the logs to ' + Folder);
  { The uninstaller runs elevated and has no way to start anything as the
    signed-in user: opening a path in the user's Documents from there
    would run as administrator whatever answers to it (a missing folder
    with a .bat of the same name beside it, say), so it only says where
    the copy is. Setup opens it as the original user, and only a real
    folder (not a link), with a trailing backslash so nothing but the
    folder can answer. }
  if not IsUninstaller and IsPlainDir(Folder) then
    ShellExecAsOriginalUser('open', AddBackslash(Folder), '', '', SW_SHOWNORMAL, ewNoWait, Err)
  else
    MsgBox('The logs are copied to:' + #13#10#13#10 + Folder + #13#10#13#10 + 'Open that folder in Explorer.', mbInformation, MB_OK);
end;

{ An install error with Retry, Copy logs and Stop Setup; Copy logs copies
  them and asks again. True: Retry. (MB_ABORTRETRYIGNORE takes its labels
  in the order Retry, Ignore, Abort.) }
function RetryOrStop(const Instruction, Text: String): Boolean;
var
  R: Integer;
begin
  { Update mode asks nothing: the step stops, and the result says why. }
  if UpdateMode then
  begin
    Log('Update mode: ' + Instruction + ': ' + Text);
    Result := False;
    Exit;
  end;
  while True do
  begin
    { (No line here may start with "[": ISCC would read it as a section.) }
    R := TaskDialogMsgBox(Instruction, Text + #13#10#13#10 + LogsHint, mbError, MB_ABORTRETRYIGNORE, ['Retry', 'Copy logs', 'Stop Setup'], 0);
    if R = IDIGNORE then
      CopyLogs(AppHelper, 'install', '')
    else
    begin
      Result := R = IDRETRY;
      Exit;
    end;
  end;
end;

{ -- the offline kit (the header) ---------------------------------------- }

function KitMode: Boolean;
begin
  Result := KitRoot <> '';
end;

{ ' --offline' on every helper call in kit mode: its network guard. }
function OfflineArg: String;
begin
  Result := '';
  if KitMode then
    Result := ' --offline';
end;

{ A file on the stick, by its kit path with backslashes. }
function KitFile(const Rel: String): String;
begin
  Result := AddBackslash(KitRoot) + Rel;
end;

{ The kit's folder: /kit=, else kit.json beside this Setup (the stick's
  release folder, or the kit's own folder); '' when there is none. }
function FindKit: String;
var
  Src: String;
begin
  Result := ExpandConstant('{param:kit|}');
  if Result = '' then
  begin
    Src := RemoveBackslashUnlessRoot(ExpandConstant('{src}'));
    if FileExists(AddBackslash(Src) + 'kit.json') then
      Result := Src
    else if (ExtractFileDir(Src) <> Src) and FileExists(AddBackslash(ExtractFileDir(Src)) + 'kit.json') then
      Result := ExtractFileDir(Src);
  end;
  if Result <> '' then
    Result := RemoveBackslashUnlessRoot(AddBackslash(Result));
end;

{ Lets the household pick the kit's folder. False: they didn't. }
function ChooseKit: Boolean;
var
  Dir: String;
begin
  Dir := KitRoot;
  Result := BrowseForFolder('Choose the offline kit: the USB stick''s top folder, the one with kit.json in it.', Dir, False);
  if Result and (Dir <> '') then
  begin
    KitRoot := RemoveBackslashUnlessRoot(AddBackslash(Dir));
    KitChecked := False;
    Log('Offline kit chosen: ' + KitRoot);
  end
  else
    Result := False;
end;

{ The helper's kit-check: fatal lines in Lines when the kit can't be used.
  The folder goes without its trailing backslash ("E:"): a quoted "E:\"
  would end in an escaped quote. }
function RunKitCheck(var Lines: TArrayOfString): Boolean;
var
  Code: Integer;
  Params, Err: String;
begin
  Params := 'kit-check' + OfflineArg + ' --kit ' + AddQuotes(RemoveBackslash(KitRoot)) +
    ' --commit {#DomovoiCommit} --version {#AppVersion}' +
    ' --ubuntu-name {#UbuntuName} --ubuntu-sha256 {#UbuntuSha256}' +
    ' --wsl-msi-name {#WslMsiName} --wsl-msi-sha256 {#WslMsiSha256}';
#ifndef ProvisionBundled
  Params := Params + ' --provision-sha256 {#ProvisionSha256}';
#endif
  if (WslState = 'missing') or (WslState = 'old') then
    Params := Params + ' --need-wsl';
  CheckPage.SetText('Checking the offline kit at ' + KitRoot + '...', 'Setup reads every file on the stick once, to catch a bad copy. This takes a few minutes.');
  CheckPage.Show;
  try
    CheckPage.Animate;
    Result := RunHelper(TmpHelper, Params, Lines, Code) and (Code = 0);
  finally
    CheckPage.Hide;
  end;
  if not Result then
  begin
    Err := KvValue(Lines, 'error');
    SetArrayLength(Lines, 1);
    Lines[0] := 'fatal=Setup could not check the offline kit (' + Err + ').';
  end;
  KitVersion := KvValue(Lines, 'kit_version');
  KitAi := KvValue(Lines, 'kit_ai') = 'yes';
  Log('kit-check ' + KitRoot + ': version ' + KitVersion + ', AI ' + KvValue(Lines, 'kit_ai') + ', ' + KvValue(Lines, 'kit_gb') + ' GB, needs ' + KvValue(Lines, 'need_gb') + ' GB free');
end;

{ The one way a download starts. In kit mode nothing may be downloaded:
  reaching this is a fault in Setup, and it stops, loudly. }
procedure AddDownload(const Url, Name, Sha: String);
begin
  if KitRoot <> '' then
    RaiseException('Setup tried to download ' + Name + ' while installing from the offline kit, and nothing may be downloaded. This is a fault in Domovoi Setup; please report it. Nothing was downloaded.');
  DownloadPage.Add(Url, Name, Sha);
end;

{ -- preflight (PLAN 4.1) ----------------------------------------------- }

{ One pop-up per program that holds a port Domovoi needs, with Retry and
  Cancel (owner decision 2026-10-09); Retry checks again with the same
  helper call. Used by the first check and, during the install, right
  before Domovoi starts and after a failed step (owner fixes 2026-10-12,
  item 2). False: the user cancelled. }
function ResolvePorts(Lines: TArrayOfString; const Exe, Params, CancelLabel: String): Boolean;
var
  Owners, Parts: TArrayOfString;
  I, Code: Integer;
begin
  Result := True;
  while True do
  begin
    Owners := KvValues(Lines, 'port_owner');
    if GetArrayLength(Owners) = 0 then
      Exit;
    if UpdateMode then
    begin
      Parts := StringSplit(Owners[0], ['|'], stAll);
      if GetArrayLength(Parts) >= 3 then
        FailureText := Parts[1] + ': ' + Parts[2]
      else
        FailureText := 'A port Domovoi needs is taken by another program.';
      Result := False;
      Exit;
    end;
    for I := 0 to GetArrayLength(Owners) - 1 do
    begin
      Parts := StringSplit(Owners[I], ['|'], stAll);
      if GetArrayLength(Parts) < 3 then
        Continue;
      if TaskDialogMsgBox(Parts[1],
        Parts[2] + #13#10#13#10 + 'Domovoi checks only the ports it needs; programs on other ports are fine.',
        mbError, MB_RETRYCANCEL, ['Retry', CancelLabel], 0) <> IDRETRY then
      begin
        Result := False;
        Exit;
      end;
    end;
    if not RunHelper(Exe, Params, Lines, Code) then
    begin
      MsgBox('Setup could not check the ports again.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

{ During the install: True when a Windows program holds a port Domovoi
  needs now; Lines then has its port_owner lines. }
function PortsTakenNow(var Lines: TArrayOfString): Boolean;
var
  Code: Integer;
begin
  Result := RunHelper(AppHelper, InstallPortCheck, Lines, Code) and (Code = 0)
    and (GetArrayLength(KvValues(Lines, 'port_owner')) > 0);
end;

{ A Setup run again on an install (repair, upgrade): the AI page starts at
  the answer this home has now, never at a Yes that would pull the AI models
  into a home that turned AI off in Settings > Models (review round 1,
  2026-10-21). The helper's saved-ai reads the household's AI_FEATURES
  through DomovoiHost, else the install record's last answer; a first
  install gets Yes. /ai= on the command line wins over both. }
procedure ReadSavedAi;
var
  Lines: TArrayOfString;
  Code: Integer;
begin
  if UpdateMode then
    Exit;
#ifdef ProvisionAiChoice
  AiSavedDefault := '';
  AiSavedFrom := '';
  CheckPage.SetText('Reading this home''s current answers...', '');
  CheckPage.Show;
  try
    CheckPage.Animate;
    if RunHelper(TmpHelper, 'saved-ai', Lines, Code) and (Code = 0) then
    begin
      AiSavedDefault := KvValue(Lines, 'ai_default');
      AiSavedFrom := KvValue(Lines, 'ai_from');
    end
    else
      Log('saved-ai did not answer: ' + KvValue(Lines, 'error'));
  finally
    CheckPage.Hide;
  end;
  Log('AI page default: ' + AiSavedDefault + ' (from ' + AiSavedFrom + ')');
  if ExpandConstant('{param:ai|}') <> '' then
    Exit;
  if AiSavedDefault = 'no' then
    RadioAiNo.Checked := True
  else
    RadioAiYes.Checked := True;
  if AiSavedFrom = 'household' then
    AiPage.Description := 'Picked as this home has it now (Settings > Models); change it if you like.'
  else if (AiSavedFrom = 'record') and (AiSavedDefault = 'no') then
    AiPage.Description := 'Picked as your last Setup left it; change it if you like.'
  else if AiSavedFrom = 'record' then
    AiPage.Description := 'Picked as your last Setup left it. Left at Yes, AI stays as this home has it now.';
#endif
end;

{ The preflight page's internet line. }
function InternetLine: String;
begin
  if KitMode then
    Result := 'Internet: not needed (installing from the offline kit at ' + KitRoot + ', Domovoi ' + KitVersion + ')' + #13#10
  else
    Result := 'Internet: ' + KvValue(Preflight, 'internet') + #13#10;
end;

function RunPreflight: Boolean;
var
  Fatal, Warn, KitLines: TArrayOfString;
  Code, R: Integer;
  Ran: Boolean;
  Command: String;
begin
  Result := False;
  { An update that reuses the Ubuntu image already here (wpReady) downloads
    nothing from the image's host, so the internet check skips it. }
  while True do
  begin
    Command := 'preflight';
    if UpdateMode and FileExists(ExpandConstant('{commonappdata}\Domovoi\host\in\{#UbuntuName}')) then
      Command := 'preflight --have-image';
    { From an offline kit: no internet check, and the helper's guard on. }
    if KitMode then
      Command := 'preflight --skip-internet --offline';
    if KitMode then
      CheckPage.SetText('Checking Windows, memory, disk and ports...', '')
    else
      CheckPage.SetText('Checking Windows, memory, disk, ports and the internet...', '');
    CheckPage.Show;
    try
      CheckPage.Animate;
      Ran := RunHelper(TmpHelper, Command, Preflight, Code);
    finally
      CheckPage.Hide;
    end;
    if not Ran or (Code <> 0) then
    begin
      if UpdateMode then
      begin
        FailureText := 'Setup could not check this PC (' + KvValue(Preflight, 'error') + ').';
        Exit;
      end;
      if MsgBox('Setup could not check this PC (' + KvValue(Preflight, 'error') + ').' + #13#10#13#10 +
        'If Windows blocked DomovoiSetupHelper.exe, Smart App Control or an antivirus program is stopping unsigned programs: this installer is not code-signed yet (see docs/WINDOWS_HOST.md, "Unsigned").',
        mbError, MB_RETRYCANCEL) = IDRETRY then
        Continue;
      Exit;
    end;
    Fatal := KvValues(Preflight, 'fatal');
    if GetArrayLength(Fatal) > 0 then
    begin
      if UpdateMode then
      begin
        FailureText := 'Domovoi can''t be updated on this PC now: ' + JoinLines(Fatal, '');
        Exit;
      end;
      { No internet here: an offline kit made by an online Domovoi server
        installs without it. }
      if not KitMode and (KvValue(Preflight, 'internet') = 'fail') then
      begin
        R := TaskDialogMsgBox('Domovoi can''t be installed on this PC yet', JoinLines(Fatal, '') + #13#10 +
          'No internet here? Install from an offline kit: a USB stick an online Domovoi server makes (Settings > System > Offline kit).',
          mbCriticalError, MB_ABORTRETRYIGNORE, ['Check again', 'Use an offline kit...', 'Cancel Setup'], 0);
        if R = IDIGNORE then
          ChooseKit;
        if (R = IDRETRY) or (R = IDIGNORE) then
          Continue;
        Exit;
      end;
      if TaskDialogMsgBox('Domovoi can''t be installed on this PC yet', JoinLines(Fatal, ''), mbCriticalError,
        MB_RETRYCANCEL, ['Check again', 'Cancel Setup'], 0) = IDRETRY then
        Continue;
      Exit;
    end;
    { The kit, before anything changes (once per kit: Check again on a
      port pop-up doesn't read the stick again). }
    WslState := KvValue(Preflight, 'wsl');
    if KitMode and not KitChecked then
    begin
      RunKitCheck(KitLines);
      Fatal := KvValues(KitLines, 'fatal');
#ifndef ProvisionAiChoice
      if (GetArrayLength(Fatal) = 0) and not KitAi then
      begin
        SetArrayLength(Fatal, 1);
        Fatal[0] := 'This kit has no AI models, and this Setup always installs them. Make a kit with AI chat ticked, or use the DomovoiSetup.exe on the stick.';
      end;
#endif
      if GetArrayLength(Fatal) > 0 then
      begin
        R := TaskDialogMsgBox('This offline kit can''t be used', JoinLines(Fatal, ''), mbCriticalError,
          MB_ABORTRETRYIGNORE, ['Check again', 'Choose another folder...', 'Cancel Setup'], 0);
        if R = IDIGNORE then
          ChooseKit;
        if (R = IDRETRY) or (R = IDIGNORE) then
          Continue;
        Exit;
      end;
      KitChecked := True;
    end;
    if not ResolvePorts(Preflight, TmpHelper, 'preflight --ports-only', 'Cancel Setup') then
      Exit;
    Break;
  end;
  LanIp := KvValue(Preflight, 'lan_ip');
  LanMac := KvValue(Preflight, 'lan_mac');
  LanSubnet := KvValue(Preflight, 'lan_subnet');
  LanPrivate := KvValue(Preflight, 'lan_private');
  WslMemGb := KvValue(Preflight, 'wsl_memory_gb');
  AiModelsGb := KvValue(Preflight, 'ai_models_gb');
  VmpState := KvValue(Preflight, 'vmp');
  WslState := KvValue(Preflight, 'wsl');
  Warn := KvValues(Preflight, 'warn');
  PreflightPage.RichEditViewer.Lines.Text :=
    'Windows 11 build ' + KvValue(Preflight, 'os_build') + ' (' + KvValue(Preflight, 'os_edition') + '), ' +
      KvValue(Preflight, 'os_arch') + #13#10 +
    'Memory: ' + KvValue(Preflight, 'ram_gb') + ' GB; Domovoi''s Linux system may use up to ' + WslMemGb + ' GB' + #13#10 +
    'Virtualization: ' + KvValue(Preflight, 'virtualization') + #13#10 +
    'Free disk space: ' + KvValue(Preflight, 'disk_free_gb') + ' GB (' + KvValue(Preflight, 'disk_media') + ')' + #13#10 +
    'WSL: ' + KvValue(Preflight, 'wsl_version') + ' (' + WslState + '); Virtual Machine Platform: ' + VmpState + #13#10 +
    'Network: ' + LanIp + ' on ' + KvValue(Preflight, 'lan_interface') + ' (' + KvValue(Preflight, 'lan_category') +
      ' network, subnet ' + LanSubnet + ')' + #13#10 +
    InternetLine +
    'Smart App Control: ' + KvValue(Preflight, 'smart_app_control') + #13#10 +
    'Ports Domovoi needs: free' + #13#10 + #13#10 +
    JoinLines(Warn, 'Note: ');
  PreflightDone := True;
  ReadSavedAi;
  Result := True;
end;

{ "Include AI chat and questions?" (owner decision 2026-10-21): yes or no,
  as the page says. The page starts at Yes, at this home's current answer
  on a re-run (ReadSavedAi), or at the /ai= answer on the command line (the
  resume after a restart passes it). Without ProvisionAiChoice (pins.iss:
  the pinned provision.sh always pulls the models) there is no page: yes. }
function AiAnswer: String;
begin
#ifdef ProvisionAiChoice
  if RadioAiNo.Checked then
    Result := 'no'
  else
    Result := 'yes';
#else
  Result := 'yes';
#endif
end;

{ For the helper's prepare --ai: the answer, or keep when it is a Yes that
  came from the install record alone and was left as it was (the
  household's own value could not be read, by a DomovoiHost from before
  that request or one not running): provision.sh then gets neither option
  and keeps what domovoi/.env says, so a home that turned AI off since
  stays off. The resume after a restart passes it on as /ai=. }
function AiPrepareArg: String;
begin
  Result := AiAnswer;
  { Update mode: as the home has it now (a recorded No stays No). }
  if UpdateMode then
  begin
    Result := 'keep';
    Exit;
  end;
#ifdef ProvisionAiChoice
  if Resumed then
  begin
    if CompareText(ExpandConstant('{param:ai|}'), 'keep') = 0 then
      Result := 'keep';
  end
  else if (ExpandConstant('{param:ai|}') = '') and (AiSavedFrom = 'record') and (AiSavedDefault = 'yes') and (Result = 'yes') then
    Result := 'keep';
#endif
end;

{ After the install: what provision.sh reported (AiReported), else the
  answer. }
function AiOutcome: String;
begin
  if AiReported <> '' then
    Result := AiReported
  else
    Result := AiAnswer;
end;

{ What the AI models download on this PC (the helper's preflight works it
  out from the memory the distro gets), or both sizes when it didn't say. }
function AiModelsText: String;
begin
  if AiModelsGb <> '' then
    Result := 'about ' + AiModelsGb + ' GB of AI models'
  else
    Result := 'about 6.4 GB of AI models (about 9.1 GB on a PC with 32 GB of memory or more)';
end;

function ConsentText: String;
var
  N: Integer;
  S: String;
begin
  S := '';
  N := 1;
  if VmpState <> 'enabled' then
  begin
    S := S + IntToStr(N) + '. Turn on the Windows feature Virtual Machine Platform, which WSL needs. Windows restarts once; Setup carries on after you sign in. (Left on when Domovoi is uninstalled: other programs may use it.)' + #13#10#13#10;
    N := N + 1;
  end;
  if WslState <> 'ok' then
  begin
    S := S + IntToStr(N) + '. Install Microsoft''s WSL 2.7.14. (Left installed when Domovoi is uninstalled: it is shared.)' + #13#10#13#10;
    N := N + 1;
  end;
  S := S + IntToStr(N) + '. A hidden Windows account, domovoi-svc: a standard user (not an administrator) with a random password nobody sees, hidden from the sign-in screen, allowed to run as a service, and refused for sign-in at the screen, over Remote Desktop or over the network.' + #13#10#13#10;
  N := N + 1;
  S := S + IntToStr(N) + '. A Windows service, DomovoiHost (starts automatically), running as domovoi-svc. It keeps Domovoi running even when nobody is signed in.' + #13#10#13#10;
  N := N + 1;
  S := S + IntToStr(N) + '. A private WSL distro named "domovoi" (Ubuntu 26.04) that belongs to domovoi-svc, stored in C:\ProgramData\Domovoi. WSL settings for domovoi-svc alone: mirrored networking, memory up to ' + WslMemGb + ' GB. Your own WSL distros, your .wslconfig and Docker Desktop are not touched.' + #13#10#13#10;
  N := N + 1;
  if LanPrivate = '1' then
    S := S + IntToStr(N) + '. Seven Hyper-V firewall rules for WSL, named Domovoi-..., so phones and rooms on your home network (' + LanSubnet + ') reach Domovoi: TCP 80 (the short address: domovoi.local opens without :6369, when nothing else on this PC uses port 80), 6369 (dashboard), 6370 (voice), 8050-8099 (music streams), 8096, 7676 and 9467 (plugins). They apply to every WSL VM on this PC, your own WSL distros and Docker Desktop''s included: anything of yours listening on those ports in mirrored mode becomes reachable from your home network too. And one Windows firewall rule, Domovoi-mDNS-5353: DomovoiHost.exe may receive UDP 5353 (mDNS) from your home network on Private and Domain networks, so other devices find this PC as domovoi.local.' + #13#10#13#10
  else
    S := S + IntToStr(N) + '. No firewall rules: this PC has no private home-network address, so Domovoi answers on this PC only.' + #13#10#13#10;
  N := N + 1;
  S := S + IntToStr(N) + '. A media folder you can open in Explorer (you choose it next; default C:\Users\Public\Domovoi) with Music, Pictures, Videos, Documents, Audiobooks and Podcasts inside. domovoi-svc may read and write it; if Setup makes the folder, every user of this PC (Users) may too.' + #13#10#13#10;
  N := N + 1;
  S := S + IntToStr(N) + '. C:\Program Files\Domovoi (this program) and C:\ProgramData\Domovoi (Domovoi''s Linux system, logs and settings; only administrators and domovoi-svc can open it). Setup won''t use a C:\ProgramData\Domovoi it didn''t make.' + #13#10#13#10;
  N := N + 1;
  S := S + IntToStr(N) + '. Domovoi''s icon in the notification area (DomovoiTray.exe), for every account on this PC: it starts when anyone signs in (a DomovoiTray value under HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run), and the Start menu gets a Domovoi entry. It shows whether Domovoi is running, opens the dashboard and prepares satellite cards; it never runs as an administrator, and each account can turn its start off.' + #13#10#13#10;
  N := N + 1;
  S := S + IntToStr(N) + '. Setup''s install record in the registry, HKLM\SOFTWARE\Domovoi: what Setup changed, so the uninstaller can undo it.' + #13#10#13#10;
  { Without the AI models: the 10.5-13.5 GB less the 6.4 or 9.1 GB of
    them (AiModels in windows\common\Provision.cs). }
  if KitMode then
    S := S + 'Nothing is downloaded: Setup installs from the offline kit at ' + KitRoot + ' (Domovoi ' + KitVersion + '). Ubuntu (and WSL, if this PC needs it) come off the stick now; then DomovoiHost opens the stick read-only inside the distro, and Domovoi''s code at commit {#Copy(DomovoiCommit, 1, 7)} (later updates follow the {#DomovoiChannel} channel{#ChannelWords}; you choose when, in Settings > About & updates), Docker Engine, Python packages, the speech recognition model and, if you include them, the AI models are copied from it, each checked against a pinned hash. Keep the stick plugged in until Setup finishes.' + #13#10#13#10
  else if AiAnswer = 'no' then
    S := S + 'Downloads: about 4.5 GB (no AI models: you left out AI chat and questions). Ubuntu (418 MB) now; then, inside the distro, Domovoi''s code at commit {#Copy(DomovoiCommit, 1, 7)} (later updates follow the {#DomovoiChannel} channel{#ChannelWords}; you choose when, in Settings > About & updates), Docker Engine, Python packages and the speech recognition model, each checked against a pinned hash.' + #13#10#13#10
  else
    S := S + 'Downloads: about 10.5-13.5 GB (the AI models are most of it; the size depends on the models this PC''s memory gets). Ubuntu (418 MB) now; then, inside the distro, Domovoi''s code at commit {#Copy(DomovoiCommit, 1, 7)} (later updates follow the {#DomovoiChannel} channel{#ChannelWords}; you choose when, in Settings > About & updates), Docker Engine, Python packages and the AI models (one of them reads pictures in chat), each checked against a pinned hash.' + #13#10#13#10;
  S := S + 'Not changed: power and sleep settings (the icon''s "Keep this PC awake" only holds Windows awake while someone is signed in), Windows Update, Smart App Control, your user accounts and files.' + #13#10#13#10;
  S := S + 'This installer is not code-signed yet: Windows SmartScreen may have warned before Setup started, and Windows may ask again about its programs.' + #13#10#13#10;
  S := S + 'Uninstalling never deletes files in your media folders: a media folder is removed only if it is empty.' + #13#10#13#10;
  S := S + 'You can undo all of this later from Settings > Apps > Installed apps > Domovoi.';
  Result := S;
end;

{ -- the internet question (PLAN 4.2a, exact text) --------------------- }

procedure InternetChoiceClick(Sender: TObject);
begin
  WizardForm.NextButton.Enabled := RadioAlways.Checked or RadioSometimes.Checked or RadioNever.Checked;
end;

procedure InternetDocClick(Sender: TObject; const Link: String; LinkType: TSysLinkType);
var
  Err: Integer;
begin
  ShellExecAsOriginalUser('open', InternetDoc, '', '', SW_SHOWNORMAL, ewNoWait, Err);
end;

function AddText(Page: TWizardPage; const Caption: String; Top, Indent: Integer; Italic, Bold: Boolean): TNewStaticText;
begin
  Result := TNewStaticText.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := Indent;
  Result.Top := Top;
  Result.Width := Page.SurfaceWidth - Indent;
  Result.WordWrap := True;
  Result.AutoSize := True;
  Result.Caption := Caption;
  if Italic then
    Result.Font.Style := [fsItalic];
  if Bold then
    Result.Font.Style := [fsBold];
  Result.AdjustHeight;
end;

function NewRadio(Page: TWizardPage; const Caption: String; Top: Integer): TNewRadioButton;
begin
  Result := TNewRadioButton.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := 0;
  Result.Top := Top;
  Result.Width := Page.SurfaceWidth;
  Result.Height := ScaleY(18);
  Result.Caption := Caption;
  Result.Font.Style := [fsBold];
  Result.Checked := False;
end;

function AddRadio(Page: TWizardPage; const Caption: String; Top: Integer): TNewRadioButton;
begin
  Result := NewRadio(Page, Caption, Top);
  Result.OnClick := @InternetChoiceClick;
end;

{ -- the AI question (owner decision 2026-10-21) ------------------------ }

{ AI chat and questions are optional: Yes (picked to begin with) downloads
  the AI models, No leaves them out (provision.sh --no-ai-models). Speech
  recognition, Domovoi's voices and the wake word come either way. The
  Yes line's size is set when the page shows (AiYesText), once the
  preflight has said what this PC's memory gets; it starts with both. }

{ Places what follows the Yes line under it, as tall as its text is now. }
procedure LayoutAiPage;
var
  Y: Integer;
begin
  AiYesText.AdjustHeight;
  Y := AiYesText.Top + AiYesText.Height + ScaleY(8);
  RadioAiNo.Top := Y;
  AiNoText.Top := RadioAiNo.Top + RadioAiNo.Height;
  AiLaterText.Top := AiNoText.Top + AiNoText.Height + ScaleY(10);
end;

{ The words under Yes and No: the download, or the kit's copy. }
function AiYesWords: String;
begin
  if KitMode and not KitAi then
    Result := 'This offline kit has no AI models. Make a kit with AI chat ticked, or turn AI on later in the dashboard, with internet.'
  else if KitMode then
    Result := 'Ask Domovoi questions and chat with it. Setup copies the AI models from the offline kit.'
  else
    Result := 'Ask Domovoi questions and chat with it. Downloads ' + AiModelsText + '.';
end;

function AiNoWords: String;
begin
  if KitMode then
    Result := 'Voice commands, music, timers and everything else still work.'
  else
    Result := 'Voice commands, music, timers and everything else still work. Setup downloads about 4.5 GB in all.';
end;

procedure CreateAiPage(AfterID: Integer);
var
  T: TNewStaticText;
  Y: Integer;
begin
  AiPage := CreateCustomPage(AfterID, 'Include AI chat and questions?', 'Yes is picked for you; change it if you like.');
  T := AddText(AiPage, 'Domovoi''s AI models answer questions and chat with you. They run on this PC, like the rest of Domovoi, and they are most of the download.', 0, 0, False, False);
  Y := T.Top + T.Height + ScaleY(10);
  RadioAiYes := NewRadio(AiPage, 'Yes, include AI chat and questions (recommended).', Y);
  AiYesText := AddText(AiPage, AiYesWords, RadioAiYes.Top + RadioAiYes.Height, ScaleX(18), True, False);
  Y := AiYesText.Top + AiYesText.Height + ScaleY(8);
  RadioAiNo := NewRadio(AiPage, 'No, leave AI out.', Y);
  AiNoText := AddText(AiPage, AiNoWords, RadioAiNo.Top + RadioAiNo.Height, ScaleX(18), True, False);
  Y := AiNoText.Top + AiNoText.Height + ScaleY(10);
  AiLaterText := AddText(AiPage, 'You can turn AI on later in the dashboard: Settings > Models.', Y, 0, False, False);
  if CompareText(ExpandConstant('{param:ai|yes}'), 'no') = 0 then
    RadioAiNo.Checked := True
  else
    RadioAiYes.Checked := True;
end;

{ -- the internet page -------------------------------------------------- }

{ Its first line, with the download the AI answer means (set again each
  time the page shows: the AI page comes before it). }
function InternetIntroText(WithAi: Boolean): String;
begin
  if KitMode then
    Result := 'Setup installs from the offline kit and needs no internet. After that, Domovoi works with or without it. Your voice and recordings never leave this PC either way.'
  else if not WithAi then
    Result := 'Setup needs the internet right now to download Domovoi (about 4.5 GB). After that, Domovoi works with or without it. Your voice and recordings never leave this PC either way.'
  else
    Result := 'Setup needs the internet right now to download Domovoi (about 10.5-13.5 GB). After that, Domovoi works with or without it. Your voice and recordings never leave this PC either way.';
end;

procedure CreateInternetPage(AfterID: Integer);
var
  T: TNewStaticText;
  L: TNewLinkLabel;
  Y: Integer;
begin
  InternetPage := CreateCustomPage(AfterID, 'Will this PC have internet after setup?', 'Pick one answer to continue.');
  { Laid out with the longer text, so the shorter one never overlaps. }
  InternetIntro := AddText(InternetPage, InternetIntroText(True), 0, 0, False, False);
  T := InternetIntro;
  Y := T.Top + T.Height + ScaleY(10);
  RadioAlways := AddRadio(InternetPage, 'Yes, always. It''s on our home internet.', Y);
  T := AddText(InternetPage, 'Turns on the extras that use the internet: web answers (weather, scores, news), song lyrics, other names for artists, podcast downloads. They pause by themselves if the internet drops.', RadioAlways.Top + RadioAlways.Height, ScaleX(18), True, False);
  Y := T.Top + T.Height + ScaleY(8);
  RadioSometimes := AddRadio(InternetPage, 'Sometimes. The connection comes and goes, or it''s slow or metered.', Y);
  T := AddText(InternetPage, 'Small lookups run whenever the internet is up. Big automatic downloads stay off until you turn them on.', RadioSometimes.Top + RadioSometimes.Height, ScaleX(18), True, False);
  Y := T.Top + T.Height + ScaleY(8);
  RadioNever := AddRadio(InternetPage, 'No. Keep everything in the house.', Y);
  T := AddText(InternetPage, 'There''s no internet here, or I don''t want Domovoi to use it. Domovoi won''t contact the internet at all. Online extras are switched off; music, timers, intercom, voice and everything else on your network work as normal.', RadioNever.Top + RadioNever.Height, ScaleX(18), True, False);
  Y := T.Top + T.Height + ScaleY(10);
  L := TNewLinkLabel.Create(InternetPage);
  L.Parent := InternetPage.Surface;
  L.Left := 0;
  L.Top := Y;
  L.Width := InternetPage.SurfaceWidth;
  L.Caption := 'With the internet on, some extras send short text out, such as a web search you asked for or a song title. <a href="' + InternetDoc + '">What goes out</a>';
  L.OnLinkClick := @InternetDocClick;
  L.AdjustHeight;
  Y := L.Top + L.Height + ScaleY(6);
  AddText(InternetPage, 'You can change this later in the dashboard: Settings > Privacy > Internet.', Y, 0, False, False);
end;

function InternetAnswer: String;
begin
  Result := ExpandConstant('{param:internet|}');
  if Result <> '' then
    Exit;
  if UpdateMode then
  begin
    { The answer Setup recorded; an existing domovoi/.env's own answer is
      never written over (provision.sh), so Settings > Privacy > Internet stands. }
    Result := SavedInternet;
    Exit;
  end;
  if RadioAlways.Checked then
    Result := 'always'
  else if RadioSometimes.Checked then
    Result := 'sometimes'
  else if RadioNever.Checked then
    Result := 'never';
end;


{ -- the house identity (S3-4, owner decision 2026-11-22) --------------- }

{ Every room is set up to trust this house's identity, a key the server
  makes on its first start. A plain reinstall makes a new one, and the
  rooms then refuse it (they only say "my server changed"). So Setup offers:
  the data a keep-data uninstall left (C:\ProgramData\Domovoi-kept-<time>:
  the database, settings and the identity, imported in place), a house
  identity backup file (Settings > System > About & updates; checked in
  the distro before use), or a fresh start. After a restart the choice
  comes back on the command line (/usekept=, /identity=). }

procedure IdentityChoiceClick(Sender: TObject);
begin
  IdentityEdit.Enabled := RadioIdentityBackup.Checked;
  IdentityBrowse.Enabled := RadioIdentityBackup.Checked;
end;

procedure IdentityBrowseClick(Sender: TObject);
var
  F: String;
begin
  F := IdentityEdit.Text;
  if GetOpenFileName('Choose the house identity backup', F, '', 'House identity backup (*.json)|*.json|All files|*.*', 'json') then
    IdentityEdit.Text := F;
end;

procedure CreateIdentityPage(AfterID: Integer);
var
  T: TNewStaticText;
  Y: Integer;
begin
  IdentityPage := CreateCustomPage(AfterID, 'Your house''s identity', 'Installing Domovoi again? Keep your rooms working.');
  T := AddText(IdentityPage, 'Every room device is set up to trust one Domovoi server, by its identity. A new install has a new identity, so rooms set up before won''t connect to it until they are set up again, unless you bring the identity back.', 0, 0, False, False);
  Y := T.Top + T.Height + ScaleY(10);
  RadioIdentityKept := NewRadio(IdentityPage, 'Use the data kept from the last install (recommended).', Y);
  RadioIdentityKept.OnClick := @IdentityChoiceClick;
  IdentityKeptText := AddText(IdentityPage, 'No kept data was found on this PC.', RadioIdentityKept.Top + RadioIdentityKept.Height, ScaleX(18), True, False);
  Y := IdentityKeptText.Top + IdentityKeptText.Height + ScaleY(8);
  RadioIdentityBackup := NewRadio(IdentityPage, 'Restore the house identity from a backup file.', Y);
  RadioIdentityBackup.OnClick := @IdentityChoiceClick;
  T := AddText(IdentityPage, 'The file from Settings > System > About & updates > House identity backup. Your rooms reconnect; the database starts fresh.', RadioIdentityBackup.Top + RadioIdentityBackup.Height, ScaleX(18), True, False);
  Y := T.Top + T.Height + ScaleY(4);
  IdentityEdit := TNewEdit.Create(IdentityPage);
  IdentityEdit.Parent := IdentityPage.Surface;
  IdentityEdit.SetBounds(ScaleX(18), Y, IdentityPage.SurfaceWidth - ScaleX(18) - ScaleX(90), ScaleY(21));
  IdentityBrowse := TNewButton.Create(IdentityPage);
  IdentityBrowse.Parent := IdentityPage.Surface;
  IdentityBrowse.Caption := 'Browse...';
  IdentityBrowse.SetBounds(IdentityPage.SurfaceWidth - ScaleX(82), Y - ScaleY(1), ScaleX(82), ScaleY(23));
  IdentityBrowse.OnClick := @IdentityBrowseClick;
  Y := Y + ScaleY(30);
  RadioIdentityFresh := NewRadio(IdentityPage, 'Start fresh, with a new identity.', Y);
  RadioIdentityFresh.OnClick := @IdentityChoiceClick;
  T := AddText(IdentityPage, 'For a first install. Rooms set up for an earlier install must be set up again.', RadioIdentityFresh.Top + RadioIdentityFresh.Height, ScaleX(18), True, False);
  RadioIdentityKept.Enabled := False;
  RadioIdentityFresh.Checked := True;
  IdentityChoiceClick(nil);
end;

{ Ask the helper (read-only) for the newest kept data, once. }
procedure FindKeptData;
var
  Lines: TArrayOfString;
  Code: Integer;
begin
  if KeptLooked then
    Exit;
  KeptLooked := True;
  KeptDir := '';
  if RunHelper(TmpHelper, 'kept-data', Lines, Code) and (Code = 0) then
    KeptDir := KvValue(Lines, 'kept_dir');
  if KeptDir <> '' then
  begin
    IdentityKeptText.Caption := 'Found: ' + KeptDir + ' (kept ' + KvValue(Lines, 'kept_when') + ', ' + KvValue(Lines, 'kept_gb') + ' GB). Your database, settings and the house identity come back, and your rooms reconnect by themselves.';
    RadioIdentityKept.Enabled := True;
    RadioIdentityKept.Checked := True;
  end;
  IdentityChoiceClick(nil);
end;

{ The kept folder to use, or ''. }
function UseKeptDir: String;
begin
  Result := ExpandConstant('{param:usekept|}');
  if Result <> '' then
    Exit;
  if (not UpdateMode) and (KeptDir <> '') and RadioIdentityKept.Checked then
    Result := KeptDir;
end;

{ The backup file to restore, or ''. }
function IdentityBackupFile: String;
begin
  Result := ExpandConstant('{param:identity|}');
  if Result <> '' then
    Exit;
  if (not UpdateMode) and RadioIdentityBackup.Checked then
    Result := Trim(IdentityEdit.Text);
end;

function IdentityJobArgs: String;
begin
  Result := '';
  if UseKeptDir <> '' then
    Result := ' --import-in-place'
  else if IdentityBackupFile <> '' then
    Result := ' --restore-identity ' + AddQuotes(IdentityBackupFile);
end;

{ -- the finish page ---------------------------------------------------- }

procedure CopyCodeClick(Sender: TObject);
begin
  CodeEdit.SelectAll;
  SendMessage(CodeEdit.Handle, WM_COPY, 0, 0);
  CopyButton.Caption := 'Copied';
end;

procedure OpenDomovoiClick(Sender: TObject);
var
  Err: Integer;
begin
  { The steps say the code is on the clipboard when the page opens: copy it
    first, as Copy does. }
  if CodeEdit.Visible and (CodeEdit.Text <> '') then
    CopyCodeClick(Sender);
  { localhost, not the LAN address: a secure context for the browser (PLAN
    4.12); with this build's mark, so no old service worker answers it
    from its cache (OpenUrl). }
  ShellExecAsOriginalUser('open', OpenUrl, '', '', SW_SHOWNORMAL, ewNoWait, Err);
end;

{ The steps to the admin account, by the dashboard's own labels (web/static:
  the Admin card's "set up admin", the "first-run admin setup" dialog and
  its "setup code", "confirm password" and "set password"; the dashboard's
  domovoi/tests/test_web_settings_routes.py holds it to them). With
  DashboardWelcome (pins.iss) the same address opens the first-run welcome
  instead: "Welcome to Domovoi!" with those three fields, then "You're all
  set!" with the household token and "Keep this one"
  (domovoi/tests/test_windows_installer.py holds these words to
  web/static/components.jsx). }
function FinishSteps: String;
begin
#ifdef DashboardWelcome
  Result := '1. Click Open Domovoi. It copies the code and opens Domovoi, which greets you with "Welcome to Domovoi!" (if it doesn''t, click "set up admin" in the Admin box).' + #13#10;
#else
#ifdef DashboardTabRoutes
  Result := '1. Click Open Domovoi. It copies the code and opens Settings > Advanced with "first-run admin setup" showing (if it isn''t, click "set up admin" in the Admin box).' + #13#10;
#else
  Result := '1. Click Open Domovoi. It copies the code and opens Domovoi''s Settings. Click the Configuration tab (the last one), then "set up admin" in the Admin box.' + #13#10;
#endif
#endif
  Result := Result + '2. Paste the code into "setup code", choose an admin password of at least 10 characters, type it again in "confirm password", and click "set password".';
#ifdef DashboardWelcome
  Result := Result + #13#10 + '3. Under "You''re all set!", copy your household token: every phone, tablet and browser asks for it once. It is not your admin password. Click "Keep this one", or "Want an easier one?" to choose your own.';
#endif
end;

{ The names of the graphics cards Setup found, each once, with a count for
  more than one of the same: "NVIDIA GeForce RTX 4070 Ti SUPER (x2)", the
  x being the multiplication sign. Raw is the host's gpu= value, the names
  joined with ", ". }
function GpuNames(const Raw: String): String;
var
  Parts, Names: TArrayOfString;
  Counts: array of Integer;
  I, J, N: Integer;
  Name: String;
  Found: Boolean;
begin
  Parts := StringSplit(Raw, [', '], stExcludeEmpty);
  N := 0;
  for I := 0 to GetArrayLength(Parts) - 1 do
  begin
    Name := Trim(Parts[I]);
    if Name = '' then
      Continue;
    Found := False;
    for J := 0 to N - 1 do
      if Names[J] = Name then
      begin
        Counts[J] := Counts[J] + 1;
        Found := True;
        Break;
      end;
    if not Found then
    begin
      SetArrayLength(Names, N + 1);
      SetArrayLength(Counts, N + 1);
      Names[N] := Name;
      Counts[N] := 1;
      N := N + 1;
    end;
  end;
  Result := '';
  for J := 0 to N - 1 do
  begin
    if J > 0 then
      Result := Result + ', ';
    Result := Result + Names[J];
    if Counts[J] > 1 then
      Result := Result + ' (' + #$00D7 + IntToStr(Counts[J]) + ')';
  end;
end;

{ Where the AI models run, from what Setup found (the host's gpu= value:
  the NVIDIA cards provision.sh saw, "none", or '' when it couldn't tell).
  Ollama uses an NVIDIA card it can reach; speech recognition always runs
  on the processor (provision.sh writes WHISPER_DEVICE=cpu). Nothing here
  claims more than that (owner, 2026-10-12: never show what isn't true). }
function ModelsText(const Raw, Tier: String): String;
var
  Names: String;
begin
  { AI left out (as provision.sh reported it): no chat or voice-command
    models to run anywhere. }
  if AiOutcome = 'no' then
  begin
    Result := 'AI chat and questions are left out; you can turn them on later in Settings > Models. Speech recognition runs on the processor.';
    Exit;
  end;
  Names := '';
  if (Raw <> '') and (Raw <> 'none') then
    Names := GpuNames(Raw);
  if Names <> '' then
    Result := 'Chat and voice-command models run on: ' + Names + '. Speech recognition runs on the processor.'
  else if Raw = 'none' then
    Result := 'No NVIDIA graphics card found: chat, voice-command and speech recognition models all run on the processor.'
  else
    Result := 'Speech recognition runs on the processor. Setup could not check for an NVIDIA graphics card for the chat and voice-command models.';
  if Tier <> '' then
    Result := Result + ' Model size: ' + Tier + '.';
end;

{ The setup-code QR in a window of its own: the dashboard's address on
  this network with the code in the fragment (never the path or query, so
  it never reaches a log), which a phone's camera opens with the code
  filled in. Drawn here from the helper's modules: no image file holds the
  code. }
procedure PhoneCodeClick(Sender: TObject);
var
  F: TSetupForm;
  Img: TBitmapImage;
  Lead: TNewStaticText;
  CloseButton: TNewButton;
  N, Cell, Quiet, Side, X, Y, W: Integer;
begin
  N := SetupQrSize;
  if (N <= 0) or (Length(SetupQrBits) <> N * N) then
    Exit;
  Quiet := 4;
  Cell := ScaleX(6);
  Side := (N + 2 * Quiet) * Cell;
  W := Side + ScaleX(40);
  if W < ScaleX(360) then
    W := ScaleX(360);
  F := CreateCustomForm(W, Side + ScaleY(130), False, True);
  try
    F.Caption := 'Domovoi on your phone';
    Lead := TNewStaticText.Create(F);
    Lead.Parent := F;
    Lead.WordWrap := True;
    Lead.Caption := 'Point your phone''s camera at the code. It opens Domovoi with your setup code filled in: choose an admin password there. The phone must be on the same Wi-Fi as this PC.';
    Lead.SetBounds(ScaleX(20), ScaleY(14), W - ScaleX(40), ScaleY(48));
    Lead.AdjustHeight;
    Img := TBitmapImage.Create(F);
    Img.Parent := F;
    Img.SetBounds((W - Side) div 2, Lead.Top + Lead.Height + ScaleY(12), Side, Side);
    Img.Bitmap.Width := Side;
    Img.Bitmap.Height := Side;
    Img.Bitmap.Canvas.Brush.Color := clWhite;
    Img.Bitmap.Canvas.Pen.Color := clWhite;
    Img.Bitmap.Canvas.Rectangle(0, 0, Side, Side);
    Img.Bitmap.Canvas.Brush.Color := clBlack;
    Img.Bitmap.Canvas.Pen.Color := clBlack;
    for Y := 0 to N - 1 do
      for X := 0 to N - 1 do
        if SetupQrBits[Y * N + X + 1] = '1' then
          Img.Bitmap.Canvas.Rectangle((X + Quiet) * Cell, (Y + Quiet) * Cell, (X + Quiet + 1) * Cell, (Y + Quiet + 1) * Cell);
    CloseButton := TNewButton.Create(F);
    CloseButton.Parent := F;
    CloseButton.Caption := 'Close';
    CloseButton.ModalResult := mrOk;
    CloseButton.Default := True;
    CloseButton.Cancel := True;
    CloseButton.SetBounds(W - ScaleX(95), Img.Top + Side + ScaleY(14), ScaleX(75), ScaleY(23));
    F.ClientHeight := CloseButton.Top + CloseButton.Height + ScaleY(12);
    F.ActiveControl := CloseButton;
    F.FlipAndCenterIfNeeded(True, WizardForm, False);
    F.ShowModal;
  finally
    F.Free;
  end;
end;

procedure CopyLogsClick(Sender: TObject);
begin
  CopyLogs(AppHelper, 'install', '');
end;

procedure CreateFinishControls;
var
  P: TNewNotebookPage;
begin
  P := WizardForm.FinishedPage;
  CodeTitle := TNewStaticText.Create(WizardForm);
  CodeTitle.Parent := P;
  CodeTitle.Font.Style := [fsBold];
  CodeTitle.Caption := 'Your setup code (shown only here; it works for 24 hours)';
  CodeEdit := TNewEdit.Create(WizardForm);
  CodeEdit.Parent := P;
  CodeEdit.ReadOnly := True;
  CodeEdit.Font.Size := 11;
  CopyButton := TNewButton.Create(WizardForm);
  CopyButton.Parent := P;
  CopyButton.Caption := 'Copy';
  CopyButton.OnClick := @CopyCodeClick;
  CodeHint := TNewStaticText.Create(WizardForm);
  CodeHint.Parent := P;
  CodeHint.WordWrap := True;
  CodeHint.Caption := FinishSteps;
  OpenButton := TNewButton.Create(WizardForm);
  OpenButton.Parent := P;
  OpenButton.Caption := 'Open Domovoi';
  OpenButton.OnClick := @OpenDomovoiClick;
  PhoneCodeButton := TNewButton.Create(WizardForm);
  PhoneCodeButton.Parent := P;
  PhoneCodeButton.Caption := 'Set up from a phone';
  PhoneCodeButton.OnClick := @PhoneCodeClick;
  CopyLogsButton := TNewButton.Create(WizardForm);
  CopyLogsButton.Parent := P;
  CopyLogsButton.Caption := 'Copy logs';
  CopyLogsButton.OnClick := @CopyLogsClick;
  LanText := TNewStaticText.Create(WizardForm);
  LanText.Parent := P;
  LanText.WordWrap := True;
end;

procedure LayoutFinishControls;
var
  X, Y, W: Integer;
begin
  X := WizardForm.FinishedLabel.Left;
  W := WizardForm.FinishedLabel.Width;
  { Tight: the steps and both notes below have to fit the page. }
  Y := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(10);
  CodeTitle.SetBounds(X, Y, W, ScaleY(16));
  Y := Y + ScaleY(18);
  CopyButton.SetBounds(X + W - ScaleX(80), Y, ScaleX(80), ScaleY(25));
  CodeEdit.SetBounds(X, Y + ScaleY(1), W - ScaleX(88), ScaleY(23));
  Y := Y + ScaleY(30);
  CodeHint.SetBounds(X, Y, W, ScaleY(30));
  CodeHint.AdjustHeight;
  Y := CodeHint.Top + CodeHint.Height + ScaleY(8);
  { One of the two shows: Open Domovoi after an install, Copy logs after a failure. }
  OpenButton.SetBounds(X, Y, ScaleX(120), ScaleY(25));
  CopyLogsButton.SetBounds(X, Y, ScaleX(120), ScaleY(25));
  PhoneCodeButton.SetBounds(X + ScaleX(128), Y, ScaleX(150), ScaleY(25));
  Y := Y + ScaleY(32);
  LanText.SetBounds(X, Y, W, ScaleY(60));
  LanText.AdjustHeight;
end;

procedure ShowFinish;
var
  HasCode: Boolean;
begin
  HasCode := InstallOk and (SetupCodeText <> '');
  CodeTitle.Visible := HasCode;
  CodeEdit.Visible := HasCode;
  CopyButton.Visible := HasCode;
  CodeHint.Visible := HasCode;
  OpenButton.Visible := InstallOk;
  PhoneCodeButton.Visible := HasCode and (SetupQrSize > 0);
  CopyLogsButton.Visible := not InstallOk and FileExists(AppHelper);
  LanText.Visible := True;
  if InstallOk then
  begin
    WizardForm.FinishedHeadingLabel.Caption := 'Domovoi is running';
    WizardForm.FinishedLabel.Caption := 'Domovoi runs in the background from now on, also when nobody is signed in. You use it in your browser at ' + DashboardUrl + '.';
    CodeEdit.Text := SetupCodeText;
    if SetupCodeText = '' then
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 + 'Setup could not read the setup code. Restarting the DomovoiHost service writes a fresh one (see docs/WINDOWS_HOST.md).';
    { Over an existing install, provision.sh leaves the server's code where
      the dashboard's updates put it (2026-11-16: "Setup didn't update
      Linux"). Said only when DomovoiHost saw it really wasn't moved. }
    if CheckoutKept = 'yes' then
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 + 'Setup updated Domovoi''s programs on this PC. It didn''t change the server''s own code, which stays as it was: that updates with "Update Domovoi" in the Domovoi icon''s menu, or in the dashboard (Settings > About & updates).';
    LanText.Caption := 'Phones and rooms reach Domovoi at http://' + LanIp + ':6369. If it changes, rooms look for Domovoi again by themselves; to keep it fixed, reserve it for this PC in your router (DHCP reservation for ' + LanMac + ').' + #13#10;
    { The network name is for browsers; a phone pairs with the address. }
    if LanName <> '' then
      LanText.Caption := LanText.Caption + 'In a browser on your home network you can also type http://' + LanName + ':6369.' + #13#10;
    LanText.Caption := LanText.Caption + ModelsText(GpuRaw, TierText);
    { provision.sh's phone-app step is never fatal: when it couldn't
      download the Android app, its warning, in its words. }
    if (PhoneAppReported = 'no') and KitMode then
      LanText.Caption := LanText.Caption + #13#10 + 'The phone app wasn''t on the offline kit: upload it in Settings, or let this server use the internet once.'
    else if PhoneAppReported = 'no' then
      LanText.Caption := LanText.Caption + #13#10 + 'The phone app couldn''t be downloaded: this server needs the internet once, at some point, to put it here (or upload it in Settings).';
  end
  else
  begin
    WizardForm.FinishedHeadingLabel.Caption := 'Setup did not finish';
    if UndoOnExit then
      WizardForm.FinishedLabel.Caption := FailureText + #13#10#13#10 + 'When you click Finish, Domovoi''s uninstaller removes everything Setup changed.'
    else
      WizardForm.FinishedLabel.Caption := FailureText + #13#10#13#10 + 'What Setup changed so far is still there. Run Setup again to carry on, or remove it with Settings > Apps > Installed apps > Domovoi > Uninstall.';
    if not FileExists(AppHelper) then
      LanText.Caption := 'Setup stopped before it changed anything.'
    else if UndoOnExit then
      LanText.Caption := LogsHint + ' Copy them before you click Finish: removing Domovoi deletes that folder.'
    else
      LanText.Caption := LogsHint;
  end;
  WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
  LayoutFinishControls;
end;

{ -- the install (after the files are copied) -------------------------- }

{ True when the user clicked Stop Setup and confirmed. }
function StopConfirmed: Boolean;
begin
  Result := False;
  if not StopRequested then
    Exit;
  StopRequested := False;
  if MsgBox('Stop Setup now? Domovoi is not finished. You can run Setup again later and it carries on, or remove what Setup did.', mbConfirmation, MB_YESNO) = IDYES then
  begin
    FailureText := 'You stopped Setup before Domovoi was ready.';
    Result := True;
  end;
end;

{ One helper step with Retry and Stop. False: the user stopped Setup. }
function Step(const Title, Params: String; Percent: Integer; var Lines: TArrayOfString): Boolean;
var
  Code: Integer;
  Err: String;
begin
  while True do
  begin
    ProgressPage.SetText(Title, '');
    ProgressPage.SetProgress(Percent, 100);
    if StopConfirmed then
    begin
      Result := False;
      Exit;
    end;
    Log('Step: ' + Title);
    if RunHelper(AppHelper, Params, Lines, Code) and (Code = 0) then
    begin
      Result := True;
      Exit;
    end;
    Err := KvValue(Lines, 'error');
    if Err = '' then
      Err := 'the helper exited with code ' + IntToStr(Code);
    Log('Step failed: ' + Title + ': ' + Err);
    if not RetryOrStop(Title + ': it did not work', Err) then
    begin
      FailureText := Title + ' did not work: ' + Err;
      Result := False;
      Exit;
    end;
  end;
end;

function SizeText(const Bytes, Total: String): String;
begin
  Result := '';
  if Bytes = '' then
    Exit;
  Result := Format('%.1f', [StrToInt64Def(Bytes, 0) / 1073741824.0]) + ' GB';
  if Total <> '' then
    Result := Result + ' of ' + Format('%.1f', [StrToInt64Def(Total, 0) / 1073741824.0]) + ' GB';
end;

procedure StopClick(Sender: TObject);
begin
  StopRequested := True;
end;

{ Follows DomovoiHost until Domovoi answers. False: the user stopped, or
  gave up after the time limit. }
function WaitForHost: Boolean;
var
  Lines, Ignored, PortLines: TArrayOfString;
  Code, Percent, WantAttempt: Integer;
  State, Service, Err: String;
  Started, LimitMs: Cardinal;
begin
  Result := False;
  WantAttempt := 0;
  Started := GetTickCount;
  { Four hours: far more than a slow line needs for 10.5-13.5 GB. }
  LimitMs := 4 * 3600 * 1000;
  while True do
  begin
    if StopConfirmed then
      Exit;
    if GetTickCount - Started > LimitMs then
    begin
      if UpdateMode then
      begin
        FailureText := 'Domovoi was still not ready after ' + IntToStr(LimitMs div 3600000) + ' hours.';
        Exit;
      end;
      if MsgBox('Domovoi is still not ready after ' + IntToStr(LimitMs div 3600000) + ' hours. Keep waiting another hour?' + #13#10#13#10 +
        'What it is doing: ' + KvValue(Lines, 'message') + #13#10 + 'Logs: C:\ProgramData\Domovoi\logs (host.log, provision.log).', mbConfirmation, MB_YESNO) <> IDYES then
      begin
        FailureText := 'Domovoi was still not ready after ' + IntToStr(LimitMs div 3600000) + ' hours.';
        Exit;
      end;
      LimitMs := LimitMs + 3600 * 1000;
    end;
    if not RunHelper(AppHelper, 'host-status', Lines, Code) or (Code <> 0) then
    begin
      Wait(2000, ProgressPage.ProgressBar.Position);
      Continue;
    end;
    State := KvValue(Lines, 'state');
    Service := KvValue(Lines, 'service');
    { The helper steps took 0-14; the host's 0-100 maps onto 15-99. }
    Percent := 15 + StrToIntDef(KvValue(Lines, 'percent'), 0) * 84 div 100;
    if Percent > 99 then
      Percent := 99;
    { Until the host picks up a Retry, its status still shows the old failure. }
    if (State = 'failed') and (StrToIntDef(KvValue(Lines, 'attempt'), 0) < WantAttempt) then
      State := 'retrying';
    if State = 'ready' then
    begin
      GpuRaw := KvValue(Lines, 'gpu');
      LanName := KvValue(Lines, 'lan_name');
      TierText := KvValue(Lines, 'tier');
      { AI chat and questions as provision.sh settled them (its options,
        or the household's domovoi/.env): the last page says that, and the
        install record keeps it, not the click. }
      AiReported := KvValue(Lines, 'ai');
      PhoneAppReported := KvValue(Lines, 'phone_app');
      CheckoutKept := KvValue(Lines, 'checkout_kept');
      if AiReported <> '' then
        if not RunHelper(AppHelper, 'record-ai', Ignored, Code) or (Code <> 0) then
          Log('record-ai did not work: ' + KvValue(Ignored, 'error'));
      Result := True;
      Exit;
    end;
    ProgressPage.SetText(KvValue(Lines, 'message'), SizeText(KvValue(Lines, 'bytes'), KvValue(Lines, 'bytes_total')));
    ProgressPage.SetProgress(Percent, 100);
    { Right before Domovoi starts, DomovoiHost checks the ports again and
      waits while one is taken (state "ports"): the same pop-up per program
      as the first check; the host carries on by itself once they are free.
      (Only with a pinned provision.sh that has --stop-before.) }
    if State = 'ports' then
    begin
      if PortsTakenNow(PortLines) then
        if not ResolvePorts(PortLines, AppHelper, InstallPortCheck, 'Stop Setup') then
        begin
          FailureText := 'A port Domovoi needs is taken by another program: ' + KvValue(Lines, 'message');
          Exit;
        end;
      Wait(3000, Percent);
      Continue;
    end;
    Err := '';
    if State = 'failed' then
    begin
      { A port taken since the first check makes the start fail (2026-10-12:
        Docker's domovoi-postgres came back on 6432 during the downloads).
        Then the pop-up per program, not the generic error, and once the
        ports are free Setup asks the host to carry on by itself. }
      if PortsTakenNow(PortLines) then
      begin
        Log('Host failed with a port taken: ' + KvValue(Lines, 'host_error'));
        if not ResolvePorts(PortLines, AppHelper, InstallPortCheck, 'Stop Setup') then
        begin
          FailureText := 'Setting up Domovoi''s Linux system did not work: a port Domovoi needs is taken by another program.';
          Exit;
        end;
        if not Step('Asking DomovoiHost to try again', 'host-retry', ProgressPage.ProgressBar.Position, Ignored) then
          Exit;
        WantAttempt := StrToIntDef(KvValue(Ignored, 'attempt'), 0);
        Wait(1500, Percent);
        Continue;
      end;
      Err := KvValue(Lines, 'host_error');
    end
    else if (Service = 'stopped') or (Service = 'missing') then
      Err := 'the DomovoiHost service is not running'
    else if StrToIntDef(KvValue(Lines, 'age_sec'), 0) > 300 then
      Err := 'DomovoiHost has not reported for five minutes';
    if Err <> '' then
    begin
      Log('Host problem: ' + Err);
      if not RetryOrStop('Setting up Domovoi''s Linux system did not work', Err + #13#10#13#10 + 'Retry carries on where it stopped.') then
      begin
        FailureText := 'Setting up Domovoi''s Linux system did not work: ' + Err;
        Exit;
      end;
      if not Step('Asking DomovoiHost to try again', 'host-retry', ProgressPage.ProgressBar.Position, Ignored) then
        Exit;
      WantAttempt := StrToIntDef(KvValue(Ignored, 'attempt'), 0);
    end;
    Wait(1500, Percent);
  end;
end;

{ --lan-ip for the setup-code verb: this PC's address as the preflight
  found it, for the QR a phone scans (the helper checks it is a usable
  IPv4 address, and leaves the QR out otherwise). }
function LanIpArg: String;
begin
  Result := '';
  if LanIp <> '' then
    Result := ' --lan-ip ' + AddQuotes(LanIp);
end;

function ReadSetupCode: String;
var
  Lines: TArrayOfString;
  Code, Tries: Integer;
begin
  Result := '';
  for Tries := 1 to 5 do
  begin
    ProgressPage.SetText('Reading your setup code', '');
    if RunHelper(AppHelper, 'setup-code' + LanIpArg, Lines, Code) and (Code = 0) then
    begin
      Result := KvValue(Lines, 'code');
      SetupQrSize := StrToIntDef(KvValue(Lines, 'qr_size'), 0);
      SetupQrBits := KvValue(Lines, 'qr');
      if (SetupQrSize < 21) or (SetupQrSize > 177) or (Length(SetupQrBits) <> SetupQrSize * SetupQrSize) then
      begin
        SetupQrSize := 0;
        SetupQrBits := '';
      end;
      SetArrayLength(Lines, 0);
      if Result <> '' then
        Exit;
    end;
    Log('Setup code not available yet (try ' + IntToStr(Tries) + ')');
    Wait(5000, 99);
  end;
end;

{ The job step's --kit, in kit mode. }
function KitArg: String;
begin
  Result := '';
  if KitMode then
    Result := ' --kit ' + AddQuotes(RemoveBackslash(KitRoot));
end;

function MachineSetup: Boolean;
var
  Lines: TArrayOfString;
  Deny: String;
begin
  Result := False;
  StopRequested := False;
  ProgressPage.Show;
  StopButton.Visible := True;
  if KitMode then
    ProgressPage.Description := 'Most of this time is copying from the offline kit. Keep the stick plugged in; you can keep using the PC.'
  else if AiAnswer = 'no' then
    ProgressPage.Description := 'Most of this time is downloading. You can keep using the PC.';
  try
    if not Step('Recording your answers', 'prepare --internet ' + InternetAnswer + ' --ai ' + AiPrepareArg + ' --media-root ' + AddQuotes(RemoveBackslashUnlessRoot(MediaPage.Values[0])) + ' --commit {#DomovoiCommit}', 2, Lines) then Exit;
    Deny := '';
    if NoDenyLogon then
      Deny := ' --no-deny-logon';
    if not Step('Creating the hidden account domovoi-svc and the DomovoiHost service', 'account --exe ' + AddQuotes(ExpandConstant('{app}\DomovoiHost.exe')) + Deny, 4, Lines) then Exit;
    if not Step('Setting up the media folders', 'media', 6, Lines) then Exit;
    if not Step('Adding the firewall rules', 'firewall', 8, Lines) then Exit;
    if UseKeptDir <> '' then
      if not Step('Bringing back the data kept from the last install', 'adopt-kept --dir ' + AddQuotes(UseKeptDir), 10, Lines) then Exit;
    if not Step('Handing the work to DomovoiHost', 'job' + OfflineArg + KitArg + IdentityJobArgs + ' --image ' + AddQuotes(ImagePath) +
      ' --image-sha256 {#UbuntuSha256} --provision ' + AddQuotes(ProvisionPath) +
      ' --provision-sha256 {#ProvisionSha256} --repo-url {#DomovoiRepoUrl} --ref {#DomovoiCommit} --track-branch {#DomovoiChannel} --lan-ip auto', 12, Lines) then Exit;
    if not Step('Starting DomovoiHost', 'host-start', 14, Lines) then Exit;
    if not WaitForHost then Exit;
    ProgressPage.SetProgress(100, 100);
    StopButton.Visible := False;
    { An update repairs a claimed install: no code to show. }
    if not UpdateMode then
      SetupCodeText := ReadSetupCode;
    Result := True;
  finally
    StopButton.Visible := False;
    ProgressPage.Hide;
  end;
end;

{ -- restart and resume (PLAN 4.3) -------------------------------------- }

{ The entry runs Setup elevated after the restart, so it must not run it
  from wherever it was downloaded (a folder the user's own programs can
  write): it runs a copy in C:\Program Files\Domovoi\resume, which only
  administrators can change. The uninstaller removes that folder.

  Setup makes the copy itself, before [Files] (PrepareToInstall), so
  the app folder may not exist yet: the helper there isn't installed, and the one
  in Setup's temporary folder only reads. It calls Windows directly, so a
  refusal has Windows' own error code, in the log and in the message:
  the bare "Runtime error ... could not copy itself" before 1.4.0 had
  none and ended Setup. What usually refuses it: an antivirus (an
  unsigned program copying itself into Program Files and setting itself
  to run at the next sign-in looks like malware to some), or a copy from
  an earlier attempt still open (that Setup still running, or being
  scanned). A copy already there that is this Setup, byte for byte, is
  used as it is; a locked one is tried again for a few seconds, then
  passed by for DomovoiSetup-2.exe .. -5.exe in the same folder. The
  entry only ever names a copy in that folder. }

function CopyFileW(lpExistingFileName, lpNewFileName: String; bFailIfExists: Integer): Integer; external 'CopyFileW@kernel32.dll stdcall';
{ lpSecurityAttributes is a pointer, always nil here (Setup's engine is 32-bit). }
function CreateDirectoryW(lpPathName: String; lpSecurityAttributes: Integer): Integer; external 'CreateDirectoryW@kernel32.dll stdcall';

{ Windows' words for an error code, with the number. }
function WinError(Code: Integer): String;
begin
  Result := Trim(SysErrorMessage(Code));
  if (Result <> '') and (Result[Length(Result)] = '.') then
    Result := Copy(Result, 1, Length(Result) - 1);
  Result := Result + ' (Windows error ' + IntToStr(Code) + ')';
end;

{ Someone else has the file open: sharing or lock violation, a running
  program's image, or (on a file that is there) access denied. }
function IsLockError(Code: Integer): Boolean;
begin
  Result := (Code = 5) or (Code = 32) or (Code = 33) or (Code = 1224);
end;

{ What to do, in words for the household, by what refused it. }
function ResumeAdvice(Code: Integer; Locked: Boolean): String;
begin
  if (Code = 39) or (Code = 112) then
    Result := 'Drive C: is full. Free some space on it, then try again.'
  else if Locked and IsLockError(Code) then
    Result := 'A copy from an earlier attempt is still in use. Close any other Domovoi Setup window (Task Manager lists it as Domovoi Setup), then try again. If none is open, your antivirus may be checking that copy: wait a minute and try again, or restart Windows and run Setup again.'
  else
    Result := 'Your antivirus may have blocked it: Setup isn''t code-signed yet, and it copies itself into Program Files to start again after the restart. Check your antivirus''s quarantine (in Windows Security: Virus & threat protection > Protection history), allow or restore DomovoiSetup.exe, then try again. Also close any other Domovoi Setup window.';
end;

{ A folder for the copy: made if missing (False, with Windows' error code,
  when it can't be), never a link. }
function MakeResumeDir(const Dir: String; var Code: Integer): Boolean;
begin
  Code := 0;
  if not DirExists(Dir) then
    if CreateDirectoryW(Dir, 0) = 0 then
    begin
      Code := DLLGetLastError;
      Log('Resume copy: could not make ' + Dir + ': ' + WinError(Code));
      { 183: made meanwhile; anything else is the answer. }
      if not DirExists(Dir) then
      begin
        Result := False;
        Exit;
      end;
    end;
  Result := IsPlainDir(Dir);
  if not Result then
  begin
    Code := 0;
    Log('Resume copy: ' + Dir + ' is a link (junction or symbolic link), not a folder; Setup won''t use it');
  end;
end;

{ The folder's rules set, not inherited: SYSTEM and Administrators full,
  everyone else read and run (what C:\Program Files gives; the entry may
  start before the sign-in's token is elevated). Best effort: without it
  the folder keeps what it inherits from Program Files, admin-only too. }
procedure LockDownResumeDir(const Dir: String);
var
  Code: Integer;
begin
  if Exec(ExpandConstant('{sys}\icacls.exe'), AddQuotes(Dir) + ' /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX', '',
    SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
    Log('Resume copy: ' + Dir + ': SYSTEM and Administrators full, Users read')
  else
    Log('Resume copy: icacls on ' + Dir + ' did not finish (' + IntToStr(Code) + '); it keeps the rules it inherits from Program Files');
end;

{ The copy at Path is this Setup, byte for byte: same size, same SHA-256. }
function SameCopy(const Path, Src, SrcSha: String): Boolean;
var
  A, B: Int64;
begin
  Result := False;
  try
    if FileSize64(Path, A) and FileSize64(Src, B) and (A = B) then
      Result := SameText(GetSHA256OfFile(Path), SrcSha);
  except
    Log('Resume copy: could not read ' + Path + ': ' + GetExceptionMessage);
  end;
end;

{ True: ResumeExe is a copy of this Setup in the resume folder. False: Problem
  says what happened and what to do, with Windows' error; the log has
  each try. }
function MakeResumeCopy(var ResumeExe, Problem: String): Boolean;
var
  Src, Dir, SrcSha: String;
  I, Attempt, Code: Integer;
  Existed: Boolean;
begin
  Result := False;
  ResumeExe := '';
  Src := ExpandConstant('{srcexe}');
  Dir := ExpandConstant('{app}\resume');
  { A second restart: Setup already runs from a copy there. }
  if SameText(ExtractFileDir(Src), Dir) then
  begin
    ResumeExe := Src;
    Result := True;
    Exit;
  end;
  { Read first: a Setup run from inside a zip file, a network folder or a
    file that is only in the cloud may not be readable to copy. }
  try
    SrcSha := GetSHA256OfFile(Src);
  except
    Log('Resume copy: could not read ' + Src + ': ' + GetExceptionMessage);
    Problem := 'Setup couldn''t read its own file, ' + Src + '. Copy DomovoiSetup.exe to a folder on this PC, such as Downloads, and run it from there (not from inside a zip file, a network folder, or a cloud folder whose files aren''t kept on this PC).';
    Exit;
  end;
  if not MakeResumeDir(ExpandConstant('{app}'), Code) or not MakeResumeDir(Dir, Code) then
  begin
    if Code = 0 then
      Problem := 'Setup couldn''t use the folder ' + Dir + ': it is a link, not a folder. Delete it, then try again.'
    else
      Problem := 'Windows refused to make the folder ' + Dir + ': ' + WinError(Code) + '. ' + ResumeAdvice(Code, False);
    Exit;
  end;
  LockDownResumeDir(Dir);
  Code := 0;
  Existed := False;
  for I := 1 to 5 do
  begin
    if I = 1 then
      ResumeExe := Dir + '\DomovoiSetup.exe'
    else
      ResumeExe := Dir + '\DomovoiSetup-' + IntToStr(I) + '.exe';
    { A link where the copy goes would send the write elsewhere: remove it. }
    if (GetFileAttributesW(ResumeExe) <> $FFFFFFFF) and ((GetFileAttributesW(ResumeExe) and $400) <> 0) then
      DeleteFile(ResumeExe);
    Existed := FileExists(ResumeExe);
    if Existed and SameCopy(ResumeExe, Src, SrcSha) then
    begin
      Log('Resume copy: ' + ResumeExe + ' is already this Setup; using it');
      Result := True;
      Exit;
    end;
    for Attempt := 1 to 5 do
    begin
      if CopyFileW(Src, ResumeExe, 0) <> 0 then
      begin
        { An antivirus may let the write through, then take the file away. }
        if SameCopy(ResumeExe, Src, SrcSha) then
        begin
          Log('Resume copy: copied Setup to ' + ResumeExe);
          Result := True;
          Exit;
        end;
        Code := 225;
        Log('Resume copy: ' + ResumeExe + ' was gone or changed right after the copy');
        Problem := 'Setup copied itself to ' + ResumeExe + ', but the copy was gone or changed right afterwards. ' + ResumeAdvice(Code, False);
        Exit;
      end;
      Code := DLLGetLastError;
      Log('Resume copy: copying to ' + ResumeExe + ' failed (try ' + IntToStr(Attempt) + '): ' + WinError(Code));
      { Only a copy that was there and is held open is worth waiting for. }
      if not Existed or not IsLockError(Code) then
        Break;
      Sleep(1000);
    end;
    { ... and only that one is worth passing by for another name. }
    if not Existed or not IsLockError(Code) then
      Break;
  end;
  Problem := 'Windows refused the copy of Setup in ' + Dir + ': ' + WinError(Code) + '. ' + ResumeAdvice(Code, Existed);
  ResumeExe := '';
end;

{ The copy, then the entry. False: Problem says why (MakeResumeCopy). }
function CreateRunOnceEntry(var Problem: String): Boolean;
var
  Data, ResumeExe: String;
begin
  Result := MakeResumeCopy(ResumeExe, Problem);
  if not Result then
    Exit;
  Data := AddQuotes(ResumeExe) + ' /resume=1 /internet=' + InternetAnswer + ' /ai=' + AiPrepareArg +
    ' /mediaroot=' + AddQuotes(RemoveBackslashUnlessRoot(MediaPage.Values[0]));
  { The kit's folder (no trailing backslash: "E:" stays E:). }
  if KitMode then
    Data := Data + ' /kit=' + AddQuotes(RemoveBackslash(KitRoot));
  if NoDenyLogon then
    Data := Data + ' /nodenylogon=1';
  if UseKeptDir <> '' then
    Data := Data + ' /usekept=' + AddQuotes(UseKeptDir)
  else if IdentityBackupFile <> '' then
    Data := Data + ' /identity=' + AddQuotes(IdentityBackupFile);
  Result := RegWriteStringValue(HKLM, RunOnceKey, RunOnceName, Data);
  if not Result then
  begin
    Log('Resume: could not write HKLM\' + RunOnceKey + '\' + RunOnceName);
    Problem := 'Windows refused the setting that starts Setup again after the restart (HKLM\' + RunOnceKey + '). ' + ResumeAdvice(0, False);
  end;
end;

{ Before a restart (PrepareToInstall): the copy and the entry, offering
  Try again while the household fixes what refused them, instead of the
  bare Runtime error that ended Setup before 1.4.0. '': ready to restart.
  Otherwise what Setup stops with (the Preparing page; an update's
  result). What: what the restart finishes. }
function ArrangeResume(const What: String): String;
var
  Problem, LogLine: String;
  Stop: Boolean;
begin
  Result := '';
  LogLine := '';
  if ExpandConstant('{log}') <> '' then
    LogLine := #13#10#13#10 + 'Setup''s log: ' + ExpandConstant('{log}');
  while not CreateRunOnceEntry(Problem) do
  begin
    Log('Resume: ' + Problem);
    { Update mode and a silent run ask nothing. }
    Stop := UpdateMode or WizardSilent;
    if not Stop then
      Stop := TaskDialogMsgBox('Setup couldn''t copy itself to carry on after the restart', 'Windows has to restart to finish ' + What + ', and Setup starts again by itself afterwards, from a copy of itself in ' + ExpandConstant('{app}\resume') + '.' + #13#10#13#10 + Problem + LogLine, mbError, MB_RETRYCANCEL, ['Try again', 'Stop Setup'], 0) <> IDRETRY;
    if Stop then
    begin
      Result := 'Setup stopped before the restart. ' + Problem + #13#10#13#10 + 'When that is done, restart Windows (it still has to finish ' + What + ') and run Setup again.' + LogLine;
      Exit;
    end;
  end;
end;

{ -- update mode (the header) -------------------------------------------- }

{ The answers this install has (the helper's saved-answers, from Setup's
  install record). False: none, so this is no install to update. }
function ReadSavedAnswers: Boolean;
var
  Lines: TArrayOfString;
  Code: Integer;
begin
  Result := False;
  if not RunHelper(TmpHelper, 'saved-answers', Lines, Code) or (Code <> 0) then
  begin
    FailureText := 'This PC has no Domovoi for Setup to update (' + KvValue(Lines, 'error') + '). Run Setup itself.';
    Exit;
  end;
  SavedInternet := KvValue(Lines, 'internet');
  SavedMediaRoot := KvValue(Lines, 'media_root');
  if (SavedInternet = '') or (SavedMediaRoot = '') then
  begin
    FailureText := 'Setup''s install record has no answers to keep. Run Setup itself.';
    Exit;
  end;
  Result := True;
end;

{ The outcome, for the tray: /updateresult names the file (a result.txt). }
procedure WriteUpdateResult;
var
  Path, Text, LogPath: String;
begin
  if ResultWritten or not UpdateMode then
    Exit;
  ResultWritten := True;
  Path := ExpandConstant('{param:updateresult|}');
  { Only a result.txt in a run folder under C:\ProgramData\Domovoi\updates,
    which only administrators can write (so no junction of anyone else's
    can send this elevated write elsewhere). }
  if (Path = '') or (CompareText(ExtractFileName(Path), 'result.txt') <> 0) or not DirExists(ExtractFileDir(Path))
    or (Pos('..', Path) > 0)
    or (CompareText(ExtractFileDir(ExtractFileDir(Path)), ExpandConstant('{commonappdata}\Domovoi\updates')) <> 0) then
  begin
    Log('Update mode: no usable /updateresult (' + Path + ')');
    Exit;
  end;
  LogPath := ExpandConstant('{log}');
  if InstallOk then
    Text := 'ok=1' + #13#10
  else
  begin
    if FailureText = '' then
      FailureText := 'Setup stopped before it finished.';
    StringChangeEx(FailureText, #13#10, ' ', True);
    StringChangeEx(FailureText, #10, ' ', True);
    Text := 'ok=0' + #13#10 + 'error=' + FailureText + #13#10;
  end;
  if LogPath <> '' then
    Text := Text + 'log=' + LogPath + #13#10;
  if LinuxOutcome <> '' then
    Text := Text + 'linux=' + LinuxOutcome + #13#10;
  Text := Text + 'version={#AppVersion}' + #13#10;
  if not SaveStringToFile(Path, Text, False) then
    Log('Update mode: could not write ' + Path);
end;

{ The files were replaced but the repair didn't finish: 10, for the tray
  (it reads the result file too). }
function GetCustomSetupExitCode: Integer;
begin
  Result := 0;
  if UpdateMode and not InstallOk then
    Result := 10;
end;

{ -- event functions ---------------------------------------------------- }

function InitializeSetup: Boolean;
var
  Sac: Cardinal;
begin
  { Smart App Control blocks unsigned programs, and this installer is
    unsigned (code signing is on hold). If Setup is running at all, Windows
    let it start; the helper and the service it unpacks would still be
    blocked, so say so now rather than fail later. 0 off, 1 on, 2
    evaluation. }
  if RegQueryDWordValue(HKLM, 'SYSTEM\CurrentControlSet\Control\CI\Policy', 'VerifiedAndReputablePolicyState', Sac) and (Sac = 1) then
  begin
    SuppressibleMsgBox('Smart App Control is on, and this version of Domovoi is not code-signed yet. Windows would block Setup''s helper and the Domovoi service, so Setup stops here.' + #13#10#13#10 +
      'To install it anyway, turn Smart App Control off in Windows Security > App & browser control > Smart App Control settings. On many Windows 11 builds it can only be turned back on by resetting or reinstalling Windows; read what Windows Security says first (docs/WINDOWS_HOST.md, "Unsigned").',
      mbCriticalError, MB_OK, IDOK);
    if ExpandConstant('{param:update|0}') = '1' then
    begin
      UpdateMode := True;
      FailureText := 'Smart App Control is on, and Domovoi is not code-signed, so Windows would block its programs.';
      WriteUpdateResult;
    end;
    Result := False;
    Exit;
  end;
  Resumed := ExpandConstant('{param:resume|0}') = '1';
  NoDenyLogon := ExpandConstant('{param:nodenylogon|0}') = '1';
  UpdateMode := ExpandConstant('{param:update|0}') = '1';
  LinuxFirst := UpdateMode and (ExpandConstant('{param:linuxfirst|0}') = '1');
#ifndef ProvisionAiChoice
  { This build's provision.sh always pulls the AI models (pins.iss): say so
    now, not after the account, the firewall rules and the downloads. }
  if CompareText(ExpandConstant('{param:ai|}'), 'no') = 0 then
  begin
    SuppressibleMsgBox('This Setup always includes AI chat and questions: the Domovoi it installs (commit {#Copy(DomovoiCommit, 1, 7)}) can''t leave the AI models out, so /ai=no can''t be done. Run it without /ai=no, or use a newer Setup.',
      mbCriticalError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
#endif
  ExtractTemporaryFile('{#Helper}');
  { An offline kit beside this Setup, or /kit= (never for an update: the
    Windows update comes from the release, online). }
  if not UpdateMode then
    KitRoot := FindKit;
  if KitRoot <> '' then
    Log('Offline kit: ' + KitRoot);
  if UpdateMode then
  begin
    { Only silently, and only over an install: a repair, never a first
      install, and never with a question. }
    if not WizardSilent then
    begin
      FailureText := 'The update mode runs silently only (the Domovoi tray starts it so).';
      WriteUpdateResult;
      Result := False;
      Exit;
    end;
    if not ReadSavedAnswers then
    begin
      Log('Update mode: ' + FailureText);
      WriteUpdateResult;
      Result := False;
      Exit;
    end;
    Log('Update mode: internet ' + SavedInternet + ', media folder ' + SavedMediaRoot + ', Linux part first: ' + IntToStr(Ord(LinuxFirst)));
  end;
  Result := True;
end;

procedure InitializeWizard;
var
  Root: String;
begin
  PreflightPage := CreateOutputMsgMemoPage(wpWelcome, 'This PC', 'What Setup found', 'Setup checked this PC. Nothing has been changed yet.', '');
  { Before the changes page: its download size follows the answer. Only
    when the pinned provision.sh takes it (pins.iss ProvisionAiChoice). }
#ifdef ProvisionAiChoice
  CreateAiPage(PreflightPage.ID);
  ConsentPage := CreateOutputMsgMemoPage(AiPage.ID, 'What Setup will change', 'Read this before you go on',
#else
  ConsentPage := CreateOutputMsgMemoPage(PreflightPage.ID, 'What Setup will change', 'Read this before you go on',
#endif
    'Domovoi runs a small Linux system on this PC, as its own hidden account. To set that up, Setup makes these changes:', '');
  CreateInternetPage(ConsentPage.ID);
  MediaPage := CreateInputDirPage(InternetPage.ID, 'Your media folder', 'Where your music, pictures and videos live',
    'Domovoi keeps your media in a Windows folder you can open in Explorer and copy files into. Setup makes Music, Pictures, Videos, Documents, Audiobooks and Podcasts inside it. Pick a folder on a drive of this PC.',
    False, '');
  MediaPage.Add('Media folder:');
  CreateIdentityPage(MediaPage.ID);
  Root := ExpandConstant('{param:mediaroot|}');
  if UpdateMode then
    Root := SavedMediaRoot;
  if Root = '' then
    Root := 'C:\Users\Public\Domovoi';
  MediaPage.Values[0] := Root;
  DownloadPage := CreateDownloadPage('Downloading', 'Setup downloads what it needs and checks each file against its pinned SHA-256.', nil);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
  CheckPage := CreateOutputMarqueeProgressPage('Checking this PC', 'Setup changes nothing until you say so.');
  ProgressPage := CreateOutputProgressPage('Setting up Domovoi', 'This takes 20 to 40 minutes on a typical connection; most of it is downloading the AI models. You can keep using the PC.');
  StopButton := TNewButton.Create(ProgressPage);
  StopButton.Parent := ProgressPage.Surface;
  StopButton.Caption := 'Stop Setup';
  StopButton.SetBounds(ProgressPage.SurfaceWidth - ScaleX(110), ProgressPage.SurfaceHeight - ScaleY(30), ScaleX(110), ScaleY(25));
  StopButton.OnClick := @StopClick;
  StopButton.Visible := False;
  CreateFinishControls;
  if KitMode then
    WizardForm.WelcomeLabel2.Caption := WizardForm.WelcomeLabel2.Caption + #13#10#13#10 + 'Installing from the offline kit. No internet needed.';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  { After the restart the answers come back on the command line (the AI
    one as /ai=, which the AI page starts at). }
  { Update mode shows no page; its answers are the saved ones. }
  if UpdateMode then
  begin
    Result := (PageID <> wpWelcome) and (PageID <> wpReady);
    Exit;
  end;
  if Resumed and (ExpandConstant('{param:internet|}') <> '') then
#ifdef ProvisionAiChoice
    Result := (PageID = AiPage.ID) or (PageID = ConsentPage.ID) or (PageID = InternetPage.ID) or (PageID = MediaPage.ID) or (PageID = IdentityPage.ID);
#else
    Result := (PageID = ConsentPage.ID) or (PageID = InternetPage.ID) or (PageID = MediaPage.ID) or (PageID = IdentityPage.ID);
#endif
end;

procedure CurPageChanged(CurPageID: Integer);
begin
#ifdef ProvisionAiChoice
  if CurPageID = AiPage.ID then
  begin
    AiYesText.Caption := AiYesWords;
    AiNoText.Caption := AiNoWords;
    { A kit without AI models: No, and Yes can't be picked. }
    RadioAiYes.Enabled := not (KitMode and not KitAi);
    if KitMode and not KitAi then
      RadioAiNo.Checked := True;
    LayoutAiPage;
  end
  else
#endif
  if CurPageID = IdentityPage.ID then
    FindKeptData
  else if CurPageID = ConsentPage.ID then
    ConsentPage.RichEditViewer.Lines.Text := ConsentText
  else if CurPageID = InternetPage.ID then
  begin
    InternetIntro.Caption := InternetIntroText(AiAnswer <> 'no');
    { A house installing from a stick most likely has no internet (as
      install.sh --kit): No is picked to begin with. }
    if KitMode and not (RadioAlways.Checked or RadioSometimes.Checked or RadioNever.Checked) then
      RadioNever.Checked := True;
    WizardForm.NextButton.Enabled := RadioAlways.Checked or RadioSometimes.Checked or RadioNever.Checked;
  end
  else if CurPageID = wpFinished then
    ShowFinish;
end;

{ Online: the pinned downloads (DownloadPage), each checked against its
  SHA-256. Never in kit mode: reaching it there is a fault, and it stops. }
function RunDownloads: Boolean;
begin
  Result := True;
  if KitRoot <> '' then
    RaiseException('Setup reached its downloads while installing from the offline kit, and nothing may be downloaded. This is a fault in Domovoi Setup; please report it. Nothing was downloaded.');
  ProvisionPath := ExpandConstant('{tmp}\provision.sh');
  DownloadPage.Clear;
#ifdef ProvisionBundled
  { Shipped inside Setup (pins.iss); the helper checks its SHA-256 when it
    hands it to DomovoiHost, and the host again before it runs it. }
  ExtractTemporaryFile('provision.sh');
#else
  AddDownload('{#ProvisionUrl}', 'provision.sh', '{#ProvisionSha256}');
#endif
  { An update over an install whose DomovoiHost folder holds the pinned
    image (the helper's job step checks its SHA-256 and copies nothing
    when it matches; DomovoiHost imports only a distro that isn't
    registered): no 418 MB download again. }
  ImagePath := ExpandConstant('{tmp}\{#UbuntuName}');
  if UpdateMode and FileExists(ExpandConstant('{commonappdata}\Domovoi\host\in\{#UbuntuName}')) then
  begin
    ImagePath := ExpandConstant('{commonappdata}\Domovoi\host\in\{#UbuntuName}');
    Log('Update mode: the Ubuntu image is already in ' + ImagePath + '; not downloading it');
  end
  else
    AddDownload('{#UbuntuUrl}', '{#UbuntuName}', '{#UbuntuSha256}');
  if (WslState = 'missing') or (WslState = 'old') then
    AddDownload('{#WslMsiUrl}', '{#WslMsiName}', '{#WslMsiSha256}');
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
    except
      FailureText := 'A download did not work: ' + GetExceptionMessage;
      if not DownloadPage.AbortedByUser then
        SuppressibleMsgBox('A download did not work: ' + GetExceptionMessage, mbCriticalError, MB_OK, IDOK);
      Result := False;
    end;
  finally
    DownloadPage.Hide;
  end;
end;

{ From the kit: Ubuntu's image and provision.sh are read off the stick by
  the helper's job step, which copies each only when it matches its pin;
  WSL's MSI is copied here and checked against its pin before msiexec
  runs it. Nothing is downloaded. }
function UseKitFiles: Boolean;
var
  Msi: String;
begin
  Result := True;
  ImagePath := KitFile('windows\{#UbuntuName}');
#ifdef ProvisionBundled
  ExtractTemporaryFile('provision.sh');
  ProvisionPath := ExpandConstant('{tmp}\provision.sh');
#else
  ProvisionPath := KitFile('linux\provision.sh');
#endif
  if (WslState = 'missing') or (WslState = 'old') then
  begin
    Msi := ExpandConstant('{tmp}\{#WslMsiName}');
    if not FileCopy(KitFile('windows\{#WslMsiName}'), Msi, False) or (GetSHA256OfFile(Msi) <> '{#WslMsiSha256}') then
    begin
      FailureText := 'This stick has a damaged file: windows\{#WslMsiName}. Make the stick again.';
      SuppressibleMsgBox(FailureText, mbCriticalError, MB_OK, IDOK);
      Result := False;
      Exit;
    end;
    Log('WSL''s MSI copied from the offline kit and checked against its pin');
  end;
  if not FileExists(ImagePath) or not FileExists(ProvisionPath) then
  begin
    FailureText := 'The offline kit at ' + KitRoot + ' isn''t there any more. Plug the stick in again and run Setup again.';
    SuppressibleMsgBox(FailureText, mbCriticalError, MB_OK, IDOK);
    Result := False;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Problem: String;
  Lines: TArrayOfString;
  Code: Integer;
begin
  Result := True;
  if CurPageID = wpWelcome then
  begin
    if not PreflightDone then
      Result := RunPreflight;
    if not Result then
    begin
      ForceCancel := True;
      WizardForm.Close;
    end;
  end
  else if CurPageID = InternetPage.ID then
    Result := InternetAnswer <> ''
  else if CurPageID = MediaPage.ID then
  begin
    { The helper's own rules: a local folder, not a system or program
      folder, a profile root or anything holding one, not a link. }
    Problem := '';
    if not RunHelper(TmpHelper, 'check-media --root ' + AddQuotes(RemoveBackslashUnlessRoot(MediaPage.Values[0])), Lines, Code) then
      Problem := 'Setup could not check that folder.'
    else if Code <> 0 then
      Problem := KvValue(Lines, 'error');
    if Problem <> '' then
    begin
      if UpdateMode then
        FailureText := 'The media folder ' + MediaPage.Values[0] + ' can''t be used: ' + Problem
      else
        MsgBox(Problem, mbError, MB_OK);
      Result := False;
    end;
  end
  else if CurPageID = IdentityPage.ID then
  begin
    { A backup file: its shape checked now (read-only); the key pair is
      checked in the new install before it is used. }
    if RadioIdentityBackup.Checked then
    begin
      Problem := '';
      if Trim(IdentityEdit.Text) = '' then
        Problem := 'Choose the house identity backup file, or pick another answer.'
      else if not RunHelper(TmpHelper, 'check-backup --file ' + AddQuotes(Trim(IdentityEdit.Text)), Lines, Code) then
        Problem := 'Setup could not check that file.'
      else if Code <> 0 then
        Problem := KvValue(Lines, 'error');
      if Problem <> '' then
      begin
        MsgBox(Problem, mbError, MB_OK);
        Result := False;
      end
      else
        Result := MsgBox('This backup is the house identity ' + KvValue(Lines, 'fingerprint') + '. Rooms set up for that house will trust this install.' + #13#10#13#10 + 'Keep the backup file somewhere safe afterwards: it is the server''s private key.', mbInformation, MB_OKCANCEL) = IDOK;
    end;
  end
  else if CurPageID = wpReady then
  begin
    if KitMode then
      Result := UseKitFiles
    else
      Result := RunDownloads;
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if ForceCancel then
    Confirm := False;
end;

function NextWords: String;
begin
  if KitMode then
    Result := 'Next, Setup installs from the offline kit at ' + KitRoot + ' (Domovoi ' + KitVersion + '). Nothing is downloaded. Keep the stick plugged in until Setup finishes.'
  else
#ifdef ProvisionBundled
    Result := 'Next, Setup downloads Ubuntu''s WSL image (418 MB), then installs.';
#else
    Result := 'Next, Setup downloads Ubuntu''s WSL image (418 MB) and provision.sh, then installs.';
#endif
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Ai: String;
begin
  if AiAnswer = 'no' then
    Ai := 'no (about 4.5 GB to download in all)'
  else if AiPrepareArg = 'keep' then
    Ai := 'as this home has it now (Settings > Models; unchanged)'
  else
    Ai := 'yes (' + AiModelsText + ')';
#ifdef ProvisionAiChoice
  Result := 'AI chat and questions:' + NewLine + Space + Ai + NewLine + NewLine;
#else
  Result := '';
#endif
  Result := Result + 'Internet after setup:' + NewLine + Space + InternetAnswer + NewLine + NewLine +
    'Media folder:' + NewLine + Space + RemoveBackslashUnlessRoot(MediaPage.Values[0]) + NewLine + NewLine +
    'Domovoi:' + NewLine + Space + 'commit {#DomovoiCommit}' + NewLine + NewLine +
    NextWords;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Lines: TArrayOfString;
  Code: Integer;
begin
  Result := '';
  { Update mode from the tray's own "Update Domovoi": the Linux part first,
    through this Setup's helper (DomovoiHost runs the dashboard's update in
    the distro), waiting until the update unit is done. If it fails,
    nothing on Windows has changed yet, and nothing does. }
  if LinuxFirst then
  begin
    Log('Update mode: the Linux part first');
    if not RunHelper(TmpHelper, 'linux-update --wait', Lines, Code) or (Code <> 0) then
    begin
      LinuxOutcome := 'failed';
      Result := 'The Linux part didn''t update: ' + KvValue(Lines, 'error') + '. Nothing on Windows was changed.';
      FailureText := Result;
      Exit;
    end;
    LinuxOutcome := KvValue(Lines, 'linux');
    Log('Update mode: the Linux part: ' + LinuxOutcome);
  end;
  { A repair or a second run: stop the host so its exe can be replaced,
    and the tray icons, theirs. }
  if FileExists(AppHelper) then
  begin
    HostStopped := True;
    RunHelper(AppHelper, 'host-stop', Lines, Code);
  end;
  StopTrays;
  if VmpState <> 'enabled' then
  begin
    Log('Enabling VirtualMachinePlatform');
    if not Exec(ExpandConstant('{sys}\dism.exe'), '/online /enable-feature /featurename:VirtualMachinePlatform /all /norestart /quiet', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Code := -1;
    if Code = 3010 then
    begin
      Result := ArrangeResume('turning on Virtual Machine Platform');
      if Result <> '' then
      begin
        FailureText := Result;
        Exit;
      end;
      NeedsRestart := True;
      Result := 'Windows has to restart to finish turning on Virtual Machine Platform. Setup starts again by itself after you sign in, and carries on from here.';
      FailureText := Result;
      Exit;
    end;
    if Code <> 0 then
    begin
      Result := 'Windows could not turn on Virtual Machine Platform (dism exit code ' + IntToStr(Code) + ').';
      FailureText := Result;
      Exit;
    end;
  end;
  if WslState = 'denied' then
  begin
    Result := 'This PC''s WSL version has a known problem with the networking Domovoi uses. Update it (run "wsl --update" as an administrator), then run Setup again.';
    FailureText := Result;
    Exit;
  end;
  if (WslState = 'missing') or (WslState = 'old') then
  begin
    Log('Installing WSL 2.7.14');
    if not Exec(ExpandConstant('{sys}\msiexec.exe'), '/i ' + AddQuotes(ExpandConstant('{tmp}\{#WslMsiName}')) + ' /qn /norestart', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Code := -1;
    if Code = 3010 then
    begin
      Result := ArrangeResume('installing WSL');
      if Result <> '' then
      begin
        FailureText := Result;
        Exit;
      end;
      NeedsRestart := True;
      Result := 'Windows has to restart to finish installing WSL. Setup starts again by itself after you sign in, and carries on from here.';
      Exit;
    end;
    if Code <> 0 then
      Result := 'WSL did not install (msiexec exit code ' + IntToStr(Code) + ').';
  end;
  if Result <> '' then
    FailureText := Result;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
  Code: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    InstallOk := MachineSetup;
    if InstallOk and not UpdateMode then
      StartTray;
    if not InstallOk and UpdateMode then
      { Never "Remove what Setup has changed" (that uninstalls, and the
        uninstall deletes the database), and the service isn't parked:
        Domovoi keeps running as it is, and the result says what failed. }
      Log('Update mode: the repair did not finish; everything is kept as it is: ' + FailureText)
    else if not InstallOk then
    begin
      UndoOnExit := MsgBox(FailureText + #13#10#13#10 + 'Remove what Setup has changed so far? (Recommended unless you want to run Setup again later to carry on. Either way, the last page has a Copy logs button.)',
        mbConfirmation, MB_YESNO) = IDYES;
      { Kept for later: DomovoiHost stopped and set to Manual, so it doesn't
        start provisioning again at every boot. Setup sets it back. }
      if not UndoOnExit and FileExists(AppHelper) then
        RunHelper(AppHelper, 'host-park', Lines, Code);
    end;
  end;
end;

procedure DeinitializeSetup;
var
  Code: Integer;
  Lines: TArrayOfString;
begin
  { The code was only ever in memory; drop it. }
  SetupCodeText := '';
  SetupQrSize := 0;
  SetupQrBits := '';
  if UpdateMode then
  begin
    UndoOnExit := False;
    { Every way an update can fail after the host was stopped (a step, the
      media folder on a drive that isn't there, PrepareToInstall): start it
      again, best effort, so Domovoi keeps running as it was. }
    if HostStopped and not InstallOk and FileExists(AppHelper) then
      if RunHelper(AppHelper, 'host-start', Lines, Code) and (Code = 0) then
        Log('Update mode: started DomovoiHost again after the failure')
      else
        Log('Update mode: could not start DomovoiHost again: ' + KvValue(Lines, 'error'));
    WriteUpdateResult;
    { This account's icon again (the others start at their sign-in). }
    if TraysStopped then
      StartTray;
  end;
  if UndoOnExit and FileExists(ExpandConstant('{uninstallexe}')) then
    Exec(ExpandConstant('{uninstallexe}'), '/SILENT /NORESTART /KEEPDATA=0', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

{ -- uninstall (PLAN 7) ------------------------------------------------- }

{ Keep the data (the default, S3-4) or remove everything: asked first. Kept:
  the distro's disk, with the database, settings and the house identity,
  in an administrators-only C:\ProgramData\Domovoi-kept-<time>; the next
  Setup offers to use it, so the rooms reconnect by themselves. Silent
  (Setup's own undo of a failed first install passes /KEEPDATA=0): as the
  command line says, keep by default. }
function InitializeUninstall: Boolean;
var
  Choice: Integer;
begin
  Result := True;
  UninstallKeepData := ExpandConstant('{param:keepdata|1}') <> '0';
  if UninstallSilent then
    Exit;
  Choice := TaskDialogMsgBox('Keep your Domovoi data?',
    'Keep my data (recommended): Domovoi is removed, and its database, settings and the house''s identity are kept on this PC (C:\ProgramData\Domovoi-kept-..., administrators only). Install Domovoi again and it picks them up: your rooms reconnect by themselves.' + #13#10#13#10 +
    'Remove everything: also deletes the database (history, voice profiles, settings) and the house''s identity. Every room must then be set up again after a new install.' + #13#10#13#10 +
    'Files in your media folders are kept either way.',
    mbConfirmation, MB_YESNOCANCEL, ['Keep my data (recommended)', 'Remove everything'], 0);
  if Choice = IDCANCEL then
  begin
    Result := False;
    Exit;
  end;
  UninstallKeepData := Choice = IDYES;
  if not UninstallKeepData then
    Result := TaskDialogMsgBox('Delete all of Domovoi''s data?',
      'The database and the house''s identity are deleted for good. To keep the identity instead, download a backup first (Settings > System > About & updates > House identity backup).',
      mbError, MB_YESNO, ['Delete everything', 'Don''t uninstall'], 0) = IDYES;
end;

{ The steps as they happen, under the progress bar (owner fixes
  2026-10-12, item 7: the window sat on one message for minutes while the
  helper's log showed each step). }
procedure InitializeUninstallProgressForm;
var
  F: TUninstallProgressForm;
  Top, H: Integer;
begin
  { An exception here would stop the uninstaller (Inno treats it as
    fatal), so the list is optional: without it the status text and the
    bar still move. }
  try
    F := UninstallProgressForm;
    Top := F.ProgressBar.Top + F.ProgressBar.Height + ScaleY(12);
    H := F.InstallingPage.ClientHeight - Top - ScaleY(4);
    if H < ScaleY(48) then
      H := ScaleY(48);
    UninstallDetails := TNewMemo.Create(F);
    UninstallDetails.Parent := F.InstallingPage;
    UninstallDetails.ReadOnly := True;
    UninstallDetails.ScrollBars := ssVertical;
    UninstallDetails.WordWrap := True;
    UninstallDetails.SetBounds(F.ProgressBar.Left, Top, F.ProgressBar.Width, H);
    UninstallDetails.Visible := False;
  except
    Log('No list of steps: ' + GetExceptionMessage);
    UninstallDetails := nil;
  end;
end;

procedure AddDetail(const S: String);
begin
  if UninstallDetails = nil then
    Exit;
  UninstallDetails.Visible := True;
  UninstallDetails.Lines.Add(S);
  { Keep the newest line in view. }
  SendMessage(UninstallDetails.Handle, WM_VSCROLL, SB_BOTTOM, 0);
end;

{ One line of the helper's uninstall output, shown: progress=N|TOTAL|what
  drives the status text and the bar; info=, leftover=, kept= and
  restart= lines go to the list. }
procedure ShowUninstallLine(const S: String);
var
  P, Index, Total: Integer;
  Rest: String;
begin
  if Copy(S, 1, 9) = 'progress=' then
  begin
    Rest := Copy(S, 10, Length(S));
    P := Pos('|', Rest);
    Index := StrToIntDef(Copy(Rest, 1, P - 1), 0);
    Rest := Copy(Rest, P + 1, Length(Rest));
    P := Pos('|', Rest);
    Total := StrToIntDef(Copy(Rest, 1, P - 1), 0);
    Rest := Copy(Rest, P + 1, Length(Rest));
    UninstallProgressForm.StatusLabel.Caption := Rest + '...';
    if (Total > 0) and (Index > 0) then
    begin
      UninstallProgressForm.ProgressBar.Min := 0;
      UninstallProgressForm.ProgressBar.Max := Total;
      UninstallProgressForm.ProgressBar.Position := Index - 1;
    end;
    AddDetail(IntToStr(Index) + '/' + IntToStr(Total) + '  ' + Rest);
  end
  else if Copy(S, 1, 5) = 'info=' then
    AddDetail('        ' + Copy(S, 6, Length(S)))
  else if Copy(S, 1, 9) = 'leftover=' then
    AddDetail('Left over: ' + Copy(S, 10, Length(S)))
  else if Copy(S, 1, 8) = 'restart=' then
    AddDetail('At the next restart: ' + Copy(S, 9, Length(S)))
  else if Copy(S, 1, 5) = 'kept=' then
    AddDetail('Kept: ' + Copy(S, 6, Length(S)));
end;

{ The helper's output as it runs (ExecAndLogOutput reads it a line at a
  time while the helper works, and keeps the window responsive). Every
  line is kept for the messages at the end. }
procedure UninstallOutput(const S: String; const Error, FirstLine: Boolean);
var
  N: Integer;
begin
  if Error then
  begin
    Log('Reading the helper''s output: ' + S);
    Exit;
  end;
  N := GetArrayLength(UninstLines);
  SetArrayLength(UninstLines, N + 1);
  UninstLines[N] := S;
  { Showing a line must never stop the reading: an exception here would
    end the wait with the helper still at work. }
  try
    ShowUninstallLine(S);
  except
    Log('Showing a line of the helper''s output: ' + GetExceptionMessage);
  end;
end;

{ A message with a Copy logs button, which copies the logs (Domovoi's
  folder, while it is still there, and the uninstall's own record) and asks
  again. (MB_YESNO: the labels in that order, Yes first.) }
procedure UninstallNotice(const Instruction, Text: String; const Typ: TMsgBoxType; const HelperExe, LogFile: String);
begin
  while SuppressibleTaskDialogMsgBox(Instruction, Text, Typ, MB_YESNO, ['OK', 'Copy logs'], 0, IDYES) = IDNO do
    CopyLogs(HelperExe, 'uninstall', LogFile);
end;

{ The messages are the helper's notice_*= lines, in plain words (owner,
  2026-10-12: "Windows still had it open (error 32), so a one-time startup
  task ... deletes it" was too technical for a dialog). The paths, error
  codes and how to remove each leftover by hand stay in the list of steps
  (Left over:, At the next restart:) and the uninstall's log. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  HelperExe, LogFile, Later, LeftText, StillText, Params: String;
  Kept: TArrayOfString;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { The tray's own settings, for the account running the uninstaller (the
      Run value Setup made goes with the [Registry] entry; a copy of the
      tray run on its own registers under HKCU instead). }
    if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'DomovoiTray', Later) and (Pos('DomovoiTray.exe', Later) > 0) then
      RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'DomovoiTray');
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Domovoi\Tray');
    RegDeleteKeyIfEmpty(HKCU, 'Software\Domovoi');
    Exit;
  end;
  if CurUninstallStep <> usUninstall then
    Exit;
  HelperExe := ExpandConstant('{app}\{#Helper}');
  if not FileExists(HelperExe) then
  begin
    SuppressibleMsgBox(HelperExe + ' is missing, so the uninstaller can''t undo what Setup changed. Run Domovoi Setup again (it repairs the program), then uninstall.', mbError, MB_OK, IDOK);
    Abort;
  end;
  StopTrays;
  UninstallProgressForm.StatusLabel.Caption := 'Looking at what Setup changed...';
  SetArrayLength(UninstLines, 0);
  try
    if UninstallKeepData then
      Params := 'uninstall --keep-data'
    else
      Params := 'uninstall';
    if not ExecAndLogOutput(HelperExe, Params, '', SW_HIDE, ewWaitUntilTerminated, Code, @UninstallOutput) then
      Code := -1;
  except
    Log('Could not run the helper: ' + GetExceptionMessage);
    Code := -1;
  end;
  LogFile := KvValue(UninstLines, 'log');
  Log('Uninstall helper exit code ' + IntToStr(Code) + '; record: ' + LogFile);
  UninstallProgressForm.StatusLabel.Caption := 'Removing the program...';
  Kept := KvValues(UninstLines, 'kept');
  Later := KvValue(UninstLines, 'notice_restart');
  LeftText := KvValue(UninstLines, 'notice_left');
  StillText := KvValue(UninstLines, 'notice_still');
  if Later <> '' then
    Later := #13#10#13#10 + Later;
  if GetArrayLength(Kept) > 0 then
    UninstallNotice('Kept on purpose', Trim(JoinLines(Kept, '')), mbInformation, HelperExe, LogFile);
  { 0: all gone now, or at the next restart by itself (owner fixes
    2026-10-12, items 8 and 9: the profile Windows still held is deleted by
    a one-time startup task, not left to the household). }
  if (Code = 0) and (Later <> '') then
    UninstallNotice('Domovoi is removed', Trim(Later), mbInformation, HelperExe, LogFile);
  if Code = 0 then
    Exit;
  { 4: finished; what is left holds no data and grants nothing (or this was
    the third try): say so, point at the log for removing it by hand, and
    let Uninstall remove the program, so nothing here asks for the
    uninstaller again. }
  if Code = 4 then
  begin
    if LeftText = '' then
      LeftText := 'A few things could not be removed. They hold none of your data and are safe to leave.';
    UninstallNotice('Domovoi is removed', LeftText + Later + #13#10#13#10 +
      'To remove them by hand anyway, the uninstall''s log says how: ' + LogFile, mbInformation, HelperExe, LogFile);
    Exit;
  end;
  { Anything else short of "all gone": stop here, before Uninstall deletes
    C:\Program Files\Domovoi and the Apps entry, so the uninstaller (and the
    helper it runs) stay for the retry. }
  if Code = 3 then
  begin
    if StillText = '' then
      StillText := 'Windows was still using some of Domovoi.';
    UninstallNotice('Domovoi is not fully removed yet', StillText + Later + #13#10#13#10 +
      'Restart Windows, then uninstall Domovoi again (Settings > Apps > Installed apps > Domovoi > Uninstall). It carries on where it stopped.' + #13#10#13#10 +
      'Details: ' + LogFile, mbInformation, HelperExe, LogFile);
  end
  else
    UninstallNotice('The uninstaller stopped', 'It stopped before removing Domovoi''s account, service or data: ' + KvValue(UninstLines, 'error') + #13#10#13#10 +
      'Nothing of Domovoi''s data was deleted, and Domovoi stays in Installed apps. Fix what the message says, then uninstall Domovoi again (Settings > Apps > Installed apps > Domovoi > Uninstall); it carries on where it stopped.' + #13#10#13#10 +
      'Details: ' + LogFile, mbError, HelperExe, LogFile);
  Abort;
end;
