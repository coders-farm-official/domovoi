import Foundation
import XCTest
@testable import DomovoiKit

/// The three Chat & AI / Privacy sections' logic: where this phone's heads-up
/// answer rests, the dashboard address the two signpost sections hand out,
/// the DEBUG seed a UI case starts from, and the Chat panel's state machine.
///
/// Android specifies none of this. `HeadsUpStore` has no JVM test of its own,
/// `dashboardSettingsUrl` has none, and `ChatSettingsPanel` is a composable —
/// so the three-way note, the filter on read and the trailing-slash rule are
/// tested here for the first time on either phone.
///
/// ### There is no read-after-`await` case in this file, and that is a claim
///
/// The defect class this track watches for — a reply for server A landing
/// under server B — needs a round trip, and **these three sections make no
/// server call at all**. The Chat panel writes `UserDefaults`
/// (`HeadsUpStore.kt:46-47`; the answer reaches the server only as a field in
/// the Chat screen's own send body, `ChatDocsApi.kt:105-107`), and Models and
/// Internet draw one string built from the address already in hand
/// (``AiSettingsDashboard``) because every route behind them is admin-gated
/// and 401s a phone. ``AiSettingsModel`` has no `async` method and no
/// `ApiRead`: there is no `await` to read across. The nearest real hazard is
/// two readers of one store diverging, which
/// ``testAnAnswerWrittenElsewhereIsPickedUpOnRefresh`` is about.
final class AiSettingsHeadsUpBookTests: XCTestCase {

    private func blob(_ json: String) -> Data { Data(json.utf8) }

    /// Nothing stored at all: the heads-up is on and nothing is silenced.
    /// `HeadsUpPrefs()`'s own defaults (`HeadsUpStore.kt:11`).
    func testAFreshPhoneIsWarnedAboutEverything() {
        let book = AiSettingsHeadsUpBook(place: SavedStateBox())
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: false, skip: []), book.prefs)
    }

    /// A second book over the same place is the next launch of the app — the
    /// shape `ChatModelTests.testAPinIsKeptOnThisPhone…` uses, and the only
    /// way to tell a store that writes from one that remembers until quit.
    func testAnAnswerComesBackFromASecondBookOverTheSamePlace() {
        let place = SavedStateBox()
        let first = AiSettingsHeadsUpBook(place: place)
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: true, skip: []), first.setOff(true))
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: true, skip: ["calc"]), first.neverWarn("calc"))
        _ = first.neverWarn("scanned")

        let next = AiSettingsHeadsUpBook(place: place)
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: true, skip: ["calc", "scanned"]), next.prefs)

        // And the switch comes back the other way too, so the test is not
        // passing on a default that happens to agree.
        _ = next.setOff(false)
        XCTAssertFalse(AiSettingsHeadsUpBook(place: place).prefs.off)
        XCTAssertEqual(["calc", "scanned"], AiSettingsHeadsUpBook(place: place).prefs.skip)
    }

    /// `HeadsUpStore.read()` filters the stored set against `DocHeadsUp.KINDS`
    /// (`HeadsUpStore.kt:29`), so a key written by another build is dropped
    /// rather than listed as a row nobody has a word for.
    func testAKindThisBuildDoesNotKnowIsDroppedOnRead() {
        let place = SavedStateBox(blob(#"{"off":false,"skip":["calc","sparkly","scanned"]}"#))
        XCTAssertEqual(["calc", "scanned"], AiSettingsHeadsUpBook(place: place).prefs.skip)
    }

    /// Two ways a blob can be wrong, and both of them read as the default
    /// rather than throwing: a build that cannot parse its own storage must
    /// not be a Settings section that crashes for ever on a phone whose owner
    /// cannot clear it.
    func testABlobThisBuildCannotReadIsTheDefaultAndNotACrash() {
        XCTAssertEqual(
            AiSettingsHeadsUpPrefs(),
            AiSettingsHeadsUpBook(place: SavedStateBox(blob("not json at all"))).prefs
        )
        // A blob from a build that did not have these fields: tolerant, as
        // every `Saved` on this track is.
        XCTAssertEqual(
            AiSettingsHeadsUpPrefs(),
            AiSettingsHeadsUpBook(place: SavedStateBox(blob("{}"))).prefs
        )
        // One field present, the other not.
        XCTAssertEqual(
            AiSettingsHeadsUpPrefs(off: true, skip: []),
            AiSettingsHeadsUpBook(place: SavedStateBox(blob(#"{"off":true}"#))).prefs
        )
    }

    /// The write that is not a read-modify-write of the whole answer: turning
    /// the switch must not clear the silenced set, and clearing one kind must
    /// not turn the switch. Both halves of `copy(...)`
    /// (`HeadsUpStore.kt:38-40`).
    func testOneFieldsWriteLeavesTheOtherAlone() {
        let book = AiSettingsHeadsUpBook(place: SavedStateBox())
        _ = book.neverWarn("calc")
        _ = book.neverWarn("blurry")
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: true, skip: ["blurry", "calc"]), book.setOff(true))
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: true, skip: ["blurry"]), book.warnAgain("calc"))
        // Clearing a kind that is not silenced is not an error and changes
        // nothing — `skip - kind` on Android.
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: true, skip: ["blurry"]), book.warnAgain("calc"))
    }
}

