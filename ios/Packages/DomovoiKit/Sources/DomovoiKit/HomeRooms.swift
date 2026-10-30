import Foundation

// ---------------------------------------------------------------------------
// Home's rooms section, as rules — the port of `HomeModels.kt:421-512` and
// `HomeIo.kt:128` (read at `6859b1f7`), map §3.4 and its strings §6.5.
//
// Three of the rules here exist because of specific bugs, and each says so
// where it is: ``roomSong(_:)`` (a stopped room looked like it was playing at
// 0:00), ``roomElapsedSec(_:stale:sinceReadSec:)`` (`0:14 / 0:13`) and
// ``weakWifi(_:)`` (every quiet room read weak, because an idle link reports
// the beacon rate). They are the ones to leave alone.
// ---------------------------------------------------------------------------

/// `HOME_STOP_ALL_ARM_MS` (`HomeModels.kt:34`).
public let homeStopAllArmMs: Int64 = 4_000

// MARK: - the Wi-Fi push

/// A `satellites.wifi.changed` payload, decoded, with the instant it arrived
/// (`wifiPush`, `HomeScreen.kt:168`, a `Pair(now, map)`).
///
/// The timestamp is not decoration: the push is merged onto the **last read**,
/// and a push that predates that read describes a house that has since been
/// re-read. See ``applies(toReadAt:)``.
public struct WifiPush: Equatable, Sendable {

    /// When this phone received it, on the phone's clock — the same clock
    /// ``ApiRead/dataAt`` is stamped on, which is the only reason the
    /// comparison in ``applies(toReadAt:)`` means anything.
    public let receivedAtMs: Int64

    /// Room id → its new link. Rooms the push does not name are not in it.
    public let rooms: [String: HomeWifi]

    public init(receivedAtMs: Int64, rooms: [String: HomeWifi]) {
        self.receivedAtMs = receivedAtMs
        self.rooms = rooms
    }

    /// `wifiPush?.takeIf { it.first >= satsAt }` (`HomeScreen.kt:227`).
    ///
    /// **A push older than the last read is discarded**, and that is a rule
    /// rather than an optimisation: the read is the complete truth as of its
    /// own instant, so an older push can only put back a link the read has
    /// already superseded. `>=` and not `>`: a push and a read landing in the
    /// same millisecond let the push win, which is the Kotlin's choice and is
    /// the right way round — the push is the later event in the house even
    /// when the clock cannot tell them apart.
    public func applies(toReadAt roomsReadAtMs: Int64) -> Bool {
        receivedAtMs >= roomsReadAtMs
    }
}

/// `decodeWifiPush(payload)` (`HomeIo.kt:128`).
///
/// "The push is advisory; a bad one changes nothing." So:
///
/// - **nil** when the frame's `data` is absent or is not an object at all —
///   the caller then merges nothing and keeps the read;
/// - a value that is not an object, or will not decode as a link, is
///   **skipped**, leaving the rest of the push in force.
///
/// Both come out of the decoding rather than being checked for: a keyed
/// container over a non-object throws, and `try?` on one room's value skips
/// exactly that room. A `null`, a number, a string and an array all fail to
/// decode as ``HomeWifi`` and are dropped, as Kotlin's `v as? JsonObject`
/// drops them; `{}` decodes to a link with four nils, as Kotlin's does, and
/// that is a report that says nothing rather than a bad one.
public func decodeWifiPush(_ event: StateEvent) -> [String: HomeWifi]? {
    event.data(as: LenientWifiMap.self)?.rooms
}

extension HomeRoom {

    /// Kotlin's `r.copy(wifi = …)`, which is the only copy the rooms rules
    /// need. Here rather than in `HomeModels.swift` so the wire layer stays a
    /// wire layer: it decodes, and nothing in it rewrites a room.
    func with(wifi: HomeWifi?) -> HomeRoom {
        HomeRoom(
            roomID: roomID,
            status: status,
            lastConnectedAt: lastConnectedAt,
            nowPlaying: nowPlaying,
            wifi: wifi,
            inCallWith: inCallWith,
            satType: satType,
            roomLabel: roomLabel,
            display: display
        )
    }
}

/// The per-room skipping, as a `Decodable` so `JSONDecoder` does the work.
struct LenientWifiMap: Decodable {

    let rooms: [String: HomeWifi]

