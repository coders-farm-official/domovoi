import DomovoiKit
import SwiftUI

/// The words to what a room is playing, following the room's clock.
///
/// `LyricsPanel.kt`, the `followRoom` half. ``MusicRoomLyricsModel`` holds
/// the anchor, the 100 ms republish, the nudge and the follow flag; this
/// draws the five states, the source label, the follow chip and the timing
/// row.
///
/// ### Nothing at all is drawn for a refusal
///
/// ``MusicRoomLyricsModel/isHidden`` covers two cases — the room is not on a
/// library track, and the house refused the read (401/403, a missing route,
/// or lyrics not set up). In **both** the section header is absent too, not
/// only the body: a header with a `retry` under it over a read that refuses
/// every time is the control [[no-misleading-controls]] forbids, and it is
/// the exact shape `LyricsPanel.kt:395` guards.
///
/// ### No lyric is logged, printed or put in a failure's message
///
/// `player/Lyrics.kt:41`: the household's own copy of copyrighted text, which
/// is why the routes are device-tier and `no-store`.
/// ``MusicLyricsResult/failed`` carries no message, and nothing here builds
/// one — ``MusicMoreStrings/lyricsFailed`` is a fixed sentence.
///
/// ### The panel comes back to the song by itself
///
/// A drag pauses following and it resumes four seconds after the drag **ends**
/// — Android's `FOLLOW_PAUSE_MS` (`LyricsPanel.kt:468-485`, `:118`), the owner's
/// answer 35. This view only **reports the phase** (`.onScrollPhaseChange`,
/// below); the timer is ``MusicRoomLyricsModel/handReleased()``'s, in a model
/// that outlives this `ScrollView`.
///
/// It was left out for one round on the argument that SwiftUI has no
/// drag-interaction stream and a timer started from a scroll phase would
/// guess at the gesture — and that argument stayed in this header, in the
/// same commit that built the feature 160 lines below it (review 1,
/// finding 4). What answers it is that `.interacting` is reported for a
/// finger and for nothing else, so a release that follows no pause starts
/// nothing: the model refuses it, and `ScreensLyricsFollowTests`'
/// `testAReleaseWithNoDragBeforeItStartsNothing` is the measurement. The
/// `follow` chip stays as well — Android ships it too — and is the immediate
/// way back.
///
/// ### What is not, and why there is no control for it
///
/// * **A tap on a line to seek.** Rooms cannot seek — there is no route, and
///   Android's tap is local-and-seekable only (`LyricsPanel.kt:461`). The
///   lines here are text, not buttons.
/// * **Anything that fetches or re-fetches lyrics.** The lyrics router
///   writes nothing at all (`lyrics.py:26`), so a "look again" button would
///   have no route behind it. `retry` exists for a failed **read** only.
struct MusicLyricsPanelView: View {

    let lyrics: MusicRoomLyricsModel

    /// Whether the section starts open. Android keeps it in prefs
    /// (`PlayerTab.kt:304`); here it is the caller's, because the track sheet
    /// and the player tab want different defaults and neither is a thing
    /// worth a stored preference of its own.
    @State var open: Bool

    /// Whether the fold-open header is drawn. False under the player tab's
    /// `lyrics` panel tab (``MusicPlayerPanelTab``), where the tab strip is
    /// what opens and closes it — the body is then always open.
    var titled = true

    /// Whether anything is drawn. False while another panel tab is picked:
    /// the view **stays in the hierarchy**, so its one `.task`/`.onDisappear`
    /// pair (below) keeps the model reading — the tab's default rule needs to
    /// know whether this song has words before anyone looks.
    var shown = true

    /// Half the container at most, and never under the floor
    /// (`LyricsPanel.kt:134`'s `capLyricsHeight`). The reason survives the
    /// port: a phone on its side with a panel filling it has no scrollable
    /// page left.
    static let minHeight: CGFloat = 120