/// ``AiSettingsDashboard/url(serverURL:section:)`` — `dashboardSettingsUrl`
/// (`SettingsModels.kt:56-63`).
final class AiSettingsDashboardTests: XCTestCase {

    /// The two addresses the two signpost sections actually draw, character
    /// for character. `SettingsSection.dashboard` is where the key comes from
    /// (`SettingsSections.swift`), so this also pins that the right section
    /// gets the right key — a copy-paste that leaves `models` behind in the
    /// Internet card is the one failure the two cards can have.
    func testTheTwoSectionsOfThisItemGetTheirOwnAddress() {
        let server = "http://192.168.0.5:6369"
        XCTAssertEqual(
            "http://192.168.0.5:6369/#settings/models",
            AiSettingsDashboard.url(serverURL: server, section: SettingsSection.named("models")?.dashboard)
        )
        XCTAssertEqual(
            "http://192.168.0.5:6369/#settings/internet",
            AiSettingsDashboard.url(serverURL: server, section: SettingsSection.named("internet")?.dashboard)
        )
        // Both sections have a dashboard key at all — the signpost is drawn
        // from it, so a nil here would be a card with no address.
        XCTAssertEqual("models", SettingsSection.named("models")?.dashboard)
        XCTAssertEqual("internet", SettingsSection.named("internet")?.dashboard)
    }

    /// No section: the dashboard's Settings page itself
    /// (`SettingsModels.kt:62`, the `isNullOrEmpty` branch). An **empty**
    /// section is the same as none, which is why the Kotlin tests
    /// `isNullOrEmpty` and not `== null`.
    func testWithoutASectionItIsTheSettingsPageItself() {
        XCTAssertEqual("http://host:6369/#settings", AiSettingsDashboard.url(serverURL: "http://host:6369"))
        XCTAssertEqual(
            "http://host:6369/#settings",
            AiSettingsDashboard.url(serverURL: "http://host:6369", section: "")
        )
    }

    /// Nil is what disables the button and draws
    /// ``AiSettingsStrings/noServer`` instead — so every way of having no
    /// server has to reach it, including an address that is nothing but
    /// slashes.
    func testNoServerIsNoAddress() {
        XCTAssertNil(AiSettingsDashboard.url(serverURL: nil, section: "models"))
        XCTAssertNil(AiSettingsDashboard.url(serverURL: "", section: "models"))
        XCTAssertNil(AiSettingsDashboard.url(serverURL: "   \n ", section: "models"))
        XCTAssertNil(AiSettingsDashboard.url(serverURL: "///", section: "models"))
    }

    /// `trimEnd('/')` removes the whole run of trailing slashes, not one
    /// (`SettingsModels.kt:60`) — and the address is trimmed of whitespace
    /// first, because it came from a text field.
    func testEveryTrailingSlashGoesAndTheAddressIsTrimmed() {
        for stored in ["http://host:6369/", "http://host:6369//", "  http://host:6369///  "] {
            XCTAssertEqual(
                "http://host:6369/#settings/models",
                AiSettingsDashboard.url(serverURL: stored, section: "models"),
                "a stored \(stored) must build one address"
            )
        }
        // A path on the base is left alone: only the trailing slash is the
        // Kotlin's business, and a server behind a prefix keeps it.
        XCTAssertEqual(
            "http://host/domovoi/#settings/internet",
            AiSettingsDashboard.url(serverURL: "http://host/domovoi/", section: "internet")
        )
    }
}

/// ``AiSettingsHeadsUpSeed`` — the DEBUG launch argument, parsed here so the
/// grammar is specified without a Simulator (`DECISIONS.md` §6).
final class AiSettingsHeadsUpSeedTests: XCTestCase {

