// The hidden service account domovoi-svc (PLAN 4.5) and DomovoiHost's
// registration under it (PLAN 4.6).
//
// The password: 40 characters from a CSPRNG, generated inside this
// process, handed to NetUserAdd / NetUserSetInfo and CreateService /
// ChangeServiceConfig as unmanaged buffers, then zeroed. It is never
// printed, logged, written to a file or put on a command line, and no
// PowerShell cmdlet ever sees it (script-block logging would record it).
// Nothing stores it but the SAM and the SCM (an LSA secret); repair and
// uninstall set a fresh one ("rotate, don't store").

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using Microsoft.Win32;

namespace Domovoi.Windows.Helper
{
    sealed class Secret : IDisposable
    {
        const string Upper = "ABCDEFGHJKLMNPQRSTUVWXYZ";
        const string Lower = "abcdefghijkmnopqrstuvwxyz";
        const string Digits = "23456789";
        const string Symbols = "!#%+-.:=?@^_~";

        public IntPtr Buffer { get; private set; }
        readonly int length;

        Secret(char[] chars)
        {
            length = chars.Length;
            Buffer = Marshal.AllocHGlobal((length + 1) * 2);
            Marshal.Copy(chars, 0, Buffer, length);
            Marshal.WriteInt16(Buffer, length * 2, 0);
        }

        public static Secret NewPassword()
        {
            string all = Upper + Lower + Digits + Symbols;
            var chars = new char[40];
            using (var rng = RandomNumberGenerator.Create())
            {
                var b = new byte[1];
                while (true)
                {
                    for (int i = 0; i < chars.Length; i++)
                    {
                        // Rejection sampling: no modulo bias.
                        int limit = 256 - (256 % all.Length);
                        do rng.GetBytes(b); while (b[0] >= limit);
                        chars[i] = all[b[0] % all.Length];
                    }
                    if (Has(chars, Upper) && Has(chars, Lower) && Has(chars, Digits) && Has(chars, Symbols)) break;
                }
            }
            try { return new Secret(chars); }
            finally { Array.Clear(chars, 0, chars.Length); }
        }

        static bool Has(char[] chars, string set)
        {
            foreach (char c in chars) if (set.IndexOf(c) >= 0) return true;
            return false;
        }

        public void Dispose()
        {
            if (Buffer == IntPtr.Zero) return;
            Marshal.Copy(new byte[(length + 1) * 2], 0, Buffer, (length + 1) * 2);
            Marshal.FreeHGlobal(Buffer);
            Buffer = IntPtr.Zero;
        }
    }

    static class Account
    {
        // Setup's mark on the account, so an uninstall without install.json
        // still knows the account is Domovoi's and nobody else's.
        public const string Marker = "Domovoi service account (made by Domovoi Setup; its uninstaller removes it)";
        public const string UserListKey = @"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList";
        public const string SpecialAccountsKey = @"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts";
        public const string ProfileListKey = @"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList";

        public static readonly string[] BaseRights = { "SeServiceLogonRight" };
        // PLAN 4.5 hardening; [S1 variant] confirms WSL still starts with them.
        public static readonly string[] DenyRights = { "SeDenyInteractiveLogonRight", "SeDenyRemoteInteractiveLogonRight", "SeDenyNetworkLogonRight" };

        public sealed class Info
        {
            public bool Exists;
            public uint Flags;
            public string Comment;
            public bool HasMarker { get { return Comment != null && Comment.StartsWith("Domovoi service account", StringComparison.Ordinal); } }
        }

        public static Info Get()
        {
            var info = new Info();
            IntPtr buf;
            int rc = Native.NetUserGetInfo(null, Paths.AccountName, 1, out buf);
            if (rc == Native.NERR_UserNotFound) return info;
            if (rc != Native.NERR_Success) throw new Win32Exception(rc, "NetUserGetInfo " + Paths.AccountName);
            try
            {
                var u = (Native.USER_INFO_1)Marshal.PtrToStructure(buf, typeof(Native.USER_INFO_1));
                info.Exists = true;
                info.Flags = u.flags;
                info.Comment = u.comment == IntPtr.Zero ? null : Marshal.PtrToStringUni(u.comment);
            }
            finally { Native.NetApiBufferFree(buf); }
            return info;
        }

        public static SecurityIdentifier Sid()
        {
            var acct = new NTAccount(Environment.MachineName, Paths.AccountName);
            return (SecurityIdentifier)acct.Translate(typeof(SecurityIdentifier));
        }

