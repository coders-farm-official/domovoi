import XCTest

/// Settings → Health, looked at — the container guard's panel against the stub
/// house.
///
/// ### How a person gets here, and how this file does
///
/// `settingsTabFor(.containers)` is `"health"` (`Route.swift:140`) and
/// `ShellNavigation.navigate(_:)` turns such a route into
/// `openSettingsTab(tab)`. The rig's seeded push calls
/// `model.navigate(route)` (`WorkspaceView.swift:48`), so
/// `-domovoi-audit-push Containers` lands on **Settings → Health** — not on
/// the `containers` route. That is the one path a tap, a link or a `fix`
/// target can take, so it is the path every case here takes.
///
/// `HealthRouteScreen` — the `containers` route's own slot — is therefore
/// **not reachable from this rig at all**: nothing in the app calls
/// `go(.containers)`, and a case that claimed to exercise it would be
/// exercising the Settings section under another name. It is filled so that a
/// future deep link cannot land on `not built yet`, and
/// ``testHealth_TheContainersLinkLandsOnThePanelAndNotAPlaceholder`` asserts
/// the half of that this rig can see: the redirect arrives at the panel, and
/// no placeholder is drawn anywhere on the way.
///
/// ### Two branches of this screen no UI case can reach
///
/// `AttentionView.Summary` and `AttentionView.None` need
/// `home_problems_visibility` to be `summary` or `admins`, and
/// `stubhouse.py:707` hard-codes it to `everyone` in `/api/config` — a route
/// this item's fixture may not claim, because the house already serves it.
/// Both branches are covered by `HealthPanelTests` over the pure function.
/// It is in DECISIONS under "Needs a shared change".
///
/// ### Every assertion names data only one branch can produce
///
/// `domovoi-searxng` is in no other fixture and in no other row; `2 open` is
/// false of a count taken from the four-row list; `cleared` is false of #39
/// and `resolved` is false of #38; `open models` is false of a check that is
/// grey rather than a problem. The seed is written for that in
/// `fixtures/health.py`, which says so row by row.
extension StubHouseUITests {

    // MARK: - getting there

    /// Reset the fixture, set the state this case wants, launch, and wait for
    /// the panel.
    ///
    /// **Reset first, then the switches** — the Chat pass's order, and for its
    /// reason: `reset-fixtures` puts `state` and `tier` back to their seed, so
    /// the other order would undo whatever the case asked for. Nothing is
    /// cleaned up afterwards: this class sets `continueAfterFailure = false`,
    /// so a failed assertion leaves the case by an ObjC exception and a
    /// `defer` never runs, while the stub process outlives every case in the
    /// run.
    ///
    /// What it waits for is the **dashboard foot**, which is drawn in all
    /// three branches of the screen (`ContainersScreen.kt:160`) and in none of
    /// the shell around it. The panel is a plain `VStack` inside Settings'
    /// `ScrollView` and not a lazy list, so everything it draws exists in the
    /// tree whether or not it is on screen — no `scrollUntilBuilt` here, which
    /// is the one place this screen is easier than Chat's.
    @MainActor
    func openHealth(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        state: String? = nil,
        tier: String? = nil,
        paired: Bool = true
    ) -> (app: XCUIApplication, landed: Bool) {
        control("reset-fixtures", "{}", port: 6601, what: "every fixture module")
        if let state {
            control(
                "health-state",
                "{\"state\":\"\(state)\"}",
                port: 6601,
                what: "the health fixture's state"
            )
        }
        if let tier {
            control(
                "health-tier",
                "{\"tier\":\"\(tier)\"}",
                port: 6601,
                what: "the health fixture's tier"
            )
        }
        let app = launch(
            mode: mode,
            paired: paired,
            push: "Containers",
            pushDelay: Self.pushDelay
        )
        let foot = startingWith(app, HealthUnderTest.dashboardFootHead)
        return (app, foot.waitForExistence(timeout: 40))
    }

    // MARK: - the screen, drawn from the fixture's data

