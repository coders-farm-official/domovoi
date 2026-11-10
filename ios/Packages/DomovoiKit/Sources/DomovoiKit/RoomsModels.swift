import Foundation

// ---------------------------------------------------------------------------
// The wire models for the Rooms screen — the port of `SatModels.kt:1-250`
// (read tonight in this worktree), plus the two shapes Android keeps
// elsewhere: `RoomQuietHours` (`SatQuietHours.kt:52`) and the health document
// (`SatModels.kt:44-75`).
//
// **Every field is `decodeIfPresent` with a default**, which is the Kotlin's
// own rule ("everything is nullable-with-defaults; the backend evolves",
// `SatModels.kt:12-13`). `JSONDecoder` ignores unknown keys but does *not*
// tolerate a missing one, so each model writes its `init(from:)` by hand —
// the same decision `HomeModels.swift` records, and for the same reason: one
// absent field must not throw the whole roster away.
//
// **Why these are not `HomeRoom` and friends.** `HomeRoom`
// (`HomeModels.swift:268`) decodes nine of the forty-odd fields the
// `Satellite` schema has (`web/backend/schemas.py:513`), and it lives in a
// file this item may not edit (NIGHT-RULES §2). So this is a **second decoder
// over the same route**, not an edit to Home's; if a later batch wants one
// model, the merge belongs to whoever owns `HomeModels.swift`. The one type
// that *is* reused is ``HomeAnnounceResult`` — `{"announced_to": [...]}` is
// one shape and two copies of it would be two things to keep in step.
// ---------------------------------------------------------------------------

// MARK: - the value a room is pushed with (D56)

/// Which room a pushed room screen is for.
///
/// Kamron, answer 56: "take the recommendation: a room opens as its own
/// pushed screen with swipe back." This is what names the room the pushed
/// screen is for: a **wrapper around the room id** rather than the id itself,
/// so that the thing a room screen is handed cannot be mistaken for any other
/// screen's `String`. ``RoomsSatellite`` is not it either, because this value
/// outlives the roster row it came from and a room that went offline while
/// its screen was up would then be read from its old state
/// (``RoomsDetailView`` keeps the live row instead).
///
/// It is **not** a navigation-path element, which is what it was built to be
/// and could not be. The shell's stack is `NavigationStack(path: $model.path)`
/// and ``ShellModel/path`` is `[Route]`; a typed path holds only its own
/// element type, so `NavigationLink(value: RoomsOpenRoom(…))` was inert and
/// train I003 measured the consequence on twenty-four cases. ``RoomsView``
/// pushes ``RoomsRoomScreen`` as a destination instead and hands it one of
/// these; its doc comment has the whole reading. It stays `Hashable` and
/// `Codable` because it is cheap and because the shared change that would
/// make it a path element again (a `NavigationPath` shell) wants both.
public struct RoomsOpenRoom: Hashable, Sendable, Identifiable, Codable {

    public let roomID: String

    public init(_ roomID: String) { self.roomID = roomID }

    public var id: String { roomID }
}

// MARK: - the roster: GET /api/satellites

/// One room of `GET /api/satellites` (`Satellite`, `SatModels.kt:117`;
/// `schemas.py:513`).
///
/// ``volume`` is a `Double?` here although the schema says `int`
/// (`schemas.py:533`): Android reads it as `Double?` and rounds
/// (`SatOverviewTab.kt:231`), and a server that ever sent `72.0` would
/// otherwise fail the decode of the whole roster for one key. The rounding is
/// in ``roomsVolumeLevel(_:)``.
public struct RoomsSatellite: Equatable, Sendable, Decodable, Identifiable {

    public let roomID: String

    /// `online` | `offline` | `waiting` (set up, never connected).
    public let status: String?

    /// **Not when the satellite was last seen.** The schema says so in so
    /// many words: this is when its MPD row was last allocated, "kept for
    /// compatibility; show `last_seen_at`" (`schemas.py:518-528`). Read
    /// through ``roomsLastSeenISO(_:)``, never directly.
    public let lastConnectedAt: String?

    /// When the core last heard from this room — the field Android does not
    /// read, which is why an offline card over there said `19d ago` for rooms
    /// heard minutes before the core froze (`schemas.py:518-528`).
    public let lastSeenAt: String?

