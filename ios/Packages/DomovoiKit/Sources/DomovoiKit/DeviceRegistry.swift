import Foundation

/// This install's identity on the server — the iOS half of the web's
/// `DeviceIdentity` (web/static/data.js) and of
/// `android/.../net/DeviceRegistry.kt`.
///
/// `POST /api/devices/register` is an upsert and is safe on every launch: the
/// server seeds `name` only when the row is NEW, so sending a platform
/// default can never stomp a name someone chose (docs/API_REFERENCE.md says
/// so about the route, and that is why the app may keep sending one).
///
/// The reply carries `shared_screen`, which is how this install learns an
/// admin marked it a shared screen — the kitchen tablet. This batch returns
/// it and acts on nothing; the masking is the shell's (I1-C and Home).
public enum DeviceRegistry {

    /// The device's own row, as the route answers it.
    public struct DeviceRow: Equatable, Sendable, Decodable {
        public let deviceID: String
        public let name: String
        public let platform: String?
        public let lastSeenAt: String?

        /// Whether an admin marked this device a shared screen (V014). Absent
        /// from a server older than shared screens — see
        /// ``sharedScreenAnswer(_:)``, which reads absent as "no".
        public let sharedScreen: Bool?

        public init(deviceID: String = "", name: String = "", platform: String? = nil, lastSeenAt: String? = nil, sharedScreen: Bool? = nil) {
            self.deviceID = deviceID
            self.name = name
            self.platform = platform
            self.lastSeenAt = lastSeenAt
            self.sharedScreen = sharedScreen
        }

        enum CodingKeys: String, CodingKey {
            case deviceID = "device_id"
            case name
            case platform
            case lastSeenAt = "last_seen_at"
            case sharedScreen = "shared_screen"
        }

        public init(from decoder: any Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            deviceID = try c.decodeIfPresent(String.self, forKey: .deviceID) ?? ""
            name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
            platform = try c.decodeIfPresent(String.self, forKey: .platform)
            lastSeenAt = try c.decodeIfPresent(String.self, forKey: .lastSeenAt)
            sharedScreen = try c.decodeIfPresent(Bool.self, forKey: .sharedScreen)
        }
    }

    /// What this app calls itself to the server.
    ///
    /// Sent honestly even though the server does not treat it neutrally
    /// today: `web/backend/api/printers.py` labels a print job's source "app"
    /// only for `platform == "android"` (or a UA that looks like
    /// okhttp/domovoi-android), and `web/static/manual.jsx` decides whether
    /// the house has a phone app with `devices.some(d => d.platform ===
    /// 'android')`. So an iPhone is mislabelled in both places until the
    /// server learns about this app. Claiming to be Android to work around
    /// that would put a lie in the device roster an admin reads, which is
    /// worse than a wrong label on a print job. Both server sites are in the
    /// batch report; `web/` is out of bounds here.
    public static let platform = "ios"

    /// The register body, as bytes.
    ///
    /// `ownerPersonId` is this server's "This phone belongs to" (Settings →
    /// Connection, a `people.id` kept as text): **always sent**, as a
    /// positive integer or as JSON `null`, because the route documents that a
    /// field left out keeps the stored value — so omitting it would keep a
    /// stale owner on the server forever. Null (nobody, or an id that isn't a
    /// number ≥ 1) clears it.
    ///
    /// Returning `Data` rather than a dictionary is what lets the test assert
    /// the bytes that go on the wire, including that `owner_person_id` is
    /// *present* and null rather than missing — the one thing about this body
    /// that a `[String: Any]` round-trip cannot tell apart.
    public static func registerPayload(
        deviceID: String,
        name: String,
        userAgent: String,
        ownerPersonID: String?
    ) throws -> Data {
        try JSONEncoder().encode(Registration(
            deviceID: deviceID,
            name: name,
            userAgent: userAgent,
            ownerPersonID: ownerPersonID(ownerPersonID)
        ))
    }

