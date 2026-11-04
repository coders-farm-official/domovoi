import XCTest
@testable import DomovoiKit

/// The three pieces of pure `DomovoiKit` value logic this batch's shell unit
/// wrote: the folded plugin band's words, the toolbar pill's composed label,
/// and the hint ink's numbers.
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
