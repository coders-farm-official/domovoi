import Foundation
import XCTest
@testable import DomovoiKit

/// openWakeWord's streaming detector, with fake networks (Android's
/// `WakeDetectorTest.kt`): the buffering (80 ms chunks, the 480-sample
/// context, 76-frame windows, the 16-embedding input, the five silent
/// warm-up scores) and the noise seed. The real models are
/// `RoomRealModelTests`.
final class WakeDetectorTests: XCTestCase {

    /// Records what the pipeline asks of each network.
    final class FakeNets: WakeNets {
        var melInputs: [Int] = []
        var embeddings = 0
        var wakes = 0
        var windowSizes: Set<Int> = []
        var featureSizes: Set<Int> = []
        var windowsNewest: [Float] = []
        let wakeInputFrames = 16
        private let scores: (Int) -> Float

        init(scores: @escaping (Int) -> Float = { _ in 0.9 }) {
            self.scores = scores
        }

        func melspectrogram(_ samples: [Float]) throws -> [[Float]] {
            melInputs.append(samples.count)
            let frames = Int((Double(samples.count) / 160 - 3).rounded(.up))
            let value = samples.last ?? 0
            return Array(repeating: Array(repeating: value, count: 32), count: max(frames, 0))
        }

        func embedding(_ window: [[Float]]) throws -> [Float] {
            windowSizes.insert(window.count)
            embeddings += 1
            windowsNewest.append(window.last?.first ?? .nan)
            return Array(repeating: Float(embeddings), count: 96)
        }

        func wake(_ features: [[Float]]) throws -> Float {
            featureSizes.insert(features.count)
            wakes += 1
            return scores(wakes)
        }
    }

    private func zeros(_ n: Int) -> [[Float]] {
        Array(repeating: Array(repeating: 0, count: 96), count: n)
    }

    func testEightyMillisecondChunksWithTheirContext() throws {
        let nets = FakeNets()
        let d = try WakeDetector(nets: nets, seed: zeros(41))
        // 30 ms frames, as the room's mic delivers them: 1280 samples is 2.67
        // frames, so a chunk completes on the 3rd, 6th and 8th frame.
        let frame = [Int16](repeating: 100, count: 480)
        let results = try (1...8).map { _ in try d.accept(frame) }
        XCTAssertEqual([false, false, true, false, false, true, false, true], results.map { $0 != nil })
        // The first chunk after a reset has no context; the next ones carry 480.
        XCTAssertEqual([1280, 1760, 1760], nets.melInputs)
        // One embedding and one score per chunk, on 76 frames and 16 embeddings.
        XCTAssertEqual(3, nets.embeddings)
        XCTAssertEqual(3, nets.wakes)
        XCTAssertEqual([76], nets.windowSizes)
        XCTAssertEqual([16], nets.featureSizes)
        XCTAssertEqual(3, d.chunksSeen)
    }

    func testTheContextIsTheLast480SamplesOfThePreviousChunk() throws {
        final class Recorder: WakeNets {
            var inputs: [[Float]] = []
            let wakeInputFrames = 16
            func melspectrogram(_ samples: [Float]) throws -> [[Float]] { inputs.append(samples); return [] }
            func embedding(_ window: [[Float]]) throws -> [Float] { Array(repeating: 0, count: 96) }
            func wake(_ features: [[Float]]) throws -> Float { 0 }
        }
        let nets = Recorder()
        let d = try WakeDetector(nets: nets, seed: zeros(16))
        _ = try d.accept((0..<1280).map { Int16($0) })
        _ = try d.accept((1280..<2560).map { Int16(truncatingIfNeeded: $0) })
        XCTAssertEqual(2, nets.inputs.count)
        let second = nets.inputs.last ?? []
        XCTAssertEqual(1760, second.count)
        XCTAssertEqual(Array((800..<2560).map { Float($0) }), second, "480 samples of context, then the chunk")
    }

    func testTheFirstFiveScoresAfterAResetAreSilent() throws {
        let nets = FakeNets { _ in 0.99 }
        let d = try WakeDetector(nets: nets, seed: zeros(41))
        let chunk = [Int16](repeating: 0, count: 1280)
        let scores = try (1...7).map { _ in try d.accept(chunk) }
        XCTAssertEqual([0, 0, 0, 0, 0, 0.99, 0.99], scores)
        d.reset()
        XCTAssertEqual(0, try d.accept(chunk))
    }

