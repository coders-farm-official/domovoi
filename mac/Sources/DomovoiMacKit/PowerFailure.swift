import Foundation

/// What "Start after a power failure" changed, written before it changes
/// anything, so turning it off and uninstalling both put it back.
public struct AutoRestartRecord: Codable, Equatable {
    public var schema = 1
    /// pmset's autorestart before Domovoi set it (0 off, 1 on).
    public var before: Int
    public var setAt: String

    enum CodingKeys: String, CodingKey {
        case schema
        case before = "autorestart_before"
        case setAt = "set_at"
    }

    public init(before: Int, setAt: String) {
        self.before = before
        self.setAt = setAt
    }
}

/// "Start after a power failure" (owner decision 2026-11-03): `pmset -a
/// autorestart 1`, so a desktop Mac (a Mac mini, a Mac Studio) starts up by
/// itself when the power comes back, and Domovoi with it. Desktop Macs
/// only: a laptop rides out a power cut on its battery, and is never
/// offered it. Behind FeatureGates.startAfterPowerFailure, OFF in this
/// build: unverified on Apple Silicon, so no control shows it, and
/// turning it on is refused. Turning it off is always allowed, so an
/// uninstall puts the setting back whatever the gate says.
///
/// As KeepAwake: the old value is recorded first (`state/autorestart.json`),
/// and only once; "off" puts it back only while the setting is still
/// Domovoi's 1, and if someone has changed it since, theirs stays.
public final class StartAfterPowerFailure {
    public static let key = "autorestart"
    let layout: Layout
    let runner: CommandRunner
    let fileOps: FileOps
    let now: () -> Date
    let enabled: Bool
    let isLaptop: () -> Bool

    public init(layout: Layout, runner: CommandRunner, fileOps: FileOps, now: @escaping () -> Date = Date.init,
                enabled: Bool = FeatureGates.startAfterPowerFailure, isLaptop: (() -> Bool)? = nil) {
        self.layout = layout
        self.runner = runner
        self.fileOps = fileOps
        self.now = now
        self.enabled = enabled
        self.isLaptop = isLaptop ?? { Preflight.thisMacIsLaptop(runner: runner) }
    }

    /// The setting in `pmset -g custom` (nil: this Mac doesn't list it).
    public static func value(_ p: PowerProfile) -> Int? { p.ac[key].flatMap { Int($0) } }

    func current() throws -> Int? {
        let r = try runner.run(Command(KeepAwake.pmset, ["-g", "custom"], timeout: 20))
        return r.ok ? Self.value(PowerProfile.parse(r.stdout)) : nil
    }

    public static func record(at url: URL) -> AutoRestartRecord? {
        guard let d = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(AutoRestartRecord.self, from: d)
    }

    public var record: AutoRestartRecord? { Self.record(at: layout.autoRestartRecord) }

    func set(_ v: Int) throws {
        try runner.check(Command(KeepAwake.pmset, ["-a", Self.key, String(v)], timeout: 30), "pmset -a \(Self.key) \(v)")
    }

    @discardableResult
    public func turnOn() throws -> String {
        guard enabled else { throw HelperError("Start after a power failure isn't available in this version of Domovoi") }
        guard FileManager.default.fileExists(atPath: layout.state.path) else { throw HelperError("Domovoi isn't installed on this Mac") }
        guard !isLaptop() else { throw HelperError("this Mac is a laptop: Start after a power failure is for desktop Macs only") }
        guard let before = try current() else { throw HelperError("this Mac has no setting to start after a power failure (pmset -g custom lists no autorestart)") }
        if record == nil {
            let rec = AutoRestartRecord(before: before, setAt: HostFiles.iso(now()))
            let enc = JSONEncoder()
            enc.outputFormatting = [.sortedKeys]
            try fileOps.writeAtomically(try enc.encode(rec) + Data([0x0a]), to: layout.autoRestartRecord, mode: 0o644)
        }
        if before != 1 { try set(1) }
        guard try current() == 1 else { throw HelperError("this Mac's power-failure setting didn't change (pmset -a autorestart 1)") }
        return before == 1 ? "this Mac already started by itself after a power failure" : "this Mac now starts by itself when the power comes back"
    }

    @discardableResult
    public func turnOff() throws -> String {
        guard let rec = record else { return "Domovoi hadn't changed this Mac's power-failure setting" }
        // Unreadable: keep the record, so a later run can still put it back.
        guard let cur = try current() else { throw HelperError("can't read this Mac's power-failure setting (pmset -g custom)") }
        let said: String
        if cur == 1 && rec.before != 1 {
            try set(rec.before)
            said = "this Mac stays off after a power failure again, as before"
        } else if cur == 1 {
            said = "this Mac already started by itself after a power failure before Domovoi, so it is left as it was"
        } else {
            said = "this Mac's power-failure setting was changed since, so it is left as it is"
        }
        try FileManager.default.removeItem(at: layout.autoRestartRecord)
        return said
    }

    /// The install record's answer, so a repair keeps what the menu chose.
    public func saveAnswer(_ on: Bool) throws {
        guard let rec = try RecordStore.load(layout.installRecord) else { return }
        let store = RecordStore(url: layout.installRecord, record: rec, fileOps: fileOps)
        try store.update { $0.answers.startAfterPowerFailure = on }
    }
}

/// The menu's "Start after a power failure" item. nil (no item at all)
/// while the gate is off, on a laptop, or when the setting can't be read.
public struct PowerFailureItem: Equatable {
    public var checked: Bool
    public var enabled: Bool
    public var line: String

    public static func decide(gate: Bool = FeatureGates.startAfterPowerFailure, laptop: Bool, value: Int?, ours: Bool) -> PowerFailureItem? {
        guard gate, !laptop, let v = value else { return nil }
        if v == 1 {
            if !ours { return PowerFailureItem(checked: true, enabled: false, line: "This Mac's own settings start it after a power failure.") }
            return PowerFailureItem(checked: true, enabled: true, line: "This Mac starts up by itself when the power comes back.")
        }
        return PowerFailureItem(checked: false, enabled: true, line: "After a power cut this Mac stays off until someone presses its power button.")
    }
}

/// What the setup pages offer, from the gate and the check's report.
public enum SetupOffers {
    /// "Start this Mac after a power failure": the gate on, and Preflight
    /// sure this is a desktop Mac (no report: not offered).
    public static func powerFailure(gate: Bool, report: PreflightReport?) -> Bool {
        gate && report?.laptop == false
    }
}
