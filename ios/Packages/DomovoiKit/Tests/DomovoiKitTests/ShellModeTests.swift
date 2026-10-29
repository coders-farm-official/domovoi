import XCTest
@testable import DomovoiKit

/// Which shell the app draws, and why.
///
/// A port of `android/.../test/ui/shell/ShellModeTest.kt` **case for case**,
/// plus the cases of `TokenTroubleTest.kt` that are about `shellMode` rather
/// than about the card's words (the card's words are `TokenTroubleTests`),
/// plus the seven-leaf ``shellScreen(serverURL:unreachable:pairingRequired:preferLocal:identityChanged:trouble:pairingDismissed:)``
/// that Android spreads between `shellMode` and `ShellContent`.
///
/// Every Kotlin test name is kept in a comment beside its Swift counterpart,
/// so the two files can be diffed by eye.
final class ShellModeTests: XCTestCase {

    private let url = "http://192.168.1.20:6370"

    // MARK: - shellMode, the Kotlin's cases

    /// `noServerIsLocalMedia`
    func testNoServerIsLocalMedia() {
        XCTAssertEqual(.local, shellMode(serverURL: "", unreachable: false, pairingRequired: false))
        XCTAssertEqual(.local, shellMode(serverURL: "", unreachable: true, pairingRequired: true))
    }

    /// `reachableServerIsTheWorkspace`
    func testReachableServerIsTheWorkspace() {
        XCTAssertEqual(.workspace, shellMode(serverURL: url, unreachable: false, pairingRequired: false))
    }

    /// `unreachableServerFallsBackToLocalMedia`
    func testUnreachableServerFallsBackToLocalMedia() {
        // The phone left the house: a saved server must not strand the app
        // on panels that can only spin.
        XCTAssertEqual(.local, shellMode(serverURL: url, unreachable: true, pairingRequired: false))
    }

    /// `pairingRefusalKeepsTheWorkspace`
    func testPairingRefusalKeepsTheWorkspace() {
        // The server answered — it wants the household token, so the
        // workspace stays up to show the pairing screen.
        XCTAssertEqual(.workspace, shellMode(serverURL: url, unreachable: true, pairingRequired: true))
    }

    /// `choosingThePhoneHoldsEvenWithTheServerAnswering`
    func testChoosingThePhoneHoldsEvenWithTheServerAnswering() {
        XCTAssertEqual(
            .local,
            shellMode(serverURL: url, unreachable: false, pairingRequired: false, preferLocal: true)
        )
    }

    /// `choosingThePhoneBeatsAPairingRefusal` — the first of the two orderings
    /// that look wrong until you read why.
    func testChoosingThePhoneBeatsAPairingRefusal() {
        // The pairing screen is a workspace screen; a person who chose the
        // phone has not asked to see it.
        XCTAssertEqual(
            .local,
            shellMode(serverURL: url, unreachable: false, pairingRequired: true, preferLocal: true)
        )
    }

    /// `choosingTheServerKeepsTheAutomaticFallback`
    func testChoosingTheServerKeepsTheAutomaticFallback() {
        XCTAssertEqual(
            .workspace,
            shellMode(serverURL: url, unreachable: false, pairingRequired: false, preferLocal: false)
        )
        XCTAssertEqual(
            .local,
            shellMode(serverURL: url, unreachable: true, pairingRequired: false, preferLocal: false)
        )
    }

    /// `TokenTroubleTest.theCardKeepsTheWorkspaceUpEvenIfTheProbesFail` — the
    /// second surprising ordering.
    func testTheCardKeepsTheWorkspaceUpEvenIfTheProbesFail() {
        // A held or refused token makes every request fail, so the probe
        // reads the server as out of reach; bouncing the person to local
        // media would leave them with no explanation.
        XCTAssertEqual(
            .workspace,
            shellMode(serverURL: url, unreachable: true, pairingRequired: false, tokenTrouble: true)
        )
        // ...but the person's own "this phone" still wins.
        XCTAssertEqual(
            .local,
            shellMode(
                serverURL: url, unreachable: false, pairingRequired: false,
                preferLocal: true, tokenTrouble: true
            )
        )
    }

    /// `TokenTroubleTest.anIdentityChangeComesFirstAndIsNotThisCard`, the
    /// `shellMode` half.
    func testAnIdentityChangeTakesTheShellToLocalMedia() {
        XCTAssertEqual(
            .local,
            shellMode(
                serverURL: url, unreachable: false, pairingRequired: false,
                identityChanged: true, tokenTrouble: false
            )
        )
    }

