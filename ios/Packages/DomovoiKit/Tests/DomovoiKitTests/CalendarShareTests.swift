import Foundation
import XCTest
@testable import DomovoiKit

/// "Share this phone's calendars with the house" — the port of
/// `SharePayloadTest.kt` and `ShareSyncTest.kt` (merge `bf05916b`) **with the
/// review's ten corrections** (coordinator, 2026-11-14). Every correction has
/// a case here, named for it, that fails on the defect.
final class CalendarShareTests: XCTestCase {

    // MARK: - fixtures

    private let now: Int64 = 1_795_000_000_000          // mid-November 2026
    private let hour: Int64 = 3_600_000

    private func cal(_ id: String, _ name: String = "Work", account: String? = "alex@example.com",
                     color: String? = "#33b679") -> PhoneCalendarInfo {
        PhoneCalendarInfo(id: id, name: name, accountName: account, colorHex: color)
    }

    private func row(_ event: String, cal: String = "a", at: Int64? = nil, title: String? = "Dentist",
                     location: String? = nil, cancelled: Bool = false, declined: Bool = false,
                     isPrivate: Bool = false) -> PhoneEventRow {
        let begin = at ?? (now + hour)
        return PhoneEventRow(eventID: event, calendarID: cal, beginMs: begin, endMs: begin + hour, allDay: false,
                             title: title, location: location, cancelled: cancelled, declined: declined,
                             isPrivate: isPrivate)
    }

    private func json(_ body: ShareSnapshotBody) throws -> [String: Any] {
        let object = try JSONSerialization.jsonObject(with: CalendarSharePayload.encode(body))
        return try XCTUnwrap(object as? [String: Any])
    }

    private func build(_ calendars: [PhoneCalendarInfo], _ rows: [PhoneEventRow], picked: Set<String>? = nil,
                       phoneName: String? = "Alex's iPhone") -> BuiltSharePayload {
        CalendarSharePayload.build(
            phoneName: phoneName, window: CalendarSharePayload.window(nowMs: now), calendars: calendars,
            rows: rows, picked: picked ?? Set(calendars.map(\.id)), nowMs: now
        )
    }

    // MARK: - the body (SharePayloadTest)

    func testTheBodyHasTheServersShapeAndOnlyTheMinimumFields() throws {
        let built = build([cal("a")], [row("e1", location: "Market Street")])
        let body = try json(built.body)
        XCTAssertEqual(Set(["phone_name", "window_start_ms", "window_end_ms", "calendars"]), Set(body.keys))
        let calendars = try XCTUnwrap(body["calendars"] as? [[String: Any]])
        let first = try XCTUnwrap(calendars.first)
        XCTAssertEqual(Set(["calendar_id", "name", "color", "events"]), Set(first.keys))
        let events = try XCTUnwrap(first["events"] as? [[String: Any]])
        let event = try XCTUnwrap(events.first)
        XCTAssertEqual(Set(["uid", "title", "starts_at_ms", "ends_at_ms", "all_day", "location"]), Set(event.keys))
        XCTAssertEqual("e1:\(now + hour)", event["uid"] as? String, "one uid per occurrence")
        XCTAssertEqual("Market Street", event["location"] as? String)
    }

    /// The window: 30 days back, 180 ahead (`SharePayload.window`).
    func testTheWindowIsThirtyDaysBackAndOneEightyAhead() {
        let w = CalendarSharePayload.window(nowMs: now)
        XCTAssertEqual(now - 30 * 86_400_000, w.startMs)
        XCTAssertEqual(now + 180 * 86_400_000, w.endMs)
    }

    /// Correction 5: no account name is ever sent, and a calendar named after
    /// an email address goes as `Main calendar`. Fails on the Android
    /// original, which sent `account_name`.
    func testCorrection5_NoAccountNameAndAnEmailTitleIsMainCalendar() throws {
        let built = build([cal("a", "alex@example.com"), cal("b", "Kids")], [])
        let text = String(decoding: CalendarSharePayload.encode(built.body), as: UTF8.self)
        XCTAssertFalse(text.contains("account_name"))
        XCTAssertFalse(text.contains("example.com"), "neither as an account nor as a name")
        XCTAssertEqual(["Main calendar", "Kids"], built.body.calendars.map(\.name))
        XCTAssertTrue(CalendarSharePayload.looksLikeEmail(" jordan.p@mail.example.org "))
        XCTAssertFalse(CalendarSharePayload.looksLikeEmail("Work @ home"))
        XCTAssertFalse(CalendarSharePayload.looksLikeEmail("Holidays in the UK"))
        XCTAssertFalse(CalendarSharePayload.looksLikeEmail("@handle"))
    }

