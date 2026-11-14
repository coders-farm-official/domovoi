import Foundation

/// What a room card's buttons do, and the two library-wide jobs in the page
/// header.
///
/// `MusicScreen.kt:180-230` (`roomCtl`, `onPlayRandom`, `onFavoriteNp`,
/// `onRescan`, `onEnrich`) and `NowPlayingCards.kt:53` (`RoomTransport`). An
/// extension rather than more lines in ``MusicModel`` because they share one
/// shape — a `POST`, a now-playing refresh, a toast on failure — and the
/// shape is worth saying once.
public extension MusicModel {

    // MARK: - the room's transport

    /// Pause, resume, skip, previous, stop — for one room.
    ///
    /// **A room the server has not reported is refused here**, exactly as
    /// ``play(_:in:)`` refuses one: the cards are drawn from the now-playing
    /// answer, so a room this model has not seen cannot have a card, and a
    /// method that would send one anyway makes the check a courtesy.
    ///
    /// Returns whether the request went, which is what a test asserts on and
    /// what stops this being a method whose refusal is invisible.
    @discardableResult
    func control(_ action: MusicTransportAction, in room: String) -> Bool {
        guard castRooms.contains(room) else { return false }
        post(
            MusicQueuePath.transport(action, room: room),
            failure: { MusicStrings.actionFailed(action.label, $0) }
        )
        return true
    }

    /// The pause/resume button, which is **one** control with two states.
    ///
    /// The decision of which to send belongs here and not in the view: it is
    /// read off ``NowPlayingRoom/isPlaying``, the same property that chooses
    /// the icon, so the icon and the request can never disagree — which they
    /// can when a view reads `state == "play"` for the picture and the model
    /// reads something else for the action.
    @discardableResult
    func togglePlayback(_ room: NowPlayingRoom) -> Bool {
        control(room.isPlaying ? .pause : .resume, in: room.roomID)
    }

    /// "Play something" for an idle room.
    ///
    /// `POST /api/music/play` with `query: "something random"`
    /// (`MusicScreen.kt:192`) — the shuffle the room cards offer when there
    /// is nothing playing. The words are the **server's** search, not a
    /// client-side pick, which is why there is no track id in the body: the
    /// house decides what "something random" means, and a plugin can change
    /// that answer without this app shipping again.
    @discardableResult
    func playSomething(in room: String) -> Bool {
        guard castRooms.contains(room) else { return false }
        setToast(MusicStrings.shuffleRequested(room: room))
        post(
            "/api/music/play",
            body: Self.json(["room_id": room, "query": MusicStrings.somethingRandom]),
            failure: MusicStrings.playFailed
        )
        return true
    }

    /// Favourite whatever a room is playing now.
    ///
    /// Not the same thing as favouriting a library row: the room may be
    /// playing a station, a podcast or a provider's stream, which have no
    /// library id at all. The server answers what it did
    /// (``NpFavoriteResponse``) and **its sentence wins** —
    /// ``NpFavoriteResponse/toast`` is where that order is written down, and
    /// the reason is that the `kind` vocabulary is open and a plugin knows
    /// what its own thing is called.
    ///
    /// A newly favourited **library** track reloads the list, because a row
    /// already on screen now has a star it did not have; anything else does
    /// not, because nothing on screen changed (`MusicScreen.kt:219`).
    @discardableResult
    func favoriteNowPlaying(in room: String) -> Bool {
        guard castRooms.contains(room) else { return false }
        let client = container.client
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                let answer = try await client.post(
                    MusicQueuePath.favoriteNowPlaying(room: room),
                    as: NpFavoriteResponse.self
                )
                guard let self else { return }
                self.setToast(answer.toast)
                if answer.kind == "library", !answer.alreadyFavorited {
                    self.library.reload()
                }
                self.nowPlaying.refresh()
            } catch let trouble {
                self?.setToast(MusicStrings.favoriteFailed(trouble.message))
            }
        }
        return true
    }

    // MARK: - the two library-wide jobs

    /// Rescan the music folders — `POST /api/music/library/reindex`.
    ///
    /// Fire-and-forget by design: the sweep runs on the server and the
    /// library's own push (`library.indexer.changed`) is what brings the rows
    /// in, so there is nothing to wait for and no progress this app can
    /// honestly show.
    func rescanLibrary() {
        post(
            "/api/music/library/reindex",
            success: { MusicStrings.rescanStarted },
            failure: MusicStrings.rescanFailed
        )
    }

    /// Identify untagged tracks — `POST /api/music/library/enrich`.
    ///
    /// The toast is the **server's answer**, not "started": the core begins a
    /// recognition sweep only when it can, and otherwise answers
    /// `{"queued": false, "reason": …}`. Saying "enrich started" over that is
    /// the class of sentence this port keeps finding and removing — a claim
    /// the app did not measure.
    func enrichLibrary() {
        let client = container.client
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                let answer = try await client.post(
                    "/api/music/library/enrich",
                    as: EnrichReply.self
                )
                self?.setToast(answer.text)
            } catch let trouble {
                self?.setToast(MusicStrings.enrichFailed(trouble.message))
            }
        }
    }

    // MARK: - one POST, one shape

    /// `POST`, refresh the now-playing answer, toast only on failure.
    ///
    /// Every room action above is this. The now-playing refresh is in the
    /// success path rather than beside each call, because a transport action
    /// that did not move the card is the bug this screen would otherwise
    /// have: MPD answers the POST before its own state has settled, so the
    /// push is what is relied on and this refresh is the belt for a house
    /// whose socket is down.
    private func post(
        _ path: String,
        body: Data? = nil,
        success: @escaping () -> String? = { nil },
        failure: @escaping (String) -> String
    ) {
        let client = container.client
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                try await client.send(method: "POST", path: path, body: body)
                guard let self else { return }
                if let said = success() { self.setToast(said) }
                self.nowPlaying.refresh()
            } catch let trouble {
                self?.setToast(failure(trouble.message))
            }
        }
    }
}

