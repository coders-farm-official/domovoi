import DomovoiKit
import SwiftUI

/// What each room is playing, **and the controls for it**.
///
/// `NowPlayingCards.kt`. The first Music batch drew this as information only
/// and said why in its own doc: a row of buttons that did nothing would have
/// been worse than no row. The buttons work now, so they are here, and the
/// paragraph that promised them is gone rather than left to rot.
///
/// Each card carries its cover and the line of lyric the room is on, both
/// only when the room is on a **library track**: a stream has no `track_id`,
/// so neither has anything to read.
///
/// ### One card per room, not a grid
///
/// Android lays these out one column on a phone and two on a wider screen
/// (`compactWidth()`). This is one column always: the iPhone app has no wide
/// layout yet anywhere, and a two-column grid that only ever renders as one
/// column is code nothing exercises.
struct MusicNowPlayingStrip: View {

    let music: MusicModel

    /// The screen's one cover store.
    let covers: MusicCoverStore

    /// The lyrics model for a room, **looked up** rather than built: the
    /// dictionary is ``MusicView``'s `@State` and a model made inside a body
    /// is a fresh one every redraw. Nil for a room whose model has not been
    /// synced yet, and for a house with no lyrics store.
    let lyrics: (NowPlayingRoom) -> MusicRoomLyricsModel?

    var body: some View {
        // Nothing at all while no room has answered: a card saying "nothing
        // playing" over a house whose satellites simply have not been read
        // yet is a claim the app cannot make. Android draws "no rooms
        // provisioned yet" here from the same empty list, which is the same
        // claim — it cannot tell an unprovisioned house from an unread one.
        if !music.rooms.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(music.rooms, id: \.roomID) { room in
                    MusicRoomCard(
                        music: music,
                        room: room,
                        covers: covers,
                        lyrics: lyrics(room)
                    )
                }
            }
        }
    }
}

/// "Show rooms" / "Show rooms (2 playing)" / "Hide rooms": the text link the
/// strip is folded behind (`RoomsToggleLink`, `RoomsToggle.kt:67-87`).
///
/// A **button**, read out as expanded or collapsed — `accessibilityValue`,
/// which is where iOS puts what Android's `stateDescription` says — and
/// underlined in the brand ink, as Android draws it. ``HomeLink``'s shape:
/// a 44-point target whose frame, not its glyphs, is what a tap hits.
struct MusicRoomsToggleLink: View {

    let open: Bool
    let playingCount: Int
    let onToggle: () -> Void

    var body: some View {
        Button(action: onToggle) {
            Text(MusicRoomsToggle.label(open: open, playingCount: playingCount))
                .font(.subheadline)
                .underline()
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
                .frame(minHeight: 44, alignment: .center)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .accessibilityValue(MusicRoomsToggle.accessibilityValue(open: open))
    }
}

/// The strip's place when the now-playing read **failed** with nothing to
/// show from before it: said, with a retry, rather than drawn as a house
/// with no rooms (`MusicScreen.kt:430-437`, `ErrorState` in a card).
struct MusicRoomsUnavailableCard: View {

    let message: String
    let onRetry: () -> Void

    var body: some View {
        HomeCard(title: MusicRoomsStrings.unavailableTitle) {
            VStack(alignment: .leading, spacing: 4) {
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(MusicRoomsStrings.retry, action: onRetry)
            }
        }
    }
}

/// One room: what it is playing, how far in, and what can be done to it.
struct MusicRoomCard: View {

    let music: MusicModel
    let room: NowPlayingRoom
    let covers: MusicCoverStore

    /// This room's lyrics, or nil.
    let lyrics: MusicRoomLyricsModel?

    private var isLive: Bool { room.isPlaying }
    private var hasSong: Bool { room.song != nil && (room.isPlaying || room.isPaused) }

    var body: some View {
        HomeCard(title: room.roomID) {
            if hasSong, let song = room.song {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .top, spacing: 12) {
                        // **Only for a library track.** A room playing a
                        // stream has no `track_id` (`schemas.py:252`), so
                        // there is nothing to ask the cover route for and
                        // nothing is drawn — not a placeholder, which would
                        // claim the song has no art rather than that this
                        // app cannot know.
                        if let trackID = room.trackID {
                            MusicCoverView(trackID: trackID, size: 56, corner: 8, covers: covers)
                        }
                        identity(song)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    // The one line of lyric this room is on, timed lyrics
                    // only (`LyricsPanel.kt:701`). Nothing for a stream, and
                    // nothing when the house refuses the read.
                    if let lyrics { MusicRoomLyricLine(lyrics: lyrics) }
                    progress(song)
                    transport
                }
            } else {
                idle
            }
        }
    }