    func testTheMelTransformIsAppliedBeforeTheEmbedding() throws {
        let nets = FakeNets()
        let d = try WakeDetector(nets: nets, seed: [])
        _ = try d.accept([Int16](repeating: 30, count: 1280))
        // mel value 30 -> 30 / 10 + 2 = 5 in the newest frame of the window.
        XCTAssertEqual(5, try XCTUnwrap(nets.windowsNewest.last), accuracy: 1e-6)
        XCTAssertNil(try d.accept([Int16](repeating: 0, count: 100)))
    }

    /// With fewer embeddings than the wake model reads, the score is 0.
    func testTooFewEmbeddingsScoreNothing() throws {
        let nets = FakeNets { _ in 0.99 }
        let d = try WakeDetector(nets: nets, seed: [])
        let chunk = [Int16](repeating: 0, count: 1280)
        let scores = try (1...16).map { _ in try d.accept(chunk) }
        XCTAssertEqual(Array(repeating: Float(0), count: 15) + [0.99], scores)
        XCTAssertEqual(1, nets.wakes, "the wake model runs only once 16 embeddings exist")
    }

    /// `count` limits what is read; a count past the frame is the frame.
    func testOnlyCountSamplesAreRead() throws {
        let nets = FakeNets()
        let d = try WakeDetector(nets: nets, seed: zeros(41))
        XCTAssertNil(try d.accept([Int16](repeating: 1, count: 1280), count: 1279))
        XCTAssertNotNil(try d.accept([Int16](repeating: 1, count: 4), count: 1))
        XCTAssertNil(try d.accept([Int16](repeating: 1, count: 4), count: 99))
        XCTAssertEqual([1280], nets.melInputs)
    }

    func testTheNoiseSeedIsEveryEighthWindowOfFourSeconds() throws {
        let nets = FakeNets()
        let seed = try WakeDetector.noiseFeatures(nets)
        // 64000 samples -> 397 mel frames -> windows at 0, 8, ..., 320.
        XCTAssertEqual(41, seed.count)
        XCTAssertEqual([64000], nets.melInputs)
    }

    /// The noise is Android's to the sample: `java.util.Random(0x0D0E)`,
    /// `nextInt(2000) - 1000`, 64,000 times (the values were printed by a
    /// JDK 21 run of exactly that loop).
    func testTheSeededNoiseIsAndroidsToTheSample() {
        let noise = WakeDetector.seededNoise()
        XCTAssertEqual(64_000, noise.count)
        XCTAssertEqual([-89, -818, -645, 919, 19, 856, -468, 843], Array(noise.prefix(8)))
        XCTAssertEqual(817, noise.last)
        let weighted = noise.enumerated().reduce(Int64(0)) { sum, item in sum + Int64(item.element) * Int64(item.offset % 7 + 1) }
        XCTAssertEqual(-564_983, weighted)
        XCTAssertTrue(noise.allSatisfy { $0 >= -1000 && $0 < 1000 })
    }

    /// `nextInt` with a power-of-two bound takes Java's other path.
    func testJavaRandomPowerOfTwoBound() {
        var r = JavaRandom(seed: 42)
        // new java.util.Random(42).nextInt(1024) x 3, from a JDK 21 run.
        XCTAssertEqual([745, 55, 699], [r.nextInt(1024), r.nextInt(1024), r.nextInt(1024)])
    }

    /// A network that fails is the caller's error, never a trap.
    func testANetworkFailureIsThrown() throws {
        final class Broken: WakeNets {
            let wakeInputFrames = 16
            func melspectrogram(_ samples: [Float]) throws -> [[Float]] { throw OnnxModel.Failure(note: "broken") }
            func embedding(_ window: [[Float]]) throws -> [Float] { [] }
            func wake(_ features: [[Float]]) throws -> Float { 0 }
        }
        XCTAssertThrowsError(try WakeDetector(nets: Broken()))
        let d = try WakeDetector(nets: Broken(), seed: [])
        XCTAssertThrowsError(try d.accept([Int16](repeating: 0, count: 1280)))
    }
}
