import DomovoiKit
import SwiftUI

/// One show's episodes.
///
/// `PodcastsEpisodes.kt:71-213`'s `EpisodeOverlay`. Android draws it
/// full-screen on a compact width and as a 520 dp right-hand panel over a
/// scrim on an expanded one (`:83-84, 129-133`); **an iPhone is always the
/// compact case**, so this is a sheet and nothing is lost — the panel branch
/// is for tablets.
///
/// ### What is here
///
/// The show's cover, name and author; `unsubscribe` and `close`; a search
/// over the episode titles that goes to the **server** (`?q=`, title only —
/// unlike the shows search, which also covers the author and the feed URL);
/// the episodes a page at a time, newest first; and the destructive confirm.
///
/// ### What is not, and why there is no control for it
///
/// **The play button**, which Android draws on every row and *disables*
/// unless the house has the file (`:223`). There is no play control here at
/// all — not because of `has_file` but because nothing in this app owns an
/// audio engine to play into (``PodcastsView``'s doc comment has the whole
/// reason). The meta line still says `· pending` or `· failed` where the
/// button would be, which is the answer to "why can I not play this", and
/// ``PodcastStrings/inTheDashboard`` at the foot says where listening is
/// done.
///
/// **`save to this device`**, for the reason in ``PodcastStrings/inTheDashboard``:
/// an episode row carries no size and the app's only save path has a 64 MiB
/// ceiling.
struct PodcastEpisodesSheet: View {

    let show: PodcastShow
    let podcasts: PodcastsModel
    let art: PodcastArtStore