    private struct AnyKey: CodingKey {
        let stringValue: String
        var intValue: Int? { nil }
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { nil }
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: AnyKey.self)
        var out: [String: HomeWifi] = [:]
        for key in container.allKeys {
            guard let wifi = try? container.decode(HomeWifi.self, forKey: key) else { continue }
            out[key.stringValue] = wifi
        }
        rooms = out
    }
}

/// `mergeWifi(rooms, push)` (`HomeModels.kt:457`).
///
/// **It copies four fields, nulls included**: `rx_mbits`, `tx_mbits`,
/// `signal_dbm` and `mode`. The Kotlin's own doc says "Nothing else is taken
/// from it" and its test is called `aWifiPushUpdatesOnlyRxAndTx`; both are
/// wrong about the code they stand in front of (map §11.3), and the test
/// passes only because its fixture happens to have nil in the other two. The
/// behaviour is ported; the false claim is not.
///
/// The consequence the comment hides is worth stating, because it is the
/// thing a reader would otherwise have to work out: a push that omits
/// `signal_dbm` **erases** a signal the read reported, which moves
/// ``weakWifi(_:)`` from its signal rule to its rate rule, and the rate rule
/// reads an idle link's 1 Mbit/s beacon rate as weak. In practice the same
/// satellite produces both the read and the push, so a satellite new enough
/// to report a signal reports it in the push too and the window is narrow —
/// but it is a window, not an impossibility, and `aPushWithoutASignalErases`
/// is the test that says which way it goes.
///
/// Rooms the push does not name are returned untouched, and a room with no
/// `wifi` at all gets one built from the push.
public func mergeWifi(_ rooms: [HomeRoom], _ push: [String: HomeWifi]?) -> [HomeRoom] {
    guard let push, !push.isEmpty else { return rooms }
    return rooms.map { room in
        guard let w = push[room.roomID] else { return room }
        return room.with(wifi: HomeWifi(
            rxMbits: w.rxMbits,
            txMbits: w.txMbits,
            signalDbm: w.signalDbm,
            mode: w.mode
        ))
    }
}

/// The rooms Home draws: the read, with the Wi-Fi push merged in **when the
/// push is newer than the read** (`HomeScreen.kt:227-228`).
///
/// One call rather than two, for the same reason ``TimerBook/timerView(of:serverNowMs:)``
/// is: the discard is a rule, and a rule left for a view to apply is a rule a
/// view can forget. `nil` from the read becomes `[]` here, as Kotlin's
/// `orEmpty()` does — but note that the nil **is** kept where it matters, in
/// ``statusLine(rooms:coreDown:active:)``, which is handed the optional
/// separately; map §11.2 is the bug that comes from flattening it everywhere.
public func homeRooms(read: [HomeRoom]?, readAtMs: Int64, push: WifiPush?) -> [HomeRoom] {
    let inForce = push.flatMap { $0.applies(toReadAt: readAtMs) ? $0.rooms : nil }
    return mergeWifi(read ?? [], inForce)
}

// MARK: - order and grouping

/// `roomRank(r)` (`HomeModels.kt:428`): 0 playing, 1 paused, 2 online and
/// quiet, 3 waiting, 4 offline.
///
/// The playing and paused ranks need a **song** as well as a state, which is
/// the same rule ``roomSong(_:)`` states: a stopped room whose player still
/// names a track does not sort as if it were playing one.
public func roomRank(_ room: HomeRoom) -> Int {
    let np = room.nowPlaying
    let hasSong = np?.song != nil
    if room.online && hasSong && np?.state == "play" { return 0 }
    if room.online && hasSong && np?.state == "pause" { return 1 }
    if room.online { return 2 }
    if room.status == "waiting" { return 3 }
    return 4
}

/// `sortRooms` (`HomeModels.kt:439`) — by rank, then by `room_id`.
///
/// The second key is what makes the order stable between reads: rank alone
/// would let two quiet rooms swap places every time the server answered in a
/// different order.
public func sortRooms(_ rooms: [HomeRoom]) -> [HomeRoom] {
    rooms.sorted { left, right in
        let a = roomRank(left), b = roomRank(right)
        return a == b ? left.roomID < right.roomID : a < b
    }
}

/// One group of the rooms list (`RoomGroup`, `HomeModels.kt:442`).
public struct RoomGroup: Equatable, Sendable {

