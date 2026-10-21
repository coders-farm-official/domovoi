; DomovoiSatelliteSetup.exe: makes a spare Windows laptop or PC a Domovoi
; room, by double-click (2026-10-21; docs/SPARE_DEVICE_SATELLITES.md).
;
; Only the wizard. Everything it does is the dashboard's PowerShell command,
; run for the household: satellite-setup.ps1 (packed in here, tested by
; domovoi/tests/test_windows_satellite_setup.py) trades the setup code for
; the room's enrolment payload and the satellite bundle's SHA-256 at the
; Domovoi server, downloads the bundle from that server, runs nothing
; unless the digest matches, and runs the bundle's own
; satellite\scripts\install-windows.ps1, which refuses the PC that runs the
; Domovoi server, makes the server prove its fingerprint before anything
; is written, and installs for the signed-in account only, with no
; administrator rights. So this Setup copies no files of its own, writes
; nothing to the registry and has no uninstaller of its own: the satellite
; registers itself in Installed apps ("Domovoi Satellite (room)", its
; uninstall.ps1 removes everything it added).
;
; The setup code and the server: a download from the dashboard's link is
; named DomovoiSatelliteSetup_<code>_<host>_<port>.exe by the server, and
; Setup reads them back from its own file name; otherwise it asks. On the
; command line: /SERVER= /CODE= /PERMISSIONS=ask|automatic
; /POWER=no|standby|lid|yes /INSTALLROOT= (with /VERYSILENT for a silent
; install).
;
; Not code-signed (owner decision 2026-10-21): Windows SmartScreen says
; "Windows protected your PC" until it is; the dashboard and the docs say
; what to press. Signing goes back in at windows\build.ps1 -Sign (Invoke-Sign,
; which signs DomovoiSetup.exe too): ISCC's SignTool, below, then signs this
; Setup.
;
; Build: windows\build.ps1 (Inno Setup 6), beside DomovoiSetup.exe.

#define AppVersion "1.0.0-rc3"
#define Worker "satellite-setup.ps1"

