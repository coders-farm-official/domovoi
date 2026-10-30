import DomovoiKit
import SwiftUI

/// Home's **timers** card — a port of `HomeTimers` (`HomeSections.kt:370`),
/// map §3.3.
///
/// **Every decision is the package's**: whether the card appears
/// (``timersShown(_:)``), which rows (``TimerView``), what each is called
/// (``timerTitle(_:shared:)``), how long is left (``fmtLeft(_:)``,
/// ``secondsLeft(_:nowMs:)``), how far the bar has run
/// (``elapsedFraction(_:nowMs:)``), what the done lines say
/// (``doneLineTitle(_:)``) and what VoiceOver reads for the cross
/// (``HomeTimerStrings/cancelLabel(_:shared:)``). What is in this file is
/// layout, which `ios/scripts/pkgtest.sh` cannot see.
///
/// ### The clock here is the server's
///
/// Every figure on this card is measured against ``HomeModel/serverNowMs``
/// and not ``HomeModel/nowMs``, because the server deletes a timer the moment
/// it fires and a phone whose clock is a minute out would draw a countdown a
/// minute wrong. The date and today's grouping use the phone's. Two clocks,
/// and this is the one that is not the obvious one.
///
/// ### Neither alert-hint row is here
///
/// Owner decision, 2026-10-30, settled rather than deferred: `Timer alerts
/// are off on this phone` and `Timers may ring a few minutes late on this
/// phone` are not ported, because this app schedules no local notifications
/// and a row offering to turn on an alert that will never ring is the
/// misleading control the owner has ruled out. `HomeTimers.swift`'s header
/// carries the reasoning and the price of bringing it back.
///
/// ### What a person has to confirm on a screen
///
/// * that the 24-point monospace countdown does not collide with a long
///   title at AX5 — the row reflows at the accessibility sizes, which is
///   unverified until someone looks;
/// * that the cancel cross is a 44-point target in fact and not only in
///   this file (the measured history: 8.7 points, then 15.7);
/// * that a `warn`-coloured countdown is legible in Dark Mode and with
///   Increase Contrast, and that the colour is not the only thing saying a
///   timer is close — the number itself is.
struct HomeTimersView: View {

    let view: TimerView

    /// ``HomeModel/serverNowMs``. **Not** `nowMs`, and not `Date()`.
    let serverNowMs: Int64

    /// ``ShellModel``'s `visibility.sharedScreen`: a reminder on a hall screen
    /// reads `reminder · office` and never its own words.
    let shared: Bool

    /// Rooms that are online, for dimming a room chip whose room is not
    /// (`alpha 0.7`, `HomeSections.kt:460`).
    let onlineRooms: Set<String>

    /// `+N more` has been tapped. `@State` in ``HomeView``, as Android's
    /// `rememberSaveable` is in the screen, and it never collapses again.
    let expanded: Bool

    let onMore: () -> Void

    /// A cancel was asked for. ``HomeView`` owns the request and the
    /// ``TimerBook``'s guard, because **a row holding the `Task` is the
    /// announce bug** in a second place: the DELETE and its toast must not
    /// die because the row scrolled away or the read that follows removed it.
    let onCancel: (HomeTimer) -> Void

    /// Which ids have a DELETE in flight — ``TimerBook/isCancelling(_:)``.
    let cancelling: (Int64) -> Bool

    @Environment(\.horizontalSizeClass) private var sizeClass

    @Environment(\.dynamicTypeSize) private var typeSize

    private var compact: Bool { ShellWidth(sizeClass) == .compact }

    /// `HOME_PHONE_TIMERS = 2` on a phone, every row on a wide pane
    /// (`HomeSections.kt:410`).
    private var shownTimers: [HomeTimer] {
        guard compact, !expanded else { return view.active }
        return Array(view.active.prefix(homePhoneTimers))
    }

    private var moreLabel: String? {
        guard compact, !expanded else { return nil }
        return homeMoreLabel(extra: view.active.count - homePhoneTimers)
    }

