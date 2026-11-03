import Foundation

/// Publishing the lock screen, and taking its commands.
///
/// The one place that touches `MediaPlayer`. ``PlayerRemoteCommands`` decides
/// *what* is enabled and ``PlayerNowPlayingInfo`` decides *what it says*; this
/// only pushes and listens, so both of those are testable on a machine with no
/// lock screen at all.
@MainActor
public protocol PlayerNowPlayingPublisher: AnyObject {

    /// Write the dictionary. Called whenever the current item, the position,
    /// the rate, the target or the room's reading changes — on iOS the
    /// dictionary is a **push**, so there is no listener plumbing to port from
    /// `CastAwarePlayer.refresh()` (`:245`).
    func publish(_ info: PlayerNowPlayingInfo?)

    /// Enable exactly these commands and disable the rest. The second half
    /// matters as much as the first: a command left enabled after a cast
    /// starts is a lock-screen scrubber that does nothing.
    func setEnabled(_ commands: Set<PlayerRemoteCommand>)

    /// Where a command goes. Set once by ``PlayerModel``.
    var handler: (@MainActor (PlayerRemoteCommand, Double?) -> Void)? { get set }

    /// Fetch and attach the artwork for a path, bounded.
    ///
    /// `PlaybackService.kt:147` caps artwork at 1024 px; a print-size embedded
    /// scan must not land in the lock screen as a 36 MB bitmap here either.
    func attachArtwork(path: String?, absolute: @escaping @Sendable (String) -> String)
}

#if os(iOS)
import MediaPlayer
import UIKit

/// The real publisher.
///
/// iOS only. `MPNowPlayingInfoCenter` exists on macOS too, but the package's
/// tests run there and a test process that claims the machine's Now Playing
/// slot is a test that changes the person's lock screen — so the real one is
/// behind the platform check and the tests see a fake.
@MainActor
public final class MPNowPlayingPublisher: PlayerNowPlayingPublisher {

    public var handler: (@MainActor (PlayerRemoteCommand, Double?) -> Void)?

    private var wired = false
    private var artworkTask: Task<Void, Never>?
    private var artworkFor: String?

    public init() {}

    public func publish(_ info: PlayerNowPlayingInfo?) {
        wireIfNeeded()
        let centre = MPNowPlayingInfoCenter.default()
        guard let info else {
            centre.nowPlayingInfo = nil
            artworkFor = nil
            return
        }
        var d = centre.nowPlayingInfo ?? [:]
        d[MPMediaItemPropertyTitle] = info.title
        d[MPMediaItemPropertyArtist] = info.artist
        d[MPMediaItemPropertyAlbumTitle] = info.album
        d[MPNowPlayingInfoPropertyElapsedPlaybackTime] = info.elapsedSec
        d[MPMediaItemPropertyPlaybackDuration] = info.durationSec
        d[MPNowPlayingInfoPropertyPlaybackRate] = info.rate
        // While casting the room's place is not ours to be scrubbed to, so the
        // default rate says "not seekable here" as well as the disabled
        // command does.
        d[MPNowPlayingInfoPropertyDefaultPlaybackRate] = info.scrubbable ? 1.0 : 0.0
        centre.nowPlayingInfo = d
    }

    public func setEnabled(_ commands: Set<PlayerRemoteCommand>) {
        wireIfNeeded()
        let centre = MPRemoteCommandCenter.shared()
        centre.playCommand.isEnabled = commands.contains(.play)
        centre.pauseCommand.isEnabled = commands.contains(.pause)
        centre.togglePlayPauseCommand.isEnabled = commands.contains(.togglePlayPause)
        centre.nextTrackCommand.isEnabled = commands.contains(.nextTrack)
        centre.previousTrackCommand.isEnabled = commands.contains(.previousTrack)
        centre.changePlaybackPositionCommand.isEnabled = commands.contains(.changePlaybackPosition)
        centre.skipForwardCommand.isEnabled = commands.contains(.skipForward)
        centre.skipBackwardCommand.isEnabled = commands.contains(.skipBackward)
        centre.changePlaybackRateCommand.isEnabled = commands.contains(.changePlaybackRate)
    }

