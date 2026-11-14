#if os(iOS)
import AVFoundation
import Foundation

/// The room's one `AVAudioEngine`, for listening and speaking
/// (ROOMMODE-MAP §4): the microphone's tap (16 kHz mono PCM16, raw while
/// listening, voice processing on during a drop-in), the player node for the
/// core's speech, the player node for clips and the chime, and the room's
/// gain on the main mixer. One engine, because Apple's echo canceller only
/// subtracts what the SAME engine plays: a drop-in's audio on any other
/// player would howl.
///
/// **The session.** ``activate()`` makes it `.playAndRecord`, mode
/// `.default`, `.defaultToSpeaker` — `PlayerAudioCategory.room`'s settings,
/// applied here rather than through `AVAudioSessionControl` because the
/// engine starts from whatever thread `RoomEngine.start()` runs on and that
/// class is main-actor-bound. A drop-in moves the mode to `.voiceChat`;
/// leaving it moves it back. ``deactivate()`` gives the session back with
/// `.notifyOthersOnDeactivation`.
///
/// **The microphone indicator.** Once an engine has used its input node its
/// I/O unit records for as long as it lives, and iOS's orange dot stays on.
/// So when the room stops wanting the microphone (Mute, leaving a call, a
/// stop) the graph builds a fresh engine without input — at once when
/// nothing is playing, else when the speech and the clip have finished.
///
/// **Interruptions** (a call, Siri, an alarm): `.began` stops the source
/// (its `read` answers negative, ``stoppedBy`` is `.interrupted`), the
/// listening loop closes the microphone and tries again every 2 s;
/// `.ended` with `.shouldResume` reactivates the session and clears it;
/// without it the retries go on until another app lets go. A media-services
/// reset rebuilds the engine and its nodes, `.mediaServicesReset` until the
/// next open. Route changes are left alone: `.defaultToSpeaker` keeps the
/// speaker.
///
/// `@unchecked Sendable`: every field is behind ``lock`` (recursive, because
/// a rebuild calls back into the helpers that take it), and the tap block
/// touches only the current source, which has its own lock.
public final class RoomAudioGraph: @unchecked Sendable {

    public static let shared = RoomAudioGraph()

    private let lock = NSRecursiveLock()
    private var engine = AVAudioEngine()
    private var speech = AVAudioPlayerNode()
    private var clips = AVAudioPlayerNode()
    private var speechFormat: AVAudioFormat?
    private var clipFormat: AVAudioFormat?
    private var inputLive = false
    private var inputUsed = false
    private var voiceProcessing = false
    private var sessionActive = false
    private var speechBusy = false
    private var clipBusy = false
    private var gain: Float = 1
    private var source: RoomAudioSource?
    private var interrupted = false
    private var observing = false
    private var reason: RoomMicStop?

    private init() {
        attachPlayers()
    }

    private func locked<T>(_ body: () throws -> T) rethrows -> T {
        lock.lock()
        defer { lock.unlock() }
        return try body()
    }

    /// Why iOS has the microphone now, or nil.
    public var stoppedBy: RoomMicStop? { locked { reason } }

    // MARK: - the session

    /// The room's session, active. False when iOS refused.
    @discardableResult
    public func activate() -> Bool {
        locked {
            observe()
            let session = AVAudioSession.sharedInstance()
            do {
                try session.setCategory(.playAndRecord, mode: voiceProcessing ? .voiceChat : .default, options: [.defaultToSpeaker])
                try session.setActive(true)
                sessionActive = true
                return true
            } catch {
                return false
            }
        }
    }

    /// Stops everything and gives the session back.
    public func deactivate() {
        locked {
            source?.halt(nil)
            source = nil
            inputLive = false
            speechBusy = false
            clipBusy = false
            engine.stop()
            rebuild(keepInput: false)
            voiceProcessing = false
            sessionActive = false
            try? AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
        }
    }

