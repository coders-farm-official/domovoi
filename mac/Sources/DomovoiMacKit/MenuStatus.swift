import Foundation

// What the menu-bar icon says about Domovoi (mac-installer PLAN 4.9), the
// twin of the Windows tray's Status.cs: from sources that need no password.
// The install record (any account can read it), the dashboard's open
// /api/health and the core's open /v1/alive on this Mac's loopback, which
// of Domovoi's two background services are switched off (`launchctl
// print-disabled system`, readable by any account) and whether their
// programs run (the process list). Nothing new is opened on the server for
// it. `MenuStatus.decide` is pure: the tests feed it facts; MenuProbe
// gathers them.

public enum InstallFact: String, Equatable {
    /// No install record: Setup never ran (or Remove everything did).
    case notSetUp
    case installed
    /// Uninstalled with Keep my data.
    case dataKept
    /// The record is there but can't be read.
    case unreadable
}

public enum WebFact: String, Equatable { case ok, degraded, noAnswer }

/// One background service (a LaunchDaemon in /Library/LaunchDaemons).
public enum JobFact: String, Equatable {
    /// Its program runs.
    case running
    /// Its file is there and it isn't switched off, but its program doesn't run.
    case notRunning
    /// Switched off: in System Settings > General > Login Items & Extensions
    /// (Background Items), or by `launchctl disable`.
    case off
    /// No file in /Library/LaunchDaemons: a repair puts it back.
    case missing
    /// Couldn't be told.
    case unknown
}

public struct MenuFacts: Equatable {
    public var install: InstallFact = .notSetUp
    /// Domovoi runs its own Ollama (not the household's).
    public var ownOllama = true
    public var web: WebFact = .noAnswer
    public var dbReachable = false
    public var coreReachable = false
    /// The web's view of the core, else the core's own /v1/alive.
    public var coreAlive: Bool?
    public var vm: JobFact = .unknown
    public var ollama: JobFact = .unknown
    /// Domovoi's own Ollama answers /api/version (nil: not asked).
    public var ollamaAnswers: Bool?

    public init() {}
}

public enum MenuState: String, Equatable {
    /// Green.
    case running
    /// Amber: starting, restarting or updating.
    case starting
    /// Grey.
    case stopped
    /// Red.
    case problem
    /// Grey: nothing to watch (not set up, or uninstalled).
    case notInstalled
}

public struct MenuVerdict: Equatable {
    public var state: MenuState
    /// "Domovoi is running": the tooltip and the menu's first line.
    public var line: String
    /// A second line for the menu, or nil.
    public var detail: String?
    /// The notification's text when Domovoi goes down this way.
    public var notice: String?

    public init(state: MenuState, line: String, detail: String? = nil, notice: String? = nil) {
        self.state = state
        self.line = line
        self.detail = detail
        self.notice = notice
    }

    public var up: Bool { state == .running }
    public var settled: Bool { state != .starting }
}

/// Something to point out beside the status, with what the menu offers for it.
public struct Attention: Equatable {
    public enum Kind: String, Equatable { case vmSwitchedOff, ollamaSwitchedOff, aiNotAnswering }
    public var kind: Kind
    public var text: String
    /// Open System Settings > General > Login Items & Extensions.
    public var opensLoginItems: Bool
    /// Worth a notification when it first appears.
    public var notify: Bool
}

public enum MenuStatus {
    /// How long Domovoi may take to come up (after a restart, an update, or
    /// the Mac starting) before the icon says something is wrong: the
    /// Windows tray's grace.
    public static let defaultGrace: TimeInterval = 10 * 60
    /// The shorter grace when the dashboard answers and only the core or
    /// the database doesn't (an update restarts the core in well under it).
    public static let degradedGrace: TimeInterval = 5 * 60

