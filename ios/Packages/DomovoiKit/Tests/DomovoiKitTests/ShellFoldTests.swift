import XCTest
@testable import DomovoiKit

/// The pure `DomovoiKit` value logic this batch's shell unit wrote: the folded
/// plugin band's words, the toolbar pill's composed label, the hint ink's
/// numbers, the destructive pair's, answer 29's header rule and answer 6's
/// inline-fallback guard.
///
/// The three view *bodies* that draw answer 29's answer are read separately,
/// by `ShellFoldViewBodyTests` — this file reads the rule, and a rule with a
/// reading and two call sites without one is what review 3 caught.
///
/// ### Why this file exists at all
///
/// Review 1 sabotaged `PluginBandStrings.collapsedLabel` — gutted it so the
/// collapsed band stopped saying the `plugin` badge and `by <publisher>` — and
/// **nothing in the repository caught it**: not `pkgtest.sh`, not
/// `ios/DomovoiTests`, and no UI case, because when that review ran the stub
/// house published a flat capability list that iOS does not decode and both
/// plugin screens were invisible on it. The unit had run `pkgtest` three times
/// against code none of its own changes reached.
///
/// So these are the readings a Windows agent can actually get a verdict on
/// before a train (NIGHT-RULES §5), and `StubHouseUITests+Shell.swift`'s two
/// band cases are the behavioural half now that `fixtures/stations.py` has
/// made the routes reachable.
///
/// **The expectations are written out, never re-derived.** Calling the
/// function under test a second time and comparing it with itself is the
/// tautology `ShellChromeTests`' own header warns about, and it is exactly
/// what would have passed against the sabotage.
final class ShellFoldTests: XCTestCase {

    // MARK: - the folded band's words (owner answer 58)

    /// The collapsed row at the accessibility sizes says all three things the
    /// band draws at other sizes, because it draws only the name there.
    ///
    /// Written as the one sentence a screen reader reads, so a change to the
    /// separator, the order or the badge fails here.
    func testTheCollapsedBandSaysTheBadgeAndThePublisher() {
        XCTAssertEqual(
            "Image Generation, plugin, by Coders Farm",
            PluginBandStrings.collapsedLabel(name: "Image Generation", publisher: "Coders Farm")
        )
    }

    /// No publisher, no `by`: `PluginFrame.kt:144` draws that line only when
    /// the manifest has a non-blank publisher, and a label reading
    /// "Radio Stations, plugin, by" would be the bare `by` the band's own
    /// fallback exists to prevent.
    func testTheCollapsedBandSaysNoBareBy() {
        XCTAssertEqual(
            "Radio Stations, plugin",
            PluginBandStrings.collapsedLabel(name: "Radio Stations", publisher: nil)
        )
    }

    /// The three words the band draws, ported from Android word for word
    /// (`PluginFrame.kt:152`, `:144`, `:178`, `:181`). One copy now, so this
    /// is the only place they are checked.
    func testTheBandsWordsAreAndroids() {
        XCTAssertEqual("plugin", PluginBandStrings.badge)
        XCTAssertEqual("manage", PluginBandStrings.manage)
        XCTAssertEqual("by Coders Farm", PluginBandStrings.by("Coders Farm"))
        XCTAssertEqual(
            "manage Image Generation on the dashboard's Plugins page",
            PluginBandStrings.manageLabel(name: "Image Generation")
        )
    }

    /// The band's value, which the fold did not change and which one view now
    /// reads for both screens: a blank name falls back to the slug, a blank
    /// publisher is nil rather than an empty line, and `manage` goes to the
    /// dashboard's row for this plugin.
    func testTheBandFallsBackToTheSlug() {
        let bare = PluginBand.of(CapabilityPlugin(slug: "imagegen", name: "   ", publisher: " "))
        XCTAssertEqual("imagegen", bare?.name)
        XCTAssertNil(bare?.publisher)
        XCTAssertEqual("/#plugins/imagegen", bare?.managePath)
        XCTAssertNil(PluginBand.of(nil), "no plugin in the manifest means no band")
    }

