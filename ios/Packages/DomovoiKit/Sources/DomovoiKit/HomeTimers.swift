import Foundation

// ---------------------------------------------------------------------------
// Home's timers section, as rules — the port of `HomeModels.kt:242-407`
// (read at `6859b1f7`), map §3.3 and its strings §6.4.
//
// Two clocks and only one of them is here. Every figure in this file is
// measured against the **server's** clock — `nowMs + serverOffsetMs(...)` —
// because the server deletes a timer the moment it fires and a phone whose
// clock is a minute out would draw a countdown a minute wrong. The date and
// today's grouping use the phone's; see `HomeClock.swift`.
//
// **Neither alert-hint row is ported — owner decision, 2026-10-30, and it is
// settled rather than deferred.** Android's timers card can show
// `Timer alerts are off on this phone` (`POST_NOTIFICATIONS`) or
// `Timers may ring a few minutes late on this phone` (`SCHEDULE_EXACT_ALARM`).
// The second has no iOS concept at all. The first maps cleanly onto a
// notification authorisation, which is exactly what made it dangerous: it
// would compile, it would read naturally, and it would be a lie, because this
// app schedules no local notifications — a phone that granted the permission
// would ring for nothing and a phone that refused would lose nothing. So the
// row is out, not stubbed and not behind a flag; `homePhoneTimers` rows and
// the done lines are the whole of the card. If local timer alerts are ever
// built here, the row comes back with them and not before.
// ---------------------------------------------------------------------------

// MARK: - the caps and windows

/// `HOME_DONE_MS` (`HomeModels.kt:32`) — how long a `done · kitchen` line
/// lingers.
public let homeDoneMs: Int64 = 60_000

/// `HOME_PHONE_TIMERS` (`:37`) — timers a phone shows before `+N more`,
/// "so the rooms start above the fold".
public let homePhoneTimers = 2

/// `HOME_SOON_SEC` (`:39`) — a countdown turns warn-coloured under this.
public let homeSoonSec: Int64 = 600

/// How long a vanished row's due time may be in the past and still count as
/// having fired rather than having been cancelled (`TimerBook.observe`,
/// `HomeModels.kt:333`: `at <= nowMs + 2000`).
///
/// Note the sign: the allowance is on the **future** side. A row that is gone
/// and was due two seconds from now fired; the read simply raced the fire.
let timerFiredSlackMs: Int64 = 2_000

// MARK: - `n timer` / `n timers`

/// `plural(n, one, many = one + "s")` (`HomeModels.kt:218`).
///
/// It builds `"$n $word"`, count included — the Kotlin's does, and three
/// toasts plus the status line read it that way. A version that returned only
/// the word would be the sort of near-miss that reads correctly at one call
/// site and drops the number at another.
public func plural(_ n: Int, _ one: String, _ many: String? = nil) -> String {
    "\(n) \(n == 1 ? one : (many ?? one + "s"))"
}

// MARK: - `+N more`

/// `+$extra more` — the header link that expands a capped section
/// (`HomeSections.kt:300` for the attention card, and the identical label on
/// the timers card's header, §3.3 item 2).
///
/// **Added in 3b, because 3a provided it for one of the two sections only.**
/// ``HomeAttentionState/moreLabel`` built the string inline, so the timers
/// card had no way to say the same words without a literal in a view — and a
/// literal in a view is outside what `ShellStringsTests` can see. Both call
/// sites now come through here, so the two cards cannot drift apart and the
/// `" more"` tail stays pinned against the Kotlin in one place.
///
/// Nil when nothing is hidden, which is the caller's whole test: the link is
/// drawn only when `extra > 0`.
public func homeMoreLabel(extra: Int) -> String? {
    guard extra > 0 else { return nil }
    return HomeMoreStrings.plus + "\(extra)" + HomeMoreStrings.moreSuffix
}

/// The two halves of `+N more`, because the count sits between them.
public enum HomeMoreStrings {

    /// Not a word: the `+` of `"+$extra more"`.
    public static let plus = "+"

    /// `HomeSections.kt:300`'s literal tail — **a leading space**, which is
    /// what makes `+2 more` rather than `+2more`.
    public static let moreSuffix = " more"
}

