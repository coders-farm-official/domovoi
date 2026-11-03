import DomovoiKit
import SwiftUI

/// The server-or-device switch on top of Music, Videos and Files, and the two
/// sides under it.
///
/// `ui/screens/local/YourDevice.kt:126`'s `SourceSwitchPage` and `:196`'s
/// `SourceSwitch`.
///
/// ### Both sides stay alive *once shown*, and that is the whole point
///
/// Android keeps a `kept` set and composes a side only when it is the shown
/// one or has been shown before (`YourDevice.kt:140`); after that it keeps
/// both compositions and merely declines to **place** the
/// hidden one (`YourDevice.kt:173`), parking its back handlers on a
/// dispatcher nothing presses (`:181`). The reason is stated at
/// `YourDevice.kt:134`: switching to the phone and back must find the
/// server's page exactly where it was left — the folder, the tab, the page of
/// tracks, the scroll — **without asking the server again**.
///
/// A SwiftUI `if` would destroy the hidden side's `@State` and every model it
/// holds, so the iOS shape is a `ZStack` with the hidden side at zero
/// opacity, not hit-testable and hidden from VoiceOver. All three matter and
/// none is cosmetic: without `allowsHitTesting(false)` an invisible button
/// takes taps meant for the visible one, and without `accessibilityHidden`
/// VoiceOver reads two screens at once. `.opacity(0)` rather than `.hidden()`
/// because `.hidden()` also removes the view from layout, and a list that was
/// never laid out has no scroll position to come back to.
///
/// ### What is not, and why there is no control for it
///
/// **Audiobooks has no switch.** Android wraps it too
/// (`ScreenRouter.kt:63`), with the books saved on the phone as its device
/// side; there is no saved-book store on iOS yet
/// (``ShellModel/savedBooks`` is a constant `false` whose own comment says
/// the store is a later batch's), so a "your device" segment there would lead
/// to nothing. DECISIONS records it.
///
/// **The switch does not step aside for the keyboard.** Android's does
/// (`YourDevice.kt:198`) because its pinned header sits in a window the
/// keyboard is drawn *over*. This pill is pinned too — it is in the `VStack`
/// above the two sides, and each side scrolls under it — so the earlier
/// reason given here ("it scrolls with the page it heads") was simply wrong
/// about this file's own layout. The real reason the omission is safe is the
/// platform's: on iOS the keyboard shrinks the safe area rather than
/// overlaying the window, so everything above the scrolling content is still
/// on screen. Nothing to step aside from, for a different reason than the one
/// that was written here.
///
/// **It does not step aside for a type size either, and that is the one
/// Android's rule nearly covers.** `keyboardCrowdsTheWindow()`
/// (`AppShell.kt:498`) returns early unless the IME is up, so its
/// `CONTENT_FLOOR` of 260 dp is a keyboard rule and Android has no answer for
/// a window the *text* has made short. iOS does need one — at
/// `AccessibilityXXXL` this switch left the page 178.7 of 852 points, 81
/// under that floor — and the answer taken here is to **collapse** rather
/// than to vanish: a control that goes away at a type size never comes back,
/// where a keyboard does. See ``PhoneSourceSwitchPill``.
struct PhoneSourceSwitchView<Server: View, Device: View>: View {

    let page: PhoneMediaPageKind

    /// The side each page last showed, for the workspace's lifetime. Held by
    /// ``RouteScreen``, which is the one view wrapping all three routes and
    /// therefore has the same lifetime Android's shell-provided
    /// `LocalSourceChoices` has (`YourDevice.kt:57`).
    let choices: PhoneSourceChoices

    /// The server's own name, which is the left segment's label
    /// (`YourDevice.kt:212`).
    let serverTitle: String

    @ViewBuilder let server: Server
    @ViewBuilder let device: Device

    private var shown: PhoneContentSource { choices.of(page) }

