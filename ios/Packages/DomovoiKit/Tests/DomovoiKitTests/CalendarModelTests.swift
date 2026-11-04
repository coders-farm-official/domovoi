import Foundation
import XCTest
@testable import DomovoiKit

/// Calendar's data layer against a server that is not a server: what the read
/// asks for, which state the screen is in, what each of the three writes sends
/// and says, and what a reply that lands after the app has changed house is
/// allowed to touch.
///
/// `CalendarScreen.kt` is a composable, so **none** of this is specified over
/// there: the two emptinesses, the stale-after-failure branch, the id read out
/// of the 201, the 204 delete and the server key are tested here for the first
/// time on either phone.
@MainActor
final class CalendarModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let otherServer = "http://10.0.0.99:6369"
    private let chicago = TimeZone(identifier: "America/Chicago")!

    /// 2026-11-04 14:30 in Chicago — a Wednesday, three days after the clocks
    /// went back.
    private let nowMs: Int64 = 1_793_824_200_000

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// Four events: two today, one tomorrow, one brought over from google.
    private final class House: Sendable {

        let rows = Shared<[String]>([
            House.row(1, "bin collection", "2026-11-04T07:00:00-06:00"),
            House.row(2, "dentist", "2026-11-04T09:00:00-06:00", end: "2026-11-04T09:30:00-06:00"),
            House.row(3, "call Mum", "2026-11-05T19:00:00-06:00"),
            House.row(
                4, "book club", "2026-11-07T19:00:00-06:00",
                end: "2026-11-07T21:00:00-06:00",
                source: "google",
                lastSynced: "2026-11-04T14:15:00-06:00"
            ),
        ])
        let readStatus = Shared<Int>(200)
        let writeStatus = Shared<Int>(200)

        /// The whole created row the POST answers with — `calendar.py:110`
        /// returns the event, `id` included.
        let created = Shared<String>(House.row(77, "dinner with mom", "2026-11-05T18:00:00-06:00"))

        /// Every request, as `METHOD path`. The **bodies** are read off
        /// ``StubURLProtocol/recorded`` rather than captured here: the
        /// protocol reads the body stream into its own recorded copy before
        /// the dispatcher runs, so a dispatcher that read the stream again
        /// would see nothing.
        let seen = Shared<[String]>([])

        /// Every GET's query string, in order.
        let asked = Shared<[String]>([])

        static func row(
            _ id: Int64,
            _ title: String,
            _ startsAt: String,
            end: String? = nil,
            location: String? = nil,
            description: String? = nil,
            source: String? = nil,
            lastSynced: String? = nil
        ) -> String {
            func text(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
            return """
            {"id":\(id),"title":"\(title)","starts_at":"\(startsAt)","ends_at":\(text(end)),
             "location":\(text(location)),"description":\(text(description)),
             "source":\(text(source)),"external_id":null,"last_synced_at":\(text(lastSynced))}
            """
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let method = request.httpMethod ?? "GET"
                if parts.path == "/api/health" {
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                }
                self.seen.mutate { $0.append("\(method) \(parts.path)") }
                // The query, separately: these cases assert on paths, and
                // since answer 16 the read carries a window that
                // ``CalendarWindowTests`` is the file for. Recorded rather
                // than folded into ``seen`` so the path assertions here stay
                // about paths.
                if method == "GET" {
                    self.asked.mutate { $0.append(url.query ?? "") }
                }
                switch method {
                case "GET":
                    guard self.readStatus.current == 200 else {
                        return .json(
                            #"{"detail":"this device is not paired with the house"}"#,
                            status: self.readStatus.current
                        )
                    }
                    // **Every row, whatever the window.** This rig is about
                    // the model's states and its three writes; the window is
                    // honoured by the rig in ``CalendarWindowTests``, which is
                    // where the read's bounds are the thing under test.
                    return .json("[\(self.rows.current.joined(separator: ","))]")
                case "POST":
                    guard self.writeStatus.current == 200 else {
                        return .json(#"{"detail":"the calendar is read-only"}"#,
                                     status: self.writeStatus.current)
                    }
                    return .json(self.created.current, status: 201)
                case "PATCH":
                    guard self.writeStatus.current == 200 else {
                        return .json(#"{"detail":"the calendar is read-only"}"#,
                                     status: self.writeStatus.current)
                    }
                    return .json(self.created.current)
                case "DELETE":
                    guard self.writeStatus.current == 200 else {
                        return .json(#"{"detail":"the calendar is read-only"}"#,
                                     status: self.writeStatus.current)
                    }
                    // **204, no body** (`calendar.py:172`) — which is why the
                    // model sends it with `send(method:path:)`.
                    return .json("", status: 204)
                default:
                    return .json("{}", status: 405)
                }
            }
        }
    }

    private func rig(
        house: House = House(),
        server address: String? = nil
    ) -> (CalendarModel, House, InMemoryShellStore, AppContainer) {
        house.install()
        let target = address ?? server
        let store = InMemoryShellStore(serverURL: target, token: "household-abc")
        store.trustServer(target, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = CalendarModel(
            container: container,
            nowMs: { [nowMs] in nowMs },
            timeZone: { [chicago] in chicago }
        )
        return (model, house, store, container)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func everythingLands() async {
        for _ in 0..<settleYieldLimit { await Task.yield() }
    }

    /// The bodies that actually left the phone, in order — read off the
    /// protocol's own recording, which is where a streamed `PATCH` body is
    /// legible.
    private func sentBodies() -> [String] {
        StubURLProtocol.recorded
            .compactMap(\.httpBody)
            .map { String(decoding: $0, as: UTF8.self) }
    }

    private func started(_ model: CalendarModel) async {
        model.start()
        await settleUntil({ model.events.answered }, "the calendar read")
    }

    /// Wait for the read of whatever range the model is now on.
    ///
    /// Needed since answer 16 and not before: a view change or a chevron now
    /// asks the server for its own window, so `emptyText` and `visible` are
    /// not facts about the new range until that read lands
    /// (``CalendarModel/rangePending``). A case that asserted straight after
    /// `show(_:)` would be reading the previous range's page.
    private func onRange(_ model: CalendarModel) async {
        await settleUntil({ !model.rangePending }, "the read for the new range")
    }

    // MARK: - the read

    /// **One read, bounded to the agenda's window** — Kamron's answer 16.
    ///
    /// It used to be one read with **no parameters** at all
    /// (`CalendarScreen.kt:84`, DECISIONS §7), relying on the server's default
    /// first 500 by `starts_at` with every view's window cut client-side. The
    /// bounds themselves are ``CalendarWindowTests``; what this pins is that
    /// the opening read is still exactly **one** request to this route, and
    /// that it now carries a `start`.
    func testTheScreenMakesOneBoundedReadWhenItOpens() async {
        let (model, house, _, _) = rig()
        await started(model)
        XCTAssertEqual(["GET /api/calendar/events"], house.seen.current)
        XCTAssertEqual(1, model.events.fetches)
        XCTAssertEqual(1, house.asked.current.count)
        XCTAssertTrue(
            house.asked.current[0].contains("start="),
            "the opening read is unbounded again: \(house.asked.current[0])"
        )
        XCTAssertEqual(4, model.all.count)
        XCTAssertEqual("4 events in view · the household's calendar", model.subtitle)
    }

    /// The push is the one string the server actually sends:
    /// `calendar_changed` → topic `calendar.events` → `"\(channel).changed"`.
    /// A subscription to `calendar_changed` or to `calendar.changed` would
    /// never fire.
    func testThePushThatMeansReReadIsCalendarEventsChanged() async {
        let (model, _, _, container) = rig()
        await started(model)
        XCTAssertEqual(["calendar.events.changed"], Array(CalendarModel.eventTypes))

        container.stateEventArrived(StateEvent(type: "calendar_changed", frame: "{}"))
        await everythingLands()
        XCTAssertEqual(1, model.events.fetches, "the raw channel name is not the frame type")

        container.stateEventArrived(StateEvent(type: "calendar.events.changed", frame: "{}"))
        await settleUntil({ model.events.fetches == 2 }, "the re-read")
    }

    /// **A failure after a success keeps the rows**, silently, which is
    /// ``ApiRead``'s ported behaviour and Android's (`:162`). The error state
    /// only replaces the screen while there is nothing to show.
    func testAFailedReadBlanksNothingOnceTheRowsAreThere() async {
        let (model, house, _, _) = rig()
        await started(model)
        XCTAssertNil(model.errorText)

        house.readStatus.set(401)
        model.events.refresh()
        await settleUntil({ model.events.error != nil }, "the refusal")
        XCTAssertNil(model.errorText, "the error state must not take over a screen that has rows")
        XCTAssertEqual(4, model.all.count)
        XCTAssertTrue(model.events.isStale)
    }

    /// With nothing to show, the refusal is what the screen says — a phone
    /// whose token the house does not know sees why, not an empty calendar.
    func testWithoutATokenTheScreenSaysWhyRatherThanLookingEmpty() async {
        let house = House()
        house.readStatus.set(401)
        let (model, _, _, _) = rig(house: house)
        await started(model)
        XCTAssertNotNil(model.errorText)
        XCTAssertNil(model.emptyText, "an unanswered read is not an empty calendar")
        XCTAssertTrue(model.all.isEmpty)
    }

    // MARK: - the two emptinesses

    func testTheTwoEmptyWordingsAreAgendasOwnAndTheRangesOwn() async {
        let house = House()
        house.rows.set([])
        let (model, _, _, _) = rig(house: house)
        await started(model)

        XCTAssertEqual(CalendarStrings.emptyAgendaTitle, model.emptyText?.title)
        XCTAssertEqual(CalendarStrings.emptyAgendaBody, model.emptyText?.body)

        model.show(.month)
        await onRange(model)
        XCTAssertEqual(CalendarStrings.emptyRangeTitle, model.emptyText?.title)
        XCTAssertEqual("tap new event to add one for month", model.emptyText?.body)

        model.show(.week)
        await onRange(model)
        XCTAssertEqual(
            "tap new event to add one for week",
            model.emptyText?.body,
            "the week uses the range wording and its own word (answer 14)"
        )

        // No `onRange`: the page in hand is still the **month grid's** — the
        // week asked for nothing either, being inside it — and the 4th is
        // inside that grid too, so ``CalendarPage/serves(_:)`` answers both
        // with no request at all.
        model.show(.day)
        XCTAssertEqual(
            "tap new event to add one for wed, nov 4",
            model.emptyText?.body,
            "day view names the day, not the word `day` (`CalendarScreen.kt:178`)"
        )
    }

    /// A month with rows is not empty, and stepping to one without them is —
    /// so the range wording is reachable in a house that has events, which is
    /// what the agenda wording would otherwise hide.
    func testSteppingToAnEmptyMonthSaysNothingInThisRange() async {
        let (model, _, _, _) = rig()
        await started(model)
        model.show(.month)
        await onRange(model)
        XCTAssertNil(model.emptyText, "november has four events")
        XCTAssertEqual("november 2026", model.rangeLabel)

        model.step(1)
        XCTAssertEqual("december 2026", model.rangeLabel)
        model.step(1)
        XCTAssertEqual("january 2027", model.rangeLabel)
        await onRange(model)
        XCTAssertEqual(CalendarStrings.emptyRangeTitle, model.emptyText?.title)
    }

    // MARK: - moving about

    func testTheChevronsAndTodayMoveTheAnchorPerView() async {
        let (model, _, _, _) = rig()
        await started(model)
        XCTAssertEqual("from wed, nov 4", model.rangeLabel)

        model.step(1)
        XCTAssertEqual("from wed, nov 11", model.rangeLabel, "the agenda steps a week")
        model.goToToday()
        XCTAssertEqual("from wed, nov 4", model.rangeLabel)

        model.show(.day)
        model.step(-1)
        XCTAssertEqual("tuesday, november 3", model.rangeLabel)
        model.goToToday()
        XCTAssertEqual("wednesday, november 4", model.rangeLabel)
    }

    /// A month cell's tap is the only way this port's cell reveals what is on
    /// a date, so it has to land in day view **on that date** (DECISIONS §4).
    func testAMonthCellsTapOpensThatDayInDayView() async {
        let (model, _, _, _) = rig()
        await started(model)
        model.show(.month)
        await onRange(model)
        model.pickDay(CalendarDay(year: 2026, month: 11, day: 7))
        XCTAssertEqual(CalendarViewMode.day, model.mode)
        XCTAssertEqual("saturday, november 7", model.rangeLabel)
        XCTAssertEqual([4], model.visible.map(\.id), "the book club is the only thing on the 7th")
    }

    /// The agenda's groups, end to end: today's header is the literal word and
    /// tomorrow's is its label, and the open-ended 07:00 row is still in
    /// today's group because the floor is **midnight**, not now.
    func testTheAgendaGroupsTodayAndTomorrowSeparately() async {
        let (model, _, _, _) = rig()
        await started(model)
        XCTAssertEqual(
            [CalendarStrings.today, "thu, nov 5", "sat, nov 7"],
            model.agendaSections.map(\.header)
        )
        XCTAssertEqual([[1, 2], [3], [4]], model.agendaSections.map { $0.rows.map(\.id) })
        XCTAssertEqual("2026-11-04", model.agendaSections.first?.key)
    }

    // MARK: - the detail

    func testTheSelectedRowIsReadOutOfTheListAndCarriesAllNineFields() async {
        let house = House()
        house.rows.mutate {
            $0[3] = House.row(
                4, "book club", "2026-11-07T19:00:00-06:00",
                end: "2026-11-07T21:00:00-06:00",
                location: "the back room",
                description: "brought over from google",
                source: "google",
                lastSynced: "2026-11-04T14:15:00-06:00"
            )
        }
        let (model, _, _, _) = rig(house: house)
        await started(model)
        model.select(4)
        guard let row = model.selected else { return XCTFail("nothing selected") }
        XCTAssertEqual("brought over from google", row.description)
        XCTAssertEqual("the back room", row.location)
        XCTAssertTrue(row.isGoogle)
        XCTAssertEqual("15m ago", relTime(row.lastSyncedAt, nowMs: nowMs))
        XCTAssertEqual("synced 15m ago", CalendarStrings.synced(relTime(row.lastSyncedAt, nowMs: nowMs)))
        XCTAssertEqual("event #4", CalendarStrings.eventNumber(row.id))

        model.select(1)
        XCTAssertNil(model.selected?.lastSyncedAt, "a local row has nothing to say about syncing")
    }

    // MARK: - creating

    /// The POST, its body, the **201's own `id`** as the new selection, and
    /// the refresh afterwards.
    func testANewEventIsPostedAndTheReplysIdBecomesTheSelection() async {
        let (model, house, _, _) = rig()
        await started(model)
        model.openNewEvent()
        guard var draft = model.draft else { return XCTFail("no draft") }
        XCTAssertEqual(
            "2:00pm",
            calendarClock(ms: Int64(draft.start.timeIntervalSince1970 * 1000), timeZone: chicago),
            "the anchor is today and it is already past nine"
        )
        draft.title = "dinner with mom"
        draft.start = Date(timeIntervalSince1970: Double(isoMs("2026-11-05T18:00:00-06:00") ?? 0) / 1000)
        draft.end = Date(timeIntervalSince1970: Double(isoMs("2026-11-05T19:00:00-06:00") ?? 0) / 1000)

        XCTAssertTrue(model.save(draft))
        await settleUntil({ model.toast != nil }, "the create")

        XCTAssertEqual(CalendarStrings.eventCreated, model.toast)
        XCTAssertEqual(77, model.selectedID, "the id came out of the 201 body, not out of a re-read")
        XCTAssertNil(model.draft, "the editor closed")
        XCTAssertTrue(house.seen.current.contains("POST /api/calendar/events"))
        XCTAssertEqual(
            #"{"title":"dinner with mom","starts_at":"2026-11-06T00:00:00Z","ends_at":"2026-11-06T01:00:00Z","location":null,"description":null}"#,
            sentBodies().first
        )
        await settleUntil({ model.events.fetches == 2 }, "the refresh")
    }

    /// A draft that fails its own validation **sends nothing** — the editor
    /// stays open with its message and the row count is untouched.
    func testADraftThatFailsValidationIsNotSentAtAll() async {
        let (model, house, _, _) = rig()
        await started(model)
        model.openNewEvent()
        guard let draft = model.draft else { return XCTFail("no draft") }
        XCTAssertEqual(CalendarStrings.titleRequired, draft.refusal)

        XCTAssertFalse(model.save(draft))
        await everythingLands()
        XCTAssertEqual(
            ["GET /api/calendar/events"],
            house.seen.current,
            "a blank title reached the server"
        )
        XCTAssertNil(model.toast)
        XCTAssertNotNil(model.draft, "the editor stayed open to show why")
    }

    func testASaveTheHouseRefusedSaysSoAndKeepsTheEditorOpen() async {
        let (model, house, _, _) = rig()
        await started(model)
        house.writeStatus.set(403)
        model.openNewEvent()
        guard var draft = model.draft else { return XCTFail("no draft") }
        draft.title = "dinner"

        XCTAssertTrue(model.save(draft))
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertTrue(model.toast?.hasPrefix("save failed: ") == true, model.toast ?? "nil")
        XCTAssertNotNil(model.draft, "a refused save does not close the editor over the words")
        XCTAssertNil(model.selectedID)
    }

    // MARK: - editing

    /// The PATCH goes to the row's own path and the row stays selected.
    func testAnEditPatchesTheRowAndKeepsItSelected() async {
        let (model, house, _, _) = rig()
        await started(model)
        model.select(2)
        guard let row = model.selected else { return XCTFail("nothing selected") }
        model.openEditor(for: row)
        guard var draft = model.draft else { return XCTFail("no draft") }
        XCTAssertEqual(2, draft.id)
        XCTAssertEqual("dentist", draft.title)
        draft.title = "dentist — moved"

        XCTAssertTrue(model.save(draft))
        await settleUntil({ model.toast != nil }, "the edit")
        XCTAssertEqual(CalendarStrings.eventUpdated, model.toast)
        XCTAssertEqual(2, model.selectedID)
        XCTAssertTrue(house.seen.current.contains("PATCH /api/calendar/events/2"))
        XCTAssertTrue(
            sentBodies().first?.contains(#""title":"dentist — moved""#) == true,
            sentBodies().first ?? "no body"
        )
    }

    /// **A blank field is sent as `null`**, which is how a PATCH clears a
    /// column the editor had filled (`calendar.py:146`'s `exclude_unset`): a
    /// body that simply left the key out would leave the old place on the row
    /// for ever.
    func testClearingAPlaceSendsAnExplicitNull() async {
        let house = House()
        house.rows.mutate {
            $0[1] = House.row(
                2, "dentist", "2026-11-04T09:00:00-06:00",
                end: "2026-11-04T09:30:00-06:00",
                location: "the high street"
            )
        }
        let (model, _, _, _) = rig(house: house)
        await started(model)
        model.select(2)
        guard let row = model.selected, var draft = CalendarDraft(row) else {
            return XCTFail("no draft")
        }
        XCTAssertEqual("the high street", draft.location)
        draft.location = ""

        XCTAssertTrue(model.save(draft))
        await settleUntil({ model.toast != nil }, "the edit")
        XCTAssertTrue(
            sentBodies().first?.contains(#""location":null"#) == true,
            sentBodies().first ?? "no body"
        )
    }

    // MARK: - deleting

    /// The confirm, the **204** and the toast — and that the row's own
    /// selection is cleared. `delete(_:as:)` on a 204 would throw, so a
    /// delete that worked would say it failed.
    func testDeleteAsksFirstThenHandlesTheTwoOhFour() async {
        let (model, house, _, _) = rig()
        await started(model)
        model.select(3)
        guard let row = model.selected else { return XCTFail("nothing selected") }

        model.askToDelete(row)
        XCTAssertNotNil(model.deleteTarget)
        await everythingLands()
        XCTAssertEqual(
            ["GET /api/calendar/events"],
            house.seen.current,
            "asking is not deleting"
        )
        XCTAssertEqual(CalendarStrings.deleteTitle, "delete event")
        XCTAssertEqual(#"Delete "call Mum"? This can't be undone."#, CalendarStrings.deleteBody(row.title))

        model.confirmDelete(row)
        await settleUntil({ model.toast != nil }, "the delete")
        XCTAssertEqual(#"deleted "call Mum""#, model.toast)
        XCTAssertNil(model.selectedID)
        XCTAssertNil(model.deleteTarget)
        XCTAssertTrue(house.seen.current.contains("DELETE /api/calendar/events/3"))
    }

    /// Cancel is the alert's **other** button: it closes the alert and
    /// `confirmDelete(_:)` is never reached.
    func testCancellingTheAlertDeletesNothing() async {
        let (model, house, _, _) = rig()
        await started(model)
        model.select(3)
        guard let row = model.selected else { return XCTFail("nothing selected") }
        model.askToDelete(row)
        model.cancelDelete()
        XCTAssertNil(model.deleteTarget)
        await everythingLands()
        XCTAssertEqual(["GET /api/calendar/events"], house.seen.current)
        XCTAssertNil(model.toast)
    }

    /// **The dismissal racing the confirm.** The alert's destructive button
    /// and the `isPresented` binding that calls ``CalendarModel/cancelDelete()``
    /// fire from one tap, in an order SwiftUI does not contract. On the order
    /// where the dismissal wins, a `confirmDelete()` that read `deleteTarget`
    /// back found it nil and did nothing at all — no DELETE, no toast, the row
    /// still there. The row is an argument now, so either order deletes.
    func testADismissalRacingTheConfirmStillDeletes() async {
        let (model, house, _, _) = rig()
        await started(model)
        model.select(3)
        guard let row = model.selected else { return XCTFail("nothing selected") }
        model.askToDelete(row)
        model.cancelDelete()
        model.confirmDelete(row)
        await settleUntil({ model.toast != nil }, "the delete")
        XCTAssertEqual(#"deleted "call Mum""#, model.toast)
        XCTAssertTrue(house.seen.current.contains("DELETE /api/calendar/events/3"))
    }

    /// And the house check that replaced the target read: a confirm after the
    /// saved server moved names a row in the house the person has left, so it
    /// sends nothing — what ``CalendarModel/serverChanged()`` used to get from
    /// nilling `deleteTarget`.
    func testAConfirmAfterMovingHouseSendsNoDelete() async {
        let (model, house, store, _) = rig()
        await started(model)
        model.select(3)
        guard let row = model.selected else { return XCTFail("nothing selected") }
        model.askToDelete(row)

        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(otherServer))
        model.serverChanged()

        model.confirmDelete(row)
        await everythingLands()
        XCTAssertFalse(
            house.seen.current.contains("DELETE /api/calendar/events/3"),
            "the delete went to the house the person had left"
        )
        XCTAssertNil(model.toast)
    }

    func testADeleteTheHouseRefusedSaysSoAndKeepsTheSelection() async {
        let (model, house, _, _) = rig()
        await started(model)
        house.writeStatus.set(404)
        model.select(3)
        guard let row = model.selected else { return XCTFail("nothing selected") }
        model.askToDelete(row)
        model.confirmDelete(row)
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertTrue(model.toast?.hasPrefix("delete failed: ") == true, model.toast ?? "nil")
        XCTAssertEqual(3, model.selectedID, "a row the house still has stays selected")
    }

    // MARK: - the house changed under the write

    /// **The read-after-await case, switched inside the round trip.**
    ///
    /// The POST is held open, the app moves house, and then the reply lands.
    /// A toast, a selection or a refresh applied now would all belong to the
    /// house the person has left — and `77` is an id that exists in both. This
    /// is the top defect class on this track, so the key is captured before the
    /// `await` and compared again after it.
    func testACreateThatLandsAfterMovingHouseTouchesNothing() async {
        let arrived = Shared<Bool>(false)
        let release = Shared<Bool>(false)
        let ranOut = Shared<Bool>(false)
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })
            else { return .json("{}", status: 404) }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            guard request.httpMethod == "POST" else { return .json("[]") }
            arrived.set(true)
            for step in 0..<400 {
                if release.current { break }
                if step == 399 { ranOut.set(true) }
                Thread.sleep(forTimeInterval: 0.002)
            }
            return .json(House.row(77, "dinner with mom", "2026-11-05T18:00:00-06:00"), status: 201)
        }
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = CalendarModel(
            container: container,
            nowMs: { [nowMs] in nowMs },
            timeZone: { [chicago] in chicago }
        )
        model.start()
        await settleUntil({ model.events.answered }, "the first read")

        model.openNewEvent()
        guard var draft = model.draft else { return XCTFail("no draft") }
        draft.title = "dinner with mom"
        XCTAssertTrue(model.save(draft))
        await settle(for: arrived, what: "the create")

        // The app moves house **while the POST is in flight**.
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(otherServer))
        model.serverChanged()

        release.set(true)
        await everythingLands()
        XCTAssertFalse(ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertNil(
            model.toast,
            "the first house said `event created` into the second house's screen"
        )
        XCTAssertNil(
            model.selectedID,
            "the first house's new id selected a row in the second house's list"
        )
    }

    /// Moving house also drops a half-written draft and a pending delete: both
    /// name a row id that belongs to the house the person has left.
    func testMovingHouseClearsTheDraftTheSelectionAndThePendingDelete() async {
        let (model, _, store, _) = rig()
        await started(model)
        model.select(2)
        guard let row = model.selected else { return XCTFail("nothing selected") }
        model.openEditor(for: row)
        model.askToDelete(row)
        XCTAssertNotNil(model.draft)
        XCTAssertNotNil(model.deleteTarget)

        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(otherServer))
        model.serverChanged()

        XCTAssertNil(model.draft)
        XCTAssertNil(model.deleteTarget)
        XCTAssertNil(model.selectedID)
    }

    // MARK: - lifecycle

    /// `stop()` **leaves** the push registry, so a screen on its way out does
    /// not re-read on every push for the rest of the process — and is not a
    /// closure the container holds for ever.
    ///
    /// Fired through ``AppContainer/stateEventArrived(_:)`` and not through
    /// `model.received(_:)`: the registry is what `stop()` leaves, so a test
    /// that called the method by hand would be asserting that a method does
    /// nothing when called, which is not the property.
    func testAStoppedScreenLeavesTheFanOutAndNoLongerReReads() async {
        let (model, _, _, container) = rig()
        XCTAssertEqual(0, container.stateEventSinkCount, "nothing listens before the screen")
        await started(model)
        XCTAssertEqual(1, container.stateEventSinkCount)

        let before = model.events.fetches
        model.stop()
        XCTAssertEqual(0, container.stateEventSinkCount, "and a dead screen is not a leak")
        container.stateEventArrived(StateEvent(type: "calendar.events.changed", frame: "{}"))
        await everythingLands()
        XCTAssertEqual(
            before,
            model.events.fetches,
            "a stopped read answered a push it was no longer on screen for"
        )
    }

    /// An entry whose `starts_at` will not parse opens **no** editor: Android
    /// opens one on `LocalDateTime.now()`, and a save from there overwrites
    /// the row's real time with today's.
    func testAnUnreadableRowOpensNoEditor() async {
        let house = House()
        house.rows.set([House.row(1, "who knows", "not a time")])
        let (model, _, _, _) = rig(house: house)
        await started(model)
        guard let row = model.all.first else { return XCTFail("no row") }
        model.openEditor(for: row)
        XCTAssertNil(model.draft)
    }
}