// MARK: - the countdown

/// `fmtLeft(sec)` (`HomeModels.kt:242`) — `2d 3h`, `1h 5m`, `12m 04s`, `40s`.
///
/// **The seconds are zero-padded only in the `m`/`s` form.** `12m 04s` and
/// `40s`: the padding is there so a countdown does not jiggle as it crosses a
/// ten, and in the bare-seconds form there is nothing to keep still. Padding
/// both would read `04s`, which no clock anywhere writes.
///
/// A negative input is clamped to zero (`max(0L, sec)`), so a timer the
/// server has not caught up with reads `0s` rather than counting upwards.
public func fmtLeft(_ sec: Int64) -> String {
    let s = max(0, sec)
    if s >= 86_400 { return "\(s / 86_400)d \((s % 86_400) / 3600)h" }
    let h = s / 3600
    let m = (s % 3600) / 60
    let ss = s % 60
    if h > 0 { return "\(h)h \(m)m" }
    if m > 0 { return "\(m)m \(String(format: "%02d", ss))s" }
    return "\(ss)s"
}

/// `secondsLeft` (`HomeModels.kt:282`): whole seconds until `timer` fires, on
/// the **server's** clock, never negative.
public func secondsLeft(_ timer: HomeTimer, nowMs: Int64) -> Int64 {
    guard let at = isoMs(timer.expiresAt) else { return 0 }
    return max(0, Int64((Double(at - nowMs) / 1000.0).rounded()))
}

/// `elapsedFraction` (`HomeModels.kt:289`): how much of `timer` has run,
/// 0…1, for the 2 dp bar along the bottom of its row.
///
/// Zero when either end is missing, and zero for a span that is not positive
/// — a bar that is full because its timer has no creation time reads as a
/// timer about to fire.
public func elapsedFraction(_ timer: HomeTimer, nowMs: Int64) -> Double {
    guard let at = isoMs(timer.expiresAt), let created = isoMs(timer.createdAt) else { return 0 }
    let span = at - created
    guard span > 0 else { return 0 }
    return min(1, max(0, Double(nowMs - created) / Double(span)))
}

// MARK: - what a timer is called

/// `timerTitle(t, shared)` (`HomeModels.kt:262`).
///
/// **The shared-screen rule is the load-bearing half.** A reminder's words
/// are household content — "take the tablets at six" — so on a screen in the
/// hall it reads `reminder · office` and never its own words. A timer's label
/// (`pasta`) is low-risk and always shows, which is the Kotlin's own
/// judgement and not a simplification of it.
///
/// A timer with no label falls back to its length, from `expires_at` minus
/// `created_at`: under 90 seconds `40s timer`, above `3 min timer`, and the
/// bare literal `timer` when either timestamp is missing or the span is not
/// positive. Note the spacing differs between the two — `40s timer` has no
/// space before the `s` and `3 min timer` has one before `min`
/// (map §6.4) — and that is the Kotlin's spelling, not a typo in it.
public func timerTitle(_ timer: HomeTimer, shared: Bool) -> String {
    if timer.isReminder {
        if shared { return "\(HomeTimerStrings.reminder)\(HomeTimerStrings.middot)\(timer.roomID ?? HomeTimerStrings.noRoom)" }
        if let message = timer.message, !message.isEmpty { return message }
        if let label = timer.label, !label.isEmpty { return label }
        return HomeTimerStrings.reminder
    }
    if let label = timer.label, !label.isEmpty { return label }
    guard let at = isoMs(timer.expiresAt), let created = isoMs(timer.createdAt) else {
        return HomeTimerStrings.timer
    }
    let total = Double(at - created) / 1000.0
    // Kotlin's `if (!(total > 0))`, which also catches the NaN it computes
    // when a timestamp is missing. Here the missing case is the guard above,
    // so this is the non-positive span alone.
    guard total > 0 else { return HomeTimerStrings.timer }
    if total < 90 { return "\(Int64(total.rounded()))s timer" }
    return "\(Int64((total / 60).rounded())) min timer"
}

