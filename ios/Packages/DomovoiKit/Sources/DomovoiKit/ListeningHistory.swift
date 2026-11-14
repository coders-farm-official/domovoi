import Foundation

/// People → a person: "this phone" (whose it is, "this is me") and their
/// listening history (how much, clearing it). A port of
/// `android/.../listens/ListeningHistory.kt` (a1e611b6). The words are the
/// dashboard's (web/static/people.jsx `PersonThisBrowser`,
/// `PersonListening`) with "phone" for "browser"; the rules are the server's
/// (web/backend/api/devices.py, music_listens.py), only mirrored here so the
/// app never offers a press the server would refuse.

/// `GET /api/music/listens/summary`: the setting and counts, never a song.
public struct ListensSummary: Equatable, Sendable, Decodable {
    public let keep: String
    public let keepLabel: String
    public let total: Int
    public let household: Int
    public let people: [PersonListens]

    public init(keep: String = "", keepLabel: String = "", total: Int = 0, household: Int = 0, people: [PersonListens] = []) {
        self.keep = keep
        self.keepLabel = keepLabel
        self.total = total
        self.household = household
        self.people = people
    }

    enum CodingKeys: String, CodingKey {
        case keep, total, household, people
        case keepLabel = "keep_label"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        keep = try c.decodeIfPresent(String.self, forKey: .keep) ?? ""
        keepLabel = try c.decodeIfPresent(String.self, forKey: .keepLabel) ?? ""
        total = try c.decodeIfPresent(Int.self, forKey: .total) ?? 0
        household = try c.decodeIfPresent(Int.self, forKey: .household) ?? 0
        people = try c.decodeIfPresent([PersonListens].self, forKey: .people) ?? []
    }

    public func count(for personID: Int) -> Int {
        people.first { $0.personID == personID }?.listens ?? 0
    }

    public var off: Bool { keep == "off" }

    public static let path = "/api/music/listens/summary"
}

public struct PersonListens: Equatable, Sendable, Decodable {
    public let personID: Int
    public let name: String
    public let listens: Int

    public init(personID: Int, name: String = "", listens: Int = 0) {
        self.personID = personID
        self.name = name
        self.listens = listens
    }

    enum CodingKeys: String, CodingKey {
        case name, listens
        case personID = "person_id"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        personID = try c.decodeIfPresent(Int.self, forKey: .personID) ?? 0
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        listens = try c.decodeIfPresent(Int.self, forKey: .listens) ?? 0
    }
}

/// The words of this section, `ThisPhone.kt` and `ListeningHistory.kt`
/// verbatim (curly apostrophes and all).
public enum ListeningStrings {

    public static func songs(_ n: Int) -> String { "\(n) song\(n == 1 ? "" : "s")" }

    /// The listening-history line on a person's page.
    public static func line(_ summary: ListensSummary, personID: Int, name: String) -> String {
        if summary.off {
            return "Domovoi keeps no listening history (Settings → Privacy → Listening history)."
        }
        let n = summary.count(for: personID)
        if n == 0 { return "nothing kept for \(name)" }
        let kept = summary.keepLabel == "1 year" ? "a year" : summary.keepLabel
        return "\(songs(n)) played for \(name) · kept \(kept)"
    }

    public static let thisPhoneLabel = "this phone"
    public static let listeningHistoryLabel = "listening history"
    public static let thisIsMe = "this is me"
    public static func thisIsMeDescription(_ name: String) -> String { "this is me: this phone is \(name)’s" }
    public static let notMe = "not me"
    public static func itsTheirsInstead(_ name: String) -> String { "it’s \(name)’s instead" }
    public static let openTheDashboard = "open the dashboard"
    public static func couldNotRead(_ why: String) -> String { "couldn’t read the listening history: \(why)" }
    public static let reading = "reading…"
    public static let clear = "clear"
    public static func clearDescription(_ name: String) -> String { "clear \(name)’s listening history" }
    public static let adminsOnly =
        "Delete-all and how long it is kept are an admin’s: the dashboard’s Settings → Privacy → Listening history."
    public static func clearTitle(_ name: String) -> String { "Clear \(name)’s listening history?" }
    public static let cantBeUndone = "This can’t be undone."
    public static let clearConfirm = "Clear"
    public static let cancel = "cancel"
    /// "Clear" pressed after the app moved to another server: nothing was
    /// sent. iOS's own line — Android sends the DELETE to the server the
    /// dialog was opened for and has no such case.
    public static let clearNotSentServerChanged = "not cleared: the app moved to another server, so nothing was sent"
    public static func cleared(_ name: String, _ n: Int) -> String {
        "cleared \(name)’s listening history (\(songs(n)))"
    }

