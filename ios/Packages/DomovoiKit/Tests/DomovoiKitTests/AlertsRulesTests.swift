import XCTest
@testable import DomovoiKit

/// The alerts' rules and models: the server's own JSON decoded, every window
/// and cap at its boundary, and the words of all three notifications.
///
/// The Kotlin counterparts are `AlertStoreTest`, `FireModelsTest`,
/// `TimerAlarmMirrorTest`, `HelpDeliveriesTest` and `ContainerIncidentsTest`;
/// where a case here is one of theirs, the rule it pins is named in the
/// comment rather than the test name, because the test name says what must be
/// true.
final class AlertsRulesTests: XCTestCase {

    /// A fixed moment, so every age in this file is arithmetic and not a race.
    /// `2026-11-02T21:00:00Z`.
    private static let nowISO = "2026-11-02T21:00:00Z"
    private static var nowMs: Int64 { isoMs(nowISO)! }

    private static func ago(_ seconds: Int64) -> String {
        let date = Date(timeIntervalSince1970: Double(nowMs - seconds * 1000) / 1000.0)
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        formatter.timeZone = TimeZone(identifier: "UTC")
        return formatter.string(from: date)
    }

    // MARK: - decoding what the server actually sends

    /// One fire, whole, with every key `schemas.py:735` writes — including the
    /// three the iOS model deliberately does not keep (`settled_at`,
    /// `acked_at`, `acked_by`), which must be ignored rather than fail the row.
    func testAFireDecodesFromTheServersOwnBody() throws {
        let json = alertsFireListJSON(
            serverNow: Self.nowISO,
            fires: [
                alertsFireJSON(
                    id: 9, timerID: 4,
                    createdAt: Self.ago(600), dueAt: Self.ago(30), firedAt: Self.ago(30),
                    summary: "heard in garage"
                ),
            ]
        )
        let list = try alertsDecode(json, as: AlertsFireList.self)
        XCTAssertEqual(Self.nowISO, list.serverNow)
        XCTAssertNil(list.windowSec, "a household credential reads the whole ledger")
        let fire = try XCTUnwrap(list.fires.first)
        XCTAssertEqual(9, fire.id)
        XCTAssertEqual(4, fire.timerID)
        XCTAssertEqual("pasta", fire.label)
        XCTAssertEqual("heard in garage", fire.summary)
        XCTAssertEqual(["kitchen"], fire.heardIn)
        XCTAssertFalse(fire.masked)
        XCTAssertEqual(1, fire.deliveries.count)
    }

    /// The open tier's answer (rule F1, `satellites.py:1130`): the last 600
    /// seconds, each reminder masked and cut down. `window_sec` is the field
    /// the history rules spend, so it has to survive the decode as a number.
    func testTheOpenTiersAnswerKeepsItsWindowAndItsMask() throws {
        let json = alertsFireListJSON(
            serverNow: Self.nowISO,
            fires: [
                alertsFireJSON(
                    id: 12, timerID: 7, kind: "reminder", label: nil, message: nil, masked: true,
                    roomID: "office",
                    createdAt: Self.ago(300), dueAt: Self.ago(10), firedAt: Self.ago(10),
                    summary: "heard in office"
                ),
            ],
            windowSec: 600
        )
        let list = try alertsDecode(json, as: AlertsFireList.self)
        XCTAssertEqual(600, list.windowSec)
        let fire = try XCTUnwrap(list.fires.first)
        XCTAssertTrue(fire.masked)
        XCTAssertNil(fire.message)
        XCTAssertTrue(alertsIsReminder(fire), "`kind` alone makes it a reminder")
    }

    /// `GET /api/timers`: the row's `masked`, which is the one field this
    /// item's timer model has and Home's has not.
    func testATimerRowKeepsMasked() throws {
        let list = try alertsDecode(
            """
            {"server_now": "\(Self.nowISO)",
             "timers": [{"id": 4, "label": null, "message": "take the tablets at six",
                         "room_id": "office", "is_reminder": true, "masked": true,
                         "created_at": "\(Self.ago(60))", "expires_at": "\(Self.ago(-600))",
                         "seconds_left": 600}],
             "fires": null}
            """,
            as: AlertsTimerList.self
        )
        let timer = try XCTUnwrap(list.timers.first)
        XCTAssertTrue(timer.masked)
        XCTAssertTrue(timer.isReminder)
        XCTAssertNil(list.fires, "`fires: null` is a fact about the server, not an empty list")
    }

    /// The help read is **wrapped** and the push is a **bare list**
    /// (`help.py:320`, `realtime.py:984`), and a row with no `thread_id` is
    /// skipped rather than failing the batch.
    func testHelpDecodesWrappedAndBareAndSkipsAnUnusableRow() throws {
        let wrapped = try alertsDecode(
            """
            {"deliveries": [{"id": 7, "person_id": 3, "thread_id": 11,
                             "title": "Help: add a room", "created_at": "\(Self.ago(30))"},
                            {"id": 8, "person_id": 3, "title": "no thread"}]}
            """,
            as: AlertsHelpDeliveryList.self
        )
        XCTAssertEqual([7], wrapped.deliveries.map(\.id))
        XCTAssertEqual("Help: add a room", wrapped.deliveries[0].title)

        let push = try alertsDecode(
            """
            [{"id": 9, "person_id": 3, "thread_id": 12, "title": ""}, 17]
            """,
            as: AlertsHelpDeliveryPush.self
        )
        XCTAssertEqual([9], push.deliveries.map(\.id), "a member that is not a row is stepped over")
    }

