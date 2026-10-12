; DomovoiSetup.exe: the Windows installer's first test build (step 2).
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
; resume, then the helper's steps and DomovoiHost's progress, then the
; setup code. Uninstall runs the helper's uninstall, which undoes all of
; it, and works after a half-finished install too.
;
; Build: windows\build.ps1 (Inno Setup 6). Docs: docs/WINDOWS_HOST.md.

#include "pins.iss"
#define AppVersion "0.1.0-test"
#define Helper "DomovoiSetupHelper.exe"

[Setup]
AppId={{8F3C2A61-5D4B-4E7A-9C1F-2B6D0E4A7C35}
AppName=Domovoi
AppVersion={#AppVersion}
AppVerName=Domovoi {#AppVersion} (test build, {#Copy(DomovoiCommit, 1, 7)})
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
VersionInfoVersion=0.1.0.0
VersionInfoCompany=Coders Farm
VersionInfoDescription=Domovoi Setup (test build)
VersionInfoProductName=Domovoi

[Files]
; The helper first: preflight extracts it before anything is installed.
Source: "..\build\bin\{#Helper}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\bin\DomovoiHost.exe"; DestDir: "{app}"; Flags: ignoreversion

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
  InternetDoc = '{#DomovoiRepoUrl}/blob/main/docs/INTERNET.md#what-goes-out';
  DashboardUrl = 'http://localhost:6369';

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
  GpuText, TierText: String;
  CodeTitle, CodeHint, LanText: TNewStaticText;
  CodeEdit: TNewEdit;
  CopyButton, OpenButton: TNewButton;
  StopButton: TNewButton;
  StopRequested: Boolean;

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

{ -- preflight (PLAN 4.1) ----------------------------------------------- }

{ One pop-up per program that holds a port Domovoi needs, with Retry and
  Cancel (owner decision 2026-10-09). False: the user cancelled. }
function ResolvePorts(Lines: TArrayOfString): Boolean;
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
        mbError, MB_RETRYCANCEL, ['Retry', 'Cancel Setup'], 0) <> IDRETRY then
      begin
        Result := False;
        Exit;
      end;
    end;
    if not RunHelper(TmpHelper, 'preflight --ports-only', Lines, Code) then
    begin
      MsgBox('Setup could not check the ports again.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
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
        'If Windows blocked DomovoiSetupHelper.exe, Smart App Control or an antivirus program is stopping unsigned programs: this test build is unsigned (see docs/WINDOWS_HOST.md, "Unsigned").',
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
    if not ResolvePorts(Preflight) then
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
  S := 'Uninstalling Domovoi (Settings > Apps > Installed apps > Domovoi) undoes every change below.' + #13#10 + #13#10;
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
  S := S + IntToStr(N) + '. Setup''s install record in the registry, HKLM\SOFTWARE\Domovoi: what Setup changed, so the uninstaller can undo it.' + #13#10#13#10;
  S := S + 'Downloads: about 8-9 GB. Ubuntu (418 MB) now; then, inside the distro, Domovoi''s code at commit {#Copy(DomovoiCommit, 1, 7)}, Docker Engine, Python packages and the AI models, each checked against a pinned hash.' + #13#10#13#10;
  S := S + 'Not changed: power and sleep settings (this test build doesn''t ask), Windows Update, Smart App Control, your user accounts and files.' + #13#10#13#10;
  S := S + 'This test build is not code-signed: Windows SmartScreen may have warned before Setup started, and Windows may ask again about its programs.' + #13#10#13#10;
  S := S + 'Uninstalling never deletes files in your media folders: a media folder is removed only if it is empty.';
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
  T := AddText(InternetPage, 'Setup needs the internet right now to download Domovoi (about 8-9 GB). After that, Domovoi works with or without it. Your voice and recordings never leave this PC either way.', 0, 0, False, False);
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
  { localhost, not the LAN address: a secure context for the browser (PLAN 4.12). }
  ShellExecAsOriginalUser('open', DashboardUrl, '', '', SW_SHOWNORMAL, ewNoWait, Err);
end;

procedure CreateFinishControls;
var
  P: TNewNotebookPage;
begin
  P := WizardForm.FinishedPage;
  CodeTitle := TNewStaticText.Create(WizardForm);
  CodeTitle.Parent := P;
  CodeTitle.Font.Style := [fsBold];
  CodeTitle.Caption := 'Your setup code';
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
  CodeHint.Caption := 'Shown only here. Open Domovoi and enter it to make your admin account; it works for 24 hours.';
  OpenButton := TNewButton.Create(WizardForm);
  OpenButton.Parent := P;
  OpenButton.Caption := 'Open Domovoi';
  OpenButton.OnClick := @OpenDomovoiClick;
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
  Y := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(12);
  CodeTitle.SetBounds(X, Y, W, ScaleY(16));
  Y := Y + ScaleY(20);
  CopyButton.SetBounds(X + W - ScaleX(80), Y, ScaleX(80), ScaleY(25));
  CodeEdit.SetBounds(X, Y + ScaleY(1), W - ScaleX(88), ScaleY(23));
  Y := Y + ScaleY(32);
  CodeHint.SetBounds(X, Y, W, ScaleY(30));
  CodeHint.AdjustHeight;
  Y := CodeHint.Top + CodeHint.Height + ScaleY(10);
  OpenButton.SetBounds(X, Y, ScaleX(120), ScaleY(25));
  Y := Y + ScaleY(36);
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
  LanText.Visible := True;
  if InstallOk then
  begin
    WizardForm.FinishedHeadingLabel.Caption := 'Domovoi is running';
    WizardForm.FinishedLabel.Caption := 'Domovoi runs in the background from now on, also when nobody is signed in. The dashboard at ' + DashboardUrl + ' is where you use it.';
    CodeEdit.Text := SetupCodeText;
    if SetupCodeText = '' then
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 + 'Setup could not read the setup code. Restarting the DomovoiHost service writes a fresh one (see docs/WINDOWS_HOST.md).';
    LanText.Caption := 'Phones and satellites reach Domovoi at http://' + LanIp + ':6369. Give this PC a fixed address in your router (a DHCP reservation for ' + LanMac + '), or satellites lose Domovoi when the address changes.' + #13#10 +
      'AI models run on: ' + GpuText + '. Model size: ' + TierText + '.';
  end
  else
  begin
    WizardForm.FinishedHeadingLabel.Caption := 'Setup did not finish';
    if UndoOnExit then
      WizardForm.FinishedLabel.Caption := FailureText + #13#10#13#10 + 'When you click Finish, Domovoi''s uninstaller removes everything Setup changed.'
    else
      WizardForm.FinishedLabel.Caption := FailureText + #13#10#13#10 + 'What Setup changed so far is still there. Run Setup again to carry on, or remove it with Settings > Apps > Installed apps > Domovoi > Uninstall.';
    LanText.Caption := 'Logs: C:\ProgramData\Domovoi\logs (install.log, host.log, provision.log).';
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
    if TaskDialogMsgBox(Title + ': it did not work', Err + #13#10#13#10 + 'Logs: C:\ProgramData\Domovoi\state\install.log and C:\ProgramData\Domovoi\logs',
      mbError, MB_RETRYCANCEL, ['Retry', 'Stop Setup'], 0) <> IDRETRY then
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
  Lines, Ignored: TArrayOfString;
  Code, Percent, WantAttempt: Integer;
  State, Service, Err: String;
  Started, LimitMs: Cardinal;
begin
  Result := False;
  WantAttempt := 0;
  Started := GetTickCount;
  { Four hours: far more than a slow line needs for 8-9 GB. }
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
      GpuText := KvValue(Lines, 'gpu');
      if (GpuText = '') or (GpuText = 'none') then
        GpuText := 'the processor (no GPU found)';
      TierText := KvValue(Lines, 'tier');
      Result := True;
      Exit;
    end;
    ProgressPage.SetText(KvValue(Lines, 'message'), SizeText(KvValue(Lines, 'bytes'), KvValue(Lines, 'bytes_total')));
    ProgressPage.SetProgress(Percent, 100);
    Err := '';
    if State = 'failed' then
      Err := KvValue(Lines, 'host_error')
    else if (Service = 'stopped') or (Service = 'missing') then
      Err := 'the DomovoiHost service is not running'
    else if StrToIntDef(KvValue(Lines, 'age_sec'), 0) > 300 then
      Err := 'DomovoiHost has not reported for five minutes';
    if Err <> '' then
    begin
      Log('Host problem: ' + Err);
      if TaskDialogMsgBox('Setting up Domovoi''s Linux system did not work', Err + #13#10#13#10 +
        'Retry carries on where it stopped. Logs: C:\ProgramData\Domovoi\logs (host.log, provision.log).',
        mbError, MB_RETRYCANCEL, ['Retry', 'Stop Setup'], 0) <> IDRETRY then
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
      ' --provision-sha256 {#ProvisionSha256} --repo-url {#DomovoiRepoUrl} --ref {#DomovoiCommit} --lan-ip auto', 12, Lines) then Exit;
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
    if not ForceDirectories(ExtractFileDir(ResumeExe)) or not FileCopy(ExpandConstant('{srcexe}'), ResumeExe, False) then
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
  { Smart App Control blocks unsigned programs, and this test build is
    unsigned (code signing is on hold). If Setup is running at all, Windows
    let it start; the helper and the service it unpacks would still be
    blocked, so say so now rather than fail later. 0 off, 1 on, 2
    evaluation. }
  if RegQueryDWordValue(HKLM, 'SYSTEM\CurrentControlSet\Control\CI\Policy', 'VerifiedAndReputablePolicyState', Sac) and (Sac = 1) then
  begin
    SuppressibleMsgBox('Smart App Control is on, and this test build of Domovoi is not code-signed. Windows would block Setup''s helper and the Domovoi service, so Setup stops here.' + #13#10#13#10 +
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
    DownloadPage.Add('{#ProvisionUrl}', 'provision.sh', '{#ProvisionSha256}');
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
    'Next, Setup downloads Ubuntu''s WSL image (418 MB) and provision.sh, then installs.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Lines: TArrayOfString;
  Code: Integer;
begin
  Result := '';
  { A repair or a second run: stop the host so its exe can be replaced. }
  if FileExists(AppHelper) then
    RunHelper(AppHelper, 'host-stop', Lines, Code);
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
    if not InstallOk then
    begin
      UndoOnExit := MsgBox(FailureText + #13#10#13#10 + 'Remove what Setup has changed so far? (Recommended unless you want to look at the logs first; you can also run Setup again later to carry on.)',
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

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Output: TExecOutput;
  Code: Integer;
  HelperExe: String;
  Lines: TArrayOfString;
  Left, Kept: TArrayOfString;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  HelperExe := ExpandConstant('{app}\{#Helper}');
  if not FileExists(HelperExe) then
  begin
    SuppressibleMsgBox(HelperExe + ' is missing, so the uninstaller can''t undo what Setup changed. Run Domovoi Setup again (it repairs the program), then uninstall.', mbError, MB_OK, IDOK);
    Abort;
  end;
  UninstallProgressForm.StatusLabel.Caption := 'Removing Domovoi''s Linux system, its hidden account, its service and its firewall rules. This can take a few minutes...';
  try
    if not ExecAndCaptureOutput(HelperExe, 'uninstall', '', SW_HIDE, ewWaitUntilTerminated, Code, Output) then
      Code := -1;
    Lines := Output.StdOut;
  except
    Code := -1;
  end;
  Log('Uninstall helper exit code ' + IntToStr(Code) + '; record: ' + KvValue(Lines, 'log'));
  Left := KvValues(Lines, 'leftover');
  Kept := KvValues(Lines, 'kept');
  if GetArrayLength(Kept) > 0 then
    SuppressibleMsgBox('Kept on purpose:' + #13#10#13#10 + JoinLines(Kept, '- '), mbInformation, MB_OK, IDOK);
  if Code = 0 then
    Exit;
  { 4: finished; what is left holds no data and grants nothing (or this was
    the third try): list it with how to remove it by hand, and let Uninstall
    remove the program. }
  if Code = 4 then
  begin
    SuppressibleMsgBox('Domovoi is removed. A few things could not be, and are safe to leave or remove by hand:' + #13#10#13#10 +
      JoinLines(Left, '- ') + #13#10 + 'The same list is in ' + KvValue(Lines, 'log'), mbInformation, MB_OK, IDOK);
    Exit;
  end;
  { Anything else short of "all gone": stop here, before Uninstall deletes
    C:\Program Files\Domovoi and the Apps entry, so the uninstaller (and the
    helper it runs) stay for the retry. }
  if Code = 3 then
    SuppressibleMsgBox('Domovoi is not fully removed yet (try ' + KvValue(Lines, 'run') + '). Still there:' + #13#10#13#10 + JoinLines(Left, '- ') + #13#10 +
      'Restart Windows, then uninstall Domovoi again (Settings > Apps > Installed apps > Domovoi > Uninstall); it carries on where it stopped. On the third try it finishes and lists whatever is left.' + #13#10#13#10 +
      'Details: ' + KvValue(Lines, 'log'), mbInformation, MB_OK, IDOK)
  else
    SuppressibleMsgBox('The uninstaller stopped before removing Domovoi''s account, service or data: ' + KvValue(Lines, 'error') + #13#10#13#10 +
      'Nothing of Domovoi''s data was deleted. Fix what the message says, then uninstall Domovoi again (Settings > Apps > Installed apps > Domovoi > Uninstall); it carries on where it stopped.' + #13#10#13#10 +
      'Details: ' + KvValue(Lines, 'log'), mbError, MB_OK, IDOK);
  Abort;
end;
