import XCTest
@testable import DomovoiKit

/// The server book: who is trusted, what the app may be pointed at, and
/// **what forgetting a server deletes**.
///
/// The twin of the `Prefs` and `ServerCredentials` tests in
/// `android/.../data/`. Two of these are the batch's security rules rather
/// than its behaviour: ``TrustBook/setServerURL(_:)`` refusing an untrusted
/// server, and ``TrustBook/forget(_:)`` reaching exactly six things.
final class TrustBookTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let other = "http://10.0.0.7:6369"

    private var pin: ServerIdentity.Pin { TestIdentity.real.pin }
    private var rogue: ServerIdentity.Pin { TestIdentity.rogue.pin }

    // MARK: - the refusal (Prefs.kt:283)

    /// **The safety property, not a formality.** The picker trusts and then
    /// points; pointing first would be the app talking to a server nobody
    /// agreed to.
    func testTheAppCannotBePointedAtAServerNobodyTrusted() {
        var book = TrustBook()
        XCTAssertFalse(book.setServerURL(server))
        XCTAssertEqual("", book.serverURL, "nothing is written when the answer is no")

        book.trust(server, identity: pin)
        XCTAssertTrue(book.setServerURL(server))
        XCTAssertEqual(server, book.serverURL)
    }

    /// Blank is allowed through: that is how a forget leaves the app with no
    /// server at all, and `isTrusted("")` is false by design.
    func testABlankAddressIsAllowedBecauseThatIsWhatForgettingLeaves() {
        var book = TrustBook()
        book.trust(server)
        XCTAssertTrue(book.setServerURL(server))
        XCTAssertTrue(book.setServerURL(""))
        XCTAssertEqual("", book.serverURL)
    }

    /// The write order the picker uses, and why it is an order: trust, known,
    /// active, and only then the person's "use the server" choice.
    func testTheWriteOrderOnYesIsTrustThenKnownThenActive() {
        var book = TrustBook()
        book.trust(server, identity: pin)
        book.upsertKnown(url: server, name: "kitchen-box")
        XCTAssertTrue(book.setServerURL(server))
        XCTAssertEqual([server], book.trusted)
        XCTAssertEqual([KnownServer(url: server, name: "kitchen-box")], book.known)
        XCTAssertEqual(server, book.serverURL)
        XCTAssertEqual(pin, book.pin(forServer: server))
    }

    /// Switching servers drops trust that belongs to nothing — **and only the
    /// trust**. A harness-paired install whose server was never listed would
    /// otherwise be unpaired by the next switch (P2-at-01 review).
    func testSwitchingDropsOrphanTrustAndNothingElse() {
        var book = TrustBook()
        book.trust("http://10.0.0.99:6369", identity: pin)   // trusted, never listed
        book.setToken("household-99", for: "http://10.0.0.99:6369")
        book.trust(other)
        book.upsertKnown(url: other, name: nil)

        XCTAssertTrue(book.setServerURL(other))
        XCTAssertFalse(book.isTrusted("http://10.0.0.99:6369"), "orphan trust goes")
        XCTAssertEqual("household-99", book.token(for: "http://10.0.0.99:6369"), "its token stays")
        XCTAssertNotNil(book.pins[IdentityGate.pinKey("http://10.0.0.99:6369")!], "its pin stays")
    }

    /// The one being switched to is never pruned, however little else knows
    /// about it.
    func testTheServerBeingSwitchedToIsNeverPruned() {
        var book = TrustBook()
        book.trust(server)
        XCTAssertTrue(book.setServerURL(server))
        XCTAssertTrue(book.isTrusted(server))
    }

    // MARK: - the pin, which is not the trust (§4.4)

    /// A pin is per `scheme://host:port`; trust and the token are per saved
    /// spelling. So two spellings **share a pin** and have separate trust.
    func testTwoSpellingsShareAPinAndHaveSeparateTrust() {
        var book = TrustBook()
        book.trust("http://10.0.0.42:6369", identity: pin)
        XCTAssertEqual(pin, book.pin(forServer: "http://10.0.0.42:6369/"))
        XCTAssertFalse(
            book.isTrusted("HTTP://10.0.0.42:6369"),
            "normalize does not fold the scheme's case, so this is a different trust key"
        )
    }

    /// Trusting again never overwrites a pin: a pin changes only by
    /// forgetting the server or by the trust-again dialog's yes.
    func testTrustingAgainDoesNotOverwriteAPin() {
        var book = TrustBook()
        book.trust(server, identity: pin)
        book.trust(server, identity: rogue)
        XCTAssertEqual(pin, book.pin(forServer: server), "the first pin stands")
    }

    /// Finding S-T1: the yes drops the household token **before** it writes
    /// the new key, so the old token can never go to whatever holds it.
    func testTrustAgainDropsTheTokenBeforeItPinsTheNewKey() {
        var book = TrustBook()
        book.trust(server, identity: pin)
        book.setToken("household-abc", for: server)
        XCTAssertTrue(book.setServerURL(server))

        book.replacePin(url: server, pin: rogue)
        XCTAssertNil(book.token(for: server), "the old household's token is gone")
        XCTAssertEqual(rogue, book.pin(forServer: server))
        XCTAssertTrue(book.isTrusted(server), "the trust itself survives: the person said yes to it")
    }

    // MARK: - the token

    /// A blank token removes the entry: that is what unpairing means. And a
    /// new token is not the one the server refused.
    func testABlankTokenUnpairsAndClearsTheRefusal() {
        var book = TrustBook()
        book.trust(server)
        book.setToken("household-abc", for: server)
        book.rejected.insert(server)

        book.setToken(nil, for: server)
        XCTAssertNil(book.token(for: server))
        XCTAssertFalse(book.rejected.contains(server), "a token that is gone is not the refused one")

        book.rejected.insert(server)
        book.setToken("household-xyz", for: server)
        XCTAssertFalse(book.rejected.contains(server), "nor is a new one")
    }

    // MARK: - the cascade (§6.4), which deletes things

    /// **What a forget reaches, named one by one.** A cascade that takes one
    /// thing too many is a data-loss bug, so this test lists the six and then
    /// the next one lists what a forget of a *different* spelling must leave
    /// alone.
    func testForgettingAServerReachesExactlySixThings() {
        var book = TrustBook()
        book.trust(server, identity: pin)
        book.upsertKnown(url: server, name: "kitchen-box")
        book.setToken("household-abc", for: server)
        book.rejected.insert(server)
        book.sharedScreens[server] = true
        book.owners[server] = "7"
        XCTAssertTrue(book.setServerURL(server))

        // Another server's everything, to prove the cascade is about one.
        book.trust(other, identity: rogue)
        book.upsertKnown(url: other, name: "study")
        book.setToken("household-other", for: other)
        book.sharedScreens[other] = false
        book.owners[other] = "9"

        let reach = book.forget(server)
        XCTAssertEqual(
            TrustBook.ForgetReach(
                row: true, trust: true, token: true, rejection: true,
                pin: true, sharedScreenAnswer: true, owner: true
            ),
            reach
        )
        XCTAssertEqual([], book.known.filter { $0.url == server })
        XCTAssertFalse(book.isTrusted(server))
        XCTAssertNil(book.token(for: server))
        XCTAssertNil(book.pin(forServer: server))
        XCTAssertNil(book.sharedScreens[server])
        XCTAssertNil(book.owners[server])

        // What survives, which is the half that matters.
        XCTAssertTrue(book.isTrusted(other))
        XCTAssertEqual("household-other", book.token(for: other))
        XCTAssertEqual(rogue, book.pin(forServer: other))
        XCTAssertEqual(false, book.sharedScreens[other])
        XCTAssertEqual("9", book.owners[other])
        XCTAssertEqual(1, book.known.count)
    }

    /// The pin is the one conditional in the cascade: forgetting a spelling
    /// that is **not** the active server must not clear the active server's
    /// pin, which would reopen trust-on-first-use for it on the next network
    /// (P2-at-01 review).
    func testForgettingAnotherSpellingKeepsTheActiveServersPin() {
        var book = TrustBook()
        let slashed = "http://10.0.0.42:6369/"
        book.trust(server, identity: pin)
        book.upsertKnown(url: server, name: nil)
        XCTAssertTrue(book.setServerURL(server))
        // The same server, saved a second time with a trailing slash — which
        // normalises to the same thing, so build the drift by hand the way a
        // pre-trust-list install or a harness write would.
        book.trusted.insert(slashed)
        book.known.append(KnownServer(url: slashed, name: nil))
        book.tokens[slashed] = "household-slashed"

        let reach = book.forget(slashed)
        XCTAssertFalse(reach.pin, "the pin is shared with the active spelling, so it stays")
        XCTAssertEqual(pin, book.pin(forServer: server))
        XCTAssertNil(book.tokens[slashed], "that spelling's own token goes")
        XCTAssertEqual(server, book.serverURL)
    }

    /// Forgetting the **active** server clears its pin, and the order inside
    /// ``TrustBook/forgetActiveServer()`` is what makes that true: the
    /// address is cleared first, so ``TrustBook/clearsPin(of:active:)``
    /// answers yes. Reversing the two lines leaves the pin behind.
    func testForgettingTheActiveServerClearsItsPinAndLeavesNoServer() {
        var book = TrustBook()
        book.trust(server, identity: pin)
        book.upsertKnown(url: server, name: nil)
        book.setToken("household-abc", for: server)
        XCTAssertTrue(book.setServerURL(server))

        let forgotten = book.forgetActiveServer()
        XCTAssertEqual(server, forgotten?.url)
        XCTAssertTrue(forgotten?.reach.pin == true)
        XCTAssertEqual("", book.serverURL)
        XCTAssertNil(book.pin(forServer: server))
        XCTAssertNil(book.token(for: server))
        XCTAssertFalse(book.isTrusted(server))
    }

    func testForgettingTheActiveServerWhenThereIsNoneAnswersNil() {
        var book = TrustBook()
        XCTAssertNil(book.forgetActiveServer())
    }

    /// A known row keeps its name when a later sighting did not resolve one,
    /// and the row moves to the end (Android's `kept + KnownServer(...)`).
    func testUpsertKeepsANameASightingDidNotResolve() {
        var book = TrustBook()
        book.upsertKnown(url: server, name: "kitchen-box")
        book.upsertKnown(url: other, name: nil)
        book.upsertKnown(url: server, name: nil)
        XCTAssertEqual([other, server], book.known.map(\.url))
        XCTAssertEqual("kitchen-box", book.known.last?.name)
    }

    /// The store delegates whole, so the two rules are not re-implemented
    /// behind a protocol.
    func testTheStoreDelegatesTheRefusalAndTheCascade() {
        let store = InMemoryShellStore()
        XCTAssertFalse(store.setServerURL(server), "the store refuses an untrusted server too")
        store.trustServer(server, identity: pin)
        store.upsertKnownServer(url: server, name: "kitchen-box")
        XCTAssertTrue(store.setServerURL(server))
        store.setToken("household-abc", for: server)

        XCTAssertEqual(server, store.forgetActiveServer())
        XCTAssertEqual("", store.serverURL)
        XCTAssertNil(store.token(for: server))
        XCTAssertNil(store.pin(forServer: server))
        XCTAssertEqual([], store.knownServers)
        XCTAssertEqual([], store.trustedServers)
    }

    /// The rig's own default, stated as a test so a later batch does not
    /// quietly change it: a store built with a server has trusted it, because
    /// an install cannot otherwise hold one.
    func testAStoreBuiltWithAServerHasTrustedIt() {
        XCTAssertTrue(InMemoryShellStore(serverURL: server).isTrusted(server))
        XCTAssertFalse(
            InMemoryShellStore(serverURL: server, trusted: false).isTrusted(server),
            "and the one install that can hold an untrusted address is buildable on purpose"
        )
    }
}
