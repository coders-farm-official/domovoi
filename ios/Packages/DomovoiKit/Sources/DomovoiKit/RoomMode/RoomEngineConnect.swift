import Foundation

// The connection and the socket's frames — `RoomEngine.kt:270-691`.

extension RoomEngine {

    // MARK: - the connect loop (one thread)

    func connectLoop(_ enrol: RoomEnrolment, run: Int) {
        var backoff: Int64 = 1_000
        var lastNudge = shared.withLock { $0.checkNow }
        let loopback = Self.isLoopback(enrol.coreURL)
        while isRunning(run) {
            // A loopback core needs no Wi-Fi: only a UI-test launch can be
            // enrolled to one (R3's DEBUG flag; the parser refuses it).
            if !loopback && !wifiUp() {
                update { $0.link = .offline(reason: "waiting for Wi-Fi") }
                sleep(5_000, run: run) { [self] in wifiUp() }
                continue
            }
            // While parked or refused, the screen keeps saying so (and the
            // code) through each retry.
            update { s in
                switch s.link {
                case .awaitingApproval, .rejected, .conflict: break
                default: s.link = .connecting
                }
            }
            // This round dials one address, and the proof is asked for that
            // address (``RoomIdentity/dialBase(_:resolve:)``).
            let round: RoomEnrolment
            do {
                let dialed = try RoomIdentity.dialBase(enrol.coreURL, resolve: parts.resolve)
                round = RoomEnrolment(
                    coreURL: dialed, fingerprint: enrol.fingerprint,
                    roomID: enrol.roomID, roomLabel: enrol.roomLabel,
                    inRoom: enrol.inRoom, inRoomName: enrol.inRoomName
                )
                _ = try RoomIdentity.verifyServer(httpBase: round.coreURL, fingerprint: round.fingerprint) { url in
                    let body = try parts.http.get(url, limit: 64 * 1024)
                    return String(decoding: body, as: UTF8.self)
                }
            } catch {
                let why = (error as? RoomIdentity.Failure)?.message ?? "the server did not answer"
                update { $0.link = .offline(reason: why) }
                sleep(backoff, run: run)
                backoff = min(60_000, backoff * 2)
                continue
            }
            guard isRunning(run) else { break }
            syncModels(round)
            guard isRunning(run) else { break }
            let result = session(round, run: run)
            guard isRunning(run) else { break }
            switch result {
            case "ready":
                backoff = 1_000
                let closed = shared.withLock { $0.closed }
                _ = closed?.wait(while: { [self] in isRunning(run) })
                guard isRunning(run) else { break }
                // Closed here to say a new hello (``maybeRehello()``): dial
                // again at once.
                if takeRehello() {
                    update { $0.link = .connecting }
                    continue
                }
                update { $0.link = .offline(reason: "the connection to the server dropped") }
                sleep(backoff, run: run)
            case "awaiting_approval", "pairing_rejected", "approval_conflict", "approval_throttled", "satellite_id_mismatch":
                // Parked or refused: ask again later, or at once on "check now".
                lastNudge = waitOrNudge(RoomSessionRules.retryAfterMS(result, backoff: backoff), last: lastNudge, run: run)
            case "approval_code_invalid":
                store.renewApprovalCode()
                sleep(1_000, run: run)
            default:
                update { $0.link = .offline(reason: "the server closed the connection (\(result))") }
                sleep(backoff, run: run)
                backoff = min(30_000, backoff * 2)
            }
        }
    }

    func wifiUp() -> Bool {
        shared.withLock { $0.wifiUp } || parts.wifi.available
    }

    static func isLoopback(_ coreURL: String) -> Bool {
        guard let host = URL(string: coreURL)?.host?.lowercased() else { return false }
        return host == "127.0.0.1" || host == "localhost" || host == "::1" || host == "[::1]"
    }

    /// Waits `ms`, or until "check now" — but never retries within 10 s of
    /// the last attempt (the core's per-source approval budget).
    func waitOrNudge(_ ms: Int64, last: Int, run: Int) -> Int {
        let start = parts.clock()
        sleep(ms, run: run) { [self] in shared.withLock { $0.checkNow } != last }
        let since = parts.clock() - start
        if since < Self.nudgeFloorMS { sleep(Self.nudgeFloorMS - since, run: run) }
        return shared.withLock { $0.checkNow }
    }

