import Foundation

// Room mode's shared contracts (I7, batch 2026-11-14): the types and seams the
// three build units hold each other to, so they can run in parallel without
// touching each other's files.
//
//   R1 ENGINE-ML   implements ``RoomEars`` / ``RoomEarsLoading`` (and owns
//                  WakeNets, WakeDetector, WakeGate, CaptureMachine,
//                  OrtWakeNets, SileroVad, RoomOnnxEars).
//   R2 PROTOCOL    implements ``RoomEngineControl`` (RoomEngine and all it
//                  drives), consumes ``RoomKeeping``, ``RoomEarsLoading``,
//                  ``RoomPCMSource`` and ``RoomDeviceFacts``.
//   R3 SETUP+KIOSK implements ``RoomKeeping`` (RoomStore, the Keychain) and
//                  drives ``RoomEngineControl`` from the kiosk screen.
//
// design-notes/ios/batch-2026-11-14/ROOMMODE-MAP.md §6 is the long form. THIS
// FILE IS FROZEN for the three units: a change goes through the batch
// coordinator, because three branches compile against it.
//
// The Android spec for each type is named on it.
//
// 1.4.0 (several wake words per room) added, additively: ``RoomWakeWord``,
// ``RoomWakeWords``, ``RoomModelFiles/extras``, ``RoomEars/wakeScores(_:)``
// and ``RoomEars/wakeWords`` (with one-word defaults), and the store's
// ``RoomKeeping/wakeExtras`` / ``RoomKeeping/setWakeWords(_:)``.

// MARK: - the enrolment

/// What the dashboard's room code hands a phone that is to become a room
/// (`roommode/RoomEnrolment.kt:33-45`, `Enrolment`): the core to dial, the
/// fingerprint of the server identity it is held to, and the room.
/// R3's parser makes one; R2's engine dials it.
public struct RoomEnrolment: Equatable, Sendable, Codable {
    /// `http(s)://host[:port]`, scheme and host lower-case, no trailing slash.
    public let coreURL: String
    /// `SHA256:<43 base64 characters>` of the core's Ed25519 key.
    public let fingerprint: String
    /// The room id the phone connects as: `^[a-z][a-z0-9_]{0,31}$`.
    public let roomID: String
    /// The room's name exactly as the payload carried it (or as typed).
    /// With ``inRoom`` set it is this phone's own name ("Kitchen 2").
    public let roomLabel: String
    /// `in=` (1.5.0): the existing room this phone joins ("Add a satellite
    /// to the Kitchen" on the dashboard makes `room=Kitchen%202&in=kitchen`);
    /// nil: a room of its own. Sent in the hello as `requested_room` until
    /// the phone is approved. A value that is not a room id
    /// (``RoomEnrolmentParser/isRoomID(_:)``) is ignored, like an unknown key.
    /// `Enrolment.inRoom` (`RoomEnrolment.kt`).
    public let inRoom: String?
    /// The name of the room ``inRoom`` names, as far as the payload tells
    /// (``RoomEnrolmentParser/invitedRoomName(_:)``); nil without ``inRoom``.
    /// What the phone says it joins until the server names the room.
    public let inRoomName: String?

    public init(
        coreURL: String, fingerprint: String, roomID: String, roomLabel: String,
        inRoom: String? = nil, inRoomName: String? = nil
    ) {
        self.coreURL = coreURL
        self.fingerprint = fingerprint
        self.roomID = roomID
        self.roomLabel = roomLabel
        self.inRoom = inRoom
        self.inRoomName = inRoomName
    }
}

/// The room this phone is in (1.5.0, `ready.room` and the `room` frame):
/// its id, its NAME (what the phone shows: "This phone is in the Kitchen";
/// never the id), whether other devices share it, and `claim` (another
/// device is in it or a linked room: the one-turn-per-wake claims, which
/// this phone does not declare yet, so it is only kept).
/// `RoomProtocol.RoomPlace` (`RoomProtocol.kt`).
public struct RoomPlace: Equatable, Sendable {
    public let roomID: String
    /// The room's name; nil when the core could not read the room's row (it
    /// sends null, never the id). The phone then keeps the name it last
    /// showed for the same room, or says "this room"
    /// (``RoomStatusText/roomName(_:kept:keptFor:label:)``).
    public let name: String?
    public let shared: Bool
    public let claim: Bool

    public init(roomID: String, name: String?, shared: Bool, claim: Bool = false) {
        self.roomID = roomID
        self.name = name
        self.shared = shared
        self.claim = claim
    }
}

// MARK: - what the room screen shows

/// The connection, as the room screen says it (`RoomEngine.kt:70-79`, `Link`).
public enum RoomLink: Equatable, Sendable {
    case offline(reason: String)
    case connecting
    /// Parked for approval; `removed` when the core had accepted this room
    /// before (it was removed on the dashboard, or its pairing reset).
    case awaitingApproval(code: String?, removed: Bool)
    case rejected(reason: String)
    case conflict(reason: String)
    case ready
}

