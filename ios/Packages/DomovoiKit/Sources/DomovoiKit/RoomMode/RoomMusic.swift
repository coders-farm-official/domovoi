import AVFoundation
import Foundation

/// The room's music, as the engine drives it.
public protocol RoomMusicPlaying: AnyObject, Sendable {
    var playing: Bool { get }
    /// `onReady`: it plays (`music_ready`). `onFailed(url, reason,
    /// attempts)`: it gave up (`music_failed`).
    func bind(
        onReady: @escaping @Sendable () -> Void,
        onFailed: @escaping @Sendable (String, String, Int) -> Void
    )
    func start(_ streamURL: String)
    func stop()
    func setGain(_ gain: Float)
}

/// The room's music (`music_start`): the room's MPD stream in an `AVPlayer`
/// of its own (not the app's player, which a room phone never builds),
/// outside the room's audio engine, never over mobile data
/// (`AVURLAssetAllowsCellularAccessKey: false`). Reports `music_ready` once
/// it is ready to play and `music_failed` after ~8 s of failures, retrying
/// meanwhile, as the Pi and Android do (`RoomAudio.kt:280-357`, ROOMMODE-MAP
/// §4). While it plays the wake word listens raw under it, as on a Pi.
///
/// Every touch of the player is on the main queue (the player's own rule
/// for its observers); callable from any thread.
public final class RoomMusic: RoomMusicPlaying, @unchecked Sendable {

    static let retryWindowMS: Int64 = 8_000

    // Main-queue state.
    private var player: AVPlayer?
    private var observations: [NSKeyValueObservation] = []
    private var failObserver: NSObjectProtocol?
    private var url: String?
    private var attempts = 0
    private var firstAttemptAt: Int64 = 0
    private var readySent = false
    private var gain: Float = 1
    private var token = 0

    private let isPlaying = RoomLock(false)
    private let callbacks = RoomLock<(ready: (@Sendable () -> Void)?, failed: (@Sendable (String, String, Int) -> Void)?)>((nil, nil))
    private let now: @Sendable () -> Int64

    public init(now: @escaping @Sendable () -> Int64 = RoomClock.monotonicMS) {
        self.now = now
    }

    public var playing: Bool { isPlaying.withLock { $0 } }

    public func bind(
        onReady: @escaping @Sendable () -> Void,
        onFailed: @escaping @Sendable (String, String, Int) -> Void
    ) {
        callbacks.withLock { $0 = (onReady, onFailed) }
    }

    public func start(_ streamURL: String) {
        DispatchQueue.main.async { [self] in
            stopNow()
            url = streamURL
            attempts = 0
            firstAttemptAt = now()
            readySent = false
            open(streamURL)
        }
    }

    public func stop() {
        DispatchQueue.main.async { [self] in stopNow() }
    }

    public func setGain(_ gain: Float) {
        DispatchQueue.main.async { [self] in
            self.gain = gain
            player?.volume = gain
        }
    }

    private func open(_ streamURL: String) {
        guard let u = URL(string: streamURL) else {
            giveUp(streamURL, "bad_url")
            return
        }
        attempts += 1
        token += 1
        let mine = token
        let asset = AVURLAsset(url: u, options: [AVURLAssetAllowsCellularAccessKey: false])
        let item = AVPlayerItem(asset: asset)
        let p = AVPlayer(playerItem: item)
        p.volume = gain
        p.automaticallyWaitsToMinimizeStalling = true
        observations = [
            item.observe(\.status, options: [.new]) { [weak self] item, _ in
                let status = item.status
                let reason = item.error.map { ($0 as NSError).localizedDescription } ?? "the stream failed"
                DispatchQueue.main.async { self?.itemStatus(status, reason: reason, token: mine) }
            },
            p.observe(\.timeControlStatus, options: [.new]) { [weak self] player, _ in
                let on = player.timeControlStatus == .playing
                self?.isPlaying.withLock { $0 = on }
            },
        ]
        failObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime, object: item, queue: .main
        ) { [weak self] _ in
            self?.failed("the stream stopped", token: mine)
        }
        player = p
        p.play()
    }

    private func itemStatus(_ status: AVPlayerItem.Status, reason: String, token mine: Int) {
        guard mine == token else { return }
        switch status {
        case .readyToPlay:
            if !readySent {
                readySent = true
                callbacks.withLock { $0.ready }?()
            }
        case .failed:
            failed(reason, token: mine)
        default:
            break
        }
    }

    private func failed(_ reason: String, token mine: Int) {
        guard mine == token, let u = url else { return }
        teardown()
        let elapsed = now() - firstAttemptAt
        if elapsed < Self.retryWindowMS {
            let delay = Double(attempts)
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [self] in
                if url == u && player == nil { open(u) }
            }
        } else {
            giveUp(u, reason)
        }
    }

    private func giveUp(_ u: String, _ reason: String) {
        url = nil
        teardown()
        let n = attempts
        callbacks.withLock { $0.failed }?(u, reason, n)
    }

    private func teardown() {
        observations.forEach { $0.invalidate() }
        observations = []
        if let failObserver { NotificationCenter.default.removeObserver(failObserver) }
        failObserver = nil
        player?.pause()
        player?.replaceCurrentItem(with: nil)
        player = nil
        token += 1
        isPlaying.withLock { $0 = false }
    }

    private func stopNow() {
        url = nil
        teardown()
    }
}
