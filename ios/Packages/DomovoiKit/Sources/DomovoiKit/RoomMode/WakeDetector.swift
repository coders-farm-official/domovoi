import Foundation

/// openWakeWord's streaming detector (its `AudioFeatures` and
/// `Model.predict`, openWakeWord 0.6.0, Apache-2.0), as
/// `roommode/WakeDetector.kt` rebuilds it, so a phone hears the same wake
/// word a Pi hears with the same models:
///
/// - audio goes in as 80 ms chunks (1280 samples at 16 kHz);
/// - each chunk's melspectrogram is taken over the chunk and the 480 samples
///   before it, transformed `x / 10 + 2` (what makes the ONNX model match
///   Google's), and kept (at most 970 frames, ~10 s);
/// - one embedding per chunk, of the last 76 mel frames, kept (at most 120);
/// - the wake model scores the last 16 embeddings;
/// - the first five scores after a reset are 0, as openWakeWord's are.
///
/// After a reset the embedding history is seeded with the embeddings of four
/// seconds of low noise, as openWakeWord seeds it; computed once per model
/// (``noiseFeatures(_:noise:)``) and reused on every reset, as the Pi does
/// (`_prime_wake_reset`), so a reset costs nothing.
///
/// Not thread-safe: one listening thread feeds it.
public final class WakeDetector {
    public static let chunk = 1280
    public static let context = 160 * 3
    public static let melBins = 32
    public static let window = 76
    public static let step = 8
    public static let melMax = 10 * 97
    public static let featuresMax = 120
    public static let warmup = 5
    public static let noiseSeconds = 4
    /// `java.util.Random(0x0D0E)`, Android's seed, so a load here makes the
    /// same noise history a phone running Android makes.
    public static let noiseSeed: Int64 = 0x0D0E

    public let nets: any WakeNets
    private let noise: [[Float]]
    private var raw = [Int16](repeating: 0, count: WakeDetector.chunk + WakeDetector.context)
    private var rawFill = 0
    private var mel: [[Float]] = []
    private var features: [[Float]] = []
    private var pending = [Int16](repeating: 0, count: WakeDetector.chunk)
    private var pendingFill = 0
    private var predictions = 0
    /// 80 ms chunks scored since this detector was made (never reset): what
    /// the ears' timing divides by.
    public private(set) var chunksSeen = 0

    /// `seed`: the history after a reset; ``noiseFeatures(_:noise:)`` by default.
    public init(nets: any WakeNets, seed: [[Float]]? = nil) throws {
        self.nets = nets
        self.noise = try seed ?? WakeDetector.noiseFeatures(nets)
        reset()
    }

    public func reset() {
        rawFill = 0
        pendingFill = 0
        mel = [[Float]](repeating: [Float](repeating: 1, count: Self.melBins), count: Self.window)
        mel.reserveCapacity(Self.melMax + 16)
        features = noise
        features.reserveCapacity(Self.featuresMax + 1)
        predictions = 0
    }

    /// Feed `count` samples of `samples` (any length; the room feeds 30 ms
    /// frames). The highest score of the chunks this completed, or nil when
    /// no 80 ms chunk completed.
    public func accept(_ samples: [Int16], count: Int? = nil) throws -> Float? {
        let total = min(max(count ?? samples.count, 0), samples.count)
        var best: Float?
        var at = 0
        while at < total {
            let take = min(Self.chunk - pendingFill, total - at)
            pending.withUnsafeMutableBufferPointer { dst in
                samples.withUnsafeBufferPointer { src in
                    for i in 0..<take { dst[pendingFill + i] = src[at + i] }
                }
            }
            pendingFill += take
            at += take
            if pendingFill == Self.chunk {
                let score = try chunk()
                pendingFill = 0
                best = best.map { max($0, score) } ?? score
            }
        }
        return best
    }

