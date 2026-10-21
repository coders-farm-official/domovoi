; DomovoiSetup.exe: the Windows installer, a preview (owner decision 2, 2026-10-16).
;
; Setup is only the user interface. Every change to the PC is made by
; DomovoiSetupHelper.exe, run elevated from C:\Program Files\Domovoi
; (admin-owned) after Setup copies it there; everything that runs as the
; hidden account is done by DomovoiHost.exe, the service. Setup itself
; only runs Windows' own dism.exe and msiexec.exe (System32) when WSL's
; prerequisites are missing, before the install.
;
; Flow (PLAN 4.1-4.12): preflight (ports by owner, Retry/Cancel), what
; changes, the internet question, the media folder, downloads (pinned,
; SHA-256 checked), Virtual Machine Platform and WSL with restart and
; resume, then the helper's steps and DomovoiHost's progress (the ports
; checked again right before Domovoi starts, and after a failed step),
; then the setup code. Uninstall runs the helper's uninstall, which undoes
; all of it, works after a half-finished install too, and shows each step
; as it goes. Every error page offers "Copy logs": the logs are in an
; administrators-only folder, so the helper copies them, redacted, to
; Documents\Domovoi logs and Explorer opens it.
;
; Build: windows\build.ps1 (Inno Setup 6). Docs: docs/WINDOWS_HOST.md.

#include "pins.iss"
; Where "Open Domovoi" lands (pins.iss, DashboardTabRoutes): Settings >
; Configuration with the first-run admin setup open, in a dashboard that
; has the tab addresses; Settings on its first tab in one from before them.
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
#define AppVersion "1.0.0-rc3"
#define Helper "DomovoiSetupHelper.exe"
; Which build this is, for the dashboard link on the last page: a URL no
; earlier build opened, so an old cache-first service worker of the
; dashboard can't answer it from its cache.
#define BuildStamp GetDateTimeString('yyyymmddhhnnss', '', '')