    /// How System Settings lists the two services on an unsigned build
    /// (P3-6, macOS 26.4): under the programs they run, never "Domovoi".
    public static let vmListedAs = "“limactl”"
    /// Ollama shows only as its maker (Train 1 finding 19).
    public static let ollamaListedAs = "“Infra Technologies, Inc” (Ollama)"
    /// The speech service as Login Items lists it (an ad-hoc build: the
    /// program's own name; PHASE8A-STT.md).
    public static let sttListedAs = "“whisper-server”"
    public static let loginItemsPlace = "System Settings > General > Login Items & Extensions"

    /// `notReadySince`: when the icon first saw Domovoi not ready in this
    /// stretch (ReadyClock); `now`: the time of these facts.
    public static func decide(_ f: MenuFacts, notReadySince: Date, now: Date, grace: TimeInterval = defaultGrace) -> MenuVerdict {
        switch f.install {
        case .notSetUp:
            if f.web == .ok { return V(.running, "Domovoi is running") }
            return V(.notInstalled, "Domovoi isn't set up on this Mac", "Open Domovoi from Applications to set it up.")
        case .dataKept:
            return V(.notInstalled, "Domovoi is uninstalled", "Its data is kept: open Domovoi from Applications to install it again.")
        case .installed, .unreadable:
            break
        }
        if f.web == .ok { return V(.running, "Domovoi is running") }
        switch f.vm {
        case .off:
            return V(.stopped, "Domovoi is switched off", "\(vmListedAs) is off in Login Items & Extensions.",
                     notice: "Its background service \(vmListedAs) is switched off in \(loginItemsPlace). Switch it on there to start Domovoi again.")
        case .missing:
            return V(.problem, "Domovoi needs a repair", "Its background service is missing: open Domovoi from Applications and choose Repair.",
                     notice: "Its background service is missing. Open Domovoi from Applications and choose Repair.")
        case .notRunning:
            if now.timeIntervalSince(notReadySince) < grace { return V(.starting, "Domovoi is starting", "This can take a few minutes.") }
            return V(.stopped, "Domovoi is stopped", "Restart Domovoi below starts it again.",
                     notice: "Rooms and phones can't reach it until it starts again.")
        case .running, .unknown:
            break
        }
        let waited = now.timeIntervalSince(notReadySince)
        // The dashboard answers but the core or the database doesn't: the
        // virtual machine is up, so this is a short wait at most. Past
        // `degradedGrace` it is a problem, not "starting" (Train 1 finding
        // 26: a broken core read "starting or updating" for minutes).
        if f.web == .degraded && waited >= degradedGrace {
            let why = Self.why(f)
            return V(.problem, "Domovoi has a problem", why, notice: why)
        }
        if waited < grace {
            return V(.starting, "Domovoi is starting or updating",
                     "If it isn't back in a few minutes, choose Restart Domovoi below.")
        }
        let why = Self.why(f)
        return V(.problem, "Domovoi has a problem", why, notice: why)
    }

    static func why(_ f: MenuFacts) -> String {
        if f.web == .noAnswer { return f.coreAlive == true ? "The dashboard isn't answering." : "Nothing is answering." }
        if !f.dbReachable { return "Its database isn't answering." }
        if f.coreAlive == false { return "The voice service isn't running." }
        return "The voice service isn't answering properly."
    }

    static func V(_ s: MenuState, _ line: String, _ detail: String? = nil, notice: String? = nil) -> MenuVerdict {
        MenuVerdict(state: s, line: line, detail: detail, notice: notice)
    }

    /// What to point out beside the status (PLAN R8: a Background Item the
    /// owner switched off is said, not left as a silent outage).
    public static func attention(_ f: MenuFacts) -> [Attention] {
        guard f.install == .installed || f.install == .unreadable else { return [] }
        var out: [Attention] = []
        if f.vm == .off {
            // The status line says it; this adds the way to the switch.
            out.append(Attention(kind: .vmSwitchedOff, text: "Switch \(vmListedAs) on in Login Items & Extensions.", opensLoginItems: true, notify: false))
        }
        if f.ownOllama && f.ollama == .off {
            out.append(Attention(kind: .ollamaSwitchedOff,
                                 text: "AI answers are off: \(ollamaListedAs) is switched off in Login Items & Extensions.",
                                 opensLoginItems: true, notify: true))
        } else if f.ownOllama && f.web == .ok && f.ollamaAnswers == false {
            out.append(Attention(kind: .aiNotAnswering, text: "Domovoi's AI engine isn't answering. Restart Domovoi starts it again.",
                                 opensLoginItems: false, notify: false))
        }
        return out
    }
}