/// Where the room is in a turn (`RoomEngine.kt:81`, `Phase`).
public enum RoomPhase: String, Equatable, Sendable, CaseIterable {
    case listening, ack, capturing, thinking, speaking, call
}

/// Why iOS — not the person, not the server — has the room deaf
/// (ROOMMODE-MAP §3). **iOS-only**: Android's service keeps its microphone
/// through all of these.
public enum RoomMicStop: String, Equatable, Sendable {
    /// An audio-session interruption that has not ended, or ended without
    /// "should resume": a call, Siri, an alarm, another app's audio.
    case interrupted
    /// The app left the foreground: room mode listens only on screen (Q9).
    case leftForeground
    /// The media services reset; the room rebuilds its audio when it can.
    case mediaServicesReset
    /// The microphone permission is off in Settings.
    case permissionDenied
}

/// Everything the room screen draws, as one value
/// (`RoomEngine.kt:83-99`, `Status`, without `appOffer`: an iPhone's app
/// comes from the App Store, never from the household's server).
public struct RoomStatus: Equatable, Sendable {
    public var link: RoomLink
    public var phase: RoomPhase
    public var muted: Bool
    /// What the mic hears is going to the server (a capture, a call).
    public var micLive: Bool
    /// A recorder is open right now: what the mic chip follows.
    public var micOpen: Bool
    /// Two seconds of exact zeros: something is handing the room silence.
    public var micBlocked: Bool
    public var wakeReady: Bool
    /// Why the wake word is not ready (models missing on the server).
    public var wakeProblem: String?
    public var peerRoom: String?
    public var musicPlaying: Bool
    /// iOS has taken the microphone (``RoomMicStop``); nil while it has not.
    public var stoppedByIOS: RoomMicStop?
    /// The room this phone is in, as the server said it (`ready.room`, the
    /// `room` frame; 1.5.0); nil before that, or from an older core. The
    /// screen says its name, never its id.
    public var place: RoomPlace?

    public init(
        link: RoomLink = .connecting,
        phase: RoomPhase = .listening,
        muted: Bool = false,
        micLive: Bool = false,
        micOpen: Bool = false,
        micBlocked: Bool = false,
        wakeReady: Bool = false,
        wakeProblem: String? = nil,
        peerRoom: String? = nil,
        musicPlaying: Bool = false,
        stoppedByIOS: RoomMicStop? = nil,
        place: RoomPlace? = nil
    ) {
        self.link = link
        self.phase = phase
        self.muted = muted
        self.micLive = micLive
        self.micOpen = micOpen
        self.micBlocked = micBlocked
        self.wakeReady = wakeReady
        self.wakeProblem = wakeProblem
        self.peerRoom = peerRoom
        self.musicPlaying = musicPlaying
        self.stoppedByIOS = stoppedByIOS
        self.place = place
    }
}

/// The running room, as the kiosk screen drives it (R2 implements it as
/// `RoomEngine`; `RoomEngine.kt:201-266`). Every method may be called from
/// any thread and returns at once.
public protocol RoomEngineControl: AnyObject, Sendable {
    /// The status now.
    var status: RoomStatus { get }
    /// Every status change, starting with the current one. Each call is its
    /// own stream; it finishes when the engine stops.
    func statusUpdates() -> AsyncStream<RoomStatus>
    /// Starts the room. False (with the reason on ``status``) when the mic is
    /// someone else's (``PlayerMicArbiter``) or the phone was never enrolled.
    /// Call it only with the room screen in the foreground: iOS will not open
    /// a microphone for an app that is not.
    @discardableResult func start() -> Bool
    /// Stops listening, closes the socket and gives the microphone back.
    func stop()
    func setMuted(_ on: Bool)
    /// "check now" while waiting for approval: one retry, at most every 10 s.
    func retryNow()
}

// MARK: - what the room keeps (R3 implements, R2 consumes)

/// A signed file list the room accepted (`RoomIdentity.kt:162`, `Accepted`).
public struct RoomSignedList: Equatable, Sendable {
    public let manifest: [String: String]
    public let serial: Int64
    public let issuedAt: Int64
    public let digest: String

    public init(manifest: [String: String], serial: Int64, issuedAt: Int64, digest: String) {
        self.manifest = manifest
        self.serial = serial
        self.issuedAt = issuedAt
        self.digest = digest
    }
}

/// What the room last accepted on a channel (`RoomIdentity.kt:165`, `Seen`).
public struct RoomSeenList: Equatable, Sendable {
    public let serial: Int64
    public let digest: String?

