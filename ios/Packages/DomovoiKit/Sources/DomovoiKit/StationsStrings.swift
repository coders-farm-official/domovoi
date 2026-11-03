import Foundation

/// Every word the Stations screen says, transcribed from the Kotlin.
///
/// Three sentences had to change, each one a sentence Android has no need of,
/// and each is marked **iOS-only** here and recorded in
/// `design-notes/ios/overnight/stations/IOS-ONLY-STRINGS.md`: the note where
/// Android's `import fcc fm` button is, the FM tip (which tells you to hit a
/// `browse` button that pages a list this port scrolls), and the FM empty
/// state (which tells you to press that same button).
public enum StationsStrings {

    // MARK: - the page (`StationsScreen.kt:181`)

    public static let title = "Stations"

    /// `"23 favorited · 3 recent · tap a station to play it"`.
    ///
    /// Both counts are the ones Android shows: the favourites total from
    /// `/badge`, and the **returned** size of `/recent`, which the server
    /// trims to ten — so a house with eleven plays says ten (MAP §8.3).
    public static func subtitle(favorites: Int, recent: Int) -> String {
        "\(favorites) favorited · \(recent) recent · tap a station to play it"
    }

    /// The subtitle of a build that **cannot play anything**, which is this
    /// branch until the `player` item's witness reaches the route slot.
    ///
    /// **iOS-only.** Android's sentence ends `· tap a station to play it`,
    /// and a screen whose rows do not play must not say that: it is the one
    /// instruction on the page, and following it would do nothing. The two
    /// counts are unchanged, and ``playingIsNotBuiltYet`` says what is
    /// missing rather than leaving the omission to be guessed at
    /// ([[no-misleading-controls]]).
    public static func subtitleWithoutPlaying(favorites: Int, recent: Int) -> String {
        "\(favorites) favorited · \(recent) recent"
    }

    /// Said once, where the play controls would be, when this build has no
    /// audio engine (``StationsPlayback/canPlay``).
    ///
    /// **iOS-only**, for the same reason as ``subtitleWithoutPlaying(favorites:recent:)``:
    /// no play affordance is drawn at all, and a page that silently lacked
    /// one would read as a screen that had failed rather than one that is
    /// honest about what it does. Everything else on it works.
    public static let playingIsNotBuiltYet =
        "playing on this phone isn't built yet — favoriting, sampling and detections all work"

    /// Where Android draws `import fcc fm` (`StationsScreen.kt:191`).
    ///
    /// **iOS-only.** `POST /fcc-import` carries no `@device_endpoint`, so it
    /// sits on the radio module's admin default and the module's own
    /// docstring says so: the import "stays on the admin default"
    /// (`web.py:38-40`). This app has no admin session and cannot get one, so
    /// a button there would 401 from every phone, every time — Files' delete
    /// hit the same wall and says so the same way
    /// (``FileStrings/deleteNeedsAnAdmin``).
    public static let importIsAnAdminJob =
        "importing the FCC FM list is an admin job — do it on the dashboard"

    // MARK: - the plugin band (`ui/components/PluginFrame.kt`)

    /// The bordered badge beside the plugin's name (`PluginFrame.kt:151`).
    public static let pluginBadge = "plugin"

    /// `"by Coders Farm"` (`PluginFrame.kt:144`). Nil-safe at the call site:
    /// a plugin with no publisher draws no line at all rather than a bare
    /// `by`.
    public static func pluginBy(_ publisher: String) -> String { "by \(publisher)" }

    // MARK: - the search card (`StationSearch.kt:84`)

    public static let scopeOnline = "online stations"
    public static let scopeOnlineSub = "radio-browser catalog"
    public static let scopeFM = "local fm"
    public static let scopeFMSub = "fcc-imported, near you"

    public static func placeholder(_ scope: StationsScope) -> String {
        switch scope {
        case .online: return "search stations — name, callsign, network…"
        case .fm: return "name, call sign, city, or frequency (e.g. 97.5)"
        }
    }

    public static let countryPlaceholder = "US"

    /// `"searching…"` while a request is open, `"browse"` in the FM scope,
    /// else `"search"` (`StationSearch.kt:222`).
    public static func submit(_ scope: StationsScope, loading: Bool) -> String {
        if loading { return "searching…" }
        return scope == .fm ? "browse" : "search"
    }

    public static let yourFavorites = "your favorites"

    /// `"1 match"` / `"3 matches"` (`StationSearch.kt:247`).
    public static func matches(_ count: Int) -> String {
        "\(count) match" + (count == 1 ? "" : "es")
    }

    public static let tagsLabel = "tags:"

    /// `ONLINE_TAG_PRESETS` (`StationSearch.kt:36`), in order.
    public static let tagPresets = ["indie", "jazz", "classical", "news", "electronic", "rock"]

    /// The FM scope's tip (`StationSearch.kt:286`).
    ///
    /// **iOS-only**, in its first sentence only. Android's reads "leave the
    /// box blank and hit browse to **paginate** the full FCC import"; this
    /// port's FM list has no pages to step through (MAP §5), so the sentence
    /// says what the gesture actually is. The other two sentences are
    /// Android's word for word.
    public static let fmTip =
        "Tip: leave the box blank and hit browse to scroll the full FCC import. "
        + "Type a number (97.5) to filter to that frequency, or a fragment (WJIM, Lansing) "
        + "for a name / call-sign / city match."

