import Foundation

/// The phone's full-duplex voice audio: the microphone up as 16 kHz mono
/// PCM16 frames, and the room's relayed audio and chimes down, played as they
/// arrive.
///
/// This is the layer D54 asked to be **shared**: the chat mic and room mode
/// want the same capture — the same rate, the same frame size, the same echo
/// cancellation — so the protocol is written for all three and only the
/// drop-in call uses it today. What a later caller adds is a second consumer,
/// not a second copy of this.
///
/// ## The protocol, and why there is one
///
/// `AVAudioEngine` cannot be exercised where this package's tests run — the
/// nodes exist on macOS but `AVAudioSession` does not, and a Simulator is the
/// only place a microphone answers at all. So the engine is behind
/// ``DropInAudio`` and ``RecordingDropInAudio`` is what `swift test` drives:
/// every decision about *when* audio starts, what a frame is and who holds
/// the microphone is testable, and the part that cannot be tested here is the
/// part with nothing left to decide. ``URLSessionStateSocketTransport`` is
/// split the same way, for the same reason.
///
/// ## The audio **session** is not this layer's
///
/// `AVAudioSession` is process-wide and `@MainActor` in this package
/// (``PlayerAudioSessionControl``), while the capture callback runs on
/// whatever thread CoreAudio hands it. Mixing the two in one type means a
/// main-actor hop inside an audio callback, or an `assumeIsolated` that traps
/// the first time a teardown runs off the main actor. So ``DropInCallModel``
/// activates ``PlayerAudioCategory/recording`` and puts the category back,
/// and this layer only drives the engine. The order matters and the model
/// keeps it: voice processing needs an **active** `.playAndRecord` session
/// before the nodes are asked for it.
///
/// ## What is not here, and why there is no control for it
///
/// * **No level meter and no waveform.** Android's dialog draws neither
///   (`PhoneDropinCall.kt:129-198`), and a meter is a tap on the capture path
///   that would have to be right about silence to be worth drawing.
/// * **No route picker.** Android sets `isSpeakerphoneOn = true` and offers
///   no way back (`DropinCallClient.kt:159`); here the same thing is
///   `.defaultToSpeaker` on ``PlayerAudioCategory/recording``, and iOS's own
///   route picker is in Control Centre. A picker of ours would have to claim
///   it could move audio to a device the session may refuse.
public protocol DropInAudio: AnyObject, Sendable {

    /// 16 kHz mono PCM16, in 30 ms frames of ``DropInAudioFormat/frameBytes``
    /// — Android's `frameBytes = 960`, "matching the Pi"
    /// (`DropinCallClient.kt:67`). The closure is called off the main actor,
    /// from whatever thread the capture callback runs on.
    ///
    /// Throws ``DropInAudioFailed`` when the microphone cannot be opened at
    /// all, which is a sentence the screen shows.
    func start(onFrame: @escaping @Sendable (Data) -> Void) throws

    /// Play `pcm` — 16 kHz mono PCM16, as it came off the socket. The server
    /// sends the room's relayed microphone **and** the connected and
    /// disconnected chimes down the same binary channel
    /// (`domovoi/phone_dropin.py:33-34`), so there is nothing to tell apart.
    func play(_ pcm: Data)

    /// Android drops frames while muted rather than sending silence
    /// (`DropinCallClient.kt:205`): the microphone stays open, and nothing
    /// goes up. Same here — a muted call that stopped the engine would have
    /// to re-negotiate the session to come back.
    func setMuted(_ muted: Bool)

    /// Close the microphone and the speaker. Idempotent, as
    /// ``PlayerMicLease/release()`` is and for the same reason: it is called
    /// from a teardown that may already have run.
    func stop()
}

/// The numbers the wire fixes, in one place.
public enum DropInAudioFormat {

    /// `DropinCallClient.kt:66`, and the `audio_sample_rate` the server
    /// states in both `phone-info` (`satellites.py:1553`) and `dropin_start`
    /// (`phone_dropin.py:30`).
    public static let sampleRate: Double = 16_000

    /// 30 ms of 16 kHz mono PCM16 (`DropinCallClient.kt:67`). The bridge
    /// relays verbatim, so this is the Pi's frame and not ours to choose.
    public static let frameBytes = 960
}

