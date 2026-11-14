import Foundation

// The listening thread — `RoomEngine.kt:693-1058`. One `Thread`, as Android:
// it owns the microphone (through ``RoomMic``, so Mute is honoured within a
// frame in every state), the ears, the wake gate and the capture machine.

/// What only the listening thread touches.
final class RoomListening {
    let run: Int
    let mic: RoomMic
    var frame = [Int16](repeating: 0, count: RoomAudioFrame.samples)
    var ears: (any RoomEars)?
    var earsGen = 0
    /// The wake gate (rebuilt when the threshold changes).
    var gate: WakeGate?
    /// The peak score of the wake that opened the next turn.
    var wakePeak: Float?
    var uttSeq = 0

    init(run: Int, mic: RoomMic) {
        self.run = run
        self.mic = mic
    }

    /// The first `n` samples of the frame.
    func slice(_ n: Int) -> [Int16] {
        n >= frame.count ? frame : Array(frame[0..<max(0, n)])
    }
}

enum RoomCaptureOutcome: Equatable { case sent, abandoned, dropped, cancelled }

extension RoomEngine {

    func listenLoop(run: Int) {
        let make = parts.pcm
        let mic = RoomMic(
            source: { [weak self] in
                let s = make()
                self?.track(s)
                return s
            },
            muted: { [weak self] in self?.muted ?? true },
            onOpenChanged: { [weak self] open in self?.update { $0.micOpen = open } }
        )
        let l = RoomListening(run: run, mic: mic)
        defer {
            mic.close()
            l.ears = nil
        }
        while isRunning(run) {
            loadEarsIfWanted(l)
            if muted {
                if mic.openAs != nil { mic.close() }
                update {
                    $0.micLive = false
                    $0.micBlocked = false
                }
                sleep(100, run: run)
                continue
            }
            let wantCall = inCall
            if mic.openAs != wantCall {
                do {
                    try mic.open(call: wantCall)
                    if mic.isOpen { update { $0.stoppedByIOS = nil } }
                } catch {
                    let stop = (error as? RoomMicFailure)?.stop
                    update {
                        if let stop {
                            $0.stoppedByIOS = stop
                            $0.micBlocked = false
                        } else {
                            $0.micBlocked = true
                        }
                    }
                    sleep(2_000, run: run)
                    continue
                }
            }
            let n = mic.read(&l.frame)
            if n == RoomMic.mutedRead { continue }
            if n <= 0 {
                // iOS took the microphone (an interruption: try again every
                // 2 s), or the source ended.
                let stop = mic.lastStop
                mic.close()
                if let stop { update { $0.stoppedByIOS = stop } }
                sleep(stop != nil ? 2_000 : 500, run: run)
                continue
            }
            noteFrame()
            let blocked = mic.blocked
            if status.micBlocked != blocked { update { $0.micBlocked = blocked } }
            do {
                if inCall {
                    if let heard = try callFrame(l, n), let ears = l.ears {
                        try callCommand(l, ears, wakeScore: heard)
                    }
                } else if let ears = l.ears {
                    if try listenFrame(l, n, ears) { try turn(l, ears, firstTrigger: "wake_word") }
                } else {
                    idleFrame()
                }
            } catch {
                // A failed step (a model that throws) must not take the room
                // down: back to listening for the wake word.
                shared.withLock { $0.captureUtt = nil }
                setPhase(.listening)
                update { $0.micLive = false }
                l.ears?.resetWake()
                sleep(200, run: run)
            }
        }
    }

    /// New models (first load, a pushed wake word): swapped here, the only
    /// thread that uses them.
    func loadEarsIfWanted(_ l: RoomListening) {
        guard let want = shared.withLock({ $0.wantModels }), want.gen != l.earsGen else { return }
        l.earsGen = want.gen
        l.ears = nil
        l.gate = nil
        do {
            l.ears = try parts.ears.load(want.files)
            update { $0.wakeReady = true }
        } catch {
            let why = (error as? OnnxModel.Failure)?.note ?? RoomIdentity.describe(error)
            update {
                $0.wakeReady = false
                $0.wakeProblem = "The wake word could not be loaded: \(why)."
            }
        }
    }

