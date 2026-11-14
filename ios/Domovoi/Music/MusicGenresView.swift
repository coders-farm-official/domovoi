import DomovoiKit
import SwiftUI

/// Music's `genres` tab: the search, the sort and the count, the "reading
/// genres from your files…" line while the server is still reading tags, and
/// a row per genre that opens its songs.
///
/// `GenresTab.kt`'s `genresTab`, `GenreFilters`, `GenreReadingLine` and
/// `GenreListRow` (3cdd261b). Every decision is ``MusicGenresModel``'s; this
/// draws it.
///
/// Rows are emitted inside ``MusicView``'s `LazyVStack`, so a long genre list
/// is built as it scrolls, and the next page is asked for through
/// ``MusicGenresModel/shouldLoadMore(lastVisible:)`` rather than a bare
/// `onAppear` on the last row.
struct MusicGenresView: View {

    let genres: MusicGenresModel

    /// Open a genre's sheet. A closure, so a row can open one and write
    /// nothing else of the screen's.
    let onOpen: (MusicGenreRow) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            filters
            if let line = genres.readingLine {
                readingLine(line)
            }
            if genres.isFirstLoad {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .accessibilityLabel(MusicGenresStrings.tab)
            }
            if let trouble = genres.list.state.error, genres.rows.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text(trouble)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                    HomeLink(MusicStrings.retry) { genres.list.retry() }
                }
            }
            if let empty = genres.emptyNote {
                emptyState(empty)
            }
            ForEach(Array(genres.rows.enumerated()), id: \.element.key) { index, row in
                MusicGenreListRow(row: row) { onOpen(row) }
                    .onAppear {
                        if genres.shouldLoadMore(lastVisible: index) {
                            genres.list.loadMore()
                        }
                    }
            }
            if !genres.list.state.items.isEmpty {
                footer
            }
        }
    }

    // MARK: - search, sort, count

    @Environment(\.dynamicTypeSize) private var typeSize

    @ViewBuilder
    private var filters: some View {
        VStack(alignment: .leading, spacing: 10) {
            searchField
                .accessibilityIdentifier("music-genres-search")
            // A column at an accessibility size: a menu and a count beside
            // it do not fit a phone at AX3, and `minimumScaleFactor` is
            // rejected throughout this app.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { sortAndCount }
                VStack(alignment: .leading, spacing: 6) { sortAndCount }
            }
        }
    }

    @ViewBuilder
    private var sortAndCount: some View {
        Picker(MusicGenresStrings.sortLabel, selection: sortChoice) {
            ForEach(MusicGenreSort.allCases, id: \.self) { sort in
                Text(sort.label).tag(sort)
            }
        }
        .pickerStyle(.menu)
        Spacer(minLength: 0)
        if let count = genres.countText {
            Text(count)
                .font(.caption.monospaced())
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The library search's two shapes (``MusicFilters``' `searchField`, and
    /// its reasons): a vertical field at an accessibility size, so the hint
    /// is not clipped, with no `search` Return key on it and newlines
    /// stripped as they arrive; the ordinary rounded field otherwise.
    @ViewBuilder
    private var searchField: some View {
        if typeSize.isAccessibilitySize {
            TextField(
                MusicGenresStrings.searchPlaceholder,
                text: searchText,
                prompt: ShellHintPrompt.text(MusicGenresStrings.searchPlaceholder),
                axis: .vertical
            )
                .onChange(of: genres.search) { _, new in
                    if new.contains(where: \.isNewline) {
                        genres.search = new.filter { !$0.isNewline }
                    }
                }
                .lineLimit(2...)
                .padding(.horizontal, 8)
                .padding(.vertical, 10)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
                .overlay { RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary) }
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityLabel(MusicGenresStrings.searchPlaceholder)
        } else {
            TextField(MusicGenresStrings.searchPlaceholder, text: searchText)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .accessibilityLabel(MusicGenresStrings.searchPlaceholder)
        }
    }

    private var searchText: Binding<String> {
        Binding(get: { genres.search }, set: { genres.search = $0 })
    }

    private var sortChoice: Binding<MusicGenreSort> {
        Binding(get: { genres.sort }, set: { genres.sort = $0 })
    }

    // MARK: - the read in progress

    /// Spoken as it changes (`liveRegion = Polite` on Android): the numbers
    /// move every ten seconds while the server reads.
    private func readingLine(_ line: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            ProgressView()
                .controlSize(.mini)
                .accessibilityHidden(true)
            Text(MusicGenresStrings.readingLine(line))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.updatesFrequently)
        .accessibilityIdentifier("music-genres-reading")
    }

    // MARK: - empty, footer

    private func emptyState(_ empty: (title: String, note: String)) -> some View {
        VStack(spacing: 6) {
            Text(empty.title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(empty.note)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if genres.mayClearSearch {
                HomeLink(MusicGenresStrings.clearSearch) { genres.clearSearch() }
            }
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.vertical, 24)
    }

    /// The next page's spinner, a failure with its retry, or `load more` for
    /// someone who would rather tap — ``MusicLibraryList``'s footer.
    @ViewBuilder
    private var footer: some View {
        let list = genres.list.state
        if list.loading {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        } else if let trouble = list.error {
            VStack(alignment: .leading, spacing: 8) {
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(MusicStrings.retry) { genres.list.retry() }
            }
        } else if list.canLoadMore {
            HomeLink(MusicStrings.loadMore) { genres.list.loadMore() }
        }
    }
}

/// One genre: its name and how many tracks, the whole row a button.
/// "no genre" is drawn muted, as Android's is.
struct MusicGenreListRow: View {

    let row: MusicGenreRow
    let onOpen: () -> Void

    var body: some View {
        Button(action: onOpen) {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.name)
                        .font(.subheadline)
                        .foregroundStyle(row.key == MusicGenres.noGenreKey ? Color.fgMuted : Color.primary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(MusicGenresStrings.tracks(row.trackCount))
                        .font(.caption.monospaced())
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityHidden(true)
            }
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityIdentifier("music-genre-row-\(row.key)")
    }
}
