import XCTest
@testable import DomovoiKit

/// The token at rest.
///
/// Most of this runs against ``InMemoryKeychain``, because the logic worth
/// testing is the vault's and not Apple's: that two servers' tokens coexist,
/// that forgetting REMOVES the item rather than blanking it, and that a value
/// a header could not carry is refused in front of the save. The one test
/// that exercises the real Keychain is last, and when it cannot run it says
/// so out loud — see the comment on it.
final class TokenVaultTests: XCTestCase {

    private let home = "http://10.0.0.9:6369"
    private let other = "http://10.0.0.20:6369"
    private let token = "acorn-maple-river-thistle"

    func testATokenSavedIsATokenRead() throws {
        let keychain = InMemoryKeychain()
        let vault = TokenVault(items: keychain)
        XCTAssertNil(vault.token(for: home))
        try vault.save(token: token, for: home)
        XCTAssertEqual(vault.token(for: home), token)
    }

    func testTwoServersTokensCoexistAndDoNotLeakIntoEachOther() throws {
        let keychain = InMemoryKeychain()
        let vault = TokenVault(items: keychain)
        try vault.save(token: token, for: home)
        try vault.save(token: "harbor-quartz-willow-ember", for: other)
        XCTAssertEqual(vault.token(for: home), token)
        XCTAssertEqual(vault.token(for: other), "harbor-quartz-willow-ember")
        XCTAssertEqual(keychain.accounts.count, 2)
        // Two Domovois are two households: forgetting one says nothing about
        // the other.
        try vault.forget(server: home)
        XCTAssertNil(vault.token(for: home))
        XCTAssertEqual(vault.token(for: other), "harbor-quartz-willow-ember")
    }

    /// The spelling a person typed is not part of the server's identity: one
    /// address with a trailing slash and one without are one server, and must
    /// find one token — the same normalisation the refusal book and the wait
    /// book key by.
    func testOneServerSpeltTwoWaysFindsOneToken() throws {
        let keychain = InMemoryKeychain()
        let vault = TokenVault(items: keychain)
        try vault.save(token: token, for: "  http://10.0.0.9:6369/  ")
        XCTAssertEqual(vault.token(for: home), token)
        XCTAssertEqual(keychain.accounts, [home])
    }

    /// The brief's point: forget must actually remove the item.
    func testForgettingRemovesTheItemRatherThanBlankingIt() throws {
        let keychain = InMemoryKeychain()
        let vault = TokenVault(items: keychain)
        try vault.save(token: token, for: home)
        try vault.forget(server: home)
        XCTAssertTrue(keychain.accounts.isEmpty, "no leftover row for a future bug to read")
        XCTAssertNil(try keychain.get(account: home))
    }

    func testForgettingWhatIsNotThereIsNotAnError() throws {
        let vault = TokenVault(items: InMemoryKeychain())
        try vault.forget(server: home)
        try vault.forget(server: "")
    }

    /// Unpairing is what a blank token means — the rule Kotlin's
    /// `ServerCredentials.withToken` carries.
    func testABlankTokenRemovesTheEntry() throws {
        let keychain = InMemoryKeychain()
        let vault = TokenVault(items: keychain)
        try vault.save(token: token, for: home)
        try vault.save(token: "   ", for: home)
        XCTAssertTrue(keychain.accounts.isEmpty)
        try vault.save(token: token, for: home)
        try vault.save(token: nil, for: home)
        XCTAssertTrue(keychain.accounts.isEmpty)
    }

    func testAValueTheServerWouldNeverMintIsRefusedInFrontOfTheSave() throws {
        let keychain = InMemoryKeychain()
        let vault = TokenVault(items: keychain)
        for bad in ["short", "café-token-abcdef", String(repeating: "a", count: 129), "abcdef\tghijkl"] {
            XCTAssertThrowsError(try vault.save(token: bad, for: home)) { error in
                XCTAssertEqual(error as? TokenVaultError, .notStorable, bad)
            }
        }
        XCTAssertTrue(keychain.accounts.isEmpty, "nothing was saved")
    }

    func testThereIsNoServerToFileATokenUnder() {
        let vault = TokenVault(items: InMemoryKeychain())
        XCTAssertThrowsError(try vault.save(token: token, for: "   ")) { error in
            XCTAssertEqual(error as? TokenVaultError, .noServer)
        }
        XCTAssertNil(vault.token(for: ""))
    }