    public let nowPlaying: RoomsNowPlaying?
    public let wifi: RoomsWifi?
    public let mpdPorts: RoomsMpdPorts?
    public let volume: Double?
    public let voice: String?
    public let version: String?
    public let fullDuplex: Bool
    public let inCallWith: String?

    /// "Only reminders for this device": on means this room announces only the
    /// timers and reminders set on it. Absent (an older server) is off, which
    /// is every room's default (`SatModels.kt:137-141`).
    public let timersOwnOnly: Bool

    /// An upgrade under way or just finished. **Decode-only here**: nothing on
    /// this phone can ask for one (see ``RoomsStrings/upgradeIsOnTheDashboard``),
    /// so the clock, the press ledger and the outcome sentences
    /// (`SatUpgrade.kt`) are not ported — only the fact that one is running,
    /// which is why a room may be offline for a minute.
    public let upgrade: RoomsUpgrade?

    public var online: Bool { status == "online" }

    public var id: String { roomID }

    public init(
        roomID: String = "",
        status: String? = nil,
        lastConnectedAt: String? = nil,
        lastSeenAt: String? = nil,
        nowPlaying: RoomsNowPlaying? = nil,
        wifi: RoomsWifi? = nil,
        mpdPorts: RoomsMpdPorts? = nil,
        volume: Double? = nil,
        voice: String? = nil,
        version: String? = nil,
        fullDuplex: Bool = false,
        inCallWith: String? = nil,
        timersOwnOnly: Bool = false,
        upgrade: RoomsUpgrade? = nil
    ) {
        self.roomID = roomID
        self.status = status
        self.lastConnectedAt = lastConnectedAt
        self.lastSeenAt = lastSeenAt
        self.nowPlaying = nowPlaying
        self.wifi = wifi
        self.mpdPorts = mpdPorts
        self.volume = volume
        self.voice = voice
        self.version = version
        self.fullDuplex = fullDuplex
        self.inCallWith = inCallWith
        self.timersOwnOnly = timersOwnOnly
        self.upgrade = upgrade
    }

    enum CodingKeys: String, CodingKey {
        case roomID = "room_id"
        case status
        case lastConnectedAt = "last_connected_at"
        case lastSeenAt = "last_seen_at"
        case nowPlaying = "now_playing"
        case wifi
        case mpdPorts = "mpd_ports"
        case volume, voice, version
        case fullDuplex = "full_duplex"
        case inCallWith = "in_call_with"
        case timersOwnOnly = "timers_own_only"
        case upgrade
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try c.decodeIfPresent(String.self, forKey: .roomID) ?? ""
        status = try c.decodeIfPresent(String.self, forKey: .status)
        lastConnectedAt = try c.decodeIfPresent(String.self, forKey: .lastConnectedAt)
        lastSeenAt = try c.decodeIfPresent(String.self, forKey: .lastSeenAt)
        nowPlaying = try c.decodeIfPresent(RoomsNowPlaying.self, forKey: .nowPlaying)
        wifi = try c.decodeIfPresent(RoomsWifi.self, forKey: .wifi)
        mpdPorts = try c.decodeIfPresent(RoomsMpdPorts.self, forKey: .mpdPorts)
        volume = try c.decodeIfPresent(Double.self, forKey: .volume)
        voice = try c.decodeIfPresent(String.self, forKey: .voice)
        version = try c.decodeIfPresent(String.self, forKey: .version)
        fullDuplex = try c.decodeIfPresent(Bool.self, forKey: .fullDuplex) ?? false
        inCallWith = try c.decodeIfPresent(String.self, forKey: .inCallWith)
        timersOwnOnly = try c.decodeIfPresent(Bool.self, forKey: .timersOwnOnly) ?? false
        upgrade = try c.decodeIfPresent(RoomsUpgrade.self, forKey: .upgrade)
    }
}

/// `SatSong` (`SatModels.kt:16`).
public struct RoomsSong: Equatable, Sendable, Decodable {

    public let title: String?
    public let artist: String?
    public let file: String?
    public let durationSec: Double?

    public init(title: String? = nil, artist: String? = nil, file: String? = nil, durationSec: Double? = nil) {
        self.title = title
        self.artist = artist
        self.file = file
        self.durationSec = durationSec
    }

