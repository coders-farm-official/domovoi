import DomovoiKit
import SwiftUI

/// What each room is playing, **and the controls for it**.
///
/// `NowPlayingCards.kt`. The first Music batch drew this as information only
/// and said why in its own doc: a row of buttons that did nothing would have
/// been worse than no row. The buttons work now, so they are here, and the
/// paragraph that promised them is gone rather than left to rot.
///
/// ### One card per room, not a grid
///
/// Android lays these out one column on a phone and two on a wider screen
/// (`compactWidth()`). This is one column always: the iPhone app has no wide
/// layout yet anywhere, and a two-column grid that only ever renders as one
/// column is code nothing exercises.
struct MusicNowPlayingStrip: View {

    let music: MusicModel

    var body: some View {
        // Nothing at all while no room has answered: a card saying "nothing
        // playing" over a house whose satellites simply have not been read
        // yet is a claim the app cannot make. Android draws "no rooms
        // provisioned yet" here from the same empty list, which is the same
        // claim — it cannot tell an unprovisioned house from an unread one.
        if !music.rooms.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(music.rooms, id: \.roomID) { room in
                    MusicRoomCard(music: music, room: room)
                }
            }
        }
    }
}

/// One room: what it is playing, how far in, and what can be done to it.
struct MusicRoomCard: View {

    let music: MusicModel
    let room: NowPlayingRoom

    private var isLive: Bool { room.isPlaying }
    private var hasSong: Bool { room.song != nil && (room.isPlaying || room.isPaused) }

    var body: some View {
        HomeCard(title: room.roomID) {
            if hasSong, let song = room.song {
                VStack(alignment: .leading, spacing: 10) {
                    identity(song)
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