/// `timerNoun(t, shared)` (`HomeModels.kt:279`) — what a toast calls it:
/// `cancelled pasta timer`.
///
/// Not the title: a reminder is always the bare word `reminder` here, shared
/// screen or not, because the toast is about the act and not about the
/// reminder. A labelled timer is `<label> timer` even where the title would
/// be the label alone.
public func timerNoun(_ timer: HomeTimer, shared: Bool) -> String {
    if timer.isReminder { return HomeTimerStrings.reminder }
    if let label = timer.label, !label.isEmpty { return "\(label) timer" }
    return timerTitle(timer, shared: shared)
}

/// The room part of a timer row and of a `done ·` line: the room, or the
/// literal `no room` (`HomeSections.kt:430`, `:541`).
public func timerRoomLabel(_ roomID: String?) -> String {
    guard let roomID, !roomID.isEmpty else { return HomeTimerStrings.noRoom }
    return roomID
}

/// `done · kitchen` (`HomeSections.kt:430`). **U+00B7.**
public func doneLineTitle(_ roomID: String?) -> String {
    HomeTimerStrings.done + HomeTimerStrings.middot + timerRoomLabel(roomID)
}

// MARK: - the fire ledger

/// `fireAsTimer(f)` (`HomeModels.kt:364`) — a fire read back as the timer it
/// was, so the title rules apply to it unchanged.
///
/// `is_reminder || kind == "reminder"`: a server that records the kind and
/// not the flag still gets the shared-screen masking. Reading only one of the
/// two fields is how a reminder's words reach a hall screen.
public func fireAsTimer(_ fire: HomeFire) -> HomeTimer {
    HomeTimer(
        id: fire.timerID,
        expiresAt: fire.dueAt,
        createdAt: fire.createdAt,
        label: fire.label,
        message: fire.message,
        roomID: fire.roomID,
        isReminder: fire.isReminder || fire.kind == "reminder"
    )
}

/// `fireTone(f)` (`HomeModels.kt:374`): heard somewhere → ok, nothing heard
/// but a delivery still going → warn, heard nowhere → err.
///
/// The order is the whole rule. `heard_in` non-empty wins outright, so a fire
/// heard in the kitchen and still trying the garage is **ok** and not warn —
/// somebody heard it, which is what the timer was for.
public func fireTone(_ fire: HomeFire) -> HealthTone {
    if !fire.heardIn.isEmpty { return .ok }
    if fire.deliveries.contains(where: { $0.outcome == "pending" || $0.outcome == "sending" }) {
        return .warn
    }
    return .err
}

/// One `done ·` line (`DoneTimer`, `HomeModels.kt:304`).
public struct DoneTimer: Equatable, Sendable {

    public let timer: HomeTimer
    public let doneAtMs: Int64

    /// The server's own words — `heard in garage, kitchen · still
    /// announcing`. Room ids and outcomes, **never speech**, which is why a
    /// shared screen may show it. Nil from the book, which knows nothing
    /// about where a fire was heard.
    public let summary: String?
    public let tone: HealthTone

    public init(timer: HomeTimer, doneAtMs: Int64, summary: String? = nil, tone: HealthTone = .ok) {
        self.timer = timer
        self.doneAtMs = doneAtMs
        self.summary = summary
        self.tone = tone
    }
}

/// The running timers and the done lines (`TimerView`, `HomeModels.kt:311`).
public struct TimerView: Equatable, Sendable {
    public let active: [HomeTimer]
    public let done: [DoneTimer]

    public init(active: [HomeTimer] = [], done: [DoneTimer] = []) {
        self.active = active
        self.done = done
    }
}

/// `timersShown(view)` (`HomeSections.kt:347`) — the card appears for either
/// list, and a section with nothing to say gets no slot and leaves no gap.
public func timersShown(_ view: TimerView) -> Bool {
    !view.active.isEmpty || !view.done.isEmpty
}