    public init(serial: Int64, digest: String?) {
        self.serial = serial
        self.digest = digest
    }
}

/// What the room remembers about the lists and models it took
/// (`RoomModels.kt:29-39`, `ModelBook`).
public protocol RoomModelBook: AnyObject, Sendable {
    /// The last list accepted under `record` (freshness).
    func seen(_ record: String) -> RoomSeenList?
    func remember(_ record: String, _ accepted: RoomSignedList)
    /// The digest the signed list gave a trained model when it was taken.
    func trainedSHA(_ name: String) -> String?
    func setTrainedSHA(_ name: String, _ sha: String)
}

/// The room's own store, as the engine uses it (`RoomStore.kt`). R3's
/// `RoomStore` keeps it in the Keychain (the pairing key) and its own
/// defaults suite (the rest); ``InMemoryRoomStore`` is the tests'.
///
/// The household device token is NEVER in here: a room phone holds no
/// household credential (owner decision 2026-10-12).
public protocol RoomKeeping: RoomModelBook {
    var enrolment: RoomEnrolment? { get }
    /// The room's pairing key, or nil when there is none (or the Keychain
    /// will no longer give it: the phone then enrols again).
    func pairingToken() -> String?
    var approvalCode: String? { get }
    /// The code the core has on file wins over ours (4-8 digits only).
    func setApprovalCode(_ code: String)
    func renewApprovalCode()
    /// The core accepted this room at least once (a `ready`).
    var approvedOnce: Bool { get }
    func setApprovedOnce()
    var wakeWord: String { get }
    var wakeThreshold: Float { get }
    /// A pushed wake word; `threshold` replaces the room's threshold only
    /// when ``RoomWakeSettings/thresholdValue(_:)`` takes it.
    func setWakeWord(_ slug: String, threshold: Double?)
    /// The room's other wake words (1.4.0), after the main word, as last
    /// pushed: [] for a one-word room, and [] when the kept list does not
    /// start with ``wakeWord`` (``RoomWakeSettings/extras(kept:main:)``).
    var wakeExtras: [RoomWakeWord] { get }
    /// Keep the room's whole list, main word first; one word keeps none.
    func setWakeWords(_ words: [RoomWakeWord])
    var muted: Bool { get }
    func setMuted(_ on: Bool)
    /// The room this phone asked to join (the room code's `in=`, 1.5.0),
    /// sent in every hello as `requested_room` until the server accepts the
    /// phone (its first `ready`, ``clearRequestedRoom()``); nil: none asked
    /// for, or already approved. It only pre-selects the approval card's room.
    var requestedRoom: String? { get }
    /// Approved: the server decides the room from here on.
    func clearRequestedRoom()
    /// The name of the room this phone is in, as the server last said it
    /// (`ready.room` / the `room` frame), or before that the room the room
    /// code said it joins or makes; nil: none known. Kept WITH the room it
    /// names (``roomNameRoom``): a name is only ever shown for that room.
    var roomName: String? { get }
    /// The room id ``roomName`` belongs to; nil for a name kept before 1.5.0.
    var roomNameRoom: String? { get }
    /// The server said where this phone is (``RoomNameKeeping/nameToKeep(_:keptName:keptFor:)``):
    /// a name is kept with its room; no name writes nothing.
    func noteRoom(_ place: RoomPlace)
}

/// The kept room name's one rule (`RoomStore.nameToKeep`), here so the real
/// store and the in-memory one cannot differ.
public enum RoomNameKeeping {
    /// What ``RoomKeeping/noteRoom(_:)`` writes for `place`: its (room id,
    /// name) when it has a name that is not already the one kept for that
    /// room; nil (write nothing) for no name, so a room the core could not
    /// read never overwrites the last name.
    public static func nameToKeep(_ place: RoomPlace, keptName: String?, keptFor: String?) -> (roomID: String, name: String)? {
        guard let name = place.name, !name.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        if name == keptName && place.roomID == keptFor { return nil }
        return (place.roomID, name)
    }

    /// The name an enrolment knows, kept with its room: the room `in=` joins
    /// (``RoomEnrolment/inRoomName``), else the room the code makes (its own
    /// name, ``RoomEnrolment/roomLabel``).
    public static func enrolled(_ e: RoomEnrolment) -> (roomID: String, name: String)? {
        func shown(_ v: String?) -> String? {
            guard let v, !v.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
            return v
        }
        if let asked = e.inRoom.flatMap({ RoomEnrolmentParser.isRoomID($0) ? $0 : nil }) {
            return shown(e.inRoomName).map { (asked, $0) }
        }
        return shown(e.roomLabel).map { (e.roomID, $0) }
    }
}

/// The store's rules every implementation shares (`RoomStore.kt:185-208`),
/// here so the real store and the in-memory one cannot differ.
public enum RoomWakeSettings {
    public static let defaultWakeWord = "hey_jarvis"
    public static let defaultThreshold: Float = 0.5

