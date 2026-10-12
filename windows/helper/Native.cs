// Win32 calls the helper needs and .NET Framework doesn't wrap: local
// accounts (netapi32), account rights (LSA), service registration with a
// password (SCM), profiles (userenv) and listening sockets (iphlpapi).
// Passwords cross these calls only as unmanaged buffers the caller zeroes.

using System;
using System.Runtime.InteropServices;

namespace Domovoi.Windows.Helper
{
    static class Native
    {
        // -- netapi32 ----------------------------------------------------
        public const int NERR_Success = 0;
        public const int NERR_UserNotFound = 2221;
        public const int NERR_UserExists = 2224;
        public const int ERROR_MEMBER_IN_ALIAS = 1378;

        public const uint UF_SCRIPT = 0x0001;
        public const uint UF_ACCOUNTDISABLE = 0x0002;
        public const uint UF_LOCKOUT = 0x0010;
        public const uint UF_PASSWD_CANT_CHANGE = 0x0040;
        public const uint UF_NORMAL_ACCOUNT = 0x0200;
        public const uint UF_DONT_EXPIRE_PASSWD = 0x10000;
        public const uint USER_PRIV_USER = 1;

        [StructLayout(LayoutKind.Sequential)]
        public struct USER_INFO_1
        {
            public IntPtr name;
            public IntPtr password;
            public uint password_age;
            public uint priv;
            public IntPtr home_dir;
            public IntPtr comment;
            public uint flags;
            public IntPtr script_path;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct USER_INFO_1003 { public IntPtr password; }

        [StructLayout(LayoutKind.Sequential)]
        public struct USER_INFO_1008 { public uint flags; }

        [StructLayout(LayoutKind.Sequential)]
        public struct LOCALGROUP_MEMBERS_INFO_0 { public IntPtr sid; }

        [DllImport("netapi32.dll", CharSet = CharSet.Unicode)]
        public static extern int NetUserAdd(string server, int level, ref USER_INFO_1 buf, out int parmErr);

        [DllImport("netapi32.dll", CharSet = CharSet.Unicode)]
        public static extern int NetUserSetInfo(string server, string user, int level, ref USER_INFO_1003 buf, out int parmErr);

        [DllImport("netapi32.dll", CharSet = CharSet.Unicode, EntryPoint = "NetUserSetInfo")]
        public static extern int NetUserSetFlags(string server, string user, int level, ref USER_INFO_1008 buf, out int parmErr);

        [DllImport("netapi32.dll", CharSet = CharSet.Unicode)]
        public static extern int NetUserGetInfo(string server, string user, int level, out IntPtr buf);

        [DllImport("netapi32.dll", CharSet = CharSet.Unicode)]
        public static extern int NetUserDel(string server, string user);

        [DllImport("netapi32.dll", CharSet = CharSet.Unicode)]
        public static extern int NetLocalGroupAddMembers(string server, string group, int level, ref LOCALGROUP_MEMBERS_INFO_0 buf, int count);

        [DllImport("netapi32.dll")]
        public static extern int NetApiBufferFree(IntPtr buf);

        // -- LSA account rights -----------------------------------------
        public const int POLICY_ALL_ACCESS = 0x00F0FFF;
        public const uint STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034;

        [StructLayout(LayoutKind.Sequential)]
        public struct LSA_UNICODE_STRING
        {
            public ushort Length;
            public ushort MaximumLength;
            public IntPtr Buffer;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct LSA_OBJECT_ATTRIBUTES
        {
            public int Length;
            public IntPtr RootDirectory;
            public IntPtr ObjectName;
            public int Attributes;
            public IntPtr SecurityDescriptor;
            public IntPtr SecurityQualityOfService;
        }

        [DllImport("advapi32.dll")]
        public static extern uint LsaOpenPolicy(IntPtr systemName, ref LSA_OBJECT_ATTRIBUTES attrs, int access, out IntPtr handle);

        [DllImport("advapi32.dll")]
        public static extern uint LsaAddAccountRights(IntPtr policy, byte[] sid, LSA_UNICODE_STRING[] rights, int count);

