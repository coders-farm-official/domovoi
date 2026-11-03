import XCTest
@testable import DomovoiKit

/// The three engines, driven through the fakes in `AlertsTestDoubles.swift`:
/// what posts, what is scheduled, what is taken back, and what happens when
/// the house changes **in the middle of a read**.
///
/// The Kotlin counterparts are `AlertEngineTest`, `HelpDeliveriesTest` and
/// `IncidentEngineTest`, which drive the same decisions through a
/// `MockWebServer`.
final class AlertsEngineTests: XCTestCase {

    private static let nowISO = "2026-11-02T21:00:00Z"
    private static var nowMs: Int64 { isoMs(nowISO)! }
    private static let keyA = "aaaa1111"
    private static let keyB = "bbbb2222"

    private static func ago(_ seconds: Int64) -> String {
        let date = Date(timeIntervalSince1970: Double(nowMs - seconds * 1000) / 1000.0)
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        formatter.timeZone = TimeZone(identifier: "UTC")
        return formatter.string(from: date)
    }

    private static func fire(
        id: Int64,
        timerID: Int64,
        agoSec: Int64 = 30,
        kind: String = "timer",
        label: String? = "pasta",
        message: String? = nil,
        roomID: String? = "kitchen",
        summary: String? = "heard in kitchen"
    ) -> HomeFire {
        HomeFire(
            id: id, timerID: timerID, kind: kind, isReminder: kind == "reminder",
            label: label, message: message, roomID: roomID,
            createdAt: ago(agoSec + 600), dueAt: ago(agoSec), firedAt: ago(agoSec),
            heardIn: ["kitchen"], summary: summary
        )
    }

    /// Everything an engine is built from, so a case says only what it is
    /// about.
    private struct Rig {
        let activeKey: Shared<String?>
        let centre: FakeAlertsCentre
        let books: MemoryAlertsBooks
        let mirror: MemoryAlertsMirror
        let reader: FakeAlertsReader
        let engine: AlertsEngine
    }

    private func makeRig(
        reader: FakeAlertsReader,
        state: AlertsPermissionState = .authorized,
        shared: Bool = false,
        mirror: AlertsMirrorBook = AlertsMirrorBook(),
        key: String? = AlertsEngineTests.keyA
    ) -> Rig {
        let activeKey = Shared<String?>(key)
        let centre = FakeAlertsCentre(state: state)
        let books = MemoryAlertsBooks()
        let mirrorStore = MemoryAlertsMirror(mirror)
        let engine = AlertsEngine(
            reader: reader,
            books: books,
            mirrorStore: mirrorStore,
            centre: centre,
            serverKey: { activeKey.current },
            shared: { shared },
            clock: { Self.nowMs }
        )
        return Rig(
            activeKey: activeKey, centre: centre, books: books,
            mirror: mirrorStore, reader: reader, engine: engine
        )
    }

    // MARK: - the read-after-await case

    /// **The top defect class on this track** (`memory/read-after-await-wrong-key.md`):
    /// the server is switched *inside* the `catchUp()` round trip, and the fires
    /// server A answered must not be posted, nor its mark written, under server
    /// B's book.
    ///
    /// Watched failing by deleting the `guard serverKey() == key` after the
    /// read: the fire posts under `bbbb2222`, and `bbbb2222`'s mark moves to 9
    /// — a house that has never been read from remembers another house's fire.
    func testACatchUpThatLandsAfterAServerSwitchChangesNothing() async {
        // The key is read through this, so the handler can move the phone to
        // another house **while the engine is suspended on the read** — the
        // only moment that can produce the bug.
        let keyNow = Shared<String?>(Self.keyA)
        let switched = Shared(false)
        let books = MemoryAlertsBooks()
        let centre = FakeAlertsCentre()
        let engine = AlertsEngine(
            reader: FakeAlertsReader(fires: { _ in
                keyNow.set(Self.keyB)
                switched.set(true)
                return AlertsFireList(serverNow: Self.nowISO, fires: [Self.fire(id: 9, timerID: 4)])
            }),
            books: books,
            mirrorStore: MemoryAlertsMirror(),
            centre: centre,
            serverKey: { keyNow.current },
            clock: { Self.nowMs }
        )

        let answered = await engine.catchUp()
        XCTAssertTrue(switched.current, "the read really ran, and really switched the house")
        XCTAssertFalse(answered, "the answer was for a house this phone has left")
        XCTAssertTrue(centre.added.isEmpty, "nothing posts for either house")
        XCTAssertNil(books.firesSeen(Self.keyA), "nothing was processed")
        XCTAssertNil(books.firesSeen(Self.keyB), "and above all, nothing landed under the new house")
        XCTAssertTrue(books.alerted().isEmpty)
    }

