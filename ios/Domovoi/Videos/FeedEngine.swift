import AVFoundation
import AVKit
import DomovoiKit
import Observation
import SwiftUI

/// The `AVPlayer` the swipe feed drives, and the only part of Videos that a
/// test cannot reach.
///
/// `VideoFeed.kt:187`'s `ExoPlayer.Builder(...)`, its listener and its two
/// effects. Everything that *decides* anything is in ``VideoFeedModel``; this
/// is the part that talks to AVFoundation, deliberately kept to commands in
/// and facts out so that the untestable surface is as small as it can be.
///
/// ### One player for the whole feed
///
/// Android keeps one `ExoPlayer` and moves it to whichever page settled,
/// because a surface per page was torn down and re-attached on every swipe
/// and a swipe that changed the aspect ratio could leave the new one black
/// (`VideoFeed.kt:339`). The same holds here and more so: an `AVPlayer` per
/// page would each open their own HTTPS connection to the house and each
/// start buffering.
///
/// ### The audio session
///
/// A video with sound needs the `playback` category, or iOS mutes it with the
/// ring/silent switch and stops it when the screen locks. It is set when the
/// feed opens and **deactivated when it closes**, so the rest of the app — and
/// anything else on the phone that was playing — gets the session back.
/// Android has no equivalent step; it instead pauses the app's own music
/// player, which this app does not have yet.
@MainActor
@Observable
final class FeedEngine {

    let player = AVPlayer()

    private(set) var positionMs: Int64 = 0

    /// The length, or nil while it is not known — a stream still loading, or
    /// one the server is sending without a length. **Never zero standing in
    /// for unknown**: a scrubber over a zero-length video would divide by it.
    private(set) var durationMs: Int64?

    private(set) var state: FeedPlayback = .idle

    private(set) var failed = false

    /// What the player learned about the stream, for the details sheet.
    private(set) var facts = FeedStreamFacts()

    /// Whether it means to be playing, which is not the same as whether a
    /// picture is moving (a stall to buffer still means to play).
    ///
    /// ### Stored, not computed
    ///
    /// The obvious version is `player.timeControlStatus != .paused`, and it
    /// is wrong here: `AVPlayer` is not `@Observable`, so a view reading it
    /// through this type would never be told the status changed and the pause
    /// glyph would stay on screen through a tap. These two are **stored**,
    /// set the instant a command is given and corrected by the ticker, which
    /// is what makes the glyph answer the tap at once and still be right when
    /// the player stalls on its own.
    private(set) var wantsToPlay = false

    private(set) var isPlaying = false

    /// Called when the video runs to its end, so the resume row is saved as
    /// watched before it loops.
    var onLoop: ((Int64?) -> Void)?

    private var timeObserver: Any?
    private var endSink: NSObjectProtocol?
    private var failSink: NSObjectProtocol?
    private var statusWatch: NSKeyValueObservation?
    private var ticker: Task<Void, Never>?

    init() {
        // Looping is ours to do: `.none` leaves the player parked at the end
        // so the notification can fire, and the handler seeks back. iOS has
        // `AVPlayerLooper`, but it needs an `AVQueuePlayer` and a template
        // item per video, which is a second player object per page — the
        // thing one player exists to avoid.
        player.actionAtItemEnd = .none
        player.allowsExternalPlayback = true
    }

    // MARK: - the session

