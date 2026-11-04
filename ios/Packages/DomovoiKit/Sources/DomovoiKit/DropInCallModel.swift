import Foundation
import Observation
import Synchronization

/// Phone → room drop-in: one call, its state machine, and the two things that
/// end it.
///
/// A port of `android/.../dropin/DropinCallClient.kt` (255 lines), whose
/// header is the specification: "Phone side of a drop-in call — the
/// counterpart of domovoi/phone_dropin.py. One WebSocket to
/// ws://<domovoi>/v1/dropin/{room}: we stream 16 kHz mono PCM16 mic frames up
/// (voice-communication source = hardware AEC, which is what lets the phone
/// claim full-duplex), and play the room's relayed audio + chimes straight
/// into an AudioTrack." The wire protocol itself is written out in
/// `domovoi/phone_dropin.py:19-42` and read from there, not from the Kotlin.
///
/// Kamron, answer 54: "yes: phone-to-room drop-in is an iPhone feature; build
/// it (shared audio layer with the chat mic and room mode)".
///
/// ### A phone never starts a room-to-room call
///
/// Answers 5 and 53: "phones never create room-to-room drop-ins; a phone can
/// only drop into a room". That is a different route —
/// `/v1/admin/dropin/start`, admin *security* tier
/// (`domovoi/main.py:2649-2658`) — and it stays off both phones. This socket
/// is the device tier, the core checks the household token on the upgrade
/// itself (`main.py:7069-7100`), and the only call it can open is this
/// phone's into one room. ``RoomsStrings/dropInStartIsOnTheDashboard`` says
/// so on the screen.
///
/// ### What is not here, and why there is no control for it
///
/// * **No ringing of a second room, and no transfer.** Neither exists on the
///   wire for a phone peer.
/// * **No reconnect.** Android has none either: `onFailure` ends the call
///   (`DropinCallClient.kt:127-133`). A call that silently came back after a
///   gap would have opened the room's microphone again without anyone there
///   being told, which is the one thing this whole socket's tier is about.
/// * **No background call.** `UIBackgroundModes` is `audio` only
///   (`project.yml:239`); CallKit and VoIP pushes would make this an app that
///   rings, which Kamron has not asked for. The call ends with the screen, as
///   Android's dialog ends it on dispose (`PhoneDropinCall.kt:125-127`).
@MainActor
@Observable
public final class DropInCallModel {

    /// `DropinCallClient.CallState` (`:44-50`) plus one case iOS adds.
    public enum State: Equatable, Sendable {
        case idle
        case connecting
        /// **Not Android's.** The server's ring mode parks the call and rings
        /// the room first (`phone_dropin.py:26-32`); Android handles no
        /// `dropin_ringing` frame, so over there a rung room looks like a
        /// call that will not connect. See ``DropInStrings/ringing(room:)``.
        case ringing(peer: String)
        case live(peer: String)
        case ended(reason: String)
        case failed(note: String)

        /// Whether audio is flowing, which is the only thing that decides
        /// whether `mute` is drawn (`PhoneDropinCall.kt:167`).
        public var isLive: Bool {
            if case .live = self { return true }
            return false
        }

        /// Whether a call is under way at all — Android's
        /// `Connecting || Live` guard against a second `start()`
        /// (`DropinCallClient.kt:72`), with `ringing` counted in because the
        /// room has already been disturbed by then.
        public var isBusy: Bool {
            switch self {
            case .connecting, .ringing, .live: return true
            case .idle, .ended, .failed: return false
            }
        }
    }

    public private(set) var state: State = .idle
    public private(set) var muted = false

    /// Seconds since the call went live (`PhoneDropinCall.kt:114-123`).
    public private(set) var elapsedSec = 0

    /// The room this call is with, for the sentence under the controls.
    public private(set) var room = ""

    private let client: DomovoiClient
    private let transport: any DropInSocketTransport
    private let audio: any DropInAudio
    private let session: PlayerAudioSessionControl
    private let permission: any DropInMicPermission
    private let phoneID: String?
    private let arbiter: PlayerMicOwnership
    private let tick: @Sendable (Int64) async throws -> Void

