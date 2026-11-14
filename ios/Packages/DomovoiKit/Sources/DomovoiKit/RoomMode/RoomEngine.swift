import Foundation

/// The room itself: a phone speaking the satellite protocol on
/// `/v1/stream/{room}` the way a Pi does — `roommode/RoomEngine.kt`.
///
/// **R2 PROTOCOL owns this file.** The base leaves only what R3's kiosk
/// builds and drives — ``live(store:ears:pcm:device:directory:acquireMic:micRefusal:)`` and
/// ``RoomEngineControl`` — with a body that starts nothing and says so;
/// R2 replaces the body, never the signatures (ROOMMODE-MAP §6.3).
public final class RoomEngine: RoomEngineControl, @unchecked Sendable {

    private let current = RoomLock(RoomStatus(link: .offline(reason: "room mode is not built into this version yet")))

    init() {}

    /// The real room: the household-token-free client, the room socket, the
    /// model and sound sync under `directory` (Application Support/room,
    /// excluded from backups), the speaker and the given microphone.
    ///
    /// `acquireMic` takes the phone's one microphone for room mode and
    /// returns its release, or nil with `micRefusal` saying why — R3 wires
    /// them to `PlayerMicArbiter` (`.roomMode`). Closures, not the arbiter by
    /// name, because this folder stays iOS-15-clean and the arbiter uses
    /// `Mutex` (ROOMMODE-MAP §6.0).
    public static func live(
        store: any RoomKeeping,
        ears: any RoomEarsLoading = OnnxRoomEarsLoader(),
        pcm: @escaping RoomPCMSourceMaking,
        device: any RoomDeviceFacts,
        directory: URL,
        acquireMic: @escaping @Sendable () -> (@Sendable () -> Void)?,
        micRefusal: @escaping @Sendable () -> String?
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
