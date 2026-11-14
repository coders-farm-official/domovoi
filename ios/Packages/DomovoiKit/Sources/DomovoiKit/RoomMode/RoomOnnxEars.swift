import Foundation

/// The room's ears on ONNX Runtime: openWakeWord's three networks
/// (`OrtWakeNets`) under `WakeDetector`, and Silero (`SileroVad`), behind
/// ``RoomEars`` — `roommode/OrtModels.kt`, `WakeDetector.kt` and
/// `RoomEngine.kt:695-718,1047-1053`.
///
/// **R1 ENGINE-ML owns this file** (and adds WakeNets.swift,
/// WakeDetector.swift, OrtWakeNets.swift, SileroVad.swift beside it). The
/// base leaves the loader R2's engine defaults to; until R1 lands it refuses,
/// which the room shows as "could not load the wake word" — never a room
/// that claims to listen.
public struct OnnxRoomEarsLoader: RoomEarsLoading {
    public init() {}

    public func load(_ files: RoomModelFiles) throws -> any RoomEars {
        throw OnnxModel.Failure(note: "the wake word is not built into this version yet")
    }
}
