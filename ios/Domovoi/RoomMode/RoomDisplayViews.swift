import DomovoiKit
import SwiftUI
import UIKit

// The room display's parts (`roommode/RoomDisplay.kt`): the clock, the
// voice state, the ringing card, the music, the timers and the scrolling
// column that never ends in a hard cut. What shows, and which controls, is
// decided by ``RoomDisplayModel``; these only draw it. ``RoomKioskView``
// lays them out.

/// The room screen's own dark palette, dimmer late at night
/// (`RoomDisplay.kt`, `roomColors`): text, the clock and the amber step down,
/// and the cover fades back.
struct RoomPalette {
    let text1: Color
    let text2: Color
    let clock: Color
    let dim: Color
    let amber: Color
    let amberInk: Color
    let amberSoft: Color
    let onAmber: Color
    let red: Color
    let card: Color
    let raised: Color
    let border: Color
    let artOpacity: Double

    init(night: Bool) {
        text1 = RoomInk.text1.opacity(night ? 0.55 : 1)
        text2 = RoomInk.text2.opacity(night ? 0.8 : 1)
        // The clock is the biggest thing on screen and never moves far: a
        // step below the text's white, and further down at night.
        clock = RoomInk.text1.opacity(night ? 0.45 : 0.88)
        dim = RoomInk.dim
        amber = RoomInk.amber.opacity(night ? 0.7 : 1)
        amberInk = RoomInk.amber.opacity(night ? 0.75 : 1)
        amberSoft = RoomInk.amber.opacity(0.14)
        onAmber = .black
        red = RoomInk.red
        card = RoomInk.card.opacity(night ? 0.6 : 1)
        raised = RoomInk.raised
        border = RoomInk.border
        artOpacity = night ? 0.5 : 1
    }
}

/// When the display redraws: every second while something counts down or
/// moves on (a timer, a song), else on each minute's turn — nothing moves
/// on an idle room (ROOMMODE-MAP §3.4).
struct RoomTickSchedule: TimelineSchedule {
    let everySecond: Bool

    func entries(from startDate: Date, mode: TimelineScheduleMode) -> AnyIterator<Date> {
        let step: TimeInterval = everySecond ? 1 : 60
        var next: Date? = startDate
        return AnyIterator {
            guard let now = next else { return nil }
            let t = now.timeIntervalSinceReferenceDate
            next = Date(timeIntervalSinceReferenceDate: (t / step).rounded(.down) * step + step)
            return now
        }
    }
}

/// VoiceOver's announcements for the room: the voice state politely (queued
/// behind whatever is being read), a ringing timer assertively (now).
@MainActor
enum RoomAnnouncer {
    static func polite(_ text: String) { post(text, queued: true) }
    static func assertive(_ text: String) { post(text, queued: false) }

    private static func post(_ text: String, queued: Bool) {
        guard UIAccessibility.isVoiceOverRunning else { return }
        let words = NSAttributedString(
            string: text,
            attributes: [.accessibilitySpeechQueueAnnouncement: NSNumber(value: queued)]
        )
        UIAccessibility.post(notification: .announcement, argument: words)
    }
}

/// A scrolling column that never ends in a hard cut: while there is more
/// below, its foot fades into the screen, and its content ends with that much
/// room so the last line can always be scrolled clear of the fade.
/// `centered`: content that fits sits in the middle.
struct RoomFadingScroll<Content: View>: View {
    let centered: Bool
    let content: () -> Content
    @State private var more = false

    init(centered: Bool = false, @ViewBuilder content: @escaping () -> Content) {
        self.centered = centered
        self.content = content
    }

    static var fade: CGFloat { 32 }

    var body: some View {
        GeometryReader { proxy in
            ScrollView {
                VStack(spacing: 0) { content() }
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, Self.fade)
                    .frame(minHeight: proxy.size.height, alignment: centered ? .center : .top)
            }
            .scrollIndicators(.hidden)
            .scrollBounceBehavior(.basedOnSize)
            .onScrollGeometryChange(for: Bool.self) { g in
                g.visibleRect.maxY < g.contentSize.height - 1
            } action: { _, now in
                more = now
            }
            .overlay(alignment: .bottom) {
                if more {
                    LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom)
                        .frame(height: Self.fade)
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
            }
        }
    }
}