    /// One socket: "ready" (then `closed` completes at its end) or the
    /// refusal / close reason.
    func session(_ enrol: RoomEnrolment, run: Int) -> String {
        guard let token = store.pairingToken() else { return "no_key" }
        let outcome = RoomOneShot<String>()
        let closed = RoomOneShot<Bool>()
        let wordsMax = parts.wakeWordsMax
        let (gen, word, words) = shared.withLock { s -> (Int, String, [String]) in
            s.sessionGen += 1
            s.outcome = outcome
            s.closed = closed
            s.rehello = false
            // What the room listens for (1.4.0): the words loaded or about
            // to load, else the main word.
            let said = Self.reportedWords(s)
            s.helloWords = said
            return (s.sessionGen, s.wakeWord, said)
        }
        let hello = RoomProtocol.text(RoomProtocol.hello(
            roomID: enrol.roomID, wakeWord: word, pairingToken: token,
            approvalCode: store.approvalCode, echoCanceller: parts.echoCanceller,
            wakeWords: words, wakeWordsMax: wordsMax,
            // The room the room code asked to join (`in=`), until approved.
            requestedRoom: store.requestedRoom
        ))
        // The hello goes the moment the socket opens, whichever of "opened"
        // and "open returned" comes first.
        let box = RoomLock<(socket: (any RoomSocket)?, opened: Bool)>((nil, false))
        let events = RoomSocketEvents(
            opened: {
                let s = box.withLock { b -> (any RoomSocket)? in
                    b.opened = true
                    return b.socket
                }
                s?.sendText(hello)
            },
            text: { [weak self] text in
                guard let self, self.current(gen) else { return }
                self.onText(text)
            },
            binary: { [weak self] data in
                guard let self, self.current(gen) else { return }
                self.onAudio(data)
            },
            failed: { [weak self] why in
                if let self, self.current(gen) { self.sessionEnded() }
                outcome.complete("failed: \(why)")
                closed.complete(true)
            },
            closed: { [weak self] in
                if let self, self.current(gen) { self.sessionEnded() }
                outcome.complete("closed")
                closed.complete(true)
            }
        )
        let socket = parts.transport.open(RoomProtocol.streamURL(coreURL: enrol.coreURL, roomID: enrol.roomID), events: events)
        let sendNow = box.withLock { b -> Bool in
            b.socket = socket
            return b.opened
        }
        if sendNow { socket.sendText(hello) }
        let stale: Bool = shared.withLock { s in
            guard s.running, s.run == run else { return true }
            s.socket = socket
            return false
        }
        if stale {
            socket.close()
            return "stopped"
        }
        return outcome.wait(while: { [self] in isRunning(run) }) ?? "stopped"
    }

    /// Frames from session `gen` only: a late frame from a socket that has
    /// been replaced is dropped.
    func current(_ gen: Int) -> Bool {
        shared.withLock { $0.sessionGen == gen }
    }

    // MARK: - the core's frames

    func onText(_ text: String) {
        switch RoomProtocol.parse(text) {
        case let .ready(features, _, _, roomVoice, _, room):
            onReady(features: features, roomVoice: roomVoice, room: room)
        case let .room(place):
            onRoom(place)
        case let .error(reason, _, code):
            onError(reason: reason, code: code)
        case .transcript, .pong, .other:
            break
        case let .responseStart(_, rate):
            onResponseStart(rate)
        case let .responseEnd(interrupted, followup, ignored):
            onResponseEnd(interrupted: interrupted, expectFollowup: followup, ignored: ignored)
        case let .endCapture(utt):
            shared.withLock { s in
                if let utt, utt == s.captureUtt { s.endCaptureFor = utt }
            }
        case let .setVolume(level):
            parts.gain.set(level)
            send(RoomProtocol.volumeStatus(level: parts.gain.level))
        case let .musicStart(url):
            onMusicStart(url)
        case .musicStop:
            shared.withLock { $0.pendingMusic = nil }
            parts.music.stop()
            update { $0.musicPlaying = false }
        case let .soundsChanged(voice, saysVoice):
            if saysVoice { shared.withLock { $0.roomVoice = voice } }
            worker.async { [self] in syncSounds() }
        case let .setWakeWord(slug, threshold, words):
            worker.async { [self] in switchWakeWord(slug, threshold: threshold, words: words) }
        case .wakeModelsChanged:
            worker.async { [self] in refreshModels() }
        case let .dropinStart(peerRoom, peerLabel, rate, fullDuplex, awaitReady):
            onDropinStart(peerRoom: peerRoom, peerLabel: peerLabel, sampleRate: rate, fullDuplex: fullDuplex, awaitReady: awaitReady)
        case .dropinEnd:
            onDropinEnd()
        case let .display(feed):
            onDisplay(feed)
        }
    }