    /// The four parts and a check whose words only the stub's rooms read can
    /// produce.
    ///
    /// `study is offline · it can't hear anyone right now` needs the rooms
    /// read to have answered **and** to have decoded: the stub's second room
    /// is `status: "offline"`, and `HealthCheck.text` is
    /// `"\(roomID)\(isOfflineSuffix)"`, so a read that failed would say
    /// `not checked yet` and a read that decoded to defaults would say
    /// ` is offline` with no room in front of it.
    ///
    /// The row is one accessibility element (the label, what was found and the
    /// rooms it names read as one phrase), so these are CONTAINS queries
    /// rather than exact labels.
    @MainActor
    func testHealth_SettingsSectionDrawsTheChecks() throws {
        let (app, landed) = openHealth()
        evidence(app, "health-01-section")
        XCTAssertTrue(landed, "the panel never drew — Settings → Health is empty")
        for part in HealthUnderTest.partLabels {
            XCTAssertTrue(
                app.staticTexts[part].exists,
                "no `\(part)` card — the parts are not drawn in order, or the "
                    + "health slot was not filled"
            )
        }
        XCTAssertTrue(
            containing(app, HealthUnderTest.studyOffline).exists,
            "the rooms check does not name the offline room — `healthChecks` "
                + "was fed the wrong read, or the rooms read did not land"
        )
        XCTAssertFalse(
            containing(app, HealthUnderTest.notBuilt).exists,
            "something on this screen still says `not built yet`"
        )
    }

    /// The link that names `Containers` lands on the panel.
    ///
    /// Every `fix` target, every sidebar-less link and the rig's own seeded
    /// push all go through `navigate(.containers)`, which is
    /// `openSettingsTab("health")`. So this case is about the **redirect plus
    /// the slot**: the Settings section's own header (drawn by
    /// `SettingsPlaceholder`, so it proves where we landed) and the panel's
    /// summary line (drawn by the slot, so it proves what is in it).
    ///
    /// The dead-screen class `ScreenRouter.kt:49` exists to prevent is the
    /// third assertion: nothing says `not built yet`.
    @MainActor
    func testHealth_TheContainersLinkLandsOnThePanelAndNotAPlaceholder() throws {
        let (app, landed) = openHealth()
        evidence(app, "health-02-redirect")
        XCTAssertTrue(landed, "the Containers link did not reach the panel")
        XCTAssertTrue(
            containing(app, HealthUnderTest.sectionSub).exists,
            "`navigate(.containers)` did not land on Settings → Health"
        )
        XCTAssertTrue(
            containing(app, HealthUnderTest.oneProblemLine).exists
                || containing(app, HealthUnderTest.thingsNeedAttention).exists
                || app.staticTexts[HealthUnderTest.allFine].exists,
            "the panel drew no summary line at all"
        )
        XCTAssertFalse(
            containing(app, HealthUnderTest.notBuilt).exists,
            "the Containers destination still draws a placeholder"
        )
    }

    /// The freeze card is **above** the open list, the count is the open ones,
    /// and the tamper's own sentence is on screen.
    ///
    /// Three distinct breakages, and the seed tells them apart:
    ///
    ///  * the freeze card drawn after the list — `frame.minY` says so;
    ///  * `openCount` taken from `incidents.count` — the seed has **four**
    ///    incidents and **two** open, so a wrong count reads `4 open`;
    ///  * `frozen` read off the wrong field — the payload's `frozen` is true
    ///    and only #41 carries `freezes`.
    @MainActor
    func testHealth_OpenIncidentsComeFirstWithTheFreezeCard() throws {
        let (app, landed) = openHealth()
        let freeze = app.staticTexts[HealthUnderTest.pausedTitle]
        let needAttention = app.staticTexts[HealthUnderTest.needAttentionTitle]
        evidence(app, "health-03-open-incidents")
        XCTAssertTrue(landed, "the panel never drew")
        XCTAssertTrue(freeze.exists, "no freeze card for a frozen house")
        XCTAssertTrue(needAttention.exists, "no `containers that need attention` section")
        XCTAssertLessThan(
            freeze.frame.minY,
            needAttention.frame.minY,
            "the freeze card is drawn at y=\(Int(freeze.frame.minY)) and the "
                + "open list at y=\(Int(needAttention.frame.minY)) — the card "
                + "belongs above the list it explains"
        )
        XCTAssertTrue(
            app.staticTexts[HealthUnderTest.twoOpen].exists,
            "the count beside the section is not `\(HealthUnderTest.twoOpen)` — "
                + "it was taken from the whole list, which has four rows"
        )
        XCTAssertTrue(
            containing(app, HealthUnderTest.searxngSummary).exists,
            "the tamper incident's own sentence is not on the screen"
        )
    }