    private func observe() {
        guard !observing else { return }
        observing = true
        let center = NotificationCenter.default
        center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: nil) { [weak self] note in
            let raw = (note.userInfo?[AVAudioSessionInterruptionTypeKey] as? NSNumber)?.uintValue ?? 0
            let options = (note.userInfo?[AVAudioSessionInterruptionOptionKey] as? NSNumber)?.uintValue ?? 0
            self?.interruption(began: raw == AVAudioSession.InterruptionType.began.rawValue,
                               resume: AVAudioSession.InterruptionOptions(rawValue: options).contains(.shouldResume))
        }
        center.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: nil, queue: nil) { [weak self] _ in
            self?.mediaServicesReset()
        }
    }

    private func interruption(began: Bool, resume: Bool) {
        locked {
            if began {
                interrupted = true
                reason = .interrupted
                source?.halt(.interrupted)
                source = nil
                inputLive = false
                speechBusy = false
                clipBusy = false
                return
            }
            interrupted = false
            if resume {
                if (try? AVAudioSession.sharedInstance().setActive(true)) != nil {
                    sessionActive = true
                    reason = nil
                }
            }
            // Without "should resume" the listening loop's retries try the
            // session again (``startInput``) until another app lets go.
        }
    }

    private func mediaServicesReset() {
        locked {
            reason = .mediaServicesReset
            source?.halt(.mediaServicesReset)
            source = nil
            inputLive = false
            speechBusy = false
            clipBusy = false
            // Everything the old daemon gave us is gone: a new engine, new
            // nodes, and the session set up again.
            engine = AVAudioEngine()
            speech = AVAudioPlayerNode()
            clips = AVAudioPlayerNode()
            speechFormat = nil
            clipFormat = nil
            inputUsed = false
            voiceProcessing = false
            attachPlayers()
            sessionActive = false
            _ = activate()
        }
    }

    // MARK: - the engine

    private func attachPlayers() {
        engine.attach(speech)
        engine.attach(clips)
        engine.mainMixerNode.outputVolume = gain
    }

    /// A fresh engine (no input unless `keepInput`), the players moved over.
    private func rebuild(keepInput: Bool) {
        // A stream still playing keeps its format on the new speech node.
        let keepSpeech = speechBusy ? speechFormat : nil
        engine.stop()
        speech.stop()
        clips.stop()
        engine.detach(speech)
        engine.detach(clips)
        engine = AVAudioEngine()
        speech = AVAudioPlayerNode()
        clips = AVAudioPlayerNode()
        speechFormat = nil
        clipFormat = nil
        inputUsed = false
        if !keepInput { voiceProcessing = false }
        attachPlayers()
        if let format = keepSpeech {
            engine.connect(speech, to: engine.mainMixerNode, format: format)
            speechFormat = format
        }
    }

    private func ensureRunning() throws {
        if !sessionActive, !activate() {
            throw RoomMicFailure(stop: interrupted ? .interrupted : nil, note: "the audio session would not start")
        }
        if !engine.isRunning {
            engine.prepare()
            try engine.start()
        }
    }

    /// When the microphone is no longer wanted and nothing is playing, drop
    /// the engine that used it (the orange dot goes out).
    private func settle() {
        guard inputUsed, !inputLive, !speechBusy, !clipBusy else { return }
        rebuild(keepInput: false)
        if voiceProcessing == false, sessionActive {
            try? AVAudioSession.sharedInstance().setMode(.default)
        }
    }

    public func setGain(_ g: Float) {
        locked {
            gain = g
            engine.mainMixerNode.outputVolume = g
        }
    }

    // MARK: - the microphone

    func startInput(call: Bool, for owner: RoomAudioSource) throws {
        try locked {
            if Self.permissionDenied() {
                throw RoomMicFailure(stop: .permissionDenied, note: "the microphone is turned off for Domovoi in Settings")
            }
            if interrupted {
                // An interruption that ended without "should resume": try
                // the session again; another app may still hold it.
                guard (try? AVAudioSession.sharedInstance().setActive(true)) != nil else {
                    throw RoomMicFailure(stop: .interrupted, note: "another app or a call has the microphone")
                }
                interrupted = false
                sessionActive = true
            }
            source?.halt(nil)
            source = nil
            if inputLive { engine.inputNode.removeTap(onBus: 0) }
            inputLive = false
            if call != voiceProcessing {
                // Voice processing is set before the engine starts, on input
                // AND output (the output's is the canceller's reference); a
                // change means a fresh engine.
                rebuild(keepInput: true)
                voiceProcessing = call
                try? AVAudioSession.sharedInstance().setMode(call ? .voiceChat : .default)
            } else {
                engine.stop()
            }
            if !sessionActive, !activate() {
                throw RoomMicFailure(stop: .interrupted, note: "the audio session would not start")
            }
            let input = engine.inputNode
            inputUsed = true
            if call {
                do {
                    try input.setVoiceProcessingEnabled(true)
                    try engine.outputNode.setVoiceProcessingEnabled(true)
                } catch {
                    throw RoomMicFailure(stop: nil, note: "the echo canceller would not start")
                }
                if #available(iOS 17.0, *) {
                    // The room's own audio is the call; nothing else to duck.
                    input.voiceProcessingOtherAudioDuckingConfiguration =
                        AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
                }
            }
            // Read AFTER voice processing: turning it on changes the format.
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0,
                  let wire = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: Double(RoomAudioFrame.sampleRate), channels: 1, interleaved: true),
                  let converter = AVAudioConverter(from: format, to: wire)
            else {
                throw RoomMicFailure(stop: nil, note: "the microphone has no usable format")
            }
            owner.converting(with: converter, to: wire)
            input.installTap(onBus: 0, bufferSize: 4_096, format: format) { buffer, _ in
                owner.tapped(buffer)
            }
            inputLive = true
            source = owner
            do {
                try ensureRunning()
                if speechFormat != nil, speechBusy { speech.play() }
            } catch {
                input.removeTap(onBus: 0)
                inputLive = false
                source = nil
                if let failure = error as? RoomMicFailure { throw failure }
                throw RoomMicFailure(stop: interrupted ? .interrupted : nil, note: "the microphone would not start")
            }
            reason = nil
        }
    }

    func stopInput(for owner: RoomAudioSource) {
        locked {
            guard source === owner else { return }
            source = nil
            if inputLive { engine.inputNode.removeTap(onBus: 0) }
            inputLive = false
            settle()
        }
    }

    static func convert(_ buffer: AVAudioPCMBuffer, converter: AVAudioConverter, wire: AVAudioFormat) -> [Int16] {
        let ratio = wire.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1_024
        guard let out = AVAudioPCMBuffer(pcmFormat: wire, frameCapacity: capacity) else { return [] }
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
        guard failure == nil, out.frameLength > 0, let channel = out.int16ChannelData else { return [] }
        return Array(UnsafeBufferPointer(start: channel[0], count: Int(out.frameLength)))
    }

    private static func permissionDenied() -> Bool {
        if #available(iOS 17.0, *) {
            return AVAudioApplication.shared.recordPermission == .denied
        }
        return AVAudioSession.sharedInstance().recordPermission == .denied
    }

    // MARK: - the speech

    func openSpeech(sampleRate: Int) throws {
        try locked {
            guard let format = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: Double(sampleRate), channels: 1, interleaved: true) else {
                throw RoomMicFailure(stop: nil, note: "no speech format at \(sampleRate) Hz")
            }
            speech.stop()
            if speechFormat != format {
                engine.disconnectNodeOutput(speech)
                engine.connect(speech, to: engine.mainMixerNode, format: format)
                speechFormat = format
            }
            speechBusy = true
            try ensureRunning()
            speech.play()
        }
    }

    func scheduleSpeech(_ pcm: Data, played: @escaping @Sendable () -> Void) {
        locked {
            guard let format = speechFormat, speechBusy, engine.isRunning else {
                played()
                return
            }
            let frames = AVAudioFrameCount(pcm.count / 2)
            guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
                  let channel = buffer.int16ChannelData
            else {
                played()
                return
            }
            buffer.frameLength = frames
            pcm.withUnsafeBytes { raw in
                guard let base = raw.baseAddress else { return }
                channel[0].update(from: base.assumingMemoryBound(to: Int16.self), count: Int(frames))
            }
            speech.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { _ in played() }
            if !speech.isPlaying { speech.play() }
        }
    }

    func closeSpeech() {
        locked {
            speech.stop()
            speechBusy = false
            settle()
        }
    }

    // MARK: - clips

    /// Plays `file` on the clip node, blocking until it ends or `capMS`.
    func playClip(_ file: URL, capMS: Int64) -> Bool {
        let done = DispatchSemaphore(value: 0)
        let started: Bool = locked {
            guard let audio = try? AVAudioFile(forReading: file) else { return false }
            do {
                try connectClips(audio.processingFormat)
                clipBusy = true
                try ensureRunning()
                clips.scheduleFile(audio, at: nil, completionCallbackType: .dataPlayedBack) { _ in done.signal() }
                clips.play()
                return true
            } catch {
                clipBusy = false
                return false
            }
        }
        guard started else { return false }
        _ = done.wait(timeout: .now() + .milliseconds(Int(capMS)))
        endClip()
        return true
    }

    /// The two-note chime on the clip node. Blocking.
    func playChime() {
        let pcm = RoomChime.pcm()
        let done = DispatchSemaphore(value: 0)
        let started: Bool = locked {
            guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(RoomChime.sampleRate), channels: 1, interleaved: false),
                  let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(pcm.count)),
                  let channel = buffer.floatChannelData
            else { return false }
            buffer.frameLength = AVAudioFrameCount(pcm.count)
            for (i, s) in pcm.enumerated() { channel[0][i] = Float(s) / 32_768 }
            do {
                try connectClips(format)
                clipBusy = true
                try ensureRunning()
                clips.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { _ in done.signal() }
                clips.play()
                return true
            } catch {
                clipBusy = false
                return false
            }
        }
        guard started else { return }
        _ = done.wait(timeout: .now() + .milliseconds(600))
        endClip()
    }

    private func connectClips(_ format: AVAudioFormat) throws {
        clips.stop()
        if clipFormat != format {
            engine.disconnectNodeOutput(clips)
            engine.connect(clips, to: engine.mainMixerNode, format: format)
            clipFormat = format
        }
    }

    private func endClip() {
        locked {
            clips.stop()
            clipBusy = false
            settle()
        }
    }
}

