import DomovoiKit
import SwiftUI

/// Music's `player` tab: this phone's own queue, its transport, and the
/// words to what it is playing.
///
/// `PlayerTab.kt`, over ``MusicMorePlayerEngine``.
///
/// ### Nothing conforms to that protocol on this branch, so this is not drawn
///
/// ``MusicTab/shown(hasPlayer:)`` leaves `player` out of the tab bar
/// entirely when ``MusicLocalQueueModel/hasEngine`` is false — not greyed,
/// not empty — and ``MusicView`` draws this only for the tab that is not
/// offered. The audio engine is the separate overnight item "player"; this
/// is the screen that adopts it with one conformance and nothing else moved
/// (DECISIONS). Every rule the tab needs is already specified by a test:
/// which entry the lyrics follow, which chapter is current, what a cast
/// sends and what it refuses are ``MusicLocalQueueModel``'s and
/// ``MusicCastPlanner``'s.
///
/// ### The queue is emitted a row at a time, and the position is not in it
///
/// Android's own comment is the reason: the single-item version "built a
/// clickable row with three icon buttons for EVERY queue entry on the main
/// thread in one frame: with a few thousand tracks queued that froze the app
/// for seconds, then ran it out of memory (the 2026-09-30 freeze and crash)"
/// (`PlayerTab.kt:80-85`). So the rows are a `LazyVStack`, and the seek row
/// is the **only** piece that reads ``MusicLocalQueueModel/positionSec`` —
/// everything else reads the queue, the index and the target, which a
/// position tick does not change.
///
/// ### What is not, and why there is no control for it
///
/// * **"Save queue as playlist"** (`MusicScreen.kt:515`).
///   ``MusicLocalQueueModel/libraryIDsToSave`` names the ids it would store
///   and nothing writes them: that is a create-then-add-per-id loop, which
///   belongs to a model and there is none. A button with no request behind
///   it is the control [[no-misleading-controls]] forbids, so it is absent
///   until the engine's item brings the write.
/// * **Songs stored on the phone** (``MusicPlayKind/device``). This app has
///   no list of them; it is the `phonemedia` item's.
/// * **Speed and chapters while casting.** A room plays at its own rate and
///   reports a title and nothing else, so neither can be acted on — the
///   model answers both (``MusicLocalQueueModel/maySetSpeed`` and
///   ``MusicLocalQueueModel/chapters``) and the sections are absent rather
///   than inert.
struct MusicPlayerTabView: View {

    let player: MusicLocalQueueModel
    let covers: MusicCoverStore

    /// The lyrics for whatever is playing, or nil when there is no track with
    /// a library id to look one up by.
    let lyrics: MusicRoomLyricsModel?

    /// The rooms the **server** reported, for the cast menu.
    let rooms: [String]

    /// The panel tab picked **for one song** — its key
    /// (``MusicPlayerPanel/songKey(castingTo:lyricsTrackID:currentUID:)``)
    /// and the tab. The session's pick is ``MusicPlayerPanelMemory``.
    @State private var pickedHere: PanelPick?

