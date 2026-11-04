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
        /// the accessibility sizes and the navigation title above them.
        static let homeCrumb = "domovoi / home"
        static let videosCrumb = "domovoi / videos"

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

        /// What a **typed** glyph must clear, which is a different question
        /// from the hint's floor: the primary ink reads near 20:1 on a white
        /// field and the muted ink about 7, so a reading under this means the
        /// appearance proxy in `DomovoiApp.init` recoloured the field's own
        /// text and not only its placeholder (review 1, S3).
        static let typedInkFloor = 10.0

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
        try assertTheHeaderScrollsAway(app, crumb: StubShell.homeCrumb, named: "shell-home-ax5")
    }

    /// A **pushed** screen at AX5, which is the half the measurement was taken
    /// on: train S001 measured the page at 102 of 852 points on Images, a
    /// pushed screen with a plugin band.
    @MainActor
    func testShell_TheHeaderScrollsAwayOnAPushedScreen() throws {
        let (app, landed) = openVideos(StubShell.ax5)
        XCTAssertTrue(landed, "the videos list never answered — no count line drew")
        try assertTheHeaderScrollsAway(app, crumb: StubShell.videosCrumb, named: "shell-videos-ax5")

        // **The back control is not named after the screen it leaves.**
        // Review 1's S1: one `crumb` was handed to the stack's root *and* to
        // its destinations, and a `UINavigationBar` builds the pushed
        // screen's back button out of the ROOT's navigation item — so with
        // the open route in both places, the way out of Videos was called
        // `domovoi / videos`.
        //
        // The honest limit of this reading: UIKit shortens a back button to
        // `Back` when the previous title does not fit, and at AX5 it usually
        // will, so this asserts the **negative** — never the pushed screen's
        // own crumb — rather than the home crumb it would otherwise be.
        let named = app.navigationBars.buttons.allElementsBoundByIndex
            .filter { $0.exists && $0.label == StubShell.videosCrumb }
        XCTAssertEqual(
            named.count,
            0,
            "a navigation-bar control on the pushed Videos screen is labelled "
                + "`\(StubShell.videosCrumb)` — the stack's root is titled "
                + "after the open route, so the control for leaving Videos is "
                + "named Videos and VoiceOver says `back to domovoi / videos`"
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
        // The local shell's own breadcrumb, trimmed of the two leading spaces
        // ``ShellStrings/localTopbar`` carries for a bar that is not drawn
        // here — ``ShellScrollAwayChrome`` trims them, because a screen reader
        // reads a title and not a layout.
        let crumb = "domovoi / local media"
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
    /// 5. **The page grew, and what it did not get is accounted for.** The
    ///    brief asked for "the header gone and the window grown" and review 2
    ///    was right that the grown half was a `print`. Two assertions now: the
    ///    page does not **shrink** across the swipe, and everything the page
    ///    does *not* hold after it — the window minus the page minus the
    ///    collapsed navigation bar — is under ``unaccountedChromeShare``. The
    ///    second is the discriminating one: chrome that is neither
    ///    `ShellTopBar` nor big enough to break the 45% floor has nowhere to
    ///    hide in it. The phone bar measured 146 of 852 points (17%) at AX5
    ///    and the safe areas take about 6%, so the share is 28%.
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
    ///    unit's work.
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
        // Reading 5. **Grew**, asserted rather than printed (review 2). A
        // point of slack for rounding: the claim is that the page does not
        // lose room when the header folds away, and a page that came back a
        // pixel short of itself is not that defect.
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
        let (floor, rest) = Self.floorAndRest(try auditIssues(app, name))
        print("SHELL [\(name)] audit: floor \(floor), rest \(rest.count) — \(rest)")
        // The chrome's two leaves, by the words only they carry. The pill's
        // label begins with `switch server, ` and the title is the breadcrumb;
        // `connect` is the local shell's pill, which has no status word.
        let chromes = rest.filter { line in
            line.contains(crumb.trimmingCharacters(in: .whitespaces))
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
        XCTAssertFalse(
            app.navigationBars.staticTexts[StubShell.homeCrumb].exists,
            "`\(StubShell.homeCrumb)` is a navigation bar title at the ordinary "
                + "type size — the large title is drawn where Home used to "
                + "have no navigation bar at all"
        )
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
    /// So the three readings are: one `forget` control, nothing of the panel
    /// in the tree, and no audit finding naming a view behind the card.
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

        let found = try auditIssues(app, "shell-forget-card")
        let (floor, rest) = Self.floorAndRest(found)
        print("SHELL [shell-forget-card] audit floor \(floor), rest \(rest.count)")
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
            "Apple's audit still names views behind the dialog: \(behind). That "
                + "is what the `ZStack` did, and it is what a presentation is "
                + "supposed to end."
        )
        // **Everything else the audit attributed, except the card's own.**
        //
        // This was `rest == []`, which is a tripwire for another unit's work:
        // ``ForgetServerCard`` belongs to the player unit, and a finding
        // against the card itself would have failed *this* case, whose subject
        // is what is **behind** a dialog (review 1, N4). So the card's own
        // vocabulary is named and anything outside it is a stranger — which is
        // stricter than the old sweep, not weaker: a view behind the card that
        // the `behind` filter above does not know about still fails here, and
        // it would have been swallowed by a filter written the other way round.
        //
        // The card's own leaves, by the words they carry: `forget this
        // server?`, the address under it (`the server at …`, the label Apple's
        // audit asked for), the paragraph's opening, `cancel` and `forget`.
        // **`the server at ` and not the bare address**: the
        // pill behind the card says `127.0.0.1:6601` as well, which is why the
        // bare address is in the `behind` list above, and a filter that let
        // either one through would stop telling them apart.
        let cardsOwn = [
            ShellStringsUnderTest.forgetServerTitle,
            "the server at ",
            "This phone will forget ",
            ShellStringsUnderTest.forget,
            "cancel",
        ]
        let strangers = rest.filter { line in
            !cardsOwn.contains { line.contains($0) }
        }
        XCTAssertEqual(
            strangers,
            [],
            "the audit attributed findings to views that are neither the forget "
                + "card's nor on the list this case knows to look for: "
                + "\(strangers). The card's own findings, which belong to "
                + "whoever owns `ForgetServerCard`, were \(rest)."
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
        // **The other half of the same proxy**, on the same field: a
        // `UILabel` appearance proxy cannot be narrowed to a placeholder, and
        // whether `UITextField` draws its own text through a `UILabel`
        // subclass is UIKit's business. So the typed text is read too
        // (review 1, S3).
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
    /// **This case is the only answer there is about that leg.** Whether the
    /// `UITextView` appearance proxy in `DomovoiApp.init` reaches a prompt
    /// SwiftUI may be drawing itself is not something either file can assert
    /// (review 2's must finding), so it is read instead: Apple's grey measures
    /// about 1.7:1 in a field and this ink about 6.7:1, far enough apart that
    /// a 4.5 floor cannot confuse them. If this goes red, the fix is **not** a
    /// different class in the proxy — it is
    /// `TextField(…, prompt: Text(hint).foregroundColor(Color.fgMuted))` at
    /// the 24 vertical-axis call sites in 18 files, which is in DECISIONS.md
    /// under "Needs a shared change" because none of those files is this
    /// unit's.
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
            // **Not asserted, because it is not known.** This used to say a
            // `UITextView` has no placeholder of its own, which contradicted
            // `DomovoiApp.swift`'s claim that a vertical-axis field *is* a
            // `UITextView` with a `UILabel` in it — review 2's must finding,
            // and both sentences were about a private view tree neither file
            // had read. What is certain is that `placeholderValue` is a
            // `UITextField` property and this element may be a `textView`, so
            // asking for it here would fail on the class rather than on the
            // colour. The element carries the hint as its **label** — the
            // lookup above asserts that, and an overlay `Text` in place of a
            // prompt would not produce it — and the ink is read off the
            // screen's own pixels either way, which is the reading that
            // answers the question.
            readsPlaceholder: false,
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
        // At AX5 the crumb is the navigation bar's title rather than
        // ``ShellTopBar``'s text, so the landing check asks for it wherever it
        // is drawn — that is answer 29, measured by the cases above.
        let crumb = anything(app, labelled: ShellStringsUnderTest.musicCrumb)
        return (app, crumb.waitForExistence(timeout: 40))
    }

    /// The field with this hint, whichever class it is drawn as: a
    /// single-line `TextField` is a `textField` and `axis: .vertical` is a
    /// `textView`, and which one a size produces is the view's business.
    @MainActor
    private func hintField(_ app: XCUIApplication, _ hint: String) -> XCUIElement {
        let single = app.textFields[hint]
        if single.waitForExistence(timeout: 20) { return single }
        return app.textViews[hint]
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
                + "the system colour and the appearance proxy in "
                + "`DomovoiApp.init` did not take"
        )
        guard audits else { return }
        let (_, rest) = Self.floorAndRest(try auditIssues(app, name))
        XCTAssertEqual(rest, [], "attributed audit findings on \(name): \(rest)")
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
                + "\(StubShell.typedInkFloor):1 — the appearance proxy in "
                + "`DomovoiApp.init` is recolouring the field's own text and "
                + "not only its hint, in all 39 files with a `TextField`. The "
                + "fix is not a narrower class: it is "
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
    @MainActor
    private func inkContrast(
        _ app: XCUIApplication,
        in box: CGRect
    ) -> (ratio: Double, ink: String, paper: String)? {
        guard let whole = app.screenshot().image.cgImage, app.frame.width > 0 else { return nil }
        // The screenshot is in pixels and the frame is in points, and the
        // factor between them is read rather than assumed: a Simulator's scale
        // is whatever device the train booted.
        let scaleX = CGFloat(whole.width) / app.frame.width
        let scaleY = CGFloat(whole.height) / app.frame.height
        let pixels = CGRect(
            x: box.minX * scaleX,
            y: box.minY * scaleY,
            width: box.width * scaleX,
            height: box.height * scaleY
        ).integral
        guard let crop = whole.cropping(to: pixels), crop.width > 2, crop.height > 2 else {
            return nil
        }
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
            paper: say(lightest.rgb)
        )
    }
}
