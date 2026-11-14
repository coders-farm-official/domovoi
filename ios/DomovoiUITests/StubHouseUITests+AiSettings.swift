import XCTest

/// Settings → **Models**, **Chat**, **Internet** — the AiSettings item.
///
/// `design-notes/ios/overnight/aisettings/MAP.md` §3.5 is what these cases
/// were planned from; the three sections are one real panel
/// (``AiSettingsChatPanel``) and one signpost drawn twice
/// (``AiSettingsDashboardPanel``).
///
/// ### There is no network state to test here, and that is the finding
///
/// Neither panel makes a server call. The Chat panel is phone-local
/// (`UserDefaults`, via `AiSettingsHeadsUpBook`) and the signpost is one
/// address built from the server URL the app already holds. So:
///
/// * **No loading state, no error state.** Nothing can fail. The one failure
///   branch either panel has is the signpost's "no server connected", and
///   that is **unreachable from a UI case**: every launch here passes
///   `-domovoi-server`, and `launch(server: false)` is the local-media shell
///   with no workspace and therefore no Settings to push. It is covered where
///   it can be — `AiSettingsDashboard.url` answers nil for nil, blank and
///   all-whitespace in the package tests — and that is said here so the gap
///   is a recorded one rather than an oversight.
/// * **The empty state is the Chat panel with nothing silenced**, which is
///   what a fresh phone is in and what
///   ``testAiSettings_ChatPanelTogglesAndKeepsTheOffNote()`` starts from.
///
/// ### Phone-local state is seeded at launch, not by the house
///
/// `control("reset-fixtures", …)` cannot reset a setting that never reaches
/// the house, and `UserDefaults` survives between cases in one run — so the
/// second case of a run would otherwise start from whatever the first left
/// behind. ``aiSettingsLaunch(seed:mode:)`` passes the DEBUG argument
/// `-domovoi-aisettings-headsup`, which `AiSettingsHeadsUpSeed` parses.
/// It is also the only way to reach a non-empty silenced list at all: the
/// control that fills one is the Chat screen's card, which is the ChatMore
/// item's.
extension StubHouseUITests {

    // MARK: - what the two panels say, spelled out

    /// The strings these cases match on, spelled out rather than imported —
    /// this file's standing rule, for the reason ``StubChat`` gives: a query
    /// that silently followed a renamed constant would stop testing what it
    /// claims to. These are `ChatSettingsPanel.kt` and `SettingsScreen.kt`
    /// word for word.
    enum AiSettingsUnderTest {
        /// `ChatSettingsPanel.kt:47`.
        static let headsUpTitle = "Heads-up before shaky results"
        /// `ChatSettingsPanel.kt:84`.
        static let offNote =
            "Off: exact sums and scanned pages still get a one-line note under the answer."
        /// `ChatSettingsPanel.kt:64`.
        static let notWarnedHeading = "not warned about on this phone:"
        /// `ChatSettingsPanel.kt:77`.
        static let manualLink = "what Domovoi is good and not so good at"
        /// `ChatSettingsPanel.kt:57` — the switch's label **is** its state.
        static let on = "on"
        static let off = "off"

        /// `HeadsUpStore.kt:52-57`. Three kinds that discriminate: no one of
        /// these words contains another's, and none contains another's key.
        static let calcWord = "exact sums, averages and dates"
        static let scannedWord = "scanned pages"
        static let compareWord = "comparing parts of a long document"

        /// `SettingsScreen.kt:411`.
        static let dashboardTitle = "On the dashboard"
        /// `SettingsScreen.kt:434`.
        static let openOnTheDashboard = "Open on the dashboard"

        /// The group list rows and section rows these cases walk through
        /// (``SettingsGroup/label``, ``SettingsSection/label``). `internet`
        /// is in **Privacy**, not Chat & AI — the two groups are why four of
        /// these exist.
        static let chatAndAiGroup = "Chat & AI"
        static let privacyGroup = "Privacy"
        static let chatSection = "Chat"
        static let modelsSection = "Models"
        static let internetSection = "Internet"

