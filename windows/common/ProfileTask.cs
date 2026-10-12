// When Windows won't let go of domovoi-svc's profile (C:\Users\domovoi-svc)
// during the uninstall, its deletion is scheduled for the next restart
// instead of left to the household: a one-time startup task, run as SYSTEM
// before anyone signs in, that deletes the profile the way Settings > User
// Profiles does (Win32_UserProfile, which calls DeleteProfile) and then
// deletes itself.
//
// It deletes only that profile, and only when its account is gone: the
// SID must no longer resolve to an account (a reinstall makes a new
// account with a new SID), the profile must not be loaded, and its folder
// must be domovoi-svc's (C:\Users\domovoi-svc or domovoi-svc.<name>). It
// unregisters itself whatever happens, so it runs once.
//
// Both scripts are fixed text around a SID checked against the shape of a
// local account's SID; the task runs Windows' own powershell.exe with the
// cleanup script as -EncodedCommand, so there is no script file anyone
// could change. [unverified on a real run: the task's registration as
// SYSTEM by SID ("S-1-5-18"); the tests check the text, run the cleanup
// script against stand-in cmdlets, and parse both.]

using System;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public static class ProfileTask
    {
        public const string TaskName = "DomovoiRemoveProfile";

        // A local account's SID: S-1-5-21-<machine>-<machine>-<machine>-<rid>.
        static readonly Regex LocalSid = new Regex(@"^S-1-5-21-\d{1,10}-\d{1,10}-\d{1,10}-\d{1,10}\z");

        public static bool IsLocalAccountSid(string sid) { return sid != null && LocalSid.IsMatch(sid); }

        static string CheckSid(string sid)
        {
            if (!IsLocalAccountSid(sid)) throw new ArgumentException("not a local account's SID: " + sid);
            return sid;
        }

        // What the task runs at startup, as SYSTEM.
        public static string CleanupScript(string sid)
        {
            CheckSid(sid);
            var sb = new StringBuilder();
            sb.Append("$ErrorActionPreference = 'Stop'\n");
            sb.Append("$sid = '").Append(sid).Append("'\n");
            sb.Append("try {\n");
            sb.Append("  $gone = $false\n");
            sb.Append("  try { [void](New-Object System.Security.Principal.SecurityIdentifier $sid).Translate([System.Security.Principal.NTAccount]) }\n");
            sb.Append("  catch { $gone = $true }\n");
            sb.Append("  if ($gone) {\n");
            sb.Append("    $p = Get-CimInstance -ClassName Win32_UserProfile -Filter \"SID='$sid'\"\n");
            sb.Append("    if ($p -and -not $p.Loaded -and ($p.LocalPath -match '\\\\domovoi-svc(\\.[^\\\\]+)?$')) { Remove-CimInstance -InputObject $p }\n");
            sb.Append("  }\n");
            sb.Append("} finally {\n");
            sb.Append("  Unregister-ScheduledTask -TaskName '").Append(TaskName).Append("' -TaskPath '\\' -Confirm:$false -ErrorAction SilentlyContinue\n");
            sb.Append("}\n");
            return sb.ToString();
        }

        // What the elevated helper runs to register the task. Prints
        // "scheduled" when it is registered.
        public static string RegisterScript(string sid)
        {
            string encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(CleanupScript(sid)));
            var sb = new StringBuilder();
            sb.Append("$ErrorActionPreference = 'Stop'\n");
            sb.Append("$exe = Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'\n");
            sb.Append("$action = New-ScheduledTaskAction -Execute $exe -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ").Append(encoded).Append("'\n");
            sb.Append("$trigger = New-ScheduledTaskTrigger -AtStartup\n");
            sb.Append("$principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest\n");
            sb.Append("$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)\n");
            sb.Append("Register-ScheduledTask -TaskName '").Append(TaskName).Append("' -TaskPath '\\' -Action $action -Trigger $trigger -Principal $principal -Settings $settings ");
            sb.Append("-Description 'Deletes the profile of the account Domovoi''s uninstaller removed (domovoi-svc), then itself. Made by Domovoi''s uninstaller.' -Force | Out-Null\n");
            sb.Append("'scheduled'\n");
            return sb.ToString();
        }
    }
}