    /// Not a Kotlin case: the branch order, stated as a single table over the
    /// whole input space.
    ///
    /// Six booleans would be 64 rows; the five `shellMode` reads are
    /// `serverURL` blank or not, and the four flags, so this walks all 32 and
    /// checks each against the ordering written out longhand. It is the test
    /// that fails if a branch is moved, whichever way it is moved — the
    /// named cases above only pin the two orderings somebody already got
    /// wrong.
    func testTheBranchOrderHoldsOverEveryCombinationOfInputs() {
        for bits in 0..<32 {
            let blank = bits & 1 != 0
            let preferLocal = bits & 2 != 0
            let identityChanged = bits & 4 != 0
            let pairing = bits & 8 != 0
            let trouble = bits & 16 != 0
            // Every combination is run twice, once with the probes failing,
            // because `unreachable` is the input the two surprising
            // orderings are about.
            for unreachable in [false, true] {
                let expected: ShellMode
                if blank { expected = .local }
                else if preferLocal { expected = .local }
                else if identityChanged { expected = .local }
                else if pairing || trouble { expected = .workspace }
                else if unreachable { expected = .local }
                else { expected = .workspace }
                let actual = shellMode(
                    serverURL: blank ? "" : url,
                    unreachable: unreachable,
                    pairingRequired: pairing,
                    preferLocal: preferLocal,
                    identityChanged: identityChanged,
                    tokenTrouble: trouble
                )
                XCTAssertEqual(
                    expected, actual,
                    "blank=\(blank) preferLocal=\(preferLocal) identityChanged=\(identityChanged) "
                        + "pairing=\(pairing) trouble=\(trouble) unreachable=\(unreachable)"
                )
            }
        }
    }

    /// Kotlin's `isBlank()`, which is the question `shellMode` asks.
    func testAServerAddressOfNothingButSpacesIsNoServer() {
        XCTAssertEqual(.local, shellMode(serverURL: "   ", unreachable: false, pairingRequired: false))
        XCTAssertEqual(.local, shellMode(serverURL: "\n\t", unreachable: false, pairingRequired: false))
        // `"/"` is not blank, on either platform. It is nonsense as an
        // address and nothing is sent to it, but the two apps take the same
        // branch on it — which they did not before this batch
        // (`ServerCredentials.isBlank`).
        XCTAssertEqual(.workspace, shellMode(serverURL: "/", unreachable: false, pairingRequired: false))
        XCTAssertNotNil(tokenTrouble(serverURL: "/", paired: true, rejected: true, waitMs: 0))
    }

    // MARK: - the seven leaves

    private func screen(
        serverURL: String? = nil,
        unreachable: Bool = false,
        pairingRequired: Bool = false,
        preferLocal: Bool = false,
        identityChanged: Bool = false,
        trouble: TokenTrouble? = nil,
        pairingDismissed: Bool = false
    ) -> ShellScreen {
        shellScreen(
            serverURL: serverURL ?? url,
            unreachable: unreachable,
            pairingRequired: pairingRequired,
            preferLocal: preferLocal,
            identityChanged: identityChanged,
            trouble: trouble,
            pairingDismissed: pairingDismissed
        )
    }

    func testTheSevenLeaves() {
        // §2.2 no server: nothing is wrong, and nothing opens by itself.
        XCTAssertEqual(.localMedia(.noServer), screen(serverURL: ""))
        // §2.3 the person chose "this phone".
        XCTAssertEqual(.localMedia(.choseThisPhone), screen(preferLocal: true))
        // §2.5 the server proved a different key.
        XCTAssertEqual(.localMedia(.identityChanged), screen(identityChanged: true))
        // §2.4 nothing answered.
        XCTAssertEqual(.localMedia(.outOfReach), screen(unreachable: true))
        // §2.8 the token card, in place of the whole workspace.
        XCTAssertEqual(
            .tokenTroubleCard(.notRecognised(waitMs: 0)),
            screen(trouble: .notRecognised(waitMs: 0))
        )
        // §2.7 the pairing screen, in place of the whole workspace.
        XCTAssertEqual(.pairing, screen(pairingRequired: true))
        // §2.9 paired and reachable.
        XCTAssertEqual(.workspace, screen())
    }