    /// The open socket, in a lock rather than in a stored property.
    ///
    /// **Because the microphone callback has to reach it.** The capture tap
    /// runs on CoreAudio's own thread, so a closure that read `self.socket`
    /// would be reading a `@MainActor` property off the main actor — and a
    /// hop to the main actor per 30 ms frame is latency on a live call for
    /// nothing. The box is `Sendable`, the closure captures the box and not
    /// `self`, and ``tearDown()`` empties it, so a frame that arrives during
    /// a hang-up goes nowhere instead of onto a cancelled task.
    private let outbound = DropInOutbound()

    private var lease: PlayerMicLease?
    private var clock: Task<Void, Never>?

    /// The call this model's tasks were started for.
    ///
    /// The read-after-await rule, which is the top defect class on this track:
    /// `start(room:)` awaits the `phone-info` read, and an answer for the
    /// house the phone has just left — or for a call that was hung up while
    /// the read was in flight — must not open a socket. Taken **before** the
    /// first await and checked after it.
    private var attempt = 0

    public init(
        client: DomovoiClient,
        transport: any DropInSocketTransport,
        audio: any DropInAudio,
        session: PlayerAudioSessionControl,
        permission: any DropInMicPermission,
        phoneID: String?,
        arbiter: PlayerMicOwnership = PlayerMicArbiter.shared,
        tick: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.client = client
        self.transport = transport
        self.audio = audio
        self.session = session
        self.permission = permission
        self.phoneID = phoneID
        self.arbiter = arbiter
        self.tick = tick
    }

    // MARK: - starting

    /// Ask for the microphone, resolve the socket's address, and open it.
    ///
    /// The order is Android's, with the permission ask in front of it because
    /// iOS's is asynchronous: `PhoneDropinRow` checks `RECORD_AUDIO` and
    /// launches the system prompt before it calls `start`
    /// (`PhoneDropinCall.kt:82-88`), so the two steps are the same two in the
    /// same order; only the asking is a `await` here rather than a callback.
    public func start(room: String) async {
        guard !state.isBusy else { return }
        self.room = room
        state = .connecting
        muted = false
        elapsedSec = 0
        attempt += 1
        let mine = attempt

        guard await permission.request() else {
            guard mine == attempt else { return }
            state = .failed(note: DropInStrings.micRefused)
            return
        }
        guard mine == attempt else { return }

        // **The arbiter, before anything is opened** (`MicArbiter.kt`): one
        // owner at a time, and a refusal is a sentence rather than a silent
        // failure. The player plugs its pause into ``whenTaken(_:whenFreed:)``
        // and the lease's release puts the session's category back, so taking
        // the microphone here is also what stops music on this phone.
        do {
            lease = try arbiter.acquireOrThrow(.dropIn)
        } catch let refused as PlayerMicRefused {
            state = .failed(note: refused.note)
            return
        } catch {
            state = .failed(note: arbiter.refusal(.dropIn) ?? DropInStrings.micInUse)
            return
        }

        // The house, taken before the await and asked again after it.
        let house = client.baseURL
        let info: DropInPhoneInfo
        do {
            info = try await client.get(RoomsApi.dropInPhoneInfo(room), as: DropInPhoneInfo.self)
        } catch {
            guard mine == attempt else { return }
            freeTheMic()
            state = .failed(note: DropInStrings.cannotReachTheServer)
            return
        }
        guard mine == attempt else { return freeTheMic() }
        guard client.baseURL == house else {
            // The phone changed house while the read was in flight. The
            // address that came back is the old house's, and `kitchen` is a
            // room name both houses can have.
            freeTheMic()
            state = .idle
            return
        }

        guard let address = dropInSocketURL(house: house, info: info, room: room, phoneID: phoneID),
              let upgrade = client.webSocketUpgrade(address)
        else {
            freeTheMic()
            state = .failed(note: DropInStrings.cannotReachTheServer)
            return
        }
        // `mint()` immediately before the task is made, never once into a
        // stored property — ``DomovoiClient/webSocketUpgrade(_:)``'s stated
        // obligation. Nil is "not now", not a failure: the server asked this
        // phone to wait, or the identity gate is holding it.
        guard let request = upgrade.mint() else {
            freeTheMic()
            state = .failed(note: DropInStrings.cannotReachTheServer)
            return
        }

        // The audio session before the engine: voice processing needs an
        // active `.playAndRecord` session before the nodes are asked for it
        // (``DropInAudioEngine``).
        guard session.activate(.recording) else {
            freeTheMic()
            state = .failed(note: DropInStrings.micInUse)
            return
        }
        // Playback is opened before the socket, as Android's `startPlayback()`
        // is (`DropinCallClient.kt:91`): the first thing down the wire is a
        // chime, and a track that was not ready yet would swallow it.
        do {
            // The box and not `self`: see ``outbound``. "Send exactly what was
            // read — the bridge relays verbatim" (`DropinCallClient.kt:206`).
            try audio.start { [outbound] frame in outbound.sendBinary(frame) }
        } catch let failed as DropInAudioFailed {
            session.setCategory(.music)
            freeTheMic()
            state = .failed(note: failed.note)
            return
        } catch {
            session.setCategory(.music)
            freeTheMic()
            state = .failed(note: DropInStrings.micInUse)
            return
        }

        outbound.hold(transport.open(request, reporting: events(for: mine)))
    }

