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

    private func chip(_ person: NewsPerson, stacked: Bool) -> some View {
        let picked = news.selectedID == person.id
        return Button {
            onPick(person.id)
        } label: {
            HStack(spacing: 8) {
                NewsAvatarBubble(person: person, picked: picked)
                VStack(alignment: .leading, spacing: 2) {
                    Text(person.displayName)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    if stacked {
                        Text(person.heardLine(nowMs: nowMs))
                            .font(.caption)
                            // `fgMuted` is measured against the card fill and
                            // the white canvas, which is what is behind an
                            // unpicked chip. On the picked one the fill is the
                            // brand amber, where the measured pair is
                            // `brandFg` — the same rule ``HomeTonePill``
                            // follows.
                            .foregroundStyle(picked ? Color.brandFg : Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if stacked { Spacer(minLength: 4) }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(minHeight: 44)
            .frame(maxWidth: stacked ? .infinity : nil, alignment: .leading)
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
            .overlay(
                Capsule().stroke(
                    picked ? Color.brandInk : Color.fgMuted.opacity(0.35),
                    lineWidth: 1
                )
            )
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(picked ? Color.brandFg : Color.primary)
        // **iOS-only** (`IOS-ONLY-STRINGS.md`): Android's chip is a
        // `Surface(onClick:)` whose only child is the name, so the name *is*
        // the control's label. Here the bubble and the name are two views and
        // a reader would otherwise hear the initial and then the name.
        .accessibilityLabel(NewsStrings.personChip(person.displayName))
        .accessibilityIdentifier("news-chip-\(person.displayName)")
        .accessibilityAddTraits(picked ? [.isButton, .isSelected] : [.isButton])
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
/// Dynamic Type rule is about.
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
            .accessibilityHidden(true)
    }
}