    /// The physical-room label, `ungrouped` for the loose ones — or **nil**,
    /// which means "draw no label at all".
    public let label: String?
    public let rooms: [HomeRoom]

    public init(label: String?, rooms: [HomeRoom]) {
        self.label = label
        self.rooms = rooms
    }
}

/// `groupRooms(sorted)` (`HomeModels.kt:447`): one group per distinct
/// non-empty `room_label`, labels A→Z, the unlabelled last as `ungrouped`.
///
/// **A house where nobody set a label gets one group with a nil label and no
/// heading**, which is not the same as one group called `ungrouped`: a single
/// heading reading `ungrouped` over every room in the house would be a label
/// that tells a person nothing and implies there are others.
///
/// `sorted()` on the labels is Kotlin's natural `String` order — UTF-16 code
/// units, so capitals sort before lower case. Swift's `<` on `String` is
/// Unicode-canonical ordering, which agrees for the ASCII labels a person
/// types and can differ for combining marks; the divergence is recorded
/// rather than worked around, because matching Kotlin's would mean comparing
/// UTF-16 views and no label in any house has needed it.
public func groupRooms(_ sorted: [HomeRoom]) -> [RoomGroup] {
    var labels: [String] = []
    for room in sorted {
        guard let label = room.roomLabel, !label.isEmpty else { continue }
        if !labels.contains(label) { labels.append(label) }
    }
    guard !labels.isEmpty else { return [RoomGroup(label: nil, rooms: sorted)] }
    var out = labels.sorted().map { label in
        RoomGroup(label: label, rooms: sorted.filter { $0.roomLabel == label })
    }
    let loose = sorted.filter { ($0.roomLabel ?? "").isEmpty }
    if !loose.isEmpty { out.append(RoomGroup(label: HomeRoomStrings.ungrouped, rooms: loose)) }
    return out
}

// MARK: - what a room is playing

/// `roomSong(r)` (`HomeModels.kt:421`) — a song **only** for an online room
/// whose state is `play` or `pause`.
///
/// **This is the 2026-10-11 bug, and the rule is the fix.** A *stopped*
/// room's player keeps the last song loaded and `/api/satellites` still names
/// it, with `state: "stop"` beside it. Reading the song without reading the
/// state made a room nobody was listening to show that track at `0:00` on
/// Home while Music and Satellites both said nothing — the same read will
/// arrive on iOS, so the same rule has to be here.
///
/// Offline is excluded for the same reason one step further on: a room that
/// is not there is not playing whatever it was playing when it left.
public func roomSong(_ room: HomeRoom) -> HomeSong? {
    guard let np = room.nowPlaying, room.online, let song = np.song else { return nil }
    return (np.state == "play" || np.state == "pause") ? song : nil
}

/// `roomElapsedSec(r, stale, sinceReadSec)` (`HomeModels.kt:471`): the read's
/// `elapsed_sec`, plus the seconds since the read **only while playing and
/// not stale**, clamped to the song's duration.
///
/// Two rules, each from an observed wrongness:
///
/// - **only while playing**: a paused room's position does not advance, and a
///   stale read (the core is down) is a reading of where the song *was*, so
///   running the clock on either invents a position the house never reported.
/// - **the clamp**: between a song ending and the read that brings the next
///   one, the count used to run past the end and draw `0:14 / 0:13`. The
///   clamp is why that stopped. It applies only when the duration is
///   positive, because a stream with no duration has no end to clamp to.
///
/// `sinceReadSec` is the caller's — `((nowMs - satsAt) / 1000.0)` floored at
/// zero (`HomeScreen.kt:424`) — and is a parameter for the reason `relTime`'s
/// `nowMs` is: a function that reads the wall clock cannot be tested at the
/// boundary it is about.
public func roomElapsedSec(_ room: HomeRoom, stale: Bool, sinceReadSec: Double) -> Double {
    guard let np = room.nowPlaying, let song = roomSong(room) else { return 0 }
    let playing = np.state == "play"
    let ran = (np.elapsedSec ?? 0) + ((playing && !stale) ? sinceReadSec : 0)
    let duration = song.durationSec ?? 0
    return duration > 0 ? min(ran, duration) : ran
}

