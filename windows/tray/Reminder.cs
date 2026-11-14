// A request for the Windows step from another device (a phone, a laptop)
// stays in sight until it is handled (2026-11-14: the owner pressed "Update
// Domovoi" on his phone; the tray's only sign was one 30-second
// notification, which Windows hid while nobody was at the PC, so nothing
// happened and it looked like a failure).
//
// While such a request waits (unclaimed, still needed), the tray:
// * puts its icon in an attention state: a blue "!" badge instead of the
//   status dot, and the tooltip "Domovoi update waiting: click to finish";
//   a click on the icon itself starts the step (not only the notification);
// * shows the notification again, backing off: the first at once, then
//   after 5, 15, 30 and 60 minutes, then every 60 minutes; never while
//   nobody is at the PC (no keyboard or mouse input for 5 minutes, which a
//   locked screen also is), and at once when someone comes back (input after
//   that idle, or the session unlocked), at most once every 2 minutes;
// * stops when the request is claimed (by this tray or another account's),
//   withdrawn from the dashboard, answered, or no longer needed.
//
// Pure: TrayApp feeds it the state, the clock and the idle time, and does
// what it says; the tests run it whole.

using System;

namespace Domovoi.Windows.Tray
{
    public static class ReminderWords
    {
        // The tooltip (a NotifyIcon's text is at most 63 characters).
        public const string Tip = "Domovoi update waiting: click to finish";
        public const string Title = "Finish updating Domovoi";

        public static string Text(UpdateState s)
        {
            return (s != null && s.RequestAfterLinux ? "The Linux part is updated. " : "")
                + "Click here (or the Domovoi icon) to update the Windows part"
                + (s != null && s.TargetVersion != null ? " to Domovoi " + s.TargetVersion : "")
                + "; Windows asks for an administrator's permission.";
        }
    }

    public sealed class RequestReminder
    {
        // Between one reminder and the next: the first gap after the first
        // notification, and so on; the last repeats.
        public static readonly TimeSpan[] Gaps =
        {
            TimeSpan.FromMinutes(5), TimeSpan.FromMinutes(15), TimeSpan.FromMinutes(30), TimeSpan.FromMinutes(60),
        };
        // No input for this long: nobody is at the PC (no timed reminder),
        // and input after it is someone coming back.
        public static readonly TimeSpan Away = TimeSpan.FromMinutes(5);
        // Input this recent counts as someone being here now.
        public static readonly TimeSpan Here = TimeSpan.FromSeconds(60);
        // Never two reminders closer than this, whatever the reason.
        public static readonly TimeSpan MinGap = TimeSpan.FromMinutes(2);

        public string RequestId { get; private set; }
        public int Shown { get; private set; }
        DateTime last;
        bool wasAway;
        bool cameBack;

        // The request a reminder is about, or null: one from another device
        // that waits for a click here. Not one from a browser on this PC (that
        // starts at once), one another tray claimed, one with no step left,
        // nor while a step runs, an earlier run's outcome is on its way, or
        // the Linux part is updating.
        public static string Waiting(UpdateState s, bool stepRunning, bool pendingRun)
        {
            if (s == null || s.RequestId == null || stepRunning || pendingRun) return null;
            if (!s.StepNeeded || s.LinuxBusy || s.RequestClaimed || s.RequestOnThisPc) return null;
            return s.RequestId;
        }

        // What waits now. "started" (a new request), "ended" (none any
        // more) or null (no change), for the log and the icon.
        public string Track(string requestId)
        {
            if (requestId == RequestId) return null;
            string was = RequestId;
            RequestId = requestId;
            Shown = 0;
            last = DateTime.MinValue;
            cameBack = false;
            if (requestId != null) return "started";
            return was != null ? "ended" : null;
        }

        // The time since the last keyboard or mouse input. Remembers someone
        // coming back after being away.
        public void Observe(TimeSpan idle)
        {
            if (idle >= Away) wasAway = true;
            else if (wasAway && idle < Here) { wasAway = false; cameBack = true; }
        }

        // The session was unlocked: someone is back.
        public void Unlocked() { wasAway = false; cameBack = true; }

        // Why a reminder is due now ("first", "again", "back"), or null.
        public string Due(DateTime now, TimeSpan idle)
        {
            if (RequestId == null) return null;
            bool here = idle < Away;
            if (Shown == 0) return here ? "first" : null;
            TimeSpan since = now - last;
            if (cameBack && here && since >= MinGap) return "back";
            if (!here) return null;
            TimeSpan gap = Gaps[Math.Min(Shown - 1, Gaps.Length - 1)];
            return since >= gap ? "again" : null;
        }

        public void Mark(DateTime now)
        {
            Shown++;
            last = now;
            cameBack = false;
        }
    }
}
