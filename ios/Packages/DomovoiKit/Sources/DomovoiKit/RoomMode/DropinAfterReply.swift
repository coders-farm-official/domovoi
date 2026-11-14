import Foundation

/// A drop-in whose `dropin_start` says `await_ready` (a core from 2026-11-12,
/// to a room whose hello declared `dropin_ready`): the Pi's
/// `_dropin_start_after_reply`, as `roommode/DropinAfterReply.kt` ports it
/// (220085d8).
///
/// Entering the call at once cut off the reply that announced it ("Okay,
/// connecting you with the kitchen."). So the call opens once that reply has
/// played out, at most ``capMS`` later, and then `dropin_ready` tells the
/// core to send the start chime and the other room's audio (it holds them
/// until then, with a 10 s backstop of its own). A `dropin_end`, a newer
/// `dropin_start` or a disconnect while waiting drops the waiting call
/// (``cancel()``, or a newer ``start(awaitReady:enter:)``).
///
/// Without `await_ready` the call opens at once, as before, and no
/// `dropin_ready` is sent.
///
/// The engine holds this object's lock (``exclusive(_:)``) around every
/// change to its call state, so a call that opens here and a `dropin_end` on
/// the socket's thread never interleave. The lock is recursive, as Kotlin's
/// `synchronized` is: the engine calls ``cancel()`` while holding it.
public final class DropinAfterReply: @unchecked Sendable {

    public enum Outcome: Equatable, Sendable { case playedOut, capped, cancelled }

    /// The longest a call waits for its announcement (the core's backstop is
    /// 10 s).
    public static let capMS: Int64 = 8_000
    public static let pollMS: Int64 = 50

    private let stillPlaying: @Sendable () -> Bool
    private let sendReady: @Sendable () -> Void
    private let now: @Sendable () -> Int64
    private let launch: @Sendable (@escaping @Sendable () -> Void) -> Void
    private let pause: @Sendable (Int64) -> Void

    private let lock = NSRecursiveLock()
    private var seq = 0
    private var pending: Int?

    /// - Parameters:
    ///   - stillPlaying: a reply is still coming out of the speaker (or still
    ///     arriving).
    ///   - sendReady: says `dropin_ready` to the core.
    ///   - now: monotonic milliseconds.
    ///   - launch: runs the wait off the caller's thread (the socket's).
    ///   - pause: blocks the waiting thread for that many milliseconds.
    public init(
        stillPlaying: @escaping @Sendable () -> Bool,
        sendReady: @escaping @Sendable () -> Void,
        now: @escaping @Sendable () -> Int64,
        launch: @escaping @Sendable (@escaping @Sendable () -> Void) -> Void,
        pause: @escaping @Sendable (Int64) -> Void
    ) {
        self.stillPlaying = stillPlaying
        self.sendReady = sendReady
        self.now = now
        self.launch = launch
        self.pause = pause
    }

    /// Runs `body` holding this object's (recursive) lock.
    public func exclusive<T>(_ body: () throws -> T) rethrows -> T {
        lock.lock()
        defer { lock.unlock() }
        return try body()
    }

    /// A call is waiting for its announcement to finish.
    public var waiting: Bool { exclusive { pending != nil } }

    /// A `dropin_start` the engine accepted. `enter` opens the call (under
    /// this object's lock); with `awaitReady` it runs once the reply has
    /// played out and is followed by `dropin_ready`. Returns the call's
    /// number (0 when it opened at once without waiting).
    @discardableResult
    public func start(awaitReady: Bool, enter: @escaping @Sendable () -> Void) -> Int {
        if !awaitReady {
            exclusive {
                pending = nil // a newer start drops one still waiting
                enter()
            }
            return 0
        }
        let n: Int = exclusive {
            seq += 1
            pending = seq
            return seq
        }
        if !stillPlaying() {
            open(n, enter: enter)
            return n
        }
        launch { [self] in
            if waitForReply(n) != .cancelled { open(n, enter: enter) }
        }
        return n
    }

    /// A `dropin_end` or a disconnect: the waiting call (if any) is gone.
    public func cancel() {
        exclusive { pending = nil }
    }

    /// Waits for the reply to play out (or the cap) while call `n` is the one
    /// waiting. Blocking.
    public func waitForReply(_ n: Int) -> Outcome {
        let deadline = now() + Self.capMS
        while stillPlaying() {
            if !isPending(n) { return .cancelled }
            if now() >= deadline { return .capped }
            pause(Self.pollMS)
        }
        return isPending(n) ? .playedOut : .cancelled
    }

    /// Opens call `n` unless it was dropped meanwhile, then `dropin_ready`.
    @discardableResult
    public func open(_ n: Int, enter: () -> Void) -> Bool {
        exclusive {
            guard pending == n else { return false }
            pending = nil
            enter()
            sendReady()
            return true
        }
    }

    private func isPending(_ n: Int) -> Bool { exclusive { pending == n } }
}
