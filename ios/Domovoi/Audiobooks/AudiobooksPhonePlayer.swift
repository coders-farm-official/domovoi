import DomovoiKit
import SwiftUI

/// The merge of this item with the Player item: what happens when somebody
/// taps `play` on a book.
///
/// ``AudiobooksModel/player`` is an ``AudiobooksPlayer`` seam declared by this
/// item, because the Player item was written in parallel and none of its names
/// existed when Audiobooks' models were. This is the one adapter file
/// `MAP.md` §5 said the merge would need: it conforms ``PlayerModel`` — the
/// Player item's `AVQueuePlayer`-backed engine, queue, Now Playing slot,
/// remote commands, sleep timer and chapter jumps — to that seam, and nothing
/// else in Audiobooks knows the player exists.
///
/// ### Why there is a process-wide object here, and what it is not
///
/// An audiobook is listened to for hours, with the phone locked and the app in
/// the background. A player held as a view's `@State` dies when the tab
/// changes, which would make `play` a control that stops when you look at
/// something else — the misleading control this project will not ship. So the
/// player is held here, for the process, keyed by the household it belongs to.
///
/// **This is the wrong home for it and it says so.** The player belongs on
/// ``AppContainer`` beside the client and the socket, exactly as Android has
/// it on its own container (`AppContainer.kt:95`, `BookPlay.kt:19` reaching
/// `app.player`), so that Podcasts, Stations, Music and PhoneMedia share one
/// player rather than one each. `AppContainer.swift` is a shared DomovoiKit
/// file and NIGHT-RULES §2 forbids this item from editing it, so the holder
/// lives in this item's own folder with the shared change written down
/// (DECISIONS, "Needs a shared change"). The keying by household is what
/// makes the move safe: whoever lifts this onto the container is lifting a
/// single instance, not merging several.
///
/// **The key is only consulted when ``shared(in:deviceID:places:)`` is
/// called**, so whoever holds a player must ask for it again when the house
/// changes — a player kept across a move still stamps every item with the old
/// house's `origin`, and a place written under that key is a place the book
/// page (which reads the *connected* key) will never find.
/// ``AudiobooksView``'s `onChange(of: model.inputs.serverURL)` re-asks for
/// exactly that reason.
///
/// ### Nil, and what nil means
///
/// ``shared(in:deviceID:places:)`` answers nil when this phone has no device
/// id. ``PlayerModel`` requires one — every position it saves is a slot the
/// server keys by device (`PlayerModel.swift:241`) — and a player built with
/// an empty id would write every listener's place into one row that belongs to
/// nobody. Nil means ``AudiobooksModel/canPlay(_:)`` is false, so **no `play`
/// button and no tappable chapter row is drawn at all**, which is the honest
/// answer to a phone that has not introduced itself. The book page says so in
/// words (``AudiobooksStrings/noDeviceID``).
@MainActor
final class AudiobooksPhonePlayer: AudiobooksPlayer {

    /// The one player in this process, and the household it was built for.
    ///
    /// A `static var` rather than a lazily-built singleton: the household can
    /// change while the app runs, and a player holding the old house's queue
    /// is a player whose next position save goes to the wrong server.
    private static var held: (key: BookServerKey, player: AudiobooksPhonePlayer)?

    /// The player for this household, or nil when this phone has no device
    /// id.
    ///
    /// - Parameter places: Audiobooks' own store of where you are in a book
    ///   (``BookPositionSync``), handed to the player as its position store.
    ///   **This is the half of the merge that matters**: without it the
    ///   player would save places straight to the server slot, the phone's
    ///   store would stay empty for ever, and a book page would show no place
    ///   however long you had listened — because ``AudiobookDetailModel``
    ///   reads the local place, as Android's `produceState` does
    ///   (`BookPage.kt:96-98`). One store, written by the player and read by
    ///   the page, is the owner decision of 2026-10-20: one place per book on
    ///   the phone, shared by streaming and by a saved copy.
    static func shared(
        in container: AppContainer,
        deviceID: String?,
        places: BookPositionSync
    ) -> AudiobooksPhonePlayer? {
        guard let deviceID, !deviceID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        let key = Self.key(of: container)
        if let held, held.key.isSame(key) { return held.player }
        // A player for another house is told the house changed and then
        // stopped before it is let go.
        //
        // **Both halves matter.** `serverChanged(to:)` is the Player item's
        // own move-house path (`PlayerModel.swift:889`, whose doc says the
        // shell calls it — nothing does, so this item calls it for its own
        // player): it drops whatever came from the old server from the queue
        // and ends a cast to one of its rooms *without a word to the new
        // server*, which a bare `stop()` would not — `stop()` while casting
        // posts a room action, and that room is the old house's. Then
        // `stop()`, because an `AVQueuePlayer` that is merely dropped keeps
        // playing until it is collected, and two audiobooks heard at once is
        // the bug the Player item's own `leaveRoom` exists for.
        held?.player.player.serverChanged(
            to: PlayerServerScope(address: key.address, fingerprint: key.fingerprint)
        )
        held?.player.player.stop()
        let made = AudiobooksPhonePlayer(
            in: container,
            deviceID: deviceID,
            places: places,
            key: key
        )
        held = (key: key, player: made)
        return made
    }

    /// Which household the container is pointed at — ``BookPositionSync``'s
    /// own key, read the same way ``AudiobooksModel`` reads it, so the player
    /// and the places cannot disagree about which house they belong to.
    private static func key(of container: AppContainer) -> BookServerKey {
        let url = container.store.serverURL
        let pin = IdentityGate.pinKey(url).flatMap { container.store.pinFor($0) }
        return BookServerKey.of(serverURL: url, pin: pin)
    }

