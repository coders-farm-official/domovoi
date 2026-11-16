import Foundation

// ---------------------------------------------------------------------------
// Where a phone calendar comes from — the line under its name in Settings →
// Household → Phone calendars, so an empty "Calendar" on this iPhone does not
// look like the main one (1.3.0; Android labels its rows the same way).
//
// Pure: the app's EventKit reader turns `EKSource.sourceType` into
// ``PhoneCalendarSourceKind`` and hands over the source's title, which is
// read **only** to tell iCloud apart (EventKit reports iCloud as CalDAV). The
// title — often an email address — is never shown and never sent: only the
// label is (DATA_SAFETY: account names are not sent or shown).
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
    case iCloud
    case exchange
    case otherAccount
    case subscribed
    case birthdays
    case onThisPhone

    /// The source's type, plus its title **only** to spot iCloud: iCloud is a
    /// CalDAV source whose title is "iCloud" (EventKit has no iCloud type —
    /// the common detection, there being no documented one).
    public static func of(_ kind: PhoneCalendarSourceKind, sourceTitle: String?) -> PhoneCalendarSource {
        switch kind {
        case .calDAV:
            let title: String = (sourceTitle ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return title.caseInsensitiveCompare("iCloud") == .orderedSame ? .iCloud : .otherAccount
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
        case .iCloud: return "iCloud"
        case .exchange: return "Microsoft / Outlook"
        case .otherAccount: return "Other account"
        case .subscribed: return "Subscribed"
        case .birthdays: return "Birthdays"
        case .onThisPhone: return "On this iPhone"
        }
    }

    /// The picker's order: iCloud and the other signed-in accounts first,
    /// subscribed and birthdays next, "On this iPhone" last.
    public var sortGroup: Int {
        switch self {
        case .iCloud, .exchange, .otherAccount: return 0
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
