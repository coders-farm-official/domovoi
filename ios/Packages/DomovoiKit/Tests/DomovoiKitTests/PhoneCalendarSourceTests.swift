import Foundation
import XCTest
@testable import DomovoiKit

/// Settings → Household → Phone calendars: each row says where its calendar
/// comes from, so an empty local "Calendar" does not look like the main one —
/// and never the account's own name or email (DATA_SAFETY).
final class PhoneCalendarSourceTests: XCTestCase {

    private func cal(_ id: String, account: String? = nil, _ source: PhoneCalendarSource) -> PhoneCalendarInfo {
        PhoneCalendarInfo(id: id, name: "Calendar", accountName: account, colorHex: nil, source: source)
    }

    // MARK: - the mapping, one case each

    func testCalDAVTitledICloudIsICloud() {
        XCTAssertEqual(PhoneCalendarSource.of(.calDAV, sourceTitle: "iCloud"), .iCloud)
        XCTAssertEqual(PhoneCalendarSource.of(.calDAV, sourceTitle: " icloud "), .iCloud)
        XCTAssertEqual(PhoneCalendarSource.of(.calDAV, sourceTitle: "iCloud").label, "iCloud")
    }

    func testAnyOtherCalDAVIsOtherAccount() {
        XCTAssertEqual(PhoneCalendarSource.of(.calDAV, sourceTitle: "alex@gmail.com"), .otherAccount)
        XCTAssertEqual(PhoneCalendarSource.of(.calDAV, sourceTitle: "Gmail"), .otherAccount)
        XCTAssertEqual(PhoneCalendarSource.of(.calDAV, sourceTitle: nil), .otherAccount)
        XCTAssertEqual(PhoneCalendarSource.of(.calDAV, sourceTitle: ""), .otherAccount)
        XCTAssertEqual(PhoneCalendarSource.of(.calDAV, sourceTitle: "Gmail").label, "Other account")
    }

    func testLocalIsOnThisIPhone() {
        XCTAssertEqual(PhoneCalendarSource.of(.local, sourceTitle: "Default").label, "On this iPhone")
        // The title only ever tells iCloud apart: a local source named
        // "iCloud" is still on this phone.
        XCTAssertEqual(PhoneCalendarSource.of(.local, sourceTitle: "iCloud"), .onThisPhone)
    }

    func testExchangeIsMicrosoftOutlook() {
        XCTAssertEqual(PhoneCalendarSource.of(.exchange, sourceTitle: "alex@work.example").label,
                       "Microsoft / Outlook")
    }

    func testSubscribedIsSubscribed() {
        XCTAssertEqual(PhoneCalendarSource.of(.subscribed, sourceTitle: "Subscribed Calendars").label, "Subscribed")
    }

    func testBirthdaysIsBirthdays() {
        XCTAssertEqual(PhoneCalendarSource.of(.birthdays, sourceTitle: "Other").label, "Birthdays")
    }

    func testUnknownAndMobileMeAreOtherAccount() {
        XCTAssertEqual(PhoneCalendarSource.of(.unknown, sourceTitle: nil).label, "Other account")
        XCTAssertEqual(PhoneCalendarSource.of(.unknown, sourceTitle: "iCloud"), .otherAccount)
        XCTAssertEqual(PhoneCalendarSource.of(.mobileMe, sourceTitle: "me.com"), .otherAccount)
    }

    // MARK: - what the row shows

    func testTheRowShowsTheLabelNeverTheAccount() {
        let row = CalendarShareStrings.label(cal("a", account: "alex@example.com", .otherAccount))
        XCTAssertEqual(row.name, "Calendar")
        XCTAssertEqual(row.account, "Other account")
        XCTAssertFalse((row.account ?? "").contains("example.com"))
        XCTAssertEqual(CalendarShareStrings.label(cal("b", account: "Default", .onThisPhone)).account,
                       "On this iPhone")
    }

    /// Display only: the source changes nothing the house is sent.
    func testTheSourceIsNeverSent() {
        let window = CalendarSharePayload.window(nowMs: 1_795_000_000_000)
        func body(_ source: PhoneCalendarSource) -> Data {
            let built = CalendarSharePayload.build(
                phoneName: "Phone", window: window,
                calendars: [PhoneCalendarInfo(id: "a", name: "Work", accountName: "alex@example.com",
                                              colorHex: "#33b679", source: source)],
                rows: [], picked: ["a"], nowMs: 1_795_000_000_000
            )
            return CalendarSharePayload.encode(built.body)
        }
        XCTAssertEqual(body(.iCloud), body(.onThisPhone))
        let text = String(decoding: body(.iCloud), as: UTF8.self)
        XCTAssertFalse(text.contains("iCloud"))
    }

    // MARK: - the order

    func testAccountsFirstThisPhoneLastEachGroupInItsOrder() {
        let list: [PhoneCalendarInfo] = [
            cal("local-1", .onThisPhone),
            cal("birthdays", .birthdays),
            cal("gmail", .otherAccount),
            cal("local-2", .onThisPhone),
            cal("icloud", .iCloud),
            cal("subscribed", .subscribed),
            cal("outlook", .exchange),
        ]
        XCTAssertEqual(PhoneCalendarSource.ordered(list).map(\.id),
                       ["gmail", "icloud", "outlook", "birthdays", "subscribed", "local-1", "local-2"])
    }

    func testOrderingNothingIsNothing() {
        XCTAssertEqual(PhoneCalendarSource.ordered([]), [])
    }
}
