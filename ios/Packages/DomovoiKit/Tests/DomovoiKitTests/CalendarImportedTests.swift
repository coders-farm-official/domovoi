import Foundation
import XCTest
@testable import DomovoiKit

/// 1.3.0: events from linked and phone calendars, read-only — the port of
/// `CalendarImportedTest.kt` (merge `bf05916b`), plus the two refusals the
/// iPhone's model adds and the read window the review asked to pin.
///
/// Each case names the defect it fails on.
@MainActor
final class CalendarImportedTests: XCTestCase {

    private func decode(_ json: String) throws -> [CalendarEntry] {
        try JSONDecoder().decode([CalendarEntry].self, from: Data(json.utf8))
    }

    private func phoneEvent(owner: String? = "Pixel 8", name: String? = "Work") -> CalendarEntry {
        CalendarEntry(id: 1, title: "x", startsAt: "2026-11-20T15:00:00Z", source: "phone",
                      calendarName: name, calendarKind: "phone", ownerName: owner, readOnly: true)
    }

    private func linkEvent(name: String? = "School") -> CalendarEntry {
        CalendarEntry(id: 2, title: "x", startsAt: "2026-11-20T15:00:00Z", source: "link",
                      calendarName: name, calendarKind: "link", readOnly: true)
    }

    /// `theNewFieldsDecodeAndOldRowsStillDo`. Fails on a decoder that drops
    /// `read_only` (the sheet would draw edit and delete) or that needs the
    /// new keys (an older house's rows would blank the screen).
    func testTheNewFieldsDecodeAndOldRowsStillDo() throws {
        let rows = try decode("""
        [{"id":9,"title":"Dentist","starts_at":"2026-11-20T15:00:00Z","source":"phone","all_day":false,
          "calendar_id":4,"calendar_name":"Work","calendar_color":"#33b679","calendar_kind":"phone",
          "owner_name":"Pixel 8","read_only":true},
         {"id":1,"title":"x","starts_at":"2026-11-20T15:00:00Z"}]
        """)
        XCTAssertEqual(2, rows.count)
        guard let e = rows.first, let old = rows.last else { return }
        XCTAssertTrue(e.readOnly)
        XCTAssertEqual("Work", e.calendarName)
        XCTAssertEqual(4, e.calendarID)
        XCTAssertEqual("#33b679", e.calendarColor)
        XCTAssertEqual("Pixel 8", e.ownerName)
        XCTAssertFalse(old.readOnly)
        XCTAssertFalse(old.isImported)
        XCTAssertNil(old.readOnlyNote)
        XCTAssertEqual("household", old.sourceLabel)
    }

    /// `theNoteSaysWhereToChangeIt`, character for character — em dash and
    /// all. Fails if a read-only event says nothing about where it changes.
    func testTheNoteSaysWhereToChangeIt() {
        XCTAssertEqual("from Work on Pixel 8 \u{2014} change it on that phone", phoneEvent().readOnlyNote)
        XCTAssertEqual("from Work \u{2014} change it on that phone", phoneEvent(owner: nil).readOnlyNote)
        XCTAssertEqual("from a phone calendar on Pixel 8 \u{2014} change it on that phone", phoneEvent(name: " ").readOnlyNote)
        XCTAssertEqual("from School \u{2014} change it there", linkEvent().readOnlyNote)
        XCTAssertEqual("from another calendar \u{2014} change it there", linkEvent(name: nil).readOnlyNote)
        XCTAssertTrue(linkEvent().isImported)
        XCTAssertEqual("School", linkEvent().sourceLabel)
        XCTAssertEqual("linked calendar", linkEvent(name: nil).sourceLabel)
        XCTAssertEqual("phone calendar", phoneEvent(name: nil).sourceLabel)
        XCTAssertEqual("google", CalendarEntry(id: 3, source: "google").sourceLabel)
    }