    /// The same guard, after the second read a catch-up can make (the
    /// "is the server behind us?" check).
    func testTheHistoryCheckAlsoStopsWhenTheServerChanged() async {
        let keyNow = Shared<String?>(Self.keyA)
        let calls = Shared(0)
        let books = MemoryAlertsBooks()
        books.setFiresSeen(Self.keyA, id: 40, at: Self.ago(3_600))
        let centre = FakeAlertsCentre()
        let engine = AlertsEngine(
            reader: FakeAlertsReader(fires: { path in
                calls.mutate { $0 += 1 }
                if path == AlertsPaths.newestFire {
                    keyNow.set(Self.keyB)
                    return AlertsFireList(serverNow: Self.nowISO, fires: [Self.fire(id: 1, timerID: 1)])
                }
                return AlertsFireList(serverNow: Self.nowISO, fires: [])
            }),
            books: books,
            mirrorStore: MemoryAlertsMirror(),
            centre: centre,
            serverKey: { keyNow.current },
            clock: { Self.nowMs }
        )
        let gaveUp = await engine.catchUp()
        XCTAssertFalse(gaveUp)
        XCTAssertEqual(2, calls.current, "the page, then the newest-fire check")
        XCTAssertEqual(40, books.firesSeen(Self.keyA), "the old house's mark is untouched")
        XCTAssertNil(books.firesSeen(Self.keyB))
        XCTAssertTrue(centre.added.isEmpty)
    }

    // MARK: - what posts, and how often

    /// One timer, one notification — whichever path got there first.
    func testAFirePostsOnceHoweverOftenItIsPushed() async {
        let rig = makeRig(reader: FakeAlertsReader())
        let fire = Self.fire(id: 9, timerID: 4)
        await rig.engine.onFires([fire])
        XCTAssertEqual(1, rig.centre.added.count)
        let request = rig.centre.added[0]
        XCTAssertEqual("timer_fire:\(Self.keyA):4", request.identifier)
        XCTAssertEqual("Timer done · kitchen", request.title)
        XCTAssertEqual("pasta", request.body)
        XCTAssertEqual(9, rig.books.firesSeen(Self.keyA))
        XCTAssertTrue(alertsWasAlerted(rig.books.alerted(), serverKey: Self.keyA, timerID: 4))

        await rig.engine.onFires([fire])
        XCTAssertEqual(1, rig.centre.added.count, "the same fire again says nothing new")
    }

    /// `refreshIfShowing`: the server's later word ("heard in garage") updates
    /// a notification **still on screen**, and never brings back one the person
    /// swiped away.
    func testALaterSummaryUpdatesTheNotificationOnlyWhileItIsShowing() async {
        let rig = makeRig(reader: FakeAlertsReader())
        await rig.engine.onFires([Self.fire(id: 9, timerID: 4, summary: "heard in kitchen")])
        XCTAssertEqual(1, rig.centre.added.count)

        await rig.engine.onFires([Self.fire(id: 9, timerID: 4, summary: "heard in garage")])
        XCTAssertEqual(2, rig.centre.added.count, "the line under it changed")
        XCTAssertEqual("heard in garage", rig.centre.added.last?.subtitle)

        rig.centre.dismiss("timer_fire:\(Self.keyA):4")
        await rig.engine.onFires([Self.fire(id: 9, timerID: 4, summary: "heard in garage, kitchen")])
        XCTAssertEqual(2, rig.centre.added.count, "a dismissed notification is not posted again")
    }