    /// The confirm dialog's words.
    public static func confirmText(name: String, count: Int) -> String {
        "This deletes the \(songs(count)) played for \(name) in every room and on every phone and browser. "
            + "Up next stops learning from them. Songs played for the whole household and for other people stay."
    }
}

// ─── "This phone" ───────────────────────────────────────────────────────────

/// What the "this phone" block offers.
public enum PhoneAction: Equatable, Sendable { case thisIsMe, notMe, itsTheirsInstead }

public struct ThisPhoneBlock: Equatable, Sendable {
    public let line: String
    public let action: PhoneAction?
    public let small: String
}

/// The block on `name`'s page (`personID`) for a phone that is `owner`'s
/// (the server's answer; nil: nobody's), a `shared` screen or not, that
/// `keyHeld` a claim key there or not. `deviceCount`: the phones and
/// browsers that are `name`'s (`device_count` on `GET /api/people`).
/// `thisPhoneBlock` (`ListeningHistory.kt`).
public func thisPhoneBlock(
    personID: Int,
    name: String,
    owner: DeviceOwner?,
    shared: Bool,
    keyHeld: Bool,
    deviceCount: Int
) -> ThisPhoneBlock {
    let mine = !shared && owner?.id == personID
    let elsewhere = max(0, deviceCount - (mine ? 1 : 0))
    let line: String
    let action: PhoneAction?
    if shared {
        line = ShellStrings.sharedScreenIsNobodys
        action = nil
    } else if mine && !keyHeld {
        line = "This phone is \(name)’s, but it can’t prove it yet. Say “this is me” again to act as \(name) here."
        action = .thisIsMe
    } else if mine {
        line = "This phone is \(name)’s. What plays here counts as \(name)’s listening, and \(name) can clear it from here."
        action = .notMe
    } else if let owner {
        line = "This phone is \(owner.name)’s."
        action = .itsTheirsInstead
    } else {
        line = "Is this \(name)’s phone? Then what plays here counts as \(name)’s listening, and \(name) can clear it from here."
        action = .thisIsMe
    }
    let other = mine ? "other " : ""
    var small: String
    if elsewhere == 0 {
        small = "no \(other)phone or browser is \(name)’s"
    } else {
        let one = elsewhere == 1
        small = "\(elsewhere) \(other)phone\(one ? "" : "s") or browser\(one ? "" : "s") \(one ? "is" : "are") \(name)’s"
    }
    if !shared && !mine && elsewhere > 0 { small += " · an admin has to say yes to add this one" }
    return ThisPhoneBlock(line: line, action: action, small: small)
}

// ─── Clearing ─────────────────────────────────────────────────────────────

/// How long after "this is me" clearing stays an admin's (the server's FRESH_CLAIM).
public let freshClaimMs: Int64 = 10 * 60 * 1000

/// What pressing "clear" may do on this phone.
public enum ClearCheck: Equatable, Sendable {
    /// This phone may clear it itself: confirm, then DELETE.
    case allowed
    /// It may not: say `sentence` and `why`, and offer `offers`.
    case notHere(sentence: String, why: String, offers: [ClearOffer])
}

public enum ClearOffer: Equatable, Sendable { case thisIsMe, openDashboard }