    enum CodingKeys: String, CodingKey {
        case title, artist, file
        case durationSec = "duration_sec"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        artist = try c.decodeIfPresent(String.self, forKey: .artist)
        file = try c.decodeIfPresent(String.self, forKey: .file)
        durationSec = try c.decodeIfPresent(Double.self, forKey: .durationSec)
    }
}

/// `SatNowPlaying` (`SatModels.kt:23`).
///
/// ``state`` carries the whole stopped-player trap: a stopped room keeps its
/// last song loaded and this read still names it, so the card must test
/// `state == "play"` and not "is there a song" (``roomsPlaying(_:)``).
public struct RoomsNowPlaying: Equatable, Sendable, Decodable {

    public let state: String?
    public let song: RoomsSong?
    public let elapsedSec: Double?
    public let streamURL: String?

    public init(
        state: String? = nil,
        song: RoomsSong? = nil,
        elapsedSec: Double? = nil,
        streamURL: String? = nil
    ) {
        self.state = state
        self.song = song
        self.elapsedSec = elapsedSec
        self.streamURL = streamURL
    }

    enum CodingKeys: String, CodingKey {
        case state, song
        case elapsedSec = "elapsed_sec"
        case streamURL = "stream_url"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = try c.decodeIfPresent(String.self, forKey: .state)
        song = try c.decodeIfPresent(RoomsSong.self, forKey: .song)
        elapsedSec = try c.decodeIfPresent(Double.self, forKey: .elapsedSec)
        streamURL = try c.decodeIfPresent(String.self, forKey: .streamURL)
    }
}

/// `SatWifi` (`SatModels.kt:31`).
///
/// **Not ``HomeWifi``**, which the map expected to be reusable: it decodes
/// `rx_mbits`, `tx_mbits`, `signal_dbm` and `mode` and has **no `ssid`**
/// (`HomeModels.swift:212-245`), and the overview's Wi-Fi pair prints the
/// network's name under the signal (`SatOverviewTab.kt:130,146`). Adding a
/// field to `HomeWifi` is an edit to another item's file, so this decodes the
/// five keys the screen reads. Recorded in DECISIONS.
public struct RoomsWifi: Equatable, Sendable, Decodable {

    public let rxMbits: Double?
    public let txMbits: Double?
    public let ssid: String?

    /// The received signal at the satellite, in dBm; nil from one too old to
    /// report it (`SatModels.kt:35-37`).
    public let signalDbm: Double?

    /// `"ap"` while the radio hosts its own setup hotspot — which means it is
    /// **not** on the house Wi-Fi.
    public let mode: String?

    public init(
        rxMbits: Double? = nil,
        txMbits: Double? = nil,
        ssid: String? = nil,
        signalDbm: Double? = nil,
        mode: String? = nil
    ) {
        self.rxMbits = rxMbits
        self.txMbits = txMbits
        self.ssid = ssid
        self.signalDbm = signalDbm
        self.mode = mode
    }

    enum CodingKeys: String, CodingKey {
        case rxMbits = "rx_mbits"
        case txMbits = "tx_mbits"
        case ssid
        case signalDbm = "signal_dbm"
        case mode
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rxMbits = try c.decodeIfPresent(Double.self, forKey: .rxMbits)
        txMbits = try c.decodeIfPresent(Double.self, forKey: .txMbits)
        ssid = try c.decodeIfPresent(String.self, forKey: .ssid)
        signalDbm = try c.decodeIfPresent(Double.self, forKey: .signalDbm)
        mode = try c.decodeIfPresent(String.self, forKey: .mode)
    }
}

/// `SatMpdPorts` (`SatModels.kt:111`).
public struct RoomsMpdPorts: Equatable, Sendable, Decodable {

    public let control: Int?
    public let http: Int?

    public init(control: Int? = nil, http: Int? = nil) {
        self.control = control
        self.http = http
    }

    enum CodingKeys: String, CodingKey { case control, http }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        control = try c.decodeIfPresent(Int.self, forKey: .control)
        http = try c.decodeIfPresent(Int.self, forKey: .http)
    }
}

/// `SatelliteUpgrade` (`schemas.py:481`) — **decode-only**.
///
/// Nothing here is drawn as a control: the upgrade route is admin security
/// tier (`satellites.py:1618-1623`), so the screen says where the button is
/// instead of offering one. ``state`` is read for one honest sentence: a room
/// that is mid-upgrade is offline on purpose.
public struct RoomsUpgrade: Equatable, Sendable, Decodable {

