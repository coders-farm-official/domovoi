import Foundation

/// What the room display shows, decided without SwiftUI
/// (`roommode/RoomDisplayModel.kt`, `RoomDisplayModelTests`): the clock, the
/// voice state, this room's music and timers when the server tells the room
/// about them, a timer going off, and which layout. Every section that has
/// no real data behind it is nil (hidden), and every control is present only
/// when it works for this room right now.
///
/// The status line, the approval code and the big button are the kiosk's
/// (``RoomKioskModel``): an iPhone room has states an Android one does not
/// (the heat stop, the microphone permission), and they stay where they are
/// decided.
public enum RoomDisplayModel {

    public enum Layout: Equatable, Sendable { case portrait, landscape }

    public struct Input: Sendable {
        public var status: RoomStatus?
        /// The room runs on screen (``RoomKioskModel``: running and an engine up).
        public var running: Bool
        /// The server's latest `room_display`; nil: none.
        public var feed: RoomFeed?
        /// This room's output level, nil when it cannot be read.
        public var volume: Int?
        public var now: Date
        /// Monotonic ms, the clock ``RoomFeed/receivedAt`` is on.
        public var elapsedMS: Int64
        public var timeZone: TimeZone
        public var locale: Locale
        public var use24h: Bool
        /// The screen inside its safe area, in points.
        public var width: Double
        public var height: Double

        public init(
            status: RoomStatus?,
            running: Bool,
            feed: RoomFeed?,
            volume: Int?,
            now: Date,
            elapsedMS: Int64,
            timeZone: TimeZone,
            locale: Locale,
            use24h: Bool,
            width: Double,
            height: Double
        ) {
            self.status = status
            self.running = running
            self.feed = feed
            self.volume = volume
            self.now = now
            self.elapsedMS = elapsedMS
            self.timeZone = timeZone
            self.locale = locale
            self.use24h = use24h
            self.width = width
            self.height = height
        }
    }

    public struct State: Equatable, Sendable {
        public var layout: Layout
        /// A small screen (an old phone, a phone on its side): a size down.
        public var compact: Bool
        /// Late at night: everything dimmer.
        public var night: Bool
        public var time: String
        /// "PM" beside a 12-hour clock; nil on a 24-hour one.
        public var amPm: String?
        public var date: String
        public var ring: RoomStatusText.Ring
        public var voiceLabel: String
        public var ringing: Ringing?
        public var nowPlaying: NowPlaying?
        public var timers: [TimerRow]
        /// Timers past the ones shown.
        public var moreTimers: Int
        public var nextAlarm: AlarmRow?
        /// A timer is nearly done: the timers go above the music, so the
        /// soonest one is on screen without scrolling.
        public var timersFirst: Bool

        /// Anything for the second column of a landscape screen.
        public var hasCards: Bool {
            ringing != nil || nowPlaying != nil || !timers.isEmpty || nextAlarm != nil
        }

        /// Something on screen counts down or moves on: the display redraws
        /// every second while this holds, once a minute otherwise.
        public var ticks: Bool {
            ringing != nil || !timers.isEmpty || nowPlaying != nil
        }
    }

    public struct NowPlaying: Equatable, Sendable {
        public var title: String
        /// "artist · album", or what kind of thing is playing; nil: none.
        public var subtitle: String?
        public var art: String?
        public var playing: Bool
        /// The one play/pause button: `.pause` while playing, `.resume` while
        /// paused, nil when the server would not do it.
        public var playPause: RoomDisplayTransport?
        public var previous: Bool
        public var next: Bool
        /// 0-100, or nil: no volume control.
        public var volume: Int?
        /// How far through the song, 0-1; nil: unknown or a stream.
        public var progress: Double?
        /// What a screen reader hears for the card.
        public var description: String {
            (playing ? "now playing: " : "paused: ") + title + (subtitle.map { ", \($0)" } ?? "")
        }
    }

    public struct TimerRow: Equatable, Sendable {
        public var id: Int64
        public var label: String
        public var countdown: String
        public var remainingMS: Int64
        public var description: String
    }

    public struct AlarmRow: Equatable, Sendable {
        public var id: Int64
        public var time: String
        public var description: String
    }

    public struct Ringing: Equatable, Sendable {
        public var kind: RoomTimerKind
        public var title: String
        public var fireID: Int64
        public var canStop: Bool
        public var announcement: String
        /// "stop the timer", "stop the alarm".
        public var stopLabel: String { "stop the " + (kind == .alarm ? "alarm" : "timer") }
    }

    /// At most this many timers are listed; the rest are counted.
    public static let maxTimersShown = 3

    /// A timer with less than this left is the most urgent thing on the
    /// screen after a ringing one (``State/timersFirst``).
    public static let urgentTimerMS: Int64 = 10 * 60_000

