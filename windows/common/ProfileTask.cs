// When Windows won't let go of domovoi-svc's profile (C:\Users\domovoi-svc)
// during the uninstall, its deletion is scheduled for the next restart
// instead of left to the household: a one-time startup task, run as SYSTEM
// before anyone signs in, that deletes the profile the way Settings > User
// Profiles does (Win32_UserProfile, which calls DeleteProfile) and then
// deletes itself. The uninstall schedules it only once the account is
// deleted (UninstallPlan puts DeleteProfile after DeleteAccount).
//
// It deletes only that profile, and only when its account is gone: the
// SID must not map to an account any more (IdentityNotMappedException; a
// reinstall makes a new account with a new SID), the profile must not be
// loaded, and its folder must be domovoi-svc's (C:\Users\domovoi-svc or
// domovoi-svc.<name>). When it can't tell (the lookup fails some other
// way) or the profile is loaded, it does nothing and stays for the next
// boot; otherwise it unregisters itself, so it runs once.
//
// Both scripts are fixed text around a SID checked against the shape of a
// local account's SID and the System32 path the helper passes (never
// $env:SystemRoot, which user code can set, while the task runs as
// SYSTEM); the task runs Windows' own powershell.exe with the cleanup
// script as -EncodedCommand, so there is no script file anyone could
// change. [unverified on a real run: the task's registration as SYSTEM by
// SID ("S-1-5-18"); the tests check the text, run the cleanup script
// against stand-in cmdlets, and parse both.]

using System;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public static class ProfileTask
    {
        public const string TaskName = "DomovoiRemoveProfile";

        // A local account's SID: S-1-5-21-<machine>-<machine>-<machine>-<rid>.
        static readonly Regex LocalSid = new Regex(@"^S-1-5-21-\d{1,10}-\d{1,10}-\d{1,10}-\d{1,10}\z");
        // C:\Windows\System32 and the like: a drive and plain folder names.
        static readonly Regex PlainDir = new Regex(@"^[A-Za-z]:\\[A-Za-z0-9 ._\\-]+\z");

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
            sb.Append("try { [void](New-Object System.Security.Principal.SecurityIdentifier $sid).Translate([System.Security.Principal.NTAccount]); $gone = $false }\n");
            sb.Append("catch [System.Security.Principal.IdentityNotMappedException] { $gone = $true }\n");
            sb.Append("# Any other failure: can't tell, so nothing now; this task runs again at the next boot.\n");
            sb.Append("catch { return }\n");
            sb.Append("if ($gone) {\n");
            sb.Append("  $p = Get-CimInstance -ClassName Win32_UserProfile -Filter \"SID='$sid'\"\n");
            sb.Append("  # Loaded: in use after all; again at the next boot.\n");
            sb.Append("  if ($p -and $p.Loaded) { return }\n");
            sb.Append("  if ($p -and ($p.LocalPath -match '\\\\domovoi-svc(\\.[^\\\\]+)?$')) { Remove-CimInstance -InputObject $p }\n");
            sb.Append("}\n");
            sb.Append("Unregister-ScheduledTask -TaskName '").Append(TaskName).Append("' -TaskPath '\\' -Confirm:$false -ErrorAction SilentlyContinue\n");
            return sb.ToString();
        }

        // What the elevated helper runs to register the task. systemDirectory:
        // Environment.SystemDirectory, so the task's program is Windows' own
        // powershell.exe whatever the environment says. Prints "scheduled".
        public static string RegisterScript(string sid, string systemDirectory)
        {
            if (systemDirectory == null || !PlainDir.IsMatch(systemDirectory)) throw new ArgumentException("not a plain system folder: " + systemDirectory);
            string exe = Path.Combine(systemDirectory, @"WindowsPowerShell\v1.0\powershell.exe");
            string encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(CleanupScript(sid)));
            var sb = new StringBuilder();
            sb.Append("$ErrorActionPreference = 'Stop'\n");
            sb.Append("$action = New-ScheduledTaskAction -Execute '").Append(exe).Append("' -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ").Append(encoded).Append("'\n");
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