/// The room's name, where this phone is under it ("This phone is in the
/// Kitchen"; "domovoi room" when there is no line), and the mic chip.
struct RoomTopRow: View {
    let label: String
    /// The line under the name (``RoomKioskModel/whereLine``); nil: "domovoi room".
    var whereLine: String? = nil
    let compact: Bool
    let palette: RoomPalette
    let micOpen: Bool
    let micLive: Bool
    let muted: Bool

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 0) {
                Text(label)
                    .font(.system(size: compact ? 22 : 28, weight: .semibold))
                    .foregroundStyle(palette.text1)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .accessibilityAddTraits(.isHeader)
                Text(whereLine ?? RoomModeStrings.roomBrand)
                    .font(.footnote)
                    .foregroundStyle(palette.text2)
                    .lineLimit(2)
                    .truncationMode(.tail)
            }
            Spacer(minLength: 0)
            RoomMicChip(open: micOpen, live: micLive, muted: muted)
        }
    }
}

/// The big clock with the day and date.
struct RoomClockView: View {
    let state: RoomDisplayModel.State
    let palette: RoomPalette
    let size: CGFloat

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .lastTextBaseline, spacing: 8) {
                Text(state.time)
                    .font(.system(size: size, weight: .light).monospacedDigit())
                    .foregroundStyle(palette.clock)
                    .lineLimit(1)
                    .minimumScaleFactor(0.5)
                if let amPm = state.amPm {
                    Text(amPm)
                        .font(.system(size: size * 0.28))
                        .foregroundStyle(palette.text2)
                }
            }
            Text(state.date)
                .font(.system(size: state.compact ? 18 : 22))
                .foregroundStyle(palette.text2)
                .multilineTextAlignment(.center)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("room-clock")
    }
}

/// The ring and its word, one element for VoiceOver (`room-ring`). It moves
/// only during a turn (the pulse, the thinking arc), never while idle, and
/// never when the phone is hot: a paused timeline draws nothing new.
struct RoomVoiceState: View {
    let ring: RoomStatusText.Ring
    let label: String
    let animates: Bool
    let ringSize: CGFloat
    let compact: Bool
    let palette: RoomPalette

    private var color: Color {
        switch ring {
        case .micBlocked: return palette.red
        case .muted, .offline: return palette.dim
        case .idle: return palette.amber.opacity(0.35)
        default: return palette.amber
        }
    }

    private var wordColor: Color {
        switch ring {
        case .micBlocked: return palette.red
        case .idle: return palette.text2
        default: return palette.text1
        }
    }

    var body: some View {
        HStack(spacing: 16) {
            TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !animates)) { context in
                let t = context.date.timeIntervalSinceReferenceDate
                let pulse = animates ? 0.55 + 0.45 * (0.5 + 0.5 * sin(t * .pi / 0.9)) : 1
                let turn = animates ? (t.truncatingRemainder(dividingBy: 1.4) / 1.4) * 360 : 0
                let stroke = ringSize / 12
                ZStack {
                    switch ring {
                    case .thinking:
                        Circle().stroke(color.opacity(0.2), lineWidth: stroke)
                        Circle()
                            .trim(from: 0, to: 0.25)
                            .stroke(color, lineWidth: stroke)
                            .rotationEffect(.degrees(turn))
                    case .listening, .call:
                        Circle().stroke(color.opacity(pulse), lineWidth: stroke)
                    case .speaking:
                        Circle().stroke(color, lineWidth: stroke * 1.6)
                    default:
                        Circle().stroke(color, lineWidth: stroke)
                    }
                }
                .padding(stroke * 0.8)
                .frame(width: ringSize, height: ringSize)
            }
            Text(label)
                .font(.system(size: compact ? 24 : 30))
                .foregroundStyle(wordColor)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(RoomModeStrings.stateDescription(label))
        .accessibilityAddTraits(.updatesFrequently)
        .accessibilityIdentifier("room-ring")
    }
}