/// `roomProgress(r, elapsedSec)` (`HomeModels.kt:481`) — the 3 dp bar, 0…1.
///
/// Note it reads the duration off `now_playing.song` directly rather than
/// through ``roomSong(_:)``, exactly as the Kotlin does. For a stopped room
/// that is harmless, because the elapsed it is given is already zero.
public func roomProgress(_ room: HomeRoom, elapsedSec: Double) -> Double {
    let duration = room.nowPlaying?.song?.durationSec ?? 0
    return duration > 0 ? min(1, elapsedSec / duration) : 0
}

/// `songTitle(song)` (`HomeModels.kt:486`): the title, else the file's last
/// path component, else the literal `unknown`.
public func songTitle(_ song: HomeSong?) -> String {
    if let title = song?.title, !title.isEmpty { return title }
    if let file = song?.file {
        let last = String(file.split(separator: "/").last ?? "")
        if !last.isEmpty { return last }
    }
    return HomeRoomStrings.unknownSong
}

/// The song line — `<title> · <artist>`, or the title alone when the read
/// names no artist (`HomeSections.kt:746`). **U+00B7, one space each side.**
public func songLine(_ song: HomeSong?) -> String {
    let title = songTitle(song)
    guard let artist = song?.artist, !artist.isEmpty else { return title }
    return title + HomeRoomStrings.middot + artist
}

/// `weakWifi(r)` (`HomeModels.kt:495`).
///
/// The order is the rule, and getting it wrong marks every quiet room in the
/// house:
///
/// 1. no Wi-Fi report, or the room is offline → **false**. An offline room's
///    last link is not news.
/// 2. `mode == "ap"` → **true**. The radio is hosting the setup hotspot, so
///    it is not on the house network at all.
/// 3. a reported `signal_dbm` decides **alone**, below −70 dBm — and
///    *decides*, meaning a room that reports a good signal is not weak and
///    the rate is never consulted.
/// 4. only with no signal at all does the rate decide, under 5 Mbit/s.
///
/// Step 3's exclusivity is the whole point: "an idle link reads the 1 Mbit/s
/// beacon rate, so a signal-reporting room is never weak for its rate alone".
/// A rule that took the worse of the two, or that checked the rate first,
/// would put `weak wi-fi` on every room in a quiet house.
public func weakWifi(_ room: HomeRoom) -> Bool {
    guard let wifi = room.wifi, room.online else { return false }
    if wifi.mode == "ap" { return true }
    if let signal = wifi.signalDbm { return signal < -70 }
    guard let rx = wifi.rxMbits else { return false }
    return rx < 5
}

// MARK: - the row, as values

/// The one pill a room row carries, **first match wins** (map §3.4,
/// `HomeSections.kt:731-737`).
///
/// A value and not a string built in a view, so the "first match wins" is in
/// one place and `swift test` can see it. There are five cases and a sixth
/// possibility that is **nil**: an online quiet room gets no pill at all, and
/// a sixth case reading `online` or `quiet` would put a badge on every
/// idle room in the house.
public enum RoomPill: Equatable, Sendable, CaseIterable {

    /// The core is down: what the server said last, not what is true now.
    case lastKnown
    case playing
    case paused
    case waiting
    case offline

    public var label: String {
        switch self {
        case .lastKnown: return HomeRoomStrings.lastKnown
        case .playing: return HomeRoomStrings.playing
        case .paused: return HomeRoomStrings.paused
        case .waiting: return HomeRoomStrings.waiting
        case .offline: return HomeRoomStrings.offline
        }
    }

    public var tone: HealthTone {
        switch self {
        case .lastKnown, .paused, .offline: return .idle
        case .playing: return .brand
        case .waiting: return .warn
        }
    }

    /// Only `playing` pulses (`live = true`, `HomeSections.kt:733`).
    public var live: Bool { self == .playing }
}

/// `roomPill(r, stale)` — the `when` block at `HomeSections.kt:731`.
///
/// `stale` is the screen's `coreDown` (`HomeScreen.kt:423`), and it is tested
/// **first**, before playing: with the core away, a room that was playing a
/// minute ago is reported as `last known` rather than as `playing`, because
/// nothing has confirmed it since.
public func roomPill(_ room: HomeRoom, stale: Bool) -> RoomPill? {
    if stale { return .lastKnown }
    let song = roomSong(room)
    if song != nil && room.nowPlaying?.state == "play" { return .playing }
    if song != nil && room.nowPlaying?.state == "pause" { return .paused }
    if room.status == "waiting" { return .waiting }
    if !room.online { return .offline }
    return nil
}

