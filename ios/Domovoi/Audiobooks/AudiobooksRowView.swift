import DomovoiKit
import SwiftUI

/// One book in the library: its cover, its title, who wrote it, how long it
/// runs — and `play`, when there is something to play it with.
///
/// `AudiobooksScreen.kt:290`'s `BookCard`, with two things left out and said
/// here rather than silently:
///
/// * **No `SaveBadge`.** The badge reads the phone's own shelf
///   (`SaveStates.of(...)`, `BookUi.kt:124`), which is the PhoneMedia item's.
///   ``AudiobooksModel/shelfState(_:)`` answers nil for every row while that
///   shelf is absent, and nil draws nothing at all rather than a pill saying
///   `not saved` — a phone that cannot save anything has nothing to report
///   about saving. The call is here, so PhoneMedia's merge is a seam filled
///   and not a row rewritten.
/// * **No `play` for a book with no playable file.** ``AudiobooksModel/canPlay(_:)``
///   is false for a folder book that names none, whose audio route answers
///   **400** (`audiobooks.py:325-326`), and for a phone with nothing to play
///   audio with. The row still draws and still opens; what it does not do is
///   offer a button that cannot work ([[no-misleading-controls]]).
///
/// ### Nothing in this row holds a `Task`
///
/// The row is a lazy cell and is recycled. The cover fetch belongs to
/// ``AudiobookCoverStore``, which outlives the row, the list and the screen;
/// the row only ever *asks* for one (`.task(id:)`), and an ask that is
/// cancelled by recycling is an ask the store has already recorded.
struct AudiobooksBookRow: View {

    let book: AudiobookRow
    let model: AudiobooksModel

    /// What a tap on the card does — the library swaps itself for the book's
    /// page, as `openBook = book.id` does on Android (`:118`).
    let onOpen: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Button(action: onOpen) {
                face
            }
            .buttonStyle(.plain)
            // The whole card is the button, so what it reads out has to be
            // the book rather than the word "open": VoiceOver walks a list of
            // these, and nine rows saying "open" name nothing.
            .accessibilityLabel(book.displayTitle)
            .accessibilityValue(model.meta(book))
            if model.canPlay(book) {
                playButton
            }
        }
    }

    // MARK: - the card

    private var face: some View {
        HStack(alignment: .top, spacing: 12) {
            AudiobookCoverView(book: book, covers: model.covers, side: .row)
            VStack(alignment: .leading, spacing: 2) {
                Text(book.displayTitle)
                    .font(.subheadline)
                    // Android caps this at one line (`:306`). Here it wraps:
                    // `lineLimit(1)` is what Apple's audit reported as "Text
                    // clipped" against the recently-played strip's caption,
                    // and it was right — a long title is cut at **every**
                    // type size, not only the large ones. `minimumScaleFactor`
                    // is rejected throughout this app.
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if let author = book.author, !author.isEmpty {
                    Text(author)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                Text(model.meta(book))
                    .font(.caption)
                    .monospaced()
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                // Nil for every row while there is no shelf, which is every
                // row on this branch.
                if let shelf = model.shelfState(book) {
                    HomeTonePill(label: shelf.label, tone: shelf.live ? .brand : .idle)
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .accessibilityHidden(true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(.rect)
    }

    /// `play` on the card (`:325`).
    ///
    /// An icon with a label rather than Android's bare glyph, and 44 points
    /// either way: Apple's audit has measured a plain text button on this
    /// track at 8.7 points, and a glyph with no `accessibilityLabel` is a
    /// control VoiceOver reads as nothing.
    private var playButton: some View {
        Button {
            model.start(book)
        } label: {
            Image(systemName: "play.fill")
                .font(.body)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .accessibilityLabel(AudiobooksStrings.play)
        .accessibilityValue(book.displayTitle)
    }
}

/// How big a cover is drawn.
///
/// Two sizes, because Android has two — 56 dp on a card (`:292`) and 112 dp
/// in a book's head (`BookPage.kt:316`) — and a number written at each call
/// site is how the two come to drift.
enum AudiobookCoverSide {
    case row
    case head

    var points: CGFloat {
        switch self {
        case .row: return 56
        case .head: return 112
        }
    }
}

/// A book's cover, with the book glyph behind it.
///
/// `BookCover` (`BookUi.kt:95-120`): the glyph is drawn first and the picture
/// laid over it, so a cover that never arrives leaves something rather than a
/// hole. The bytes come from ``AudiobookCoverStore`` and **not** `AsyncImage`
/// — that store's own doc has the whole reason, and the short version is that
/// `AsyncImage` would be the one request in this app made outside the
/// client's cleartext and redirect policies.
///
/// The side is a ``ScaledMetric``, not a flat number. A fixed `frame` on a
/// square that sits beside three lines of text is the exact shape of the
/// Files row's bug: at AX5 the words grow past the picture and the row reads
/// as two overlapping things.
struct AudiobookCoverView: View {

    let book: AudiobookRow
    let covers: AudiobookCoverStore
    let side: AudiobookCoverSide

    @ScaledMetric(relativeTo: .body) private var scale: CGFloat = 1

    private var points: CGFloat { side.points * scale }

    /// Nil for a book the server named no cover for, which is a book nothing
    /// is ever asked about.
    private var path: String? { BookRules.coverPath(book) }

    var body: some View {
        Rectangle()
            .fill(Color(.secondarySystemBackground))
            .overlay(alignment: .center) { picture }
            .frame(width: points, height: points)
            .clipShape(.rect(cornerRadius: 6))
            .overlay {
                RoundedRectangle(cornerRadius: 6)
                    .strokeBorder(Color(.separator), lineWidth: 1)
            }
            // Decorative: every caller draws the title beside it, and a cover
            // VoiceOver announced would be a second name for the same book.
            .accessibilityHidden(true)
            .task(id: book.id) {
                // Only when the server named a cover route at all. A book
                // with neither `cover_url` nor `artwork` is one the indexer
                // found no picture for, and asking for a 404 we can predict
                // is a request for nothing.
                guard path != nil else { return }
                covers.want(book.id)
            }
    }

    @ViewBuilder
    private var picture: some View {
        switch covers.cover(book.id) {
        case .image(let bytes):
            if let image = UIImage(data: bytes) {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                // 200, over the byte floor, and still not something the phone
                // can decode: a format rather than an absence. Written out
                // because `Image(uiImage:)` of a nil would draw nothing at
                // all and read as a blank cover.
                AudiobookGlyph(side: side)
            }
        case .loading, .absent, .failed, nil:
            // No spinner. A list of nine spinners on a slow house is noise,
            // and the glyph is what most rows settle to anyway — the indexer
            // only finds a cover when the file carries one.
            AudiobookGlyph(side: side)
        }
    }
}

/// The book glyph a cover-less book draws.
///
/// ``Color/fgMuted`` and not a dimmed `.secondary`: a glyph that carries
/// meaning needs 3:1, this token measures 7.44:1 on the light canvas, and
/// `.secondary` at 60% is about 2:1 — the finding Apple's audit opened this
/// app with.
struct AudiobookGlyph: View {

    let side: AudiobookCoverSide

    var body: some View {
        Image(systemName: "book.closed")
            .font(.system(size: side == .head ? 44 : 22))
            .foregroundStyle(Color.fgMuted)
            .accessibilityHidden(true)
    }
}
