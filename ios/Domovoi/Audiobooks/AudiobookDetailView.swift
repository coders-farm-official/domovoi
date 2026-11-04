import DomovoiKit
import SwiftUI

/// One book: its head, `play` or `resume`, and its chapters.
///
/// `BookPage.kt:69`. What is **not** drawn, and why there is no control for
/// any of it, is in ``AudiobookDetailModel``'s own doc comment — the shelf's
/// six controls (`:175-212`) and `remove from server (admin)` (`:226-239`).
/// The short version of the second one: the delete needs an
/// `Authorization: Bearer` that ``DomovoiClient`` cannot send from here, and
/// sending an admin password over a `URLSession` of this item's own would drop
/// the cleartext and redirect policies that protect it. Files set the same
/// precedent for the same route (`FilesView.swift:26-31`).
///
/// ### The back row is ported after all
///
/// `MAP.md` §1.2 B1 planned to drop Android's back row and lean on the
/// navigation stack's own button, on the strength of this screen being pushed.
/// **It is not pushed.** ``ShellModel/path`` is `[Route]`, so the workspace's
/// `NavigationStack` can only carry a route, and a book is not one; adding a
/// second `navigationDestination` for `Int64` would need a change to a shared
/// file this item may not make. So the library swaps itself for this page
/// exactly as Android does (`AudiobooksScreen.kt:118`, `:178-190`) and the
/// back control is the one Android draws (`:140-145`). DECISIONS §7.
struct AudiobookDetailView: View {

    let detail: AudiobookDetailModel

    /// The library this page came from — the one holder of the resume
    /// dialog's state, so a page and the list cannot disagree about which
    /// book is being asked about.
    let library: AudiobooksModel

    let onBack: () -> Void

