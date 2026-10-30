import DomovoiKit
import SwiftUI

// ---------------------------------------------------------------------------
// The chrome Home's seven cards share, and the three small pieces that would
// otherwise be copied into each of them.
//
// Android has one `HomeCard(title, action)` that every section draws inside
// (`HomeSections.kt`). Part 2 built the attention card's chrome inline and
// said why: "a shared component with one use is a shape guessed from one
// example. Part 3 brings timers, rooms, announce, today and the everything
// grid, and that is the batch that knows what the shared card has to hold."
// This is that batch, and the answer is: a title that is a heading, an
// optional link at the right of the title row, and the content. Nothing else
// was needed by all six.
//
// **Nothing in this file holds a string.** Every title and every link label
// comes from the package's pinned enums, because a literal in a view is
// outside what `ShellStringsTests` can see.
// ---------------------------------------------------------------------------

/// One of Home's cards: a heading, an optional header link, and the contents.
///
/// `rooms` is the one section that draws **no** card (`HomeSections.kt:570` —
/// a plain label row with the stop-all button at its right), so
/// ``HomeRoomsView`` uses ``HomeSectionHeader`` directly and not this.
struct HomeCard<Content: View>: View {

    let title: String

    /// The header link — `+N more`, `calendar`, `edit the bar` — or nil.
    let actionLabel: String?
    let onAction: () -> Void

    /// The link's minimum tap target. 44 is Apple's and is the default; the
    /// attention card passes ``HomeAttentionState/rowMinHeight``, which is
    /// Android's 40 dp on a wide pane.
    let actionMinHeight: CGFloat

    /// No `@ViewBuilder` on the property: there is an explicit `init` below,
    /// which is where the builder goes. (``PanelCard`` relies on the attribute
    /// because it takes the synthesised memberwise initialiser.)
    let content: Content

    init(
        title: String,
        actionLabel: String? = nil,
        actionMinHeight: CGFloat = 44,
        onAction: @escaping () -> Void = {},
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.actionLabel = actionLabel
        self.actionMinHeight = actionMinHeight
        self.onAction = onAction
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HomeSectionHeader(
                title: title,
                actionLabel: actionLabel,
                actionMinHeight: actionMinHeight,
                onAction: onAction
            )
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        // The same fill the rest of the app's cards use
        // (`ConnectionDialogView.swift:330`, `PhonesAndBrowsersView.swift:303`,
        // and the attention card since part 2), so Home does not introduce a
        // second card look.
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
    }
}

/// A section's title row: the heading, and a link at its right.
///
/// Separate from ``HomeCard`` because the rooms section is exactly this row
/// with no card around it.
struct HomeSectionHeader: View {

    let title: String
    let actionLabel: String?
    var actionMinHeight: CGFloat = 44
    let onAction: () -> Void

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                .font(.headline)
                // A card title is a heading, so VoiceOver's heading rotor can
                // reach it and the rows under it read as belonging to it.
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            if let actionLabel {
                HomeLink(actionLabel, minHeight: actionMinHeight, action: onAction)
            }
        }
    }
}

/// Android's `HomeLink`: a word that navigates.
///
/// ### Why this exists rather than a `Button` at each site
///
/// Every one of these has been got wrong on this track at least once. Apple's
/// audit measured the header's pair link at **15.7 points** and the forget
/// button at **8.7**, because one line of `.footnote` is all the height a
/// plain text button gets and SwiftUI hit-tests the *drawn* content. So this
/// states both halves of the fix in one place — the 44-point minimum **and**
/// the `contentShape` that makes the frame the target rather than merely
/// reserved — and the label wraps instead of truncating, because these are
/// phrases (`set up a room`, `edit the bar`) and `minimumScaleFactor` is
/// rejected throughout this app.
struct HomeLink: View {

    let label: String
    var minHeight: CGFloat = 44
    let action: () -> Void

    init(_ label: String, minHeight: CGFloat = 44, action: @escaping () -> Void) {
        self.label = label
        self.minHeight = minHeight
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
                .frame(minHeight: minHeight, alignment: .center)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // `brandInk`, never ``Color/brand``: 5.65:1 against 2.05:1, and a link
        // is text.
        .foregroundStyle(Color.brandInk)
    }
}

