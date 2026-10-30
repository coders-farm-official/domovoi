import Foundation

// ---------------------------------------------------------------------------
// The header's counts, and the first-run hint — the port of
// `HomeModels.kt:515` and `:686`, and `HomeScreen.kt:271`, `:282`
// (read at `6859b1f7`); map §3.1 and §3.8.
// ---------------------------------------------------------------------------

/// `statusLine(rooms, coreDown, active)` (`HomeModels.kt:515`) — the parts
/// after the date, which ``HomeHeaderState/spokenLine`` joins with
/// `  ·  `.
///
/// ### nil rooms and empty rooms are different, and that is the whole trap
///
/// `rooms` is handed in **only when the rooms read answered with data**:
/// Android writes `statusLine(if (sats.data != null) rooms else null, …)`
/// (`HomeScreen.kt:271`). So:
///
/// - **nil** → no room parts at all, and the **timer parts still count**.
///   The timers come from a different read of a different table, so a failed
///   rooms read is no reason to stop saying there are two timers running.
/// - **empty** → the single part `no rooms yet`, which is a statement about
///   the house and not about the read.
///
/// Collapsing the two is map §11.2 in a new place: three lines below this
/// call, Home hands the health checks `sats.data.orEmpty()` instead, so with
/// the rooms read failed they report that none of the rooms is offline. The
/// status line gets it right and the attention card does not, on the same
/// screen, from the same read. This function keeps the optional, and
/// `theStatusLineTellsNoRoomsApartFromNoRoomsRead` is the test that stops it
/// being helpfully flattened later.
///
/// ### The order, which is pinned verbatim on Android
///
/// `last known` first **if the core is down**, then `N online`, then
/// `N offline` if any, `N waiting` if any, `N playing` if any — and
/// **`playing` is forced to 0 while the core is down**, because what the
/// server said last is not evidence that anything is playing now. Then
/// `N timer`/`N timers` and `N reminder`/`N reminders` off the active list.
///
/// `online` is counted from ``HomeRoom/online`` (`status == "online"`), while
/// `offline` and `waiting` are counted from `status` **equalling** those
/// words — so a room with some other status, or none, is in none of the three
/// counts. That is the Kotlin's arithmetic, and it means the parts need not
/// add up to the number of rooms.
public func statusLine(rooms: [HomeRoom]?, coreDown: Bool, active: [HomeTimer]) -> [String] {
    var line: [String] = []
    if let rooms {
        if rooms.isEmpty {
            line.append(HomeRoomStrings.noRoomsYet)
        } else {
            if coreDown { line.append(HomeRoomStrings.lastKnown) }
            line.append("\(rooms.filter(\.online).count)\(HomeStatusStrings.onlineSuffix)")
            let off = rooms.filter { $0.status == "offline" }.count
            let waiting = rooms.filter { $0.status == "waiting" }.count
            if off > 0 { line.append("\(off)\(HomeStatusStrings.offlineSuffix)") }
            if waiting > 0 { line.append("\(waiting)\(HomeStatusStrings.waitingSuffix)") }
            let playing = coreDown ? 0 : rooms.filter { roomRank($0) == 0 }.count
            if playing > 0 { line.append("\(playing)\(HomeStatusStrings.playingSuffix)") }
        }
    }
    let timers = active.filter { !$0.isReminder }.count
    let reminders = active.count - timers
    if timers > 0 { line.append(plural(timers, HomeTimerStrings.timer)) }
    if reminders > 0 { line.append(plural(reminders, HomeTimerStrings.reminder)) }
    return line
}

/// The count words of the status line. Each is a **tail** with a number in
/// front of it, which is why they are stored with their leading space:
/// dropping it is how `3online` ships.
public enum HomeStatusStrings {
    public static let onlineSuffix = " online"
    public static let offlineSuffix = " offline"
    public static let waitingSuffix = " waiting"
    public static let playingSuffix = " playing"
}

// MARK: - the first-run hint

/// The manual's answer, **including "asked, and got nothing"** —
/// `ManualAnswer` (`HomeScreen.kt:283`).
///
/// A struct wrapping an optional rather than a bare optional, for the reason
/// Android wraps it: `hint != nil` with `phrase == nil` still draws the card,
/// using the fallback. A bare `String?` would make "the manual has not been
/// read yet" and "the manual was read and had no phrase" the same value, and
/// the card would either flash the fallback before the read lands or never
/// appear on a server whose manual is missing.
public struct HomeHint: Equatable, Sendable {

    /// The manual's Start here, its first `try saying` phrase; nil when the
    /// manual could not be read or had none.
    public let phrase: String?

    public init(phrase: String?) {
        self.phrase = phrase
    }

    /// What the card actually shows — ``hintPhrase(_:)``, never nil.
    public var text: String { hintPhrase(phrase) }
}

