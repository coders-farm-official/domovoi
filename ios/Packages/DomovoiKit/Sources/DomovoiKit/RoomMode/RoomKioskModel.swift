import Combine
import Foundation

/// `ProcessInfo.thermalState`, as room mode reads it (ROOMMODE-MAP D8, §3.8).
public enum RoomThermal: Int, Comparable, Sendable {
    case nominal, fair, serious, critical

    public init(_ state: ProcessInfo.ThermalState) {
        switch state {
        case .nominal: self = .nominal
        case .fair: self = .fair
        case .serious: self = .serious
        case .critical: self = .critical
        @unknown default: self = .serious
        }
    }

    public static func < (a: RoomThermal, b: RoomThermal) -> Bool { a.rawValue < b.rawValue }
}

/// The room screen's few powers over the phone, which only UIKit has
/// (the app's `UIApplication.isIdleTimerDisabled` and the screen's
/// brightness; a recorder in the tests).
@MainActor
public protocol RoomScreenControl: AnyObject {
    /// Keep the screen from locking (§3.3).
    func keepAwake(_ on: Bool)
    /// Dim to the 0.1 floor (never above the person's own level), or put
    /// the person's level back (D9).
    func dim(_ on: Bool)
}

/// Where the room screen's content sits this minute, against burn-in
/// (`RoomModeActivity.kt:332-339`): ±12 points.
public struct RoomDrift: Equatable, Sendable {
    public var x: Double
    public var y: Double
    public static let zero = RoomDrift(x: 0, y: 0)
    public static let reach = 12
}

/// The room screen (`roommode/RoomModeActivity.kt` plus `RoomService.kt`'s
/// start rules), as one model the kiosk view draws:
///
/// - starts the room only with the screen in the foreground and stops it
///   when Domovoi leaves it, posting "tap to resume" (§3.1, §3.2); comes back
///   by itself on return when the room was running;
/// - re-checks ``RoomLockRules`` at every return (a household phone whose
///   passcode went stops with ``RoomStatusText/needsLock``);
/// - Mute, Resume, check now, and Exit (held, then the phone's unlock, then
///   Keep / Forget);
/// - keeps the screen awake only while the room runs on screen (§3.3), dims
///   it after 60 s with nothing to show (D9), drifts it once a minute;
/// - the heat response (D8) and the Guided Access tip (§3.5).
///
/// The engine is anything that is ``RoomEngineControl`` — R2's `RoomEngine`
/// in the app, a fake in the tests and previews.
@MainActor
public final class RoomKioskModel: ObservableObject {

    public enum Dialog: Equatable, Sendable { case confirmNoLock, setALock, keepOrForget }

    @Published public private(set) var status: RoomStatus? = nil
    /// An engine is up (Android: `running && engine != null`).
    @Published public private(set) var engineUp = false
    @Published public private(set) var needsLock = false
    @Published public private(set) var micPermissionOff = false
    /// The heat stop: not listening until it has cooled for a minute.
    @Published public private(set) var tooHot = false
    /// `.serious` or worse: darkest display, no animation.
    @Published public private(set) var hot = false
    @Published public var dialog: Dialog? = nil
    @Published public private(set) var dimmed = false
    @Published public private(set) var drift = RoomDrift.zero
    @Published public private(set) var guidedAccessOn = false
    @Published public private(set) var guidedTipDismissed: Bool

    public let label: String

    private let store: RoomStore
    private let makeEngine: @MainActor () -> any RoomEngineControl
    private let lock: any RoomLockFacts
    private let screen: any RoomScreenControl
    private let notices: any RoomNoticePosting
    private let micGranted: @MainActor () -> Bool
    private let now: @MainActor () -> TimeInterval
    private let onLeave: @MainActor (Bool) -> Void

    private var engine: (any RoomEngineControl)?
    private var watching: Task<Void, Never>?
    private var foreground = false
    private var lastActivity: TimeInterval
    private var lastDrift: TimeInterval
    private var thermal = RoomThermal.nominal
    private var coolSince: TimeInterval?
    private var interruptedSince: TimeInterval?
    private var interruptionNoticed = false

    /// Idle this long (no touch, no turn) and the screen dims (D9).
    public static let dimAfterSeconds: TimeInterval = 60
    /// Content moves this often (`RoomModeActivity.kt:337`).
    public static let driftEverySeconds: TimeInterval = 60
    /// After the heat stop, `.fair` or better this long before listening again.
    public static let coolForSeconds: TimeInterval = 60

    public init(
        store: RoomStore,
        makeEngine: @escaping @MainActor () -> any RoomEngineControl,
        lock: any RoomLockFacts,
        screen: any RoomScreenControl,
        notices: any RoomNoticePosting,
        micGranted: @escaping @MainActor () -> Bool,
        now: @escaping @MainActor () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        onLeave: @escaping @MainActor (_ forgot: Bool) -> Void
    ) {
        self.store = store
        self.makeEngine = makeEngine
        self.lock = lock
        self.screen = screen
        self.notices = notices
        self.micGranted = micGranted
        self.now = now
        self.onLeave = onLeave
        label = store.enrolment?.roomLabel ?? "room"
        guidedTipDismissed = store.guidedAccessTipDismissed
        let t = now()
        lastActivity = t
        lastDrift = t
    }