    /// One 80 ms chunk (`pending`): its score.
    private func chunk() throws -> Float {
        // The raw history: the 480 samples before this chunk, then the chunk.
        let keep = min(rawFill, Self.context)
        raw.withUnsafeMutableBufferPointer { r in
            for i in 0..<keep { r[i] = r[rawFill - keep + i] }
            pending.withUnsafeBufferPointer { p in
                for i in 0..<Self.chunk { r[keep + i] = p[i] }
            }
        }
        rawFill = keep + Self.chunk
        var input = [Float](repeating: 0, count: rawFill)
        raw.withUnsafeBufferPointer { r in
            for i in 0..<rawFill { input[i] = Float(r[i]) }
        }
        for frame in try nets.melspectrogram(input) {
            mel.append(Self.transform(frame))
        }
        if mel.count > Self.melMax { mel.removeFirst(mel.count - Self.melMax) }
        if mel.count >= Self.window {
            features.append(try nets.embedding(Array(mel.suffix(Self.window))))
            if features.count > Self.featuresMax { features.removeFirst(features.count - Self.featuresMax) }
        }
        let n = nets.wakeInputFrames
        let score: Float = features.count >= n ? try nets.wake(Array(features.suffix(n))) : 0
        predictions += 1
        chunksSeen += 1
        return predictions <= Self.warmup ? 0 : score
    }

    /// `x / 10 + 2` over the first 32 bins (Android's `FloatArray(MEL_BINS)
    /// { frame[it] / 10f + 2f }`; a short frame is a model fault, padded with
    /// the transform of 0 rather than trapping).
    static func transform(_ frame: [Float]) -> [Float] {
        var out = [Float](repeating: 2, count: melBins)
        for i in 0..<min(melBins, frame.count) { out[i] = frame[i] / 10 + 2 }
        return out
    }

    /// openWakeWord's `_get_embeddings` over 4 s of noise in [-1000, 1000):
    /// every 8th 76-frame window's embedding. `noise` nil: Android's seeded
    /// noise (``JavaRandom`` with ``noiseSeed``), so every load is alike, and
    /// alike on both phones.
    public static func noiseFeatures(_ nets: any WakeNets, noise: [Int16]? = nil) throws -> [[Float]] {
        let audio = noise ?? seededNoise()
        let spec = try nets.melspectrogram(audio.map { Float($0) }).map(transform)
        var out: [[Float]] = []
        var i = 0
        while i + window <= spec.count {
            out.append(try nets.embedding(Array(spec[i..<(i + window)])))
            i += step
        }
        return out
    }

    /// `ShortArray(16_000 * 4) { (r.nextInt(2000) - 1000).toShort() }` with
    /// `Random(0x0D0E)`.
    static func seededNoise() -> [Int16] {
        var random = JavaRandom(seed: noiseSeed)
        var out = [Int16](repeating: 0, count: 16_000 * noiseSeconds)
        for i in out.indices { out[i] = Int16(random.nextInt(2000) - 1000) }
        return out
    }
}

/// `java.util.Random`'s generator (the 48-bit LCG and `nextInt(bound)`), for
/// the one place a number has to match Android's: the wake word's noise seed.
struct JavaRandom {
    private static let multiplier: UInt64 = 0x5DEECE66D
    private static let mask: UInt64 = (1 << 48) - 1
    private var seed: UInt64

    init(seed: Int64) {
        self.seed = (UInt64(bitPattern: seed) ^ Self.multiplier) & Self.mask
    }

    mutating func next(_ bits: Int) -> Int32 {
        seed = (seed &* Self.multiplier &+ 0xB) & Self.mask
        return Int32(truncatingIfNeeded: Int64(bitPattern: seed) >> (48 - bits))
    }

    /// `Random.nextInt(bound)`, bound > 0.
    mutating func nextInt(_ bound: Int32) -> Int32 {
        if bound & -bound == bound {
            return Int32(truncatingIfNeeded: (Int64(bound) &* Int64(next(31))) >> 31)
        }
        var bits: Int32
        var value: Int32
        repeat {
            bits = next(31)
            value = bits % bound
        } while bits &- value &+ (bound &- 1) < 0
        return value
    }
}