    /// Correction 9: declined and cancelled occurrences are left out, and a
    /// location keeps no web link. Fails if either reaches the body.
    func testCorrection9_DeclinedEventsAndWebLinksStayOnThePhone() {
        let built = build([cal("a")], [
            row("keep", location: "Room 4 https://zoom.us/j/123"),
            row("link", at: now + 2 * hour, location: "https://meet.google.com/abc-defg"),
            row("no", at: now + 3 * hour, declined: true),
            row("off", at: now + 4 * hour, cancelled: true),
        ])
        let events = built.body.calendars.first?.events ?? []
        XCTAssertEqual(["keep", "link"], events.map { String($0.uid.split(separator: ":").first ?? "") })
        XCTAssertEqual(["Room 4", "online meeting"], events.map { $0.location ?? "-" })
        XCTAssertNil(CalendarSharePayload.sentLocation("  "))
        XCTAssertEqual("Kitchen", CalendarSharePayload.sentLocation("Kitchen"))
        XCTAssertEqual("Church hall", CalendarSharePayload.sentLocation("Church hall (www.example.org/map)"))
    }

    /// Correction 4: a private event goes as `busy` with no place. On an
    /// iPhone the reader can never say private (EventKit has no flag), so
    /// this pins the rule the body shares with Android, and the panel says
    /// the iPhone can't tell.
    func testCorrection4_APrivateEventIsBusyWithNoPlace() {
        let built = build([cal("a")], [row("p", title: "Therapy", location: "Elm St", isPrivate: true)])
        let event = built.body.calendars.first?.events.first
        XCTAssertEqual("busy", event?.title)
        XCTAssertNil(event?.location)
        XCTAssertTrue(CalendarShareStrings.privateEvents.contains("doesn't tell apps which events are marked private"))
    }

    /// Correction 10: `phone_name` within the server's 80 characters.
    func testCorrection10_ThePhoneNameFitsTheServer() {
        let long = String(repeating: "x", count: 120)
        XCTAssertEqual(80, build([cal("a")], [], phoneName: long).body.phoneName?.count)
        XCTAssertNil(build([cal("a")], [], phoneName: "  ").body.phoneName)
    }

    /// Kotlin's `take`, in UTF-16 units, never through a surrogate pair.
    func testClippingNeverSplitsAPair() {
        XCTAssertEqual("ab", CalendarSharePayload.clip("abc", 2))
        XCTAssertEqual("a", CalendarSharePayload.clip("a\u{1F600}", 2), "the emoji would need units 2 and 3")
        XCTAssertNil(CalendarSharePayload.clip(" \n", 5))
    }

    /// Only the ticked calendars, at most 25, and a ticked one the phone no
    /// longer has is simply not sent.
    func testOnlyTickedCalendarsGoAndTheCapHolds() {
        let many = (0..<30).map { cal(String(format: "c%02d", $0), "Cal \($0)") }
        let built = build(many, [], picked: Set(many.map(\.id)).union(["gone"]))
        XCTAssertEqual(25, built.calendars)
        XCTAssertEqual(5, built.droppedCalendars)
        let one = build([cal("a"), cal("b")], [row("x", cal: "b")], picked: ["a"])
        XCTAssertEqual(["a"], one.body.calendars.map(\.calendarID))
        XCTAssertEqual(0, one.events)
    }

    /// Past the cap: what is still to come first, then the most recent past.
    func testKeepPrefersWhatIsAheadThenTheRecentPast() {
        let rows = [
            row("old", at: now - 10 * hour), row("recent", at: now - 3 * hour),
            row("soon", at: now + hour), row("later", at: now + 9 * hour),
        ]
        XCTAssertEqual(["recent", "soon", "later"],
                       CalendarSharePayload.keep(rows, cap: 3, nowMs: now).map(\.eventID))
        XCTAssertEqual(["soon"], CalendarSharePayload.keep(rows, cap: 1, nowMs: now).map(\.eventID))
    }

