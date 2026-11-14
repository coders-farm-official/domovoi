import XCTest
@testable import DomovoiKit

/// The store that remembers, and **where** it puts each thing.
///
/// Every test here is about one of three claims, because those are the three
/// things that were wrong or missing before it existed:
///
///  1. a second store over the same two places answers what the first one was
///     told — which is "the app does not forget the house when it is
///     force-quit", and is what the owner hit within a minute of the first
///     install;
///  2. the household token is in the Keychain and **not** in the plist, and
///     it leaves the Keychain when the person forgets the server;
///  3. a saved blob this build does not fully understand still gives the
///     person their server back.
///
/// A ``SavedStateBox`` and an ``InMemoryKeychain`` stand in for
/// `UserDefaults` and the real Keychain. Neither substitution is a weakening
/// of what is claimed: both are byte-for-byte the same interface, and the
/// real Keychain cannot be reached from `swift test` on a Mac at all
/// (``TokenVault``).
final class SavedShellStoreTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let other = "http://10.0.0.7:6369"
    private let token = "household-token-abcdef"

    /// Two places and the two stores that share them, so a test can say
    /// "quit the app and start it again" in one line.
    private struct Install {
        let place = SavedStateBox()
        let keychain = InMemoryKeychain()

        var vault: TokenVault { TokenVault(items: keychain) }

        /// A launch: a brand-new store over the state the last one left.
        func launch() -> SavedShellStore {
            SavedShellStore(place: place, vault: vault)
        }
    }

    // MARK: - 1. it comes back

    /// The whole of what the shell reads, written by one launch and read by
    /// the next.
    ///
    /// One test over every field rather than one per field, because the
    /// failure this is really about is **a field that nobody added to the
    /// saved shape** — and that failure looks like one assertion in a list of
    /// nine, which is exactly how it should look.
    func testASecondLaunchKnowsEverythingTheFirstOneWasTold() {
        let install = Install()

        let first = install.launch()
        first.trustServer(server, identity: TestIdentity.real.pin)
        first.upsertKnownServer(url: server, name: "kitchen-box")
        first.upsertKnownServer(url: other, name: nil)
        XCTAssertTrue(first.setServerURL(server))
        first.setToken(token, for: server)
        first.setPreferLocal(true)
        first.setPhoneBar(#"["home","music"]"#)
        first.markTokenRejected(other)
        first.noteLocalNetworkAnswered()

        let second = install.launch()
        XCTAssertEqual(server, second.serverURL)
        XCTAssertTrue(second.preferLocal)
        XCTAssertEqual(#"["home","music"]"#, second.phoneBar)
        XCTAssertTrue(second.isTrusted(server))
        XCTAssertEqual([server], Array(second.trustedServers))
        // In saved order, with the name that was resolved and the nil that
        // was not — the picker lists them in this order.
        XCTAssertEqual([server, other], second.knownServers.map(\.url))
        XCTAssertEqual(["kitchen-box", nil], second.knownServers.map(\.name))
        XCTAssertEqual(TestIdentity.real.pin, second.pin(forServer: server))
        XCTAssertTrue(second.isTokenRejected(other))
        XCTAssertTrue(second.localNetworkHasAnswered)
        XCTAssertEqual(token, second.token(for: server))
    }

    /// Nothing saved is a new install: blank, and not a crash.
    func testAnEmptyPlaceIsAFreshInstall() {
        let store = Install().launch()
        XCTAssertEqual("", store.serverURL)
        XCTAssertFalse(store.preferLocal)
        XCTAssertNil(store.phoneBar)
        XCTAssertTrue(store.knownServers.isEmpty)
        XCTAssertTrue(store.trustedServers.isEmpty)
        XCTAssertFalse(store.localNetworkHasAnswered)
    }

    /// A refused write is not saved, because it did not happen: the book
    /// refuses an address that is not trusted, and a store that persisted
    /// anyway would hand the next launch a server the app cannot reach
    /// through its own picker.
    func testAnUntrustedAddressIsNeitherWrittenNorSaved() {
        let install = Install()
        let first = install.launch()
        XCTAssertFalse(first.setServerURL(server), "the book has to refuse this")
        XCTAssertEqual("", first.serverURL)
        XCTAssertEqual("", install.launch().serverURL)
    }

    /// The selection hook, over the saved store: the fix that made picking a
    /// server do anything at all has to hold for the store the app actually
    /// builds, and `5a57dea9` only pinned it for the in-memory one.
    ///
    /// The sink **re-enters the store**, which is the half that deadlocks if
    /// it is ever fired under the lock — and with a `persist()` between the
    /// write and the sink there is more code in there to get that wrong.
    func testTheSelectionHookFiresForEveryWriteThatMovesThePhone() {
        let install = Install()
        let store = install.launch()
        store.trustServer(server, identity: nil)

        let seen = Shared<[String]>([])
        store.notifySelectionChanges {
            // Reading the store from inside its own sink: this is what the
            // shell's sink does, through `AppContainer.shellInputs()`.
            seen.mutate { $0.append("\(store.serverURL)|\(store.preferLocal)") }
        }

        XCTAssertTrue(store.setServerURL(server))
        store.setPreferLocal(true)
        XCTAssertEqual(server, store.forgetActiveServer())

        XCTAssertEqual(
            ["\(server)|false", "\(server)|true", "|true"],
            seen.current,
            "a write that moves this phone did not reach the shell, or reached it early"
        )
    }

    /// A write that does not move the phone does not pretend to.
    func testTheSelectionHookIsSilentForAWriteThatMovesNothing() {
        let install = Install()
        let store = install.launch()
        let fired = Shared<Int>(0)
        store.notifySelectionChanges { fired.mutate { $0 += 1 } }

        store.trustServer(server, identity: nil)
        store.upsertKnownServer(url: server, name: "kitchen-box")
        store.setToken(token, for: server)
        store.markTokenRejected(server)
        store.setPhoneBar(nil)
        store.noteLocalNetworkAnswered()
        XCTAssertFalse(store.setServerURL("http://10.0.0.99:6369"), "not trusted, so not written")

        XCTAssertEqual(0, fired.current)
    }

    /// Set once, never cleared, and saved the first time.
    func testTheLocalNetworkAnswerIsSavedAndNeverGoesBack() {
        let install = Install()
        let first = install.launch()
        first.noteLocalNetworkAnswered()
        XCTAssertTrue(first.localNetworkHasAnswered)

        let second = install.launch()
        XCTAssertTrue(second.localNetworkHasAnswered)
        // Nothing clears it, including the one write that clears most things.
        second.trustServer(server, identity: nil)
        _ = second.setServerURL(server)
        second.removeKnownServer(server)
        XCTAssertTrue(second.localNetworkHasAnswered)
        XCTAssertTrue(install.launch().localNetworkHasAnswered)
    }

    // MARK: - 2. the token is in the Keychain, and only there

    /// **The claim this test exists for**: the plist does not contain the
    /// household token, as bytes.
    ///
    /// Asserted against the saved bytes rather than against an answer,
    /// because that is the only place the claim can be read. It holds because
    /// ``TrustBook``'s coding keys leave `tokens` out — a property of the
    /// type, not a rule this store remembers to follow.
    func testTheHouseholdTokenIsInTheKeychainAndNotInThePlist() throws {
        let install = Install()
        let store = install.launch()
        store.trustServer(server, identity: nil)
        store.upsertKnownServer(url: server, name: "kitchen-box")
        XCTAssertTrue(store.setServerURL(server))
        store.setToken(token, for: server)

        let saved = try XCTUnwrap(install.place.saved)
        let text = try XCTUnwrap(String(data: saved, encoding: .utf8))
        XCTAssertFalse(text.contains(token), "the household token is in the plist: \(text)")
        // The address is not a secret and is in there. Matched without the
        // scheme on purpose: whether a JSON writer escapes `/` as `\/` is its
        // business, and a test that cared would be about Foundation.
        XCTAssertTrue(text.contains("10.0.0.42:6369"), "the address did not get saved: \(text)")

        // Where it is instead, under the normalised address.
        XCTAssertEqual([server], Array(install.keychain.accounts))
        XCTAssertEqual(token, install.vault.token(for: server))
    }

    /// Forgetting a server removes the credential. The row going is not
    /// enough: this store cannot enumerate the Keychain, so an item left
    /// behind is one nothing would ever read and nothing would ever delete.
    func testForgettingAServerTakesItsTokenOutOfTheKeychain() {
        let install = Install()
        let store = install.launch()
        for url in [server, other] {
            store.trustServer(url, identity: nil)
            store.upsertKnownServer(url: url, name: nil)
            store.setToken(token, for: url)
        }
        XCTAssertEqual(Set([server, other]), install.keychain.accounts)

        store.removeKnownServer(other)
        XCTAssertEqual([server], Array(install.keychain.accounts), "the forgotten token is still there")
        XCTAssertNil(install.launch().token(for: other))
    }

    /// The same for the active server, which is the forget a person is asked
    /// to confirm.
    func testForgettingTheActiveServerTakesItsTokenTooAndLeavesNoServer() {
        let install = Install()
        let store = install.launch()
        store.trustServer(server, identity: TestIdentity.real.pin)
        store.upsertKnownServer(url: server, name: nil)
        XCTAssertTrue(store.setServerURL(server))
        store.setToken(token, for: server)

        XCTAssertEqual(server, store.forgetActiveServer())

        let next = install.launch()
        XCTAssertEqual("", next.serverURL)
        XCTAssertTrue(next.knownServers.isEmpty)
        XCTAssertTrue(next.trustedServers.isEmpty)
        XCTAssertNil(next.pin(forServer: server))
        XCTAssertNil(next.token(for: server))
        XCTAssertTrue(install.keychain.accounts.isEmpty)
    }

    /// Unpairing by hand — Settings → Phones & browsers writes a nil token —
    /// reaches the Keychain as well.
    func testANilTokenRemovesTheKeychainItemRatherThanBlankingIt() {
        let install = Install()
        let store = install.launch()
        store.trustServer(server, identity: nil)
        store.setToken(token, for: server)
        XCTAssertFalse(install.keychain.accounts.isEmpty)

        store.setToken(nil, for: server)
        XCTAssertTrue(
            install.keychain.accounts.isEmpty,
            "an item blanked instead of removed is a row a later bug can read"
        )
    }

    /// The trust-again dialog's yes: the book drops the old household token
    /// before pinning the new key (the property behind finding S-T1), and the
    /// Keychain has to lose it in the same breath — otherwise the old
    /// household's credential sits there for whoever answers at that address
    /// next.
    func testReplacingThePinDropsTheTokenFromTheKeychainToo() {
        let install = Install()
        let store = install.launch()
        store.trustServer(server, identity: TestIdentity.real.pin)
        store.setToken(token, for: server)
        XCTAssertFalse(install.keychain.accounts.isEmpty)

        let newPin = ServerIdentity.Pin(publicKey: "another-key", fingerprint: "another-print")
        store.replacePin(url: server, pin: newPin)

        XCTAssertTrue(install.keychain.accounts.isEmpty)
        XCTAssertNil(store.token(for: server))
        XCTAssertEqual(newPin, install.launch().pin(forServer: server))
    }

    /// A token the vault will not keep is not kept in memory either.
    ///
    /// ``TokenVault/save(token:for:)`` refuses a value a header could not
    /// carry; ``TrustBook/setToken(_:for:)`` has no such rule and would hold
    /// it. A store that let the two disagree would work for the rest of the
    /// launch and be unpaired after it, for reasons nothing on screen could
    /// explain.
    func testATokenTheVaultRefusesIsNotKeptInMemoryEither() {
        let install = Install()
        let store = install.launch()
        store.trustServer(server, identity: nil)
        store.setToken("short", for: server)   // under `deviceTokenMinLength`

        XCTAssertNil(store.token(for: server), "memory is holding a token the Keychain refused")
        XCTAssertTrue(install.keychain.accounts.isEmpty)
        XCTAssertNil(install.launch().token(for: server))
    }

    /// Two households at once, which is the whole reason the token is keyed
    /// by address.
    func testTwoServersKeepTwoTokens() {
        let install = Install()
        let store = install.launch()
        store.trustServer(server, identity: nil)
        store.trustServer(other, identity: nil)
        store.upsertKnownServer(url: server, name: nil)
        store.upsertKnownServer(url: other, name: nil)
        store.setToken(token, for: server)
        store.setToken("household-token-999999", for: other)

        let next = install.launch()
        XCTAssertEqual(token, next.token(for: server))
        XCTAssertEqual("household-token-999999", next.token(for: other))
    }

    // MARK: - 3. a blob this build does not fully understand

    /// A blob written by an older build is missing whichever keys that build
    /// did not have. The person's server has to survive that, and the
    /// synthesised decoder would have thrown on the first missing key and
    /// handed back a fresh install.
    ///
    /// Written as JSON by hand, deliberately: a round trip through this
    /// build's own encoder could not produce the shape being tested.
    func testABlobFromAnOlderBuildStillGivesBackTheServer() throws {
        let older = """
        {"book":{"server_url":"\(server)","trusted_servers":["\(server)"]}}
        """
        let install = Install()
        install.place.write(Data(older.utf8))

        let store = install.launch()
        XCTAssertEqual(server, store.serverURL)
        XCTAssertTrue(store.isTrusted(server))
        XCTAssertTrue(store.knownServers.isEmpty, "that build had no rows and this one invents none")
        XCTAssertFalse(store.preferLocal)
        XCTAssertFalse(store.localNetworkHasAnswered)
    }

    /// A blob that is not this shape at all. Starting blank is the only thing
    /// left; crashing on every launch for ever, on a phone whose owner cannot
    /// clear it, is not.
    func testABlobThisBuildCannotReadAtAllIsAFreshInstallAndNotACrash() {
        let install = Install()
        install.place.write(Data("not json, not even close".utf8))

        let store = install.launch()
        XCTAssertEqual("", store.serverURL)
        // And it writes over the rubbish on the next change, rather than
        // leaving it there to be re-read for ever.
        store.trustServer(server, identity: nil)
        XCTAssertTrue(install.launch().isTrusted(server))
    }

    /// The round trip is the book's own, so this pins the one thing the
    /// store's correctness rests on and the one thing a future field will
    /// break: a book encoded and decoded is the same book **except for its
    /// tokens**, which do not go in.
    func testABookSurvivesItsOwnRoundTripExceptForTheTokens() throws {
        var book = TrustBook(serverURL: server)
        book.trust(server, identity: TestIdentity.real.pin)
        book.upsertKnown(url: server, name: "kitchen-box")
        book.setToken(token, for: server)
        book.rejected.insert(other)
        book.sharedScreens[server] = true
        book.owners[server] = "jordan"

        let blob = try JSONEncoder().encode(book)
        let back = try JSONDecoder().decode(TrustBook.self, from: blob)

        XCTAssertEqual(server, back.serverURL)
        XCTAssertEqual(book.trusted, back.trusted)
        XCTAssertEqual(book.known, back.known)
        XCTAssertEqual(book.pins, back.pins)
        XCTAssertEqual(book.rejected, back.rejected)
        XCTAssertEqual(book.sharedScreens, back.sharedScreens)
        XCTAssertEqual(book.owners, back.owners)
        XCTAssertNil(back.token(for: server), "the tokens came through the plist")
        XCTAssertNotEqual(book, back, "and so the two books are not equal, which is the point")
    }
}
