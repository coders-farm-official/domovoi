import XCTest
@testable import DomovoiKit

/// The checked paste: one attempt, never retried, and **saved on exactly one
/// branch**.
///
/// The twin of the `CheckedPairingField` half of
/// `android/.../test/ui/screens/settings/TokenCheckMessageTest.kt`. The
/// incident behind it (2026-10-17) was an old token saved unchecked, after
/// which every page failed.
@MainActor
final class PairingFieldModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let token = "household-abcdef"

    private func makeField(
        store: InMemoryShellStore,
        answer: TokenCheck,
        calls: Shared<Int> = Shared(0),
        cleared: Shared<Int> = Shared(0),
        saved: Shared<[String]> = Shared([]),
        clock: Shared<Int64> = Shared(0),
        ticks: Shared<Int> = Shared(0),
        tickAdvancesMs: Int64 = 0
    ) -> PairingFieldModel {
        PairingFieldModel(
            store: store,
            check: { _ in
                calls.mutate { $0 += 1 }
                return answer
            },
            clearPairingRequired: { cleared.mutate { $0 += 1 } },
            clock: { clock.current },
            tick: { _ in
                ticks.mutate { $0 += 1 }
                guard tickAdvancesMs > 0 else {
                    try await Task.sleep(for: .seconds(3600))
                    return
                }
                clock.advance(tickAdvancesMs)
            },
            onSaved: { candidate in saved.mutate { $0.append(candidate) } }
        )
    }

    // MARK: - the button

    /// Not "not blank": 12…128 characters, every one printable ASCII. A value
    /// the HTTP layer would refuse used to be saved unchecked and then throw,
    /// **with the token in the message**, on every later request.
    func testTheButtonIsLiveOnlyForATokenThatCouldBeSent() {
        let field = makeField(store: InMemoryShellStore(serverURL: server), answer: .accepted)
        XCTAssertFalse(field.canSubmit, "empty")
        field.typed = "short"
        XCTAssertFalse(field.canSubmit)
        XCTAssertEqual("at least 12 characters", field.hint)
        field.typed = "household-ab\u{0007}"
        XCTAssertFalse(field.canSubmit)
        XCTAssertEqual("letters, digits, punctuation and spaces only", field.hint)
        field.typed = token
        XCTAssertTrue(field.canSubmit)
        XCTAssertNil(field.hint, "nothing to say about a usable token")
    }

    /// A submit that cannot happen does not reach the network.
    func testAnUnusableTokenIsNeverSent() async {
        let calls = Shared<Int>(0)
        let field = makeField(store: InMemoryShellStore(serverURL: server), answer: .accepted, calls: calls)
        field.typed = "short"
        let answer = await field.submit()
        XCTAssertNil(answer)
        XCTAssertEqual(0, calls.current)
    }

    // MARK: - the four answers

    /// Accepted: saved, the pairing flag cleared, the field emptied.
    func testAnAcceptedTokenIsSavedAndTheFieldIsEmptied() async {
        let store = InMemoryShellStore(serverURL: server)
        let cleared = Shared<Int>(0)
        let saved = Shared<[String]>([])
        let field = makeField(store: store, answer: .accepted, cleared: cleared, saved: saved)
        field.typed = "  \(token)  "

        let answer = await field.submit()
        XCTAssertEqual(.accepted, answer)
        XCTAssertEqual(token, store.token(for: server), "trimmed, as the field submits it")
        XCTAssertEqual(1, cleared.current)
        XCTAssertEqual([token], saved.current)
        XCTAssertEqual("", field.typed)
        XCTAssertNil(field.message)
    }

    /// Refused: **not saved**, and the person is told where the right token
    /// is rather than being told to try again.
    func testARefusedTokenIsNotSavedAndSaysWhereTheRightOneIs() async {
        let store = InMemoryShellStore(serverURL: server)
        let saved = Shared<[String]>([])
        let field = makeField(store: store, answer: .refused, saved: saved)
        field.typed = token

        let answer = await field.submit()
        XCTAssertEqual(.refused, answer)
        XCTAssertNil(store.token(for: server))
        XCTAssertEqual([], saved.current)
        XCTAssertTrue(field.messageIsRefusal)
        XCTAssertEqual(
            "That token doesn't match this server. Copy it from Settings \u{2192} Phones & browsers "
                + "\u{2192} Household token on this server's dashboard.",
            field.message
        )
        XCTAssertEqual(token, field.typed, "and the paste is still there to be corrected")
    }

    /// A 503, or no answer at all: **not saved**, and never called wrong. The
    /// server gave no verdict, and telling a person their token is wrong
    /// while the box restarts is how a working token gets thrown away.
    func testAnUnavailableServerIsNotToldTheTokenIsWrong() async {
        let store = InMemoryShellStore(serverURL: server)
        let field = makeField(store: store, answer: .unavailable)
        field.typed = token

        let answer = await field.submit()
        XCTAssertEqual(.unavailable, answer)
        XCTAssertNil(store.token(for: server))
        XCTAssertFalse(field.messageIsRefusal)
        XCTAssertEqual("Couldn't check it with the server — try again.", field.message)
    }

    /// Throttled: not saved, the field off for the whole wait, and the wait
    /// **counted down in the message**.
    func testAThrottledPasteTurnsTheFieldOffForTheWholeWaitAndCountsItDown() async {
        let store = InMemoryShellStore(serverURL: server)
        let clock = Shared<Int64>(0)
        let ticks = Shared<Int>(0)
        let field = makeField(
            store: store,
            answer: .throttled(waitMs: 42_000),
            clock: clock,
            ticks: ticks,
            tickAdvancesMs: 1_000
        )
        field.typed = token
        let answer = await field.submit()
        XCTAssertEqual(.throttled(waitMs: 42_000), answer)

        XCTAssertNil(store.token(for: server))
        XCTAssertFalse(field.isEnabled, "the field and the button are off")
        XCTAssertFalse(field.canSubmit)
        XCTAssertEqual("Too many tries — wait 0:42 before the next one.", field.message)
        XCTAssertTrue(field.isCountingDown, "and something is actually counting")

        // Let the countdown run out on the fake clock. Bounded: the loop can
        // take at most 44 ticks by construction.
        for _ in 0..<settleYieldLimit where field.waitLeftMs > 0 { await Task.yield() }
        XCTAssertEqual(0, field.waitLeftMs)
        XCTAssertEqual("You can try again now.", field.message, "never 0:42 for ever, and never 0:00")
        XCTAssertTrue(field.isEnabled)
        field.stop()
    }

    /// **Never retried by itself.** A wrong paste costs one wrong token on
    /// the server's count, no more — it allows five per address before it
    /// starts making the phone wait.
    func testAWrongPasteCostsExactlyOneAttempt() async {
        let calls = Shared<Int>(0)
        let field = makeField(store: InMemoryShellStore(serverURL: server), answer: .refused, calls: calls)
        field.typed = token
        _ = await field.submit()
        XCTAssertEqual(1, calls.current)
        for _ in 0..<200 { await Task.yield() }
        XCTAssertEqual(1, calls.current, "nothing tried again on its own")
    }

    // MARK: - editing

    /// Editing clears the message — but **not while a wait is running**,
    /// because the wait is a fact about the server and not about what is in
    /// the field.
    func testEditingClearsTheMessageExceptWhileAWaitIsRunning() async {
        let clock = Shared<Int64>(0)
        let field = makeField(
            store: InMemoryShellStore(serverURL: server),
            answer: .refused,
            clock: clock
        )
        field.typed = token
        _ = await field.submit()
        XCTAssertNotNil(field.message)
        field.typed = token + "x"
        XCTAssertNil(field.message)

        let throttled = makeField(
            store: InMemoryShellStore(serverURL: server),
            answer: .throttled(waitMs: 42_000),
            clock: clock
        )
        throttled.typed = token
        _ = await throttled.submit()
        XCTAssertNotNil(throttled.message)
        throttled.typed = token + "x"
        XCTAssertEqual(
            "Too many tries — wait 0:42 before the next one.", throttled.message,
            "a wait does not go away because someone typed"
        )
        throttled.stop()
    }

    /// The button says `checking…` while it is in flight and the given word
    /// otherwise — `pair` on the pairing screen, `replace` in Settings,
    /// `pair again` on the trouble card.
    ///
    /// The check parks until this test lets it go, so "while it is in
    /// flight" is a moment the test is actually standing in rather than one
    /// it assumes. Both waits are bounded.
    func testTheButtonSaysCheckingWhileItIsInFlight() async {
        let arrived = Shared<Bool>(false)
        let release = Shared<Bool>(false)
        let field = PairingFieldModel(
            store: InMemoryShellStore(serverURL: server),
            label: ShellStrings.replace,
            check: { _ in
                arrived.set(true)
                for _ in 0..<settleYieldLimit where !release.current { await Task.yield() }
                return .accepted
            }
        )
        XCTAssertEqual(ShellStrings.replace, field.buttonLabel)
        field.typed = token

        let submitting = Task { await field.submit() }
        await settle(for: arrived, what: "the token check", reset: false)
        XCTAssertTrue(field.checking)
        XCTAssertEqual(ShellStrings.checking, field.buttonLabel)
        XCTAssertFalse(field.canSubmit, "and it cannot be tapped twice")
        XCTAssertFalse(field.isEnabled)

        release.set(true)
        let answer = await submitting.value
        XCTAssertEqual(.accepted, answer)
        XCTAssertEqual(ShellStrings.replace, field.buttonLabel, "and back again afterwards")
        XCTAssertFalse(field.checking)
    }
}
