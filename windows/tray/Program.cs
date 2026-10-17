// DomovoiTray.exe: Domovoi's icon in the notification area, for whoever is
// signed in to the PC Domovoi runs on. It runs as that person, never as an
// administrator; the two things that need one (Restart Domovoi, Save logs
// for support) ask Windows, which asks the person. Options.cs lists the
// command line. Docs: docs/WINDOWS_HOST.md, "The tray icon".
//
// One tray per signed-in account: a second start hands its request (show
// the icon, open the dashboard, open the card helper) to the running one
// and exits.

using System;
using System.IO;
using System.Threading;
using System.Windows.Forms;

namespace Domovoi.Windows.Tray
{
    static class Program
    {
        public static bool Snapshotting;

        [STAThread]
        static int Main(string[] argv)
        {
            Options opt;
            try { opt = Options.Parse(argv); }
            catch (ArgumentException e)
            {
                MessageBox.Show(e.Message, "Domovoi", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 2;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            string exe = Application.ExecutablePath;
            var store = new RegistryTrayStore(opt.Profile);
            if (opt.Snapshot != null)
            {
                Snapshotting = true;
                return Snapshot.Run(opt, store, exe);
            }

            string name = opt.Dev ? "DomovoiTray-dev-" + opt.Profile : "DomovoiTray";
            bool created;
            using (var mutex = new Mutex(true, @"Local\" + name, out created))
            using (var showEv = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\" + name + "-show"))
            using (var openEv = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\" + name + "-open"))
            using (var cardEv = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\" + name + "-card"))
            {
                if (!created)
                {
                    if (opt.Autostart) return 0;
                    if (opt.Open) openEv.Set();
                    else if (opt.Card) cardEv.Set();
                    else showEv.Set();
                    return 0;
                }

                string handOver;
                LaunchAction act = Startup.OnLaunch(store, exe, opt.Autostart, File.Exists, out handOver);
                if (act == LaunchAction.Quit) return 0;
                if (act == LaunchAction.HandOver && !opt.Dev)
                {
                    mutex.ReleaseMutex();
                    try
                    {
                        System.Diagnostics.Process.Start(handOver, string.Join(" ", argv));
                        return 0;
                    }
                    catch (System.ComponentModel.Win32Exception) { }
                }

                Log log = new Log(LogPath(opt), 1024L * 1024);
                using (var app = new TrayApp(opt, store, log, exe))
                {
                    var stop = new ManualResetEvent(false);
                    var waiter = new Thread(delegate ()
                    {
                        WaitHandle[] hs = { showEv, openEv, cardEv, stop };
                        while (true)
                        {
                            int i = WaitHandle.WaitAny(hs);
                            if (i == 3) return;
                            if (i == 0) app.Invoke(app.ShowIcon);
                            else if (i == 1) app.Invoke(delegate { app.ShowIcon(); app.OpenDashboard(); });
                            else app.Invoke(delegate { app.ShowIcon(); app.OpenCardHelper(); });
                        }
                    });
                    waiter.IsBackground = true;
                    waiter.Start();
                    if (opt.Open) app.OpenDashboard();
                    if (opt.Card) app.OpenCardHelper();
                    Application.Run(app);
                    stop.Set();
                }
            }
            return 0;
        }

        static string LogPath(Options opt)
        {
            string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Domovoi");
            Directory.CreateDirectory(dir);
            return Path.Combine(dir, opt.Dev ? "tray-dev-" + opt.Profile + ".log" : "tray.log");
        }
    }
}