    /// 56 points, as Android's 56 dp header art (`PodcastsEpisodes.kt:143`),
    /// and scaled for the same reason the card's is.
    @ScaledMetric private var side: CGFloat = 56

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    heading
                    searchField
                    list
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(show.displayTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button {
                        podcasts.closeEpisodes()
                    } label: {
                        Image(systemName: "xmark")
                            .frame(minWidth: 44, minHeight: 44)
                            .contentShape(.rect)
                    }
                    .accessibilityLabel(PodcastStrings.close)
                }
                ToolbarItem(placement: .destructiveAction) {
                    Button {
                        podcasts.confirmUnsubscribe(show)
                    } label: {
                        Image(systemName: "bell.slash")
                            .frame(minWidth: 44, minHeight: 44)
                            .contentShape(.rect)
                    }
                    // Icon-only, so it carries the word (NIGHT-RULES §3) —
                    // and the word is Android's own title for the confirm
                    // this opens (`PodcastsEpisodes.kt:196`).
                    .accessibilityLabel(PodcastStrings.unsubscribe)
                }
            }
        }
        // An `alert` rather than a `confirmationDialog`, as
        // ``MusicQueueView``'s clear does: it has a body to read as well as a
        // choice to make, and the destructive option must be the one that is
        // harder to hit by accident.
        //
        // The sentence names the show through ``PodcastShow/displayTitle``,
        // so a feed with no title reads `Unsubscribe from "podcast"?` where
        // Android reads `Unsubscribe from "null"?`.
        .alert(
            PodcastStrings.unsubscribe,
            isPresented: Binding(
                get: { podcasts.confirmingUnsubscribe != nil },
                // Only ever a dismissal: the two buttons below act and then
                // the model clears the field itself, so nothing here may set
                // a show.
                set: { if !$0 { podcasts.cancelUnsubscribe() } }
            ),
            presenting: podcasts.confirmingUnsubscribe
        ) { asked in
            Button(PodcastStrings.unsubscribe, role: .destructive) {
                podcasts.unsubscribe(asked)
            }
            Button(PodcastStrings.cancel, role: .cancel) {
                podcasts.cancelUnsubscribe()
            }
        } message: { asked in
            Text(PodcastStrings.unsubscribeBody(asked.displayTitle))
        }
    }

    // MARK: - the show

    /// The cover, the name and the author — the same three the card on the
    /// grid draws, because a sheet with no name on its body is a sheet whose
    /// title bar is the only thing saying which show this is, and a
    /// navigation title truncates where a wrapping `Text` does not.
    private var heading: some View {
        layout {
            PodcastArtView(path: PodcastArt.path(for: show), store: art, side: side)
            VStack(alignment: .leading, spacing: 2) {
                Text(show.displayTitle)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if let author = show.author, !author.isEmpty {
                    Text(author)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var layout: AnyLayout {
        dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 10))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 10))
    }

    private var searchField: some View {
        TextField(
            PodcastStrings.searchEpisodes,
            text: Binding(get: { podcasts.episodeFilter }, set: { podcasts.typedEpisode($0) })
        )
        .textFieldStyle(.roundedBorder)
        .autocorrectionDisabled()
        .textInputAutocapitalization(.never)
        .submitLabel(.search)
        .accessibilityLabel(PodcastStrings.searchEpisodes)
    }

    // MARK: - the episodes

    @ViewBuilder
    private var list: some View {
        if let count = podcasts.episodeCountText {
            Text(count)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        if let episodes = podcasts.episodes {
            // Three states, not two: a list that has not answered is not an
            // empty list (``PagedState/loaded``).
            if !episodes.state.loaded {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .accessibilityLabel(PodcastStrings.episodesNoun)
            }
            ForEach(Array(episodes.state.items.enumerated()), id: \.element.id) { index, episode in
                PodcastEpisodeRow(episode: episode)
                    .onAppear {
                        // The guarded question, never a bare "the last row
                        // appeared" — and the **list's** threshold, because
                        // this is a list (`Paged.kt:89`).
                        if podcasts.shouldLoadMoreEpisodes(lastVisible: index) {
                            episodes.loadMore()
                        }
                    }
                Divider()
            }
            if let note = podcasts.episodesEmptyNote {
                VStack(alignment: .leading, spacing: 4) {
                    Text(note.title)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                    // `try "poll now"` — which is on the screen behind this
                    // sheet, so it is said rather than offered: a second
                    // `poll now` button in here would be a control that
                    // reloads a list the person cannot see while it works.
                    Text(note.detail)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 8)
            }
            footer(episodes)
        }
        // Where listening and saving happen, said once at the foot — the same
        // move ``FilesView`` makes for its missing delete, and in a `Text`
        // rather than a disabled button for the same reason: a greyed-out
        // play control is the exact thing this screen is built not to draw.
        Text(PodcastStrings.inTheDashboard)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 8)
    }

    @ViewBuilder
    private func footer(_ episodes: Pager<PodcastEpisode, Int64>) -> some View {
        let state = episodes.state
        if state.loading, !state.items.isEmpty {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        } else if let trouble = state.error {
            VStack(alignment: .leading, spacing: 8) {
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(PodcastStrings.retry) { episodes.retry() }
            }
        } else if state.canLoadMore {
            // Drawn as well as the scroll trigger, for ``PodcastsView``'s
            // reason: VoiceOver's reader does not scroll.
            HomeLink(PodcastStrings.loadMore) { episodes.loadMore() }
        }
    }
}

/// One episode: its name, and a mono line saying how long it is, how many
/// chapters it has and — when the house has no file — what the download is
/// doing.
///
/// `PodcastsEpisodes.kt:216-265`'s `EpisodeRow`, less the two controls it
/// draws: the play button (nothing owns an engine) and `save to this device`
/// (no download manager, and no size on the row). Both are
/// ``PodcastsView``'s doc comment.
///
/// The whole line comes from ``PodcastEpisode/metaLine``, which is where
/// `has_file` and `download_status` are read **together** — the one pair on
/// this screen that must agree.
struct PodcastEpisodeRow: View {

    let episode: PodcastEpisode

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(episode.displayTitle)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Text(episode.metaLine)
                .font(.caption)
                .monospaced()
                // `errInk` for a download that failed, `fgMuted` otherwise —
                // and never `.red`, which is not an ink (NIGHT-RULES §3). The
                // colour is **not** the only carrier: the word `failed` is in
                // the line it colours.
                .foregroundStyle(episode.downloadFailed ? Color.errInk : Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 4)
        // One stop per episode, reading the name and then the line: two
        // elements would make a back catalogue of fifty-seven a hundred and
        // fourteen stops.
        .accessibilityElement(children: .combine)
    }
}
