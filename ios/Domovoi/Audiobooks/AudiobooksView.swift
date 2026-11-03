import DomovoiKit
import SwiftUI

/// Audiobooks: every book on the server, a page at a time, and one book's
/// chapters and your place in it.
///
/// `android/.../ui/screens/audiobooks/AudiobooksScreen.kt` and `BookPage.kt`.
/// `ScreenRouter.kt:63` wraps the Android screen in a `SourceSwitchPage` over
/// two halves — the server's library and the phone's saved shelf — and **this
/// is the server half**. The device half (`SavedBooksScreen.kt`, the download
/// jobs, the upload picker) is the PhoneMedia item's, and this view is
/// deliberately one view with no chrome of its own so that wrapping it is one
/// line in the slot.
///
/// ### What is here
///
/// The library with its search, its three sorts and its count; `reindex` in
/// the header, which re-walks the server's audiobooks folder and says how
/// many books it found. A book opens its own page: the cover, who read it,
/// how long it runs, where you are, and a row per chapter that plays from
/// there. `play` asks first when you have a place in the book or when a room
/// of the same listener got further — "continue from the kitchen (2 h 13 m)",
/// the owner decision of 2026-10-20.
///
/// ### What is not, and why there is no control for it
///
/// **Add audiobooks to the server** (`:197-199`, `:428-455`). A document
/// picker, a folder grant and a multipart upload are the PhoneMedia item's,
/// and an `add` button that opens nothing is exactly the control the owner's
/// rule forbids. The empty library says where to put files instead, and
/// points at the `reindex` that is here.
///
/// **Save to phone**, the save badge, the progress bar and `discard what was
/// saved` (`:300`, `BookPage.kt:175-212`). All six are the phone's shelf.
/// ``AudiobooksModel/shelf`` is the seam and it is nil here, which draws
/// nothing rather than a dead pill.
///
/// **A "listening as" chip** (`:205`, `:334-344`). Nothing on iOS stores a
/// listener person, so the chip could only ever read `listening as …`. Every
/// position this screen reads or writes is therefore the server's
/// *unidentified* listener's slot, which is a different row from a person's.
/// DECISIONS §1 puts the question to the owner.
///
/// **Remove from server (admin)** (`BookPage.kt:226-239`). Two independent
/// blockers, either sufficient; ``AudiobookDetailModel`` states both.
///
/// **Play in a room.** Android offers none and the server has no route for
/// it: a cast hands the room library track ids and a book has no library id
/// (`CastPlan.kt:7-26`); a book in a room is a voice path inside the core
/// (`domovoi/handlers/spoken_audio_rooms.py`). The page's subtitle does not
/// promise one. DECISIONS §4.
///
/// ### The list is lazy and no row holds anything
///
/// `LazyVStack`, because this is a files-like page and the owner's standing
/// decision of 2026-10-11 is that those load as you scroll. Android's
/// `LazyVerticalGrid(GridCells.Adaptive(260.dp))` is **one column** under 520
/// dp, which every iPhone in portrait is, so a grid that is always one wide
/// is a list — and the paging threshold stays the grid's 12
/// (``Paging/gridThreshold``) so the two apps ask for page two at the same
/// row. A lazy row is recycled, so no row owns a `Task`: the cover fetch
/// belongs to ``AudiobookCoverStore`` and every write to
/// ``AudiobooksModel``, both of which outlive the row.
struct AudiobooksView: View {

    let model: ShellModel

    @State private var books: AudiobooksModel?

    /// The book whose page is open, or nil for the library.
    ///
    /// Android's `openBook` (`:118`), and for Android's reason rather than by
    /// imitation: the workspace's `NavigationStack` is typed `[Route]`
    /// (`ShellModel.swift:497`), so a book cannot be pushed onto it without a
    /// change to a shared file this item may not make. The page draws its own
    /// back control, as Android's does.
    @State private var openBook: Int64?

    /// The open page's model, kept beside ``openBook`` so the page is not
    /// rebuilt on every redraw — ``AudiobooksModel/detail(bookID:)`` starts an
    /// `ApiRead`, and one per frame would be one request per frame.
    @State private var page: AudiobookDetailModel?

