import UIKit
import XCTest

/// The shell's four app-wide fixes, against the stub house.
///
/// Every case is `testShell_<What>` and every piece of evidence is `shell-…`,
/// which is how a train hands these results back to the branch that wrote them
/// (NIGHT-RULES §2). The four are owner answers 6, 29, 65 and 58:
///
///  1. **A dialog is a presentation**, so nothing is behind it — in the
///     accessibility tree or in Apple's audit
///     (``testShell_ADialogLeavesNothingBehindIt()``).
///  2. **At the accessibility sizes the shell's header scrolls away**, on
///     three screens (`…TheHeaderScrollsAwayOn…`), and **below them nothing
///     changes** (``testShell_OrdinaryTypeSizesKeepTheBandAboveTheStack()``).
///  3. **Every text-field hint is readable**, measured off the screen's own
///     pixels rather than asserted (``testShell_TheHintInkIsReadable()``).
///  4. **One plugin band**, on both screens it folded together and in both of
///     its shapes (`…ThePluginBandNamesThePlugin…`,
///     `…ThePluginBandIsOneRowAtTheLargestTypeSize`).
///
/// The fourth had no case at all when review 1 read this branch, and that
/// review's one uncaught sabotage was in the band's words. The reason was the
/// house, not the fold: both plugin routes are capability-gated and
/// `/api/capabilities` published a **flat** list iOS does not decode, so
/// Images and Stations were invisible here. `fixtures/stations.py` closed
/// that on 2026-11-03 — it wraps that one route additively and appends a
/// `plugins` array built from every loaded module's `PLUGIN` dict — so the
/// band is reachable now, verified by curl against a local house:
/// `plugins` carries `imagegen`/`Image Generation` and `radio`/`Radio
/// Stations`, both `Coders Farm`, with `features.ai` and the flat list
/// untouched. The kit half of the fold has package tests as well
/// (`ShellFoldTests`), which is the verdict that can be had without a train.
///
/// ### The one thing to know before reading a failure here
///
/// Two of these cases assert what is **not** on the screen — no bar above the
/// stack, nothing behind a dialog — so a failure here is usually a tree
/// question. Every case attaches its tree and its picture before it asserts,
/// for the reason this track has learnt twice: when an element will not
/// attribute, look at the picture.
extension StubHouseUITests {

    // MARK: - the two type sizes, and the words the chrome says

    enum StubShell {

        /// AX5, spelt as the launch argument wants it.
        static let ax5 = "UICTContentSizeCategoryAccessibilityXXXL"

        /// ``ShellStrings/homeCrumb`` plus the route, which is what
        /// ``WorkspaceView/crumb`` composes: the bar's leading string below
        /// the accessibility sizes.
        static let homeCrumb = "domovoi / home"
        static let videosCrumb = "domovoi / videos"

        /// The **navigation title** at the accessibility sizes, which is
        /// `WorkspaceView.screenTitle(for:)` — the route's label and not the
        /// breadcrumb.
        ///
        /// They diverged in train I003. The breadcrumb does not fit a
        /// one-line `UINavigationBar` large title at AX5 and UIKit truncates
        /// it: the pictures of this train read `domovoi / h…` on Home and
        /// `domovoi / vi…` on a pushed Videos, and Apple's audit called both
        /// clipped. The pill still carries the server's address and
        /// ``ShellTopBar`` still draws the whole breadcrumb below the
        /// accessibility sizes, so the house is still named.
        static let homeTitle = "home"
        static let videosTitle = "videos"
        static let settingsTitle = "settings"
        static let localMediaTitle = "local media"

        /// ``ShellPill/accessibilityLabel`` for a saved server **on its own**,
        /// which is the bar's pill (`ShellStringsUnderTest.pillLabel`), and the
        /// prefix of ``ShellPill/toolbarAccessibilityLabel``, which is the
        /// toolbar's. The difference between an exact match and a prefix is
        /// what tells the two pills apart, so it is spelt out here once.
        static let barPill = "switch server"
        static let toolbarPillPrefix = "switch server, "

        /// ``MusicStrings/searchPlaceholder`` and
        /// ``VideoStrings/filterPlaceholder`` — two hints on two reachable
        /// screens, spelt out rather than imported, as every other string in
        /// these files is.
        static let musicHint = "search title, artist, album, path…"
        static let videosHint = "filter…"

        /// The floor Apple's audit asks of body text, and what ``HintInk``
        /// claims against the app's own field background (6.67:1).
        static let contrastFloor = 4.5

        /// How far apart, in WCAG relative luminance, the lightest pixel
        /// inside the Chat composer's greyed-out send and the lightest pixel
        /// of the composer beside it may read before the send counts as
        /// having a fill (unit mx-ink r2). Under ``BrandFillButtonStyle`` both
        /// are the surface, a gap of 0; the old disabled `.bordered` fill
        /// over white is rgb(233,233,235), luminance 0.816 against 1.000, a
        /// gap of 0.18. 0.04 is about rgb(250,250,250) on white: clear of
        /// both, with room for a screenshot's rounding.
        static let sendSurfaceTolerance = 0.04

        /// What a **typed** glyph must clear, which is a different question
        /// from the hint's floor: the primary ink reads near 20:1 on a white
        /// field and the muted ink about 7, so a reading under this means
        /// `ShellHintInkAppearance` recoloured the field's own text and not
        /// only its placeholder (review 1, S3).
        static let typedInkFloor = 10.0

        /// How wide a pushed screen's back control may be at AX5 once it has
        /// stopped drawing the word beside its chevron.
        ///
        /// **86.3 is what it measured with the word** — train I004,
        /// `att/testTheAX5WordingIsTheSameBarFindings/41-ax5-none-tree…`:
        /// `Button, {{0.0, 53.7}, {86.3, 44.0}}, label: 'home'` inside a
        /// 117-point `NavigationBar`. A chevron on its own is the bar's
        /// leading inset plus one glyph, well under half of that. 65 points
        /// is between the two with room on both sides, because the glyph's
        /// own size at AX5 is UIKit's business and not something this file
        /// has measured.
        static let minimalBackControlWidth: CGFloat = 65

        /// The two plugins the stub house's manifest publishes
        /// (`fixtures/images.py`'s and `fixtures/stations.py`'s `PLUGIN`),
        /// spelt out rather than imported like every other string here.
        ///
        /// **Two of them, which is what makes the band's reading
        /// discriminating**: a band that read `plugins.first` instead of the
        /// plugin declaring its own capability would name Image Generation on
        /// Stations.
        static let imagegenName = "Image Generation"
        static let stationsName = "Radio Stations"
        static let publisher = "Coders Farm"

        /// ``DialogPresentationGuard/inlineFallbackIdentifier``: the
        /// identifier `DialogCard` puts on the card when it gives up waiting
        /// for its presentation and draws inline. Spelt out here like every
        /// other string in these files, and asserted **absent** wherever a
        /// case's subject is what a presentation takes out of the window.
        static let inlineFallback = "shell-dialog-inline-fallback"

        /// ``PluginBandStrings/badge``, ``by(_:)``, ``manageLabel(name:)`` and
        /// ``collapsedLabel(name:publisher:)`` — the words one band draws now.
        static let pluginBadge = "plugin"
        static func by(_ who: String) -> String { "by \(who)" }
        static func manageLabel(_ name: String) -> String {
            "manage \(name) on the dashboard's Plugins page"
        }
        static func collapsedBand(_ name: String, _ who: String) -> String {
            "\(name), plugin, by \(who)"
        }
    }

    // MARK: - 29. the header scrolls away at the accessibility sizes

    /// Home, at AX5: the band above the stack is gone, the breadcrumb is the
    /// navigation bar's large title, and one swipe collapses it.
    @MainActor
    func testShell_TheHeaderScrollsAwayOnHome() throws {
        let app = launch(mode: StubShell.ax5)
        XCTAssertTrue(
            houseName(in: app).waitForExistence(timeout: 30),
            "the header never drew the house's name — the config read did not land"
        )
        try assertTheHeaderScrollsAway(app, crumb: StubShell.homeTitle, named: "shell-home-ax5")
    }

    /// A **pushed** screen at AX5, which is the half the measurement was taken
    /// on: train S001 measured the page at 102 of 852 points on Images, a
    /// pushed screen with a plugin band.
    ///
    /// ### It was Videos, and Videos cannot answer this question
    ///
    /// Train I003 read 117 → 117 points here: the large title did not
    /// collapse on a swipe, though the page under it scrolled. The picture
    /// says why (`overnight-trains/I003/ALL/att/`
    /// `testShell_TheHeaderScrollsAwayOnAPushedScreen/`
    /// `shell-videos-ax5-after-a-swipe…png`): the source switch —
    /// `PhoneSourceSwitchView`, which `RouteScreen` wraps **music, videos and
    /// files** in — is pinned above the page's own `ScrollView`, and the
    /// tree agrees (the switch at y=178.7, the scroll view starting at
    /// 245.3). UIKit collapses a large title for a scroll view that reaches
    /// the navigation bar; a scroll view with a static row above it never
    /// moves the bar.
    ///
    /// That was three screens' shape, and `PhoneSourceSwitchView` was not
    /// this unit's file, so the limitation was written up for its owner
    /// (`fx-shell/REPORT.md`, "for others") and this case moved to a pushed
    /// screen whose page **is** the stack's content: Settings, whose own
    /// tree puts its `ScrollView` at y=93.3, the navigation bar's own top.
    ///
    /// The integration pass then fixed it — the switch is a
    /// `.safeAreaInset(edge: .top)` now, so those three pages reach the bar
    /// too — but this case stays on Settings. It is the reading that answers
    /// "does a pushed screen's header fold at all", and a page with no
    /// chrome of its own is the cleanest place to ask it.
    ///
    /// The Videos reading is **not** dropped by that move:
    /// ``testShell_TheSourceSwitchScreensStillGetTheirWindow()`` asserts the
    /// collapse on Videos outright, with the `XCTExpectFailure` that named
    /// the owner deleted now that the pin is gone.
    @MainActor
    func testShell_TheHeaderScrollsAwayOnAPushedScreen() throws {
        let app = launch(mode: StubShell.ax5, push: "Settings", pushDelay: Self.pushDelay)
        XCTAssertTrue(
            app.navigationBars.staticTexts[StubShell.settingsTitle]
                .waitForExistence(timeout: 40),
            "Settings never drew its navigation title at AX5 — the seeded push "
                + "did not land, so there is no pushed screen to measure"
        )
        try assertTheHeaderScrollsAway(
            app,
            crumb: StubShell.settingsTitle,
            named: "shell-settings-ax5"
        )

        // **The back control is not named after the screen it leaves.**
        // Review 1's S1: one `crumb` was handed to the stack's root *and* to
        // its destinations, and a `UINavigationBar` builds the pushed
        // screen's back button out of the ROOT's navigation item — so with
        // the open route in both places, the way out of Settings was called
        // `settings`.
        //
        // The honest limit of this reading: UIKit shortens a back button to
        // `Back` when the previous title does not fit, so this asserts the
        // **negative** — never the pushed screen's own title — rather than
        // the home title it would otherwise be.
        let named = app.navigationBars.buttons.allElementsBoundByIndex
            .filter { $0.exists && $0.label == StubShell.settingsTitle }
        XCTAssertEqual(
            named.count,
            0,
            "a navigation-bar control on the pushed Settings screen is labelled "
                + "`\(StubShell.settingsTitle)` — the stack's root is titled "
                + "after the open route, so the control for leaving Settings is "
                + "named Settings and VoiceOver says `back to settings`"
        )
    }

