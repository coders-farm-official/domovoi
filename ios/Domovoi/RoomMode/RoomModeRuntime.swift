import AVFoundation
import DomovoiKit
import Darwin
import SwiftUI
import UIKit
import UserNotifications

/// Room mode's UIKit glue: the engine it builds, the screen it keeps awake
/// and dims, the notice it posts and the permissions it asks for
/// (ROOMMODE-MAP §3; Android's `RoomService.kt` and `RoomNotifications`).
///
/// No foreground service, no ongoing notification, no boot receiver (§3.7):
/// the engine lives exactly as long as the room screen is in the foreground,
/// which ``RoomKioskModel`` decides.
@MainActor
enum RoomModeRuntime {

    /// The real room (R2's ``RoomEngine``), on this phone's microphone — or,
    /// under the UI tests' `-domovoi-room-feed`, on quiet noise.
    ///
    /// The microphone goes through ``PlayerMicArbiter`` as `.roomMode` for as
    /// long as the engine runs (§4): two closures, because the engine's
    /// folder never names the arbiter (it uses `Mutex`, §6.0).
    static func makeEngine(store: RoomStore, feed: Bool) -> any RoomEngineControl {
        let pcm: RoomPCMSourceMaking
        if feed {
            pcm = { RoomFeedPCMSource(paced: true) }
        } else {
            pcm = microphone
        }
        let acquireMic: @Sendable () -> (@Sendable () -> Void)? = {
            guard let lease = PlayerMicArbiter.tryAcquire(.roomMode) else { return nil }
            let release: @Sendable () -> Void = { lease.release() }
            return release
        }
        let micRefusal: @Sendable () -> String? = { PlayerMicArbiter.refusal(.roomMode) }
        return RoomEngine.live(
            store: store,
            pcm: pcm,
            device: deviceFacts,
            directory: roomDirectory(),
            acquireMic: acquireMic,
            micRefusal: micRefusal
        )
    }

    /// This phone's microphone.
    ///
    /// **On the base branch there is none to give**: R2's `AVAudioEngine`
    /// source (`RoomAudioSource.making`, ROOMMODE-MAP §6.3) lands with R2,
    /// and this is the one line R3's rebase onto R2 changes. Until then it
    /// refuses to open, with the reason — never a feed of noise passed off as
    /// a microphone (no silent fallbacks).
    static let microphone: RoomPCMSourceMaking = { UnbuiltMicrophone() }

    /// The phone's battery and process facts for the health frame.
    ///
    /// R2's `RoomModeDeviceFacts` (UIKit) is the real one and replaces this
    /// at the rebase; until then every fact is "this phone will not say",
    /// which the health frame leaves out (never made-up numbers).
    static let deviceFacts: any RoomDeviceFacts = UnsaidRoomDeviceFacts()

    /// Application Support/room: the models and sounds the room syncs,
    /// excluded from backups (they come from the server again).
    static func roomDirectory() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        var dir = base.appendingPathComponent("room", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? dir.setResourceValues(values)
        return dir
    }

    /// The room's lock facts: the phone's own, or fixed under a UI test.
    static func lockFacts(fixed: Bool) -> any RoomLockFacts {
        if fixed { return FixedRoomLockFacts() }
        return SystemRoomLockFacts(holdsHouseholdToken: { TokenVault().holdsAnyToken() })
    }

    static var micGranted: Bool {
        AVAudioApplication.shared.recordPermission == .granted
    }
}

/// See ``RoomModeRuntime/microphone``.
private final class UnbuiltMicrophone: RoomPCMSource {
    func start(call: Bool) throws {
        throw RoomMicFailure(stop: nil, note: "room mode is not built into this version yet")
    }
    func read(into frame: inout [Int16]) -> Int { -1 }
    func stop() {}
}

/// See ``RoomModeRuntime/deviceFacts``.
private struct UnsaidRoomDeviceFacts: RoomDeviceFacts {
    func battery() -> RoomBattery { RoomBattery(percent: nil, charging: nil, onPower: nil) }
    func batteryChanges() -> AsyncStream<RoomBattery> { AsyncStream { _ in } }
    func rssKB() -> Int64? { nil }
    func threads() -> Int? { nil }
    func cpuSeconds() -> Double? { nil }
    func uptimeSeconds() -> Int64 { Int64(ProcessInfo.processInfo.systemUptime) }
    func processUptimeSeconds() -> Int64 { 0 }
}