/// What `POST /api/music/library/enrich` answers.
///
/// `EnrichReply.kt`, as a decoded type rather than Android's
/// `JsonElement?`-poking. The reasons are an **open** set on the wire — the
/// core may learn a new one — so an unknown reason is said rather than
/// swallowed: Android's `else` branch puts the raw slug in the sentence, and
/// so does this, because "enrich didn't start" with no reason is a dead end
/// for whoever has to work out why.
public struct EnrichReply: Equatable, Sendable, Decodable {

    /// Whether the sweep started. Nil for an older core, which always
    /// answered `{"queued": true}` and sometimes meant it.
    public let queued: Bool?
    public let reason: String?

    public init(queued: Bool? = nil, reason: String? = nil) {
        self.queued = queued
        self.reason = reason
    }

    private enum CodingKeys: String, CodingKey {
        case queued, reason
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        queued = try values.decodeIfPresent(Bool.self, forKey: .queued)
        reason = try values.decodeIfPresent(String.self, forKey: .reason)
    }

    /// The toast. `enrichReplyText` in `EnrichReply.kt:16`, branch for
    /// branch — including that a **nil** `queued` reads as started, because
    /// an older core that does not send the field is the one case where
    /// "started" was the only thing it ever said.
    public var text: String {
        guard queued == false else { return MusicStrings.enrichStarted }
        switch reason {
        case "disabled": return MusicStrings.enrichDisabled
        case "offline": return MusicStrings.enrichOffline
        case "no_provider": return MusicStrings.enrichNoProvider
        case "running": return MusicStrings.enrichRunning
        default: return MusicStrings.enrichRefused(reason)
        }
    }
}

// MARK: - "play in <room>" starts the room's mix (cb780e90)

public extension MusicModel {

    /// Play one library row in one room, **starting the room's Up next**:
    /// the song plays, then songs like it (or the rest of `context`) follow
    /// it there, the mix following whoever this phone listens as
    /// (`TrackDrawer.kt:92-117`).
    ///
    /// ``play(_:in:)``'s rules and words — a room the server never reported
    /// is refused here, the toast is the same — with the body Android sends
    /// since P4: `device_id`, the list the song was opened from, and the
    /// listener fields (``RoomPlayBodies/playTrack(room:trackID:deviceID:context:listener:autoplay:)``).
    /// Worked out at the tap, for the server the tap was on, and its answer
    /// is said only while the app still talks to that server.
    @discardableResult
    func play(
        _ track: LibraryTrack,
        in room: String,
        deviceID: String,
        context: UpNextContext?,
        listener: RoomListener
    ) -> Bool {
        guard castRooms.contains(room) else { return false }
        let label = track.displayTitle
        let client = container.client
        let server = container.store.serverURL
        let body = RoomPlayBodies.playTrack(
            room: room,
            trackID: track.id,
            deviceID: deviceID,
            context: context,
            listener: listener
        )
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                try await client.send(method: "POST", path: "/api/music/play-track", body: body)
                guard let self, self.container.store.serverURL == server else { return }
                self.setToast(MusicStrings.queuedInRoom(label, room: room))
                self.nowPlaying.refresh()
            } catch let trouble {
                guard let self, self.container.store.serverURL == server else { return }
                self.setToast(MusicStrings.playFailed(trouble.message))
            }
        }
        return true
    }
}