    /// `incident_view`'s seventeen keys (`views.py:84`), of which this item
    /// keeps ten; `open` and `acknowledged` are derived when absent.
    func testAnIncidentDecodesAndDerivesOpenAndAcknowledged() throws {
        let payload = try alertsDecode(
            """
            {"incidents": [
               {"id": 3, "class": "tamper", "kind": "image", "severity": "error",
                "label": "searxng", "summary": "domovoi-searxng runs a different image",
                "owner": "plugin:searxng", "container": "domovoi-searxng",
                "opened_at": "\(Self.ago(120))", "last_seen_at": "\(Self.ago(5))",
                "resolved_at": null, "resolution": null, "acknowledged": false,
                "acknowledged_at": null, "cleared_at": null, "open": true,
                "freezes": true, "can_acknowledge": false, "can_clear": false},
               {"id": 4, "class": "failure", "kind": "stopped", "label": "whisper",
                "resolved_at": "\(Self.ago(60))", "acknowledged_at": "\(Self.ago(90))"},
               {"class": "failure", "kind": "stopped"}],
             "banner": {"show": true, "tone": "tamper", "text": "…"},
             "frozen": true, "attention": true}
            """,
            as: AlertsIncidentsPayload.self
        )
        XCTAssertEqual([3, 4], payload.incidents.map(\.id), "a row with no id is skipped")
        let tamper = payload.incidents[0]
        XCTAssertTrue(tamper.tamper)
        XCTAssertTrue(tamper.bannerWorthy)
        let closed = payload.incidents[1]
        XCTAssertFalse(closed.open, "`resolved_at` with no `open` means closed")
        XCTAssertTrue(closed.acknowledged, "`acknowledged_at` with no `acknowledged` means yes")
        XCTAssertFalse(closed.bannerWorthy)
    }