/// A room row's second line (`HomeSections.kt:742-771`).
///
/// ``lastSeen(_:)`` carries the **ISO timestamp**, not a rendered
/// `relTime`: the words change every minute against a clock, so rendering
/// them here would freeze them at whatever second this value was built.
public enum RoomSecondLine: Equatable, Sendable {
    case song(HomeSong)
    case quiet
    case notConnectedYet
    case lastSeen(String)
    case neverConnected

    /// The static text, for every case but the song and the time.
    public var text: String? {
        switch self {
        case .song: return nil
        case .quiet: return HomeRoomStrings.quiet
        case .notConnectedYet: return HomeRoomStrings.notConnectedYet
        case .lastSeen: return nil
        case .neverConnected: return HomeRoomStrings.neverConnected
        }
    }
}

/// `roomSecondLine(r)` — the song if there is one, else the four words.
///
/// The branch order is the Kotlin's: `online` → `quiet` comes **before** the
/// waiting and last-seen branches, so a room that is online now reads `quiet`
/// whatever its `status` field or its last-connected time says.
public func roomSecondLine(_ room: HomeRoom) -> RoomSecondLine {
    if let song = roomSong(room) { return .song(song) }
    if room.online { return .quiet }
    if room.status == "waiting" { return .notConnectedYet }
    if let at = room.lastConnectedAt { return .lastSeen(at) }
    return .neverConnected
}

/// `last seen <relTime>` (`HomeSections.kt:766`), rendered against a clock
/// the caller names.
public func lastSeenLine(_ iso: String?, nowMs: Int64) -> String {
    HomeRoomStrings.lastSeenPrefix + relTime(iso, nowMs: nowMs)
}

/// `in call with <who>` (`HomeSections.kt:778`), drawn only for an online
/// room (`online && r.in_call_with != null`).
public func inCallChip(_ room: HomeRoom) -> String? {
    guard room.online, let who = room.inCallWith else { return nil }
    return HomeRoomStrings.inCallWithPrefix + who
}

/// The accessibility label of a transport button (`HomeSections.kt:788-793`):
/// `pause kitchen`, `resume kitchen`, `stop kitchen`.
public func transportLabel(_ verb: String, room: String) -> String { "\(verb) \(room)" }

/// Which transport buttons a room row draws (`HomeSections.kt:784-795`).
///
/// **Only `online && !stale`, and nothing at all for a quiet room** —
/// "starting something in a room belongs to the Music page, which picks the
/// room and the track together". A play button here would have to invent what
/// to play.
public func roomTransport(_ room: HomeRoom, stale: Bool) -> [String] {
    guard room.online, !stale else { return [] }
    let song = roomSong(room)
    if song != nil && room.nowPlaying?.state == "play" { return ["pause", "stop"] }
    if song != nil && room.nowPlaying?.state == "pause" { return ["resume", "stop"] }
    return []
}

// MARK: - stop all

/// The rooms `stop all` would stop (`HomeSections.kt:563`).
///
/// Empty while the core is down, because the playing list is then what the
/// server said last — and the button is drawn at all only for **two or
/// more**, which is ``stopAllShown(candidates:stopping:)``.
public func stopAllCandidates(_ rooms: [HomeRoom], stale: Bool) -> [String] {
    stale ? [] : sortRooms(rooms).filter { roomRank($0) == 0 }.map(\.roomID)
}

/// `if (playing.size >= 2 || stoppingAll)` (`HomeSections.kt:575`).
public func stopAllShown(candidates: [String], stopping: Bool) -> Bool {
    candidates.count >= 2 || stopping
}

/// Two taps inside ``homeStopAllArmMs`` — `StopAllArm`
/// (`HomeModels.kt:712`).
///
/// "`stop all` touches other people's rooms", which is the whole reason it
/// arms rather than firing: the first tap asks, the second does it, and an
/// armed button left alone disarms itself.
///
/// Pure here as it is there, and a value rather than an object so the caller
/// cannot arm a copy. **Where it lives is 3b's decision**: on Android the arm
/// is `remember`ed inside the button, so scrolling the rooms section off a
/// phone disarms it — harmless, and an accident of Compose rather than a
/// decision, and the opposite of the announce field's deliberate hoisting
/// (map §4).
public struct StopAllArm: Equatable, Sendable {