    /// The socket's callbacks, each hopping to the main actor and each
    /// checking it is still the call that opened them.
    ///
    /// The generation check is the same one ``StateSocket`` makes: a link that
    /// was cancelled may still report, and a late `failed` from the call
    /// before would otherwise put a sentence on a call that is fine.
    private func events(for mine: Int) -> DropInSocketEvents {
        DropInSocketEvents(
            opened: { [weak self] in
                Task { @MainActor in self?.opened(mine) }
            },
            binary: { [audio] data in
                // Straight to the speaker, off the main actor and without
                // touching `self`: this is the room's voice and the chimes,
                // arriving every 30 ms, and ``DropInAudio`` is `Sendable`
                // precisely so that this one call needs no hop.
                audio.play(data)
            },
            text: { [weak self] text in
                Task { @MainActor in self?.said(text, mine) }
            },
            failed: { [weak self] status in
                Task { @MainActor in self?.lost(status, mine) }
            },
            closed: { [weak self] in
                Task { @MainActor in self?.closed(mine) }
            }
        )
    }

    /// The upgrade was accepted. Android starts the microphone here
    /// (`DropinCallClient.kt:96-98`); this model has it open already — see
    /// ``start(room:)``'s note on the chime — so there is nothing to do but
    /// wait for the server to say whether the call is live or rung.
    private func opened(_ mine: Int) {
        guard mine == attempt else { return }
    }

    /// One text frame (`phone_dropin.py:25-42`).
    private func said(_ text: String, _ mine: Int) {
        guard mine == attempt, let frame = DropInFrame.parse(text) else { return }
        switch frame.type {
        case "dropin_ringing":
            // Nothing flows yet in either direction. The microphone stays
            // open: the call becomes live without another handshake, and an
            // engine stopped and restarted here would miss the first words.
            state = .ringing(peer: frame.peerRoom ?? room)
        case "dropin_start":
            state = .live(peer: frame.peerRoom ?? room)
            startTheClock()
        case "dropin_end":
            tearDown()
            state = .ended(reason: frame.reason ?? "ended")
        case "error":
            tearDown()
            state = .failed(note: DropInStrings.refusal(frame.code ?? "refused"))
        default:
            // A frame this app does not know is not a failure. The server may
            // grow one, and a call that ended on an unread word would be the
            // worse answer.
            break
        }
    }

    /// The upgrade or the socket failed (`DropinCallClient.kt:127-133`).
    ///
    /// The status is handed to the client exactly as Android hands the
    /// response to `notePossiblePairingRefusal` (`:128`), so a `403` on this
    /// socket can still raise the pairing screen.
    private func lost(_ status: Int?, _ mine: Int) {
        guard mine == attempt else { return }
        if let status { client.notePossiblePairingRefusal(status: status, body: nil) }
        guard state.isBusy else { return }
        tearDown()
        state = .failed(note: DropInStrings.connectionLost)
    }

    /// The peer closed cleanly (`DropinCallClient.kt:135-140`). Android
    /// reports this only from `Live`; a close during `connecting` is a refusal
    /// the `error` frame has already said more about.
    private func closed(_ mine: Int) {
        guard mine == attempt, state.isLive else { return }
        tearDown()
        state = .ended(reason: "closed")
    }

    // MARK: - during the call

    public func setMuted(_ muted: Bool) {
        self.muted = muted
        audio.setMuted(muted)
    }