        // Makes the account, or sets a fresh password and the right flags
        // on the one Setup made before. Refuses a domovoi-svc Setup didn't
        // make. The caller passes the same Secret to the service step.
        public static bool CreateOrRotate(Secret pw, bool ledgerSaysOurs, Action<string> say)
        {
            Info info = Get();
            const uint wanted = Native.UF_SCRIPT | Native.UF_NORMAL_ACCOUNT | Native.UF_DONT_EXPIRE_PASSWD | Native.UF_PASSWD_CANT_CHANGE;
            if (!info.Exists)
            {
                IntPtr name = Marshal.StringToHGlobalUni(Paths.AccountName);
                IntPtr comment = Marshal.StringToHGlobalUni(Marker);
                try
                {
                    var u = new Native.USER_INFO_1();
                    u.name = name;
                    u.password = pw.Buffer;
                    u.priv = Native.USER_PRIV_USER;
                    u.comment = comment;
                    u.flags = wanted;
                    int err;
                    int rc = Native.NetUserAdd(null, 1, ref u, out err);
                    if (rc != Native.NERR_Success) throw new Win32Exception(rc, "NetUserAdd " + Paths.AccountName + " (parameter " + Inv.S(err) + ")");
                }
                finally
                {
                    Marshal.FreeHGlobal(name);
                    Marshal.FreeHGlobal(comment);
                }
                say("created the local account " + Paths.AccountName + " (standard user, password never expires, never shown)");
                return true;
            }
            if (!info.HasMarker && !ledgerSaysOurs)
                throw new InvalidOperationException("a local account named " + Paths.AccountName + " already exists and Domovoi Setup did not make it; remove or rename it, then run Setup again");
            SetPassword(pw);
            var f = new Native.USER_INFO_1008();
            f.flags = (info.Flags | wanted) & ~(Native.UF_ACCOUNTDISABLE | Native.UF_LOCKOUT);
            int perr;
            int rc2 = Native.NetUserSetFlags(null, Paths.AccountName, 1008, ref f, out perr);
            if (rc2 != Native.NERR_Success) throw new Win32Exception(rc2, "NetUserSetInfo flags");
            say("set a fresh password (never shown) and the account flags on the existing " + Paths.AccountName);
            return false;
        }

        public static void SetPassword(Secret pw)
        {
            var p = new Native.USER_INFO_1003();
            p.password = pw.Buffer;
            int err;
            int rc = Native.NetUserSetInfo(null, Paths.AccountName, 1003, ref p, out err);
            if (rc != Native.NERR_Success) throw new Win32Exception(rc, "NetUserSetInfo password");
        }

        // Member of Users only (NetUserAdd may already have put it there).
        public static void JoinUsers(SecurityIdentifier sid)
        {
            var users = new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null);
            string group = ((NTAccount)users.Translate(typeof(NTAccount))).Value;
            int slash = group.IndexOf('\\');
            if (slash >= 0) group = group.Substring(slash + 1);
            var bytes = new byte[sid.BinaryLength];
            sid.GetBinaryForm(bytes, 0);
            IntPtr p = Marshal.AllocHGlobal(bytes.Length);
            try
            {
                Marshal.Copy(bytes, 0, p, bytes.Length);
                var m = new Native.LOCALGROUP_MEMBERS_INFO_0();
                m.sid = p;
                int rc = Native.NetLocalGroupAddMembers(null, group, 0, ref m, 1);
                if (rc != Native.NERR_Success && rc != Native.ERROR_MEMBER_IN_ALIAS) throw new Win32Exception(rc, "NetLocalGroupAddMembers " + group);
            }
            finally { Marshal.FreeHGlobal(p); }
        }

        // Which of the two keys Hide would have to make (recorded before it does).
        public static void MissingHideKeys(out bool specialAccounts, out bool userList)
        {
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            {
                using (var k = hklm.OpenSubKey(SpecialAccountsKey)) specialAccounts = k == null;
                using (var k = hklm.OpenSubKey(UserListKey)) userList = k == null;
            }
        }