    public func attachArtwork(path: String?, absolute: @escaping @Sendable (String) -> String) {
        guard let path, let url = PlayerArtwork.model(coverPath: path, absolute: absolute),
              let source = URL(string: url) else {
            artworkTask?.cancel()
            dropArtwork()
            return
        }
        // The same picture is not fetched twice: a plain no-op, because what is
        // on the lock screen is already this track's.
        guard artworkFor != url else { return }
        // A cover the server already said it has none of is not asked for
        // again (``PlayerArtwork/noteFailure(_:status:)``) — but the previous
        // track's sleeve has to come **off**. `publish()` merges into the
        // existing dictionary deliberately, so artwork survives a metadata
        // update; every no-artwork exit therefore has to clear it itself, or
        // track B plays under track A's cover.
        guard !PlayerArtwork.isMissing(url) else {
            artworkTask?.cancel()
            dropArtwork()
            return
        }
        artworkFor = url
        artworkTask?.cancel()
        artworkTask = Task { [weak self] in
            guard let (data, response) = try? await URLSession.shared.data(from: source) else { return }
            let status = (response as? HTTPURLResponse)?.statusCode
            if status != 200 {
                PlayerArtwork.noteFailure(url, status: status)
                self?.dropArtwork()
                return
            }
            guard let image = UIImage(data: data) else { return }
            guard !Task.isCancelled else { return }
            // Bounds-respecting: the lock screen asks for the size it wants
            // and we draw into it, rather than handing over the original.
            let artwork = MPMediaItemArtwork(boundsSize: image.size) { size in
                let format = UIGraphicsImageRendererFormat()
                format.scale = 1
                return UIGraphicsImageRenderer(size: size, format: format).image { _ in
                    image.draw(in: CGRect(origin: .zero, size: size))
                }
            }
            var d = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]
            d[MPMediaItemPropertyArtwork] = artwork
            MPNowPlayingInfoCenter.default().nowPlayingInfo = d
        }
    }

    /// Take any artwork off the lock screen, and forget which cover is on it.
    ///
    /// Written back into `MPNowPlayingInfoCenter` rather than left to the next
    /// `publish()`, which merges and would keep the stale key.
    private func dropArtwork() {
        artworkFor = nil
        var d = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]
        d[MPMediaItemPropertyArtwork] = nil
        MPNowPlayingInfoCenter.default().nowPlayingInfo = d
    }

    private func wireIfNeeded() {
        guard !wired else { return }
        wired = true
        let centre = MPRemoteCommandCenter.shared()
        // The remote command centre calls its handlers on the main thread, but
        // the closure's type is not isolated, so the hop is stated rather than
        // assumed by the compiler.
        func bind(_ command: MPRemoteCommand, _ which: PlayerRemoteCommand) {
            _ = command.addTarget { [weak self] event in
                let position = (event as? MPChangePlaybackPositionCommandEvent)?.positionTime
                return MainActor.assumeIsolated {
                    guard let handler = self?.handler else { return .commandFailed }
                    handler(which, position)
                    return .success
                }
            }
        }
        bind(centre.playCommand, .play)
        bind(centre.pauseCommand, .pause)
        bind(centre.togglePlayPauseCommand, .togglePlayPause)
        bind(centre.nextTrackCommand, .nextTrack)
        bind(centre.previousTrackCommand, .previousTrack)
        bind(centre.changePlaybackPositionCommand, .changePlaybackPosition)
        bind(centre.skipForwardCommand, .skipForward)
        bind(centre.skipBackwardCommand, .skipBackward)
        bind(centre.changePlaybackRateCommand, .changePlaybackRate)
    }
}
#endif

/// A publisher that records what it was told — the stand-in on macOS, and what
/// ``PlayerModel``'s tests read to assert the lock screen says the room's
/// title and not the phone's.
@MainActor
public final class RecordingNowPlayingPublisher: PlayerNowPlayingPublisher {
    public var handler: (@MainActor (PlayerRemoteCommand, Double?) -> Void)?
    public private(set) var published: [PlayerNowPlayingInfo?] = []
    public private(set) var enabled: Set<PlayerRemoteCommand> = []
    public private(set) var artworkPaths: [String?] = []

    public init() {}

    public var latest: PlayerNowPlayingInfo? { published.last ?? nil }

    public func publish(_ info: PlayerNowPlayingInfo?) { published.append(info) }

    public func setEnabled(_ commands: Set<PlayerRemoteCommand>) { enabled = commands }

    public func attachArtwork(path: String?, absolute: @escaping @Sendable (String) -> String) {
        artworkPaths.append(path)
    }
}
