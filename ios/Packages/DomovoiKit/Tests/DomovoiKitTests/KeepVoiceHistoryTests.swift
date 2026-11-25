import Foundation
import XCTest
@testable import DomovoiKit

/// Settings → Privacy → Keep voice history (``KeepVoiceHistory``), case for
/// case with Android's `KeepVoiceHistoryTest`: the value from
/// `GET /api/voice-history`, and the dashboard link only for a server whose
/// dashboard has the page (1.5.0 and later).
final class KeepVoiceHistoryTests: XCTestCase {

    private let body = """
        {"keep":"90","keep_label":"90 days","keep_days":90,
         "choices":[{"value":"30","label":"30 days"},{"value":"90","label":"90 days"},
                    {"value":"365","label":"1 year"},{"value":"forever","label":"Forever"}],
         "turns":4120,"oldest_at":"2026-08-03T10:00:00Z","delete_all":null,"last_cleanup":null}
        """

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    func testTheValueIsTheServersWords() {
        XCTAssertEqual("90 days", KeepVoiceHistory.label(Data(body.utf8)))
        XCTAssertEqual("1 year", KeepVoiceHistory.label(Data(#"{"keep":"365","choices":[{"value":"365","label":"1 year"}]}"#.utf8)))
        XCTAssertEqual("forever", KeepVoiceHistory.label(Data(#"{"keep":"forever"}"#.utf8)))
        XCTAssertNil(KeepVoiceHistory.label(Data(#"{"turns":3}"#.utf8)))
        XCTAssertNil(KeepVoiceHistory.label(Data("[]".utf8)))
        XCTAssertNil(KeepVoiceHistory.label(Data("<html>".utf8)))
        XCTAssertNil(KeepVoiceHistory.label(nil))
        XCTAssertEqual("Keep voice history: 90 days", KeepVoiceHistory.valueLine("90 days"))
    }

    func testOnlyA150DashboardHasThePage() {
        for label in ["1.5.0", "1.5.0 + 14 (abc1234)", "1.5.2", "1.10.0", "2.0.0"] {
            XCTAssertTrue(KeepVoiceHistory.dashboardHasPage(label), label)
        }
        for label in ["1.4.1", "1.4.1 + 128 (3a606cd)", "0.9.0 (abc1234)", "abc1234", "", "1.45", "١.٥.٠"] {
            XCTAssertFalse(KeepVoiceHistory.dashboardHasPage(label), label)
        }
        XCTAssertFalse(KeepVoiceHistory.dashboardHasPage(nil))
        XCTAssertEqual("keep-voice-history", KeepVoiceHistory.dashboard)
    }

    private func rig(voice: (String, Int), version: (String, Int)) -> AppContainer {
        let house = FhHouse()
        house.answer("GET", "/api/voice-history", voice.0, status: voice.1)
        house.answer("GET", "/api/config/version", version.0, status: version.1)
        return fhContainer(house, server: "http://192.168.1.20:6369").0
    }

    func testANewServerGetsTheValueAndTheLink() async throws {
        let c = rig(voice: (body, 200), version: (#"{"version":"1.5.0","sha":"x"}"#, 200))
        let read = try await KeepVoiceHistory.read(c.client)
        XCTAssertEqual(KeepVoiceHistory.Read(label: "90 days", linkable: true), read)
        XCTAssertFalse(c.client.pairingRequired)
    }

    func testAnOlderServerGetsTheValueOnly() async throws {
        var read = try await KeepVoiceHistory.read(rig(voice: (body, 200), version: (#"{"version":"1.4.1"}"#, 200)).client)
        XCTAssertEqual(KeepVoiceHistory.Read(label: "90 days", linkable: false), read)
        read = try await KeepVoiceHistory.read(rig(voice: (body, 200), version: (#"{"detail":"no"}"#, 401)).client)
        XCTAssertEqual(KeepVoiceHistory.Read(label: "90 days", linkable: false), read)
    }

    func testARefusalOrNoRouteIsNoValueAndNoAnswerThrows() async throws {
        let v = (#"{"version":"1.5.0"}"#, 200)
        let refused = rig(voice: (#"{"detail":"X-Device-Token required"}"#, 401), version: v)
        let r1 = try await KeepVoiceHistory.read(refused.client)
        XCTAssertNil(r1.label)
        XCTAssertFalse(refused.client.pairingRequired, "untracked: a refusal never raises pairing")
        let r2 = try await KeepVoiceHistory.read(rig(voice: (#"{"detail":"Not Found"}"#, 404), version: v).client)
        XCTAssertNil(r2.label)
        let r3 = try await KeepVoiceHistory.read(rig(voice: ("<html>not the dashboard</html>", 200), version: v).client)
        XCTAssertNil(r3.label)
        do {
            _ = try await KeepVoiceHistory.read(rig(voice: ("core unreachable", 502), version: v).client)
            XCTFail("a 502 is no answer")
        } catch {
            XCTAssertEqual(502, (error as? DomovoiError)?.failure?.status)
        }
    }
}
