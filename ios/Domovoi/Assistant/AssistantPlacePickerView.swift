import DomovoiKit
import SwiftUI

/// Where a chat runs: the `runs` label and a chip for each place a chat can
/// start in (`PlacePicker`, `ChatHome.kt:735-772`).
///
/// **One choice draws the chip alone** (`ChatHome.kt:741`), with nothing to
/// tap: a picker with one option is a control that cannot do what it says.
/// That is the branch this file gets wrong most easily, so it has a case of
/// its own (`testAssistant_PlacePickerOffersBothPlaces`).
///
/// The chips keep Android's order — the house first (`listedPlaces`,
/// `ChatPlace.kt:172`) — and the phone's is drawn in the brand ink where the
/// house's is neutral (`PlaceChip`, `ChatHome.kt:691`). The **ink** token and
/// not ``Color/brand``: the brand is 2.05:1 on white and a chip's words are
/// text somebody has to read.
///
/// ### The selected chip's fill is 0.10, and that is a contrast number
///
/// Apple's audit reported "Contrast nearly passed" against the selected
/// `on this phone` chip on train S003, and it was right. ``Color/brandInk``
/// is documented at **5.65:1 on white** — but the chip is not on white, it is
/// on its own fill, and the fill was ``Color/fgMuted`` at 0.18. `fgMuted` is
/// 7.44:1 on the light canvas, so at 0.18 over white the chip's background
/// sits at a relative luminance of about 0.749 and the ink's ratio **against
/// what it is actually drawn on** falls to roughly **4.30:1** — under the
/// 4.5:1 that `.caption` needs, which is exactly what "nearly passed … unless
/// font size is larger" means.
///
/// This is the same mistake the shared token's own doc comment warns about
/// (`fgMuted` measured against the white canvas, failing on
/// `secondarySystemBackground`), made one layer further in: a measured ratio
/// read as if the surface under it were still white.
///
/// The fix is the **fill**, not the ink and not the font. At 0.10 the
/// background is about 0.855 and brandInk measures **4.87:1**, which passes;
/// the house chip's `fgMuted` on the same fill is 6.07:1. Nothing is lost,
/// because the fill was never what said which chip is chosen: the stroke is
/// (0.45 against 0.18), and VoiceOver is told by ``traits(picked:)``.
struct AssistantPlacePickerView: View {

    let choices: [ChatPlace]
    let selected: ChatPlace
    let onPick: (ChatPlace) -> Void

    /// Drawn **inside a conversation's header row** rather than as the band
    /// above a list (D63, ``AssistantConversationPlaceChip``).
    ///
    /// The chip is the same chip, with the same fill and the same measured
    /// ratio; what goes is the band's own padding and its greed for the
    /// width, because a header row already has both. It is only ever true of
    /// the one-choice branch: a conversation is already in a place, so a
    /// picker there would offer to move a chat that cannot be moved.
    ///
    /// Last, and defaulted, so the three call sites that draw the band pass
    /// what they always passed.
    var inHeader = false

    var body: some View {
        if choices.count <= 1 {
            // The place, said once. A chip and not a button, so VoiceOver does
            // not offer an action that does nothing.
            if inHeader {
                chip(selected, picked: true)
                    .accessibilityElement(children: .combine)
            } else {
                chip(selected, picked: true)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityElement(children: .combine)
            }
        } else {
            layout {
                Text(AssistantStrings.runsLabel)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(choices, id: \.key) { place in
                    Button {
                        onPick(place)
                    } label: {
                        chip(place, picked: place == selected)
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(traits(picked: place == selected))
                }
                if !wrapped { Spacer(minLength: 0) }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
    }

    /// A row of chips, or a column of them at an accessibility text size.
    ///
    /// Two chips whose words are "on this phone" and "on home server · shared
    /// with the household" do not fit beside a label on one line at AX5, and a
    /// row that cannot fit squeezes its text instead of wrapping it.
    /// `minimumScaleFactor` is rejected everywhere in this app, so the row
    /// becomes a column — the swap every screen here makes on
    /// `dynamicTypeSize.isAccessibilitySize`.
    @ViewBuilder private func layout<Content: View>(
        @ViewBuilder _ content: () -> Content
    ) -> some View {
        if wrapped {
            VStack(alignment: .leading, spacing: 8) { content() }
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(spacing: 8) { content() }
        }
    }

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private var wrapped: Bool { dynamicTypeSize.isAccessibilitySize }

    /// Spelled out with its type rather than as a ternary of two array
    /// literals, which is a shape Swift's inference reads badly in a modifier
    /// argument.
    private func traits(picked: Bool) -> AccessibilityTraits {
        var out: AccessibilityTraits = .isButton
        if picked { out.insert(.isSelected) }
        return out
    }

    private func chip(_ place: ChatPlace, picked: Bool) -> some View {
        // `serverShareSeen: false` on every call, and ``AssistantPlaceSwitch``
        // says why: this app cannot know whether a house chat has been used
        // from this phone, so the house's chip keeps its longer, truer words.
        Text(assistantPlaceChip(place, serverShareSeen: false))
            .font(.caption)
            .foregroundStyle(place == .phone ? Color.brandInk : Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .frame(minHeight: 44)
            .background(
                // 0.10, not 0.18: the file's doc comment has the measurement.
                // A darker fill under `brandInk` is a contrast defect, and the
                // stroke below is what actually says which chip is chosen.
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color.fgMuted.opacity(picked ? 0.10 : 0.04))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(Color.fgMuted.opacity(picked ? 0.45 : 0.18), lineWidth: 1)
            )
            .contentShape(.rect)
    }
}