    // MARK: - playing, or paused

    private func identity(_ song: NowPlayingSong) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                ToneDot(tone: isLive ? .brand : .idle)
                Text(isLive ? MusicStrings.playingIn(room: room.roomID)
                            : MusicStrings.pausedIn(room: room.roomID))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(song.displayTitle)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            if let artist = song.artist?.trimmingCharacters(in: .whitespacesAndNewlines),
               !artist.isEmpty {
                Text(artist)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        // One sentence per card head: VoiceOver reads "playing in garage, Hey
        // Jude, The Beatles" rather than stopping at a dot.
        .accessibilityElement(children: .combine)
    }

    /// The bar and the clock.
    ///
    /// The position is ``NowPlayingRoom/elapsed(tick:)``, so it counts up
    /// between pushes and never runs past the end of the song, and the bar is
    /// `ProgressView` rather than a hand-drawn `Capsule` so that it is an
    /// element VoiceOver and the contrast audit can both see.
    @ViewBuilder
    private func progress(_ song: NowPlayingSong) -> some View {
        let at = room.elapsed(tick: music.tick) ?? 0
        VStack(alignment: .leading, spacing: 4) {
            if let total = song.durationSec, total > 0 {
                ProgressView(value: min(at, total), total: total)
                    .tint(Color.brand)
                    .accessibilityHidden(true)
            }
            Text(clock(at, song.durationSec))
                .font(.caption.monospacedDigit())
                .foregroundStyle(Color.fgMuted)
        }
    }

    private func clock(_ at: Double, _ total: Double?) -> String {
        guard let total, total > 0 else { return fmtDur(at) }
        return "\(fmtDur(at)) / \(fmtDur(total))"
    }

    /// Previous, pause-or-resume, skip, stop, favourite.
    ///
    /// Android's five, in Android's order, with two differences that are
    /// both about a phone rather than about Music:
    ///
    /// - Each is a 44pt target with `contentShape(.rect)`, Apple's minimum,
    ///   where Android's `SmallIconButton` is 32dp.
    /// - The row **wraps** at an accessibility text size instead of
    ///   squeezing, because five controls plus a clock is more than a phone's
    ///   width at AX3 and `minimumScaleFactor` is rejected throughout this
    ///   app.
    private var transport: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 4) { buttons }
            // Two rows of controls rather than one scaled-down row. A grid
            // would be neater and is wrong: `LazyVGrid` inside the screen's
            // `LazyVStack` would be a second lazy container to reason about
            // for five buttons.
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 4) { transportButtons }
                favouriteButton
            }
        }
    }

    @ViewBuilder
    private var buttons: some View {
        transportButtons
        Spacer(minLength: 0)
        favouriteButton
    }

    @ViewBuilder
    private var transportButtons: some View {
        control("backward.end", MusicTransportAction.previous.label) {
            music.control(.previous, in: room.roomID)
        }
        control(
            isLive ? "pause.fill" : "play.fill",
            isLive ? MusicTransportAction.pause.label : MusicTransportAction.resume.label
        ) {
            // ``MusicModel/togglePlayback(_:)``, not a `isLive ? … : …` here:
            // the icon and the request then read the same property, and
            // cannot disagree about which one this button is.
            music.togglePlayback(room)
        }
        control("forward.end", MusicTransportAction.skip.label) {
            music.control(.skip, in: room.roomID)
        }
        control("stop.fill", MusicTransportAction.stop.label) {
            music.control(.stop, in: room.roomID)
        }
    }

    /// Favourite what is playing — **not** the same control as a library
    /// row's star, and labelled so that it cannot be mistaken for it: the
    /// room may be playing a station or a podcast, which has no library row
    /// at all.
    private var favouriteButton: some View {
        control(
            room.favorited ? "heart.fill" : "heart",
            room.favorited ? MusicStrings.unfavoriteThis : MusicStrings.favoriteThis,
            tint: room.favorited ? Color.brandInk : Color.fgMuted
        ) {
            music.favoriteNowPlaying(in: room.roomID)
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
        .accessibilityLabel(label)
    }

    // MARK: - idle

    /// A room with nothing playing, and the one thing worth offering it.
    private var idle: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(MusicStrings.nothingPlayingIn(room: room.roomID))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            HomeLink(MusicStrings.playSomething) {
                music.playSomething(in: room.roomID)
            }
        }
    }
}
