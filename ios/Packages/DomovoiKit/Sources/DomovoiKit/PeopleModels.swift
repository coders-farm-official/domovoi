import Foundation

/// The wire types the People screen reads, ported from
/// `ui/screens/people/PeopleModels.kt` and checked field for field against
/// `web/backend/schemas.py` in the same tree.
///
/// Every field is optional-with-a-default, exactly as the Kotlin's
/// `@Serializable` data classes are: the backend grows columns, an older
/// house leaves some out, and **one row that fails to decode must not cost
/// the whole roster**. That is why ``Person/createdAt`` is a `String?` here
/// although `schemas.py:295` makes it non-optional — the same judgement
/// ``FileLibrary`` records.
///
/// Names are prefixed `Person`/`People` per NIGHT-RULES §2: `Session`,
/// `Memory`, `Favorite` and `DenylistEntry` are all bare words that another
/// screen ported tonight would collide with.

// MARK: - a person

/// One enrolled person. `schemas.py:295`'s `Person`.
public struct Person: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int
    public let name: String
    /// **Decode-only** — nothing writes it. Non-optional on the server
    /// (`schemas.py:297`) and optional here on purpose; see this file's
    /// heading.
    public let createdAt: String?
    public let lastSeenAt: String?
    public let notes: String?
    public let voiceProfileCount: Int

    /// `"low"`, `"medium"` or `"high"`, **recomputed server-side** from
    /// `last_seen_at` against `voice_profile_soft_tier_sec` /
    /// `voice_profile_hard_tier_sec` (`people.py:772`). The phone never
    /// computes it, which is why there is no rule for it in
    /// `PeopleRules.swift`.
    public let presenceTier: String?

    /// How many **other** people share this name, case and spacing aside —
    /// counted in SQL (`people.py:101-103`), not here.
    public let namesakes: Int

    public init(
        id: Int = 0,
        name: String = "",
        createdAt: String? = nil,
        lastSeenAt: String? = nil,
        notes: String? = nil,
        voiceProfileCount: Int = 0,
        presenceTier: String? = nil,
        namesakes: Int = 0
    ) {
        self.id = id
        self.name = name
        self.createdAt = createdAt
        self.lastSeenAt = lastSeenAt
        self.notes = notes
        self.voiceProfileCount = voiceProfileCount
        self.presenceTier = presenceTier
        self.namesakes = namesakes
    }

    private enum CodingKeys: String, CodingKey {
        case id, name, notes, namesakes
        case createdAt = "created_at"
        case lastSeenAt = "last_seen_at"
        case voiceProfileCount = "voice_profile_count"
        case presenceTier = "presence_tier"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int.self, forKey: .id) ?? 0
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
        lastSeenAt = try values.decodeIfPresent(String.self, forKey: .lastSeenAt)
        notes = try values.decodeIfPresent(String.self, forKey: .notes)
        voiceProfileCount = try values.decodeIfPresent(Int.self, forKey: .voiceProfileCount) ?? 0
        presenceTier = try values.decodeIfPresent(String.self, forKey: .presenceTier)
        namesakes = try values.decodeIfPresent(Int.self, forKey: .namesakes) ?? 0
    }
}

// MARK: - a session, and a turn in one

/// One conversation session. `schemas.py:403`'s `Session`.
public struct PersonSession: Equatable, Sendable, Decodable, Identifiable {

    public let id: String
    public let roomID: String?
    public let startedAt: String?
    public let lastActivity: String?
    public let personID: Int?
    public let intentCount: Int

    public init(
        id: String = "",
        roomID: String? = nil,
        startedAt: String? = nil,
        lastActivity: String? = nil,
        personID: Int? = nil,
        intentCount: Int = 0
    ) {
        self.id = id
        self.roomID = roomID
        self.startedAt = startedAt
        self.lastActivity = lastActivity
        self.personID = personID
        self.intentCount = intentCount
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case roomID = "room_id"
        case startedAt = "started_at"
        case lastActivity = "last_activity"
        case personID = "person_id"
        case intentCount = "intent_count"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        roomID = try values.decodeIfPresent(String.self, forKey: .roomID)
        startedAt = try values.decodeIfPresent(String.self, forKey: .startedAt)
        lastActivity = try values.decodeIfPresent(String.self, forKey: .lastActivity)
        personID = try values.decodeIfPresent(Int.self, forKey: .personID)
        intentCount = try values.decodeIfPresent(Int.self, forKey: .intentCount) ?? 0
    }