    /// The volume buttons' step.
    public static let volumeStep = 10

    /// Night is 22:00 to 06:59, the phone's own time.
    public static func isNight(hour: Int) -> Bool { hour >= 22 || hour < 7 }

    public static func layout(width: Double, height: Double) -> Layout {
        width > height && width >= 560 ? .landscape : .portrait
    }

    /// An old phone is about 640 points tall: everything a size down there,
    /// so the room fits without scrolling; a phone on its side likewise.
    public static func compact(_ layout: Layout, width: Double, height: Double) -> Bool {
        switch layout {
        case .portrait: return height < 720 || width < 360
        case .landscape: return height < 520
        }
    }

    public static func build(_ i: Input) -> State {
        let layout = Self.layout(width: i.width, height: i.height)
        let cal = calendar(i.timeZone)
        let ring = RoomStatusText.ring(i.status, running: i.running)
        let linked = i.running && i.status?.link == .ready
        // What the server said is shown only while the room is connected: a
        // feed is cleared when the session ends, and this holds the line for
        // the moment between.
        let feed = linked ? i.feed : nil
        let since = feed.map { max(0, i.elapsedMS - $0.receivedAt) } ?? 0
        let all = feed?.timers ?? []
        let timers = all.filter { $0.kind == .timer }
            .map { ($0, max(0, $0.remainingMS - since)) }
            .sorted { ($0.1, $0.0.id) < ($1.1, $1.0.id) }
        let alarm = all.filter { $0.kind == .alarm }
            .map { ($0, max(0, $0.remainingMS - since)) }
            .min { ($0.1, $0.0.id) < ($1.1, $1.0.id) }
        let ringing: Ringing? = (feed?.ringing).map { r in
            let title = Self.title(r.kind, r.label)
            return Ringing(kind: r.kind, title: title, fireID: r.fireID, canStop: linked, announcement: "\(title): done")
        }
        let nextAlarm: AlarmRow? = alarm.map { pair in
            let (a, left) = pair
            let at = i.now.addingTimeInterval(Double(left) / 1000)
            let time = alarmTime(at, now: i.now, timeZone: i.timeZone, locale: i.locale, use24h: i.use24h)
            return AlarmRow(id: a.id, time: time, description: "next alarm \(time)")
        }
        return State(
            layout: layout,
            compact: compact(layout, width: i.width, height: i.height),
            night: isNight(hour: cal.component(.hour, from: i.now)),
            time: clockTime(i.now, timeZone: i.timeZone, locale: i.locale, use24h: i.use24h),
            amPm: i.use24h ? nil : format("a", i.now, i.timeZone, i.locale),
            date: dayAndDate(i.now, timeZone: i.timeZone, locale: i.locale),
            ring: ring,
            voiceLabel: RoomStatusText.label(ring),
            ringing: ringing,
            nowPlaying: (feed?.nowPlaying).map { nowPlaying($0, since: since, linked: linked, volume: i.volume) },
            timers: timers.prefix(maxTimersShown).map { pair in
                let (t, left) = pair
                let label = timerLabel(t)
                return TimerRow(
                    id: t.id, label: label, countdown: countdown(left), remainingMS: left,
                    description: "\(label), \(spokenDuration(left)) left"
                )
            },
            moreTimers: max(0, timers.count - maxTimersShown),
            nextAlarm: nextAlarm,
            timersFirst: timers.first.map { $0.1 < urgentTimerMS } ?? false
        )
    }

    private static func nowPlaying(_ np: RoomFeedNowPlaying, since: Int64, linked: Bool, volume: Int?) -> NowPlaying {
        // Something is playing with no name the server knows: the card says
        // what kind of thing it is ("radio", "music"), never a made-up title,
        // and keeps its controls (hiding it would hide Pause).
        let title = np.title ?? sourceWords(np.source) ?? "music"
        let playing = np.state == .play
        let wanted: RoomDisplayTransport = playing ? .pause : .resume
        let elapsed = np.elapsedSec.map { playing ? $0 + Double(since) / 1000 : $0 }
        var progress: Double?
        if let elapsed, let duration = np.durationSec {
            progress = min(1, max(0, elapsed / duration))
        }
        let words = [np.artist, np.album].compactMap { $0 }.joined(separator: " · ")
        return NowPlaying(
            title: title,
            subtitle: (words.isEmpty ? sourceWords(np.source) : words).flatMap { $0 == title ? nil : $0 },
            art: np.art,
            playing: playing,
            playPause: linked && np.controls.contains(wanted) ? wanted : nil,
            previous: linked && np.controls.contains(.previous),
            next: linked && np.controls.contains(.skip),
            volume: linked ? volume.map { min(100, max(0, $0)) } : nil,
            progress: progress
        )
    }