    /// The meta line, end to end, and the freeze line on the one row that
    /// holds it.
    ///
    /// `#41 · tampering · different image · domovoi-searxng · opened …` is
    /// `incidentMeta`'s order, its separators and `kindWords`' mapping in one
    /// string: `image` must read `different image`, the class must read
    /// `tampering` and not `failure`, and the container must be in it.
    ///
    /// `pauses risky changes` is then asserted **on #41's row and against
    /// #40's**, which is the assertion that catches `freezes` derived from
    /// `class` alone: both rows are open, one is a tamper, and only one
    /// carries the flag.
    @MainActor
    func testHealth_AnIncidentsMetaLineNamesItsContainer() throws {
        let (app, landed) = openHealth()
        let tamper = containing(app, HealthUnderTest.metaOf41)
        let failure = containing(app, HealthUnderTest.metaOf40)
        evidence(app, "health-04-meta")
        XCTAssertTrue(landed, "the panel never drew")
        XCTAssertTrue(
            tamper.exists,
            "no row whose meta line reads `\(HealthUnderTest.metaOf41)` — the "
                + "order, the separators or `kindWords` are wrong"
        )
        XCTAssertTrue(tamper.label.contains(HealthUnderTest.openedPrefix),
                      "the meta line has no `opened …` at all: \(tamper.label)")
        XCTAssertTrue(
            tamper.label.contains(HealthUnderTest.pausesRiskyChanges),
            "#41 holds the freeze and does not say so: \(tamper.label)"
        )
        XCTAssertTrue(failure.exists, "no row for the restarting music player")
        XCTAssertTrue(
            failure.label.contains(HealthUnderTest.acknowledged),
            "#40 is acknowledged and the meta line does not say so: \(failure.label)"
        )
        XCTAssertFalse(
            failure.label.contains(HealthUnderTest.pausesRiskyChanges),
            "#40 does not hold the freeze and says it does — `freezes` is "
                + "being read off the class: \(failure.label)"
        )
    }

    /// `resolved` and `cleared` are not the same word, and the seed has one of
    /// each.
    ///
    /// #39's `resolution` is `recovered`, so its line reads `resolved …`;
    /// #38's is `cleared`, so its line reads `cleared …`. A port that ignored
    /// `resolution` would put the same word on both, and a case that only
    /// asserted one would pass.
    @MainActor
    func testHealth_TheResolvedAndClearedWordsTellTheTwoApart() throws {
        let (app, landed) = openHealth()
        let recovered = containing(app, HealthUnderTest.metaOf39)
        let cleared = containing(app, HealthUnderTest.metaOf38)
        evidence(app, "health-05-resolved-vs-cleared")
        XCTAssertTrue(landed, "the panel never drew")
        XCTAssertTrue(recovered.exists, "no row for the resolved migration failure")
        XCTAssertTrue(
            recovered.label.contains(HealthUnderTest.resolvedWord),
            "#39 recovered by itself and its line does not say `resolved`: "
                + recovered.label
        )
        XCTAssertFalse(
            recovered.label.contains(HealthUnderTest.clearedWord),
            "#39 was not cleared by anyone and its line says it was: "
                + recovered.label
        )
        XCTAssertTrue(cleared.exists, "no row for the cleared unknown container")
        XCTAssertTrue(
            cleared.label.contains(HealthUnderTest.clearedWord),
            "#38 was cleared and its line does not say so: \(cleared.label)"
        )
    }

    /// A house with nothing open still shows the last day — and shows it under
    /// the Containers part, not as the open section.
    ///
    /// `health-state {"state":"resolved-only"}` leaves exactly one closed
    /// incident. So `last 24 hours · resolved and cleared` must be there,
    /// `containers that need attention` must not, and the sentence saying who
    /// deals with them must still be drawn — `showsAdminNote` is true when
    /// **either** list is non-empty.
    @MainActor
    func testHealth_AResolvedIncidentIsUnderTheContainersPart() throws {
        let (app, landed) = openHealth(state: "resolved-only")
        evidence(app, "health-06-resolved-only")
        XCTAssertTrue(landed, "the panel never drew")
        XCTAssertTrue(
            app.staticTexts[HealthUnderTest.resolvedLabel].exists,
            "no `last 24 hours` block for a house with a resolved incident"
        )
        XCTAssertFalse(
            app.staticTexts[HealthUnderTest.needAttentionTitle].exists,
            "the open-incidents section is drawn for a house with nothing "
                + "open — the resolved list is being used as the open one"
        )
        XCTAssertFalse(
            app.staticTexts[HealthUnderTest.pausedTitle].exists,
            "the freeze card is drawn for a house that is not frozen"
        )
        XCTAssertTrue(
            containing(app, HealthUnderTest.adminClearsThese).exists,
            "the panel does not say where these are acknowledged or cleared"
        )
    }