    /// Kotlin's `ownerPersonId?.trim()?.toLongOrNull()?.takeIf { it >= 1 }`.
    static func ownerPersonID(_ raw: String?) -> Int64? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              let value = Int64(trimmed), value >= 1 else { return nil }
        return value
    }

    struct Registration: Encodable {
        let deviceID: String
        let name: String
        let userAgent: String
        let ownerPersonID: Int64?

        enum CodingKeys: String, CodingKey {
            case deviceID = "device_id"
            case name
            case platform
            case userAgent = "user_agent"
            case ownerPersonID = "owner_person_id"
        }

        func encode(to encoder: any Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(deviceID, forKey: .deviceID)
            try c.encode(name, forKey: .name)
            try c.encode(DeviceRegistry.platform, forKey: .platform)
            try c.encode(userAgent, forKey: .userAgent)
            // `encodeNil` rather than letting an optional be skipped: the
            // field must be THERE.
            if let ownerPersonID { try c.encode(ownerPersonID, forKey: .ownerPersonID) }
            else { try c.encodeNil(forKey: .ownerPersonID) }
        }
    }

    /// The rename body for `PATCH /api/devices/{id}`.
    public static func renamePayload(name: String) throws -> Data {
        try JSONEncoder().encode(["name": name])
    }

    // ---- the name and the user agent ---------------------------------------

    /// "iPhone" rather than a model identifier — a SEED only; the real answer
    /// is whatever the person types in Settings.
    ///
    /// Android can be specific here, because `Build.MODEL` is "Pixel 8" and
    /// it may read it. iOS cannot be as specific honestly. `UIDevice.name`
    /// is the obvious source, and Apple's documentation for it says an app
    /// linked on or after iOS 16 gets the device's *model* name there unless
    /// it holds the user-assigned device name entitlement, which this app has
    /// no grounds to ask for. `sysctlbyname("hw.machine")` gives
    /// "iPhone15,2", which no person would recognise in a queue — the exact
    /// thing Android's comment says not to do ("Pixel 8" rather than
    /// "google/shiba"). So the seed is the plain model word, and the Settings
    /// field is the answer.
    ///
    /// Pure, and takes the model, so it is testable on a Mac with no UIKit.
    /// The app passes `UIDevice.current.model`.
    public static func suggestedDeviceName(model: String) -> String {
        let trimmed = model.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "iPhone" : trimmed
    }

    /// Android sends `"Android 15; Pixel 8"`; this sends `"iOS 18.3; iPhone"`
    /// — same shape, so a roster row reads the same way whichever phone wrote
    /// it. The app passes `UIDevice.current.systemVersion` and
    /// `UIDevice.current.model`.
    public static func userAgent(systemVersion: String, model: String) -> String {
        "iOS \(systemVersion); \(suggestedDeviceName(model: model))"
    }

    // ---- when to ask -------------------------------------------------------

    /// Whether the shell may send this install's registration now: when the
    /// live connection is up (the server evidently lets this install in), or
    /// when it is paired.
    ///
    /// The second half matters because a paired install counts as shared
    /// until it has an answer (``isSharedScreen(answer:paired:)``), so it
    /// must be able to get one over plain HTTP while the socket is down — a
    /// proxy that doesn't pass WebSocket upgrades, say. Gated on the socket
    /// alone, such a phone would lose Chat and see "busy" for good. An
    /// unpaired install with no socket is not asked for: its registration
    /// would only be refused.
    public static func canRegister(connected: Bool, paired: Bool) -> Bool {
        connected || paired
    }

    /// Android re-asks every couple of minutes while the app is on screen.
    public static let reaskEveryMs: Int64 = 2 * 60 * 1000

    /// …and never twice inside this gap, however many things changed at once.
    public static let reaskGapMs: Int64 = 15 * 1000

    /// The cadence rule, without the timer.
    ///
    /// Android hangs this on Compose: a `LaunchedEffect` keyed on the
    /// connection, the server, the token and the owner fires one register
    /// when any of them changes, and a second effect wakes every
    /// ``reaskEveryMs`` while the lifecycle is `STARTED` and registers if
    /// ``reaskGapMs`` has passed since the last ask. Nothing pushes an
    /// admin's shared-screen change, and a wall tablet never leaves Home, so
    /// the poll is how it is ever learnt.
    ///
    /// This batch has no UI to hang a timer on, so what is ported is the
    /// decision: given when it last asked and what has changed, should it ask
    /// now? I1-C drives it from the scene phase (iOS's counterpart of
    /// `repeatOnLifecycle(STARTED)`, and the reason the poll stops when the
    /// app leaves the screen on both phones).
    ///
    /// `reason` being `.changed` is Android's first effect — a new
    /// connection, server, token or owner — and it still respects the gap, as
    /// Android's second effect does, because the two can fire together.
    public static func shouldRegister(
        now: Int64,
        lastAskedAt: Int64?,
        reason: ReaskReason,
        connected: Bool,
        paired: Bool
    ) -> Bool {
        guard canRegister(connected: connected, paired: paired) else { return false }
        guard let lastAskedAt else { return true }
        let since = now - lastAskedAt
        switch reason {
        case .changed: return since >= reaskGapMs
        case .onScreenTick: return since >= reaskEveryMs
        }
    }

    public enum ReaskReason: Equatable, Sendable {
        /// The connection came up, the server changed, a token arrived, or
        /// "This phone belongs to" changed.
        case changed
        /// The on-screen poll woke up.
        case onScreenTick
    }

    // ---- the shared-screen answer ------------------------------------------

    /// The answer a device row gives.
    ///
    /// A row with no `shared_screen` at all comes from a web backend older
    /// than shared screens (V014), which cannot mark a device — so it is
    /// "no", never "unknown". (The web leaves such a row unanswered, which on
    /// a paired browser means "shared" forever; for a phone that would hide
    /// Chat for good the moment it met an older server.)
    public static func sharedScreenAnswer(_ row: DeviceRow) -> Bool {
        row.sharedScreen ?? false
    }

    /// What the launchers and Home treat this install as. `answer` is the
    /// server's last word (nil while it has never answered): a paired install
    /// with no answer yet may well be the kitchen tablet, so it counts as
    /// shared until the server says otherwise — the web's rule. An unpaired
    /// install can't learn (its registration would be refused), so it is
    /// never masked.
    public static func isSharedScreen(answer: Bool?, paired: Bool) -> Bool {
        answer ?? paired
    }
}