        /// `SettingsScreen.kt:413-419` with the section's own label
        /// interpolated, as the app does. The two sections' cards differ in
        /// **this sentence and the address only**, so a case that asserts
        /// both catches a slot filled with the other section's key.
        static func paragraph(_ section: String) -> String {
            "\(section) is changed on the Domovoi dashboard, where the admin signs in, "
                + "so changes are handled properly. The dashboard runs at the same address "
                + "this app is connected to."
        }

        /// What ``AiSettingsDashboard/url(serverURL:section:)`` must build
        /// from the launch's own `-domovoi-server 127.0.0.1:6601`.
        ///
        /// `ServerAddress.fromTyped` puts the scheme on
        /// (`ServerAddress.swift:30`), so the saved address is
        /// `http://127.0.0.1:6601` — and this is **the launch's** value, not
        /// a stored one, which is what makes the assertion catch a card
        /// reading a stale address.
        static func address(_ dashboardKey: String) -> String {
            "http://127.0.0.1:6601/#settings/\(dashboardKey)"
        }

        /// The identifier ``AiSettingsChatPanel`` gives each row's button.
        /// Every one of them *reads* `warn again`, so the label cannot say
        /// which row a query found.
        static func warnAgainButton(_ kind: String) -> String {
            "aisettings-warn-again-\(kind)"
        }
    }

    // MARK: - getting there

    /// Launch into Settings with this phone's heads-up answer seeded.
    ///
    /// Not a parameter added to ``launch(mode:paired:server:port:strip:push:pushDelay:)``:
    /// that is a shared file and twenty branches editing it collide in a
    /// train. Everything else about the launch is the same, deliberately —
    /// same port, same token, same push.
    ///
    /// - Parameter seed: ``AiSettingsHeadsUpSeed``'s grammar —
    ///   `"on"`, `"off"`, `"off:calc,scanned"`. Passing nil leaves the phone's
    ///   own answer alone, which no case here wants.
    ///
    /// `fileprivate`, like every helper in this file and like
    /// `StubHouseUITests+VoiceSettings.swift`'s: twenty overnight branches add
    /// files to one test target, and an internal member of the shared class
    /// that another branch also names is an `invalid redeclaration` that drops
    /// a whole branch from a train (NIGHT-RULES §2).
    @MainActor
    fileprivate func aiSettingsLaunch(seed: String, mode: String = StubHouseUITests.ordinaryTypeSize) -> XCUIApplication {
        let app = freshApp()
        app.launchArguments = [
            "-domovoi-server", "127.0.0.1:6601",
            "-domovoi-token", Self.token,
            "-domovoi-aisettings-headsup", seed,
            "-domovoi-audit-push", "Settings",
            "-domovoi-audit-push-delay", "\(Self.pushDelay)",
            "-UIPreferredContentSizeCategoryName", mode,
        ]
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        return app
    }

    /// Walk Settings' index → group → section, and answer whether the section
    /// was reached.
    ///
    /// ### Why a topmost-wins search and not a subscript
    ///
    /// `Chat` is **two buttons**: this section's row, and the phone bar's Chat
    /// tab — the bar is laid out outside the navigation stack
    /// (`WorkspaceView.swift:74-77`), so it is on screen over a pushed
    /// Settings and `app.buttons["Chat"]` is ambiguous. This is the same
    /// family of trap as `firstMatch` taking a keyboard key that shares a
    /// label, and it is bounded the same way: by geometry. The section row is
    /// in the scroll content near the top of the screen and the tab is at the
    /// very bottom, so the **topmost** match is the one in the content.
    ///
    /// The alternative — `launch(strip: "phoneBar")` — would take the bar
    /// away and make the query unambiguous, but then no case here would ever
    /// have audited or photographed the screen a person actually sees.
    ///
    /// ### The wait between the two taps, and why it is not the section's own
    ///
    /// ``aiSettingsTapTopmostButton(_:_:orFail:timeout:)`` waits for its label
    /// to exist before resolving the matches. For `Chat` that wait is
    /// **satisfied by the wrong element**: the phone bar's Chat tab exists from
    /// launch, so the wait returns immediately and the snapshot
    /// `allElementsBoundByIndex` resolves against can still predate the group
    /// list — one match, the tab, tapped, and the case lands on the Chat
    /// conversation and then fails naming the panel. So the group list is
    /// waited for by a marker **only it draws**: `all settings`
    /// (`RouteScreen.swift:276`, the `.group` case alone). Four of the six
    /// cases here go through `Chat`.
    @MainActor
    fileprivate func reachAiSettingsSection(_ app: XCUIApplication, group: String, section: String) -> Bool {
        guard aiSettingsTapTopmostButton(
            app, group,
            orFail: "Settings never drew the \(group) group row — it may not have been pushed",
            timeout: 30
        ) else { return false }
        guard app.buttons[ShellStringsUnderTest.allSettings].waitForExistence(timeout: 15) else {
            XCTFail("the \(group) group list never drew — no `all settings` button after the group tap")
            return false
        }
        return aiSettingsTapTopmostButton(
            app, section, orFail: "the \(group) group list has no \(section) row"
        )
    }