    /// A green house: the summary line, the containers check, and no incident
    /// anywhere.
    ///
    /// `health-state {"state":"clean"}` empties the list and hides the banner,
    /// which is what makes `guardCheck` green. The third assertion is the one
    /// that catches a screen still drawing an empty section card: the tamper's
    /// container name appears nowhere.
    @MainActor
    func testHealth_AGreenHouseSaysEverythingLooksFine() throws {
        let (app, landed) = openHealth(state: "clean")
        evidence(app, "health-07-clean")
        XCTAssertTrue(landed, "the panel never drew")
        XCTAssertTrue(
            containing(app, HealthUnderTest.containersFine).exists,
            "the Containers check is not green for a house with no incidents"
        )
        XCTAssertFalse(
            app.staticTexts[HealthUnderTest.needAttentionTitle].exists,
            "a green house still draws the open-incidents section"
        )
        XCTAssertFalse(
            containing(app, HealthUnderTest.searxngContainer).exists,
            "an incident from the seed survived the clean state"
        )
    }

    /// A phone that is not paired is a **guest**: it is told to pair, and it
    /// is told nothing about any container.
    ///
    /// This is the privacy half of `containersView`. The incidents read 401s
    /// (`require_device_read`), the open summary answers, so the tier is
    /// `OPEN` — and `guardCheck` then says
    /// `something needs an admin's attention` rather than which container or
    /// why. The second and third assertions are what fail if the tier rule
    /// collapses: the container's name and the server's own tamper sentence
    /// must be nowhere on the screen.
    @MainActor
    func testHealth_AnUnpairedPhoneGetsTheGuestLine() throws {
        let (app, landed) = openHealth(paired: false)
        evidence(app, "health-08-unpaired")
        XCTAssertTrue(landed, "the panel never drew for an unpaired phone")
        XCTAssertTrue(
            containing(app, HealthUnderTest.pairToSee).exists,
            "an unpaired phone is not told to pair under the Containers part"
        )
        XCTAssertTrue(
            containing(app, HealthUnderTest.adminAttention).exists,
            "the Containers check does not fall back to the admin sentence on "
                + "the open tier"
        )
        XCTAssertFalse(
            containing(app, HealthUnderTest.searxngContainer).exists,
            "an unpaired phone was shown a container's name"
        )
        XCTAssertFalse(
            containing(app, HealthUnderTest.searxngSummary).exists,
            "an unpaired phone was shown the server's tamper sentence"
        )
    }

    /// The dashboard foot, and its link at a size a finger can hit.
    ///
    /// Drawn in every branch (`:160`), so it is checked in two states — a
    /// house with incidents and a green one. The height is the measured
    /// defect `HomeCardView.swift:112` records: Apple's audit found a plain
    /// text button in this app at **15.7 points**, so the assertion is the
    /// 44-point minimum rather than mere existence.
    @MainActor
    func testHealth_TheDashboardFootOffersAFullSizedLink() throws {
        for state in ["seed", "clean"] {
            let (app, landed) = openHealth(state: state)
            let button = app.buttons[HealthUnderTest.openTheDashboard]
            let exists = button.waitForExistence(timeout: 20)
            evidence(app, "health-09-foot-\(state)")
            XCTAssertTrue(landed, "the panel never drew in the \(state) state")
            XCTAssertTrue(
                startingWith(app, HealthUnderTest.dashboardFootHead).exists,
                "the \(state) state has no dashboard paragraph"
            )
            XCTAssertTrue(exists, "the \(state) state has no dashboard button")
            XCTAssertGreaterThanOrEqual(
                button.frame.height,
                44,
                "the dashboard link is \(Int(button.frame.height)) points tall "
                    + "in the \(state) state"
            )
        }
    }

