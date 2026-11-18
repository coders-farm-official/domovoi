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

    /// Security audit round 6, IOS-5: every CalDAV source is "Other
    /// account", iCloud included; nothing is "iCloud" because of a title,
    /// which any account's server can choose (Android's rule: the type only).
    func testEveryCalDAVIsOtherAccountWhateverItIsCalled() {
        XCTAssertEqual(PhoneCalendarSource.of(.calDAV), .otherAccount)
        XCTAssertEqual(PhoneCalendarSource.of(.calDAV).label, "Other account")
        for source in [PhoneCalendarSource.exchange, .otherAccount, .subscribed, .birthdays, .onThisPhone] {
            XCTAssertNotEqual("iCloud", source.label)
        }
    }

    func testLocalIsOnThisIPhone() {
        XCTAssertEqual(PhoneCalendarSource.of(.local).label, "On this iPhone")
    }

    func testExchangeIsMicrosoftOutlook() {
        XCTAssertEqual(PhoneCalendarSource.of(.exchange).label, "Microsoft / Outlook")
    }

    func testSubscribedIsSubscribed() {
        XCTAssertEqual(PhoneCalendarSource.of(.subscribed).label, "Subscribed")
    }

    func testBirthdaysIsBirthdays() {
        XCTAssertEqual(PhoneCalendarSource.of(.birthdays).label, "Birthdays")
    }

    func testUnknownAndMobileMeAreOtherAccount() {
        XCTAssertEqual(PhoneCalendarSource.of(.unknown).label, "Other account")
        XCTAssertEqual(PhoneCalendarSource.of(.mobileMe), .otherAccount)
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
        XCTAssertEqual(body(.otherAccount), body(.onThisPhone))
        let text = String(decoding: body(.otherAccount), as: UTF8.self)
        XCTAssertFalse(text.contains("Other account"))
    }

    // MARK: - the order

    func testAccountsFirstThisPhoneLastEachGroupInItsOrder() {
        let list: [PhoneCalendarInfo] = [
            cal("local-1", .onThisPhone),
            cal("birthdays", .birthdays),
            cal("gmail", .otherAccount),
            cal("local-2", .onThisPhone),
            cal("icloud", .otherAccount),
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