/// A tone, as a dot.
///
/// **The colour is never the only carrier of meaning**: beside every one of
/// these is a word saying what it grades. The dot is hidden from VoiceOver
/// because the words are what a row reads out and a stop saying "red circle"
/// is noise.
struct ToneDot: View {

    let tone: HealthTone

    /// Android pulses the brand dot for a playing room and for the online
    /// announce pill (`StatusDot(live = true)`). **Not ported**, and this is
    /// the parameter that records it rather than a silent omission: nothing in
    /// this app animates yet, an animation that never stops is a thing to put
    /// in front of Reduce Motion first, and the pill beside it already says
    /// `playing` in words. On the Simulator checklist.
    var live = false

    var body: some View {
        Circle()
            .fill(tone.dotColour)
            .frame(width: 8, height: 8)
            // Lifted to the cap height of the line beside it, which
            // `firstTextBaseline` alignment on a `Circle` cannot do by itself:
            // a shape has no baseline.
            .offset(y: -2)
            .accessibilityHidden(true)
    }
}

/// One pill: a word, in its tone, on an **opaque** surface.
///
/// The surface is opaque on purpose. `.background(.bar)` cost this track five
/// rounds of misdiagnosis on Apple's contrast audit, and the reason is simple
/// once said: a contrast check cannot read a colour that is whatever happens
/// to be behind it. `Color(.secondarySystemBackground)` is a colour.
///
/// The brand pill is the exception and takes the fill-and-ink pair the app
/// already uses for its one prominent button: ``Color/brand`` behind
/// ``Color/brandFg``, which is 8.49:1. ``Color/brand`` as *text* would be
/// 2.05:1.
struct HomeTonePill: View {

    let label: String
    let tone: HealthTone

    var body: some View {
        Text(label)
            .font(.caption)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(tone == .brand ? Color.brandFg : tone.inkColour)
            .background(
                tone == .brand ? Color.brand : Color(.secondarySystemBackground),
                in: Capsule()
            )
    }
}

extension HealthTone {

    /// ### The three system colours here are a known, measured gap
    ///
    /// Moved out of ``HomeAttentionView`` in 3b, unchanged, because six
    /// sections now draw dots and two mappings of one enum is how they come to
    /// disagree.
    ///
    /// `.red`, `.orange` and `.green` are 3.55:1, **2.20:1** and **2.22:1** on
    /// the light canvas, and a dot that carries meaning needs 3:1 — so two of
    /// the three fail. Apple's audit does not say so, and cannot: the dot is
    /// `.accessibilityHidden(true)`, so it is not an element the audit looks
    /// at. Measured by hand, not reported.
    ///
    /// They are **not** swapped for ``inkColour`` here, although those pass,
    /// because the ink tokens are *text* inks at 5.65-6.08:1 and a dot drawn
    /// in one reads brown rather than amber. That is the open question already
    /// in front of Kamron about the header's live dot — bright fill with a
    /// dark ring, or the duller ink — and this is the same question, so it
    /// waits for the same answer rather than being settled twice differently.
    ///
    /// `.idle` and `.brand` are tokens because neither had that trade-off:
    /// `.secondary` was failing Apple's audit outright as *text* in the
    /// attention card (3.44:1, "Contrast nearly passed"), and `brandInk` was
    /// already chosen for the live dot.
    var dotColour: Color {
        switch self {
        case .err: return .red
        case .warn: return .orange
        case .ok: return .green
        case .idle: return Color.fgMuted
        case .brand: return Color.brandInk
        }
    }

    /// The same tone **as text or as a glyph a person has to read**, where the
    /// system colours are 2.20:1 and 2.22:1 and these are 5.65:1 or better.
    /// `web/static/colors_and_type.css:166` draws the same distinction and
    /// says why: at their lightness amber and green read about 2:1 as text on
    /// white, so there is a second, deeper token for the text role.
    var inkColour: Color {
        switch self {
        case .err: return Color.errInk
        case .warn: return Color.warnInk
        case .ok: return Color.okInk
        case .idle: return Color.fgMuted
        case .brand: return Color.brandInk
        }
    }
}