/// Cut a stream of captured bytes into whole ``DropInAudioFormat/frameBytes``
/// frames, keeping the remainder for the next call.
///
/// Its own type because this is the one piece of the capture path with a
/// decision in it, and the decision is easy to get wrong in a way nothing
/// notices. A callback that sent its buffer as it came would send frames of
/// whatever size the hardware chose — 1024 samples at 48 kHz is 341⅓ samples
/// at 16 kHz, so most buffers are not a whole number of frames — and the Pi,
/// which relays verbatim, would play the seams. Android never meets this
/// because `AudioRecord.read` is asked for exactly 960 bytes;
/// `AVAudioEngine` hands over what it likes.
public struct DropInFrameCutter: Sendable {

    private var held = Data()

    public init() {}

    /// Append `chunk` and take every whole frame it completes.
    public mutating func take(_ chunk: Data) -> [Data] {
        held.append(chunk)
        var out: [Data] = []
        while held.count >= DropInAudioFormat.frameBytes {
            out.append(Data(held.prefix(DropInAudioFormat.frameBytes)))
            held = Data(held.dropFirst(DropInAudioFormat.frameBytes))
        }
        return out
    }

    /// Bytes held back because they do not fill a frame. Read by the tests.
    public var pending: Int { held.count }

    public mutating func reset() { held = Data() }
}

/// The microphone would not open. One sentence, because that is all a screen
/// can usefully say about it.
public struct DropInAudioFailed: Error, Equatable, Sendable {
    public let note: String
    public init(note: String) { self.note = note }
}

// ---------------------------------------------------------------------------
// The engine
// ---------------------------------------------------------------------------

#if os(iOS)
import AVFoundation

/// The real one: `AVAudioEngine` with Apple's voice processing on, which is
/// what makes the phone's claim of full duplex true.
///
/// ### Where the echo cancellation comes from
///
/// Android gets it twice over — `MediaRecorder.AudioSource.VOICE_COMMUNICATION`
/// picks the hardware-cancelled input, and `AcousticEchoCanceler` is attached
/// to the record session as well (`DropinCallClient.kt:190-196`). That file's
/// header says what it is for: "voice-communication source = hardware AEC,
/// which is what lets the phone claim full-duplex". iOS's counterpart is
/// `setVoiceProcessingEnabled(true)` on the engine's input **and** output
/// nodes: one switch for the pair, which must be set before the engine starts
/// and needs an active `.playAndRecord` session (the model's, above).
///
/// The server trusts this: a phone peer goes straight into
/// `satellite_full_duplex`, because "phones have hardware AEC via the
/// voice-communication audio path, which is the entire meaning of that flag"
/// (`domovoi/phone_dropin.py:14-16`). So an engine that failed to turn voice
/// processing on would make the room talk to its own echo, and
/// ``start(onFrame:)`` **throws** rather than going ahead without it.
public final class DropInAudioEngine: DropInAudio, @unchecked Sendable {

    /// `@unchecked Sendable` with the reason the house rule asks for: every
    /// field below is read and written only under ``lock``, `AVAudioEngine`
    /// and its nodes are documented safe to drive from one thread at a time,
    /// and the capture callback touches nothing else.
    private let lock = NSLock()
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var cutter = DropInFrameCutter()
    private var muted = false
    private var running = false
    private var converter: AVAudioConverter?