/// Since when Domovoi has been installed but not answering: the grace
/// counts from the first poll that saw it so (or the icon starting), never
/// from a time it was switched off or not set up.
public final class ReadyClock {
    var start: Date?

    public init() {}

    public func since(_ f: MenuFacts, now: Date) -> Date {
        let waiting = f.web != .ok && (f.install == .installed || f.install == .unreadable) && f.vm != .off && f.vm != .missing
        if !waiting { start = nil; return now }
        if start == nil { start = now }
        return start!
    }
}

/// When to tell the household (a notification): Domovoi went down, or came
/// back. Not on every poll, not while it is starting, not when the icon
/// itself starts (the Windows tray's rule).
public final class StatusNotifier {
    var lastUp: Bool?

    public init() {}

    /// The notification (title, body) for this verdict, or nil.
    public func next(_ v: MenuVerdict) -> (title: String, body: String)? {
        guard v.settled, v.state != .notInstalled else { return nil }
        let was = lastUp
        lastUp = v.up
        guard let w = was, w != v.up else { return nil }
        if v.up { return ("Domovoi is running again", "Rooms and phones can reach it.") }
        return (v.line, v.notice ?? "Rooms and phones may not reach it.")
    }
}

/// A notification for each attention item that appears (and is worth one),
/// once, until it goes away and comes back. Also at the icon's start: a
/// switched-off service is worth saying at sign-in.
public final class AttentionNotifier {
    var shown: Set<Attention.Kind> = []

    public init() {}

    public func next(_ items: [Attention]) -> [(title: String, body: String)] {
        let now = Set(items.map(\.kind))
        var out: [(title: String, body: String)] = []
        for a in items where a.notify && !shown.contains(a.kind) {
            switch a.kind {
            case .ollamaSwitchedOff:
                out.append(("Domovoi's AI answers are off",
                            "\(MenuStatus.ollamaListedAs) is switched off in \(MenuStatus.loginItemsPlace). Switch it on there to bring them back."))
            default:
                out.append(("Domovoi", a.text))
            }
        }
        shown = shown.intersection(now).union(items.filter(\.notify).map(\.kind))
        return out
    }
}

/// Whether the icon's notifications reach the person, in plain terms
/// (the app reads UNUserNotificationCenter's settings into it).
public enum NotificationPermission: Equatable {
    /// Allowed, shown as banners or alerts.
    case shown
    /// Never answered: the system's question is due.
    case notAsked
    /// Off in System Settings, or allowed without banners or alerts (only
    /// in Notification Center, or delivered quietly).
    case hidden
}

/// Where the icon says something the person is waiting for, such as
/// Restart's "Domovoi is back" (Train 1 S2-4: Domovoi's notifications were
/// Off, nobody had turned them off, and the result went nowhere). A
/// notification when they show; otherwise an alert, which offers to open
/// Notification settings the first time only.
public enum ResultRoute: Equatable {
    case notification
    case alert(offerSettings: Bool)

    public static func decide(_ p: NotificationPermission, offeredSettingsBefore: Bool) -> ResultRoute {
        switch p {
        case .shown: return .notification
        // The system's question is on its way or unanswered: nothing to open yet.
        case .notAsked: return .alert(offerSettings: false)
        case .hidden: return .alert(offerSettings: !offeredSettingsBefore)
        }
    }

    /// What the alert adds when it offers the settings.
    public static let settingsNote = "Domovoi's notifications are off on this Mac, so this shows here instead. To get it as a notification next time, "
        + "switch on Allow notifications for Domovoi in System Settings > Notifications."
}
