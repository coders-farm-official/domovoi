import Foundation

// The room's speaker, its clips and chime, and its gain — `roommode/RoomAudio.kt`
// (`RoomSpeaker`, `RoomClips`, `RoomVolume`; the music is RoomMusic.swift).
//
// The rules live here, platform-free, so the package tests hold them; the
// sound itself goes to a ``RoomSpeakerOutput``, which on a phone is a player
// node on the room's one `AVAudioEngine` (RoomAudioSource.swift, iOS only),
// the same engine the microphone is tapped on, so that during a drop-in
// Apple's echo canceller hears what the speaker plays (ROOMMODE-MAP §4).

// MARK: - the speaker

/// Where the speaker's PCM goes: 16-bit mono at the rate it was opened with.
public protocol RoomSpeakerOutput: AnyObject, Sendable {
    /// Ready a stream at `sampleRate` (`call`: a drop-in's 16 kHz track).
    func open(sampleRate: Int, call: Bool) throws
    /// Queue `pcm`; `played` runs once it has come out of the speaker (or
    /// once the output was closed under it).
    func schedule(_ pcm: Data, played: @escaping @Sendable () -> Void)
    /// Silence it now and drop whatever was queued.
    func close()
}

/// The speaker the engine drives (``RoomSpeaker``; a test's own).
public protocol RoomSpeaking: AnyObject, Sendable {
    /// True from ``begin(sampleRate:call:)`` until the last frame has played
    /// (or ``stop()``).
    var active: Bool { get }
    /// What runs when the speaker has played out (or was stopped while it
    /// played): when the room may listen again.
    func bind(onIdle: @escaping @Sendable () -> Void)
    func begin(sampleRate: Int, call: Bool)
    func write(_ pcm: Data)
    /// No more audio for this stream: idle once what is queued has played.
    func end()
    /// Silence it now and drop whatever was queued.
    func stop()
    func release()
}

/// The room's speaker for what the core streams: a reply's or an
/// announcement's speech (raw PCM16 mono at the rate `response_start` names)
/// and, during a drop-in, the other room's voice at 16 kHz
/// (`RoomAudio.kt:42-203`).
///
/// A reply plays from its first 150 ms (a short prebuffer against Wi-Fi
/// jitter) and is over when the output has PLAYED its last frame, not when
/// the last byte arrived: `onIdle` fires then. A backstop ends a stream whose
/// output stops reporting (an interruption took the engine): its length plus
/// two seconds after its end.
public final class RoomSpeaker: RoomSpeaking, @unchecked Sendable {

    private struct State {
        var gen = 0
        var playing = false
        var rate = RoomAudioFrame.sampleRate
        var prebufferBytes = 0
        var pending = Data()
        var started = false
        var ended = false
        var outstanding = 0
        var scheduledSamples: Int64 = 0
        var startedAtMS: Int64 = 0
        var onIdle: (@Sendable () -> Void)?
    }

    private let output: any RoomSpeakerOutput
    private let now: @Sendable () -> Int64
    private let state = RoomLock(State())
    private let queue = DispatchQueue(label: "room-speaker", qos: .userInitiated)

    public init(output: any RoomSpeakerOutput, now: @escaping @Sendable () -> Int64 = RoomClock.monotonicMS) {
        self.output = output
        self.now = now
    }

    public var active: Bool { state.withLock { $0.playing } }

    public func bind(onIdle: @escaping @Sendable () -> Void) {
        state.withLock { $0.onIdle = onIdle }
    }

    public func begin(sampleRate: Int, call: Bool) {
        let g: Int = state.withLock { s in
            s.gen += 1
            s.playing = true
            s.rate = sampleRate
            // A call plays through at once; a reply holds 150 ms first.
            s.prebufferBytes = call ? 0 : sampleRate * 2 * 150 / 1000
            s.pending = Data()
            s.started = false
            s.ended = false
            s.outstanding = 0
            s.scheduledSamples = 0
            return s.gen
        }
        queue.async { [self] in
            guard current(g) else { return }
            output.close()
            do {
                try output.open(sampleRate: sampleRate, call: call)
            } catch {
                finish(g)
            }
        }
    }

    public func write(_ pcm: Data) {
        guard let g = state.withLock({ $0.playing ? $0.gen : nil }) else { return }
        queue.async { [self] in
            let ready: Data? = state.withLock { s in
                guard s.gen == g, s.playing, !s.ended else { return nil }
                if s.started { return pcm }
                s.pending.append(pcm)
                guard s.pending.count >= s.prebufferBytes else { return nil }
                s.started = true
                s.startedAtMS = now()
                let held = s.pending
                s.pending = Data()
                return held
            }
            if let ready { schedule(ready, g) }
        }
    }

    public func end() {
        guard let g = state.withLock({ $0.playing ? $0.gen : nil }) else { return }
        queue.async { [self] in
            let held: Data? = state.withLock { s in
                guard s.gen == g, s.playing else { return nil }
                if !s.started && !s.pending.isEmpty {
                    s.started = true
                    s.startedAtMS = now()
                    let held = s.pending
                    s.pending = Data()
                    return held
                }
                return nil
            }
            if let held { schedule(held, g) }
            let (idle, backstopMS): (Bool, Int64) = state.withLock { s in
                guard s.gen == g, s.playing else { return (false, 0) }
                s.ended = true
                let lengthMS = s.scheduledSamples * 1000 / Int64(max(1, s.rate))
                let left = max(0, s.startedAtMS + lengthMS - now())
                return (s.outstanding == 0, left + 2_000)
            }
            if idle {
                finish(g)
            } else {
                queue.asyncAfter(deadline: .now() + .milliseconds(Int(backstopMS))) { [self] in finish(g) }
            }
        }
    }

