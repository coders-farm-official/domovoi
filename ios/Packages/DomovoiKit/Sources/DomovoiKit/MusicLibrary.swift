import Foundation

/// How the library list is asked for: the search, the source filter, the sort
/// and the favourites switch, as **one value that becomes one path**.
///
/// `MusicScreen.kt:110`'s `libPath`, which is a `buildString` inside the
/// screen with the `remember` keys beside it. It is a type here because of
/// what it is for: **the sort, the search and the filters are the server's**,
/// so a change to any of them is a different list from page one — and that
/// makes "has the query changed?" a decision, not a redraw. A screen that
/// compared four `@State` properties by hand would get it wrong the first
/// time a fifth was added.
public struct MusicLibraryQuery: Equatable, Sendable {

    /// What the person typed. Sent to the server, which searches title,
    /// artist, album and path.
    public var search = ""

    /// A source slug, or ``MusicStrings/allSources`` for no filter.
    ///
    /// **An open vocabulary**: the options are the slugs seen on the pages
    /// fetched so far (`MusicScreen.kt:137`), because a provider plugin
    /// stamps its own and nothing client-side can know the list. Never a
    /// hard-coded enum.
    public var source = MusicStrings.allSources

    public var sort = MusicSort.newest

    public var favoritedOnly = false

    public init(
        search: String = "",
        source: String = MusicStrings.allSources,
        sort: MusicSort = .newest,
        favoritedOnly: Bool = false
    ) {
        self.search = search
        self.source = source
        self.sort = sort
        self.favoritedOnly = favoritedOnly
    }

    /// The path with the filters and the sort, and **no paging** — the pager
    /// adds that (``Paging/path(_:_:)``).
    ///
    /// The search is percent-encoded; a blank one is left out entirely rather
    /// than sent as `q=`, and so is a source of `all`, because the server
    /// treats a present-but-empty filter as a filter.
    public var path: String {
        var out = "/api/music/library?sort=\(sort.rawValue)"
        let typed = search.trimmingCharacters(in: .whitespacesAndNewlines)
        if !typed.isEmpty {
            let allowed = CharacterSet(charactersIn: "-._~").union(.alphanumerics)
            out += "&q=\(typed.addingPercentEncoding(withAllowedCharacters: allowed) ?? typed)"
        }
        if source != MusicStrings.allSources { out += "&source=\(source)" }
        if favoritedOnly { out += "&favorited=true" }
        return out
    }

    /// Whether the list the person is looking at is narrowed at all.
    ///
    /// What it is for: the empty state. "No tracks here" is the wrong thing
    /// to say to someone whose library has five thousand tracks and whose
    /// search matched none of them, and it is the wrong thing to say to
    /// someone whose library really is empty. Three sentences, and this
    /// picks between the two that are about the list.
    public var isNarrowed: Bool {
        !search.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || source != MusicStrings.allSources
            || favoritedOnly
    }

    /// What an empty answer means, said as the thing that is actually true.
    ///
    /// - Parameter libraryTotal: `stats.total_tracks`, or nil while the stats
    ///   read has not answered. **Nil is not zero**: a library whose size is
    ///   not known yet must not be called empty, which is the same mistake as
    ///   telling a first install its server is down
    ///   ([[reasoning-that-hides-a-bug]] — a sentence that claims more than
    ///   the app measured).
    public func emptyNote(libraryTotal: Int?) -> String {
        // Android's three narrowed cases in Android's order
        // (`LibraryTab.kt:91-95`), because "no tracks match" is the wrong
        // sentence for a favourites list nobody has put anything in and for a
        // source whose plugin has not indexed anything yet.
        if !search.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return MusicStrings.noTracksForSearch
        }
        if favoritedOnly { return MusicStrings.noFavoritesYet }
        if source != MusicStrings.allSources {
            return MusicStrings.noTracksFromSource(source)
        }
        if let libraryTotal, libraryTotal == 0 { return MusicStrings.libraryEmpty }
        return MusicStrings.noTracks
    }
}