    // MARK: - what the screen draws

    public var running: Bool { store.running }
    public var wakeWord: String { store.wakeWord }

    public var ring: RoomStatusText.Ring {
        RoomStatusText.ring(status, running: store.running && engineUp)
    }

    /// The pulse and the arc run only during a turn, and never when hot.
    public var animates: Bool { !hot && RoomStatusText.isTurn(ring) }

    public var detail: String? {
        if needsLock { return RoomStatusText.needsLock }
        if tooHot { return RoomStatusText.tooHot }
        if micPermissionOff { return RoomStatusText.micPermissionOff }
        return RoomStatusText.detail(status, running: store.running && engineUp, wakeWord: store.wakeWord)
    }

    /// The code to approve on the dashboard, while the core asks for one.
    public var approvalCode: String? {
        guard engineUp, case .awaitingApproval(let code, _)? = status?.link else { return nil }
        return code
    }

    public var muted: Bool { status?.muted ?? store.muted }

    /// Follows the recorder itself, never the Mute button.
    public var micOpen: Bool { engineUp && (status?.micOpen ?? false) }
    public var micLive: Bool { status?.micLive ?? false }

    /// The one big button: Mute while the room runs, Resume when it was
    /// stopped; none while a passcode, the heat or the microphone permission
    /// is what holds it (the line says so, and Resume could not help).
    public enum Action: Equatable, Sendable { case mute, unmute, resume, openSettings, none }

    public var action: Action {
        if needsLock || tooHot { return .none }
        if micPermissionOff { return .openSettings }
        if store.running && engineUp { return muted ? .unmute : .mute }
        return .resume
    }

    public var showsGuidedAccessTip: Bool { !guidedAccessOn && !guidedTipDismissed }

    // MARK: - the scene (ROOMMODE-MAP §3.1)

    /// The scene is `.active`: start (or resume) the room.
    public func enteredForeground() {
        foreground = true
        notices.cancel()
        noteActivity()
        if !tooHot { checkLockAndStart() }
        applyAwake()
    }

    /// The scene is `.background`: iOS will not keep a microphone for a
    /// room nobody can see (Q9), so it stops, truthfully offline on the
    /// dashboard, and asks for a tap. `.inactive` (Control Centre, a banner)
    /// is not this.
    public func enteredBackground() {
        foreground = false
        let wasListening = engine != nil
        stopEngine()
        if wasListening,
           RoomModeNotices.shouldPost(.leftForeground, active: store.active, running: store.running, inBackground: true) {
            notices.post(title: RoomModeNotices.title(roomLabel: label), body: RoomModeNotices.body(.leftForeground))
        }
        if dimmed {
            dimmed = false
            screen.dim(false)
        }
        applyAwake()
    }

    /// Start the room unless a rule says not (`RoomModeActivity.checkLockAndStart`
    /// and `RoomService.onStartCommand`).
    public func checkLockAndStart() {
        let holds = lock.holdsHouseholdToken
        let secure = lock.deviceSecure
        let lockOK = RoomLockRules.mayRun(holdsHouseholdToken: holds, deviceSecure: secure)
        if needsLock != !lockOK { needsLock = !lockOK }
        let decision = RoomLockRules.start(
            stickyRestart: false,
            active: store.active,
            enrolled: store.enrolment != nil,
            running: store.running,
            micGranted: micGranted(),
            holdsHouseholdToken: holds,
            deviceSecure: secure
        )
        let permissionOff = decision == .askForTap && store.running
        if micPermissionOff != permissionOff { micPermissionOff = permissionOff }
        switch decision {
        case .run:
            // The start rule says the room MAY run; whether it was running
            // (not stopped by the person) is the screen's to keep
            // (`RoomModeActivity.kt:208`, `if (store.running && engine == null)`).
            if store.running && engine == nil { startEngine() }
        case .needsLock, .askForTap, .nothing:
            stopEngine()
        }
    }

    private func startEngine() {
        let e = makeEngine()
        engine = e
        engineUp = true
        status = e.status
        watching = Task { @MainActor [weak self] in
            for await s in e.statusUpdates() {
                guard let self, self.engine === e else { return }
                self.statusChanged(s)
            }
        }
        if !e.start() { status = e.status }
        applyAwake()
    }

    private func stopEngine() {
        watching?.cancel()
        watching = nil
        engine?.stop()
        engine = nil
        if engineUp { engineUp = false }
        if status != nil { status = nil }
        interruptedSince = nil
        applyAwake()
    }

