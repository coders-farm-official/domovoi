import Foundation
import XCTest
@testable import DomovoiKit

/// The People wire types against the bytes the server really sends.
///
/// The JSON in here is `web/backend/schemas.py`'s shapes — `Person`,
/// `Session`, `ConversationTurn`, `Memory`, `Favorite`, `DenylistEntry` — and
/// the **bare object** `GET /api/people/{id}/preferences` answers
/// (`people.py:688`). The bare-object route has a test per JSON type because
/// "a wrapper where the server sends a bare value" is the trap that cost Home
/// four sections: four of its reads were never decoded at all, and a fixture
/// that looked right was checked against nothing.
final class PeopleModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type = T.self) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - a person

    func testPersonDecodesTheServersShape() throws {
        let roster: [Person] = try decode("""
        [{"id":1,"name":"Jordan","created_at":"2026-01-02T03:04:05Z",
          "last_seen_at":"2026-11-02T10:00:00Z","notes":"prefers the kitchen satellite",
          "voice_profile_count":3,"presence_tier":"high","namesakes":0},
         {"id":3,"name":"Alex","created_at":"2026-05-05T00:00:00Z","last_seen_at":null,
          "notes":null,"voice_profile_count":2,"presence_tier":"low","namesakes":1}]
        """)
        XCTAssertEqual(roster.count, 2)
        XCTAssertEqual(roster[0].id, 1)
        XCTAssertEqual(roster[0].name, "Jordan")
        XCTAssertEqual(roster[0].notes, "prefers the kitchen satellite")
        XCTAssertEqual(roster[0].voiceProfileCount, 3)
        XCTAssertEqual(roster[0].presenceTier, "high")
        XCTAssertEqual(roster[0].namesakes, 0)
        XCTAssertEqual(roster[0].createdAt, "2026-01-02T03:04:05Z")
        XCTAssertNil(roster[1].lastSeenAt)
        XCTAssertNil(roster[1].notes)
        XCTAssertEqual(roster[1].namesakes, 1)
    }

    /// The roster is a **bare array**, not `{"people": [...]}` — asserted
    /// here rather than assumed, because the decoder is the only thing that
    /// ever checks which it is.
    func testRosterIsABareArrayAndAWrapperDoesNotDecode() {
        XCTAssertNoThrow(try decode(#"[{"id":1,"name":"a"}]"#, as: [Person].self))
        XCTAssertThrowsError(try decode(#"{"people":[{"id":1,"name":"a"}]}"#, as: [Person].self))
    }

    /// An older house that has not grown every column must not cost the
    /// whole roster — the reason every field is optional-with-a-default.
    func testAPersonWithNothingButAnIdStillDecodes() throws {
        let person: Person = try decode(#"{"id":7}"#)
        XCTAssertEqual(person.id, 7)
        XCTAssertEqual(person.name, "")
        XCTAssertEqual(person.voiceProfileCount, 0)
        XCTAssertEqual(person.namesakes, 0)
        XCTAssertNil(person.createdAt)
        XCTAssertNil(person.presenceTier)
    }

    /// `created_at` is non-optional on the server (`schemas.py:297`) and
    /// optional here on purpose: a null in an older database must not fail
    /// the row.
    func testACreatedAtOfNullDoesNotFailTheRow() throws {
        let person: Person = try decode(#"{"id":4,"name":"alex ","created_at":null}"#)
        XCTAssertEqual(person.name, "alex ")
        XCTAssertNil(person.createdAt)
    }

    // MARK: - sessions

    func testSessionDecodesAndMeasuresItsOwnSpan() throws {
        let sessions: [PersonSession] = try decode("""
        [{"id":"s-aaaa1111-2222-3333","room_id":"kitchen",
          "started_at":"2026-11-02T10:00:00Z","last_activity":"2026-11-02T10:02:00Z",
          "person_id":1,"intent_count":2}]
        """)
        let session = try XCTUnwrap(sessions.first)
        XCTAssertEqual(session.roomID, "kitchen")
        XCTAssertEqual(session.personID, 1)
        XCTAssertEqual(session.intentCount, 2)
        XCTAssertEqual(session.durationSec, 120)
        XCTAssertEqual(session.shortID, "s-aaaa11")
        XCTAssertEqual(fmtDur(session.durationSec), "2:00")
    }

    /// Android falls back to `0.0` when either timestamp is missing, and the
    /// row then reads `0:00` for a session that lasted some unknown time.
    /// Nil here, so ``fmtDur(_:)`` says em dash — a divergence, logged.
    func testASessionWithNoEndHasNoDurationRatherThanZero() throws {
        let session: PersonSession = try decode(
            #"{"id":"s-x","started_at":"2026-11-02T10:00:00Z","intent_count":1}"#
        )
        XCTAssertNil(session.durationSec)
        XCTAssertEqual(fmtDur(session.durationSec), "—")
    }

    func testASessionTheServerNamedNothingReadsAsADash() throws {
        let session: PersonSession = try decode(#"{"room_id":null}"#)
        XCTAssertEqual(session.shortID, "—")
    }

    // MARK: - turns

    func testTurnDecodesAndNamesItsHandler() throws {
        let turns: [PersonTurn] = try decode("""
        [{"id":9001,"session_id":"s-aaaa1111","at":"2026-11-02T10:01:00Z","room_id":"kitchen",
          "user_text":"what's the sourdough schedule","assistant_text":"feed it at eight",
          "matched_handler":"music","matched_path":"play/album","utterance_trigger":"wake"},
         {"id":9002,"session_id":"s-aaaa1111","at":"2026-11-02T10:01:30Z","room_id":null,
          "user_text":"why","assistant_text":null,"matched_handler":null,"matched_path":null}]
        """)
        XCTAssertEqual(turns[0].id, 9001)
        XCTAssertEqual(turns[0].sessionID, "s-aaaa1111")
        XCTAssertEqual(turns[0].matchedPath, "play/album")
        XCTAssertEqual(turns[0].utteranceTrigger, "wake")
        XCTAssertEqual(turns[0].handlerLabel, "music")
        XCTAssertTrue(turns[0].matchedAHandler)

        // A turn nothing matched is the `qa` pill, in the idle tone.
        XCTAssertEqual(turns[1].handlerLabel, "qa")
        XCTAssertFalse(turns[1].matchedAHandler)
        XCTAssertNil(turns[1].assistantText)
    }

    // MARK: - memories

    func testMemoryDecodesItsStatusAsAValue() throws {
        let memories: [PersonMemory] = try decode("""
        [{"id":1,"person_id":1,"body":"allergic to peanuts","topic":"food",
          "source":"manual","status":"active","created_at":"2026-10-30T00:00:00Z"},
         {"id":2,"person_id":1,"body":"bakes sourdough","topic":null,
          "source":"implicit","status":"pending","created_at":"2026-10-31T00:00:00Z"},
         {"id":3,"person_id":1,"body":"rides a unicycle","source":"implicit",
          "status":"rejected","created_at":"2026-10-31T00:00:00Z"}]
        """)
        XCTAssertEqual(memories.map(\.status), [.active, .pending, .rejected])
        XCTAssertEqual(memories[0].topic, "food")
        XCTAssertEqual(memories[0].source, "manual")
        XCTAssertNil(memories[1].topic)
    }

    /// A status a future server invents sorts into ``PersonMemoryStatus/other(_:)``
    /// rather than failing the decode — the whole reason the type is
    /// `RawRepresentable` over the string.
    func testAnUnknownStatusDecodesIntoOtherRatherThanThrowing() throws {
        let memory: PersonMemory = try decode(#"{"id":4,"body":"x","status":"quarantined"}"#)
        XCTAssertEqual(memory.status, .other("quarantined"))
        XCTAssertEqual(memory.status?.rawValue, "quarantined")
        // And it is in none of the three lists, which is Android's behaviour.
        XCTAssertNotEqual(memory.status, .active)
        XCTAssertNotEqual(memory.status, .pending)
        XCTAssertNotEqual(memory.status, .rejected)
    }

    func testMemoryStatusRoundTripsThroughItsRawValue() {
        for raw in ["active", "pending", "rejected", "quarantined"] {
            XCTAssertEqual(PersonMemoryStatus(rawValue: raw).rawValue, raw)
        }
    }

    /// `explicit · active · food · 3d ago` (`PersonTabs.kt:390`), and
    /// whichever of the three the server left out is simply absent.
    func testMemoryMetaLineJoinsWhatThereIs() {
        let now: Int64 = 1_762_000_000_000
        let then = Date(timeIntervalSince1970: Double(now) / 1000 - 7200)
        let iso = ISO8601DateFormatter().string(from: then)

        let full = PersonMemory(
            body: "x", topic: "food", source: "explicit", status: .active, createdAt: iso
        )
        XCTAssertEqual(full.metaLine(nowMs: now), "explicit · active · food · 2h ago")

        let bare = PersonMemory(body: "x", createdAt: iso)
        XCTAssertEqual(bare.metaLine(nowMs: now), "2h ago")

        let noTime = PersonMemory(body: "x", source: "manual", status: .pending)
        XCTAssertEqual(noTime.metaLine(nowMs: now), "manual · pending · —")
    }

    // MARK: - favourites and the denylist

    func testFavoriteDecodes() throws {
        let favorites: [PersonFavorite] = try decode("""
        [{"id":11,"person_id":1,"kind":"team","value":"Mariners","rank":0},
         {"id":12,"person_id":1,"kind":"team","value":"Sounders","rank":1},
         {"id":13,"person_id":1,"kind":"coffee","value":"oat flat white","rank":0}]
        """)
        XCTAssertEqual(favorites.map(\.kind), ["team", "team", "coffee"])
        XCTAssertEqual(favorites[1].rank, 1)
        XCTAssertEqual(favorites[2].value, "oat flat white")
    }

    func testDenylistEntryDecodesEvenThoughOnlyItsCountIsShown() throws {
        let entries: [PeopleDenylistEntry] = try decode("""
        [{"id":1,"denylisted_at":"2026-09-01T00:00:00Z","notes":"asked at the door"},
         {"id":2,"denylisted_at":"2026-10-01T00:00:00Z","notes":null}]
        """)
        XCTAssertEqual(entries.count, 2)
        XCTAssertEqual(entries[0].notes, "asked at the door")
        XCTAssertNil(entries[1].notes)
    }

    // MARK: - preferences: the bare object

    func testPreferencesReadsABareObjectOfArbitraryJSON() throws {
        let rows = try XCTUnwrap(PeoplePreferences.read(Data("""
        {"wake_volume":0.7,"news_source":"npr","tts_voice":"amy"}
        """.utf8)))
        // Sorted by key, which is this port's chosen order: see
        // ``PeoplePreferences/read(_:)``.
        XCTAssertEqual(rows.map(\.key), ["news_source", "tts_voice", "wake_volume"])
        XCTAssertEqual(rows.map(\.display), ["npr", "amy", "0.7"])
    }

    func testPreferencesReadsAnEmptyObjectAsNoRows() throws {
        let rows = try XCTUnwrap(PeoplePreferences.read(Data("{}".utf8)))
        XCTAssertTrue(rows.isEmpty)
    }

    /// A body that is not an object at all is **nil**, not an empty list:
    /// ``PersonDetailModel`` turns that into a failed read rather than into
    /// an innocent "no preferences set yet".
    func testPreferencesRefusesAShapeTheRouteDoesNotDocument() {
        XCTAssertNil(PeoplePreferences.read(Data("[]".utf8)))
        XCTAssertNil(PeoplePreferences.read(Data(#"{"preferences":{"a":1}"#.utf8)))
        XCTAssertNil(PeoplePreferences.read(Data("".utf8)))
        XCTAssertNil(PeoplePreferences.read(Data("not json at all".utf8)))
    }

    /// `prettyPref` (`PeopleModels.kt:284`), type by type. The boolean case
    /// is the one that matters: `JSONSerialization` hands back `NSNumber`
    /// for both, and `as? Bool` answers `true` for the number `1`, which
    /// would print a wake volume of 1 as `true`.
    func testPrettyPrefRendersEveryJSONType() throws {
        let rows = try XCTUnwrap(PeoplePreferences.read(Data("""
        {"a_string":"npr","b_float":0.7,"c_int":3,"d_true":true,"e_false":false,
         "f_null":null,"g_list":["x","y"],"h_object":{"k":"v"},"i_one":1}
        """.utf8)))
        let byKey = Dictionary(uniqueKeysWithValues: rows.map { ($0.key, $0.display) })
        XCTAssertEqual(byKey["a_string"], "npr")
        XCTAssertEqual(byKey["b_float"], "0.7")
        XCTAssertEqual(byKey["c_int"], "3")
        XCTAssertEqual(byKey["d_true"], "true")
        XCTAssertEqual(byKey["e_false"], "false")
        XCTAssertEqual(byKey["f_null"], "null")
        XCTAssertEqual(byKey["g_list"], #"["x","y"]"#)
        XCTAssertEqual(byKey["h_object"], #"{"k":"v"}"#)
        // The number one, not `true`.
        XCTAssertEqual(byKey["i_one"], "1")
    }

    // MARK: - the routes

    /// The paths and their `limit`s, which are the server's own bounds:
    /// sessions 1–200 (`people.py:204`) and conversations 1–500 (`:230`).
    /// Android asks for 50 and 200 (`PeopleModels.kt:247`, `:250`).
    func testThePathsAreTheRoutesAndroidAsksFor() {
        XCTAssertEqual(PeoplePath.roster, "/api/people")
        XCTAssertEqual(PeoplePath.denylist, "/api/denylist")
        XCTAssertEqual(PeoplePath.sessions(1), "/api/people/1/sessions?limit=50")
        XCTAssertEqual(PeoplePath.conversations(1), "/api/people/1/conversations?limit=200")
        XCTAssertEqual(PeoplePath.memories(2), "/api/people/2/memories")
        XCTAssertEqual(PeoplePath.memory(2, 9), "/api/people/2/memories/9")
        XCTAssertEqual(PeoplePath.favorites(2), "/api/people/2/favorites")
        XCTAssertEqual(PeoplePath.favorite(2, 11), "/api/people/2/favorites/11")
        XCTAssertEqual(PeoplePath.preferences(3), "/api/people/3/preferences")
    }

    func testTheBodiesAreTheShapesTheRoutesTake() {
        XCTAssertEqual(
            String(decoding: PeoplePath.memoryBody("allergic to shellfish"), as: UTF8.self),
            #"{"body":"allergic to shellfish"}"#
        )
        XCTAssertEqual(
            String(decoding: PeoplePath.statusBody(.active), as: UTF8.self),
            #"{"status":"active"}"#
        )
        XCTAssertEqual(
            String(decoding: PeoplePath.statusBody(.rejected), as: UTF8.self),
            #"{"status":"rejected"}"#
        )
        // Sorted keys, so the bytes can be asserted rather than parsed back.
        XCTAssertEqual(
            String(decoding: PeoplePath.favoriteBody(kind: "team", value: "Mariners"), as: UTF8.self),
            #"{"kind":"team","rank":0,"value":"Mariners"}"#
        )
    }
}
