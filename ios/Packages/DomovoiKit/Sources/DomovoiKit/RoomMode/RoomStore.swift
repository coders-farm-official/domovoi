import Foundation

/// The small settings a room keeps beside its key (`shared_prefs/domovoi-room.xml`
/// on Android). One protocol so the store is the same code over
/// `UserDefaults(suiteName: "domovoi-room")` on a phone and a dictionary in
/// the tests and in a UI-test launch, where nothing may outlive the process.
public protocol RoomPreferences: AnyObject, Sendable {
    func bool(_ key: String) -> Bool
    func string(_ key: String) -> String?
    func float(_ key: String) -> Float?
    func setBool(_ value: Bool, _ key: String)
    func setString(_ value: String?, _ key: String)
    func setFloat(_ value: Float?, _ key: String)
    /// Every key this suite holds (Forget this room).
    func removeAll()
}

/// The phone's: its own defaults suite, so "forget this room" can drop the
/// whole suite without touching the household app's defaults.
public final class UserDefaultsRoomPreferences: RoomPreferences, @unchecked Sendable {
    // `UserDefaults` is documented thread-safe; it is not marked Sendable in
    // every SDK this package builds against, hence the unchecked conformance.
    private let defaults: UserDefaults
    private let suite: String

    public init?(suiteName: String = RoomStore.suiteName) {
        guard let defaults = UserDefaults(suiteName: suiteName) else { return nil }
        self.defaults = defaults
        self.suite = suiteName
    }

    public func bool(_ key: String) -> Bool { defaults.bool(forKey: key) }
    public func string(_ key: String) -> String? { defaults.string(forKey: key) }
    public func float(_ key: String) -> Float? {
        guard defaults.object(forKey: key) != nil else { return nil }
        return defaults.float(forKey: key)
    }
    public func setBool(_ value: Bool, _ key: String) { defaults.set(value, forKey: key) }
    public func setString(_ value: String?, _ key: String) {
        if let value { defaults.set(value, forKey: key) } else { defaults.removeObject(forKey: key) }
    }
    public func setFloat(_ value: Float?, _ key: String) {
        if let value { defaults.set(value, forKey: key) } else { defaults.removeObject(forKey: key) }
    }
    public func removeAll() { defaults.removePersistentDomain(forName: suite) }
}

/// A dictionary: the tests' and a UI-test launch's.
public final class InMemoryRoomPreferences: RoomPreferences {
    private enum Value { case bool(Bool), string(String), float(Float) }
    private let values = RoomLock<[String: Value]>([:])

    public init() {}

    public func bool(_ key: String) -> Bool {
        values.withLock { if case .bool(let b)? = $0[key] { return b } else { return false } }
    }
    public func string(_ key: String) -> String? {
        values.withLock { if case .string(let s)? = $0[key] { return s } else { return nil } }
    }
    public func float(_ key: String) -> Float? {
        values.withLock { if case .float(let f)? = $0[key] { return f } else { return nil } }
    }
    public func setBool(_ value: Bool, _ key: String) { values.withLock { $0[key] = .bool(value) } }
    public func setString(_ value: String?, _ key: String) { values.withLock { $0[key] = value.map { .string($0) } } }
    public func setFloat(_ value: Float?, _ key: String) { values.withLock { $0[key] = value.map { .float($0) } } }
    public func removeAll() { values.withLock { $0.removeAll() } }
}

/// Everything a room phone keeps, apart from the household app's own store
/// (`roommode/RoomStore.kt`):
///
/// - the enrolment (the core's address, the PINNED server fingerprint, the
///   room), the approval code it shows, the wake word the core pushed;
/// - the room's pairing key: 32 random bytes minted here, in the Keychain
///   under room mode's own service (``RoomKeychainKey``). Only its sha256
///   ever reaches the core. Never the household device token: a room phone
///   holds no household credential (owner decision 2026-10-12), and on a
///   phone that is also a household device that token stays in the
///   household's Keychain item, unused, while room mode is on (``active``);
/// - whether room mode is ON (``active``: the normal app is locked until the
///   person exits with the phone's unlock) and whether it was RUNNING (not
///   stopped by the person), which a return to Domovoi resumes;
/// - the serial of the last signed list accepted per channel.
///
/// Thread-safe: the engine reads it from its listening thread, the screens
/// from the main actor. Compound writes hold one lock.
public final class RoomStore: RoomKeeping, @unchecked Sendable {