    /// A wake threshold as the room keeps it: 0-1 (the dashboard's Wake
    /// sensitivity range, the Pi's `_wake_threshold_value`), else nil.
    public static func thresholdValue(_ raw: Double?) -> Float? {
        guard let raw, raw.isFinite, (0.0...1.0).contains(raw) else { return nil }
        return Float(raw)
    }

    /// The extra words of a kept list: the list only counts when it starts
    /// with the room's main word (the Pi's `wake_words.read_list` rule: a
    /// later one-word push replaced the main word without it).
    public static func extras(kept: [RoomWakeWord], main: String) -> [RoomWakeWord] {
        guard kept.count > 1, kept.first?.slug == main else { return [] }
        return Array(kept.dropFirst())
    }

    /// An approval code the core may hand back: 4-8 ASCII digits.
    public static func isApprovalCode(_ code: String) -> Bool {
        (4...8).contains(code.count) && code.allSatisfy { ("0"..."9").contains($0) }
    }

    /// 32 random bytes, URL-safe base64 without padding
    /// (`secrets.token_urlsafe(32)`, `RoomStore.kt:190`).
    public static func newPairingKey() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        var rng = SystemRandomNumberGenerator()
        for i in bytes.indices { bytes[i] = rng.next() }
        return Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    /// Six digits, what the dashboard's approval card takes.
    public static func newApprovalCode() -> String {
        String(Int.random(in: 100_000...999_999))
    }
}

/// ``RoomKeeping`` in memory: the package tests', and R3's UI-test seed.
/// Keeps exactly what it is told, by the shared rules above.
public final class InMemoryRoomStore: RoomKeeping, @unchecked Sendable {

    private struct State {
        var enrolment: RoomEnrolment?
        var token: String?
        var code: String?
        var approved = false
        var wakeWord = RoomWakeSettings.defaultWakeWord
        var threshold = RoomWakeSettings.defaultThreshold
        var wakeWords: [RoomWakeWord] = []
        var muted = false
        var seen: [String: RoomSeenList] = [:]
        var trained: [String: String] = [:]
        var requested: String?
        var roomName: String?
        var roomNameRoom: String?
    }

    private let state: RoomLock<State>

    public init(enrolment: RoomEnrolment? = nil, pairingToken: String? = nil, approvalCode: String? = nil) {
        state = RoomLock(State(
            enrolment: enrolment, token: pairingToken, code: approvalCode,
            requested: enrolment?.inRoom.flatMap { RoomEnrolmentParser.isRoomID($0) ? $0 : nil },
            roomName: enrolment.flatMap(RoomNameKeeping.enrolled)?.name,
            roomNameRoom: enrolment.flatMap(RoomNameKeeping.enrolled)?.roomID
        ))
    }

    public var enrolment: RoomEnrolment? { state.withLock { $0.enrolment } }
    public func pairingToken() -> String? { state.withLock { $0.token } }
    public var approvalCode: String? { state.withLock { $0.code } }
    public func setApprovalCode(_ code: String) {
        guard RoomWakeSettings.isApprovalCode(code) else { return }
        state.withLock { $0.code = code }
    }
    public func renewApprovalCode() { state.withLock { $0.code = RoomWakeSettings.newApprovalCode() } }
    public var approvedOnce: Bool { state.withLock { $0.approved } }
    public func setApprovedOnce() { state.withLock { $0.approved = true } }
    public var wakeWord: String { state.withLock { $0.wakeWord } }
    public var wakeThreshold: Float { state.withLock { $0.threshold } }
    public func setWakeWord(_ slug: String, threshold: Double?) {
        state.withLock {
            $0.wakeWord = slug
            if let t = RoomWakeSettings.thresholdValue(threshold) { $0.threshold = t }
        }
    }
    public var wakeExtras: [RoomWakeWord] {
        state.withLock { RoomWakeSettings.extras(kept: $0.wakeWords, main: $0.wakeWord) }
    }
    public func setWakeWords(_ words: [RoomWakeWord]) {
        state.withLock { $0.wakeWords = words.count > 1 ? words : [] }
    }
    public var muted: Bool { state.withLock { $0.muted } }
    public func setMuted(_ on: Bool) { state.withLock { $0.muted = on } }
    public func seen(_ record: String) -> RoomSeenList? { state.withLock { $0.seen[record] } }
    public func remember(_ record: String, _ accepted: RoomSignedList) {
        state.withLock { $0.seen[record] = RoomSeenList(serial: accepted.serial, digest: accepted.digest) }
    }
    public func trainedSHA(_ name: String) -> String? { state.withLock { $0.trained[name] } }
    public func setTrainedSHA(_ name: String, _ sha: String) { state.withLock { $0.trained[name] = sha } }
    public var requestedRoom: String? { state.withLock { $0.requested } }
    public func clearRequestedRoom() { state.withLock { $0.requested = nil } }
    public var roomName: String? {
        state.withLock { $0.roomName.flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 } }
    }
    public var roomNameRoom: String? { state.withLock { $0.roomNameRoom } }
    public func noteRoom(_ place: RoomPlace) {
        state.withLock {
            guard let kept = RoomNameKeeping.nameToKeep(place, keptName: $0.roomName, keptFor: $0.roomNameRoom) else { return }
            $0.roomName = kept.name
            $0.roomNameRoom = kept.roomID
        }
    }
}

