import XCTest
@testable import DomovoiKit

/// Two payload shapes the core grew on 2026-11-09, and what this app does with
/// them: **nothing, on purpose** (the standing rule: the phone shows only what
/// Android shows, and Android changed neither).
///
/// - `/api/health` gained a `speech` block (the turn watchdog: `state` ok /
///   slow / stalled, plus `last_timeout` and `last_reply_timeout`). The web's
///   Health page derives a `speech` row and an `answers` row from it;
///   Android's `HealthChecks.kt` has neither, so neither does
///   ``healthChecks(health:rooms:plugins:acq:guard:)``. The "answers row" is
///   not a payload key at all: it is the web computing a row from
///   `speech.last_reply_timeout`.
/// - A config field gained `step`, `widget` ("slider") and `slider_labels`
///   `[left, right]`. The app reads a room's config read-only and has no
///   stepper or slider, so the keys must decode harmlessly and change nothing.
///
/// Each test says what a strict decoder (one that throws on a key it does not
/// know) would break. The identity readers (`ServerIdentity.advertisedIdentity`)
/// use `JSONSerialization` and look at `identity` only, so they have no
/// strictness to pin.
final class NewServerPayloadShapesTests: XCTestCase {

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - /api/health `speech`

    /// The shape `speech_health()` sends, inside the fields the web passes
    /// through (`docs/API_REFERENCE.md`, `GET /api/health`).
    private func healthJSON(speech: String?) -> String {
        let speechPart = speech.map { ",\"speech\":" + $0 } ?? ""
        return """
        {"status":"ok","db_reachable":true,"domovoi_reachable":true,"stt":"ok",
         "identity":{"algorithm":"ed25519","fingerprint":"SHA256:abc","public_key":"AAAA"},
         "core_alive":true\(speechPart)}
        """
    }

    private let stalled = """
    {"state":"stalled","workers":8,"stuck_workers":8,"stuck_for_s":700,
     "last_timeout":{"stage":"transcribe","at":"2026-11-09T20:15:00+00:00"},
     "last_reply_timeout":{"stage":"model","at":"2026-11-09T20:16:00+00:00"}}
    """
    private let slow = """
    {"state":"slow","workers":8,"stuck_workers":0,"stuck_for_s":0,
     "last_timeout":{"stage":"voice_id","at":"2026-11-09T20:15:00+00:00"},
     "last_reply_timeout":null}
    """
    private let quiet = """
    {"state":"ok","workers":8,"stuck_workers":0,"stuck_for_s":0,
     "last_timeout":null,"last_reply_timeout":null}
    """

    private func checks(_ health: HomeHealth) -> [HealthCheck] {
        healthChecks(
            health: health,
            rooms: [HomeRoom(roomID: "kitchen", status: "online")],
            plugins: HomePlugins(),
            acq: HomeAcquisitions(coreReachable: true),
            guard: nil
        )
    }

    /// A strict decoder throws on the unexpected `speech` key, which in the
    /// app fails the whole health read and turns every check grey.
    func testAHealthAnswerWithASpeechBlockStillDecodesToTheSameValues() throws {
        let before = try decode(HomeHealth.self, healthJSON(speech: nil))
        for speech in [quiet, slow, stalled] {
            let after = try decode(HomeHealth.self, healthJSON(speech: speech))
            XCTAssertEqual(before, after, "speech block: \(speech)")
        }
        XCTAssertEqual("ok", before.stt)
        XCTAssertEqual(true, before.dbReachable)
        XCTAssertEqual(true, before.domovoiReachable)
    }

