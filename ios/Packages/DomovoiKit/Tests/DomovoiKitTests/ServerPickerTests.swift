import XCTest
@testable import DomovoiKit

/// The picker's decisions: which rows, which pill, which forget, and **which
/// "nothing was found" line is a true statement about what the app did**.
///
/// The twin of `android/.../test/ui/shell/ServerScanTest.kt` plus the pure
/// parts of its picker.
@MainActor
final class ServerPickerTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let other = "http://10.0.0.7:6369"

    /// ``serverPickerNotice(outcome:scanning:rowsEmpty:localNetworkHasAnswered:port:)``
    /// for an install where something **has** answered before.
    ///
    /// Which is every test in this file except the two about a first install,
    /// and those call the function directly with `false` so the difference is
    /// on the screen rather than in a default.
    private func notice(
        _ outcome: SweepOutcome?,
        scanning: Bool = false,
        rowsEmpty: Bool = true
    ) -> ServerPickerNotice {
        serverPickerNotice(
            outcome: outcome,
            scanning: scanning,
            rowsEmpty: rowsEmpty,
            localNetworkHasAnswered: true
        )
    }

    // MARK: - §3.4, the five facts

    /// **The one test this file exists for.** Each fact gets its own
    /// sentence, and no two facts share one.
    func testEachNothingFoundLineStatesWhatTheAppActuallyDid() {
        // 254 probed, every one silent.
        XCTAssertEqual(
            .nothingAnswered(port: 6369),
            notice(.swept(servers: []))
        )
        // Not on wifi: nothing was probed.
        XCTAssertEqual(
            .notOnWifi,
            notice(.notOnLan)
        )
        // On wifi, no address of our own: nothing was probed.
        XCTAssertEqual(
            .noAddressYet,
            notice(.noLocalNetwork)
        )
        // Local Network refused: nothing could answer.
        XCTAssertEqual(
            .localNetworkDenied,
            notice(.localNetworkDenied)
        )

        // Nothing answered, and nothing ever has: the permission, not the
        // house. The fact the sweep measured is still in there — see
        // ``testAFirstInstallIsNotToldItsServerIsDown``.
        XCTAssertEqual(
            .nothingAnsweredYet(port: 6369),
            serverPickerNotice(
                outcome: .swept(servers: []),
                scanning: false,
                rowsEmpty: true,
                localNetworkHasAnswered: false
            )
        )

        // And the five sentences are five sentences, so no mapping mistake
        // can hide behind two of them being the same words.
        let said = [
            ServerPickerNotice.nothingAnswered(port: 6369),
            .nothingAnsweredYet(port: 6369),
            .notOnWifi,
            .noAddressYet,
            .localNetworkDenied,
        ].map { $0.text ?? "" }
        XCTAssertEqual(5, Set(said).count)
        for sentence in said { XCTAssertFalse(sentence.isEmpty) }
    }

    /// Nothing has been probed at all, so nothing is claimed. This is the
    /// state the panel opens in.
    func testAPanelThatHasNotSweptYetSaysNothingAtAll() {
        XCTAssertEqual(
            .nothing,
            notice(nil)
        )
        XCTAssertNil(notice(nil).text)
    }

    /// A sweep in flight has not failed to find anything yet.
    func testASweepInFlightClaimsNothing() {
        XCTAssertEqual(
            .nothing,
            notice(.swept(servers: []), scanning: true)
        )
    }

    /// "Nothing answered" is the only one of the four that the list's own
    /// emptiness decides, because it is the only one that is a claim about
    /// the list.
    func testNothingAnsweredIsWithheldWhenThereIsARowToTap() {
        let hit = FoundDomovoi(url: server, name: nil, identity: nil)
        XCTAssertEqual(
            .nothing,
            notice(.swept(servers: [hit]), rowsEmpty: false)
        )
    }

    /// The three facts about the sweep are said whether or not there is a row
    /// — "not on wifi" is true and worth saying with a saved server in the
    /// list, which is what Android's separate off-LAN line is for.
    func testTheFactsAboutTheSweepAreSaidEvenWithASavedServerListed() {
        XCTAssertEqual(
            .notOnWifi,
            notice(.notOnLan, rowsEmpty: false)
        )
        XCTAssertEqual(
            .noAddressYet,
            notice(.noLocalNetwork, rowsEmpty: false)
        )
    }

    /// The loud ones are the ones a person can act on.
    func testTheLineIsLoudOnlyWhereThereIsSomethingToDoAboutIt() {
        XCTAssertTrue(ServerPickerNotice.notOnWifi.isWarning)
        XCTAssertTrue(ServerPickerNotice.noAddressYet.isWarning)
        XCTAssertTrue(ServerPickerNotice.localNetworkDenied.isWarning)
        XCTAssertFalse(ServerPickerNotice.nothingAnswered(port: 6369).isWarning)
        XCTAssertFalse(ServerPickerNotice.nothing.isWarning)
        // The one that breaks the rule, on purpose: the person CAN act on it,
        // and it is quiet anyway, because the app does not know that the
        // permission is what is wrong. The sentence hedges and the colour
        // agrees with the sentence (``ServerPickerNotice/isWarning``).
        XCTAssertFalse(ServerPickerNotice.nothingAnsweredYet(port: 6369).isWarning)
    }

    /// The owner's open question, pinned as a seam: **no notice offers a
    /// Settings button today.** When he says yes, this test is what says the
    /// change was deliberate.
    func testNoNoticeOffersTheSettingsButtonYet() {
        for notice in [
            ServerPickerNotice.nothing,
            .nothingAnswered(port: 6369),
            .nothingAnsweredYet(port: 6369),
            .notOnWifi,
            .noAddressYet,
            .localNetworkDenied,
        ] {
            XCTAssertFalse(notice.offersTheSettingsButton, "\(notice) grew a button nobody decided on")
        }
    }

    /// The iOS-only sentences are the drafts, character for character, with
    /// em dashes rather than hyphens — the failure this kind of test exists
    /// for.
    func testTheIOSOnlySentencesAreTheDraftsWithEmDashes() {
        XCTAssertEqual(IOSOnlyStrings.localNetworkDenied, ServerPickerNotice.localNetworkDenied.text)
        XCTAssertEqual(IOSOnlyStrings.noAddressYet, ServerPickerNotice.noAddressYet.text)
        XCTAssertEqual(
            IOSOnlyStrings.nothingAnsweredYet(port: 6369),
            ServerPickerNotice.nothingAnsweredYet(port: 6369).text
        )
        for sentence in [
            IOSOnlyStrings.localNetworkDenied,
            IOSOnlyStrings.noAddressYet,
            IOSOnlyStrings.nothingAnsweredYet(port: 6369),
        ] {
            XCTAssertTrue(sentence.contains("\u{2014}"), "\(sentence) lost its em dash")
            XCTAssertFalse(sentence.contains(" - "), "\(sentence) has a hyphen for a dash")
        }
    }

    /// **The second test this file exists for**, and the one the owner found by
    /// installing the app.
    ///
    /// The connection sheet sweeps as it opens, and on a first install that
    /// sweep is the connection iOS puts its Local Network alert up for: every
    /// probe fails while the alert is on screen, the sweep ends `.swept` with
    /// nothing found, and there is no API that says the permission was the
    /// reason (TN3179, which is why ``SweepOutcome/localNetworkDenied`` cannot
    /// be reached here). The line that came out of that said
    /// *"is the web backend running?"* about a server that was running.
    ///
    /// So: the fact the sweep measured, then the permission — and **not** a
    /// word about his backend until something has answered this install at
    /// least once.
    func testAFirstInstallIsNotToldItsServerIsDown() {
        let first = serverPickerNotice(
            outcome: .swept(servers: []),
            scanning: false,
            rowsEmpty: true,
            localNetworkHasAnswered: false
        )
        XCTAssertEqual(.nothingAnsweredYet(port: 6369), first)

        let line = first.text ?? ""
        XCTAssertTrue(
            line.contains("nothing answered on port 6369"),
            "the measured fact has to come first, or a person whose server really "
                + "is down has no way in from this line: \(line)"
        )
        XCTAssertTrue(line.contains("local network"), "the likely cause is not named: \(line)")
        XCTAssertFalse(
            line.contains("backend"),
            "a first install is told its own server is the problem: \(line)"
        )

        // And the moment something has answered, the permission is settled
        // and the other sentence — the one that does ask about the backend —
        // is the right one.
        XCTAssertEqual(.nothingAnswered(port: 6369), notice(.swept(servers: [])))
        XCTAssertTrue(ShellStrings.nothingAnswered(port: 6369).contains("backend"))
    }

    /// The flag changes **one** line and no other. A first install that is
    /// off wifi is told it is off wifi, not asked about a permission that
    /// could not have been the problem — nothing was probed.
    func testInstallHistoryChangesNothingAboutASweepThatProbedNothing() {
        for outcome in [SweepOutcome.notOnLan, .noLocalNetwork, .localNetworkDenied] {
            let fresh = serverPickerNotice(
                outcome: outcome,
                scanning: false,
                rowsEmpty: true,
                localNetworkHasAnswered: false
            )
            XCTAssertEqual(notice(outcome), fresh, "install history moved \(outcome)")
        }
        XCTAssertEqual(
            .nothing,
            serverPickerNotice(
                outcome: nil,
                scanning: false,
                rowsEmpty: true,
                localNetworkHasAnswered: false
            )
        )
    }

    // MARK: - §3.2, the label

    func testTheLabelCountsWhileItScansAndNamesTheEmptinessAfterwards() {
        var progress = ServerScanProgress()
        progress.start(total: 254)
        progress.report(done: 137, total: 254, found: 0)
        XCTAssertEqual(
            "scanning 137/254\u{2026}",
            serverPickerSectionLabel(progress: progress, swept: false, foundEmpty: true, knownEmpty: true)
        )

        progress.finish()
        XCTAssertEqual(
            ShellStrings.noDomovoisFound,
            serverPickerSectionLabel(progress: progress, swept: true, foundEmpty: true, knownEmpty: true)
        )
        // A phone with a saved server it cannot see has not found nothing.
        XCTAssertEqual(
            ShellStrings.domovois,
            serverPickerSectionLabel(progress: progress, swept: true, foundEmpty: true, knownEmpty: false)
        )
    }

    /// Progress never goes backwards, whatever order forty probes report in.
    func testProgressNeverGoesBackwards() {
        var progress = ServerScanProgress()
        progress.start(total: 254)
        progress.report(done: 90, total: 254, found: 1)
        progress.report(done: 41, total: 254, found: 0)
        XCTAssertEqual(90, progress.done)
        XCTAssertEqual(1, progress.found)
    }

    // MARK: - §3.3, the rows

    /// Known first in saved order, then this sweep's finds that no row covers
    /// — and a saved row the sweep saw is one row, not two.
    func testKnownComeFirstInSavedOrderAndASightingDoesNotDuplicateARow() {
        let known = [KnownServer(url: other, name: "study"), KnownServer(url: server, name: "kitchen-box")]
        let found = [
            FoundDomovoi(url: server, name: "kitchen-box-renamed", identity: TestIdentity.real.pin),
            FoundDomovoi(url: "http://10.0.0.100:6369", name: nil, identity: nil),
        ]
        let rows = serverPickerRows(known: known, found: found, active: server)

        XCTAssertEqual([other, server, "http://10.0.0.100:6369"], rows.map(\.url))
        XCTAssertEqual([true, true, false], rows.map(\.saved))
        XCTAssertEqual([false, true, false], rows.map(\.active))
        XCTAssertEqual([false, true, true], rows.map(\.online))
        XCTAssertEqual(
            "study", rows[0].name,
            "a saved row keeps its saved name, not what a later sighting called it"
        )
        XCTAssertEqual(
            TestIdentity.real.pin, rows[1].advertised,
            "but it does carry the identity THIS sweep saw, which is what a trust decision would pin"
        )
        XCTAssertNil(rows[0].advertised, "a saved row nothing answered at offers no key to pin")
    }

    /// A server with no `bot_name` is `domovoi`, not blank.
    func testAServerThatOfferedNoNameIsCalledDomovoi() {
        let rows = serverPickerRows(known: [], found: [FoundDomovoi(url: server)], active: "")
        XCTAssertEqual(ShellStrings.unnamedServer, rows[0].displayName)
        XCTAssertEqual("10.0.0.42:6369", rows[0].address)
    }

    /// `connected` only while the server is the one in use; otherwise merely
    /// `selected`, and every other row offers `use`.
    func testTheActiveRowIsConnectedOnlyWhileItIsTheOneInUse() {
        let rows = serverPickerRows(
            known: [KnownServer(url: server, name: nil), KnownServer(url: other, name: nil)],
            found: [],
            active: server
        )
        XCTAssertEqual(.connected, rows[0].affordance(inUse: true))
        XCTAssertEqual(.selected, rows[0].affordance(inUse: false))
        XCTAssertEqual(.use, rows[1].affordance(inUse: true))
        XCTAssertEqual(ShellStrings.connected, ServerRowAffordance.connected.text)
        XCTAssertEqual(ShellStrings.selected, ServerRowAffordance.selected.text)
        XCTAssertEqual(ShellStrings.use, ServerRowAffordance.use.text)
    }

    /// The active row cannot be tapped to select; every other row can.
    func testTheActiveRowIsNotSelectable() {
        let rows = serverPickerRows(
            known: [KnownServer(url: server, name: nil), KnownServer(url: other, name: nil)],
            found: [],
            active: server
        )
        XCTAssertFalse(rows[0].isSelectable)
        XCTAssertTrue(rows[1].isSelectable)
    }

    /// **The asymmetry, ported as it is.** The active row asks first, a saved
    /// row forgets at once, and a row this sweep merely found has nothing to
    /// forget. §11.8 asks the owner whether the middle one should confirm;
    /// this test is what fails the day he says yes, rather than the change
    /// happening quietly.
    func testTheActiveRowAsksFirstAndASavedRowDoesNot() {
        let rows = serverPickerRows(
            known: [KnownServer(url: server, name: nil), KnownServer(url: other, name: nil)],
            found: [FoundDomovoi(url: "http://10.0.0.100:6369")],
            active: server
        )
        XCTAssertEqual(.confirmFirst, rows[0].forgetAffordance)
        XCTAssertEqual(.immediate, rows[1].forgetAffordance)
        XCTAssertEqual(.none, rows[2].forgetAffordance)
    }

    /// Multiple hits are all kept, with no auto-pick and **Android's plain
    /// string sort**, so `…100` comes before `…20`. Ported as it is because
    /// the two apps should list a house's servers in the same order; a
    /// numeric sort is the owner's call.
    func testMultipleHitsAreAllKeptInAndroidsStringOrder() async {
        let hits = await Discovery.sweep(
            ["http://10.0.0.100:6369", "http://10.0.0.20:6369", "http://10.0.0.3:6369"],
            onProgress: { _, _, _ in },
            probe: { FoundDomovoi(url: $0) }
        )
        XCTAssertEqual(
            ["http://10.0.0.100:6369", "http://10.0.0.20:6369", "http://10.0.0.3:6369"],
            hits.map(\.url)
        )
    }

    // MARK: - §3.6, the switch

    /// The phone side is lit when it was chosen, when there is no server, and
    /// when the server cannot be used — the last being the one that is easy
    /// to get wrong.
    func testThePhoneSideIsLitWheneverTheServerCannotBeUsed() {
        XCTAssertTrue(connectionShowsThePhone(
            preferLocal: true, serverURL: server, choice: .available(label: "s")
        ))
        XCTAssertTrue(connectionShowsThePhone(
            preferLocal: false, serverURL: "", choice: .noServer
        ))
        XCTAssertTrue(connectionShowsThePhone(
            preferLocal: false, serverURL: server, choice: .notOurs(label: "s")
        ))
        XCTAssertTrue(connectionShowsThePhone(
            preferLocal: false, serverURL: server, choice: .coreDown(label: "s")
        ))
        XCTAssertFalse(connectionShowsThePhone(
            preferLocal: false, serverURL: server, choice: .available(label: "s")
        ))
        XCTAssertFalse(
            connectionShowsThePhone(
                preferLocal: false, serverURL: server,
                choice: .available(label: "s", needsPairing: true)
            ),
            "a server that wants the household token can still be used: that is what pairing is for"
        )
    }
}