[Setup]
AppId={{8F3C2A61-5D4B-4E7A-9C1F-2B6D0E4A7C35}
AppName=Domovoi
AppVersion={#AppVersion}
AppVerName=Domovoi {#AppVersion} (preview, {#Copy(DomovoiCommit, 1, 7)})
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
RestartIfNeededByRun=no
UsePreviousAppDir=no
Uninstallable=yes
UninstallDisplayName=Domovoi
VersionInfoVersion=1.0.0.0
VersionInfoProductTextVersion={#AppVersion}
VersionInfoCompany=Coders Farm
VersionInfoDescription=Domovoi Setup (preview)
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
ConfirmUninstall=Remove Domovoi from this PC?%n%nThis deletes Domovoi's Linux system with its database (settings, history, voice profiles), the hidden account domovoi-svc, the DomovoiHost service, Domovoi's firewall rules and C:\ProgramData\Domovoi.%n%nFiles in your media folders are kept; a media folder is removed only if it is empty.

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
  LogsHint = 'Logs: C:\ProgramData\Domovoi\logs (only administrators can open it). Copy logs puts a copy in Documents\Domovoi logs and opens it.';

var
  Resumed: Boolean;
  NoDenyLogon: Boolean;
  ForceCancel: Boolean;
  Preflight: TArrayOfString;
  PreflightDone: Boolean;
  LanIp, LanMac, LanSubnet, LanPrivate, WslMemGb, VmpState, WslState: String;

  PreflightPage: TOutputMsgMemoWizardPage;
  ConsentPage: TOutputMsgMemoWizardPage;
  InternetPage: TWizardPage;
  RadioAlways, RadioSometimes, RadioNever: TNewRadioButton;
  MediaPage: TInputDirWizardPage;
  DownloadPage: TDownloadWizardPage;
  ProgressPage: TOutputProgressWizardPage;
  CheckPage: TOutputMarqueeProgressWizardPage;

  InstallOk: Boolean;
  FailureText: String;
  UndoOnExit: Boolean;
  SetupCodeText: String;
  GpuRaw, TierText: String;
  CodeTitle, CodeHint, LanText: TNewStaticText;
  CodeEdit: TNewEdit;
  CopyButton, OpenButton, CopyLogsButton: TNewButton;
  StopButton: TNewButton;
  StopRequested: Boolean;

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
  URL) to a new folder under Documents\Domovoi logs, and Explorer opens
  it. Kind: install or uninstall; UninstallLog: the uninstall's own record
  in the temp folder, or ''. }
procedure CopyLogs(const Exe, Kind, UninstallLog: String);
var
  Lines: TArrayOfString;
  Code, Err: Integer;
  Params, Folder: String;
begin
  if not FileExists(Exe) then
  begin
    MsgBox('The logs can''t be copied: ' + Exe + ' is missing.', mbError, MB_OK);
    Exit;
  end;
  Params := 'copy-logs --kind ' + Kind + ' --dest ' + AddQuotes(ExpandConstant('{userdocs}\Domovoi logs'));
  if UninstallLog <> '' then
    Params := Params + ' --uninstall-log ' + AddQuotes(UninstallLog);
  if not RunHelper(Exe, Params, Lines, Code) or (Code <> 0) then
  begin
    MsgBox('The logs could not be copied: ' + KvValue(Lines, 'error'), mbError, MB_OK);
    Exit;
  end;
  Folder := KvValue(Lines, 'folder');
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

function RunPreflight: Boolean;
var
  Fatal, Warn: TArrayOfString;
  Code: Integer;
  Ran: Boolean;
begin
  Result := False;
  while True do
  begin
    CheckPage.SetText('Checking Windows, memory, disk, ports and the internet...', '');
    CheckPage.Show;
    try
      CheckPage.Animate;
      Ran := RunHelper(TmpHelper, 'preflight', Preflight, Code);
    finally
      CheckPage.Hide;
    end;
    if not Ran or (Code <> 0) then
    begin
      if MsgBox('Setup could not check this PC (' + KvValue(Preflight, 'error') + ').' + #13#10#13#10 +
        'If Windows blocked DomovoiSetupHelper.exe, Smart App Control or an antivirus program is stopping unsigned programs: this preview is unsigned (see docs/WINDOWS_HOST.md, "Unsigned").',
        mbError, MB_RETRYCANCEL) = IDRETRY then
        Continue;
      Exit;
    end;
    Fatal := KvValues(Preflight, 'fatal');
    if GetArrayLength(Fatal) > 0 then
    begin
      if TaskDialogMsgBox('Domovoi can''t be installed on this PC yet', JoinLines(Fatal, ''), mbCriticalError,
        MB_RETRYCANCEL, ['Check again', 'Cancel Setup'], 0) = IDRETRY then
        Continue;
      Exit;
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
    'Internet: ' + KvValue(Preflight, 'internet') + #13#10 +
    'Smart App Control: ' + KvValue(Preflight, 'smart_app_control') + #13#10 +
    'Ports Domovoi needs: free' + #13#10 + #13#10 +
    JoinLines(Warn, 'Note: ');
  PreflightDone := True;
  Result := True;
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
    S := S + IntToStr(N) + '. Six Hyper-V firewall rules for WSL, named Domovoi-..., so phones and satellites on your home network (' + LanSubnet + ') reach Domovoi: TCP 6369 (dashboard), 6370 (voice), 8050-8099 (music streams), 8096, 7676 and 9467 (plugins). They apply to every WSL VM on this PC, your own WSL distros and Docker Desktop''s included: anything of yours listening on those ports in mirrored mode becomes reachable from your home network too.' + #13#10#13#10
  else
    S := S + IntToStr(N) + '. No firewall rules: this PC has no private home-network address, so Domovoi answers on this PC only.' + #13#10#13#10;
  N := N + 1;
  S := S + IntToStr(N) + '. A media folder you can open in Explorer (you choose it next; default C:\Users\Public\Domovoi) with Music, Pictures, Videos, Documents and Audiobooks inside. domovoi-svc may read and write it; if Setup makes the folder, every user of this PC (Users) may too.' + #13#10#13#10;
  N := N + 1;
  S := S + IntToStr(N) + '. C:\Program Files\Domovoi (this program) and C:\ProgramData\Domovoi (Domovoi''s Linux system, logs and settings; only administrators and domovoi-svc can open it). Setup won''t use a C:\ProgramData\Domovoi it didn''t make.' + #13#10#13#10;
  N := N + 1;
  S := S + IntToStr(N) + '. Domovoi''s icon in the notification area (DomovoiTray.exe), for every account on this PC: it starts when anyone signs in (a DomovoiTray value under HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run), and the Start menu gets a Domovoi entry. It shows whether Domovoi is running, opens the dashboard and prepares satellite cards; it never runs as an administrator, and each account can turn its start off.' + #13#10#13#10;
  N := N + 1;
  S := S + IntToStr(N) + '. Setup''s install record in the registry, HKLM\SOFTWARE\Domovoi: what Setup changed, so the uninstaller can undo it.' + #13#10#13#10;
  S := S + 'Downloads: about 10.5-13.5 GB (the AI models are most of it; the size depends on the models this PC''s memory gets). Ubuntu (418 MB) now; then, inside the distro, Domovoi''s code at commit {#Copy(DomovoiCommit, 1, 7)} (later updates follow the {#DomovoiChannel} channel{#ChannelWords}; you choose when, in Settings > Configuration > Version), Docker Engine, Python packages and the AI models (one of them reads pictures in chat), each checked against a pinned hash.' + #13#10#13#10;
  S := S + 'Not changed: power and sleep settings (the icon''s "Keep this PC awake" only holds Windows awake while someone is signed in), Windows Update, Smart App Control, your user accounts and files.' + #13#10#13#10;
  S := S + 'This preview is not code-signed: Windows SmartScreen may have warned before Setup started, and Windows may ask again about its programs.' + #13#10#13#10;
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

function AddRadio(Page: TWizardPage; const Caption: String; Top: Integer): TNewRadioButton;
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
  Result.OnClick := @InternetChoiceClick;
end;

procedure CreateInternetPage(AfterID: Integer);
var
  T: TNewStaticText;
  L: TNewLinkLabel;
  Y: Integer;
begin
  InternetPage := CreateCustomPage(AfterID, 'Will this PC have internet after setup?', 'Pick one answer to continue.');
  T := AddText(InternetPage, 'Setup needs the internet right now to download Domovoi (about 10.5-13.5 GB). After that, Domovoi works with or without it. Your voice and recordings never leave this PC either way.', 0, 0, False, False);
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
  AddText(InternetPage, 'You can change this later in the dashboard: Settings > Internet.', Y, 0, False, False);
end;

function InternetAnswer: String;
begin
  Result := ExpandConstant('{param:internet|}');
  if Result <> '' then
    Exit;
  if RadioAlways.Checked then
    Result := 'always'
  else if RadioSometimes.Checked then
    Result := 'sometimes'
  else if RadioNever.Checked then
    Result := 'never';
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
  domovoi/tests/test_web_settings_routes.py holds it to them). }