/// Whether a claim made at `claimedAt` (ISO) is still fresh at `nowMs`; an
/// unknown or unreadable time counts as fresh (the web's rule).
public func claimIsFresh(_ claimedAt: String?, nowMs: Int64) -> Bool {
    guard let at = claimedAt.flatMap(parseClaimTime) else { return true }
    return nowMs - at < freshClaimMs
}

/// The server's `owner_claimed_at`: ISO 8601 with or without fractional
/// seconds, `Z` or an offset, a space or a `T` between date and time.
/// Microseconds (Python's `isoformat`) are cut to milliseconds first:
/// `ISO8601DateFormatter` reads at most three fraction digits.
func parseClaimTime(_ raw: String) -> Int64? {
    var s = raw.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: " ", with: "T")
    if let dot = s.firstIndex(of: ".") {
        let fraction = s[s.index(after: dot)...].prefix { $0.isNumber }
        let rest = s[s.index(after: dot)...].dropFirst(fraction.count)
        s = String(s[..<dot]) + "." + String(fraction.prefix(3)).padding(toLength: 3, withPad: "0", startingAt: 0) + rest
    }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = formatter.date(from: s) { return Int64((d.timeIntervalSince1970 * 1000).rounded()) }
    formatter.formatOptions = [.withInternetDateTime]
    if let d = formatter.date(from: s) { return Int64((d.timeIntervalSince1970 * 1000).rounded()) }
    return nil
}

/// Whether this phone may clear `name`'s listening history itself: it is
/// `name`'s (not a shared screen), holds its claim key, and said "this is
/// me" more than 10 minutes ago — the three checks the server makes. Else
/// the dashboard's sentence, the reason, and what can be done.
/// `clearCheck` (`ListeningHistory.kt`).
public func clearCheck(
    personID: Int,
    name: String,
    owner: DeviceOwner?,
    shared: Bool,
    keyHeld: Bool,
    nowMs: Int64
) -> ClearCheck {
    let mine = !shared && owner?.id == personID
    if mine, keyHeld, !claimIsFresh(owner?.claimedAt, nowMs: nowMs) { return .allowed }
    let why: String
    if shared {
        why = "This is a shared screen, so it can’t be anyone’s."
    } else if mine && !keyHeld {
        why = "This phone is \(name)’s but can’t prove it yet: say “this is me” again on this page."
    } else if mine {
        why = "This phone said it was \(name)’s less than 10 minutes ago. Until then only an admin can clear it."
    } else if let owner {
        why = "This phone is \(owner.name)’s."
    } else {
        why = "If this is \(name)’s phone, say “this is me” on this page; from 10 minutes later \(name) can clear it from here."
    }
    // "this is me" helps unless this is a shared screen, or the phone is
    // already theirs with its key (then only the 10 minutes stand between).
    var offers: [ClearOffer] = []
    if !shared && !(mine && keyHeld) { offers.append(.thisIsMe) }
    offers.append(.openDashboard)
    return .notHere(
        sentence: "Each person clears their own. Only \(name)’s own phone or browser, or an admin, can clear it.",
        why: why,
        offers: offers
    )
}

/// `DELETE /api/music/listens/people/{personID}` from this phone at
/// `server`, as its person: `X-Device-Id` and the claim key held for
/// `server`. Only after ``clearCheck(personID:name:owner:shared:keyHeld:nowMs:)``
/// said ``ClearCheck/allowed`` and the person confirmed. The number of
/// listens deleted. Throws the server's refusal (a 403 carries its sentence).
public func clearListeningHistory(
    client: DomovoiClient,
    book: any ClaimBook,
    server: String,
    deviceID: String,
    personID: Int
) async throws(DomovoiError) -> Int {
    let data = try await client.raw(
        method: "DELETE",
        path: "/api/music/listens/people/\(personID)",
        headers: claimHeaders(deviceID: deviceID, claimKey: book.claimKey(for: server)),
        onlyTo: server
    )
    let said = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    return (said?["deleted"] as? NSNumber)?.intValue ?? 0
}