/// The room's microphone on the shared ``RoomAudioGraph`` (ROOMMODE-MAP §4,
/// §6.3): what `RoomMic` opens on a phone. `start(call:)` taps the input,
/// raw for listening or with voice processing for a call; the tap's 16 kHz
/// mono PCM16 goes into a two-second ring (Android's `bufferSeconds`, so a
/// slow wake-word step on an old phone does not lose audio) that ``read(into:)``
/// takes from, blocking.
public final class RoomAudioSource: RoomPCMSource, RoomPCMSourceStopReporting, @unchecked Sendable {

    /// What R3 hands `RoomEngine.live(pcm:)` on a phone.
    public static let making: RoomPCMSourceMaking = { RoomAudioSource() }

    static let capacity = RoomAudioFrame.sampleRate * 2

    private let graph: RoomAudioGraph
    private let cond = NSCondition()
    private var ring: [Int16] = []
    private var open = false
    private var why: RoomMicStop?

    public init(graph: RoomAudioGraph = .shared) {
        self.graph = graph
    }

    public var stoppedBy: RoomMicStop? {
        cond.lock()
        defer { cond.unlock() }
        return why
    }

    public func start(call: Bool) throws {
        cond.lock()
        ring.removeAll(keepingCapacity: true)
        open = true
        why = nil
        cond.unlock()
        do {
            try graph.startInput(call: call, for: self)
        } catch {
            cond.lock()
            open = false
            cond.unlock()
            throw error
        }
    }