    func noteFrame() {
        let t = parts.clock()
        shared.withLock { s in
            s.lastFrameAt = t
            s.framesThisMinute += 1
        }
    }

    /// A frame with no wake word to run (models not there yet).
    func idleFrame() {
        let p: RoomPhase = parts.speaker.active ? .speaking : .listening
        if phase != p { setPhase(p) }
        maybeStartMusic()
    }

    func wakeGate(_ l: RoomListening) -> WakeGate {
        let threshold = shared.withLock { $0.wakeThreshold }
        if let g = l.gate, g.threshold == threshold { return g }
        let g = WakeGate(threshold: threshold)
        l.gate = g
        return g
    }

    /// One frame while listening for the wake word: true on a wake.
    func listenFrame(_ l: RoomListening, _ n: Int, _ ears: any RoomEars) throws -> Bool {
        if phase != .listening && phase != .speaking { setPhase(.listening) }
        // Our own voice (a reply, an announcement) does not wake us.
        if parts.speaker.active {
            if phase != .speaking { setPhase(.speaking) }
            return false
        }
        let g = wakeGate(l)
        if phase == .speaking {
            setPhase(.listening)
            ears.resetWake()
            g.reset()
        }
        // Back to listening after a turn that went wrong: no wake for the
        // cooldown, counted from here (the reply, if any, is over).
        let quiet: Bool = shared.withLock { s in
            let q = s.quietNextWake
            s.quietNextWake = false
            return q
        }
        if quiet { g.quietFor(now: parts.clock()) }
        maybeStartMusic()
        guard let score = try ears.wakeScore(l.slice(n)) else { return false }
        if g.feed(score, now: parts.clock()) {
            l.wakePeak = g.lastPeak
            return true
        }
        return false
    }

    /// A whole turn from the wake: acknowledge, capture, reply, follow-ups.
    func turn(_ l: RoomListening, _ ears: any RoomEars, firstTrigger: String) throws {
        let wokeAt = parts.clock()
        // Taken once: a wake's score is about the capture right after it.
        let wakeScore = l.wakePeak
        l.wakePeak = nil
        // Whatever run the gate was in is over with the turn.
        defer { l.gate?.reset() }
        try turnAfterWake(l, ears, firstTrigger: firstTrigger, wokeAt: wokeAt, wakeScore: wakeScore)
    }

    func turnAfterWake(_ l: RoomListening, _ ears: any RoomEars, firstTrigger: String, wokeAt: Int64, wakeScore: Float?) throws {
        shared.withLock { $0.pendingMusic = nil }
        let musicWas = parts.music.playing
        parts.music.stop()
        update { $0.musicPlaying = false }
        let sounds = self.sounds
        let clips = parts.clips
        if !ready {
            // No server: say so instead of streaming into the void.
            setPhase(.ack)
            playAndDrop(l) {
                if let clip = sounds.clip("network_issues.mp3") {
                    _ = clips.play(clip, capMS: 6_000)
                } else {
                    clips.chime()
                }
            }
            // No server: a turn that went wrong.
            shared.withLock { $0.quietNextWake = true }
            ears.resetWake()
            setPhase(.listening)
            return
        }
        setPhase(.ack)
        let greeting = RoomLock<URL?>(nil)
        playAndDrop(l) {
            let pick = musicWas ? nil : sounds.pickGreeting()
            let played = pick.map { clips.play($0, capMS: 4_000) } ?? false
            if played {
                greeting.withLock { $0 = pick }
            } else {
                clips.chime()
            }
        }
        if muted {
            // Muted during the acknowledgement: no capture.
            ears.resetWake()
            setPhase(.listening)
            return
        }
        let ack = CaptureMachine.ackFields(greetingClip: greeting.withLock { $0 }?.lastPathComponent, ackBeforeCapture: true)
        var trigger = firstTrigger
        var preSpeech: Int?
        var wakeMS: Int? = Int(parts.clock() - wokeAt)
        var first = true
        while isRunning(l.run) && !muted {
            let outcome = try capture(l, ears, trigger: trigger, ack: first ? ack : [:], wakeMS: wakeMS,
                                      preSpeech: preSpeech, wakeScore: first ? wakeScore : nil)
            first = false
            wakeMS = nil
            if outcome != .sent { break }
            if !awaitReply(l) { break }
            let followup: Bool = shared.withLock { s in
                let f = s.expectFollowup
                s.expectFollowup = false
                return f
            }
            if !followup { break }
            trigger = "followup"
            preSpeech = CaptureRules().followupPreSpeech
        }
        ears.resetWake()
        ears.resetVoice()
        setPhase(.listening)
    }