    func onReady(features: Set<String>, roomVoice: String?, room: RoomPlace? = nil) {
        store.setApprovedOnce()
        // Accepted: the server decides the room from now on, so the room
        // code's `in=` is asked for no more.
        store.clearRequestedRoom()
        if let room {
            store.setRoomName(room.name)
            update { $0.place = room }
        }
        let outcome: RoomOneShot<String>? = shared.withLock { s in
            s.features = features
            s.roomVoice = roomVoice
            s.ready = true
            s.readyAt = parts.clock()
            s.reconnects += 1
            return s.outcome
        }
        update { $0.link = .ready }
        outcome?.complete("ready")
        send(RoomProtocol.volumeStatus(level: parts.gain.level))
        sendConfigStatus()
        worker.async { [self] in syncSounds() }
        // The core hears the battery now, not at the end of the first minute.
        batteryReporter.reset()
        let device = parts.device
        healthQueue.async { [self] in onBattery(device.battery()) }
    }

    /// The `room` frame (1.5.0): an admin moved this phone to another room,
    /// or the room's wake claims changed. The phone stays connected and
    /// carries on in the frame's room: its name on the screen, the rest
    /// (wake words, voice, music, the room display) the server sends as for
    /// any room. Only a hello that named `satellite_id` gets this frame.
    func onRoom(_ place: RoomPlace?) {
        guard let place else { return }
        store.setRoomName(place.name)
        update { $0.place = place }
    }

    func onError(reason: String?, code: String?) {
        let v = RoomSessionRules.verdict(reason: reason, code: code, current: store.approvalCode, approvedBefore: store.approvedOnce)
        if let code = v.code { store.setApprovalCode(code) }
        if let link = v.link { update { $0.link = link } }
        if let result = v.outcome {
            shared.withLock { $0.outcome }?.complete(result)
            return
        }
        // Any other error during a turn ends it, as a Pi treats it, so the
        // listening thread never waits for a reply that is not coming — and
        // the next wake waits out the cooldown.
        shared.withLock { s in
            s.quietNextWake = true
            s.acceptingAudio = false
            s.responseEndSeen = true
            s.expectFollowup = false
        }
        parts.speaker.stop()
    }

    func onResponseStart(_ rate: Int) {
        shared.withLock { s in
            s.responseStarts += 1
            s.pendingMusic = nil
            s.callTrack = false
            s.acceptingAudio = true
            s.responseEndSeen = false
        }
        parts.music.stop()
        parts.speaker.begin(sampleRate: rate, call: false)
        update { $0.musicPlaying = false }
    }

    func onResponseEnd(interrupted: Bool, expectFollowup: Bool, ignored: Bool) {
        // The end of a reply from before the capture now open (an
        // announcement the person spoke over): its audio is over, this
        // turn's wait for its own reply is not (the Pi's `_turn_mark`).
        let earlier: Bool = shared.withLock { s in
            if s.phase == .capturing && s.responseStarts == s.captureMark {
                s.acceptingAudio = false
                return true
            }
            s.acceptingAudio = false
            // Taken for background talk: the next wake waits out the cooldown.
            if ignored { s.quietNextWake = true }
            s.expectFollowup = expectFollowup && !interrupted
            s.responseEndSeen = true
            return false
        }
        _ = earlier
        parts.speaker.end()
    }

    /// The core's audio: a reply's speech while one is on, else the other
    /// room's voice during a drop-in once the call's track is open.
    func onAudio(_ bytes: Data) {
        let take = shared.withLock { $0.acceptingAudio || ($0.inCall && $0.callTrack) }
        if take { parts.speaker.write(bytes) }
    }