// MARK: - the listening networks (R1 implements, R2 consumes)

/// One extra wake word's model file (1.4.0).
public struct RoomWakeModelFile: Equatable, Sendable {
    public let slug: String
    public let file: URL

    public init(slug: String, file: URL) {
        self.slug = slug
        self.file = file
    }
}

/// The model files a room loads (`RoomModels.kt:57`, `Files`), checked by
/// R2's model sync before they are handed over: the shared front end, the
/// voice detector, the main wake word's model and (1.4.0) the room's other
/// words' models, each a small classifier on the same front end.
public struct RoomModelFiles: Equatable, Sendable {
    public let melspectrogram: URL
    public let embedding: URL
    public let vad: URL
    public let wake: URL
    /// The wake word these are for ("hey_jarvis", or a trained slug).
    public let wakeKey: String
    /// The room's other words whose models are here and checked, in the
    /// room's order (at most the device's limit less one).
    public let extras: [RoomWakeModelFile]

    public init(melspectrogram: URL, embedding: URL, vad: URL, wake: URL, wakeKey: String, extras: [RoomWakeModelFile] = []) {
        self.melspectrogram = melspectrogram
        self.embedding = embedding
        self.vad = vad
        self.wake = wake
        self.wakeKey = wakeKey
        self.extras = extras
    }

    /// Every word these files are for, main word first.
    public var wakeKeys: [String] { [wakeKey] + extras.map(\.slug) }
}

// MARK: - several wake words (1.4.0)

/// One of a room's wake words, as `set_wake_word`'s `words` carries it: the
/// slug, and the word's own threshold (a trained word's), nil for the
/// room's Wake sensitivity.
public struct RoomWakeWord: Equatable, Sendable, Codable {
    public let slug: String
    public let threshold: Float?

    public init(slug: String, threshold: Float? = nil) {
        self.slug = slug
        self.threshold = threshold
    }
}

/// How many wake words a room phone runs, and the rules for a pushed list
/// (domovoi/wake_default.py, satellite/wake_words.py).
public enum RoomWakeWords {
    /// The most a room may have (the core's `MAX_ROOM_WORDS`).
    public static let maxPerRoom = 5
    /// The most a low-memory phone runs.
    public static let lowMemoryMax = 3
    /// At or under this much memory a phone is low-memory: a 2 GB iPhone or
    /// iPad reports a little under 2 GiB, a 3 GB one about 2.8 GiB, so
    /// 2.5 GiB splits them cleanly.
    public static let lowMemoryBytes: UInt64 = 5 * 1024 * 1024 * 1024 / 2

    /// How many words this phone runs at once: its hello's `wake_words_max`.
    ///
    /// openWakeWord's front end (melspectrogram + embedding) is shared and
    /// is nearly all the CPU (1.79 ms per 80 ms chunk on a desktop, against
    /// 0.012-0.026 ms per word's classifier), so CPU allows five on any
    /// phone that runs one. What each word does add is memory: its model
    /// and its own ONNX Runtime session (a few MB each, with the session's
    /// arena). A 2 GB phone (iPhone 6s/7/SE, the 2 GB iPads) already holds
    /// the front end, Silero, the audio graph, the room display and the
    /// reply's audio inside iOS's per-app memory limit, so it runs 3, as a
    /// Pi Zero 2 W (512 MB) does; anything bigger runs 5.
    public static func deviceMax(physicalMemory: UInt64 = ProcessInfo.processInfo.physicalMemory) -> Int {
        physicalMemory <= lowMemoryBytes ? lowMemoryMax : maxPerRoom
    }

