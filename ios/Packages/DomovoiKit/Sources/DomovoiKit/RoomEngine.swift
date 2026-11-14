import Foundation
import Synchronization

/// The room itself: a phone speaking the satellite protocol on
/// `/v1/stream/{room}` the way a Pi does — `roommode/RoomEngine.kt`.
///
/// **R2 PROTOCOL owns this file.** The base leaves only what R3's kiosk
/// builds and drives — ``live(store:ears:pcm:device:directory:)`` and
/// ``RoomEngineControl`` — with a body that starts nothing and says so;
/// R2 replaces the body, never the signatures (ROOMMODE-MAP §6.3).
public final class RoomEngine: RoomEngineControl, @unchecked Sendable {

    private let current = Mutex(RoomStatus(link: .offline(reason: "room mode is not built into this version yet")))

    init() {}

    /// The real room: the household-token-free client, the room socket, the
    /// model and sound sync under `directory` (Application Support/room,
    /// excluded from backups), the speaker and the given microphone.
    public static func live(
        store: any RoomKeeping,
        ears: any RoomEarsLoading = OnnxRoomEarsLoader(),
        pcm: @escaping RoomPCMSourceMaking,
        device: any RoomDeviceFacts,
        directory: URL
    ) -> RoomEngine {
        RoomEngine()
    }

    public var status: RoomStatus { current.withLock { $0 } }

    public func statusUpdates() -> AsyncStream<RoomStatus> {
        let now = status
        return AsyncStream { continuation in
            continuation.yield(now)
            continuation.finish()
        }
    }

    @discardableResult public func start() -> Bool { false }
    public func stop() {}
    public func setMuted(_ on: Bool) { current.withLock { $0.muted = on } }
    public func retryNow() {}
}
