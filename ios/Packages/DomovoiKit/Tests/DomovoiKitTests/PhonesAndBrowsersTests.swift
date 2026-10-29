import XCTest
@testable import DomovoiKit

/// Settings → Phones & browsers and the forget confirmation: which servers
/// are listed, what the phone holds each to, and what the confirmation says
/// about what is about to go.
///
/// The twin of `TrustedServersCardTest.kt`, `TrustedServersTest.kt` and
/// `ForgetServerTest.kt`.
final class PhonesAndBrowsersTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let other = "http://10.0.0.7:6369"

    // MARK: - §6.3, the rows

    /// One row per server, **the active one first**, then by address — and
    /// the union of three lists, so there is never a server the phone talks
    /// to that the list does not show.
    func testOnePerRowWithTheActiveOneFirst() {
        let rows = trustedServersRows(
            trusted: [other, "http://10.0.0.100:6369"],
            known: [KnownServer(url: other, name: "study")],
            active: server
        )
        XCTAssertEqual([server, "http://10.0.0.100:6369", other], rows.map(\.url))
        XCTAssertEqual([true, false, false], rows.map(\.active))
        XCTAssertEqual("study", rows[2].name)
        XCTAssertEqual(ShellStrings.unnamedServer, rows[0].displayName)
    }

    /// The active server is listed even when nothing else knows it.
    func testTheActiveServerIsListedEvenWhenNothingElseKnowsIt() {
        let rows = trustedServersRows(trusted: [], known: [], active: server)
        XCTAssertEqual([server], rows.map(\.url))
        XCTAssertTrue(rows[0].active)
    }

    /// A phone with no server and no trust has an empty list, not a row for
    /// the empty string.
    func testAPhoneWithNoServerHasNoRows() {
        XCTAssertEqual([], trustedServersRows(trusted: [], known: [], active: ""))
        XCTAssertEqual([], trustedServersRows(trusted: [""], known: [], active: "   "))
    }

    /// The two lists can drift, and the union covers it without listing one
    /// server twice.
    func testDriftBetweenTheTwoListsIsOneRow() {
        let rows = trustedServersRows(
            trusted: [server],
            known: [KnownServer(url: server, name: "kitchen-box")],
            active: server
        )
        XCTAssertEqual(1, rows.count)
        XCTAssertEqual("kitchen-box", rows[0].name)
    }

    /// The asymmetry again, on this list: active asks, anything else does
    /// not (§11.8).
    func testTheActiveRowAsksFirstHereToo() {
        let rows = trustedServersRows(trusted: [other], known: [], active: server)
        XCTAssertEqual(.confirmFirst, rows.first { $0.active }?.forgetAffordance)
        XCTAssertEqual(.immediate, rows.first { !$0.active }?.forgetAffordance)
    }

    // MARK: - §6.4, what the confirmation says

    /// The fixed opening is there whatever the identity situation is: a
    /// person is told what goes before they are told what changed.
    func testTheConfirmationAlwaysSaysWhatWillBeForgotten() {
        let body = forgetActiveServerText(address: "10.0.0.42:6369", pinned: nil, proven: nil)
        XCTAssertTrue(body.hasPrefix("This phone will forget 10.0.0.42:6369: its row in the server list, its trust, its household token "))
        XCTAssertTrue(body.contains("You will be back at the server list; trust and pair it again to use it."))
    }

    /// With no pin there is nothing to compare, so **neither identity line
    /// appears** — including the "not proved" one.
    func testWithNoPinNeitherIdentityLineAppears() {
        let body = forgetActiveServerText(address: "a", pinned: nil, proven: nil)
        XCTAssertFalse(body.contains("pinned:"))
        XCTAssertFalse(body.contains("not proved on this network yet"))
        XCTAssertFalse(body.contains("it now proves"))
    }

    func testAPinThatNothingHasProvedYetSaysSo() {
        let body = forgetActiveServerText(address: "a", pinned: "SHA256:old", proven: nil)
        XCTAssertTrue(body.contains("\n\npinned: SHA256:old"))
        XCTAssertTrue(body.contains("\nnot proved on this network yet"))
    }

    /// The one that saves a person from forgetting for no reason — **with an
    /// em dash**.
    func testTheConfirmationSaysWhenForgettingIsNotNeededForIdentitysSake() {
        let body = forgetActiveServerText(address: "a", pinned: "SHA256:same", proven: "SHA256:same")
        XCTAssertTrue(
            body.contains("\nit still proves that key \u{2014} forgetting is not needed for identity's sake")
        )
        XCTAssertFalse(body.contains("it now proves"))
    }

    func testAChangedKeyIsShownBesideThePinnedOne() {
        let body = forgetActiveServerText(address: "a", pinned: "SHA256:old", proven: "SHA256:new")
        XCTAssertTrue(body.contains("\n\npinned: SHA256:old"))
        XCTAssertTrue(body.contains("\nit now proves: SHA256:new"))
        XCTAssertFalse(body.contains("not proved on this network yet"))
    }

    // MARK: - the proven fingerprint

    /// It answers only for **this** server's pin key: a status left over from
    /// another server is not evidence about this one.
    func testAnotherServersStatusIsNotEvidenceAboutThisOne() {
        let status = IdentityGate.Status(
            base: IdentityGate.pinKey(other)!,
            verdict: .verified(fingerprint: "SHA256:other", pinnedNow: false)
        )
        XCTAssertNil(provenFingerprint(status: status, serverURL: server))
        XCTAssertEqual("SHA256:other", provenFingerprint(status: status, serverURL: other))
    }

    /// Each verdict's answer, including the two that prove nothing.
    func testEachVerdictsProvenKey() {
        let key = IdentityGate.pinKey(server)!
        func proven(_ verdict: IdentityVerdict) -> String? {
            provenFingerprint(status: IdentityGate.Status(base: key, verdict: verdict), serverURL: server)
        }
        XCTAssertEqual("SHA256:a", proven(.verified(fingerprint: "SHA256:a", pinnedNow: true)))
        XCTAssertEqual("SHA256:seen", proven(.mismatch(expected: "SHA256:a", seen: "SHA256:seen", seenPin: nil)))
        XCTAssertEqual(TestIdentity.real.fingerprint, proven(.legacy(offered: TestIdentity.real.pin)))
        XCTAssertNil(proven(.legacy(offered: nil)))
        XCTAssertNil(proven(.unproven(expected: nil, reason: "bad body")))
        XCTAssertNil(proven(.unavailable(expected: nil, reason: "core down")))
        XCTAssertNil(provenFingerprint(status: nil, serverURL: server))
    }

    // MARK: - §4.4, trusted is not pinned

    /// The four states, and the one that must never be silent: a server
    /// trusted while it offered no identity, which proves one now, gets a
    /// **button** and not a pin.
    func testAServerThatGrewAnIdentityIsNeverPinnedBehindThePersonsBack() {
        let display = IdentityDisplay.of(pin: nil, verdict: .legacy(offered: TestIdentity.real.pin))
        XCTAssertEqual(.offered(TestIdentity.real.pin), display)
        XCTAssertTrue(display.offersPinning)
        XCTAssertEqual(TestIdentity.real.fingerprint, display.fingerprint)
        XCTAssertTrue(display.detail.contains("Until then it is sent as before (unverified)."))
    }

    func testTheFourIdentityStates() {
        XCTAssertEqual(
            .pinned(fingerprint: TestIdentity.real.fingerprint),
            IdentityDisplay.of(pin: TestIdentity.real.pin, verdict: nil)
        )
        XCTAssertEqual(
            .noIdentityOffered,
            IdentityDisplay.of(pin: nil, verdict: .legacy(offered: nil))
        )
        XCTAssertEqual(
            .notPinnedYet,
            IdentityDisplay.of(pin: nil, verdict: nil)
        )
        XCTAssertEqual(
            .notPinnedYet,
            IdentityDisplay.of(pin: nil, verdict: .unproven(expected: nil, reason: "x"))
        )
        XCTAssertFalse(IdentityDisplay.noIdentityOffered.offersPinning)
        XCTAssertFalse(IdentityDisplay.notPinnedYet.offersPinning)
        XCTAssertFalse(IdentityDisplay.pinned(fingerprint: "SHA256:a").offersPinning)
    }

    /// A pinned key wins over an offered one, so a stale `legacy` status
    /// cannot draw a button that would overwrite a pin.
    func testAPinnedKeyWinsOverAnOfferedOne() {
        XCTAssertEqual(
            .pinned(fingerprint: TestIdentity.real.fingerprint),
            IdentityDisplay.of(pin: TestIdentity.real.pin, verdict: .legacy(offered: TestIdentity.rogue.pin))
        )
    }

    // MARK: - §5.1 and A6-03, who may be offered the field

    /// **A 401 may invite a paste only from a server that has proved
    /// itself.** The two verdicts that admit the token get the field; the
    /// three that do not, do not.
    func testOnlyAServerThatProvedItselfIsOfferedTheTokenField() {
        XCTAssertTrue(pairingOffersTheField(verdict: .verified(fingerprint: "SHA256:a", pinnedNow: false)))
        XCTAssertTrue(pairingOffersTheField(verdict: .legacy(offered: nil)))
        XCTAssertFalse(pairingOffersTheField(verdict: .mismatch(expected: "a", seen: "b", seenPin: nil)))
        XCTAssertFalse(pairingOffersTheField(verdict: .unproven(expected: nil, reason: "x")))
        XCTAssertFalse(pairingOffersTheField(verdict: .unavailable(expected: nil, reason: "x")))

        // Every verdict's answer is its own `admitsToken`, so the two cannot
        // drift apart as the gate grows a case.
        for verdict: IdentityVerdict in [
            .verified(fingerprint: "SHA256:a", pinnedNow: true),
            .legacy(offered: TestIdentity.real.pin),
            .mismatch(expected: "a", seen: "b", seenPin: TestIdentity.rogue.pin),
            .unproven(expected: "a", reason: "x"),
            .unavailable(expected: nil, reason: "x"),
        ] {
            XCTAssertEqual(verdict.admitsToken, pairingOffersTheField(verdict: verdict), "for \(verdict)")
        }
    }

    /// A server nothing has asked about yet **is** offered the field: that is
    /// every server on a cold launch, and it is the state Android is
    /// permanently in.
    func testAServerNothingHasAskedAboutYetIsOfferedTheField() {
        XCTAssertTrue(pairingOffersTheField(verdict: nil))
    }
}
