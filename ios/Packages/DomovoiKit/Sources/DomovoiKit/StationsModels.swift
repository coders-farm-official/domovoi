import Foundation

// ---------------------------------------------------------------------------
// The radio plugin's wire models (`/api/plugins/radio/*`), ported from
// `ui/screens/stations/StationModels.kt`.
//
// Every field is decoded with `decodeIfPresent ?? default`, which is what
// Kotlin's `coerceInputValues` does for `StationModels.kt` — so a null in a
// field the server declares non-null (`source`, `sample_interval_sec`,
// `fingerprint_source`) becomes the default here exactly as it does on
// Android, and the *rendering* is what says "—".
//
// The whole screen is gated on the `stations` capability (``capStations``),
// so these routes exist whenever anything here is read.
// ---------------------------------------------------------------------------

/// One `radio_stations` row — `web.py:92`'s `RadioStation`, and
/// `StationModels.kt:16`'s `Station`.
///
/// ### `id == 0` is not a row
///
/// The search proxy answers directory hits in this same shape with `id == 0`
/// until something persists them (`web.py:93-95`, `_search_hit_to_station`).
/// The whole star state machine turns on it (``StationsRules/starTap(rowID:favorited:)``),
/// and it is why this type is **not `Identifiable`**: thirty unsaved hits all
/// have `id == 0`, and a `ForEach` keyed on that would draw one row and stop
/// updating. ``rowKey`` is the identity a list uses — the MAP planned
/// `Identifiable` and this is the one place it was wrong.
///
/// ### Why the fields are `var`
///
/// A write updates the loaded row in place (``Pager/update(_:_:)``) rather
/// than re-reading the page it is on: a star on favourite 300 must not cost
/// fifteen requests, and the id a POST answers has to be kept on the row that
/// was tapped (plugin board F-P013).
public struct StationsRow: PagedRow, Equatable {

    public var id: Int
    public var name: String

    /// `"online"` or `"fm"`. Non-null on the server (`web.py:95`) and
    /// nullable on Android (`StationModels.kt:18`); optional here, so a row
    /// from an older build renders Android's `"online"` fallback rather than
    /// failing to decode.
    public var source: String?

    public var streamURL: String?
    public var externalID: String?
    public var countryCode: String?
    public var language: String?
    public var tags: [String]
    public var favorited: Bool

    /// Seconds between audio samples. 180 on the server, nullable on Android,
    /// and nil renders the `—` in `"… · sampling every —s"`.
    public var sampleIntervalSec: Int?

    public var frequencyMHz: Double?
    public var callSign: String?
    public var marketCity: String?
    public var marketState: String?

    public var nowPlaying: String?
    public var nowPlayingUpdatedAt: String?

    /// Tristate: nil never probed, true ICY confirmed, false no ICY headers
    /// after several polls (`StationModels.kt:32`).
    public var icySupported: Bool?

    public var lastSampledAt: String?

    /// Recency (plugin V002): sorts the Recent strip.
    public var lastPlayedAt: String?

    /// A row that exists only because it was played — never favorited,
    /// reclaimed server-side when it ages out of the ten.
    public var createdByPlay: Bool

    /// What a list keys this row by.
    ///
    /// A saved row is its id. An unsaved hit is its `external_id`, or its
    /// name when the directory gave none — which is also how Android keys the
    /// search row's local state (`remember(hit.id, hit.external_id)`,
    /// `StationSearch.kt:400`).
    public var rowKey: String {
        id != 0 ? "id:\(id)" : "ext:\(externalID ?? name)"
    }

    /// Whether this row is in the server's table at all.
    public var isSaved: Bool { id != 0 }

