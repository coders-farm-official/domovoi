// The TCP listeners on this PC and the program behind each one, for the
// port checks (Ports.cs). Read-only, and it needs no administrator (the
// same table `netstat -ano` shows): the helper reads it for Setup's first
// check and again during the install, and DomovoiHost (a standard account)
// right before provision.sh starts Domovoi.
//
// A Linux program inside a mirrored-mode distro does not show up here: on
// 2026-10-12 Domovoi's own database, core, dashboard and Ollama ran in the
// distro while Windows' own tools reported 6369, 6370, 6432 and 11434 free
// (windows-installer\phaseB-before-uninstall.txt). So during the install
// this sees only Windows programs, which is what a port clash is.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace Domovoi.Windows
{
    public static class PortScan
    {
        const int AF_INET = 2;
        const int AF_INET6 = 23;
        const int TCP_TABLE_OWNER_PID_LISTENER = 3;
        const uint ERROR_INSUFFICIENT_BUFFER = 122;

        [DllImport("iphlpapi.dll", SetLastError = true)]
        static extern uint GetExtendedTcpTable(IntPtr table, ref int size, [MarshalAs(UnmanagedType.Bool)] bool order, int af, int tableClass, uint reserved);

        // Every listening TCP socket (IPv4 and IPv6) with its process name,
        // null when the process is gone or can't be read.
        public static List<Listener> Listeners()
        {
            var list = new List<Listener>();
            ReadTable(AF_INET, list);
            ReadTable(AF_INET6, list);
            var names = new Dictionary<int, string>();
            foreach (var l in list)
            {
                string n;
                if (!names.TryGetValue(l.Pid, out n))
                {
                    try
                    {
                        using (var p = Process.GetProcessById(l.Pid)) n = p.ProcessName;
                    }
                    catch (ArgumentException) { n = null; }
                    catch (InvalidOperationException) { n = null; }
                    names[l.Pid] = n;
                }
                l.Process = n;
            }
            return list;
        }

        static void ReadTable(int af, List<Listener> list)
        {
            int size = 0;
            GetExtendedTcpTable(IntPtr.Zero, ref size, false, af, TCP_TABLE_OWNER_PID_LISTENER, 0);
            for (int tries = 0; tries < 5; tries++)
            {
                IntPtr buf = Marshal.AllocHGlobal(size);
                try
                {
                    uint rc = GetExtendedTcpTable(buf, ref size, false, af, TCP_TABLE_OWNER_PID_LISTENER, 0);
                    if (rc == ERROR_INSUFFICIENT_BUFFER) continue; // the table grew
                    if (rc != 0) throw new System.ComponentModel.Win32Exception((int)rc, "GetExtendedTcpTable");
                    int n = Marshal.ReadInt32(buf);
                    // MIB_TCPROW_OWNER_PID: 6 DWORDs (state, local addr, local
                    // port, remote addr, remote port, pid). MIB_TCP6ROW_OWNER_PID:
                    // 16-byte addr, scope, port, 16-byte addr, scope, port,
                    // state, pid = 56 bytes. Ports in network byte order.
                    int rowSize = af == AF_INET ? 24 : 56;
                    int portOff = af == AF_INET ? 8 : 20;
                    int pidOff = af == AF_INET ? 20 : 52;
                    for (int i = 0; i < n; i++)
                    {
                        IntPtr row = new IntPtr(buf.ToInt64() + 4 + i * rowSize);
                        int rawPort = Marshal.ReadInt32(row, portOff);
                        int port = ((rawPort & 0xFF) << 8) | ((rawPort >> 8) & 0xFF);
                        int pid = Marshal.ReadInt32(row, pidOff);
                        list.Add(new Listener(port, pid, null));
                    }
                    return;
                }
                finally { Marshal.FreeHGlobal(buf); }
            }
            throw new System.ComponentModel.Win32Exception((int)ERROR_INSUFFICIENT_BUFFER, "GetExtendedTcpTable kept growing");
        }
    }
}