    private struct PanelPick: Equatable {
        let song: String
        let tab: MusicPlayerPanelTab
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if player.isEmpty {
                empty
            } else {
                head
                if player.maySeek { seek }
                transport
                if player.maySetSpeed { speeds }
                sleep
                if !player.chapters.isEmpty { chapters }
                panel
            }
        }
    }

    // MARK: - the panel: Lyrics · Queue · Hide

    /// The full player's panel tabs (`PlayerQueueSheet.kt:102-123`,
    /// 3cdd261b) — on this tab because the phone has no mini player and this
    /// is the full player (``MusicPlayerPanelTab``).
    ///
    /// The lyrics panel stays in the hierarchy whichever tab is picked, with
    /// `shown` false when it is not the lyrics tab: its `.task` is what keeps
    /// the words loading, and the default rule — Lyrics when the song has
    /// some — has to know before anyone looks.
    @ViewBuilder
    private var panel: some View {
        let tab = panelTab
        tabStrip(selected: tab)
        if let lyrics {
            MusicLyricsPanelView(lyrics: lyrics, open: true, titled: false, shown: tab == .lyrics)
        }
        if tab == .queue { queue }
    }

    /// Whether this song can have a lyrics tab: a library track (the model
    /// has a track), whose words the house did not refuse this viewer.
    private var lyricsTab: Bool {
        guard let lyrics else { return false }
        return lyrics.trackID != nil && lyrics.result != .hidden
    }

    private var songKey: String {
        MusicPlayerPanel.songKey(
            castingTo: player.castingTo,
            lyricsTrackID: player.lyricsTrackID,
            currentUID: player.current?.uid
        )
    }

    private var panelTab: MusicPlayerPanelTab {
        MusicPlayerPanel.choose(
            here: pickedHere.flatMap { $0.song == songKey ? $0.tab : nil },
            picked: MusicPlayerPanelMemory.picked,
            lyricsTab: lyricsTab,
            lyricsFound: MusicPlayerPanel.lyricsFound(lyrics?.state),
            lyricsLoading: lyrics != nil && lyrics?.result == nil
        )
    }

    private func tabStrip(selected: MusicPlayerPanelTab) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 4) {
                ForEach(MusicPlayerPanel.tabs(lyricsTab: lyricsTab), id: \.self) { one in
                    chipButton(one.label, chosen: one == selected) {
                        pickedHere = PanelPick(song: songKey, tab: one)
                        MusicPlayerPanelMemory.picked = one
                    }
                    .accessibilityIdentifier(one.identifier)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - nothing queued

    private var empty: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(MusicMoreStrings.nothingQueued)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(MusicMoreStrings.nothingQueuedNote)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .center)
        .multilineTextAlignment(.center)
        .padding(.vertical, 24)
    }

    // MARK: - what is playing

    @ViewBuilder
    private var head: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let room = player.castingTo {
                HomeTonePill(label: MusicMoreStrings.castingTo(room), tone: .brand)
            }
            HStack(alignment: .top, spacing: 12) {
                // **No cover while casting.** `PlayerTab.kt:259` passes null
                // for a remote target, and the reason holds here: the room
                // reports a title, and the picture this phone would draw is
                // whatever it last had queued rather than what the room is
                // playing.
                if !player.isRemote, let trackID = player.current?.coverTrackID {
                    MusicCoverView(trackID: trackID, size: 112, corner: 10, covers: covers)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(player.current?.title ?? MusicStrings.unknownTitle)
                        .font(.title3)
                        .fixedSize(horizontal: false, vertical: true)
                    if let artist = player.current?.artist, !artist.isEmpty {
                        Text(artist)
                            .font(.subheadline)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let album = player.current?.album, !album.isEmpty {
                        Text(album)
                            .font(.footnote)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityElement(children: .combine)
                // **"Stop and close"** — the "×" Android and the web put on
                // the mini player (`MiniPlayer.kt`, d19efd9c; `player.jsx`).
                // This app has no docked bar: this tab is where the phone's
                // queue is shown, so this is where it is shut. It stops,
                // empties the queue and the tab goes back to "nothing
                // queued"; while casting it stops the room first, and a room
                // that says it didn't keeps everything and is said
                // (``MusicLocalQueueModel/stopAndClose()``). The transport's
                // `stop` below is `PlayerTab.kt:392`'s and is unchanged.
                control("xmark", MusicMoreStrings.stopAndClose) {
                    player.stopAndClose()
                }
            }
        }
    }

    /// The bar and the clock — the **only** piece that follows the tick.
    ///
    /// The slider writes ``MusicLocalQueueModel/scrubbingSec`` while a finger
    /// is down and the engine's own position is ignored until it lifts: a bar
    /// that snapped back to the last tick under a finger is the one
    /// interaction everybody notices.
    private var seek: some View {
        let total = player.current?.durationSec ?? 0
        return VStack(alignment: .leading, spacing: 4) {
            Slider(
                value: scrub,
                in: 0...max(total, 1),
                onEditingChanged: { editing in
                    guard !editing, let at = player.scrubbingSec else { return }
                    player.engine?.seek(toSec: at)
                    player.scrubbingSec = nil
                }
            )
            .tint(Color.brand)
            .accessibilityLabel(MusicStrings.title)
            Text("\(fmtDur(player.positionSec)) / \(fmtDur(total))")
                .font(.caption.monospacedDigit())
                .foregroundStyle(Color.fgMuted)
        }
    }

    private var scrub: Binding<Double> {
        Binding(
            get: { player.scrubbingSec ?? player.positionSec },
            set: { player.scrubbingSec = $0 }
        )
    }

    // MARK: - the transport

    /// Previous, play-or-pause, next, stop, cast.
    ///
    /// The row **wraps** at an accessibility text size rather than squeezing,
    /// as every other control row in this app does.
    private var transport: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 4) { transportButtons }
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 4) { transportButtons }
            }
        }
    }

    @ViewBuilder
    private var transportButtons: some View {
        if player.canPrevious {
            control("backward.end", MusicTransportAction.previous.label) {
                player.engine?.previous()
            }
        }
        control(
            player.engine?.isPlaying == true ? "pause.fill" : "play.fill",
            player.engine?.isPlaying == true
                ? MusicTransportAction.pause.label
                : MusicTransportAction.resume.label
        ) {
            player.engine?.toggle()
        }
        // Greyed is not a thing this app does: the last entry in a queue has
        // no next, so the control is absent (`PlayerTab.kt:361`'s `canNext`
        // read as presence rather than as an alpha).
        if player.canNext {
            control("forward.end", MusicTransportAction.skip.label) {
                player.engine?.next()
            }
        }
        control("stop.fill", MusicTransportAction.stop.label) {
            player.engine?.stop()
        }
        castControl
    }

    /// Cast to a room, or come back from one.
    ///
    /// Drawn only when ``MusicLocalQueueModel/mayCast`` — a menu over a queue
    /// nothing in which can play in a room is the control Android shipped
    /// before 2026-09-30, "casting to office" over a silent room.
    @ViewBuilder
    private var castControl: some View {
        if player.isRemote {
            control("iphone", MusicMoreStrings.cast) {
                Task { @MainActor in
                    guard let engine = player.engine else { return }
                    _ = await engine.playHere()
                }
            }
        } else if player.mayCast, !rooms.isEmpty {
            Menu {
                ForEach(rooms, id: \.self) { room in
                    Button(MusicStrings.playIn(room: room)) { player.cast(to: room) }
                }
            } label: {
                Image(systemName: "airplayaudio")
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            .accessibilityLabel(MusicMoreStrings.cast)
        }
    }

    // MARK: - speed and sleep

    private var speeds: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(MusicMoreStrings.speed)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 4) {
                    ForEach(MusicPlayerMath.speeds, id: \.self) { chip in
                        chipButton(
                            MusicMoreStrings.speedLabel(chip),
                            chosen: MusicPlayerMath.isSpeedSelected(chip, speed: player.speed)
                        ) {
                            player.engine?.setSpeed(chip)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var sleep: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(MusicMoreStrings.sleep)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
            if let left = player.sleepRemainingSec {
                HStack(spacing: 8) {
                    Text(fmtDur(Double(left)))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(Color.fgMuted)
                    HomeLink(MusicMoreStrings.cancel) { player.engine?.cancelSleep() }
                        .accessibilityLabel(MusicMoreStrings.cancelSleepTimer)
                }
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 4) {
                        ForEach(MusicPlayerMath.sleepMinutes, id: \.self) { minutes in
                            chipButton(MusicMoreStrings.sleepMinutes(minutes), chosen: false) {
                                player.engine?.setSleep(minutes: minutes)
                            }
                        }
                        chipButton(MusicMoreStrings.endOfTrack, chosen: false) {
                            player.engine?.setSleepEndOfTrack()
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    // MARK: - chapters

    private var chapters: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(player.chapters.enumerated()), id: \.offset) { index, chapter in
                let here = index == player.currentChapter
                Button {
                    player.engine?.seek(toSec: chapter.startSec)
                } label: {
                    HStack(spacing: 8) {
                        Text(chapter.title.isEmpty
                             ? MusicMoreStrings.chapterFallback(index)
                             : chapter.title)
                            .font(.subheadline)
                            .fontWeight(here ? .semibold : .regular)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 8)
                        Text(fmtDur(chapter.startSec))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(Color.fgMuted)
                    }
                    .frame(minHeight: 44)
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(here ? Color.brandInk : Color.primary)
                .accessibilityAddTraits(here ? [.isButton, .isSelected] : .isButton)
            }
        }
    }

    // MARK: - the queue

    private var queue: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(MusicMoreStrings.queueHeader(player.queue.count))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if !player.queue.isEmpty {
                    HomeLink(MusicMoreStrings.clear) { player.engine?.clearQueue() }
                }
            }
            LazyVStack(alignment: .leading, spacing: 4) {
                ForEach(Array(player.queue.enumerated()), id: \.element.uid) { index, item in
                    MusicPlayerQueueRow(player: player, item: item, index: index)
                }
            }
        }
    }

    // MARK: - the two shapes every control row here uses

    private func control(
        _ symbol: String,
        _ label: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .accessibilityLabel(label)
    }

    private func chipButton(
        _ text: String,
        chosen: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(text)
                .font(.subheadline)
                .fontWeight(chosen ? .semibold : .regular)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(chosen ? Color.brandInk : Color.fgMuted)
        .background(chosen ? Color(.secondarySystemBackground) : .clear, in: Capsule())
        .accessibilityAddTraits(chosen ? [.isButton, .isSelected] : .isButton)
    }
}

