import DomovoiKit
import SwiftUI

/// Home's **needs attention** card — a port of `AttentionCard`
/// (`ui/screens/home/HomeSections.kt:263-345`).
///
/// **Every decision is ``HomeAttentionState``'s**: which branch, which rows,
/// how many of them, whether the `checking…` line and the stale mark are
/// there, and what VoiceOver reads for a row. Every word is
/// ``HomeAttentionStrings``' or ``HealthStrings``', checked against the Kotlin
/// by `ShellStringsTests`. What is in this file is layout, which
/// `ios/scripts/pkgtest.sh` cannot see — it tests the packages, and the app
/// target has never been through `xcodebuild` in this session.
///
/// ### The card, and why there is no shared `HomeCard` yet
///
/// Android has one `HomeCard(title, action)` that all nine sections draw
/// inside. This batch builds **one** section, so the card's chrome is here
/// rather than in a shared view that has a single caller: a shared component
/// with one use is a shape guessed from one example. Part 3 brings timers,
/// rooms, announce, today and the everything grid, and that is the batch that
/// knows what the shared card has to hold.
///
/// ### What a person has to confirm on a screen
///
/// Listed in `design-notes/ios/I1D2-REPORT.md`, because this file cannot
/// answer for itself:
///
///  * that a row's text wraps rather than clipping at AX5 — Apple's
///    `performAccessibilityAudit` failed the first screen of this app with
///    "Text of this element may be clipped at larger Dynamic Type sizes"
///    with 643 package tests green (`I1C-SIMULATOR-PASS.md`), so the
///    accessibility-size branch below is unverified until someone looks;
///  * that VoiceOver reads a row as **one** phrase — the text and its time
///    together — rather than as two stops with no relation between them,
///    which is what three `Text`s in an `HStack` give;
///  * that the tone colours are distinguishable with Increase Contrast on
///    and in Dark Mode, and that none of them is the only carrier of meaning
///    (the words say the problem; the colour only grades it).
struct HomeAttentionView: View {

    let state: HomeAttentionState

    /// ``HomeModel/nowMs`` — **not** `Date()`. A view that reads the wall
    /// clock itself cannot be driven to a boundary by a test, and the row
    /// times would drift from the rest of the screen's.
    let nowMs: Int64

    /// A row was tapped: ``AttentionRow/target`` is where it goes, which for
    /// every Home row today is Settings → Health (``healthTarget``).
    let onRow: (AttentionRow) -> Void

    /// `+N more` — Android's `action` on the card, which expands the list in
    /// place rather than navigating (`HomeSections.kt:304`).
    let onMore: () -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if state.shown {
            VStack(alignment: .leading, spacing: 12) {
                header
                content
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            // The same fill the rest of the app's cards use
            // (`ConnectionDialogView.swift:330`,
            // `PhonesAndBrowsersView.swift:303`), so Home does not introduce
            // a second card look.
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
    }

    // MARK: - the card's own chrome

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(HomeAttentionStrings.title)
                .font(.headline)
                // A card title is a heading, so VoiceOver's heading rotor can
                // reach it and the rows under it are read as belonging to it.
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 8)
            if let more = state.moreLabel {
                Button(more, action: onMore)
                    .font(.subheadline)
                    // Android's row minimum applies to this too
                    // (`HomeSections.kt:309`), and it is the smallest control
                    // on the card.
                    .frame(minHeight: state.rowMinHeight)
                    .contentShape(.rect)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        if let count = state.summaryCount {
            summary(count)
        } else {
            rows
        }
    }

    // MARK: - the summary branch (`HomeSections.kt:273-289`)