    /// A phone whose server has no fire ledger (503, `satellites.py:1265`) or
    /// is too old for the route (404): nothing to catch up from, and **nothing
    /// is blanked**.
    func testA503LeavesEveryBookExactlyAsItWas() async {
        let rig = makeRig(reader: FakeAlertsReader(fires: { _ in throw FakeAlertsFailure(status: 503) }))
        rig.books.setFiresSeen(Self.keyA, id: 40, at: Self.ago(600))
        rig.books.setAlerted(["\(Self.keyA)|4|\(Self.nowMs)"])

        let answered = await rig.engine.catchUp()
        XCTAssertFalse(answered, "the server did not answer")
        XCTAssertEqual(40, rig.books.firesSeen(Self.keyA))
        XCTAssertEqual(["\(Self.keyA)|4|\(Self.nowMs)"], rig.books.alerted())
        XCTAssertTrue(rig.centre.added.isEmpty)
    }

    /// An **open** read's empty answer (`window_sec: 600`) must not be read as
    /// "this ledger is empty" — otherwise an unpaired phone starts over on
    /// every quiet evening and re-posts the next fire it sees.
    func testAnEmptyWindowedAnswerDoesNotMakeThePhoneStartOver() async {
        let rig = makeRig(reader: FakeAlertsReader(fires: { _ in
            AlertsFireList(serverNow: Self.nowISO, fires: [], windowSec: 600)
        }))
        rig.books.setFiresSeen(Self.keyA, id: 40, at: Self.ago(600))
        rig.books.setAlerted(["\(Self.keyA)|4|\(Self.nowMs)"])

        let caughtUp = await rig.engine.catchUp()
        XCTAssertTrue(caughtUp)
        XCTAssertEqual(40, rig.books.firesSeen(Self.keyA), "the mark stands")
        XCTAssertFalse(rig.books.alerted().isEmpty)
    }

    /// The other side of the same rule: a **household** read whose newest fire
    /// is behind the mark really is another history, and the phone starts over
    /// and reads the newest page as a first run.
    func testALedgerBehindThisPhoneIsReadAgainFromTheTop() async {
        let reader = FakeAlertsReader(fires: { path in
            if path == AlertsPaths.newestFire {
                return AlertsFireList(serverNow: Self.nowISO, fires: [Self.fire(id: 3, timerID: 1, agoSec: 3_600)])
            }
            if path.contains("since_id=40") {
                return AlertsFireList(serverNow: Self.nowISO, fires: [])
            }
            // The first run's page, newest first.
            return AlertsFireList(serverNow: Self.nowISO, fires: [Self.fire(id: 3, timerID: 7, agoSec: 20)])
        })
        let rig = makeRig(reader: reader)
        rig.books.setFiresSeen(Self.keyA, id: 40, at: Self.ago(3_600))
        rig.books.setAlerted(["\(Self.keyA)|4|\(Self.nowMs)"])

        let caughtUp = await rig.engine.catchUp()
        XCTAssertTrue(caughtUp)
        XCTAssertEqual(3, rig.books.firesSeen(Self.keyA), "the mark is the new history's")
        XCTAssertEqual(1, rig.centre.added.count, "the fire that just went off posts")
        XCTAssertFalse(
            alertsWasAlerted(rig.books.alerted(), serverKey: Self.keyA, timerID: 4),
            "the old history's posted timers are forgotten: their ids restart too"
        )
    }

    /// A rebuilt ledger noticed from a push instead: a fire below the mark that
    /// went off after the remembered one.
    func testAPushFromARebuiltLedgerStartsOverAndPosts() async {
        let rig = makeRig(reader: FakeAlertsReader())
        rig.books.setFiresSeen(Self.keyA, id: 40, at: Self.ago(3_600))
        await rig.engine.onFires([Self.fire(id: 2, timerID: 7, agoSec: 20)])
        XCTAssertEqual(1, rig.centre.added.count)
        XCTAssertEqual(2, rig.books.firesSeen(Self.keyA))
    }

    /// With notifications off nothing posts — and the mark still moves, so the
    /// phone does not post an old evening's fires the moment they are granted.
    func testWithNotificationsOffNothingPostsAndTheMarkStillMoves() async {
        let rig = makeRig(reader: FakeAlertsReader(), state: .denied)
        await rig.engine.onFires([Self.fire(id: 9, timerID: 4)])
        XCTAssertTrue(rig.centre.added.isEmpty)
        XCTAssertEqual(9, rig.books.firesSeen(Self.keyA))
    }

    // MARK: - the mirror