    static func capped(_ containerHeight: CGFloat) -> CGFloat {
        max(minHeight, containerHeight / 2)
    }

    /// **One `.task`, one `.onDisappear`, on a view whose identity does not
    /// change with what is drawn.**
    ///
    /// They used to hang off the `else` arm of `if lyrics.isHidden`, and that
    /// arm flips: `isHidden` is true until the first answer names a track and
    /// becomes true again the moment a refusal lands. Every flip runs
    /// `stop()` on the leaving arm and `start()` on the arriving one, SwiftUI
    /// does not order the two against each other, and a `stop()` that lands
    /// last cancels the read and the republish loop the arriving arm just
    /// started — a panel frozen on whatever was last drawn. The same shape,
    /// worse, was ``MusicRoomLyricLine``'s.
    ///
    /// The cost is that a hidden panel is a zero-size subview rather than an
    /// `EmptyView`, so its container's spacing is still spent on it. Nothing
    /// is drawn and no element exists — which is what the rule is about —
    /// and it is the smaller of the two prices (DECISIONS).
    var body: some View {
        ZStack(alignment: .topLeading) {
            if !lyrics.isHidden, shown {
                VStack(alignment: .leading, spacing: 8) {
                    if titled { header }
                    if open || !titled {
                        stateBody(for: lyrics.state)
                        footer
                    }
                }
            }
        }
        .task { lyrics.start() }
        .onDisappear { lyrics.stop() }
    }

    // MARK: - the header

