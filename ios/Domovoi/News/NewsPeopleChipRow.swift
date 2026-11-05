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
/// ### The chip's label is one `Text`, and there is no longer a bubble
///
/// This is a shape, not a style, and it is what the five `Contrast failed`
/// this screen carried for four trains were: see ``chip(_:stacked:)``. The
/// avatar bubble that used to be drawn over the pill is **gone** — Kamron's
/// answer 39, 2026-11-03.
///
/// **It is a divergence from Android, not a port of it**, and the question
/// Kamron answered said otherwise. `PeopleChipRow` draws
/// `AvatarBubble(p.name, 20)` inside the chip's own `Row`
/// (`NewsScreen.kt:139`), and the expanded rail draws
/// `AvatarBubble(p.name, 32)` (`:185`), so Android has the bubble in both
/// branches. Answer 39's own wording — "which is Android's shape
/// (`NewsScreen.kt`)" — was wrong about that, and the answer is still the
/// spec (BATCH-RULES: his answers win). What follows from the correction is
/// that answer 39 needs an **Android half** like answers 2 and 24 got, and
/// it is logged as one in this unit's DECISIONS.md.
struct NewsPeopleChipRow: View {

    let news: NewsModel
    let nowMs: Int64
    let onPick: (Int64) -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

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
    /// ``HomeAttentionView``'s rows were `Button` + `children: .ignore` +
    /// `accessibilityLabel` and came out the same three-deep shape in the very
    /// same run (`R002/Alerts/.../alerts-no-late-ring-row-tree…txt`). Those
    /// rows were relabelled on the `Button` itself in `94283d81` and train
    /// I005 drew each as one; the last `.ignore`-on-a-`Button` in the app,
    /// Chat's draft version chips, went the same way in the mx-home unit.
    ///
    /// What **does** produce one childless element is a button whose label is
    /// a single `Text`, which is every clean pill in this app: the nine
    /// `news-category-…` chips (``NewsCategoryChip``),
    /// ``AssistantPlacePickerView``'s places, Home's `not live · updates every
    /// 30s`, and Alerts' `not now` with an `accessibilityLabel` of its own.
    /// Round 3 took the bubble out of the label and drew it **over** the pill
    /// as a hidden decoration, which cleared the five findings.
    ///
    /// ### And then the bubble went altogether (answer 39)
    ///
    /// Kamron's answer 39, 2026-11-03: `drop the avatar bubble out of the
    /// News chip`. It was the row's only colour, it carried no information
    /// that the name beside it did not — the initial *is* the first letter of
    /// the name — and with the five-hue avatar declined (answer 37) there was
    /// nothing left for it to say.
    ///
    /// **Android keeps its bubble**, so this is an unlabelled divergence no
    /// longer: `NewsScreen.kt:139` is `AvatarBubble(p.name, 20)` inside the
    /// chip's `Row`, and `:185` is the rail's `AvatarBubble(p.name, 32)`.
    /// What is true is the other half — a chip row of plain pills is every
    /// other pill in **this** app, and it is the shape that audits clean.
    /// The Android half of answer 39 is logged in DECISIONS.md.
    ///
    /// It also removes the last thing between this row and an audit it cannot
    /// explain: a filled circle inside a capsule was two overlapping fills
    /// for the auditor to sample, and the whole four-train story above was
    /// about what that sampling did. Nothing is classified in its place —
    /// ``newsChipRowIsOneElement`` still pins the one-element shape, and a
    /// `Contrast failed` anywhere in this row fails
    /// ``testNews_PassesApplesAccessibilityAudit``.
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
                // Even on both sides now that nothing is drawn into the
                // leading one (answer 39). It was `12 + bubbleBox + 8`, a
                // `ScaledMetric` inset so the bubble could never land on the
                // first letter of the name at AX5.
                .padding(.horizontal, 12)
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

// ---------------------------------------------------------------------------
// `NewsAvatarBubble` used to live here — `AvatarBubble`
// (`ui/components/Kit.kt:140-158`), a circle with the person's initial in it,
// drawn as one ``Color/brand`` bubble rather than Android's five hue
// gradients (whose white initial measures about 2:1 on every one of them).
//
// **It is gone, and so is its only caller** (Kamron's answers 37 and 39,
// 2026-11-03): the gradients are declined and the bubble is dropped out of
// the chip. It is deleted rather than kept for later because a view with no
// caller reads as shipped behaviour to the next person through this file —
// the defect class this track has already paid for — and because what it
// would have to come back as is a decision nobody has made: the brand bubble
// it was, a measured dark ink on the five hues, or something else. The
// arithmetic that rules out white-on-gradient is in this comment and in
// DECISIONS.md, which is where a later round needs it; the twenty lines of
// view were not carrying any of it.
//
// ``NewsPerson/initial`` stays: it is the server's field, ported, and
// ``NewsPersonView``'s heading is where a future avatar would read it.
// ---------------------------------------------------------------------------
