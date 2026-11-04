import Foundation

/// Every `/api/plugins/radio` route this screen asks for, and every body it
/// sends, escaped once, here.
///
/// `StationSearch.kt` builds these inline with `URLEncoder.encode` and a
/// `StringBuilder`; they are a type for ``FilePath``'s reason — a station's
/// name, call sign and city all travel as query values, and a search for
/// `AM & FM` or `97.5+` assembled by hand is a different request than the one
/// on screen.
public enum StationsPath {

    static let root = "/api/plugins/radio"

    /// Unreserved characters only, as ``FilePath/esc(_:)`` does: `&`, `=`,
    /// `#`, `+`, `/` and a space are all escaped.
    static func esc(_ raw: String) -> String { VideoPath.esc(raw) }

    // MARK: - reads

    /// The favourites list, with **no paging** — the pager adds that
    /// (``Paging/path(_:_:)``). `web.py:265`.
    public static let favorites = "\(root)/stations?favorited_only=true"

    /// `GET /recent`. **No `limit`**: the route's bound is 1-10 and the
    /// server trims the table to ten anyway (`web.py:322`, `_trim_recents`),
    /// so a client bound could only ever ask for fewer. Android sends none
    /// either.
    public static let recent = "\(root)/recent"

    public static let badge = "\(root)/badge"

    /// Up to eight favourites matching `query` — the instant-favourites
    /// surface (`StationSearch.kt:112`). Not paged: it is a peek, and the
    /// Favorites list below is the whole thing.
    public static func favoriteMatches(_ query: String, limit: Int = 8) -> String {
        "\(root)/stations?favorited_only=true&q=\(esc(query))&limit=\(limit)"
    }

    /// One scope's search, with **no paging**.
    ///
    /// Returns nil when there is nothing to ask: Android refuses an online
    /// search with neither a query nor a country (`StationSearch.kt:126`),
    /// where an FM browse with an empty box is the documented way to walk the
    /// whole import.
    ///
    /// A numeric FM query becomes an **exact** `frequency_mhz` match and not
    /// a `q`, which is what `97.5` in that box means (`StationSearch.kt:140`).
    public static func search(scope: StationsScope, query: String, country: String) -> String? {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        switch scope {
        case .online:
            let cc = country.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
            guard !q.isEmpty || !cc.isEmpty else { return nil }
            var parts: [String] = []
            if !q.isEmpty { parts.append("q=\(esc(q))") }
            if !cc.isEmpty { parts.append("country_code=\(esc(cc))") }
            // No trailing `&`. The Kotlin's StringBuilder leaves one because
            // it appends `limit=` itself; here ``Paging/path(_:_:)`` appends,
            // and `?q=a&&limit=30` is a parse surprise waiting to happen.
            return "\(root)/search?" + parts.joined(separator: "&")
        case .fm:
            var out = "\(root)/stations?source=fm"
            if !q.isEmpty {
                if let frequency = Double(q) {
                    out += "&frequency_mhz=\(frequency)"
                } else {
                    out += "&q=\(esc(q))"
                }
            }
            return out
        }
    }

    /// One station's detections, newest first (`web.py:679`).
    public static func detections(stationID: Int, limit: Int = 100) -> String {
        "\(root)/detections?station_id=\(stationID)&limit=\(limit)"
    }

    // MARK: - writes (every one of them `@device_endpoint`)

    public static let play = "\(root)/play"
    public static let create = "\(root)/stations"

    public static func station(_ id: Int) -> String { "\(root)/stations/\(id)" }

    public static func resolveSimulcast(_ id: Int) -> String {
        "\(root)/stations/\(id)/resolve-simulcast"
    }

    /// `GET /stations/{id}/stream` — the relay the audio engine opens
    /// (`web.py:727`). The proxy resolves a **row id** rather than a URL, so
    /// that the server can refuse a house-local address and relay bytes the
    /// phone cannot reach itself.
    public static func stream(_ id: Int) -> String { "\(root)/stations/\(id)/stream" }

    // MARK: - bodies

    /// `POST /play` — `{station_id}` for a saved row, else the directory-hit
    /// shape (`web.py:141`'s `RadioStationPlay`, `StationPlay.kt:63`).
    public static func playBody(_ station: StationsRow) -> Data {
        if station.isSaved {
            return json([("station_id", .int(station.id))])
        }
        return json([
            ("name", .string(station.name)),
            ("source", .string(station.source ?? "online")),
            ("stream_url", .maybe(station.streamURL)),
            ("external_id", .maybe(station.externalID)),
            ("country_code", .maybe(station.countryCode)),
            ("language", .maybe(station.language)),
            ("tags", .strings(station.tags)),
        ])
    }