    /// `set_wake_word`'s `words` (`[{"slug", "threshold"?}]`, or bare
    /// slugs), cleaned as the Pi's `parse_words`: slug-shaped
    /// (``RoomModels/isSlug(_:)``, the file stem the room loads), no
    /// repeats, at most ``maxPerRoom``, in order; a threshold outside 0-1
    /// is dropped (the room's applies). Nil when there is no usable list:
    /// then the room runs the frame's `slug` alone.
    public static func parse(_ raw: RoomJSON?) -> [RoomWakeWord]? {
        guard case .array(let items)? = raw else { return nil }
        var out: [RoomWakeWord] = []
        for item in items {
            var text: String?
            var threshold: Float?
            switch item {
            case .string(let s):
                text = s
            case .object(let o):
                text = o["slug"]?.stringValue
                switch o["threshold"] {
                case .int(let n)?: threshold = RoomWakeSettings.thresholdValue(Double(n))
                case .double(let d)?: threshold = RoomWakeSettings.thresholdValue(d)
                default: threshold = nil
                }
            default:
                text = nil
            }
            guard let slug = text?.trimmingCharacters(in: .whitespaces), RoomModels.isSlug(slug),
                  !out.contains(where: { $0.slug == slug }) else { continue }
            out.append(RoomWakeWord(slug: slug, threshold: threshold))
            if out.count >= maxPerRoom { break }
        }
        return out.isEmpty ? nil : out
    }

    /// The words a room runs after a push of `slug` with `words`: the list
    /// when it starts with `slug`, cut to `max`; otherwise `slug` alone (an
    /// older server, a one-word room, or a list that does not lead with the
    /// main word, which the Pi does not trust either).
    public static func pushed(slug: String, threshold: Float?, words: [RoomWakeWord]?, max: Int) -> [RoomWakeWord] {
        let main = RoomWakeWord(slug: slug, threshold: threshold)
        guard let words, words.first?.slug == slug else { return [main] }
        let limit = Swift.max(1, Swift.min(max, maxPerRoom))
        return [main] + Array(words.dropFirst().prefix(limit - 1))
    }

    /// Each loaded word's threshold (`words`, main word first): the first
    /// wakes at the room's (its Wake sensitivity, which a push's `threshold`
    /// sets), every other word at its own when the list gave it one, else
    /// at the room's. Android's `WakeWords.thresholds`.
    public static func thresholds(for words: [String], room: Float, extras: [RoomWakeWord]) -> [Float] {
        words.enumerated().map { i, word in
            if i == 0 { return room }
            return extras.first { $0.slug == word }?.threshold ?? room
        }
    }

    /// The words the room is set to run, main word first, no repeats, at
    /// most `max`: what a model sync fetches and the ears load.
    public static func configured(main: String, extras: [RoomWakeWord], max: Int) -> [String] {
        let limit = Swift.max(1, Swift.min(max, maxPerRoom))
        var out = [main]
        for w in extras where out.count < limit && !out.contains(w.slug) { out.append(w.slug) }
        return out
    }

    /// The hello's `wake_words` (Android's `reportedWords`): the words of
    /// models handed over and not loaded yet (the listening thread takes
    /// them on its next frame, and a new hello follows if a word then fails
    /// to load); else the words that loaded; before any model, the main
    /// word alone. Never padded with a main word that has not loaded.
    public static func reported(main: String, pending: [String]?, loaded: [String]?) -> [String] {
        pending ?? loaded ?? [main]
    }
}

/// The room's ears for one wake word: openWakeWord's streaming detector and
/// Silero voice detection (`RoomEngine.kt:695-700`, `Ears`, and
/// `isSpeech`, `:1047-1053`).
///
/// **One thread owns it**: the room's listening thread creates nothing else
/// on it and calls it from nowhere else, so it is not `Sendable` and needs
/// no lock (Android's rule for `WakeDetector` and `SileroVad`).
///
/// Every frame is 30 ms of 16 kHz mono PCM16, ``RoomAudioFrame/samples``
/// samples (fewer only at the end of a feed).
public protocol RoomEars: AnyObject {
    /// Feed one frame to the wake word. The highest score (0...1) of the
    /// 80 ms chunks it completed, or nil when none completed
    /// (`WakeDetector.accept`).
    func wakeScore(_ frame: [Int16]) throws -> Float?
    /// Feed one frame to every loaded word (1.4.0): each word's highest
    /// score, in ``wakeWords``' order, or nil when no chunk completed
    /// (`WakeDetector.acceptAll`). Call this OR ``wakeScore(_:)`` for a
    /// frame, never both. Default: the one word's.
    func wakeScores(_ frame: [Int16]) throws -> [Float]?
    /// The words that loaded, main word first; [] when the ears do not say
    /// (the room then reads them from the files it loaded).
    var wakeWords: [String] { get }
    /// Forget the wake word's history (`WakeDetector.reset`).
    func resetWake()
    /// Whether this frame is speech: quieter than
    /// ``RoomAudioFrame/speechFloorDBFS`` never is (Silero still sees it, to
    /// keep its state moving); otherwise Silero's probability at least
    /// ``RoomAudioFrame/vadThreshold``.
    func isSpeech(_ frame: [Int16]) throws -> Bool
    /// Forget Silero's state (`SileroVad.reset`), between captures.
    func resetVoice()
}