    /// An all-day event goes as the UTC midnights of its dates, whatever zone
    /// the phone is in (the server reads it by its UTC date). Fails on
    /// sending EventKit's local midnight, which puts Tokyo's holiday on the
    /// day before.
    func testAnAllDayEventGoesAsUTCMidnightsOfItsDates() throws {
        let tokyo = try XCTUnwrap(TimeZone(identifier: "Asia/Tokyo"))
        let day = CalendarDay(year: 2026, month: 11, day: 26)
        let start = day.startMs(in: tokyo)
        let end = day.startMs(in: tokyo) + 86_400_000 - 1000           // 23:59:59, EventKit's
        let bounds = CalendarSharePayload.allDayBoundsMs(startMs: start, endMs: end, timeZone: tokyo)
        let gmt = TimeZone(secondsFromGMT: 0) ?? .gmt
        XCTAssertEqual(day.startMs(in: gmt), bounds.start)
        XCTAssertEqual(day.adding(days: 1).startMs(in: gmt), bounds.end)
        // Ending exactly on a midnight is already exclusive.
        let exact = CalendarSharePayload.allDayBoundsMs(startMs: start, endMs: day.adding(days: 2).startMs(in: tokyo),
                                                        timeZone: tokyo)
        XCTAssertEqual(day.adding(days: 2).startMs(in: gmt), exact.end)
    }

    /// Correction 8's fingerprint leaves the window out: the same snapshot
    /// an hour later is the same print, and a changed title is not.
    func testTheFingerprintIgnoresTheWindowButNotTheEvents() {
        func print(_ at: Int64, _ title: String) -> String {
            let built = CalendarSharePayload.build(
                phoneName: "p", window: CalendarSharePayload.window(nowMs: at), calendars: [cal("a")],
                rows: [row("e", title: title)], picked: ["a"], nowMs: at
            )
            return CalendarSharePayload.fingerprint(built.body)
        }
        XCTAssertEqual(print(now, "Dentist"), print(now + hour, "Dentist"))
        XCTAssertNotEqual(print(now, "Dentist"), print(now, "Dentist moved"))
    }

    /// Correction 6: the disclosure says who sees the copy, in so many words.
    func testCorrection6_TheDisclosureSaysEveryoneAtHomeCanSeeAndHearThem() {
        let words = CalendarShareStrings.disclosure(server: "10.0.0.42:6369")
        XCTAssertTrue(words.contains(
            "Everyone in your household can see them, and Domovoi can read them out in any room."
        ))
        XCTAssertTrue(words.contains("(10.0.0.42:6369)"))
        XCTAssertTrue(words.contains("nothing goes to Coders Farm"))
        XCTAssertTrue(CalendarShareStrings.timing.contains("doesn't let apps update on a schedule"))
    }

    // MARK: - the rules (ShareState)

    func testTheActionFollowsTheSwitchAndTheTicks() {
        XCTAssertEqual(.nothing, CalendarShareRules.action(nil))
        let on = CalendarShareRules.turnOn(nil)
        XCTAssertEqual(.nothing, CalendarShareRules.action(on), "on with nothing ticked sends nothing")
        let ticked = CalendarShareRules.toggle(on, id: "a", name: "Work", on: true)
        XCTAssertEqual(.put, CalendarShareRules.action(ticked))
        XCTAssertEqual(.delete, CalendarShareRules.action(CalendarShareRules.turnOff(ticked)))
        XCTAssertEqual(.nothing, CalendarShareRules.action(CalendarShareRules.deleted(CalendarShareRules.turnOff(ticked))))
    }

    /// Correction 7: a ticked calendar that went loses its tick, by name.
    func testCorrection7_AVanishedCalendarIsNamedAndUnticked() {
        var e = CalendarShareRules.turnOn(nil)
        e = CalendarShareRules.toggle(e, id: "a", name: "Work", on: true)
        e = CalendarShareRules.toggle(e, id: "b", name: "Kids", on: true)
        let after = CalendarShareRules.dropVanished(e, onPhone: ["b"])
        XCTAssertEqual(["b"], after.calendarIDs)
        XCTAssertEqual("Work is no longer on this phone.", after.notice)
        XCTAssertEqual(e, CalendarShareRules.dropVanished(e, onPhone: ["a", "b"]), "nothing went, nothing changes")
    }

