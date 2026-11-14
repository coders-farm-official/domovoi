import Combine
import Foundation

/// The permissions setup asks for, in context (the app's `AVAudioApplication`,
/// `AVCaptureDevice` and `UNUserNotificationCenter`; a fake in the tests and
/// in a UI-test launch, where a system prompt would stop the run).
@MainActor
public protocol RoomSetupPermissions: AnyObject {
    var micGranted: Bool { get }
    /// Asks for the microphone; `done(granted)` on the main actor.
    func requestMic(_ done: @escaping @MainActor (Bool) -> Void)
    /// Asks once for the resume notification (§3.2). A refusal changes
    /// nothing: the room screen's own line still says it.
    func requestNotifications()
    /// The phone has a camera at all (a Simulator has none).
    var cameraAvailable: Bool { get }
    var cameraGranted: Bool { get }
    func requestCamera(_ done: @escaping @MainActor (Bool) -> Void)
}

/// The way into room mode, one decision a screen
/// (`roommode/RoomSetupActivity.kt`, ROOMMODE-MAP §1):
///
/// 1. the explainer, read to its end before **Start room mode** works;
/// 2. the microphone (and the resume notification) asked for here;
/// 3. the room code: scanned or pasted, shown back (server, key, room name,
///    which can be changed), then **Become this room**.
///
/// A link or a scan only fills the form: nothing becomes a room until the
/// person has read the explainer and pressed the buttons. The fourth screen —
/// the approval code — is the room screen itself.
@MainActor
public final class RoomSetupModel: ObservableObject {

    public enum Step: Equatable, Sendable { case explain, microphone, code }

    @Published public private(set) var step: Step = .explain
    /// The explainer was scrolled to its end (the view reports it).
    @Published public var readToTheEnd = false
    /// Room mode may run here (``RoomLockRules``): re-read at every return,
    /// so setting a passcode in Settings lets the person straight on.
    @Published public private(set) var lockOK = true
    @Published public private(set) var micDenied = false
    /// The pasted or scanned text (trimmed as it changes, as Android's field).
    @Published public private(set) var text: String
    /// A room name typed here; nil while the code's own name stands.
    @Published public var editedName: String? = nil
    @Published public private(set) var problem: String? = nil
    @Published public private(set) var scanning = false

    private let lock: any RoomLockFacts
    private let permissions: any RoomSetupPermissions
    private let micRefusal: @MainActor () -> String?
    private let onBegin: @MainActor (RoomEnrolment) -> Bool

    /// `onBegin` becomes the room (the app's scene switch: enrol, set active
    /// and running, swap to the room screen); false when the store would not
    /// keep the key.
    public init(
        payload: String?,
        lock: any RoomLockFacts,
        permissions: any RoomSetupPermissions,
        micRefusal: @escaping @MainActor () -> String?,
        onBegin: @escaping @MainActor (RoomEnrolment) -> Bool
    ) {
        text = (payload ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        self.lock = lock
        self.permissions = permissions
        self.micRefusal = micRefusal
        self.onBegin = onBegin
        refreshLock()
    }

    // MARK: - steps

    public func refreshLock() {
        let ok = RoomLockRules.mayRun(holdsHouseholdToken: lock.holdsHouseholdToken, deviceSecure: lock.deviceSecure)
        if ok != lockOK { lockOK = ok }
    }

    /// **Start room mode**: only once the explainer was read to the end.
    public func start() {
        guard readToTheEnd, step == .explain else { return }
        refreshLock()
        step = .microphone
        if permissions.micGranted { step = .code }
    }

    /// **Allow**: the microphone, and the one notification, then the code.
    public func allowMic() {
        permissions.requestNotifications()
        permissions.requestMic { [weak self] granted in
            guard let self else { return }
            if granted || self.permissions.micGranted {
                self.micDenied = false
                self.step = .code
            } else {
                self.micDenied = true
            }
        }
    }

    // MARK: - the room code

    public func setText(_ value: String) {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed != text else { return }
        text = trimmed
        editedName = nil
        problem = nil
    }

    /// A `domovoi-room:` link opened while setup is showing fills the form.
    public func incoming(_ payload: String) { setText(payload) }

    public var parsed: RoomEnrolmentParse? {
        text.isEmpty ? nil : RoomEnrolmentParser.parse(text)
    }

    public var enrolment: RoomEnrolment? { parsed?.enrolment }

    /// Why the text is not a room code, in words.
    public var refusal: String? { parsed?.refusal?.words }

    /// The name in the field: the typed one, or the code's own.
    public var shownName: String { editedName ?? enrolment?.roomLabel ?? "" }

    /// A typed name is trimmed and held to the code's rules.
    public var nameRefusal: String? {
        guard editedName != nil else { return nil }
        return RoomEnrolmentParser.typedRoomName(shownName)?.words
    }

    private var label: String? {
        guard let e = enrolment else { return nil }
        return editedName != nil ? shownName.trimmingCharacters(in: .whitespacesAndNewlines) : e.roomLabel
    }

    public var roomID: String? {
        guard nameRefusal == nil, let label else { return nil }
        return RoomEnrolmentParser.roomID(for: label)
    }

    public var canBecome: Bool { roomID != nil }

    public func scan() {
        problem = nil
        guard permissions.cameraAvailable else {
            problem = RoomModeStrings.noCamera
            return
        }
        if permissions.cameraGranted {
            scanning = true
            return
        }
        permissions.requestCamera { [weak self] ok in
            guard let self else { return }
            if ok { self.scanning = true } else { self.problem = RoomModeStrings.noCamera }
        }
    }

    public func scanned(_ code: String) {
        scanning = false
        setText(code)
    }

    public func stopScanning() { scanning = false }

    /// **Become this room.** Returns whether the phone is a room now.
    @discardableResult
    public func become() -> Bool {
        refreshLock()
        guard lockOK, let e = enrolment, let roomID, let label else { return false }
        if let refusal = micRefusal() {
            problem = RoomModeStrings.micHeld(refusal)
            return false
        }
        let chosen = RoomEnrolment(coreURL: e.coreURL, fingerprint: e.fingerprint, roomID: roomID, roomLabel: label)
        guard onBegin(chosen) else {
            problem = RoomModeStrings.keyNotKept
            return false
        }
        return true
    }
}
