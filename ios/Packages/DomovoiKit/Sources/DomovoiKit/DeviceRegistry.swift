import Foundation
import Synchronization

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

        /// Whose phone this is, as THE SERVER says (V035; "this is me",
        /// 2026-11-12): the person's id on that server, their name, and when
        /// this phone itself last said "this is me" (nil after an admin set
        /// it). For display only: what the app stores is worked out from the
        /// raw answer by ``ownerAnswer(_:before:heldKey:)``, because there an
        /// absent field and a null one mean different things.
        public let ownerPersonID: Int?
        public let ownerPersonName: String?
        public let ownerClaimedAt: String?

        public init(
            deviceID: String = "",
            name: String = "",
            platform: String? = nil,
            lastSeenAt: String? = nil,
            sharedScreen: Bool? = nil,
            ownerPersonID: Int? = nil,
            ownerPersonName: String? = nil,
            ownerClaimedAt: String? = nil
        ) {
            self.deviceID = deviceID
            self.name = name
            self.platform = platform
            self.lastSeenAt = lastSeenAt
            self.sharedScreen = sharedScreen
            self.ownerPersonID = ownerPersonID
            self.ownerPersonName = ownerPersonName
            self.ownerClaimedAt = ownerClaimedAt
        }

        // **No `claim_key` here, on purpose.** The one-time key goes from the
        // raw answer straight into the ``ClaimBook`` (the Keychain), so no
        // value that is passed around, compared or printed ever holds it.
        enum CodingKeys: String, CodingKey {
            case deviceID = "device_id"
            case name
            case platform
            case lastSeenAt = "last_seen_at"
            case sharedScreen = "shared_screen"
            case ownerPersonID = "owner_person_id"
            case ownerPersonName = "owner_person_name"
            case ownerClaimedAt = "owner_claimed_at"
        }

        public init(from decoder: any Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            deviceID = try c.decodeIfPresent(String.self, forKey: .deviceID) ?? ""
            name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
            platform = try c.decodeIfPresent(String.self, forKey: .platform)
            lastSeenAt = try c.decodeIfPresent(String.self, forKey: .lastSeenAt)
            sharedScreen = try c.decodeIfPresent(Bool.self, forKey: .sharedScreen)
            ownerPersonID = (try? c.decodeIfPresent(Int.self, forKey: .ownerPersonID)) ?? nil
            ownerPersonName = (try? c.decodeIfPresent(String.self, forKey: .ownerPersonName)) ?? nil
            ownerClaimedAt = (try? c.decodeIfPresent(String.self, forKey: .ownerClaimedAt)) ?? nil
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
    /// **No `owner_person_id`** (2026-11-12, `DeviceRegistry.kt`
    /// `registerPayload`). Left out, the server keeps the person the phone
    /// already has. Sending it — as this app did until now, `null` for a
    /// phone nobody had picked — would undo, on every app start, a claim made
    /// on the People page, in the dashboard or by an admin. Whose the phone
    /// is comes back in the ANSWER instead (``rememberDeviceAnswer(_:server:data:)``).
    ///
    /// Returning `Data` rather than a dictionary is what lets the test assert
    /// the bytes that go on the wire, including that the owner is *absent*.
    public static func registerPayload(deviceID: String, name: String, userAgent: String) throws -> Data {
        try JSONEncoder().encode(Registration(deviceID: deviceID, name: name, userAgent: userAgent))
    }

    struct Registration: Encodable {
        let deviceID: String
        let name: String
        let userAgent: String

        enum CodingKeys: String, CodingKey {
            case deviceID = "device_id"
            case name
            case platform
            case userAgent = "user_agent"
        }

        func encode(to encoder: any Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(deviceID, forKey: .deviceID)
            try c.encode(name, forKey: .name)
            try c.encode(DeviceRegistry.platform, forKey: .platform)
            try c.encode(userAgent, forKey: .userAgent)
        }
    }

    /// The rename body for `PATCH /api/devices/{id}`.
    public static func renamePayload(name: String) throws -> Data {
        try JSONEncoder().encode(["name": name])
    }

    /// Upsert this device's row and refresh `last_seen_at` **at `server`**,
    /// and store what the answer says about whose the phone is there.
    ///
    /// `registerWith` (`DeviceRegistry.kt`): the call goes only while
    /// `server` is still the active one (`onlyTo`), and the answer is filed
    /// under `server` — the address the caller read BEFORE the await, never
    /// the active one afterwards (read-after-await). With no `book` the
    /// answer is only returned.
    ///
    /// Returns nil on any failure, as Android's `registerDevice` does — the
    /// app works unnamed, its queue entries just carry no tag — so no caller
    /// has to decide what to do about a registration that did not happen.
    /// Whether it should be called at all is ``shouldRegister(now:lastAskedAt:reason:connected:paired:)``.
    public static func register(
        with client: DomovoiClient,
        book: (any ClaimBook)? = nil,
        server: String? = nil,
        deviceID: String,
        name: String,
        userAgent: String
    ) async -> DeviceRow? {
        guard let body = try? registerPayload(deviceID: deviceID, name: name, userAgent: userAgent) else {
            return nil
        }
        guard let data = try? await client.raw(
            method: "POST", path: "/api/devices/register", body: body, onlyTo: server
        ) else { return nil }
        guard let row = try? DomovoiClient.decode(data, as: DeviceRow.self) else { return nil }
        if let book, let server { rememberDeviceAnswer(book, server: server, data: data) }
        return row
    }

    /// Rename this device. Throws, so the Settings panel can report the
    /// failure — this one is a person pressing a button, not a background
    /// upsert.
    public static func rename(
        with client: DomovoiClient,
        deviceID: String,
        name: String
    ) async throws(DomovoiError) -> DeviceRow {
        guard let body = try? renamePayload(name: name) else {
            throw DomovoiError.notPermitted("that name cannot be sent")
        }
        return try await client.patch("/api/devices/\(pathEscaped(deviceID))", body: body, as: DeviceRow.self)
    }

    /// A device id in a path, escaped as Android's `Uri.encode` escapes it:
    /// everything but the unreserved set and `!.~'()*`. A well-formed id has
    /// nothing in it that needs escaping except `:`, but an id is
    /// self-asserted and this is a path segment, so it is escaped anyway.
    ///
    /// The safe set is written out rather than taken from
    /// `CharacterSet.alphanumerics`, which is Unicode-wide: it contains "é",
    /// "д" and "٣", where Android's `Uri.encode` safe set is ASCII letters and
    /// digits plus `_-!.~'()*` and nothing else.
    ///
    /// **This is for the reader, not a fix.** The two spellings produce the
    /// same bytes today, because `addingPercentEncoding` encodes every scalar
    /// outside ASCII however generous the set is — measured, in
    /// `PlatformFactsTests` fact 5 — and the two sets' ASCII halves are
    /// identical. What the explicit set buys is that the Kotlin's safe set and
    /// this one can be compared by reading them, and that the day Foundation
    /// stops encoding non-ASCII for a set that admits it, this app does not
    /// start spelling device ids differently from the Android app.
    static let pathSafeCharacters = CharacterSet(
        charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-!.~'()*"
    )

    static func pathEscaped(_ text: String) -> String {
        text.addingPercentEncoding(withAllowedCharacters: pathSafeCharacters) ?? text
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
    /// connection, the server and the token fires one register
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
    /// connection, server or token — and it still respects the gap, as
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
        /// The connection came up, the server changed, or a token arrived.
        /// (Not whose the phone is: since 2026-11-12 that is the answer's,
        /// never the body's — ``registerPayload(deviceID:name:userAgent:)``.)
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

/// What this install tells the server about itself, as one value.
///
/// The three arguments of ``DeviceRegistry/register(with:book:server:deviceID:name:userAgent:)``
/// that are this install's, collected so that ``ShellModel``'s sender takes
/// one closure rather than three and so that the **two that cannot be read in
/// `DomovoiKit` at all**
/// have an obvious home. `name` and `userAgent` come from `UIDevice`
/// (``DeviceRegistry/suggestedDeviceName(model:)``,
/// ``DeviceRegistry/userAgent(systemVersion:model:)``), and this package does
/// not import UIKit — so the app reads them on the main actor and hands them
/// over, which is also what keeps the UIKit read out of a `Sendable` closure
/// that runs anywhere.
///
/// It carried a fourth, `ownerPersonID`, until 2026-11-12: whose phone this
/// is no longer rides on the registration (an app start must not undo a claim
/// made elsewhere), so it is not this install's to say. It is the server's
/// answer, kept per server by ``DeviceOwnerBook``.
public struct DeviceSelf: Equatable, Sendable {

    public let deviceID: String
    public let name: String
    public let userAgent: String

    public init(deviceID: String, name: String, userAgent: String) {
        self.deviceID = deviceID
        self.name = name
        self.userAgent = userAgent
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

    /// `UserDefaults`, which is the app's store.
    ///
    /// In this module rather than in the app target so it is compiled and
    /// testable by `ios/scripts/pkgtest.sh` — `UserDefaults` is Foundation,
    /// and a store written beside `DomovoiApp` would be one more thing
    /// nothing checks.
    /// `@unchecked Sendable` with the reason, as the house rule asks:
    /// `UserDefaults` is not `Sendable` to the compiler, but Apple documents
    /// it as thread-safe ("the `UserDefaults` class is thread-safe"), the one
    /// stored property is a `let`, and the two methods below are the only
    /// things that touch it.
    public struct UserDefaultsStore: Store, @unchecked Sendable {

        /// The key. Android's is `Prefs.deviceId`; this one names the app so
        /// a shared suite could never collide.
        public static let key = "domovoi.deviceID"

        private let defaults: UserDefaults

        public init(defaults: UserDefaults = .standard) {
            self.defaults = defaults
        }

        public func read() -> String? { defaults.string(forKey: Self.key) }
        public func write(_ deviceID: String) { defaults.set(deviceID, forKey: Self.key) }
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

// ===========================================================================
// "This is me" — whose phone this is, per server (2026-11-12)
// ===========================================================================
//
// `DeviceRegistry.kt` (Android a1e611b6) and MOBILE-HANDOFF-2026-11-12 §1.
// The server decides (web/backend/api/devices.py `claim_decision`), and every
// device answer — the registration's and the claim's — says who the phone
// belongs to: `owner_person_id` / `owner_person_name` / `owner_claimed_at`.
// That ANSWER is what the app stores and shows, per server, never what it
// asked for, so a refused claim is never shown as the owner. Only
// ``claimThisPhone(client:book:server:deviceID:personID:)`` ("this is me" /
// "not me", and Settings' "This phone belongs to") changes it.

/// The person a server says this phone is: `id` on THAT server, the name it
/// had in the answer, and when this phone itself last said "this is me" (nil
/// after an admin set it, or a claim from before keys). `DeviceOwner`.
public struct DeviceOwner: Equatable, Sendable, Codable {
    public let id: Int
    public let name: String
    public let claimedAt: String?

    public init(id: Int, name: String = "", claimedAt: String? = nil) {
        self.id = id
        self.name = name
        self.claimedAt = claimedAt
    }
}

/// Where whose-phone-this-is and the claim key are kept, per server
/// (normalised address). ``DeviceOwnerBook`` in the app; the tests use the
/// same type over a ``SavedStateBox`` and an ``InMemoryKeychain``.
/// `ClaimBook` (`DeviceRegistry.kt`).
public protocol ClaimBook: AnyObject, Sendable {
    func deviceOwner(for server: String) -> DeviceOwner?
    func claimKey(for server: String) -> String?
    /// Store `owner` (nil: nobody) as the phone's owner at `server`, and
    /// `claimKey` as the key it holds there (nil: none).
    func rememberDeviceOwner(_ owner: DeviceOwner?, claimKey: String?, for server: String)
    func setSharedScreen(_ shared: Bool, for server: String)
    /// What `server` last said about this phone being a shared screen; nil
    /// before it has answered.
    func sharedScreen(for server: String) -> Bool?
}

/// The app's ``ClaimBook``: owners in `UserDefaults` (they are not secret —
/// the dashboard shows them to the household), claim keys in the Keychain
/// (``ClaimKeyVault``), shared-screen answers in memory (the shell re-asks
/// within seconds of every launch).
///
/// `@unchecked Sendable` with the reason the house rule asks for: every
/// mutable thing is behind the one `Mutex`, the ``SavedState`` and the vault
/// are `Sendable`, and ``changed`` is set once by the shell before any call.
public final class DeviceOwnerBook: ClaimBook, @unchecked Sendable {

    /// Its own `UserDefaults` key, under the app's prefix, for the reason
    /// ``PlayerListenerStore/key`` gives about the shell's blob.
    public static let key = "domovoi.device.owners"

    private struct State {
        var owners: [String: DeviceOwner]
        /// Keys read from the Keychain so far; `.some(nil)` is "read, none".
        var keys: [String: String?] = [:]
        var shared: [String: Bool] = [:]
    }

    private let place: any SavedState
    private let vault: ClaimKeyVault
    private let state: Mutex<State>

    /// Told after every change of owner, key or shared answer, off no
    /// particular thread. Carries nothing: a listener reads what it needs.
    public var changed: (@Sendable () -> Void)?

    public init(place: (any SavedState)? = nil, vault: ClaimKeyVault = ClaimKeyVault()) {
        let place = place ?? UserDefaultsSavedState(key: DeviceOwnerBook.key)
        self.place = place
        self.vault = vault
        let owners = place.read().flatMap { try? JSONDecoder().decode([String: DeviceOwner].self, from: $0) } ?? [:]
        self.state = Mutex(State(owners: owners))
    }

    private static func k(_ server: String) -> String { ServerCredentials.normalize(server) }

    public func deviceOwner(for server: String) -> DeviceOwner? {
        state.withLock { $0.owners[Self.k(server)] }
    }

    public func claimKey(for server: String) -> String? {
        let key = Self.k(server)
        guard !key.isEmpty else { return nil }
        if let cached = state.withLock({ $0.keys[key] }) { return cached }
        let read = vault.key(for: key)
        state.withLock { $0.keys[key] = .some(read) }
        return read
    }

    /// Whether a key is held for `server`, without handing the key out.
    public func holdsClaimKey(for server: String) -> Bool { claimKey(for: server) != nil }

    public func rememberDeviceOwner(_ owner: DeviceOwner?, claimKey: String?, for server: String) {
        let key = Self.k(server)
        guard !key.isEmpty else { return }
        let safe = headerSafeClaimKey(claimKey)
        let before = self.claimKey(for: key)
        let owners: [String: DeviceOwner]? = state.withLock { s in
            let next: [String: DeviceOwner] = {
                var o = s.owners
                o[key] = owner
                return o
            }()
            s.keys[key] = .some(safe)
            guard next != s.owners else { return nil }
            s.owners = next
            return next
        }
        if let owners, let blob = try? JSONEncoder().encode(owners) { place.write(blob) }
        if safe != before { vault.set(safe, for: key) }
        if owners != nil || safe != before { changed?() }
    }

    public func setSharedScreen(_ shared: Bool, for server: String) {
        let key = Self.k(server)
        guard !key.isEmpty else { return }
        let moved = state.withLock { s -> Bool in
            guard s.shared[key] != shared else { return false }
            s.shared[key] = shared
            return true
        }
        if moved { changed?() }
    }

    public func sharedScreen(for server: String) -> Bool? {
        state.withLock { $0.shared[Self.k(server)] }
    }

    /// The phone forgot `server`: its owner and its key go with it
    /// (`Prefs.forgetServer`, `rememberDeviceOwner(url, null, null)`).
    public func forget(server: String) {
        rememberDeviceOwner(nil, claimKey: nil, for: server)
        state.withLock { _ = $0.shared.removeValue(forKey: Self.k(server)) }
    }
}

/// What a device answer means for the phone at its server, or nil when the
/// answer says nothing about whose it is (a server from before V035).
///
/// The web's `DeviceIdentity.rememberOwner` rule (`ownerAnswer`,
/// `DeviceRegistry.kt`): the key comes once, in the answer to the claim
/// that minted it; a phone that is nobody's now, or whose person changed
/// without a new key (an admin set it), holds none.
public struct OwnerAnswer: Equatable, Sendable {
    public let owner: DeviceOwner?
    public let claimKey: String?
}

public func ownerAnswer(_ answer: [String: Any], before: DeviceOwner?, heldKey: String?) -> OwnerAnswer? {
    guard answer.keys.contains("owner_person_id") else { return nil }
    let id: Int? = {
        guard let n = answer["owner_person_id"] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() else {
            return nil
        }
        return n.intValue
    }()
    let next = id.map {
        DeviceOwner(
            id: $0,
            name: answer["owner_person_name"] as? String ?? "",
            claimedAt: answer["owner_claimed_at"] as? String
        )
    }
    let minted = headerSafeClaimKey(answer["claim_key"] as? String)
    let key: String?
    if let minted {
        key = minted
    } else if next == nil {
        key = nil
    } else if let before, before.id != next?.id {
        key = nil
    } else {
        key = heldKey
    }
    return OwnerAnswer(owner: next, claimKey: key)
}

/// Store what `data` (a device answer) says about the phone at `server` —
/// the server the call was made TO, read before it, never the active one
/// afterwards. `rememberDeviceAnswer` (`DeviceRegistry.kt`).
public func rememberDeviceAnswer(_ book: any ClaimBook, server: String, data: Data) {
    guard let row = try? JSONDecoder().decode(DeviceRegistry.DeviceRow.self, from: data),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { return }
    book.setSharedScreen(DeviceRegistry.sharedScreenAnswer(row), for: server)
    if let answer = ownerAnswer(object, before: book.deviceOwner(for: server), heldKey: book.claimKey(for: server)) {
        book.rememberDeviceOwner(answer.owner, claimKey: answer.claimKey, for: server)
    }
}

/// The server's refusals of a claim (the hand-off's table), by what the app
/// does about each. `ClaimRefusal` (`DeviceRegistry.kt`).
public enum ClaimRefusal: Equatable, Sendable {
    /// Another phone or browser already has the person: an admin's yes.
    case takenByAnother
    /// The id in the path isn't this phone: a bug.
    case notThisDevice
    /// No claim key, or the wrong one: drop it, offer "this is me".
    case cantProve
    /// An admin marked this phone a shared screen.
    case sharedScreen
    /// The person (or the device row) is gone: refresh People.
    case gone
    case other
}

/// `claimRefusal` (`DeviceRegistry.kt`), clause for clause.
public func claimRefusal(status: Int, detail: String?) -> ClaimRefusal {
    let d = detail ?? ""
    if status == 409 { return .sharedScreen }
    if status == 404 { return .gone }
    guard status == 403 else { return .other }
    if d.contains("already claimed by another phone or browser") { return .takenByAnother }
    if d.contains("only that phone or browser itself") { return .notThisDevice }
    if d.contains("can't prove it is") || d.contains("say \"this is me\" again") { return .cantProve }
    return .other
}

/// The `detail` sentence of a refusal, or nil. `apiDetail` (`DeviceRegistry.kt`).
public func claimDetail(_ error: DomovoiError) -> String? {
    guard let detail = jsonDetail(of: error.failure?.body)?.trimmingCharacters(in: .whitespacesAndNewlines),
          !detail.isEmpty
    else { return nil }
    return detail
}

/// "This is me" (`personID`) or "not me" (nil) for THIS phone at `server`:
/// `PUT /api/devices/{id}/person` with `X-Device-Id` and, **only when one is
/// held for `server`**, the claim key. `claimThisPhone` (`DeviceRegistry.kt`).
///
/// - The key is read for `server` and the call goes only while `server` is
///   the active one (`onlyTo`), so a key minted by one house can never be
///   sent to another — not by a switch mid-call, and not by a redirect
///   (``RedirectPolicy`` strips the header on every hop).
/// - The answer is stored for `server` and returned: whose the phone is NOW,
///   which is what the caller shows.
/// - Throws the server's refusal unchanged (the caller shows its sentence,
///   ``claimDetail(_:)``). A refusal that says the phone can't prove whose it
///   is drops the stored key: it no longer works.
public func claimThisPhone(
    client: DomovoiClient,
    book: any ClaimBook,
    server: String,
    deviceID: String,
    personID: Int?
) async throws(DomovoiError) -> DeviceOwner? {
    let path = "/api/devices/\(DeviceRegistry.pathEscaped(deviceID))/person"
    let body: Data
    if let personID {
        body = Data("{\"person_id\":\(personID)}".utf8)
    } else {
        body = Data("{\"person_id\":null}".utf8)
    }
    let data: Data
    do throws(DomovoiError) {
        data = try await client.raw(
            method: "PUT", path: path, body: body,
            headers: claimHeaders(deviceID: deviceID, claimKey: book.claimKey(for: server)),
            onlyTo: server
        )
    } catch {
        if let status = error.failure?.status,
           claimRefusal(status: status, detail: claimDetail(error)) == .cantProve {
            book.rememberDeviceOwner(book.deviceOwner(for: server), claimKey: nil, for: server)
        }
        throw error
    }
    guard (try? JSONSerialization.jsonObject(with: data)) is [String: Any] else {
        throw DomovoiError.notDomovoi("that isn't Domovoi: the answer is not what this app understands")
    }
    rememberDeviceAnswer(book, server: server, data: data)
    return book.deviceOwner(for: server)
}