    /// The leaves are reached in the same order `shellMode` and
    /// `ShellContent` reach them, which is what decides the cases where
    /// several are true at once.
    func testWhichLeafWinsWhenSeveralAreTrueAtOnce() {
        // No server beats everything, including a card that could not exist
        // without one.
        XCTAssertEqual(
            .localMedia(.noServer),
            screen(serverURL: "", unreachable: true, pairingRequired: true, preferLocal: true, identityChanged: true)
        )
        // "This phone" beats the pairing screen and the card.
        XCTAssertEqual(
            .localMedia(.choseThisPhone),
            screen(pairingRequired: true, preferLocal: true, trouble: .waiting(waitMs: 5_000))
        )
        // The identity question is asked before anything about pairing.
        XCTAssertEqual(
            .localMedia(.identityChanged),
            screen(pairingRequired: true, identityChanged: true)
        )
        // The card goes in front of the pairing screen: a refused token is
        // why the server refuses the request, and the card is the one that
        // says what happened.
        XCTAssertEqual(
            .tokenTroubleCard(.notRecognised(waitMs: 0)),
            screen(pairingRequired: true, trouble: .notRecognised(waitMs: 0))
        )
        // The card holds the workspace up while the probes fail.
        XCTAssertEqual(
            .tokenTroubleCard(.waiting(waitMs: 1_000)),
            screen(unreachable: true, trouble: .waiting(waitMs: 1_000))
        )
        // A pairing refusal holds it up too.
        XCTAssertEqual(.pairing, screen(unreachable: true, pairingRequired: true))
    }

    /// `not now`: the unpaired workspace, and nothing else changed.
    func testNotNowLetsTheUnpairedWorkspaceThrough() {
        XCTAssertEqual(.workspace, screen(pairingRequired: true, pairingDismissed: true))
        // Even with the probes failing — the refusal is still what
        // `shellMode` saw, and `not now` only skips the screen.
        XCTAssertEqual(
            .workspace,
            screen(unreachable: true, pairingRequired: true, pairingDismissed: true)
        )
        // Dismissing the pairing screen does not dismiss the card.
        XCTAssertEqual(
            .tokenTroubleCard(.notRecognised(waitMs: 0)),
            screen(pairingRequired: true, trouble: .notRecognised(waitMs: 0), pairingDismissed: true)
        )
        // And it cannot conjure a workspace where there is no server.
        XCTAssertEqual(.localMedia(.noServer), screen(serverURL: "", pairingRequired: true, pairingDismissed: true))
    }

    /// `shellScreen` must agree with `shellMode` on which shell it is, over
    /// the whole input space: it is one decision, and the `if`s are only
    /// written once.
    func testShellScreenNeverDisagreesWithShellMode() {
        for bits in 0..<64 {
            let blank = bits & 1 != 0
            let preferLocal = bits & 2 != 0
            let identityChanged = bits & 4 != 0
            let pairing = bits & 8 != 0
            let troubled = bits & 16 != 0
            let unreachable = bits & 32 != 0
            let trouble: TokenTrouble? = troubled ? .waiting(waitMs: 7_000) : nil
            let mode = shellMode(
                serverURL: blank ? "" : url,
                unreachable: unreachable,
                pairingRequired: pairing,
                preferLocal: preferLocal,
                identityChanged: identityChanged,
                tokenTrouble: trouble != nil
            )
            let leaf = screen(
                serverURL: blank ? "" : url,
                unreachable: unreachable,
                pairingRequired: pairing,
                preferLocal: preferLocal,
                identityChanged: identityChanged,
                trouble: trouble
            )
            let isLocal: Bool
            if case .localMedia = leaf { isLocal = true } else { isLocal = false }
            XCTAssertEqual(mode == .local, isLocal, "bits \(bits): \(mode) vs \(leaf)")
        }
    }

    // MARK: - serverChoice, the Kotlin's cases

    private func choice(
        serverURL: String? = nil,
        unreachable: Bool = false,
        notOurs: Bool = false,
        pairingRequired: Bool = false,
        coreDown: Bool = false
    ) -> ServerChoice {
        DomovoiKit.serverChoice(
            serverURL: serverURL ?? url,
            label: "Domovoi",
            unreachable: unreachable,
            notOurs: notOurs,
            pairingRequired: pairingRequired,
            coreDown: coreDown
        )
    }