    public init(
        id: Int = 0,
        name: String = "",
        source: String? = nil,
        streamURL: String? = nil,
        externalID: String? = nil,
        countryCode: String? = nil,
        language: String? = nil,
        tags: [String] = [],
        favorited: Bool = false,
        sampleIntervalSec: Int? = nil,
        frequencyMHz: Double? = nil,
        callSign: String? = nil,
        marketCity: String? = nil,
        marketState: String? = nil,
        nowPlaying: String? = nil,
        nowPlayingUpdatedAt: String? = nil,
        icySupported: Bool? = nil,
        lastSampledAt: String? = nil,
        lastPlayedAt: String? = nil,
        createdByPlay: Bool = false
    ) {
        self.id = id
        self.name = name
        self.source = source
        self.streamURL = streamURL
        self.externalID = externalID
        self.countryCode = countryCode
        self.language = language
        self.tags = tags
        self.favorited = favorited
        self.sampleIntervalSec = sampleIntervalSec
        self.frequencyMHz = frequencyMHz
        self.callSign = callSign
        self.marketCity = marketCity
        self.marketState = marketState
        self.nowPlaying = nowPlaying
        self.nowPlayingUpdatedAt = nowPlayingUpdatedAt
        self.icySupported = icySupported
        self.lastSampledAt = lastSampledAt
        self.lastPlayedAt = lastPlayedAt
        self.createdByPlay = createdByPlay
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case name
        case source
        case streamURL = "stream_url"
        case externalID = "external_id"
        case countryCode = "country_code"
        case language
        case tags
        case favorited
        case sampleIntervalSec = "sample_interval_sec"
        case frequencyMHz = "frequency_mhz"
        case callSign = "call_sign"
        case marketCity = "market_city"
        case marketState = "market_state"
        case nowPlaying = "now_playing"
        case nowPlayingUpdatedAt = "now_playing_updated_at"
        case icySupported = "icy_supported"
        case lastSampledAt = "last_sampled_at"
        case lastPlayedAt = "last_played_at"
        case createdByPlay = "created_by_play"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int.self, forKey: .id) ?? 0
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source)
        streamURL = try c.decodeIfPresent(String.self, forKey: .streamURL)
        externalID = try c.decodeIfPresent(String.self, forKey: .externalID)
        countryCode = try c.decodeIfPresent(String.self, forKey: .countryCode)
        language = try c.decodeIfPresent(String.self, forKey: .language)
        tags = try c.decodeIfPresent([String].self, forKey: .tags) ?? []
        favorited = try c.decodeIfPresent(Bool.self, forKey: .favorited) ?? false
        sampleIntervalSec = try c.decodeIfPresent(Int.self, forKey: .sampleIntervalSec)
        frequencyMHz = try c.decodeIfPresent(Double.self, forKey: .frequencyMHz)
        callSign = try c.decodeIfPresent(String.self, forKey: .callSign)
        marketCity = try c.decodeIfPresent(String.self, forKey: .marketCity)
        marketState = try c.decodeIfPresent(String.self, forKey: .marketState)
        nowPlaying = try c.decodeIfPresent(String.self, forKey: .nowPlaying)
        nowPlayingUpdatedAt = try c.decodeIfPresent(String.self, forKey: .nowPlayingUpdatedAt)
        icySupported = try c.decodeIfPresent(Bool.self, forKey: .icySupported)
        lastSampledAt = try c.decodeIfPresent(String.self, forKey: .lastSampledAt)
        lastPlayedAt = try c.decodeIfPresent(String.self, forKey: .lastPlayedAt)
        createdByPlay = try c.decodeIfPresent(Bool.self, forKey: .createdByPlay) ?? false
    }
}

/// `GET /badge` — the favourites total (`web.py:716`).
///
/// Reused as the favourites **count**, as Android reuses it, so there is no
/// second count query (`StationsScreen.kt:98`). Android also makes it the
/// page count; this port has no pages (MAP §5) and only the count survives.
public struct StationsBadge: Decodable, Equatable, Sendable {

    public var favorites: Int

    public init(favorites: Int = 0) {
        self.favorites = favorites
    }

    private enum CodingKeys: String, CodingKey { case favorites }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        favorites = try c.decodeIfPresent(Int.self, forKey: .favorites) ?? 0
    }
}

/// One row of `GET /detections` — `web.py:169`'s `RadioDetection`.
///
/// `station_id`, `detected_at` and `fingerprint_source` are required on the
/// server and nullable on Android (`StationModels.kt:46`); tolerant here for
/// the reason at the top of this file, and ``StationsRules/detectionSource(_:)``
/// is what turns an absent source into the `—` pill.
public struct StationsDetection: Decodable, Equatable, Sendable {

    public var id: Int
    public var stationID: Int?
    public var title: String?
    public var artist: String?
    public var detectedAt: String?
    public var fingerprintSource: String?
    public var inLibrary: Bool

    /// Soft ref to the library track a tier-1 local fingerprint match
    /// resolved this detection to.
    public var libraryTrackID: Int?