/// This install's device id: stable for the install, and **ours**.
///
/// Not `identifierForVendor`. Three reasons, in order of weight:
///
///  1. It is not ours to give the server. The same value identifies this
///     phone to every app Coders Farm ever ships, so handing it to the house
///     server makes the server's device roster a cross-app identifier; a
///     random id we mint ourselves tells the server only "this install".
///  2. Apple documents that it changes: the value is the same for every app
///     from one vendor on one device, and when the *last* of that vendor's
///     apps is deleted and one is installed again, the value is new. A
///     reinstall would therefore silently become a second device row, with
///     its own name and its own admin block — and an admin block that a
///     reinstall sheds is no block at all (API_REFERENCE: a block matches id
///     OR name for exactly this reason).
///  3. Android's id is its own too (`Prefs.deviceId`), and the id has to mean
///     the same kind of thing on both phones: one install, one row, renaming
///     it relabels its room-queue entries and an admin block on it covers
///     every feature.
///
/// Android mints `android-` plus **four** hex characters — 16 bits, so two
/// phones in one house collide with probability about 1 in 65,536 per pair,
/// and a collision means two phones sharing one row, one name and one block.
/// The server accepts `^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$`
/// (web/backend/api/devices.py), so there is room: this mints `ios-` plus
/// **sixteen** hex characters, 64 bits, 20 characters in all.
public enum DeviceIdentity {

    /// Where the id is kept. The app's is `UserDefaults`; a test's is a box.
    /// It is deliberately NOT the Keychain: the id is not a secret, and a
    /// Keychain item that outlives the app's own storage would resurrect an
    /// id whose server row the person had deliberately forgotten.
    public protocol Store: Sendable {
        func read() -> String?
        func write(_ deviceID: String)
    }

    /// The saved id, or a new one saved now. The only way the app gets one.
    public static func stable(in store: some Store) -> String {
        if let saved = store.read(), isWellFormed(saved) { return saved }
        let minted = mint()
        store.write(minted)
        return minted
    }

    /// `ios-` plus 64 bits of hex from the system's CSPRNG.
    public static func mint() -> String {
        var bytes = [UInt8](repeating: 0, count: 8)
        for index in bytes.indices { bytes[index] = UInt8.random(in: 0...255) }
        return "ios-" + bytes.map { String(format: "%02x", $0) }.joined()
    }

    /// The server's own rule, so a malformed id is caught here rather than as
    /// a `400` the person cannot act on: a leading letter or digit, then 2 to
    /// 63 more of letters, digits, `.`, `_`, `:` or `-`.
    ///
    /// Written out over `utf8` rather than as a regular expression, like the
    /// rest of this module's parsing: `NSRegularExpression` is ICU, and its
    /// character classes take more than ASCII.
    public static func isWellFormed(_ deviceID: String) -> Bool {
        let bytes = Array(deviceID.utf8)
        guard (3...64).contains(bytes.count) else { return false }
        func isAlphanumeric(_ b: UInt8) -> Bool {
            (b >= 0x61 && b <= 0x7a) || (b >= 0x41 && b <= 0x5a) || (b >= 0x30 && b <= 0x39)
        }
        guard isAlphanumeric(bytes[0]) else { return false }
        for b in bytes.dropFirst() {
            let ok = isAlphanumeric(b) || b == 0x2e || b == 0x5f || b == 0x3a || b == 0x2d
            if !ok { return false }
        }
        return true
    }
}
