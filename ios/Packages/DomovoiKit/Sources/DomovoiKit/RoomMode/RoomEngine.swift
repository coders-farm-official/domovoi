import Foundation

/// The room itself: a phone speaking the satellite protocol on
/// `/v1/stream/{room}` the way a Pi does (satellite/client.py) —
/// `roommode/RoomEngine.kt`, split by concern:
///
/// - **The connection** (RoomEngineConnect.swift, one `Thread`): wait for
///   Wi-Fi; prove the core is the pinned one (``RoomIdentity``, before EVERY
///   connect); fetch the listening models; open the socket and send the hello
///   at once (the core closes a socket with no hello in 5 s); then either
///   `ready`, or a refusal the screen explains. Back off and try again. The
///   socket's frames — speech to the speaker, `end_capture`, volume, music,
///   drop-ins, wake-model pushes, the sound cache — are handled there too.
/// - **The listening thread** (RoomEngineListen.swift, one `Thread`, as
///   Android): reads 30 ms frames for good, runs the wake word on them,
///   acknowledges a wake (greeting or chime, dropping what the mic heard
///   under it), streams the capture with the core's pause hints
///   (``CaptureMachine``), waits for the reply and its follow-up, and relays
///   the mic during a drop-in.
/// - **Health** (RoomEngineHealth.swift): the minute's `health` frame and the
///   battery between minutes.
///
/// Not Android's `appOffer`: an iPhone's app comes from the App Store
/// (ROOMMODE-MAP D7).
public final class RoomEngine: RoomEngineControl, @unchecked Sendable {

    // MARK: - timings (RoomEngine.kt:1176-1188)

    public static let nudgeFloorMS: Int64 = 10_000
    public static let replyTimeoutMS: Int64 = 60_000
    public static let healthIntervalMS: Int64 = 60_000
    /// 210 ms of mic dropped after the acknowledgement (the Pi's tail).
    public static let ackTailFrames = 7

    /// A drop-in is taken only on a phone with an echo canceller, and only
    /// when the core says the call is full duplex.
    public static func acceptsDropin(echoCanceller: Bool, fullDuplex: Bool) -> Bool {
        echoCanceller && fullDuplex
    }

    // MARK: - parts

    let parts: RoomEngineParts
    let models: RoomModels
    let sounds: RoomSounds
    var store: any RoomKeeping { parts.store }

    /// The session state the socket's thread writes and the listening and
    /// connect threads read (Android's `@Volatile` fields), behind one lock.
    struct Shared {
        var running = false
        var run = 0
        var micRelease: (@Sendable () -> Void)?

        // the connection
        var socket: (any RoomSocket)?
        var features: Set<String> = []
        var ready = false
        var roomVoice: String?
        var readyAt: Int64 = 0
        var downSince: Int64 = 0
        var reconnects = 0
        var sessionGen = 0
        var outcome: RoomOneShot<String>?
        var closed: RoomOneShot<Bool>?
        var checkNow = 0
        var wifiUp = false

        // the turn
        var phase = RoomPhase.listening
        var muted = false
        var captureUtt: Int?
        var endCaptureFor: Int?
        var responseStarts = 0
        var captureMark = 0
        var acceptingAudio = false
        var responseEndSeen = false
        var expectFollowup = false
        var pendingMusic: String?
        var inCall = false
        /// The speaker is on the call's 16 kHz track (not a reply's).
        var callTrack = false
        var callPeer: String?
        var wantModels: (gen: Int, files: RoomModelFiles)?
        var modelsGen = 0
        var wakeWord = RoomWakeSettings.defaultWakeWord
        var wakeThreshold = RoomWakeSettings.defaultThreshold
        /// The last turn went wrong: the next wake waits out the cooldown.
        var quietNextWake = false

        // health
        var lastFrameAt: Int64 = 0
        var framesThisMinute = 0
        var healthSeq = 0
        var lastFacts: RoomHealthSample.Facts?
        var batteryRetry = false
        var lastCPU: Double = 0
        var lastCPUAt: Int64 = 0
    }

    let shared = RoomLock(Shared())

    /// Sleeps that `stop()`, Wi-Fi and "check now" wake.
    let waiter = NSCondition()

    let worker = DispatchQueue(label: "room-worker", qos: .utility)
    let clipQueue = DispatchQueue(label: "room-clips", qos: .userInitiated)
    let healthQueue = DispatchQueue(label: "room-health", qos: .utility)

    private struct Watchers {
        var conts: [Int: AsyncStream<RoomStatus>.Continuation] = [:]
        var next = 0
        var status: RoomStatus
    }

