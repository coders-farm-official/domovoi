import Foundation
import Observation

// ===========================================================================
// Away chat on this phone (1.5.0): whether it is set up, the offer, and when
// the Tor engine may run.
// ===========================================================================

/// Whether the phone is at home, by the app's own reckoning: the shell's
/// screen. Away is the one leaf that means the saved house is out of reach
/// (``LocalReason/outOfReach``, which the reachability probe decides);
/// every other screen is either at home (the workspace, pairing, the token
/// card, the first Connecting moment) or has no house to chat with.
public func awayIsAway(_ screen: ShellScreen) -> Bool {
    screen == .localMedia(.outOfReach)
}

/// When the Tor engine runs, and the chat screen's away link while it does.
///
/// **The rule (core-0070):** the engine runs only while the chat screen is
/// open **and** the phone is off the home network, and never in the
/// background. It stops ``stopDelaySec`` after the chat closes (a quick look
/// at another tab does not pay for a new Tor start), at once when the phone
/// gets home (chat goes back to today's path) and at once when the app leaves
/// the screen.
@MainActor
@Observable
public final class AwayChatController {

    /// What the chat's header says about the connection.
    public enum Phase: Equatable, Sendable {
        /// Not running.
        case off
        case connecting
        case ready
        case failed(String)
    }

    public static let stopDelaySec: Double = 15

    public private(set) var phase: Phase = .off
    /// The away link for ``ChatModel``, while a session exists.
    public private(set) var link: (any ChatAwayLink)? {
        didSet { linkEpoch += 1 }
    }
    /// Moves whenever ``link`` does, for a view to watch.
    public private(set) var linkEpoch = 0

    public private(set) var chatOpen = false
    public private(set) var atHome = true
    public private(set) var active = true

    private let keys: AwayKeyStore
    private let transport: any AwayTransport
    private let version: String
    private let server: @MainActor () -> String
    private let clock: AwayClock
    private let stopDelay: Double
    private let directories: AwayTorDirectories?
    private var session: AwaySession?
    private var sessionToken = 0
    private var stopping: Task<Void, Never>?

    public init(
        keys: AwayKeyStore,
        transport: any AwayTransport,
        version: String,
        server: @MainActor @escaping () -> String,
        clock: AwayClock = .system,
        stopDelay: Double = AwayChatController.stopDelaySec,
        directories: AwayTorDirectories? = nil
    ) {
        self.keys = keys
        self.transport = transport
        self.version = version
        self.server = server
        self.clock = clock
        self.stopDelay = stopDelay
        self.directories = directories
    }

    /// The enrolment for the active server, if any.
    public var enrolment: AwayEnrolment? { keys.enrolment(for: server()) }

    /// The chat on screen goes the away path: the phone is away and has the
    /// keys for this house. At home, chat is today's path, whatever is kept.
    public var awayPath: Bool { !atHome && enrolment != nil }

    /// The engine may run now.
    public var wanted: Bool { chatOpen && awayPath && active }

    /// Whether the engine is running (a session exists).
    public var running: Bool { session != nil }

    /// The house is out of reach, away chat isn't set up for it now, and the
    /// house is why (§8 item 8): the chat says so rather than offering a
    /// composer that can't send.
    public var lapsed: Bool { !atHome && enrolment == nil && keys.lapsed(server()) }

    // MARK: inputs

    public func chatOpened() {
        chatOpen = true
        reconcile()
    }

    public func chatClosed() {
        chatOpen = false
        reconcile()
    }

    public func homeChanged(atHome: Bool) {
        guard atHome != self.atHome else { return }
        self.atHome = atHome
        reconcile()
    }

    public func activeChanged(_ active: Bool) {
        guard active != self.active else { return }
        self.active = active
        reconcile()
    }

    /// The keys came or went (turned off, wiped, set up again).
    public func keysChanged() {
        if session != nil { stopNow() }
        reconcile()
    }

    // MARK: the rule