    /// `theDotColourIsReadOrIgnored`.
    func testTheDotColourIsReadOrIgnored() {
        XCTAssertEqual(0xFF33_B679, CalendarEntry(calendarColor: "#33b679").dotARGB)
        XCTAssertEqual(0xFF33_B679, CalendarEntry(calendarColor: "33B679").dotARGB)
        XCTAssertNil(CalendarEntry(calendarColor: "#33b6").dotARGB)
        XCTAssertNil(CalendarEntry(calendarColor: "red").dotARGB)
        XCTAssertNil(CalendarEntry().dotARGB)
    }

    /// `anAllDayEventIsOnItsWrittenDate`: the server writes Thanksgiving as
    /// the house's midnight with the house's offset; a phone in Tokyo still
    /// puts it on the 26th, and it reads `all day`. Fails on an instant read
    /// (Tokyo would put it on the 26th at 14:00, and London on the 26th at
    /// 05:00, both drawn as clocks).
    func testAnAllDayEventIsOnItsWrittenDate() throws {
        let tokyo = try XCTUnwrap(TimeZone(identifier: "Asia/Tokyo"))
        let e = CalendarEntry(id: 1, title: "Holiday", startsAt: "2026-11-26T00:00:00-05:00",
                              endsAt: "2026-11-27T00:00:00-05:00", allDay: true)
        XCTAssertEqual(CalendarDay(year: 2026, month: 11, day: 26), calendarLocalDay(e, in: tokyo))
        XCTAssertEqual(CalendarDay(year: 2026, month: 11, day: 27).startMs(in: tokyo), calendarEndMs(e, in: tokyo))
        XCTAssertEqual("all day", calendarRange(e, in: tokyo))
        XCTAssertEqual(
            [1],
            calendarVisibleEvents(mode: .day, anchor: CalendarDay(year: 2026, month: 11, day: 26),
                                  events: [e], in: tokyo).map(\.id)
        )
    }

    // MARK: - the model refuses too

    private func model() -> CalendarModel {
        let server = "http://10.0.0.42:6369"
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return CalendarModel(container: container, nowMs: { 0 }, timeZone: { .gmt })
    }

    /// No editor and no delete question for an event from another calendar:
    /// the house answers 409 to both. Fails if the guard in
    /// ``CalendarModel/openEditor(for:)`` or ``CalendarModel/askToDelete(_:)``
    /// goes — the sheet hides the controls, but a second way in (a future
    /// swipe action, a keyboard shortcut) would open a form whose save could
    /// only fail.
    func testAnEventFromAnotherCalendarOpensNoEditorAndAsksNoDelete() {
        let m = model()
        m.openEditor(for: phoneEvent())
        XCTAssertNil(m.draft)
        m.askToDelete(linkEvent())
        XCTAssertNil(m.deleteTarget)

        let own = CalendarEntry(id: 5, title: "dinner", startsAt: "2026-11-20T18:00:00Z")
        m.openEditor(for: own)
        XCTAssertEqual(5, m.draft?.id, "the household's own still opens")
        m.askToDelete(own)
        XCTAssertEqual(5, m.deleteTarget?.id)
    }

    // MARK: - the read window (review correction 1)

    /// Every view reads a range with a start, and only the agenda leaves the
    /// end open (it is "the next 25"). Month reads its six-week grid. Fails on
    /// a read with no bounds, which the server answers with its oldest 500.
    func testEveryViewReadsABoundedRange() {
        let anchor = CalendarDay(year: 2026, month: 11, day: 14)
        for mode in [CalendarViewMode.day, .week, .month, .agenda] {
            let w = calendarWindow(mode: mode, anchor: anchor, in: .gmt)
            XCTAssertLessThanOrEqual(w.startMs, anchor.startMs(in: .gmt), "\(mode)")
            XCTAssertGreaterThan(w.startMs, 0, "\(mode) has a start")
            if mode != .agenda { XCTAssertFalse(w.isOpenEnded, "\(mode) has an end") }
        }
        let month = calendarWindow(mode: .month, anchor: anchor, in: .gmt)
        XCTAssertEqual(42 * 86_400_000, month.endMs - month.startMs, "the six-week grid")
    }
}