    /// `POST /stations` with a search hit — idempotent on `external_id`,
    /// which is why starring the same hit twice does not make two rows
    /// (`web.py:491`, `StationsScreen.kt:143`).
    ///
    /// **`frequency_mhz` and the market fields are not sent**, as Android
    /// does not send them: an online hit has none, and an FM row already
    /// exists in the table (it came out of the import), so this body is only
    /// ever a directory hit's.
    public static func createBody(_ hit: StationsRow) -> Data {
        json([
            ("name", .string(hit.name)),
            ("source", .string(hit.source ?? "online")),
            ("stream_url", .maybe(hit.streamURL)),
            ("external_id", .maybe(hit.externalID)),
            ("country_code", .maybe(hit.countryCode)),
            ("language", .maybe(hit.language)),
            ("tags", .strings(hit.tags)),
        ])
    }

    public static func favoritedBody(_ favorited: Bool) -> Data {
        json([("favorited", .bool(favorited))])
    }

    public static func intervalBody(_ seconds: Int) -> Data {
        json([("sample_interval_sec", .int(seconds))])
    }

    /// `PATCH` with a stream URL, or an explicit **null** to clear it.
    ///
    /// Clearing is allowed here and nowhere else in this screen's PATCHes:
    /// `stream_url` and `tags` are nullable columns, where an explicit null
    /// for `name`, `favorited` or `sample_interval_sec` is a 422
    /// (`_PATCH_NOT_NULL`, `web.py:163`) — so a draft wiped to blank sends
    /// `{"stream_url": null}` and the three other fields are never sent null
    /// at all.
    public static func streamURLBody(_ url: String?) -> Data {
        json([("stream_url", .maybe(url))])
    }

    // MARK: - a very small JSON writer

    /// One body's values, written by hand rather than through `Encodable`.
    ///
    /// Why: `JSONEncoder` over a `[String: Any?]` cannot be done at all, an
    /// `Encodable` struct per body would be seven structs whose only job is
    /// to be eight optional fields, and `JSONSerialization` sorts nothing —
    /// a body whose key order changes between runs is a body a test cannot
    /// assert on the wire. This writes the keys in the order they are given.
    ///
    /// The string escaping is ``MusicJSONValue``'s (`String.jsonLiteral`),
    /// not a second copy: a station's name is a person's word and may hold a
    /// quote, and two escaping rules in one package is how two screens come
    /// to disagree about a name with a backslash in it. The *shape* is this
    /// screen's own, because a `null` for an absent field is something
    /// Music's writer has no case for.
    enum Value {
        case string(String)
        case int(Int)
        case bool(Bool)
        case strings([String])

        /// Written as JSON `null` when it is absent, which is what Android's
        /// `put(key, nullableString)` does.
        case maybe(String?)

        var text: String {
            switch self {
            case .string(let s): return s.jsonLiteral
            case .int(let i): return "\(i)"
            case .bool(let b): return b ? "true" : "false"
            case .strings(let list): return "[" + list.map({ $0.jsonLiteral }).joined(separator: ",") + "]"
            case .maybe(let s): return s?.jsonLiteral ?? "null"
            }
        }
    }

    static func json(_ fields: [(String, Value)]) -> Data {
        let body = fields.map { "\($0.0.jsonLiteral):\($0.1.text)" }.joined(separator: ",")
        return Data("{\(body)}".utf8)
    }
}

/// The decisions this screen makes, apart from the model that makes them.
///
/// Every one of these is a question a view would otherwise answer inline and
/// nothing could ask it whether it got right: whether a URL is one this phone
/// can open, what a star does, what a draft in a field came to, and which of
/// three sentences a now-playing line is.
public enum StationsRules {

    /// `"97.5"` not `"97.5 0"`, `"101"` not `"101.0"`
    /// (`StationModels.kt:77`).
    public static func freq(_ f: Double) -> String {
        if f.truncatingRemainder(dividingBy: 1) == 0, abs(f) < 1e9 {
            return "\(Int(f))"
        }
        // Kotlin's `Double.toString()` and Swift's interpolation agree on the
        // shortest round-tripping form for every frequency the column can
        // hold (NUMERIC(5,1), so one decimal place).
        return "\(f)"
    }

    /// Can **this app** stream the station directly?
    ///
    /// `StationPlay.kt:30`. The proxy 409s on a missing or house-local URL —
    /// an FM row resolves to a transient local address, and an FCC import has
    /// no URL until a simulcast is resolved — so those are refused up front
    /// with a sentence that says what to do instead.
    public static func playable(_ station: StationsRow) -> Bool {
        guard let url = station.streamURL?.lowercased() else { return false }
        guard url.hasPrefix("http://") || url.hasPrefix("https://") else { return false }
        return !["localhost", "127.0.0.1", "://0.0.0.0"].contains { url.contains($0) }
    }

