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

    // MARK: - §3.4, the four facts

    /// **The one test this file exists for.** Each fact gets its own
    /// sentence, and no two facts share one.
    func testEachNothingFoundLineStatesWhatTheAppActuallyDid() {
        // 254 probed, every one silent.
        XCTAssertEqual(
            .nothingAnswered(port: 6369),
            serverPickerNotice(outcome: .swept(servers: []), scanning: false, rowsEmpty: true)
        )
        // Not on wifi: nothing was probed.
        XCTAssertEqual(
            .notOnWifi,
            serverPickerNotice(outcome: .notOnLan, scanning: false, rowsEmpty: true)
        )
        // On wifi, no address of our own: nothing was probed.
        XCTAssertEqual(
            .noAddressYet,
            serverPickerNotice(outcome: .noLocalNetwork, scanning: false, rowsEmpty: true)
        )
        // Local Network refused: nothing could answer.
        XCTAssertEqual(
            .localNetworkDenied,
            serverPickerNotice(outcome: .localNetworkDenied, scanning: false, rowsEmpty: true)
        )

        // And the four sentences are four sentences, so no mapping mistake
        // can hide behind two of them being the same words.
        let said = [
            ServerPickerNotice.nothingAnswered(port: 6369),
            .notOnWifi,
            .noAddressYet,
            .localNetworkDenied,
        ].map { $0.text ?? "" }
        XCTAssertEqual(4, Set(said).count)
        for sentence in said { XCTAssertFalse(sentence.isEmpty) }
    }

    /// Nothing has been probed at all, so nothing is claimed. This is the
    /// state the panel opens in.
    func testAPanelThatHasNotSweptYetSaysNothingAtAll() {
        XCTAssertEqual(
            .nothing,
            serverPickerNotice(outcome: nil, scanning: false, rowsEmpty: true)
        )
        XCTAssertNil(serverPickerNotice(outcome: nil, scanning: false, rowsEmpty: true).text)
    }

    /// A sweep in flight has not failed to find anything yet.
    func testASweepInFlightClaimsNothing() {
        XCTAssertEqual(
            .nothing,
            serverPickerNotice(outcome: .swept(servers: []), scanning: true, rowsEmpty: true)
        )
    }

    /// "Nothing answered" is the only one of the four that the list's own
    /// emptiness decides, because it is the only one that is a claim about
    /// the list.
    func testNothingAnsweredIsWithheldWhenThereIsARowToTap() {
        let hit = FoundDomovoi(url: server, name: nil, identity: nil)
        XCTAssertEqual(
            .nothing,
            serverPickerNotice(outcome: .swept(servers: [hit]), scanning: false, rowsEmpty: false)
        )
    }

    /// The three facts about the sweep are said whether or not there is a row
    /// — "not on wifi" is true and worth saying with a saved server in the
    /// list, which is what Android's separate off-LAN line is for.
    func testTheFactsAboutTheSweepAreSaidEvenWithASavedServerListed() {
        XCTAssertEqual(
            .notOnWifi,
            serverPickerNotice(outcome: .notOnLan, scanning: false, rowsEmpty: false)
        )
        XCTAssertEqual(
            .noAddressYet,
            serverPickerNotice(outcome: .noLocalNetwork, scanning: false, rowsEmpty: false)
        )
    }

    /// The loud ones are the ones a person can act on.
    func testTheLineIsLoudOnlyWhereThereIsSomethingToDoAboutIt() {
        XCTAssertTrue(ServerPickerNotice.notOnWifi.isWarning)
        XCTAssertTrue(ServerPickerNotice.noAddressYet.isWarning)
        XCTAssertTrue(ServerPickerNotice.localNetworkDenied.isWarning)
        XCTAssertFalse(ServerPickerNotice.nothingAnswered(port: 6369).isWarning)
        XCTAssertFalse(ServerPickerNotice.nothing.isWarning)
    }

    /// The owner's open question, pinned as a seam: **no notice offers a
    /// Settings button today.** When he says yes, this test is what says the
    /// change was deliberate.
    func testNoNoticeOffersTheSettingsButtonYet() {
        for notice in [
            ServerPickerNotice.nothing,
            .nothingAnswered(port: 6369),
            .notOnWifi,
            .noAddressYet,
            .localNetworkDenied,
        ] {
            XCTAssertFalse(notice.offersTheSettingsButton, "\(notice) grew a button nobody decided on")
        }
    }

    /// The two iOS-only sentences are the drafts, character for character,
    /// with em dashes rather than hyphens — the failure this kind of test
    /// exists for.
    func testTheTwoIOSOnlySentencesAreTheDraftsWithEmDashes() {
        XCTAssertEqual(IOSOnlyStrings.localNetworkDenied, ServerPickerNotice.localNetworkDenied.text)
        XCTAssertEqual(IOSOnlyStrings.noAddressYet, ServerPickerNotice.noAddressYet.text)
        XCTAssertTrue(IOSOnlyStrings.localNetworkDenied.contains("\u{2014}"))
        XCTAssertTrue(IOSOnlyStrings.noAddressYet.contains("\u{2014}"))
        XCTAssertFalse(IOSOnlyStrings.localNetworkDenied.contains(" - "))
        XCTAssertFalse(IOSOnlyStrings.noAddressYet.contains(" - "))
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