    private var header: some View {
        Button { open.toggle() } label: {
            HStack(spacing: 8) {
                Text(MusicMoreStrings.lyrics)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Image(systemName: open ? "chevron.up" : "chevron.down")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityHidden(true)
            }
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // The label says what the tap will **do**, which is the one thing
        // the chevron cannot: "lyrics" alone would read as a heading.
        .accessibilityLabel(open ? MusicMoreStrings.hideLyrics : MusicMoreStrings.showLyrics)
    }

    // MARK: - the five states

    @ViewBuilder
    private func stateBody(for state: MusicLyricsState?) -> some View {
        // `.some(...)` spelled out at every arm. A `switch` over an optional
        // enum with bare case patterns relies on optional-pattern lifting,
        // and this file cannot be compiled here (NIGHT-RULES §5) — so the
        // unambiguous spelling is the one that goes in.
        switch state {
        case .some(.timed(let lines)):
            timed(lines)
        case .some(.plain(let text)):
            ScrollView {
                Text(text)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: Self.minHeight * 2)
        case .some(.instrumental):
            note(MusicMoreStrings.instrumental)
        case .some(.looking):
            note(MusicMoreStrings.lookingForLyrics)
        case .some(.noLyrics):
            note(MusicMoreStrings.noLyricsForThisSong)
        case .none:
            // Nothing has answered. A failed read is a sentence and a
            // control; a read still in flight is a spinner, and the two are
            // told apart because `result` is nil only in the second case.
            if lyrics.result == .failed {
                VStack(alignment: .leading, spacing: 8) {
                    Text(MusicMoreStrings.lyricsFailed)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                    HomeLink(MusicMoreStrings.retry) { lyrics.retry() }
                }
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .accessibilityLabel(MusicMoreStrings.lookingForLyrics)
            }
        }
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// The timed list, scrolled to the active line while following.
    ///
    /// `ScrollViewReader` rather than Android's `animateScrollToItem`, and
    /// the scroll is driven by `activeIndex` changing rather than by the
    /// position tick: the model republishes ten times a second and only a
    /// few of those cross a line.
    private func timed(_ lines: [MusicLyricLine]) -> some View {
        ScrollViewReader { scroller in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                        lyricLine(line, active: index == lyrics.activeIndex)
                            .id(index)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: Self.minHeight * 2)
            // A drag turns following off; it comes back four seconds after
            // the drag ends, or at once from the chip (answer 35).
            //
            // **The view reports the phase and owns nothing.** The timer is
            // ``MusicRoomLyricsModel/handReleased()``'s, reached through
            // ``MusicRoomLyricsModel/hand(_:)``, in a model that outlives
            // this `ScrollView`, because a `Task` started here dies with the
            // panel and a lazy row never holds one (NIGHT-RULES §3).
            //
            // **Three phases, not two.** `.interacting` is a finger moving
            // the list (Android's `DragInteraction.Start`,
            // `LyricsPanel.kt:472`), and `.idle`, `.decelerating` after a
            // flick and `.animating` from the panel's own `scrollTo` below
            // are its `Stop`. `.tracking` is **neither**: a finger down that
            // has not moved the list yet.
            //
            // Round 2 wrote this as `.interacting` against everything else,
            // which sent `.tracking` down the release branch and armed the
            // four-second resume with a finger still on the glass — and the
            // model's post-sleep `!handDown` guard cannot catch that,
            // because the release is the call that clears `handDown`
            // (review 2, finding 3). Nor is `.tracking` inert: the first fix
            // for that finding made it do nothing at all, and then a finger
            // landing inside the four seconds cancelled nothing and set
            // nothing, so the resume fired under it (fix-pass review,
            // must 1). ``MusicRoomLyricsModel/handRested()`` is the third
            // branch that mapping needs. All of it is
            // ``MusicLyricsHandPhase``'s now, in the model, where
            // `ScreensLyricsFollowTests` can hold it; what is left here is
            // the one translation only a view can do.
            .onScrollPhaseChange { _, phase in
                if phase == .interacting {
                    lyrics.hand(.moving)
                } else if phase == .tracking {
                    lyrics.hand(.resting)
                } else {
                    lyrics.hand(.settled)
                }
            }
            .onChange(of: lyrics.activeIndex) { _, now in
                guard lyrics.following, now >= 0 else { return }
                withAnimation(.easeOut(duration: 0.25)) {
                    scroller.scrollTo(now, anchor: .center)
                }
            }
            .onChange(of: lyrics.following) { _, following in
                guard following, lyrics.activeIndex >= 0 else { return }
                withAnimation(.easeOut(duration: 0.25)) {
                    scroller.scrollTo(lyrics.activeIndex, anchor: .center)
                }
            }
        }
    }

    /// One line. A gap is **described** rather than drawn blank: a row with
    /// no words is a row VoiceOver skips entirely (`LyricsPanel.kt:124`).
    private func lyricLine(_ line: MusicLyricLine, active: Bool) -> some View {
        Text(line.isGap ? "\u{00B7}\u{00B7}\u{00B7}" : line.text)
            .font(.subheadline)
            .fontWeight(active ? .semibold : .regular)
            // `brandInk` for the active line and `fgMuted` for the rest:
            // both are text, so both are ink tokens, and `Color.brand` as
            // text is 2.05:1.
            .foregroundStyle(active ? Color.brandInk : Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityLabel(line.isGap ? MusicMoreStrings.instrumentalBreak : line.text)
    }

    // MARK: - the source, the follow chip, and the timing

    @ViewBuilder
    private var footer: some View {
        // Wraps rather than squeezes at an accessibility size: a source
        // label plus four chips is more than a phone's width at AX3.
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { footerPieces }
            VStack(alignment: .leading, spacing: 6) { footerPieces }
        }
    }

    /// Whether the doc on screen is the scrolling kind.
    ///
    /// Asked of ``MusicRoomLyricsModel/timedLines``, which is empty for every
    /// other state, rather than by pattern-matching an optional
    /// ``MusicLyricsState`` — one question, one answer, and the model is
    /// where that question already lives.
    private var isTimed: Bool { !lyrics.timedLines.isEmpty }

    @ViewBuilder
    private var footerPieces: some View {
        if let label = lyrics.doc?.sourceLabel?
            .trimmingCharacters(in: .whitespacesAndNewlines), !label.isEmpty {
            Text(label)
                .font(.caption2)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        if !lyrics.following, isTimed {
            // Offered only when following is actually off **and** there is a
            // list to pull back: on a plain doc nothing scrolls itself, so a
            // `follow` chip there would do nothing visible.
            HomeLink(MusicMoreStrings.follow) { lyrics.follow() }
                .accessibilityLabel(MusicMoreStrings.followTheSongAgain)
        }
        // ``MusicRoomLyricsModel/maySetTiming``, not `isTimed`: a track sheet
        // opened for a song no room is playing has a timed document and **no
        // room**, and an offset with no room to store it against is a write
        // the nudge store drops. Three chips, three taps, nothing moved — the
        // control [[no-misleading-controls]] forbids, which `isTimed` alone
        // drew.
        if lyrics.maySetTiming { timingRow }
    }

    /// The per-room nudge: a quarter of a second each way, and a reset.
    ///
    /// Timed lyrics only — there is nothing for an offset to move on a plain
    /// doc, and a chip that changed a number nobody can see is the forbidden
    /// control in its quietest form.
    private var timingRow: some View {
        HStack(spacing: 4) {
            Text(lyrics.nudgeLabel)
                .font(.caption2)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            chip(MusicMoreStrings.earlierChip, MusicMoreStrings.lyricsEarlier) {
                lyrics.nudgeEarlier()
            }
            chip(MusicMoreStrings.laterChip, MusicMoreStrings.lyricsLater) {
                lyrics.nudgeLater()
            }
            chip(MusicMoreStrings.resetChip, MusicMoreStrings.resetLyricsTiming) {
                lyrics.resetNudge()
            }
        }
    }

    private func chip(
        _ text: String,
        _ label: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(text)
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 10)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        // `−¼ s` is not a sentence; the label is what it does.
        .accessibilityLabel(label)
    }
}

/// The one line of lyric a room card carries (`LyricsPanel.kt:701`).
///
/// **Timed lyrics only.** A plain document has no line that belongs to right
/// now, so a card showing its first line would be showing the wrong words
/// for the whole song; Android's own guard is the same.
struct MusicRoomLyricLine: View {