    private let watchers: RoomLock<Watchers>

    private struct Threads {
        var listenDone: DispatchSemaphore?
        var connectDone: DispatchSemaphore?
        var healthTimer: DispatchSourceTimer?
        var battery: Task<Void, Never>?
        var source: (any RoomPCMSource)?
    }

    private let threads = RoomLock(Threads())

    /// A `dropin_start` marked `await_ready` waits here for the reply that
    /// announced it; its lock guards the call state. Set once, in `init`.
    private(set) var dropin: DropinAfterReply!

    let batteryReporter = RoomBatteryReporter()

    // MARK: - making one

    /// The room with everything injectable: a package test's (fake transport,
    /// HTTP, clock, speaker), and what ``live(store:ears:pcm:device:directory:acquireMic:micRefusal:)``
    /// fills with the real parts.
    public init(parts: RoomEngineParts) {
        self.parts = parts
        models = RoomModels(directory: parts.directory.appendingPathComponent("models"), book: parts.store)
        sounds = RoomSounds(directory: parts.directory.appendingPathComponent("sounds"), store: parts.store)
        let muted = parts.store.muted
        watchers = RoomLock(Watchers(status: RoomStatus(link: .connecting, muted: muted)))
        shared.withLock {
            $0.muted = muted
            $0.wakeWord = parts.store.wakeWord
            $0.wakeThreshold = parts.store.wakeThreshold
        }
        parts.speaker.bind(onIdle: { [weak self] in self?.onSpeakerIdle() })
        parts.music.bind(
            onReady: { [weak self] in _ = self?.send(RoomProtocol.musicReady) },
            onFailed: { [weak self] url, reason, attempts in
                guard let self else { return }
                if self.features.contains("music_failed") {
                    self.send(RoomProtocol.musicFailed(streamURL: url, reason: reason, attempts: attempts))
                }
                self.update { $0.musicPlaying = false }
            }
        )
        let music = parts.music
        parts.gain.follow { music.setGain($0) }
        let worker = self.worker
        dropin = DropinAfterReply(
            // A reply arriving or playing; a call's own track is not one.
            stillPlaying: { [weak self] in self?.replyPlaying() ?? false },
            sendReady: { [weak self] in _ = self?.send(RoomProtocol.dropinReady) },
            now: parts.clock,
            launch: { work in worker.async { work() } },
            pause: { ms in Thread.sleep(forTimeInterval: Double(ms) / 1000) }
        )
    }

    /// The real room: the household-token-free client, the room socket, the
    /// model and sound sync under `directory` (Application Support/room,
    /// excluded from backups), the speaker and the given microphone.
    ///
    /// `acquireMic` takes the phone's one microphone for room mode and
    /// returns its release, or nil with `micRefusal` saying why — R3 wires
    /// them to `PlayerMicArbiter` (`.roomMode`). Closures, not the arbiter by
    /// name, because this folder stays iOS-15-clean and the arbiter uses
    /// `Mutex` (ROOMMODE-MAP §6.0).
    public static func live(
        store: any RoomKeeping,
        ears: any RoomEarsLoading = OnnxRoomEarsLoader(),
        pcm: @escaping RoomPCMSourceMaking,
        device: any RoomDeviceFacts,
        directory: URL,
        acquireMic: @escaping @Sendable () -> (@Sendable () -> Void)?,
        micRefusal: @escaping @Sendable () -> String?
    ) -> RoomEngine {
        let network = RoomNetwork()
        let gain = RoomGain()
        #if os(iOS)
        let graph = RoomAudioGraph.shared
        gain.follow { graph.setGain($0) }
        let speaker = RoomSpeaker(output: RoomGraphSpeakerOutput(graph: graph))
        let clips: any RoomClipPlaying = RoomGraphClips(graph: graph)
        let session: any RoomAudioSessionHooks = RoomGraphSession(graph: graph)
        // An iPhone's voice processing is always there.
        let echoCanceller = true
        #else
        let speaker = RoomSpeaker(output: RoomSilentSpeakerOutput())
        let clips: any RoomClipPlaying = RoomSilentClips()
        let session: any RoomAudioSessionHooks = RoomNoAudioSession()
        let echoCanceller = false
        #endif
        return RoomEngine(parts: RoomEngineParts(
            store: store, ears: ears, pcm: pcm, device: device, directory: directory,
            acquireMic: acquireMic, micRefusal: micRefusal,
            http: network, transport: network, wifi: RoomWifiWatch(),
            speaker: speaker, clips: clips, music: RoomMusic(), gain: gain, audio: session,
            echoCanceller: echoCanceller
        ))
    }