/// `fireDoneLines(fires, nowMs)` (`HomeModels.kt:384`): every fire younger
/// than ``homeDoneMs`` on the server's clock, newest first.
///
/// A fire with no `fired_at` is dropped rather than treated as having fired
/// now — "a timer that merely vanished (cancelled anywhere) draws nothing",
/// and so does a ledger row whose time the server did not give.
public func fireDoneLines(_ fires: [HomeFire], nowMs: Int64) -> [DoneTimer] {
    let dated = fires.compactMap { fire -> (HomeFire, Int64)? in
        guard let at = isoMs(fire.firedAt) else { return nil }
        return (fire, at)
    }
    let fresh = dated.filter { nowMs - $0.1 < homeDoneMs }
    return stableDescending(fresh, by: { $0.1 }).map { fire, at in
        DoneTimer(
            timer: fireAsTimer(fire),
            doneAtMs: at,
            summary: fire.summary.flatMap { $0.isEmpty ? nil : $0 },
            tone: fireTone(fire)
        )
    }
}

// MARK: - the book, for a server with no ledger

/// The vanish-watcher: what fired on a server too old for a fire ledger
/// (`TimerBook`, `HomeModels.kt:320`).
///
/// **One instance per server.** Android re-creates it on
/// `remember(serverUrl)` with the reason in the comment beside it: "a
/// different server is a different house: nothing it never had can have
/// fired" (`HomeScreen.kt:231`). ``forget()`` is that re-creation; a book
/// carried across a server switch would announce the *previous* house's
/// timers as having just gone off, because every one of them is missing from
/// the new house's read.
///
/// The server deletes a timer the moment it fires, so a row's disappearance
/// is the only evidence there is. The rule: a row that is gone and was due
/// within two seconds of now **fired**; one that vanished well before its
/// time was **cancelled** by somebody and leaves no line; one *this* phone
/// cancelled is in ``cancelled`` and never reads done however it vanished.
///
/// ### Why there is one entry point and not two
///
/// Android feeds the book from a `remember`, not an effect:
/// `remember(timers.data) { book.observe(timerList, …) }`
/// (`HomeScreen.kt:234`). That is a Compose idiom with no SwiftUI equivalent,
/// and the reason it is written that way is ordering: a `remember` runs
/// **while** the composition is computed, so the book has already seen the
/// new read by the time `homeTimerView` three lines later asks it what is
/// done. An `onChange`/`task` on iOS runs *after* the body, which would make
/// the first frame of every new read show the previous frame's done lines —
/// one tick late, every time, and invisible in a test that calls the two in
/// the right order by hand.
///
/// So there is no order to get wrong: ``timerView(of:serverNowMs:)`` is the
/// only public entry, and it observes and then computes inside one call.
/// ``observe(_:nowMs:)`` and ``view(_:nowMs:)`` are internal — reachable from
/// the tests that pin them, and not from a view that could call them the
/// other way round.
@MainActor
public final class TimerBook {

    /// The previous read, **in its order**, because the order decides which
    /// of two rows that vanished together is listed first when their due
    /// times tie. Kotlin's `associateBy` gives a `LinkedHashMap`; a Swift
    /// `Dictionary` would make that tie non-deterministic.
    private var previous: [HomeTimer] = []

    /// Rows seen to vanish, in the order they were noticed.
    private var fired: [(id: Int64, done: DoneTimer)] = []

    /// The last read this book has already seen, so a redraw does not
    /// re-observe. Android keys on the data **object**; this keys on its
    /// value, which is stricter in the harmless direction: a re-read that
    /// returns an equal list re-observes on Android and does not here, and
    /// observing an identical list is a no-op either way (nothing is missing
    /// from it).
    private var lastObserved: HomeTimerList?
    private var hasObserved = false

    /// Ids this phone asked to cancel: never `done` when they vanish.
    ///
    /// Readable because the cancel path is optimistic — the id goes in
    /// *before* the DELETE — and a test has to be able to see that it did.
    /// Writing goes through ``beginCancel(_:)`` and ``settleCancel(_:outcome:)``.
    public private(set) var cancelled: Set<Int64> = []

    /// Cancels this phone has in flight, which is the double-tap guard.
    private var cancelling: Set<Int64> = []

    public init() {}

    /// A different server is a different house (`HomeScreen.kt:231`).
    public func forget() {
        previous = []
        fired = []
        lastObserved = nil
        hasObserved = false
        cancelled = []
        cancelling = []
    }

    // MARK: the one entry point