/// A timer or an alarm going off: the biggest thing on the screen, with Stop.
struct RoomRingingCard: View {
    let ringing: RoomDisplayModel.Ringing
    let compact: Bool
    let palette: RoomPalette
    let onStop: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            Text(ringing.title)
                .font(.system(size: compact ? 28 : 36, weight: .semibold))
                .foregroundStyle(palette.text1)
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .accessibilityLabel(ringing.announcement)
            Text(RoomModeStrings.ringingDone)
                .font(.system(size: compact ? 18 : 22))
                .foregroundStyle(palette.amberInk)
                .accessibilityHidden(true)
            if ringing.canStop {
                Button(action: onStop) {
                    Text(RoomModeStrings.stop)
                        .font(.system(size: compact ? 26 : 32, weight: .semibold))
                        .foregroundStyle(palette.onAmber)
                        .frame(maxWidth: .infinity)
                        .frame(height: compact ? 64 : 80)
                        .background(palette.amber, in: Capsule())
                }
                .buttonStyle(.plain)
                .padding(.top, compact ? 12 : 16)
                .accessibilityLabel(ringing.stopLabel)
                .accessibilityIdentifier("room-stop-ringing")
            }
        }
        .padding(compact ? 16 : 20)
        .frame(maxWidth: .infinity)
        .background(palette.amberSoft, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(palette.amber, lineWidth: 1))
    }
}

/// This room's song, its cover when there is one, how far through, and only
/// the controls the server says work here, plus the room's volume.
struct RoomNowPlayingCard: View {
    let nowPlaying: RoomDisplayModel.NowPlaying
    let compact: Bool
    let palette: RoomPalette
    let loadArt: @MainActor (String) async -> Data?
    let onControl: (RoomControl) -> Void
    let onVolume: (Int) -> Void
    /// The cover once it has loaded: nothing is drawn before, and nothing at
    /// all when it cannot be had.
    @State private var art: UIImage?

    init(
        nowPlaying: RoomDisplayModel.NowPlaying,
        compact: Bool,
        palette: RoomPalette,
        loadArt: @escaping @MainActor (String) async -> Data?,
        onControl: @escaping (RoomControl) -> Void,
        onVolume: @escaping (Int) -> Void
    ) {
        self.nowPlaying = nowPlaying
        self.compact = compact
        self.palette = palette
        self.loadArt = loadArt
        self.onControl = onControl
        self.onVolume = onVolume
    }

    var body: some View {
        let np = nowPlaying
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 16) {
                if np.art != nil, let art {
                    RoomArtTile(image: art, size: compact ? 64 : 96, palette: palette)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(np.playing ? RoomModeStrings.nowPlaying : RoomModeStrings.paused)
                        .font(.footnote)
                        .foregroundStyle(palette.text2)
                    Text(np.title)
                        .font(.system(size: compact ? 20 : 24, weight: .semibold))
                        .foregroundStyle(palette.text1)
                        .lineLimit(2)
                    if let subtitle = np.subtitle {
                        Text(subtitle)
                            .font(.system(size: compact ? 15 : 18))
                            .foregroundStyle(palette.text2)
                            .lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(np.description)
            .accessibilityIdentifier("room-now-playing")
            if let progress = np.progress {
                GeometryReader { g in
                    ZStack(alignment: .leading) {
                        Capsule().fill(palette.border)
                        Capsule().fill(palette.amber.opacity(0.7)).frame(width: g.size.width * progress)
                    }
                }
                .frame(height: 3)
                .padding(.top, 12)
                .accessibilityHidden(true)
            }
            let transport = np.previous || np.playPause != nil || np.next
            if transport || np.volume != nil {
                // Side by side when the card is wide enough, else the volume
                // under the transport: every button keeps its full size.
                ViewThatFits(in: .horizontal) {
                    HStack {
                        transportButtons
                        Spacer(minLength: 16)
                        volumeButtons
                    }
                    VStack(spacing: 8) {
                        if transport { transportButtons }
                        volumeButtons
                    }
                    .frame(maxWidth: .infinity)
                }
                .padding(.top, 12)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity)
        .background(palette.card, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(palette.border, lineWidth: 1))
        .task(id: nowPlaying.art) {
            art = nil
            guard let path = nowPlaying.art, let data = await loadArt(path) else { return }
            // A thumbnail is all it draws, made by ImageIO from the header
            // up, never `UIImage(data:)` on the whole picture; one with an
            // extreme size is not drawn (security audit round 6, IOS-4).
            art = RoomArtImage.thumbnail(data).map { UIImage(cgImage: $0) }
        }
    }

    @ViewBuilder
    private var transportButtons: some View {
        let np = nowPlaying
        HStack(spacing: 12) {
            if np.previous {
                RoomRoundButton(symbol: "backward.fill", label: RoomModeStrings.previousSong, size: compact ? 52 : 60, palette: palette) {
                    onControl(.music(.previous))
                }
            }
            if let verb = np.playPause {
                let pause = verb == .pause
                RoomRoundButton(
                    symbol: pause ? "pause.fill" : "play.fill",
                    label: pause ? RoomModeStrings.pauseMusic : RoomModeStrings.playMusic,
                    size: compact ? 60 : 72, palette: palette, accent: true
                ) {
                    onControl(.music(verb))
                }
            }
            if np.next {
                RoomRoundButton(symbol: "forward.fill", label: RoomModeStrings.nextSong, size: compact ? 52 : 60, palette: palette) {
                    onControl(.music(.skip))
                }
            }
        }
    }

    @ViewBuilder
    private var volumeButtons: some View {
        if let v = nowPlaying.volume {
            HStack(spacing: 0) {
                RoomRoundButton(
                    symbol: "speaker.wave.1.fill", label: RoomModeStrings.quieter, size: compact ? 48 : 56,
                    palette: palette, value: RoomModeStrings.volumeValue(v)
                ) { onVolume(-1) }
                Text("\(v)")
                    .font(.system(size: 18).monospacedDigit())
                    .foregroundStyle(palette.text2)
                    .frame(minWidth: 48)
                    .accessibilityHidden(true)
                RoomRoundButton(
                    symbol: "speaker.wave.3.fill", label: RoomModeStrings.louder, size: compact ? 48 : 56,
                    palette: palette, value: RoomModeStrings.volumeValue(v)
                ) { onVolume(+1) }
            }
        }
    }
}

/// A cover from the room's server, a rounded thumbnail with a hairline.
struct RoomArtTile: View {
    let image: UIImage
    let size: CGFloat
    let palette: RoomPalette

    var body: some View {
        Image(uiImage: image)
            .resizable()
            .scaledToFill()
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(palette.border, lineWidth: 1))
            .opacity(palette.artOpacity)
            .accessibilityHidden(true)
    }
}

/// The running timers, soonest first, then the next alarm.
struct RoomTimersCard: View {
    let state: RoomDisplayModel.State
    let palette: RoomPalette

