import Foundation

/// Silero VAD v4 (MIT; `silero_vad.onnx`, the copy openWakeWord ships and the
/// core pins) on ONNX Runtime: the speech probability of one 30 ms frame,
/// carrying the network's state from frame to frame, exactly as
/// openWakeWord's `VAD.predict` runs it (frame / 32767, sr 16000)
/// (`roommode/OrtModels.kt:89-134`, `SileroVad`).
///
/// v4's inputs: `input` [1, N], `sr` an int64 scalar, `h` and `c` [2, 1, 64];
/// its outputs in order: the probability, then the new `h` and `c`. A model
/// without those inputs (v5 has `state`) is refused at load, never run wrong.
///
/// Not thread-safe; ``reset()`` between captures.
public final class SileroVad {
    static let stateShape = [2, 1, 64]
    static let stateSize = 2 * 1 * 64

    private let model: OnnxModel
    private let probabilityOutput: String
    private let hOutput: String
    private let cOutput: String
    private var h = [Float](repeating: 0, count: SileroVad.stateSize)
    private var c = [Float](repeating: 0, count: SileroVad.stateSize)

    public init(contentsOf url: URL) throws {
        model = try OnnxModel(contentsOf: url, threads: 1)
        let wanted: Set<String> = ["input", "sr", "h", "c"]
        guard wanted.isSubset(of: Set(model.inputNames)) else {
            throw OnnxModel.Failure(note: "\(url.lastPathComponent) is not Silero VAD v4 (inputs \(model.inputNames))")
        }
        let outputs = model.outputNames
        guard outputs.count >= 3 else {
            throw OnnxModel.Failure(note: "\(url.lastPathComponent) is not Silero VAD v4 (outputs \(outputs))")
        }
        probabilityOutput = outputs[0]
        hOutput = outputs[1]
        cOutput = outputs[2]
    }

    public func reset() {
        h = [Float](repeating: 0, count: Self.stateSize)
        c = [Float](repeating: 0, count: Self.stateSize)
    }

    /// Probability (0...1) that `count` samples of `frame` are speech.
    public func probability(_ frame: [Int16], count: Int? = nil) throws -> Float {
        let n = min(max(count ?? frame.count, 0), frame.count)
        var x = [Float](repeating: 0, count: n)
        frame.withUnsafeBufferPointer { src in
            for i in 0..<n { x[i] = Float(src[i]) / 32767 }
        }
        let out = try model.run(
            [
                "input": .float(x, shape: [1, n]),
                "sr": .int64([16_000], shape: []),
                "h": .float(h, shape: Self.stateShape),
                "c": .float(c, shape: Self.stateShape),
            ],
            outputs: [probabilityOutput, hOutput, cOutput]
        )
        guard let p = out[probabilityOutput]?.first,
              let newH = out[hOutput], newH.count == Self.stateSize,
              let newC = out[cOutput], newC.count == Self.stateSize else {
            throw OnnxModel.Failure(note: "Silero VAD gave an answer of the wrong size")
        }
        h = newH
        c = newC
        return p
    }
}

extension SileroVad: RoomVoiceDetecting {}
