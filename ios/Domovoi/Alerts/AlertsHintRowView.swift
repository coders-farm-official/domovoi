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
/// ### Where it is mounted
///
/// Above the first timer row on Home's timers card
/// (`ios/Domovoi/Home/HomeTimersView.swift`), which is
/// `HomeSections.kt:396-407`'s position exactly. The first Simulator train
/// skipped five of this item's seven cases because nothing mounted it; the
/// hook landed after that train, and `DECISIONS.md` §2 records what was
/// applied and the one place it departs from what the mapper wrote.
///
/// It is **two** things and not one, because the read belongs to the screen
/// and the drawing to the row: ``AlertsHintLifecycle`` is
/// `HomeScreen.kt:241-247`'s `ON_RESUME` re-read and sits on the app's root
/// (``DomovoiApp``), while this view is `HomeSections.kt:396`'s row. A row
/// that read its own status could not: it is not drawn until the status is
/// known, so its own `.task` would never run — and the whole of what it has
/// to notice is a person coming back from Settings having turned
/// notifications on.
///
/// The model reaches this row through
/// ``EnvironmentValues/alertsHintModel`` rather than a parameter, for the
/// reason that property's own comment gives.
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
                // **An identifier, because the label is not a handle.** This
                // control's words change with the state (`turn on`, then
                // `open Settings`), and the dismissal beside it carries the
                // whole sentence in its label for VoiceOver — so a UI case
                // that picks either of them by label is picking by something
                // the design is free to change. Train S006 failed on exactly
                // that: `buttons["not now"]` matched nothing, because the
                // button answers to `not now, Timer alerts are off on this
                // phone`. The identifier is the stable handle; the label
                // stays what a person hears.
                .accessibilityIdentifier("alerts-hint-fix")
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
            // See `alerts-hint-fix` above: this label is a sentence on
            // purpose, so the handle is the identifier.
            .accessibilityIdentifier("alerts-hint-not-now")
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
/// It goes on the **app's root** (``DomovoiApp``) and not on the row. The row
/// is not drawn until the status is known, so a `.task` of its own would never
/// run — and the overlay it carries is a modal dialog, which a tab switch
/// must not be able to take away mid-sentence.
struct AlertsHintLifecycle: ViewModifier {

    let model: AlertsHintModel

    @Environment(\.scenePhase) private var phase

    func body(content: Content) -> some View {
        // **Hidden, not merely behind**, while the card is up — the same
        // line `SettingsPlaceholder` carries (`RouteScreen.swift:196-203`)
        // and for the same measured reason: `DialogCard`'s `.isModal` stops
        // VoiceOver *navigating* out of the card, but Home's rows stay in the
        // tree under a 35% scrim, where Apple's audit reports them as
        // `Contrast failed` — truthfully, since they are unreadable.
        // **A `ZStack` with the hide as the outermost modifier of the content
        // branch, not `content.accessibilityHidden(…).overlay { card }`.**
        //
        // The overlay form is what train S006 ran, and it measured as doing
        // nothing: with the card up, Apple's audit still reported `Contrast
        // failed` against `domovoi / home`, `unverified`, the address pill
        // and all four tab labels — every one of them a view behind the 35%
        // scrim, every one of them still in the tree
        // (`overnight-trains/S006/Alerts/att/testAlerts_TurningOnAlertsExplainsItselfFirst/`,
        // the tree attachment and the picture). The shape below is
        // ``SettingsPlaceholder``'s (`RouteScreen.swift:196-206`), which is
        // the one placement in this app where the hide was observed to take:
        // the flag sits on the content branch with nothing composed after it.
        //
        // If a later train still reports those seven, the remaining
        // difference is the level rather than the shape — this modifier is on
        // the window's root, above the `TabView`, where ``SettingsPlaceholder``
        // is inside a route — and the fix is then a shared one, written up in
        // DECISIONS §15.
        ZStack {
            content
                .accessibilityHidden(model.explaining)
            // The explain card is here and not at the row for the same reason
            // the read is: the row is inside a card inside a lazy grid, and a
            // dialog presented from a view that can be torn down by a scroll
            // is a dialog that can vanish mid-sentence. This also makes the
            // hook into Home **one** line, which is the other half of why
            // this modifier exists.
            if model.explaining {
                AlertsPermissionCardView(model: model)
            }
        }
        .task { await model.refresh() }
        .onChange(of: phase) { _, now in
            guard now == .active else { return }
            // A `Task` on the screen's root, which outlives every card on
            // it — the house rule the model's own doc comment explains.
            Task { await model.refresh() }
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
