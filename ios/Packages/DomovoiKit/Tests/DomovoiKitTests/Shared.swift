import Foundation

/// A value several `@Sendable` closures share, as a reference.
///
/// `Synchronization.Mutex` is what the library itself uses and the right tool
/// for this, but it is non-copyable: it cannot be handed to a function
/// without an ownership annotation, `let copy = someMutex` does not compile,
/// and `withLock` wants a `(inout sending Value) -> sending Result` that a
/// generic pass-through cannot satisfy. A test that needs to hand "the clock"
/// or "the token this phone holds" to a client, to a stub's dispatcher and to
/// its own assertions needs something it can pass around, so it passes one of
/// these.
///
/// `@unchecked Sendable` with the reason spelt out, as the house rule asks:
/// the one stored property is read and written only inside `lock`, and the
/// lock is never held across a suspension because none of these methods is
/// async. Nothing else reaches the value.
final class Shared<Value: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Value

    init(_ initial: Value) {
        value = initial
    }

    var current: Value {
        lock.withLock { value }
    }

    func set(_ new: Value) {
        lock.withLock { value = new }
    }

    @discardableResult
    func mutate<Result>(_ body: (inout Value) -> Result) -> Result {
        lock.withLock { body(&value) }
    }
}

extension Shared where Value == Int64 {
    func advance(_ by: Int64) {
        mutate { $0 += by }
    }
}