    func onMusicStart(_ url: String?) {
        guard RoomProtocol.isStreamURL(url), let url else {
            if let url, features.contains("music_failed") {
                send(RoomProtocol.musicFailed(streamURL: url, reason: "bad_url", attempts: 0))
            }
            return
        }
        // The app's cleartext rule holds for the room's music too: plain
        // http only to a home-network host.
        if let u = URL(string: url), (try? RoomNetwork.checkCleartext(u)) == nil {
            if features.contains("music_failed") {
                send(RoomProtocol.musicFailed(streamURL: url, reason: "not_home_network", attempts: 0))
            }
            return
        }
        shared.withLock { $0.pendingMusic = url }
        maybeStartMusic()
    }

    /// A held `music_start` plays once the room is idle: no turn, no speech.
    func maybeStartMusic() {
        let speakerActive = parts.speaker.active
        let url: String? = shared.withLock { s in
            guard let url = s.pendingMusic, s.phase == .listening, !speakerActive, !s.inCall else { return nil }
            s.pendingMusic = nil
            return url
        }
        guard let url else { return }
        parts.music.start(url)
        update { $0.musicPlaying = true }
    }

    func onSpeakerIdle() {
        // A reply in the middle of a call has played out: back to the call.
        if inCall {
            parts.speaker.begin(sampleRate: RoomProtocol.sampleRate, call: true)
            shared.withLock { $0.callTrack = true }
            return
        }
        maybeStartMusic()
    }

    func onDropinStart(peerRoom: String?, peerLabel: String?, sampleRate: Int, fullDuplex: Bool, awaitReady: Bool) {
        // Never open the mic-while-playing on a phone without an echo
        // canceller, whatever the core says (the Pi's defence in depth).
        guard Self.acceptsDropin(echoCanceller: parts.echoCanceller, fullDuplex: fullDuplex) else {
            dropin.cancel()
            send(RoomProtocol.dropinEnd)
            return
        }
        // With `await_ready` the reply announcing the call plays out first
        // (at most 8 s), then the call opens and `dropin_ready` goes out;
        // without it the call opens now.
        dropin.start(awaitReady: awaitReady) { [weak self] in
            self?.enterCall(peer: peerLabel ?? peerRoom, sampleRate: sampleRate)
        }
    }

    /// Opens the call (under the drop-in's lock). The listening thread
    /// reopens the microphone with voice processing when it sees `inCall`.
    func enterCall(peer: String?, sampleRate: Int) {
        shared.withLock { s in
            s.pendingMusic = nil
            s.callPeer = peer
            s.inCall = true
        }
        parts.music.stop()
        // A reply still playing ("dropping in…") finishes first; the call's
        // track opens when it has (`onSpeakerIdle`).
        if !parts.speaker.active {
            parts.speaker.begin(sampleRate: sampleRate, call: true)
            shared.withLock { $0.callTrack = true }
        }
        update {
            $0.phase = .call
            $0.peerRoom = peer
            $0.musicPlaying = false
        }
        shared.withLock { $0.phase = .call }
    }

    func onDropinEnd() {
        let left: Bool = dropin.exclusive {
            // A call still waiting for its announcement is dropped too.
            dropin.cancel()
            guard inCall else { return false }
            leaveCall()
            return true
        }
        guard left else { return }
        // The core's "disconnected" chime came just before this frame.
        parts.speaker.end()
        update { $0.peerRoom = nil }
    }

    func leaveCall() {
        shared.withLock { s in
            s.inCall = false
            s.callTrack = false
            s.callPeer = nil
        }
        // The call's relay is over: the mic is no longer going anywhere.
        update { $0.micLive = false }
    }

    func sessionEnded() {
        let closed: RoomOneShot<Bool>? = shared.withLock { s in
            s.socket = nil
            s.ready = false
            s.features = []
            s.downSince = parts.clock()
            s.acceptingAudio = false
            s.responseEndSeen = true
            s.expectFollowup = false
            return s.closed
        }
        parts.speaker.stop()
        dropin.exclusive {
            // A call still waiting for its announcement is gone with the session.
            dropin.cancel()
            if inCall { leaveCall() }
        }
        update { $0.peerRoom = nil }
        // What the server said about this room is not known any more.
        displayChannel.update { _ in nil }
        closed?.complete(true)
    }

    /// The room's wake settings, so what the core holds for it is what it does.
    func sendConfigStatus() {
        let threshold = shared.withLock { $0.wakeThreshold }
        send(RoomProtocol.configStatus(threshold: threshold, patienceFrames: WakeGate.patienceDefault, cooldownSec: WakeGate.cooldownSecDefault))
    }

    // MARK: - models and sounds