    var body: some View {
        // **No card at all when there is nothing to say** — not an empty card
        // and not a placeholder (map §5: "timers: no card"). A section with
        // nothing to say gets no slot and leaves no gap, which SwiftUI gives
        // for free for a child whose body evaluates to nothing (measured in
        // part 2, `HomeView.swift`).
        if timersShown(view) {
            HomeCard(
                title: HomeTimerStrings.title,
                actionLabel: moreLabel,
                onAction: onMore
            ) {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(shownTimers, id: \.id) { timer in
                        row(timer)
                    }
                    // Keyed on the timer's id and not on a position: both
                    // paths that build `done` de-duplicate by id (the fire
                    // ledger's and the book's), so the id is unique, and a
                    // positional key would re-identify every line each time a
                    // newer one arrived at the top.
                    ForEach(view.done, id: \.timer.id) { done in
                        doneLine(done)
                    }
                }
            }
        }
    }

    // MARK: - one running timer (`TimerRow`, `HomeSections.kt:460`)

    @ViewBuilder
    private func row(_ timer: HomeTimer) -> some View {
        let left = secondsLeft(timer, nowMs: serverNowMs)
        VStack(alignment: .leading, spacing: 6) {
            if typeSize.isAccessibilitySize {
                // At the accessibility sizes the countdown goes **under** the
                // title rather than beside it, which is the shape
                // ``HomeHeaderView`` and ``HomeAttentionView`` already use: a
                // 24-point monospace number and a wrapping title cannot share
                // a line at AX5, and `minimumScaleFactor` shrinks text for the
                // person who asked for bigger.
                VStack(alignment: .leading, spacing: 6) {
                    chipAndTitle(timer)
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        countdown(left)
                        Spacer(minLength: 8)
                        cancel(timer)
                    }
                }
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    chipAndTitle(timer)
                    Spacer(minLength: 8)
                    countdown(left)
                    cancel(timer)
                }
            }
            // The 2 dp elapsed bar pinned to the bottom of the row
            // (`HomeSections.kt:530`). Hidden from VoiceOver: the countdown
            // beside it is the same fact in words, and a progress stop reading
            // "41 per cent" of an unnamed thing is noise.
            ProgressView(value: elapsedFraction(timer, nowMs: serverNowMs))
                .progressViewStyle(.linear)
                .frame(height: 2)
                .tint(Color.brand)
                .accessibilityHidden(true)
        }
        // One element, one phrase: the room, the title and the time left read
        // together. Three `Text`s in an `HStack` are three VoiceOver stops
        // whose relation to each other a person has to guess.
        .accessibilityElement(children: .contain)
    }

    /// The room chip — or the `no room` pill, which is the same words either
    /// way (``timerRoomLabel(_:)``) — and the title beside it.
    private func chipAndTitle(_ timer: HomeTimer) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(timerTitle(timer, shared: shared))
                .font(.subheadline)
                // No `lineLimit`: a timer's label is a person's own words
                // (`pasta`, `the thing in the oven`), so there is no length
                // this can assume. The declared divergence from Android's
                // one-line rows, for the reason the attention card's is.
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Text(timerRoomLabel(timer.roomID))
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
                // `alpha 0.7` when the room is not online
                // (`HomeSections.kt:460`): the timer is still running, but the
                // room it will ring in is not there to ring.
                .foregroundStyle(Color.fgMuted)
                .opacity(roomIsOnline(timer) ? 1 : 0.7)
        }
    }

    /// A timer with no room is not a timer whose room is offline, so the
    /// `no room` pill is never dimmed.
    private func roomIsOnline(_ timer: HomeTimer) -> Bool {
        guard let room = timer.roomID, !room.isEmpty else { return true }
        return onlineRooms.contains(room)
    }

    /// The countdown: **monospace, title-sized**, warn-coloured under
    /// ``homeSoonSec``.
    ///
    /// Monospace is load-bearing and not a style choice: a proportional digit
    /// changes width as it changes value, so `12m 04s` counting to `12m 03s`
    /// would shift the whole row left and right once a second.
    ///
    /// ### A text style, not 24 points
    ///
    /// This read `.system(size: 24, weight: .semibold, design: .monospaced)`,
    /// Android's `24sp` taken literally, and **`Font.system(size:)` does not
    /// participate in Dynamic Type at all** — it is the same size whatever the
    /// reader asked for. Apple's audit names this exactly, in words worth
    /// quoting because they are not about layout:
    /// *"User will not be able to change the font size of this element."*
    ///
    /// It was never reported against this view, and that is the lesson rather
    /// than a reprieve: the stub house serves no timers in the state the audit
    /// runs in, so this card has never been rendered under it. An audit covers
    /// the states you launch. Found by reading the detail line on six findings
    /// elsewhere and then grepping for the construct.
    ///
    /// `.title2` is 22 points at the default size — the nearest scaling style
    /// to 24, and nearer than `.title`'s 28 — so the number looks as it did
    /// and now grows with every other word on the screen.
    private func countdown(_ left: Int64) -> some View {
        Text(fmtLeft(left))
            .font(.system(.title2, design: .monospaced).weight(.semibold))
            .fixedSize(horizontal: false, vertical: true)
            // ``Color/warnInk`` and never `.orange`: `systemOrange` is 2.20:1
            // on the light canvas and this is 5.73:1. The colour grades the
            // urgency; the number is what says it.
            .foregroundStyle(left < homeSoonSec ? Color.warnInk : Color.primary)
    }

    /// The cancel control: a 44 × 44 cross on a phone, the word on a wide
    /// pane (`HomeSections.kt:498-520`).
    ///
    /// The cross has **no visible text at all**, so
    /// ``HomeTimerStrings/cancelLabel(_:shared:)`` is the only thing that says
    /// what it cancels — and that string is the package's, with the
    /// shared-screen rule already applied by ``timerNoun(_:shared:)``.
    @ViewBuilder
    private func cancel(_ timer: HomeTimer) -> some View {
        let busy = cancelling(timer.id)
        if compact {
            Button { onCancel(timer) } label: {
                Image(systemName: "xmark")
                    .font(.footnote)
                    // A `frame` is not a hit area: SwiftUI hit-tests the drawn
                    // content, and a glyph is all a plain button gives it. The
                    // forget button in Settings measured 8.7 points under a
                    // comment promising 44.
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.fgMuted)
            .disabled(busy)
            .accessibilityLabel(HomeTimerStrings.cancelLabel(timer, shared: shared))
        } else {
            Button(busy ? HomeTimerStrings.cancelling : HomeTimerStrings.cancel) {
                onCancel(timer)
            }
            .buttonStyle(.bordered)
            .tint(Color.brandInk)
            .fixedSize(horizontal: false, vertical: true)
            .frame(minHeight: 44)
            .contentShape(.rect)
            .disabled(busy)
            .accessibilityLabel(HomeTimerStrings.cancelLabel(timer, shared: shared))
        }
    }

    // MARK: - a done line (`HomeSections.kt:541`)

    /// `done · kitchen`, the timer's title beside it, and the server's own
    /// summary underneath.
    ///
    /// **The title is omitted entirely when this is a reminder on a shared
    /// screen** — not masked, omitted — because `reminder · office` is
    /// already what ``doneLineTitle(_:)`` says and repeating
    /// ``timerTitle(_:shared:)`` beside it would print the room twice. The
    /// summary is room ids and outcomes and never speech
    /// (`heard in garage, kitchen · still announcing`), which is why a shared
    /// screen may show it.
    private func doneLine(_ done: DoneTimer) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                ToneDot(tone: done.tone)
                Text(doneLineTitle(done.timer.roomID))
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                if !(shared && done.timer.isReminder) {
                    Text(timerTitle(done.timer, shared: shared))
                        .font(.subheadline)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            if let summary = done.summary {
                Text(summary)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    // Android caps this at two lines. Not capped here, for the
                    // reason the attention card's rows are not: the summary
                    // names rooms a person chose the names of, and
                    // `.lineLimit(2)` at AX5 is the clipping Apple's audit
                    // failed the first screen of this app for. A declared
                    // divergence, on the Simulator checklist.
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }
}
