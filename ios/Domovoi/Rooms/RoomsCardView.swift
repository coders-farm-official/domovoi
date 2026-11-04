import DomovoiKit
import SwiftUI

/// One room's card — `SatCard` (`SatellitesScreen.kt:191`).
///
/// Three lines of fact and no controls: the dot with the room's id and its
/// status pill; what it is playing, how far in, and the clock; and the footer,
/// which is the Wi-Fi link and when the room was last heard from.
///
/// ### The card is the link's label, not a button of its own (D56)
///
/// The whole card opens the room, which is Android's behaviour too (`:195`) —
/// there is nothing else on it to tap, so a separate `open` control would be
/// a second way to do one thing. What changed with D56 is **who** draws the
/// control: a room is now a pushed screen, so ``RoomsView`` wraps this in a
/// `NavigationLink(value:)` and the card itself is just the label. A `Button`
/// here as well would be a button inside a button — two overlapping targets,
/// and two elements in the accessibility tree answering to one name, which is
/// exactly what sends a test's tap to the wrong one.
///
/// The combining and the `.isButton` trait stay here, on the label, so
/// VoiceOver still reads the room and its state as one thing to open.
///
/// ### `last_seen_at`, not `last_connected_at`
///
/// The footer reads ``roomsLastSeenText(_:nowMs:)``, which prefers
/// `last_seen_at`. Android reads `last_connected_at` here
/// (`SatellitesScreen.kt:290`) and the schema says in so many words that that
/// field is **not** when the satellite was last seen — with the symptom
/// recorded: "an offline card read `19d ago` on 2026-10-14 for rooms heard
/// minutes before the core froze" (`web/backend/schemas.py:518-528`). This is
/// the one place the port deliberately does not copy the Kotlin.
struct RoomsCardView: View {

    let room: RoomsSatellite

    /// ``RoomsModel/extraSec`` — the ticker's extrapolation, so the clock
    /// moves between reads without drifting away from the server's reading.
    let extraSec: Double

    /// The phone's clock, for `2h ago`. Passed in rather than read here so
    /// that every row of one frame says the same time.
    let nowMs: Int64

    @Environment(\.dynamicTypeSize) private var typeSize

    private var elapsed: Double { roomsElapsedSec(room.nowPlaying, extraSec: extraSec) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            identity
            song
            footer
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(.rect)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
        // One element, so VoiceOver reads the room and its state as a thing
        // to open rather than as five separate labels in a button.
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }

    /// The dot, the id, and the one pill.
    ///
    /// The id and not ``RoomsSatellite`` ever having a label: on this screen
    /// the id **is** the name, which is what Android draws (`:216`) and what
    /// every announce toast says back.
    private var identity: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            // `live:` is Android's pulse, which nothing in this app animates
            // yet (``ToneDot``). The pill beside it says the word.
            ToneDot(tone: room.online ? .ok : .idle, live: room.online)
            Text(room.roomID)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            HomeTonePill(
                label: room.online ? RoomsStrings.online : RoomsStrings.offline,
                tone: room.online ? .ok : .idle
            )
            Spacer(minLength: 0)
        }
    }

    /// `<title> · <artist>`, the bar, and `1:12 / 3:40` — or the two words
    /// (`SatellitesScreen.kt:259-280`).
    ///
    /// ``roomsPlaying(_:)`` and never "is there a song": a stopped room keeps
    /// its last track loaded and the roster still names it, which is the
    /// 2026-10-11 bug where an idle room looked like it was playing at 0:00.
    @ViewBuilder
    private var song: some View {
        if roomsPlaying(room.nowPlaying) {
            VStack(alignment: .leading, spacing: 4) {
                Text(songLine)
                    .font(.footnote)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                HStack(spacing: 8) {
                    ProgressView(
                        value: roomsProgress(
                            elapsed: elapsed,
                            duration: room.nowPlaying?.song?.durationSec
                        )
                    )
                    .progressViewStyle(.linear)
                    .frame(height: bar)
                    .tint(Color.brand)
                    .accessibilityHidden(true)
                    // Monospaced for the countdown's reason: a proportional
                    // digit changes width as it counts, so the line twitches
                    // once a second.
                    Text(
                        roomsSongClock(
                            elapsed: elapsed,
                            duration: room.nowPlaying?.song?.durationSec
                        )
                    )
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else {
            Text(RoomsStrings.noMusic)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The progress bar's height, scaled: three points at the ordinary size is
    /// a hair at AX5 beside 48-point text.
    @ScaledMetric(relativeTo: .caption) private var bar: CGFloat = 3

    private var songLine: String {
        let title = roomsSongTitle(room.nowPlaying)
        guard let artist = room.nowPlaying?.song?.artist, !artist.isEmpty else { return title }
        return title + " · " + artist
    }

    /// The Wi-Fi glyph with its link text, and when the room was last heard
    /// from (`SatellitesScreen.kt:284-296`).
    ///
    /// **A row that becomes a column at an accessibility size**: two readings
    /// and a glyph do not share a phone's width at AX5, and
    /// `minimumScaleFactor` is rejected throughout this app.
    @ViewBuilder
    private var footer: some View {
        let link = roomsWifiLinkText(room.wifi)
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) {
                wifi(link)
                lastSeen
            }
        } else {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                wifi(link)
                Spacer(minLength: 8)
                lastSeen
            }
        }
    }

    private func wifi(_ link: (main: String, sub: String?)) -> some View {
        HStack(spacing: 4) {
            // The glyph is never the only carrier: the reading is beside it,
            // so the image is hidden and the label reads as the words.
            Image(systemName: room.wifi?.mode == "ap" ? "wifi.exclamationmark" : "wifi")
                // `.caption`, the same text style as the reading beside it,
                // so the glyph is sized by Dynamic Type rather than by a
                // point count. Nothing in this target sets a fixed font size
                // and this is not the file that starts.
                .font(.caption)
                .accessibilityHidden(true)
            Text(link.sub.map { "\(link.main) · \($0)" } ?? link.main)
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(roomsWifiTone(room.wifi).inkColour)
        .accessibilityElement(children: .combine)
    }

    private var lastSeen: some View {
        Text(roomsLastSeenText(room, nowMs: nowMs))
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}