    /// Home's timers: the running ones, and the done lines — from the
    /// server's fire ledger when the read carries one, else from this book
    /// (`homeTimerView`, `HomeModels.kt:394`).
    ///
    /// `fires` being **nil rather than empty** is what chooses between the two
    /// rules: nil means this server keeps no ledger (V018 not applied) and the
    /// book's guesses are all there is; `[]` means the ledger exists and
    /// nothing has gone off, so the book's guesses must be ignored or an
    /// already-authoritative "nothing fired" would be contradicted.
    ///
    /// - Parameter serverNowMs: the server's clock, `nowMs + offset`. It is
    ///   used both for the observation and for the view, which is exact
    ///   rather than a shortcut: the observation only happens on the tick
    ///   where the read changed, and on that tick this *is* the current
    ///   server time. On every other tick the value reaches nothing.
    public func timerView(of list: HomeTimerList?, serverNowMs: Int64) -> TimerView {
        if !hasObserved || list != lastObserved {
            observe(list?.timers ?? [], nowMs: serverNowMs)
            lastObserved = list
            hasObserved = true
        }
        let legacy = view(list?.timers ?? [], nowMs: serverNowMs)
        guard let fires = list?.fires else { return legacy }
        return TimerView(active: legacy.active, done: fireDoneLines(fires, nowMs: serverNowMs))
    }

    // MARK: the two halves, internal

    /// A new read arrived: anything gone from it that was due within two
    /// seconds fired (`TimerBook.observe`, `HomeModels.kt:328`).
    func observe(_ list: [HomeTimer], nowMs: Int64) {
        let nextIDs = Set(list.map(\.id))
        for timer in previous {
            if nextIDs.contains(timer.id) || cancelled.contains(timer.id) { continue }
            guard let at = isoMs(timer.expiresAt) else { continue }
            guard at <= nowMs + timerFiredSlackMs else { continue }
            // Kotlin's `fired[id] = …` on a LinkedHashMap: a repeat keeps its
            // original position and replaces the value.
            let entry = DoneTimer(timer: timer, doneAtMs: at)
            if let index = fired.firstIndex(where: { $0.id == timer.id }) {
                fired[index].done = entry
            } else {
                fired.append((id: timer.id, done: entry))
            }
        }
        previous = list
    }

    /// The running timers in the read's order, and the done lines newest
    /// first (`TimerBook.view`, `HomeModels.kt:341`).
    ///
    /// A row still in the read whose time has passed is a done line too, not
    /// only a vanished one — a server that has not yet deleted a fired timer
    /// still says it went off. `cancelled` excludes it there as well.
    func view(_ list: [HomeTimer], nowMs: Int64) -> TimerView {
        var active: [HomeTimer] = []
        var done: [(id: Int64, done: DoneTimer)] = []
        for timer in list {
            guard let at = isoMs(timer.expiresAt) else { continue }
            if at > nowMs {
                active.append(timer)
            } else if !cancelled.contains(timer.id) {
                if let index = done.firstIndex(where: { $0.id == timer.id }) {
                    done[index].done = DoneTimer(timer: timer, doneAtMs: at)
                } else {
                    done.append((id: timer.id, done: DoneTimer(timer: timer, doneAtMs: at)))
                }
            }
        }
        // The book forgets a line once it is older than the window — Kotlin's
        // iterator removal, which is why a book left running for an hour does
        // not grow.
        fired.removeAll { nowMs - $0.done.doneAtMs >= homeDoneMs }
        for entry in fired where !done.contains(where: { $0.id == entry.id }) {
            done.append(entry)
        }
        let fresh = done.filter { nowMs - $0.done.doneAtMs < homeDoneMs }
        return TimerView(
            active: active,
            done: stableDescending(fresh, by: { $0.done.doneAtMs }).map(\.done)
        )
    }

    // MARK: the cancel, which is optimistic and single-flight

