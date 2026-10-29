import Foundation

/// Home's seven reads — `HomeIo.kt`'s `HomeApi` and the call sites at
/// `HomeScreen.kt:134-143`, read at `df658c2b`.
///
/// **Home has no endpoint.** There is no `/api/home`; the screen is seven
/// independent reads of endpoints the dashboard already uses, composed
/// client-side (`HomeScreen.kt:84`), and each one answers, fails and
/// refreshes on its own. Each is an ``ApiRead``, and ``HomeRead`` names them
/// so the refresh table (``homeRefreshPlan(for:in:)``) can be a table rather
/// than a pile of effects.
///
/// ## The tier of each read, and tracked or untracked
///
/// `HomeIo.kt:22` says "Reads are the open ones", `HomeScreen.kt:84` says
/// "all of them open", and `HealthChecks.kt:17` says it a third time. **Two
/// of the seven are device-tier** and the API reference and the route
/// definitions both say so. The seven-row table with the evidence for each
/// row is in `design-notes/ios/I1D2-REPORT.md`; the short form, verified
/// against `docs/API_REFERENCE.md` **and** `web/backend/api/` for this batch:
///
/// | read | tier | evidence | iOS asks |
/// |---|---|---|---|
/// | `/api/config` | Open | ref :1107; `api/config.py:71` takes no dependency | untracked |
/// | `/api/health` | Open | ref :1995; `main.py:490` takes no dependency | untracked |
/// | `/api/satellites` | **Device read** | ref :1037; `api/satellites.py:136` `dependencies=READ`, `READ = [Depends(require_device_read)]` at `:133` | **tracked** |
/// | `/api/timers` | Open, masked (M1/F1) | ref :1054; `api/satellites.py:1177` takes no dependency, masking is inside the handler | untracked |
/// | `/api/plugins` | Open | ref :664; `api/plugins.py:152` takes no dependency | untracked |
/// | `/api/acquisitions` | Open | ref :684; `api/acquisitions.py:57` takes no dependency | untracked |
/// | `/api/calendar/events` | **Device read** | ref :1097; `api/calendar.py:37` `dependencies=READ`, `READ = [Depends(require_device_read)]` at `:34` | **tracked** |
///
/// The rule behind the last column is one sentence: **a read is tracked when
/// its answer is evidence about this phone's credential, and untracked when
/// it is not.** A tracked 401/403 raises the pairing screen and a tracked 200
/// clears it (`ApiClient.kt:334`, `DomovoiClient.raw`), so tracking an Open
/// route means its 200 reports "this phone is paired" on the strength of an
/// answer every anonymous caller gets. That is exactly the defect commit
/// `d1fd984c` fixed for `/api/capabilities`, which part 1 found again in
/// `/api/config` (`ANDROID-DEFECTS-FOUND-BY-IOS.md` #10) — and Android reads
/// **all five** of the Open ones tracked, not just `config`. The other four
/// are in this batch's report as findings; `android/` is out of bounds here.
///
/// The two device-tier reads stay tracked on purpose: a 401 from them is the
/// honest signal that this phone needs pairing, and it is the only reason an
/// unpaired phone ever reaches the pairing screen from Home at all.
///
/// ## Home does not paginate (map §7.6)
///
/// The reads are capped **server-side, at sizes chosen to be whole answers
/// rather than first pages**: no cap on rooms, plugins or timers (the server
/// caps `fires` at 20), `?status=pending&limit=100` on acquisitions, and
/// `&limit=20` over a week on the calendar. Nothing here takes an offset or a
/// cursor and nothing should grow one: `Pagination.swift` has no business on
/// this screen.
public enum HomeRead: String, CaseIterable, Sendable, Hashable {
    case config
    case health
    case rooms
    case timers
    case plugins
    case acquisitions
    case calendar

    /// The push types that mean "re-read this one" (`HomeScreen.kt:99-102`,
    /// `HomeModels.kt:42`).
    ///
    /// **Rooms are not here.** Their pushes go through
    /// ``homeRoomEvents`` and the debounce instead, because a Wi-Fi push is
    /// merged rather than re-read and because "every `/api/satellites` read
    /// opens an MPD connection per room" while "every room sends one a
    /// minute" (`HomeScreen.kt:151-153`). A rooms read wired to
    /// ``ApiRead/eventTypes`` directly would make a six-room house open six
    /// MPD connections a minute for nothing, which is map §10.3.
    ///
    /// `config` and `health` have **no** push at all: they ride the 60 s tick.
    public var eventTypes: Set<String> {
        switch self {
        case .config, .health, .rooms: return []
        case .timers: return ["timers.changed", "timer_fires.changed"]
        case .plugins: return ["plugins.changed"]
        case .acquisitions: return ["acquisitions.changed"]
        case .calendar: return ["calendar.events.changed"]
        }
    }
}

/// Room pushes Home re-reads on, debounced (`HomeModels.kt:41`).
public let homeRoomEvents: Set<String> = [
    "satellites.presence.changed",
    "satellites.wifi.changed",
    "satellites.dropins.changed",
    "satellites.display.changed",
    "satellites.pending.changed",
    "music.now_playing.changed",
]