    /// Correction 8: unchanged is not sent again until a day has passed.
    func testCorrection8_AnUnchangedSnapshotWaitsADay() {
        let sent = CalendarShareRules.sent(CalendarShareEntry(enabled: true), nowMs: now, dropped: 0, fingerprint: "f1")
        XCTAssertFalse(CalendarShareRules.needsSend(sent, fingerprint: "f1", nowMs: now + hour))
        XCTAssertTrue(CalendarShareRules.needsSend(sent, fingerprint: "f2", nowMs: now + hour))
        XCTAssertTrue(CalendarShareRules.needsSend(sent, fingerprint: "f1", nowMs: now + 24 * hour))
        XCTAssertTrue(CalendarShareRules.needsSend(CalendarShareEntry(enabled: true), fingerprint: "f1", nowMs: now))
    }

    func testTheStatusLineSaysWhereThingsStand() {
        let rel: (Int64) -> String = { _ in "5m ago" }
        XCTAssertNil(CalendarShareStrings.statusLine(nil, relative: rel))
        XCTAssertEqual("tick the calendars to share",
                       CalendarShareStrings.statusLine(CalendarShareEntry(enabled: true), relative: rel))
        XCTAssertEqual("deleting the house's copy…",
                       CalendarShareStrings.statusLine(CalendarShareEntry(pendingDelete: true), relative: rel))
        XCTAssertEqual("last sent 5m ago · 3 events left out (too many to send)",
                       CalendarShareStrings.statusLine(
                        CalendarShareEntry(enabled: true, calendarIDs: ["a"], lastSentAtMs: 1, lastDropped: 3),
                        relative: rel))
    }

    func testTheStoreKeepsEachServersOwnEntryOnDisk() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let url = folder.appendingPathComponent("calendar-share.json")
        let store = CalendarShareStore(url: url)
        store.update("http://10.0.0.42:6369/") { CalendarShareRules.toggle(CalendarShareRules.turnOn($0), id: "a", name: "Work", on: true) }
        let again = CalendarShareStore(url: url)
        XCTAssertEqual(["a"], again.get("http://10.0.0.42:6369")?.calendarIDs)
        XCTAssertNil(again.get("http://10.0.0.43:6369"), "another house does not get it")
        again.forget("http://10.0.0.42:6369")
        XCTAssertNil(CalendarShareStore(url: url).get("http://10.0.0.42:6369"))
    }
}

// MARK: - the passes (ShareSyncTest)

/// A pretend phone and house for ``CalendarShareSync``.
private final class Rig: @unchecked Sendable {
    let store = CalendarShareStore(url: nil)
    let calendars = Shared<[PhoneCalendarInfo]?>([PhoneCalendarInfo(id: "a", name: "Work", colorHex: "#33b679")])
    let rows = Shared<[PhoneEventRow]?>([])
    let calls = Shared<[String]>([])
    let putError = Shared<DomovoiError?>(nil)
    let deleteError = Shared<DomovoiError?>(nil)
    /// Run inside the PUT, after it is "sent" and before it answers.
    let duringPut = Shared<(@Sendable () -> Void)?>(nil)
    let clock = Shared<Int64>(1_795_000_000_000)

    func sync() -> CalendarShareSync {
        CalendarShareSync(
            store: store,
            calendars: { [self] in calendars.current },
            events: { [self] _, ids in rows.current.map { $0.filter { ids.contains($0.calendarID) } } },
            put: { [self] server, _ in
                calls.mutate { $0.append("PUT \(server)") }
                duringPut.current?()
                try await Task.sleep(nanoseconds: 20_000_000)
                if let e = putError.current { throw e }
                calls.mutate { $0.append("PUT done") }
            },
            delete: { [self] server in
                calls.mutate { $0.append("DELETE \(server)") }
                if let e = deleteError.current { throw e }
            },
            phoneName: { "Alex's iPhone" },
            now: { [self] in clock.current }
        )
    }

    func ticked(_ server: String) {
        store.update(server) { CalendarShareRules.toggle(CalendarShareRules.turnOn($0), id: "a", name: "Work", on: true) }
    }