    /// A payload with no `incidents` array is **not** one: the Kotlin's test,
    /// and the web's (`ContainerIncidents.kt:197`).
    func testAPayloadWithoutIncidentsIsNotAnIncidentsPayload() {
        XCTAssertNil(try? alertsDecode(#"{"banner": {"show": false}}"#, as: AlertsIncidentsPayload.self))
    }

    // MARK: - the words

    func testTheTitleNamesTheKindAndTheRoom() {
        XCTAssertEqual("Timer done · kitchen", alertsFireTitle(kind: "timer", roomID: "kitchen"))
        XCTAssertEqual("Reminder · office", alertsFireTitle(kind: "reminder", roomID: "office"))
        XCTAssertEqual("Timer done · no room", alertsFireTitle(kind: "timer", roomID: nil))
        XCTAssertEqual("Timer done · no room", alertsFireTitle(kind: "timer", roomID: ""))
    }

    /// `timerNounFromSpan`'s boundary: under 90 seconds it counts in seconds,
    /// at 90 it counts in minutes.
    func testAnUnlabelledTimerIsNamedAfterHowLongItRan() {
        XCTAssertEqual("40s timer", alertsTimerNoun(createdAt: Self.ago(640), dueAt: Self.ago(600)))
        XCTAssertEqual("89s timer", alertsTimerNoun(createdAt: Self.ago(689), dueAt: Self.ago(600)))
        XCTAssertEqual("2 min timer", alertsTimerNoun(createdAt: Self.ago(690), dueAt: Self.ago(600)))
        XCTAssertEqual("3 min timer", alertsTimerNoun(createdAt: Self.ago(780), dueAt: Self.ago(600)))
        XCTAssertEqual("timer", alertsTimerNoun(createdAt: nil, dueAt: Self.ago(600)))
        XCTAssertEqual(
            "timer", alertsTimerNoun(createdAt: Self.ago(600), dueAt: Self.ago(600)),
            "a span of zero is not a length"
        )
    }

    /// The privacy rule, which is the load-bearing half of this item: a
    /// reminder's words are household speech.
    func testAremindersWordsAreWithheldFromASharedScreenAndWhenMasked() {
        let reminder = HomeFire(
            id: 1, timerID: 2, kind: "reminder", isReminder: true,
            message: "take the tablets at six", roomID: "office",
            createdAt: Self.ago(300), dueAt: Self.ago(10), firedAt: Self.ago(10),
            summary: "heard in office"
        )
        XCTAssertEqual("take the tablets at six", alertsFireBody(reminder, shared: false))
        XCTAssertNil(alertsFireBody(reminder, shared: true), "never on a hall screen")

        let masked = HomeFire(
            id: 1, timerID: 2, kind: "reminder", isReminder: true, message: nil, masked: true,
            roomID: "office", createdAt: Self.ago(300), dueAt: Self.ago(10), firedAt: Self.ago(10)
        )
        XCTAssertNil(alertsFireBody(masked, shared: false), "the server withheld them")

        let wordless = HomeFire(id: 1, timerID: 2, kind: "reminder", isReminder: true, message: "")
        XCTAssertEqual("reminder", alertsFireBody(wordless, shared: false))

        // A timer's label is low-risk and always shows — the Kotlin's own
        // judgement (`FireModels.kt:117-119`).
        let timer = HomeFire(id: 2, timerID: 3, kind: "timer", label: "pasta", roomID: "kitchen")
        XCTAssertEqual("pasta", alertsFireBody(timer, shared: false))
    }

    /// A shared screen's whole notification is its title; the summary goes
    /// with the rest.
    func testASharedScreensContentIsTitleOnly() {
        let fire = HomeFire(
            id: 1, timerID: 2, kind: "reminder", isReminder: true,
            message: "take the tablets at six", roomID: "office",
            firedAt: Self.ago(10), summary: "heard in office"
        )
        let content = alertsFireContent(fire, shared: true, serverOffsetMs: 0)
        XCTAssertEqual("Reminder · office", content.title)
        XCTAssertNil(content.body)
        XCTAssertNil(content.subtitle)
    }

    /// The server's clock is the one a timer fires against, so the
    /// notification's own time is corrected by the offset.
    func testTheFiresTimeIsCorrectedByTheServerOffset() {
        let fire = HomeFire(id: 1, timerID: 2, kind: "timer", label: "pasta", firedAt: Self.nowISO)
        let content = alertsFireContent(fire, shared: false, serverOffsetMs: 180_000)
        XCTAssertEqual(Self.nowMs - 180_000, content.whenMs, "a phone three minutes slow")
        XCTAssertEqual(180_000, alertsServerOffsetMs(serverNow: Self.nowISO, receivedAtMs: Self.nowMs - 180_000))
        XCTAssertNil(alertsServerOffsetMs(serverNow: nil, receivedAtMs: 0), "a read that did not say")
    }

    /// A pre-scheduled alert carries **no second line** (DECISIONS §3): there
    /// is nothing to say about a fire nobody confirmed.
    func testAScheduledAlertHasNoSubtitle() {
        let alarm = AlertsMirrorAlarm(
            timerID: 4, kind: "timer", label: "pasta", roomID: "kitchen",
            createdAt: Self.ago(0), expiresAt: Self.ago(-600), triggerAtMs: Self.nowMs + 600_000
        )
        let content = alertsAlarmContent(alarm, shared: false)
        XCTAssertEqual("Timer done · kitchen", content.title)
        XCTAssertEqual("pasta", content.body)
        XCTAssertNil(content.subtitle)
    }

    // MARK: - the posted book

    /// One post per timer per server, and the prune: a stale entry goes, a
    /// malformed one goes, and the book is capped.
    func testTheBookLetsOnePathPostAndPrunesItself() {
        let (first, added) = alertsMarkInBook([], serverKey: "aaaa1111", timerID: 4, nowMs: Self.nowMs)
        XCTAssertTrue(added)
        XCTAssertEqual(["aaaa1111|4|\(Self.nowMs)"], first)

        let (again, addedAgain) = alertsMarkInBook(first, serverKey: "aaaa1111", timerID: 4, nowMs: Self.nowMs)
        XCTAssertFalse(addedAgain, "the other path must stay quiet")
        XCTAssertEqual(first, again)

        // The same timer id on another server is another timer.
        let (other, addedOther) = alertsMarkInBook(first, serverKey: "bbbb2222", timerID: 4, nowMs: Self.nowMs)
        XCTAssertTrue(addedOther)
        XCTAssertEqual(2, other.count)
        XCTAssertTrue(alertsWasAlerted(other, serverKey: "bbbb2222", timerID: 4))
        XCTAssertFalse(alertsWasAlerted(other, serverKey: "cccc3333", timerID: 4))

        let stale = "aaaa1111|9|\(Self.nowMs - alertsAlertedKeepMs)"
        let (pruned, _) = alertsMarkInBook(
            [stale, "rubbish", "aaaa1111|nope"], serverKey: "aaaa1111", timerID: 5, nowMs: Self.nowMs
        )
        XCTAssertEqual(["aaaa1111|5|\(Self.nowMs)"], pruned, "stale and malformed entries go")

        let many = (0..<alertsAlertedMax).map { "aaaa1111|\($0)|\(Self.nowMs)" }
        let (capped, _) = alertsMarkInBook(many, serverKey: "aaaa1111", timerID: 9_999, nowMs: Self.nowMs)
        XCTAssertEqual(alertsAlertedMax, capped.count)
        XCTAssertEqual("aaaa1111|9999|\(Self.nowMs)", capped.last)
        XCTAssertFalse(capped.contains("aaaa1111|0|\(Self.nowMs)"), "the oldest fell off the front")
    }

    func testForgettingAServerLeavesTheOthersAlone() {
        let book = ["aaaa1111|1|10", "bbbb2222|1|10", "aaaa1111|2|10"]
        XCTAssertEqual(["bbbb2222|1|10"], alertsForgetServer(book, serverKey: "aaaa1111"))
    }

    // MARK: - where this phone is in a server's history

    /// A fire at or below the remembered id that went off **after** the
    /// remembered one: the ids started again.
    func testARebuiltLedgerIsNoticedByItsTimes() {
        let young = HomeFire(id: 2, timerID: 2, firedAt: Self.ago(10))
        XCTAssertTrue(alertsHistoryRestarted(fires: [young], seen: 40, seenAtMs: Self.nowMs - 3_600_000))
        XCTAssertFalse(
            alertsHistoryRestarted(fires: [young], seen: 40, seenAtMs: Self.nowMs),
            "an old fire below the mark is just an old fire"
        )
        XCTAssertFalse(
            alertsHistoryRestarted(fires: [young], seen: nil, seenAtMs: nil),
            "a first run has nothing to contradict"
        )
        XCTAssertFalse(
            alertsHistoryRestarted(fires: [HomeFire(id: 2, timerID: 2)], seen: 40, seenAtMs: 0),
            "a fire with no readable time proves nothing"
        )
    }

    /// The rule `window_sec` is spent on: an **open** read's empty answer says
    /// only that nothing went off lately, so it must not look like a rebuilt
    /// database.
    func testAnEmptyWindowedAnswerDoesNotMeanTheHistoryIsBehind() {
        XCTAssertTrue(alertsHistoryBehind(newest: nil, seen: 40, windowed: false))
        XCTAssertFalse(
            alertsHistoryBehind(newest: nil, seen: 40, windowed: true),
            "the last ten minutes being quiet is not a rebuilt ledger"
        )
        XCTAssertFalse(alertsHistoryBehind(newest: nil, seen: 0, windowed: false))
        XCTAssertTrue(alertsHistoryBehind(newest: HomeFire(id: 3, timerID: 1), seen: 40))
        XCTAssertFalse(alertsHistoryBehind(newest: HomeFire(id: 41, timerID: 1), seen: 40))
    }

    // MARK: - which fires post

    /// A phone meeting a house for the first time is not handed its whole
    /// afternoon: the mark jumps past everything older than two minutes.
    func testAFirstRunOnlyPostsWhatJustWentOff() {
        let old = HomeFire(id: 10, timerID: 1, firedAt: Self.ago(3_600))
        let justOld = HomeFire(id: 11, timerID: 2, firedAt: Self.ago(121))
        let fresh = HomeFire(id: 12, timerID: 3, firedAt: Self.ago(30))
        let plan = alertsPlanFires(
            fires: [old, justOld, fresh], seen: nil, serverNowMs: Self.nowMs, maxAgeMs: alertsCatchUpWindowMs
        )
        XCTAssertEqual([12], plan.post.map(\.id))
        XCTAssertEqual(12, plan.seen)
        XCTAssertEqual(fresh.firedAt, plan.seenAt)
    }

    /// Later runs: everything above the mark that is younger than the catch-up
    /// window, oldest first; an older one only moves the mark.
    func testALaterRunPostsEverythingNewEnoughAboveTheMark() {
        let stale = HomeFire(id: 13, timerID: 4, firedAt: Self.ago(3_600))
        let one = HomeFire(id: 14, timerID: 5, firedAt: Self.ago(200))
        let two = HomeFire(id: 15, timerID: 6, firedAt: Self.ago(100))
        let plan = alertsPlanFires(
            fires: [two, stale, one], seen: 12, serverNowMs: Self.nowMs, maxAgeMs: alertsCatchUpWindowMs
        )
        XCTAssertEqual([14, 15], plan.post.map(\.id), "oldest first")
        XCTAssertEqual(15, plan.seen)
    }

    /// A fire the phone cannot date is a fire it cannot judge: it never posts,
    /// and it still moves the mark.
    func testAFireWithNoReadableTimeNeverPosts() {
        let undated = HomeFire(id: 20, timerID: 7, firedAt: "not a date")
        let plan = alertsPlanFires(
            fires: [undated], seen: 19, serverNowMs: Self.nowMs, maxAgeMs: alertsCatchUpWindowMs
        )
        XCTAssertTrue(plan.post.isEmpty)
        XCTAssertEqual(20, plan.seen)
    }

    // MARK: - the mirror

    func testTheTriggerIsTheServersDueTimeInThePhonesTerms() {
        // The server's clock ran three minutes ahead of this phone's when the
        // read landed, so a timer due in ten minutes by the server arrives in
        // ten minutes by the phone — three minutes "early" on its own clock.
        let due = Self.nowMs + 600_000
        XCTAssertEqual(
            due - 180_000,
            alertsTriggerAtMs(expiresAtMs: due, serverNowMs: Self.nowMs, receivedAtMs: Self.nowMs - 180_000)
        )
    }

    /// Both kinds, nothing inside the minimum lead, the soonest first, capped.
    func testTheMirrorWantsTheSoonestTimersOnlyAndInOrder() {
        func timer(_ id: Int64, dueInSec: Int64) -> AlertsTimer {
            AlertsTimer(
                id: id, expiresAt: Self.ago(-dueInSec), createdAt: Self.nowISO,
                label: "t\(id)", roomID: "kitchen"
            )
        }
        let list = AlertsTimerList(
            serverNow: Self.nowISO,
            timers: [timer(1, dueInSec: 600), timer(2, dueInSec: 60), timer(3, dueInSec: 0)]
        )
        let desired = alertsDesiredAlarms(list, receivedAtMs: Self.nowMs)
        XCTAssertEqual([2, 1], desired.map(\.timerID), "soonest first, and the due one is the server's to fire")

        let many = (1...(alertsMirrorMax + 10)).map { timer(Int64($0), dueInSec: Int64($0) * 60) }
        let capped = alertsDesiredAlarms(
            AlertsTimerList(serverNow: Self.nowISO, timers: many.shuffled()), receivedAtMs: Self.nowMs
        )
        XCTAssertEqual(alertsMirrorMax, capped.count)
        XCTAssertEqual(1, capped.first?.timerID, "the ones iOS keeps are the soonest")
        XCTAssertEqual(Int64(alertsMirrorMax), capped.last?.timerID)
    }

    /// A reminder's words travel in the mirror, which is why the mirror is the
    /// one book kept out of the backup (DECISIONS §9).
    func testTheMirrorCarriesTheWordsAndTheMask() throws {
        let list = AlertsTimerList(
            serverNow: Self.nowISO,
            timers: [
                AlertsTimer(
                    id: 4, expiresAt: Self.ago(-600), createdAt: Self.nowISO,
                    message: "take the tablets at six", roomID: "office",
                    isReminder: true, masked: true
                ),
            ]
        )
        let alarm = try XCTUnwrap(alertsDesiredAlarms(list, receivedAtMs: Self.nowMs).first)
        XCTAssertEqual("reminder", alarm.kind)
        XCTAssertEqual("take the tablets at six", alarm.message)
        XCTAssertTrue(alarm.masked)
    }

    /// Disarm what is no longer wanted; re-arm what moved by more than a
    /// second, and nothing that did not.
    func testReconcileArmsTheNewAndTheMovedAndDisarmsTheGone() {
        func alarm(_ id: Int64, at: Int64) -> AlertsMirrorAlarm {
            AlertsMirrorAlarm(timerID: id, kind: "timer", triggerAtMs: at)
        }
        let change = alertsReconcile(
            desired: [alarm(1, at: 10_000), alarm(2, at: 20_000), alarm(3, at: 30_000)],
            current: [alarm(1, at: 10_000), alarm(2, at: 20_000 + alertsMirrorMoveMs + 1), alarm(9, at: 90_000)]
        )
        XCTAssertEqual([2, 3], change.toSchedule.map(\.timerID))
        XCTAssertEqual([9], change.toCancel)

        let unchanged = alertsReconcile(
            desired: [alarm(1, at: 10_000 + alertsMirrorMoveMs)],
            current: [alarm(1, at: 10_000)]
        )
        XCTAssertTrue(unchanged.toSchedule.isEmpty, "exactly the move threshold is not a move")
    }

    // MARK: - help steps

    func testTheHelpTitleGetsItsPrefixOnceOrNotAtAll() {
        XCTAssertEqual(
            "Help: add a room",
            alertsHelpTitle(AlertsHelpDelivery(id: 1, personID: 3, threadID: 11, title: "Help: add a room"))
        )
        XCTAssertEqual(
            "help: add a room",
            alertsHelpTitle(AlertsHelpDelivery(id: 1, personID: 3, threadID: 11, title: "help: add a room")),
            "the test is case-insensitive, and the row's own words are kept"
        )
        XCTAssertEqual(
            "Help: add a room",
            alertsHelpTitle(AlertsHelpDelivery(id: 1, personID: 3, threadID: 11, title: "add a room"))
        )
        XCTAssertEqual(
            "Help from Domovoi",
            alertsHelpTitle(AlertsHelpDelivery(id: 1, personID: 3, threadID: 11, title: "  "))
        )
    }

    /// The subtle half: **every** row moves the mark, whoever it is for, so
    /// claiming the phone later does not post an hour of other people's steps.
    func testHelpPostsOnlyThisPersonsRowsAndTheMarkMovesForAll() {
        let mine = AlertsHelpDelivery(id: 7, personID: 3, threadID: 11, title: "Help: add a room")
        let theirs = AlertsHelpDelivery(id: 8, personID: 4, threadID: 12, title: "Help: pair a phone")

        let first = alertsPlanHelp(rows: [mine, theirs], owner: 3, seen: nil, shared: false)
        XCTAssertTrue(first.post.isEmpty, "a first run only ever sets the mark")
        XCTAssertEqual(8, first.seen)

        let later = alertsPlanHelp(rows: [theirs, mine], owner: 3, seen: 6, shared: false)
        XCTAssertEqual([7], later.post.map(\.id))
        XCTAssertEqual(8, later.seen, "the other person's row moved the mark too")

        XCTAssertTrue(alertsPlanHelp(rows: [mine], owner: nil, seen: 6, shared: false).post.isEmpty)
        XCTAssertTrue(
            alertsPlanHelp(rows: [mine], owner: 3, seen: 6, shared: true).post.isEmpty,
            "never on a hall screen"
        )
        XCTAssertEqual(7, alertsPlanHelp(rows: [mine], owner: 3, seen: 6, shared: true).seen)
    }

    func testTheOwnerIsWhateverIsAPersonId() {
        XCTAssertEqual(3, alertsOwnerID(" 3 "))
        XCTAssertNil(alertsOwnerID(nil))
        XCTAssertNil(alertsOwnerID(""))
        XCTAssertNil(alertsOwnerID("nobody"))
    }

    func testHelpPathNamesThePersonAndTheMark() {
        XCTAssertEqual("/api/help/deliveries?person_id=3", alertsHelpPath(personID: 3, sinceID: nil))
        XCTAssertEqual("/api/help/deliveries?person_id=3&since_id=7", alertsHelpPath(personID: 3, sinceID: 7))
    }

    // MARK: - container incidents

    func testAnIncidentsTitleAndBody() {
        let tamper = AlertsIncident(
            id: 3, incidentClass: alertsClassTamper, kind: "image", severity: "error",
            label: "searxng", summary: "domovoi-searxng runs a different image",
            openedAt: Self.ago(120)
        )
        let content = alertsIncidentContent(tamper, shared: false)
        XCTAssertEqual("Signs of tampering", content.title)
        XCTAssertEqual("domovoi-searxng runs a different image", content.body)
        XCTAssertEqual(isoMs(Self.ago(120)), content.whenMs)

        let outdated = AlertsIncident(id: 4, kind: "outdated", label: "whisper", openedAt: Self.ago(60))
        let outdatedContent = alertsIncidentContent(outdated, shared: false)
        XCTAssertEqual("A container needs recreating", outdatedContent.title)
        XCTAssertEqual("whisper · older image", outdatedContent.body, "no summary: label and kind")

        let plain = AlertsIncident(id: 5, kind: "stopped")
        XCTAssertEqual("A container failed", alertsIncidentContent(plain, shared: false).title)
        XCTAssertEqual("stopped", alertsIncidentContent(plain, shared: false).body, "no label either")

        let onTheHallScreen = alertsIncidentContent(tamper, shared: true)
        XCTAssertEqual("Domovoi needs an admin's attention", onTheHallScreen.title)
        XCTAssertNil(onTheHallScreen.body, "no container, no words, locked or not")
    }

    func testKindWords() {
        XCTAssertEqual("docker down", AlertsStrings.incidentKindWords("daemon"))
        XCTAssertEqual("unknown container", AlertsStrings.incidentKindWords("unknown"))
        XCTAssertEqual("problem", AlertsStrings.incidentKindWords(nil))
        XCTAssertEqual("some new kind", AlertsStrings.incidentKindWords("some_new_kind"))
    }

    /// The mark carries when the incident opened, so a rebuilt database's new
    /// #3 is not hushed by an old one.
    func testTheMarkIsTheIdAndWhenItOpened() {
        let incident = AlertsIncident(id: 3, openedAt: Self.ago(120))
        XCTAssertEqual("3|\(isoMs(Self.ago(120))!)", alertsIncidentMark(incident))
        XCTAssertEqual(3, alertsMarkID(alertsIncidentMark(incident)))
        XCTAssertEqual("3|", alertsIncidentMark(AlertsIncident(id: 3)), "no time is an empty half")
        XCTAssertNil(alertsMarkID("rubbish"))
    }

    /// One notification per incident ever; only what the banner shows; an
    /// acknowledged failure is neither posted **nor remembered**.
    func testOnlyWhatTheBannerShowsIsNotifiedAndOnlyOnce() {
        let tamper = AlertsIncident(id: 3, incidentClass: alertsClassTamper, openedAt: Self.ago(120))
        let acknowledged = AlertsIncident(id: 4, openedAt: Self.ago(120), acknowledged: true)
        let resolved = AlertsIncident(id: 5, openedAt: Self.ago(120), resolvedAt: Self.ago(60), open: false)

        let plan = alertsPlanNotifications(incidents: [resolved, acknowledged, tamper], notified: [])
        XCTAssertEqual([3], plan.post.map(\.id))
        XCTAssertEqual([alertsIncidentMark(tamper)], plan.book)
        XCTAssertFalse(
            plan.book.contains(alertsIncidentMark(acknowledged)),
            "not remembered, so it posts if the server drops the acknowledgement"
        )

        let again = alertsPlanNotifications(incidents: [tamper], notified: plan.book)
        XCTAssertTrue(again.post.isEmpty, "however often it is read")

        let unacknowledged = AlertsIncident(id: 4, openedAt: Self.ago(120), acknowledged: false)
        let afterDrop = alertsPlanNotifications(incidents: [unacknowledged], notified: plan.book)
        XCTAssertEqual([4], afterDrop.post.map(\.id))
    }

    /// A posted incident that closed is taken back once, and its mark stays in
    /// the book so a late read cannot post it again.
    func testAClosedIncidentIsWithdrawnOnceAndStaysInTheBook() {
        let open = AlertsIncident(id: 3, openedAt: Self.ago(120))
        let mark = alertsIncidentMark(open)
        let closed = AlertsIncident(id: 3, openedAt: Self.ago(120), resolvedAt: Self.ago(10), open: false)

        let gone = alertsPlanWithdrawals(incidents: [closed], notified: [mark], withdrawn: [])
        XCTAssertEqual([3], gone.withdraw)
        XCTAssertEqual([mark], gone.withdrawn)

        let twice = alertsPlanWithdrawals(incidents: [closed], notified: [mark], withdrawn: gone.withdrawn)
        XCTAssertTrue(twice.withdraw.isEmpty, "once")

        let stillOpen = alertsPlanWithdrawals(incidents: [open], notified: [mark], withdrawn: [])
        XCTAssertTrue(stillOpen.withdraw.isEmpty, "an open incident keeps its notification")

        // A list that no longer carries it at all (resolved over a day ago).
        let dropped = alertsPlanWithdrawals(incidents: [], notified: [mark], withdrawn: [])
        XCTAssertEqual([3], dropped.withdraw)

        // A mark no longer in the book is not worth remembering as withdrawn.
        let pruned = alertsPlanWithdrawals(incidents: [], notified: [], withdrawn: [mark])
        XCTAssertTrue(pruned.withdrawn.isEmpty)
    }

    // MARK: - the permission, and the row

    /// The order: the status first, the dismissal second.
    func testTheRowIsSaidUntilItIsAnsweredAndNotWhenThereIsNothingToSay() {
        XCTAssertEqual(AlertsHint.alertsOff, alertsHint(canPost: false, dismissed: false))
        XCTAssertEqual(AlertsHint.none, alertsHint(canPost: false, dismissed: true))
        XCTAssertEqual(AlertsHint.none, alertsHint(canPost: true, dismissed: false))
        XCTAssertEqual(AlertsHint.none, alertsHint(canPost: true, dismissed: true))
    }

    func testOnlyAnAnswerableStatePosts() {
        XCTAssertFalse(AlertsPermissionState.notDetermined.canPost)
        XCTAssertFalse(AlertsPermissionState.denied.canPost)
        XCTAssertTrue(AlertsPermissionState.authorized.canPost)
        XCTAssertTrue(AlertsPermissionState.provisional.canPost, "quiet delivery is delivery")
    }

    /// `turn on` while there is still a prompt to show; `open Settings` once
    /// there is not — a second `turn on` would do nothing, which is the one
    /// thing the no-misleading-controls rule forbids.
    func testTheFixControlIsWhicheverCanActuallyWork() {
        let fresh = AlertsPermissionAsk(state: .notDetermined)
        XCTAssertEqual("Timer alerts are off on this phone", fresh.line(dismissed: false))
        XCTAssertEqual("turn on", fresh.fixLabel(dismissed: false))
        XCTAssertEqual(AlertsFix.explainThenAsk, fresh.fix)
        XCTAssertTrue(fresh.showsExplainCard)

        let refused = AlertsPermissionAsk(state: .denied)
        XCTAssertEqual("open Settings", refused.fixLabel(dismissed: false))
        XCTAssertEqual(AlertsFix.openSettings, refused.fix)
        XCTAssertFalse(refused.showsExplainCard, "the card's button says `ask iOS`, and iOS would not ask")

        let granted = AlertsPermissionAsk(state: .authorized)
        XCTAssertNil(granted.line(dismissed: false))
        XCTAssertNil(granted.fixLabel(dismissed: false))
        XCTAssertNil(fresh.line(dismissed: true), "answered `not now`")
        XCTAssertNil(fresh.fixLabel(dismissed: true))
    }

    // MARK: - the requests themselves

    /// One slot per timer per server, and the fire's request replaces a pending
    /// scheduled one because they share it.
    func testATimersTwoRequestsShareOneIdentifier() {
        let fire = HomeFire(
            id: 9, timerID: 4, kind: "timer", label: "pasta", roomID: "kitchen",
            firedAt: Self.nowISO, summary: "heard in garage"
        )
        let posted = AlertsNotifier.fire(
            alertsFireContent(fire, shared: false, serverOffsetMs: 0), serverKey: "aaaa1111"
        )
        XCTAssertEqual("timer_fire:aaaa1111:4", posted.identifier)
        XCTAssertEqual("Timer done · kitchen", posted.title)
        XCTAssertEqual("pasta", posted.body)
        XCTAssertEqual("heard in garage", posted.subtitle)
        XCTAssertNil(posted.afterSeconds, "a fire already happened")
        XCTAssertEqual("home", posted.userInfo[AlertsPayloadKey.route])
        XCTAssertEqual("aaaa1111", posted.userInfo[AlertsPayloadKey.serverKey])

        let alarm = AlertsMirrorAlarm(
            timerID: 4, kind: "timer", label: "pasta", roomID: "kitchen",
            triggerAtMs: Self.nowMs + 600_000
        )
        let scheduled = AlertsNotifier.scheduled(
            alarm, shared: false, serverKey: "aaaa1111", nowMs: Self.nowMs
        )
        XCTAssertEqual(posted.identifier, scheduled.identifier, "one slot, so one replaces the other")
        XCTAssertEqual(600, scheduled.afterSeconds)
        XCTAssertNil(scheduled.subtitle)

        let late = AlertsNotifier.scheduled(
            AlertsMirrorAlarm(timerID: 4, kind: "timer", triggerAtMs: Self.nowMs - 5_000),
            shared: false, serverKey: "aaaa1111", nowMs: Self.nowMs
        )
        XCTAssertNil(
            late.afterSeconds,
            "a trigger already past is delivered at once; a non-positive interval would throw"
        )
    }

    /// A help notification carries the thread, as digits, and its own title —
    /// which is what the tap needs to open Chat on it.
    func testAHelpRequestCarriesTheThreadAndTheServer() {
        let row = AlertsHelpDelivery(id: 7, personID: 3, threadID: 11, title: "add a room")
        let request = AlertsNotifier.help(row, serverKey: "aaaa1111")
        XCTAssertEqual("help_delivery:aaaa1111:7", request.identifier)
        XCTAssertEqual("Help: add a room", request.title)
        XCTAssertEqual("Tap to open the steps in Chat.", request.body)
        XCTAssertEqual("chat", request.userInfo[AlertsPayloadKey.route])
        XCTAssertEqual("11", request.userInfo[AlertsPayloadKey.chatThread])
        XCTAssertEqual("Help: add a room", request.userInfo[AlertsPayloadKey.chatTitle])
        XCTAssertEqual(AlertsRequest.helpThread, request.threadIdentifier)
    }

    func testAnIncidentRequestOpensTheContainersScreen() {
        let incident = AlertsIncident(id: 3, incidentClass: alertsClassTamper, openedAt: Self.ago(60))
        let request = AlertsNotifier.incident(
            alertsIncidentContent(incident, shared: false), serverKey: "aaaa1111"
        )
        XCTAssertEqual("container_incident:aaaa1111:3", request.identifier)
        XCTAssertEqual("containers", request.userInfo[AlertsPayloadKey.route])
        XCTAssertEqual(AlertsRequest.incidentsThread, request.threadIdentifier)
    }

    // MARK: - a tap

    /// A payload must not be able to name an arbitrary screen.
    func testATapOnlyEverAsksForOneOfThreeScreens() {
        XCTAssertEqual(AlertsTapTarget.home, alertsTapTarget([AlertsPayloadKey.route: "home"]))
        XCTAssertEqual(AlertsTapTarget.containers, alertsTapTarget([AlertsPayloadKey.route: "containers"]))
        XCTAssertNil(alertsTapTarget([AlertsPayloadKey.route: "settings"]))
        XCTAssertNil(alertsTapTarget([:]), "a notification that is not ours")

        let help = alertsTapTarget([
            AlertsPayloadKey.route: "chat",
            AlertsPayloadKey.serverKey: "aaaa1111",
            AlertsPayloadKey.chatThread: "11",
            AlertsPayloadKey.chatTitle: "Help: add a room",
        ])
        XCTAssertEqual(
            AlertsTapTarget.chatThread(serverKey: "aaaa1111", thread: 11, title: "Help: add a room"), help
        )
        XCTAssertEqual(
            AlertsTapTarget.chat,
            alertsTapTarget([AlertsPayloadKey.route: "chat", AlertsPayloadKey.serverKey: "aaaa1111"]),
            "Chat is still where the person asked to go"
        )
        XCTAssertEqual(
            AlertsTapTarget.chat,
            alertsTapTarget([
                AlertsPayloadKey.route: "chat",
                AlertsPayloadKey.serverKey: "aaaa1111",
                AlertsPayloadKey.chatThread: "0",
            ]),
            "a thread id of zero is not a thread"
        )
    }

    // MARK: - the books on disk

    /// The `UserDefaults` store, including the two derivations that are easy to
    /// get wrong: a moved mark with no time drops the stored time, and starting
    /// over forgets this server's posted timers and nobody else's.
    func testTheDefaultsStoreKeepsEachBookPerServer() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "alerts.tests.\(UUID().uuidString)"))
        let books = AlertsDefaultsBookStore(defaults: defaults, prefix: "alerts.test.")

