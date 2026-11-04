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
///  4. One plugin band — which **no case here can reach**, and
///     `design-notes/ios/batch-2026-11-03/shell/REPORT.md` says why in full:
///     both plugin routes are capability-gated and the stub's
///     `/api/capabilities` publishes a flat list iOS does not decode, so
///     Images and Stations are invisible on this house. The fold is a
///     compile-level change until that six-line fixture fix lands.
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
    /// 4. **The page gets the room.** The tallest scroll view on the screen —
    ///    the page, not the phone bar's horizontal one — is measured before
    ///    and after, and reported.
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
    }

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
        XCTAssertEqual(
            rest,
            [],
            "attributed findings with the forget dialog up: \(rest). None of "
                + "them is behind the card (that is asserted above), so each is "
                + "the card's own."
        )
    }

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

        let (videos, videosLanded) = openVideos()
        XCTAssertTrue(videosLanded, "the videos list never answered")
        try assertTheHintIsReadable(videos, StubShell.videosHint, named: "shell-hint-videos")
    }

    /// Music, pushed, waiting on its own crumb — written here rather than
    /// borrowed from another unit's `+Tag` file, so a train that drops that
    /// branch does not drop this case with it.
    @MainActor
    private func openMusicForAHint() -> (app: XCUIApplication, landed: Bool) {
        let app = launch(push: "Music", pushDelay: Self.pushDelay)
        let crumb = app.staticTexts[ShellStringsUnderTest.musicCrumb]
        return (app, crumb.waitForExistence(timeout: 40))
    }

    @MainActor
    @available(iOS 17.0, *)
    private func assertTheHintIsReadable(
        _ app: XCUIApplication,
        _ hint: String,
        named name: String
    ) throws {
        let field = app.textFields[hint]
        XCTAssertTrue(
            field.waitForExistence(timeout: 20),
            "no field labelled `\(hint)` — tree attached"
        )
        evidence(app, name)
        // **The hint is the field's own prompt, and this is what says so.**
        // The alternative fix — an overlay `Text` in place of the placeholder —
        // leaves `placeholderValue` empty, and cases read it.
        XCTAssertEqual(
            field.placeholderValue,
            hint,
            "the field's `placeholderValue` is \(field.placeholderValue ?? "nil") "
                + "and not `\(hint)`: the hint has stopped being the field's "
                + "prompt, which is what an overlay does"
        )
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
        let (_, rest) = Self.floorAndRest(try auditIssues(app, name))
        XCTAssertEqual(rest, [], "attributed audit findings on \(name): \(rest)")
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