    public static let noMatch = "no stations match"

    /// The line under ``noMatch`` (`StationSearch.kt:296`).
    ///
    /// The FM-with-no-query branch is **iOS-only**: Android's ends
    /// "— try import fcc fm", which is an instruction to press a control this
    /// app does not draw (see ``importIsAnAdminJob``). The fact is Android's;
    /// the imperative is dropped and replaced by where the import happens.
    public static func noMatchNote(scope: StationsScope, query: String, country: String) -> String {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let cc = country.trimmingCharacters(in: .whitespacesAndNewlines)
        switch scope {
        case .online:
            guard !q.isEmpty else { return "nothing for \(cc)" }
            return "nothing in \(cc.isEmpty ? "all countries" : cc) called “\(q)”"
        case .fm:
            guard !q.isEmpty else { return "no FCC FM rows imported yet — the import is an admin job" }
            return "no FCC FM rows match “\(q)”"
        }
    }

    public static let saved = "saved"

    /// The result row's play button (`StationSearch.kt:541`).
    public static func playNamed(_ name: String) -> String { "play \(name)" }

    // Android's favourite-match row also carries a play **glyph**
    // (`StationSearch.kt:427`, `contentDescription = "play"`). Not ported:
    // here the whole row is the tap (``StationsPlayTap``), so a second
    // affordance inside it would be a control with nothing of its own to do.
    // DECISIONS, "The match row's play glyph".

    public static let favorite = "favorite"
    public static let unfavorite = "unfavorite"

    /// A field, or a whole line, the server left empty.
    public static let dash = "—"

    public static func searchFailed(_ why: String) -> String { "search failed: \(why)" }

    // MARK: - Recent (`StationsScreen.kt:259`)

    public static let recent = "Recent"
    public static func lastN(_ count: Int) -> String { "last \(count)" }
    public static let nothingPlayed = "nothing played yet"
    public static let nothingPlayedSub = "tap any station to start it"

    // MARK: - Favorites (`StationsScreen.kt:372`)

    public static let favorites = "Favorites"
    public static let noFavorites = "no favorites yet"
    public static let noFavoritesSub = "search and tap the star to start collecting"

    // MARK: - a favourite's row (`StationsScreen.kt:457`)

    public static let openDetails = "details and detections"
    public static let hideDetails = "hide details"
    public static let playHere = "play here"
    public static let interval = "interval"

    /// The interval field's suffix (`StationsScreen.kt:558`).
    public static let seconds = "s"

    public static let save = "save"
    public static let cancel = "cancel"

    /// A failed read's second chance — `ErrorState`'s own button
    /// (`ui/components/Kit.kt:296`, `TextButton { Text("retry") }`), which
    /// `StationsScreen.kt:383` draws for the favourites list. The detection
    /// feed draws it too.
    public static let retry = "retry"

    /// `ErrorState`'s default title (`Kit.kt:285`), over the failure's own
    /// message.
    public static let unreachable = "couldn't reach the server"

    /// What the detection feed says when a failed read carried no message
    /// (`StationDetail.kt:325`).
    public static let requestFailed = "request failed"

    /// The foot of a list that has more.
    ///
    /// **iOS-only**, and the one word that replaces Android's whole paging
    /// footer — `prev`, `page 2 / 3`, `next` — under the owner's standing
    /// decision that these lists load as you scroll (MAP §5). Files' own
    /// footer says the same word (``FileStrings/loadMore``), so the two paged
    /// screens in this app do not use two different ones. Drawn as well as
    /// the scroll trigger, because VoiceOver's reader does not scroll.
    public static let loadMore = "load more"

    public static let noLiveMetadata = "no live metadata"
    public static let listening = "listening…"

    /// `"97.5 FM"` for an FM row with a frequency, else the country, else
    /// `"online"` (`StationsScreen.kt:508`).
    public static func head(_ station: StationsRow) -> String {
        if station.source == "fm", let f = station.frequencyMHz {
            return "\(StationsRules.freq(f)) FM"
        }
        return station.countryCode ?? "online"
    }

    /// The instant-favourites row's trailing text — `FavoriteMatchRow`
    /// (`StationSearch.kt:414`).
    ///
    /// **Not ``head(_:)``.** This row is Android's one place that falls back
    /// to the **call sign** before the country, and that appends whatever the
    /// ICY poller last heard: a local FM favourite the import left without a
    /// frequency reads `WJIM` here where ``head(_:)`` would say `US`, and a
    /// favourite with a track keeps it.
    public static func matchHead(_ station: StationsRow) -> String {
        let lead: String
        if station.source == "fm", let f = station.frequencyMHz {
            lead = "\(StationsRules.freq(f)) FM"
        } else {
            lead = station.callSign ?? station.countryCode ?? "online"
        }
        return [lead, station.nowPlaying].compactMap { $0 }.joined(separator: " · ")
    }

