import Foundation

// ---------------------------------------------------------------------------
// What the alerts remember between runs — Android's `alerts`,
// `help_deliveries` and `container_incidents` DataStores
// (`AlertStore.kt:153-235`, `HelpDeliveries.kt:127`, `IncidentEngine.kt:92`),
// behind one protocol so the engines are testable without a disk.
//
//   alerted              "<serverKey>|<timerId>|<epochMs>" for every timer
//                        this phone has posted, so the live path and a
//                        scheduled notification post one timer once between
//                        them
//   fires_seen           {serverKey: the highest fire id processed}
//   fires_seen_at        {serverKey: when that fire went off}, so a history
//                        whose ids started again is noticed
//   mirror               which timers have a notification pending, and the
//                        words it carries
//   help_seen            {serverKey: the highest delivery id seen}
//   incidents_notified   {serverKey: the incident marks posted}
//   incidents_withdrawn  {serverKey: the marks taken back again}
//   hint_dismissed       Home's row was answered `not now`
//
// ### Two stores, not one, and the reason is privacy
//
// Every book above is a book of **ids**, except the mirror, which holds a
// reminder's own words (`AlertsMirrorAlarm/message`). `UserDefaults` is in the
// device backup; household speech is not ours to put there. So the ids live in
// `UserDefaults` and the mirror lives in a file in Application Support with
// `isExcludedFromBackupKey`, which is this platform's version of the
// `android:allowBackup="false"` the Kotlin relies on (`AlertStore.kt:21-22`,
// DECISIONS §9).
//
// `exact_hint_dismissed` is not here: there is no exact-alarm hint on iOS
// (``AlertsHint``).
// ---------------------------------------------------------------------------

/// The id books and the dismissal. Synchronous on purpose: every caller is
/// inside an actor, and a book of ids is a dictionary read, so an `async`
/// accessor would add a suspension point — a place for the active server to
/// change — for nothing.
public protocol AlertsBookStore: Sendable {

    /// Every `<serverKey>|<timerId>|<epochMs>` this phone has posted.
    func alerted() -> [String]
    func setAlerted(_ entries: [String])

    func firesSeen(_ serverKey: String) -> Int64?
    func firesSeenAt(_ serverKey: String) -> String?

    /// Record `id` as processed. `at` (its `fired_at`) replaces the stored
    /// time when given; an id that **moved** without one drops it, which is
    /// `setSeen`'s third branch (`AlertStore.kt:196-203`) and the reason this
    /// is one call rather than two setters.
    func setFiresSeen(_ serverKey: String, id: Int64, at: String?)

    /// The server's fire history started again: forget where this phone was in
    /// it, and which of its timers were posted — their ids restart too — so
    /// the next read is a first run (`startOver`, `AlertStore.kt:208`).
    func startOver(_ serverKey: String)

    func helpSeen(_ serverKey: String) -> Int64?
    func setHelpSeen(_ serverKey: String, id: Int64)

    func incidentsNotified(_ serverKey: String) -> [String]
    func setIncidentsNotified(_ serverKey: String, _ marks: [String])
    func incidentsWithdrawn(_ serverKey: String) -> [String]
    func setIncidentsWithdrawn(_ serverKey: String, _ marks: [String])

    var hintDismissed: Bool { get }
    func dismissHint()

    /// Forget the dismissal, so the row is offered again.
    ///
    /// **Nothing in the app calls this.** It exists for the UI cases: the
    /// dismissal is a fact about the install (`UserDefaults`), so one case
    /// answering `not now` would hide the row from every case that ran after
    /// it, and a suite whose cases depend on their own order is a suite that
    /// measures one thing and reports another. ``AlertsHintModel`` reads the
    /// launch argument that calls it, under `#if DEBUG`.
    func forgetHint()
}

/// The mirror's own store, separate because its contents are not ids
/// (see the file comment).
public protocol AlertsMirrorStore: Sendable {
    func mirror() -> AlertsMirrorBook
    func setMirror(_ book: AlertsMirrorBook)
}

// MARK: - the id books, in UserDefaults

/// ``AlertsBookStore`` in `UserDefaults`, each book under one key as JSON.
///
/// JSON rather than a plist dictionary of `NSNumber`s so that a book written
/// by one version is read by the next by a decoder that can be given a
/// default, rather than by a cast that would trap: a corrupt or absent blob is
/// an **empty book**, never a failure, which is `runCatching { … }
/// .getOrDefault(…)` on every one of Android's decoders.
/// `@unchecked Sendable` with the reason spelt out, as the house rule asks:
/// `UserDefaults` is documented as thread-safe, this holds one and a prefix,
/// and both are set once in `init`.
public final class AlertsDefaultsBookStore: AlertsBookStore, @unchecked Sendable {

    private let defaults: UserDefaults
    private let prefix: String

    /// - Parameter prefix: so a test can hold two of these in one process
    ///   without one's books being the other's.
    public init(defaults: UserDefaults = .standard, prefix: String = "alerts.") {
        self.defaults = defaults
        self.prefix = prefix
    }