    /// Wait for a button with this label to exist, then tap the **topmost**
    /// one — see ``reachAiSettingsSection(_:group:section:)``.
    ///
    /// The wait and the choice are two steps on purpose. A query over
    /// `allElementsBoundByIndex` evaluates **once, now**: resolving it before
    /// the screen has drawn answers "none", and the fallback would then be
    /// the bare subscript — which raises "multiple matching elements" at the
    /// tap, on exactly the ambiguous label this helper exists for.
    ///
    /// Named for the item and `fileprivate` even so: the ambiguous-label trap
    /// is universal tonight, so `tapTopmostButton` is a name another branch
    /// would plausibly take on the same shared class — see
    /// ``aiSettingsLaunch(seed:mode:)``.
    @discardableResult
    @MainActor
    fileprivate func aiSettingsTapTopmostButton(
        _ app: XCUIApplication, _ label: String, orFail why: String, timeout: TimeInterval = 15
    ) -> Bool {
        // `firstMatch`, not the subscript, for the existence check: a
        // subscript element raises on an ambiguous label even to be asked
        // whether it exists.
        let present = app.buttons.matching(identifier: label).firstMatch
        guard present.waitForExistence(timeout: timeout) else {
            XCTFail(why)
            return false
        }
        aiSettingsTopmostButton(app, label).tap()
        return true
    }

    /// The highest match on screen for a button label, resolved now.
    @MainActor
    fileprivate func aiSettingsTopmostButton(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        let matches = app.buttons.matching(identifier: label).allElementsBoundByIndex
        guard matches.count > 1 else { return app.buttons.matching(identifier: label).firstMatch }
        return matches.min { $0.frame.minY < $1.frame.minY }
            ?? app.buttons.matching(identifier: label).firstMatch
    }

    /// The switch, by the word it is showing. ``AiSettingsModel/switchWord``
    /// is the state, so this existing at all is the assertion.
    @MainActor
    fileprivate func aiSettingsSwitch(_ app: XCUIApplication, reading word: String) -> XCUIElement {
        app.switches[word]
    }

    /// Flip the heads-up switch.
    ///
    /// Tapped near its **trailing** edge, not at its centre: the row is the
    /// width of the card with a two-letter label at the leading end, so its
    /// centre is empty space between the label and the control. The same
    /// normalised-offset trick the text fields in this file use.
    @MainActor
    fileprivate func flipAiSettingsSwitch(_ app: XCUIApplication, reading word: String) {
        // Not named `control`: this class already has a ``control(_:_:port:what:)``
        // for the house's test routes, and one shadowing the other in a
        // helper about a switch is a reading trap for no gain.
        let toggle = aiSettingsSwitch(app, reading: word)
        XCTAssertTrue(toggle.waitForExistence(timeout: 10), "no switch reading `\(word)`")
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
    }