    public func read(into frame: inout [Int16]) -> Int {
        cond.lock()
        defer { cond.unlock() }
        while open && ring.count < frame.count {
            // Wakes on every pushed buffer; a timeout so a tap that never
            // comes (iOS stopped the engine without telling us) ends the read.
            if !cond.wait(until: Date().addingTimeInterval(2)) && ring.count < frame.count {
                open = false
                why = graph.stoppedBy
                return -1
            }
        }
        guard open else { return -1 }
        let n = min(frame.count, ring.count)
        for i in 0..<n { frame[i] = ring[i] }
        ring.removeFirst(n)
        return n
    }

    public func stop() {
        cond.lock()
        let was = open
        open = false
        cond.broadcast()
        cond.unlock()
        if was { graph.stopInput(for: self) }
    }

    private let conversion = RoomLock<(converter: AVAudioConverter, wire: AVAudioFormat)?>(nil)

    /// The graph's converter for this source's tap (set before the tap).
    func converting(with converter: AVAudioConverter, to wire: AVAudioFormat) {
        conversion.withLock { $0 = (converter, wire) }
    }

    /// One tapped buffer: converted to the wire's 16 kHz mono PCM16.
    func tapped(_ buffer: AVAudioPCMBuffer) {
        let samples: [Int16] = conversion.withLock { c in
            guard let c else { return [] }
            return RoomAudioGraph.convert(buffer, converter: c.converter, wire: c.wire)
        }
        push(samples)
    }

    /// Samples into the ring.
    func push(_ samples: [Int16]) {
        guard !samples.isEmpty else { return }
        cond.lock()
        if open {
            ring.append(contentsOf: samples)
            if ring.count > Self.capacity { ring.removeFirst(ring.count - Self.capacity) }
            cond.broadcast()
        }
        cond.unlock()
    }

    /// iOS took the microphone (or the graph moved on): reads end.
    func halt(_ reason: RoomMicStop?) {
        cond.lock()
        open = false
        why = reason
        cond.broadcast()
        cond.unlock()
    }
}

/// The speaker's output on the graph's speech node.
public final class RoomGraphSpeakerOutput: RoomSpeakerOutput, @unchecked Sendable {
    private let graph: RoomAudioGraph

    public init(graph: RoomAudioGraph = .shared) {
        self.graph = graph
    }

    public func open(sampleRate: Int, call: Bool) throws { try graph.openSpeech(sampleRate: sampleRate) }
    public func schedule(_ pcm: Data, played: @escaping @Sendable () -> Void) { graph.scheduleSpeech(pcm, played: played) }
    public func close() { graph.closeSpeech() }
}

/// Clips and the chime on the graph's clip node.
public struct RoomGraphClips: RoomClipPlaying {
    private let graph: RoomAudioGraph

    public init(graph: RoomAudioGraph = .shared) {
        self.graph = graph
    }

    public func play(_ file: URL, capMS: Int64) -> Bool { graph.playClip(file, capMS: capMS) }
    public func chime() { graph.playChime() }
}
#endif
