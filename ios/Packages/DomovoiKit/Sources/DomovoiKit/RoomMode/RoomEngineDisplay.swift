import Foundation

// The room display's half of the engine (``RoomDisplaySource``):
// `room_display` in, `room_control` out, the room's volume and its artwork
// (`RoomEngine.kt`'s `RoomDisplaySource` members).

extension RoomEngine: RoomDisplaySource {

    /// A cover is a thumbnail: anything bigger is not drawn.
    public static let maxArtBytes = 2 * 1024 * 1024

    public func displayUpdates() -> AsyncStream<RoomFeed?> {
        displayChannel.updates()
    }

    /// Only from a core that said it sends it (`ready.features`): anything
    /// else is not this room's to show. A frame sent before the one on screen
    /// is dropped.
    func onDisplay(_ feed: RoomFeed) {
        guard features.contains(RoomDisplayFeed.ability) else {
            displayChannel.update { _ in nil }
            return
        }
        let at = parts.clock()
        displayChannel.update { current in
            guard RoomDisplayFeed.newer(current, feed) else { return current }
            var stamped = feed
            stamped.receivedAt = at
            return stamped
        }
    }

    @discardableResult
    public func roomControl(_ action: RoomControl) -> Bool {
        let (isReady, features) = shared.withLock { ($0.ready, $0.features) }
        guard isReady, features.contains(RoomDisplayFeed.ability) else { return false }
        return send(RoomDisplayFeed.control(action))
    }

    public var volumeLevel: Int { parts.gain.level }

    /// The level the room plays at (``RoomGain``, what `set_volume` sets),
    /// reported to the server as a Pi reports a turn of its knob.
    @discardableResult
    public func setVolumeLevel(_ level: Int) -> Bool {
        guard ready else { return false }
        parts.gain.set(level)
        return send(RoomProtocol.volumeStatus(level: parts.gain.level))
    }

    /// Only a cover or a photo result's thumbnail path
    /// (``RoomDisplayFeed/isImagePath(_:)``), only from the room's own server
    /// (the address it was enrolled to, under the app's cleartext rule), with
    /// no credential: the room key goes on no HTTP request.
    public func art(_ path: String) async -> Data? {
        guard RoomDisplayFeed.isImagePath(path), var base = store.enrolment?.coreURL else { return nil }
        while base.hasSuffix("/") { base.removeLast() }
        let url = base + path
        let http = parts.http
        let limit = Self.maxArtBytes
        return await withCheckedContinuation { (continuation: CheckedContinuation<Data?, Never>) in
            artQueue.async {
                continuation.resume(returning: try? http.get(url, limit: limit))
            }
        }
    }
}
