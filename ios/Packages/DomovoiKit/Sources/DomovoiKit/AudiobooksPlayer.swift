import Foundation

/*
 * The two seams this item leaves for the branches being built beside it: who
 * plays a book on the phone (the Player item), and who knows whether a book
 * is saved to it (the PhoneMedia item). Both are optional on
 * ``AudiobooksModel``, and **nil draws no control** rather than a control
 * that cannot do what it says.
 *
 * Android has neither seam: `app.player` and `app.books` are on the
 * container, built before any screen (`BookPlay.kt:19`,
 * `AudiobooksScreen.kt:254`). Here the Player and PhoneMedia items are being
 * written in parallel tonight and none of their names exist yet, so guessing
 * them would make this branch fail to compile in a train. One adapter file
 * per seam conforms the other item's type to it when the branches merge.
 * DECISIONS §5.
 */

/// One playable piece of a book, as whatever plays audio wants it:
/// a server path and how long it runs. Android's `player/MediaPart`
/// (`PlayItem.kt:88-107`, built at `BookPlay.kt:28`).
public struct AudiobookMediaPart: Sendable, Equatable {

    /// The server path — ``AudiobookPath/audio(bookID:part:)``. A path and
    /// not a URL: the player resolves it through the app's own client, which
    /// is what keeps the household token in a header and out of a URL.
    public let path: String

    /// Nil when the server did not say.
    public let durationSec: Double?

    public init(path: String, durationSec: Double?) {
        self.path = path
        self.durationSec = durationSec
    }
}

/// What Audiobooks asks of whatever plays audio on this phone.
/// `BookItems.streaming` + `playPlan` (`BookPlay.kt:19`, `BookUi.kt:196`)
/// folded into one request.
public struct AudiobookPlayRequest: Sendable, Equatable {

    public let bookID: Int64
    public let title: String
    public let author: String?

    /// The whole book's length, when the server said.
    public let durationSec: Double?

    /// The cover's **server path**, never an address elsewhere
    /// (``BookRules/coverPath(_:)``).
    public let coverPath: String?

    public let chapters: [BookChapter]

    /// In order. **Empty means nothing could be played** — a folder book that
    /// names no file — and ``AudiobooksModel`` draws no play control for it,
    /// because the audio route would 400 (`audiobooks.py:325-326`).
    public let parts: [AudiobookMediaPart]

    /// Where to start, in seconds from the beginning of the book.
    public let startSec: Double

    public let speed: Double

    public init(
        bookID: Int64,
        title: String,
        author: String?,
        durationSec: Double?,
        coverPath: String?,
        chapters: [BookChapter],
        parts: [AudiobookMediaPart],
        startSec: Double,
        speed: Double
    ) {
        self.bookID = bookID
        self.title = title
        self.author = author
        self.durationSec = durationSec
        self.coverPath = coverPath
        self.chapters = chapters
        self.parts = parts
        self.startSec = startSec
        self.speed = speed
    }

    /// The streamed book, from `startSec` at `speed`. `BookItems.streaming`
    /// (`BookPlay.kt:19`).
    public static func streaming(
        _ book: AudiobookRow,
        startSec: Double = 0,
        speed: Double = 1
    ) -> AudiobookPlayRequest {
        AudiobookPlayRequest(
            bookID: book.id,
            title: book.displayTitle,
            author: book.author,
            durationSec: book.durationSec,
            coverPath: BookRules.coverPath(book),
            chapters: BookRules.chapters(book),
            parts: BookRules.parts(book).map {
                AudiobookMediaPart(
                    path: AudiobookPath.audio(bookID: book.id, part: $0),
                    durationSec: $0.durationSec
                )
            },
            startSec: max(0, startSec),
            speed: speed
        )
    }
}

/// Whatever plays audio on this phone.
///
/// `@MainActor` because the thing that conforms to it owns an
/// `AVPlayer`-shaped object and the screen calls it from a tap.
@MainActor
public protocol AudiobooksPlayer: AnyObject {

    /// Start this book here. The request carries where and how fast; the
    /// player owns the queue.
    func play(_ request: AudiobookPlayRequest)
}

/// What the phone's own shelf says about a book — one pill's worth, which is
/// all this screen draws of it (`SaveBadge`, `BookUi.kt:124`).
///
/// The shelf's own states are PhoneMedia's (`SaveState`: none, saving,
/// waiting, stopped, saved) and are deliberately **not** redeclared here: a
/// copy of somebody else's enum is two enums that drift. This is the label
/// and the one thing that changes how it is drawn.
public struct AudiobooksShelfState: Sendable, Equatable {

    /// `SaveStates.label(state, serverName)` — already assembled by the
    /// shelf, because the wording is the shelf's to own.
    public let label: String

    /// A save in progress, which is what makes the pill pulse.
    public let live: Bool

    public init(label: String, live: Bool) {
        self.label = label
        self.live = live
    }
}

/// The phone's saved books, when there are any to ask about.
///
/// Nil on ``AudiobooksModel`` here, and nil means **no badge and no save
/// control** (`MAP.md` §9): the library and a book's page still browse, play
/// and keep a place. PhoneMedia conforms its shelf to this and passes it in
/// when the two branches merge.
@MainActor
public protocol AudiobooksShelf: AnyObject {

    /// What the shelf says about this book, or nil when it holds no copy of
    /// it — which is the case that draws nothing at all.
    func state(ofBookID id: Int64) -> AudiobooksShelfState?
}
