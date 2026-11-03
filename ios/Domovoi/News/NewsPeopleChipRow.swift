import DomovoiKit
import SwiftUI

/// Whose news: a pill per person, the picked one filled.
///
/// `PeopleChipRow` (`NewsScreen.kt:117-150`) — a `LazyRow` of pill buttons,
/// each an avatar bubble and a name. **The compact branch only**: Android's
/// other branch is a 240 dp rail no iPhone width can reach (``NewsView``).
///
/// ### It becomes a column at an accessibility size
///
/// A horizontal scroller of pills whose text is 48 points tall is a row of
/// clipped pills that have to be swiped past one at a time. At
/// `dynamicTypeSize.isAccessibilitySize` these are full-width rows in a
/// `VStack` instead — which is also where the one fact Android's rail had and
/// its chip row did not is drawn: `heard 5m ago`, under the name
/// (`NewsScreen.kt:185`). It is the rail's own sentence, kept rather than
/// dropped with the layout.
///
/// ### The chip's label is one `Text`, and the bubble is drawn over it
///
/// This is a shape, not a style, and it is what the five `Contrast failed`
/// this screen carried for four trains were: see ``chip(_:stacked:)``.
struct NewsPeopleChipRow: View {

    let news: NewsModel
    let nowMs: Int64
    let onPick: (Int64) -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    /// The avatar bubble's box, the same ``ScaledMetric`` ``NewsAvatarBubble``
    /// declares for itself.
    ///
    /// Declared twice because the bubble is drawn **over** the pill rather
    /// than inside the button's label (``chip(_:stacked:)``), so the words
    /// have to be inset by it here. A fixed inset under a scaled glyph is the
    /// bubble sitting on the first letter of the name at AX5, which is the
    /// same class of bug as a fixed `frame(width:)` on the glyph itself.
    @ScaledMetric(relativeTo: .body) private var bubbleBox: CGFloat = 28