    let player: PlayerModel

    private let key: BookServerKey

    private init(
        in container: AppContainer,
        deviceID: String,
        places: BookPositionSync,
        key: BookServerKey
    ) {
        self.key = key
        self.player = PlayerModel(
            client: container.client,
            deviceID: deviceID,
            // `spokenAudio: true` — `.timeDomain` time-stretching rather than
            // `.spectral`, and never `.varispeed`, which is the one that
            // makes a narrator sound like a chipmunk
            // (`PlayerAudioEngine.swift:132-136`). A book is the case that
            // flag was written for.
            engine: AVQueueAudioEngine(spokenAudio: true),
            session: AVAudioSessionControl(),
            nowPlaying: MPNowPlayingPublisher()
        )
        // The seam the Player item left for this one
        // (`PlayerModel.swift:210`, whose comment names this item).
        self.player.positions = AudiobookPlaces(places: places, server: key)
    }

    /// Start this book here. ``AudiobooksPlayer``'s one method.
    ///
    /// `startBook` → `player.playItems(listOf(item), 0, startSec, speed)`
    /// (`AudiobooksScreen.kt:286`, `BookUi.kt:197`). The request already
    /// carries where and how fast; the queue is the player's.
    func play(_ request: AudiobookPlayRequest) {
        let item = PlayerItem.fromBook(
            id: request.bookID,
            title: request.title,
            author: request.author,
            durationSec: request.durationSec,
            artwork: request.coverPath,
            chapters: request.chapters.map { PlayerChapter(title: $0.title, startSec: $0.startSec) },
            parts: request.parts.map { PlayerMediaPart(src: $0.path, durationSec: $0.durationSec) },
            // `origin` is what tells the player's position store which house
            // this book came from, so a place can never be saved to a server
            // the book is not on (`PlayerModel.swift:806`, `:828`).
            origin: PlayerServerScope(address: key.address, fingerprint: key.fingerprint)
        )
        player.play(
            [item],
            startAt: 0,
            resumeSec: request.startSec,
            // The server's slot and this app's store both keep speed as a
            // double; the engine's rate is a `Float`. One conversion, here,
            // rather than one at each end.
            speed: Float(request.speed)
        )
    }
}

/// Audiobooks' place store, as the player wants it.
///
/// ``PlayerPositionStore`` is `Sendable` and not actor-isolated, and
/// ``BookPositionSync`` is an actor — so this is a `struct` holding the actor
/// and the household, which is the shape a `Sendable` protocol with `async`
/// members asks for.
///
/// ### Why the player does not just use its own server slot
///
/// It would work and it would be wrong. ``PlayerModel``'s own saver posts
/// straight to `/api/audiobooks/{id}/position` and reads it back; nothing
/// would ever be written on the phone. The owner's decision of 2026-10-20 is
/// **one place per book on the phone**, shared by a streamed book and a saved
/// copy, pushed up to the server's slot for this phone — which is exactly
/// ``BookPositionSync``, and is why `PlayerModel.positions` exists as a seam
/// with "the Audiobooks item" written beside it (`PlayerModel.swift:40`).
struct AudiobookPlaces: PlayerPositionStore {

    let places: BookPositionSync

    /// The household this store belongs to. A place goes **only** to the
    /// server the book came from.
    let server: BookServerKey

    /// Books only. The player asks this of podcasts too
    /// (``PlayerItem/isSpoken``), and a podcast's place is not this item's to
    /// keep — answering false sends it down the player's own server-slot
    /// path, which is what Podcasts expects.
    func owns(_ item: PlayerItem) -> Bool {
        item.kind == .audiobook
    }

    func open(_ item: PlayerItem, server scope: PlayerServerScope?) async -> PlayerPosition {
        let key = Self.key(scope) ?? server
        let place = await places.open(bookID: item.itemID, server: key)
        return PlayerPosition(positionSec: place.positionSec, speed: Float(place.speed))
    }

    func record(
        _ item: PlayerItem,
        server scope: PlayerServerScope?,
        positionSec: Double,
        speed: Float
    ) async {
        // The key is settled **before** the first await, so a place recorded
        // while the app is moving house cannot be written under the new
        // house's key for a book it has never heard of —
        // [[read-after-await-wrong-key]], the top defect class on this track.
        let key = Self.key(scope) ?? server
        await places.record(
            bookID: item.itemID,
            server: key,
            positionSec: positionSec,
            speed: Double(speed)
        )
        // Recorded here, then sent up: ``BookPositionSync/push(bookID:server:)``
        // is a no-op unless the place is dirty and the book's own server is
        // the connected one, and it carries the in-flight guard that keeps a
        // place recorded mid-request from being marked clean
        // (`BookPositions.kt:194-208`). This is Android's
        // `flushSpokenPosition` — `.record` then `.push`
        // (`PlayerController.kt:604-618`).
        await places.push(bookID: item.itemID, server: key)
    }

    /// The player's scope as this item's key. The two types hold the same two
    /// fields; nil for a player that has no server at all, which leaves the
    /// household this store was built for standing.
    private static func key(_ scope: PlayerServerScope?) -> BookServerKey? {
        guard let scope, scope.address != nil || scope.fingerprint != nil else { return nil }
        return BookServerKey(address: scope.address, fingerprint: scope.fingerprint)
    }
}
