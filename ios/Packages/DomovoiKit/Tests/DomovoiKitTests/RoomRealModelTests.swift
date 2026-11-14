import Foundation
import XCTest
@testable import DomovoiKit

/// The room's ears on the REAL models, against openWakeWord 0.6.0 itself
/// (Android's `WakeDetectorTest.theSameScoresOpenWakeWordGives` and
/// `sileroHearsSpeechAndNotSilence`, and the ears end to end).
///
/// The models and clips are never in the repo (`hey_jarvis` is CC BY-NC-SA;
/// the clips are synthesized speech): they live on the build Mac in
/// `~/domovoi-ios/room-fixtures/` (or `$DOMOVOI_ROOM_FIXTURES`), Android's
/// `-Pdomovoi.oww.dir` rule (ROOMMODE-MAP §7). `models/` holds the server's
/// pinned openWakeWord v0.5.1 files (domovoi/satellite_media/fetchers.py
/// `OWW_MODEL_FILES`), `clips/` the 16 kHz clips and their reference scores
/// (`<name>.oww.json`, openWakeWord 0.6.0 over 1 s of padding each side,
/// its noise history from 4 s of zeros).
///
/// No folder: skipped, and the skip says so. A folder with a file missing:
/// FAILED, never skipped, so a half-provisioned Mac cannot pass quietly.
enum RoomFixtures {
    static let models = ["melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx", "silero_vad.onnx"]
    static let clips = ["hey_jarvis.wav", "hey_jarvis.oww.json", "what_time.wav", "what_time.oww.json"]

    static var folder: URL {
        if let override = ProcessInfo.processInfo.environment["DOMOVOI_ROOM_FIXTURES"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        return URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("domovoi-ios")
            .appendingPathComponent("room-fixtures")
    }

    /// The folder, every file checked; skips only when the folder is absent.
    static func require(file: StaticString = #filePath, line: UInt = #line) throws -> URL {
        let root = folder
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: root.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw XCTSkip("SKIPPED, NOT PASSED — no room fixtures at \(root.path) (models + clips are provisioned on the build Mac only)")
        }
        let wanted = models.map { "models/\($0)" } + clips.map { "clips/\($0)" }
        let missing = wanted.filter { !FileManager.default.fileExists(atPath: root.appendingPathComponent($0).path) }
        if !missing.isEmpty {
            XCTFail("the room fixtures at \(root.path) are missing \(missing)", file: file, line: line)
            throw OnnxModel.Failure(note: "room fixtures incomplete")
        }
        return root
    }

    static func model(_ root: URL, _ name: String) -> URL {
        root.appendingPathComponent("models").appendingPathComponent(name)
    }

    static func clip(_ root: URL, _ name: String) -> URL {
        root.appendingPathComponent("clips").appendingPathComponent(name)
    }

    static func files(_ root: URL) -> RoomModelFiles {
        RoomModelFiles(
            melspectrogram: model(root, "melspectrogram.onnx"),
            embedding: model(root, "embedding_model.onnx"),
            vad: model(root, "silero_vad.onnx"),
            wake: model(root, "hey_jarvis_v0.1.onnx"),
            wakeKey: "hey_jarvis"
        )
    }

    /// 16 kHz mono PCM16 samples of a canonical WAV file (Android's `readWav`).
    static func readWav(_ url: URL) throws -> [Int16] {
        let bytes = [UInt8](try Data(contentsOf: url))
        var at = 12
        while at + 8 <= bytes.count {
            let id = String(decoding: bytes[at..<(at + 4)], as: UTF8.self)
            let size = Int(bytes[at + 4]) | Int(bytes[at + 5]) << 8 | Int(bytes[at + 6]) << 16 | Int(bytes[at + 7]) << 24
            if id == "data" {
                let n = min(size, bytes.count - at - 8) / 2
                return (0..<n).map { i in
                    Int16(bitPattern: UInt16(bytes[at + 8 + 2 * i]) | UInt16(bytes[at + 9 + 2 * i]) << 8)
                }
            }
            at += 8 + size + (size & 1)
        }
        throw OnnxModel.Failure(note: "no data chunk in \(url.lastPathComponent)")
    }

    static func referenceScores(_ url: URL) throws -> [Float] {
        let object = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any]
        guard let scores = object?["scores"] as? [NSNumber] else {
            throw OnnxModel.Failure(note: "no scores in \(url.lastPathComponent)")
        }
        return scores.map(\.floatValue)
    }

    /// One second of silence, the clip, one second of silence (the reference's padding).
    static func padded(_ pcm: [Int16]) -> [Int16] {
        let pad = [Int16](repeating: 0, count: 16_000)
        return pad + pcm + pad
    }
}

final class RoomRealModelTests: XCTestCase {