    /// The mirror schedules the soonest timers, withdraws a cancelled one, and
    /// replaces a moved one **under the same identifier**.
    func testTheMirrorSchedulesWithdrawsAndReplaces() async {
        let timers = Shared<[AlertsTimer]>([
            AlertsTimer(id: 1, expiresAt: Self.ago(-600), createdAt: Self.nowISO, label: "pasta", roomID: "kitchen"),
            AlertsTimer(id: 2, expiresAt: Self.ago(-1_200), createdAt: Self.nowISO, label: "bread", roomID: "kitchen"),
        ])
        let rig = makeRig(reader: FakeAlertsReader(timers: {
            AlertsTimerList(serverNow: Self.nowISO, timers: timers.current)
        }))

        var synced = await rig.engine.syncMirror()
        XCTAssertTrue(synced)
        XCTAssertEqual(
            ["timer_fire:\(Self.keyA):1", "timer_fire:\(Self.keyA):2"],
            rig.centre.added.map(\.identifier),
            "soonest first, because iOS keeps the 64 soonest pending requests"
        )
        XCTAssertEqual(600, rig.centre.added[0].afterSeconds)
        XCTAssertEqual([1, 2], rig.mirror.mirror().alarms.map(\.timerID))
        XCTAssertEqual(Self.keyA, rig.mirror.mirror().serverKey)

        // Timer 2 was cancelled from another client; timer 1 was moved.
        timers.set([
            AlertsTimer(id: 1, expiresAt: Self.ago(-900), createdAt: Self.nowISO, label: "pasta", roomID: "kitchen"),
        ])
        synced = await rig.engine.syncMirror()
        XCTAssertTrue(synced)
        XCTAssertEqual(["timer_fire:\(Self.keyA):2"], rig.centre.log.current.removedPending)
        XCTAssertEqual(
            "timer_fire:\(Self.keyA):1", rig.centre.added.last?.identifier,
            "a moved timer goes back into the same slot, not into a second notification"
        )
        XCTAssertEqual(900, rig.centre.added.last?.afterSeconds)
        XCTAssertEqual([1], rig.mirror.mirror().alarms.map(\.timerID))
    }

    /// With notifications off, nothing stays pending: a request the person
    /// never authorised would arrive the moment they did, for a timer long
    /// finished.
    func testNotificationsOffDisarmsTheWholeMirror() async {
        let stored = AlertsMirrorBook(
            serverKey: Self.keyA,
            alarms: [AlertsMirrorAlarm(timerID: 1, kind: "timer", triggerAtMs: Self.nowMs + 600_000)]
        )
        let rig = makeRig(reader: FakeAlertsReader(), state: .denied, mirror: stored)
        let synced = await rig.engine.syncMirror()
        XCTAssertTrue(synced)
        XCTAssertEqual(["timer_fire:\(Self.keyA):1"], rig.centre.log.current.removedPending)
        XCTAssertEqual(AlertsMirrorBook(serverKey: Self.keyA), rig.mirror.mirror())
        XCTAssertTrue(rig.reader.paths.current.isEmpty, "nothing would post, so nothing is read")
    }

    /// A server switch takes back the **pending** notifications and leaves the
    /// delivered ones alone: a notification the person has already seen is
    /// their own history.
    func testSwitchingServerTakesBackOnlyWhatHasNotArrived() async {
        let stored = AlertsMirrorBook(
            serverKey: Self.keyA,
            alarms: [AlertsMirrorAlarm(timerID: 1, kind: "timer", triggerAtMs: Self.nowMs + 600_000)]
        )
        let rig = makeRig(reader: FakeAlertsReader(), mirror: stored)
        await rig.engine.onFires([Self.fire(id: 9, timerID: 4)])
        XCTAssertEqual(["timer_fire:\(Self.keyA):4"], rig.centre.log.current.delivered)

        await rig.engine.clearMirror()
        XCTAssertEqual(["timer_fire:\(Self.keyA):1"], rig.centre.log.current.removedPending)
        XCTAssertTrue(rig.centre.log.current.removedDelivered.isEmpty)
        XCTAssertEqual(AlertsMirrorBook(), rig.mirror.mirror())
    }

