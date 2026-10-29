import Synchronization

/// A value several `@Sendable` closures share, as a reference.
///
/// `Synchronization.Mutex` is the right tool for this and the tests use it
/// directly where they can, but it is non-copyable: it cannot be passed to a
/// function without an ownership annotation, and `let copy = someMutex`
/// does not compile. A test that wants to hand "the clock" or "the token this
/// phone holds" to a client, a dispatcher and its own assertions needs
/// something it can pass around, so it passes one of these.
final class Shared<Value: Sendable>: Sendable {
    private let value: Mutex<Value>

    init(_ initial: Value) {
        value = Mutex(initial)
    }

    var current: Value {
        value.withLock { $0 }
    }

    func set(_ new: Value) {
        value.withLock { $0 = new }
    }

    @discardableResult
    func mutate<Result>(_ body: (inout Value) -> Result) -> Result {
        value.withLock(body)
    }
}

extension Shared where Value == Int64 {
    func advance(_ by: Int64) {
        mutate { $0 += by }
    }
}