    public let state: String?
    public let expectedSha: String?
    public let fromSha: String?
    public let startedAt: String?
    public let finishedAt: String?

    public init(
        state: String? = nil,
        expectedSha: String? = nil,
        fromSha: String? = nil,
        startedAt: String? = nil,
        finishedAt: String? = nil
    ) {
        self.state = state
        self.expectedSha = expectedSha
        self.fromSha = fromSha
        self.startedAt = startedAt
        self.finishedAt = finishedAt
    }

    enum CodingKeys: String, CodingKey {
        case state
        case expectedSha = "expected_sha"
        case fromSha = "from_sha"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = try c.decodeIfPresent(String.self, forKey: .state)
        expectedSha = try c.decodeIfPresent(String.self, forKey: .expectedSha)
        fromSha = try c.decodeIfPresent(String.self, forKey: .fromSha)
        startedAt = try c.decodeIfPresent(String.self, forKey: .startedAt)
        finishedAt = try c.decodeIfPresent(String.self, forKey: .finishedAt)
    }

    /// A sync or a restart is under way (`SatUpgrade.kt:88`'s `running`
    /// states, which are the core's own words).
    public var running: Bool { state == "running" || state == "requested" }
}

// MARK: - the history tabs

/// `SatSession` (`SatModels.kt:145`; `schemas.py:403`).
public struct RoomsSession: Equatable, Sendable, Decodable, Identifiable {

    public let id: String?
    public let startedAt: String?
    public let lastActivity: String?
    public let intentCount: Int
    public let personID: Int64?

    public init(
        id: String? = nil,
        startedAt: String? = nil,
        lastActivity: String? = nil,
        intentCount: Int = 0,
        personID: Int64? = nil
    ) {
        self.id = id
        self.startedAt = startedAt
        self.lastActivity = lastActivity
        self.intentCount = intentCount
        self.personID = personID
    }

    enum CodingKeys: String, CodingKey {
        case id
        case startedAt = "started_at"
        case lastActivity = "last_activity"
        case intentCount = "intent_count"
        case personID = "person_id"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id)
        startedAt = try c.decodeIfPresent(String.self, forKey: .startedAt)
        lastActivity = try c.decodeIfPresent(String.self, forKey: .lastActivity)
        intentCount = try c.decodeIfPresent(Int.self, forKey: .intentCount) ?? 0
        personID = try c.decodeIfPresent(Int64.self, forKey: .personID)
    }
}

