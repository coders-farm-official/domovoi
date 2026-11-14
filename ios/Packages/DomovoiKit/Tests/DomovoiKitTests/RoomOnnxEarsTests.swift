import Foundation
import XCTest
@testable import DomovoiKit

/// The ears around the networks (`RoomEngine.kt:695-718,1047-1053`): the
/// −55 dBFS floor in front of Silero, Silero's state kept moving under it,
/// the D-PERF timing, and a load that fails as a sentence. No models: fakes.
final class RoomOnnxEarsTests: XCTestCase {

    final class FakeVoice: RoomVoiceDetecting {
        var heard = 0
        var resets = 0
        var answer: Float
        init(answer: Float) { self.answer = answer }
        func probability(_ frame: [Int16], count: Int?) throws -> Float {
            heard += 1
            return answer
        }
        func reset() { resets += 1 }
    }

    private func ears(voice: FakeVoice, scores: Float = 0.9) throws -> OnnxRoomEars {
        let nets = WakeDetectorTests.FakeNets { _ in scores }
        let seed = Array(repeating: Array(repeating: Float(0), count: 96), count: 41)
        return OnnxRoomEars(files: nil, wake: try WakeDetector(nets: nets, seed: seed), vad: voice)
    }

    private func tone(_ amplitude: Int16) -> [Int16] {
        (0..<RoomAudioFrame.samples).map { $0 % 2 == 0 ? amplitude : -amplitude }
    }

    func testTheLevelIsDBFS() {
        XCTAssertEqual(-120, OnnxRoomEars.dbfs([Int16](repeating: 0, count: 480), count: 480))
        XCTAssertEqual(-120, OnnxRoomEars.dbfs([], count: 0))
        XCTAssertEqual(0, OnnxRoomEars.dbfs([Int16](repeating: -32768, count: 480), count: 480), accuracy: 1e-9)
        // A square wave of amplitude 32768/2 is -6.02 dBFS.
        XCTAssertEqual(-6.0206, OnnxRoomEars.dbfs(tone(16384), count: 480), accuracy: 1e-3)
        // Only `count` samples count.
        XCTAssertEqual(-120, OnnxRoomEars.dbfs([0, 0, 30000], count: 2))
    }

    /// Quieter than −55 dBFS is never speech, even when Silero would say so,
    /// and Silero still hears the frame (its state keeps moving).
    func testAFrameUnderTheFloorIsNotSpeechButSileroStillHearsIt() throws {
        let voice = FakeVoice(answer: 0.99)
        let e = try ears(voice: voice)
        // amplitude 50: 20 log10(50/32768) = -56.3 dBFS, under the floor.
        XCTAssertLessThan(OnnxRoomEars.dbfs(tone(50), count: 480), RoomAudioFrame.speechFloorDBFS)
        XCTAssertFalse(try e.isSpeech(tone(50)))
        XCTAssertEqual(1, voice.heard, "Silero must still see a quiet frame")
        // amplitude 60: -54.7 dBFS, over it: Silero decides.
        XCTAssertGreaterThan(OnnxRoomEars.dbfs(tone(60), count: 480), RoomAudioFrame.speechFloorDBFS)
        XCTAssertTrue(try e.isSpeech(tone(60)))
        XCTAssertEqual(2, voice.heard)
    }

    func testSileroDecidesAtHalf() throws {
        let voice = FakeVoice(answer: 0.5)
        let e = try ears(voice: voice)
        XCTAssertTrue(try e.isSpeech(tone(3000)), "at the threshold is speech (>=)")
        voice.answer = 0.4999
        XCTAssertFalse(try e.isSpeech(tone(3000)))
        e.resetVoice()
        XCTAssertEqual(1, voice.resets)
    }