    /// The server's clock, learnt from a read, is what a trigger is measured
    /// against.
    func testTheServerOffsetIsLearntFromARead() async {
        let rig = makeRig(reader: FakeAlertsReader(fires: { _ in
            // The server's clock reads three minutes ahead of this phone's.
            AlertsFireList(serverNow: Self.ago(-180), fires: [])
        }))
        _ = await rig.engine.catchUp()
        let offset = await rig.engine.serverOffsetMs
        XCTAssertEqual(180_000, offset)
    }

    // MARK: - help steps

    /// Without "This phone belongs to" there is nothing to ask the route for,
    /// so the engine does not ask — which is why help posts nothing on iOS
    /// today (DECISIONS §4).
    func testHelpAsksForNothingWithoutAnOwner() async {
        let reader = FakeAlertsReader()
        let books = MemoryAlertsBooks()
        let centre = FakeAlertsCentre()
        let engine = AlertsHelpEngine(
            reader: reader, books: books, centre: centre,
            serverKey: { Self.keyA }, owner: { nil }
        )
        await engine.refresh()
        XCTAssertTrue(reader.paths.current.isEmpty, "no person id, no read")
        XCTAssertTrue(centre.added.isEmpty)
    }

    /// With an owner: the first read only marks, and the next one posts this
    /// person's rows and nobody else's.
    func testHelpMarksOnTheFirstRunAndPostsAfterwards() async {
        let rows = Shared<[AlertsHelpDelivery]>([
            AlertsHelpDelivery(id: 7, personID: 3, threadID: 11, title: "Help: add a room"),
            AlertsHelpDelivery(id: 8, personID: 4, threadID: 12, title: "Help: pair a phone"),
        ])
        let reader = FakeAlertsReader(help: { _ in AlertsHelpDeliveryList(deliveries: rows.current) })
        let books = MemoryAlertsBooks()
        let centre = FakeAlertsCentre()
        let engine = AlertsHelpEngine(
            reader: reader, books: books, centre: centre,
            serverKey: { Self.keyA }, owner: { "3" }
        )
        await engine.refresh()
        XCTAssertTrue(centre.added.isEmpty, "an hour of old steps must not flood a phone just set up")
        XCTAssertEqual(8, books.helpSeen(Self.keyA))
        XCTAssertEqual(["/api/help/deliveries?person_id=3"], reader.paths.current)

        rows.set([
            AlertsHelpDelivery(id: 9, personID: 3, threadID: 13, title: "Help: add a camera"),
            AlertsHelpDelivery(id: 10, personID: 4, threadID: 14, title: "Help: not yours"),
        ])
        await engine.refresh()
        XCTAssertEqual(["help_delivery:\(Self.keyA):9"], centre.added.map(\.identifier))
        XCTAssertEqual("Help: add a camera", centre.added.last?.title)
        XCTAssertEqual(10, books.helpSeen(Self.keyA), "the other person's row moved the mark")
        XCTAssertEqual(
            "/api/help/deliveries?person_id=3&since_id=8", reader.paths.current.last,
            "the second read asks for what is above the mark"
        )
    }

    /// A shared screen never gets another person's steps, whoever it belongs
    /// to.
    func testHelpNeverPostsOnASharedScreen() async {
        let reader = FakeAlertsReader()
        let books = MemoryAlertsBooks()
        books.setHelpSeen(Self.keyA, id: 6)
        let centre = FakeAlertsCentre()
        let engine = AlertsHelpEngine(
            reader: reader, books: books, centre: centre,
            serverKey: { Self.keyA }, shared: { true }, owner: { "3" }
        )
        await engine.onDeliveries([AlertsHelpDelivery(id: 7, personID: 3, threadID: 11, title: "Help: add a room")])
        XCTAssertTrue(centre.added.isEmpty)
        XCTAssertEqual(7, books.helpSeen(Self.keyA), "the mark still moves")
    }

    // MARK: - container incidents