    /// The defaults suite (`RoomStore.FILE` on Android).
    public static let suiteName = "domovoi-room"

    enum Keys {
        static let core = "core_url"
        static let fingerprint = "fingerprint"
        static let room = "room_id"
        static let label = "room_label"
        static let active = "active"
        static let running = "running"
        static let code = "approval_code"
        static let wake = "wake_word"
        static let threshold = "wake_threshold"
        static let muted = "muted"
        static let serials = "manifest_serials"
        static let approved = "approved_once"
        static let trained = "trained_sha:"
        /// iOS-only: the Guided Access tip was dismissed on the room screen.
        static let guidedTipDismissed = "guided_access_tip_dismissed"
    }

    private let prefs: any RoomPreferences
    private let key: any RoomKeyVault
    private let onActiveChanged: @Sendable (Bool) -> Void
    private let writing = RoomLock(())

    /// `onActiveChanged` runs FIRST in ``setActive(_:)``, before anything is
    /// written: the app sets ``HouseholdLock`` there, so the household token
    /// goes on no request from that moment (`RoomStore.kt:89-92`). A closure,
    /// because this folder never names `HouseholdLock` (§6.0: it uses `Mutex`).
    public init(
        prefs: any RoomPreferences,
        key: any RoomKeyVault,
        onActiveChanged: @escaping @Sendable (Bool) -> Void
    ) {
        self.prefs = prefs
        self.key = key
        self.onActiveChanged = onActiveChanged
    }

    /// The phone's store: the `domovoi-room` suite and the Keychain.
    public static func system(onActiveChanged: @escaping @Sendable (Bool) -> Void) -> RoomStore {
        RoomStore(
            prefs: UserDefaultsRoomPreferences() ?? InMemoryRoomPreferences(),
            key: RoomKeychainKey(),
            onActiveChanged: onActiveChanged
        )
    }

    /// Nothing outlives the process (tests, UI-test launches).
    public static func inMemory(
        key: String? = nil,
        onActiveChanged: @escaping @Sendable (Bool) -> Void = { _ in }
    ) -> RoomStore {
        RoomStore(prefs: InMemoryRoomPreferences(), key: InMemoryRoomKey(key), onActiveChanged: onActiveChanged)
    }

    /// Whether room mode is on, without building a store — the app's first
    /// read, before any household object exists (`RoomStore.isActive`,
    /// `MainActivity.kt:121`).
    public static func isActive(_ defaults: UserDefaults? = UserDefaults(suiteName: RoomStore.suiteName)) -> Bool {
        defaults?.bool(forKey: Keys.active) ?? false
    }

    // MARK: - what the room is

    public var enrolment: RoomEnrolment? {
        guard let core = prefs.string(Keys.core),
              let fp = prefs.string(Keys.fingerprint),
              let room = prefs.string(Keys.room)
        else { return nil }
        return RoomEnrolment(coreURL: core, fingerprint: fp, roomID: room, roomLabel: prefs.string(Keys.label) ?? room)
    }

    /// Room mode is on: the normal app is locked and the household token is
    /// never put on a request.
    public var active: Bool { prefs.bool(Keys.active) }

    /// The room was listening (not stopped by the person) — what a return
    /// to Domovoi resumes by itself.
    public var running: Bool { prefs.bool(Keys.running) }

