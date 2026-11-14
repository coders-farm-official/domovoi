import Foundation
import XCTest
@testable import DomovoiKit

/// The room-mode guards (Android's `RoomGuardsTest`, the security review's
/// F8): mute closes the microphone, the engine reads it only through the
/// mute-aware ``RoomMic``, and a drop-in needs an echo canceller. The lock
/// and start rules are R3's (`RoomRulesTests`). Where Android reads the
/// compiled class to pin that an entry point calls the tested rule, these
/// read the engine's source.
final class RoomGuardsTests: XCTestCase {

    private final class FakeSource: RoomPCMSource, @unchecked Sendable {
        var started = 0
        var stopped = 0
        func start(call: Bool) throws { started += 1 }
        func read(into frame: inout [Int16]) -> Int {
            for i in frame.indices { frame[i] = 200 }
            return frame.count
        }
        func stop() { stopped += 1 }
    }

    private final class Flag: @unchecked Sendable { var on = false }

    func testMuteClosesTheMicrophoneAtTheNextReadInWhateverState() throws {
        let source = FakeSource()
        let muted = Flag()
        var events: [Bool] = []
        let mic = RoomMic(source: { source }, muted: { muted.on }, onOpenChanged: { events.append($0) })
        try mic.open(call: false)
        XCTAssertTrue(mic.isOpen)
        var frame = [Int16](repeating: 0, count: RoomAudioFrame.samples)
        XCTAssertEqual(frame.count, mic.read(&frame))
        muted.on = true
        XCTAssertEqual(RoomMic.mutedRead, mic.read(&frame))
        XCTAssertFalse(mic.isOpen, "the microphone is closed")
        XCTAssertEqual(1, source.stopped)
        XCTAssertEqual([true, false], events)
        try mic.open(call: false)
        XCTAssertFalse(mic.isOpen, "it does not open while muted")
        XCTAssertEqual(1, source.started)
        XCTAssertEqual(RoomMic.mutedRead, mic.read(&frame))
        muted.on = false
        try mic.open(call: false)
        XCTAssertTrue(mic.isOpen)
        XCTAssertEqual([true, false, true], events)
    }

    func testTwoSecondsOfExactZerosIsABlockedMicAndOneRealSampleClearsIt() throws {
        let source = RoomFeedPCMSource(samples: [0], paced: false)
        let mic = RoomMic(source: { source })
        try mic.open(call: false)
        var frame = [Int16](repeating: 0, count: RoomAudioFrame.samples)
        for _ in 0..<(RoomMic.blockedAfterFrames - 1) { _ = mic.read(&frame) }
        XCTAssertFalse(mic.blocked)
        _ = mic.read(&frame)
        XCTAssertTrue(mic.blocked)
        let live = RoomFeedPCMSource(paced: false)
        let mic2 = RoomMic(source: { live })
        try mic2.open(call: true)
        XCTAssertEqual(true, mic2.openAs)
        _ = mic2.read(&frame)
        XCTAssertFalse(mic2.blocked)
    }

    func testAFailedOpenKeepsWhyIOSRefused() {
        final class Refusing: RoomPCMSource {
            func start(call: Bool) throws { throw RoomMicFailure(stop: .interrupted, note: "a call") }
            func read(into frame: inout [Int16]) -> Int { -1 }
            func stop() {}
        }
        let mic = RoomMic(source: { Refusing() })
        XCTAssertThrowsError(try mic.open(call: false))
        XCTAssertEqual(.interrupted, mic.lastStop)
        XCTAssertFalse(mic.isOpen)
    }

    private func engineSources() throws -> [String: String] {
        let folder = RoomTestKit.repo.appendingPathComponent("ios/Packages/DomovoiKit/Sources/DomovoiKit/RoomMode")
        var out: [String: String] = [:]
        for name in try FileManager.default.contentsOfDirectory(atPath: folder.path) where name.hasPrefix("RoomEngine") && name.hasSuffix(".swift") {
            out[name] = try String(contentsOf: folder.appendingPathComponent(name), encoding: .utf8)
        }
        XCTAssertNotNil(out["RoomEngine.swift"])
        XCTAssertNotNil(out["RoomEngineListen.swift"])
        return out
    }

    func testTheEngineReadsTheMicrophoneOnlyThroughTheMuteAwareRoomMic() throws {
        let sources = try engineSources()
        XCTAssertTrue(sources["RoomEngineListen.swift"]?.contains("RoomMic(") == true, "the listening thread builds its RoomMic")
        for (name, text) in sources {
            for line in text.components(separatedBy: "\n") {
                let code = RoomModeSourcesTests.code(line)
                XCTAssertFalse(code.contains(".start(call:"), "\(name) opens a source itself: \(line)")
                XCTAssertFalse(code.contains(".read(into:"), "\(name) reads a source itself: \(line)")
            }
        }
    }

    func testADropInNeedsAnEchoCancellerAndAFullDuplexCall() throws {
        XCTAssertTrue(RoomEngine.acceptsDropin(echoCanceller: true, fullDuplex: true))
        XCTAssertFalse(RoomEngine.acceptsDropin(echoCanceller: false, fullDuplex: true))
        XCTAssertFalse(RoomEngine.acceptsDropin(echoCanceller: true, fullDuplex: false))
        let connect = try engineSources()["RoomEngineConnect.swift"] ?? ""
        guard let at = connect.range(of: "func onDropinStart(") else { return XCTFail("no onDropinStart") }
        let body = connect[at.upperBound...].prefix(800)
        XCTAssertTrue(body.contains("Self.acceptsDropin("), "onDropinStart checks the rule first")
    }
}