    /// `pluginFor(_:)` picks the plugin that declares the capability, which is
    /// what the one folded view is handed instead of a screen-specific type.
    /// Two plugins in the manifest, so a band that read `plugins.first` would
    /// name the wrong one on one of the two screens.
    func testTheBandNamesThePluginThatDeclaredTheCapability() {
        let manifest = Capabilities(plugins: [
            CapabilityPlugin(
                slug: "imagegen",
                name: "Image Generation",
                publisher: "Coders Farm",
                appCapabilities: [capImagegen]
            ),
            CapabilityPlugin(
                slug: "radio",
                name: "Radio Stations",
                publisher: "Coders Farm",
                appCapabilities: [capStations]
            ),
        ])
        XCTAssertEqual("Radio Stations", PluginBand.of(manifest.pluginFor(capStations))?.name)
        XCTAssertEqual("Image Generation", PluginBand.of(manifest.pluginFor(capImagegen))?.name)
        XCTAssertNil(manifest.pluginFor("podcasts"), "an unknown capability has no plugin")
    }

    // MARK: - the toolbar pill's one sentence (owner answer 29)

    /// At the accessibility sizes `ShellTopBar` is not drawn and the pill is a
    /// glyph in the navigation bar, so everything the bar's four leaves said
    /// has to be in that glyph's label.
    func testTheToolbarPillSaysTheAddressTheWarningAndTheStatus() {
        let pill = ShellPill(
            label: "10.0.0.42:6369",
            accessibilityLabel: ShellStrings.switchServer,
            status: ShellStrings.statusLive,
            unverified: true
        )
        XCTAssertEqual(
            "switch server, 10.0.0.42:6369, unverified, live",
            pill.toolbarAccessibilityLabel
        )
    }

    /// The status words carry two leading spaces for a bar that draws them
    /// beside a dot (``ShellStrings/statusLive``). A screen reader reads a
    /// sentence, not a layout, so they are trimmed here — and that is checked
    /// rather than assumed, because the two spaces are invisible in a diff.
    func testTheToolbarPillTrimsTheChromesSpacing() {
        XCTAssertTrue(
            ShellStrings.statusOffline.hasPrefix("  "),
            "the status words no longer carry the bar's spacing, so trimming "
                + "them says nothing"
        )
        let pill = ShellPill(
            label: "stub house",
            accessibilityLabel: ShellStrings.switchServer,
            status: ShellStrings.statusOffline,
            unverified: false
        )
        XCTAssertEqual("switch server, stub house, offline", pill.toolbarAccessibilityLabel)
    }

    /// With no server saved the pill's words and its accessibility label are
    /// both ``ShellStrings/connectPill``, and the composed label must not read
    /// "connect, connect" — the branch at `ShellChrome.swift:61`.
    func testTheToolbarPillDoesNotSayConnectTwice() {
        let pill = ShellPill(
            label: ShellStrings.connectPill,
            accessibilityLabel: ShellStrings.connectPill,
            status: nil,
            unverified: false
        )
        XCTAssertEqual("connect", pill.toolbarAccessibilityLabel)
    }

    // MARK: - the hint ink's numbers (owner answer 65)

    /// ``HintInk/worstArithmeticRatio`` is the number the file's table claims
    /// for the worst of the four surfaces, and this is the arithmetic it
    /// claims to be: WCAG's ratio between the token's own sRGB and
    /// `secondarySystemBackground` in light mode.
    ///
    /// It is recomputed here rather than copied, so a change to the ink that
    /// left the number alone — or a number nobody checked — fails.
    func testTheHintInksWorstRatioIsItsArithmetic() {
        let computed = Self.contrast(HintInk.light, HintInk.appFieldPaperLight)
        XCTAssertEqual(
            HintInk.worstArithmeticRatio,
            computed,
            accuracy: 0.01,
            "the ink's worst surface computes at "
                + String(format: "%.3f", computed)
                + ":1 and the file claims \(HintInk.worstArithmeticRatio)"
        )
        XCTAssertGreaterThan(
            computed,
            4.5,
            "the hint ink is under the ratio Apple's audit asks of body text, "
                + "which is the whole of owner answer 65"
        )
    }