/// `SatTurn` (`SatModels.kt:154`; `ConversationTurn`, `schemas.py:412`).
public struct RoomsTurn: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int64
    public let at: String?
    public let sessionID: String?
    public let matchedHandler: String?
    public let matchedPath: String?
    public let userText: String?
    public let assistantText: String?

    /// Where the turn got to (server V043, 2026-11-09; `SatModels.kt:160-167`):
    /// `answered`, `pending` (said, the reply still being worked out),
    /// `no_answer` (see ``noAnswerReason``) or `unheard` (speech recognition
    /// never made it out; `user_text` is `""` and ``audioMs`` is the
    /// recording's length). **An older server sends none of these, and its
    /// turn reads as answered** — nil here, and every rule in
    /// `RoomsTurnStatus.swift` takes nil as `answered`.
    public let status: String?

    /// For `no_answer` / `unheard`: `timed_out`, `error`, `interrupted`,
    /// `restarted`, `speech_off` or `overheard` (V044's list) — an open
    /// vocabulary, so an unknown word is a plain "no answer", never a failure
    /// to decode.
    public let noAnswerReason: String?

    /// The request's recording length, for a satellite's turn. Drawn only on
    /// an `unheard` turn (``roomsTurnSaid(_:)``), as Android does.
    public let audioMs: Int64?

    /// The wake word's peak score, 0...1 (V044). **Decoded and not drawn**:
    /// Android's `SatTurn` does not read it at all, and the phone shows only
    /// what Android shows.
    public let wakeScore: Double?

    public init(
        id: Int64 = 0,
        at: String? = nil,
        sessionID: String? = nil,
        matchedHandler: String? = nil,
        matchedPath: String? = nil,
        userText: String? = nil,
        assistantText: String? = nil,
        status: String? = nil,
        noAnswerReason: String? = nil,
        audioMs: Int64? = nil,
        wakeScore: Double? = nil
    ) {
        self.id = id
        self.at = at
        self.sessionID = sessionID
        self.matchedHandler = matchedHandler
        self.matchedPath = matchedPath
        self.userText = userText
        self.assistantText = assistantText
        self.status = status
        self.noAnswerReason = noAnswerReason
        self.audioMs = audioMs
        self.wakeScore = wakeScore
    }

    enum CodingKeys: String, CodingKey {
        case id, at
        case sessionID = "session_id"
        case matchedHandler = "matched_handler"
        case matchedPath = "matched_path"
        case userText = "user_text"
        case assistantText = "assistant_text"
        case status
        case noAnswerReason = "no_answer_reason"
        case audioMs = "audio_ms"
        case wakeScore = "wake_score"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        at = try c.decodeIfPresent(String.self, forKey: .at)
        sessionID = try c.decodeIfPresent(String.self, forKey: .sessionID)
        matchedHandler = try c.decodeIfPresent(String.self, forKey: .matchedHandler)
        matchedPath = try c.decodeIfPresent(String.self, forKey: .matchedPath)
        userText = try c.decodeIfPresent(String.self, forKey: .userText)
        assistantText = try c.decodeIfPresent(String.self, forKey: .assistantText)
        // The four V043/V044 fields are read **leniently**: a value of the
        // wrong type reads as absent rather than failing the whole list. The
        // list is a bare array, so one bad row would otherwise blank every
        // turn in the room — the opposite of what these fields are for.
        status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? nil
        noAnswerReason = (try? c.decodeIfPresent(String.self, forKey: .noAnswerReason)) ?? nil
        audioMs = (try? c.decodeIfPresent(Int64.self, forKey: .audioMs)) ?? nil
        wakeScore = (try? c.decodeIfPresent(Double.self, forKey: .wakeScore)) ?? nil
    }
}

/// `SatNote` (`SatModels.kt:165`; `VoiceNote`, `schemas.py:702`).
public struct RoomsNote: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int64
    public let capturedAt: String?
    public let body: String?

    public init(id: Int64 = 0, capturedAt: String? = nil, body: String? = nil) {
        self.id = id
        self.capturedAt = capturedAt
        self.body = body
    }

    enum CodingKeys: String, CodingKey {
        case id, body
        case capturedAt = "captured_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        capturedAt = try c.decodeIfPresent(String.self, forKey: .capturedAt)
        body = try c.decodeIfPresent(String.self, forKey: .body)
    }
}

/// `SatTimer` (`SatModels.kt:172`; `Timer`, `schemas.py:709`).
///
/// ``masked`` is rule M1: the server held a reminder's words back because the
/// caller presented no household token (`satellites.py:1090`). It is the one
/// field on this screen that is about privacy rather than layout, and
/// ``roomsTimerLabel(_:)`` is the only thing that may put a name on a row.
public struct RoomsTimer: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int64
    public let isReminder: Bool
    public let label: String?
    public let message: String?
    public let expiresAt: String?
    public let masked: Bool

    public init(
        id: Int64 = 0,
        isReminder: Bool = false,
        label: String? = nil,
        message: String? = nil,
        expiresAt: String? = nil,
        masked: Bool = false
    ) {
        self.id = id
        self.isReminder = isReminder
        self.label = label
        self.message = message
        self.expiresAt = expiresAt
        self.masked = masked
    }

    enum CodingKeys: String, CodingKey {
        case id, label, message, masked
        case isReminder = "is_reminder"
        case expiresAt = "expires_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        isReminder = try c.decodeIfPresent(Bool.self, forKey: .isReminder) ?? false
        label = try c.decodeIfPresent(String.self, forKey: .label)
        message = try c.decodeIfPresent(String.self, forKey: .message)
        expiresAt = try c.decodeIfPresent(String.self, forKey: .expiresAt)
        masked = try c.decodeIfPresent(Bool.self, forKey: .masked) ?? false
    }
}

