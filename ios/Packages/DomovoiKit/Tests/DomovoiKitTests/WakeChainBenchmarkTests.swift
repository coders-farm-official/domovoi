import Foundation
import XCTest
@testable import DomovoiKit

/// A regression guard on the wake chain's cost per 80 ms chunk, on the build
/// Mac, with the real models (`RoomFixtures`: skipped without the folder,
/// failed when a file is missing).
///
/// What it stands in for: ROOMMODE-MAP §5's budget on the floor phone, an
/// A12 (iPhone XR/XS): **≤ 8 ms of wake-chain compute per 80 ms chunk**
/// (≤ 10 % of one core), checked for real on a device in D-PERF (the
/// iPhone 14 at ≤ 6 % of one core). This Mac number guards against a change
/// that makes the chain slower (an extra copy per chunk, a second thread, a
/// model run twice); it does not prove the phone's number.
///
/// The measure is ``OnnxRoomEars/timing``, the same running mean the kiosk's
/// DEBUG line shows in D-PERF, over 30 ms frames as the room feeds them. Three
/// passes of 10 s of audio; the best pass's mean is held to the budget, so a
/// busy build Mac (other units' tests, `nice`) does not fail it by itself.
final class WakeChainBenchmarkTests: XCTestCase {

    /// The build Mac (Apple M1 Pro) budget, ms per chunk. See
    /// ``testTheWakeChainStaysInsideItsBudgetPerChunk()`` for how it was set.
    static let macBudgetMs = 3.0
    /// ROOMMODE-MAP §5, the A12's budget, which the Mac budget stands in for.
    static let a12BudgetMs = 8.0

    func testTheWakeChainStaysInsideItsBudgetPerChunk() throws {
        let root = try RoomFixtures.require()
        let loaded = try OnnxRoomEarsLoader().load(RoomFixtures.files(root))
        let ears = try XCTUnwrap(loaded as? OnnxRoomEars)
        let speech = try RoomFixtures.readWav(RoomFixtures.clip(root, "hey_jarvis.wav"))
            + RoomFixtures.readWav(RoomFixtures.clip(root, "what_time.wav"))
        // 10 s of audio: the two clips, then quiet noise, repeated.
        var audio: [Int16] = []
        var seed: UInt32 = 0x0D0E
        while audio.count < 160_000 {
            audio += speech
            for _ in 0..<16_000 {
                seed = seed &* 1_103_515_245 &+ 12_345
                audio.append(Int16(Int((seed >> 16) % 201) - 100))
            }
        }
        audio = Array(audio.prefix(160_000))

        // Warm up (first runs allocate), then three measured passes.
        for start in stride(from: 0, to: 32_000 - 479, by: 480) {
            _ = try ears.wakeScore(Array(audio[start..<(start + 480)]))
        }
        var passes: [Double] = []
        for _ in 0..<3 {
            let before = ears.timing.snapshot
            for start in stride(from: 0, to: audio.count - 479, by: 480) {
                _ = try ears.wakeScore(Array(audio[start..<(start + 480)]))
            }
            let after = ears.timing.snapshot
            let chunks = after.chunks - before.chunks
            XCTAssertGreaterThanOrEqual(chunks, 120, "10 s is 125 chunks")
            let totalMs = after.meanMs * Double(after.chunks) - before.meanMs * Double(before.chunks)
            passes.append(totalMs / Double(max(chunks, 1)))
        }
        let best = passes.min() ?? .infinity
        func ms(_ value: Double) -> String { String(format: "%.3f", value) }
        let report = "wake chain on this Mac: best \(ms(best)) ms/chunk, passes \(passes.map(ms).joined(separator: "/")), "
            + "max \(ms(ears.timing.snapshot.maxMs)) ms (Mac budget \(Self.macBudgetMs), A12 budget \(Self.a12BudgetMs))"
        print(report)
        XCTAssertLessThanOrEqual(best, Self.macBudgetMs, report)
    }
}
