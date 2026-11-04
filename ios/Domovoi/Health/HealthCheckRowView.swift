import DomovoiKit
import SwiftUI

/// One check — `CheckRow` (`ContainersScreen.kt:221`).
///
/// Its dot, what it checks, what it found and when, the rooms or plugins it
/// is about, and the link that goes where the problem is fixed.
///
/// **Every word is ``HealthStrings``' or ``HealthPanelStrings``'** and every
/// decision is ``HealthCheck``'s: whether it is a problem, whether it has a
/// fix, what the fix is called. This file is layout, which `swift test`
/// cannot see.
struct HealthCheckRowView: View {

    let check: HealthCheck

    /// ``HealthPanelModel/nowMs`` — **not** `Date()`, so the relative times on
    /// this screen all count against one clock and a test can drive it.
    let nowMs: Int64

    /// `open(fix)` (`:116-125`): a screen, a Settings section, or the
    /// dashboard in a browser. Only ever called for a check that has one.
    let onFix: (HomeTarget) -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    /// `c.text + (c.at?.let { " · ${relTime(it)}" } ?: "")` (`:231`).
    ///
    /// One `Text` and not two, because the two halves are one phrase —
    /// `study is offline · it can't hear anyone right now · 2h ago` — and
    /// VoiceOver reading them as two stops relates the time to nothing.
    private var found: String {
        guard let at = check.at else { return check.text }
        return check.text + HealthPanelStrings.atSeparator + relTime(at, nowMs: nowMs)
    }

    /// `c.problem && c.items.size > 1` (`:235`). **Both halves matter**: a
    /// green check never names its rooms, and one room is already named in
    /// ``found`` — `study is offline` would otherwise be followed by `study`.
    private var items: String? {
        guard check.problem, check.items.count > 1 else { return nil }
        return check.items.joined(separator: ", ")
    }

    /// `"open ${c.fixLabel ?: "it"}"` (`:241`), and nil when there is nothing
    /// to open: a green check, or a problem the dashboard alone can fix that
    /// carries no target.
    private var fix: (label: String, target: HomeTarget)? {
        guard check.problem, let target = check.fix else { return nil }
        return (
            HealthPanelStrings.openFixPrefix
                + (check.fixLabel ?? HealthPanelStrings.openFixFallback),
            target
        )
    }

    var body: some View {
        rowBody
            // `heightIn(min = 48.dp).padding(horizontal = 16.dp, vertical = 10.dp)`
            // (`:223`). 48 is Android's and is over Apple's 44, so the link
            // inside it is never the thing that sets the row's height.
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
    }

    /// At an accessibility size the link moves **under** the text instead of
    /// beside it — ``HomeAttentionView``'s shape, and for its reason: at AX5
    /// `open rooms` and `study is offline · it can't hear anyone right now`
    /// cannot share a row, and shrinking text somebody turned up on purpose
    /// is not the answer.
    @ViewBuilder private var rowBody: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    ToneDot(tone: check.tone)
                    column
                }
                if let fix {
                    HomeLink(fix.label) { onFix(fix.target) }
                }
            }
        } else {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                ToneDot(tone: check.tone)
                column
                Spacer(minLength: 8)
                if let fix {
                    HomeLink(fix.label) { onFix(fix.target) }
                }
            }
        }
    }

    private var column: some View {
        VStack(alignment: .leading, spacing: 2) {
            // `labelMedium` in `fgMuted` (`:229`) — the thing being checked,
            // quieter than what was found.
            Text(check.label)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Text(found)
                .font(.subheadline)
                // The longest sentences in the app are here — `the Domovoi
                // server isn't answering · rooms show the last known state` —
                // so this is the `fixedSize` that matters most on the screen.
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            if let items {
                Text(items)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
        }
        // One element, one phrase: the label, what was found and the rooms it
        // names read together. The link is a control of its own and stays
        // outside this, so it is still a stop VoiceOver can act on.
        .accessibilityElement(children: .combine)
    }
}