    /// One notification per incident, taken back when it closes.
    func testAnIncidentPostsOnceAndIsTakenBackWhenItCloses() async {
        let open = AlertsIncident(
            id: 3, incidentClass: alertsClassTamper, kind: "image", label: "searxng",
            summary: "domovoi-searxng runs a different image", openedAt: Self.ago(120)
        )
        let books = MemoryAlertsBooks()
        let centre = FakeAlertsCentre()
        let watch = AlertsIncidentWatch(
            reader: FakeAlertsReader(), books: books, centre: centre, serverKey: { Self.keyA }
        )
        await watch.onIncidents(AlertsIncidentsPayload(incidents: [open]))
        XCTAssertEqual(["container_incident:\(Self.keyA):3"], centre.added.map(\.identifier))
        XCTAssertEqual("Signs of tampering", centre.added[0].title)
        XCTAssertEqual([alertsIncidentMark(open)], books.incidentsNotified(Self.keyA))

        await watch.onIncidents(AlertsIncidentsPayload(incidents: [open]))
        XCTAssertEqual(1, centre.added.count, "however often the payload arrives")

        let closed = AlertsIncident(
            id: 3, incidentClass: alertsClassTamper, kind: "image", label: "searxng",
            summary: "domovoi-searxng runs a different image",
            openedAt: Self.ago(120), resolvedAt: Self.ago(5), open: false
        )
        await watch.onIncidents(AlertsIncidentsPayload(incidents: [closed]))
        XCTAssertEqual(
            ["container_incident:\(Self.keyA):3"], centre.log.current.removedDelivered,
            "an incident that closed takes its notification with it"
        )
        XCTAssertEqual(
            [alertsIncidentMark(open)], books.incidentsNotified(Self.keyA),
            "the mark stays, so a late read cannot post it a second time"
        )

        await watch.onIncidents(AlertsIncidentsPayload(incidents: [closed]))
        XCTAssertEqual(1, centre.log.current.removedDelivered.count, "withdrawn once")
    }

    /// A phone refused the household list posts nothing at all: the open
    /// summary names no incident, so there is nothing it could post.
    func testAnUnpairedPhonePostsNoIncidents() async {
        let books = MemoryAlertsBooks()
        let centre = FakeAlertsCentre()
        let watch = AlertsIncidentWatch(
            reader: FakeAlertsReader(incidents: { throw FakeAlertsFailure(status: 401) }),
            books: books, centre: centre, serverKey: { Self.keyA }
        )
        await watch.refresh()
        XCTAssertTrue(centre.added.isEmpty)
        XCTAssertTrue(books.incidentsNotified(Self.keyA).isEmpty)
    }

    /// The same read-after-`await` guard, on the incidents watch.
    func testAnIncidentReadThatLandsAfterASwitchChangesNothing() async {
        let keyNow = Shared<String?>(Self.keyA)
        let books = MemoryAlertsBooks()
        let centre = FakeAlertsCentre()
        let watch = AlertsIncidentWatch(
            reader: FakeAlertsReader(incidents: {
                keyNow.set(Self.keyB)
                return AlertsIncidentsPayload(incidents: [AlertsIncident(id: 3, openedAt: Self.ago(60))])
            }),
            books: books, centre: centre, serverKey: { keyNow.current }
        )
        await watch.refresh()
        XCTAssertTrue(centre.added.isEmpty)
        XCTAssertTrue(books.incidentsNotified(Self.keyA).isEmpty)
        XCTAssertTrue(books.incidentsNotified(Self.keyB).isEmpty)
    }

    /// An acknowledged failure is neither posted nor remembered — so it posts
    /// if the server drops the acknowledgement, once.
    func testAnAcknowledgedFailureIsNotPostedAndNotRemembered() async {
        let books = MemoryAlertsBooks()
        let centre = FakeAlertsCentre()
        let watch = AlertsIncidentWatch(
            reader: FakeAlertsReader(), books: books, centre: centre, serverKey: { Self.keyA }
        )
        let acknowledged = AlertsIncident(id: 4, kind: "stopped", openedAt: Self.ago(120), acknowledged: true)
        await watch.onIncidents(AlertsIncidentsPayload(incidents: [acknowledged]))
        XCTAssertTrue(centre.added.isEmpty)
        XCTAssertTrue(books.incidentsNotified(Self.keyA).isEmpty)

        let again = AlertsIncident(id: 4, kind: "stopped", openedAt: Self.ago(120), acknowledged: false)
        await watch.onIncidents(AlertsIncidentsPayload(incidents: [again]))
        XCTAssertEqual(["container_incident:\(Self.keyA):4"], centre.added.map(\.identifier))
    }
}