function FinishSteps: String;
begin
#ifdef DashboardTabRoutes
  Result := '1. Click Open Domovoi. It copies the code and opens Settings > Configuration with "first-run admin setup" showing (if it isn''t, click "set up admin" in the Admin box).' + #13#10;
#else
  Result := '1. Click Open Domovoi. It copies the code and opens Domovoi''s Settings. Click the Configuration tab (the last one), then "set up admin" in the Admin box.' + #13#10;
#endif
  Result := Result + '2. Paste the code into "setup code", choose an admin password of at least 10 characters, type it again in "confirm password", and click "set password".';
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
  CopyLogsButton.Visible := not InstallOk and FileExists(AppHelper);
  LanText.Visible := True;
  if InstallOk then
  begin
    WizardForm.FinishedHeadingLabel.Caption := 'Domovoi is running';
    WizardForm.FinishedLabel.Caption := 'Domovoi runs in the background from now on, also when nobody is signed in. You use it in your browser at ' + DashboardUrl + '.';
    CodeEdit.Text := SetupCodeText;
    if SetupCodeText = '' then
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 + 'Setup could not read the setup code. Restarting the DomovoiHost service writes a fresh one (see docs/WINDOWS_HOST.md).';
    LanText.Caption := 'Phones and satellites reach Domovoi at http://' + LanIp + ':6369. Reserve that address for this PC in your router (DHCP reservation for ' + LanMac + '), or satellites lose Domovoi when it changes.' + #13#10 +
      ModelsText(GpuRaw, TierText);
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
      TierText := KvValue(Lines, 'tier');
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

function ReadSetupCode: String;
var
  Lines: TArrayOfString;
  Code, Tries: Integer;
begin
  Result := '';
  for Tries := 1 to 5 do
  begin
    ProgressPage.SetText('Reading your setup code', '');
    if RunHelper(AppHelper, 'setup-code', Lines, Code) and (Code = 0) then
    begin
      Result := KvValue(Lines, 'code');
      SetArrayLength(Lines, 0);
      if Result <> '' then
        Exit;
    end;
    Log('Setup code not available yet (try ' + IntToStr(Tries) + ')');
    Wait(5000, 99);
  end;
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
  try
    if not Step('Recording your answers', 'prepare --internet ' + InternetAnswer + ' --media-root ' + AddQuotes(RemoveBackslashUnlessRoot(MediaPage.Values[0])) + ' --commit {#DomovoiCommit}', 2, Lines) then Exit;
    Deny := '';
    if NoDenyLogon then
      Deny := ' --no-deny-logon';
    if not Step('Creating the hidden account domovoi-svc and the DomovoiHost service', 'account --exe ' + AddQuotes(ExpandConstant('{app}\DomovoiHost.exe')) + Deny, 4, Lines) then Exit;
    if not Step('Setting up the media folders', 'media', 6, Lines) then Exit;
    if not Step('Adding the firewall rules', 'firewall', 8, Lines) then Exit;
    if not Step('Handing the work to DomovoiHost', 'job --image ' + AddQuotes(ExpandConstant('{tmp}\{#UbuntuName}')) +
      ' --image-sha256 {#UbuntuSha256} --provision ' + AddQuotes(ExpandConstant('{tmp}\provision.sh')) +
      ' --provision-sha256 {#ProvisionSha256} --repo-url {#DomovoiRepoUrl} --ref {#DomovoiCommit} --track-branch {#DomovoiChannel} --lan-ip auto', 12, Lines) then Exit;
    if not Step('Starting DomovoiHost', 'host-start', 14, Lines) then Exit;
    if not WaitForHost then Exit;
    ProgressPage.SetProgress(100, 100);
    StopButton.Visible := False;
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
  administrators can change. The uninstaller removes that copy. }