    /// The sides that have been looked at, this view's lifetime —
    /// `YourDevice.kt:140`'s `kept`.
    ///
    /// Without it the device side was **built on every visit** to Music,
    /// Videos and Files even when nobody switched to it: its `.task` runs
    /// ``PhoneFolderBookmarks``' resolution per saved folder, a directory
    /// listing (or on Videos a whole `PHAsset` fetch), and two HTTP requests
    /// to the house for the upload status. Android does not pay that, and
    /// neither should this.
    @State private var kept: Set<PhoneContentSource> = []

    var body: some View {
        VStack(spacing: 0) {
            PhoneSourceSwitchPill(
                serverTitle: serverTitle,
                source: shown,
                onPick: { choices.choose(page, $0) }
            )
            ZStack {
                // A side that has been shown keeps the SAME modifier chain
                // afterwards — see ``side(_:visible:)``. Only a side nobody
                // has opened yet is absent.
                if kept.contains(.server) {
                    side(server, visible: shown == .server)
                }
                if kept.contains(.device) {
                    side(device, visible: shown == .device)
                }
            }
        }
        // `.onAppear` as well as `.onChange`: the first side is shown without
        // anything changing.
        .onAppear { kept.insert(shown) }
        .onChange(of: shown) { _, next in kept.insert(next) }
    }

    /// One side, shown or parked — ``ShownOrParked``'s counterpart
    /// (`YourDevice.kt:181`). **One modifier chain either way**, because
    /// swapping the shape of the view tree is what throws away the state this
    /// whole type exists to keep.
    private func side<Content: View>(_ content: Content, visible: Bool) -> some View {
        content
            .opacity(visible ? 1 : 0)
            .allowsHitTesting(visible)
            .accessibilityHidden(!visible)
    }
}

/// The 40-point pill with two segments — and, at an accessibility type size,
/// one row that names the side it is showing.
///
/// `YourDevice.kt:196`. Split out from the container so the container is only
/// about keeping both sides alive, and so this can be measured on its own at
/// two type sizes.
///
/// ### Why the column became a menu, measured
///
/// The first port obeyed the standing Dynamic Type rule — "swap a row to a
/// column on `dynamicTypeSize.isAccessibilitySize`" — and every piece of it
/// was what the rules ask for: `@ScaledMetric` glyphs, no
/// `minimumScaleFactor`, labels free to wrap. It was still the wrong control,
/// and what says so is a measurement rather than taste. From R003's
/// `AccessibilityXXXL` tree of Music
/// (`overnight-trains/R003/MusicMore/testMusicMore_TheTabStripTextGrowsAtAX5.log`),
/// on a 393×852 phone:
///
/// | band | points | owner |
/// |---|---|---|
/// | the shell's top bar (crumb, server chip, `unverified`, `live`) | 300.3 | Shell |
/// | `NavigationBar` | 44 | SwiftUI |
/// | **this control**, as a column | **164** | here |
/// | the page the person came for | **178.7** | — |
/// | the phone bar | 146 | Shell |
///
/// The column is 164 because the server's own address wraps to two 49-point
/// lines (`phone-source-server` at `{{15.5, 351.8}, {362, 99}}`) above a
/// 60-point `your device` — so the single biggest band this unit controls is
/// spent on a two-way switch, permanently, at exactly the sizes the people
/// who need large text use.
///
/// Android's shell has a rule for a window this short and the rule is an
/// absolute floor: `AppShell.kt:481`'s `CONTENT_FLOOR = 260.dp`, below which
/// `SourceSwitch` (`YourDevice.kt:198`) and `PageHeader` (`Kit.kt:212`) do not
/// draw at all. 178.7 is **81 points under that floor**. Android's trigger is
/// the keyboard, which goes away; a type size does not, so porting the rule
/// literally would make "your device" permanently unreachable at an
/// accessibility size — a control that vanishes is worse than a cramped one.
///
/// So at accessibility sizes the switch collapses to **one row**: a `Menu`
/// naming the side the page is showing, whose two choices are full-width
/// readable rows carrying the server's whole address and `your device`. One
/// 49-point line of `.subheadline` plus this view's own 16 points of vertical
/// padding is about 65 points instead of 164, which hands the page back
/// roughly 99 and puts it above Android's floor. Nothing is shrunk, nothing
/// is hidden, and the words the column showed in full are still shown in full
/// — in the menu, where there is a whole screen to show them on.
/// ``StubHouseUITests/testPhoneMedia_TheSwitchSegmentsGrowWithTheTypeSize``
/// measures both the text's growth and the page's height, so the floor is
/// asserted and not argued.
///
/// **Default sizes are unchanged.** The pill below an accessibility size is
/// Android's 40 dp row of two segments, to the point.
struct PhoneSourceSwitchPill: View {