    private func parse(_ value: String?) -> AiSettingsHeadsUpPrefs? {
        var arguments = ["/path/to/Domovoi.app", "-domovoi-audit-strip", "none"]
        if let value { arguments += [AiSettingsHeadsUpSeed.flag, value] }
        return AiSettingsHeadsUpSeed.parse(arguments)
    }

    func testTheSwitchAndTheSilencedListAreBothSeeded() {
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: false, skip: []), parse("on"))
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: true, skip: []), parse("off"))
        XCTAssertEqual(
            AiSettingsHeadsUpPrefs(off: false, skip: ["calc", "scanned"]),
            parse("on:calc,scanned")
        )
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: true, skip: ["calc"]), parse("off:calc"))
    }

    /// A seed cannot put a row on screen that the store would refuse to keep,
    /// or the UI case would be asserting against a list the app's own read
    /// filters away.
    func testAnUnknownKindIsDroppedFromASeedToo() {
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: false, skip: ["calc"]), parse("on:calc,sparkly"))
        XCTAssertEqual(AiSettingsHeadsUpPrefs(off: false, skip: []), parse("on:sparkly"))
    }

    /// No flag, a flag with nothing after it, and a flag with a word that is
    /// neither: all **nil**, which leaves the phone's real answer alone. A
    /// default here would be a mistyped case quietly resetting the state it
    /// then asserts about.
    func testAMistypedSeedChangesNothing() {
        XCTAssertNil(parse(nil))
        XCTAssertNil(AiSettingsHeadsUpSeed.parse(["/app", AiSettingsHeadsUpSeed.flag]))
        XCTAssertNil(parse(""))
        XCTAssertNil(parse("OFF"))
        XCTAssertNil(parse("yes:calc"))
        XCTAssertNil(parse(":calc"))
    }
}

/// ``AiSettingsModel`` — the Chat panel's state machine
/// (`ChatSettingsPanel.kt:52-78`).
@MainActor
final class AiSettingsModelTests: XCTestCase {

    private typealias Note = AiSettingsModel.Note
    private typealias Kind = AiSettingsModel.Kind

    /// A model over a store that starts in a known state. Named `rig` rather
    /// than `model`, as `ChatModelTests` names its own, so a case can call
    /// its result `model` without shadowing the way it was built.
    private func rig(
        off: Bool = false,
        skip: Set<String> = [],
        place: SavedStateBox = SavedStateBox()
    ) -> (AiSettingsModel, AiSettingsHeadsUpBook) {
        let book = AiSettingsHeadsUpBook(place: place)
        book.replace(AiSettingsHeadsUpPrefs(off: off, skip: skip))
        // `arguments: []` on purpose: the DEBUG seed must not reach into a
        // package test from whatever `swift test` was invoked with.
        return (AiSettingsModel(book: book, arguments: []), book)
    }

    private func calc() -> Kind { Kind(key: "calc", word: "exact sums, averages and dates") }
    private func scanned() -> Kind { Kind(key: "scanned", word: "scanned pages") }

    /// The sign is inverted exactly once — storage says `off`, the control
    /// says `on` (`ChatSettingsPanel.kt:53`, `checked = !prefs.off`). A
    /// `Toggle` bound to `off` instead of `!off` reads backwards, and the
    /// word beside it is the only thing on screen that says which way it is.
    func testTheSwitchReadsTheOppositeOfWhatIsStored() {
        let (fresh, _) = rig()
        XCTAssertTrue(fresh.isOn)
        XCTAssertEqual("on", fresh.switchWord)

        let (silent, book) = rig(off: true)
        XCTAssertFalse(silent.isOn)
        XCTAssertEqual("off", silent.switchWord)

        silent.setOn(true)
        XCTAssertTrue(silent.isOn)
        XCTAssertEqual("on", silent.switchWord)
        XCTAssertFalse(book.prefs.off, "the store keeps the Kotlin's sense, not the control's")
    }

    /// Three states, one note. The `else if` at `ChatSettingsPanel.kt:63` is
    /// the thing being pinned: a phone with the heads-up **off and something
    /// silenced** draws the off-note and **not** the list, because the list
    /// would be describing warnings that are not happening.
    func testThereIsOneNoteAndTheOffNoteWinsOverTheSilencedList() {
        XCTAssertEqual(Note.nothing, rig().0.note)
        XCTAssertEqual(Note.silenced([calc()]), rig(skip: ["calc"]).0.note)

        let (both, _) = rig(off: true, skip: ["calc", "scanned"])
        XCTAssertEqual(Note.off, both.note)

        // And back on, where the list reappears — so the case is about the
        // branch and not about the seed.
        both.setOn(true)
        XCTAssertEqual(Note.silenced([calc(), scanned()]), both.note)
    }