procedure CreateRunOnceEntry;
var
  Data, ResumeExe: String;
begin
  ResumeExe := ExpandConstant('{app}\resume\DomovoiSetup.exe');
  { A second restart: Setup already runs from that copy. }
  if not SameText(ExpandConstant('{srcexe}'), ResumeExe) then
    if not ForceDirectories(ExtractFileDir(ResumeExe)) or not CopyFile(ExpandConstant('{srcexe}'), ResumeExe, False) then
      RaiseException('Setup could not copy itself to ' + ResumeExe + ' to carry on after the restart.');
  Data := AddQuotes(ResumeExe) + ' /resume=1 /internet=' + InternetAnswer +
    ' /mediaroot=' + AddQuotes(RemoveBackslashUnlessRoot(MediaPage.Values[0]));
  if NoDenyLogon then
    Data := Data + ' /nodenylogon=1';
  RegWriteStringValue(HKLM, RunOnceKey, RunOnceName, Data);
end;

{ -- event functions ---------------------------------------------------- }

function InitializeSetup: Boolean;
var
  Sac: Cardinal;
begin
  { Smart App Control blocks unsigned programs, and this preview is
    unsigned (code signing is on hold). If Setup is running at all, Windows
    let it start; the helper and the service it unpacks would still be
    blocked, so say so now rather than fail later. 0 off, 1 on, 2
    evaluation. }
  if RegQueryDWordValue(HKLM, 'SYSTEM\CurrentControlSet\Control\CI\Policy', 'VerifiedAndReputablePolicyState', Sac) and (Sac = 1) then
  begin
    SuppressibleMsgBox('Smart App Control is on, and this preview of Domovoi is not code-signed. Windows would block Setup''s helper and the Domovoi service, so Setup stops here.' + #13#10#13#10 +
      'To install it anyway, turn Smart App Control off in Windows Security > App & browser control > Smart App Control settings. On many Windows 11 builds it can only be turned back on by resetting or reinstalling Windows; read what Windows Security says first (docs/WINDOWS_HOST.md, "Unsigned").',
      mbCriticalError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  Resumed := ExpandConstant('{param:resume|0}') = '1';
  NoDenyLogon := ExpandConstant('{param:nodenylogon|0}') = '1';
  ExtractTemporaryFile('{#Helper}');
  Result := True;
end;

procedure InitializeWizard;
var
  Root: String;
begin
  PreflightPage := CreateOutputMsgMemoPage(wpWelcome, 'This PC', 'What Setup found', 'Setup checked this PC. Nothing has been changed yet.', '');
  ConsentPage := CreateOutputMsgMemoPage(PreflightPage.ID, 'What Setup will change', 'Read this before you go on',
    'Domovoi runs a small Linux system on this PC, as its own hidden account. To set that up, Setup makes these changes:', '');
  CreateInternetPage(ConsentPage.ID);
  MediaPage := CreateInputDirPage(InternetPage.ID, 'Your media folder', 'Where your music, pictures and videos live',
    'Domovoi keeps your media in a Windows folder you can open in Explorer and copy files into. Setup makes Music, Pictures, Videos, Documents and Audiobooks inside it. Pick a folder on a drive of this PC.',
    False, '');
  MediaPage.Add('Media folder:');
  Root := ExpandConstant('{param:mediaroot|}');
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
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  { After the restart the answers come back on the command line. }
  if Resumed and (ExpandConstant('{param:internet|}') <> '') then
    Result := (PageID = ConsentPage.ID) or (PageID = InternetPage.ID) or (PageID = MediaPage.ID);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = ConsentPage.ID then
    ConsentPage.RichEditViewer.Lines.Text := ConsentText
  else if CurPageID = InternetPage.ID then
    WizardForm.NextButton.Enabled := RadioAlways.Checked or RadioSometimes.Checked or RadioNever.Checked
  else if CurPageID = wpFinished then
    ShowFinish;
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
      MsgBox(Problem, mbError, MB_OK);
      Result := False;
    end;
  end
  else if CurPageID = wpReady then
  begin
    DownloadPage.Clear;
#ifdef ProvisionBundled
    { Shipped inside Setup (pins.iss); the helper checks its SHA-256 when it
      hands it to DomovoiHost, and the host again before it runs it. }
    ExtractTemporaryFile('provision.sh');
#else
    DownloadPage.Add('{#ProvisionUrl}', 'provision.sh', '{#ProvisionSha256}');
#endif
    DownloadPage.Add('{#UbuntuUrl}', '{#UbuntuName}', '{#UbuntuSha256}');
    if (WslState = 'missing') or (WslState = 'old') then
      DownloadPage.Add('{#WslMsiUrl}', '{#WslMsiName}', '{#WslMsiSha256}');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
      except
        if not DownloadPage.AbortedByUser then
          SuppressibleMsgBox('A download did not work: ' + GetExceptionMessage, mbCriticalError, MB_OK, IDOK);
        Result := False;
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if ForceCancel then
    Confirm := False;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := 'Internet after setup:' + NewLine + Space + InternetAnswer + NewLine + NewLine +
    'Media folder:' + NewLine + Space + RemoveBackslashUnlessRoot(MediaPage.Values[0]) + NewLine + NewLine +
    'Domovoi:' + NewLine + Space + 'commit {#DomovoiCommit}' + NewLine + NewLine +
#ifdef ProvisionBundled
    'Next, Setup downloads Ubuntu''s WSL image (418 MB), then installs.';
#else
    'Next, Setup downloads Ubuntu''s WSL image (418 MB) and provision.sh, then installs.';
#endif
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Lines: TArrayOfString;
  Code: Integer;
begin
  Result := '';
  { A repair or a second run: stop the host so its exe can be replaced,
    and the tray icons, theirs. }
  if FileExists(AppHelper) then
    RunHelper(AppHelper, 'host-stop', Lines, Code);
  StopTrays;
  if VmpState <> 'enabled' then
  begin
    Log('Enabling VirtualMachinePlatform');
    if not Exec(ExpandConstant('{sys}\dism.exe'), '/online /enable-feature /featurename:VirtualMachinePlatform /all /norestart /quiet', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Code := -1;
    if Code = 3010 then
    begin
      CreateRunOnceEntry;
      NeedsRestart := True;
      Result := 'Windows has to restart to finish turning on Virtual Machine Platform. Setup starts again by itself after you sign in, and carries on from here.';
      Exit;
    end;
    if Code <> 0 then
    begin
      Result := 'Windows could not turn on Virtual Machine Platform (dism exit code ' + IntToStr(Code) + ').';
      Exit;
    end;
  end;
  if WslState = 'denied' then
  begin
    Result := 'This PC''s WSL version has a known problem with the networking Domovoi uses. Update it (run "wsl --update" as an administrator), then run Setup again.';
    Exit;
  end;
  if (WslState = 'missing') or (WslState = 'old') then
  begin
    Log('Installing WSL 2.7.14');
    if not Exec(ExpandConstant('{sys}\msiexec.exe'), '/i ' + AddQuotes(ExpandConstant('{tmp}\{#WslMsiName}')) + ' /qn /norestart', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Code := -1;
    if Code = 3010 then
    begin
      CreateRunOnceEntry;
      NeedsRestart := True;
      Result := 'Windows has to restart to finish installing WSL. Setup starts again by itself after you sign in, and carries on from here.';
      Exit;
    end;
    if Code <> 0 then
      Result := 'WSL did not install (msiexec exit code ' + IntToStr(Code) + ').';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
  Code: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    InstallOk := MachineSetup;
    if InstallOk then
      StartTray;
    if not InstallOk then
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
begin
  { The code was only ever in memory; drop it. }
  SetupCodeText := '';
  if UndoOnExit and FileExists(ExpandConstant('{uninstallexe}')) then
    Exec(ExpandConstant('{uninstallexe}'), '/SILENT /NORESTART', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

{ -- uninstall (PLAN 7) ------------------------------------------------- }

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
  HelperExe, LogFile, Later, LeftText, StillText: String;
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
    if not ExecAndLogOutput(HelperExe, 'uninstall', '', SW_HIDE, ewWaitUntilTerminated, Code, @UninstallOutput) then
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
