import Foundation
import XCTest
@testable import DomovoiKit

/// What a room phone keeps (`RoomStore.kt`), on the in-memory preferences and
/// key — the real store's code over a dictionary — plus one run on a real
/// `UserDefaults` suite, and ``TokenVault/holdsAnyToken()``.
final class RoomStoreTests: XCTestCase {

    private let kitchen = RoomEnrolment(
        coreURL: "http://192.168.1.20:6370",
        fingerprint: "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA",
        roomID: "kitchen",
        roomLabel: "Kitchen"
    )

    func testANewRoomGetsAKeyAndACodeAndTheSameRoomKeepsThem() {
        let store = RoomStore.inMemory()
        XCTAssertNil(store.enrolment)
        XCTAssertNil(store.pairingToken())
        XCTAssertTrue(store.enrol(kitchen))
        XCTAssertEqual(kitchen, store.enrolment)
        let key = store.pairingToken()
        let code = store.approvalCode
        XCTAssertEqual(43, key?.count, "32 bytes, URL-safe base64 without padding")
        XCTAssertTrue(RoomWakeSettings.isApprovalCode(code ?? ""))
        store.setApprovedOnce()
        store.setWakeWord("hey_house", threshold: 0.7)
        // The same room again (a relabel): the core knows this phone by its
        // key's hash, so the key, the code and what the core pushed stay.
        let relabelled = RoomEnrolment(coreURL: kitchen.coreURL, fingerprint: kitchen.fingerprint,
                                       roomID: "kitchen", roomLabel: "The Kitchen")
        XCTAssertTrue(store.enrol(relabelled))
        XCTAssertEqual(key, store.pairingToken())
        XCTAssertEqual(code, store.approvalCode)
        XCTAssertTrue(store.approvedOnce)
        XCTAssertEqual("hey_house", store.wakeWord)
        XCTAssertEqual("The Kitchen", store.enrolment?.roomLabel)
    }

    func testAnotherRoomOrServerStartsAfresh() {
        let store = RoomStore.inMemory()
        store.enrol(kitchen)
        let key = store.pairingToken()
        store.setApprovedOnce()
        store.setWakeWord("hey_house", threshold: 0.7)
        store.remember("satellite-sounds@", RoomSignedList(manifest: [:], serial: 4, issuedAt: 1, digest: "ab"))
        let porch = RoomEnrolment(coreURL: kitchen.coreURL, fingerprint: kitchen.fingerprint, roomID: "porch", roomLabel: "Porch")
        store.enrol(porch)
        XCTAssertNotEqual(key, store.pairingToken())
        XCTAssertFalse(store.approvedOnce)
        XCTAssertEqual(RoomWakeSettings.defaultWakeWord, store.wakeWord)
        XCTAssertEqual(RoomWakeSettings.defaultThreshold, store.wakeThreshold)
        XCTAssertNil(store.seen("satellite-sounds@"))
    }

    /// The household token goes on no request from the moment room mode is
    /// on: the closure (the app's `HouseholdLock`) runs BEFORE the store
    /// writes, in both directions (`RoomStore.kt:89-92`).
    func testSetActiveSealsFirstAndOffClearsRunning() {
        let order = RoomLock<[String]>([])
        let prefs = InMemoryRoomPreferences()
        let store = RoomStore(prefs: prefs, key: InMemoryRoomKey()) { on in
            order.withLock { $0.append("lock \(on) while active=\(prefs.bool("active"))") }
        }
        store.setActive(true)
        store.setRunning(true)
        XCTAssertTrue(store.active)
        XCTAssertTrue(store.running)
        store.setActive(false)
        XCTAssertFalse(store.active)
        XCTAssertFalse(store.running, "leaving room mode is not a room to resume")
        XCTAssertEqual(["lock true while active=false", "lock false while active=true"], order.withLock { $0 })
    }