    public let armedAtMs: Int64?

    public init(armedAtMs: Int64? = nil) {
        self.armedAtMs = armedAtMs
    }

    public func isArmed(nowMs: Int64) -> Bool {
        guard let armedAtMs else { return false }
        return nowMs - armedAtMs < homeStopAllArmMs
    }

    /// The next state, and whether **this** tap is the one that stops.
    public func tap(nowMs: Int64) -> (StopAllArm, Bool) {
        isArmed(nowMs: nowMs) ? (StopAllArm(), true) : (StopAllArm(armedAtMs: nowMs), false)
    }

    /// The button's words (`HomeSections.kt:654-658`).
    ///
    /// `stop $count rooms?` is **always plural**, including for one room. The
    /// button is drawn only for two or more today, so the singular is
    /// unreachable — but the guard lives in the caller and the count is
    /// passed in, so the string is one caller away from reading `stop 1
    /// rooms?`. Ported as it is, because `android/` is not this batch's to
    /// fix, and it is on the owner's list (map §11.5).
    public func label(count: Int, busy: Bool, nowMs: Int64) -> String {
        if busy { return HomeRoomStrings.stopping }
        if isArmed(nowMs: nowMs) { return "\(HomeRoomStrings.stopPrefix)\(count)\(HomeRoomStrings.roomsQuestionSuffix)" }
        return HomeRoomStrings.stopAll
    }
}

// MARK: - the strings

/// The rooms section's strings, verbatim from `ui/screens/home/`
/// (map §6.5). Pinned against the Kotlin on disk by `ShellStringsTests`.
public enum HomeRoomStrings {

    /// The section label — **the only section with no card title**
    /// (`HomeSections.kt:570`).
    public static let title = "rooms"

    /// `HomeSections.kt:657`.
    public static let stopAll = "stop all"

    /// `stop $count rooms?` in two pieces, because the count sits in the
    /// middle (`HomeSections.kt:656`).
    public static let stopPrefix = "stop "
    public static let roomsQuestionSuffix = " rooms?"

    /// `HomeSections.kt:655`. **U+2026.**
    public static let stopping = "stopping…"

    /// `HomeSections.kt:586`. **U+00B7**; ASCII apostrophe in `isn't`.
    public static let roomsUnavailableDBDown = "rooms unavailable · the database isn't answering"

    /// `HomeSections.kt:586`. ASCII apostrophe.
    public static let couldNotLoadRooms = "couldn't load rooms"

    /// The `EmptyState` title (`HomeSections.kt:589`), and the `rooms online`
    /// check's idle text.
    public static let noRoomsYet = "no rooms yet"

    /// …and its link (`HomeSections.kt:589`).
    public static let setUpARoom = "set up a room"

    /// The loose group's label (`HomeModels.kt:452`). Only ever drawn beside
    /// at least one real label — see ``groupRooms(_:)``.
    public static let ungrouped = "ungrouped"

    /// The five pills (`HomeSections.kt:732-736`).
    public static let lastKnown = "last known"
    public static let playing = "playing"
    public static let paused = "paused"
    public static let waiting = "waiting"
    public static let offline = "offline"

    /// The four second lines (`HomeSections.kt:764-767`).
    public static let quiet = "quiet"

    /// Also the `waiting` health check's label, which is why the two must not
    /// drift apart.
    public static let notConnectedYet = "set up, not connected yet"

    /// `last seen ` plus a ``relTime(_:nowMs:)`` (`HomeSections.kt:766`).
    public static let lastSeenPrefix = "last seen "

    public static let neverConnected = "never connected"

    /// The song-title fallback (`HomeModels.kt:488`).
    public static let unknownSong = "unknown"

    /// Between a song's title and its artist (`HomeSections.kt:746`).
    /// **U+00B7, one space each side.**
    public static let middot = " · "

    /// The warn pill for a video room whose screen went blank
    /// (`HomeSections.kt:779`).
    public static let screenStopped = "screen stopped"

    /// `HomeSections.kt:780` — **lower case, and a hyphen**, not `Wi-Fi`.
    public static let weakWifi = "weak wi-fi"

    /// `in call with ` plus the other end (`HomeSections.kt:778`).
    public static let inCallWithPrefix = "in call with "
}
