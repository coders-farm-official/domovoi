import DomovoiKit
import SwiftUI

/// The one row Home's timers card draws about this phone's alerts — a port of
/// `AlertHintRow` (`android/.../ui/screens/home/HomeSections.kt:351-407`, read
/// at `898b3af2`).
///
/// **Every decision is the package's**: whether there is a row at all
/// (``AlertsHintModel/shown``), what it says
/// (``AlertsPermissionAsk/line(dismissed:)``), what the fix link is called
/// (``AlertsPermissionAsk/fixLabel(dismissed:)``), which control it is
/// (``AlertsPermissionAsk/showsExplainCard``) and what `not now` writes
/// (``AlertsHintModel/notNow()``). What is in this file is layout, which
/// `ios/scripts/pkgtest.sh` cannot see — it builds the package, and the app
/// target has not been through `xcodebuild` in this session.
///
/// ### One state, not two
///
/// Android's row has a second case, `MAY_RING_LATE` — `Timers may ring a few
/// minutes late on this phone`, with an `allow alarms` link to
/// `Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM` (`:404`). There is no
/// exact-alarm grant on an iPhone: a scheduled notification is delivered on
/// time, and that sentence would describe nothing while its link opened
/// nothing. ``AlertsHint`` has the two cases this platform can be in, and this
/// view draws the one of them that is a row.
///
/// ### What is not, and why there is no control for it
///
///  * **`allow alarms`** — above.
///  * **A "silence timers but keep incidents" control.** Android has a
///    notification channel per kind, each silenceable on its own; iOS has one
///    app-level authorization and `UNNotificationCategory` is not a
///    user-visible control, so such a row would control nothing
///    (DECISIONS §8).
///  * **Anything promising an alert while the app is closed other than a
///    timer that was already running.** Nothing of this app runs while it is
///    suspended, so a container incident that opens meanwhile is never
///    notified (DECISIONS §10), and no wording here implies otherwise.
///
/// ### Where it is mounted, and why not yet
///
/// Nowhere, today. The row belongs above the first timer row on Home's timers
/// card, which is `HomeSections.kt:396-407`'s position exactly — and
/// `ios/Domovoi/Home/HomeTimersView.swift` is a shared file this item may not
/// edit (NIGHT-RULES §2). The hook is written out in
/// `design-notes/ios/overnight/alerts/DECISIONS.md` §2 for whoever merges, and
/// the UI cases that measure this row skip with that reference until it lands.
///
/// It is **two** lines there and not one, because the read belongs to the
/// screen and the drawing to the row: `.alertsHintLifecycle(alerts)` on Home's
/// root is `HomeScreen.kt:241-247`'s `ON_RESUME` re-read, and this view is
/// `HomeSections.kt:396`'s row. A row that read its own status could not: it
/// is not drawn until the status is known, so its own `.task` would never run
/// — and the whole of what it has to notice is a person coming back from
/// Settings having turned notifications on.
///
/// ### What a person has to confirm on a screen
///
///  * that the warn dot is still a dot and not a lozenge at AX5 — it is a
///    ``ScaledMetric`` circle, so it grows, and nothing in this file knows
///    what it grows *into*;
///  * that the three controls are each 44 points in fact and not only in this
///    file (the measured history on this track: 8.7 points, then 15.7);
///  * that the row reads as one thought and not as four stops under
///    VoiceOver.
struct AlertsHintRowView: View {

    let model: AlertsHintModel