/// `SatPlayed` (`SatModels.kt:191`; `RecentlyPlayed`, `schemas.py:428`).
///
/// ``canAdd`` is server-computed and does **not** promise the add will be
/// accepted — the core's gate is "an admin session OR a URL a registered
/// fulfiller's matcher allows" (`domovoi/main.py:5782-5788`) and this flag is
/// computed without consulting a fulfiller (`satellites.py:1035-1045`). So the
/// button is drawn (it does not fail *every* time, which is where
/// no-misleading-controls bites) and the server's own refusal text is what a
/// person is shown. DECISIONS asks Kamron whether the route should compute it
/// against the allowlist.
public struct RoomsPlayed: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int64
    public let startedAt: String?

    /// An open vocabulary: the core stamps `library` / `playlist` /
    /// `spoken_audio`, a plugin stamps its own slug (`SatModels.kt:193-195`).
    public let source: String?

    public let title: String?
    public let artist: String?
    public let channel: String?
    public let url: String?
    public let externalID: String?
    public let inLibrary: Bool
    public let canAdd: Bool

    public init(
        id: Int64 = 0,
        startedAt: String? = nil,
        source: String? = nil,
        title: String? = nil,
        artist: String? = nil,
        channel: String? = nil,
        url: String? = nil,
        externalID: String? = nil,
        inLibrary: Bool = false,
        canAdd: Bool = false
    ) {
        self.id = id
        self.startedAt = startedAt
        self.source = source
        self.title = title
        self.artist = artist
        self.channel = channel
        self.url = url
        self.externalID = externalID
        self.inLibrary = inLibrary
        self.canAdd = canAdd
    }

    enum CodingKeys: String, CodingKey {
        case id, source, title, artist, channel, url
        case startedAt = "started_at"
        case externalID = "external_id"
        case inLibrary = "in_library"
        case canAdd = "can_add"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        startedAt = try c.decodeIfPresent(String.self, forKey: .startedAt)
        source = try c.decodeIfPresent(String.self, forKey: .source)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        artist = try c.decodeIfPresent(String.self, forKey: .artist)
        channel = try c.decodeIfPresent(String.self, forKey: .channel)
        url = try c.decodeIfPresent(String.self, forKey: .url)
        externalID = try c.decodeIfPresent(String.self, forKey: .externalID)
        inLibrary = try c.decodeIfPresent(Bool.self, forKey: .inLibrary) ?? false
        canAdd = try c.decodeIfPresent(Bool.self, forKey: .canAdd) ?? false
    }
}

// MARK: - the settings tab

/// `SatConfigField` (`SatModels.kt:206`) — **read-only here**.
///
/// The GET is device read and the PATCH is admin (`satellites.py:1639`,
/// `:1712`), so every field decodes and none is editable: see
/// ``RoomsStrings/configIsOnTheDashboard``. ``value`` is ``LooseJSON`` for the
/// reason `HomeModels.swift:41` gives — the server does not fix its type, and
/// a `String?` would throw away the whole config read the first time a
/// boolean arrived.
public struct RoomsConfigField: Equatable, Sendable, Decodable, Identifiable {

    public let name: String
    public let label: String?
    public let group: String?
    public let section: String?
    public let tier: String?
    public let type: String?
    public let min: Double?
    public let max: Double?
    public let choices: [String]?
    public let choiceLabels: [String: String]?
    public let unit: String?
    public let help: String?
    public let value: LooseJSON?

    public var id: String { name }

    public init(
        name: String = "",
        label: String? = nil,
        group: String? = nil,
        section: String? = nil,
        tier: String? = nil,
        type: String? = nil,
        min: Double? = nil,
        max: Double? = nil,
        choices: [String]? = nil,
        choiceLabels: [String: String]? = nil,
        unit: String? = nil,
        help: String? = nil,
        value: LooseJSON? = nil
    ) {
        self.name = name
        self.label = label
        self.group = group
        self.section = section
        self.tier = tier
        self.type = type
        self.min = min
        self.max = max
        self.choices = choices
        self.choiceLabels = choiceLabels
        self.unit = unit
        self.help = help
        self.value = value
    }