    public func stop() {
        let g: Int = state.withLock { s in
            s.gen += 1
            return s.gen
        }
        queue.async { [self] in
            guard current(g) else { return }
            output.close()
            let (was, idle) = state.withLock { s -> (Bool, (@Sendable () -> Void)?) in
                let was = s.playing
                s.playing = false
                return (was, s.onIdle)
            }
            if was { idle?() }
        }
    }

    public func release() {
        stop()
        queue.async { [self] in output.close() }
    }

    private func current(_ g: Int) -> Bool { state.withLock { $0.gen == g } }

    private func schedule(_ pcm: Data, _ g: Int) {
        guard pcm.count >= 2 else { return }
        state.withLock { s in
            s.outstanding += 1
            s.scheduledSamples += Int64(pcm.count / 2)
        }
        output.schedule(pcm) { [self] in
            let done = state.withLock { s -> Bool in
                guard s.gen == g else { return false }
                s.outstanding = max(0, s.outstanding - 1)
                return s.ended && s.outstanding == 0
            }
            if done { queue.async { [self] in finish(g) } }
        }
    }

    /// The stream `g` is over: close the output, then idle.
    private func finish(_ g: Int) {
        let idle: (@Sendable () -> Void)?? = state.withLock { s in
            guard s.gen == g, s.playing else { return .none }
            s.playing = false
            return .some(s.onIdle)
        }
        guard let idle else { return }
        output.close()
        idle?()
    }
}

// MARK: - clips and the chime

/// Short sounds the room plays itself, blocking until they end (or a cap): a
/// server-rendered clip (a wake greeting, the "can't reach the server" line)
/// and the wake chime (`RoomAudio.kt:210-272`, `RoomClips`).
public protocol RoomClipPlaying: Sendable {
    /// Plays `file`; true when it played. At most `capMS`.
    func play(_ file: URL, capMS: Int64) -> Bool
    /// The two-note chime, 0.24 s. Blocking.
    func chime()
}

public enum RoomChime {
    public static let sampleRate = 16_000

    /// Two short notes, 0.24 s (the Pi's chime): 880 Hz then 1320 Hz, a
    /// 10 ms rise and a 25 ms fall, at 35 % of full scale
    /// (`RoomAudio.kt:240-248`).
    public static func pcm() -> [Int16] {
        let n = sampleRate * 240 / 1000
        return (0..<n).map { i in
            let t = Double(i) / Double(sampleRate)
            let f = i < n / 2 ? 880.0 : 1320.0
            let env = min(1.0, Double(i) / 160.0, Double(n - i) / 400.0)
            return Int16(truncatingIfNeeded: Int(sin(2 * Double.pi * f * t) * env * 0.35 * Double(Int16.max)))
        }
    }
}

/// Clips that play nothing: where there is no speaker (the package tests'
/// default, and a Mac build). A clip "did not play", so the room chimes
/// instead, and the chime is silent.
public struct RoomSilentClips: RoomClipPlaying {
    public init() {}
    public func play(_ file: URL, capMS: Int64) -> Bool { false }
    public func chime() {}
}

/// An output that has played everything the moment it is queued: where
/// there is no speaker.
public final class RoomSilentSpeakerOutput: RoomSpeakerOutput, @unchecked Sendable {
    public init() {}
    public func open(sampleRate: Int, call: Bool) throws {}
    public func schedule(_ pcm: Data, played: @escaping @Sendable () -> Void) { played() }
    public func close() {}
}

// MARK: - the room's gain

/// The room's own level, 0-100 (`set_volume`, `volume_status`;
/// ROOMMODE-MAP D6). iOS gives an app no way to set the phone's volume, so
/// the room moves its OWN gain — the engine's speech and clips, and the
/// music player — and reports that, so what the core holds is what the room
/// does. The phone's own volume buttons still work on top of it.
///
/// The curve is the square of the level (50 is a quarter of the amplitude,
/// about -12 dB), nearer to how loud a step sounds than a straight line.
public final class RoomGain: @unchecked Sendable {

    private struct State {
        var level: Int
        var sinks: [@Sendable (Float) -> Void] = []
    }

    private let state: RoomLock<State>

    public init(level: Int = 100) {
        state = RoomLock(State(level: min(100, max(0, level))))
    }

    public var level: Int { state.withLock { $0.level } }

    /// The amplitude the outputs apply, 0...1.
    public var gain: Float { Self.gain(for: level) }

    public static func gain(for level: Int) -> Float {
        let l = Float(min(100, max(0, level))) / 100
        return l * l
    }

    public func set(_ level: Int) {
        let (g, sinks) = state.withLock { s -> (Float, [@Sendable (Float) -> Void]) in
            s.level = min(100, max(0, level))
            return (Self.gain(for: s.level), s.sinks)
        }
        for sink in sinks { sink(g) }
    }

    /// `sink` hears the gain now and on every change.
    public func follow(_ sink: @escaping @Sendable (Float) -> Void) {
        let g = state.withLock { s -> Float in
            s.sinks.append(sink)
            return Self.gain(for: s.level)
        }
        sink(g)
    }
}

// MARK: - the clock

/// The room's clocks: monotonic milliseconds for every wait and gap (never
/// the wall clock, which a phone may move), and wall seconds for the health
/// frame's `ts`.
public enum RoomClock {
    public static let monotonicMS: @Sendable () -> Int64 = {
        Int64(ProcessInfo.processInfo.systemUptime * 1000)
    }

    public static let wallSeconds: @Sendable () -> Double = {
        Date().timeIntervalSince1970
    }
}