    var body: some View {
        if news.roster.isEmpty {
            // Nothing: the pane under this says what an empty roster means
            // (`NewsScreen.kt:82-85`). An empty scroller would be a gap with
            // no explanation in it.
            EmptyView()
        } else if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(news.roster) { person in
                    chip(person, stacked: true)
                }
            }
        } else {
            ScrollView(.horizontal) {
                HStack(spacing: 8) {
                    ForEach(news.roster) { person in
                        chip(person, stacked: false)
                    }
                }
                // Room for the ring the selected chip draws, which a zero
                // inset clips on the first and last pill.
                .padding(.vertical, 2)
            }
            .scrollIndicators(.hidden)
        }
    }

    /// The pill's leading pad, the bubble, and the gap after it — what the
    /// words are inset by, now that the bubble is not a view beside them.
    private var wordsInset: CGFloat { 12 + bubbleBox + 8 }

    /// One person's pill.
    ///
    /// ### The label is a single `Text`, and that is the whole fix
    ///
    /// For four trains this screen reported five `Contrast failed` that no
    /// colour in it explains — three chip names, and the last chip's initial
    /// and name. Train S005 is the run that named them, and the frames are
    /// what give it away:
    ///
    ///     Contrast failed | element: label='Kamron' frame=(16, 217.3, 113, 44)
    ///     Contrast failed | element: label='Chevy'  frame=(137, 217.3, 103.3, 44)
    ///
    /// A `StaticText` 113 x 44 is not a word; it is the **pill**. The chip's
    /// label used to be an `HStack` of ``NewsAvatarBubble`` and one `Text`, so
    /// SwiftUI merged the label into a single element and gave it the
    /// button's own frame — and Apple's auditor, sampling that rectangle, read
    /// mostly capsule fill and avatar bubble (242,166,24 against 206,140,20 in
    /// a picked chip, **1.30:1**; 242,242,247 against 242,166,24 in an
    /// unpicked one, **1.84:1**) instead of the glyphs, which are `brandFg` on
    /// `brand` at 8.49:1 and `Color.primary` on `secondarySystemBackground` at
    /// 19.1:1.
    ///
    /// **`accessibilityElement(children: .ignore)` is not the tool for it**,
    /// and round 1's fix used it. On a `Button` it adds a wrapper and leaves
    /// the control and its leaves where they were — R001 and R002 both show
    /// the chip three deep with the leaves still at the bottom:
    ///
    ///     Button, {{16, 217.3}, {113, 44}}, id: 'news-chip-Kamron', label: 'Kamron's news'
    ///       Button, {{16, 217.3}, {113, 44}}, label: 'Kamron'
    ///         StaticText, {{28, 225.3}, {28, 28}}, label: 'K'
    ///         StaticText, {{16, 217.3}, {113, 44}}, label: 'Kamron'
    ///
    /// It is app-wide, not News's and not the integration's:
    /// ``HomeAttentionView``'s rows are `Button` + `children: .ignore` +
    /// `accessibilityLabel` and come out the same three-deep shape in the very
    /// same run (`R002/Alerts/.../alerts-no-late-ring-row-tree…txt`).
    ///
    /// What **does** produce one childless element is a button whose label is
    /// a single `Text`, which is every clean pill in this app: the nine
    /// `news-category-…` chips (``NewsCategoryChip``),
    /// ``AssistantPlacePickerView``'s places, Home's `not live · updates every
    /// 30s`, and Alerts' `not now` with an `accessibilityLabel` of its own.
    /// So the bubble leaves the label and is drawn **over** the pill, where it
    /// is a decoration: not hit-testable, and hidden from VoiceOver by
    /// ``NewsAvatarBubble`` itself, which works out here the way it never did
    /// inside a button's label.
    ///
    /// The words stay one `Text` in both layouts — at an accessibility size
    /// the `heard …` line is the second half of the same concatenation, the
    /// way ``HomeHeaderView``'s date and counts are one phrase.
    private func chip(_ person: NewsPerson, stacked: Bool) -> some View {
        let picked = news.selectedID == person.id
        return Button {
            onPick(person.id)
        } label: {
            words(person, stacked: stacked, picked: picked)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
                // Not `.padding(.horizontal, 12)`: the leading side carries
                // the bubble the overlay draws into it.
                .padding(.leading, wordsInset)
                .padding(.trailing, 12)
                .padding(.vertical, 8)
                .frame(minHeight: 44)
                // Explicitly `CGFloat.infinity`, because the `nil` on the other
                // side of the ternary makes this an optional and `.infinity`
                // alone has nothing to infer from.
                .frame(maxWidth: stacked ? CGFloat.infinity : nil, alignment: .leading)
                .background(
                    // **Brand fill with `brandFg` text when picked**, which is
                    // the app's one measured prominent pair at 8.49:1. Android
                    // uses a `brandSoft` wash with a brand border
                    // (`NewsScreen.kt:131-136`) and this app has no `brandSoft`
                    // token; the border alone would make the picked chip's state
                    // a 2.05:1 graphic. DECISIONS.md.
                    picked ? Color.brand : Color(.secondarySystemBackground),
                    in: Capsule()
                )
                // The same ring, measured the same way, as
                // ``NewsCategoryChip``'s — 1.71:1 unpicked and 2.76:1 picked,
                // both under the 3:1 a graphic that carries meaning needs, on a
                // fill that is 1.01:1 against the card. `fgMuted@0.7` is 3.34:1
                // light and 4.05:1 dark; `brandFg` on `brand` is 8.49:1 and is
                // already this chip's text colour when it is picked. That note
                // carries the arithmetic.
                .overlay(
                    Capsule().stroke(
                        picked ? Color.brandFg : Color.fgMuted.opacity(0.7),
                        lineWidth: 1
                    )
                )
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(picked ? Color.brandFg : Color.primary)
        // **iOS-only** (`IOS-ONLY-STRINGS.md`): Android's chip is a
        // `Surface(onClick:)` whose only child is the name, so the name *is*
        // the control's label. A control labelled `Kamron` says nothing about
        // what tapping it does, and the stacked layout draws a second line a
        // reader would otherwise not hear — the same shape as Alerts'
        // `not now, Timer alerts are off on this phone`.
        .accessibilityLabel(spoken(person, stacked: stacked))
        .accessibilityIdentifier("news-chip-\(person.displayName)")
        .accessibilityAddTraits(picked ? [.isButton, .isSelected] : [.isButton])
        // **The bubble, outside the button's label.** Last in the chain so
        // the accessibility modifiers above land on the Button itself, and
        // `allowsHitTesting(false)` because a filled `Circle` over the left
        // third of a pill is otherwise a hole in the tap target.
        .overlay(alignment: .leading) {
            NewsAvatarBubble(person: person, picked: picked)
                .padding(.leading, 12)
                .allowsHitTesting(false)
        }
    }

    /// The chip's words: the name, and under it at an accessibility size the
    /// rail's `heard …` line — **one** `Text`, which is what keeps the chip
    /// one accessibility element (``chip(_:stacked:)``).
    private func words(_ person: NewsPerson, stacked: Bool, picked: Bool) -> Text {
        // Both halves are annotated `Text`, and the concatenation is its own
        // statement: `a + b.font(…)` is a precedence question nobody should
        // have to answer while reading a chip.
        let name: Text = Text(person.displayName).font(.subheadline)
        guard stacked else { return name }
        let heard: Text = Text(person.heardLine(nowMs: nowMs))
            .font(.caption)
            // `fgMuted` is measured against the card fill and the white
            // canvas, which is what is behind an unpicked chip. On the picked
            // one the fill is the brand amber, where the measured pair is
            // `brandFg` — the same rule ``HomeTonePill`` follows.
            .foregroundStyle(picked ? Color.brandFg : Color.fgMuted)
        return name + Text("\n") + heard
    }

    /// What VoiceOver hears: `Kamron's news`, and the `heard …` line with it
    /// wherever the chip draws one. A reader hearing less than the chip shows
    /// is the no-misleading rule the other way round.
    private func spoken(_ person: NewsPerson, stacked: Bool) -> String {
        let chip = NewsStrings.personChip(person.displayName)
        guard stacked else { return chip }
        return "\(chip), \(person.heardLine(nowMs: nowMs))"
    }
}