/// The one push whose **payload** Home consumes rather than treating as a
/// nudge (`HomeModels.kt:47`).
///
/// It is in ``homeRoomEvents`` as well, and `onRoomEvent` checks this case
/// **first** (`HomeScreen.kt:165`), so a Wi-Fi push never triggers a re-read.
/// Getting the order wrong is map §10.3.
public let homeWifiEvent = "satellites.wifi.changed"

// MARK: - the calls

extension HomeApi {

    /// `GET /api/health` (`HomeIo.kt:31`). Open → untracked.
    public static func health(_ client: DomovoiClient) async throws -> HomeHealth {
        try await client.getUntracked("/api/health", as: HomeHealth.self)
    }

    /// `GET /api/satellites` (`HomeIo.kt:33`). **Device read → tracked.**
    public static func rooms(_ client: DomovoiClient) async throws -> [HomeRoom] {
        try await client.get("/api/satellites", as: [HomeRoom].self)
    }

    /// `GET /api/timers` (`HomeIo.kt:35`). Open, masked → untracked.
    ///
    /// Masked means something a person can see: without a household
    /// credential every reminder's `message` and `label` answer null with
    /// `masked: true` (rule M1). ``HomeTimer`` does not decode `masked`, as
    /// the Kotlin's does not, so an unpaired phone shows a house full of
    /// anonymous rows — graceful, and nothing says why. Named in this batch's
    /// report, not papered over here.
    public static func timers(_ client: DomovoiClient) async throws -> HomeTimerList {
        try await client.getUntracked("/api/timers", as: HomeTimerList.self)
    }

    /// `GET /api/plugins` (`HomeIo.kt:37`). Open → untracked.
    public static func plugins(_ client: DomovoiClient) async throws -> HomePlugins {
        try await client.getUntracked("/api/plugins", as: HomePlugins.self)
    }

    /// `GET /api/acquisitions?status=pending&limit=100` (`HomeIo.kt:39`).
    /// Open → untracked.
    public static func acquisitions(_ client: DomovoiClient) async throws -> HomeAcquisitions {
        try await client.getUntracked(
            "/api/acquisitions?status=pending&limit=100",
            as: HomeAcquisitions.self
        )
    }

    /// `GET /api/calendar/events?start=…&end=…&limit=20` (`HomeIo.kt:42`).
    /// **Device read → tracked.**
    public static func calendar(
        _ client: DomovoiClient,
        dayStartMs: Int64,
        timeZone: TimeZone
    ) async throws -> [HomeEvent] {
        try await client.get(
            calendarPath(dayStartMs: dayStartMs, timeZone: timeZone),
            as: [HomeEvent].self
        )
    }
}

// MARK: - the calendar window

/// The path for the events Home needs — `calendarPath`
/// (`HomeModels.kt:675`).
///
/// From local midnight through **a week**, and the week is not an accident:
/// an empty today still has to be able to say `· next: …`, which means the
/// read has to reach past tomorrow. The start is midnight rather than now
/// because the server filters on `starts_at`, so an event already under way
/// would drop out of a window that began now.
///
/// Pinned against the Kotlin's own test, which asserts the query string
/// character for character
/// (`HomeModelsTest.calendarReadStartsAtLocalMidnightForAWeek`):
/// `/api/calendar/events?start=2026-09-28T05%3A00%3A00Z&end=2026-10-05T05%3A00%3A00Z&limit=20`.
public func calendarPath(dayStartMs start: Int64, timeZone: TimeZone) -> String {
    let end = DomovoiKit.dayStartMs(ms: start, timeZone: timeZone, plusDays: 7)
    return "/api/calendar/events?start=\(isoInstantEncoded(start))"
        + "&end=\(isoInstantEncoded(end))&limit=20"
}

/// `URLEncoder.encode(Instant.ofEpochMilli(ms).toString(), "UTF-8")`.
///
/// Java's `Instant.toString()` writes `2026-09-28T05:00:00Z` — seconds
/// always, and no fraction when the millisecond part is zero, which a local
/// midnight's always is. `URLEncoder` then turns each `:` into `%3A` and
/// leaves `-`, `T` and `Z` alone. Both halves are spelt out rather than left
/// to `ISO8601DateFormatter` and `addingPercentEncoding`, because the test
/// that pins this compares the whole query string and the two defaults do not
/// agree with Java's: the formatter would omit nothing but a
/// `urlQueryAllowed` character set leaves `:` as itself.
private func isoInstantEncoded(_ ms: Int64) -> String {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(secondsFromGMT: 0) ?? .gmt
    let date = Date(timeIntervalSince1970: Double(ms) / 1000)
    let p = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
    let milliseconds = ((ms % 1000) + 1000) % 1000
    let fraction = milliseconds == 0 ? "" : String(format: ".%03d", milliseconds)
    return String(
        format: "%04d-%02d-%02dT%02d%%3A%02d%%3A%02d",
        p.year ?? 1970, p.month ?? 1, p.day ?? 1, p.hour ?? 0, p.minute ?? 0, p.second ?? 0
    ) + fraction + "Z"
}