        // Hidden from the sign-in screen: a DWORD 0 named after the account
        // under Winlogon\SpecialAccounts\UserList.
        public static void Hide()
        {
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.CreateSubKey(UserListKey))
                k.SetValue(Paths.AccountName, 0, RegistryValueKind.DWord);
        }

        public static bool HiddenValuePresent()
        {
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.OpenSubKey(UserListKey))
                return k != null && k.GetValue(Paths.AccountName) != null;
        }

        public static void Unhide(bool deleteUserList, bool deleteSpecialAccounts, Action<string> say)
        {
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            {
                using (var k = hklm.OpenSubKey(UserListKey, true))
                {
                    if (k != null && k.GetValue(Paths.AccountName) != null)
                    {
                        k.DeleteValue(Paths.AccountName, false);
                        say("removed the sign-in screen entry for " + Paths.AccountName);
                    }
                }
                if (deleteUserList) DeleteIfEmpty(hklm, UserListKey, say);
                if (deleteSpecialAccounts) DeleteIfEmpty(hklm, SpecialAccountsKey, say);
            }
        }

        static void DeleteIfEmpty(RegistryKey hklm, string path, Action<string> say)
        {
            using (var k = hklm.OpenSubKey(path))
            {
                if (k == null) return;
                if (k.ValueCount > 0 || k.SubKeyCount > 0) { say("kept " + path + " (it holds other entries)"); return; }
            }
            hklm.DeleteSubKey(path, false);
            say("removed the empty key HKLM\\" + path + " Setup had made");
        }

        // -- account rights (LSA; Home has no secpol.msc) ---------------

        static IntPtr OpenPolicy()
        {
            var attrs = new Native.LSA_OBJECT_ATTRIBUTES();
            IntPtr h;
            uint st = Native.LsaOpenPolicy(IntPtr.Zero, ref attrs, Native.POLICY_ALL_ACCESS, out h);
            if (st != 0) throw new Win32Exception(Native.LsaNtStatusToWinError(st), "LsaOpenPolicy");
            return h;
        }

        static byte[] Bytes(SecurityIdentifier sid)
        {
            var b = new byte[sid.BinaryLength];
            sid.GetBinaryForm(b, 0);
            return b;
        }

        public static void AddRights(SecurityIdentifier sid, IEnumerable<string> rights)
        {
            IntPtr pol = OpenPolicy();
            try
            {
                foreach (string r in rights)
                {
                    var s = new Native.LSA_UNICODE_STRING();
                    s.Buffer = Marshal.StringToHGlobalUni(r);
                    s.Length = (ushort)(r.Length * 2);
                    s.MaximumLength = (ushort)(r.Length * 2 + 2);
                    try
                    {
                        uint st = Native.LsaAddAccountRights(pol, Bytes(sid), new[] { s }, 1);
                        if (st != 0) throw new Win32Exception(Native.LsaNtStatusToWinError(st), "LsaAddAccountRights " + r);
                    }
                    finally { Marshal.FreeHGlobal(s.Buffer); }
                }
            }
            finally { Native.LsaClose(pol); }
        }

        public static List<string> Rights(SecurityIdentifier sid)
        {
            var list = new List<string>();
            IntPtr pol = OpenPolicy();
            try
            {
                IntPtr arr;
                int n;
                uint st = Native.LsaEnumerateAccountRights(pol, Bytes(sid), out arr, out n);
                if (st == Native.STATUS_OBJECT_NAME_NOT_FOUND) return list;
                if (st != 0) throw new Win32Exception(Native.LsaNtStatusToWinError(st), "LsaEnumerateAccountRights");
                try
                {
                    int size = Marshal.SizeOf(typeof(Native.LSA_UNICODE_STRING));
                    for (int i = 0; i < n; i++)
                    {
                        var s = (Native.LSA_UNICODE_STRING)Marshal.PtrToStructure(new IntPtr(arr.ToInt64() + i * size), typeof(Native.LSA_UNICODE_STRING));
                        list.Add(Marshal.PtrToStringUni(s.Buffer, s.Length / 2));
                    }
                }
                finally { Native.LsaFreeMemory(arr); }
            }
            finally { Native.LsaClose(pol); }
            return list;
        }

        // Only these rights; the others stay.
        public static void RemoveRights(SecurityIdentifier sid, IEnumerable<string> rights)
        {
            IntPtr pol = OpenPolicy();
            try
            {
                foreach (string r in rights)
                {
                    var s = new Native.LSA_UNICODE_STRING();
                    s.Buffer = Marshal.StringToHGlobalUni(r);
                    s.Length = (ushort)(r.Length * 2);
                    s.MaximumLength = (ushort)(r.Length * 2 + 2);
                    try
                    {
                        uint st = Native.LsaRemoveAccountRights(pol, Bytes(sid), false, new[] { s }, 1);
                        if (st != 0 && st != Native.STATUS_OBJECT_NAME_NOT_FOUND)
                            throw new Win32Exception(Native.LsaNtStatusToWinError(st), "LsaRemoveAccountRights " + r);
                    }
                    finally { Marshal.FreeHGlobal(s.Buffer); }
                }
            }
            finally { Native.LsaClose(pol); }
        }

        // Every right the SID holds, and the SID's entry in the policy with
        // them (AllRights), so no orphan SID stays behind in the policy.
        public static void RemoveAllRights(SecurityIdentifier sid)
        {
            IntPtr pol = OpenPolicy();
            try
            {
                uint st = Native.LsaRemoveAccountRights(pol, Bytes(sid), true, null, 0);
                if (st != 0 && st != Native.STATUS_OBJECT_NAME_NOT_FOUND)
                    throw new Win32Exception(Native.LsaNtStatusToWinError(st), "LsaRemoveAccountRights");
            }
            finally { Native.LsaClose(pol); }
        }

        public static void Delete()
        {
            int rc = Native.NetUserDel(null, Paths.AccountName);
            if (rc != Native.NERR_Success && rc != Native.NERR_UserNotFound) throw new Win32Exception(rc, "NetUserDel");
        }

        public static bool ProfileExists(string sid)
        {
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.OpenSubKey(ProfileListKey + "\\" + sid))
                return k != null;
        }

        public static string ProfilePath(string sid)
        {
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.OpenSubKey(ProfileListKey + "\\" + sid))
                return k == null ? null : k.GetValue("ProfileImagePath") as string;
        }

        // DeleteProfile fails while the profile is loaded (the service or
        // WSL still using it); the caller retries.
        public static int DeleteProfile(string sid)
        {
            if (Native.DeleteProfileW(sid, null, null)) return 0;
            return Marshal.GetLastWin32Error();
        }
    }
}
