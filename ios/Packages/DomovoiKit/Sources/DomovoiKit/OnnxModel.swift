import Foundation
@preconcurrency import OnnxRuntimeBindings

/// One ONNX model on ONNX Runtime, the app's only copy of it (Package.swift).
///
/// The iPhone twin of what `roommode/OrtModels.kt` does with
/// `OrtEnvironment` / `OrtSession` / `OnnxTensor`: open a model file with ONE
/// intra-op thread (a room listens all day, on old phones), feed it named
/// float or int64 tensors, read named float tensors back. Nothing here knows
/// what a wake word is; `OrtWakeNets` and `SileroVad` (R1) are built on it,
/// and so will the phonemizer's G2P model be.
///
/// **Not thread-safe, and not `Sendable`.** ONNX Runtime's sessions may be run
/// from several threads, but the room's networks keep state between calls
/// (Silero's h and c) and are fed by one listening thread, exactly as
/// Android's are; a model is created, used and dropped on that thread.
///
/// Shared by every unit that runs a model: changes go through the batch
/// coordinator, not a unit (ROOMMODE-MAP §6).
public final class OnnxModel {

    /// A tensor going in.
    public enum Input {
        case float([Float], shape: [Int])
        case int64([Int64], shape: [Int])
    }

    /// A model that would not load or run; `note` is ONNX Runtime's own words.
    public struct Failure: Error, Equatable, Sendable {
        public let note: String
        public init(note: String) { self.note = note }
    }

    /// ONNX Runtime's version string ("1.24.2"), or nil if the library has
    /// none to give. What the link test checks first.
    public static var runtimeVersion: String? { ORTVersion() }

    private let env: ORTEnv
    private let session: ORTSession

    /// The model's input names, in the model's order.
    public let inputNames: [String]
    /// The model's output names, in the model's order.
    public let outputNames: [String]

    /// Open the model at `url` (a local file) with `threads` intra-op threads.
    public init(contentsOf url: URL, threads: Int32 = 1) throws {
        do {
            env = try ORTEnv(loggingLevel: .warning)
            let options = try ORTSessionOptions()
            try options.setIntraOpNumThreads(threads)
            session = try ORTSession(env: env, modelPath: url.path, sessionOptions: options)
            inputNames = try session.inputNames()
            outputNames = try session.outputNames()
        } catch {
            throw Failure(note: "could not load \(url.lastPathComponent): \(error.localizedDescription)")
        }
    }

    /// Run the model on `inputs` and return the float contents of `outputs`
    /// (each flattened, row-major), keyed by name.
    public func run(_ inputs: [String: Input], outputs: [String]) throws -> [String: [Float]] {
        do {
            var values: [String: ORTValue] = [:]
            for (name, input) in inputs {
                values[name] = try Self.value(input)
            }
            let result = try session.run(withInputs: values, outputNames: Set(outputs), runOptions: nil)
            var out: [String: [Float]] = [:]
            for name in outputs {
                guard let value = result[name] else {
                    throw Failure(note: "the model gave no output \(name)")
                }
                out[name] = try Self.floats(value)
            }
            return out
        } catch let failure as Failure {
            throw failure
        } catch {
            throw Failure(note: error.localizedDescription)
        }
    }

    /// The shape ONNX Runtime reports for output `name` after a run, for the
    /// callers that size their arrays from it (openWakeWord's mel bins).
    public func runShaped(_ inputs: [String: Input], output name: String) throws -> (values: [Float], shape: [Int]) {
        do {
            var values: [String: ORTValue] = [:]
            for (key, input) in inputs {
                values[key] = try Self.value(input)
            }
            let result = try session.run(withInputs: values, outputNames: [name], runOptions: nil)
            guard let value = result[name] else { throw Failure(note: "the model gave no output \(name)") }
            let shape = try value.tensorTypeAndShapeInfo().shape.map(\.intValue)
            return (try Self.floats(value), shape)
        } catch let failure as Failure {
            throw failure
        } catch {
            throw Failure(note: error.localizedDescription)
        }
    }

    private static func value(_ input: Input) throws -> ORTValue {
        switch input {
        case .float(let values, let shape):
            let data = values.withUnsafeBufferPointer { NSMutableData(bytes: $0.baseAddress, length: $0.count * MemoryLayout<Float>.size) }
            return try ORTValue(tensorData: data, elementType: .float, shape: shape.map { NSNumber(value: $0) })
        case .int64(let values, let shape):
            let data = values.withUnsafeBufferPointer { NSMutableData(bytes: $0.baseAddress, length: $0.count * MemoryLayout<Int64>.size) }
            return try ORTValue(tensorData: data, elementType: .int64, shape: shape.map { NSNumber(value: $0) })
        }
    }

    private static func floats(_ value: ORTValue) throws -> [Float] {
        let info = try value.tensorTypeAndShapeInfo()
        guard info.elementType == .float else {
            throw Failure(note: "an output is not a float tensor")
        }
        let data = try value.tensorData() as Data
        return data.withUnsafeBytes { raw in
            Array(raw.bindMemory(to: Float.self))
        }
    }
}