        books.setFiresSeen("aaaa1111", id: 9, at: Self.nowISO)
        books.setFiresSeen("bbbb2222", id: 4, at: Self.ago(60))
        XCTAssertEqual(9, books.firesSeen("aaaa1111"))
        XCTAssertEqual(Self.nowISO, books.firesSeenAt("aaaa1111"))

        books.setFiresSeen("aaaa1111", id: 10, at: nil)
        XCTAssertNil(books.firesSeenAt("aaaa1111"), "an id that moved without a time drops the time")

        books.setAlerted(["aaaa1111|4|1", "bbbb2222|4|1"])
        books.startOver("aaaa1111")
        XCTAssertNil(books.firesSeen("aaaa1111"))
        XCTAssertEqual(["bbbb2222|4|1"], books.alerted())
        XCTAssertEqual(4, books.firesSeen("bbbb2222"), "the other server is untouched")

        books.setHelpSeen("aaaa1111", id: 7)
        XCTAssertEqual(7, books.helpSeen("aaaa1111"))
        books.setIncidentsNotified("aaaa1111", ["3|1"])
        books.setIncidentsWithdrawn("aaaa1111", ["3|1"])
        XCTAssertEqual(["3|1"], books.incidentsNotified("aaaa1111"))
        XCTAssertEqual(["3|1"], books.incidentsWithdrawn("aaaa1111"))
        XCTAssertTrue(books.incidentsNotified("cccc3333").isEmpty)