extension RoomEars {
    public func wakeScores(_ frame: [Int16]) throws -> [Float]? {
        try wakeScore(frame).map { [$0] }
    }

    public var wakeWords: [String] { [] }
}

/// Loads ``RoomEars`` for a set of files (R1: ONNX Runtime). Called on the
/// listening thread, which then owns what it returns.
public protocol RoomEarsLoading: Sendable {
    func load(_ files: RoomModelFiles) throws -> any RoomEars
}

/// The room's audio frame, the satellite protocol's (`RoomProtocol.kt:28-34`).
public enum RoomAudioFrame {
    public static let sampleRate = 16_000
    /// 30 ms of 16 kHz mono PCM16: what a Pi sends and the core counts.
    public static let samples = 480
    public static let bytes = samples * 2
    public static let milliseconds = 30
    /// Quieter than this is never speech (`RoomEngine.kt:1187`).
    public static let speechFloorDBFS = -55.0
    /// Silero's speech threshold (`RoomEngine.kt:1188`).
    public static let vadThreshold: Float = 0.5
}

// MARK: - the microphone (R2 implements on iOS; the feed is everyone's)

/// The microphone, raw: what the platform hands over, before the room's own
/// rules (mute, "blocked", the chip), which R2's `RoomMic` keeps on top
/// (`RoomMic.kt`).
///
/// Not `Sendable`: the room's listening thread owns one at a time.
public protocol RoomPCMSource: AnyObject {
    /// Open it: for listening (`call == false`: as raw as iOS gives it), or
    /// for a drop-in (`call == true`: voice processing on, Apple's echo
    /// canceller). Throws ``RoomMicFailure`` when iOS will not open it.
    func start(call: Bool) throws
    /// Block until up to `frame.count` samples are in `frame`; the count
    /// read, or a negative number when the source has stopped (iOS took the
    /// microphone, or ``stop()``).
    func read(into frame: inout [Int16]) -> Int
    func stop()
}

/// A source the room could not open, with the reason iOS gave.
public struct RoomMicFailure: Error, Equatable, Sendable {
    public let stop: RoomMicStop?
    public let note: String

    public init(stop: RoomMicStop?, note: String) {
        self.stop = stop
        self.note = note
    }
}

/// Makes the room's microphone source: the real one on a phone, a feed in
/// tests and in a UI-test launch (the Simulator's microphone is the Mac's,
/// if any; Android's `RoomTestHooks.feed`).
public typealias RoomPCMSourceMaking = @Sendable () -> any RoomPCMSource

/// A microphone that plays back `samples` in real time, 30 ms a frame,
/// looping. With no samples it hands over quiet noise (never exact zeros,
/// which the room would call a blocked mic). For package tests and for
/// R3's UI-test launch argument; nothing in a release build makes one.
public final class RoomFeedPCMSource: RoomPCMSource, @unchecked Sendable {

    private let samples: [Int16]
    private let paced: Bool
    private let state = RoomLock<(open: Bool, at: Int, seed: UInt32)>((false, 0, 0x0D0E))

    /// `paced: false` returns at once (package tests); `true` sleeps 30 ms a
    /// frame like a microphone (a UI-test launch).
    public init(samples: [Int16] = [], paced: Bool = true) {
        self.samples = samples
        self.paced = paced
    }

    public func start(call: Bool) throws { state.withLock { $0.open = true } }

    public func read(into frame: inout [Int16]) -> Int {
        guard state.withLock({ $0.open }) else { return -1 }
        if paced { Thread.sleep(forTimeInterval: Double(RoomAudioFrame.milliseconds) / 1000) }
        return state.withLock { s in
            guard s.open else { return -1 }
            for i in frame.indices {
                if samples.isEmpty {
                    s.seed = s.seed &* 1_103_515_245 &+ 12_345
                    frame[i] = Int16(Int((s.seed >> 16) % 7) - 3) | 1
                } else {
                    frame[i] = samples[s.at % samples.count]
                    s.at += 1
                }
            }
            return frame.count
        }
    }

    public func stop() { state.withLock { $0.open = false } }
}

// MARK: - the phone's own facts (R2 implements app-side, with UIKit)

/// The battery, the core's one object (`RoomDevice.kt:16-28`, `Battery`).
/// iOS cannot say which charger (`plugged` is always nil) or the battery's
/// temperature (`tempC` nil): the health frame then leaves them out, as it
/// does on a phone that will not say.
public struct RoomBattery: Equatable, Sendable {
    public let percent: Int?
    public let charging: Bool?
    public let onPower: Bool?
    public let tempC: Double?
    public let plugged: String?

    public init(percent: Int?, charging: Bool?, onPower: Bool?, tempC: Double? = nil, plugged: String? = nil) {
        self.percent = percent
        self.charging = charging
        self.onPower = onPower
        self.tempC = tempC
        self.plugged = plugged
    }