    /// Why not, in the station's own name (`StationPlay.kt:36`).
    public static func unplayableReason(_ station: StationsRow) -> String {
        if station.source == "fm" || station.source == "sdr" {
            return StationsStrings.noSimulcastYet(station.name)
        }
        return StationsStrings.noPlayableStream(station.name)
    }

    /// What a tap on a search row's star does (`StationSearch.kt:597`).
    ///
    /// `rowID != 0` is a row in the table, so the star is a PATCH of its
    /// `favorited`. `rowID == 0` is a directory hit, so it is a POST that
    /// saves **and** favourites — and the id that POST answers is kept, or
    /// the next tap does nothing (plugin board F-P013).
    public static func starTap(rowID: Int, favorited: Bool) -> StationsStarTap {
        rowID != 0 ? .patch(id: rowID, favorited: !favorited) : .post
    }

    /// One page of a **bare array**, with Android's own heuristic for whether
    /// there is another.
    ///
    /// This is the one non-obvious thing on this screen.
    /// ``Page/init(from:)`` maps a bare array to `serverHasMore: false`, so a
    /// ``Pager`` pointed straight at `/stations` or `/search` would load page
    /// one and stop, silently — and every one of this plugin's list routes
    /// answers a bare array. So the fetch closure decodes the array itself
    /// and asks the only question the server leaves it:
    /// `hasNext = results.size == PAGE_SIZE` (`StationSearch.kt:160`). There
    /// is no total to compare against; neither route answers one.
    public static func page(_ rows: [StationsRow], limit: Int) -> Page<StationsRow> {
        Page(items: rows, total: nil, serverHasMore: rows.count >= limit, nextCursor: nil)
    }

    /// The sampling interval a draft came to, or the refusal
    /// (`StationsScreen.kt:470`).
    ///
    /// The bounds are the server's own (`sample_interval_sec` is
    /// `ge=30, le=86400`, `web.py:157`), checked here so that a typo is a
    /// sentence rather than a 422.
    public static func interval(_ draft: String) -> StationsEdit<Int> {
        let typed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let value = Int(typed), value >= 30, value <= 86_400 else {
            return .refused(StationsStrings.intervalOutOfRange)
        }
        return .ok(value)
    }

    /// The stream URL a draft came to — `nil` for a draft wiped blank, which
    /// **clears** the column (`StationDetail.kt:224`).
    public static func streamURL(_ draft: String) -> StationsEdit<String?> {
        let typed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !typed.isEmpty else { return .ok(nil) }
        let lower = typed.lowercased()
        guard lower.hasPrefix("http://") || lower.hasPrefix("https://") else {
            return .refused(StationsStrings.streamMustBeHTTP)
        }
        return .ok(typed)
    }

    /// Which of the three now-playing sentences a row gets
    /// (`StationsScreen.kt:620`).
    ///
    /// A track beats everything, because it is the freshest signal there is.
    /// Then `icy_supported == false`, which means the ICY poller has given up
    /// and the audio sampler is the only source. **Nil is not false**: never
    /// probed is `listening…`, and collapsing the two would tell someone
    /// their station has no metadata before anything had looked.
    public static func nowPlaying(_ station: StationsRow) -> StationsNowPlaying {
        if let track = station.nowPlaying {
            return .track(track, since: station.nowPlayingUpdatedAt)
        }
        if station.icySupported == false { return .noLiveMetadata }
        return .listening
    }

    /// The sampler's dot: live and `ok` once something has been sampled, else
    /// idle (`StationsScreen.kt:525`).
    public static func samplerTone(_ station: StationsRow) -> HealthTone {
        station.lastSampledAt != nil ? .ok : .idle
    }

    /// A detection's source pill (`StationDetail.kt:377`).
    ///
    /// `dejavu_local` and `local` are the local fingerprint, the highest
    /// trust, in the brand tone. Anything else — `icy`, `shazam`, a word this
    /// app has never heard of — passes through **verbatim**: the source
    /// vocabulary is the plugin's, not this app's.
    public static func detectionSource(_ raw: String?) -> (label: String, tone: HealthTone) {
        switch raw {
        case "dejavu_local", "local": return (StationsStrings.localSource, .brand)
        case nil: return (StationsStrings.dash, .idle)
        case .some(let other): return (other, .idle)
        }
    }

    /// Whether a detection was matched to something in the library
    /// (`StationDetail.kt:389`). Either signal is enough: a tier-1 local
    /// match sets the id, and the server sets the flag.
    public static func inLibrary(_ detection: StationsDetection) -> Bool {
        detection.inLibrary || detection.libraryTrackID != nil
    }
}