    private func reconcile() {
        if wanted {
            stopping?.cancel()
            stopping = nil
            if session == nil { start() }
            return
        }
        guard session != nil else { return }
        if chatOpen == false && active && !atHome && enrolment != nil {
            // Closed, still away and on screen: a short grace first.
            guard stopping == nil else { return }
            let delay = stopDelay
            let clock = clock
            stopping = Task { @MainActor [weak self] in
                try? await clock.sleep(delay)
                guard !Task.isCancelled, let self, !self.wanted else { return }
                self.stopNow()
            }
        } else {
            // Home, in the background, or no keys: at once.
            stopNow()
        }
    }

    private func start() {
        guard let enrolment else { return }
        sessionToken += 1
        let token = sessionToken
        let server = enrolment.server
        let keys = keys
        let session = AwaySession(
            enrolment: enrolment, transport: transport, version: version, clock: clock,
            closed: { [weak self] why in
                Task { @MainActor in self?.connectionClosed(why, server: server, token: token, keys: keys) }
            }
        )
        self.session = session
        link = AwayChatClient(session: session)
        guard transport.available else {
            phase = .failed(AwayStrings.unreachable)
            return
        }
        // Tor reads the house's client-authorization key from its auth
        // folder: written before it starts, deleted when it stops.
        if let directories {
            do {
                try directories.writeAuth(onion: enrolment.onion, key: enrolment.clientAuth)
            } catch {
                phase = .failed(AwayStrings.unreachable)
                return
            }
        }
        phase = .connecting
        Task { @MainActor [weak self] in
            do {
                _ = try await session.connection()
                guard let self, self.sessionToken == token else { return }
                self.phase = .ready
            } catch {
                guard let self, self.sessionToken == token else { return }
                if error as? AwayClosed == .silent { keys.markRecheck(server) }
                self.phase = .failed(awayFailureLine(error))
            }
        }
    }

    /// A connection ended. Silence, `revoked` and `away_off` all mean the
    /// next connect will fail silently: the house is asked why next time the
    /// phone is home (§5, §8 item 8). Idle and replaced connections are
    /// opened again by the next request.
    private func connectionClosed(_ why: AwayClosed, server: String, token: Int, keys: AwayKeyStore) {
        switch why {
        case .silent, .bye("revoked"), .bye("away_off"):
            keys.markRecheck(server)
        default:
            break
        }
        guard sessionToken == token, session != nil else { return }
        switch why {
        case .bye("revoked"), .bye("away_off"), .bye("shutting_down"), .silent:
            phase = .failed(AwayStrings.line(for: why))
        default:
            break
        }
    }

    /// A request on the link worked or failed: the header follows the last
    /// word (a reconnect that worked clears an old failure).
    public func linkAnswered() {
        if session != nil, transport.available { phase = .ready }
    }

    private func stopNow() {
        stopping?.cancel()
        stopping = nil
        sessionToken += 1
        let s = session
        session = nil
        link = nil
        phase = .off
        let directories = directories
        guard let s else {
            directories?.removeAuth()
            return
        }
        Task { @MainActor [weak self] in
            await s.stop()
            // Not when a new session started meanwhile: its key is the file.
            if self?.session == nil { directories?.removeAuth() }
        }
    }
}

/// Away chat in Settings and at pairing, and the controller.
@MainActor
@Observable
public final class AwayModel {

    public let controller: AwayChatController
    private let keys: AwayKeyStore
    private let api: AwayEnrolApi
    private let server: @MainActor () -> String

    /// A turn on or off is on its way.
    public private(set) var busy = false
    /// The last thing Settings has to say (a refusal, "starting", the house's
    /// reason it can't work). Nil: nothing.
    public private(set) var line: String?
    /// The offer right after pairing is up.
    public private(set) var offerUp = false
    /// The house's last answer to `GET /api/away/status`.
    public private(set) var status: AwayStatus?
    /// Bumped when the keys come or go, so a view re-reads ``enrolled``.
    public private(set) var keysTick = 0

    public init(keys: AwayKeyStore, api: AwayEnrolApi, transport: any AwayTransport, version: String,
                server: @MainActor @escaping () -> String, clock: AwayClock = .system,
                stopDelay: Double = AwayChatController.stopDelaySec, directories: AwayTorDirectories? = nil) {
        self.keys = keys
        self.api = api
        self.server = server
        self.controller = AwayChatController(keys: keys, transport: transport, version: version, server: server,
                                             clock: clock, stopDelay: stopDelay, directories: directories)
        self.engineAvailable = transport.available
    }