    /// A `staticText` matched on its **whole** label, through a predicate.
    ///
    /// `app.staticTexts[label]` cannot be used for the dashboard paragraph.
    /// XCTest refuses a string identifier longer than 128 characters —
    /// `NSInternalInconsistencyException: Invalid query - string identifier
    /// '…' exceeds maximum length of 128 characters` — and that sentence is
    /// 166 of them, so the subscript **threw** rather than answering false:
    /// round 1 lost both halves of
    /// ``testAiSettings_ModelsAndInternetSendYouToTheDashboardAddress()`` to
    /// it while the app was drawing the paragraph correctly (the train's
    /// `aisettings-models` tree has it, verbatim, at `0x102635cc0`).
    ///
    /// Still an **equality** on the full label, and deliberately not a
    /// `CONTAINS`: the negative half of that case asserts the Internet card
    /// does *not* carry the Models sentence, and a loosened match would be a
    /// case that passes because it asked less.
    @MainActor
    fileprivate func aiSettingsExactText(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        app.staticTexts.matching(NSPredicate(format: "label == %@", label)).firstMatch
    }

    // MARK: - the Chat panel

    /// The panel draws, the switch is the state, and the off-note belongs to
    /// one state only.
    ///
    /// Starts from the **empty state** — the heads-up on, nothing silenced,
    /// which is a fresh phone — so it also asserts the two conditional blocks
    /// are absent when they should be. Three screens' worth of assertion on
    /// one launch, which is what the night's batching asks for.
    ///
    /// What it catches: a `Toggle` bound to `off` rather than `!off` (the sign
    /// is inverted once, in the Kotlin at `ChatSettingsPanel.kt:53`, and
    /// ``AiSettingsModel/isOn`` is the one place that does it here); the
    /// off-note drawn in the wrong state or always; a store that does not
    /// write, because the switch is read back after the flip.
    @MainActor
    func testAiSettings_ChatPanelTogglesAndKeepsTheOffNote() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the house back to its seed")
        let app = aiSettingsLaunch(seed: "on")
        XCTAssertTrue(
            reachAiSettingsSection(
                app, group: AiSettingsUnderTest.chatAndAiGroup,
                section: AiSettingsUnderTest.chatSection
            )
        )

        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.headsUpTitle].waitForExistence(timeout: 15),
            "the Chat section did not draw the heads-up card"
        )
        // The slot is filled: `not built yet` is what every unfilled section
        // says (`SettingsSectionNotBuilt`), so its absence is the slot edit.
        XCTAssertFalse(
            app.staticTexts[ShellStringsUnderTest.notBuilt].exists,
            "the Chat section still says `not built yet`"
        )
        XCTAssertTrue(
            app.buttons[AiSettingsUnderTest.manualLink].exists,
            "the panel has no link to the manual"
        )
        evidence(app, "aisettings-chat-on")

        // On, nothing silenced: neither conditional block is drawn.
        XCTAssertTrue(
            aiSettingsSwitch(app, reading: AiSettingsUnderTest.on).waitForExistence(timeout: 10),
            "the switch does not read `on` for a phone seeded `on`"
        )
        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.offNote].exists,
            "the off-note is drawn while the heads-up is on"
        )
        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.notWarnedHeading].exists,
            "the silenced-kinds heading is drawn with nothing silenced"
        )

        flipAiSettingsSwitch(app, reading: AiSettingsUnderTest.on)

        XCTAssertTrue(
            aiSettingsSwitch(app, reading: AiSettingsUnderTest.off).waitForExistence(timeout: 10),
            "the switch still does not read `off` after being flipped"
        )
        // The **whole sentence**, not a containment check: a clause dropped
        // off either end of a user-facing string is the 2026-10-29 finding
        // this file's string checks are shaped around.
        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.offNote].waitForExistence(timeout: 10),
            "turning the heads-up off drew no off-note"
        )
        evidence(app, "aisettings-chat-off")

        // Back, so the case leaves the phone where it found it and the next
        // one's seed is not the only thing standing between them.
        flipAiSettingsSwitch(app, reading: AiSettingsUnderTest.off)
        XCTAssertTrue(
            aiSettingsSwitch(app, reading: AiSettingsUnderTest.on).waitForExistence(timeout: 10),
            "the switch would not go back to `on`"
        )
        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.offNote].exists,
            "the off-note stayed after the heads-up went back on"
        )
    }

    /// "warn again" clears **the row it is on**, and only that row.
    ///
    /// Seeded `calc,scanned`, which discriminates: neither word contains the
    /// other's word or key, so a panel that drew one row twice, or cleared
    /// the whole set, or applied the `KIND_WORDS` map to nothing, all fail
    /// differently from a pass.
    ///
    /// The row is identified **twice over** — by the button's identifier,
    /// which carries the key the button writes, and by geometry, that the
    /// button sits level with the word it claims. Either alone could pass a
    /// panel whose buttons are consistently bound to the wrong thing.
    @MainActor
    func testAiSettings_WarnAgainClearsOneKindAndLeavesTheOther() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the house back to its seed")
        let app = aiSettingsLaunch(seed: "on:calc,scanned")
        XCTAssertTrue(
            reachAiSettingsSection(
                app, group: AiSettingsUnderTest.chatAndAiGroup,
                section: AiSettingsUnderTest.chatSection
            )
        )

        let heading = app.staticTexts[AiSettingsUnderTest.notWarnedHeading]
        XCTAssertTrue(heading.waitForExistence(timeout: 15), "no silenced-kinds heading")
        let calcWord = app.staticTexts[AiSettingsUnderTest.calcWord]
        let scannedWord = app.staticTexts[AiSettingsUnderTest.scannedWord]
        XCTAssertTrue(calcWord.exists, "the `calc` kind is not listed by its word")
        XCTAssertTrue(scannedWord.exists, "the `scanned` kind is not listed by its word")
        // Sorted by **key** (`prefs.skip.sorted()`, `ChatSettingsPanel.kt:67`):
        // `calc` before `scanned`. Asserted because the two rows are told
        // apart below by where they are.
        XCTAssertLessThan(
            calcWord.frame.minY, scannedWord.frame.minY,
            "the silenced kinds are not sorted by key — `calc` should be above `scanned`"
        )
        evidence(app, "aisettings-two-silenced")

        let calcButton = app.buttons[AiSettingsUnderTest.warnAgainButton("calc")]
        XCTAssertTrue(calcButton.exists, "no `warn again` button carrying the `calc` key")
        // The button that says it writes `calc` is on the row that reads
        // `exact sums, averages and dates`, within a row's height of it.
        XCTAssertLessThan(
            abs(calcButton.frame.midY - calcWord.frame.midY), 44,
            "the `calc` button is not on the `calc` row — a button bound to one kind and "
                + "drawn beside another is the lazy-row capture bug wearing a correct identifier"
        )
        calcButton.tap()

        XCTAssertTrue(
            scannedWord.waitForExistence(timeout: 10),
            "clearing `calc` also removed `scanned pages` — `warnAgain` cleared the whole set"
        )
        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.calcWord].exists,
            "`exact sums, averages and dates` is still listed after its `warn again`"
        )
        XCTAssertTrue(
            heading.exists,
            "the heading went away while a kind is still silenced"
        )
        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.offNote].exists,
            "the off-note is drawn while the heads-up is on"
        )
        evidence(app, "aisettings-one-silenced")

        // The second row clears too, and the heading goes with the last kind
        // — the `else if (skip.isNotEmpty())` arm emptying out
        // (`ChatSettingsPanel.kt:63`).
        app.buttons[AiSettingsUnderTest.warnAgainButton("scanned")].tap()
        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.headsUpTitle].waitForExistence(timeout: 10),
            "the panel went away with the last silenced kind"
        )
        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.notWarnedHeading].exists,
            "the heading stayed with nothing left silenced"
        )
        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.scannedWord].exists,
            "`scanned pages` is still listed after its `warn again`"
        )
    }

    /// With the heads-up **off**, the silenced list is not drawn — the
    /// off-note is, instead of it and not as well as it.
    ///
    /// The Kotlin is `if (prefs.off) … else if (prefs.skip.isNotEmpty()) …`
    /// (`ChatSettingsPanel.kt:60-63`). Ported as two independent `if`s — the
    /// natural SwiftUI transcription — a phone in this exact state draws both
    /// the off-note and a list of kinds it is not warning about anyway, which
    /// says two contradictory things at once. This is the case that names
    /// that; ``AiSettingsModel/Note`` is what makes it unrepresentable.
    @MainActor
    func testAiSettings_SilencedKindsAreHiddenWhileTheSwitchIsOff() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the house back to its seed")
        let app = aiSettingsLaunch(seed: "off:calc,scanned")
        XCTAssertTrue(
            reachAiSettingsSection(
                app, group: AiSettingsUnderTest.chatAndAiGroup,
                section: AiSettingsUnderTest.chatSection
            )
        )

        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.offNote].waitForExistence(timeout: 15),
            "a phone seeded `off` drew no off-note"
        )
        XCTAssertTrue(
            aiSettingsSwitch(app, reading: AiSettingsUnderTest.off).exists,
            "a phone seeded `off` has a switch that does not read `off`"
        )
        evidence(app, "aisettings-off-with-silenced")

        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.notWarnedHeading].exists,
            "the silenced-kinds heading is drawn beside the off-note — the `else if` at "
                + "ChatSettingsPanel.kt:63 was ported as a second `if`"
        )
        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.calcWord].exists,
            "`calc` is listed while the heads-up is off"
        )
        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.scannedWord].exists,
            "`scanned` is listed while the heads-up is off"
        )
        XCTAssertFalse(
            app.buttons[AiSettingsUnderTest.warnAgainButton("calc")].exists,
            "a `warn again` button is reachable while the list is supposed to be hidden"
        )

        // And the kinds come back when the switch does: the state was hidden,
        // not cleared. Without this the case above would pass against a panel
        // that silently dropped the set every time the switch went off.
        flipAiSettingsSwitch(app, reading: AiSettingsUnderTest.off)
        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.notWarnedHeading].waitForExistence(timeout: 10),
            "turning the heads-up back on did not bring the silenced list back"
        )
        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.calcWord].exists,
            "`calc` was lost rather than hidden while the switch was off"
        )
        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.scannedWord].exists,
            "`scanned` was lost rather than hidden while the switch was off"
        )
    }

    // MARK: - the dashboard signpost, both sections

    /// Models and Internet both draw the signpost, each naming **its own**
    /// section and its own dashboard address.
    ///
    /// One launch for both, which is what the night's batching asks for and
    /// is also the stronger test: the two cards differ in the interpolated
    /// label and the last path segment only, so a slot filled with a copy of
    /// the other section's panel is invisible until the two are compared in
    /// one run.
    ///
    /// The address asserted is the launch's own `-domovoi-server` value, and
    /// that is **all** it is: `-domovoi-server` installs an
    /// `InMemoryShellStore(serverURL:token:)` (`DomovoiApp.swift:111`), so the
    /// stored copy and the one the app is talking to are the same string for
    /// the whole case. It therefore does **not** catch the stored-vs-displayed
    /// trap, and no claim that it does is made here — swapping the panel to
    /// read the stored copy would not change a character this case asserts.
    /// Catching that would need a second seeded server switched to mid-case
    /// (`ShellStringsUnderTest.pillLabel`), which is one for the soak, not for
    /// tonight. What this case does have is the Models-vs-Internet
    /// discrimination below.
    ///
    /// `internet` is in **Privacy**, not Chat & AI, so the walk crosses two
    /// groups (`SettingsSections.swift`: `.chatAndAi` and `.privacy`).
    @MainActor
    func testAiSettings_ModelsAndInternetSendYouToTheDashboardAddress() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the house back to its seed")
        let app = aiSettingsLaunch(seed: "on")

        XCTAssertTrue(
            reachAiSettingsSection(
                app, group: AiSettingsUnderTest.chatAndAiGroup,
                section: AiSettingsUnderTest.modelsSection
            )
        )
        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.dashboardTitle].waitForExistence(timeout: 15),
            "the Models section drew no dashboard card"
        )
        XCTAssertFalse(
            app.staticTexts[ShellStringsUnderTest.notBuilt].exists,
            "the Models section still says `not built yet`"
        )
        evidence(app, "aisettings-models")
        XCTAssertTrue(
            aiSettingsExactText(app, AiSettingsUnderTest.paragraph("Models")).exists,
            "the Models card's paragraph does not name Models"
        )
        // The address, as text. The exact characters, because this is a thing
        // a person reads and copies — `#` and one slash before it.
        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.address("models")].exists,
            "the Models card does not show \(AiSettingsUnderTest.address("models"))"
        )
        // Drawn, because there **is** a server, so the control can do what it
        // says. Not tapped: the tap leaves the app for Safari, and a case
        // that hands the Simulator to a browser is a case that cannot assert
        // anything afterwards.
        XCTAssertTrue(
            app.buttons[AiSettingsUnderTest.openOnTheDashboard].exists,
            "no `Open on the dashboard` button on the Models card"
        )

        // Models → Chat & AI → index → Privacy → Internet, by the app's own
        // navigation rather than a relaunch, so the two cards are compared in
        // one process with one stored address.
        XCTAssertTrue(
            aiSettingsTapTopmostButton(
                app, "back to \(AiSettingsUnderTest.chatAndAiGroup)",
                orFail: "no back button on the Models section"
            )
        )
        let allSettings = app.buttons[ShellStringsUnderTest.allSettings]
        XCTAssertTrue(allSettings.waitForExistence(timeout: 10), "no all-settings button")
        allSettings.tap()
        XCTAssertTrue(
            reachAiSettingsSection(
                app, group: AiSettingsUnderTest.privacyGroup,
                section: AiSettingsUnderTest.internetSection
            )
        )

        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.dashboardTitle].waitForExistence(timeout: 15),
            "the Internet section drew no dashboard card"
        )
        XCTAssertFalse(
            app.staticTexts[ShellStringsUnderTest.notBuilt].exists,
            "the Internet section still says `not built yet`"
        )
        evidence(app, "aisettings-internet")
        XCTAssertTrue(
            aiSettingsExactText(app, AiSettingsUnderTest.paragraph("Internet")).exists,
            "the Internet card's paragraph does not name Internet"
        )
        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.address("internet")].exists,
            "the Internet card does not show \(AiSettingsUnderTest.address("internet"))"
        )
        // **The discriminating half.** Both cards are the same view; this is
        // the assertion that says it was handed the right section.
        XCTAssertFalse(
            app.staticTexts[AiSettingsUnderTest.address("models")].exists,
            "the Internet card shows the Models address — the slot was filled with the "
                + "wrong section"
        )
        XCTAssertFalse(
            aiSettingsExactText(app, AiSettingsUnderTest.paragraph("Models")).exists,
            "the Internet card's paragraph names Models"
        )
    }

    // MARK: - Dynamic Type, measured

    /// The Chat panel's text **grows** with the type size, measured at both
    /// ends rather than inferred from an audit headline.
    ///
    /// Two launches into the same screen, one at `large` and one at AX5, and
    /// the same three things measured in each: the height of the card's sub,
    /// the height of a silenced kind's word, and whether that kind's row has
    /// become a column. A named Dynamic Type audit finding has twice been a
    /// false positive on this track, so growth is asserted on a
    /// **measurement** and the audit case below only classifies.
    ///
    /// `compare` is seeded because its word is the longest of the eight —
    /// `comparing parts of a long document` — so it is the row that wraps
    /// first and the one whose `fixedSize(horizontal: false, vertical: true)`
    /// is load-bearing. The row also swaps to a column at an accessibility
    /// size, which is asserted here as the button moving **below** the word.
    @MainActor
    func testAiSettings_TheChatPanelsTextGrowsWithTheTypeSize() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the house back to its seed")

        // Reach the Chat section at one type size and measure it. A local
        // function, like ``testWhereTheEightDynamicTypeFindingsLive``'s
        // `step`, and with no attributes of its own: the enclosing case is
        // already `@MainActor`.
        func measure(_ mode: String, _ name: String) -> (sub: CGFloat, word: CGFloat, stacked: Bool) {
            let app = aiSettingsLaunch(seed: "on:compare", mode: mode)
            XCTAssertTrue(
                reachAiSettingsSection(
                    app, group: AiSettingsUnderTest.chatAndAiGroup,
                    section: AiSettingsUnderTest.chatSection
                )
            )
            let word = app.staticTexts[AiSettingsUnderTest.compareWord]
            XCTAssertTrue(
                word.waitForExistence(timeout: 15),
                "\(name): the `compare` kind is not listed"
            )
            // The card's sub is the longest sentence on the screen, so it is
            // the one whose height a missing `fixedSize` would pin flat.
            let sub = startingWith(app, "Domovoi says first when a task")
            XCTAssertTrue(sub.exists, "\(name): the card's sub line is not on screen")
            let button = app.buttons[AiSettingsUnderTest.warnAgainButton("compare")]
            XCTAssertTrue(button.exists, "\(name): no `warn again` button for `compare`")
            evidence(app, "aisettings-type-\(name)")
            // Stacked when the button has moved clear of the word's bottom —
            // the row → column swap on `dynamicTypeSize.isAccessibilitySize`.
            let stacked = button.frame.minY >= word.frame.maxY
            print("AISETTINGS [\(name)] sub=\(sub.frame.height) word=\(word.frame.height) stacked=\(stacked)")
            let heights = (sub: sub.frame.height, word: word.frame.height, stacked: stacked)
            app.terminate()
            return heights
        }

        let ordinary = measure(StubHouseUITests.ordinaryTypeSize, "large")
        let huge = measure("UICTContentSizeCategoryAccessibilityXXXL", "ax5")

        XCTAssertGreaterThan(
            huge.sub, ordinary.sub * 1.5,
            "the card's sub barely grew: \(ordinary.sub) → \(huge.sub). A `Text` with no "
                + "`fixedSize` is squeezed flat by its parent, and `minimumScaleFactor` is "
                + "rejected everywhere in this app"
        )
        XCTAssertGreaterThan(
            huge.word, ordinary.word * 1.5,
            "a silenced kind's word barely grew: \(ordinary.word) → \(huge.word)"
        )
        XCTAssertFalse(
            ordinary.stacked,
            "the silenced row is already a column at `large` — the swap is supposed to be "
                + "conditional on `dynamicTypeSize.isAccessibilitySize`"
        )
        XCTAssertTrue(
            huge.stacked,
            "the silenced row is still a row at AX5: `comparing parts of a long document` and "
                + "`warn again` side by side at that size is the squeeze the swap exists to stop"
        )
    }

    // MARK: - the audit

    /// Apple's accessibility audit on the Chat section, classified by
    /// **signature** with ``floorAndRest(_:)`` — never a pinned total.
    ///
    /// One audit case per screen, and this is the screen with real controls:
    /// the signpost is two `Text`s and a `Button` on a `PanelCard` whose
    /// states this file already audits (`testWhereTheEightDynamicTypeFindingsLive`).
    ///
    /// Seeded with two silenced kinds so the audit sees the conditional
    /// blocks too — an audit only covers the states you launch, and a run
    /// against the empty panel would never have looked at a `warn again` row.
    ///
    /// **If this reports `Contrast failed` against the card's sub, the
    /// off-note or the address**, the cause is almost certainly the pairing
    /// and not this item's wording: `fgMuted` was measured against the
    /// **white** canvas, and ``PanelCard`` draws on `.quaternary.opacity(0.3)`
    /// (`PhonesAndBrowsersView.swift:384`). The fix is then the surface,
    /// which is another item's file — diagnose before changing anything here,
    /// and read the screenshot, not only the tree.
    @MainActor
    func testAiSettings_TheChatSectionAudits() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the house back to its seed")
        let app = aiSettingsLaunch(seed: "on:calc,scanned")
        XCTAssertTrue(
            reachAiSettingsSection(
                app, group: AiSettingsUnderTest.chatAndAiGroup,
                section: AiSettingsUnderTest.chatSection
            )
        )
        XCTAssertTrue(
            app.staticTexts[AiSettingsUnderTest.headsUpTitle].waitForExistence(timeout: 15),
            "the Chat section did not draw before the audit"
        )
        evidence(app, "aisettings-audit")

        let found = try auditIssues(app, "aisettings-chat")
        let (floor, rest) = Self.floorAndRest(found)
        print("AISETTINGS AUDIT floor=\(floor) rest=\(rest.count)")
        // Only `rest` is asserted. The floor is the anonymous Dynamic Type
        // finding every pushed screen in this app reports and that
        // `testTheBarsTextGrowsOnAPushedScreen` measured to be false of every
        // leaf in those bars; a count pinned here would be a count of the
        // shell, not of this panel.
        XCTAssertTrue(
            rest.isEmpty,
            "Apple's audit has \(rest.count) attributed finding(s) against the Chat section: "
                + rest.joined(separator: " | ")
        )
    }
}