    /// A token that is somehow in the Keychain but could not go in a header
    /// reads as no token: the phone is then "not paired", which is a screen
    /// it has, rather than a request that fails in a way nobody can explain.
    func testAStoredValueAHeaderCouldNotCarryReadsAsNoToken() throws {
        let keychain = InMemoryKeychain()
        try keychain.set(Data("café-token-abcdef".utf8), account: home)
        XCTAssertNil(TokenVault(items: keychain).token(for: home))
        try keychain.set(Data([0xFF, 0xFE, 0xFD]), account: home)
        XCTAssertNil(TokenVault(items: keychain).token(for: home), "and so does a value that is not text")
    }

    /// Replacing a token does not pass through "unpaired" on the way — the
    /// reason ``SystemKeychain`` updates in place rather than deleting and
    /// adding.
    func testReplacingATokenNeverLeavesTheAccountEmpty() throws {
        let keychain = WatchfulKeychain()
        let vault = TokenVault(items: keychain)
        try vault.save(token: token, for: home)
        try vault.save(token: "harbor-quartz-willow-ember", for: home)
        XCTAssertEqual(vault.token(for: home), "harbor-quartz-willow-ember")
        XCTAssertEqual(keychain.removals, 0, "a replacement is not a delete-then-add")
    }

    // ---- the real Keychain --------------------------------------------------

    /// The same journey against Apple's own Keychain.
    ///
    /// It cannot run in `swift test` on a Mac, and the reason is not a
    /// mystery: ``SystemKeychain`` asks for the data-protection keychain
    /// (`kSecUseDataProtectionKeychain`), which is what makes
    /// `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` mean anything, and
    /// macOS grants that only to a process signed with a keychain-access-group
    /// entitlement. An unsigned test binary is not one.
    ///
    /// **So this test reports its skip, loudly, with the `OSStatus` it got.**
    /// `design-notes/ios/` and the project's memory both record a trap where
    /// a `requires_db` skip hid two broken tests for weeks; a skip that
    /// prints the reason is the difference between "we know this is untested
    /// here" and "we thought this passed". When this suite runs on a
    /// Simulator or a device — which is where the Keychain really is — it
    /// runs for real and asserts everything the in-memory tests assert.
    func testTheRealKeychainSavesReadsAndForgets() throws {
        // A service of its own, so a run of this test can never touch the
        // app's own tokens on the machine it runs on.
        let keychain = SystemKeychain(service: "org.codersfarm.domovoi.tests.\(UUID().uuidString)")
        let account = "http://10.0.0.9:6369"
        do {
            try keychain.set(Data(token.utf8), account: account)
        } catch TokenVaultError.keychain(let status) {
            let reason = SecCopyErrorMessageString(status, nil) as String? ?? "no message"
            let note = """
                SKIPPED, NOT PASSED — the real Keychain refused: OSStatus \(status) (\(reason)). \
                This is expected in `swift test` on a Mac: the data-protection keychain needs a \
                signed process with a keychain-access-group entitlement. The vault's logic is \
                covered by the InMemoryKeychain tests above; the SecItem calls themselves are \
                exercised only on a Simulator or a device.
                """
            print("TokenVaultTests: \(note)")
            throw XCTSkip(note)
        }
        defer { try? keychain.remove(account: account) }

        XCTAssertEqual(try keychain.get(account: account).map { String(decoding: $0, as: UTF8.self) }, token)
        // Replace in place.
        try keychain.set(Data("harbor-quartz-willow-ember".utf8), account: account)
        XCTAssertEqual(
            try keychain.get(account: account).map { String(decoding: $0, as: UTF8.self) },
            "harbor-quartz-willow-ember"
        )
        // And forget.
        try keychain.remove(account: account)
        XCTAssertNil(try keychain.get(account: account))
        try keychain.remove(account: account)
    }

    /// Counts what it was asked to do, so "a replacement is not a
    /// delete-then-add" can be asserted rather than hoped for.
    private final class WatchfulKeychain: KeychainItems {
        private let inner = InMemoryKeychain()
        private let counts = NSLock()
        private var _removals = 0

        var removals: Int { counts.withLock { _removals } }

        func set(_ secret: Data, account: String) throws { try inner.set(secret, account: account) }
        func get(account: String) throws -> Data? { try inner.get(account: account) }
        func remove(account: String) throws {
            counts.withLock { _removals += 1 }
            try inner.remove(account: account)
        }
    }
}
