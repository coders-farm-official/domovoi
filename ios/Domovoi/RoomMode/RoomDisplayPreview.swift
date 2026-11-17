#if DEBUG
import DomovoiKit
import SwiftUI
import UIKit

/// DEBUG BUILDS ONLY: the room display drawn from made-up state, with no
/// room, no server and no microphone, for design screenshots on a Simulator
/// (Android's `RoomDisplayPreviewActivity`). The real kiosk view and model,
/// over a fake engine; nothing is kept, nothing is asked for.
///
///     xcrun simctl launch booted org.codersfarm.domovoi.dev \
///         -domovoi-room-display-preview playing
///
/// Scenes: `playing` (music, two timers, an alarm, listening), `idle`
/// (nothing playing, waiting for the wake word), `ringing` (a timer going
/// off), `night` (idle late at night, dimmed). `-domovoi-room-display-time
/// 18:42` pins the clock (default 18:42; night: 23:15).
enum RoomDisplayPreviewScene: String, CaseIterable {
    case playing, idle, ringing, night

    static let flag = "-domovoi-room-display-preview"
    static let timeFlag = "-domovoi-room-display-time"

    /// The scene the launch asked for; nil (the app as usual) without the flag.
    static var requested: RoomDisplayPreviewScene? {
        guard let value = value(after: flag) else { return nil }
        return RoomDisplayPreviewScene(rawValue: value) ?? .playing
    }

    /// The pinned hour and minute.
    static var pinnedTime: (hour: Int, minute: Int)? {
        guard let value = value(after: timeFlag) else { return nil }
        let parts = value.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 2, (0..<24).contains(parts[0]), (0..<60).contains(parts[1]) else { return nil }
        return (parts[0], parts[1])
    }

    private static func value(after flag: String) -> String? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: flag), arguments.index(after: index) < arguments.endIndex else {
            return nil
        }
        return arguments[arguments.index(after: index)]
    }

    var status: RoomStatus {
        RoomStatus(
            link: .ready,
            phase: self == .playing ? .capturing : (self == .ringing ? .speaking : .listening),
            micLive: self == .playing,
            micOpen: true,
            wakeReady: true,
            musicPlaying: self == .playing
        )
    }

    func feed(receivedAt: Int64) -> RoomFeed? {
        switch self {
        case .playing:
            return RoomFeed(
                nowPlaying: RoomFeedNowPlaying(
                    state: .play, title: "Harvest Moon", artist: "The Lanterns", album: "Kitchen Radio",
                    source: .library, art: "/v1/music/cover/12", durationSec: 245, elapsedSec: 82,
                    controls: [.pause, .skip, .previous]
                ),
                timers: [
                    RoomFeedTimer(id: 1, kind: .timer, label: "pasta", remainingMS: 4 * 60_000 + 37_000, totalMS: 10 * 60_000),
                    RoomFeedTimer(id: 2, kind: .timer, label: nil, remainingMS: 23 * 60_000 + 5_000, totalMS: 30 * 60_000),
                    RoomFeedTimer(id: 3, kind: .alarm, label: "7 am alarm", remainingMS: 12 * 3_600_000 + 18 * 60_000, totalMS: nil),
                ],
                ringing: nil,
                receivedAt: receivedAt
            )
        case .ringing:
            return RoomFeed(
                nowPlaying: nil,
                timers: [RoomFeedTimer(id: 2, kind: .timer, label: "bread", remainingMS: 17 * 60_000, totalMS: 40 * 60_000)],
                ringing: RoomFeedRinging(fireID: 41, kind: .timer, label: "pasta"),
                receivedAt: receivedAt
            )
        case .idle, .night:
            return nil
        }
    }
}

/// The preview's room: the scene's status and feed, a volume that moves,
/// and a made-up cover.
final class RoomDisplayPreviewEngine: RoomEngineControl, RoomDisplaySource, @unchecked Sendable {
    private let scene: RoomDisplayPreviewScene
    private let feed: RoomFeed?
    private let level = RoomLock(40)