    let serverTitle: String
    let source: PhoneContentSource
    let onPick: (PhoneContentSource) -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        Group {
            if typeSize.isAccessibilitySize {
                PhoneSourceCollapsedSwitch(
                    serverTitle: serverTitle,
                    source: source,
                    onPick: onPick
                )
            } else {
                HStack(spacing: 6) { segments }
            }
        }
        // `selectableGroup` is Android's (`YourDevice.kt:200`); the iOS
        // equivalent is the two segments carrying `.isSelected` themselves,
        // which they do, under one container VoiceOver reads as a tab bar.
        // The collapsed row is one control and reads as one.
        .accessibilityElement(children: .contain)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    @ViewBuilder private var segments: some View {
        PhoneSourceSegment(
            glyph: "house",
            label: serverTitle,
            // **What VoiceOver says is not what is written on it.** The
            // visible words are the house's address, which Apple's audit
            // reported as a label that is not human-readable (train S006);
            // ``PhoneMediaStrings/serverSideLabel(_:)`` says what the address
            // IS, and the segment still shows the address itself.
            voice: PhoneMediaStrings.serverSideLabel(serverTitle),
            // The server's segment is labelled with the house's own name,
            // which is whatever its owner called it — so a UI case cannot
            // find it by label. It carries an identifier instead; the
            // device's has one for symmetry, and neither replaces a label.
            identifier: Self.serverSegmentID,
            selected: source == .server,
            onTap: { onPick(.server) }
        )
        PhoneSourceSegment(
            glyph: "iphone",
            label: PhoneMediaStrings.yourDevice,
            // The same words it shows: `your device` is already a phrase,
            // and every case that finds this segment finds it by that label.
            voice: PhoneMediaStrings.yourDevice,
            identifier: Self.deviceSegmentID,
            selected: source == .device,
            onTap: { onPick(.device) }
        )
    }

    static let serverSegmentID = "phone-source-server"
    static let deviceSegmentID = "phone-source-device"

    /// The collapsed row's own handle, for the same reason the server's
    /// segment has one: at an accessibility size the row is labelled with
    /// whichever side is current, so there is no fixed label to find it by.
    static let collapsedID = "phone-source-switch"
}

/// The accessibility-size switch: one row that says which side the page is
/// showing, and a menu that offers the other.
///
/// Why this exists at all, and what the column it replaces cost, is in
/// ``PhoneSourceSwitchPill``'s doc comment. What is here is the shape.
///
/// ### The row truncates and the menu does not, deliberately
///
/// `lineLimit(1)` on the row is Android's own `maxLines = 1, overflow =
/// Ellipsis` (`YourDevice.kt:251-252`), and it is the whole point of the
/// collapse: one line is a fixed height, and a fixed height is what the page
/// under it gets back. A house whose address or name is longer than the row
/// therefore shows `127.0.0.1:660…` — which would be a control that cannot
/// say what it is for, except that the **menu** shows the same name on a
/// full-width row that wraps as far as it needs, and VoiceOver is given the
/// whole of it either way (``accessibilityLabel``, below, never the truncated
/// glyphs). `minimumScaleFactor` is rejected throughout this app and is not
/// what is happening here: nothing is shrunk.
///
/// ### What VoiceOver gets
///
/// The row's label is ``PhoneMediaStrings/showingSide(_:)`` over the current
/// side's spoken name — so "showing the server at 127.0.0.1:6601", never a
/// bare address, which is the finding train S006 raised against
/// ``PhoneSourceSegment``. The two menu rows carry the same spoken names and
/// the current one carries `.isSelected`, so "which side is on" and "the
/// other one is offered here" are both answered without sight.
struct PhoneSourceCollapsedSwitch: View {

