import Synchronization

/// While this phone is a room, the household side is shut: the household
/// device token goes on NO request.
///
/// A port of `android/.../net/HouseholdLock.kt`, which is seventeen lines and
/// one `@Volatile Boolean`. ``DomovoiClient`` asks here before it puts the
/// token on anything — the first request of a call, every redirect hop, the
/// paste check, and the token it hands to a download — so a phone that is
/// also a household device keeps its token sealed and unused until the person
/// leaves room mode with the phone's unlock.
///
/// **Nothing sets it yet.** Room mode is I7; when it lands, the room's own
/// store sets this the moment room mode starts or ends, and the app sets it
/// from that store at launch before anything can make a request — which is
/// what Android's `DomovoiApplication.onCreate` and `RoomStore.setActive` do,
/// and what `HouseholdLockTest` holds them to. The check is wired now so that
/// batch has only one line to write.
///
/// **Why a `Mutex` and not an actor.** The brief asked for an actor-isolated
/// flag, and for the token decision alone an actor would do: every call that
/// starts a request is already async. The redirect decision is not. It runs
/// inside `urlSession(_:task:willPerformHTTPRedirection:newRequest:completionHandler:)`,
/// a synchronous platform callback that cannot `await`, so an actor would
/// force the flag to be sampled once per call and reused for every hop of
/// that call's redirect chain. Android re-reads its volatile flag on every
/// hop, and "room mode started while a redirect was in flight" is exactly the
/// case where the difference matters. `Synchronization.Mutex` (iOS 18 /
/// macOS 15, which are this package's floors) is a `Sendable` box that reads
/// synchronously, so the Swift reads the flag where the Kotlin reads it.
public final class HouseholdLock: Sendable {

    /// The process-wide one, as Android's `object HouseholdLock` is. The
    /// client takes a lock rather than reaching for this, so a test can give
    /// it one of its own.
    public static let shared = HouseholdLock()

    private let active = Mutex<Bool>(false)

    public init(roomModeActive: Bool = false) {
        active.withLock { $0 = roomModeActive }
    }

    public var roomModeActive: Bool {
        active.withLock { $0 }
    }

    public func setRoomModeActive(_ value: Bool) {
        active.withLock { $0 = value }
    }
}