    /// Tell the room, then put everything down (`DropinCallClient.kt:216-220`).
    public func hangUp() {
        outbound.sendText(DropInFrame.endFrame)
        let failed: Bool = {
            if case .failed = state { return true }
            return false
        }()
        tearDown()
        if !failed { state = .ended(reason: "ended") }
    }

    /// Back to nothing (`DropinCallClient.kt:222-225`), which is what closing
    /// the sheet does.
    public func reset() {
        tearDown()
        state = .idle
        room = ""
        elapsedSec = 0
    }

    /// `elapsed++` every second while the call is live
    /// (`PhoneDropinCall.kt:116-123`).
    private func startTheClock() {
        clock?.cancel()
        clock = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do { try await self.tick(1000) } catch { return }
                guard !Task.isCancelled, self.state.isLive else { return }
                self.elapsedSec += 1
            }
        }
    }

    /// `teardown()` (`DropinCallClient.kt:227-245`): the microphone, the
    /// engine, the socket and the audio session, in that order, and the lease
    /// last so nothing can take the mic while this call still holds hardware.
    private func tearDown() {
        attempt += 1
        clock?.cancel(); clock = nil
        audio.stop()
        outbound.closeAndDrop()
        // The category goes back without an activation, which is what
        // ``PlayerAudioSessionControl/setCategory(_:)`` is for: activating a
        // `.playback` session while this app is silent would take the
        // process-wide session off whatever picked the audio up during the
        // call. ``PlayerModel`` does the same on a released lease.
        session.setCategory(.music)
        freeTheMic()
    }

    private func freeTheMic() {
        lease?.release()
        lease = nil
    }
}

// ---------------------------------------------------------------------------
// The address
// ---------------------------------------------------------------------------

/// `GET /api/satellites/{room}/dropin/phone-info` (`satellites.py:1520-1556`),
/// device tier: where the core's socket listens.
///
/// `domovoi_host` is **null** when the core is on the same box as the web
/// backend, and the route says what that means: "localhost means 'same host
/// as this web backend' — the phone should substitute the host it reached us
/// on" (`:1549-1551`). Android does exactly that (`DropinCallClient.kt:147`).
public struct DropInPhoneInfo: Decodable, Equatable, Sendable {

    public let roomID: String?
    public let domovoiHost: String?
    public let domovoiPort: Int?

    /// Read but **not used to build the URL**. The server computes it as
    /// `/v1/dropin/{room_id}` and nothing else (`satellites.py:1555`), and
    /// Android builds the path itself (`DropinCallClient.kt:151`); taking a
    /// path from a payload would let a read decide where this phone's
    /// microphone connects. Kept so a mismatch is visible in a log rather
    /// than invisible.
    public let wsPath: String?

    /// 16000 (`satellites.py:1556`). ``DropInAudioFormat/sampleRate`` is the
    /// one the engine is built for; a server that said something else would
    /// be a server this app cannot speak to, which is worth knowing.
    public let audioSampleRate: Double?

    enum CodingKeys: String, CodingKey {
        case roomID = "room_id"
        case domovoiHost = "domovoi_host"
        case domovoiPort = "domovoi_port"
        case wsPath = "ws_path"
        case audioSampleRate = "audio_sample_rate"
    }

    public init(
        roomID: String? = nil,
        domovoiHost: String? = nil,
        domovoiPort: Int? = nil,
        wsPath: String? = nil,
        audioSampleRate: Double? = nil
    ) {
        self.roomID = roomID
        self.domovoiHost = domovoiHost
        self.domovoiPort = domovoiPort
        self.wsPath = wsPath
        self.audioSampleRate = audioSampleRate
    }
}