        [DllImport("advapi32.dll")]
        public static extern uint LsaRemoveAccountRights(IntPtr policy, byte[] sid, [MarshalAs(UnmanagedType.U1)] bool allRights, LSA_UNICODE_STRING[] rights, int count);

        [DllImport("advapi32.dll")]
        public static extern uint LsaEnumerateAccountRights(IntPtr policy, byte[] sid, out IntPtr rights, out int count);

        [DllImport("advapi32.dll")]
        public static extern uint LsaClose(IntPtr handle);

        [DllImport("advapi32.dll")]
        public static extern uint LsaFreeMemory(IntPtr buf);

        [DllImport("advapi32.dll")]
        public static extern int LsaNtStatusToWinError(uint status);

        // -- the service control manager ---------------------------------
        public const int SC_MANAGER_ALL_ACCESS = 0xF003F;
        public const int SERVICE_ALL_ACCESS = 0xF01FF;
        public const int SERVICE_WIN32_OWN_PROCESS = 0x10;
        public const int SERVICE_AUTO_START = 2;
        public const int SERVICE_ERROR_NORMAL = 1;
        public const int SERVICE_NO_CHANGE = -1;
        public const int SERVICE_CONFIG_DESCRIPTION = 1;
        public const int SERVICE_CONFIG_FAILURE_ACTIONS = 2;
        public const int SC_ACTION_RESTART = 1;
        public const int ERROR_SERVICE_DOES_NOT_EXIST = 1060;
        public const int ERROR_SERVICE_EXISTS = 1073;
        public const int ERROR_SERVICE_MARKED_FOR_DELETE = 1072;

        [StructLayout(LayoutKind.Sequential)]
        public struct SERVICE_DESCRIPTION { public IntPtr description; }

        [StructLayout(LayoutKind.Sequential)]
        public struct SERVICE_FAILURE_ACTIONS
        {
            public int resetPeriod;
            public IntPtr rebootMsg;
            public IntPtr command;
            public int actionCount;
            public IntPtr actions;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct SC_ACTION
        {
            public int type;
            public int delay;
        }

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern IntPtr OpenSCManagerW(string machine, string database, int access);

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern IntPtr CreateServiceW(IntPtr scm, string name, string display, int access, int type, int start,
            int errorControl, string binaryPath, string loadOrderGroup, IntPtr tagId, string dependencies, string account, IntPtr password);

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern IntPtr OpenServiceW(IntPtr scm, string name, int access);

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool ChangeServiceConfigW(IntPtr service, int type, int start, int errorControl, string binaryPath,
            string loadOrderGroup, IntPtr tagId, string dependencies, string account, IntPtr password, string display);

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "ChangeServiceConfig2W")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool ChangeServiceDescription(IntPtr service, int level, ref SERVICE_DESCRIPTION info);

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "ChangeServiceConfig2W")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool ChangeServiceFailureActions(IntPtr service, int level, ref SERVICE_FAILURE_ACTIONS info);

        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool DeleteService(IntPtr service);

        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool CloseServiceHandle(IntPtr handle);

        // -- profiles -----------------------------------------------------
        [DllImport("userenv.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool DeleteProfileW(string sid, string profilePath, string computer);

        // -- files Windows deletes at the next restart --------------------
        public const int MOVEFILE_DELAY_UNTIL_REBOOT = 4;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool MoveFileExW(string existing, string newName, int flags);

        // -- sockets, memory, CPU -----------------------------------------
        public const int AF_INET = 2;
        public const int AF_INET6 = 23;
        public const int TCP_TABLE_OWNER_PID_LISTENER = 3;

        [DllImport("iphlpapi.dll", SetLastError = true)]
        public static extern uint GetExtendedTcpTable(IntPtr table, ref int size, [MarshalAs(UnmanagedType.Bool)] bool order, int af, int tableClass, uint reserved);

        [DllImport("iphlpapi.dll")]
        public static extern int GetBestInterface(uint destAddr, out uint index);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool GetPhysicallyInstalledSystemMemory(out ulong kilobytes);

        public const int PF_AVX2_INSTRUCTIONS_AVAILABLE = 40;

        [DllImport("kernel32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool IsProcessorFeaturePresent(int feature);
    }
}