    // MARK: - the status

    public var status: RoomStatus { watchers.withLock { $0.status } }

    public func statusUpdates() -> AsyncStream<RoomStatus> {
        AsyncStream { continuation in
            let id: Int = watchers.withLock { w in
                w.next += 1
                w.conts[w.next] = continuation
                continuation.yield(w.status)
                return w.next
            }
            continuation.onTermination = { [weak self] _ in
                _ = self?.watchers.withLock { $0.conts.removeValue(forKey: id) }
            }
        }
    }

    /// Changes the status and tells every watcher (only when it changed).
    func update(_ change: (inout RoomStatus) -> Void) {
        watchers.withLock { w in
            var s = w.status
            change(&s)
            guard s != w.status else { return }
            w.status = s
            for c in w.conts.values { c.yield(s) }
        }
    }

    // MARK: - control

    var running: Bool { shared.withLock { $0.running } }
    var features: Set<String> { shared.withLock { $0.features } }
    var ready: Bool { shared.withLock { $0.ready } }
    var inCall: Bool { shared.withLock { $0.inCall } }
    var muted: Bool { shared.withLock { $0.muted } }
    var phase: RoomPhase { shared.withLock { $0.phase } }
    var now: Int64 { parts.clock() }

    @discardableResult
    public func start() -> Bool {
        if running { return true }
        guard let enrol = store.enrolment, store.pairingToken() != nil else {
            update { $0.link = .offline(reason: "this phone is not set up as a room") }
            return false
        }
        guard let release = parts.acquireMic() else {
            let why = parts.micRefusal() ?? "the microphone is in use"
            update { $0.link = .offline(reason: why) }
            return false
        }
        let run: Int = shared.withLock { s in
            s.running = true
            s.run += 1
            s.micRelease = release
            s.downSince = parts.clock()
            s.muted = store.muted
            s.wakeWord = store.wakeWord
            s.wakeThreshold = store.wakeThreshold
            s.lastCPUAt = parts.clock() - parts.device.processUptimeSeconds() * 1000
            s.lastCPU = 0
            return s.run
        }
        let muted = store.muted
        update {
            $0.link = .connecting
            $0.muted = muted
            $0.stoppedByIOS = nil
        }
        _ = parts.audio.activate()
        parts.wifi.start { [weak self] up in
            guard let self else { return }
            self.shared.withLock { $0.wifiUp = up }
            self.poke()
        }
        let connectDone = DispatchSemaphore(value: 0)
        let listenDone = DispatchSemaphore(value: 0)
        threads.withLock {
            $0.connectDone = connectDone
            $0.listenDone = listenDone
        }
        let connect = Thread { [self] in
            // What is already here listens at once, before the server answers
            // (re-hashed off the main thread).
            let word = shared.withLock { $0.wakeWord }
            if let files = models.local(word) {
                shared.withLock { s in
                    if s.wantModels == nil {
                        s.modelsGen += 1
                        s.wantModels = (s.modelsGen, files)
                    }
                }
            }
            connectLoop(enrol, run: run)
            connectDone.signal()
        }
        connect.name = "room-connect"
        connect.qualityOfService = .utility
        connect.start()

        let listen = Thread { [self] in
            listenLoop(run: run)
            listenDone.signal()
        }
        listen.name = "room-listen"
        listen.qualityOfService = .userInitiated
        listen.start()

        startHealth(run: run)
        return true
    }

    public func stop() {
        let release: (@Sendable () -> Void)? = shared.withLock { s in
            guard s.running else { return nil }
            s.running = false
            let r = s.micRelease
            s.micRelease = nil
            return r ?? {}
        }
        guard let release else { return }
        // A call still waiting for its announcement never opens now.
        dropin.cancel()
        poke()
        stopHealth()
        let (socket, source, listenDone, connectDone) = (
            shared.withLock { s -> (any RoomSocket)? in
                let w = s.socket
                s.socket = nil
                return w
            },
            threads.withLock { $0.source },
            threads.withLock { $0.listenDone },
            threads.withLock { $0.connectDone }
        )
        socket?.close()
        // Wake a read blocked on the microphone; the thread closes it.
        source?.stop()
        _ = listenDone?.wait(timeout: .now() + 2)
        _ = connectDone?.wait(timeout: .now() + 2)
        threads.withLock {
            $0.listenDone = nil
            $0.connectDone = nil
            $0.source = nil
        }
        parts.speaker.stop()
        parts.music.stop()
        let wasInCall: Bool = dropin.exclusive {
            dropin.cancel()
            return shared.withLock { s -> Bool in
                let was = s.inCall
                s.inCall = false
                s.callTrack = false
                s.callPeer = nil
                return was
            }
        }
        _ = wasInCall
        shared.withLock { s in
            s.ready = false
            s.features = []
            s.pendingMusic = nil
            s.acceptingAudio = false
            s.phase = .listening
        }
        parts.wifi.stop()
        parts.audio.deactivate()
        release()
        update {
            $0.phase = .listening
            $0.micLive = false
            $0.micOpen = false
            $0.musicPlaying = false
            $0.peerRoom = nil
        }
        let conts = watchers.withLock { w -> [AsyncStream<RoomStatus>.Continuation] in
            let c = Array(w.conts.values)
            w.conts = [:]
            return c
        }
        for c in conts { c.finish() }
    }