    /// `last_activity − started_at` in seconds, or nil when either end is
    /// missing or unreadable.
    ///
    /// Android computes `Duration.between(a, b).seconds` and falls back to
    /// **`0.0`** when either parse fails (`PersonTabs.kt:484-488`), so the
    /// row reads `0:00` for a session whose timestamps the server did not
    /// send. Nil here instead, and ``fmtDur(_:)`` turns nil into an em dash:
    /// a duration that was not measured is not a duration of zero, and `0:00`
    /// beside a session with two turns in it is the sort of reading nobody
    /// can explain. Logged in `overnight/people/DECISIONS.md`.
    public var durationSec: Double? {
        guard
            let from = isoMs(startedAt),
            let to = isoMs(lastActivity)
        else { return nil }
        return Double(to - from) / 1000
    }

    /// `s.id.take(8)`, or an em dash for a session the server named nothing
    /// (`PersonTabs.kt:511`'s `ifEmpty { "—" }`).
    public var shortID: String {
        id.isEmpty ? "—" : String(id.prefix(8))
    }
}

/// One row of `conversation_log`. `schemas.py:412`'s `ConversationTurn`.
public struct PersonTurn: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int
    public let sessionID: String?
    public let at: String?
    public let roomID: String?
    public let userText: String?
    public let assistantText: String?
    public let matchedHandler: String?
    public let matchedPath: String?
    public let utteranceTrigger: String?

    public init(
        id: Int = 0,
        sessionID: String? = nil,
        at: String? = nil,
        roomID: String? = nil,
        userText: String? = nil,
        assistantText: String? = nil,
        matchedHandler: String? = nil,
        matchedPath: String? = nil,
        utteranceTrigger: String? = nil
    ) {
        self.id = id
        self.sessionID = sessionID
        self.at = at
        self.roomID = roomID
        self.userText = userText
        self.assistantText = assistantText
        self.matchedHandler = matchedHandler
        self.matchedPath = matchedPath
        self.utteranceTrigger = utteranceTrigger
    }

    private enum CodingKeys: String, CodingKey {
        case id, at
        case sessionID = "session_id"
        case roomID = "room_id"
        case userText = "user_text"
        case assistantText = "assistant_text"
        case matchedHandler = "matched_handler"
        case matchedPath = "matched_path"
        case utteranceTrigger = "utterance_trigger"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int.self, forKey: .id) ?? 0
        sessionID = try values.decodeIfPresent(String.self, forKey: .sessionID)
        at = try values.decodeIfPresent(String.self, forKey: .at)
        roomID = try values.decodeIfPresent(String.self, forKey: .roomID)
        userText = try values.decodeIfPresent(String.self, forKey: .userText)
        assistantText = try values.decodeIfPresent(String.self, forKey: .assistantText)
        matchedHandler = try values.decodeIfPresent(String.self, forKey: .matchedHandler)
        matchedPath = try values.decodeIfPresent(String.self, forKey: .matchedPath)
        utteranceTrigger = try values.decodeIfPresent(String.self, forKey: .utteranceTrigger)
    }

    /// The handler pill's word: the handler that matched, or `qa`
    /// (`PersonTabs.kt:571`). The pill is in the "ok" tone only when a
    /// handler really matched, which is ``matchedAHandler``.
    public var handlerLabel: String { matchedHandler ?? PeopleStrings.qaHandler }

    public var matchedAHandler: Bool { matchedHandler != nil }
}

// MARK: - a memory

/// `active` / `pending` / `rejected`, over the server's string.
///
/// A `RawRepresentable` rather than a plain `enum` with cases, so a status a
/// future server invents decodes into ``other(_:)`` instead of failing the
/// row. Android holds the raw string and compares it three times
/// (`PersonTabs.kt:82-84`), which has the same effect by accident; this has
/// it on purpose and in one place.
public enum PersonMemoryStatus: Equatable, Sendable, RawRepresentable {

    case active
    case pending
    case rejected
    /// Anything the server says that this app has no section for. Such a row
    /// is listed nowhere — see ``PersonDetailModel/memoriesWithStatus(_:)`` — which is
    /// Android's behaviour too: its three filters would all miss it.
    case other(String)