    /// The dark ink on the dark field background: the fourth row of the
    /// table, the one a light-mode-only check would miss — and the one whose
    /// number was **wrong**. It read 7.72:1 until this test was written; the
    /// arithmetic is 6.86:1. Still over the floor, so nothing shipped
    /// unreadable; it was a number written down and read by nobody, which is
    /// the argument for this whole file.
    func testTheDarkHintInkClearsTheFloorToo() {
        let dark = Self.contrast(HintInk.dark, HintInk.appFieldPaperDark)
        XCTAssertEqual(
            HintInk.darkArithmeticRatio,
            dark,
            accuracy: 0.01,
            "the dark ink computes at " + String(format: "%.3f", dark)
                + ":1 and the file claims \(HintInk.darkArithmeticRatio)"
        )
        XCTAssertGreaterThan(
            dark,
            4.5,
            "the dark hint ink measures " + String(format: "%.2f", dark)
                + ":1 on the dark field background"
        )
        XCTAssertLessThan(
            HintInk.worstArithmeticRatio,
            dark,
            "the light row is no longer the worst of the four, so the number "
                + "the UI case's floor was chosen against has moved"
        )
    }

    /// Apple's `placeholderText` is rgb(197, 197, 199) — **1.72:1** on a
    /// `.roundedBorder` field's white interior, which is what answer 65 is
    /// about. Here so that the comparison the hint ink exists for is a number
    /// in a test rather than a sentence in a comment.
    func testApplesPlaceholderGreyIsTheOneUnderTheFloor() {
        let apple = Self.contrast(
            (red: 197 / 255, green: 197 / 255, blue: 199 / 255),
            (red: 1, green: 1, blue: 1)
        )
        XCTAssertLessThan(apple, 2.0, "Apple's grey measures \(apple):1 on white")
        XCTAssertGreaterThan(
            Self.contrast(HintInk.light, (red: 1, green: 1, blue: 1)),
            apple * 3,
            "the token is not meaningfully darker than the grey it replaces"
        )
    }

    // MARK: - the destructive pair's three ratios (the rooms unit's ask)

    /// ``DangerInk``'s two written-down ratios, recomputed from its own sRGB.
    ///
    /// Review 3's S5: the pair was declared in the app target with its three
    /// numbers in a doc comment and **nothing recomputing any of them** —
    /// `HintInk`'s stale 7.72:1 all over again, one review later. So the sRGB
    /// moved into the kit and this is the caller it now has. Rounded to two
    /// decimals, which is how the doc comment and DECISIONS §3b write them.
    func testTheDestructivePairMeetsTheRatiosItClaims() {
        XCTAssertEqual(
            DangerInk.lightArithmeticRatio,
            Self.rounded(Self.contrast(DangerInk.textLight, DangerInk.fillLight)),
            "the light pair reads "
                + "\(Self.contrast(DangerInk.textLight, DangerInk.fillLight)), not "
                + "the \(DangerInk.lightArithmeticRatio) this type claims"
        )
        XCTAssertEqual(
            DangerInk.darkArithmeticRatio,
            Self.rounded(Self.contrast(DangerInk.textDark, DangerInk.fillDark)),
            "the dark pair reads "
                + "\(Self.contrast(DangerInk.textDark, DangerInk.fillDark)), not "
                + "the \(DangerInk.darkArithmeticRatio) this type claims"
        )
        // Both over the 4.5:1 a label on a button needs, which is the point of
        // the pair and not only of the arithmetic.
        XCTAssertGreaterThan(DangerInk.lightArithmeticRatio, 4.5)
        XCTAssertGreaterThan(DangerInk.darkArithmeticRatio, 4.5)
    }

    /// **The number the pair exists to avoid**: plain white on the dark fill.
    ///
    /// Android draws white on `colors.err` (`PhoneDropinCall.kt:180-183`) and
    /// the decision not to copy that rests entirely on this figure, so this is
    /// the one most worth recomputing. It must also be a *failing* ratio — if
    /// it ever came out over 4.5 the whole reason for a second token would be
    /// gone, and the decision would be wrong rather than the number.
    func testWhiteOnTheDarkFillIsTheFailureTheSecondTokenAvoids() {
        let white = (red: 1.0, green: 1.0, blue: 1.0)
        XCTAssertEqual(
            DangerInk.whiteOnTheDarkFillRatio,
            Self.rounded(Self.contrast(white, DangerInk.fillDark)),
            "white on the dark destructive fill reads "
                + "\(Self.contrast(white, DangerInk.fillDark)), not the "
                + "\(DangerInk.whiteOnTheDarkFillRatio) the decision cites"
        )
        XCTAssertLessThan(
            DangerInk.whiteOnTheDarkFillRatio,
            4.5,
            "white on the dark fill now passes, so `--danger-fg` flipping with "
                + "the theme is no longer a legibility fix and DECISIONS §3b is "
                + "arguing for two tokens on a number that stopped saying it"
        )
    }