    /// Android shows no speech or answers row, so neither may the phone — not
    /// even when the core says speech is stalled: the app renders exactly what
    /// it rendered before, and no check is new, unknown or failing.
    func testASpeechBlockAddsNoCheckAndChangesNone() throws {
        let before = checks(try decode(HomeHealth.self, healthJSON(speech: nil)))
        for speech in [quiet, slow, stalled] {
            let after = checks(try decode(HomeHealth.self, healthJSON(speech: speech)))
            XCTAssertEqual(before, after, "speech block changed the checks: \(speech)")
            XCTAssertEqual(
                ["core", "db", "stt", "rooms", "offline", "waiting", "kiosk", "plugins", "acq", "containers"],
                after.map(\.key)
            )
            XCTAssertFalse(after.contains { $0.key == "speech" || $0.key == "answers" })
            XCTAssertTrue(after.filter(\.problem).isEmpty, "a stalled speech block is not a failing check here")
        }
        XCTAssertEqual(
            [],
            attentionRows(
                health: try decode(HomeHealth.self, healthJSON(speech: stalled)),
                rooms: [HomeRoom(roomID: "kitchen", status: "online")],
                plugins: HomePlugins(), acq: nil, guard: nil
            ),
            "Home draws no row for it"
        )
    }

    /// `null` is documented (an older core); anything else is not. The block is
    /// never decoded, so none of it may fail the read.
    func testASpeechBlockOfAnyShapeIsNeverRead() throws {
        for speech in ["null", "\"stalled\"", "[1,2,3]", "42", "{\"state\":{\"nested\":true}}"] {
            let health = try decode(HomeHealth.self, healthJSON(speech: speech))
            XCTAssertEqual("ok", health.stt, "speech: \(speech)")
        }
    }

    // MARK: - config fields: step, widget, slider_labels

    private let plainFields = """
    [{"name":"wake_threshold","label":"Wake sensitivity","group":"wake","type":"float",
      "min":0.0,"max":1.0,"value":0.5},
     {"name":"gain","label":"Gain","group":"audio","type":"int","unit":"dB","min":0,"max":30,"value":12}]
    """
    private let steppedFields = """
    [{"name":"wake_threshold","label":"Wake sensitivity","group":"wake","type":"float",
      "min":0.0,"max":1.0,"step":0.05,"widget":"slider",
      "slider_labels":["more sensitive","fewer false wakes"],"value":0.5},
     {"name":"gain","label":"Gain","group":"audio","type":"int","unit":"dB","min":0,"max":30,
      "step":1,"widget":null,"slider_labels":null,"value":12}]
    """

    /// A strict field decoder throws on `step` / `widget` / `slider_labels`,
    /// and because the fields are one array the whole room config read would
    /// fail with it.
    func testConfigFieldsWithStepWidgetAndSliderLabelsDecodeAsBefore() throws {
        let before = try decode(RoomsConfigResponse.self, "{\"reported\":true,\"fields\":" + plainFields + "}")
        let after = try decode(RoomsConfigResponse.self, "{\"reported\":true,\"fields\":" + steppedFields + "}")
        XCTAssertEqual(before, after, "the new keys must change nothing the app reads")
        XCTAssertEqual(2, after.fields.count)
        XCTAssertEqual(0.0, after.fields.first?.min)
        XCTAssertEqual(1.0, after.fields.first?.max)
        XCTAssertEqual(
            before.fields.map(roomsConfigValueText),
            after.fields.map(roomsConfigValueText),
            "and what each row says"
        )
        XCTAssertEqual("12 dB", after.fields.last.map(roomsConfigValueText))
    }

    /// The Rooms settings tab is read-only: no stepper or slider for `step` to
    /// drive, and the sections the fields fall into do not move.
    func testConfigSplitIsUnchangedByTheNewKeys() throws {
        let before = try decode(RoomsConfigResponse.self, "{\"reported\":true,\"fields\":" + plainFields + "}")
        let after = try decode(RoomsConfigResponse.self, "{\"reported\":true,\"fields\":" + steppedFields + "}")
        XCTAssertEqual(
            roomsConfigSplit(before.fields).advanced.map(\.name),
            roomsConfigSplit(after.fields).advanced.map(\.name)
        )
        XCTAssertEqual(
            roomsConfigSplit(before.fields).common.map(\.name),
            roomsConfigSplit(after.fields).common.map(\.name)
        )
    }
}