    func testForgetRemovesTheKeyAndEverything() {
        let key = InMemoryRoomKey()
        let store = RoomStore(prefs: InMemoryRoomPreferences(), key: key, onActiveChanged: { _ in })
        store.enrol(kitchen)
        store.setActive(true)
        store.dismissGuidedAccessTip()
        store.forget()
        XCTAssertNil(key.read())
        XCTAssertNil(store.pairingToken())
        XCTAssertNil(store.enrolment)
        XCTAssertNil(store.approvalCode)
        XCTAssertFalse(store.active)
        XCTAssertFalse(store.guidedAccessTipDismissed)
    }

    func testTheSignedListSerialsRoundTrip() {
        let store = RoomStore.inMemory()
        XCTAssertNil(store.seen("wake-models"))
        store.remember("wake-models", RoomSignedList(manifest: ["a": "b"], serial: 12, issuedAt: 3, digest: "d1"))
        store.remember("wake-base", RoomSignedList(manifest: [:], serial: 7, issuedAt: 3, digest: ""))
        XCTAssertEqual(RoomSeenList(serial: 12, digest: "d1"), store.seen("wake-models"))
        XCTAssertEqual(RoomSeenList(serial: 7, digest: nil), store.seen("wake-base"))
        store.setTrainedSHA("hey_house", "abc")
        XCTAssertEqual("abc", store.trainedSHA("hey_house"))
    }

    func testTheSharedRulesHoldInTheRealStore() {
        let store = RoomStore.inMemory()
        store.enrol(kitchen)
        let code = store.approvalCode
        store.setApprovalCode("12a456")
        XCTAssertEqual(code, store.approvalCode)
        store.setApprovalCode("305117")
        XCTAssertEqual("305117", store.approvalCode)
        store.setWakeWord("hey_house", threshold: 7)
        XCTAssertEqual(RoomWakeSettings.defaultThreshold, store.wakeThreshold)
        store.setMuted(true)
        XCTAssertTrue(store.muted)
    }

    /// The app's first read — before any household object — is the suite's
    /// `active` flag, and the defaults-backed preferences write that flag.
    func testTheAppsFirstReadSeesWhatTheStoreWrote() throws {
        let suite = "domovoi-room-test-\(UUID().uuidString)"
        defer { UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite) }
        let prefs = try XCTUnwrap(UserDefaultsRoomPreferences(suiteName: suite))
        let store = RoomStore(prefs: prefs, key: InMemoryRoomKey(), onActiveChanged: { _ in })
        XCTAssertFalse(RoomStore.isActive(UserDefaults(suiteName: suite)))
        store.setActive(true)
        XCTAssertTrue(RoomStore.isActive(UserDefaults(suiteName: suite)))
        store.setWakeWord("hey_house", threshold: 0.25)
        XCTAssertEqual(0.25, store.wakeThreshold)
        store.forget()
        XCTAssertFalse(RoomStore.isActive(UserDefaults(suiteName: suite)))
        XCTAssertEqual(RoomWakeSettings.defaultThreshold, store.wakeThreshold)
    }

    // MARK: - TokenVault.holdsAnyToken (RoomLockRules' fact)

    func testHoldsAnyTokenSeesAnyServersToken() throws {
        let keychain = InMemoryKeychain()
        let vault = TokenVault(items: keychain)
        XCTAssertFalse(vault.holdsAnyToken())
        try vault.save(token: "acorn-maple-river-thistle", for: "http://10.0.0.9:6369")
        XCTAssertTrue(vault.holdsAnyToken())
        try vault.forget(server: "http://10.0.0.9:6369")
        XCTAssertFalse(vault.holdsAnyToken())
    }

    /// A Keychain that cannot say counts as holding one: the safe side of
    /// the rule is "ask for the passcode".
    func testAKeychainThatCannotSayCountsAsHolding() {
        final class Mute: KeychainItems {
            func set(_ secret: Data, account: String) throws {}
            func get(account: String) throws -> Data? { nil }
            func remove(account: String) throws {}
        }
        XCTAssertTrue(TokenVault(items: Mute()).holdsAnyToken())
    }
}
