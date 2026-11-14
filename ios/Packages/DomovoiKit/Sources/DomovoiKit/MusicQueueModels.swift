import Foundation

/// The room's own queue — what the **speaker** plays from, not what this
/// phone holds.
///
/// `android/.../ui/screens/music/MusicModels.kt:88-120` and
/// `web/backend/api/music_queue.py`. The distinction the Android file's own
/// heading draws is the whole point: this is MPD's list, shared by every
/// client in the house, so an edit here lands for everybody, and two phones
/// looking at the same room see the same thing.
///
/// Every field is optional-with-a-default for ``NowPlayingSong``'s reason.

/// One entry. Addressed by ``songID`` everywhere, never by position.
///
/// `song_id` is MPD's songid: it survives a reorder, which is why a move or a
/// remove sends it instead of an index. An edit keyed by position is an edit
/// that removes the wrong track when somebody else reordered the queue a
/// second earlier, and in a house with four phones that is not a rare race.
public struct QueueItem: Equatable, Sendable, Decodable {
    public let songID: Int64
    public let pos: Int
    public let file: String
    public let title: String?
    public let artist: String?
    public let album: String?
    /// Whole seconds — the server types this one `int`, not the `float` the
    /// library rows carry.
    public let durationSec: Int?
    /// The device that queued it, or nil when there is no record: a voice
    /// command, a cast from before devices had names, or somebody poking MPD
    /// from outside Domovoi. **Nothing is drawn for a nil**, rather than a
    /// guess (the server's own comment says the same).
    public let addedBy: String?
    public let addedByDeviceID: String?
    public let addedAt: String?
    /// This is the entry coming out of the speaker now.
    public let playing: Bool

    /// Up next (P4): `auto` for a song the room's Up next picked, with why
    /// (``reasonLabel``: "same artist", "90s", "from road trip") and its
    /// number in the session (``upNextSeq``, for "less like this");
    /// `explicit` for one somebody asked for (`music_queue.py:220`).
    public let origin: String
    public let reasonLabel: String?
    public let upNextSeq: Int?
    /// The library song the entry is; nil for a stream, a podcast or a file
    /// the library does not know.
    public let trackID: Int64?

    public init(
        songID: Int64 = 0,
        pos: Int = 0,
        file: String = "",
        title: String? = nil,
        artist: String? = nil,
        album: String? = nil,
        durationSec: Int? = nil,
        addedBy: String? = nil,
        addedByDeviceID: String? = nil,
        addedAt: String? = nil,
        playing: Bool = false,
        origin: String = "explicit",
        reasonLabel: String? = nil,
        upNextSeq: Int? = nil,
        trackID: Int64? = nil
    ) {
        self.songID = songID
        self.pos = pos
        self.file = file
        self.title = title
        self.artist = artist
        self.album = album
        self.durationSec = durationSec
        self.addedBy = addedBy
        self.addedByDeviceID = addedByDeviceID
        self.addedAt = addedAt
        self.playing = playing
        self.origin = origin
        self.reasonLabel = reasonLabel
        self.upNextSeq = upNextSeq
        self.trackID = trackID
    }