    func syncModels(_ enrol: RoomEnrolment) {
        let words = configuredWakeWords()
        switch models.sync(http: parts.http, coreURL: enrol.coreURL, fingerprint: enrol.fingerprint, wakeWords: words) {
        case .ready(let files):
            let wakeReady = status.wakeReady
            shared.withLock { s in
                // Any change of word or of the words with it reloads.
                if s.wantModels?.files != files || !wakeReady {
                    s.modelsGen += 1
                    s.wantModels = (s.modelsGen, files)
                }
            }
            update { $0.wakeProblem = nil }
        case .missing(let names):
            update {
                $0.wakeProblem = "The server has no wake-word models yet (\(names.joined(separator: ", "))). " +
                    "On the dashboard: Settings → Rooms → Prepare a room's SD card → Refresh caches."
            }
        case .failed(let reason):
            let why = reason.hasPrefix("404 ")
                ? "The server does not hand wake-word models to phones yet: update the Domovoi server."
                : "Wake-word models: \(reason)"
            let haveModels = shared.withLock { $0.wantModels != nil }
            if !haveModels && !status.wakeReady { update { $0.wakeProblem = why } }
        }
    }

    func refreshModels() {
        guard running, let enrol = store.enrolment else { return }
        syncModels(enrol)
    }

    /// A pushed wake word. A push with no usable threshold keeps the room's
    /// (the store's rule, ``RoomWakeSettings/thresholdValue(_:)``); either
    /// way the core hears the threshold the room now wakes at.
    ///
    /// `words` (1.4.0): the room's whole list, `slug` first. The room keeps
    /// at most its own limit of them (``RoomEngineParts/wakeWordsMax``),
    /// whatever arrives, each with its own threshold; without `words` it
    /// runs `slug` alone and drops the extras it had. The models sync, the
    /// listening thread loads them on one front end, and the room then says
    /// a new hello listing what loaded (``maybeRehello()``).
    func switchWakeWord(_ slug: String?, threshold: Double?, words: [RoomWakeWord]? = nil) {
        guard let slug, RoomModels.isSlug(slug) else { return }
        // A list without a top-level threshold: the main word's own, if any.
        var mainThreshold = threshold
        if mainThreshold == nil, let first = words?.first, first.slug == slug, let own = first.threshold {
            mainThreshold = Double(own)
        }
        store.setWakeWord(slug, threshold: mainThreshold)
        let t = store.wakeThreshold
        let list = RoomWakeWords.pushed(slug: slug, threshold: nil, words: words, max: parts.wakeWordsMax)
        store.setWakeWords(list)
        let extras = Array(list.dropFirst())
        shared.withLock { s in
            s.wakeWord = slug
            s.wakeThreshold = t
            s.wakeExtras = extras
        }
        sendConfigStatus()
        refreshModels()
    }

    /// The words that loaded are not what this session's hello said (a
    /// push changed them, or an extra word's model would not load): close
    /// the session so the connect loop dials again with a hello listing
    /// them. Only between turns, never in a call or while the room speaks;
    /// the listening thread asks every frame, so it happens at the first
    /// quiet moment. Each session's hello is checked once it is ready.
    func maybeRehello() {
        if parts.speaker.active { return }
        let target: (socket: any RoomSocket, gen: Int)? = shared.withLock { s in
            guard s.ready, let socket = s.socket, let said = s.helloWords, !s.rehello,
                  !s.inCall, s.phase == .listening, s.captureUtt == nil,
                  let loaded = s.loadedWakeWords, loaded != said,
                  // Nothing newer waiting to load: the next hello says `loaded`.
                  s.wantModels.map({ $0.gen == s.loadedGen }) ?? true
            else { return nil }
            s.rehello = true
            return (socket, s.sessionGen)
        }
        guard let found = target else { return }
        found.socket.close()
        if current(found.gen) { sessionEnded() }
    }

    /// Whether the session just closed was closed for a new hello (and
    /// forget it).
    func takeRehello() -> Bool {
        shared.withLock { s in
            let r = s.rehello
            s.rehello = false
            return r
        }
    }

    func syncSounds() {
        guard running, let enrol = store.enrolment else { return }
        let voice = shared.withLock { $0.roomVoice }
        // A failure keeps the cached clips.
        _ = try? sounds.sync(http: parts.http, coreURL: enrol.coreURL, fingerprint: enrol.fingerprint, voice: voice)
    }
}
