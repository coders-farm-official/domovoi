import DomovoiKit
import SwiftUI

/// Home's header: the house's name, the date and counts, the live dot, and
/// the sentence the dot reveals.
///
/// A port of `HomeHeader` (`ui/screens/home/HomeSections.kt:157`). **Every
/// decision is ``HomeHeaderState``'s** — which sentence, which label, whether
/// the pair link is there — and every word is ``HomeHeaderStrings``'s, checked
/// against the Kotlin by `ShellStringsTests`. What is here is layout, which
/// `ios/scripts/pkgtest.sh` cannot see: it tests the packages, and the app
/// target has never been through `xcodebuild`.
///
/// ### Why it reflows, and what still has to be confirmed on a screen
///
/// Apple's `performAccessibilityAudit` failed the first screen of this app
/// with **"Text of this element may be clipped at larger Dynamic Type
/// sizes"** with 643 package tests green and a clean build
/// (`design-notes/ios/I1C-SIMULATOR-PASS.md`). So this follows
/// ``ShellTopBar``'s shape: below the accessibility sizes it is a row with
/// the dot at the end, and at `.isAccessibilitySize` the dot moves under the
/// text and every label wraps to as many lines as it needs. `minimumScaleFactor`
/// is never the answer — shrinking text is the opposite of what somebody who
/// turned it up asked for.
///
/// `I1D1-REPORT.md` lists what a person has to look at. The two this file
/// cannot answer for itself: that the name wraps rather than clipping at
/// AX5, and that VoiceOver reads the date-and-counts line as one phrase
/// rather than three.
struct HomeHeaderView: View {

    let state: HomeHeaderState
    let onPair: () -> Void

    /// `rememberSaveable` on Android. A `@State` here, which is the same
    /// lifetime for this screen: Home's state dies when the route leaves,
    /// on both phones.
    @State private var explaining = false

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if typeSize.isAccessibilitySize {
                words
                dot
            } else {
                HStack(alignment: .center, spacing: 8) {
                    words
                    Spacer(minLength: 8)
                    dot
                }
            }
            if state.showsPairLink {
                Button(HomeHeaderStrings.pairLink, action: onPair)
                    .font(.footnote)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            if explaining {
                Text(state.explanation)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var words: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(state.name)
                .font(.largeTitle)
                // Android: `maxLines = 2, overflow = Ellipsis`. Kept below
                // the accessibility sizes and dropped at them, because a
                // two-line cap on a name at AX5 is the clipping the audit
                // failed the first screen for.
                .lineLimit(typeSize.isAccessibilitySize ? nil : 2)
                .fixedSize(horizontal: false, vertical: true)
            line
        }
    }

    /// The date in monospace, then each count behind a faint separator — the
    /// `AnnotatedString` of `HomeSections.kt:186-192`, as one `Text` so
    /// VoiceOver reads one phrase.
    /// The branch is ``HomeHeaderState/showsCountsSkeleton``'s, not this
    /// view's, so that `swift test` can see the decision — the skeleton
    /// itself is layout and it cannot.
    @ViewBuilder private var line: some View {
        if !state.showsCountsSkeleton {
            state.counts
                .reduce(Text(state.date).monospaced()) { so_far, part in
                    so_far
                        + Text(HomeHeaderStrings.countsSeparator).foregroundStyle(Color.fgMuted)
                        + Text(part)
                }
                .font(.body)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityLabel(state.spokenLine)
        } else {
            // A skeleton beside the date, not a spinner (`HomeSections.kt:205`):
            // 140 × 12 dp there, and the same here. Hidden from VoiceOver,
            // which has nothing to say about a grey box.
            HStack(spacing: 8) {
                Text(state.date)
                    .font(.body)
                    .monospaced()
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                RoundedRectangle(cornerRadius: 4)
                    .fill(.quaternary)
                    .frame(width: 140, height: 12)
                    .accessibilityHidden(true)
            }
        }
    }

    /// On a phone: the dot alone, in a 44-point target, with no text — so the
    /// accessibility label is the only thing that says what it means. On a
    /// wider pane: a bordered pill with the words beside it.
    private var dot: some View {
        Button {
            explaining.toggle()
        } label: {
            if typeSize.isAccessibilitySize {
                HStack(spacing: 8) {
                    circle
                    Text(state.liveLabel)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                circle.frame(width: 44, height: 44)
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(state.liveLabel)
        .accessibilityHint(state.explanation)
    }

    private var circle: some View {
        Circle()
            .fill(state.live ? Color.accentColor : Color.fgMuted)
            .frame(width: 10, height: 10)
    }
}

#Preview("live and paired") {
    HomeHeaderView(
        state: HomeHeaderState(
            name: "domovoi",
            date: "mon 28 sep",
            counts: ["3 online", "1 timer"],
            countsReady: true,
            live: true,
            paired: true
        ),
        onPair: {}
    )
    .padding(16)
}

#Preview("not paired, nothing answered") {
    HomeHeaderView(
        state: HomeHeaderState(name: "domovoi", date: "mon 28 sep", live: false, paired: false),
        onPair: {}
    )
    .padding(16)
}