    /// Plays `sound` off this thread while this thread reads and DROPS the
    /// mic, then 210 ms more: nothing said over the acknowledgement is sent.
    func playAndDrop(_ l: RoomListening, _ sound: @escaping @Sendable () -> Void) {
        let done = RoomOneShot<Bool>()
        clipQueue.async {
            sound()
            done.complete(true)
        }
        let deadline = parts.clock() + 8_000
        while !done.isCompleted && parts.clock() < deadline && isRunning(l.run) {
            if l.mic.read(&l.frame) <= 0 { Thread.sleep(forTimeInterval: 0.03) }
        }
        for _ in 0..<Self.ackTailFrames { _ = l.mic.read(&l.frame) }
    }

    func capture(
        _ l: RoomListening,
        _ ears: any RoomEars,
        trigger: String,
        ack: [String: RoomJSON],
        wakeMS: Int?,
        preSpeech: Int?,
        wakeScore: Float? = nil
    ) throws -> RoomCaptureOutcome {
        l.uttSeq += 1
        let utt = l.uttSeq
        let hints = features.contains("speech_pause")
        let m = CaptureMachine(utt: utt, rules: CaptureRules(), hints: hints, preSpeechLimit: preSpeech)
        let started = parts.clock()
        let clockNow = parts.clock
        func clock() -> [String: Int] { ["sat_ms": Int(clockNow() - started), "backlog_ms": 0] }
        shared.withLock { s in
            s.captureMark = s.responseStarts
            s.captureUtt = utt
            s.endCaptureFor = nil
            s.responseEndSeen = false
            s.expectFollowup = false
        }
        ears.resetVoice()
        if muted {
            shared.withLock { $0.captureUtt = nil }
            return .cancelled
        }
        guard send(RoomProtocol.utteranceStart(trigger: trigger, utt: utt, backlogMS: 0, wakeMS: wakeMS, wakeScore: wakeScore)) else {
            shared.withLock { $0.captureUtt = nil }
            return .dropped
        }
        setPhase(.capturing)
        update { $0.micLive = true }
        defer { update { $0.micLive = false } }
        while isRunning(l.run) {
            if shared.withLock({ $0.endCaptureFor }) == utt {
                m.endNow(.server)
                break
            }
            let n = l.mic.read(&l.frame)
            if n == RoomMic.mutedRead {
                // Muted mid-command: the turn is CANCELLED, never sent for
                // transcription (no utterance_end). The core drops the
                // half-heard audio at the next utterance_start.
                shared.withLock { $0.captureUtt = nil }
                return .cancelled
            }
            if n <= 0 {
                // iOS took the microphone mid-command: a turn that went wrong.
                shared.withLock { $0.quietNextWake = true }
                m.endNow(.shutdown)
                break
            }
            noteFrame()
            let speech = try ears.isSpeech(l.slice(n))
            let step = m.onFrame(isSpeech: speech)
            if step.resumeBefore { send(m.resumePayload(frame: step.resumeFrame, clock: clock())) }
            if step.sendAudio && !sendAudio(l.frame, n) {
                shared.withLock { $0.captureUtt = nil }
                return .dropped
            }
            if step.pauseAfter { send(m.pausePayload(ack: ack, clock: clock())) }
            if step.abandoned {
                shared.withLock { $0.captureUtt = nil }
                return .abandoned
            }
            if step.end != nil { break }
        }
        // Speech that never paused (media, most likely): the next wake waits
        // out the cooldown.
        if m.ended == .maxLength { shared.withLock { $0.quietNextWake = true } }
        let afterEnd = (m.ended == .server && m.speaking) ? try listenAfterEnd(l, ears, m) : [:]
        shared.withLock { $0.captureUtt = nil }
        setPhase(.thinking)
        return send(m.endPayload(ack: ack, clock: clock(), afterEnd: afterEnd)) ? .sent : .dropped
    }