    let serverTitle: String
    let source: PhoneContentSource
    let onPick: (PhoneContentSource) -> Void

    /// ``PhoneSourceSegment``'s reason, unchanged: the glyph is drawn at
    /// `.footnote` and is about twice as wide at AX5, and a fixed
    /// `frame(width:)` there draws the icon column across the row's words.
    @ScaledMetric(relativeTo: .footnote) private var glyphWidth: CGFloat = 18

    var body: some View {
        Menu {
            // `allCases`, in the enum's own order, which is the order the two
            // segments are drawn in (`YourDevice.kt:212`, `:215`): the
            // server's side first, because that is the side every page starts
            // on.
            ForEach(PhoneContentSource.allCases, id: \.self) { side in
                Button {
                    onPick(side)
                } label: {
                    // A checkmark for the side that is on — the platform's own
                    // way of saying so in a menu — and the side's own glyph
                    // otherwise. The row above says which is current too; this
                    // is what says it *inside* the menu, where the row is
                    // covered.
                    Label(
                        Self.title(of: side, serverTitle),
                        systemImage: side == source ? "checkmark" : Self.glyph(of: side)
                    )
                }
                .accessibilityLabel(Self.voice(of: side, serverTitle))
                .accessibilityAddTraits(side == source ? [.isButton, .isSelected] : .isButton)
                .accessibilityIdentifier(Self.identifier(of: side))
            }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: Self.glyph(of: source))
                    .font(.footnote)
                    .frame(width: glyphWidth)
                    // Decorative: the row's own label says which side it is.
                    .accessibilityHidden(true)
                Text(Self.title(of: source, serverTitle))
                    .font(.subheadline)
                    // One line, so the row is one line tall. See this type's
                    // doc comment for why truncating here is not a control
                    // that lies.
                    .lineLimit(1)
                    .truncationMode(.tail)
                Spacer(minLength: 6)
                Image(systemName: "chevron.down")
                    .font(.footnote)
                    .accessibilityHidden(true)
            }
            // `brandInk` inside the label, not `.foregroundStyle` on the
            // `Menu`: a menu tints its label with the accent colour, and the
            // ink tokens are what this app's contrast is measured in —
            // `brandInk` is 5.65:1 against 2.05:1 for ``Color/brand``.
            .foregroundStyle(Color.brandInk)
            .padding(.horizontal, 12)
            // 44, the platform minimum every tappable thing here is held to.
            // At an accessibility size the single line of `.subheadline` is
            // taller than that on its own, so this binds only in the band
            // just above `large`.
            .frame(minHeight: 44)
            .frame(maxWidth: .infinity)
            .contentShape(.rect)
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(Color.brandInk, lineWidth: 1)
            )
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.brand.opacity(0.12))
            )
        }
        // `.plain`, so the menu draws the row above and no chrome of its own:
        // the row's height is the page's height, and a bordered menu style
        // would spend points nobody chose.
        .buttonStyle(.plain)
        .accessibilityLabel(PhoneMediaStrings.showingSide(Self.voice(of: source, serverTitle)))
        .accessibilityHint(PhoneMediaStrings.pickWhichSide)
        .accessibilityIdentifier(PhoneSourceSwitchPill.collapsedID)
    }

    /// The words on screen for a side — the house's own name, or
    /// ``PhoneMediaStrings/yourDevice``. The same two strings the segments
    /// show (`YourDevice.kt:212`, `:215`).
    static func title(of side: PhoneContentSource, _ serverTitle: String) -> String {
        side == .server ? serverTitle : PhoneMediaStrings.yourDevice
    }

    /// What VoiceOver says for a side. The server's visible words are an
    /// address, which is not a label (train S006), so the two differ there and
    /// nowhere else.
    static func voice(of side: PhoneContentSource, _ serverTitle: String) -> String {
        side == .server
            ? PhoneMediaStrings.serverSideLabel(serverTitle)
            : PhoneMediaStrings.yourDevice
    }

    /// `YourDevice.kt:212`'s `Icons.Filled.Dns` and `:215`'s
    /// `Icons.Filled.PhoneAndroid`, as ``PhoneSourceSwitchPill`` spells them.
    static func glyph(of side: PhoneContentSource) -> String {
        side == .server ? "house" : "iphone"
    }

    /// The same handles the two segments carry, so a case finds the side it
    /// means at either type size.
    static func identifier(of side: PhoneContentSource) -> String {
        side == .server
            ? PhoneSourceSwitchPill.serverSegmentID
            : PhoneSourceSwitchPill.deviceSegmentID
    }
}