    /// `Timer alerts are off on this phone` is a sentence about this phone,
    /// and the dot beside it is amber: a warning, not a failure.
    ///
    /// ``ScaledMetric`` rather than `frame(width: 8)`, because a fixed glyph
    /// width is the Dynamic Type defect this track keeps finding — a dot that
    /// stays 8 points beside 48-point text has stopped being part of the line.
    @ScaledMetric(relativeTo: .footnote) private var dot: CGFloat = 8

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if model.shown {
            row
                // 44 points, which is both Android's `heightIn(min = 44.dp)`
                // (`HomeSections.kt:355`) and Apple's minimum. The row holds
                // two controls, so the minimum is on the row and
                // `contentShape` is on each control rather than here.
                .frame(minHeight: 44)
        }
    }

    @ViewBuilder
    private var row: some View {
        // A row at the ordinary sizes and a column at the accessibility ones:
        // the sentence is eight words and the two links are two more, and at
        // AX5 all of it on one line leaves the links a few points wide. The
        // swap is the house rule (NIGHT-RULES §3) and not a judgement made
        // here.
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 8) {
                sentence
                HStack(spacing: 16) { controls }
            }
        } else {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                sentence
                Spacer(minLength: 8)
                controls
            }
        }
    }

    private var sentence: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle()
                .fill(Color.warnInk)
                .frame(width: dot, height: dot)
                // A shape has no baseline, so `firstTextBaseline` cannot lift
                // it to the cap height of the line beside it — ``ToneDot``
                // does the same thing for the same reason.
                .offset(y: -2)
                // Decorative: the sentence says what the dot grades.
                .accessibilityHidden(true)
            Text(model.line ?? "")
                .font(.footnote)
                // **Not `fgMuted`.** Android's row is `fgMuted`
                // (`HomeSections.kt:361`), and that token was measured against
                // the WHITE canvas — on a card's `secondarySystemBackground`
                // it fails Apple's audit (NIGHT-RULES §3). This is the one
                // string in the item that departs from the Kotlin's colour,
                // and the reason is a measurement rather than a preference.
                .foregroundStyle(.primary)
                // The sentence wraps at AX5; without this it clips, which is
                // the finding this track has had on four screens.
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var controls: some View {
        if let fix = model.fixLabel {
            Button(fix) { model.fix() }
                .buttonStyle(.plain)
                .font(.footnote.weight(.semibold))
                // `brandInk`, not `brand`: the app's tint is the ink because
                // `brand` as text is 2.05:1 (`DomovoiApp.swift`). A plain
                // button's label does not inherit the scene tint, so it is
                // named here.
                .foregroundStyle(Color.brandInk)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        Button(AlertsStrings.notNow) { model.notNow() }
            .buttonStyle(.plain)
            .font(.footnote)
            .foregroundStyle(Color.brandInk)
            .frame(minHeight: 44)
            .contentShape(.rect)
            // Two one-or-two-word controls side by side, and the shorter of
            // them is the dismissal: VoiceOver gets the sentence with it, so
            // "not now" is never heard without what is being declined.
            .accessibilityLabel("\(AlertsStrings.notNow), \(model.line ?? "")")
    }
}

/// Keep ``AlertsHintModel``'s status honest — the port of
/// `HomeScreen.kt:241-247`, which re-reads both permission facts on every
/// `ON_RESUME`.
///
/// A modifier rather than lines in a screen, so that the hook into Home
/// (DECISIONS §2) is one call and cannot land half-applied: the whole point of
/// it is the person who leaves for Settings, turns notifications on and comes
/// back, and a `.task` without the `scenePhase` half misses exactly that
/// person.
///
/// It goes on **Home's root**, not on the row. The row is not drawn until the
/// status is known, so a `.task` of its own would never run.
struct AlertsHintLifecycle: ViewModifier {

    let model: AlertsHintModel

    @Environment(\.scenePhase) private var phase

    func body(content: Content) -> some View {
        content
            .task { await model.refresh() }
            .onChange(of: phase) { _, now in
                guard now == .active else { return }
                // A `Task` on the screen's root, which outlives every card on
                // it — the house rule the model's own doc comment explains.
                Task { await model.refresh() }
            }
            // The explain card is here and not at the row for the same reason
            // the read is: the row is inside a card inside a lazy grid, and a
            // dialog presented from a view that can be torn down by a scroll
            // is a dialog that can vanish mid-sentence. This also makes the
            // hook into Home **one** line, which is the other half of why
            // this modifier exists.
            .overlay {
                if model.explaining {
                    AlertsPermissionCardView(model: model)
                }
            }
    }
}

extension View {

    /// Read this phone's notification status when Home appears, and again
    /// whenever the app comes back to the foreground.
    func alertsHintLifecycle(_ model: AlertsHintModel) -> some View {
        modifier(AlertsHintLifecycle(model: model))
    }
}
