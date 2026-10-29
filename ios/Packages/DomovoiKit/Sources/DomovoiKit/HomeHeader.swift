import Foundation

/// Home's header, as a value: the house's name, the date, the counts, the
/// live dot and the one sentence that explains the socket to a person.
///
/// A port of `HomeHeader` (`ui/screens/home/HomeSections.kt:157`) and its
/// call site (`HomeScreen.kt:389-400`), read at `6ff79c0e`. Every string is
/// in ``HomeHeaderStrings`` and checked against the Kotlin by
/// `HomeHeaderStringsTests`, the way ``ShellStrings`` is.
///
/// **It is the socket's only display**, which is why it is in this batch at
/// all: without it the live layer ships with nothing a person can look at.
/// The rest of Home — needs attention, timers, rooms, announce, today, the
/// everything grid, the health checks and their strings — is not here.
public struct HomeHeaderState: Equatable, Sendable {

    /// `cfg.data?.bot_name` if non-blank, else the literal `domovoi`
    /// (`HomeScreen.kt:390`).
    public let name: String

    /// `homeDate(nowMs, zone)` — `mon 28 sep`.
    public let date: String

    /// The parts of the status line, already decided. Android's
    /// `statusLine(rooms, coreDown, active)` builds them from the rooms and
    /// timer reads; **neither read exists on iOS yet**, so this batch passes
    /// an empty list and the header draws the date alone. The Kotlin
    /// `HomeHeader` takes `line: List<String>` as a parameter too, so this is
    /// Android's own seam rather than a new one.
    public let counts: [String]

    /// `lineReady = sats.answered()`: false while the rooms read has not
    /// answered, which replaces the counts with a skeleton rather than a
    /// spinner (`HomeSections.kt:205`).
    public let countsReady: Bool

    /// ``StateSocket``'s `connected`. **Not** whether the server is
    /// reachable — see that type's note, and map §1.6.
    public let live: Bool

    /// Whether this phone holds a token for the saved server.
    public let paired: Bool

    public init(
        name: String,
        date: String,
        counts: [String] = [],
        countsReady: Bool = false,
        live: Bool,
        paired: Bool
    ) {
        self.name = name
        self.date = date
        self.counts = counts
        self.countsReady = countsReady
        self.live = live
        self.paired = paired
    }

    /// The words beside the dot on a wide pane. A phone shows the dot alone,
    /// with no text (`HomeSections.kt:212-220`), so this is the accessibility
    /// label there — a bare coloured circle with no label is not a status
    /// anybody can read.
    public var liveLabel: String {
        live ? HomeHeaderStrings.live : HomeHeaderStrings.notLive
    }

    /// The sentence the dot reveals when it is tapped — **the only place in
    /// either app that explains the socket to a person**.
    ///
    /// The branch order is the Kotlin's (`HomeSections.kt:169-173`) and it
    /// matters: `live` wins, then `!paired`, then "the live connection is
    /// down". An unpaired phone whose socket happened to be up would read the
    /// live sentence, which is right — the sentence is about what is
    /// happening, not about why it might not be.
    public var explanation: String {
        if live { return HomeHeaderStrings.explainLive }
        if !paired { return HomeHeaderStrings.explainUnpaired }
        return HomeHeaderStrings.explainDown
    }

    /// `if (!paired) HomeLink("pair this phone…")` (`HomeSections.kt:240`).
    public var showsPairLink: Bool { !paired }

    /// The date and the counts as one line — the plain text of the
    /// `AnnotatedString` the Kotlin builds, with the separator between parts.
    ///
    /// The view draws the date in monospace and the separators faint, which
    /// is styling; this is what the line *says*, and it is what VoiceOver
    /// reads, because three `Text`s in a row read as three elements.
    public var spokenLine: String {
        counts.reduce(date) { $0 + HomeHeaderStrings.countsSeparator + $1 }
    }
}

/// The header's strings, verbatim from `ui/screens/home/HomeSections.kt`.
///
/// Every apostrophe here is ASCII `'`, which the map checked byte by byte
/// (§6: "there is **no** curly apostrophe anywhere in `ui/screens/home/`").
/// The middle dots are U+00B7. `HomeHeaderStringsTests` reads the Kotlin off
/// disk and fails if one of these is not a whole string in it, so a curly
/// quote or a hyphen-for-dash typed here is a failing test rather than a
/// divergence a person finds.
public enum HomeHeaderStrings {

    /// The house's name when the server has not given one
    /// (`HomeScreen.kt:390`).
    public static let defaultName = "domovoi"

    /// Beside the dot on a wide pane (`HomeSections.kt:233`).
    public static let live = "live"

    /// …and when it is down. **U+00B7, one space each side.**
    public static let notLive = "not live • updates every 30s"