/// The socket's address, as `resolveWsUrl` builds it
/// (`DropinCallClient.kt:145-152`).
///
/// `wss` when the house is `https`, the core's port from the read (6370 when
/// it says nothing, which is the core's own default), and the host the phone
/// reached the house on whenever the read says null — the route's instruction,
/// above. Nil when the house's own address does not parse, which is the one
/// case Android papers over with `?: "localhost"`: a phone with no usable
/// server address has nothing to connect to, and saying so is better than
/// connecting to this device.
///
/// The token this carries is **not** decided here. The upgrade is built by
/// ``DomovoiClient/webSocketUpgrade(_:)`` and
/// ``TokenScope/admits(_:_:wsUpgrade:)`` already admits another port on the
/// active server's host "for an upgrade the app built" — which that rule's own
/// comment says is "what the drop-in socket to the core's port needs".
public func dropInSocketURL(
    house: String,
    info: DropInPhoneInfo,
    room: String,
    phoneID: String?
) -> String? {
    guard let base = ServerURL.parse(house) else { return nil }
    let scheme = base.isHttps ? "wss" : "ws"
    let host = info.domovoiHost?.trimmingCharacters(in: .whitespacesAndNewlines)
    let target = (host?.isEmpty == false ? host! : base.host)
    let port = info.domovoiPort ?? 6370
    // IPv6 literals go back into the brackets `ServerURL` took off them.
    let authority = target.contains(":") ? "[\(target)]" : target
    var url = "\(scheme)://\(authority):\(port)/v1/dropin/\(dropInEsc(room))"
    // `phone_id` is this phone's identity in `active_dropins`, which is what
    // makes `initiator_busy` work: "give it a stable per-device value"
    // (`main.py:7064`). Left off when there is none, and the core then uses
    // `phone` — one slot every unregistered phone shares, which is honest
    // about what it knows.
    if let phoneID, !phoneID.isEmpty {
        url += "?phone_id=\(dropInEsc(phoneID))"
    }
    return url
}

/// One path component or query value, percent-escaped.
///
/// Room ids are `[a-z0-9_]` on the server and this phone's device id carries
/// a hyphen, so the set is ``VideoPath/safe`` — the unreserved characters,
/// which leaves both alone and still escapes `&`, `=`, `#`, `+`, `/`, `\` and
/// a space. `.alphanumerics` alone would send `front%5Froom`, which is a room
/// the house does not have.
private func dropInEsc(_ text: String) -> String {
    text.addingPercentEncoding(withAllowedCharacters: VideoPath.safe) ?? text
}

// ---------------------------------------------------------------------------
// The frames
// ---------------------------------------------------------------------------

/// One text frame from the server (`phone_dropin.py:25-42`), read with
/// `JSONSerialization` rather than a `Decodable` per type: four frame kinds
/// share three optional fields, and a `type` this app does not know must be
/// ignored rather than throwing.
public struct DropInFrame: Equatable, Sendable {

    public let type: String
    public let peerRoom: String?
    public let reason: String?
    public let code: String?

    public init(type: String, peerRoom: String? = nil, reason: String? = nil, code: String? = nil) {
        self.type = type
        self.peerRoom = peerRoom
        self.reason = reason
        self.code = code
    }

    /// `{"type":"dropin_end"}` — the hang-up Android sends
    /// (`DropinCallClient.kt:217`).
    public static let endFrame = #"{"type":"dropin_end"}"#

    /// Nil when the text is not an object, or carries no `type`.
    public static func parse(_ text: String) -> DropInFrame? {
        guard let data = text.data(using: .utf8),
              let any = try? JSONSerialization.jsonObject(with: data),
              let object = any as? [String: Any],
              let type = object["type"] as? String
        else { return nil }
        return DropInFrame(
            type: type,
            peerRoom: object["peer_room"] as? String,
            reason: object["reason"] as? String,
            code: object["code"] as? String
        )
    }
}

// ---------------------------------------------------------------------------
// The socket
// ---------------------------------------------------------------------------

/// The open socket, reachable from the audio callback.
///
/// One small `Sendable` box so that a microphone frame can be sent from
/// CoreAudio's thread without touching ``DropInCallModel``'s `@MainActor`
/// state — see that type's ``DropInCallModel/outbound``. It is also what makes
/// a frame arriving mid-hang-up harmless: ``closeAndDrop()`` empties it, and
/// every send after that goes nowhere.
final class DropInOutbound: Sendable {

    private let held = Mutex<(any DropInSocket)?>(nil)

    func hold(_ socket: any DropInSocket) {
        let stale: (any DropInSocket)? = held.withLock { current in
            let stale = current
            current = socket
            return stale
        }
        // A socket replaced without being closed is a microphone left open on
        // the server, so the old one goes down even though nothing should
        // ever reach here with one in place.
        stale?.close()
    }

    func sendBinary(_ data: Data) { held.withLock { $0 }?.sendBinary(data) }

    func sendText(_ text: String) { held.withLock { $0 }?.sendText(text) }

