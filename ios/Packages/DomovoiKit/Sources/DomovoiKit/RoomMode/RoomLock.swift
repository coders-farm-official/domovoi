import Foundation

/// A value behind a lock, with `Mutex`'s `withLock` shape, for room mode.
///
/// **Why not `Mutex`.** Room mode's code is kept iOS-15-clean
/// (ROOMMODE-MAP §6.0): the owner may later ship it in a low-floor package so
/// iPhone 6s-X phones can be rooms, and `Synchronization.Mutex` is iOS 18.
/// `NSLock` is on every iOS; its own `withLock` is iOS 16, so this calls
/// `lock()` / `unlock()` itself. `RoomModeSourcesTests` keeps `Mutex` out of
/// this folder.
public final class RoomLock<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Value

    public init(_ value: Value) {
        self.value = value
    }

    /// Runs `body` with the value, holding the lock. Never call back into
    /// the same lock from `body`: `NSLock` is not recursive.
    public func withLock<Result>(_ body: (inout Value) throws -> Result) rethrows -> Result {
        lock.lock()
        defer { lock.unlock() }
        return try body(&value)
    }
}