/// The library's sort options, with the words the person sees.
///
/// `LibraryTab.kt:179`'s pairs, exactly: the raw values are the server's and
/// the labels are not the same words — `duration` is offered as "longest",
/// which is what it does rather than what it sorts by.
public enum MusicSort: String, CaseIterable, Sendable {
    case newest = "added_desc"
    case oldest = "added_asc"
    case title = "title"
    case artist = "artist"
    case longest = "duration"

    public var label: String {
        switch self {
        case .newest: return "newest"
        case .oldest: return "oldest"
        case .title: return "title"
        case .artist: return "artist"
        case .longest: return "longest"
        }
    }
}

/// Music's decisions that are not about one row.
public enum MusicPage {

    /// The rooms the cast menu and every "play in room" picker offer: **the
    /// ones the server reported in now-playing, and no others.**
    ///
    /// `MusicScreen.kt:78`. A made-up `"kitchen"` used to stand in when none
    /// answered — or before the first read — so the menu offered a room that
    /// was not there. That is the same defect class as the whole
    /// [[no-misleading-controls]] rule: a control that cannot do what it
    /// says. An empty list means the picker is not offered at all.
    public static func castRooms(_ nowPlaying: [NowPlayingRoom]) -> [String] {
        var seen = Set<String>()
        var out: [String] = []
        for room in nowPlaying where !room.roomID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            if seen.insert(room.roomID).inserted { out.append(room.roomID) }
        }
        return out
    }

    /// How many rooms have sound coming out of them.
    public static func playingCount(_ nowPlaying: [NowPlayingRoom]) -> Int {
        nowPlaying.filter(\.isPlaying).count
    }

    /// The source filter's options: the slugs seen so far, sorted, with
    /// `all` first and the currently chosen one always present.
    ///
    /// The last clause is `LibraryTab.kt:168`'s `+ ui.source`, and it is not
    /// cosmetic: the chosen source came from a page that may since have been
    /// scrolled away from, and a filter chip that vanished while it was
    /// active would leave the list narrowed with nothing on screen saying so.
    public static func sourceOptions(seen: [String], chosen: String) -> [String] {
        var out = [MusicStrings.allSources]
        var rest = Set(seen.filter { !$0.isEmpty })
        if chosen != MusicStrings.allSources { rest.insert(chosen) }
        out += rest.sorted()
        return out
    }

    /// The distinct source slugs on the pages fetched so far, accumulated
    /// across pages — `MusicScreen.kt:136`.
    ///
    /// Accumulated rather than recomputed from the loaded rows, because the
    /// rows are a window: a slug seen on page one is still a real option
    /// after a reload has dropped that page.
    public static func addingSources(seen: [String], from tracks: [LibraryTrack]) -> [String] {
        var out = seen
        var known = Set(seen)
        for track in tracks {
            guard let slug = track.sourcePill else { continue }
            if known.insert(slug).inserted { out.append(slug) }
        }
        return out
    }

    /// The row a card's "play in a room" should default to: the room already
    /// playing, else the first room there is.
    ///
    /// `MusicScreen.kt:367` picks `first { it.state == "play" }` for the room
    /// queue, and this is the same choice for the same reason — the room a
    /// person is listening to is the one they mean.
    public static func defaultRoom(_ nowPlaying: [NowPlayingRoom]) -> String? {
        if let playing = nowPlaying.first(where: \.isPlaying) { return playing.roomID }
        return castRooms(nowPlaying).first
    }

    /// A long name with its middle taken out — `MusicBits.kt:109`'s
    /// `truncMid`.
    ///
    /// The middle, not the end: a library path's distinguishing part is
    /// usually its tail, so `…/Albums/2019/track.mp3` truncated at the end
    /// says nothing at all.
    public static func truncateMiddle(_ text: String, max: Int = 50) -> String {
        guard text.count > max, max > 1 else { return text }
        let half = max / 2
        let head = text.prefix(half)
        let tail = text.suffix(half)
        return "\(head)…\(tail)"
    }
}