/// `isIdleTimerDisabled` and the brightness (§3.3, D9).
///
/// The person's own level is taken when the screen dims and put back on
/// undim; it is never raised above it. If the app is killed while dimmed the
/// level stays low until auto-brightness moves it: accepted (§3.4).
@MainActor
final class RoomScreen: RoomScreenControl {
    static let floor: CGFloat = 0.1
    private var personLevel: CGFloat?

    private var screen: UIScreen? {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first { $0.activationState == .foregroundActive }?.screen
            ?? UIApplication.shared.connectedScenes.compactMap { ($0 as? UIWindowScene)?.screen }.first
    }

    func keepAwake(_ on: Bool) {
        if UIApplication.shared.isIdleTimerDisabled != on {
            UIApplication.shared.isIdleTimerDisabled = on
        }
    }

    func dim(_ on: Bool) {
        guard let screen else { return }
        if on {
            if personLevel == nil { personLevel = screen.brightness }
            screen.brightness = min(Self.floor, personLevel ?? Self.floor)
        } else if let level = personLevel {
            screen.brightness = level
            personLevel = nil
        }
    }
}

/// The resume notice through `UNUserNotificationCenter` (§3.2). If the
/// person refused notifications, nothing is posted and the room screen's own
/// line still says it.
@MainActor
final class RoomNotices: RoomNoticePosting {
    func post(title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let request = UNNotificationRequest(identifier: RoomModeNotices.identifier, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { _ in }
    }

    func cancel() {
        let centre = UNUserNotificationCenter.current()
        centre.removeDeliveredNotifications(withIdentifiers: [RoomModeNotices.identifier])
        centre.removePendingNotificationRequests(withIdentifiers: [RoomModeNotices.identifier])
    }
}

/// Setup's permissions: the microphone and the notice (step 2), the camera
/// (the scanner). Under `-domovoi-room-feed` nothing is asked: a system
/// prompt would stop a UI test, and the feed needs no microphone.
@MainActor
final class RoomPermissions: RoomSetupPermissions {
    private let feed: Bool

    init(feed: Bool) { self.feed = feed }

    var micGranted: Bool { feed || RoomModeRuntime.micGranted }

    func requestMic(_ done: @escaping @MainActor (Bool) -> Void) {
        if feed { done(true); return }
        AVAudioApplication.requestRecordPermission { granted in
            Task { @MainActor in done(granted) }
        }
    }

    func requestNotifications() {
        if feed { return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    var cameraAvailable: Bool { AVCaptureDevice.default(for: .video) != nil }

    var cameraGranted: Bool { AVCaptureDevice.authorizationStatus(for: .video) == .authorized }

    func requestCamera(_ done: @escaping @MainActor (Bool) -> Void) {
        AVCaptureDevice.requestAccess(for: .video) { granted in
            Task { @MainActor in done(granted) }
        }
    }
}

#if DEBUG
/// D-PERF's line (ROOMMODE-MAP §5), DEBUG builds only: the app's CPU over
/// the last reading, its memory footprint and the thermal state.
///
/// The wake chain's ms per chunk (R1's `RoomEarsTiming`, read by downcast
/// from the engine's ears) is not here: neither R1's type nor a way to reach
/// the engine's ears exists on the base branch; it joins this line at the
/// integration.
@MainActor
final class RoomPerfProbe: ObservableObject {
    @Published private(set) var line = ""
    private var lastCPU: Double?
    private var lastAt: TimeInterval?

    func sample() {
        let now = ProcessInfo.processInfo.systemUptime
        let cpu = Self.cpuSeconds()
        var pct = "–"
        if let cpu, let lastCPU, let lastAt, now > lastAt {
            pct = String(format: "%.1f%%", (cpu - lastCPU) / (now - lastAt) * 100)
        }
        lastCPU = cpu
        lastAt = now
        let mb = Self.footprintBytes().map { String(format: "%.0f MB", Double($0) / 1_048_576) } ?? "–"
        let thermal: String
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: thermal = "nominal"
        case .fair: thermal = "fair"
        case .serious: thermal = "serious"
        case .critical: thermal = "critical"
        @unknown default: thermal = "?"
        }
        line = "D-PERF cpu \(pct) · \(mb) · \(thermal) · wake ms/chunk –"
    }

    private static func cpuSeconds() -> Double? {
        var usage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else { return nil }
        let user = Double(usage.ru_utime.tv_sec) + Double(usage.ru_utime.tv_usec) / 1_000_000
        let system = Double(usage.ru_stime.tv_sec) + Double(usage.ru_stime.tv_usec) / 1_000_000
        return user + system
    }

    private static func footprintBytes() -> UInt64? {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return result == KERN_SUCCESS ? info.phys_footprint : nil
    }
}
#endif