    let lyrics: MusicRoomLyricsModel

    /// **The lifecycle is outside the conditional.**
    ///
    /// It used to be on all three arms, and `current` is nil for an
    /// instrumental gap — so the arm, and with it the view's identity,
    /// flipped every time the room crossed into or out of a break, which a
    /// real `.lrc` is full of. Each flip ran `stop()` on the leaving arm and
    /// `start()` on the arriving one; `start()` re-issues
    /// `GET /api/music/now-playing/{room}/lyrics`, so every gap boundary cost
    /// a round trip per room card on screen, and because SwiftUI does not
    /// order a removed view's `onDisappear` against an inserted view's
    /// `task`, a `stop()` landing last cancelled the 100 ms republish loop
    /// and froze the card on the words last drawn until the next
    /// now-playing push.
    ///
    /// One `ZStack`, one `.task`, one `.onDisappear`. The zero-size empty
    /// case is what the `else` arm already was.
    var body: some View {
        ZStack(alignment: .leading) {
            if !lyrics.isHidden, let line = current {
                Text(line)
                    .font(.caption)
                    // A lyric is text, so an ink token. `brandInk` is 5.65:1;
                    // `Color.brand` as text is 2.05:1.
                    .foregroundStyle(Color.brandInk)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .task { lyrics.start() }
        .onDisappear { lyrics.stop() }
    }

    /// The active line's words, or nil for a gap, a plain doc, or before the
    /// first line.
    private var current: String? {
        let lines = lyrics.timedLines
        let index = lyrics.activeIndex
        guard lines.indices.contains(index) else { return nil }
        let line = lines[index]
        return line.isGap ? nil : line.text
    }
}