    private enum CodingKeys: String, CodingKey {
        case pos, file, title, artist, album, playing, origin
        case reasonLabel = "reason_label"
        case upNextSeq = "up_next_seq"
        case trackID = "track_id"
        case songID = "song_id"
        case durationSec = "duration_sec"
        case addedBy = "added_by"
        case addedByDeviceID = "added_by_device_id"
        case addedAt = "added_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        songID = try values.decodeIfPresent(Int64.self, forKey: .songID) ?? 0
        pos = try values.decodeIfPresent(Int.self, forKey: .pos) ?? 0
        file = try values.decodeIfPresent(String.self, forKey: .file) ?? ""
        title = try values.decodeIfPresent(String.self, forKey: .title)
        artist = try values.decodeIfPresent(String.self, forKey: .artist)
        album = try values.decodeIfPresent(String.self, forKey: .album)
        durationSec = try values.decodeIfPresent(Int.self, forKey: .durationSec)
        addedBy = try values.decodeIfPresent(String.self, forKey: .addedBy)
        addedByDeviceID = try values.decodeIfPresent(String.self, forKey: .addedByDeviceID)
        // **Lenient, and not for symmetry.** The server declares this field
        // `added_at: Any = None` (music_queue.py:206), so its type is not
        // promised: today FastAPI renders the timestamp it holds as an ISO
        // string, and a day it holds an epoch number instead, a strict
        // `decodeIfPresent(String.self)` would throw — and the throw is not
        // on this one line, it is on the **whole queue**, so one odd
        // timestamp would empty the screen. An unreadable timestamp is worth
        // nothing, so it reads as nothing.
        addedAt = try? values.decodeIfPresent(String.self, forKey: .addedAt)
        playing = try values.decodeIfPresent(Bool.self, forKey: .playing) ?? false
        // The four Up next fields are read **leniently**, each on its own:
        // they are new (P4), and one of them in an unexpected shape must not
        // throw the whole queue away, which is `added_at`'s reason above. An
        // origin that is not `auto` is `explicit`, as the server writes it.
        origin = (try? values.decodeIfPresent(String.self, forKey: .origin)) == "auto" ? "auto" : "explicit"
        reasonLabel = try? values.decodeIfPresent(String.self, forKey: .reasonLabel)
        upNextSeq = try? values.decodeIfPresent(Int.self, forKey: .upNextSeq)
        trackID = try? values.decodeIfPresent(Int64.self, forKey: .trackID)
    }

    /// What a row calls it.
    ///
    /// **The basename, where Android shows the whole stored path.**
    /// `QueueTab.kt:360` is `item.title ?: item.file.ifBlank { "unknown" }`,
    /// and that is a defect rather than a choice: The owner's server is Windows,
    /// so an untagged entry reads `C:\Users\…\Music\Artist\Song.mp3` across a
    /// phone-width row that is one line with an ellipsis. The library rows on
    /// both apps already strip the path (``MusicFile/basename(_:)``), and a
    /// track does not change what it is called by being in a queue.
    public var displayTitle: String {
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return title
        }
        if let leaf = MusicFile.basename(file), !leaf.isEmpty { return leaf }
        return MusicStrings.unnamedTrack
    }

    /// `"The Beatles · 3:04 · added by Jordan's iPhone"`.
    ///
    /// One line with the parts that exist, in Android's order, and the "added
    /// by" tag **appended to it rather than given a line of its own** —
    /// `QueueTab.kt:366`, whose own comment says why: an absent record draws
    /// nothing at all.
    public var subtitle: String {
        let who = artist?.trimmingCharacters(in: .whitespacesAndNewlines)
        var out = (who?.isEmpty == false ? who : nil) ?? MusicStrings.unknownArtist
        if let durationSec { out += " · \(fmtDur(Double(durationSec)))" }
        if let addedBy, !addedBy.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            out += " · \(MusicStrings.addedBy(addedBy))"
        }
        return out
    }
}

/// `GET /api/music/queue/{room}` — one room's queue and what this phone may
/// do to it.
public struct RoomQueue: Equatable, Sendable, Decodable {
    public let roomID: String
    public let items: [QueueItem]
    public let currentSongID: Int64?

    /// Whether **this** device may edit this queue.
    ///
    /// The server re-checks every edit; this is here so the screen can draw
    /// its controls off it instead of discovering the 403 on the first tap
    /// (the server's own comment, and the owner's standing rule that a control
    /// which cannot do what it says is not drawn).
    public let editable: Bool

    /// Why not, when not — a sentence written by the server, shown as it
    /// stands.
    public let blockedReason: String?

    /// This answer was given to a **guest**: a phone with no household
    /// credential.
    ///
    /// It narrows what is possible beyond `editable`, which is why it is a
    /// second field and not a shade of the first. A guest may add, remove and
    /// move on a room that is **playing**, but may never clear the queue
    /// (`POST …/clear` is household-only) and may never remove the entry
    /// playing now (the core refuses that one, because the core is what knows
    /// which entry it is).
    ///
    /// **Android has no such field.** `MusicModels.kt:111-119` stops at
    /// `blockedReason`, so an unpaired Android phone is shown a "clear queue"
    /// button and a remove control on the playing row, taps one, and gets a
    /// refusal — the thing `editable` exists to prevent, for the one caller
    /// most likely to meet it. Logged as another defect the port found in the
    /// shipping app ([[android-defects-found-by-ios-port]]); not fixed there
    /// during the soak.
    public let guest: Bool