    /// `noSavedServerCannotBePicked`
    func testNoSavedServerCannotBePicked() {
        let c = choice(serverURL: "")
        XCTAssertEqual(.noServer, c)
        XCTAssertFalse(c.isEnabled)
        XCTAssertEqual("no server yet · pick one below", c.reason)
        XCTAssertNil(c.label)
    }

    /// `anUnreachableServerIsGreyedWithItsReason`
    func testAnUnreachableServerIsGreyedWithItsReason() {
        let c = choice(unreachable: true)
        XCTAssertEqual(.unreachable(label: "Domovoi"), c)
        XCTAssertFalse(c.isEnabled)
        XCTAssertEqual("can't reach it right now", c.reason)
    }

    /// `aServerThatFailedTheIdentityProofIsGreyedEvenIfItAnswers`
    func testAServerThatFailedTheIdentityProofIsGreyedEvenIfItAnswers() {
        // Identity beats reachability: something answering at the address is
        // exactly the case the proof exists for.
        let c = choice(unreachable: false, notOurs: true)
        XCTAssertEqual(.notOurs(label: "Domovoi"), c)
        XCTAssertFalse(c.isEnabled)
    }

    /// `anAnsweringServerCanBePicked`
    func testAnAnsweringServerCanBePicked() {
        let c = choice()
        XCTAssertTrue(c.isEnabled)
        XCTAssertEqual("available", c.reason)
        XCTAssertEqual("Domovoi", c.title)
    }

    /// `aServerWantingTheTokenCanStillBePickedAndSaysSo`
    func testAServerWantingTheTokenCanStillBePickedAndSaysSo() {
        let c = choice(pairingRequired: true)
        XCTAssertTrue(c.isEnabled)
        XCTAssertEqual("available · needs the household token", c.reason)
    }

    /// `aServerWhoseCoreIsDownIsGreyedWithItsOwnReasonNotUnreachable`
    func testAServerWhoseCoreIsDownIsGreyedWithItsOwnReasonNotUnreachable() {
        // The web backend answered, so "can't reach it" would be wrong; the
        // token is held until the core proves the identity, so it cannot be
        // picked either. Named before unreachable: with the token held every
        // request fails and the shell's probe reads the server as unreachable.
        let c = choice(unreachable: true, coreDown: true)
        XCTAssertEqual(.coreDown(label: "Domovoi"), c)
        XCTAssertFalse(c.isEnabled)
        XCTAssertEqual("up, but its core isn't answering yet", c.reason)
        XCTAssertEqual("Domovoi", c.title)
    }

    /// `anIdentityFailureBeatsACoreDownReading`
    func testAnIdentityFailureBeatsACoreDownReading() {
        // Both cannot be true of one verdict, but if the inputs disagree the
        // impostor reading wins: it is the one that must never be softened.
        XCTAssertEqual(.notOurs(label: "Domovoi"), choice(notOurs: true, coreDown: true))
    }

    /// `coreDownDefaultsToFalseForEveryOlderCaller`
    func testCoreDownDefaultsToFalseForEveryOlderCaller() {
        XCTAssertTrue(choice().isEnabled)
    }

    /// `TokenTroubleTest.theConnectionDialogSaysItTheSameWay`
    func testTheConnectionDialogSaysItTheSameWay() {
        XCTAssertEqual("looks like a different Domovoi, or it was reinstalled", choice(notOurs: true).reason)
    }

    /// `ServerChoice.title()` for no server, which is the one case that is
    /// not the label.
    func testTheServerSideIsCalledYourDomovoiBeforeThereIsOne() {
        XCTAssertEqual("your Domovoi", ServerChoice.noServer.title)
    }

    // MARK: - the local shell's banner

    func testTheBannerSaysWhichOfTheThreeFacesItIs() {
        XCTAssertEqual(
            "Can't reach 10.0.0.42:6369. Showing media on this phone; "
                + "the workspace comes back when the server does.",
            localBanner(for: .unreachable(label: "10.0.0.42:6369"), canTrustAgain: false)
        )
        XCTAssertEqual(
            "10.0.0.42:6369 is up but its core is not answering (starting, updating, or busy), so nothing "
                + "was sent to it yet. Showing media on this phone; the workspace comes back when the core does.",
            localBanner(for: .coreDown(label: "10.0.0.42:6369"), canTrustAgain: false)
        )
        let notOurs = localBanner(for: .notOurs(label: "10.0.0.42:6369"), canTrustAgain: true)
        XCTAssertEqual(identityChangedText(address: "10.0.0.42:6369", canTrustAgain: true), notOurs)
    }