    /// The three explanations (`HomeSections.kt:170-172`). Each carries a
    /// **U+00B7**; the second's two apostrophes are **ASCII**.
    public static let explainLive = "live · changes show up the moment they happen"
    public static let explainUnpaired =
        "this phone isn't paired, so the server doesn't stream to it · the page re-reads every 30s"
    public static let explainDown =
        "the live connection is down · reconnecting, and re-reading every 30s meanwhile"

    /// The link under the row when this phone has no token
    /// (`HomeSections.kt:241`).
    public static let pairLink = "pair this phone for live updates and controls"

    /// Between the date and each count (`HomeSections.kt:189`): **two spaces,
    /// U+00B7, two spaces**, so the line reads `mon 28 sep  ·  3 online`.
    public static let countsSeparator = "  ·  "
}

/// `mon 28 sep` — the header's date (`homeDate`, `HomeModels.kt:224`).
///
/// Hand-written tables and **not** a locale formatter, on both platforms, for
/// the reason the Kotlin's are: the dashboard writes this date this way
/// (`web`'s `HomeFmtDate`), and a formatter would spell it differently per
/// region while the same house's dashboard did not.
///
/// `HOME_WEEKDAYS` is Monday-first, where Foundation's `.weekday` is
/// Sunday-first, so the index is shifted rather than copied — the kind of
/// off-by-one that reads correctly and is wrong on one day in seven.
/// `testTheDateNamesEveryWeekdayCorrectly` walks a whole week.
public func homeDate(ms: Int64, timeZone: TimeZone) -> String {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = timeZone
    let date = Date(timeIntervalSince1970: Double(ms) / 1000)
    let parts = calendar.dateComponents([.weekday, .day, .month], from: date)
    guard let weekday = parts.weekday, let day = parts.day, let month = parts.month else { return "" }
    // Foundation: 1 = Sunday … 7 = Saturday. Kotlin's `dayOfWeek.value`:
    // 1 = Monday … 7 = Sunday. So Sunday (1) is the table's last entry.
    let index = (weekday + 5) % 7
    return "\(homeWeekdays[index]) \(day) \(homeMonths[month - 1])"
}

/// `HOME_WEEKDAYS` (`HomeModels.kt:221`), Monday first.
public let homeWeekdays = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

/// `HOME_MONTHS` (`HomeModels.kt:222`).
public let homeMonths = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

// MARK: - the one read the header needs

/// `GET /api/config`, as far as Home reads it.
///
/// Android's `HomeConfig` (`HomeModels.kt:52`) is the whole settings object;
/// these are the two fields Home uses. `bot_name` is the header's, and
/// `home_problems_visibility` is the needs-attention card's — decoded here
/// because leaving a field out of a model is how a later batch silently gets
/// a nil, and it costs one line.
public struct HomeConfig: Equatable, Sendable, Decodable {

    public let botName: String?
    public let homeProblemsVisibility: String?

    public init(botName: String? = nil, homeProblemsVisibility: String? = nil) {
        self.botName = botName
        self.homeProblemsVisibility = homeProblemsVisibility
    }

    enum CodingKeys: String, CodingKey {
        case botName = "bot_name"
        case homeProblemsVisibility = "home_problems_visibility"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        botName = try c.decodeIfPresent(String.self, forKey: .botName)
        homeProblemsVisibility = try c.decodeIfPresent(String.self, forKey: .homeProblemsVisibility)
    }

    /// `cfg.data?.bot_name?.takeIf { it.isNotBlank() } ?: "domovoi"`
    /// (`HomeScreen.kt:390`). A blank name is not a name.
    public static func name(of config: HomeConfig?) -> String {
        guard let raw = config?.botName,
              !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return HomeHeaderStrings.defaultName }
        return raw
    }
}

/// Home's calls. One so far: `HomeApi.config` (`HomeIo.kt:29`).
///
/// **Untracked, and that is a deliberate difference from Android.**
/// `GET /api/config` is an **Open** route — `docs/API_REFERENCE.md:1107`
/// lists its tier as Open, and `web/backend/api/config.py:71`'s `get_config`
/// takes no dependency — so its 200 says nothing whatever about whether this
/// phone is paired. Android reads it with the tracked `api.get`
/// (`HomeIo.kt:29`), and a tracked 200 clears `pairingRequired`
/// (`ApiClient.kt:334`), so on Android this read *clears* a pairing screen
/// that `/api/satellites`' 401 had just raised — and Home re-reads it on the
/// 30 s poll, so it clears it again and again.
///
/// That is the identical defect commit `d1fd984c` fixed for
/// `/api/capabilities` yesterday (map §12.1) in the file next door; this read
/// was not in that change. Reported as an Android finding in
/// `design-notes/ios/I1D1-REPORT.md`, not fixed there: `android/` is out of
/// bounds for this batch. On iOS it is one word, so the word is the right
/// one. ``Capabilities/fetch(using:)`` carries the same reasoning for the
/// same reason.
public enum HomeApi {

    public static func config(_ client: DomovoiClient) async throws -> HomeConfig {
        try await client.get("/api/config", as: HomeConfig.self)
    }
}
