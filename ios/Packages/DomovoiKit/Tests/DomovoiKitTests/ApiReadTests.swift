import XCTest
@testable import DomovoiKit

/// The read hook: ``ApiRead``, the port of `rememberApi`.
///
/// `ApiHooks.kt` has no test on Android — it is Compose, and the JVM suite is
/// unit-only — so the five behaviours Home depends on are pinned here for the
/// first time on either phone.
@MainActor
final class ApiReadTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    /// A read over a fetch the test drives: it answers whatever `answer`
    /// holds, counts its calls, and can be made to park until released.
    private func read(
        base: Shared<String>,
        answer: Shared<Result<String, DomovoiError>>,
        calls: Shared<Int>,
        eventTypes: Set<String> = [],
        parkUntil: Shared<Bool>? = nil
    ) -> ApiRead<String> {
        ApiRead(base: { base.current }, eventTypes: eventTypes) {
            calls.mutate { $0 += 1 }
            if let parkUntil {
                for _ in 0..<settleYieldLimit {
                    if parkUntil.current { break }
                    await Task.yield()
                }
            }
            return try answer.current.get()
        }
    }

    /// 2: `loading` is only true while there has never been a value.
    func testLoadingIsOnlyTrueBeforeTheFirstValue() async {
        let base = Shared(server)
        let answer = Shared<Result<String, DomovoiError>>(.success("one"))
        let calls = Shared(0)
        let read = read(base: base, answer: answer, calls: calls)
        XCTAssertTrue(read.loading, "nothing has come back yet")
        XCTAssertFalse(read.answered)

        read.refresh()
        await yieldUntil { read.answered }
        XCTAssertEqual("one", read.data)
        XCTAssertFalse(read.loading)
        XCTAssertTrue(read.answered)

        answer.set(.success("two"))
        read.refresh()
        XCTAssertFalse(read.loading, "a refresh of a section that has rows shows no spinner")
        await yieldUntil { read.data == "two" }
    }

    /// 3: **a failure does not clear `data`** — the trap. A section that
    /// succeeded once and then fails keeps its last value, which is Android's
    /// behaviour and the one iOS had to decide rather than transcribe.
    /// ``ApiRead/isStale`` is what lets a view say so.
    func testAFailureKeepsTheLastValueAndSaysItIsStale() async {
        let base = Shared(server)
        let answer = Shared<Result<String, DomovoiError>>(.success("rooms: 3"))
        let calls = Shared(0)
        let read = read(base: base, answer: answer, calls: calls)
        read.refresh()
        await yieldUntil { read.answered }
        XCTAssertFalse(read.isStale)

        answer.set(.failure(.unreachable("offline?")))
        read.refresh()
        await yieldUntil { read.error != nil }
        XCTAssertEqual("rooms: 3", read.data, "stale and silent is the default failure, as on Android")
        XCTAssertTrue(read.isStale)
        XCTAssertTrue(read.answered)

        answer.set(.success("rooms: 4"))
        read.refresh()
        await yieldUntil { read.data == "rooms: 4" }
        XCTAssertNil(read.error, "a success clears it")
        XCTAssertFalse(read.isStale)
    }

    /// …and a read that has **never** loaded reports the failure plainly,
    /// which is the only state that can say it failed.
    func testAReadThatNeverLoadedReportsItsFailure() async {
        let base = Shared(server)
        let answer = Shared<Result<String, DomovoiError>>(.failure(.noServer))
        let calls = Shared(0)
        let read = read(base: base, answer: answer, calls: calls)
        read.refresh()
        await yieldUntil { read.answered }
        XCTAssertNil(read.data)
        XCTAssertNotNil(read.error)
        XCTAssertFalse(read.isStale, "nothing to be stale")
    }

    /// 4: two refreshes in quick succession mean **one** request's worth of
    /// published answer — the second cancels the first.
    func testARefreshCancelsTheOneInFlight() async {
        let base = Shared(server)
        let answer = Shared<Result<String, DomovoiError>>(.success("first"))
        let calls = Shared(0)
        let gate = Shared(false)
        let read = read(base: base, answer: answer, calls: calls, parkUntil: gate)

        read.refresh()
        await yieldUntil { calls.current == 1 }
        answer.set(.success("second"))
        read.refresh()
        await yieldUntil { calls.current == 2 }
        gate.set(true)
        await yieldUntil { read.data != nil }
        XCTAssertEqual("second", read.data, "the cancelled fetch does not publish over the live one")
    }

    /// 1: the server URL is an implicit key, and only a **change** re-reads.
    /// A caller that shouted "the server changed" on every redraw would
    /// otherwise re-read seven endpoints a frame.
    func testOnlyAServerChangeReReads() async {
        let base = Shared(server)
        let answer = Shared<Result<String, DomovoiError>>(.success("one"))
        let calls = Shared(0)
        let read = read(base: base, answer: answer, calls: calls)
        read.refresh()
        await yieldUntil { read.answered }
        XCTAssertEqual(1, calls.current)

        read.serverChanged()
        read.serverChanged()
        XCTAssertEqual(1, calls.current, "the address has not changed")

        base.set("http://10.0.0.77:6369")
        read.serverChanged()
        XCTAssertEqual(2, calls.current)
    }

    /// Only the named pushes re-read. Every consumer filters by `type`
    /// client-side, because the socket subscribes to everything.
    func testOnlyTheNamedPushesReRead() async {
        let base = Shared(server)
        let answer = Shared<Result<String, DomovoiError>>(.success("one"))
        let calls = Shared(0)
        let read = read(
            base: base,
            answer: answer,
            calls: calls,
            eventTypes: ["timers.changed", "timer_fires.changed"]
        )
        read.refresh()
        await yieldUntil { read.answered }

        read.received(StateEvent(type: "plugins.changed", frame: "{}"))
        read.received(StateEvent(type: "music.now_playing.changed", frame: "{}"))
        XCTAssertEqual(1, calls.current, "not ours")

        read.received(StateEvent(type: "timer_fires.changed", frame: "{}"))
        XCTAssertEqual(2, calls.current)
    }

    /// A read with no event types listens for nothing, which is what
    /// `/api/config` does (`HomeScreen.kt:134` registers none).
    func testAReadWithNoEventTypesIgnoresEveryPush() async {
        let base = Shared(server)
        let answer = Shared<Result<String, DomovoiError>>(.success("one"))
        let calls = Shared(0)
        let read = read(base: base, answer: answer, calls: calls)
        read.refresh()
        await yieldUntil { read.answered }
        read.received(StateEvent(type: "timers.changed", frame: "{}"))
        XCTAssertEqual(1, calls.current)
    }

    /// 5: `answered()` separates "has not come back yet" from "came back
    /// empty" — `HomeScreen.kt:106`, and the reason four sections can say
    /// they are checking rather than lying.
    func testAnsweredIsNotTheSameAsHavingData() async {
        let base = Shared(server)
        let answer = Shared<Result<String, DomovoiError>>(.failure(.unreachable("no")))
        let calls = Shared(0)
        let read = read(base: base, answer: answer, calls: calls)
        XCTAssertFalse(read.answered, "checking…")
        read.refresh()
        await yieldUntil { read.answered }
        XCTAssertNil(read.data)
        XCTAssertTrue(read.answered, "came back, with nothing")
    }

    /// The filter that reacts **without** re-reading: `OnStateEvents`. One
    /// push on Home is consumed this way, because every `/api/satellites`
    /// read opens an MPD connection per room.
    func testTheEventFilterPassesOnlyItsOwnTypes() {
        let seen = Shared<[String]>([])
        let filter = StateEventFilter(["satellites.wifi.changed"]) { event in
            seen.mutate { $0.append(event.type) }
        }
        filter.received(StateEvent(type: "timers.changed", frame: "{}"))
        filter.received(StateEvent(type: "satellites.wifi.changed", frame: "{}"))
        XCTAssertEqual(["satellites.wifi.changed"], seen.current)
    }

    /// Bounded, like `settle(for:what:)` — a condition that never comes is a
    /// failing test and never a hung suite.
    private func yieldUntil(
        _ done: @MainActor () -> Bool,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("the read never settled in \(settleYieldLimit) yields", file: file, line: line)
    }
}