    func testTheWakeScoreIsTheDetectorsAndTheTimingCountsChunks() throws {
        let voice = FakeVoice(answer: 0)
        let e = try ears(voice: voice, scores: 0.75)
        XCTAssertEqual(RoomEarsTiming.Snapshot(), e.timing.snapshot)
        var scores: [Float?] = []
        for _ in 0..<24 { scores.append(try e.wakeScore([Int16](repeating: 0, count: 480))) }
        // 24 x 480 = 11,520 samples = 9 chunks; the first five are warm-up.
        let completed = scores.compactMap { $0 }
        XCTAssertEqual(9, completed.count)
        XCTAssertEqual([0, 0, 0, 0, 0, 0.75, 0.75, 0.75, 0.75], completed)
        let t = e.timing.snapshot
        XCTAssertEqual(9, t.chunks)
        XCTAssertGreaterThan(t.meanMs, 0)
        XCTAssertGreaterThanOrEqual(t.maxMs, t.meanMs)
        e.resetWake()
        XCTAssertNil(try e.wakeScore([Int16](repeating: 0, count: 480)))
        XCTAssertEqual(9, e.timing.snapshot.chunks, "a frame that completes no chunk adds no chunk")
    }

    /// The mean is all of the time over all of the chunks: the time of the
    /// frames that complete none is carried into the next chunk.
    func testTheTimingCarriesTheFramesBetweenChunks() {
        let timing = RoomEarsTiming()
        timing.record(nanoseconds: 1_000_000, chunks: 0)
        timing.record(nanoseconds: 1_000_000, chunks: 0)
        timing.record(nanoseconds: 2_000_000, chunks: 1)
        XCTAssertEqual(RoomEarsTiming.Snapshot(chunks: 1, meanMs: 4, recentMs: 4, maxMs: 4), timing.snapshot)
        timing.record(nanoseconds: 2_000_000, chunks: 1)
        let t = timing.snapshot
        XCTAssertEqual(2, t.chunks)
        XCTAssertEqual(3, t.meanMs, accuracy: 1e-9)
        XCTAssertEqual(4 + (2 - 4) / 100.0, t.recentMs, accuracy: 1e-9)
        XCTAssertEqual(4, t.maxMs)
        XCTAssertEqual(3, timing.meanMs, accuracy: 1e-9)
    }

    /// A model that is not there is ONNX Runtime's sentence naming it, never a
    /// trap and never ears that claim to listen.
    func testALoadWithAMissingModelThrowsItsName() {
        let dir = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("room-ears-missing-\(UUID().uuidString)")
        let files = RoomModelFiles(
            melspectrogram: dir.appendingPathComponent("melspectrogram.onnx"),
            embedding: dir.appendingPathComponent("embedding_model.onnx"),
            vad: dir.appendingPathComponent("silero_vad.onnx"),
            wake: dir.appendingPathComponent("hey_jarvis_v0.1.onnx"),
            wakeKey: "hey_jarvis"
        )
        XCTAssertThrowsError(try OnnxRoomEarsLoader().load(files)) { error in
            let note = (error as? OnnxModel.Failure)?.note ?? "\(error)"
            XCTAssertTrue(note.contains("melspectrogram.onnx"), note)
        }
    }

    /// A model that loads but is not the network asked for (here the tiny
    /// `Add` fixture) is refused at load, never run wrong.
    func testTheWrongModelIsRefusedAtLoad() throws {
        let tiny = try XCTUnwrap(Bundle.module.url(forResource: "tiny_add", withExtension: "onnx", subdirectory: "Fixtures"))
        XCTAssertThrowsError(try SileroVad(contentsOf: tiny)) { error in
            let note = (error as? OnnxModel.Failure)?.note ?? "\(error)"
            XCTAssertTrue(note.contains("not Silero VAD v4"), note)
        }
        XCTAssertThrowsError(try OrtWakeNets(melspectrogram: tiny, embedding: tiny, wake: tiny))
    }

    /// The wake model's input size comes from ONNX Runtime's own refusal
    /// when it is not the stock 16.
    func testTheExpectedFramesAreReadFromTheRefusal() {
        let note = "Got invalid dimensions for input: x.1 for the following indices\n index: 1 Got: 16 Expected: 28\n Please fix either the inputs/outputs or the model."
        XCTAssertEqual(28, OrtWakeNets.expectedFrames(in: note))
        XCTAssertNil(OrtWakeNets.expectedFrames(in: "index: 2 Got: 96 Expected: 64"))
        XCTAssertNil(OrtWakeNets.expectedFrames(in: "could not load"))
    }
}