    /// At AX5 a pushed screen's back control is **a chevron and no word**, and
    /// it still goes back.
    ///
    /// ### The defect, and how it was attributed
    ///
    /// `UINavigationBar` caps the font of its back-button title. At AX5 the
    /// word is drawn at roughly the ordinary 17 points beside a page whose
    /// body text is near 50 — a control labelled in a size the person did not
    /// ask for. Apple's audit reports it as `Text clipped`, and in train I004
    /// it was the **one finding the audit could not attribute**: the label
    /// inside a `UIBarButtonItem` is not an accessibility element of its own,
    /// so `issue.element` would not resolve and the line arrived as
    /// `| element: nil`.
    ///
    /// Five I004 readings place it, and nothing in the app had to change to
    /// take them: pushed at AX5 carries exactly one such finding
    /// (`shell-settings-ax5-audited` floor 3 rest 1, `ax5-none` on Music,
    /// `sizes-AX5` on Phones & browsers), a **root** at AX5 carries none
    /// (`shell-home-ax5-audited`, whose one rest names Home's own card;
    /// `shell-local-ax5-audited`, floor 0 rest 0), and the same pushed screen
    /// at `S` and `large` carries none (`SIZES SUMMARY S=8 large=8 AX5=3`,
    /// where only the AX5 run has a fourth, clipped finding). A root draws the
    /// large title and the toolbar pill too, so both of those are out.
    ///
    /// ### What this asserts, and what it cannot
    ///
    /// The width, because that is where the word is: the same control measured
    /// **86.3 points** with it
    /// (`att/testTheAX5WordingIsTheSameBarFindings/41-ax5-none-tree…`) and a
    /// chevron alone is the bar's leading inset plus one glyph. And that it
    /// still works, because a back control that has lost its word and its job
    /// would pass a width reading.
    ///
    /// It says nothing about the ordinary sizes, where the word is drawn and
    /// should be: that half is `ShellBackButtonWordTests`, which reads the
    /// rule and the one line of `ShellScrollAwayChrome.body` that applies it.
    @MainActor
    func testShell_TheBackButtonDropsItsWordAtTheLargestTypeSize() throws {
        let app = launch(mode: StubShell.ax5, push: "Settings", pushDelay: Self.pushDelay)
        XCTAssertTrue(
            app.navigationBars.staticTexts[StubShell.settingsTitle]
                .waitForExistence(timeout: 40),
            "Settings never drew its navigation title at AX5 — the seeded push "
                + "did not land, so there is no back control to measure"
        )
        evidence(app, "shell-back-ax5")

        // **The leftmost control in the bar, not `firstMatch`.** The bar also
        // carries the toolbar pill, and `firstMatch` is tree order rather
        // than a promise about which end of the bar a control is at.
        let controls = app.navigationBars.buttons.allElementsBoundByIndex
            .filter { $0.exists }
        guard let back = controls.min(by: { $0.frame.minX < $1.frame.minX }) else {
            XCTFail("the pushed Settings screen's navigation bar has no controls at all")
            return
        }
        print(
            "SHELL [shell-back-ax5] back control \(back.frame), label "
                + "`\(back.label)`, of \(controls.count) in the bar"
        )
        XCTAssertLessThan(
            back.frame.width,
            StubShell.minimalBackControlWidth,
            "the back control is \(Int(back.frame.width)) points wide at AX5, over "
                + "the \(Int(StubShell.minimalBackControlWidth))-point allowance. It "
                + "measured 86.3 with its word in train I004, so this is the word "
                + "back: `ShellScrollAwayChrome.body` is not applying "
                + "`toolbarRole(.editor)` at the "
                + "accessibility sizes, and Apple's audit will report it as an "
                + "unattributable `Text clipped` on every pushed screen again"
        )
        XCTAssertTrue(
            back.isHittable,
            "the back control at \(back.frame) is not hittable — a chevron with no "
                + "word is still the only way out of a pushed screen without a "
                + "swipe"
        )
        back.tap()
        XCTAssertTrue(
            app.navigationBars.staticTexts[StubShell.homeTitle]
                .waitForExistence(timeout: 20),
            "tapping the back control at AX5 did not land on the stack's root — "
                + "the word came off the control and its job went with it"
        )
    }

    /// **What answer 29 buys on a source-switch screen** — the reading train
    /// I003 found on Videos, kept rather than dropped, and now asserting the
    /// whole of it.
    ///
    /// `RouteScreen` wraps music, videos and files in
    /// `PhoneSourceSwitchView`. In I003 that view pinned its row as the first
    /// child of a `VStack` above the page's own `ScrollView`, so on those
    /// three screens the large title had no scroll view touching the bar and
    /// **did not collapse**: 117 → 117 points across a swipe that did scroll
    /// the page.
    ///
    /// Two things are asserted here, and only one of them has changed.
    /// Unchanged: what answer 29 buys regardless, the 300 points
    /// ``ShellTopBar`` was taking — the bar is gone, the title and the pill
    /// are in the navigation bar, and the page clears the same 45% floor the
    /// other three screens do. Changed: the collapse itself. The integration
    /// pass made the switch a `.safeAreaInset(edge: .top)` over the two sides
    /// instead of a row above them, which puts the page's `ScrollView` flush
    /// with the bar, so the fold is asserted here directly and the
    /// `XCTExpectFailure` that held this reading while the file belonged to
    /// another unit is deleted.
    @MainActor
    func testShell_TheSourceSwitchScreensStillGetTheirWindow() throws {
        let (app, landed) = openVideos(StubShell.ax5)
        XCTAssertTrue(landed, "the videos list never answered — no count line drew")
        XCTAssertTrue(
            app.navigationBars.staticTexts[StubShell.videosTitle].waitForExistence(timeout: 20),
            "no `\(StubShell.videosTitle)` in a navigation bar on a pushed Videos "
                + "at AX5 — the breadcrumb is not the stack's title"
        )
        XCTAssertEqual(
            app.buttons.matching(NSPredicate(format: "label == %@", StubShell.barPill)).count,
            0,
            "`ShellTopBar`'s pill is still on the screen at AX5 — the band above "
                + "the stack is still drawn, and it cannot scroll away from up "
                + "there (300 of 852 points, train S001)"
        )
        guard let page = tallestScrollView(app) else {
            return XCTFail("no scroll view on a pushed Videos at AX5")
        }
        let barBefore = app.navigationBars.firstMatch.frame.height
        page.swipeUp(velocity: .slow)
        let barAfter = app.navigationBars.firstMatch.frame.height
        evidence(app, "shell-videos-ax5-source-switch")
        print(
            "SHELL [shell-videos-ax5-source-switch] navigation bar "
                + "\(Int(barBefore)) → \(Int(barAfter)) points, page "
                + "\(Int(page.frame.height)) of \(Int(app.frame.height)) — the "
                + "source switch is pinned above this page's scroll view, so "
                + "UIKit has nothing to collapse the large title against"
        )
        let floor = app.frame.height * Self.pageFloorShare
        XCTAssertGreaterThan(
            page.frame.height,
            floor,
            "the page is \(Int(page.frame.height)) of \(Int(app.frame.height)) "
                + "points on a pushed Videos at AX5, under the \(Int(floor))-point "
                + "floor. The large title does not collapse here (the pinned "
                + "source switch), so this is the whole of what answer 29 buys on "
                + "this screen — and it was 102 of 852 before the fold."
        )

        // **The collapse is asserted outright now, and the wrapper is gone.**
        // This assertion spent train I003 inside an `XCTExpectFailure` naming
        // the owner of `PhoneSourceSwitchView`: green while the pin was
        // there, red the day it was un-pinned, which is what a defect in
        // another unit's file gets instead of either a bare failure (review
        // 1's N4) or a `print` nobody reds on.
        //
        // The integration pass un-pinned it — the switch is a
        // `.safeAreaInset(edge: .top)` over the sides rather than the first
        // row of a `VStack` above them, so the page's `ScrollView` reaches
        // the navigation bar again. So the expectation would now go unmet,
        // which is itself a failure, and the wrapper comes off exactly as its
        // own note said it should.
        XCTAssertLessThan(
            barAfter,
            barBefore,
            "the large title did not collapse on a pushed Videos: "
                + "\(Int(barBefore)) → \(Int(barAfter)) points across a swipe "
                + "that did scroll the page. I003 measured 117 → 117 here with "
                + "`PhoneSourceSwitchView` pinning its row above this page's "
                + "`ScrollView` (the switch at y=178.7, the scroll view at "
                + "245.3); that row is a top safe-area inset now, so the scroll "
                + "view reaches the bar and UIKit should fold the title. If this "
                + "is still 117 → 117, the inset did not put the scroll view "
                + "flush with the bar and the three source-switch screens still "
                + "get only the 300 points `ShellTopBar` was taking."
        )
    }

