import Foundation

/// openWakeWord's three networks on ONNX Runtime, through ``OnnxModel``
/// (`roommode/OrtModels.kt:16-87`, `OrtWakeNets`). One intra-op thread each:
/// a room listens all day, on old phones.
///
/// Not thread-safe and not `Sendable`: the listening thread that loads it is
/// the only one that runs it.
public final class OrtWakeNets: WakeNets {
    private let mel: OnnxModel
    private let emb: OnnxModel
    private let wakeModel: OnnxModel
    private let melInput: String
    private let melOutput: String
    private let embInput: String
    private let embOutput: String
    private let wakeInput: String
    private let wakeOutput: String

    /// The stock models' embedding size: the wake model reads frames x 96.
    static let embeddingSize = 96
    /// What a wake model reads when it says nothing else (Android's default).
    static let defaultWakeFrames = 16

    public let wakeInputFrames: Int

    public init(melspectrogram: URL, embedding: URL, wake: URL) throws {
        mel = try OnnxModel(contentsOf: melspectrogram, threads: 1)
        emb = try OnnxModel(contentsOf: embedding, threads: 1)
        wakeModel = try OnnxModel(contentsOf: wake, threads: 1)
        (melInput, melOutput) = try Self.names(mel, melspectrogram)
        (embInput, embOutput) = try Self.names(emb, embedding)
        (wakeInput, wakeOutput) = try Self.names(wakeModel, wake)
        wakeInputFrames = try Self.probeFrames(wakeModel, input: wakeInput, output: wakeOutput)
    }

    public func melspectrogram(_ samples: [Float]) throws -> [[Float]] {
        let out = try mel.runShaped([melInput: .float(samples, shape: [1, samples.count])], output: melOutput)
        guard let bins = out.shape.last, bins > 0 else {
            throw OnnxModel.Failure(note: "the melspectrogram model gave a shapeless answer \(out.shape)")
        }
        let frames = out.values.count / bins
        var rows: [[Float]] = []
        rows.reserveCapacity(frames)
        out.values.withUnsafeBufferPointer { flat in
            for f in 0..<frames {
                rows.append(Array(flat[(f * bins)..<(f * bins + bins)]))
            }
        }
        return rows
    }

    public func embedding(_ window: [[Float]]) throws -> [Float] {
        let bins = window.first?.count ?? 0
        let out = try emb.run([embInput: .float(Self.flat(window), shape: [1, window.count, bins, 1])], outputs: [embOutput])
        return out[embOutput] ?? []
    }

    public func wake(_ features: [[Float]]) throws -> Float {
        let dim = features.first?.count ?? 0
        let out = try wakeModel.run([wakeInput: .float(Self.flat(features), shape: [1, features.count, dim])], outputs: [wakeOutput])
        return out[wakeOutput]?.first ?? 0
    }

    // MARK: - helpers

    private static func names(_ model: OnnxModel, _ url: URL) throws -> (String, String) {
        guard let input = model.inputNames.first, let output = model.outputNames.first else {
            throw OnnxModel.Failure(note: "\(url.lastPathComponent) has no input or no output")
        }
        return (input, output)
    }

    private static func flat(_ rows: [[Float]]) -> [Float] {
        var out: [Float] = []
        out.reserveCapacity(rows.count * (rows.first?.count ?? 0))
        for row in rows { out.append(contentsOf: row) }
        return out
    }

    /// How many embeddings the wake model reads. Android reads it from the
    /// model's declared input shape (`OrtModels.kt:29-32`); ``OnnxModel``
    /// (frozen for this batch) does not expose input shapes, so the wake
    /// model is asked: a run on 16 zero embeddings (the stock models' size)
    /// either works, or ONNX Runtime's refusal names the size it expects
    /// ("index: 1 Got: 16 Expected: 28"), which is then run once to prove it.
    /// A dynamic dimension accepts 16, which is Android's default for it.
    static func probeFrames(_ model: OnnxModel, input: String, output: String) throws -> Int {
        func attempt(_ frames: Int) throws {
            let zeros = [Float](repeating: 0, count: frames * embeddingSize)
            _ = try model.run([input: .float(zeros, shape: [1, frames, embeddingSize])], outputs: [output])
        }
        do {
            try attempt(defaultWakeFrames)
            return defaultWakeFrames
        } catch let failure as OnnxModel.Failure {
            guard let expected = expectedFrames(in: failure.note), expected > 0, expected != defaultWakeFrames else {
                throw failure
            }
            try attempt(expected)
            return expected
        }
    }

    /// The size ONNX Runtime says dimension 1 should be, from its refusal.
    static func expectedFrames(in note: String) -> Int? {
        guard let rule = try? NSRegularExpression(pattern: #"index:\s*1\s+Got:\s*\d+\s+Expected:\s*(\d+)"#) else { return nil }
        let range = NSRange(note.startIndex..., in: note)
        guard let match = rule.firstMatch(in: note, range: range), match.numberOfRanges > 1,
              let found = Range(match.range(at: 1), in: note) else { return nil }
        return Int(note[found])
    }
}