    /// On its charger (charging, or held full on it); nil when unknown.
    public var onCharger: Bool? {
        if charging == true || onPower == true { return true }
        if charging == nil && onPower == nil { return nil }
        return false
    }
}

/// What a room's health frame says about the phone (`RoomDevice.kt`,
/// `HealthSample.kt`). DomovoiKit cannot import UIKit, so the app supplies
/// the real one; ``FixedRoomDeviceFacts`` is the tests'.
public protocol RoomDeviceFacts: Sendable {
    func battery() -> RoomBattery
    /// Every battery change (level or charger), for the between-minute frame
    /// (`BatteryReporter.kt`). Finishes never; the engine stops reading.
    func batteryChanges() -> AsyncStream<RoomBattery>
    /// The process's memory footprint in kB, its thread count and its CPU
    /// seconds so far; nil where iOS will not say.
    func rssKB() -> Int64?
    func threads() -> Int?
    func cpuSeconds() -> Double?
    /// Seconds since the phone booted, and since this process started.
    func uptimeSeconds() -> Int64
    func processUptimeSeconds() -> Int64
}

/// Fixed answers, for tests.
public struct FixedRoomDeviceFacts: RoomDeviceFacts {
    public var batteryNow: RoomBattery

    public init(battery: RoomBattery = RoomBattery(percent: 80, charging: true, onPower: true)) {
        batteryNow = battery
    }

    public func battery() -> RoomBattery { batteryNow }
    public func batteryChanges() -> AsyncStream<RoomBattery> { AsyncStream { _ in } }
    public func rssKB() -> Int64? { nil }
    public func threads() -> Int? { nil }
    public func cpuSeconds() -> Double? { nil }
    public func uptimeSeconds() -> Int64 { 0 }
    public func processUptimeSeconds() -> Int64 { 0 }
}

// MARK: - a frame's JSON (every unit builds frames with it)

/// One JSON value of the satellite protocol, with whole numbers kept apart
/// from fractions: the core reads `utt` and `frames` as integers, and a
/// signed list's canonical form (`RoomIdentity.kt:238-269`, R2) has no room
/// for a float spelling. CaptureMachine's payloads (R1) and RoomProtocol's
/// frames (R2) are `[String: RoomJSON]`; R2 parses the core's text into it.
public indirect enum RoomJSON: Equatable, Sendable {
    case null
    case bool(Bool)
    case int(Int64)
    case double(Double)
    case string(String)
    case array([RoomJSON])
    case object([String: RoomJSON])

    /// Compact JSON, keys in code-point order (stable, so tests can compare
    /// text); a non-finite double is written as `null`, which JSON has no
    /// other spelling for.
    public var serialized: String {
        var out = ""
        write(into: &out)
        return out
    }

    private func write(into out: inout String) {
        switch self {
        case .null: out += "null"
        case .bool(let b): out += b ? "true" : "false"
        case .int(let n): out += String(n)
        case .double(let d):
            if !d.isFinite {
                out += "null"
            } else if d == d.rounded(), abs(d) < 1e15 {
                // 0.6 stays 0.6; a whole 2.0 is written 2.0, as Python and
                // kotlinx write a Double, so the core reads a float.
                out += String(Int64(d)) + ".0"
            } else {
                out += "\(d)"
            }
        case .string(let s): RoomJSON.writeString(s, into: &out)
        case .array(let items):
            out += "["
            for (i, item) in items.enumerated() {
                if i > 0 { out += "," }
                item.write(into: &out)
            }
            out += "]"
        case .object(let fields):
            out += "{"
            for (i, key) in fields.keys.sorted(by: RoomJSON.codePointOrder).enumerated() {
                if i > 0 { out += "," }
                RoomJSON.writeString(key, into: &out)
                out += ":"
                fields[key]?.write(into: &out)
            }
            out += "}"
        }
    }

    /// Python's `ensure_ascii=False` escaping: `"` `\` and control
    /// characters only.
    static func writeString(_ s: String, into out: inout String) {
        out += "\""
        for scalar in s.unicodeScalars {
            switch scalar {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case "\u{08}": out += "\\b"
            case "\u{0C}": out += "\\f"
            default:
                if scalar.value < 0x20 {
                    out += String(format: "\\u%04x", scalar.value)
                } else {
                    out.unicodeScalars.append(scalar)
                }
            }
        }
        out += "\""
    }

    /// Code-point order, Python's `sort_keys` (not Swift's `<`, which
    /// compares grapheme clusters).
    static func codePointOrder(_ a: String, _ b: String) -> Bool {
        a.unicodeScalars.lexicographicallyPrecedes(b.unicodeScalars) { $0.value < $1.value }
    }
}