    /// A problem row offers its fix; a row that is not a problem offers
    /// nothing.
    ///
    /// Both halves on one screen, from the stub's own data:
    ///
    ///  * `open rooms` — the offline study is a `warn`, and the room checks'
    ///    `fix` is `Screen(Route.Satellites)` with the label `rooms`;
    ///  * `open music` — a media request is waiting and no plugin can fill
    ///    it, which the fixture's `/api/acquisitions` is written to produce;
    ///  * **no `open models`** — the stub's `/api/health` reports no `stt`
    ///    field, so that check is `idle`, and `idle` is not a problem. The
    ///    check *has* a `fix`, so a port that drew the link whenever one
    ///    existed would draw this one.
    @MainActor
    func testHealth_AProblemRowOffersItsFix() throws {
        let (app, landed) = openHealth()
        evidence(app, "health-10-fixes")
        XCTAssertTrue(landed, "the panel never drew")
        XCTAssertTrue(
            app.buttons[HealthUnderTest.openRooms].exists,
            "the offline-room row offers no way to the rooms"
        )
        XCTAssertTrue(
            app.buttons[HealthUnderTest.openMusic].exists,
            "the stuck media request offers no way to music"
        )
        XCTAssertFalse(
            app.buttons[HealthUnderTest.openModels].exists,
            "a grey check is offering a fix — the link is drawn from `fix` "
                + "alone instead of from `problem && fix`"
        )
    }

    /// Apple's own audit on this screen, classified by signature.
    ///
    /// ``floorAndRest(_:)``: a finding with no element and one of the two
    /// known Dynamic Type headlines is the platform floor every pushed screen
    /// in this app sits on; **everything else is a defect**. Never a pinned
    /// total — a ninth anonymous finding, or one of any other kind, fails
    /// this.
    ///
    /// What it is most likely to catch here: `fgMuted` was measured against
    /// the **white** canvas and these cards are drawn on
    /// `.quaternary.opacity(0.4)`, and this screen has more muted text on a
    /// card fill than any other.
    @MainActor
    @available(iOS 17.0, *)
    func testHealth_HealthPassesApplesAccessibilityAudit() throws {
        let (app, landed) = openHealth()
        let found = try auditIssues(app, "health-audit")
        let (floor, rest) = Self.floorAndRest(found)
        evidence(app, "health-11-audit")
        XCTAssertTrue(landed, "the panel never drew, so the audit saw Settings")
        print("AUDIT [health] floor=\(floor) rest=\(rest.count)")
        XCTAssertTrue(
            rest.isEmpty,
            "Apple's audit has \(rest.count) finding(s) against Health that "
                + "are not the measured floor:\n" + rest.joined(separator: "\n")
        )
    }

    /// The largest accessibility size: everything is still there, and a leaf
    /// **grew**.
    ///
    /// The growth is the half that matters. A named Dynamic Type finding has
    /// twice been a false positive on this track, so the question is never
    /// what the audit says — it is whether the element is bigger at
    /// `UICTContentSizeCategoryAccessibilityXXXL` than at
    /// ``ordinaryTypeSize``. `minimumScaleFactor` creeping in, or a row that
    /// clips instead of becoming a column, both show up as a height that did
    /// not move.
    @MainActor
    func testHealth_HealthAtTheLargestTypeSize() throws {
        // **`staticTexts`, not `.any`** — the Chat pass's lesson: a row's
        // words sit inside an element carrying the same label, and `.any`
        // measures the outer one, which is a 44-point control with padding
        // rather than a line of text. The question here is whether the
        // *text* grew.
        func heights(_ app: XCUIApplication) -> [String: CGFloat] {
            var out: [String: CGFloat] = [:]
            for part in HealthUnderTest.partLabels {
                let element = app.staticTexts[part]
                if element.exists { out[part] = element.frame.height }
            }
            let summary = app.staticTexts
                .matching(
                    NSPredicate(
                        format: "label CONTAINS %@",
                        HealthUnderTest.thingsNeedAttention
                    )
                )
                .firstMatch
            if summary.exists { out["summary"] = summary.frame.height }
            return out
        }

        let (ordinary, ordinaryLanded) = openHealth()
        let before = heights(ordinary)
        evidence(ordinary, "health-12-ordinary")
        XCTAssertTrue(ordinaryLanded, "the panel never drew at the ordinary size")

        let (big, bigLanded) = openHealth("UICTContentSizeCategoryAccessibilityXXXL")
        let after = heights(big)
        evidence(big, "health-13-ax5")
        XCTAssertTrue(bigLanded, "the panel never drew at AX5")

        for part in HealthUnderTest.partLabels {
            XCTAssertTrue(
                big.staticTexts[part].exists,
                "the `\(part)` card's heading is gone at AX5"
            )
        }
        XCTAssertTrue(
            containing(big, HealthUnderTest.thingsNeedAttention).exists,
            "the summary line is gone at AX5"
        )
        var grew: [String] = []
        var stuck: [String] = []
        for (name, small) in before {
            guard let large = after[name] else { continue }
            let line = "\(name) \(Int(small))->\(Int(large))"
            if large > small { grew.append(line) } else { stuck.append(line) }
        }
        print("SCALE GREW \(grew.joined(separator: " | "))")
        print("SCALE STUCK \(stuck.joined(separator: " | "))")
        XCTAssertTrue(
            stuck.isEmpty,
            "these did not grow between the two type sizes: "
                + stuck.joined(separator: " | ")
        )
        XCTAssertFalse(grew.isEmpty, "nothing was measured at either size")
    }
}