/// `AvatarBubble` (`ui/components/Kit.kt:140-158`): a circle with the
/// person's initial in it.
///
/// **One brand bubble, not Android's five hue gradients.** The gradients draw
/// a white initial, which measures about 2:1 on every one of them; the app's
/// own measured pair is ``Color/brand`` behind ``Color/brandFg`` at 8.49:1.
/// The five hues carry no information — they are `name.hashCode() % 5` — so
/// nothing is lost but the colour. DECISIONS.md.
///
/// The box is a ``ScaledMetric`` and never a fixed `frame(width:)`: a glyph
/// box that does not grow with the type size is the clipped-initial bug the
/// Dynamic Type rule is about. ``NewsPeopleChipRow/bubbleBox`` is the same
/// metric, declared again because the chip insets its words by it.
struct NewsAvatarBubble: View {

    let person: NewsPerson
    let picked: Bool

    @ScaledMetric(relativeTo: .body) private var box: CGFloat = 28

    var body: some View {
        Circle()
            .fill(picked ? Color.brandFg.opacity(0.18) : Color.brand)
            .frame(width: box, height: box)
            .overlay(
                Text(person.initial)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.brandFg)
            )
            // The name is beside it in words; an initial read out twice is
            // noise, which is what ``NewsStrings/personChip(_:)`` is for.
            //
            // **Inside a button's label this did not take.** Trains S005,
            // S008, S011, R001 and R002 all have the initial in the tree as a
            // leaf of the chip `Button` — a hidden view inside a `Button`'s
            // label is walked anyway, and it was the *second* element in that
            // label, which is what made SwiftUI hand the name the pill's
            // frame (``NewsPeopleChipRow/chip(_:stacked:)``). The chip now
            // draws the bubble as an overlay **outside** its label, where
            // this line is an ordinary hidden decoration.
            .accessibilityHidden(true)
    }
}