    /// Become the room `e`. A NEW pairing key and approval code go with a new
    /// room or server; re-entering the same room keeps both (the core knows
    /// this phone by its key's hash). False when the Keychain would not take
    /// the key (`RoomStore.kt:72-87`).
    @discardableResult
    public func enrol(_ e: RoomEnrolment) -> Bool {
        writing.withLock { _ in
            let was = enrolment
            let same = was.map { $0.coreURL == e.coreURL && $0.roomID == e.roomID && $0.fingerprint == e.fingerprint } ?? false
            if !same || pairingToken() == nil {
                guard key.write(RoomWakeSettings.newPairingKey()) else { return false }
                prefs.setString(RoomWakeSettings.newApprovalCode(), Keys.code)
                prefs.setString(nil, Keys.serials)
                prefs.setString(nil, Keys.wake)
                prefs.setFloat(nil, Keys.threshold)
                prefs.setBool(false, Keys.approved)
            }
            prefs.setString(e.coreURL, Keys.core)
            prefs.setString(e.fingerprint, Keys.fingerprint)
            prefs.setString(e.roomID, Keys.room)
            prefs.setString(e.roomLabel, Keys.label)
            return true
        }
    }

    public func pairingToken() -> String? {
        guard let token = key.read(), !token.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return token
    }

    public func setActive(_ on: Bool) {
        // The household token goes on no request from this moment, before
        // anything else happens (`RoomStore.kt:89-92`).
        onActiveChanged(on)
        writing.withLock { _ in
            prefs.setBool(on, Keys.active)
            if !on { prefs.setBool(false, Keys.running) }
        }
    }

    public func setRunning(_ on: Bool) { prefs.setBool(on, Keys.running) }

    /// Forget the room entirely: key, code, enrolment (Exit, then Forget).
    public func forget() {
        writing.withLock { _ in
            key.write(nil)
            prefs.removeAll()
        }
    }

    // MARK: - RoomKeeping

    public var approvalCode: String? { prefs.string(Keys.code) }

    public func setApprovalCode(_ code: String) {
        guard RoomWakeSettings.isApprovalCode(code) else { return }
        prefs.setString(code, Keys.code)
    }

    public func renewApprovalCode() { prefs.setString(RoomWakeSettings.newApprovalCode(), Keys.code) }

    public var approvedOnce: Bool { prefs.bool(Keys.approved) }

    public func setApprovedOnce() {
        if !approvedOnce { prefs.setBool(true, Keys.approved) }
    }

    public var wakeWord: String { prefs.string(Keys.wake) ?? RoomWakeSettings.defaultWakeWord }

    public var wakeThreshold: Float { prefs.float(Keys.threshold) ?? RoomWakeSettings.defaultThreshold }

    public func setWakeWord(_ slug: String, threshold: Double?) {
        writing.withLock { _ in
            prefs.setString(slug, Keys.wake)
            if let t = RoomWakeSettings.thresholdValue(threshold) { prefs.setFloat(t, Keys.threshold) }
        }
    }

    public var muted: Bool { prefs.bool(Keys.muted) }

    public func setMuted(_ on: Bool) { prefs.setBool(on, Keys.muted) }

    public func trainedSHA(_ name: String) -> String? { prefs.string(Keys.trained + name) }

    public func setTrainedSHA(_ name: String, _ sha: String) { prefs.setString(sha, Keys.trained + name) }

    public func seen(_ record: String) -> RoomSeenList? {
        guard let raw = serials()[record] else { return nil }
        let parts = raw.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
        guard let first = parts.first, let serial = Int64(first) else { return nil }
        let digest = parts.count > 1 ? String(parts[1]) : ""
        return RoomSeenList(serial: serial, digest: digest.isEmpty ? nil : digest)
    }

    public func remember(_ record: String, _ accepted: RoomSignedList) {
        writing.withLock { _ in
            var next = serials()
            next[record] = "\(accepted.serial):\(accepted.digest)"
            if let data = try? JSONSerialization.data(withJSONObject: next, options: [.sortedKeys]),
               let text = String(data: data, encoding: .utf8) {
                prefs.setString(text, Keys.serials)
            }
        }
    }

    private func serials() -> [String: String] {
        guard let text = prefs.string(Keys.serials), let data = text.data(using: .utf8),
              let map = try? JSONSerialization.jsonObject(with: data) as? [String: String]
        else { return [:] }
        return map
    }

    // MARK: - iOS-only

    /// The room screen's Guided Access tip was dismissed (ROOMMODE-MAP §3.5).
    public var guidedAccessTipDismissed: Bool { prefs.bool(Keys.guidedTipDismissed) }

    public func dismissGuidedAccessTip() { prefs.setBool(true, Keys.guidedTipDismissed) }
}
