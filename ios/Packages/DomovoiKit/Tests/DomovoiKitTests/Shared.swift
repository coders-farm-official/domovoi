import Foundation
import XCTest

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

/// How many `Task.yield()`s a `settle` waits before it gives up. Enough that
/// a loop on the cooperative pool has every chance to run, and small enough
/// that giving up takes no time at all.
let settleYieldLimit = 20_000

/// Wait for `flag`, and **fail rather than hang** when it never comes.
///
/// Every `settle()` in these tests used to be `while !parked.current { await
/// Task.yield() }`, which turns a wiring that never parks into a suite that
/// hangs instead of a test that fails. That is not hypothetical: it left a
/// 3 h 34 m orphaned `xctest` on the shared Mac, which someone had to find
/// and kill. `ShellModelTests.Rig.settleVerdict` was already bounded and said
/// why; this is that bound, in one place, for everything else.
///
/// - Parameters:
///   - flag: the thing that is set when whatever is being waited for happened.
///   - what: named in the failure, so the message says what never parked.
///   - reset: whether to put the flag back to `false` on the way out, which is
///     what a rig that settles repeatedly wants.
@discardableResult
func settle(
    for flag: Shared<Bool>,
    what: String,
    reset: Bool = true,
    file: StaticString = #filePath,
    line: UInt = #line
) async -> Bool {
    for _ in 0..<settleYieldLimit {
        if flag.current {
            if reset { flag.set(false) }
            return true
        }
        await Task.yield()
    }
    XCTFail("\(what) never parked: nothing set the flag in \(settleYieldLimit) yields", file: file, line: line)
    return false
}

/// One request held open at the server, so a test can make something happen
/// **inside** a round trip rather than after it.
///
/// Moved here from `ShellModelSocketTests` on 2026-10-29, where it was
/// private, because the shape it tests — **a value read after an await that
/// should have been captured before it** — has now come up three times on
/// this track, and the only honest test of it needs the switch to happen
/// inside the round trip. A second copy of this helper would be the second
/// mechanism that nobody reads.
final class ParkedRequest: Sendable {

    /// Set when the request reached the stub, so a test waits on the park
    /// itself rather than on `StubURLProtocol.recorded`.
    let arrived = Shared<Bool>(false)

    let release = Shared<Bool>(false)

    /// Set when the park hit its bound instead of being released — which
    /// means the request timed out and whatever the test asserted afterwards
    /// was asserted over an answer that never came. **Assert this is false**:
    /// a park that outlasts the session timeout turns the test it serves into
    /// one that cannot fail, which is what happened on 2026-10-29 when
    /// `StubURLProtocol` still called its dispatcher inside its own lock.
    let ranOut = Shared<Bool>(false)
}

/// A dispatcher that holds `path` open until the park is released.
///
/// It blocks one `URLSession` worker thread and nothing else — a
/// `URLProtocol`'s `startLoading` is synchronous, so there is nowhere else to
/// hold it. **Bounded**, like every wait in this suite, and the bound is
/// **under** the session's own `timeoutIntervalForRequest` (1 s in these
/// rigs): a wiring that never releases fails the test rather than leaving an
/// orphaned `xctest` on the shared Mac, which an unbounded spin loop once did
/// for three and a half hours.
func parkRequest(
    to path: String,
    answering answer: @Sendable @escaping () -> StubURLProtocol.Answer,
    otherwise: @Sendable @escaping (URLRequest) -> StubURLProtocol.Answer = { _ in .json("{}") }
) -> ParkedRequest {
    let park = ParkedRequest()
    StubURLProtocol.dispatch { request in
        guard request.url?.path == path else { return otherwise(request) }
        park.arrived.set(true)
        for step in 0..<400 {
            if park.release.current { return answer() }
            if step == 399 { park.ranOut.set(true) }
            Thread.sleep(forTimeInterval: 0.002)
        }
        return answer()
    }
    return park
}