    /// `"97.5 FM · sampling every 180s"` (`StationsScreen.kt:514`).
    public static func metaLine(_ station: StationsRow) -> String {
        let every = station.sampleIntervalSec.map(String.init) ?? dash
        return "\(head(station)) · sampling every \(every)s"
    }

    /// An FM result row's first line — `"WJIM · 97.5 FM"`
    /// (`StationSearch.kt:455`). The call sign is what somebody looking for a
    /// local station knows, so it leads; a row the import left without one
    /// falls back to ``head(_:)`` rather than drawing a bare separator.
    public static func fmHead(_ station: StationsRow) -> String {
        let call = (station.callSign ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return call.isEmpty ? head(station) : "\(call) · \(head(station))"
    }

    /// `"Lansing, MI"`, the city alone, or nil (`StationSearch.kt:468`).
    public static func market(_ station: StationsRow) -> String? {
        let city = (station.marketCity ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let state = (station.marketState ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if city.isEmpty { return state.isEmpty ? nil : state }
        return state.isEmpty ? city : "\(city), \(state)"
    }

    public static let intervalOutOfRange = "interval must be between 30 and 86400 s"

    public static func intervalSaved(_ name: String, _ value: Int) -> String {
        "\(name): sampling every \(value)s"
    }

    public static func saveFailed(_ why: String) -> String { "save failed: \(why)" }

    // MARK: - the writes' own words

    public static func favorited(_ name: String) -> String { "favorited \(name)" }
    public static func unfavorited(_ name: String) -> String { "unfavorited \(name)" }
    public static func favoriteFailed(_ why: String) -> String { "favorite failed: \(why)" }

    public static func simulcastFound(_ name: String) -> String { "simulcast found for \(name)" }
    public static func noSimulcast(_ message: String) -> String { "no simulcast: \(message)" }
    public static func simulcastFailed(_ why: String) -> String { "simulcast lookup failed: \(why)" }

    // MARK: - forgetting (`StationsScreen.kt:600`)

    public static let forget = "forget"
    public static func forgetTitle(_ name: String) -> String { "forget \(name)?" }
    public static let forgetBody = "Removes the station and stops sampling it."
    public static func forgot(_ name: String) -> String { "forgot \(name)" }
    public static func forgetFailed(_ why: String) -> String { "forget failed: \(why)" }

    // MARK: - the detail (`StationDetail.kt:60`)

    public static func lastSampled(_ rel: String) -> String { "last sampled \(rel)" }

    /// `"station · #4 · fm · 97.5 mhz"` (`StationDetail.kt:88`).
    public static func identity(_ station: StationsRow) -> String {
        var out = "station · #\(station.id) · \(station.source ?? "online")"
        if let f = station.frequencyMHz { out += " · \(StationsRules.freq(f)) mhz" }
        return out
    }

    public static let fieldStream = "stream"
    public static let fieldCountry = "country"
    public static let fieldLanguage = "language"
    public static let fieldTags = "tags"
    public static let fieldInterval = "interval"

    public static let recentDetections = "recent detections"

    // MARK: - the stream-URL editor (`StationDetail.kt:217`)

    public static let streamPlaceholder = "http(s)://…"
    public static let editStreamURL = "edit stream URL"
    public static let streamMustBeHTTP = "stream URL must start with http(s)://"
    public static let streamSaved = "stream URL saved"
    public static let streamCleared = "stream URL cleared"
    public static let noStreamURL = "no stream URL — polling can't reach this station"
    public static let resolve = "resolve"
    public static let resolving = "resolving…"
    public static let pasteURL = "paste url"

    public static func foundSimulcast(_ name: String) -> String { "found simulcast for \(name)" }
    public static let noSimulcastFound = "no simulcast found"
    public static func resolveFailed(_ why: String) -> String { "resolve failed: \(why)" }

    // MARK: - the detection feed (`StationDetail.kt:339`)

    public static let noDetections = "no detections yet"
    public static let noDetectionsSub = "sampler runs every few minutes — give it a song or two"
    public static let unknownArtist = "unknown artist"

    /// `"unknown artist · 4m ago"` (`StationDetail.kt:368`).
    public static func detectionMeta(_ detection: StationsDetection, nowMs: Int64) -> String {
        "\(detection.artist ?? unknownArtist) · \(relTime(detection.detectedAt, nowMs: nowMs))"
    }

    public static let localSource = "local"
    public static let inLibrary = "in library"
    public static let newDetection = "new"

    // MARK: - playing (`StationPlay.kt`)

    public static func playing(_ name: String) -> String { "playing \(name)" }
    public static func playFailed(_ why: String) -> String { "play failed: \(why)" }

    /// `StationPlay.kt:37`.
    public static func noSimulcastYet(_ name: String) -> String {
        "\(name) has no online simulcast yet — resolve one, or play it through a room"
    }

    /// `StationPlay.kt:39`. `"that station"` when the row has no name at all.
    public static func noPlayableStream(_ name: String) -> String {
        let shown = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return "\(shown.isEmpty ? "that station" : shown) has no playable stream URL"
    }
}