    static func failure(_ status: Int) -> ApiFailure { ApiFailure(status: status, message: "\(status)") }
}

final class CalendarShareSyncTests: XCTestCase {

    private let house = "http://10.0.0.42:6369"
    private let other = "http://10.0.0.43:6369"

    func testASendRecordsWhenAndWhat() async {
        let rig = Rig()
        rig.ticked(house)
        let out = await rig.sync().run(server: house)
        XCTAssertEqual(.done, out)
        XCTAssertEqual(["PUT \(house)", "PUT done"], rig.calls.current)
        XCTAssertNotNil(rig.store.get(house)?.lastSentAtMs)
        XCTAssertNotNil(rig.store.get(house)?.lastFingerprint)
    }

    /// Correction 3: switched off while the PUT was out. The PUT is not
    /// cancelled, and the DELETE goes **after** it answers, in the same
    /// pass. Fails on the Android original, whose re-queued job could land
    /// the PUT after the DELETE, or not delete until the next run.
    func testCorrection3_OffDuringAnUploadDeletesAfterIt() async {
        let rig = Rig()
        rig.ticked(house)
        let store = rig.store
        let server = house
        rig.duringPut.set({ store.update(server) { CalendarShareRules.turnOff($0) } })
        _ = await rig.sync().run(server: house)
        XCTAssertEqual(["PUT \(house)", "PUT done", "DELETE \(house)"], rig.calls.current)
        XCTAssertEqual(false, rig.store.get(house)?.pendingDelete)
        XCTAssertNil(rig.store.get(house)?.lastSentAtMs)
    }

    /// Correction 3: two passes for one server never overlap.
    func testCorrection3_PassesForOneServerRunOneAfterAnother() async {
        let rig = Rig()
        rig.ticked(house)
        let sync = rig.sync()
        // A new event arrives while the first PUT is out, so the second
        // pass has something new to send.
        let rows = rig.rows
        let begin = rig.clock.current + 3_600_000
        rig.duringPut.set({
            rows.set([PhoneEventRow(eventID: "new", calendarID: "a", beginMs: begin, endMs: nil,
                                    allDay: false, title: "New", location: nil)])
        })
        let server = house
        async let first = sync.run(server: server)
        async let second = sync.run(server: server)
        _ = await (first, second)
        XCTAssertEqual(["PUT \(house)", "PUT done", "PUT \(house)", "PUT done"], rig.calls.current)
    }

    /// Correction 8: an unchanged snapshot is not sent again the same day.
    func testCorrection8_AnUnchangedSnapshotIsSkipped() async {
        let rig = Rig()
        rig.ticked(house)
        let sync = rig.sync()
        _ = await sync.run(server: house)
        _ = await sync.run(server: house)
        XCTAssertEqual(["PUT \(house)", "PUT done"], rig.calls.current)
        rig.clock.mutate { $0 += 24 * 3_600_000 }
        _ = await sync.run(server: house)
        XCTAssertEqual(4, rig.calls.current.count, "a day on, it goes again so the window moves")
    }

    /// Correction 7: calendar access taken back → off, the copy deleted,
    /// and a notice. Fails on the Android original, which kept the house's
    /// copy and only said so.
    func testCorrection7_AccessTakenBackTurnsItOffAndDeletes() async {
        let rig = Rig()
        rig.ticked(house)
        rig.calendars.set(nil)
        _ = await rig.sync().run(server: house)
        XCTAssertEqual(["DELETE \(house)"], rig.calls.current)
        let e = rig.store.get(house)
        XCTAssertEqual(false, e?.enabled)
        XCTAssertEqual(false, e?.pendingDelete)
        XCTAssertEqual(CalendarShareStrings.permissionGoneNotice, e?.notice)
    }

    /// Correction 7: a ticked calendar gone from the phone is unticked and
    /// named, and the send carries the rest.
    func testCorrection7_AVanishedCalendarIsDroppedBeforeTheSend() async {
        let rig = Rig()
        rig.ticked(house)
        rig.store.update(house) { CalendarShareRules.toggle($0, id: "gone", name: "Old club", on: true) }
        _ = await rig.sync().run(server: house)
        XCTAssertEqual(["a"], rig.store.get(house)?.calendarIDs)
        XCTAssertEqual("Old club is no longer on this phone.", rig.store.get(house)?.notice)
        XCTAssertEqual(["PUT \(house)", "PUT done"], rig.calls.current)
    }