    var body: some View {
        let compact = state.compact
        VStack(alignment: .leading, spacing: 0) {
            ForEach(state.timers, id: \.id) { t in
                HStack(spacing: 12) {
                    Text(t.label)
                        .font(.system(size: compact ? 18 : 22))
                        .foregroundStyle(palette.text1)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text(t.countdown)
                        .font(.system(size: compact ? 32 : 44, weight: .light).monospacedDigit())
                        .foregroundStyle(palette.text1)
                        .lineLimit(1)
                }
                .padding(.vertical, 6)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(t.description)
                .accessibilityIdentifier("room-timer-\(t.id)")
            }
            if state.moreTimers > 0 {
                Text(RoomModeStrings.moreTimers(state.moreTimers))
                    .font(.system(size: 15))
                    .foregroundStyle(palette.text2)
                    .padding(.bottom, 6)
            }
            if let alarm = state.nextAlarm {
                HStack(spacing: 12) {
                    Image(systemName: "alarm")
                        .font(.system(size: 24))
                        .foregroundStyle(palette.text2)
                    VStack(alignment: .leading, spacing: 0) {
                        Text(RoomModeStrings.nextAlarm)
                            .font(.system(size: compact ? 14 : 16))
                            .foregroundStyle(palette.text2)
                        Text(alarm.time)
                            .font(.system(size: compact ? 22 : 28))
                            .foregroundStyle(palette.text1)
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.vertical, 8)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(alarm.description)
                .accessibilityIdentifier("room-next-alarm")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background(palette.card, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(palette.border, lineWidth: 1))
    }
}

/// A round, outlined button: nothing bright and filled that stays on screen
/// for hours.
struct RoomRoundButton: View {
    let symbol: String
    let label: String
    let size: CGFloat
    let palette: RoomPalette
    var accent = false
    var value: String? = nil
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: size * 0.36))
                .foregroundStyle(accent ? palette.amberInk : palette.text1)
                .frame(width: size, height: size)
                .background(palette.raised, in: Circle())
                .overlay(Circle().stroke(accent ? palette.amber : palette.border, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
        .accessibilityValue(value ?? "")
    }
}
