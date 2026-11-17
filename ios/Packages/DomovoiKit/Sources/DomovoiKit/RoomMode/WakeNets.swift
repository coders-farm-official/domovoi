import Foundation

/// openWakeWord's shared front end: the melspectrogram model and Google's
/// speech-embedding model, run once per 80 ms chunk however many wake words
/// listen (1.4.0: a room may wake on up to five). This is the expensive
/// part (measured on a desktop: 1.79 ms per chunk, against 0.012-0.026 ms
/// per word's classifier; satellite/wake_words.py).
///
/// Not `Sendable`: the room's listening thread owns one at a time.
public protocol WakeFrontEnd: AnyObject {
    /// Raw melspectrogram of `samples` (int16 values as floats): frames x 32.
    func melspectrogram(_ samples: [Float]) throws -> [[Float]]

    /// One 96-value embedding of a 76 x 32 window of transformed mel frames.
    func embedding(_ window: [[Float]]) throws -> [Float]
}

/// One wake word's model: a small classifier over the front end's last
/// ``inputFrames`` embeddings (~1 MB each).
public protocol WakeClassifier: AnyObject {
    /// How many embeddings the model reads (16 for the stock models).
    var inputFrames: Int { get }

    /// The word's score, 0...1, for the last ``inputFrames`` embeddings.
    func score(_ features: [[Float]]) throws -> Float
}

/// The three networks of openWakeWord's pipeline for ONE wake word, each run
/// on its own: the front end's two and a wake-word model
/// (`roommode/WakeDetector.kt:10-24`, `WakeNets`). ``OrtWakeNets`` runs them
/// on ONNX Runtime; the tests use fakes. Several words share one
/// ``WakeFrontEnd`` and each add a ``WakeClassifier`` (``WakeDetector``).
///
/// Not `Sendable`: the room's listening thread owns one at a time.
public protocol WakeNets: WakeFrontEnd {
    /// How many embeddings the wake model reads (16 for the stock models).
    var wakeInputFrames: Int { get }

    /// The wake word's score, 0...1, for the last ``wakeInputFrames`` embeddings.
    func wake(_ features: [[Float]]) throws -> Float
}

/// A ``WakeNets``' own wake model, as one ``WakeClassifier`` of a detector.
final class WakeNetsClassifier: WakeClassifier {
    let nets: any WakeNets

    init(_ nets: any WakeNets) {
        self.nets = nets
    }

    var inputFrames: Int { nets.wakeInputFrames }

    func score(_ features: [[Float]]) throws -> Float { try nets.wake(features) }
}