    func closeAndDrop() {
        let going: (any DropInSocket)? = held.withLock { current in
            let going = current
            current = nil
            return going
        }
        going?.close()
    }
}

/// An open drop-in socket, as the little the call needs of it.
///
/// **Not ``StateSocketLink``**, and that is the one thing this file could not
/// reuse: that protocol is `send(_ text: String)` with a `text` callback, and
/// `URLSessionStateSocketTransport` drops every binary frame it receives on
/// purpose — "Binary frames fall into the `else` and are ignored, as Android
/// ignores them … because the server sends JSON text"
/// (`URLSessionStateSocket.swift:191-197`). A drop-in call is binary in both
/// directions. The shape here is deliberately the same shape, so that a later
/// batch can fold the two into one transport; that is a change to
/// `DomovoiClient.swift` and `StateSocket.swift`, which this unit does not
/// own, and it is in DECISIONS under "Needs a shared change".
public protocol DropInSocket: Sendable {
    func sendBinary(_ data: Data)
    func sendText(_ text: String)
    func close()
}

/// What a transport tells the call. Every closure may be called from any
/// thread; the model hops to the main actor itself, except for the audio.
public struct DropInSocketEvents: Sendable {

    public let opened: @Sendable () -> Void
    public let binary: @Sendable (Data) -> Void
    public let text: @Sendable (String) -> Void

    /// `status` is the upgrade's HTTP status when there was a response, nil
    /// when the request never got an answer.
    public let failed: @Sendable (Int?) -> Void
    public let closed: @Sendable () -> Void

    public init(
        opened: @Sendable @escaping () -> Void,
        binary: @Sendable @escaping (Data) -> Void,
        text: @Sendable @escaping (String) -> Void,
        failed: @Sendable @escaping (Int?) -> Void,
        closed: @Sendable @escaping () -> Void
    ) {
        self.opened = opened
        self.binary = binary
        self.text = text
        self.failed = failed
        self.closed = closed
    }
}

/// How a call's socket is opened. The app passes
/// ``URLSessionDropInTransport``; a test passes its own.
///
/// The obligation is ``StateSocketTransport``'s: **exactly one of `failed` or
/// `closed` per link, and neither after `close()`**.
public protocol DropInSocketTransport: Sendable {
    func open(_ request: URLRequest, reporting events: DropInSocketEvents) -> any DropInSocket
}

/// ``DropInSocketTransport`` over `URLSessionWebSocketTask`.
///
/// Its own `URLSession`, built from ``DomovoiClient/defaultConfiguration()`` —
/// the client's own factory, so this inherits the same no-cookies,
/// no-cache configuration the rest of the app uses. The client's session
/// itself is internal and ``DomovoiClient/webSocketTransport`` hands back a
/// text-only transport, so a shared session here is the shared change above.
public final class URLSessionDropInTransport: DropInSocketTransport {

    private let session: URLSession

    public init(configuration: URLSessionConfiguration = DomovoiClient.defaultConfiguration()) {
        session = URLSession(configuration: configuration)
    }

    public func open(_ request: URLRequest, reporting events: DropInSocketEvents) -> any DropInSocket {
        let task = session.webSocketTask(with: request)
        let link = Link(task: task, events: events)
        task.resume()
        link.run()
        return link
    }

    /// One open call, and its receive loop.
    ///
    /// `@unchecked Sendable` for ``URLSessionStateSocketTransport/Link``'s
    /// reason: the one mutable field is behind the lock, `task` and `events`
    /// are `let`, and `URLSessionWebSocketTask` is documented thread-safe for
    /// `send`, `receive` and `cancel`.
    final class Link: DropInSocket, @unchecked Sendable {

        private struct State {
            var opened = false
            var finished = false
            var pump: Task<Void, Never>?
        }

        private let task: URLSessionWebSocketTask
        private let events: DropInSocketEvents
        private let lock = NSLock()
        private var state = State()

        init(task: URLSessionWebSocketTask, events: DropInSocketEvents) {
            self.task = task
            self.events = events
        }

        func sendBinary(_ data: Data) {
            task.send(.data(data)) { [weak self] error in
                if error != nil { self?.fail(nil) }
            }
        }

        func sendText(_ text: String) {
            task.send(.string(text)) { [weak self] error in
                if error != nil { self?.fail(nil) }
            }
        }