    enum CodingKeys: String, CodingKey {
        case name, label, group, section, tier, type, min, max, choices, unit, help, value
        case choiceLabels = "choice_labels"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        label = try c.decodeIfPresent(String.self, forKey: .label)
        group = try c.decodeIfPresent(String.self, forKey: .group)
        section = try c.decodeIfPresent(String.self, forKey: .section)
        tier = try c.decodeIfPresent(String.self, forKey: .tier)
        type = try c.decodeIfPresent(String.self, forKey: .type)
        min = try c.decodeIfPresent(Double.self, forKey: .min)
        max = try c.decodeIfPresent(Double.self, forKey: .max)
        choices = try c.decodeIfPresent([String].self, forKey: .choices)
        choiceLabels = try c.decodeIfPresent([String: String].self, forKey: .choiceLabels)
        unit = try c.decodeIfPresent(String.self, forKey: .unit)
        help = try c.decodeIfPresent(String.self, forKey: .help)
        value = try c.decodeIfPresent(LooseJSON.self, forKey: .value)
    }
}

/// `SatConfigResponse` (`SatModels.kt:224`).
public struct RoomsConfigResponse: Equatable, Sendable, Decodable {

    /// Whether the room has reported its config at all. `false` with no
    /// fields is the offline room, and it has its own sentence.
    public let reported: Bool
    public let fields: [RoomsConfigField]

    public init(reported: Bool = false, fields: [RoomsConfigField] = []) {
        self.reported = reported
        self.fields = fields
    }

    enum CodingKeys: String, CodingKey { case reported, fields }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        reported = try c.decodeIfPresent(Bool.self, forKey: .reported) ?? false
        fields = try c.decodeIfPresent([RoomsConfigField].self, forKey: .fields) ?? []
    }
}

/// `RoomQuietHours` (`SatQuietHours.kt:52`; `schemas.py:678`) — the one
/// writable thing on the settings tab (`satellites.py:1425-1432`, device).
public struct RoomsQuietHours: Equatable, Sendable, Decodable {

    public let roomID: String
    public let mode: String
    public let start: String?
    public let end: String?

    public init(roomID: String = "", mode: String = "house", start: String? = nil, end: String? = nil) {
        self.roomID = roomID
        self.mode = mode
        self.start = start
        self.end = end
    }

    enum CodingKeys: String, CodingKey {
        case roomID = "room_id"
        case mode, start, end
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try c.decodeIfPresent(String.self, forKey: .roomID) ?? ""
        mode = try c.decodeIfPresent(String.self, forKey: .mode) ?? "house"
        start = try c.decodeIfPresent(String.self, forKey: .start)
        end = try c.decodeIfPresent(String.self, forKey: .end)
    }
}

/// `TimerAnnouncements` (`schemas.py:806`) — the GET behind the
/// "Only reminders for this device" switch.
///
/// The switch reads the **roster**'s `timers_own_only` while the screen is
/// open (that is what Android does, `SatOverviewTab.kt:404`), so this shape is
/// here for the one thing the roster cannot say: what the server answered
/// after a PUT, for a room whose roster row has not caught up yet.
public struct RoomsTimerAnnouncements: Equatable, Sendable, Decodable {

    public let roomID: String
    public let ownOnly: Bool
    public let since: String?

    public init(roomID: String = "", ownOnly: Bool = false, since: String? = nil) {
        self.roomID = roomID
        self.ownOnly = ownOnly
        self.since = since
    }

    enum CodingKeys: String, CodingKey {
        case roomID = "room_id"
        case ownOnly = "own_only"
        case since
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try c.decodeIfPresent(String.self, forKey: .roomID) ?? ""
        ownOnly = try c.decodeIfPresent(Bool.self, forKey: .ownOnly) ?? false
        since = try c.decodeIfPresent(String.self, forKey: .since)
    }
}

// MARK: - the health document

/// `SatNetStep` (`SatModels.kt:44`): what root's boot-time network step did on
/// this boot.
///
/// ``version`` is `Int64` and not `Int` for the Kotlin's own reason, kept
/// verbatim because it is a decoding decision: "one out-of-range number must
/// never fail the decoding of the whole health document".
public struct RoomsNetStep: Equatable, Sendable, Decodable {

    public let state: String?
    public let detail: String?
    public let when: String?
    public let at: Double?
    public let version: Int64?

    /// What the unit that sets the host name before the network stands did,
    /// when there is something to say (`SatModels.kt:55-57`).
    public let early: String?

    public init(
        state: String? = nil,
        detail: String? = nil,
        when: String? = nil,
        at: Double? = nil,
        version: Int64? = nil,
        early: String? = nil
    ) {
        self.state = state
        self.detail = detail
        self.when = when
        self.at = at
        self.version = version
        self.early = early
    }

