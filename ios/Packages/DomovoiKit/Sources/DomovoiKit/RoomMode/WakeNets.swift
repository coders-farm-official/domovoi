import Foundation

/// The three networks of openWakeWord's pipeline, each run on its own: the
/// melspectrogram model, Google's speech-embedding model and a wake-word
/// model (`roommode/WakeDetector.kt:10-24`, `WakeNets`). ``OrtWakeNets``
/// runs them on ONNX Runtime; the tests use fakes.
///
/// Not `Sendable`: the room's listening thread owns one at a time.
public protocol WakeNets: AnyObject {
    /// Raw melspectrogram of `samples` (int16 values as floats): frames x 32.
    func melspectrogram(_ samples: [Float]) throws -> [[Float]]

    /// One 96-value embedding of a 76 x 32 window of transformed mel frames.
    func embedding(_ window: [[Float]]) throws -> [Float]

    /// How many embeddings the wake model reads (16 for the stock models).
    var wakeInputFrames: Int { get }

    /// The wake word's score, 0...1, for the last ``wakeInputFrames`` embeddings.
    func wake(_ features: [[Float]]) throws -> Float
}