/// One segment. `YourDevice.kt:222`'s `SourceSegment`: brand-soft fill and a
/// brand border when it is the chosen one.
struct PhoneSourceSegment: View {

    let glyph: String
    let label: String
    /// What VoiceOver reads, which is the visible `label` for every segment
    /// whose words are already a phrase. The server's are its address, and an
    /// address alone is a label Apple's audit calls not human-readable — so
    /// the two are separate arguments rather than one.
    let voice: String
    /// An accessibility identifier **beside** the label, never instead of it:
    /// the server's segment is named by its house and a case needs a stable
    /// handle on it.
    let identifier: String
    let selected: Bool
    let onTap: () -> Void

    /// `@ScaledMetric`, never a fixed `frame(width:)`: the glyph is drawn at
    /// `.footnote` and at AX5 it is about twice as wide, which is how a
    /// 26-point icon column came to be drawn straight across a row's own
    /// words one screen earlier (``FileRowView``'s note).
    @ScaledMetric(relativeTo: .footnote) private var glyphWidth: CGFloat = 18

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 6) {
                Image(systemName: glyph)
                    .font(.footnote)
                    .frame(width: glyphWidth)
                    // Decorative: the button's own label says which side it
                    // is.
                    .accessibilityHidden(true)
                Text(label)
                    .font(.subheadline)
                    // Phrases, not words — a server's name is whatever its
                    // owner called it — so they wrap.
                    // `minimumScaleFactor` is rejected throughout this app.
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            .padding(.horizontal, 12)
            // 44, not Android's 40: the platform minimum, and the number
            // every tappable thing in this app is held to.
            .frame(minHeight: 44)
            .frame(maxWidth: .infinity)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // `brandInk` for the chosen one's words, never ``Color/brand``:
        // 5.65:1 against 2.05:1, and a segment's label is text.
        .foregroundStyle(selected ? Color.brandInk : Color.primary)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(selected ? Color.brand.opacity(0.12) : Color.clear)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(
                    selected ? Color.brandInk : Color.fgMuted.opacity(0.35),
                    lineWidth: 1
                )
        )
        // `Role.Tab` is Android's (`YourDevice.kt:224`). `.isSelected` is what
        // tells VoiceOver which of the two is on; without it both read
        // identically and the switch is unusable without sight.
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
        // Before the identifier, so the identifier is not what names it: an
        // identifier is a handle for a test and never reaches VoiceOver.
        .accessibilityLabel(voice)
        .accessibilityIdentifier(identifier)
    }
}