    // MARK: - which shape the header takes (owner answer 29)

    /// The rule, both sides of the accessibility boundary.
    ///
    /// This is the reading review 2's uncaught sabotage asked for: it undid
    /// answer 29 in **both** view bodies at once — the size gate off
    /// `WorkspaceView`'s `if`, the branch off `ShellScrollAwayChrome.body` —
    /// and the full `DomovoiKit` suite stayed green, because `pkgtest.sh`
    /// builds the package and `appcheck.sh` is broken on the base branch too.
    /// The decision is ``ShellHeaderFold`` now and the views draw its answer,
    /// so a sabotage of the **rule** fails here.
    ///
    /// It is only half the reading, and review 3 proved it: re-applying
    /// review 2's own sabotage — the two **view bodies**, rule untouched —
    /// left this file 18 of 18 green. `ShellFoldViewBodyTests` is the other
    /// half; it reads the three bodies as source text and fails on that
    /// sabotage. A train is still what reads the real screens.
    func testTheHeaderIsABarBelowTheAccessibilitySizesAndATitleAbove() {
        XCTAssertEqual(
            .barAboveTheStack,
            ShellHeaderFold.forHeader(drawn: true, isAccessibilitySize: false)
        )
        XCTAssertEqual(
            .titleInsideTheStack,
            ShellHeaderFold.forHeader(drawn: true, isAccessibilitySize: true)
        )
    }

    /// Below the accessibility sizes there is **no title at all**, which is not
    /// the same as an empty one: an empty large title still reserves the large
    /// navigation bar's height on every screen in the app, and that is the
    /// alternative REPORT's N3 rejected.
    func testBelowTheAccessibilitySizesThereIsNoTitleAndNoToolbarPill() {
        let bar = ShellHeaderFold.forHeader(drawn: true, isAccessibilitySize: false)
        XCTAssertNil(bar.navigationTitle(crumb: "domovoi / home"))
        XCTAssertTrue(bar.drawsBarAboveTheStack)
        XCTAssertFalse(bar.putsTheHeaderInsideTheStack)
    }

    /// At them it is the breadcrumb, **trimmed**.
    ///
    /// `ShellStrings.localTopbar` carries two leading spaces for a bar that
    /// draws them beside a dot; a screen reader reads a title, not a layout.
    /// The trimming is part of the rule rather than something a call site
    /// remembers, which is why it is asserted here with the string that has
    /// the spaces in it.
    func testAtTheAccessibilitySizesTheTitleIsTheTrimmedBreadcrumb() {
        let folded = ShellHeaderFold.forHeader(drawn: true, isAccessibilitySize: true)
        XCTAssertEqual("domovoi / home", folded.navigationTitle(crumb: "domovoi / home"))
        XCTAssertEqual(
            "domovoi / local media",
            folded.navigationTitle(crumb: ShellStrings.localTopbar),
            "the local shell's title keeps the bar's leading spaces"
        )
        XCTAssertFalse(folded.drawsBarAboveTheStack)
        XCTAssertTrue(folded.putsTheHeaderInsideTheStack)
    }

    /// The `AuditStrip.topBar` variant loses the header at **every** size.
    ///
    /// Review 1's N5: that variant's whole job is the shell without its
    /// header, and when the fold put the header in the navigation bar the
    /// variant became identical to the control at AX5 — so the bisect that
    /// variant exists for lost its one difference.
    func testTheHeaderlessVariantHasNoHeaderAtEitherSize() {
        for accessibility in [false, true] {
            let fold = ShellHeaderFold.forHeader(drawn: false, isAccessibilitySize: accessibility)
            XCTAssertEqual(.noHeaderAtAll, fold)
            XCTAssertFalse(fold.drawsBarAboveTheStack)
            XCTAssertFalse(fold.putsTheHeaderInsideTheStack)
            XCTAssertNil(fold.navigationTitle(crumb: "domovoi / home"))
        }
    }

