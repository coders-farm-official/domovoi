import XCTest
@testable import DomovoiKit

/// `ANDROID-SHELL-MAP.md` §2.11 — state → drawn — row by row
/// (``shellChrome(screen:choice:connected:unverified:canTrustAgain:containerBanner:)``).
///
/// Every row starts from the **inputs**, not from a hand-built
/// ``ShellScreen``: each case below drives part 1's
/// ``shellScreen(serverURL:unreachable:pairingRequired:preferLocal:identityChanged:trouble:pairingDismissed:)``
/// and ``serverChoice(serverURL:label:unreachable:notOurs:pairingRequired:coreDown:)``
/// and then asks what is drawn. So the table checks the whole path a state
/// takes to the screen, and a wrong `if` in either half fails here.
///
/// **The expectations are written out, not re-derived.** Part 1's review
/// caught a 64-row table that asserted a tautology by calling the same
/// function twice, so the banner rows below assert the sentence a person can
/// tell apart — "trust it again" present or absent, "its core is not
/// answering", "Can't reach" — rather than calling
/// ``identityChangedText(address:canTrustAgain:)`` again and comparing it
/// with itself.
final class ShellChromeTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let label = "10.0.0.42:6369"

    /// One row of §2.11's table, as inputs.
    private struct Row {
        var serverURL: String
        var unreachable = false
        var pairingRequired = false
        var preferLocal = false
        var identityChanged = false
        var notOurs = false
        var coreDown = false
        var trouble: TokenTrouble?
        var connected = false
        var unverified = false
        var canTrustAgain = false
    }

    private func chrome(_ row: Row) -> ShellChrome {
        let screen = shellScreen(
            serverURL: row.serverURL,
            unreachable: row.unreachable,
            pairingRequired: row.pairingRequired,
            preferLocal: row.preferLocal,
            identityChanged: row.identityChanged,
            trouble: row.trouble,
            pairingDismissed: false
        )
        let choice = serverChoice(
            serverURL: row.serverURL,
            label: row.serverURL.isEmpty ? "" : label,
            unreachable: row.unreachable,
            notOurs: row.notOurs,
            pairingRequired: row.pairingRequired || {
                if case .notRecognised = row.trouble { return true }
                return false
            }(),
            coreDown: row.coreDown
        )
        return shellChrome(
            screen: screen,
            choice: choice,
            connected: row.connected,
            unverified: row.unverified,
            canTrustAgain: row.canTrustAgain,
            containerBanner: nil
        )
    }

    /// Row 1 — no server. The local shell, no banner, a `connect` pill with
    /// **no dot and no word** beside it, and a greyed card saying where to
    /// start.
    func testNoServer() {
        let drawn = chrome(Row(serverURL: ""))
        XCTAssertEqual(.localMedia(.noServer), drawn.screen)
        XCTAssertNil(drawn.banner)
        XCTAssertFalse(drawn.offersTrustAgain)
        XCTAssertEqual("connect", drawn.pill?.label)
        XCTAssertEqual("connect", drawn.pill?.accessibilityLabel)
        XCTAssertNil(drawn.pill?.status, "nothing to say available or offline about")
        XCTAssertFalse(drawn.serverCard.isEnabled)
        XCTAssertEqual("no server yet · pick one below", drawn.serverCard.reason)
        XCTAssertEqual("your Domovoi", drawn.serverCard.title)
    }

    /// Row 2 — the person chose "this phone". **Nothing is wrong**: no
    /// banner, the dot says available, and the card is pickable.
    func testTheirOwnChoiceOfThisPhoneIsNotAnOutage() {
        let drawn = chrome(Row(serverURL: server, preferLocal: true))
        XCTAssertEqual(.localMedia(.choseThisPhone), drawn.screen)
        XCTAssertNil(drawn.banner)
        XCTAssertEqual(label, drawn.pill?.label)
        XCTAssertEqual("switch server", drawn.pill?.accessibilityLabel)
        XCTAssertEqual("  available", drawn.pill?.status)
        XCTAssertTrue(drawn.serverCard.isEnabled)
        XCTAssertEqual("available", drawn.serverCard.reason)
    }

    /// Row 3 — nothing answered.
    func testUnreachable() {
        let drawn = chrome(Row(serverURL: server, unreachable: true))
        XCTAssertEqual(.localMedia(.outOfReach), drawn.screen)
        XCTAssertEqual("  offline", drawn.pill?.status)
        XCTAssertFalse(drawn.serverCard.isEnabled)
        XCTAssertEqual("can't reach it right now", drawn.serverCard.reason)
        let banner = drawn.banner ?? ""
        XCTAssertTrue(banner.hasPrefix("Can't reach \(label)."), banner)
        XCTAssertTrue(banner.contains("the workspace comes back when the server does"), banner)
        XCTAssertFalse(drawn.offersTrustAgain)
    }

    /// Row 4 — something answered and proved a **different** key, with the
    /// key in hand: the identity banner, and the yes beside it.
    func testAMismatchAsksAboutTheKeyAndOffersTheYes() {
        let drawn = chrome(Row(
            serverURL: server,
            identityChanged: true,
            notOurs: true,
            canTrustAgain: true
        ))
        XCTAssertEqual(.localMedia(.identityChanged), drawn.screen)
        XCTAssertEqual("  offline", drawn.pill?.status)
        XCTAssertEqual("looks like a different Domovoi, or it was reinstalled", drawn.serverCard.reason)
        let banner = drawn.banner ?? ""
        XCTAssertTrue(banner.hasPrefix("This looks like a different Domovoi"), banner)
        XCTAssertTrue(banner.contains("trust it again"), banner)
        XCTAssertTrue(drawn.offersTrustAgain)
    }

    /// Row 5 — `Unproven`: it reads as `notOurs` too, but **no key was
    /// proved**, so there is nothing to trust again and the banner says the
    /// other way out instead. It reaches the local shell through the probe,
    /// which is why `identityChanged` is false here and the leaf is
    /// `outOfReach`.
    func testAnUnprovenAnswerWithholdsTheYesAndSaysTheOtherWayOut() {
        let drawn = chrome(Row(
            serverURL: server,
            unreachable: true,
            notOurs: true,
            canTrustAgain: false
        ))
        XCTAssertEqual(.localMedia(.outOfReach), drawn.screen)
        let banner = drawn.banner ?? ""
        XCTAssertTrue(banner.hasPrefix("This looks like a different Domovoi"), banner)
        XCTAssertFalse(banner.contains("trust it again"), banner)
        XCTAssertTrue(banner.contains("forget it"), banner)
        XCTAssertFalse(drawn.offersTrustAgain, "no key was proved, so there is no yes")
        XCTAssertEqual("looks like a different Domovoi, or it was reinstalled", drawn.serverCard.reason)
    }

    /// Row 6 — the web backend answered but its core did not. **Never
    /// rendered as "can't reach it"**: the token is being held, which is not
    /// an outage and never a reason to forget the server (map §10.2).
    func testCoreDownSaysItsOwnThingAndNotCantReach() {
        let drawn = chrome(Row(serverURL: server, unreachable: true, coreDown: true))
        XCTAssertEqual(.localMedia(.outOfReach), drawn.screen)
        XCTAssertEqual("up, but its core isn't answering yet", drawn.serverCard.reason)
        let banner = drawn.banner ?? ""
        XCTAssertTrue(banner.contains("is up but its core is not answering"), banner)
        XCTAssertFalse(banner.contains("Can't reach"), banner)
        XCTAssertFalse(drawn.offersTrustAgain)
    }

    /// Row 7 — reachable, not paired: the pairing screen, which replaces the
    /// whole shell. No pill, no banner, and the card says what it wants.
    func testNotPairedIsThePairingScreenWithNoChrome() {
        let drawn = chrome(Row(serverURL: server, pairingRequired: true))
        XCTAssertEqual(.pairing, drawn.screen)
        XCTAssertNil(drawn.pill)
        XCTAssertNil(drawn.banner)
        XCTAssertTrue(drawn.serverCard.isEnabled)
        XCTAssertEqual("available · needs the household token", drawn.serverCard.reason)
    }

    /// Row 8 — the token was refused, or the phone is being made to wait: one
    /// card in place of the workspace, and no chrome.
    func testTokenTroubleIsOneCardWithNoChrome() {
        let refused = chrome(Row(serverURL: server, trouble: .notRecognised(waitMs: 0)))
        XCTAssertEqual(.tokenTroubleCard(.notRecognised(waitMs: 0)), refused.screen)
        XCTAssertNil(refused.pill)
        XCTAssertNil(refused.banner)
        // A refused token says the same thing the picker says for an unpaired
        // server (`AppShell.kt:205`).
        XCTAssertEqual("available · needs the household token", refused.serverCard.reason)

        let waiting = chrome(Row(serverURL: server, trouble: .waiting(waitMs: 30_000)))
        XCTAssertEqual(.tokenTroubleCard(.waiting(waitMs: 30_000)), waiting.screen)
        XCTAssertEqual("available", waiting.serverCard.reason, "a wait is not a pairing problem")
    }

    /// Row 9 — paired and reachable. The workspace, and the status word comes
    /// from the **live socket** here, not from the reachability verdict.
    func testTheWorkspaceSaysLiveFromTheSocketAndNotFromTheProbe() {
        let live = chrome(Row(serverURL: server, connected: true))
        XCTAssertEqual(.workspace, live.screen)
        XCTAssertEqual("  live", live.pill?.status)
        XCTAssertEqual(label, live.pill?.label)
        XCTAssertFalse(live.pill?.unverified ?? true)
        XCTAssertNil(live.banner, "the container banner is a later batch's, and there is no incident")
        XCTAssertTrue(live.serverCard.isEnabled)

        // The socket down, the server still in reach: the workspace stays up
        // and the word is `offline` — which is NOT what the local shell's
        // `offline` means (map §11.6).
        let socketDown = chrome(Row(serverURL: server, connected: false))
        XCTAssertEqual(.workspace, socketDown.screen)
        XCTAssertEqual("  offline", socketDown.pill?.status)
        XCTAssertEqual("available", socketDown.serverCard.reason)

        // Trusted with nothing pinned: said where the name is.
        let unverified = chrome(Row(serverURL: server, connected: true, unverified: true))
        XCTAssertTrue(unverified.pill?.unverified ?? false)
        XCTAssertEqual("unverified", ShellStrings.unverified)
    }

    /// The two shells do not mean the same thing by their status word, and
    /// the same inputs prove it: reachable and the socket down reads
    /// `offline` in the workspace and `available` in the local shell.
    func testTheTwoShellsStatusWordsAreDifferentFacts() {
        let workspace = chrome(Row(serverURL: server, connected: false))
        let local = chrome(Row(serverURL: server, preferLocal: true, connected: false))
        XCTAssertEqual("  offline", workspace.pill?.status)
        XCTAssertEqual("  available", local.pill?.status)
    }

    /// `try the old token again` is offered only where it can do what it
    /// says: during a wait nothing is sent, so trying would not try.
    func testTheRetryButtonIsOnlyThereWhereItCanWork() {
        XCTAssertTrue(tokenTroubleOffersOldTokenRetry(.notRecognised(waitMs: 0)))
        XCTAssertFalse(tokenTroubleOffersOldTokenRetry(.notRecognised(waitMs: 1)))
        XCTAssertFalse(tokenTroubleOffersOldTokenRetry(.notRecognised(waitMs: 240_000)))
        XCTAssertFalse(tokenTroubleOffersOldTokenRetry(.waiting(waitMs: 0)), "a wait of zero is not this card's case")
        XCTAssertFalse(tokenTroubleOffersOldTokenRetry(.waiting(waitMs: 240_000)))
    }

    /// Every leaf of ``ShellScreen`` is drawn by something: the two that
    /// replace the shell have no pill, the other two have one.
    func testEveryLeafIsDrawn() {
        let states: [Row] = [
            Row(serverURL: ""),
            Row(serverURL: server, preferLocal: true),
            Row(serverURL: server, unreachable: true),
            Row(serverURL: server, identityChanged: true, notOurs: true, canTrustAgain: true),
            Row(serverURL: server, pairingRequired: true),
            Row(serverURL: server, trouble: .notRecognised(waitMs: 0)),
            Row(serverURL: server, connected: true),
        ]
        var leaves: Set<String> = []
        for row in states {
            let drawn = chrome(row)
            leaves.insert("\(drawn.screen)")
            switch drawn.screen {
            case .pairing, .tokenTroubleCard:
                XCTAssertNil(drawn.pill)
            case .localMedia, .workspace:
                XCTAssertNotNil(drawn.pill)
            }
        }
        XCTAssertEqual(7, leaves.count, "seven leaves, each reached by its own inputs")
    }
}