    private func statusChanged(_ s: RoomStatus) {
        let wasTurn = RoomStatusText.isTurn(ring)
        status = s
        if RoomStatusText.isTurn(ring) && !wasTurn { noteActivity() }
        if s.stoppedByIOS == .interrupted {
            if interruptedSince == nil { interruptedSince = now() }
        } else {
            interruptedSince = nil
            interruptionNoticed = false
        }
    }

    private func applyAwake() {
        screen.keepAwake(foreground && engineUp)
    }

    // MARK: - the buttons

    public func perform(_ a: Action) {
        switch a {
        case .mute: setMuted(true)
        case .unmute: setMuted(false)
        case .resume: resume()
        case .openSettings, .none: noteActivity()
        }
    }

    public func setMuted(_ on: Bool) {
        noteActivity()
        store.setMuted(on)
        engine?.setMuted(on)
    }

    public func resume() {
        noteActivity()
        store.setRunning(true)
        checkLockAndStart()
    }

    public func checkNow() {
        noteActivity()
        engine?.retryNow()
    }

    // MARK: - leaving (`RoomModeActivity.askToLeave`, `leave`)

    /// Exit was HELD (a tap does nothing: a room is not left by accident).
    public func exitHeld() {
        noteActivity()
        switch RoomLockRules.exit(holdsHouseholdToken: lock.holdsHouseholdToken, deviceSecure: lock.deviceSecure) {
        case .deviceCredential:
            lock.unlock(reason: RoomModeStrings.unlockReason) { ok in
                guard ok else { return }
                Task { @MainActor in self.dialog = .keepOrForget }
            }
        case .confirmNoLock:
            dialog = .confirmNoLock
        case .setALockFirst:
            dialog = .setALock
        }
    }

    /// "Exit" on the no-passcode confirmation.
    public func confirmExit() { dialog = .keepOrForget }

    public func dismissDialog() { dialog = nil }

    /// Keep this room (`forget: false`) or Forget this room.
    public func leave(forget: Bool) {
        dialog = nil
        stopEngine()
        notices.cancel()
        foreground = false
        applyAwake()
        if dimmed {
            dimmed = false
            screen.dim(false)
        }
        store.setActive(false)
        if forget { store.forget() }
        onLeave(forget)
    }

    // MARK: - the display (D9, §3.4)

    /// Any touch: the person's brightness back (not while hot).
    public func touched() { noteActivity() }

    private func noteActivity() {
        lastActivity = now()
        if dimmed && !hot {
            dimmed = false
            screen.dim(false)
        }
    }

    /// Called every few seconds while the room screen is up.
    public func tick() {
        let t = now()
        if t - lastDrift >= Self.driftEverySeconds {
            lastDrift = t
            drift = RoomDrift(
                x: Double(Int.random(in: -RoomDrift.reach...RoomDrift.reach)),
                y: Double(Int.random(in: -RoomDrift.reach...RoomDrift.reach))
            )
        }
        if !dimmed, foreground, dialog == nil, !RoomStatusText.isTurn(ring), t - lastActivity >= Self.dimAfterSeconds {
            dimmed = true
            screen.dim(true)
        }
        if tooHot, thermal <= .fair, let since = coolSince, t - since >= Self.coolForSeconds {
            tooHot = false
            coolSince = nil
            if foreground { checkLockAndStart() }
        }
        if let since = interruptedSince, !interruptionNoticed, t - since >= RoomModeNotices.interruptionGraceSeconds {
            interruptionNoticed = true
            if RoomModeNotices.shouldPost(.interruptedTooLong, active: store.active, running: store.running, inBackground: !foreground) {
                notices.post(title: RoomModeNotices.title(roomLabel: label), body: RoomModeNotices.body(.interruptedTooLong))
            }
        }
    }

    /// `thermalStateDidChangeNotification` (D8).
    public func thermalChanged(_ t: RoomThermal) {
        thermal = t
        if t >= .serious {
            if !hot { hot = true }
            if !dimmed {
                dimmed = true
                screen.dim(true)
            }
        } else if hot {
            hot = false
        }
        if t == .critical {
            coolSince = nil
            if !tooHot {
                tooHot = true
                let wasRunning = engine != nil || store.running
                stopEngine()
                if wasRunning,
                   RoomModeNotices.shouldPost(.tooHot, active: store.active, running: store.running, inBackground: !foreground) {
                    notices.post(title: RoomModeNotices.title(roomLabel: label), body: RoomModeNotices.body(.tooHot))
                }
            }
        } else if t <= .fair, tooHot, coolSince == nil {
            coolSince = now()
        } else if t == .serious {
            coolSince = nil
        }
    }

    // MARK: - Guided Access (§3.5)

    public func guidedAccessChanged(_ on: Bool) {
        if guidedAccessOn != on { guidedAccessOn = on }
    }

    public func dismissGuidedAccessTip() {
        store.dismissGuidedAccessTip()
        guidedTipDismissed = true
    }
}