/// `HOME_HINT_FALLBACK` (`HomeModels.kt:686`).
///
/// Chosen rather than picked: a timer works on a fresh install by the fast
/// path, with no internet and no plugins, so the first thing the card ever
/// suggests is the first thing that will work.
public let homeHintFallback = "set a timer for 10 minutes"

/// `hintPhrase(startPhrase)` (`HomeModels.kt:690`) — the manual's phrase, or
/// the fallback. A blank phrase is not a phrase (`isNotBlank`).
public func hintPhrase(_ startPhrase: String?) -> String {
    guard let startPhrase, !startPhrase.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
        return homeHintFallback
    }
    return startPhrase
}

/// Whether this is a first run — all four conditions (`HomeScreen.kt:282`).
///
/// **Two of them require the read to have answered with data**, which is what
/// `?.isEmpty() == true` means in Kotlin and is false for null. Hence the
/// optionals here rather than booleans: an empty rooms list and a rooms read
/// that failed are different facts, and only the first means the house is
/// empty. Pass the reads' `data` straight in.
///
/// - Parameters:
///   - rooms: `sats.data` — nil until it has answered **with data**.
///   - timers: `timers.data` — only that it answered; the list's contents are
///     the next parameter's business.
///   - activeTimers: `tv.active`, from the timer view, which is why this
///     takes the derived list and not the read: a house with a running timer
///     is in use whatever else is empty.
///   - events: `cal.data`, nil until it has answered with data.
public func homeFirstRun(
    rooms: [HomeRoom]?,
    timers: HomeTimerList?,
    activeTimers: [HomeTimer],
    events: [HomeEvent]?
) -> Bool {
    guard let rooms, rooms.isEmpty else { return false }
    guard timers != nil else { return false }
    guard activeTimers.isEmpty else { return false }
    guard let events, events.isEmpty else { return false }
    return true
}

/// The first-run card's strings (map §6.6). Pinned against the Kotlin on disk
/// by `ShellStringsTests`.
public enum HomeFirstRunStrings {

    /// `HomeSections.kt:994`.
    public static let trySaying = "try saying"

    /// `HomeSections.kt:1006` — the link to the Manual.
    public static let startHere = "start here"

    /// The phrase is drawn **wrapped in curly double quotes**, in monospace,
    /// with no spaces inside them (`HomeSections.kt:1000`): **U+201C** and
    /// **U+201D**, not the ASCII `"`.
    public static let openQuote = "“"
    public static let closeQuote = "”"

    /// `“set a timer for 10 minutes”`, quotes included.
    public static func quoted(_ phrase: String) -> String {
        openQuote + phrase + closeQuote
    }
}

// MARK: - announce, and the everything grid's strings

/// The announce section's strings (map §6.6). Its text and its send are
/// hoisted out of the section on Android, and the comment says why: "the
/// section is a `LazyColumn` item, and scrolling it out of view while a send
/// is in flight would cancel the request (and its toast) with it"
/// (`HomeScreen.kt:347`). **A `LazyVStack` row holding a `Task` is the
/// identical bug**, so 3b hoists it too.
public enum HomeAnnounceStrings {

    /// The card title (`HomeSections.kt:857`).
    public static let title = "announce"

    /// The header pill with no room online (`HomeSections.kt:859`).
    public static let noRoomsOnline = "no rooms online"

    /// …and with some: a count plus ``HomeStatusStrings/onlineSuffix``.
    public static func onlinePill(_ count: Int) -> String {
        "\(count)\(HomeStatusStrings.onlineSuffix)"
    }

    /// The field's placeholder (`HomeSections.kt:871`).
    public static let placeholder = "say something in every room"

    /// The send button's word on a wide pane (`HomeSections.kt:889`).
    public static let send = "send"

    /// The megaphone's accessibility label (`HomeSections.kt:885`), which on
    /// a phone is all the button has.
    public static let announceInEveryRoom = "announce in every room"
}

/// Whether the announce field and its button are live.
///
/// `announceOnline` is **forced to 0 while the core is down**
/// (`HomeScreen.kt:352`), "so the field greys out rather than offering to
/// speak into a house that cannot hear" — the owner's no-misleading-controls
/// rule, already applied in the Kotlin.
public func announceOnlineCount(onlineRooms: Int, coreDown: Bool) -> Int {
    coreDown ? 0 : onlineRooms
}

/// `enabled = msg.isNotBlank() && !none && !sending` (`HomeSections.kt:881`).
public func announceSendEnabled(message: String, onlineCount: Int, sending: Bool) -> Bool {
    !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && onlineCount > 0 && !sending
}

/// The `everything` card's strings (map §6.6).
public enum HomeEverythingStrings {

    /// The card title (`HomeSections.kt:1028`).
    public static let title = "everything"

    /// …and its header link, which opens Settings → App layout.
    public static let editTheBar = "edit the bar"
}