    public typealias RawValue = String

    public init(rawValue: String) {
        switch rawValue {
        case "active": self = .active
        case "pending": self = .pending
        case "rejected": self = .rejected
        default: self = .other(rawValue)
        }
    }

    public var rawValue: String {
        switch self {
        case .active: return "active"
        case .pending: return "pending"
        case .rejected: return "rejected"
        case .other(let raw): return raw
        }
    }
}

/// One remembered fact. `schemas.py:351`'s `Memory`.
public struct PersonMemory: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int
    public let personID: Int
    public let body: String
    public let topic: String?
    /// `"explicit"`, `"implicit"` or `"manual"` — what put it there. A
    /// phone's own add is `"manual"` (`people.py:478`).
    public let source: String?
    public let status: PersonMemoryStatus?
    public let createdAt: String?

    public init(
        id: Int = 0,
        personID: Int = 0,
        body: String = "",
        topic: String? = nil,
        source: String? = nil,
        status: PersonMemoryStatus? = nil,
        createdAt: String? = nil
    ) {
        self.id = id
        self.personID = personID
        self.body = body
        self.topic = topic
        self.source = source
        self.status = status
        self.createdAt = createdAt
    }

    private enum CodingKeys: String, CodingKey {
        case id, body, topic, source, status
        case personID = "person_id"
        case createdAt = "created_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int.self, forKey: .id) ?? 0
        personID = try values.decodeIfPresent(Int.self, forKey: .personID) ?? 0
        body = try values.decodeIfPresent(String.self, forKey: .body) ?? ""
        topic = try values.decodeIfPresent(String.self, forKey: .topic)
        source = try values.decodeIfPresent(String.self, forKey: .source)
        status = (try values.decodeIfPresent(String.self, forKey: .status))
            .map(PersonMemoryStatus.init(rawValue:))
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
    }

    /// `explicit · active · food · 3d ago` — the row's meta line
    /// (`PersonTabs.kt:390`), which joins whichever of source / status /
    /// topic the server sent and then appends the time **unconditionally**.
    ///
    /// The Kotlin appends `" · " + relTime(...)` outside the `joinToString`,
    /// so a memory with none of the three reads `" · 3d ago"` with a leading
    /// separator. Ported with the leading separator dropped, which is the
    /// only visible difference and is not a sentence, so it is not an
    /// IOS-ONLY string.
    public func metaLine(nowMs: Int64) -> String {
        let parts = [source, status?.rawValue, topic].compactMap { $0 }
            + [relTime(createdAt, nowMs: nowMs)]
        return parts.joined(separator: " · ")
    }
}

// MARK: - a favourite

/// One `kind = value` favourite. `schemas.py:372`'s `Favorite`.
public struct PersonFavorite: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int
    public let personID: Int
    public let kind: String
    public let value: String
    public let rank: Int

    public init(id: Int = 0, personID: Int = 0, kind: String = "", value: String = "", rank: Int = 0) {
        self.id = id
        self.personID = personID
        self.kind = kind
        self.value = value
        self.rank = rank
    }

    private enum CodingKeys: String, CodingKey {
        case id, kind, value, rank
        case personID = "person_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int.self, forKey: .id) ?? 0
        personID = try values.decodeIfPresent(Int.self, forKey: .personID) ?? 0
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? ""
        value = try values.decodeIfPresent(String.self, forKey: .value) ?? ""
        rank = try values.decodeIfPresent(Int.self, forKey: .rank) ?? 0
    }
}

/// The favourites of one kind, in the order the server sent them.
///
/// `favorites.groupBy { it.kind }` (`PersonTabs.kt:85`) — a Kotlin
/// `LinkedHashMap`, so the **first appearance** of each kind decides the
/// group order and the rows inside a group keep the server's `kind, rank`
/// ordering (`people.py:573`). A Swift `Dictionary(grouping:)` would lose
/// both, which is the reason this is a list of values and not a dictionary.
public struct PersonFavoriteGroup: Equatable, Sendable, Identifiable {
    public let kind: String
    public let favorites: [PersonFavorite]
    public var id: String { kind }

    public init(kind: String, favorites: [PersonFavorite]) {
        self.kind = kind
        self.favorites = favorites
    }
}