    /// A line and a count, for a house whose `home_problems_visibility` is
    /// `summary` — or for **any** house on a shared screen, which is the
    /// privacy rule: "notes, memories, people's names, chat titles, Wi-Fi
    /// names" must not be on a wall (`HomeScreen.kt:84`), and a problem row
    /// names rooms.
    private func summary(_ count: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            // ``HomeAttentionState/summaryTone`` — **grey**, as
            // `HomeSections.kt:282`'s `StatusDot(Tone.Idle)` is, and not a
            // literal in this file where `swift test` cannot see it. It drew
            // amber, which grades a problem for the one viewer the house has
            // decided not to tell what the problem is.
            toneDot(state.summaryTone)
            Text(HomeAttentionStrings.summaryLine)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            Text(count)
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
        }
        // One element, one phrase: the line and the count read together, and
        // the count is not a separate stop whose "4" relates to nothing.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(HomeAttentionStrings.summaryLine), \(count)")
    }

    // MARK: - the rows branch (`HomeSections.kt:304-341`)

    private var rows: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(state.shownRows) { row in
                Button { onRow(row) } label: { rowBody(row) }
                    .buttonStyle(.plain)
                    // `heightIn(min = if (compact) 44.dp else 40.dp)`
                    // (`HomeSections.kt:309`), which is also Apple's 44 pt
                    // minimum. The label is a subheadline and a footnote —
                    // about 20 pt at default type — on a control whose whole
                    // job is to be tapped. The figure is
                    // ``HomeAttentionState/rowMinHeight``'s, so a test holds
                    // it; `contentShape` is what makes the whole of the frame
                    // the tap target rather than just the text in it.
                    .frame(minHeight: state.rowMinHeight, alignment: .leading)
                    .contentShape(.rect)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(state.spokenRow(row, nowMs: nowMs))
                    .accessibilityAddTraits(.isButton)
            }
            // Both of these live **inside** the rows branch on Android
            // (`:337-339`), which is why ``HomeAttentionState`` answers false
            // for them in the summary branch rather than this view deciding.
            if state.showsChecking {
                Text(HomeAttentionStrings.checking)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if state.showsStaleMark {
                Text(HomeAttentionStrings.stale)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// One row: the tone, the words, and when the check last had an answer.
    ///
    /// At accessibility sizes the time moves **under** the text instead of
    /// beside it — the shape ``HomeHeaderView`` already uses, and for the
    /// same reason: `minimumScaleFactor` shrinks text that somebody turned
    /// up on purpose.
    @ViewBuilder
    private func rowBody(_ row: AttentionRow) -> some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    toneDot(row.tone)
                    rowText(row)
                }
                if let at = row.at {
                    rowTime(at)
                }
            }
        } else {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                toneDot(row.tone)
                rowText(row)
                Spacer(minLength: 8)
                if let at = row.at {
                    rowTime(at)
                }
            }
        }
    }

    private func rowText(_ row: AttentionRow) -> some View {
        Text(row.text)
            .font(.subheadline)
            // **No `lineLimit`, and that is a declared divergence from
            // Android** — `HomeSections.kt:311-316` caps a compact row at one
            // line with an ellipsis, "so three of them and the timers leave
            // the rooms above the fold". It is in `I1D2-FIX-REPORT.md`'s
            // divergence list and on the Simulator checklist, which is where a
            // divergence belongs; this comment is the reason, not the record.
            //
            // The reason: a row says which room is offline and the room is
            // named by the person who set it up, so there is no length this
            // can assume — and Apple's `performAccessibilityAudit` failed the
            // first screen of this app for text that "may be clipped at larger
            // Dynamic Type sizes". The cost is a taller card, which the owner
            // can see on the pass and the cap can be added in one line.
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
    }

    private func rowTime(_ at: String) -> some View {
        Text(relTime(at, nowMs: nowMs))
            .font(.footnote)
            .foregroundStyle(.secondary)
            .monospacedDigit()
    }

    /// The tone, as a dot.
    ///
    /// **The colour is never the only carrier**: the row's words say what is
    /// wrong, and a person who cannot tell the red dot from the amber one
    /// still reads "kitchen is offline · it can't hear anyone right now".
    /// The dot is also hidden from VoiceOver, because the spoken row is the
    /// words and a stop that reads "red circle" is noise.
    private func toneDot(_ tone: HealthTone) -> some View {
        Circle()
            .fill(colour(of: tone))
            .frame(width: 8, height: 8)
            // Lifted to the cap height of the line beside it, which
            // `firstTextBaseline` alignment on a `Circle` cannot do by
            // itself: a shape has no baseline.
            .offset(y: -2)
            .accessibilityHidden(true)
    }

    private func colour(of tone: HealthTone) -> Color {
        switch tone {
        case .err: return .red
        case .warn: return .orange
        case .ok: return .green
        case .idle: return .secondary
        case .brand: return .accentColor
        }
    }
}