/// One entry in this phone's queue.
///
/// A tap plays it here — or, **while casting, starts the room there**
/// (`PlayerTab.kt:598-611`): playing it on the phone while a room is also
/// playing would put two players on at once.
/// ``MusicLocalQueueModel/tapped(_:)`` is where that choice lives, so the
/// row is one button either way.
///
/// **A lazy row holds no `Task`.** Every write here is the model's or the
/// engine's, both of which outlive the row, the tab and the screen.
struct MusicPlayerQueueRow: View {

    let player: MusicLocalQueueModel
    let item: MusicPlayerItem
    let index: Int

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Button {
                player.tapped(index)
            } label: {
                HStack(alignment: .top, spacing: 10) {
                    place
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.title)
                            .font(.subheadline)
                            .fontWeight(index == player.index ? .semibold : .regular)
                            .fixedSize(horizontal: false, vertical: true)
                        if let artist = item.artist, !artist.isEmpty {
                            Text(artist)
                                .font(.caption)
                                .foregroundStyle(Color.fgMuted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            edits
        }
        .padding(.vertical, 2)
    }

    /// The number, or the speaker for the entry playing now. The icon is
    /// labelled rather than hidden: the semibold title is not something
    /// VoiceOver reads out, so it is the only thing on the row that says
    /// which one this is.
    @ViewBuilder
    private var place: some View {
        if index == player.index {
            Image(systemName: "speaker.wave.2.fill")
                .font(.caption)
                .foregroundStyle(Color.brandInk)
                .accessibilityLabel(MusicStrings.nowPlayingHere)
        } else {
            Text("\(index + 1)")
                .font(.caption.monospacedDigit())
                .foregroundStyle(Color.fgMuted)
                .accessibilityHidden(true)
        }
    }

    @ViewBuilder
    private var edits: some View {
        HStack(spacing: 0) {
            if index > 0 {
                control("arrow.up", MusicMoreStrings.moveUp) {
                    player.engine?.move(from: index, to: index - 1)
                }
            }
            if index < player.queue.count - 1 {
                control("arrow.down", MusicMoreStrings.moveDown) {
                    player.engine?.move(from: index, to: index + 1)
                }
            }
            control("minus.circle", MusicStrings.removeFromQueue, tint: Color.errInk) {
                player.engine?.remove(at: index)
            }
        }
    }

    private func control(
        _ symbol: String,
        _ label: String,
        tint: Color = .brandInk,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(tint)
        .accessibilityLabel("\(label) \(item.title)")
    }
}