    private func key(_ name: String) -> String { prefix + name }

    private func read<Value: Decodable>(_ name: String, default fallback: Value) -> Value {
        guard let data = defaults.data(forKey: key(name)),
              let decoded = try? JSONDecoder().decode(Value.self, from: data)
        else { return fallback }
        return decoded
    }

    private func write<Value: Encodable>(_ name: String, _ value: Value) {
        guard let data = try? JSONEncoder().encode(value) else { return }
        defaults.set(data, forKey: key(name))
    }

    public func alerted() -> [String] { read("alerted", default: []) }

    public func setAlerted(_ entries: [String]) { write("alerted", entries) }

    public func firesSeen(_ serverKey: String) -> Int64? {
        read("fires_seen", default: [String: Int64]())[serverKey]
    }

    public func firesSeenAt(_ serverKey: String) -> String? {
        read("fires_seen_at", default: [String: String]())[serverKey]
    }

    public func setFiresSeen(_ serverKey: String, id: Int64, at: String?) {
        var ids = read("fires_seen", default: [String: Int64]())
        let before = ids[serverKey]
        ids[serverKey] = id
        write("fires_seen", ids)
        var times = read("fires_seen_at", default: [String: String]())
        if let at {
            times[serverKey] = at
        } else if before != id {
            times.removeValue(forKey: serverKey)
        }
        write("fires_seen_at", times)
    }

    public func startOver(_ serverKey: String) {
        var ids = read("fires_seen", default: [String: Int64]())
        ids.removeValue(forKey: serverKey)
        write("fires_seen", ids)
        var times = read("fires_seen_at", default: [String: String]())
        times.removeValue(forKey: serverKey)
        write("fires_seen_at", times)
        setAlerted(alertsForgetServer(alerted(), serverKey: serverKey))
    }

    public func helpSeen(_ serverKey: String) -> Int64? {
        read("help_seen", default: [String: Int64]())[serverKey]
    }

    public func setHelpSeen(_ serverKey: String, id: Int64) {
        var ids = read("help_seen", default: [String: Int64]())
        ids[serverKey] = id
        write("help_seen", ids)
    }

    public func incidentsNotified(_ serverKey: String) -> [String] {
        read("incidents_notified", default: [String: [String]]())[serverKey] ?? []
    }

    public func setIncidentsNotified(_ serverKey: String, _ marks: [String]) {
        var book = read("incidents_notified", default: [String: [String]]())
        book[serverKey] = marks
        write("incidents_notified", book)
    }

    public func incidentsWithdrawn(_ serverKey: String) -> [String] {
        read("incidents_withdrawn", default: [String: [String]]())[serverKey] ?? []
    }

    public func setIncidentsWithdrawn(_ serverKey: String, _ marks: [String]) {
        var book = read("incidents_withdrawn", default: [String: [String]]())
        book[serverKey] = marks
        write("incidents_withdrawn", book)
    }

    public var hintDismissed: Bool { defaults.bool(forKey: key("hint_dismissed")) }

    public func dismissHint() { defaults.set(true, forKey: key("hint_dismissed")) }

    public func forgetHint() { defaults.removeObject(forKey: key("hint_dismissed")) }
}

// MARK: - the mirror, in a file the backup does not take

/// ``AlertsMirrorStore`` in one JSON file in Application Support, excluded
/// from the backup.
///
/// Every failure is swallowed to an empty book or a dropped write, for the
/// reason Android's `decodeMirror` never throws: a mirror that cannot be read
/// costs the phone one sync (the next `GET /api/timers` rebuilds it), and a
/// mirror that threw would cost it the alerts.
public final class AlertsFileMirrorStore: AlertsMirrorStore {

    private let url: URL

    /// - Parameter url: the file. The default is
    ///   `Application Support/Domovoi/alerts-mirror.json`; a test passes a
    ///   temporary one.
    public init(url: URL? = nil) {
        if let url {
            self.url = url
        } else {
            let base = (try? FileManager.default.url(
                for: .applicationSupportDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            )) ?? URL(fileURLWithPath: NSTemporaryDirectory())
            self.url = base.appendingPathComponent("Domovoi/alerts-mirror.json")
        }
    }

    public func mirror() -> AlertsMirrorBook {
        guard let data = try? Data(contentsOf: url),
              let book = try? JSONDecoder().decode(AlertsMirrorBook.self, from: data)
        else { return AlertsMirrorBook() }
        return book
    }

    public func setMirror(_ book: AlertsMirrorBook) {
        let folder = url.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        guard let data = try? JSONEncoder().encode(book) else { return }
        try? data.write(to: url, options: .atomic)
        // The reminder's words are household speech: the file is not in the
        // iCloud backup. Set after every write, because an atomic write
        // replaces the file and the attribute goes with the old one.
        var excluded = URLResourceValues()
        excluded.isExcludedFromBackup = true
        var target = url
        try? target.setResourceValues(excluded)
    }
}