    /// After the core ended the capture: listen on, sending nothing, for as
    /// long as this phone would have, to report speech that came after.
    func listenAfterEnd(_ l: RoomListening, _ ears: any RoomEars, _ m: CaptureMachine) throws -> [String: Int] {
        let budget = m.listenAfterEndBudget()
        var listened = 0
        while listened < budget && isRunning(l.run) {
            let (starts, mark) = shared.withLock { ($0.responseStarts, $0.captureMark) }
            if starts != mark && parts.speaker.active { break }
            if l.mic.read(&l.frame) <= 0 { break }
            listened += 1
            if try ears.isSpeech(l.frame) {
                let ms = listened * RoomAudioFrame.milliseconds
                return ["voiced_after_end_ms": ms, "listened_after_end_ms": ms]
            }
        }
        return ["voiced_after_end_ms": 0, "listened_after_end_ms": listened * RoomAudioFrame.milliseconds]
    }

    /// Waits for the reply (reading and dropping the mic meanwhile): true
    /// when it ended normally.
    func awaitReply(_ l: RoomListening) -> Bool {
        let deadline = parts.clock() + Self.replyTimeoutMS
        while isRunning(l.run) && parts.clock() < deadline {
            let (endSeen, isReady, call) = shared.withLock { ($0.responseEndSeen, $0.ready, $0.inCall) }
            let speaking = parts.speaker.active
            if endSeen && (!speaking || call) { return true }
            if !isReady { return false }
            if speaking && phase != .speaking { setPhase(.speaking) }
            if l.mic.read(&l.frame) <= 0 { Thread.sleep(forTimeInterval: 0.03) }
        }
        // No reply: the next wake waits out the cooldown.
        if isRunning(l.run) { shared.withLock { $0.quietNextWake = true } }
        return false
    }

    /// One frame of a drop-in: relayed as it is. The wake word's score when
    /// it was heard ("hey jarvis, hang up"), else nil. One chunk over the
    /// threshold is enough here, as on a Pi: the wake gate's patience and
    /// cooldown are the main wake loop's only.
    func callFrame(_ l: RoomListening, _ n: Int) throws -> Float? {
        if phase != .call { setPhase(.call) }
        update { if !$0.micLive { $0.micLive = true } }
        sendAudio(l.frame, n)
        guard let ears = l.ears, let score = try ears.wakeScore(l.slice(n)) else { return nil }
        let threshold = shared.withLock { $0.wakeThreshold }
        return score > threshold ? score : nil
    }

    /// A command in the middle of a call: framed as a turn, so the core
    /// transcribes it instead of relaying it; the call goes on after.
    func callCommand(_ l: RoomListening, _ ears: any RoomEars, wakeScore: Float) throws {
        let outcome = try capture(l, ears, trigger: "wake_word", ack: CaptureMachine.ackFields(greetingClip: nil, ackBeforeCapture: false),
                                  wakeMS: 0, preSpeech: nil, wakeScore: wakeScore)
        if outcome == .sent { _ = awaitReply(l) }
        ears.resetWake()
        setPhase(inCall ? .call : .listening)
    }
}