    public init(
        id: Int = 0,
        stationID: Int? = nil,
        title: String? = nil,
        artist: String? = nil,
        detectedAt: String? = nil,
        fingerprintSource: String? = nil,
        inLibrary: Bool = false,
        libraryTrackID: Int? = nil
    ) {
        self.id = id
        self.stationID = stationID
        self.title = title
        self.artist = artist
        self.detectedAt = detectedAt
        self.fingerprintSource = fingerprintSource
        self.inLibrary = inLibrary
        self.libraryTrackID = libraryTrackID
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case stationID = "station_id"
        case title
        case artist
        case detectedAt = "detected_at"
        case fingerprintSource = "fingerprint_source"
        case inLibrary = "in_library"
        case libraryTrackID = "library_track_id"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int.self, forKey: .id) ?? 0
        stationID = try c.decodeIfPresent(Int.self, forKey: .stationID)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        artist = try c.decodeIfPresent(String.self, forKey: .artist)
        detectedAt = try c.decodeIfPresent(String.self, forKey: .detectedAt)
        fingerprintSource = try c.decodeIfPresent(String.self, forKey: .fingerprintSource)
        inLibrary = try c.decodeIfPresent(Bool.self, forKey: .inLibrary) ?? false
        libraryTrackID = try c.decodeIfPresent(Int.self, forKey: .libraryTrackID)
    }
}

/// `POST /stations/{id}/resolve-simulcast` — `workers/simulcast.py:68`.
///
/// The server answers `{resolved, station_id, stream_url, external_id,
/// message, status?}`; Android decodes `{resolved, message}`
/// (`StationModels.kt:73`) and nothing reads the rest, so nothing reads it
/// here either — except `stream_url`, which is the one field that says what
/// was found, and is decode-only.
public struct StationsSimulcast: Decodable, Equatable, Sendable {

    public var resolved: Bool
    public var message: String?

    /// Decode-only: nothing draws it. The row is re-read after a resolve, so
    /// the URL on screen is the table's rather than this answer's.
    public var streamURL: String?

    public init(resolved: Bool = false, message: String? = nil, streamURL: String? = nil) {
        self.resolved = resolved
        self.message = message
        self.streamURL = streamURL
    }

    private enum CodingKeys: String, CodingKey {
        case resolved
        case message
        case streamURL = "stream_url"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        resolved = try c.decodeIfPresent(Bool.self, forKey: .resolved) ?? false
        message = try c.decodeIfPresent(String.self, forKey: .message)
        streamURL = try c.decodeIfPresent(String.self, forKey: .streamURL)
    }
}

/// Which directory a search asks.
///
/// `StationSearch.kt:92`'s `"online" | "fm"`, as a type: the Kotlin passes
/// the scope as a bare string through six call sites and compares it to a
/// literal in nine places.
public enum StationsScope: String, Equatable, Sendable, CaseIterable {

    /// `GET /search` — the radio-browser catalogue.
    case online

    /// `GET /stations?source=fm` — the FCC import.
    case fm
}

/// What a tap on a search row's star does (`StationSearch.kt:590`, plugin
/// board F-P013).
public enum StationsStarTap: Equatable, Sendable {

    /// A saved row: flip its favourite to `favorited`.
    case patch(id: Int, favorited: Bool)

    /// A new online hit (`id == 0`): save it, which favourites it. **The id
    /// the POST answers has to be kept** — the second tap used to do nothing
    /// because it was dropped.
    case post
}

/// What the now-playing line says, which is three different sentences and not
/// one with blanks in it (`StationsScreen.kt:620`).
public enum StationsNowPlaying: Equatable, Sendable {

    /// The ICY poller has a track: the text, and when it was read.
    case track(String, since: String?)

    /// `icy_supported == false`: the audio sampler is the only source for
    /// this station, said quietly and in italic.
    case noLiveMetadata

    /// Never probed, or probed and confirmed with nothing playing yet.
    case listening
}

/// What a draft in a field came to.
///
/// A `Result`-shaped answer rather than a `Bool` plus a separate message,
/// because the two have to agree: the refusal Android toasts is the reason
/// the value was rejected, and a model that returned them apart could toast
/// one and send the other.
public enum StationsEdit<Value: Equatable & Sendable>: Equatable, Sendable {
    case ok(Value)
    case refused(String)
}
