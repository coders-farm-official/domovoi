import XCTest
@testable import DomovoiKit

/// Home's five remaining sections, as rules: the timers, the rooms, today,
/// the header's counts and the first-run condition.
///
/// **Five of the tests here exist because of a bug, and each says which.**
/// They are the ones to read first, because they are the ones that fail if
/// somebody simplifies a rule that looks redundant:
///
/// - ``testAStoppedRoomIsQuietNotPlayingItsLastSong()`` — `roomSong` excludes
///   a stopped room (2026-10-11: an idle room read as playing at `0:00`).
/// - ``testTheElapsedCountNeverRunsPastTheSongsEnd()`` — `roomElapsedSec`'s
///   clamp, which is why `0:14 / 0:13` stopped happening.
/// - ``testARoomThatReportsASignalIsJudgedBySignalNotRate()`` — `weakWifi`'s
///   signal rule decides **alone**, because an idle link reads the 1 Mbit/s
///   beacon rate and the rate rule alone marks every quiet room weak.
/// - ``testAPhonesThreeRowsNeverLeaveABareDayLabel()`` — `todayDays` caps
///   before grouping, so a day whose rows were all cut leaves no heading.
/// - ``testADifferentServerIsADifferentHouse()`` — `TimerBook` is per server,
///   because every one of the previous house's timers is missing from the new
///   house's read and would read as having just fired.
///
/// The sixth, the cancel's double-tap guard, needs a server and is in
/// `HomeWritesTests`.
///
/// Where Android has a test of the same rule the name here is **its** name
/// (`HomeModelsTest.kt`), so a divergence is findable from either side.
@MainActor
final class HomeSectionRulesTests: XCTestCase {

    // MARK: - fixtures

    private let utc = TimeZone(secondsFromGMT: 0)!