    /// The same per-chunk scores openWakeWord 0.6.0 gives, within 0.02
    /// (Android's tolerance): "hey jarvis" fires, "what time is it" does not.
    func testTheSameScoresOpenWakeWordGives() throws {
        let root = try RoomFixtures.require()
        for name in ["hey_jarvis", "what_time"] {
            let want = try RoomFixtures.referenceScores(RoomFixtures.clip(root, "\(name).oww.json"))
            let audio = RoomFixtures.padded(try RoomFixtures.readWav(RoomFixtures.clip(root, "\(name).wav")))
            let nets = try OrtWakeNets(
                melspectrogram: RoomFixtures.model(root, "melspectrogram.onnx"),
                embedding: RoomFixtures.model(root, "embedding_model.onnx"),
                wake: RoomFixtures.model(root, "hey_jarvis_v0.1.onnx")
            )
            XCTAssertEqual(16, nets.wakeInputFrames)
            let d = try WakeDetector(nets: nets, seed: WakeDetector.noiseFeatures(nets, noise: [Int16](repeating: 0, count: 64_000)))
            var got: [Float] = []
            var at = 0
            while at + 1280 <= audio.count {
                guard let score = try d.accept(Array(audio[at..<(at + 1280)])) else {
                    XCTFail("\(name): chunk at \(at) gave no score")
                    return
                }
                got.append(score)
                at += 1280
            }
            XCTAssertEqual(want.count, got.count, "\(name): chunk count")
            for (i, pair) in zip(want, got).enumerated() {
                XCTAssertEqual(pair.0, pair.1, accuracy: 0.02, "\(name) chunk \(i)")
            }
            let peak = got.max() ?? 0
            if name == "hey_jarvis" {
                XCTAssertGreaterThan(peak, 0.5, "it fires: \(got)")
            } else {
                XCTAssertLessThan(peak, 0.1, "it stays quiet: \(got)")
            }
        }
    }

    func testSileroHearsSpeechAndNotSilence() throws {
        let root = try RoomFixtures.require()
        let vad = try SileroVad(contentsOf: RoomFixtures.model(root, "silero_vad.onnx"))
        let silence = try (0..<20).map { _ in try vad.probability([Int16](repeating: 0, count: 480)) }
        XCTAssertLessThan(silence.max() ?? 1, 0.2, "\(silence)")
        vad.reset()
        let pcm = try RoomFixtures.readWav(RoomFixtures.clip(root, "what_time.wav"))
        var probabilities: [Float] = []
        var at = 0
        while at + 480 <= pcm.count {
            probabilities.append(try vad.probability(Array(pcm[at..<(at + 480)])))
            at += 480
        }
        XCTAssertGreaterThan(probabilities.filter { $0 > 0.5 }.count, probabilities.count / 4, "\(probabilities)")
    }

    /// What R2's engine does with them: the loader's ears (Android's own
    /// seeded noise history, not the reference's zeros), fed 30 ms frames,
    /// through the default gate — "hey jarvis" wakes the room with a
    /// `wake_score` over the threshold, "what time is it" does not; Silero
    /// behind the floor hears the question and not the silence around it.
    func testTheLoadedEarsWakeOnHeyJarvisAndHearTheQuestion() throws {
        let root = try RoomFixtures.require()
        let loaded = try OnnxRoomEarsLoader().load(RoomFixtures.files(root))
        let ears = try XCTUnwrap(loaded as? OnnxRoomEars, "the loader's ears expose the D-PERF timing")

        func listen(_ clip: String) throws -> (woke: Bool, peak: Float?) {
            ears.resetWake()
            let gate = WakeGate(threshold: RoomWakeSettings.defaultThreshold)
            let audio = RoomFixtures.padded(try RoomFixtures.readWav(RoomFixtures.clip(root, clip)))
            var woke = false
            var now: Int64 = 0
            var at = 0
            while at + 480 <= audio.count {
                if let score = try ears.wakeScore(Array(audio[at..<(at + 480)])), gate.feed(score, now: now) {
                    woke = true
                }
                at += 480
                now += 30
            }
            return (woke, gate.lastPeak)
        }

        let jarvis = try listen("hey_jarvis.wav")
        XCTAssertTrue(jarvis.woke, "hey jarvis did not wake the room")
        XCTAssertGreaterThan(jarvis.peak ?? 0, RoomWakeSettings.defaultThreshold)
        XCTAssertFalse(try listen("what_time.wav").woke, "what time is it woke the room")
        XCTAssertGreaterThan(ears.timing.snapshot.chunks, 0)

        ears.resetVoice()
        let silent = try (0..<20).map { _ in try ears.isSpeech([Int16](repeating: 0, count: 480)) }
        XCTAssertFalse(silent.contains(true))
        let pcm = try RoomFixtures.readWav(RoomFixtures.clip(root, "what_time.wav"))
        var speech = 0
        var frames = 0
        var at = 0
        while at + 480 <= pcm.count {
            if try ears.isSpeech(Array(pcm[at..<(at + 480)])) { speech += 1 }
            frames += 1
            at += 480
        }
        XCTAssertGreaterThan(speech, frames / 4, "\(speech) of \(frames) frames heard as speech")
    }
}