/// What the stub house's Health half shows, by the words that appear on
/// screen.
///
/// Spelt out here rather than imported, like every other string this file
/// matches on: a query that silently followed a renamed constant would stop
/// testing what it claims to. The incident data is the fixture's
/// (`design-notes/ios/stubhouse/fixtures/health.py`); the sentences are
/// `HealthPanelStrings`' and `HealthStrings`', which are themselves pinned
/// against the Kotlin by `HealthPanelStringsTests`.
enum HealthUnderTest {

    /// The four `HealthPart` labels, in the order the panel draws them.
    static let partLabels = ["Server", "Rooms", "Plugins", "Containers"]

    /// `SettingsSection`'s own line under the Health heading, drawn by
    /// `SettingsPlaceholder` — so it says where we landed, not what the slot
    /// holds.
    static let sectionSub = "Every check on your Domovoi this phone can see"

    static let notBuilt = "not built yet"

    // MARK: the panel's words

    static let allFine = "everything checked looks fine"
    static let oneProblemLine = "1 thing needs attention"
    static let thingsNeedAttention = "things need attention"
    static let needAttentionTitle = "containers that need attention"
    static let twoOpen = "2 open"
    static let pausedTitle = "paused until an admin reviews"
    static let pausesRiskyChanges = "pauses risky changes"
    static let resolvedLabel = "last 24 hours · resolved and cleared"
    static let adminClearsThese =
        "an admin acknowledges or clears these on the dashboard's Settings → Health"
    static let pairToSee = "pair this phone to see what Domovoi watches"
    static let dashboardFootHead = "Updates, disk space, rooms waiting for approval"
    static let openTheDashboard = "Open the dashboard's Health"
    static let openedPrefix = "opened "
    static let acknowledged = "acknowledged"
    static let clearedWord = "cleared"
    static let resolvedWord = "resolved"

    // MARK: the checks' words

    /// The stub's second room is `study`, `status: "offline"`.
    static let studyOffline = "study is offline · it can't hear anyone right now"

    static let containersFine = "every container Domovoi expects looks right"

    /// `guardCheck`'s words for a phone the server will not show the detail
    /// to — lower case and no full stop, which is what tells it apart from
    /// `ShellStrings.adminAttention`.
    static let adminAttention = "something needs an admin's attention"

    // MARK: the fix links

    static let openRooms = "open rooms"
    static let openMusic = "open music"
    static let openModels = "open models"

    // MARK: the fixture's four incidents

    static let searxngContainer = "domovoi-searxng"
    static let searxngSummary =
        "The web search helper is running a different image than the one Domovoi expects."

    /// The whole head of #41's meta line: the id, the class, `kindWords` of
    /// `image`, and the container.
    static let metaOf41 = "#41 · tampering · different image · domovoi-searxng"
    static let metaOf40 = "#40 · failure · restarting · domovoi-mpd-kitchen"
    static let metaOf39 = "#39 · failure · failed · domovoi-flyway"
    static let metaOf38 = "#38 · tampering · unknown container · domovoi-oops"
}