    /// **The folded title comes back off.** The one audit finding that failed
    /// about twenty cases across the app in train I003, as a case of its own.
    ///
    /// ### What it is
    ///
    /// `performAccessibilityAudit` overrides the content-size trait while it
    /// looks for clipping. ``ShellScrollAwayChrome`` reads that trait, so the
    /// shell folds its header into the navigation bar **inside the audit** —
    /// correct, and not the bug. The bug is the other end: the audit finishes,
    /// the trait reverts, the title branch goes away, and the bar stays
    /// folded, because `.navigationTitle` and `.navigationBarTitleDisplayMode`
    /// are preferences that UIKit keeps when SwiftUI stops writing them.
    ///
    /// I003 left a 96-point navigation bar with a `7.33 x 40.67` title in it
    /// at y=141, every page 52 points down, and an empty band on the screen
    /// between `Back` and the page
    /// (`att/testCalendar_AuditHasNothingNew/calendar-17-audit-agenda….png`;
    /// the trees taken *before* an audit, `testRooms_Audit` and
    /// `testAiSettings_TheChatSectionAudits`, have the bar at 44 and no such
    /// element). Apple's audit called it `Text clipped`, which is the audit
    /// being right.
    ///
    /// ### Why it is worth a case and not just a fix
    ///
    /// It arrives at every other owner's door as "my frames are 52 points out
    /// and there is a finding I did not cause", which is how it cost a whole
    /// train. And it is not only the audit's: a person who changes the text
    /// size in Settings and comes back drives the same trait change.
    ///
    /// Videos rather than Settings because `openVideos` has a landing check
    /// that cannot pass early (``Pager/countText(shown:total:noun:)``); the
    /// pinned source switch below the bar makes no difference to the bar.
    @MainActor
    func testShell_TheFoldedTitleComesBackOffAfterAnAudit() throws {
        let (app, landed) = openVideos()
        XCTAssertTrue(landed, "the videos list never answered — no count line drew")
        let bar = app.navigationBars.firstMatch
        XCTAssertTrue(
            bar.waitForExistence(timeout: 20),
            "no navigation bar on a pushed Videos — there is no bar to fold"
        )
        let before = bar.frame.height
        evidence(app, "shell-bar-before-audit")
        guard #available(iOS 17.0, *) else {
            throw XCTSkip("performAccessibilityAudit needs iOS 17")
        }
        _ = try auditIssues(app, "shell-bar-after-audit")
        let after = app.navigationBars.firstMatch.frame.height
        evidence(app, "shell-bar-after-audit")
        print(
            "SHELL [shell-bar-after-audit] navigation bar \(Int(before)) → "
                + "\(Int(after)) points across Apple's audit"
        )
        XCTAssertEqual(
            after,
            before,
            accuracy: 1,
            "the navigation bar is \(Int(after)) points after Apple's audit and "
                + "was \(Int(before)) before it. The audit's type-size override "
                + "folded the shell's header into the bar and the fold did not "
                + "come back off, so every screen audited in this run is left "
                + "with a blank large-title row and its page ~52 points down "
                + "(train I003, about twenty cases)."
        )
        // The other half of the same defect, and the half Apple reported: the
        // stale title is still an element, with the crumb as its label and a
        // width no glyph fits in.
        let squeezed = app.navigationBars.staticTexts.allElementsBoundByIndex
            .filter { $0.exists && !$0.label.isEmpty && $0.frame.width < 16 }
            .map { "`\($0.label)` \($0.frame)" }
        XCTAssertTrue(
            squeezed.isEmpty,
            "a navigation-bar title is still in the tree with no width after "
                + "the audit: \(squeezed.joined(separator: ", ")). That is the "
                + "`Text clipped` finding about twenty audit cases reported in "
                + "I003 — the title SwiftUI stopped drawing, in a bar UIKit was "
                + "never told to put back."
        )
    }

    /// The **local** shell at AX5 — the third screen, and a different
    /// mechanism: it is a `TabView`, so the chrome is a `NavigationStack` per
    /// tab (``LocalShellView``).
    ///
    /// `server: false` saves nothing, which is the shell a fresh install lands
    /// on: the phone's own media with a `connect` pill. Its pill has no status
    /// word and is not `unverified`, so the toolbar label is the two-part one.
    @MainActor
    func testShell_TheHeaderScrollsAwayOnTheLocalShell() throws {
        let app = launch(mode: StubShell.ax5, server: false)
        // ``ShellStrings/localMediaTitle``: the local shell's navigation
        // title, which is its breadcrumb without the house crumb in front of
        // it. It was `domovoi / local media` until train I003 measured that
        // label at the full 360 points with Apple's audit calling it clipped
        // — a one-line large title at AX5 does not hold it.
        let crumb = StubShell.localMediaTitle
        let title = app.navigationBars.staticTexts[crumb]
        XCTAssertTrue(
            title.waitForExistence(timeout: 30),
            "no `\(crumb)` in a navigation bar on the local shell at AX5 — the "
                + "header is not the stack's title, so it cannot scroll away. "
                + "Tree attached."
        )
        evidence(app, "shell-local-ax5")
        // ``ShellStrings/connectPill`` is both the pill's words and its
        // accessibility label with no server saved, so the toolbar's composed
        // label is the same one word — and **one** button carries it. Two
        // would be the bar still drawn above the tabs with the toolbar's pill
        // under it.
        let connect = NSPredicate(format: "label == %@", "connect")
        XCTAssertEqual(
            app.buttons.matching(connect).count,
            1,
            "\(app.buttons.matching(connect).count) `connect` pills on the local "
                + "shell at AX5 — one is the navigation bar's, and any other is "
                + "`ShellTopBar` still being drawn above the tabs"
        )
        XCTAssertEqual(
            app.navigationBars.buttons.matching(connect).count,
            1,
            "the `connect` pill is not in a navigation bar on the local shell at "
                + "AX5, so it is not the thing that scrolls away with the page"
        )
        // **Reading 6 on this screen too.** This case counts pills directly
        // rather than going through ``assertTheHeaderScrollsAway`` — the local
        // shell's pill is the one word `connect` and not the composed prefix
        // that helper matches — and until review 3's N1 that meant the AX5
        // audit never ran here, while the `label='connect'` clause in
        // ``assertTheChromeAuditsAtAX5`` matched nothing on either screen it
        // *was* called from. S1's account said three screens. It does now:
        // one swipe to collapse the large title into the translucent inline
        // bar, which is the surface the reading is about, then the audit.
        if let page = tallestScrollView(app) {
            page.swipeUp(velocity: .slow)
        } else {
            XCTFail(
                "no scroll view on the local shell at AX5 — the tab's page is not "
                    + "the stack's content, so the large title cannot collapse and "
                    + "the audit below would measure the expanded bar"
            )
        }
        if #available(iOS 17.0, *) {
            try assertTheChromeAuditsAtAX5(app, crumb: crumb, named: "shell-local-ax5-audited")
        }
    }

    /// **The local shell's header is the stack's title on every tab**, which
    /// is the reading review 2's uncaught sabotage left open.
    ///
    /// That sabotage was one argument — `drawn: false` on
    /// ``LocalShellView``'s `.shellScrollAwayChrome` — with the rule, both
    /// gates and all three of `WorkspaceView`'s `forHeader` calls untouched.
    /// It makes ``ShellScrollAwayChrome``'s fold answer `noHeaderAtAll`, so
    /// `navigationTitle(crumb:)` returns nil and the body takes `else {
    /// content }`: at AX5 the shell a fresh install lands on has no large
    /// title and no toolbar pill, and `drawsBarAboveTheStack` is false there
    /// too, so there is no `ShellTopBar` either. **ShellFold ran 29 of 29
    /// green against it.** `ShellFoldViewBodyTests`' one reading of this file
    /// (`localShell.contains("drawn: true")`) is satisfied by the
    /// `forHeader(drawn: true, …)` call that computes the gate, not by the
    /// modifier, so the package half cannot close it.
    ///
    /// What this adds over
    /// ``testShell_TheHeaderScrollsAwayOnTheLocalShell()``, which would also
    /// go red under it: that case reads the tab the shell opens on. The
    /// modifier is applied **per tab** (``LocalShellView``'s `tab(_:)` —
    /// there is no one stack in a `TabView`), so a header that is only wired
    /// on the first tab is a shape neither case saw. This walks the tabs and
    /// asserts the title and the pill on each, which is also the cheapest
    /// place to catch a later unit wrapping one tab's screen in a stack of
    /// its own.
    ///
    /// **It never opens the connection sheet** (NIGHT-RULES §1: that starts a
    /// LAN sweep of Kamron's real network). The pill is asserted to exist and
    /// is not tapped.
    @MainActor
    func testShell_TheLocalShellsHeaderIsOnEveryTab() throws {
        let app = launch(mode: StubShell.ax5, server: false)
        let crumb = StubShell.localMediaTitle
        XCTAssertTrue(
            app.navigationBars.staticTexts[crumb].waitForExistence(timeout: 30),
            "no `\(crumb)` in a navigation bar on the local shell's first tab at "
                + "AX5 — `ShellScrollAwayChrome` drew no large title, which is "
                + "what `drawn: false` on `LocalShellView`'s "
                + "`.shellScrollAwayChrome` does. Tree attached."
        )
        evidence(app, "shell-local-ax5-tabs")

        // `music`, `videos`, `files`: ``localTabs(phoneCanChat:sharedScreen:savedBooks:)``
        // always offers these three, and with no server there is no chat and
        // no saved book to add a fourth.
        let connect = NSPredicate(format: "label == %@", "connect")
        var read = 0
        for name in ["music", "videos", "files"] {
            let item = app.tabBars.buttons[name].exists
                ? app.tabBars.buttons[name]
                : app.buttons[name]
            guard item.waitForExistence(timeout: 10) else {
                print("SHELL [local-tabs] no `\(name)` tab item — tree follows")
                print(app.debugDescription)
                continue
            }
            item.tap()
            let title = app.navigationBars.staticTexts[crumb]
            XCTAssertTrue(
                title.waitForExistence(timeout: 15),
                "the `\(name)` tab of the local shell at AX5 has no `\(crumb)` in "
                    + "a navigation bar. The chrome is applied per tab, so a tab "
                    + "whose `.shellScrollAwayChrome` is not drawn has no header "
                    + "and no way back to the connection sheet at all"
            )
            XCTAssertEqual(
                app.navigationBars.buttons.matching(connect).count,
                1,
                "\(app.navigationBars.buttons.matching(connect).count) `connect` "
                    + "pills in the navigation bar of the `\(name)` tab at AX5, "
                    + "not one — the toolbar pill is the only way back to the "
                    + "connection sheet once the bar above the tabs is gone"
            )
            print("SHELL [local-tabs] \(name): title and pill in the navigation bar")
            read += 1
        }
        evidence(app, "shell-local-ax5-tabs-walked")
        XCTAssertGreaterThanOrEqual(
            read,
            2,
            "only \(read) of the local shell's three tabs could be reached at "
                + "AX5, so this case measured almost nothing. The trees above "
                + "say what the tab bar looked like"
        )
    }

    /// What "the header scrolls away" means, as four readings and no opinions.
    ///
    /// 1. **The band is gone.** No button whose label is exactly
    ///    ``StubShell/barPill`` — that is ``ShellTopBar``'s pill, and at these
    ///    sizes the bar is not drawn.
    /// 2. **The chrome is in the navigation bar instead.** The breadcrumb is a
    ///    `staticText` *inside* a navigation bar, and the pill is a button
    ///    whose label begins with ``StubShell/toolbarPillPrefix`` — the
    ///    composed one, which still says the address, `unverified` and the
    ///    status word that the bar's three leaves used to.
    /// 3. **It collapses.** The navigation bar is shorter after one swipe than
    ///    before it. Both numbers go in the message either way, because the
    ///    number is the finding.
    /// 4. **The page gets the room**, which is the headline and is therefore
    ///    asserted and not only printed (review 1, S2). The tallest scroll
    ///    view on the screen — the page, not the phone bar's horizontal one —
    ///    is measured before and after, and after the swipe it must hold more
    ///    than ``pageFloorShare`` of the window. The measurement answer 29
    ///    exists for is **102 of 852 points**, 12% of the window; the floor is
    ///    45%, so a page left at the old window fails whatever caused it — a
    ///    `safeAreaInset` some later unit adds, the plugin band growing — and
    ///    not only `ShellTopBar` being drawn again, which reading 1 catches.
    /// 5. **What the page did not get is accounted for** — and, cheaply, that
    ///    it did not shrink. The brief asked for "the header gone and the
    ///    window grown"; review 2 was right that the grown half was a `print`,
    ///    and **review 3 was right that the assertion which replaced it is not
    ///    a growth assertion.** `pageAfter > pageBefore - 1` is satisfied by
    ///    `pageAfter == pageBefore`, which is what it will usually be: a
    ///    collapsing large title moves the scroll view's *content offset*, not
    ///    its frame. So the honest account is one assertion that matters and
    ///    one that guards a corner. The one that matters is the **ceiling**:
    ///    everything the page does not hold after the swipe — the window minus
    ///    the page minus the collapsed navigation bar — is under
    ///    ``unaccountedChromeShare``, which chrome that is neither
    ///    `ShellTopBar` nor big enough to break the 45% floor has nowhere to
    ///    hide in. The phone bar measured 146 of 852 points (17%) at AX5 and
    ///    the safe areas take about 6%, so the share is 28%. The one that
    ///    guards a corner is the non-shrink: a page that *loses* room as the
    ///    header folds away has a real cause and nothing else here sees it.
    ///    A reading that the window **grew** would have to compare two
    ///    launches, which is
    ///    ``testShell_OrdinaryTypeSizesKeepTheBandAboveTheStack()``'s shape
    ///    and not this helper's; REPORT's limits say so.
    /// 6. **Apple's audit, at AX5, on the collapsed bar** — and only the
    ///    chrome's own leaves are asserted on. ``ShellPillToolbarButton``'s
    ///    doc comment claimed the large-title bar's background is the system
    ///    background, which is the surface `Color.warnInk` was measured
    ///    against; review 2 pointed out that once the large title collapses —
    ///    which this helper makes it do — UIKit draws the inline bar as a
    ///    translucent material over the scrolling page, the same class of
    ///    surface that measured `warnInk` at 4.21:1 on the `.quaternary`
    ///    capsule. So it is measured. A finding naming the breadcrumb or the
    ///    toolbar pill fails; everything else is printed and left to the unit
    ///    that owns the screen, because Home, Videos and the local shell are
    ///    not this unit's and N4's mistake was failing a case for another
    ///    unit's work. A finding Apple returns with **no element** is asserted
    ///    on as well, separately: that is how a contrast finding against the
    ///    collapsed bar arrives (I002, R002 and R003), and a filter over
    ///    labels cannot see it (review 3, S4). Since the I006 fix an
    ///    anonymous *contrast* finding is allowed up to the text leaves the
    ///    page's band cuts in half (``measureViewportBand(_:)``, measured
    ///    before the audit), and whenever one is let through the title's and
    ///    the pill's own pixels must clear ``StubShell/contrastFloor``.
    ///
    /// The swipe is `.slow` on the page's own scroll view and not on the app:
    /// a plain `swipeUp()` steps over rows, and a swipe on the app can land on
    /// the phone bar's scroll view, which is the mistake train S010 made.
    @MainActor
    private func assertTheHeaderScrollsAway(
        _ app: XCUIApplication,
        crumb: String,
        named name: String
    ) throws {
        let title = app.navigationBars.staticTexts[crumb]
        XCTAssertTrue(
            title.waitForExistence(timeout: 20),
            "no `\(crumb)` in a navigation bar — the breadcrumb is not the "
                + "stack's title, so there is nothing for UIKit to scroll away. "
                + "Tree attached."
        )
        let bandPill = app.buttons.matching(
            NSPredicate(format: "label == %@", StubShell.barPill)
        )
        let toolbarPill = app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH %@", StubShell.toolbarPillPrefix)
        )
        let page = tallestScrollView(app)
        let barBefore = app.navigationBars.firstMatch.frame
        let pageBefore = page?.frame ?? .zero
        evidence(app, name)

        XCTAssertEqual(
            bandPill.count,
            0,
            "`ShellTopBar`'s pill is still on the screen at AX5 — the band "
                + "above the stack is still drawn, and it cannot scroll away "
                + "from up there (300 of 852 points, train S001)"
        )
        XCTAssertGreaterThan(
            toolbarPill.count,
            0,
            "no toolbar pill: the bar is gone and nothing replaced it, so the "
                + "server's address, `unverified` and the status word are off "
                + "the screen with no way back to the connection sheet"
        )

        guard let page else {
            XCTFail("no scroll view on \(name) — nothing here can scroll away")
            return
        }
        // One slow swipe, which is what a person does to read the second line
        // of the page.
        page.swipeUp(velocity: .slow)
        let barAfter = app.navigationBars.firstMatch.frame
        let pageAfter = page.frame
        evidence(app, "\(name)-after-a-swipe")
        print(
            "SHELL [\(name)] navigation bar \(Int(barBefore.height)) → "
                + "\(Int(barAfter.height)) points, page "
                + "\(Int(pageBefore.height)) → \(Int(pageAfter.height)) of "
                + "\(Int(app.frame.height))"
        )
        XCTAssertLessThan(
            barAfter.height,
            barBefore.height,
            "the navigation bar is \(Int(barAfter.height)) points after a swipe "
                + "and was \(Int(barBefore.height)) before it — the large title "
                + "did not collapse, so the header does not scroll away on "
                + "\(name). Either the page's scroll view is not the stack's "
                + "content, or the title is drawing inline already."
        )
        XCTAssertLessThan(
            barAfter.height,
            100,
            "the navigation bar is still \(Int(barAfter.height)) points after a "
                + "swipe: it collapsed but not to the inline bar, so the page's "
                + "window is \(Int(pageAfter.height)) of \(Int(app.frame.height))"
        )
        // The headline, pinned. A share of the window and not a number of
        // points, because the train boots whatever device it boots and 852 is
        // one phone's height.
        let floor = app.frame.height * Self.pageFloorShare
        XCTAssertGreaterThan(
            pageAfter.height,
            floor,
            "the page is \(Int(pageAfter.height)) of \(Int(app.frame.height)) "
                + "points on \(name) after a swipe, under the \(Int(floor))-point "
                + "floor — answer 29 is about this number: it was 102 of 852 "
                + "with the header above the stack (train S001). The navigation "
                + "bar collapsed (\(Int(barBefore.height)) → "
                + "\(Int(barAfter.height))), so something else is taking the "
                + "room: a `safeAreaInset` added since, or chrome that is not "
                + "inside the page's scroll view."
        )
        // Reading 5, first half: the page **does not shrink** across the
        // swipe. Not "grew", and the account of this case said "grew" until
        // review 3's S3 — `pageAfter > pageBefore - 1` is satisfied by
        // `pageAfter == pageBefore`, and that is what it will usually be: a
        // large title collapsing moves a scroll view's **content offset**, not
        // its frame, so the frame the swipe returns is the frame it started
        // with. This is therefore a cheap guard, not the headline — the
        // headline is reading 4's floor, and the discriminating new reading is
        // the unaccounted-chrome ceiling below. It is kept because a page that
        // *loses* room when the header folds away is a real defect with a real
        // cause (a `safeAreaInset` reacting to the collapse), and nothing else
        // here would see it.
        XCTAssertGreaterThan(
            pageAfter.height,
            pageBefore.height - 1,
            "the page SHRANK across the swipe, \(Int(pageBefore.height)) → "
                + "\(Int(pageAfter.height)) of \(Int(app.frame.height)) on "
                + "\(name): the navigation bar collapsed and the room it gave "
                + "up went to something other than the page"
        )
        // …and what the page did not get is accounted for. This is the
        // reading that catches chrome which is neither `ShellTopBar` nor large
        // enough to break the 45% floor.
        let unaccounted = app.frame.height - pageAfter.height - barAfter.height
        let allowed = app.frame.height * Self.unaccountedChromeShare
        print(
            "SHELL [\(name)] off the page: \(Int(unaccounted)) points beside "
                + "the \(Int(barAfter.height))-point bar, of "
                + "\(Int(app.frame.height))"
        )
        XCTAssertLessThan(
            unaccounted,
            allowed,
            "\(Int(unaccounted)) points of the \(Int(app.frame.height)) are "
                + "neither the page nor the collapsed navigation bar on "
                + "\(name), over the \(Int(allowed))-point allowance. The phone "
                + "bar is 146 of 852 at AX5 and the two safe areas about 93 "
                + "between them; anything past that is chrome outside the "
                + "page's scroll view, which is what answer 29 is about — it "
                + "was 706 points off the page before the fold."
        )
        // Reading 6. The audit at AX5, with the large title collapsed into the
        // inline bar — the surface `ShellPillToolbarButton`'s comment used to
        // assert about. Only the chrome's own leaves are asserted on.
        if #available(iOS 17.0, *) {
            try assertTheChromeAuditsAtAX5(app, crumb: crumb, named: "\(name)-audited")
        }
    }

    /// How much of the window may be neither the page nor the collapsed
    /// navigation bar: the phone bar (146 of 852 at AX5, train S001) plus the
    /// two safe areas (59 and 34 on that phone) is 239 points, 28% — so the
    /// allowance is **34%**, 50 points of slack, because whether a SwiftUI
    /// scroll view's frame is inset by a safe area or spans behind it is not
    /// something this file can assert and a reading that false-reds on a
    /// rounding is worth nothing.
    ///
    /// What each reading catches, since neither catches everything: the shape
    /// answer 29 fixes left **706** of 852 points off the page (83%) and
    /// `ShellTopBar` alone is 300 (35%), so this allowance catches both. A
    /// 256-point plugin band on its own would clear 34% if the page's scroll
    /// view spans behind the safe areas — reading 4's 45% floor is what
    /// catches that one (852 − 239 − 256 − 44 = 313, 37%), and the band has a
    /// 200-point ceiling of its own in
    /// ``testShell_ThePluginBandIsOneRowAtTheLargestTypeSize()``. Three
    /// readings, each with a shape it is the one to catch.
    private static let unaccountedChromeShare: CGFloat = 0.34

    /// Apple's audit on a folded shell, classified so that it reads **this
    /// unit's** chrome and nothing else.
    ///
    /// The screens this runs on (Home and the pushed Videos list) belong to
    /// other units, and a finding against one of their rows must not fail a
    /// shell case — that is the
    /// mistake N4 found in the dialog case. So: every finding is printed, the
    /// measured floor is split off as everywhere else, and the assertion is
    /// over the findings that **name the chrome** — the breadcrumb that is now
    /// a large title, or the toolbar pill that carries the address, the
    /// warning and the status word.
    ///
    /// What it is for: `warnInk` is 5.73:1 on white and 4.21:1 on the
    /// `.quaternary` capsule it had to be moved off, and the collapsed inline
    /// navigation bar is a translucent material over whatever the page has
    /// scrolled under it. Whether the warning glyph survives that was an
    /// argument in a comment until this reading existed.
    @MainActor
    @available(iOS 17.0, *)
    private func assertTheChromeAuditsAtAX5(
        _ app: XCUIApplication,
        crumb: String,
        named name: String
    ) throws {
        // **The band and its straddlers, before the audit** — the rule
        // ``testTheStatesTheAuditHadNeverSeen()`` reads, through the same
        // helper. Measured first because a tree read after the audit is a read
        // of wherever the audit left the page. See the anonymous allowance
        // below for what it is for.
        let band = measureViewportBand(app)
        if let band {
            print(
                "SHELL [\(name)] band top=\(band.top) bottom=\(band.bottom) "
                    + "straddling text leaves=\(band.straddlers.count) \(band.straddlers)"
            )
        } else {
            print("SHELL [\(name)] no scroll view — no viewport band, so no straddlers")
        }
        let (floor, rest) = Self.floorAndRest(try auditIssues(app, name))
        print("SHELL [\(name)] audit: floor \(floor), rest \(rest.count) — \(rest)")
        // The chrome's two leaves, by the words only they carry. The pill's
        // label begins with `switch server, `; `connect` is the local
        // shell's pill, which has no status word.
        //
        // **The title is matched as a whole label and not as a substring**,
        // which it was while the title was the breadcrumb. It is the route's
        // own word now (`home`, `videos`), and `line.contains("home")` also
        // matches the phone bar's `home` tab — another unit's leaf, and
        // failing this case for it is review 1's N4 mistake with the owners
        // the other way round.
        //
        // And it is matched **in the top half of the window**, because
        // `home` is also the phone bar's tab caption, at the foot of the
        // screen. A navigation title cannot be down there.
        let titled = "label='\(crumb.trimmingCharacters(in: .whitespaces))'"
        let waist = app.frame.height / 2
        let chromes = rest.filter { line in
            let high = (Self.elementFrame(in: line)?.minY ?? 0) < waist
            return (line.contains(titled) && high)
                || line.contains(StubShell.toolbarPillPrefix)
                || line.contains("label='connect'")
        }
        XCTAssertEqual(
            chromes,
            [],
            "Apple's audit names the shell's own folded chrome on \(name): "
                + "\(chromes). The large title and the toolbar pill are drawn "
                + "on the navigation bar's surface, and at AX5 the collapsed "
                + "bar is a translucent material over the page — which is the "
                + "same class of surface that measured `Color.warnInk` at "
                + "4.21:1 on the `.quaternary` capsule the word had to be "
                + "moved off (`ShellChromeViews.swift`). Picture attached."
        )
        // **The shape that filter cannot see.** Apple returns contrast
        // findings with `element: nil` routinely — I002's own diagnosis says
        // exactly that of R002 and R003, "the allowance swallowed it" — and an
        // anonymous finding carries no crumb, no pill prefix and no
        // `label='connect'`, so the filter above would print it and pass. The
        // leaf most likely to arrive that way here is the one this reading
        // exists for: a warning glyph on a translucent collapsed bar.
        //
        // ### Anonymous contrast is allowed by geometry, and only that far
        //
        // This used to fail on **every** anonymous finding, and its comment
        // called the cost — "an anonymous finding can be theirs and this can
        // red for their work" — a trade taken on purpose. Train I006 paid it:
        // `shell-home-ax5-audited` came back `floor 6, rest 1`, the one an
        // anonymous `Contrast failed`, where I005 had returned the same
        // finding **named** — Home's media-request row,
        // `frame=(48.0, 577.3, 313.0, 290.7)`, straddling the phone bar's top
        // (its own text 18.82:1, `i006-diag/shell.md`). Same picture, same
        // row, attributed once and not the next time. The states case had
        // already solved that shape by geometry, so this borrows its rule
        // through ``measureViewportBand(_:)``:
        //
        //  * an anonymous `Contrast failed` is allowed up to the number of
        //    text leaves the band's edges cut in half, less the attributed
        //    contrast findings already on such a leaf;
        //  * one past that count fails, and so does an anonymous finding of
        //    any **other** kind, as before;
        //  * and the loophole that opens — an anonymous finding against the
        //    chrome absorbed because some row happens to straddle — is closed
        //    by reading the chrome's own pixels whenever one is let through
        //    (``assertTheChromeInkClearsTheFloor``): if the title or the pill
        //    measures under ``StubShell/contrastFloor``, this fails anyway.
        let anonymous = rest.filter { $0.hasSuffix("| element: nil") }
        let anonymousContrast = anonymous.filter { $0.hasPrefix("Contrast failed") }
        let anonymousOther = anonymous.filter { !$0.hasPrefix("Contrast failed") }
        let namedOnAnEdge = rest.filter { line in
            guard line.hasPrefix("Contrast failed"), let band,
                  let box = Self.elementFrame(in: line) else { return false }
            return Self.crosses(band.top, box) || Self.crosses(band.bottom, box)
        }.count
        let straddling = band?.straddlers.count ?? 0
        let allowance = max(0, straddling - namedOnAnEdge)
        let letThrough = Array(anonymousContrast.prefix(allowance))
        let pastTheCount = Array(anonymousContrast.dropFirst(allowance))
        for line in letThrough {
            print(
                "SHELL [\(name)] unattributed contrast, one of \(straddling) "
                    + "straddling leaves (\(namedOnAnEdge) already named on an edge) :: \(line)"
            )
        }
        XCTAssertEqual(
            anonymousOther + pastTheCount,
            [],
            "Apple returned finding(s) it could not attribute on \(name), past "
                + "what the page's geometry accounts for: \(anonymousOther.count) "
                + "of a kind other than contrast, and \(pastTheCount.count) "
                + "contrast finding(s) beyond the \(straddling) text leaves the "
                + "band's edges cut in half (\(namedOnAnEdge) of those already "
                + "named): \(anonymousOther + pastTheCount). `floorAndRest` has "
                + "already taken out the two known anonymous headlines. **Read "
                + "the attached picture to attribute it.** A row of Home's or "
                + "Videos' belongs to that unit; the large title or the toolbar "
                + "pill is this unit's and is the defect reading 6 is for. "
                + "ONE of these has already been attributed and fixed: a "
                + "`Text clipped` on a pushed screen at AX5 is the back "
                + "button's word, which `UINavigationBar` draws at about 17 "
                + "points whatever size is asked for and which is not an "
                + "accessibility element of its own — so the audit names it "
                + "and XCUITest cannot. It came off in `ShellScrollAwayChrome` "
                + "(`toolbarRole(.editor)`) and "
                + "`testShell_TheBackButtonDropsItsWordAtTheLargestTypeSize` "
                + "holds it; if that case is red too, this line is the same "
                + "defect and not a second one."
        )
        if !letThrough.isEmpty {
            assertTheChromeInkClearsTheFloor(app, crumb: crumb, named: name)
        }
    }

    /// The large title's and the toolbar pill's **glyphs'** own pixels, read
    /// whenever ``assertTheChromeAuditsAtAX5`` lets an anonymous contrast
    /// finding through on the page's geometry — so that an absorbed finding
    /// cannot be one against the chrome.
    ///
    /// Each reading is ``inkContrast(_:in:)``: the darkest pixel in a box
    /// against the lightest, which is the *best* contrast in that box. So the
    /// boxes are kept to **one thing each**:
    ///
    /// * the title's frame, inset by a point;
    /// * the pill's `server.rack` image, on its own;
    /// * the pill's `exclamationmark.triangle.fill` warning glyph, on its own,
    ///   whenever the pill says `unverified` — the glyph this reading exists
    ///   for (`Color.warnInk` on the collapsed bar's translucent material,
    ///   the surface class that measured it at 4.21:1 on the `.quaternary`
    ///   capsule). It is required, not optional: an unverified pill with no
    ///   warning image to find fails here and prints the tree.
    ///
    /// The first version read the whole pill as one box (revision 1's M3):
    /// the pill is one button holding both images, so a faded warning glyph
    /// passed on the server glyph's ink — on every Home AX5 run, because a
    /// row always straddles there and so this always runs. Read separately,
    /// each glyph has to carry its own contrast. Train I006's whole-pill
    /// reading was `rgb(157, 83, 0)` on `rgb(249, 249, 248)`, **5.43:1**; the
    /// per-glyph values are the next train's to report.
    ///
    /// What it still cannot see: darkest-against-lightest inside one glyph's
    /// box is that glyph's best pixel against its paper, so a glyph drawn with
    /// a readable core and faded edges passes. And the floor is the body-text
    /// ``StubShell/contrastFloor`` (4.5), stricter than the 3:1 WCAG asks of
    /// a non-text glyph, on purpose: `warnInk` is the token already measured
    /// failing a surface like this one.
    @MainActor
    private func assertTheChromeInkClearsTheFloor(
        _ app: XCUIApplication,
        crumb: String,
        named name: String
    ) {
        let trimmed = crumb.trimmingCharacters(in: .whitespaces)
        let title = app.navigationBars.staticTexts.matching(
            NSPredicate(format: "label == %@ OR label == %@", crumb, trimmed)
        ).firstMatch
        let pill = app.navigationBars.buttons.matching(
            NSPredicate(
                format: "label BEGINSWITH %@ OR label == %@",
                StubShell.toolbarPillPrefix,
                "connect"
            )
        ).firstMatch
        guard pill.exists else {
            evidence(app, "\(name)-chrome-not-found")
            XCTFail(
                "an anonymous contrast finding was let through on \(name) on the page's "
                    + "geometry, and there is no toolbar pill in a navigation bar to read — "
                    + "so the finding cannot be ruled off the chrome. Tree printed above."
            )
            return
        }
        var boxes: [(what: String, element: XCUIElement, inset: CGFloat)] = [
            ("the large title", title, 1),
            ("the toolbar pill's server glyph",
             pill.images.matching(identifier: "server.rack").firstMatch, 0),
        ]
        if pill.label.contains("unverified") {
            boxes.append((
                "the toolbar pill's warning glyph",
                pill.images.matching(identifier: "exclamationmark.triangle.fill").firstMatch,
                0
            ))
        }
        for (what, element, inset) in boxes {
            let box = element.exists ? element.frame : .zero
            guard box.width > 2, box.height > 2 else {
                evidence(app, "\(name)-chrome-not-found")
                XCTFail(
                    "an anonymous contrast finding was let through on \(name) on "
                        + "the page's geometry, and \(what) is not there to read "
                        + "(frame \(box), pill '\(pill.label)') — so the finding "
                        + "cannot be ruled off the chrome. Tree printed above."
                )
                continue
            }
            guard let reading = inkContrast(app, in: box.insetBy(dx: inset, dy: inset)) else {
                evidence(app, "\(name)-chrome-unread")
                XCTFail(
                    "an anonymous contrast finding was let through on \(name), "
                        + "and \(what)'s pixels at \(box) could not be read"
                )
                continue
            }
            print(
                "SHELL [\(name)] \(what) ink \(reading.ink) on \(reading.paper) in \(box) — "
                    + String(format: "%.2f", reading.ratio) + ":1"
            )
            XCTAssertGreaterThanOrEqual(
                reading.ratio,
                StubShell.contrastFloor,
                "\(what) measures " + String(format: "%.2f", reading.ratio)
                    + ":1 (ink \(reading.ink) on \(reading.paper)) on \(name), "
                    + "while Apple returned an anonymous contrast finding this "
                    + "case let through on the page's geometry — the finding may "
                    + "be the chrome's and not the straddling row's. Picture attached."
            )
        }
    }

    /// How much of the window the page must hold once the header has scrolled
    /// away. 45% of 852 is 383 points; the shell at AX5 left it 102.
    private static let pageFloorShare: CGFloat = 0.45

    /// The **page's** scroll view: the tallest one on screen.
    ///
    /// At AX5 the phone bar is a horizontally scrolling row of tabs
    /// (``PhoneBarView``), so `scrollViews.firstMatch` is a coin toss between
    /// the page and the bar — and a swipe on the wrong one scrolls nothing,
    /// which is how train S010 lost a case.
    @MainActor
    private func tallestScrollView(_ app: XCUIApplication) -> XCUIElement? {
        app.scrollViews.allElementsBoundByIndex
            .filter { $0.exists }
            .max { $0.frame.height < $1.frame.height }
    }

    /// Below the accessibility sizes, **nothing changes**: the band is above
    /// the stack, with its pill, and Home's navigation bar has no title at all.
    ///
    /// This is the half of answer 29 that is a promise rather than a fix, and
    /// it is the case that would catch the fix leaking downwards.
    @MainActor
    func testShell_OrdinaryTypeSizesKeepTheBandAboveTheStack() throws {
        let app = launch()
        XCTAssertTrue(
            houseName(in: app).waitForExistence(timeout: 30),
            "the header never drew the house's name"
        )
        evidence(app, "shell-home-large")
        let bandPill = app.buttons.matching(
            NSPredicate(format: "label == %@", StubShell.barPill)
        )
        XCTAssertEqual(
            bandPill.count,
            1,
            "`ShellTopBar`'s pill is not on Home at the ordinary type size — "
                + "the accessibility-size chrome has leaked down to every size"
        )
        // **Both spellings**, because the title stopped being the breadcrumb
        // in train I003's fix: `domovoi / home` would no longer be drawn even
        // if the fold leaked down to this size, so a reading that asked only
        // for it would have stopped catching the leak it is here for.
        for title in [StubShell.homeCrumb, StubShell.homeTitle] {
            XCTAssertFalse(
                app.navigationBars.staticTexts[title].exists,
                "`\(title)` is a navigation bar title at the ordinary type size "
                    + "— the large title is drawn where Home used to have no "
                    + "navigation bar at all"
            )
        }
        XCTAssertEqual(
            app.buttons.matching(
                NSPredicate(format: "label BEGINSWITH %@", StubShell.toolbarPillPrefix)
            ).count,
            0,
            "the toolbar pill is drawn at the ordinary type size as well as the "
                + "bar's — two pills, one of them saying everything twice"
        )
    }

    // MARK: - 6. a dialog has nothing behind it

    /// The forget confirmation over Settings → Phones & browsers: the dialog
    /// the `forget-card` sweep took its two findings from.
    ///
    /// ### What this case is about
    ///
    /// With the card up as a `ZStack` sibling, Apple's audit reported
    /// `Contrast failed` against the Trusted-servers paragraph and a
    /// `connected` badge — **both behind the card** — and the tree showed the
    /// panel's own `forget` control and the dialog's destructive `forget`
    /// both live, both labelled `forget`, one of which deletes a server's
    /// token without asking again. `.isModal` took the duplicate out of
    /// VoiceOver's reach and left both in the tree; `.accessibilityHidden` on
    /// the screen beside it measured as doing nothing twice (trains S006,
    /// S009). A presentation takes the whole screen out of the window.
    ///
    /// So the readings are: one `forget` control, nothing of the panel in the
    /// tree, no audit finding naming a view behind the card — and none naming
    /// the card itself.
    ///
    /// ### The fourth reading is the one this case let through
    ///
    /// From train I004 to I011 Apple's audit reported `Contrast failed`
    /// against `label='forget'` — the card's own confirm, white on amber at
    /// 2.05:1 — and this case logged `audit floor 0, rest 1` and **passed**.
    /// Its last assertion filtered the card's own vocabulary out of the
    /// findings before asserting, so a finding on the card could only ever be
    /// printed in a failure message that never fired. The reason given was
    /// review 1's N4: the card belonged to another unit, and its defects
    /// should not red a case about what is behind it. That reasoning is the
    /// hole: a dialog is audited **only** here, so excusing its findings here
    /// excused them everywhere (unit ix-fill). The card's own findings now
    /// fail the case, after the `behind` reading, with a message that says
    /// which of the two it is.
    @MainActor
    @available(iOS 17.0, *)
    func testShell_ADialogLeavesNothingBehindIt() throws {
        let app = launch(paired: false)
        XCTAssertTrue(
            reachPhonesAndBrowsers(app),
            "never reached Settings → Phones & browsers, so the forget dialog "
                + "was never opened"
        )
        let forgetControl = app.buttons[ShellStringsUnderTest.forget]
        XCTAssertTrue(
            forgetControl.waitForExistence(timeout: 20),
            "no `forget` control in the panel — the dialog cannot be opened "
                + "from here any more"
        )
        forgetControl.tap()
        let card = anything(app, labelled: ShellStringsUnderTest.forgetServerTitle)
        XCTAssertTrue(
            card.waitForExistence(timeout: 20),
            "the forget dialog never came up. **If the screen is blank**, the "
                + "presentation failed rather than the dialog: `DialogCard` is "
                + "a `fullScreenCover` now, and this card is the one opened "
                + "from inside a `NavigationStack` destination. Picture attached."
        )
        evidence(app, "shell-forget-card")

        // **The presentation is what was measured, not the fallback.**
        // `DialogCard` draws the card inline if the cover has not arrived in
        // 1.5 seconds (`DialogPresentationGuard`), and inline is the
        // pre-answer-6 shape: the page still behind the card. Every assertion
        // below would then be measuring the guard rather than the fix, and the
        // case would go red for the wrong reason. So the fallback's own
        // identifier is asserted absent, which is the one reading a dropped
        // presentation can have from a UI case — it cannot be provoked.
        XCTAssertFalse(
            app.descendants(matching: .any)[StubShell.inlineFallback].exists,
            "the dialog is `DialogCard`'s INLINE FALLBACK, not its "
                + "presentation: the `fullScreenCover` did not arrive within "
                + "1.5s, so the page is still behind the card and the readings "
                + "below are about the guard. If the Alerts card is on screen, "
                + "this is the cover-inside-a-cover DECISIONS §1 calls a merge "
                + "blocker. Picture attached."
        )

        XCTAssertEqual(
            app.buttons.matching(
                NSPredicate(format: "label == %@", ShellStringsUnderTest.forget)
            ).count,
            1,
            "two live controls labelled `forget` with the confirmation up — the "
                + "panel's and the dialog's, as the tree showed at y=658 and "
                + "y=1409. One of them forgets a server without asking again."
        )
        XCTAssertFalse(
            app.staticTexts[ShellStringsUnderTest.householdToken].exists,
            "the panel's `Household token` card is still in the tree behind the "
                + "dialog — the screen was not taken out of the window, so "
                + "everything under the 35% scrim is still being offered as "
                + "content"
        )

        // **The confirm's own pixels, read before the audit.** A band through
        // the middle of `forget`, inset from its ends, holds only the fill and
        // the label's glyphs, so its darkest and lightest pixels are the
        // label-on-fill pair. Train I011's screenshot of this card reads
        // `(255,255,255)` on `(242,166,24)` there, **2.05:1**, which is what
        // the audit's `Contrast failed` was about and what this case passed
        // with. ``DangerFillButtonStyle`` draws `dangerFg` on `danger`,
        // 5.91:1 by the tokens. Read here rather than left to the audit
        // alone, because the audit says only "failed", never by how much.
        let confirm = app.buttons
            .matching(NSPredicate(format: "label == %@", ShellStringsUnderTest.forget))
            .firstMatch
        let confirmFrame = confirm.frame
        XCTAssertTrue(
            confirm.exists && confirmFrame.width > 24 && confirmFrame.height > 14,
            "the card's `forget` has no frame to read (\(confirmFrame)) — tree "
                + "follows\n" + app.debugDescription
        )
        let band = CGRect(
            x: confirmFrame.minX + 10,
            y: confirmFrame.midY - 5,
            width: confirmFrame.width - 20,
            height: 10
        )
        if let reading = inkContrast(app, in: band) {
            print(
                "SHELL [shell-forget-card] confirm label \(reading.ink) / "
                    + "\(reading.paper): \(String(format: "%.2f", reading.ratio)):1 "
                    + "in \(band)"
            )
            XCTAssertGreaterThanOrEqual(
                reading.ratio,
                StubShell.contrastFloor,
                "the forget card's `forget` reads \(reading.ink) against "
                    + "\(reading.paper), \(String(format: "%.2f", reading.ratio)):1, "
                    + "in \(band) — under the body-text floor. I011's was white on "
                    + "amber, 2.05:1: a filled button whose label colour the site "
                    + "did not choose. A ratio near 1 means the band found no "
                    + "glyph at all; picture attached as `shell-forget-card`."
            )
        } else {
            XCTFail(
                "could not read the pixels of `forget` in \(band) — tree follows\n"
                    + app.debugDescription
            )
        }

        let found = try auditIssues(app, "shell-forget-card")
        let (floor, all) = Self.floorAndRest(found)
        print("SHELL [shell-forget-card] audit floor \(floor), rest \(all.count)")
        // **What the tree holds with the card up, and what it does not.**
        //
        // Train I003's audit named four ``ShellTopBar`` leaves and two
        // Settings-index leaves behind this card, at the positions they had
        // before the cover went up — while the picture showed a plain grey
        // scrim with nothing on it but the card, and the tree held none of
        // them. The two assertions above, which are the real claim of answer
        // 6, both passed in that run. `performAccessibilityAudit` samples the
        // window's **views**, and a `fullScreenCover`'s presenting hierarchy
        // is still a set of views: out of the tree, out of VoiceOver's reach
        // and off the screen, which is everything answer 6 promised.
        //
        // ``StubHouseUITests/findingsTheTreeHolds(_:_:)`` carries the whole
        // reading. Nothing is dropped silently — `elsewhere` is printed.
        let (rest, elsewhere) = Self.findingsTheTreeHolds(app, all)
        for line in elsewhere {
            print(
                "SHELL [shell-forget-card] NOT A FINDING — the audit named a view "
                    + "the accessibility tree does not hold there with the card "
                    + "up, so it is a presenting view UIKit has already taken off "
                    + "the screen: \(line)"
            )
        }
        // **Named by what they are about.** A finding against a view behind the
        // card is the defect this change closes; one against the card itself
        // would be a defect of the card. They are told apart by the words in
        // the finding rather than by counting.
        let behind = rest.filter { line in
            ["domovoi /", ShellStringsUnderTest.householdToken, "127.0.0.1:6601", "unverified"]
                .contains { line.contains($0) }
        }
        XCTAssertEqual(
            behind,
            [],
            "Apple's audit names views behind the dialog that the accessibility "
                + "tree still holds: \(behind). That is what the `ZStack` did, "
                + "and it is what a presentation is supposed to end. (A finding "
                + "whose element is in no tree is printed above instead — the "
                + "audit reading a view that is off the screen.)"
        )
        // **The card's own findings fail too.** This used to filter them out
        // as another unit's (review 1, N4) and assert only on what was left,
        // which is how `forget` failing contrast on every train from I004 to
        // I011 passed here — see the doc comment. They are told apart from a
        // stranger by the words they carry, so the message can say which.
        //
        // The card's own leaves: `forget this server?`, the address under it
        // (`the server at …`, the label Apple's audit asked for), the
        // paragraph's opening, `cancel` and `forget`. **`the server at ` and
        // not the bare address**: the pill behind the card says
        // `127.0.0.1:6601` as well, which is why the bare address is in the
        // `behind` list above, and a filter that let either one through would
        // stop telling them apart.
        let cardsWords = [
            ShellStringsUnderTest.forgetServerTitle,
            "the server at ",
            "This phone will forget ",
            ShellStringsUnderTest.forget,
            "cancel",
        ]
        let cardsOwn = rest.filter { line in
            cardsWords.contains { line.contains($0) }
        }
        XCTAssertEqual(
            cardsOwn,
            [],
            "Apple's audit names the forget card's OWN leaves: \(cardsOwn). "
                + "These are defects of `ForgetServerCard` "
                + "(`ConnectionDialogView.swift`), not of what is behind it — "
                + "train I011's was `forget` drawn white on amber, 2.05:1, by a "
                + "`.borderedProminent` that named no label colour. Picture "
                + "attached as `shell-forget-card`."
        )
        // **And everything else the audit attributed.** `rest` is already the
        // findings the tree holds where the audit put them (the split above),
        // so an off-the-screen presenting view cannot arrive here; what does is
        // a view behind the card that the `behind` list does not know about, or
        // an anonymous finding of a kind the floor does not count.
        let strangers = rest.filter { line in
            !cardsWords.contains { line.contains($0) }
        }
        XCTAssertEqual(
            strangers,
            [],
            "the audit attributed findings to views that are neither the forget "
                + "card's nor on the list this case knows to look for: "
                + "\(strangers)."
        )
    }

    // MARK: - 58. one plugin band, on both the screens it folded

    /// The band above Images and the band above Stations are now one view, and
    /// this is the first case in the repository to open either of them.
    ///
    /// ### What it discriminates
    ///
    /// The stub's manifest publishes **two** plugins, so every reading here
    /// could be wrong in a way that is visible: `imagegen`/`Image Generation`
    /// and `radio`/`Radio Stations`, both published by `Coders Farm`. A band
    /// that read `plugins.first` rather than the plugin declaring the screen's
    /// own capability would name Image Generation above Stations — so each
    /// screen asserts its own plugin's name **and the other plugin's absence**.
    ///
    /// ### Stations gains `manage` here, and that is the point
    ///
    /// `StationsPluginBand` left the link out while the https-interstitial
    /// question was open; owner answer 28 closes it, and one band cannot
    /// answer it two ways (`design-notes/.../shell/DECISIONS.md` §10). So the
    /// link's full label is asserted on Stations, where it did not exist
    /// before the fold.
    @MainActor
    func testShell_ThePluginBandNamesThePluginAboveBothScreens() throws {
        // The fixture modules share one house for the whole run and Images'
        // own control route can empty its `PLUGIN` dict, which would leave
        // this screen with no band at all.
        control("reset-fixtures", "{}", port: 6601, what: "every fixture back to its seed")

        let images = launch(push: "Images", pushDelay: Self.pushDelay)
        XCTAssertTrue(
            images.staticTexts[StubShell.imagegenName].waitForExistence(timeout: 40),
            "no `\(StubShell.imagegenName)` above Images. Either the push "
                + "bounced back to Home — the route is capability-gated, and it "
                + "is visible only because `fixtures/stations.py` adds the "
                + "`plugins` array to `/api/capabilities` — or the band drew "
                + "nothing. Tree and picture attached."
        )
        evidence(images, "shell-band-images")
        XCTAssertTrue(
            images.staticTexts[StubShell.by(StubShell.publisher)].exists,
            "the band does not draw `\(StubShell.by(StubShell.publisher))` "
                + "(`PluginFrame.kt:144`)"
        )
        XCTAssertTrue(
            images.staticTexts[StubShell.pluginBadge].exists,
            "the bordered `plugin` badge is not drawn (`PluginFrame.kt:152`)"
        )
        XCTAssertTrue(
            anything(images, labelled: StubShell.manageLabel(StubShell.imagegenName)).exists,
            "no `manage` link naming the plugin. It is drawn only when there "
                + "is a server address to open, and this launch has one."
        )
        XCTAssertFalse(
            images.staticTexts[StubShell.stationsName].exists,
            "`\(StubShell.stationsName)` is named above Images — the band took "
                + "the manifest's first plugin instead of the one declaring "
                + "this screen's capability"
        )

        let stations = launch(push: "Stations", pushDelay: Self.pushDelay)
        XCTAssertTrue(
            stations.staticTexts[StubShell.stationsName].waitForExistence(timeout: 40),
            "no `\(StubShell.stationsName)` above Stations — the folded band's "
                + "other call site (`StationsView.swift:86`) drew nothing"
        )
        evidence(stations, "shell-band-stations")
        XCTAssertTrue(
            anything(stations, labelled: StubShell.manageLabel(StubShell.stationsName)).exists,
            "Stations has no `manage` link. The fold gave it one (owner answer "
                + "28); before it, this was the one difference between the two "
                + "copies that was not layout."
        )
        XCTAssertFalse(
            stations.staticTexts[StubShell.imagegenName].exists,
            "`\(StubShell.imagegenName)` is named above Stations"
        )
    }

    /// At the accessibility sizes the band is **one row**, and what it stops
    /// drawing it says instead.
    ///
    /// The measurement this answers: the band took **256 of 852 points** at
    /// AX5 on a 393-point phone, which with the old header's 300 and the phone
    /// bar's 146 left the page a 102-point window (train S001,
    /// `testImages_AtTheLargestTypeSize`). Answer 29 asks chrome to scroll
    /// away; what the band can do in its own file is stop eating a third of
    /// the screen, and answer 62 already approved this shape for the source
    /// switch.
    ///
    /// Three readings:
    ///
    ///  1. the row's own label is the whole band in one sentence — the name,
    ///     the badge and the publisher (``PluginBandStrings/collapsedLabel``,
    ///     the value review 1's sabotage gutted with nothing to catch it);
    ///  2. the badge and the publisher line are **not drawn** as leaves of
    ///     their own, which is what "collapsed" means and what reading 1
    ///     alone would not prove;
    ///  3. the row is under ``bandRowCeiling`` points tall, with the number in
    ///     the message, because 256 points of band is the thing it replaces.
    @MainActor
    func testShell_ThePluginBandIsOneRowAtTheLargestTypeSize() throws {
        control("reset-fixtures", "{}", port: 6601, what: "every fixture back to its seed")
        let app = launch(mode: StubShell.ax5, push: "Images", pushDelay: Self.pushDelay)
        let said = StubShell.collapsedBand(StubShell.imagegenName, StubShell.publisher)
        let row = anything(app, labelled: said)
        XCTAssertTrue(
            row.waitForExistence(timeout: 40),
            "nothing on Images at AX5 is labelled `\(said)`. That sentence is "
                + "the collapsed band: at these sizes the badge and the "
                + "publisher are not drawn, so they are said. Tree attached — "
                + "if the row is there with a shorter label, "
                + "`PluginBandStrings.collapsedLabel` stopped composing it."
        )
        evidence(app, "shell-band-images-ax5")
        XCTAssertFalse(
            app.staticTexts[StubShell.pluginBadge].exists,
            "the `plugin` badge is still drawn at AX5 — the band did not "
                + "collapse, and at 256 points it is a third of the window"
        )
        XCTAssertFalse(
            app.staticTexts[StubShell.by(StubShell.publisher)].exists,
            "`\(StubShell.by(StubShell.publisher))` is still drawn as its own "
                + "line at AX5 — the collapse is only half done"
        )
        print(
            "SHELL [shell-band-images-ax5] collapsed band "
                + "\(Int(row.frame.height)) points of \(Int(app.frame.height))"
        )
        XCTAssertLessThan(
            row.frame.height,
            Self.bandRowCeiling,
            "the collapsed band is \(Int(row.frame.height)) points tall of "
                + "\(Int(app.frame.height)) — over the "
                + "\(Int(Self.bandRowCeiling))-point ceiling. The expanded band "
                + "measured 256 at AX5, which is what one row is here to "
                + "replace; the same collapse on the source switch measured 164 "
                + "down to 59."
        )
    }

    /// What one row of the band may cost at AX5. One line of `.subheadline`
    /// plus 20 points of padding is well under this; the three-leaf band it
    /// replaces measured 256.
    private static let bandRowCeiling: CGFloat = 200

    // MARK: - 65. the hint ink

    /// Two screens with hints, measured: the hint is still the field's own
    /// prompt, and it is readable.
    ///
    /// ### Measured, not asserted
    ///
    /// ``HintInk`` claims 6.67:1 at worst. Apple's audit cannot check it — a
    /// placeholder is not an element it measures, which is exactly why the
    /// 1.72:1 grey shipped — so this case reads the screen's own pixels:
    /// ``inkContrast(_:in:)`` takes the darkest and the lightest pixel inside
    /// the field and reports the WCAG ratio between them. Against Apple's grey
    /// that reading is about 1.5–1.7:1, and against this ink about 6.7:1, so
    /// the floor of 4.5 separates them by a wide margin rather than by a hair.
    ///
    /// The reading is **conservative**: antialiasing means the darkest pixel of
    /// a glyph is lighter than the ink itself, so a pass is a pass with room.
    @MainActor
    @available(iOS 17.0, *)
    func testShell_TheHintInkIsReadable() throws {
        // Music first: its hint is the longest in the app, so there are the
        // most glyph pixels to find the ink in.
        let (music, musicLanded) = openMusicForAHint()
        XCTAssertTrue(musicLanded, "music never drew — no field to read a hint in")
        try assertTheHintIsReadable(music, StubShell.musicHint, named: "shell-hint-music")
        // **The control that says this recoloured the hint and not the
        // text**, on the same field. It was written for the appearance proxy,
        // which could not be narrowed to a placeholder and so might have
        // caught `UITextFieldLabel` with it (review 1, S3). The proxy is gone
        // — I004 measured that it recoloured nothing at all — and
        // `ShellHintInkAppearance` writes the ink into the placeholder's own
        // `NSAttributedString`, which cannot reach typed text by
        // construction. The reading stays: "by construction" is the kind of
        // sentence this track keeps a ledger of.
        try assertTypedTextKeepsThePrimaryInk(music, StubShell.musicHint, named: "shell-typed-music")

        let (videos, videosLanded) = openVideos()
        XCTAssertTrue(videosLanded, "the videos list never answered")
        try assertTheHintIsReadable(videos, StubShell.videosHint, named: "shell-hint-videos")
    }

    /// The **vertical-axis** field: the leg of answer 65 that nothing had
    /// measured, and the size the hint matters most at.
    ///
    /// Music's search field becomes `TextField(…, axis: .vertical)` only at an
    /// accessibility size (`MusicView.swift:686-688`), so the single-line
    /// readings above never reach it — review 1's S3, and it was right: both
    /// fields it measured were `.roundedBorder` single-line ones.
    ///
    /// **This case is the only answer there is about that leg**, and it has
    /// answered twice. Train I004: `1.70:1`, with the appearance proxy in
    /// place. Train I005, with `ShellHintInkAppearance` swapping both of
    /// `UITextField`'s placeholder setters and the single-line field in the
    /// same run reading **7.43:1**: `hint ink rgb(188, 188, 193) on
    /// rgb(242, 242, 247) — 1.70:1`, Apple's grey to the integer again. So
    /// the two legs are **not** one mechanism — I004's reading of the
    /// accessibility tree (`TextField … placeholderValue: …`, therefore a
    /// `UITextField`) was a reading of what SwiftUI *reports*, not of what
    /// draws it. The vertical leg is `ShellHintPrompt` at the call sites
    /// since this fix. Apple's grey measures about 1.7:1 in a field and this
    /// ink about 6.7:1, far enough apart that a 4.5 floor cannot confuse
    /// them.
    ///
    /// No audit here. Music at AX5 is `MusicMore`'s screen to audit, and a
    /// finding against it would fail this case for another unit's work — the
    /// mistake N4 found in the dialog case.
    @MainActor
    @available(iOS 17.0, *)
    func testShell_TheHintInkIsReadableOnAVerticalField() throws {
        let (music, landed) = openMusicForAHint(StubShell.ax5)
        XCTAssertTrue(landed, "music never drew at AX5 — no vertical field to read")
        try assertTheHintIsReadable(
            music,
            StubShell.musicHint,
            named: "shell-hint-music-ax5",
            // **Asserted now, because train I004 settled the class.** This was
            // false with a paragraph explaining that `placeholderValue` is a
            // `UITextField` property, that the element here "may be a
            // `textView`", and that asking for it would fail on the class
            // rather than on the colour — one of two contradicting guesses
            // about a private view tree (review 2's must finding).
            //
            // I004 attached the hierarchy: this element is
            // `TextField, {{24.0, 300.7}, {345.0, 125.3}}, label: 'search
            // title, artist, album, path…', placeholderValue: 'search title,
            // artist, album, path…'` at AX5, where
            // `MusicView.searchField` is `axis: .vertical`. A `UITextField`
            // with a real placeholder, 125 points tall. So the hint is the
            // field's own prompt on this leg too, and that is worth asserting
            // rather than assuming: it is the half of answer 65 an overlay
            // `Text` would break, and this is the only case that reaches the
            // vertical field at all.
            readsPlaceholder: true,
            audits: false
        )
    }

    /// Music, pushed, waiting on its own crumb — written here rather than
    /// borrowed from another unit's `+Tag` file, so a train that drops that
    /// branch does not drop this case with it.
    @MainActor
    private func openMusicForAHint(
        _ mode: String = StubHouseUITests.ordinaryTypeSize
    ) -> (app: XCUIApplication, landed: Bool) {
        let app = launch(mode: mode, push: "Music", pushDelay: Self.pushDelay)
        // At the accessibility sizes the header is the navigation bar's
        // **title** rather than ``ShellTopBar``'s text — answer 29 — and
        // since train I003 the two say different things: the bar still draws
        // the whole breadcrumb, the title is the route's word alone, because
        // a one-line large title truncated the breadcrumb at AX5. So the
        // landing check asks for whichever this size draws.
        let crumb = anything(app, labelled: ShellStringsUnderTest.musicCrumb)
        if crumb.waitForExistence(timeout: 40) { return (app, true) }
        return (app, app.navigationBars.staticTexts["music"].waitForExistence(timeout: 20))
    }

    /// The field with this hint, whichever class it is drawn as: a
    /// single-line `TextField` is a `textField` and `axis: .vertical` is a
    /// `textView`, and which one a size produces is the view's business.
    ///
    /// ### It flicks, because a lazy page's foot is unbuilt
    ///
    /// Train I003 failed ``testShell_TheHintInkIsReadableOnAVerticalField()``
    /// with "no field labelled … as either a text field or a text view", and
    /// the tree it attached says why: at AX5 Music's built content stopped at
    /// y=1781 and the search field is below it — **not drawn at all**, which
    /// is a different thing from off the screen (NIGHT-RULES §5). A wait of
    /// any length cannot make a lazy row exist; only a scroll can.
    @MainActor
    private func hintField(_ app: XCUIApplication, _ hint: String) -> XCUIElement {
        let single = app.textFields[hint]
        let vertical = app.textViews[hint]
        if single.waitForExistence(timeout: 20) { return single }
        if vertical.exists { return vertical }
        for _ in 0..<20 {
            guard let page = tallestScrollView(app) else { break }
            page.swipeUp(velocity: .slow)
            if single.exists { return single }
            if vertical.exists { return vertical }
        }
        return single.exists ? single : vertical
    }

    /// Scroll `element` until its whole frame is inside the window, so that a
    /// screenshot crop of it is a crop of pixels that exist.
    ///
    /// ### Why this is here at all
    ///
    /// Train I003 failed ``testShell_TheHintInkIsReadable()`` with "could not
    /// read shell-hint-music's pixels — no screenshot to measure", and the
    /// tree names the cause exactly: the field was at
    /// `{{16.0, 925.3}, {361.0, 34.0}}` on an 852-point window — built, in
    /// the tree, and **73 points past the bottom of the screen**. `CGImage`'s
    /// `cropping(to:)` answers nil for a rectangle outside the image, so
    /// ``inkContrast(_:in:)`` returned nil and the case blamed the
    /// screenshot. A reading of a field nobody can see would have been worth
    /// nothing either way.
    ///
    /// Bounded at twenty flicks and it returns what it achieved, so a field
    /// that never arrives fails at the caller's own assertion with the
    /// frame printed rather than looping.
    @MainActor
    private func scrollOntoTheScreen(
        _ app: XCUIApplication,
        _ element: XCUIElement
    ) -> Bool {
        guard let page = tallestScrollView(app) else { return false }
        for _ in 0..<20 {
            guard element.exists else { return false }
            let frame = element.frame
            if app.frame.contains(frame) { return true }
            if frame.maxY > app.frame.maxY {
                page.swipeUp(velocity: .slow)
            } else if frame.minY < app.frame.minY {
                page.swipeDown(velocity: .slow)
            } else {
                // Off to one side, which no page in this app scrolls to.
                return false
            }
        }
        return element.exists && app.frame.contains(element.frame)
    }

    @MainActor
    @available(iOS 17.0, *)
    private func assertTheHintIsReadable(
        _ app: XCUIApplication,
        _ hint: String,
        named name: String,
        readsPlaceholder: Bool = true,
        audits: Bool = true
    ) throws {
        let field = hintField(app, hint)
        XCTAssertTrue(
            field.exists,
            "no field labelled `\(hint)` as either a text field or a text view "
                + "— tree attached"
        )
        // **On the screen before its pixels are read.** I003 measured this
        // field at y=925.3 on an 852-point window and the crop came back nil
        // (``scrollOntoTheScreen(_:_:)`` carries the reading).
        XCTAssertTrue(
            scrollOntoTheScreen(app, field),
            "the `\(hint)` field is at \(field.frame) on a "
                + "\(app.frame) window and twenty flicks did not bring it "
                + "wholly onto the screen — there are no pixels of it to "
                + "measure, so a hint ink read here would be a reading of "
                + "whatever is in that rectangle instead"
        )
        evidence(app, name)
        // **The hint is the field's own prompt, and this is what says so.**
        // The alternative fix — an overlay `Text` in place of the placeholder —
        // leaves `placeholderValue` empty, and cases read it.
        if readsPlaceholder {
            XCTAssertEqual(
                field.placeholderValue,
                hint,
                "the field's `placeholderValue` is \(field.placeholderValue ?? "nil") "
                    + "and not `\(hint)`: the hint has stopped being the field's "
                    + "prompt, which is what an overlay does"
            )
        }
        guard let reading = inkContrast(app, in: field.frame.insetBy(dx: 3, dy: 3)) else {
            XCTFail("could not read \(name)'s pixels — no screenshot to measure")
            return
        }
        print(
            "SHELL [\(name)] hint ink \(reading.ink) on \(reading.paper) — "
                + String(format: "%.2f", reading.ratio) + ":1"
        )
        XCTAssertGreaterThan(
            reading.ratio,
            StubShell.contrastFloor,
            "the hint measures " + String(format: "%.2f", reading.ratio)
                + ":1 (ink \(reading.ink) on \(reading.paper)) — Apple's "
                + "placeholder grey reads about 1.7:1 there, so this is still "
                + "the system colour. Two mechanisms ink a hint and each "
                + "covers one class of field: `ShellHintInkAppearance` swaps "
                + "`UITextField`'s two placeholder setters (the single-line "
                + "fields) and `ShellHintPrompt` supplies the `prompt:` of "
                + "every `TextField(axis: .vertical)` (this one). A red here "
                + "says which: a single-line reading of 1.7 is the swap, an "
                + "AX5 one is a vertical field that was left without a "
                + "prompt — `grep -rn \"axis: .vertical\" ios/Domovoi` "
                + "against the list in hx-shell/REPORT.md"
        )
        guard audits else { return }
        let (_, rest) = Self.floorAndRest(try auditIssues(app, name))
        // **Scoped to the field, and to anything the audit cannot attribute.**
        //
        // This asserted `rest == []` over the whole screen and had **never
        // run**: train I003 failed this case at the screenshot crop and I004
        // at the contrast reading, both before this line. The screen it would
        // have graded is ``MusicView``, which is the music unit's, and I004
        // measured three contrast findings on its cards —
        // `testMusicMore_AuditThePlaylistsTab` reds on exactly
        // `label='parlour'`, `label='playing in parlour'` and
        // `label='Veldrassen Overture'` in that train, in that unit's own
        // case. So they are held, attributed, and not this case's: a hint case
        // failing for them is review 1's N4 mistake (a shell case red for
        // another screen's row), and it would also have hidden this reading
        // behind somebody else's fix.
        //
        // What stays strict: a finding that **names the field** — the one
        // thing this case is about, and the shape a hint drawn as an overlay
        // or in a colour the audit dislikes would arrive as — and any finding
        // the audit could not attribute at all, which cannot be given to
        // another screen by inspection.
        let mine = rest.filter { $0.contains("label='\(hint)'") || $0.hasSuffix("| element: nil") }
        for line in rest where !mine.contains(line) {
            print("SHELL [\(name)] the screen's own row, not the field's: \(line)")
        }
        XCTAssertEqual(
            mine,
            [],
            "Apple's audit names the hint field on \(name), or returned a "
                + "finding it could not attribute: \(mine). The lines printed "
                + "beside this one are the host screen's own rows and belong to "
                + "its unit; these do not."
        )
    }

    /// The same field with a word **typed** into it: the darkest pixel is now
    /// a typed glyph, and it must still be the primary ink.
    ///
    /// This is the failure mode the proxy's own comment used to argue away.
    /// `UILabel.appearance(whenContainedInInstancesOf: [UITextField.self])`
    /// recolours every contained `UILabel`, and `UITextField` has drawn its
    /// own text through `UITextFieldLabel` — a `UILabel` subclass — for most
    /// of its life. If it still does, every one of the app's 39 fields types
    /// in `fgMuted`, which is 7:1 on white where the primary ink is near 20:1.
    /// So the floor is ``StubShell/typedInkFloor`` and the two are far apart.
    ///
    /// The reading is conservative in the direction that matters: antialiasing
    /// makes the darkest glyph pixel *lighter* than the ink, so a pass is a
    /// pass with room, and a muted-ink failure cannot read as a pass.
    @MainActor
    @available(iOS 17.0, *)
    private func assertTypedTextKeepsThePrimaryInk(
        _ app: XCUIApplication,
        _ hint: String,
        named name: String
    ) throws {
        let field = hintField(app, hint)
        XCTAssertTrue(field.exists, "no field labelled `\(hint)` to type into")
        XCTAssertTrue(
            scrollOntoTheScreen(app, field),
            "the `\(hint)` field is at \(field.frame) on a \(app.frame) window "
                + "— it cannot be tapped or photographed from there"
        )
        field.tap()
        // A word of upper-case stems, which is the most ink a short string can
        // put in a field: the measurement is of one pixel, and a `.` would
        // give it almost nothing to find.
        field.typeText("HOLLOW")
        evidence(app, name)
        // Read live: the keyboard coming up moves the page under a pushed
        // screen, and a frame captured before the tap is a box somewhere else.
        guard let reading = inkContrast(app, in: field.frame.insetBy(dx: 3, dy: 3)) else {
            XCTFail("could not read \(name)'s pixels — no screenshot to measure")
            return
        }
        print(
            "SHELL [\(name)] typed ink \(reading.ink) on \(reading.paper) — "
                + String(format: "%.2f", reading.ratio) + ":1"
        )
        XCTAssertGreaterThan(
            reading.ratio,
            StubShell.typedInkFloor,
            "a word typed into the field measures "
                + String(format: "%.2f", reading.ratio)
                + ":1 (ink \(reading.ink) on \(reading.paper)), under "
                + "\(StubShell.typedInkFloor):1 — `ShellHintInkAppearance` is "
                + "reaching the field's own text and not only its hint, in all "
                + "39 files with a `TextField`. It writes the ink into the "
                + "placeholder's own `NSAttributedString`, so if this is red "
                + "the two setters are not the separate slots UIKit documents "
                + "them as, and the fix is "
                + "`prompt: Text(hint).foregroundColor(…)` at the call sites."
        )
    }

    /// The darkest and the lightest pixel inside a rectangle of the screen, and
    /// the WCAG contrast between them.
    ///
    /// The darkest pixel in a field with a hint in it and nothing typed is the
    /// hint's ink; the lightest is the field's interior. There is no glyph to
    /// find by name — a placeholder is not an element — so this is the only way
    /// to measure what is drawn rather than to assert what was intended.
    ///
    /// The box is handed in already inset, so the field's own border is not the
    /// reading.
    ///
    /// Internal rather than private since unit mx-ink: the Chat case
    /// `testChat_TheSendIsGreyedOutWhileAReplyIsBeingWritten` measures the
    /// greyed send's label on its own surface with this same reading, so the
    /// two pixel measurements in the suite are one helper, not two. It also
    /// hands back the lightest pixel's relative luminance, so that case can
    /// compare the paper inside the button with the surface beside it: a
    /// translucent disabled fill reads above the floor on white, and only
    /// that comparison sees it.
    @MainActor
    func inkContrast(
        _ app: XCUIApplication,
        in box: CGRect
    ) -> (ratio: Double, ink: String, paper: String, paperLuminance: Double)? {
        guard let whole = app.screenshot().image.cgImage, app.frame.width > 0 else { return nil }
        // The screenshot is in pixels and the frame is in points, and the
        // factor between them is read rather than assumed: a Simulator's scale
        // is whatever device the train booted.
        let scaleX = CGFloat(whole.width) / app.frame.width
        let scaleY = CGFloat(whole.height) / app.frame.height
        // **Clipped to the image, and said out loud when it had to be.**
        // `cropping(to:)` answers nil for a rectangle that leaves the image,
        // and a caller then reports "no screenshot to measure" for a field
        // that was merely off the bottom of the screen — which is exactly
        // what train I003 reported (``scrollOntoTheScreen(_:_:)``). Callers
        // bring the element on screen first; this is the second line of
        // defence, and it names the geometry rather than the screenshot.
        let wanted = CGRect(
            x: box.minX * scaleX,
            y: box.minY * scaleY,
            width: box.width * scaleX,
            height: box.height * scaleY
        ).integral
        let pixels = wanted.intersection(
            CGRect(x: 0, y: 0, width: whole.width, height: whole.height)
        )
        if pixels != wanted {
            print(
                "SHELL ink box \(wanted) is not wholly inside the "
                    + "\(whole.width)x\(whole.height) screenshot — measuring the "
                    + "\(pixels) that is"
            )
        }
        guard !pixels.isNull, let crop = whole.cropping(to: pixels),
              crop.width > 2, crop.height > 2
        else { return nil }
        let width = crop.width
        let height = crop.height
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let drew = bytes.withUnsafeMutableBytes { raw -> Bool in
            guard let context = CGContext(
                data: raw.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else { return false }
            context.draw(crop, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drew else { return nil }

        func channel(_ raw: UInt8) -> Double {
            let value = Double(raw) / 255
            return value <= 0.03928 ? value / 12.92 : pow((value + 0.055) / 1.055, 2.4)
        }
        var darkest = (luminance: 2.0, rgb: (0, 0, 0) as (Int, Int, Int))
        var lightest = (luminance: -1.0, rgb: (0, 0, 0) as (Int, Int, Int))
        for index in stride(from: 0, to: bytes.count, by: 4) {
            let red = bytes[index]
            let green = bytes[index + 1]
            let blue = bytes[index + 2]
            let luminance = 0.2126 * channel(red)
                + 0.7152 * channel(green)
                + 0.0722 * channel(blue)
            let rgb = (Int(red), Int(green), Int(blue))
            if luminance < darkest.luminance { darkest = (luminance, rgb) }
            if luminance > lightest.luminance { lightest = (luminance, rgb) }
        }
        guard darkest.luminance <= 1, lightest.luminance >= 0 else { return nil }
        func say(_ rgb: (Int, Int, Int)) -> String { "rgb(\(rgb.0), \(rgb.1), \(rgb.2))" }
        return (
            ratio: (lightest.luminance + 0.05) / (darkest.luminance + 0.05),
            ink: say(darkest.rgb),
            paper: say(lightest.rgb),
            paperLuminance: lightest.luminance
        )
    }
}