    enum CodingKeys: String, CodingKey {
        case state, detail, when, at, version, early
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = try c.decodeIfPresent(String.self, forKey: .state)
        detail = try c.decodeIfPresent(String.self, forKey: .detail)
        when = try c.decodeIfPresent(String.self, forKey: .when)
        at = try c.decodeIfPresent(Double.self, forKey: .at)
        version = try c.decodeIfPresent(Int64.self, forKey: .version)
        early = try c.decodeIfPresent(String.self, forKey: .early)
    }
}

/// `SatHealthSample` (`SatModels.kt:68`) — the slice of the health document
/// this screen reads.
public struct RoomsHealthSample: Equatable, Sendable, Decodable {

    public let rebootNeeded: String?
    public let netStep: RoomsNetStep?
    public let hostname: String?

    public init(rebootNeeded: String? = nil, netStep: RoomsNetStep? = nil, hostname: String? = nil) {
        self.rebootNeeded = rebootNeeded
        self.netStep = netStep
        self.hostname = hostname
    }

    enum CodingKeys: String, CodingKey {
        case rebootNeeded = "reboot_needed"
        case netStep = "net_step"
        case hostname
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rebootNeeded = try c.decodeIfPresent(String.self, forKey: .rebootNeeded)
        netStep = try c.decodeIfPresent(RoomsNetStep.self, forKey: .netStep)
        hostname = try c.decodeIfPresent(String.self, forKey: .hostname)
    }
}

/// `SatHealthLatest` (`SatModels.kt:72`).
public struct RoomsHealthLatest: Equatable, Sendable, Decodable {

    public let sample: RoomsHealthSample?

    public init(sample: RoomsHealthSample? = nil) { self.sample = sample }

    enum CodingKeys: String, CodingKey { case sample }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sample = try c.decodeIfPresent(RoomsHealthSample.self, forKey: .sample)
    }
}

/// `SatHealthDoc` (`SatModels.kt:75`). `{"latest": null}` is a room that has
/// never reported one, and every rule over it reads that as "nothing to say".
public struct RoomsHealthDoc: Equatable, Sendable, Decodable {

    public let latest: RoomsHealthLatest?

    public init(latest: RoomsHealthLatest? = nil) { self.latest = latest }

    enum CodingKeys: String, CodingKey { case latest }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        latest = try c.decodeIfPresent(RoomsHealthLatest.self, forKey: .latest)
    }
}

// MARK: - the two small answers

/// `VersionInfo` (`SatModels.kt:231`) — `GET /api/config/version`, the
/// server's own git SHA (`config.py:258`, device read).
public struct RoomsVersionInfo: Equatable, Sendable, Decodable {

    public let sha: String?

    public init(sha: String? = nil) { self.sha = sha }

    enum CodingKeys: String, CodingKey { case sha }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sha = try c.decodeIfPresent(String.self, forKey: .sha)
    }
}

/// `AddByUrlResult` (`SatModels.kt:234`) — `POST /api/music/add-by-url`.
///
/// ``message`` is the acquisition queue's graceful-absence copy ("no fulfiller
/// plugin is installed"), and it is shown **as the server wrote it**: the one
/// place this screen prefers the server's words to its own.
public struct RoomsAddByUrlResult: Equatable, Sendable, Decodable {

    public let queued: Bool
    public let alreadyInLibrary: Bool
    public let alreadyDownloading: Bool
    public let message: String?

    public init(
        queued: Bool = false,
        alreadyInLibrary: Bool = false,
        alreadyDownloading: Bool = false,
        message: String? = nil
    ) {
        self.queued = queued
        self.alreadyInLibrary = alreadyInLibrary
        self.alreadyDownloading = alreadyDownloading
        self.message = message
    }

    enum CodingKeys: String, CodingKey {
        case queued, message
        case alreadyInLibrary = "already_in_library"
        case alreadyDownloading = "already_downloading"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        queued = try c.decodeIfPresent(Bool.self, forKey: .queued) ?? false
        alreadyInLibrary = try c.decodeIfPresent(Bool.self, forKey: .alreadyInLibrary) ?? false
        alreadyDownloading = try c.decodeIfPresent(Bool.self, forKey: .alreadyDownloading) ?? false
        message = try c.decodeIfPresent(String.self, forKey: .message)
    }
}