// MARK: - the denylist

/// One opted-out voice. `schemas.py:394`'s `DenylistEntry`, prefixed because
/// `DenylistEntry` is a bare word.
///
/// Decoded in full although **only the count is ever shown**: the view's
/// whole claim is that nothing identifying is kept, and the rows carry an id,
/// a timestamp and a note (`PeopleScreen.kt:307-367`).
public struct PeopleDenylistEntry: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int
    public let denylistedAt: String?
    public let notes: String?

    public init(id: Int = 0, denylistedAt: String? = nil, notes: String? = nil) {
        self.id = id
        self.denylistedAt = denylistedAt
        self.notes = notes
    }

    private enum CodingKeys: String, CodingKey {
        case id, notes
        case denylistedAt = "denylisted_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int.self, forKey: .id) ?? 0
        denylistedAt = try values.decodeIfPresent(String.self, forKey: .denylistedAt)
        notes = try values.decodeIfPresent(String.self, forKey: .notes)
    }
}

// MARK: - preferences, which are not a Decodable shape at all

/// One row of the preferences table: a key and the value as text.
///
/// `GET /api/people/{id}/preferences` answers the `people.preferences` JSONB
/// column **as a bare object of arbitrary JSON** — `dict(result) if result
/// else {}` (`people.py:688`), no wrapper, no schema. There is no
/// `JSONValue`/`AnyCodable` in this package, so the route is read with
/// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
/// and `JSONSerialization` and flattened into these.
///
/// Written down rather than quietly handled because "a wrapper where the
/// server sends a bare value" is the trap that cost Home four sections
/// ([[ios-stub-house]]): the shape is checked against the route's own code,
/// and ``PeoplePreferences/read(_:)`` has a test per JSON type.
public struct PersonPreference: Equatable, Sendable, Identifiable {
    public let key: String
    /// ``prettyPersonPref(_:)`` of the value.
    public let display: String
    public var id: String { key }

    public init(key: String, display: String) {
        self.key = key
        self.display = display
    }
}

/// Reading that bare object, and nothing else.
public enum PeoplePreferences {

    /// The object's keys and rendered values, **sorted by key**, or nil when
    /// the body is not a JSON object at all.
    ///
    /// Nil rather than an empty list for a body that is an array or a number:
    /// "the house has no preferences for them" and "this is not the shape the
    /// route documents" are different facts, and the model turns the second
    /// into a failed read rather than into an innocent empty section.
    ///
    /// **Sorted**, where Kotlin's `JsonObject` is a `LinkedHashMap` and keeps
    /// the server's own order (`PersonTabs.kt:325`). `JSONSerialization`
    /// hands back a `Dictionary`, whose order is not even stable between two
    /// runs of the same process, so an order had to be chosen; alphabetical
    /// is the one a reader can predict. Logged in
    /// `overnight/people/DECISIONS.md`.
    public static func read(_ data: Data) -> [PersonPreference]? {
        guard
            let any = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]),
            let object = any as? [String: Any]
        else { return nil }
        return object.keys.sorted().map {
            PersonPreference(key: $0, display: prettyPersonPref(object[$0] ?? NSNull()))
        }
    }
}

/// `prettyPref` (`PeopleModels.kt:284`): a primitive's own text, anything
/// else its raw JSON.
///
/// Kotlin's `JsonPrimitive.content` is the token's text **unquoted** — a
/// string's characters, a number's digits, `true`, `null` — and
/// `JsonElement.toString()` for an array or an object is compact JSON. Both
/// halves are reproduced here, over what `JSONSerialization` hands back.
///
/// The boolean branch is first and uses `CFBooleanGetTypeID`, not `as? Bool`:
/// `JSONSerialization` returns `kCFBooleanTrue` for `true`, and an `NSNumber`
/// bridged through `as? Bool` answers `true` for the number `1` as well —
/// the classic Foundation trap, which would print a wake volume of 1 as
/// `true`.
public func prettyPersonPref(_ value: Any) -> String {
    if value is NSNull { return "null" }
    if let text = value as? String { return text }
    if let number = value as? NSNumber {
        if CFGetTypeID(number as CFTypeRef) == CFBooleanGetTypeID() {
            return number.boolValue ? "true" : "false"
        }
        let double = number.doubleValue
        // An integral JSON number prints without a `.0`, which is what the
        // Kotlin's `content` gives for `3`. Bounded because `Int64(1e30)`
        // traps.
        if double == double.rounded(), abs(double) < 1e15 { return String(Int64(double)) }
        return String(double)
    }
    guard
        let json = try? JSONSerialization.data(
            withJSONObject: value,
            options: [.sortedKeys, .fragmentsAllowed]
        )
    else { return String(describing: value) }
    return String(decoding: json, as: UTF8.self)
}

