import Foundation
import Observation

/// One book's page: the read, the phone's place in it, the chapters and which
/// one is playing. `BookPage.kt:69`.
///
/// ### What is not here, and why there is no control for it
///
/// * **No "save to phone", "remove from phone", "cancel save", "resume save",
///   progress bar or "discard what was saved"** (`BookPage.kt:175-212`).
///   Every one of them is the phone's shelf and its download jobs, which are
///   the PhoneMedia item's. While ``AudiobooksModel/shelf`` is nil this page
///   draws none of them.
/// * **No "remove from server (admin)"** (`:226-239`). Two independent
///   blockers, either sufficient: the delete needs an
///   `Authorization: Bearer` that
///   ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
///   cannot send, and sending an admin password over a `URLSession` of this
///   item's own would drop the cleartext and redirect policies that protect
///   it. Files set the same precedent for the same route. DECISIONS §2, and
///   the shared change it asks for.
/// * **No second back control** (`:140-145`). The navigation stack already
///   has one, and two controls for one action is one too many.
///
/// ### The resume dialog is the library's, not this page's
///
/// Android keeps a `plan` in `BookPage` and another in `AudiobooksScreen`
/// (`:87`, `AudiobooksScreen.kt:164`), because a Compose screen holds its own
/// `remember`. Here both pages ask ``AudiobooksModel/start(_:)`` and the one
/// dialog state lives on ``AudiobooksModel/ask`` — one question, one answer,
/// and no way for a page and the list to disagree about which book is being
/// asked about.
@MainActor
@Observable
public final class AudiobookDetailModel {

    public let bookID: Int64

    /// `rememberApi("audiobook", bookId)` — `GET /api/audiobooks/{id}`, or a
    /// 404 `{"detail": "audiobook N not found"}` (`audiobooks.py:244-262`).
    public let read: ApiRead<AudiobookRow>

    /// The phone's place in this book, in seconds, or nil when it has none.
    ///
    /// `produceState(null, bookId, playing, plan)` (`BookPage.kt:96-98`): the
    /// local place, re-read when the book changes, when play starts and when
    /// a plan is answered. Here ``refreshPlace()`` is that re-read and the
    /// view calls it on the same three occasions.
    public private(set) var placeSec: Double?

    private let library: AudiobooksModel
    private let serverKey: @Sendable () -> BookServerKey
    private let remote: @Sendable () -> (any BookPositionRemote)?

    init(
        bookID: Int64,
        in container: AppContainer,
        library: AudiobooksModel,
        serverKey: @Sendable @escaping () -> BookServerKey,
        remote: @Sendable @escaping () -> (any BookPositionRemote)?
    ) {
        self.bookID = bookID
        self.library = library
        self.serverKey = serverKey
        self.remote = remote
        let client = container.client
        self.read = ApiRead(in: container) {
            try await client.get(AudiobookPath.book(bookID), as: AudiobookRow.self)
        }
    }

    public var book: AudiobookRow? { read.data }

    // MARK: - the page's own reads

    /// Read the book and the place. The view's `onAppear`.
    public func start() {
        read.refresh()
        refreshPlace()
    }

    /// Re-read the phone's place in this book. **The local place only** — the
    /// compare with the server's slot belongs to
    /// ``BookPositionSync/open(bookID:server:)``, which runs when a book
    /// starts, as Android's does.
    public func refreshPlace() {
        let key = serverKey()
        let sync = library.positions
        let id = bookID
        Task { @MainActor [weak self] in
            let place = await sync.local(bookID: id, server: key)
            // The key was captured before the await. A page left for another
            // house while this was in flight must not show that house's
            // place under this book: [[read-after-await-wrong-key]], the top
            // defect class on this track.
            guard let self, self.serverKey().isSame(key) else { return }
            self.placeSec = place?.positionSec
        }
    }

    public func stop() {
        read.stop()
    }

    public func serverChanged() {
        placeSec = nil
        read.serverChanged()
    }

    // MARK: - what the page draws

    /// `read by Simon Vance`, or nil when the server named no narrator.
    public var narratorLine: String? {
        guard let narrator = book?.narrator, !ServerCredentials.isBlank(narrator) else { return nil }
        return AudiobooksStrings.readBy(narrator)
    }