    public func setMuted(_ on: Bool) {
        shared.withLock { $0.muted = on }
        store.setMuted(on)
        update {
            $0.muted = on
            if on { $0.micLive = false }
        }
    }

    /// "Check now" while waiting for approval: one retry, at most every 10 s.
    public func retryNow() {
        shared.withLock { $0.checkNow += 1 }
        poke()
    }

    /// Hands the listening thread these models, as a verified sync would
    /// (the package tests' seam: their fake ears have no pinned files).
    func useModels(_ files: RoomModelFiles) {
        shared.withLock { s in
            s.modelsGen += 1
            s.wantModels = (s.modelsGen, files)
        }
    }

    // MARK: - helpers the other files share

    /// Wakes every interruptible sleep.
    func poke() {
        waiter.lock()
        waiter.broadcast()
        waiter.unlock()
    }

    /// Sleeps `ms`, or until `stop()` (or `until` turns true). False when
    /// the room stopped.
    @discardableResult
    func sleep(_ ms: Int64, run: Int, until: (() -> Bool)? = nil) -> Bool {
        let deadline = Date().addingTimeInterval(Double(max(0, ms)) / 1000)
        waiter.lock()
        defer { waiter.unlock() }
        while isRunning(run) {
            if let until, until() { return true }
            if !waiter.wait(until: deadline) { return isRunning(run) }
        }
        return false
    }

    func isRunning(_ run: Int) -> Bool {
        shared.withLock { $0.running && $0.run == run }
    }

    /// The mic source the listening thread has open, so `stop()` can end a
    /// blocked read.
    func track(_ source: any RoomPCMSource) {
        threads.withLock { $0.source = source }
    }

    func setPhase(_ p: RoomPhase) {
        shared.withLock { $0.phase = p }
        update { if $0.phase != p { $0.phase = p } }
    }

    /// Sends `frame` if a session is up and the core understands it.
    @discardableResult
    func send(_ frame: RoomProtocol.Frame) -> Bool {
        let (socket, features) = shared.withLock { ($0.socket, $0.features) }
        guard let socket else { return false }
        guard RoomProtocol.maySend(frame, features: features) else { return false }
        return socket.sendText(RoomProtocol.text(frame))
    }

    /// One frame of PCM16, little-endian, as the core reads it.
    @discardableResult
    func sendAudio(_ frame: [Int16], _ n: Int) -> Bool {
        guard let socket = shared.withLock({ $0.socket }) else { return false }
        var bytes = [UInt8](repeating: 0, count: n * 2)
        for i in 0..<n {
            let v = UInt16(bitPattern: frame[i])
            bytes[2 * i] = UInt8(v & 0xFF)
            bytes[2 * i + 1] = UInt8(v >> 8)
        }
        return socket.sendBinary(Data(bytes))
    }

    /// A reply arriving or playing (a call's own track is not one).
    func replyPlaying() -> Bool {
        let (accepting, callTrack) = shared.withLock { ($0.acceptingAudio, $0.callTrack) }
        return accepting || (parts.speaker.active && !callTrack)
    }

    // MARK: - health thread plumbing (RoomEngineHealth.swift fills it)

    func setHealthTimer(_ timer: DispatchSourceTimer?, battery: Task<Void, Never>?) {
        threads.withLock {
            $0.healthTimer = timer
            $0.battery = battery
        }
    }

    func stopHealth() {
        let (timer, battery) = threads.withLock { t -> (DispatchSourceTimer?, Task<Void, Never>?) in
            let r = (t.healthTimer, t.battery)
            t.healthTimer = nil
            t.battery = nil
            return r
        }
        timer?.cancel()
        battery?.cancel()
    }
}