    /// Correction 2: the house refusing this phone (a revoked token) ends
    /// sharing and does not keep retrying with a token that can't work.
    func testCorrection2_ARefusedPhoneStopsSharing() async {
        let rig = Rig()
        rig.ticked(house)
        rig.putError.set(.refused(Rig.failure(401)))
        let out = await rig.sync().run(server: house)
        XCTAssertEqual(.done, out)
        let e = rig.store.get(house)
        XCTAssertEqual(false, e?.enabled)
        XCTAssertEqual(false, e?.pendingDelete)
        XCTAssertEqual(CalendarShareStrings.refusedNotice, e?.notice)
    }

    /// Correction 2's parting delete is decided by this: a server that may
    /// hold a copy gets the DELETE before it is forgotten.
    func testCorrection2_WhoMayHoldACopy() {
        XCTAssertFalse(CalendarShareRules.mayHaveCopy(nil))
        XCTAssertFalse(CalendarShareRules.mayHaveCopy(CalendarShareRules.turnOn(nil)))
        XCTAssertTrue(CalendarShareRules.mayHaveCopy(CalendarShareEntry(pendingDelete: true)))
        XCTAssertTrue(CalendarShareRules.mayHaveCopy(CalendarShareEntry(lastSentAtMs: 1)))
        XCTAssertTrue(CalendarShareRules.mayHaveCopy(CalendarShareEntry(enabled: true, calendarIDs: ["a"])))
    }

    /// A 429 keeps the server's wait, and the next pass waits it out.
    func testATooSoonWaitsWhatTheServerSaid() async {
        let rig = Rig()
        rig.ticked(house)
        rig.putError.set(.rateLimited(Rig.failure(429), waitMs: 30_000))
        let sync = rig.sync()
        let first = await sync.run(server: house)
        XCTAssertEqual(.retry(afterMs: 30_000), first)
        rig.putError.set(nil)
        let second = await sync.run(server: house)
        XCTAssertEqual(.retry(afterMs: 30_000), second)
        XCTAssertEqual(["PUT \(house)"], rig.calls.current, "not sent again inside the wait")
    }

    /// A pending delete that could not get through stays pending; a 404
    /// (a server too old for the route) means there is nothing to delete.
    func testADeleteIsKeptUntilItAnswers() async {
        let rig = Rig()
        rig.store.update(house) { CalendarShareRules.turnOff($0) }
        rig.deleteError.set(.unreachable("offline"))
        let sync = rig.sync()
        let offline = await sync.run(server: house)
        XCTAssertEqual(.retry(afterMs: nil), offline)
        XCTAssertEqual(true, rig.store.get(house)?.pendingDelete)
        rig.deleteError.set(.httpError(Rig.failure(404)))
        let tooOld = await sync.run(server: house)
        XCTAssertEqual(.done, tooOld)
        XCTAssertEqual(false, rig.store.get(house)?.pendingDelete)
    }

    /// Read-after-await: a pass for one house writes only that house's
    /// entry, even when another house's entry changes meanwhile.
    func testAPassWritesOnlyItsOwnServersEntry() async {
        let rig = Rig()
        rig.ticked(house)
        rig.ticked(other)
        _ = await rig.sync().run(server: house)
        XCTAssertNotNil(rig.store.get(house)?.lastSentAtMs)
        XCTAssertNil(rig.store.get(other)?.lastSentAtMs)
        XCTAssertEqual(["PUT \(house)", "PUT done"], rig.calls.current)
    }

    /// A shared screen (or a room) sends nothing, but may still delete.
    func testAPhoneThatMayNotSendStillDeletes() async {
        let rig = Rig()
        rig.ticked(house)
        let sync = CalendarShareSync(
            store: rig.store, calendars: { nil }, events: { _, _ in nil },
            put: { _, _ in XCTFail("no send") }, delete: { _ in },
            phoneName: { nil }, maySend: { _ in false }
        )
        let out = await sync.run(server: house)
        XCTAssertEqual(.done, out)
        XCTAssertEqual(true, rig.store.get(house)?.enabled, "not sent, and not turned off either")
    }
}