    /// The format the socket speaks: 16 kHz mono **Int16**, interleaved.
    private let wire = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: DropInAudioFormat.sampleRate,
        channels: 1,
        interleaved: true
    )

    public init() {}

    public func start(onFrame: @escaping @Sendable (Data) -> Void) throws {
        lock.lock()
        defer { lock.unlock() }
        guard !running else { return }
        guard let wire else { throw DropInAudioFailed(note: DropInStrings.micInUse) }

        let input = engine.inputNode
        let output = engine.outputNode
        do {
            // Both calls, because Apple's guidance is that the input and the
            // output must agree: the output's is what gives the canceller the
            // reference signal it subtracts.
            try input.setVoiceProcessingEnabled(true)
            try output.setVoiceProcessingEnabled(true)
        } catch {
            throw DropInAudioFailed(note: DropInStrings.micInUse)
        }

        // Read the format AFTER voice processing is on: turning it on changes
        // the node's format, and a converter built from the format before
        // would convert from a layout the tap no longer delivers.
        let captured = input.outputFormat(forBus: 0)
        guard captured.sampleRate > 0, captured.channelCount > 0,
              let converter = AVAudioConverter(from: captured, to: wire)
        else { throw DropInAudioFailed(note: DropInStrings.micInUse) }
        self.converter = converter

        engine.attach(player)
        // Connected in the wire's own format: `AVAudioEngine` inserts the
        // conversion to the mixer's rate itself, which is the one part of
        // this file it does better than we would.
        engine.connect(player, to: engine.mainMixerNode, format: wire)

        input.installTap(onBus: 0, bufferSize: 2048, format: captured) { [weak self] buffer, _ in
            guard let frames = self?.captured(buffer, into: wire) else { return }
            for frame in frames { onFrame(frame) }
        }
        do {
            engine.prepare()
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            engine.detach(player)
            self.converter = nil
            throw DropInAudioFailed(note: DropInStrings.micInUse)
        }
        player.play()
        running = true
    }

    /// One captured buffer, converted and cut into whole frames.
    ///
    /// Nil while muted: Android drops the frames rather than sending silence
    /// (`DropinCallClient.kt:205`), and the held remainder goes with them so
    /// that unmuting cannot send a frame half of which is from before the
    /// mute.
    private func captured(_ buffer: AVAudioPCMBuffer, into wire: AVAudioFormat) -> [Data]? {
        lock.lock()
        defer { lock.unlock() }
        guard running else { return nil }
        if muted {
            cutter.reset()
            return nil
        }
        guard let converter else { return nil }
        let ratio = wire.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
        guard let out = AVAudioPCMBuffer(pcmFormat: wire, frameCapacity: capacity) else { return nil }
        // One buffer in, one out: the block is asked again when the converter
        // wants more, and `.noDataNow` is how it is told there is no more of
        // this buffer rather than that the stream has ended.
        var handed = false
        var failure: NSError?
        converter.convert(to: out, error: &failure) { _, status in
            if handed {
                status.pointee = .noDataNow
                return nil
            }
            handed = true
            status.pointee = .haveData
            return buffer
        }
        guard failure == nil, out.frameLength > 0, let channel = out.int16ChannelData else { return nil }
        let chunk = Data(bytes: channel[0], count: Int(out.frameLength) * 2)
        return cutter.take(chunk)
    }

    public func play(_ pcm: Data) {
        lock.lock()
        let live = running
        lock.unlock()
        guard live, let wire, pcm.count >= 2 else { return }
        let frames = AVAudioFrameCount(pcm.count / 2)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: wire, frameCapacity: frames),
              let channel = buffer.int16ChannelData
        else { return }
        buffer.frameLength = frames
        pcm.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { return }
            channel[0].update(
                from: base.assumingMemoryBound(to: Int16.self),
                count: Int(frames)
            )
        }
        player.scheduleBuffer(buffer, completionHandler: nil)
    }

    public func setMuted(_ muted: Bool) {
        lock.lock()
        self.muted = muted
        if muted { cutter.reset() }
        lock.unlock()
    }

    public func stop() {
        lock.lock()
        guard running else {
            lock.unlock()
            return
        }
        running = false
        cutter.reset()
        converter = nil
        lock.unlock()
        engine.inputNode.removeTap(onBus: 0)
        player.stop()
        engine.stop()
        engine.detach(player)
    }
}
#endif

/// What the package's tests drive: it records what it was asked for, hands
/// frames over on demand, and refuses only when told to.
public final class RecordingDropInAudio: DropInAudio, @unchecked Sendable {

    private struct State {
        var starts = 0
        var stops = 0
        var muted: [Bool] = []
        var played: [Data] = []
        var refuse: DropInAudioFailed?
        var sink: (@Sendable (Data) -> Void)?
    }

    private let lock = NSLock()
    private var state: State

    public init(refusing: DropInAudioFailed? = nil) {
        state = State(refuse: refusing)
    }

    public var starts: Int { lock.withLock { state.starts } }
    public var stops: Int { lock.withLock { state.stops } }
    public var muteCalls: [Bool] { lock.withLock { state.muted } }
    public var played: [Data] { lock.withLock { state.played } }

    /// Whether the microphone is open now — a start nothing has stopped.
    public var open: Bool { lock.withLock { state.starts > state.stops } }

    public func start(onFrame: @escaping @Sendable (Data) -> Void) throws {
        let refusal: DropInAudioFailed? = lock.withLock {
            if let refuse = state.refuse { return refuse }
            state.starts += 1
            state.sink = onFrame
            return nil
        }
        if let refusal { throw refusal }
    }

    public func play(_ pcm: Data) { lock.withLock { state.played.append(pcm) } }

    public func setMuted(_ muted: Bool) { lock.withLock { state.muted.append(muted) } }

    public func stop() {
        lock.withLock {
            guard state.starts > state.stops else { return }
            state.stops += 1
            state.sink = nil
        }
    }

    /// Pretend the microphone produced `frame`.
    public func feed(_ frame: Data) {
        let sink = lock.withLock { state.sink }
        sink?(frame)
    }
}
