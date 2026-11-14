import Foundation

/// The room's ears on ONNX Runtime: openWakeWord's three networks
/// (``OrtWakeNets``) under ``WakeDetector``, and Silero (``SileroVad``), behind
/// ``RoomEars`` — `roommode/OrtModels.kt`, `WakeDetector.kt` and
/// `RoomEngine.kt:695-718,1047-1053` (`Ears`, the load, `isSpeech`, `dbfs`).
///
/// **R1 ENGINE-ML owns this file** (and WakeNets.swift, WakeDetector.swift,
/// OrtWakeNets.swift, SileroVad.swift beside it). A load that fails throws
/// ONNX Runtime's own words (``OnnxModel/Failure``), which the room shows as
/// "could not load the wake word" — never a room that claims to listen.
public struct OnnxRoomEarsLoader: RoomEarsLoading {
    public init() {}

    /// `RoomEngine.kt:711-716`: the three wake networks, the detector (its
    /// noise history computed here, once per load) and Silero. Called on the
    /// listening thread, which owns the result.
    public func load(_ files: RoomModelFiles) throws -> any RoomEars {
        let nets = try OrtWakeNets(melspectrogram: files.melspectrogram, embedding: files.embedding, wake: files.wake)
        let detector = try WakeDetector(nets: nets)
        let vad = try SileroVad(contentsOf: files.vad)
        return OnnxRoomEars(files: files, wake: detector, vad: vad)
    }
}

/// What ``OnnxRoomEars/isSpeech(_:)`` asks of a voice detector: Silero, or a
/// test's fake.
protocol RoomVoiceDetecting: AnyObject {
    func probability(_ frame: [Int16], count: Int?) throws -> Float
    func reset()
}

/// ``RoomEars`` on ONNX Runtime. One thread owns it (the contract's rule).
///
/// ``timing`` is for the D-PERF device run (ROOMMODE-MAP §5): the kiosk's
/// DEBUG line reads it by downcast (`ears as? OnnxRoomEars`), so it is not
/// on the frozen protocol. It is the only part that is safe to read from
/// another thread.
public final class OnnxRoomEars: RoomEars {
    public let files: RoomModelFiles?
    private let wake: WakeDetector
    private let vad: RoomVoiceDetecting
    /// The wake chain's cost per 80 ms chunk, as a running mean.
    public let timing = RoomEarsTiming()

    init(files: RoomModelFiles?, wake: WakeDetector, vad: RoomVoiceDetecting) {
        self.files = files
        self.wake = wake
        self.vad = vad
    }

    public func wakeScore(_ frame: [Int16]) throws -> Float? {
        let start = DispatchTime.now().uptimeNanoseconds
        let chunksBefore = wake.chunksSeen
        let score = try wake.accept(frame)
        let spent = DispatchTime.now().uptimeNanoseconds &- start
        timing.record(nanoseconds: spent, chunks: wake.chunksSeen - chunksBefore)
        return score
    }

    public func resetWake() { wake.reset() }

    /// `RoomEngine.kt:1047-1053`: quieter than the floor is never speech, but
    /// Silero still hears the frame, to keep its state moving.
    public func isSpeech(_ frame: [Int16]) throws -> Bool {
        if Self.dbfs(frame, count: frame.count) < RoomAudioFrame.speechFloorDBFS {
            _ = try vad.probability(frame, count: frame.count)
            return false
        }
        return try vad.probability(frame, count: frame.count) >= RoomAudioFrame.vadThreshold
    }

    public func resetVoice() { vad.reset() }

    /// The frame's level in dBFS (`RoomEngine.kt:1194-1203`): −120 for an
    /// empty or silent frame.
    public static func dbfs(_ frame: [Int16], count: Int) -> Double {
        let n = min(count, frame.count)
        if n <= 0 { return -120 }
        var sum = 0.0
        for i in 0..<n {
            let s = Double(frame[i]) / 32768
            sum += s * s
        }
        let rms = (sum / Double(n)).squareRoot()
        return rms <= 0 ? -120 : 20 * log10(rms)
    }
}

/// A running mean of the wake chain's milliseconds per 80 ms chunk
/// (melspectrogram + embedding + wake model, and the buffering around them),
/// for the D-PERF run: the A12 budget is 8 ms (ROOMMODE-MAP §5).
///
/// The time of a frame that completes no chunk is carried into the next
/// chunk, so the mean is all of the wake chain's time over all its chunks.
/// Written by the listening thread, readable from any.
public final class RoomEarsTiming: @unchecked Sendable {
    public struct Snapshot: Equatable, Sendable {
        /// Chunks measured since the ears were loaded.
        public var chunks: Int
        /// The mean since the ears were loaded, ms per chunk.
        public var meanMs: Double
        /// The mean of about the last 100 chunks (8 s), ms per chunk
        /// (exponential, weight 1/100).
        public var recentMs: Double
        /// The slowest chunk seen, ms.
        public var maxMs: Double

        public init(chunks: Int = 0, meanMs: Double = 0, recentMs: Double = 0, maxMs: Double = 0) {
            self.chunks = chunks
            self.meanMs = meanMs
            self.recentMs = recentMs
            self.maxMs = maxMs
        }
    }

    private struct State {
        var snapshot = Snapshot()
        var totalNs: UInt64 = 0
        var carriedNs: UInt64 = 0
    }

    private let state = RoomLock(State())

    public init() {}

    public var snapshot: Snapshot { state.withLock { $0.snapshot } }

    /// The running mean, ms per chunk (0 before the first chunk).
    public var meanMs: Double { snapshot.meanMs }

    func record(nanoseconds: UInt64, chunks: Int) {
        state.withLock { s in
            if chunks <= 0 {
                s.carriedNs &+= nanoseconds
                return
            }
            let spent = s.carriedNs &+ nanoseconds
            s.carriedNs = 0
            s.totalNs &+= spent
            let perChunk = Double(spent) / Double(chunks) / 1_000_000
            let first = s.snapshot.chunks == 0
            s.snapshot.chunks += chunks
            s.snapshot.meanMs = Double(s.totalNs) / Double(s.snapshot.chunks) / 1_000_000
            s.snapshot.recentMs = first ? perChunk : s.snapshot.recentMs + (perChunk - s.snapshot.recentMs) / 100
            s.snapshot.maxMs = max(s.snapshot.maxMs, perChunk)
        }
    }
}