    /// Sorted by the **key**, as `prefs.skip.sorted()` is
    /// (`ChatSettingsPanel.kt:67`) — not by the word. `calc` sorts before
    /// `rewrite` while its word, `exact sums…`, sorts after `rewriting…`, so
    /// a sort on the wrong field fails here.
    func testTheSilencedKindsAreSortedByKeyAndCarryTheirWords() {
        let (model, _) = rig(skip: ["rewrite", "calc", "scanned"])
        guard case .silenced(let kinds) = model.note else { return XCTFail("\(model.note)") }
        XCTAssertEqual(["calc", "rewrite", "scanned"], kinds.map(\.key))
        XCTAssertEqual(
            ["exact sums, averages and dates", "rewriting a whole document", "scanned pages"],
            kinds.map(\.word)
        )
        // The row's identity is the key the button writes about, not its
        // position: that is what makes "warn again" on the second row
        // impossible to bind to the first.
        XCTAssertEqual(kinds.map(\.key), kinds.map(\.id))
    }

    /// "warn again" clears **one** kind, leaves the rest, and is still clear
    /// at the next launch. `calc` and `scanned` discriminate: neither word
    /// contains the other's word or key.
    func testWarnAgainClearsOneKindAndSurvivesALaunch() {
        let place = SavedStateBox()
        let (model, _) = rig(skip: ["calc", "scanned"], place: place)
        model.warnAgain("calc")
        XCTAssertEqual(Note.silenced([scanned()]), model.note)

        // A second model over the same place is the next launch of the app.
        let next = rig2(place: place)
        XCTAssertEqual(Note.silenced([scanned()]), next.note)

        // The last one clears the note altogether rather than leaving an
        // empty heading on screen.
        next.warnAgain("scanned")
        XCTAssertEqual(Note.nothing, next.note)
        XCTAssertEqual(Note.nothing, rig2(place: place).note)
    }

    /// A model over a place that is **already** written, without replacing
    /// what is there — the next launch, which `rig` cannot be because it
    /// seeds.
    private func rig2(place: SavedStateBox) -> AiSettingsModel {
        AiSettingsModel(book: AiSettingsHeadsUpBook(place: place), arguments: [])
    }

    /// The reason the book reads its place on every access instead of caching
    /// at init, which is the one way this item's state can go wrong without a
    /// server: **two readers**. The Chat screen's own "don't warn me about
    /// this again" (`ChatScreen.kt:1454`, the ChatMore item's) writes the
    /// same store while this panel is alive, and a panel that cached at init
    /// would show a stale list and then write that stale set back.
    func testAnAnswerWrittenElsewhereIsPickedUpOnRefresh() {
        let place = SavedStateBox()
        let (panel, _) = rig(place: place)
        XCTAssertEqual(Note.nothing, panel.note)

        // Somebody else's book over the same place — the Chat screen.
        let elsewhere = AiSettingsHeadsUpBook(place: place)
        _ = elsewhere.neverWarn("blurry")

        panel.refresh()
        XCTAssertEqual(Note.silenced([Kind(key: "blurry", word: "blurry photos")]), panel.note)

        // And this panel's write does not throw the other one's away: the
        // store, not a cached copy, is what each write is built on.
        panel.setOn(false)
        XCTAssertEqual(["blurry"], elsewhere.prefs.skip)
        XCTAssertTrue(elsewhere.prefs.off)
    }

    #if DEBUG
    /// The seed reaches the model, because a UI case has no other way to
    /// start from a known switch and a known silenced list.
    func testTheDebugSeedIsWhatAUiCaseStartsFrom() {
        let book = AiSettingsHeadsUpBook(place: SavedStateBox())
        let seeded = AiSettingsModel(
            book: book,
            arguments: ["/app", AiSettingsHeadsUpSeed.flag, "off:calc,scanned"]
        )
        XCTAssertFalse(seeded.isOn)
        XCTAssertEqual(Note.off, seeded.note)
        seeded.setOn(true)
        XCTAssertEqual(Note.silenced([calc(), scanned()]), seeded.note)
        XCTAssertEqual(["calc", "scanned"], book.prefs.skip, "the seed went to the store, not to the model")
    }
    #endif
}
