import Foundation

// ---------------------------------------------------------------------------
// Where a phone calendar comes from — the line under its name in Settings →
// Household → Phone calendars, so an empty "Calendar" on this iPhone does not
// look like the main one (1.3.0; Android labels its rows the same way).
//
// Pure: the app's EventKit reader turns `EKSource.sourceType` into
// ``PhoneCalendarSourceKind``, and the label comes from that type alone, as
// Android's comes from the account type alone (`CalendarSource.kt`). The
// source's title is never read here: it is often an email address, and it
// is whatever the account's server chose, so a CalDAV account titled
// "iCloud" is not evidence of iCloud (security audit round 6, IOS-5).
// EventKit reports iCloud as CalDAV and has no iCloud type, so iCloud is
// "Other account" like every other CalDAV source. Only the label is shown,
// and nothing is sent (DATA_SAFETY: account names are not sent or shown).
// ---------------------------------------------------------------------------

/// `EKSourceType`, mirrored so the package needs no EventKit.
public enum PhoneCalendarSourceKind: Equatable, Sendable {
    case local
    case exchange
    case calDAV
    case mobileMe
    case subscribed
    case birthdays
    /// No source, or a type a later iOS adds (`@unknown default`).
    case unknown
}

/// The label a calendar's row carries.
public enum PhoneCalendarSource: Equatable, Sendable {
    case exchange
    case otherAccount
    case subscribed
    case birthdays
    case onThisPhone

    /// The source's type, and nothing else: every CalDAV source (iCloud
    /// among them; EventKit has no iCloud type) is "Other account".
    public static func of(_ kind: PhoneCalendarSourceKind) -> PhoneCalendarSource {
        switch kind {
        case .calDAV: return .otherAccount
        case .local: return .onThisPhone
        case .exchange: return .exchange
        case .subscribed: return .subscribed
        case .birthdays: return .birthdays
        case .mobileMe, .unknown: return .otherAccount
        }
    }

    /// The words under the calendar's name.
    public var label: String {
        switch self {
        case .exchange: return "Microsoft / Outlook"
        case .otherAccount: return "Other account"
        case .subscribed: return "Subscribed"
        case .birthdays: return "Birthdays"
        case .onThisPhone: return "On this iPhone"
        }
    }

    /// The picker's order: the signed-in accounts first,
    /// subscribed and birthdays next, "On this iPhone" last.
    public var sortGroup: Int {
        switch self {
        case .exchange, .otherAccount: return 0
        case .subscribed, .birthdays: return 1
        case .onThisPhone: return 2
        }
    }

    /// `calendars` with the groups in ``sortGroup`` order and each group in
    /// the order it came (a stable sort, whatever `sorted` guarantees).
    public static func ordered(_ calendars: [PhoneCalendarInfo]) -> [PhoneCalendarInfo] {
        calendars.enumerated()
            .sorted { (a: (offset: Int, element: PhoneCalendarInfo), b: (offset: Int, element: PhoneCalendarInfo)) -> Bool in
                let left = (a.element.source.sortGroup, a.offset)
                let right = (b.element.source.sortGroup, b.offset)
                return left < right
            }
            .map(\.element)
    }
}