    /// "Choosing 'this phone' with the server right there is not an outage",
    /// and a fresh install has nothing wrong with it either.
    func testTheTwoSilentCases() {
        XCTAssertNil(localBanner(for: .noServer, canTrustAgain: false))
        XCTAssertNil(localBanner(for: .available(label: "Domovoi"), canTrustAgain: false))
        XCTAssertNil(localBanner(for: .available(label: "Domovoi", needsPairing: true), canTrustAgain: false))
    }

    /// `coreDown` must never be worded as "can't reach it": the token is
    /// being held, which is not an outage and never a reason to forget the
    /// server (map §10.2).
    func testCoreDownIsNeverWordedAsAnOutageOrAnImpostor() {
        let text = localBanner(for: .coreDown(label: "box:6369"), canTrustAgain: false) ?? ""
        XCTAssertFalse(text.contains("Can't reach"), text)
        XCTAssertFalse(text.lowercased().contains("different domovoi"), text)
        XCTAssertFalse(text.lowercased().contains("forget"), text)
        XCTAssertFalse(ServerChoice.coreDown(label: "x").reason.contains("reach"))
    }

    // MARK: - identity changed (IdentityChanged.kt's pure half)

    private let newPin = ServerIdentity.Pin(publicKey: "bmV3LWtleQ==", fingerprint: "SHA256:new")

    /// `TokenTroubleTest.theIdentityWarningIsInHouseholdWords`
    func testTheIdentityWarningIsInHouseholdWords() {
        let t = identityChangedText(address: "192.168.1.30:6369", canTrustAgain: true)
        XCTAssertTrue(t.hasPrefix("This looks like a different Domovoi, or it was reinstalled."), t)
        XCTAssertTrue(t.contains("sent it nothing"), t)
        XCTAssertTrue(t.contains("trust it again"), t)
        let old = identityChangedText(address: "192.168.1.30:6369", canTrustAgain: false)
        XCTAssertTrue(old.contains("forget it"), "without a proven key, the old way out")
        // The two differ only in the last sentence pair, and the second one
        // does NOT offer a yes it cannot honour.
        XCTAssertFalse(old.contains("trust it again."), old)
    }

    /// `TokenTroubleTest.onlyAProvenKeyCanBeTrustedAgain`
    func testOnlyAProvenKeyCanBeTrustedAgain() {
        XCTAssertEqual(
            ChangedIdentity(expected: "SHA256:old", seen: "SHA256:new", seenPin: newPin),
            changedIdentity(.mismatch(expected: "SHA256:old", seen: "SHA256:new", seenPin: newPin))
        )
        XCTAssertNil(changedIdentity(.mismatch(expected: "SHA256:old", seen: "SHA256:new", seenPin: nil)))
        XCTAssertNil(changedIdentity(.unproven(expected: "SHA256:old", reason: "offered no identity")))
        XCTAssertNil(changedIdentity(.verified(fingerprint: "SHA256:old", pinnedNow: false)))
        XCTAssertNil(changedIdentity(.unavailable(expected: "SHA256:old", reason: "its core is not answering")))
        XCTAssertNil(changedIdentity(.legacy(offered: newPin)))
        XCTAssertNil(changedIdentity(nil))
    }

    /// `TokenTroubleTest.theYesShowsBothKeys`
    func testTheYesShowsBothKeys() {
        let t = trustAgainText(pinned: "SHA256:old", now: "SHA256:new")
        XCTAssertTrue(t.contains("SHA256:old") && t.contains("SHA256:new"))
        XCTAssertTrue(t.contains("Settings → About & updates"))
        XCTAssertTrue(t.contains("say no"))
        // The order matters: what this phone trusted, then what answered.
        let pinnedAt = t.range(of: "SHA256:old")!.lowerBound
        let seenAt = t.range(of: "SHA256:new")!.lowerBound
        XCTAssertLessThan(pinnedAt, seenAt)
        // And the yes says plainly that it drops the old token (finding S-T1).
        XCTAssertTrue(t.contains("forgets this phone's old household token"), t)
    }
}