// MARK: - the parts

/// The audio session around the room (ROOMMODE-MAP §4: one owner, one
/// session).
public protocol RoomAudioSessionHooks: Sendable {
    /// The room's session, active. False when iOS refused (the microphone's
    /// retries then try again).
    func activate() -> Bool
    func deactivate()
}

/// No session at all: a Mac, and the package tests.
public struct RoomNoAudioSession: RoomAudioSessionHooks {
    public init() {}
    public func activate() -> Bool { true }
    public func deactivate() {}
}

#if os(iOS)
/// The graph's session.
public struct RoomGraphSession: RoomAudioSessionHooks {
    let graph: RoomAudioGraph
    public init(graph: RoomAudioGraph = .shared) { self.graph = graph }
    public func activate() -> Bool { graph.activate() }
    public func deactivate() { graph.deactivate() }
}
#endif

/// Everything a room is made of. ``RoomEngine/live(store:ears:pcm:device:directory:acquireMic:micRefusal:)``
/// fills it with the real parts; a package test with its own.
public struct RoomEngineParts: Sendable {
    public var store: any RoomKeeping
    public var ears: any RoomEarsLoading
    public var pcm: RoomPCMSourceMaking
    public var device: any RoomDeviceFacts
    public var directory: URL
    public var acquireMic: @Sendable () -> (@Sendable () -> Void)?
    public var micRefusal: @Sendable () -> String?
    public var http: any RoomHTTP
    public var transport: any RoomSocketTransport
    public var wifi: any RoomWifiWatching
    public var speaker: any RoomSpeaking
    public var clips: any RoomClipPlaying
    public var music: any RoomMusicPlaying
    public var gain: RoomGain
    public var audio: any RoomAudioSessionHooks
    /// The phone can cancel its own echo (an iPhone always can): it takes
    /// drop-ins, and says `full_duplex` and `dropin_ready`.
    public var echoCanceller: Bool
    /// Monotonic milliseconds.
    public var clock: @Sendable () -> Int64
    /// Wall seconds (the health frame's `ts`).
    public var wall: @Sendable () -> Double
    public var healthIntervalMS: Int64

    public init(
        store: any RoomKeeping,
        ears: any RoomEarsLoading,
        pcm: @escaping RoomPCMSourceMaking,
        device: any RoomDeviceFacts,
        directory: URL,
        acquireMic: @escaping @Sendable () -> (@Sendable () -> Void)?,
        micRefusal: @escaping @Sendable () -> String?,
        http: any RoomHTTP,
        transport: any RoomSocketTransport,
        wifi: any RoomWifiWatching,
        speaker: any RoomSpeaking,
        clips: any RoomClipPlaying = RoomSilentClips(),
        music: any RoomMusicPlaying,
        gain: RoomGain = RoomGain(),
        audio: any RoomAudioSessionHooks = RoomNoAudioSession(),
        echoCanceller: Bool,
        clock: @escaping @Sendable () -> Int64 = RoomClock.monotonicMS,
        wall: @escaping @Sendable () -> Double = RoomClock.wallSeconds,
        healthIntervalMS: Int64 = RoomEngine.healthIntervalMS
    ) {
        self.store = store
        self.ears = ears
        self.pcm = pcm
        self.device = device
        self.directory = directory
        self.acquireMic = acquireMic
        self.micRefusal = micRefusal
        self.http = http
        self.transport = transport
        self.wifi = wifi
        self.speaker = speaker
        self.clips = clips
        self.music = music
        self.gain = gain
        self.audio = audio
        self.echoCanceller = echoCanceller
        self.clock = clock
        self.wall = wall
        self.healthIntervalMS = healthIntervalMS
    }
}

/// A value that arrives once, waited for on a thread (Kotlin's
/// `CompletableDeferred`).
final class RoomOneShot<T: Sendable>: @unchecked Sendable {
    private let cond = NSCondition()
    private var value: T?

    func complete(_ v: T) {
        cond.lock()
        if value == nil {
            value = v
            cond.broadcast()
        }
        cond.unlock()
    }

    var isCompleted: Bool {
        cond.lock()
        defer { cond.unlock() }
        return value != nil
    }

    /// The value, or nil once `keepWaiting` says to give up (checked every
    /// 100 ms).
    func wait(while keepWaiting: () -> Bool) -> T? {
        cond.lock()
        defer { cond.unlock() }
        while value == nil {
            if !keepWaiting() { return nil }
            _ = cond.wait(until: Date().addingTimeInterval(0.1))
        }
        return value
    }
}