[Setup]
AppId={{6948152C-CFA6-47C5-BDC3-A3E1608DABC1}
AppName=Domovoi Satellite
AppVersion={#AppVersion}
AppVerName=Domovoi Satellite Setup {#AppVersion} (preview)
AppPublisher=Coders Farm
AppPublisherURL=https://github.com/coders-farm-official/domovoi
AppSupportURL=https://github.com/coders-farm-official/domovoi/blob/main/docs/SPARE_DEVICE_SATELLITES.md
; For the signed-in account only, never elevated: the satellite must never
; run with administrator rights (install-windows.ps1 refuses an elevated
; window too).
PrivilegesRequired=lowest
CreateAppDir=no
Uninstallable=no
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableReadyPage=no
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
; Windows 10 1809 or later: tar.exe and curl's TLS are in the box.
MinVersion=10.0.17763
OutputBaseFilename=DomovoiSatelliteSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern dynamic
CloseApplications=no
RestartIfNeededByRun=no
VersionInfoVersion=1.0.0.0
VersionInfoProductTextVersion={#AppVersion}
VersionInfoCompany=Coders Farm
VersionInfoDescription=Domovoi Satellite Setup (preview)
VersionInfoProductName=Domovoi
SetupIconFile=art\domovoi.ico
WizardImageFile=art\wizard-202x386.png,art\wizard-269x515.png,art\wizard-336x643.png,art\wizard-403x772.png,art\wizard-430x824.png,art\wizard-498x953.png,art\wizard-534x1022.png
WizardSmallImageFile=art\small-58.png,art\small-77.png,art\small-97.png,art\small-116.png,art\small-124.png,art\small-143.png,art\small-159.png
; The signing hook (on hold): build.ps1 -Sign will pass ISCC a sign tool
; named "domovoi" (/Sdomovoi=...), and this line signs Setup with it.
#ifdef SignSetup
SignTool=domovoi
#endif

[Files]
; The worker, extracted to {tmp} only (Setup deletes it when it closes).
Source: "{#Worker}"; Flags: dontcopy

[Messages]
WelcomeLabel1=Make this PC a Domovoi room
ButtonInstall=&Install
ReadyLabel1=Setup is ready to make this PC a room.
ReadyLabel2a=Click Install to continue, or click Back to change something.

[Code]
const
  PsExe = '{sys}\WindowsPowerShell\v1.0\powershell.exe';

var
  CodePage: TInputQueryWizardPage;
  ChoicePage: TWizardPage;
  RoomText: TNewStaticText;
  RadioAsk, RadioAuto: TNewRadioButton;
  CheckStandby, CheckLid: TNewCheckBox;
  WorkPage: TOutputMarqueeProgressWizardPage;
  CodeLabel, HintLabel: TNewStaticText;

  Resolved: Boolean;
  Room, Core, Fingerprint, ServerUrl: String;
  ApprovalCode: String;
  Approved: Boolean;
  Lines: TArrayOfString;

{ -- the worker ---------------------------------------------------------- }

function WorkerFile: String;
begin
  Result := ExpandConstant('{tmp}\{#Worker}');
end;

function ResultFile: String;
begin
  Result := ExpandConstant('{tmp}\result.txt');
end;

{ What may go on the worker's command line from a box a person typed in:
  the characters an address or a code has, nothing that could end the
  quoted argument. }
function Clean(const S: String): String;
var
  I: Integer;
  C: Char;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := S[I];
    if ((C >= 'a') and (C <= 'z')) or ((C >= 'A') and (C <= 'Z')) or ((C >= '0') and (C <= '9')) or
       (Pos(C, '.:-[]/_ ') > 0) then
      Result := Result + C;
  end;
  Result := Trim(Result);
end;

{ A folder for /INSTALLROOT: anything but a quote, and no trailing
  backslash (which would escape the closing quote). }
function CleanPath(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '"', '', True);
  Result := RemoveBackslashUnlessRoot(Trim(Result));
  if (Length(Result) > 0) and (Result[Length(Result)] = '\') then
    Result := Result + '.';
end;

function KvValue(const Res: TArrayOfString; const Key: String): String;
var
  I: Integer;
  P: String;
begin
  Result := '';
  P := Key + '=';
  for I := 0 to GetArrayLength(Res) - 1 do
    if Copy(Res[I], 1, Length(P)) = P then
    begin
      Result := Copy(Res[I], Length(P) + 1, Length(Res[I]));
      Exit;
    end;
end;

procedure AddLine(const S: String);
var
  N: Integer;
begin
  N := GetArrayLength(Lines);
  SetArrayLength(Lines, N + 1);
  Lines[N] := S;
end;

{ The worker's progress, a line at a time (the approval code is never
  among them: the worker writes it to the result file only). }
procedure WorkerOutput(const S: String; const Error, FirstLine: Boolean);
begin
  if Error then
  begin
    Log('Reading the worker''s output: ' + S);
    Exit;
  end;
  Log(S);
  AddLine(S);
  try
    if Trim(S) <> '' then
      WorkPage.SetText('Making this PC a room...', S);
    WorkPage.Animate;
  except
    Log('Showing a line: ' + GetExceptionMessage);
  end;
end;

{ Runs the worker with Args; Res gets its result file. Streaming: its
  output goes to WorkerOutput as it comes. }
function RunWorker(const Args: String; const Streaming: Boolean; var Res: TArrayOfString; var Code: Integer): Boolean;
var
  Params: String;
begin
  DeleteFile(ResultFile);
  SetArrayLength(Res, 0);
  Code := -1;
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + WorkerFile + '" -Out "' + ResultFile + '" ' + Args;
  try
    if Streaming then
      Result := ExecAndLogOutput(ExpandConstant(PsExe), Params, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, Code, @WorkerOutput)
    else
      Result := Exec(ExpandConstant(PsExe), Params, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, Code);
  except
    Log('Could not run the worker: ' + GetExceptionMessage);
    Result := False;
  end;
  if FileExists(ResultFile) then
    LoadStringsFromFile(ResultFile, Res);
end;

function TargetArgs: String;
begin
  Result := '-Server "' + Clean(CodePage.Values[0]) + '" -Code "' + Clean(CodePage.Values[1]) + '"';
end;

{ The code and the server: asks the server what the code names. False
  (and says why) when it can't be used. }
function ResolveCode: Boolean;
var
  Res: TArrayOfString;
  Code: Integer;
  Err: String;
begin
  Result := False;
  if (Clean(CodePage.Values[0]) = '') or (Clean(CodePage.Values[1]) = '') then
  begin
    SuppressibleMsgBox('Type your Domovoi server''s address and the setup code, both as the dashboard shows them (Settings > Satellites > add a spare device).', mbError, MB_OK, IDOK);
    Exit;
  end;
  if not RunWorker('-Resolve ' + TargetArgs, False, Res, Code) then
    Err := 'Setup could not run Windows PowerShell.'
  else if (Code <> 0) or (KvValue(Res, 'result') <> 'ok') then
  begin
    Err := KvValue(Res, 'error');
    if Err = '' then
      Err := 'Setup could not ask the server (status ' + IntToStr(Code) + ').';
  end;
  if Err <> '' then
  begin
    SuppressibleMsgBox(Err, mbError, MB_OK, IDOK);
    Exit;
  end;
  Room := KvValue(Res, 'room');
  Core := KvValue(Res, 'core');
  Fingerprint := KvValue(Res, 'fingerprint');
  ServerUrl := KvValue(Res, 'server');
  Resolved := True;
  Result := True;
end;

function PowerAnswer: String;
var
  P: String;
begin
  P := ExpandConstant('{param:power|}');
  if (P = 'no') or (P = 'standby') or (P = 'lid') or (P = 'yes') then
    Result := P
  else if CheckStandby.Checked and CheckLid.Checked then
    Result := 'yes'
  else if CheckStandby.Checked then
    Result := 'standby'
  else if CheckLid.Checked then
    Result := 'lid'
  else
    Result := 'no';
end;

function PermissionsAnswer: String;
var
  P: String;
begin
  P := ExpandConstant('{param:permissions|}');
  if (P = 'ask') or (P = 'automatic') then
    Result := P
  else if RadioAuto.Checked then
    Result := 'automatic'
  else
    Result := 'ask';
end;

function LastLines(Count: Integer): String;
var
  I, First: Integer;
begin
  Result := '';
  First := GetArrayLength(Lines) - Count;
  if First < 0 then
    First := 0;
  for I := First to GetArrayLength(Lines) - 1 do
    if Trim(Lines[I]) <> '' then
      Result := Result + #13#10 + Lines[I];
end;

{ The install itself. False (and says why) when it did not finish. }
function RunInstall: Boolean;
var
  Res: TArrayOfString;
  Code: Integer;
  Args, Err, Root: String;
begin
  Result := False;
  SetArrayLength(Lines, 0);
  Args := '-Install ' + TargetArgs + ' -Permissions ' + PermissionsAnswer + ' -Power ' + PowerAnswer +
    ' -Work "' + ExpandConstant('{tmp}') + '"';
  Root := CleanPath(ExpandConstant('{param:installroot|}'));
  if Root <> '' then
    Args := Args + ' -InstallRoot "' + Root + '"';
  WorkPage.SetText('Making this PC a room...', 'Starting');
  WorkPage.Show;
  try
    WorkPage.Animate;
    if not RunWorker(Args, True, Res, Code) then
      Err := 'Setup could not run Windows PowerShell.'
    else if (Code <> 0) or (KvValue(Res, 'result') <> 'ok') then
    begin
      Err := KvValue(Res, 'error');
      if Err = '' then
        Err := 'The install stopped (status ' + IntToStr(Code) + ').';
    end;
  finally
    WorkPage.Hide;
  end;
  if Err <> '' then
  begin
    SuppressibleMsgBox('This PC is not a room yet:' + #13#10#13#10 + Err + #13#10#13#10 +
      'The last lines:' + LastLines(6) + #13#10#13#10 +
      'Click Install to try again, or Cancel. More help: docs/SPARE_DEVICE_SATELLITES.md, "When it doesn''t work (Windows)".',
      mbError, MB_OK, IDOK);
    Exit;
  end;
  ApprovalCode := KvValue(Res, 'approval_code');
  Approved := KvValue(Res, 'approved') = '1';
  Result := True;
end;

{ -- pages --------------------------------------------------------------- }

function AddText(Page: TWizardPage; const Caption: String; Top, Indent: Integer; Bold: Boolean): TNewStaticText;
begin
  Result := TNewStaticText.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := Indent;
  Result.Top := Top;
  Result.Width := Page.SurfaceWidth - Indent;
  Result.WordWrap := True;
  Result.AutoSize := True;
  Result.Caption := Caption;
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
end;

function AddCheck(Page: TWizardPage; const Caption: String; Top: Integer): TNewCheckBox;
begin
  Result := TNewCheckBox.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := 0;
  Result.Top := Top;
  Result.Width := Page.SurfaceWidth;
  Result.Height := ScaleY(18);
  Result.Caption := Caption;
  Result.Checked := False;
end;

procedure CreateChoicePage(AfterID: Integer);
var
  T: TNewStaticText;
  Y: Integer;
begin
  ChoicePage := CreateCustomPage(AfterID, 'This PC as a room', 'Two choices, both changeable later.');
  { Two short lines, filled in when the page opens (CurPageChanged). }
  RoomText := AddText(ChoicePage, ' ', 0, 0, False);
  Y := RoomText.Top + ScaleY(40);
  T := AddText(ChoicePage, 'May the Domovoi server update and restart this satellite on its own?', Y, 0, False);
  Y := T.Top + T.Height + ScaleY(6);
  RadioAsk := AddRadio(ChoicePage, 'Ask me first', Y);
  T := AddText(ChoicePage, 'Updates, settings from the dashboard and restarts wait for your OK in the tray icon''s menu.', RadioAsk.Top + RadioAsk.Height, ScaleX(18), False);
  Y := T.Top + T.Height + ScaleY(6);
  RadioAuto := AddRadio(ChoicePage, 'Automatic', Y);
  T := AddText(ChoicePage, 'It installs signed satellite updates, applies settings from the dashboard and restarts when it needs to, like a Raspberry Pi room.', RadioAuto.Top + RadioAuto.Height, ScaleX(18), False);
  RadioAsk.Checked := True;
  Y := T.Top + T.Height + ScaleY(12);
  T := AddText(ChoicePage, 'While it is plugged in (this changes the PC''s power plan, which every account on it shares; removing the satellite puts it back):', Y, 0, False);
  Y := T.Top + T.Height + ScaleY(6);
  CheckStandby := AddCheck(ChoicePage, 'Never sleep (the screen can still turn off)', Y);
  CheckLid := AddCheck(ChoicePage, 'Keep running with the lid closed', CheckStandby.Top + CheckStandby.Height + ScaleY(4));
end;

{ Where the code and the server come from, in order: the command line,
  then this Setup's own file name (the server names a download from the
  dashboard's link that way). }
procedure Prefill;
var
  Res: TArrayOfString;
  Code: Integer;
begin
  CodePage.Values[0] := ExpandConstant('{param:server|}');
  CodePage.Values[1] := ExpandConstant('{param:code|}');
  if (CodePage.Values[0] <> '') and (CodePage.Values[1] <> '') then
    Exit;
  if RunWorker('-Name "' + ExtractFileName(ExpandConstant('{srcexe}')) + '"', False, Res, Code) and (Code = 0) then
  begin
    if (CodePage.Values[1] = '') and (KvValue(Res, 'code') <> '') then
      CodePage.Values[1] := KvValue(Res, 'code');
    if (CodePage.Values[0] = '') and (KvValue(Res, 'server') <> '') then
      CodePage.Values[0] := KvValue(Res, 'server');
  end;
end;

procedure ShowFinish;
var
  Y: Integer;
begin
  WizardForm.FinishedHeadingLabel.Caption := 'This PC is now the room ' + Room;
  if Approved then
    WizardForm.FinishedLabel.Caption := 'It is approved and listening. Say the wake word to try it.'
  else if ApprovalCode <> '' then
    WizardForm.FinishedLabel.Caption := 'One last step: approve it. On the dashboard, open Settings > Satellites. Under "waiting for approval", type this code and press approve:'
  else
    WizardForm.FinishedLabel.Caption := 'One last step: approve it. Its six-digit code is in the notification Windows shows, and in the menu of Domovoi''s icon near the clock. On the dashboard, open Settings > Satellites and type it under "waiting for approval".';
  WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
  Y := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(12);
  CodeLabel.Visible := (ApprovalCode <> '') and not Approved;
  CodeLabel.Caption := ApprovalCode;
  CodeLabel.Left := WizardForm.FinishedLabel.Left;
  CodeLabel.Top := Y;
  if CodeLabel.Visible then
    Y := CodeLabel.Top + CodeLabel.Height + ScaleY(12);
  HintLabel.Left := WizardForm.FinishedLabel.Left;
  HintLabel.Width := WizardForm.FinishedLabel.Width;
  HintLabel.Top := Y;
  HintLabel.AdjustHeight;
  WizardForm.RunList.Visible := False;
end;

procedure CreateFinishControls;
begin
  CodeLabel := TNewStaticText.Create(WizardForm);
  CodeLabel.Parent := WizardForm.FinishedPage;
  CodeLabel.Font.Size := 22;
  CodeLabel.Font.Style := [fsBold];
  CodeLabel.Visible := False;
  HintLabel := TNewStaticText.Create(WizardForm);
  HintLabel.Parent := WizardForm.FinishedPage;
  HintLabel.WordWrap := True;
  HintLabel.AutoSize := False;
  HintLabel.Caption := 'It runs while this account is signed in and starts by itself at sign-in; Domovoi''s icon near the clock shows when it listens. To remove it: Settings > Apps > Installed apps > Domovoi Satellite > Uninstall.';
end;

{ -- event functions ------------------------------------------------------ }

function InitializeSetup: Boolean;
var
  Sac: Cardinal;
begin
  Result := False;
  if IsAdmin then
  begin
    SuppressibleMsgBox('Setup is running as administrator. Close it and open it again with a double-click (not "Run as administrator"): the satellite installs for you only and must never run with administrator rights.', mbCriticalError, MB_OK, IDOK);
    Exit;
  end;
  { Smart App Control (Windows 11) checks every program file; parts of the
    satellite are not signed yet. Read only, never changed. 1 on, 2
    evaluation. }
  if RegQueryDWordValue(HKLM, 'SYSTEM\CurrentControlSet\Control\CI\Policy', 'VerifiedAndReputablePolicyState', Sac) then
  begin
    if Sac = 1 then
      SuppressibleMsgBox('Smart App Control is on. It may block parts of the satellite that are not signed yet, with no "run anyway": then the satellite does not start, or Windows says it blocked part of an app. See "Smart App Control" in docs/SPARE_DEVICE_SATELLITES.md.', mbInformation, MB_OK, IDOK)
    else if Sac = 2 then
      SuppressibleMsgBox('Smart App Control is in evaluation mode. Windows may turn it on later and then block parts of the satellite that are not signed yet. See "Smart App Control" in docs/SPARE_DEVICE_SATELLITES.md.', mbInformation, MB_OK, IDOK);
  end;
  ExtractTemporaryFile('{#Worker}');
  Result := True;
end;

procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel2.Caption :=
    'Setup makes this PC a room of your Domovoi: it listens for the wake word, answers, plays the room''s music and takes announcements.' + #13#10#13#10 +
    'It installs for the account signed in now (' + GetUserNameString + ') only, needs no administrator rights, and runs while this account is signed in. Not on the PC that runs your Domovoi server: Setup stops there.' + #13#10#13#10 +
    'You need the setup code shown on the dashboard: Settings > Satellites > add a spare device.';
  CodePage := CreateInputQueryPage(wpWelcome, 'Your setup code', 'From the dashboard: Settings > Satellites > add a spare device > windows laptop or pc.',
    'Type both as the dashboard shows them. A Setup downloaded from the dashboard''s link has them filled in already: just click Next.');
  CodePage.Add('Domovoi server (for example 192.168.1.20):', False);
  CodePage.Add('Setup code (for example K7M4-QX2P):', False);
  Prefill;
  CreateChoicePage(CodePage.ID);
  WorkPage := CreateOutputMarqueeProgressPage('Making this PC a room', 'A few minutes, mostly downloads from your Domovoi server. You can keep using the PC.');
  CreateFinishControls;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = CodePage.ID then
  begin
    Resolved := False;
    Result := ResolveCode;
  end
  else if CurPageID = wpReady then
  begin
    { On a silent install the pages are not visited one by one. }
    if not Resolved then
      Result := ResolveCode;
    if Result then
      Result := RunInstall;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = ChoicePage.ID then
  begin
    WizardForm.PageDescriptionLabel.Caption := 'This PC becomes the room "' + Room + '".';
    RoomText.Caption := 'Domovoi server: ' + Core + #13#10 + 'Its fingerprint: ' + Fingerprint;
    RoomText.AdjustHeight;
  end
  else if CurPageID = wpFinished then
    ShowFinish;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Power, Updates: String;
begin
  if PermissionsAnswer = 'automatic' then
    Updates := 'automatic'
  else
    Updates := 'ask me first';
  Power := PowerAnswer;
  if Power = 'no' then
    Power := 'unchanged'
  else if Power = 'standby' then
    Power := 'never sleep while plugged in'
  else if Power = 'lid' then
    Power := 'keep running with the lid closed while plugged in'
  else
    Power := 'never sleep, and keep running with the lid closed, while plugged in';
  Result := 'Room:' + NewLine + Space + Room + NewLine + NewLine +
    'Domovoi server:' + NewLine + Space + Core + NewLine + NewLine +
    'Updates and restarts:' + NewLine + Space + Updates + NewLine + NewLine +
    'Power:' + NewLine + Space + Power + NewLine + NewLine +
    'For:' + NewLine + Space + GetUserNameString + ' (this account only), into ' + ExpandConstant('{localappdata}') + '\Domovoi\Satellite' + NewLine + NewLine +
    'Setup downloads the satellite from your Domovoi server, checks it, then installs it (about 350 MB on disk).';
end;