// MARK: - the routes

/// Every path this screen asks for, in one place, with the auth tier the
/// server really puts on it.
///
/// The tiers are the whole story of this port and they are written here
/// rather than in a comment on a view, because what is **missing** from this
/// enum is the point: `PATCH /api/people/{id}` (rename) and `DELETE
/// /api/people/{id}` (forget) are `require_admin_mutation`
/// (`people.py:145`, `:355`), which is Bearer-only
/// (`admin_auth.py:1288-1305`). This app holds a household device token and
/// never a Bearer, so both would 401 every single time — so there is no
/// control for them and **no path constant for them either**. A constant with
/// no caller is the `TokenVault` mistake ([[reasoning-that-hides-a-bug]]);
/// one that a later fixer could reach for is worse.
public enum PeoplePath {

    /// `people.py:87` — `require_device_read`. No `limit`: the route returns
    /// every row, which is why the roster is an ``ApiRead`` and not a
    /// ``Pager``.
    public static let roster = "/api/people"

    /// `denylist.py:22` — **no dependency at all**, so this one answers even
    /// an unpaired phone.
    public static let denylist = "/api/denylist"

    /// `people.py:204`. `limit` is 1–200 on the server; Android asks for 50
    /// (`PeopleModels.kt:247`).
    public static func sessions(_ personID: Int, limit: Int = 50) -> String {
        "/api/people/\(personID)/sessions?limit=\(limit)"
    }

    /// `people.py:230`. `limit` is 1–500; Android asks for 200
    /// (`PeopleModels.kt:250`) — and that cap is why a session's turns can be
    /// missing from the sessions tab, which says so rather than looking empty.
    public static func conversations(_ personID: Int, limit: Int = 200) -> String {
        "/api/people/\(personID)/conversations?limit=\(limit)"
    }

    /// `people.py:409` (read) / `:461` (add) — the add is `require_device`,
    /// which **a paired phone passes**.
    public static func memories(_ personID: Int) -> String {
        "/api/people/\(personID)/memories"
    }

    /// `people.py:498` (status) / `:552` (delete), both `require_device`.
    public static func memory(_ personID: Int, _ memoryID: Int) -> String {
        "/api/people/\(personID)/memories/\(memoryID)"
    }

    /// `people.py:573` (read) / `:617` (add, upserting on
    /// `(person, kind, value)`).
    public static func favorites(_ personID: Int) -> String {
        "/api/people/\(personID)/favorites"
    }

    /// `people.py:657` — `204`, no body.
    public static func favorite(_ personID: Int, _ favoriteID: Int) -> String {
        "/api/people/\(personID)/favorites/\(favoriteID)"
    }

    /// `people.py:679`. A **bare JSON object**; see ``PeoplePreferences``.
    public static func preferences(_ personID: Int) -> String {
        "/api/people/\(personID)/preferences"
    }

    // ---- the bodies --------------------------------------------------------

    /// `{"body": …}` (`PersonTabs.kt:94`).
    public static func memoryBody(_ body: String) -> Data {
        json(["body": body])
    }

    /// `{"status": …}` (`PersonTabs.kt:109`).
    public static func statusBody(_ status: PersonMemoryStatus) -> Data {
        json(["status": status.rawValue])
    }

    /// `{"kind": …, "value": …, "rank": 0}` (`PersonTabs.kt:134`). The kind is
    /// lower-cased by the caller, as Android lower-cases it (`:127`).
    public static func favoriteBody(kind: String, value: String) -> Data {
        json(["kind": kind, "value": value, "rank": 0])
    }

    /// One encoder for the four bodies, with sorted keys so a test can assert
    /// the bytes rather than parse them back.
    private static func json(_ fields: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys]))
            ?? Data("{}".utf8)
    }
}