    /// The pill is the bar's **or** the toolbar's, never both: two
    /// `switch server` controls on one screen is the bar still being drawn
    /// with the toolbar item under it, which is the shape
    /// `testShell_TheHeaderScrollsAwayOnTheLocalShell` counts on the screen.
    ///
    /// **Stated over the two properties the view bodies actually read.** It
    /// used to be `drawsBarAboveTheStack && drawsTheToolbarPill`, and that
    /// second property had no caller left once the dead gate came out of
    /// `ShellScrollAwayChrome.body` — a seam read only by the test that
    /// asserted it, which on this track is its own defect class. A non-nil
    /// `navigationTitle(crumb:)` *is* the toolbar pill being drawn: it is the
    /// branch the body takes to hang the title and the toolbar item together.
    func testTheBarAndTheToolbarNeverBothCarryThePill() {
        for fold in ShellHeaderFold.allCases {
            XCTAssertFalse(
                fold.drawsBarAboveTheStack
                    && fold.navigationTitle(crumb: "domovoi / home") != nil,
                "\(fold.rawValue) draws the pill twice: `ShellTopBar` above the "
                    + "stack carries it and the toolbar item under it carries it "
                    + "again"
            )
        }
    }

    // MARK: - the dialog's inline fallback (owner answer 6)

    /// The guard, on all four of its inputs.
    ///
    /// Review 2: the fallback review 1 asked for had no test on any path. It
    /// is a value now, so both paths have one — and the one that matters is
    /// the **late** cover: a presentation that arrives after the deadline
    /// takes the screen back, because it is opaque and on top, so the two
    /// cards are never both the thing a person is looking at.
    func testTheCardIsInlineOnlyWhenTheDeadlinePassedWithNoCover() {
        XCTAssertTrue(
            DialogPresentationGuard.cardIsInline(coverAppeared: false, deadlinePassed: true),
            "the deadline passed with no cover and the card is not drawn: that "
                + "is the blank undismissable screen the guard exists for"
        )
        XCTAssertFalse(
            DialogPresentationGuard.cardIsInline(coverAppeared: false, deadlinePassed: false),
            "the card is inline before the deadline, so every dialog in the app "
                + "draws twice for a second and a half"
        )
        XCTAssertFalse(
            DialogPresentationGuard.cardIsInline(coverAppeared: true, deadlinePassed: false),
            "the presentation arrived and the card is inline as well"
        )
        XCTAssertFalse(
            DialogPresentationGuard.cardIsInline(coverAppeared: true, deadlinePassed: true),
            "a cover that arrived LATE leaves the inline card up behind it"
        )
    }

    /// The deadline is a second and a half, and the identifier is the one the
    /// UI cases spell out.
    ///
    /// Both are numbers-and-strings written in one place and read in another,
    /// which on this branch has already been wrong once (`HintInk`'s 7.72).
    /// A normal presentation takes about a third of a second, so the deadline
    /// has to be several times that and nowhere near a person's patience.
    func testTheDeadlineIsQuietForAWorkingPresentation() {
        XCTAssertEqual(1_500_000_000, DialogPresentationGuard.lostAfterNanoseconds)
        XCTAssertEqual(
            "shell-dialog-inline-fallback",
            DialogPresentationGuard.inlineFallbackIdentifier,
            "the identifier `StubHouseUITests+Shell.swift` asserts absent has "
                + "changed, so that case now asserts the absence of nothing"
        )
    }

    /// Two decimals, the precision every ratio in this project is written at.
    private static func rounded(_ ratio: Double) -> Double {
        (ratio * 100).rounded() / 100
    }

    /// WCAG 2.1's contrast ratio between two sRGB colours, 0–1 per channel.
    /// The same arithmetic the UI case does on the screen's own pixels, so the
    /// two readings are comparable.
    private static func contrast(
        _ a: (red: Double, green: Double, blue: Double),
        _ b: (red: Double, green: Double, blue: Double)
    ) -> Double {
        func channel(_ value: Double) -> Double {
            value <= 0.03928 ? value / 12.92 : pow((value + 0.055) / 1.055, 2.4)
        }
        func luminance(_ rgb: (red: Double, green: Double, blue: Double)) -> Double {
            0.2126 * channel(rgb.red) + 0.7152 * channel(rgb.green) + 0.0722 * channel(rgb.blue)
        }
        let one = luminance(a)
        let two = luminance(b)
        return (max(one, two) + 0.05) / (min(one, two) + 0.05)
    }
}