    /// `onAppear`/`onDisappear`, for ``HomeView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy with its
    /// `onChange` live.
    @State private var visible = false

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        ZStack {
            if let books {
                if let page, openBook != nil {
                    AudiobookDetailView(detail: page, library: books) {
                        openBook = nil
                        self.page = nil
                    }
                } else {
                    library(books)
                }
            } else {
                // Before the model exists there is nothing to read and
                // nothing to say about the house; an empty canvas rather than
                // a spinner, which would claim a request is in flight.
                Color.clear
            }
            if let books, let ask = books.ask {
                AudiobookResumeDialog(
                    title: ask.plan.title,
                    plan: ask.plan,
                    onPlay: { books.playPlan(from: $0) },
                    onDismiss: { books.dismissAsk() }
                )
            }
        }
        .task {
            // Built here and not in `init`, for ``HomeView``'s reason: the
            // model needs the container and a `@State` initialiser runs
            // before the view has one.
            if books == nil {
                let made = AudiobooksModel(in: model.container, deviceID: model.deviceID)
                // **The Player merge.** Nil until this line, and nil draws no
                // `play` anywhere on the screen; the bridge is nil in turn
                // when this phone has no device id, because the player cannot
                // own a server slot without one. ``AudiobooksPhonePlayer``
                // has the whole reason it is a process-wide object.
                made.player = AudiobooksPhonePlayer.shared(
                    in: model.container,
                    deviceID: model.deviceID,
                    places: made.positions
                )
                books = made
            }
            books?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            books?.stop()
        }
        // **The toast is the shell's**, drawn over the whole app
        // (`AppShellView.swift:36`), because a toast about a reindex that
        // failed must outlive a scroll and does outlive this screen.
        .onChange(of: books?.toast) { _, text in
            guard let said = text else { return }
            model.showToast(said)
            books?.setToast(nil)
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            // The open page is the old house's book. Closed before the model
            // is told, so nothing reads a book id against a server that has
            // never heard of it — [[read-after-await-wrong-key]] in its
            // simplest form.
            openBook = nil
            page = nil
            books?.serverChanged()
        }
    }

    // MARK: - the library

    @ViewBuilder
    private func library(_ books: AudiobooksModel) -> some View {
        ScrollViewReader { scroller in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    header(books)
                    searchField(books)
                    sortChips(books)
                    list(books)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            // A new sort or search **opens at the top**. Batch 18 on Android:
            // the grid opened where the *old* list was scrolled to, the first
            // book out of view, because a lazy grid follows the old first
            // row's key into the new list (`:143-150`). The model raises
            // ``AudiobooksModel/wantsTop`` only once a page of the new list
            // has landed, which is the wait Android spells as a reload.
            .onChange(of: books.wantsTop) { _, wants in
                guard wants else { return }
                if let first = books.books.first?.id {
                    scroller.scrollTo(first, anchor: .top)
                }
                books.toppedTheList()
            }
        }
    }

    private func header(_ books: AudiobooksModel) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(AudiobooksStrings.title)
                    .font(.title2)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
                Text(AudiobooksStrings.subtitle)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            reindexButton(books)
        }
    }

    /// `reindex` / `indexing…` (`:200-202`).
    ///
    /// A **word**, as Android has it, and not Videos' `…` menu: there is one
    /// job on this screen rather than two, and a menu with a single item is a
    /// tap more for nothing. While the POST is in flight the label says
    /// `indexing…` and the button is disabled — the one state where pressing
    /// it again would start a second walk of the same folder.
    private func reindexButton(_ books: AudiobooksModel) -> some View {
        Button(books.reindexing ? AudiobooksStrings.indexing : AudiobooksStrings.reindex) {
            books.reindex()
        }
        .buttonStyle(.bordered)
        .tint(Color.brandInk)
        .disabled(books.reindexing)
        .accessibilityLabel(AudiobooksStrings.reindex)
    }

    /// The search box — **a different control at an accessibility size**.
    ///
    /// ``MusicFilters/searchField``'s shape and its reason: Apple's audit
    /// named Music's field at AX5 for "Text of this element may be clipped at
    /// larger Dynamic Type sizes", and a search you can see half of is the
    /// fault the server address field was given `axis: .vertical` for. It is
    /// **not** vertical at the ordinary sizes, because a search box
    /// permanently two lines tall is a worse screen for everybody who never
    /// changes their type size.
    ///
    /// The accessibility variant has **no `submitLabel`**: a vertical field is
    /// a multi-line one and its Return key puts in a newline, so a key
    /// labelled `search` would say one thing and do another — the defect
    /// found in three screens on 2026-11-02. Nothing is lost: the list has
    /// always searched as you type (``AudiobooksModel/typed(_:)``), and the
    /// key was never what ran it. The newline is stripped as it arrives, so a
    /// needle can never carry one into `&q=`.
    @ViewBuilder
    private func searchField(_ books: AudiobooksModel) -> some View {
        if typeSize.isAccessibilitySize {
            TextField(
                AudiobooksStrings.searchPlaceholder,
                text: Binding(
                    get: { books.search },
                    set: { typed in
                        books.typed(String(typed.filter { !$0.isNewline }))
                    }
                ),
                axis: .vertical
            )
            .lineLimit(2...)
            .padding(.horizontal, 8)
            .padding(.vertical, 10)
            .background(
                Color(.secondarySystemBackground),
                in: RoundedRectangle(cornerRadius: 8)
            )
            .overlay {
                RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary)
            }
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .accessibilityLabel(AudiobooksStrings.searchPlaceholder)
        } else {
            TextField(
                AudiobooksStrings.searchPlaceholder,
                text: Binding(get: { books.search }, set: { books.typed($0) })
            )
            .textFieldStyle(.roundedBorder)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .submitLabel(.search)
            .accessibilityLabel(AudiobooksStrings.searchPlaceholder)
        }
    }

    /// `title` · `author` · `newest` (`:211-223`).
    ///
    /// Three of them and no more: `sort` is a `Literal` on the server
    /// (`audiobooks.py:86`), so a fourth value is a 422 — which is why
    /// ``AudiobooksSort`` is the whole set and the chips are drawn from it
    /// rather than from a list written here.
    ///
    /// A row that becomes a **column** rather than squeezing, as Music's
    /// filter row does: `minimumScaleFactor` is rejected throughout this app
    /// and three pills do not fit a phone's width at an accessibility size.
    private func sortChips(_ books: AudiobooksModel) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { chips(books) }
            VStack(alignment: .leading, spacing: 8) { chips(books) }
        }
    }

    @ViewBuilder
    private func chips(_ books: AudiobooksModel) -> some View {
        ForEach(AudiobooksSort.allCases, id: \.self) { sort in
            AudiobooksSortChip(sort: sort, chosen: books.sort == sort) {
                books.choose(sort)
            }
        }
    }

    // MARK: - the rows

    @ViewBuilder
    private func list(_ books: AudiobooksModel) -> some View {
        if let count = books.countText {
            Text(count)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        // Three states, not two: a list that has not answered is not an empty
        // list (``PagedState/loaded``).
        if !books.list.state.loaded {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(AudiobooksStrings.title)
        }
        ForEach(Array(books.books.enumerated()), id: \.element.id) { index, book in
            AudiobooksBookRow(book: book, model: books) {
                page = books.detail(bookID: book.id)
                openBook = book.id
            }
            .id(book.id)
            .onAppear {
                // The guarded question, never a bare "the last row appeared":
                // a one-screen list's last row is on screen from the start,
                // and asking then is the iOS shape of Android's `nearEnd`
                // bug.
                if books.shouldLoadMore(lastVisible: index) {
                    books.list.loadMore()
                }
            }
        }
        // Two lines, and which two says which emptiness it is: a search that
        // matched nothing names the needle back, a house with no books says
        // where to put some (``AudiobooksModel/emptyNote``). Getting these the
        // wrong way round tells somebody with a library that they have none.
        if let note = books.emptyNote {
            VStack(alignment: .leading, spacing: 4) {
                Text(note.title)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(note.detail)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 8)
        }
        footer(books)
    }

    @ViewBuilder
    private func footer(_ books: AudiobooksModel) -> some View {
        let list = books.list.state
        if list.loading, !list.items.isEmpty {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        } else if let trouble = list.error {
            // A failed read draws the failure and a retry. **Never an empty
            // library**: "no audiobooks yet" beside a read that did not land
            // is a lie about the house.
            VStack(alignment: .leading, spacing: 8) {
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(AudiobooksStrings.retry) { books.list.retry() }
            }
        } else if list.canLoadMore {
            // Drawn as well as the scroll trigger, not instead of it: the
            // trigger cannot fire for a list the person has stopped
            // scrolling, and VoiceOver's reader does not scroll at all.
            HomeLink(AudiobooksStrings.loadMore) { books.list.loadMore() }
        }
    }
}

/// One sort chip: the word, and whether it is the one in force.
///
/// Android's `FilterChip` (`:214`). The chosen chip is **the brand fill with
/// `brandFg` on it** rather than amber text, which would be 2.05:1, and it
/// says which it is to VoiceOver with `.isSelected` as well as by colour —
/// this app's standing rule that a colour is never the only carrier of
/// meaning.
struct AudiobooksSortChip: View {

    let sort: AudiobooksSort
    let chosen: Bool
    let onPick: () -> Void

    var body: some View {
        Button(action: onPick) {
            Text(sort.label)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(chosen ? Color.brandFg : Color.brandInk)
        .background(
            // Opaque either way: a contrast check cannot read a colour that
            // is whatever happens to be behind it (``HomeTonePill``'s note).
            chosen ? Color.brand : Color(.secondarySystemBackground),
            in: Capsule()
        )
        .accessibilityAddTraits(chosen ? [.isButton, .isSelected] : [.isButton])
    }
}