    /// Plain words for what is playing when there is no artist or album to
    /// show; nil when the words would add nothing ("library", "other").
    public static func sourceWords(_ source: RoomMediaSource?) -> String? {
        switch source {
        case .radio?: return "radio"
        case .sleep?: return "sleep sounds"
        case .podcast?: return "podcast"
        case .audiobook?: return "audiobook"
        case .library?, .other?, nil: return nil
        }
    }

    /// "pasta timer", "timer", "7 am alarm", "alarm".
    public static func timerLabel(_ t: RoomFeedTimer) -> String { title(t.kind, t.label) }

    private static func title(_ kind: RoomTimerKind, _ label: String?) -> String {
        guard let label else { return kind == .alarm ? "alarm" : "timer" }
        if kind == .timer && !label.lowercased().hasSuffix("timer") { return "\(label) timer" }
        return label
    }

    /// `4:05`, `1:02:03`: whole seconds, rounded up, so a timer shows 0:00
    /// only when it is done.
    public static func countdown(_ ms: Int64) -> String {
        let s = (max(0, ms) + 999) / 1000
        let h = Int(s / 3600)
        let m = Int(s % 3600 / 60)
        let sec = Int(s % 60)
        if h > 0 { return String(format: "%ld:%02ld:%02ld", h, m, sec) }
        return String(format: "%ld:%02ld", m, sec)
    }

    /// "4 minutes 5 seconds" for a screen reader.
    public static func spokenDuration(_ ms: Int64) -> String {
        let s = (max(0, ms) + 999) / 1000
        let h = s / 3600
        let m = s % 3600 / 60
        let sec = s % 60
        func part(_ n: Int64, _ unit: String) -> String { n == 1 ? "1 \(unit)" : "\(n) \(unit)s" }
        var parts: [String] = []
        if h > 0 { parts.append(part(h, "hour")) }
        if m > 0 { parts.append(part(m, "minute")) }
        if sec > 0 || (h == 0 && m == 0) { parts.append(part(sec, "second")) }
        return parts.joined(separator: " ")
    }

    public static func clockTime(_ t: Date, timeZone: TimeZone, locale: Locale, use24h: Bool) -> String {
        format(use24h ? "H:mm" : "h:mm", t, timeZone, locale)
    }

    /// "Monday, November 16" in the US, "Monday 16 November" elsewhere.
    public static func dayAndDate(_ t: Date, timeZone: TimeZone, locale: Locale) -> String {
        let us = (locale as NSLocale).countryCode == "US"
        return format(us ? "EEEE, MMMM d" : "EEEE d MMMM", t, timeZone, locale)
    }

    /// The alarm's time, with its day unless it is today: "7:00 AM",
    /// "tomorrow 7:00 AM", "Wed 7:00 AM", "28 Nov 7:00 AM".
    public static func alarmTime(_ at: Date, now: Date, timeZone: TimeZone, locale: Locale, use24h: Bool) -> String {
        let clock = format(use24h ? "H:mm" : "h:mm a", at, timeZone, locale)
        let cal = calendar(timeZone)
        let days = cal.dateComponents([.day], from: cal.startOfDay(for: now), to: cal.startOfDay(for: at)).day ?? 0
        if days <= 0 { return clock }
        if days == 1 { return "tomorrow \(clock)" }
        if days < 7 { return format("EEE", at, timeZone, locale) + " " + clock }
        return format("d MMM", at, timeZone, locale) + " " + clock
    }

    /// The next volume for a press of quieter (-1) or louder (+1): a step of
    /// ``volumeStep``, landing on the step, within 0-100.
    public static func stepVolume(_ level: Int, direction: Int) -> Int {
        let l = min(100, max(0, level))
        let next = direction > 0
            ? (l / volumeStep + 1) * volumeStep
            : ((l + volumeStep - 1) / volumeStep - 1) * volumeStep
        return min(100, max(0, next))
    }

    /// Whether this phone shows a 24-hour clock: the hour pattern iOS picks
    /// for the locale, which follows Settings → General → Date & Time.
    public static func uses24HourClock(_ locale: Locale) -> Bool {
        let pattern = DateFormatter.dateFormat(fromTemplate: "j", options: 0, locale: locale) ?? "h"
        return pattern.contains("H") || pattern.contains("k")
    }

    // MARK: - formatting

    private static func calendar(_ timeZone: TimeZone) -> Calendar {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        return cal
    }

    private static func format(_ pattern: String, _ t: Date, _ timeZone: TimeZone, _ locale: Locale) -> String {
        let f = DateFormatter()
        f.calendar = calendar(timeZone)
        f.locale = locale
        f.timeZone = timeZone
        f.dateFormat = pattern
        return f.string(from: t)
    }
}