        XCTAssertFalse(books.hintDismissed)
        books.dismissHint()
        XCTAssertTrue(books.hintDismissed)
    }

    /// ``AlertsBookStore/forgetEverything()`` — the UI cases' reset. Every
    /// book this type writes, and not only the ones a reader thought of: a
    /// book left behind would hush exactly the notification the case it exists
    /// for is waiting for, which is how train S012 recorded that case as
    /// unmeasured.
    ///
    /// Written against a **second** store over the same defaults with another
    /// prefix, so the assertion is "this store's keys, and nobody else's".
    func testForgettingEverythingLeavesNoBookAndNoNeighboursBook() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "alerts.tests.\(UUID().uuidString)"))
        let books = AlertsDefaultsBookStore(defaults: defaults, prefix: "alerts.test.")
        let neighbour = AlertsDefaultsBookStore(defaults: defaults, prefix: "alerts.other.")

        for store in [books, neighbour] {
            store.setAlerted(["aaaa1111|4|1"])
            store.setFiresSeen("aaaa1111", id: 9, at: Self.nowISO)
            store.setHelpSeen("aaaa1111", id: 7)
            store.setIncidentsNotified("aaaa1111", ["3|1"])
            store.setIncidentsWithdrawn("aaaa1111", ["3|1"])
            store.dismissHint()
        }

        books.forgetEverything()

        XCTAssertEqual([], books.alerted())
        XCTAssertNil(books.firesSeen("aaaa1111"))
        XCTAssertNil(books.firesSeenAt("aaaa1111"))
        XCTAssertNil(books.helpSeen("aaaa1111"))
        XCTAssertEqual([], books.incidentsNotified("aaaa1111"))
        XCTAssertEqual([], books.incidentsWithdrawn("aaaa1111"))
        XCTAssertFalse(books.hintDismissed)

        XCTAssertEqual(["aaaa1111|4|1"], neighbour.alerted(), "another store's books stay")
        XCTAssertEqual(9, neighbour.firesSeen("aaaa1111"))
        XCTAssertTrue(neighbour.hintDismissed)
    }

    /// The mirror's file: it round-trips, a corrupt one is an empty book
    /// rather than a throw, and the file is out of the backup.
    func testTheMirrorFileRoundTripsAndIsExcludedFromTheBackup() throws {
        let url = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("alerts-mirror-\(UUID().uuidString)/mirror.json")
        let store = AlertsFileMirrorStore(url: url)
        XCTAssertEqual(AlertsMirrorBook(), store.mirror(), "no file yet")

        let book = AlertsMirrorBook(
            serverKey: "aaaa1111",
            alarms: [AlertsMirrorAlarm(timerID: 4, kind: "reminder", message: "take the tablets at six")]
        )
        store.setMirror(book)
        XCTAssertEqual(book, store.mirror())
        let excluded = try url.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup
        XCTAssertEqual(true, excluded, "a reminder's words are not ours to put in a backup")

        try Data("not json".utf8).write(to: url)
        XCTAssertEqual(AlertsMirrorBook(), store.mirror(), "a corrupt book is an empty one, never a throw")
        try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
    }
}
