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

/// The 40-point pill with two segments.
///
/// `YourDevice.kt:196`. Split out from the container so the container is only
/// about keeping both sides alive, and so this can be measured on its own at
/// two type sizes.
struct PhoneSourceSwitchPill: View {

    let serverTitle: String
    let source: PhoneContentSource
    let onPick: (PhoneContentSource) -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // **A column at an accessibility size.** Android's pill is a 40 dp
        // row of two segments; two long labels side by side at AX5 — a house
        // called "the flat upstairs" beside "your device" — cannot both fit
        // across a phone, and the standing rule here is to swap the row for a
        // column rather than shrink the text.
        Group {
            if typeSize.isAccessibilitySize {
                VStack(spacing: 6) { segments }
            } else {
                HStack(spacing: 6) { segments }
            }
        }
        // `selectableGroup` is Android's (`YourDevice.kt:200`); the iOS
        // equivalent is the two segments carrying `.isSelected` themselves,
        // which they do, under one container VoiceOver reads as a tab bar.
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