    /// An ISO instant in UTC, the shape the web backend writes.
    private func iso(_ ms: Int64) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        return formatter.string(from: Date(timeIntervalSince1970: Double(ms) / 1000))
    }

    /// A UTC instant from its parts, so every fixture reads as a wall time
    /// rather than as a number nobody can check.
    private func at(
        _ year: Int, _ month: Int, _ day: Int,
        _ hour: Int = 0, _ minute: Int = 0
    ) -> Int64 {
        var parts = DateComponents()
        parts.year = year; parts.month = month; parts.day = day
        parts.hour = hour; parts.minute = minute
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        return Int64(calendar.date(from: parts)!.timeIntervalSince1970 * 1000)
    }

    /// Monday 28 September 2026, noon UTC — the map's own example day.
    private var noon: Int64 { at(2026, 9, 28, 12) }

    private func room(
        _ id: String,
        status: String? = "online",
        state: String? = nil,
        song: HomeSong? = nil,
        elapsed: Double? = nil,
        wifi: HomeWifi? = nil,
        label: String? = nil,
        satType: String = "voice",
        kioskAlive: Bool? = nil,
        lastConnectedAt: String? = nil,
        inCallWith: String? = nil
    ) -> HomeRoom {
        HomeRoom(
            roomID: id,
            status: status,
            lastConnectedAt: lastConnectedAt,
            nowPlaying: (state == nil && song == nil && elapsed == nil)
                ? nil
                : HomeNowPlaying(state: state, song: song, elapsedSec: elapsed),
            wifi: wifi,
            inCallWith: inCallWith,
            satType: satType,
            roomLabel: label,
            display: kioskAlive == nil ? nil : HomeDisplay(kioskAlive: kioskAlive)
        )
    }

    private func song(_ title: String, artist: String? = nil, duration: Double? = nil, file: String? = nil) -> HomeSong {
        HomeSong(title: title, artist: artist, file: file, durationSec: duration)
    }

    private func timer(
        _ id: Int64,
        expires: Int64?,
        created: Int64? = nil,
        label: String? = nil,
        message: String? = nil,
        room: String? = nil,
        reminder: Bool = false
    ) -> HomeTimer {
        HomeTimer(
            id: id,
            expiresAt: expires.map(iso),
            createdAt: created.map(iso),
            label: label,
            message: message,
            roomID: room,
            isReminder: reminder
        )
    }

    private func fire(
        _ id: Int64,
        firedAt: Int64?,
        due: Int64? = nil,
        heardIn: [String] = [],
        deliveries: [HomeFireDelivery] = [],
        summary: String? = nil,
        label: String? = nil,
        message: String? = nil,
        kind: String? = nil,
        reminder: Bool = false,
        room: String? = nil
    ) -> HomeFire {
        HomeFire(
            id: id,
            timerID: id,
            kind: kind,
            isReminder: reminder,
            label: label,
            message: message,
            roomID: room,
            createdAt: nil,
            dueAt: due.map(iso),
            firedAt: firedAt.map(iso),
            heardIn: heardIn,
            summary: summary,
            deliveries: deliveries
        )
    }

    private func event(
        _ id: Int64,
        starts: Int64,
        ends: Int64? = nil,
        title: String = "Dentist",
        location: String? = nil
    ) -> HomeEvent {
        HomeEvent(id: id, title: title, startsAt: iso(starts), endsAt: ends.map(iso), location: location)
    }

    // MARK: - fmtLeft

    /// `2d 3h` / `1h 5m` / `12m 04s` / `40s`, and the padding **only** in the
    /// `m`/`s` form.
    func testTheCountdownPadsSecondsOnlyBesideMinutes() {
        XCTAssertEqual("2d 3h", fmtLeft(2 * 86_400 + 3 * 3600 + 59))
        XCTAssertEqual("1d 0h", fmtLeft(86_400))
        XCTAssertEqual("1h 5m", fmtLeft(3600 + 5 * 60 + 7))
        XCTAssertEqual("1h 0m", fmtLeft(3600))
        XCTAssertEqual("12m 04s", fmtLeft(12 * 60 + 4))
        XCTAssertEqual("1m 00s", fmtLeft(60))
        XCTAssertEqual("1m 30s", fmtLeft(90))
        // The bare-seconds form is NOT padded: `04s` is not a time anybody
        // writes, and this is the assertion that catches a tidy-up that pads
        // both branches.
        XCTAssertEqual("4s", fmtLeft(4))
        XCTAssertEqual("40s", fmtLeft(40))
        XCTAssertEqual("59s", fmtLeft(59))
        XCTAssertEqual("0s", fmtLeft(0))
        // A server that has not caught up reads zero, never upwards.
        XCTAssertEqual("0s", fmtLeft(-30))
    }

    func testSecondsLeftIsRoundedAndNeverNegative() {
        let t = timer(1, expires: noon + 10_400)
        XCTAssertEqual(10, secondsLeft(t, nowMs: noon))
        XCTAssertEqual(0, secondsLeft(t, nowMs: noon + 60_000))
        XCTAssertEqual(0, secondsLeft(timer(2, expires: nil), nowMs: noon))
    }

    func testTheElapsedBarIsZeroWithoutBothEnds() {
        XCTAssertEqual(0, elapsedFraction(timer(1, expires: noon + 1000), nowMs: noon))
        XCTAssertEqual(0, elapsedFraction(timer(1, expires: noon, created: noon), nowMs: noon))
        XCTAssertEqual(
            0.5,
            elapsedFraction(timer(1, expires: noon + 10_000, created: noon), nowMs: noon + 5_000),
            accuracy: 0.0001
        )
        XCTAssertEqual(1, elapsedFraction(timer(1, expires: noon + 10_000, created: noon), nowMs: noon + 99_000))
    }

    // MARK: - what a timer is called

    /// A reminder's **words never reach a shared screen**; it reads
    /// `reminder · office` there, and `reminder · no room` with no room.
    func testASharedScreenNeverShowsAReminderesWords() {
        let r = timer(1, expires: noon, message: "take the tablets", room: "office", reminder: true)
        XCTAssertEqual("take the tablets", timerTitle(r, shared: false))
        XCTAssertEqual("reminder · office", timerTitle(r, shared: true))

        let roomless = timer(2, expires: noon, message: "ring Mum", reminder: true)
        XCTAssertEqual("reminder · no room", timerTitle(roomless, shared: true))

        // A timer's own label is low-risk and shows on a shared screen too —
        // the Kotlin's judgement, and not a simplification of it.
        let pasta = timer(3, expires: noon, label: "pasta")
        XCTAssertEqual("pasta", timerTitle(pasta, shared: true))
    }

    func testAReminderWithNoWordsIsJustTheWordReminder() {
        XCTAssertEqual("reminder", timerTitle(timer(1, expires: noon, reminder: true), shared: false))
        // An empty string is not a message.
        XCTAssertEqual(
            "reminder",
            timerTitle(timer(1, expires: noon, message: "", label: "", reminder: true), shared: false)
        )
        // The label is the fallback before the bare word.
        XCTAssertEqual(
            "tablets",
            timerTitle(timer(1, expires: noon, label: "tablets", reminder: true), shared: false)
        )
    }

    /// An unlabelled timer falls back to its length, and the two spellings
    /// differ: `40s timer` with no space, `3 min timer` with one.
    func testAnUnlabelledTimerIsNamedByItsLength() {
        XCTAssertEqual("40s timer", timerTitle(timer(1, expires: noon + 40_000, created: noon), shared: false))
        XCTAssertEqual("3 min timer", timerTitle(timer(1, expires: noon + 180_000, created: noon), shared: false))
        // 90 s is the boundary and goes to the minutes form: 1.5 rounds to 2.
        XCTAssertEqual("2 min timer", timerTitle(timer(1, expires: noon + 90_000, created: noon), shared: false))
        XCTAssertEqual("89s timer", timerTitle(timer(1, expires: noon + 89_000, created: noon), shared: false))
        // No span, or a span that is not positive, is the bare word.
        XCTAssertEqual("timer", timerTitle(timer(1, expires: noon + 1000), shared: false))
        XCTAssertEqual("timer", timerTitle(timer(1, expires: noon, created: noon), shared: false))
        XCTAssertEqual("timer", timerTitle(timer(1, expires: noon, created: noon + 5_000), shared: false))
    }

    /// The toast's noun is not the title: a reminder is the bare word even
    /// where its title would be its message, and a labelled timer carries the
    /// word `timer` even where its title would not.
    func testTheToastsNounDiffersFromTheTitle() {
        let r = timer(1, expires: noon, message: "take the tablets", room: "office", reminder: true)
        XCTAssertEqual("reminder", timerNoun(r, shared: false))
        XCTAssertEqual("reminder", timerNoun(r, shared: true))
        XCTAssertEqual("pasta timer", timerNoun(timer(2, expires: noon, label: "pasta"), shared: false))
        XCTAssertEqual(
            "40s timer",
            timerNoun(timer(3, expires: noon + 40_000, created: noon), shared: false)
        )
        XCTAssertEqual("cancel pasta timer", HomeTimerStrings.cancelLabel(timer(2, expires: noon, label: "pasta"), shared: false))
    }

    func testTheRoomLabelFallsBackToNoRoom() {
        XCTAssertEqual("no room", timerRoomLabel(nil))
        XCTAssertEqual("no room", timerRoomLabel(""))
        XCTAssertEqual("kitchen", timerRoomLabel("kitchen"))
        XCTAssertEqual("done · kitchen", doneLineTitle("kitchen"))
        XCTAssertEqual("done · no room", doneLineTitle(nil))
    }

    // MARK: - the fire ledger

    /// Heard somewhere wins outright, **including over a delivery still
    /// going**: somebody heard it, which is what the timer was for.
    func testAFireHeardSomewhereIsOkEvenWhileStillAnnouncing() {
        XCTAssertEqual(
            .ok,
            fireTone(fire(1, firedAt: noon, heardIn: ["kitchen"], deliveries: [
                HomeFireDelivery(roomID: "garage", outcome: "pending"),
            ]))
        )
        XCTAssertEqual(
            .warn,
            fireTone(fire(1, firedAt: noon, deliveries: [HomeFireDelivery(roomID: "garage", outcome: "sending")]))
        )
        XCTAssertEqual(
            .err,
            fireTone(fire(1, firedAt: noon, deliveries: [HomeFireDelivery(roomID: "garage", outcome: "failed")]))
        )
        XCTAssertEqual(.err, fireTone(fire(1, firedAt: noon)))
    }

    /// A fire's `kind` is read as well as its flag, so a server that records
    /// only the kind still gets the shared-screen masking.
    func testAFireWhoseOnlyReminderEvidenceIsItsKindIsStillAReminder() {
        let asTimer = fireAsTimer(fire(1, firedAt: noon, message: "tablets", kind: "reminder", room: "office"))
        XCTAssertTrue(asTimer.isReminder)
        XCTAssertEqual("reminder · office", timerTitle(asTimer, shared: true))
    }

    func testTheDoneLinesComeOffTheLedgerNewestFirstAndOnlyForAMinute() {
        let fires = [
            fire(1, firedAt: noon - 10_000, summary: "heard in kitchen", heardIn: ["kitchen"]),
            fire(2, firedAt: noon - 1_000, summary: ""),
            fire(3, firedAt: noon - 61_000, summary: "too old"),
            fire(4, firedAt: nil, summary: "never recorded a time"),
        ]
        let lines = fireDoneLines(fires, nowMs: noon)
        XCTAssertEqual([2, 1], lines.map(\.timer.id), "newest first, and the stale and undated ones dropped")
        XCTAssertEqual("heard in kitchen", lines[1].summary)
        XCTAssertNil(lines[0].summary, "an empty summary is no summary")
        XCTAssertEqual(.ok, lines[1].tone)
        XCTAssertEqual(.err, lines[0].tone)
        // Exactly on the boundary is out: `nowMs - at < HOME_DONE_MS`.
        XCTAssertTrue(fireDoneLines([fire(5, firedAt: noon - homeDoneMs)], nowMs: noon).isEmpty)
    }

    // MARK: - the timer view, and the one call that cannot be ordered wrongly

    func testTheActiveListIsTheReadsOrderAndOnlyTheFutureOnes() {
        let book = TimerBook()
        let list = HomeTimerList(
            serverNow: iso(noon),
            timers: [
                timer(1, expires: noon + 60_000),
                timer(2, expires: noon + 10_000),
                timer(3, expires: noon - 5_000),
                timer(4, expires: nil),
            ],
            fires: []
        )
        let view = book.timerView(of: list, serverNowMs: noon)
        XCTAssertEqual([1, 2], view.active.map(\.id), "the read's order, not sorted again")
        XCTAssertTrue(timersShown(view))
    }

    /// With a ledger present the book's guesses are **ignored**, and with no
    /// ledger at all they are all there is. `fires: []` is not `fires: nil`.
    func testTheLedgerWinsWhereThereIsOneAndTheBookWhereThereIsNot() {
        let due = timer(7, expires: noon - 1_000)

        let withLedger = TimerBook()
        _ = withLedger.timerView(of: HomeTimerList(timers: [due], fires: []), serverNowMs: noon - 2_000)
        let ledgerView = withLedger.timerView(of: HomeTimerList(timers: [], fires: []), serverNowMs: noon)
        XCTAssertTrue(ledgerView.done.isEmpty, "an empty ledger is an authoritative nothing")

        let withoutLedger = TimerBook()
        _ = withoutLedger.timerView(of: HomeTimerList(timers: [due], fires: nil), serverNowMs: noon - 2_000)
        let bookView = withoutLedger.timerView(of: HomeTimerList(timers: [], fires: nil), serverNowMs: noon)
        XCTAssertEqual([7], bookView.done.map(\.timer.id), "no ledger: the book's guess is all there is")
    }

    /// **The ordering trap.** Android feeds the book from a Compose
    /// `remember`, so it has seen the new read *before* the view is computed;
    /// an iOS `onChange` or `task` would run after the body and show the
    /// previous frame's done lines for one tick, every time.
    ///
    /// So there is one call. This test asserts the consequence: the very call
    /// that is handed the read a row has vanished from already reports the row
    /// as done. A design with `observe` and `view` as two public calls passes
    /// this only if the caller remembered the order, which is exactly what
    /// cannot be tested from here.
    func testTheReadThatLosesARowReportsItDoneOnTheSameCall() {
        let book = TimerBook()
        let due = timer(11, expires: noon + 1_000)
        let first = book.timerView(of: HomeTimerList(timers: [due], fires: nil), serverNowMs: noon)
        XCTAssertEqual([11], first.active.map(\.id))
        XCTAssertTrue(first.done.isEmpty)

        // One call, with the row gone. No second call, no tick in between.
        let second = book.timerView(of: HomeTimerList(timers: [], fires: nil), serverNowMs: noon + 1_500)
        XCTAssertEqual([11], second.done.map(\.timer.id), "done on the call that brought the news")
    }

    /// A redraw with the same read must not re-observe and must not change
    /// anything — the `remember(timers.data)` key, as a value comparison.
    func testARedrawWithTheSameReadChangesNothing() {
        let book = TimerBook()
        let list = HomeTimerList(timers: [timer(1, expires: noon + 1_000)], fires: nil)
        _ = book.timerView(of: list, serverNowMs: noon)
        let again = book.timerView(of: list, serverNowMs: noon + 500)
        XCTAssertEqual([1], again.active.map(\.id))
        XCTAssertTrue(again.done.isEmpty, "nothing vanished, so nothing fired")
    }

    /// A row that is gone and was due within two seconds **fired**; one that
    /// vanished well before its time was cancelled and leaves no line.
    func testARowThatVanishesEarlyWasCancelledNotFired() {
        let book = TimerBook()
        let soon = timer(1, expires: noon + 1_000)
        let later = timer(2, expires: noon + 600_000)
        _ = book.timerView(of: HomeTimerList(timers: [soon, later], fires: nil), serverNowMs: noon)

        let view = book.timerView(of: HomeTimerList(timers: [], fires: nil), serverNowMs: noon)
        XCTAssertEqual([1], view.done.map(\.timer.id), "due in a second: it fired")
        XCTAssertFalse(view.done.contains { $0.timer.id == 2 }, "due in ten minutes: somebody cancelled it")
    }

    func testATimerThisPhoneCancelledNeverReadsDone() {
        let book = TimerBook()
        let soon = timer(1, expires: noon + 1_000)
        _ = book.timerView(of: HomeTimerList(timers: [soon], fires: nil), serverNowMs: noon)
        XCTAssertTrue(book.beginCancel(1))
        book.settleCancel(1, outcome: .cancelled("cancelled 40s timer"))

        let view = book.timerView(of: HomeTimerList(timers: [], fires: nil), serverNowMs: noon)
        XCTAssertTrue(view.done.isEmpty, "this phone cancelled it, so it did not go off")
    }

    /// A row still in the read whose time has passed is a done line too — a
    /// server that has not yet deleted a fired timer still says it went off.
    func testARowStillInTheReadButPastIsDone() {
        let book = TimerBook()
        let view = book.timerView(
            of: HomeTimerList(timers: [timer(1, expires: noon - 5_000)], fires: nil),
            serverNowMs: noon
        )
        XCTAssertTrue(view.active.isEmpty)
        XCTAssertEqual([1], view.done.map(\.timer.id))
    }

    func testADoneLineLastsAMinuteAndThenGoes() {
        let book = TimerBook()
        _ = book.timerView(of: HomeTimerList(timers: [timer(1, expires: noon)], fires: nil), serverNowMs: noon - 1_000)
        XCTAssertEqual(
            [1],
            book.timerView(of: HomeTimerList(timers: [], fires: nil), serverNowMs: noon + 30_000).done.map(\.timer.id)
        )
        XCTAssertTrue(
            book.timerView(of: HomeTimerList(timers: [], fires: nil), serverNowMs: noon + homeDoneMs).done.isEmpty,
            "a minute after it fired, it is gone"
        )
    }

    /// **A different server is a different house: nothing it never had can
    /// have fired** (`HomeScreen.kt:231`).
    ///
    /// Every timer of the previous house is missing from the new house's
    /// read, so a book carried across the switch would announce all of them
    /// as having just gone off — on a screen whose rooms, name and calendar
    /// all belong to somebody else.
    func testADifferentServerIsADifferentHouse() {
        let book = TimerBook()
        let theirs = [
            timer(1, expires: noon + 500, label: "pasta"),
            timer(2, expires: noon + 1_500, message: "tablets", reminder: true),
        ]
        _ = book.timerView(of: HomeTimerList(timers: theirs, fires: nil), serverNowMs: noon)
        XCTAssertTrue(book.beginCancel(1))

        // The server changed. This is what `remember(serverUrl) { TimerBook() }`
        // does, and the only reason it is a method here is that a Swift
        // instance outlives a composition.
        book.forget()

        let view = book.timerView(of: HomeTimerList(timers: [], fires: nil), serverNowMs: noon + 1_000)
        XCTAssertTrue(view.done.isEmpty, "the new house never had these timers")
        XCTAssertTrue(book.cancelled.isEmpty, "nor this phone's cancel of one of them")

        // And the opposite, so the test is about `forget` and not about an
        // empty book: without it, both rows read as having fired.
        let kept = TimerBook()
        _ = kept.timerView(of: HomeTimerList(timers: theirs, fires: nil), serverNowMs: noon)
        XCTAssertEqual(
            2,
            kept.timerView(of: HomeTimerList(timers: [], fires: nil), serverNowMs: noon + 1_000).done.count
        )
    }

    func testTheRoomChipTakesTheSoonestTimerInEachRoom() {
        let left = timerLeftByRoom(
            [
                timer(1, expires: noon + 30_000, room: "kitchen"),
                timer(2, expires: noon + 90_000, room: "kitchen"),
                timer(3, expires: noon + 10_000, room: "office"),
                timer(4, expires: noon + 10_000),
            ],
            nowMs: noon
        )
        XCTAssertEqual(30, left["kitchen"], "the read is soonest-first, so the first wins")
        XCTAssertEqual(10, left["office"])
        XCTAssertNil(left["no room"], "a timer with no room is in no room's chip")
    }

    // MARK: - the Wi-Fi push

    func testAPushThatIsNotAnObjectChangesNothing() {
        XCTAssertNil(decodeWifiPush(StateEvent(type: homeWifiEvent, frame: #"{"type":"x","data":[1,2]}"#)))
        XCTAssertNil(decodeWifiPush(StateEvent(type: homeWifiEvent, frame: #"{"type":"x","data":7}"#)))
        XCTAssertNil(decodeWifiPush(StateEvent(type: homeWifiEvent, frame: #"{"type":"x"}"#)))
        XCTAssertNil(decodeWifiPush(StateEvent(type: homeWifiEvent, frame: #"{"type":"x","data":null}"#)))
    }

    func testAPushSkipsTheRoomsItCannotReadAndKeepsTheRest() {
        let push = decodeWifiPush(StateEvent(type: homeWifiEvent, frame: """
            {"type":"satellites.wifi.changed","data":{
              "kitchen":{"rx_mbits":48.5,"tx_mbits":12.0,"signal_dbm":-55.0,"mode":"sta"},
              "garage":7,
              "hall":"nonsense",
              "shed":null,
              "loft":[],
              "study":{}
            }}
            """))
        XCTAssertEqual(["kitchen", "study"], push.map { $0.keys.sorted() })
        XCTAssertEqual(48.5, push?["kitchen"]?.rxMbits)
        XCTAssertEqual("sta", push?["kitchen"]?.mode)
        XCTAssertEqual(HomeWifi(), push?["study"], "an empty object is a report that says nothing")
    }

    /// **A push older than the last read is discarded.** The read is the
    /// complete truth as of its own instant, so an older push can only put
    /// back a link it has already superseded.
    func testAPushOlderThanTheReadIsDiscarded() {
        let kitchen = room("kitchen", wifi: HomeWifi(rxMbits: 50, signalDbm: -40))
        let stale = WifiPush(receivedAtMs: 1_000, rooms: ["kitchen": HomeWifi(rxMbits: 1)])
        let fresh = WifiPush(receivedAtMs: 3_000, rooms: ["kitchen": HomeWifi(rxMbits: 1)])

        XCTAssertFalse(stale.applies(toReadAt: 2_000))
        XCTAssertTrue(fresh.applies(toReadAt: 2_000))
        // Same millisecond: the push wins, because it is the later event in
        // the house even where the clock cannot tell them apart.
        XCTAssertTrue(WifiPush(receivedAtMs: 2_000, rooms: [:]).applies(toReadAt: 2_000))

        XCTAssertEqual(
            50,
            homeRooms(read: [kitchen], readAtMs: 2_000, push: stale).first?.wifi?.rxMbits
        )
        XCTAssertEqual(
            1,
            homeRooms(read: [kitchen], readAtMs: 2_000, push: fresh).first?.wifi?.rxMbits
        )
        XCTAssertTrue(homeRooms(read: nil, readAtMs: 2_000, push: fresh).isEmpty)
    }

    /// `mergeWifi` copies **four** fields, nulls included — map §11.3, where
    /// the Kotlin's own doc and its test name both say two.
    func testAWifiPushCopiesFourFieldsAndNotTwo() {
        let before = room("kitchen", wifi: HomeWifi(rxMbits: 10, txMbits: 2, signalDbm: -40, mode: "sta"))
        let after = mergeWifi([before], ["kitchen": HomeWifi(rxMbits: 48, txMbits: 12, signalDbm: -55, mode: "ap")])
        XCTAssertEqual(HomeWifi(rxMbits: 48, txMbits: 12, signalDbm: -55, mode: "ap"), after.first?.wifi)
    }

    /// The consequence the Kotlin's comment hides: a push that omits
    /// `signal_dbm` **erases** the signal the read reported, which moves
    /// ``weakWifi(_:)`` from its signal rule to its rate rule.
    func testAPushWithoutASignalErasesTheSignalTheReadGave() {
        let strong = room("kitchen", wifi: HomeWifi(rxMbits: 1, signalDbm: -40))
        XCTAssertFalse(weakWifi(strong), "a good signal, and an idle link's beacon rate")

        let merged = mergeWifi([strong], ["kitchen": HomeWifi(rxMbits: 1, txMbits: 1)])
        XCTAssertNil(merged.first?.wifi?.signalDbm)
        XCTAssertTrue(
            weakWifi(merged.first!),
            "with the signal erased the rate decides, and an idle link reads 1 Mbit/s"
        )
    }

    func testAPushLeavesTheRoomsItDoesNotNameAlone() {
        let rooms = [room("kitchen", wifi: HomeWifi(rxMbits: 10)), room("office", wifi: HomeWifi(rxMbits: 20))]
        let after = mergeWifi(rooms, ["kitchen": HomeWifi(rxMbits: 1)])
        XCTAssertEqual(1, after[0].wifi?.rxMbits)
        XCTAssertEqual(20, after[1].wifi?.rxMbits)
        XCTAssertEqual(rooms, mergeWifi(rooms, nil))
        XCTAssertEqual(rooms, mergeWifi(rooms, [:]))
    }

    func testAPushGivesARoomWithNoLinkOne() {
        let after = mergeWifi([room("kitchen")], ["kitchen": HomeWifi(rxMbits: 5, mode: "sta")])
        XCTAssertEqual(HomeWifi(rxMbits: 5, mode: "sta"), after.first?.wifi)
    }

    // MARK: - order, grouping and the song

    func testRoomsSortPlayingPausedQuietWaitingOffline() {
        let playing = room("z-playing", state: "play", song: song("a"))
        let paused = room("y-paused", state: "pause", song: song("a"))
        let quiet = room("x-quiet")
        let waiting = room("w-waiting", status: "waiting")
        let offline = room("v-offline", status: "offline")
        XCTAssertEqual([0, 1, 2, 3, 4], [playing, paused, quiet, waiting, offline].map(roomRank))
        XCTAssertEqual(
            ["z-playing", "y-paused", "x-quiet", "w-waiting", "v-offline"],
            sortRooms([offline, waiting, quiet, paused, playing]).map(\.roomID)
        )
        // Within a rank, by room_id, which is what keeps the list still
        // between reads.
        XCTAssertEqual(
            ["a", "b", "c"],
            sortRooms([room("c"), room("a"), room("b")]).map(\.roomID)
        )
    }

    /// A stopped room does not sort as if it were playing the track its
    /// player still names.
    func testAStoppedRoomDoesNotRankAsPlaying() {
        XCTAssertEqual(2, roomRank(room("kitchen", state: "stop", song: song("a"))))
        // Nor does a playing room with no song: there is nothing to show.
        XCTAssertEqual(2, roomRank(room("kitchen", state: "play")))
    }

    func testAHouseWithNoLabelsGetsOneGroupAndNoHeading() {
        let groups = groupRooms(sortRooms([room("kitchen"), room("office")]))
        XCTAssertEqual(1, groups.count)
        XCTAssertNil(groups[0].label, "one heading reading `ungrouped` over the whole house says nothing")
        XCTAssertEqual(["kitchen", "office"], groups[0].rooms.map(\.roomID))
    }

    func testLabelsSortAToZWithTheLooseRoomsLast() {
        let rooms = sortRooms([
            room("kitchen", label: "downstairs"),
            room("loft"),
            room("bath", label: "upstairs"),
            room("study", label: "downstairs"),
            room("shed", label: ""),
        ])
        let groups = groupRooms(rooms)
        XCTAssertEqual(["downstairs", "upstairs", "ungrouped"], groups.map { $0.label })
        XCTAssertEqual(["kitchen", "study"], groups[0].rooms.map(\.roomID))
        XCTAssertEqual(["bath"], groups[1].rooms.map(\.roomID))
        XCTAssertEqual(["loft", "shed"], groups[2].rooms.map(\.roomID), "an empty label is no label")
    }

    /// **A stopped room is quiet, not playing its last song** (2026-10-11).
    ///
    /// `/api/satellites` keeps naming the loaded track with `state: "stop"`
    /// beside it, and reading the song without reading the state made a room
    /// nobody was listening to show that track at `0:00` on Home while Music
    /// and Satellites both said nothing. The same read arrives on iOS.
    func testAStoppedRoomIsQuietNotPlayingItsLastSong() {
        let loaded = song("Teardrop", artist: "Massive Attack", duration: 330)
        XCTAssertNil(roomSong(room("kitchen", state: "stop", song: loaded)))
        XCTAssertEqual(.quiet, roomSecondLine(room("kitchen", state: "stop", song: loaded)))
        XCTAssertNil(roomPill(room("kitchen", state: "stop", song: loaded), stale: false))
        XCTAssertEqual([], roomTransport(room("kitchen", state: "stop", song: loaded), stale: false))
        XCTAssertEqual(0, roomElapsedSec(room("kitchen", state: "stop", song: loaded, elapsed: 14), stale: false, sinceReadSec: 9))

        // …and the two states that do have a song, so the test is about the
        // stop and not about the function always answering nil.
        XCTAssertEqual(loaded, roomSong(room("kitchen", state: "play", song: loaded)))
        XCTAssertEqual(loaded, roomSong(room("kitchen", state: "pause", song: loaded)))
        // An offline room is not playing what it was playing when it left.
        XCTAssertNil(roomSong(room("kitchen", status: "offline", state: "play", song: loaded)))
        XCTAssertNil(roomSong(room("kitchen", state: "play")))
    }

    /// **The clamp, which is why `0:14 / 0:13` stopped happening.**
    ///
    /// Between a song ending and the read that brings the next one, the
    /// elapsed count used to run past the end.
    func testTheElapsedCountNeverRunsPastTheSongsEnd() {
        let short = song("Her Majesty", duration: 23)
        let playing = room("kitchen", state: "play", song: short, elapsed: 20)
        XCTAssertEqual(23, roomElapsedSec(playing, stale: false, sinceReadSec: 30), accuracy: 0.0001)
        XCTAssertEqual(22, roomElapsedSec(playing, stale: false, sinceReadSec: 2), accuracy: 0.0001)
        // The bar cannot overflow either.
        XCTAssertEqual(1, roomProgress(playing, elapsedSec: 99), accuracy: 0.0001)
        // A stream with no duration has no end to clamp to.
        let stream = room("kitchen", state: "play", song: song("BBC 6", duration: 0), elapsed: 20)
        XCTAssertEqual(50, roomElapsedSec(stream, stale: false, sinceReadSec: 30), accuracy: 0.0001)
    }

    /// The other half of the same rule: the clock runs **only while playing
    /// and not stale**. A paused room's position does not advance, and a
    /// stale read is a reading of where the song was.
    func testTheElapsedCountOnlyRunsWhilePlayingAndNotStale() {
        let track = song("Teardrop", duration: 330)
        XCTAssertEqual(
            40,
            roomElapsedSec(room("kitchen", state: "play", song: track, elapsed: 10), stale: false, sinceReadSec: 30),
            accuracy: 0.0001
        )
        XCTAssertEqual(
            10,
            roomElapsedSec(room("kitchen", state: "play", song: track, elapsed: 10), stale: true, sinceReadSec: 30),
            accuracy: 0.0001
        )
        XCTAssertEqual(
            10,
            roomElapsedSec(room("kitchen", state: "pause", song: track, elapsed: 10), stale: false, sinceReadSec: 30),
            accuracy: 0.0001
        )
    }

    func testTheSongLineAndItsFallbacks() {
        XCTAssertEqual("Teardrop · Massive Attack", songLine(song("Teardrop", artist: "Massive Attack")))
        XCTAssertEqual("Teardrop", songLine(song("Teardrop")))
        XCTAssertEqual("Teardrop", songLine(song("Teardrop", artist: "")))
        XCTAssertEqual("track03.flac", songTitle(HomeSong(file: "/music/albums/track03.flac")))
        XCTAssertEqual("unknown", songTitle(nil))
        XCTAssertEqual("unknown", songTitle(HomeSong(title: "", file: "")))
        // `fmtDur`'s em dash, which is what the row shows for an unknown
        // length.
        XCTAssertEqual("—", fmtDur(nil))
        XCTAssertEqual("5:30", fmtDur(330))
        XCTAssertEqual("1:02:33", fmtDur(3753))
        XCTAssertEqual("0:07", fmtDur(7))
        XCTAssertEqual("—", fmtDur(.nan))
        XCTAssertEqual("—", fmtDur(-1))
    }

    // MARK: - weak Wi-Fi

    /// **A room that reports a signal is judged by its signal and never by
    /// its rate.** "An idle link reads the 1 Mbit/s beacon rate", so a rule
    /// that consulted the rate as well would put `weak wi-fi` on every quiet
    /// room in the house.
    func testARoomThatReportsASignalIsJudgedBySignalNotRate() {
        // The case the rule is for: a strong signal and a 1 Mbit/s idle link.
        XCTAssertFalse(weakWifi(room("kitchen", wifi: HomeWifi(rxMbits: 1, txMbits: 1, signalDbm: -45))))
        // …and a weak signal on a busy link, which is weak anyway.
        XCTAssertTrue(weakWifi(room("kitchen", wifi: HomeWifi(rxMbits: 80, txMbits: 40, signalDbm: -75))))
        // The band's edge: below −70, not at it.
        XCTAssertFalse(weakWifi(room("kitchen", wifi: HomeWifi(signalDbm: -70))))
        XCTAssertTrue(weakWifi(room("kitchen", wifi: HomeWifi(signalDbm: -70.1))))
        // Only with no signal at all does the rate decide.
        XCTAssertTrue(weakWifi(room("kitchen", wifi: HomeWifi(rxMbits: 1))))
        XCTAssertFalse(weakWifi(room("kitchen", wifi: HomeWifi(rxMbits: 5))))
        // The radio hosting the setup hotspot is weak whatever it reports.
        XCTAssertTrue(weakWifi(room("kitchen", wifi: HomeWifi(rxMbits: 300, signalDbm: -30, mode: "ap"))))
        // No report, and an offline room's last link, are not news.
        XCTAssertFalse(weakWifi(room("kitchen")))
        XCTAssertFalse(weakWifi(room("kitchen", status: "offline", wifi: HomeWifi(signalDbm: -90))))
    }

    func testADeadScreenNeedsAllThreeFacts() {
        XCTAssertTrue(kioskDead(room("hall", satType: "video", kioskAlive: false)))
        XCTAssertFalse(kioskDead(room("hall", satType: "video", kioskAlive: true)))
        XCTAssertFalse(kioskDead(room("hall", satType: "video")), "no report is not a dead screen")
        XCTAssertFalse(kioskDead(room("hall", kioskAlive: false)), "a voice room has no screen")
        XCTAssertFalse(kioskDead(room("hall", status: "offline", satType: "video", kioskAlive: false)))
    }

    // MARK: - the row's pill, line and buttons

    /// One pill, first match wins, and **nothing at all for an online quiet
    /// room** — a sixth case would badge every idle room in the house.
    func testOnlyOnePillAndNoneForAQuietRoom() {
        let track = song("Teardrop", duration: 330)
        XCTAssertEqual(.playing, roomPill(room("k", state: "play", song: track), stale: false))
        XCTAssertEqual(.paused, roomPill(room("k", state: "pause", song: track), stale: false))
        XCTAssertEqual(.waiting, roomPill(room("k", status: "waiting"), stale: false))
        XCTAssertEqual(.offline, roomPill(room("k", status: "offline"), stale: false))
        XCTAssertNil(roomPill(room("k"), stale: false))
        // With the core away, everything reads `last known` — tested first,
        // before playing, because nothing has confirmed the room since.
        XCTAssertEqual(.lastKnown, roomPill(room("k", state: "play", song: track), stale: true))
        XCTAssertEqual(.lastKnown, roomPill(room("k", status: "offline"), stale: true))
        XCTAssertEqual(
            ["last known", "playing", "paused", "waiting", "offline"],
            RoomPill.allCases.map(\.label)
        )
        XCTAssertEqual([.idle, .brand, .idle, .warn, .idle], RoomPill.allCases.map(\.tone))
        XCTAssertEqual([false, true, false, false, false], RoomPill.allCases.map(\.live))
    }

    func testTheSecondLineSaysWhereTheRoomIs() {
        let track = song("Teardrop")
        XCTAssertEqual(.song(track), roomSecondLine(room("k", state: "play", song: track)))
        XCTAssertEqual(.quiet, roomSecondLine(room("k")))
        // An online room reads `quiet` whatever its status field says, which
        // is the Kotlin's branch order.
        XCTAssertEqual(.quiet, roomSecondLine(room("k", status: "online", lastConnectedAt: iso(noon))))
        XCTAssertEqual(.notConnectedYet, roomSecondLine(room("k", status: "waiting")))
        XCTAssertEqual(
            .lastSeen(iso(noon)),
            roomSecondLine(room("k", status: "offline", lastConnectedAt: iso(noon)))
        )
        XCTAssertEqual(.neverConnected, roomSecondLine(room("k", status: "offline")))
        XCTAssertEqual("set up, not connected yet", RoomSecondLine.notConnectedYet.text)
        XCTAssertEqual("last seen 5m ago", lastSeenLine(iso(noon), nowMs: noon + 300_000))
        XCTAssertEqual("last seen —", lastSeenLine(nil, nowMs: noon))
    }

    /// Nothing for a quiet room: "starting something in a room belongs to the
    /// Music page, which picks the room and the track together".
    func testTheTransportButtonsAppearOnlyWhereThereIsSomethingToStop() {
        let track = song("Teardrop")
        XCTAssertEqual(["pause", "stop"], roomTransport(room("k", state: "play", song: track), stale: false))
        XCTAssertEqual(["resume", "stop"], roomTransport(room("k", state: "pause", song: track), stale: false))
        XCTAssertEqual([], roomTransport(room("k"), stale: false), "a play button here would have to invent what to play")
        XCTAssertEqual([], roomTransport(room("k", state: "play", song: track), stale: true))
        XCTAssertEqual([], roomTransport(room("k", status: "offline", state: "play", song: track), stale: false))
        XCTAssertEqual("pause kitchen", transportLabel("pause", room: "kitchen"))
    }

    func testTheCallChipOnlyShowsForAnOnlineRoom() {
        XCTAssertEqual("in call with Mum", inCallChip(room("k", inCallWith: "Mum")))
        XCTAssertNil(inCallChip(room("k", status: "offline", inCallWith: "Mum")))
        XCTAssertNil(inCallChip(room("k")))
    }

    // MARK: - stop all

    func testStopAllTakesASecondTapInsideFourSeconds() {
        let arm = StopAllArm()
        XCTAssertFalse(arm.isArmed(nowMs: noon))
        let (armed, firedOnFirst) = arm.tap(nowMs: noon)
        XCTAssertFalse(firedOnFirst, "the first tap asks")
        XCTAssertTrue(armed.isArmed(nowMs: noon + 3_999))
        XCTAssertFalse(armed.isArmed(nowMs: noon + homeStopAllArmMs), "and disarms itself")

        let (after, fired) = armed.tap(nowMs: noon + 1_000)
        XCTAssertTrue(fired, "the second does it")
        XCTAssertNil(after.armedAtMs, "and leaves it unarmed")

        // A tap after the window arms again rather than firing.
        let (rearmed, lateFire) = armed.tap(nowMs: noon + 5_000)
        XCTAssertFalse(lateFire)
        XCTAssertTrue(rearmed.isArmed(nowMs: noon + 5_000))
    }

    /// `stop $count rooms?` is **always plural**, including for one — map
    /// §11.5, ported as it is because `android/` is not this batch's to fix.
    func testTheStopAllLabelIsHardCodedPluralAsTheKotlinsIs() {
        let armed = StopAllArm(armedAtMs: noon)
        XCTAssertEqual("stop all", StopAllArm().label(count: 3, busy: false, nowMs: noon))
        XCTAssertEqual("stop 3 rooms?", armed.label(count: 3, busy: false, nowMs: noon))
        XCTAssertEqual("stop 1 rooms?", armed.label(count: 1, busy: false, nowMs: noon), "the Kotlin's plural, kept")
        XCTAssertEqual("stopping…", armed.label(count: 3, busy: true, nowMs: noon))
    }

    func testStopAllOffersNothingWhileTheCoreIsAway() {
        let track = song("Teardrop")
        let rooms = [
            room("kitchen", state: "play", song: track),
            room("office", state: "play", song: track),
            room("hall", state: "pause", song: track),
        ]
        XCTAssertEqual(["kitchen", "office"], stopAllCandidates(rooms, stale: false))
        XCTAssertEqual([], stopAllCandidates(rooms, stale: true), "what the server said last is not evidence")
        XCTAssertTrue(stopAllShown(candidates: ["kitchen", "office"], stopping: false))
        XCTAssertFalse(stopAllShown(candidates: ["kitchen"], stopping: false), "two or more, or a batch in flight")
        XCTAssertTrue(stopAllShown(candidates: [], stopping: true))
    }

    // MARK: - today

    /// `9:00am`: **not** a locale formatter. A 24-hour locale still gets
    /// this, because the house's dashboard writes it this way.
    func testTheClockIsAlwaysTwelveHourAndLowerCase() {
        XCTAssertEqual("9:00am", homeClock(ms: at(2026, 9, 28, 9), timeZone: utc))
        XCTAssertEqual("12:00am", homeClock(ms: at(2026, 9, 28, 0), timeZone: utc))
        XCTAssertEqual("12:30pm", homeClock(ms: at(2026, 9, 28, 12, 30), timeZone: utc))
        XCTAssertEqual("11:59pm", homeClock(ms: at(2026, 9, 28, 23, 59), timeZone: utc))
        XCTAssertEqual("1:05pm", homeClock(ms: at(2026, 9, 28, 13, 5), timeZone: utc))
        // No space, lower case, and the minute is always two digits.
        XCTAssertFalse(homeClock(ms: noon, timeZone: utc).contains(" "))
        // The zone is the parameter it should be.
        XCTAssertEqual(
            "7:00am",
            homeClock(ms: at(2026, 9, 28, 12), timeZone: TimeZone(secondsFromGMT: -5 * 3600)!)
        )
    }

    func testAnEventWithNoEndRunsAnHour() {
        XCTAssertEqual(noon + 3_600_000, eventEndMs(event(1, starts: noon)))
        XCTAssertEqual(noon + 600_000, eventEndMs(event(1, starts: noon, ends: noon + 600_000)))
        // An unreadable start sorts last and is filtered out by every window.
        let broken = HomeEvent(id: 9, title: "?", startsAt: "not a date")
        XCTAssertEqual(Int64.max, eventStartMs(broken))
        XCTAssertEqual(Int64.max, eventEndMs(broken))
    }

    /// **The cap comes before the grouping**, so a day whose every row was
    /// cut leaves no bare label behind (`aPhonesThreeRowsNeverLeaveABareDayLabel`).
    ///
    /// Capping per day instead shows `today` with its three rows and then the
    /// word `tomorrow` with nothing under it — a heading promising rows the
    /// cap has already taken.
    func testAPhonesThreeRowsNeverLeaveABareDayLabel() {
        let events = [
            event(1, starts: noon + 3_600_000, title: "one"),
            event(2, starts: noon + 7_200_000, title: "two"),
            event(3, starts: noon + 10_800_000, title: "three"),
            event(4, starts: at(2026, 9, 29, 9), title: "tomorrow one"),
            event(5, starts: at(2026, 9, 29, 10), title: "tomorrow two"),
        ]
        let days = todayDays(events: events, nowMs: noon, timeZone: utc, shared: false, limit: homePhoneRows)
        XCTAssertEqual(["today"], days.map(\.label), "tomorrow's rows were all cut, so its heading is gone too")
        XCTAssertEqual(["one", "two", "three"], days[0].rows.map(\.title))

        // Six rows reach tomorrow, which is what makes the test above about
        // the order of the cap and not about tomorrow never appearing.
        let wide = todayDays(events: events, nowMs: noon, timeZone: utc, shared: false, limit: homeTodayRows)
        XCTAssertEqual(["today", "tomorrow"], wide.map(\.label))
        XCTAssertEqual(["tomorrow one", "tomorrow two"], wide[1].rows.map(\.title))
    }

    func testTodayDropsWhatIsOverAndWhatIsTooFarOff() {
        let events = [
            event(1, starts: noon - 7_200_000, ends: noon - 3_600_000, title: "over"),
            event(2, starts: noon - 1_800_000, ends: noon + 1_800_000, title: "under way"),
            event(3, starts: at(2026, 9, 30, 9), title: "the day after tomorrow"),
        ]
        let days = todayDays(events: events, nowMs: noon, timeZone: utc, shared: false)
        XCTAssertEqual(["under way"], days.flatMap { $0.rows }.map(\.title))
        XCTAssertTrue(days[0].rows[0].running, "it started before now and has not ended")

        // The one that is over is dropped from the rows but is still what
        // makes the day read `nothing more today` once the rest are gone.
        XCTAssertEqual(
            "nothing more today",
            todayEmptyText(
                events: [events[0]], nowMs: noon, timeZone: utc, shared: false
            )
        )
    }

    func testASharedScreenShowsBusyAndNoLocationAtAll() {
        let events = [event(1, starts: noon + 600_000, title: "Dentist", location: "Market Street")]
        let own = todayDays(events: events, nowMs: noon, timeZone: utc, shared: false)
        XCTAssertEqual("Dentist", own[0].rows[0].title)
        XCTAssertEqual("Market Street", own[0].rows[0].location)

        let hall = todayDays(events: events, nowMs: noon, timeZone: utc, shared: true)
        XCTAssertEqual("busy", hall[0].rows[0].title)
        XCTAssertNil(hall[0].rows[0].location, "dropped entirely, not blanked")

        // An empty location is no location on this phone either.
        let blank = todayDays(
            events: [event(2, starts: noon + 600_000, location: "")],
            nowMs: noon, timeZone: utc, shared: false
        )
        XCTAssertNil(blank[0].rows[0].location)
    }

    func testARowsSecondTimeIsOnlyThereWhenTheServerGaveOne() {
        let rows = todayDays(
            events: [event(1, starts: noon + 600_000), event(2, starts: noon + 700_000, ends: noon + 900_000)],
            nowMs: noon, timeZone: utc, shared: false
        )[0].rows
        XCTAssertNil(rows[0].endMs, "the invented hour is for the filtering, not for the row")
        XCTAssertEqual(noon + 900_000, rows[1].endMs)
    }

    func testTheEmptyLineSaysWhetherTheDayIsOverAndWhatIsNext() {
        XCTAssertEqual(
            "nothing on today",
            todayEmptyText(events: [], nowMs: noon, timeZone: utc, shared: false)
        )
        XCTAssertEqual(
            "nothing more today",
            todayEmptyText(
                events: [event(1, starts: noon - 7_200_000, ends: noon - 3_600_000)],
                nowMs: noon, timeZone: utc, shared: false
            )
        )
        XCTAssertEqual(
            "nothing on today · next: wed 30 sep 9:00am Dentist",
            todayEmptyText(
                events: [event(1, starts: at(2026, 9, 30, 9))],
                nowMs: noon, timeZone: utc, shared: false
            )
        )
        XCTAssertEqual(
            "nothing on today · next: wed 30 sep 9:00am busy",
            todayEmptyText(
                events: [event(1, starts: at(2026, 9, 30, 9))],
                nowMs: noon, timeZone: utc, shared: true
            )
        )
        // Tomorrow is not "next": it has its own day in `todayDays`.
        XCTAssertEqual(
            "nothing on today",
            todayEmptyText(
                events: [event(1, starts: at(2026, 9, 29, 9))],
                nowMs: noon, timeZone: utc, shared: false
            )
        )
    }

    // MARK: - the header's counts

    /// **nil rooms and empty rooms are different.** nil means "no room parts
    /// at all, and the timer parts still count"; empty means the one part
    /// `no rooms yet`.
    ///
    /// Collapsing them is map §11.2 in a new place — the same screen's
    /// attention card reports that none of the rooms is offline when the rooms
    /// read has failed, because it is handed `orEmpty()`.
    func testTheStatusLineTellsNoRoomsApartFromNoRoomsRead() {
        let timers = [timer(1, expires: noon + 60_000), timer(2, expires: noon + 70_000, reminder: true)]

        XCTAssertEqual(
            ["1 timer", "1 reminder"],
            statusLine(rooms: nil, coreDown: false, active: timers),
            "a failed rooms read is no reason to stop counting the timers"
        )
        XCTAssertEqual(
            ["no rooms yet", "1 timer", "1 reminder"],
            statusLine(rooms: [], coreDown: false, active: timers),
            "an empty house is a statement about the house"
        )
        XCTAssertEqual([], statusLine(rooms: nil, coreDown: false, active: []))
    }

    /// The order, pinned verbatim on Android by
    /// `statusLineCountsFromTheSections`.
    func testTheStatusLineCountsFromTheSections() {
        let track = song("Teardrop")
        let rooms = [
            room("kitchen", state: "play", song: track),
            room("office", state: "play", song: track),
            room("hall"),
            room("shed", status: "offline"),
            room("loft", status: "waiting"),
        ]
        XCTAssertEqual(
            ["3 online", "1 offline", "1 waiting", "2 playing"],
            statusLine(rooms: rooms, coreDown: false, active: [])
        )
        // `last known` first, and playing forced to 0, while the core is down.
        XCTAssertEqual(
            ["last known", "3 online", "1 offline", "1 waiting"],
            statusLine(rooms: rooms, coreDown: true, active: [])
        )
        // The zero branches are absent rather than zero.
        XCTAssertEqual(["1 online"], statusLine(rooms: [room("kitchen")], coreDown: false, active: []))
        // …and the plurals.
        XCTAssertEqual(
            ["1 online", "2 timers", "3 reminders"],
            statusLine(
                rooms: [room("kitchen")],
                coreDown: false,
                active: [
                    timer(1, expires: noon), timer(2, expires: noon),
                    timer(3, expires: noon, reminder: true),
                    timer(4, expires: noon, reminder: true),
                    timer(5, expires: noon, reminder: true),
                ]
            )
        )
    }

    func testPluralCountsAndPicksTheWord() {
        XCTAssertEqual("1 room", plural(1, "room"))
        XCTAssertEqual("3 rooms", plural(3, "room"))
        XCTAssertEqual("0 rooms", plural(0, "room"))
        XCTAssertEqual("2 boxes", plural(2, "box", "boxes"))
        XCTAssertEqual("1 box", plural(1, "box", "boxes"))
    }

    // MARK: - the first run

    /// All four conditions, and **two of them need the read to have answered
    /// with data** — `?.isEmpty() == true` is false for nil, which is the
    /// whole reason these are optionals.
    func testTheFirstRunHintNeedsAnEmptyHouseAndNotAnUnreadOne() {
        XCTAssertTrue(homeFirstRun(rooms: [], timers: HomeTimerList(), activeTimers: [], events: []))

        XCTAssertFalse(
            homeFirstRun(rooms: nil, timers: HomeTimerList(), activeTimers: [], events: []),
            "a rooms read that has not answered is not an empty house"
        )
        XCTAssertFalse(
            homeFirstRun(rooms: [], timers: HomeTimerList(), activeTimers: [], events: nil),
            "nor an unread calendar"
        )
        XCTAssertFalse(
            homeFirstRun(rooms: [], timers: nil, activeTimers: [], events: []),
            "the timers read must have answered at all"
        )
        XCTAssertFalse(
            homeFirstRun(
                rooms: [], timers: HomeTimerList(),
                activeTimers: [timer(1, expires: noon + 1_000)], events: []
            ),
            "a house with a running timer is in use"
        )
        XCTAssertFalse(homeFirstRun(rooms: [room("kitchen")], timers: HomeTimerList(), activeTimers: [], events: []))
    }

    /// "Asked, and got nothing" is data: `hint != nil` with a nil phrase
    /// still draws the card, on the fallback.
    func testAskedAndGotNothingStillDrawsTheCard() {
        XCTAssertEqual("set a timer for 10 minutes", HomeHint(phrase: nil).text)
        XCTAssertEqual("set a timer for 10 minutes", HomeHint(phrase: "   ").text)
        XCTAssertEqual("play some jazz", HomeHint(phrase: "play some jazz").text)
        XCTAssertEqual("set a timer for 10 minutes", hintPhrase(nil))
        XCTAssertEqual("“set a timer for 10 minutes”", HomeFirstRunStrings.quoted(hintPhrase(nil)))
    }

    // MARK: - announce's guards

    func testAnnounceGreysOutRatherThanSpeakingIntoAHouseThatCannotHear() {
        XCTAssertEqual(0, announceOnlineCount(onlineRooms: 3, coreDown: true))
        XCTAssertEqual(3, announceOnlineCount(onlineRooms: 3, coreDown: false))
        XCTAssertTrue(announceSendEnabled(message: "tea", onlineCount: 1, sending: false))
        XCTAssertFalse(announceSendEnabled(message: "  ", onlineCount: 1, sending: false))
        XCTAssertFalse(announceSendEnabled(message: "tea", onlineCount: 0, sending: false))
        XCTAssertFalse(announceSendEnabled(message: "tea", onlineCount: 1, sending: true))
        XCTAssertEqual("no rooms online", HomeAnnounceStrings.noRoomsOnline)
        XCTAssertEqual("3 online", HomeAnnounceStrings.onlinePill(3))
    }

    /// The empty message is checked **before** the online count, so a person
    /// who taps send with an empty field is told to type something rather
    /// than told the house is empty.
    func testTheAnnounceNoOpBranchesEachSayWhyRatherThanDoingNothing() {
        XCTAssertEqual(
            .toast("type a message first"),
            announceAttempt(message: "   ", onlineCount: 0, sending: false)
        )
        XCTAssertEqual(
            .toast("no rooms connected — nothing to broadcast to"),
            announceAttempt(message: "tea", onlineCount: 0, sending: false)
        )
        XCTAssertEqual(.nothing, announceAttempt(message: "tea", onlineCount: 2, sending: true))
        XCTAssertEqual(.send("tea"), announceAttempt(message: "  tea  ", onlineCount: 2, sending: false))
    }
}