    func begin() {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback)
            try AVAudioSession.sharedInstance().setActive(true)
        } catch {
            // Not fatal and not worth a message: the video plays, it may be
            // silenced by the ring switch. Saying "audio unavailable" over a
            // video that is about to play fine would be worse than silence.
        }
        startTicking()
    }

    /// Hold the screen awake, or let it time out.
    ///
    /// Driven by ``FeedRules/keepsScreenOn(wantsToPlay:state:failed:)``,
    /// which is where the rule lives and is tested; this is the one line that
    /// acts on it. Android sets `keepScreenOn` on the player view, which does
    /// the same thing through the window.
    func holdScreen(_ on: Bool) {
        UIApplication.shared.isIdleTimerDisabled = on
    }

    /// Everything this engine holds, let go. Called when the feed closes.
    func end() {
        player.pause()
        player.replaceCurrentItem(with: nil)
        wantsToPlay = false
        isPlaying = false
        stopWatching()
        ticker?.cancel()
        ticker = nil
        UIApplication.shared.isIdleTimerDisabled = false
        try? AVAudioSession.sharedInstance().setActive(false)
    }

    // MARK: - commands

    /// Load `url` and start it at `startMs`.
    ///
    /// The seek happens **before** play rather than after: a seek on a
    /// playing item shows a second of the beginning first, which on a resumed
    /// video looks like the resume was lost.
    func load(_ url: URL, startMs: Int64) {
        stopWatching()
        failed = false
        state = .buffering
        durationMs = nil
        positionMs = startMs
        facts = FeedStreamFacts()
        let item = AVPlayerItem(url: url)
        player.replaceCurrentItem(with: item)
        watch(item)
        wantsToPlay = true
        if startMs > 0 {
            item.seek(to: CMTime(value: startMs, timescale: 1000)) { [weak self] _ in
                MainActor.assumeIsolated { self?.play() }
            }
        } else {
            play()
        }
    }

    func play() {
        player.play()
        wantsToPlay = true
    }

    func pause() {
        player.pause()
        wantsToPlay = false
        isPlaying = false
    }

    func toggle() {
        if isPlaying { pause() } else { play() }
    }

    func seek(toMs target: Int64) {
        let clamped = FeedRules.seekTarget(target, durationMs: durationMs)
        positionMs = clamped
        // `toleranceAfter: .zero` only: snapping to the keyframe *before* the
        // target keeps a drag along the scrubber responsive, which is what
        // Android's `SeekParameters.CLOSEST_SYNC` buys (`VideoFeed.kt:201`).
        // An exact seek re-decodes from the previous keyframe on every drag
        // sample and the bar stutters.
        player.seek(
            to: CMTime(value: clamped, timescale: 1000),
            toleranceBefore: CMTime(seconds: 1, preferredTimescale: 1000),
            toleranceAfter: .zero
        )
    }

    /// Where the player is now, in whole seconds, for a resume save.
    var positionSec: Int64 { max(0, positionMs / 1000) }

    /// The length in whole seconds, or nil — which is what stops a save
    /// overwriting a good `duration_sec` with a useless one.
    var durationSec: Int64? {
        guard let durationMs, durationMs > 0 else { return nil }
        return durationMs / 1000
    }

    // MARK: - facts out

    private func watch(_ item: AVPlayerItem) {
        endSink = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.state = .ended
                self.onLoop?(self.durationSec)
                self.player.seek(to: .zero) { [weak self] _ in self?.player.play() }
            }
        }
        failSink = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.failed = true }
        }
        statusWatch = item.observe(\.status, options: [.initial, .new]) { [weak self] item, _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                switch item.status {
                case .readyToPlay:
                    self.state = .ready
                    self.takeFacts(item)
                case .failed:
                    self.failed = true
                    self.state = .idle
                default:
                    self.state = .buffering
                }
            }
        }
    }

    private func stopWatching() {
        if let endSink { NotificationCenter.default.removeObserver(endSink) }
        if let failSink { NotificationCenter.default.removeObserver(failSink) }
        statusWatch?.invalidate()
        endSink = nil
        failSink = nil
        statusWatch = nil
    }

    /// The scrubber's clock.
    ///
    /// A `Task` rather than `addPeriodicTimeObserver`, for the reason
    /// ``HomeModel``'s tick is one: the observer only fires while the player
    /// is playing, and the scrubber has to keep reporting a sensible position
    /// while it is paused and being dragged. Four times a second playing and
    /// twice a second paused, as Android's loop does (`VideoFeed.kt:295`).
    private func startTicking() {
        ticker?.cancel()
        ticker = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.readClock()
                let step = self.isPlaying ? 250 : 500
                try? await Task.sleep(for: .milliseconds(step))
            }
        }
    }

    private func readClock() {
        guard let item = player.currentItem else { return }
        let at = item.currentTime()
        if at.isNumeric { positionMs = max(0, Int64(at.seconds * 1000)) }
        let length = item.duration
        // `.indefinite` is a live stream and `.invalid` is "not yet": both
        // leave the duration nil rather than becoming zero.
        if length.isNumeric, length.seconds.isFinite, length.seconds > 0 {
            durationMs = Int64(length.seconds * 1000)
        }
        isPlaying = player.timeControlStatus == .playing
        if player.timeControlStatus != .paused { wantsToPlay = true }
        if !failed, player.timeControlStatus == .waitingToPlayAtSpecifiedRate {
            state = .buffering
        } else if !failed, isPlaying {
            state = .ready
        }
    }

    /// What the stream turned out to be, for the details sheet.
    ///
    /// ### Loaded, not read
    ///
    /// All five of these facts are `await`ed rather than read. The
    /// synchronous `track.naturalSize` and its neighbours were deprecated in
    /// iOS 16 because they block the calling thread on a read of the asset —
    /// and for a video arriving over the house's Wi-Fi that read is a network
    /// round trip on the main actor, which is the stall they were deprecated
    /// for. So the duration, which the player already knows, is set at once,
    /// and the shape of the picture arrives when it arrives. A details sheet
    /// opened in that gap shows the rows that are known and gains the rest.
    ///
    /// ### The guard after the await
    ///
    /// A swipe can load the next video while these are still loading. The
    /// item is captured and compared against `player.currentItem` **after**
    /// the await, so the previous video's resolution cannot land in the
    /// details sheet of the one now playing — the read-after-await mistake
    /// this project has made before. The item is its own key here; nothing
    /// else needs to be invented to hold one.
    private func takeFacts(_ item: AVPlayerItem) {
        let length = item.duration
        let seconds = length.isNumeric && length.seconds.isFinite ? length.seconds : nil
        // Set before the await, not instead of it: a stream with no video
        // track at all (an audio file in a video library) still has a length.
        facts = FeedStreamFacts(durationSec: seconds)
        guard let track = item.tracks.first(where: { $0.assetTrack?.mediaType == .video })?.assetTrack else {
            return
        }
        Task { @MainActor [weak self] in
            // Three loads rather than one: the multi-property `load` tops out
            // below five, and splitting it keeps each tuple readable.
            guard
                let shape = try? await track.load(.naturalSize, .preferredTransform),
                let rates = try? await track.load(.estimatedDataRate, .nominalFrameRate),
                let formats = try? await track.load(.formatDescriptions),
                let self,
                self.player.currentItem === item
            else { return }
            let size = shape.0.applying(shape.1)
            self.facts = FeedStreamFacts(
                durationSec: seconds,
                width: Int(abs(size.width)),
                height: Int(abs(size.height)),
                bitsPerSecond: Double(rates.0),
                frameRate: Double(rates.1),
                codec: Self.codec(of: formats.first)
            )
        }
    }

    /// The codec's four-character code, as a word.
    ///
    /// Android prints the MIME subtype (`avc`, `hevc`). AVFoundation gives a
    /// `FourCharCode` instead, so this prints that — `avc1`, `hvc1`, `vp09`.
    /// Not translated into the MIME spelling: a table mapping one to the
    /// other would be four entries that are right and an unknown number that
    /// are missing, and the four characters are what the file actually says.
    private static func codec(of description: CMFormatDescription?) -> String? {
        guard let description else { return nil }
        let code = CMFormatDescriptionGetMediaSubType(description)
        let bytes = [
            UInt8((code >> 24) & 0xFF),
            UInt8((code >> 16) & 0xFF),
            UInt8((code >> 8) & 0xFF),
            UInt8(code & 0xFF),
        ]
        let text = String(decoding: bytes, as: UTF8.self).trimmingCharacters(in: .whitespaces)
        return text.isEmpty ? nil : text
    }
}

/// The one video surface, behind the pager and never rebuilt.
struct FeedSurface: UIViewRepresentable {

    let player: AVPlayer

    func makeUIView(context: Context) -> PlayerSurface {
        let view = PlayerSurface()
        view.backgroundColor = .black
        view.layer.player = player
        view.layer.videoGravity = .resizeAspect
        return view
    }

    func updateUIView(_ view: PlayerSurface, context: Context) {
        if view.layer.player !== player { view.layer.player = player }
    }

    /// A `UIView` whose backing layer **is** the player layer, so the video
    /// resizes with the view instead of being laid out by hand on every
    /// rotation.
    final class PlayerSurface: UIView {
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        override var layer: AVPlayerLayer { super.layer as! AVPlayerLayer }
    }
}
