import DomovoiKit
import SwiftUI

/// The adapter between Audiobooks and the app's one player: what happens when
/// somebody taps `play` on a book.
///
/// ``AudiobooksModel/player`` is an ``AudiobooksPlayer`` seam declared by the
/// Audiobooks item, because the Player item was written in parallel and none
/// of its names existed when Audiobooks' models were. This is the one adapter
/// file `MAP.md` §5 said the merge would need: it conforms ``PlayerModel`` —
/// the `AVQueuePlayer`-backed engine, queue, Now Playing slot, remote
/// commands, sleep timer and chapter jumps — to that seam, and nothing else in
/// Audiobooks knows the player exists.
///
/// ### It no longer holds a player of its own
///
/// It used to. Until the 2026-11-03 batch this file kept a `static var held`
/// — one process-wide player keyed by household — and its own doc comment
/// said, in as many words, that this was the wrong home for it: *"The player
/// belongs on `AppContainer` beside the client and the socket, exactly as
/// Android has it on its own container (`AppContainer.kt:95`, `BookPlay.kt:19`
/// reaching `app.player`), so that Podcasts, Stations, Music and PhoneMedia
/// share one player rather than one each."* `AppContainer.swift` was a shared
/// file that item could not edit, so the holder lived here with the shared
/// change written down.
///
/// Kamron answered it (answer 2 / 49: "yes, as the next batch"), and the owner
/// is ``ShellModel/player`` — not `AppContainer`, because ``PlayerModel`` is
/// `@MainActor` and `AppContainer` is `Sendable` and not main-actor isolated,
/// so a player cannot be one of its stored properties. What is left here is
/// the adapter: the request-to-``PlayerItem`` mapping and the position store,
/// both of which are Audiobooks' own.
///
/// ### What this still owns: the key, and the places
///
/// ``PlayerModel/positions`` is the seam the Player item left for *this* item
/// (`PlayerModel.swift:219`, whose comment names it). Without it the player
/// would post straight to the server slot and nothing would ever be written on
/// the phone — so a book page would show no place however long you had
/// listened, because ``AudiobookDetailModel`` reads the **local** place, as
/// Android's `produceState` does (`BookPage.kt:96-98`). One store, written by
/// the player and read by the page, is the owner decision of 2026-10-20.
///
/// The store is keyed by household, so ``over(_:in:places:)`` is asked again
/// whenever the house changes — a place recorded under the old key is a place
/// the book page (which reads the connected one) would never find.
/// ``AudiobooksView``'s `onChange(of: model.inputs.serverURL)` is that ask.
///
/// ### Nil, and what nil means
///
/// ``over(_:in:places:)`` answers nil when there is no player — which is a
/// phone with no device id (``ShellModel/player`` says why) or a platform with
/// no audio engine. Nil means ``AudiobooksModel/canPlay(_:)`` is false, so
/// **no `play` button and no tappable chapter row is drawn at all**, which is
/// the honest answer rather than a control that cannot do what it says. The
/// book page says so in words (``AudiobooksStrings/noDeviceID``).
@MainActor
final class AudiobooksPhonePlayer: AudiobooksPlayer {

    /// The adapter for this household, or nil when there is no player.
    ///
    /// - Parameters:
    ///   - player: ``ShellModel/player`` — the app's one player. Reading it is
    ///     what builds it, which is why this is called from `.task` and from
    ///     the server-change hook rather than from a `body`.
    ///   - places: Audiobooks' own store of where you are in a book
    ///     (``BookPositionSync``), handed to the player as its position store.
    static func over(
        _ player: PlayerModel?,
        in container: AppContainer,
        places: BookPositionSync
    ) -> AudiobooksPhonePlayer? {
        guard let player else { return nil }
        return AudiobooksPhonePlayer(player: player, key: Self.key(of: container), places: places)
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

    private init(player: PlayerModel, key: BookServerKey, places: BookPositionSync) {
        self.player = player
        self.key = key
        // **Re-pointed on every ask**, which is what makes a house move safe:
        // the store carries the household it writes under, and the player is
        // the one object that outlives the move. The player itself has already
        // been told the house changed by ``ShellModel/refresh()``, which is
        // what drops the old house's queue.
        player.positions = AudiobookPlaces(places: places, server: key)
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