        func close() {
            let pump = lock.withLock { () -> Task<Void, Never>? in
                state.finished = true
                let pump = state.pump
                state.pump = nil
                return pump
            }
            pump?.cancel()
            task.cancel(with: .normalClosure, reason: Data("bye".utf8))
        }

        /// The receive loop. No ping and no connect watchdog, unlike
        /// ``URLSessionStateSocketTransport``: a drop-in call has audio
        /// flowing in both directions within a second of opening, and silence
        /// on it is the server's own silence watchdog's business
        /// (`phone_dropin.py`). A ping loop of ours would be a second,
        /// quieter opinion about a dead call.
        func run() {
            let pump = Task<Void, Never> { [weak self] in await self?.pump() }
            let stale = lock.withLock { () -> Task<Void, Never>? in
                guard !state.finished else { return pump }
                state.pump = pump
                return nil
            }
            stale?.cancel()
        }

        private func pump() async {
            while !Task.isCancelled {
                do {
                    let message = try await task.receive()
                    reportOpenedOnce()
                    switch message {
                    case .data(let data): events.binary(data)
                    case .string(let text): events.text(text)
                    @unknown default: break
                    }
                } catch {
                    if lock.withLock({ state.finished }) { return }
                    ended()
                    return
                }
            }
        }

        /// `opened` on the first frame, which is the same fact as the upgrade
        /// having been accepted — ``URLSessionStateSocketTransport/Link``'s
        /// own reasoning, minus the ping it uses to bring that fact forward.
        /// The server speaks first on this socket, always
        /// (`dropin_ringing` or `dropin_start`), so there is nothing to
        /// bring forward.
        private func reportOpenedOnce() {
            let first = lock.withLock { () -> Bool in
                guard !state.finished, !state.opened else { return false }
                state.opened = true
                return true
            }
            if first { events.opened() }
        }

        private func ended() {
            if task.closeCode != .invalid {
                guard claim() else { return }
                tearDown()
                events.closed()
                return
            }
            fail((task.response as? HTTPURLResponse)?.statusCode)
        }

        private func fail(_ status: Int?) {
            guard claim() else { return }
            tearDown()
            events.failed(status)
        }

        private func tearDown() {
            let pump = lock.withLock { () -> Task<Void, Never>? in
                let pump = state.pump
                state.pump = nil
                return pump
            }
            pump?.cancel()
            task.cancel(with: .goingAway, reason: nil)
        }

        /// One end per link, so a failed send at the same moment as the
        /// receive loop cannot report two.
        private func claim() -> Bool {
            lock.withLock { () -> Bool in
                guard !state.finished else { return false }
                state.finished = true
                return true
            }
        }
    }
}

// ---------------------------------------------------------------------------
// The microphone permission
// ---------------------------------------------------------------------------

/// May this app use the microphone? Asked once, in front of the call
/// (`PhoneDropinCall.kt:82-88`).
///
/// A protocol for the same reason the audio layer is one: the real answer
/// comes from a system prompt that cannot run where this package's tests do.
/// `NSMicrophoneUsageDescription` is already in `ios/project.yml:163-167`, so
/// no `project.yml` change is needed for this item.
public protocol DropInMicPermission: Sendable {
    /// True when the microphone may be used. Asks the person the first time
    /// and answers from the stored decision afterwards.
    func request() async -> Bool
}

#if os(iOS)
import AVFoundation

/// `AVAudioApplication` (iOS 17+, and this app's floor is 18 —
/// `project.yml:20`). The deprecated `AVAudioSession.requestRecordPermission`
/// is not used.
public struct AVAudioMicPermission: DropInMicPermission {

    public init() {}

    public func request() async -> Bool {
        switch AVAudioApplication.shared.recordPermission {
        case .granted: return true
        case .denied: return false
        case .undetermined:
            return await ask()
        @unknown default:
            return await ask()
        }
    }

    /// The completion-handler form, wrapped. `AVAudioApplication` has no
    /// documented `async` overload, and a guess at one is a compile error the
    /// package's own tests cannot catch — this file's iOS half is skipped by
    /// `swift test` (``DropInAudioEngine`` says why).
    private func ask() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }
}
#endif

/// The answer a test gives.
public struct FixedMicPermission: DropInMicPermission {
    private let allowed: Bool
    public init(_ allowed: Bool) { self.allowed = allowed }
    public func request() async -> Bool { allowed }
}