    /// This build has a Tor engine.
    public let engineAvailable: Bool

    /// Set up for the active server.
    public var enrolled: Bool {
        _ = keysTick
        return keys.enrolment(for: server()) != nil
    }

    /// The house said this phone isn't enrolled any more.
    public var lapsed: Bool {
        _ = keysTick
        return keys.lapsed(server())
    }

    private var atHome: Bool { controller.atHome }

    private func keysMoved() {
        keysTick += 1
        controller.keysChanged()
    }

    /// The keys came or went from somewhere else (a server switch or forget,
    /// a device-key start-over).
    public func keysChanged() { keysMoved() }

    // MARK: the offer

    /// A token was just accepted at home: offer away chat, once, if it isn't
    /// set up.
    public func paired() {
        guard !enrolled else { return }
        offerUp = true
    }

    public func offerAnswered(_ yes: Bool) {
        offerUp = false
        if yes { Task { await turnOn() } }
    }

    // MARK: on and off

    /// Make the keys, enrol them, keep them. Home network only (the house
    /// answers `lan_only` otherwise, and that is said).
    public func turnOn() async {
        guard !busy else { return }
        let at = server()
        guard !ServerCredentials.isBlank(at) else { return }
        busy = true
        line = nil
        defer { busy = false }
        do {
            let enrolment = try await api.enroll(server: at)
            // The person moved to another house meanwhile: nothing is kept
            // (the house's copy is replaced by the next enrolment there).
            if enrolment.server != ServerCredentials.normalize(server()) { return }
            if !keys.keep(enrolment) {
                line = AwayStrings.somethingWrong
                return
            }
            keysMoved()
            line = engineAvailable ? nil : AwayStrings.engineMissing
            status = AwayStatus(house: "on", enrolled: true, state: nil)
        } catch {
            line = error.line
            if error == .awayOff || error == .internetOff {
                status = AwayStatus(house: error == .awayOff ? "away_off" : "internet_off", enrolled: false, state: nil)
            }
        }
    }

    /// Off for this phone: the house revokes the keys, and they are wiped
    /// here whatever the house answered (the private halves are what make
    /// them work; without them the house's copy opens nothing).
    public func turnOff() async {
        guard !busy else { return }
        busy = true
        line = nil
        defer { busy = false }
        let at = server()
        do {
            try await api.unenroll()
        } catch {
            line = error == .lanOnly ? AwayStrings.homeNetworkOnly : nil
        }
        keys.wipe(server: at)
        keysMoved()
    }

    /// The forget-this-server parting: off at the house while the token still
    /// works, then the keys go (the store's forget signal wipes them too).
    public func parting(_ forgotten: String) async {
        guard keys.enrolment(for: forgotten) != nil else { return }
        try? await api.unenroll()
        keys.wipe(server: forgotten)
        keysMoved()
    }

    // MARK: the house's word

    /// The phone is home: when a connect met silence since, or Settings is
    /// open, ask the house why (§8 item 8). Not enrolled any more: the keys
    /// go, and Settings says so and offers it again.
    public func checkAtHome(force: Bool = false) async {
        let at = server()
        guard atHome, !ServerCredentials.isBlank(at) else { return }
        guard force || keys.needsRecheck(at) else { return }
        do {
            let s = try await api.status()
            guard ServerCredentials.normalize(server()) == ServerCredentials.normalize(at) else { return }
            status = s
            keys.clearRecheck()
            if !s.enrolled && keys.enrolment(for: at) != nil {
                keys.markLapsed(at)
                keysMoved()
            }
            line = statusLine(s)
        } catch {
            // Not answered: asked again next time.
        }
    }

    private func statusLine(_ s: AwayStatus) -> String? {
        switch s.house {
        case "away_off": return AwayStrings.awayOff
        case "internet_off": return AwayStrings.internetOff
        default:
            if !s.enrolled { return lapsed ? AwayStrings.notSetUpAnyMore : nil }
            return s.state == "starting" ? AwayStrings.startingUp : (engineAvailable ? nil : AwayStrings.engineMissing)
        }
    }

    /// The active server changed or was forgotten.
    public func serverChanged() {
        line = nil
        status = nil
        offerUp = false
        keysMoved()
    }
}