    /// The one save in flight, or nil on a page with no server.
    ///
    /// **The shell's, not this page's.** ``AudiobooksView`` holds it, because
    /// a save of a six-hour book outlives the back button — a `Task` owned by
    /// a page that goes away is a `Task` cancelled half a gigabyte in. It is
    /// also why there is one: ``SaveToDeviceModel`` is one transfer at a time.
    var saves: SaveToDeviceModel?

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            backRow
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    if let book = detail.book {
                        head(book)
                        actions
                        chapters
                    } else if detail.read.loading {
                        ProgressView()
                            .frame(maxWidth: .infinity, alignment: .center)
                            .accessibilityLabel(AudiobooksStrings.title)
                    } else if let trouble = detail.read.error {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(trouble)
                                .font(.footnote)
                                .foregroundStyle(Color.errInk)
                                .fixedSize(horizontal: false, vertical: true)
                            HomeLink(AudiobooksStrings.retry) { detail.read.refresh() }
                        }
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .task { detail.start() }
        .onDisappear { detail.stop() }
        // The place is re-read when a plan is answered, which is Android's
        // `produceState(null, bookId, playing, plan)` (`:96-98`) minus the
        // `playing` key — nothing on this screen knows whether the phone is
        // playing, and the player's own saves write through the same store.
        .onChange(of: library.ask == nil) { _, closed in
            if closed { detail.refreshPlace() }
        }
    }

    // MARK: - the back row

    /// `:140-145` — the arrow and the word `audiobooks`.
    ///
    /// One control, not two: the arrow and the label are inside a single
    /// `Button`, because two adjacent controls that do the same thing is the
    /// finding the connection panel's two `forget` buttons produced.
    private var backRow: some View {
        Button(action: onBack) {
            HStack(spacing: 6) {
                Image(systemName: "chevron.left")
                    .font(.body)
                Text(AudiobooksStrings.title.lowercased())
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .accessibilityLabel(AudiobooksStrings.backToLibrary)
        .padding(.horizontal, 16)
        .padding(.top, 4)
    }

    // MARK: - the head

    /// `BookHead` (`BookUi.kt:313-344`) without the save badge: the cover, the
    /// title, the author, `read by <narrator>`, the meta line and the place.
    @ViewBuilder
    private func head(_ book: AudiobookRow) -> some View {
        // A row at the ordinary sizes and a column at an accessibility one: a
        // 112-point cover beside wrapped title, author, narrator, meta and
        // place is more than a phone's width at AX3, and
        // `minimumScaleFactor` is rejected throughout this app.
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 12) {
                AudiobookCoverView(book: book, covers: library.covers, side: .head)
                headWords(book)
            }
        } else {
            HStack(alignment: .top, spacing: 16) {
                AudiobookCoverView(book: book, covers: library.covers, side: .head)
                headWords(book)
            }
        }
    }

    @ViewBuilder
    private func headWords(_ book: AudiobookRow) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(book.displayTitle)
                .font(.title3)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            if let author = book.author, !author.isEmpty {
                Text(author)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            if let narrator = detail.narratorLine {
                Text(narrator)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            Text(detail.metaLine)
                .font(.caption)
                .monospaced()
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            if let place = detail.placeText {
                Text(place)
                    .font(.footnote)
                    .foregroundStyle(Color.brandInk)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            // Said rather than drawn: this phone has never introduced itself
            // to the house, so the server has no slot for it and nothing
            // elsewhere can be offered. **Not an error** — the place kept
            // here still works, which is what the sentence says.
            if let note = detail.noDeviceNote {
                Text(note)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - the two actions

    /// `play` / `resume` (`:173`) and `save to this device` (`:175-212`).
    ///
    /// Android's save control has five states and they are its **shelf's** —
    /// a managed library of saved books with a discard, which this app has not
    /// got. What is here is the one-off: fetch the book and hand it to the
    /// share sheet, so it lands wherever the person keeps things (D47). A
    /// folder book arrives as the zip the server builds.
    ///
    /// `play` is absent for a book with no playable file and on a phone that
    /// cannot play audio (``AudiobookDetailModel/canPlay``). The save does not
    /// need the phone to be able to play — keeping a file needs no engine —
    /// but it does need the book to **name** one
    /// (``AudiobookDetailModel/canSave``): the download route answers 404 for
    /// a folder book whose files name nothing, so a save drawn there would be
    /// a control that always fails. So either control may be the only one
    /// here, and the row lays itself out around whichever is drawn.
    ///
    /// **A column at an accessibility size**, as every two-control row in this
    /// app is: `play` and a progress bar side by side at AX5 put the bar at
    /// about forty points wide.
    @ViewBuilder
    private var actions: some View {
        if detail.canPlay || (saves != nil && detail.canSave) {
            let layout = typeSize.isAccessibilitySize
                ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                : AnyLayout(HStackLayout(alignment: .center, spacing: 12))
            layout {
                if detail.canPlay {
                    Button(detail.playWord) {
                        detail.playOrResume()
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Color.brand)
                    .foregroundStyle(Color.brandFg)
                }
                if let saves, detail.canSave, let book = detail.book {
                    SaveToDeviceButton(
                        state: saves.state,
                        onTap: { save(book, with: saves) },
                        onCancel: { saves.cancel() }
                    )
                }
            }
            .padding(.vertical, 8)
        }
    }

    /// Fetch the whole book and let ``AudiobooksView``'s share sheet hand it
    /// over.
    ///
    /// The name is built from the row rather than read off the response: the
    /// model writes to a path it chose before the first byte arrives
    /// (``AudiobookPath/downloadName(title:isFolder:fileExt:)``).
    private func save(_ book: AudiobookRow, with saves: SaveToDeviceModel) {
        saves.save(
            path: AudiobookPath.download(book.id),
            named: FileSaveModel.safeName(AudiobookPath.downloadName(
                title: book.displayTitle,
                isFolder: book.isFolder,
                fileExt: book.fileExt
            ))
        )
    }

    // MARK: - the chapters

    /// `:215-223` with `ChapterRow` (`BookUi.kt:280-309`).
    ///
    /// A chapter row is a **button only when something can play it**. On a
    /// book with no playable file, or with no player, the rows are plain text:
    /// a tap that silently did nothing is the misleading control this project
    /// will not ship, and a chapter list is still worth reading.
    @ViewBuilder
    private var chapters: some View {
        let list = detail.chapters
        if !list.isEmpty {
            Text(AudiobooksStrings.chaptersLabel)
                .font(.caption)
                .monospaced()
                .foregroundStyle(Color.fgMuted)
                .accessibilityAddTraits(.isHeader)
                .padding(.top, 8)
            ForEach(Array(list.enumerated()), id: \.offset) { index, chapter in
                AudiobookChapterRow(
                    number: index + 1,
                    chapter: chapter,
                    current: detail.currentChapter == index,
                    onPlay: detail.canPlay ? { detail.play(fromSec: chapter.startSec) } : nil
                )
            }
        }
    }
}

/// One chapter: its number, its title and where it starts.
///
/// `ChapterRow` (`BookUi.kt:280-309`). The current chapter is on
/// ``Color/brandSoft``-equivalent ground — here the app's own raised fill,
/// because there is no `brandSoft` token on this side — **and** it says so in
/// words to VoiceOver, since a background colour is not a thing a reader
/// hears and "the colour is never the only carrier of meaning" is this app's
/// standing rule.
struct AudiobookChapterRow: View {

    let number: Int
    let chapter: BookChapter

    /// The chapter the place falls in. `indexOfLast { startSec <= place + 0.5 }`,
    /// and only past five seconds (`BookPage.kt:218-219`) — below that nobody
    /// has started the book and highlighting chapter one would claim they had.
    let current: Bool

    /// Nil when nothing can play this chapter, which makes the row plain text
    /// rather than a dead button.
    let onPlay: (() -> Void)?

    /// The number column, which **grows with the type size**. A flat
    /// `frame(width:)` here is the Files row's bug: at AX5 the digits are
    /// about twice as wide and the title is drawn across them.
    @ScaledMetric(relativeTo: .body) private var numberWidth: CGFloat = 22

    private var title: String {
        chapter.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? AudiobooksStrings.chapterNumber(number)
            : chapter.title
    }

    var body: some View {
        if let onPlay {
            Button(action: onPlay) {
                face
            }
            .buttonStyle(.plain)
            .accessibilityLabel(title)
            .accessibilityValue(
                current
                    ? AudiobooksStrings.chapterPlayingNow(fmtDur(chapter.startSec))
                    : fmtDur(chapter.startSec)
            )
            .accessibilityHint(AudiobooksStrings.play)
        } else {
            face
                .accessibilityElement(children: .combine)
        }
    }

    private var face: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text("\(number)")
                .font(.caption)
                .monospaced()
                .foregroundStyle(Color.fgMuted)
                .frame(width: numberWidth, alignment: .trailing)
            Text(title)
                .font(.subheadline)
                // Android ellipsises this at one line (`:296`). It wraps here
                // for the row caption's reason: a one-line limit clips at
                // every type size, not only the large ones.
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Spacer(minLength: 8)
            Text(fmtDur(chapter.startSec))
                .font(.caption)
                .monospaced()
                .foregroundStyle(Color.fgMuted)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        .background(
            // An **opaque** fill, not a material: `.background(.bar)` cost
            // this track five rounds of misdiagnosis on Apple's contrast
            // audit, because a check cannot read a colour that is whatever
            // happens to be behind it (``HomeTonePill``'s note).
            current ? Color(.secondarySystemBackground) : Color.clear,
            in: RoundedRectangle(cornerRadius: 6)
        )
        .contentShape(.rect)
    }
}
