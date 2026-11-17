import Darwin
import Foundation

/// `pmset -g custom`: each power source's settings (mac-installer PLAN 4.5).
/// Any account can read them; changing them needs root.
public struct PowerProfile: Equatable {
    /// "AC Power" (a desktop Mac has only this one).
    public var ac: [String: String] = [:]
    /// "Battery Power", on a MacBook.
    public var battery: [String: String]?

    /// The text `pmset -g custom` prints: a "<Source> Power:" header, then
    /// one " key value" line per setting (a key may hold spaces, "Sleep On
    /// Power Button 1"). Lines before any header count as AC.
    public static func parse(_ text: String) -> PowerProfile {
        var p = PowerProfile()
        var current = "AC Power"
        for raw in text.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }
            if !raw.hasPrefix(" ") && !raw.hasPrefix("\t") && line.hasSuffix(":") {
                current = String(line.dropLast())
                if current == "Battery Power" && p.battery == nil { p.battery = [:] }
                continue
            }
            let parts = line.split(whereSeparator: { $0 == " " || $0 == "\t" })
            guard parts.count >= 2 else { continue }
            let key = parts.dropLast().joined(separator: " "), value = String(parts.last!)
            switch current {
            case "AC Power": p.ac[key] = value
            case "Battery Power": p.battery?[key] = value
            default: break
            }
        }
        return p
    }

    /// Minutes of idle before the Mac sleeps on the charger; 0 never.
    public var acSleepMinutes: Int? { ac["sleep"].flatMap { Int($0) } }

    /// A Mac with a battery section is a MacBook. (hw.model can't tell:
    /// since the M2 every Mac, laptops included, reports "MacNN,N".)
    public var laptop: Bool { battery != nil }
}

/// What Domovoi changed, written before it changes anything, so turning
/// "Keep this Mac awake" off and uninstalling both put it back.
public struct PowerRecord: Codable, Equatable {
    public var schema = 1
    public var acSleepBefore: Int
    public var setAt: String

    enum CodingKeys: String, CodingKey {
        case schema
        case acSleepBefore = "ac_sleep_before"
        case setAt = "set_at"
    }

    public init(acSleepBefore: Int, setAt: String) {
        self.acSleepBefore = acSleepBefore
        self.setAt = setAt
    }
}

/// "Keep this Mac awake" (PLAN 4.5): `pmset -c sleep 0`, so the Mac doesn't
/// go to sleep by itself while it is plugged in (the display may still
/// turn off). One setting, nothing else: not `disablesleep` (undocumented,
/// global, and a MacBook closed in a bag would overheat), not the battery
/// timers. A MacBook still sleeps when its lid closes; the consent page and
/// the menu say so.
///
/// The old value is recorded first (`state/power.json`), and only once: a
/// second "on" never records our own 0 as "before". "Off" puts the old value
/// back only while the timer is still Domovoi's 0; if someone has changed it
/// since, theirs stays. Uninstall does the same "off".
public final class KeepAwake {
    public static let pmset = "/usr/bin/pmset"
    let layout: Layout
    let runner: CommandRunner
    let fileOps: FileOps
    let now: () -> Date

    public init(layout: Layout, runner: CommandRunner, fileOps: FileOps, now: @escaping () -> Date = Date.init) {
        self.layout = layout
        self.runner = runner
        self.fileOps = fileOps
        self.now = now
    }

    public func profile() throws -> PowerProfile? {
        let r = try runner.run(Command(Self.pmset, ["-g", "custom"], timeout: 20))
        return r.ok ? PowerProfile.parse(r.stdout) : nil
    }

    public static func record(at url: URL) -> PowerRecord? {
        guard let d = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(PowerRecord.self, from: d)
    }

    public var record: PowerRecord? { Self.record(at: layout.powerRecord) }

    func setACSleep(_ minutes: Int) throws {
        try runner.check(Command(Self.pmset, ["-c", "sleep", String(minutes)], timeout: 30), "pmset -c sleep \(minutes)")
    }