    /// The room's Up next session (P4), all nil without one: its id (for
    /// PATCH / feedback on `/api/music/up-next/sessions/{id}`), autoplay, and
    /// what the mix is built from (``RoomUpNext``).
    public let sessionID: String?
    public let autoplay: Bool?
    public let upNext: RoomUpNext?

    public init(
        roomID: String = "",
        items: [QueueItem] = [],
        currentSongID: Int64? = nil,
        editable: Bool = true,
        blockedReason: String? = nil,
        guest: Bool = false,
        sessionID: String? = nil,
        autoplay: Bool? = nil,
        upNext: RoomUpNext? = nil
    ) {
        self.roomID = roomID
        self.items = items
        self.currentSongID = currentSongID
        self.editable = editable
        self.blockedReason = blockedReason
        self.guest = guest
        self.sessionID = sessionID
        self.autoplay = autoplay
        self.upNext = upNext
    }

    private enum CodingKeys: String, CodingKey {
        case items, editable, guest, autoplay
        case sessionID = "session_id"
        case upNext = "up_next"
        case roomID = "room_id"
        case currentSongID = "current_song_id"
        case blockedReason = "blocked_reason"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try values.decodeIfPresent(String.self, forKey: .roomID) ?? ""
        items = try values.decodeIfPresent([QueueItem].self, forKey: .items) ?? []
        currentSongID = try values.decodeIfPresent(Int64.self, forKey: .currentSongID)
        // **Defaults to false, where the server defaults to true.** The
        // server knows whether this caller may edit; a client that could not
        // read the answer knows nothing, and the safe reading of nothing is
        // "do not offer the edit". `editable: true` by default would mean a
        // field the server stops sending turns every queue into an editable
        // one — a control that cannot do what it says, arrived at by a
        // default.
        editable = try values.decodeIfPresent(Bool.self, forKey: .editable) ?? false
        blockedReason = try values.decodeIfPresent(String.self, forKey: .blockedReason)
        // And **true** by default here, for the same direction: the narrower
        // of the two readings is the one an unreadable answer gets.
        //
        // The cost of both defaults, stated rather than left to be
        // discovered: against a server too old to send these fields, a
        // household phone loses its clear button and its reorder arrows and
        // is told nothing, because ``watchOnlyNote`` needs a reason the old
        // server did not send either. That is the price of the safe
        // direction, and it is the right way round — a control that is
        // missing is visible and recoverable on the dashboard, while a
        // control that answers with a refusal is neither. Both fields are
        // present in every answer the current `music_queue.py` writes
        // (pydantic defaults put them in the JSON whether or not they were
        // set), so this bites only a server older than the field.
        guest = try values.decodeIfPresent(Bool.self, forKey: .guest) ?? true
        // Lenient, for the reason ``QueueItem``'s Up next fields are: new,
        // and nothing to throw a whole queue away over.
        sessionID = try? values.decodeIfPresent(String.self, forKey: .sessionID)
        autoplay = try? values.decodeIfPresent(Bool.self, forKey: .autoplay)
        upNext = try? values.decodeIfPresent(RoomUpNext.self, forKey: .upNext)
    }

    /// Can this phone empty the whole queue?
    ///
    /// Household only. `POST /api/music/queue/{room}/clear` carries the
    /// `DEVICE` dependency where remove and move carry `GUEST_OR_DEVICE`, so
    /// a guest is refused this one before the block lookup even runs.
    public var mayClear: Bool { editable && !guest }

    /// Can this phone take **this** entry out?
    ///
    /// Everything but the entry playing now, for a guest. The core refuses
    /// that one; a client that offered it would be offering a refusal.
    public func mayRemove(_ item: QueueItem) -> Bool {
        guard editable else { return false }
        return !(guest && item.playing)
    }