    /// `"10:12:00 · 584 MB · 12 chapters"` — the head's meta line, which is
    /// **ordered differently from a row's** and is Android's own order
    /// (`BookPage.kt:160-164`): length, size, chapters, and each one left out
    /// rather than guessed.
    public var metaLine: String {
        guard let book else { return "" }
        var pieces: [String] = []
        if let length = book.durationSec { pieces.append(fmtDur(length)) }
        if let size = BookRules.sizeBytes(book) { pieces.append(fmtBytes(size)) }
        let chapters = BookRules.chapters(book).count
        if chapters > 0 { pieces.append(AudiobooksStrings.chapterCount(chapters)) }
        return pieces.joined(separator: " · ")
    }

    /// `"your place: 1:02:33 of 10:12:00 · 10%"`, or nil before the first
    /// listen.
    public var placeText: String? {
        AudiobooksStrings.place(positionSec: placeSec, durationSec: book?.durationSec)
    }

    /// `resume` past five seconds, else `play` (`BookPage.kt:173`).
    public var playWord: String {
        (placeSec ?? 0) > 5 ? AudiobooksStrings.resume : AudiobooksStrings.play
    }

    /// Whether the play control is drawn at all —
    /// ``AudiobooksModel/canPlay(_:)``, which is false for a book with no
    /// playable file and while nothing can play audio on this phone.
    public var canPlay: Bool {
        guard let book else { return false }
        return library.canPlay(book)
    }

    /// Whether `save to this device` is drawn at all.
    ///
    /// **A player is not needed — a file is.** Keeping a copy needs no audio
    /// engine, which is why this is not ``canPlay``; but the route that
    /// serves the copy needs something to serve. A folder book whose `files`
    /// name nothing has no audio in its folder, and
    /// `GET /api/audiobooks/{id}/download` answers **404 "book folder has no
    /// audio files"** for it (`web/backend/api/audiobooks.py:388-393`). A
    /// button that always fails is the control
    /// [[no-misleading-controls]] forbids, so it is absent rather than
    /// failing on the tap.
    ///
    /// ``BookRules/parts(_:)`` is the same predicate ``AudiobooksModel/canPlay(_:)``
    /// uses for the play control — one reading of "this book names a file",
    /// so the two controls cannot disagree about the same book.
    ///
    /// **Android draws its save ungated** (`BookPage.kt:174-181`): on that
    /// phone, tapping it on Sabriel starts a download that 404s and reports a
    /// failure. That divergence is a line in
    /// `design-notes/ios/ANDROID-DEFECTS-FOUND-BY-IOS.md`, written up in this
    /// unit's DECISIONS — not a reason to ship the same control here.
    public var canSave: Bool {
        guard let book else { return false }
        return !BookRules.parts(book).isEmpty
    }

    public var chapters: [BookChapter] {
        book.map(BookRules.chapters) ?? []
    }

    /// Which chapter row is highlighted, or nil for none.
    ///
    /// `chapters.indexOfLast { it.startSec <= current + 0.5 }`, and **only
    /// when the place is past five seconds** (`BookPage.kt:218-219`) — below
    /// that nobody has started the book and highlighting chapter one would
    /// claim they had.
    public var currentChapter: Int? {
        guard let place = placeSec, place > 5 else { return nil }
        // Kotlin's `indexOfLast`, which Swift spells `lastIndex(where:)`.
        return chapters.lastIndex(where: { $0.startSec <= place + 0.5 })
    }

    /// Said in place of the cross-device line when this phone has no device
    /// id: the local place still works and the server is asked nothing.
    /// **iOS-only wording**; Android has no such case. DECISIONS §3.
    public var noDeviceNote: String? {
        library.knowsThisPhone ? nil : AudiobooksStrings.noDeviceID
    }

    // MARK: - what the page does

    /// The `play` / `resume` button: asks first when there is a place to
    /// resume or somewhere further on.
    public func playOrResume() {
        guard let book else { return }
        library.start(book)
    }

    /// A tap on a chapter row: play from there, with no question
    /// (`BookPage.kt:221`).
    public func play(fromSec: Double) {
        guard let book else { return }
        library.play(book, from: fromSec)
    }
}