    /// Claim the cancel of `id`, or refuse it because one is already in
    /// flight — the double-tap guard (`HomeScreen.kt:329`), and the optimistic
    /// mark, in one call.
    ///
    /// **Both halves or neither, which is why they are one method.** Android
    /// writes the two as separate lines in the screen — `if (cancelling[t.id]
    /// != true) { cancelling[t.id] = true; book.cancelled += t.id; … }` — and
    /// the comment above them says what the guard is for: "a double tap must
    /// not send a second one and toast 'that one already finished' over
    /// 'cancelled'". A second DELETE arrives after the server has already
    /// deleted the timer, so it 404s, so the honest toast for *that* request
    /// is `that one already finished`, which lands on top of the correct
    /// `cancelled pasta timer` and tells the person the opposite of what
    /// happened. Two lines a caller can forget to write is how that comes
    /// back.
    ///
    /// - Returns: true when the caller should send the DELETE; false when it
    ///   must send nothing at all.
    public func beginCancel(_ id: Int64) -> Bool {
        guard !cancelling.contains(id) else { return false }
        cancelling.insert(id)
        // Optimistic: the row stops reading "done" the moment the person
        // taps, not when the server answers.
        cancelled.insert(id)
        return true
    }

    /// The cancel came back: release the guard, and keep the optimistic mark
    /// **only** if the server confirmed (`HomeScreen.kt:335`:
    /// `if (out !is CancelOutcome.Cancelled) book.cancelled -= t.id`).
    ///
    /// A cancel that failed or found the timer already gone leaves the id
    /// unmarked, so a timer that in fact fired can still show its `done ·`
    /// line.
    public func settleCancel(_ id: Int64, outcome: CancelOutcome) {
        cancelling.remove(id)
        if case .cancelled = outcome { return }
        cancelled.remove(id)
    }

    /// Whether a cancel of `id` is in flight — the cancel control reads
    /// `cancelling…` and is disabled.
    public func isCancelling(_ id: Int64) -> Bool { cancelling.contains(id) }
}

/// Seconds to the soonest running timer in each room — the rooms' chips
/// (`timerLeftByRoom`, `HomeModels.kt:406`).
///
/// First wins, because `active` is already in the read's order and the server
/// sends soonest first. A room with no timer is absent rather than zero.
public func timerLeftByRoom(_ active: [HomeTimer], nowMs: Int64) -> [String: Int64] {
    var out: [String: Int64] = [:]
    for timer in active {
        guard let room = timer.roomID else { continue }
        if out[room] == nil { out[room] = secondsLeft(timer, nowMs: nowMs) }
    }
    return out
}

/// Kotlin's `sortedByDescending`, which is **stable**; Swift's `sorted(by:)`
/// is not guaranteed to be.
///
/// Worth spelling out rather than trusting: two done lines with the same
/// `fired_at` millisecond are not hypothetical — a fire ledger writes a batch
/// of reminders at one instant — and an unstable sort would reorder them
/// between redraws of an otherwise unchanged screen.
func stableDescending<Element>(
    _ elements: [Element],
    by key: (Element) -> Int64
) -> [Element] {
    elements.enumerated()
        .sorted { left, right in
            let a = key(left.element), b = key(right.element)
            return a == b ? left.offset < right.offset : a > b
        }
        .map(\.element)
}

// MARK: - the strings

/// The timers section's strings, verbatim from `ui/screens/home/`
/// (map §6.4). Pinned against the Kotlin on disk by `ShellStringsTests`.
public enum HomeTimerStrings {

    /// The card title (`HomeSections.kt:393`).
    public static let title = "timers"

    /// `HomeModels.kt:264`, `HomeSections.kt:430`, `:541`.
    public static let noRoom = "no room"

    /// `HomeModels.kt:265` — a reminder with neither message nor label, and
    /// the toast's noun for any reminder.
    public static let reminder = "reminder"

    /// `HomeModels.kt:275` — a timer whose length cannot be worked out.
    public static let timer = "timer"

    /// The head of the done line (`HomeSections.kt:430`).
    public static let done = "done"

    /// Between `done`/`reminder` and the room. **U+00B7, one space each
    /// side.**
    public static let middot = " · "

    /// The cancel control, wide enough to carry a word
    /// (`HomeSections.kt:520`).
    public static let cancel = "cancel"

    /// …and while the DELETE is in flight. **U+2026**, one character.
    public static let cancelling = "cancelling…"

    /// The cross's accessibility label on a phone (`HomeSections.kt:498`):
    /// `cancel` plus ``timerNoun(_:shared:)``.
    public static func cancelLabel(_ timer: HomeTimer, shared: Bool) -> String {
        "\(cancel) \(timerNoun(timer, shared: shared))"
    }
}