    @discardableResult
    public func turnOn() throws -> String {
        guard FileManager.default.fileExists(atPath: layout.state.path) else { throw HelperError("Domovoi isn't installed on this Mac") }
        guard let before = try profile()?.acSleepMinutes else { throw HelperError("can't read this Mac's sleep setting (pmset -g custom)") }
        // Recorded once; again only when the record says the Mac already
        // never slept (nothing of ours to undo) and it sleeps now: the
        // household changed it since, so this "on" is Domovoi's change
        // (Train 1 finding 18).
        if record == nil || (record!.acSleepBefore == 0 && before != 0) {
            let rec = PowerRecord(acSleepBefore: before, setAt: HostFiles.iso(now()))
            let enc = JSONEncoder()
            enc.outputFormatting = [.sortedKeys]
            try fileOps.writeAtomically(try enc.encode(rec) + Data([0x0a]), to: layout.powerRecord, mode: 0o644)
        }
        if before != 0 { try setACSleep(0) }
        guard try profile()?.acSleepMinutes == 0 else { throw HelperError("this Mac's sleep setting didn't change (pmset -c sleep 0)") }
        return before == 0 ? "this Mac already didn't sleep while plugged in" : "this Mac no longer sleeps by itself while plugged in (it slept after \(before) min)"
    }

    @discardableResult
    public func turnOff() throws -> String {
        guard let rec = record else { return "Domovoi hadn't changed this Mac's sleep setting" }
        // Unreadable: keep the record, so a later run can still put it back.
        guard let current = try profile()?.acSleepMinutes else { throw HelperError("can't read this Mac's sleep setting (pmset -g custom)") }
        let said: String
        if current == 0 && rec.acSleepBefore != 0 {
            try setACSleep(rec.acSleepBefore)
            said = "this Mac's sleep setting is back to \(rec.acSleepBefore) min"
        } else if current == 0 {
            said = "this Mac didn't sleep while plugged in before Domovoi either: left as it was"
        } else {
            said = "this Mac's sleep setting was changed since (\(current) min): left as it is"
        }
        try FileManager.default.removeItem(at: layout.powerRecord)
        return said
    }

    /// The install record's answer, so a repair keeps what the menu chose.
    public func saveAnswer(_ on: Bool) throws {
        guard let rec = try RecordStore.load(layout.installRecord) else { return }
        let store = RecordStore(url: layout.installRecord, record: rec, fileOps: fileOps)
        try store.update { $0.answers.keepAwake = on }
    }
}

/// The menu's "Keep this Mac awake" item, from what any account can read:
/// the plugged-in sleep timer, whether Domovoi set it, and whether this is
/// a MacBook. nil: the setting can't be read, so there is no item (no
/// control that can't say what it does).
public struct KeepAwakeItem: Equatable {
    public var checked: Bool
    public var enabled: Bool
    public var line: String

    /// Whether the plugged-in "never sleep" is Domovoi's doing: a record
    /// whose "before" was a real timer. A record of 0 (the Mac already never
    /// slept at install) is not: turning it off would change nothing, so
    /// the item is shown greyed from the start (Train 1 finding 18), not as
    /// a tick that asks for a password to do nothing.
    public static func ours(_ rec: PowerRecord?) -> Bool { (rec?.acSleepBefore ?? 0) != 0 }

    public static func decide(acSleepMinutes: Int?, ours: Bool, laptop: Bool) -> KeepAwakeItem? {
        guard let m = acSleepMinutes else { return nil }
        if m == 0 {
            if !ours { return KeepAwakeItem(checked: true, enabled: false, line: "This Mac's own settings keep it awake.") }
            return KeepAwakeItem(checked: true, enabled: true, line: laptop
                ? "A MacBook still sleeps when its lid is closed or on battery."
                : "This Mac doesn't go to sleep by itself while it's plugged in.")
        }
        return KeepAwakeItem(checked: false, enabled: true, line: "This Mac sleeps after \(minutes(m)) idle, and Domovoi stops while it sleeps.")
    }

    static func minutes(_ m: Int) -> String {
        if m >= 120 && m % 60 == 0 { return "\(m / 60) hours" }
        if m == 60 { return "1 hour" }
        return m == 1 ? "1 minute" : "\(m) minutes"
    }
}