    init(scene: RoomDisplayPreviewScene) {
        self.scene = scene
        feed = scene.feed(receivedAt: RoomClock.monotonicMS())
    }

    var status: RoomStatus { scene.status }

    func statusUpdates() -> AsyncStream<RoomStatus> {
        let now = scene.status
        return AsyncStream { $0.yield(now) }
    }

    func start() -> Bool { true }
    func stop() {}
    func setMuted(_ on: Bool) {}
    func retryNow() {}

    func displayUpdates() -> AsyncStream<RoomFeed?> {
        let now = feed
        return AsyncStream { $0.yield(now) }
    }

    func roomControl(_ action: RoomControl) -> Bool { true }

    var volumeLevel: Int { level.withLock { $0 } }

    func setVolumeLevel(_ value: Int) -> Bool {
        level.withLock { $0 = min(100, max(0, value)) }
        return true
    }

    /// A made-up cover: the dashboard's no-art gradient.
    func art(_ path: String) async -> Data? {
        await MainActor.run { Self.gradient() }
    }

    @MainActor
    private static func gradient() -> Data? {
        let size = CGSize(width: 256, height: 256)
        let image = UIGraphicsImageRenderer(size: size).image { ctx in
            let colors = [
                UIColor(red: 0xE9 / 255, green: 0xC8 / 255, blue: 0x8E / 255, alpha: 1).cgColor,
                UIColor(red: 0xC0 / 255, green: 0x6A / 255, blue: 0x2A / 255, alpha: 1).cgColor,
            ] as CFArray
            if let g = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: [0, 1]) {
                ctx.cgContext.drawLinearGradient(g, start: .zero, end: CGPoint(x: size.width, y: size.height), options: [])
            }
        }
        return image.pngData()
    }
}

@MainActor
private final class RoomPreviewScreen: RoomScreenControl {
    func keepAwake(_ on: Bool) {}
    func dim(_ on: Bool) {}
}

@MainActor
private final class RoomPreviewNotices: RoomNoticePosting {
    func post(title: String, body: String) {}
    func cancel() {}
}

/// The real ``RoomKioskView`` over ``RoomDisplayPreviewEngine``.
struct RoomDisplayPreviewScreen: View {
    let scene: RoomDisplayPreviewScene
    @StateObject private var model: RoomKioskModel
    private let clockOffset: TimeInterval

    init(scene: RoomDisplayPreviewScene) {
        self.scene = scene
        let store = RoomStore.inMemory()
        store.enrol(RoomEnrolment(
            coreURL: "http://192.168.1.20:6370",
            fingerprint: "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA",
            roomID: "kitchen",
            roomLabel: "kitchen"
        ))
        store.setActive(true)
        store.setRunning(true)
        _model = StateObject(wrappedValue: RoomKioskModel(
            store: store,
            makeEngine: { RoomDisplayPreviewEngine(scene: scene) },
            lock: FixedRoomLockFacts(),
            screen: RoomPreviewScreen(),
            notices: RoomPreviewNotices(),
            micGranted: { true },
            onLeave: { _ in }
        ))
        let pinned = Self.pinned ?? (scene == .night ? (23, 15) : (18, 42))
        let today = Calendar.current.date(bySettingHour: pinned.0, minute: pinned.1, second: 0, of: Date()) ?? Date()
        clockOffset = today.timeIntervalSinceNow
    }

    private static var pinned: (Int, Int)? {
        RoomDisplayPreviewScene.pinnedTime.map { ($0.hour, $0.minute) }
    }

    var body: some View {
        RoomKioskView(model: model, clockOffset: clockOffset, onGuidedAccess: {}) { EmptyView() }
            .statusBarHidden()
            .persistentSystemOverlays(.hidden)
            .preferredColorScheme(.dark)
            .onAppear {
                // The Guided Access tip is not part of the design under review.
                model.guidedAccessChanged(true)
                model.enteredForeground()
            }
    }
}
#endif