    /// Reordering needs nothing beyond `editable`.
    public var mayMove: Bool { editable }

    /// The notice over a queue this phone can only watch, or nil when it can
    /// do more than watch.
    ///
    /// `QueueTab.kt:304`: the server's own sentence, then what that means for
    /// the person in front of the screen. Only when the server gave a reason
    /// — `editable: false` with nothing said is not a sentence this app can
    /// invent, and the controls are already gone.
    public var watchOnlyNote: String? {
        guard !editable, let blockedReason else { return nil }
        let said = blockedReason.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !said.isEmpty else { return nil }
        return MusicStrings.watchOnly(said)
    }
}

/// How a queue is asked for and edited.
///
/// A type rather than string-building at four call sites, for
/// ``MusicLibraryQuery``'s reason: the room id is a **person's word** — "Kids'
/// room" — so every one of these five paths has to escape it, and four places
/// that each remember to do that is three places that eventually will not.
public enum MusicQueuePath {

    /// The room id, safe in a path segment.
    ///
    /// Written as an explicit allowed set rather than `.urlPathAllowed` for
    /// ``DeviceRegistry``'s reason: `.urlPathAllowed` admits `/`, `;` and
    /// `&`, and a room called `a/b` would then address a path this app did
    /// not write.
    static let safe = CharacterSet(charactersIn: "-._~").union(.alphanumerics)

    public static func escaped(_ segment: String) -> String {
        segment.addingPercentEncoding(withAllowedCharacters: safe) ?? segment
    }

    /// `GET` the queue. `device_id` decides `editable` and `blocked_reason`
    /// only; the read itself is never blocked, so a device an admin has
    /// stopped can still **see** the queue and why it cannot change it.
    public static func read(room: String, deviceID: String) -> String {
        "/api/music/queue/\(escaped(room))?device_id=\(escaped(deviceID))"
    }

    public static func remove(room: String) -> String {
        "/api/music/queue/\(escaped(room))/remove"
    }

    public static func move(room: String) -> String {
        "/api/music/queue/\(escaped(room))/move"
    }

    public static func clear(room: String) -> String {
        "/api/music/queue/\(escaped(room))/clear"
    }

    /// Add library tracks, `where` next or at the end (21b5ccfb, cb780e90).
    public static func add(room: String) -> String {
        "/api/music/queue/\(escaped(room))/add"
    }

    /// Play one entry now: the room's own jump, MPD `playid` (P4).
    public static func play(room: String) -> String {
        "/api/music/queue/\(escaped(room))/play"
    }

    /// The room's queue, in order, as a new playlist.
    public static func savePlaylist(room: String) -> String {
        "/api/music/queue/\(escaped(room))/save-playlist"
    }

    /// The room's Up next session: `PATCH` it for autoplay.
    public static func session(_ id: String) -> String {
        "/api/music/up-next/sessions/\(escaped(id))"
    }

    /// "Less like this" on one pick of the session.
    public static func feedback(session id: String) -> String {
        "/api/music/up-next/sessions/\(escaped(id))/feedback"
    }

    /// One room transport action — `POST /api/music/{action}/{room}`.
    public static func transport(_ action: MusicTransportAction, room: String) -> String {
        "/api/music/\(action.rawValue)/\(escaped(room))"
    }

    /// Favourite whatever a room is playing right now.
    public static func favoriteNowPlaying(room: String) -> String {
        "/api/music/now-playing/\(escaped(room))/favorite"
    }
}

/// The five things a room's transport can be told to do.
///
/// `NowPlayingCards.kt:53`'s `RoomTransport`, as a closed set rather than the
/// `(action: String, room: String)` closure it is over there. The Android
/// shape sends whatever string a call site passes and names the failure after
/// it (`roomCtl(action, room, action)`), so a typo is a 404 whose toast reads
/// "paus failed"; here a typo does not compile.
public enum MusicTransportAction: String, CaseIterable, Sendable {
    case previous
    case pause
    case resume
    case skip
    case stop

    /// The word a failure is reported with — Android passes the action itself
    /// as its own `failLabel`, and so does this.
    public var label: String { rawValue }
}
