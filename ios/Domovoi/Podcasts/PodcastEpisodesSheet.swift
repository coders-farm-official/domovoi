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
/// the episodes a page at a time, newest first; the destructive confirm; and
/// on every row the house has the audio for, **`play`** and **`save to this
/// device`** — the two controls Android draws (`PodcastsEpisodes.kt:183`,
/// `:258`).
///
/// ### A row with no file draws neither control
///
/// Android draws `play` always and *disables* it (`:223`). Here it is absent:
/// a control that cannot do what it says is not drawn, and the meta line
/// already says `· pending` or `· failed` where the button would be, which is
/// the answer to "why can I not play this". The save is absent for the same
/// row and the same reason — there is no file to save.
///
/// ### What is not, and why there is no control for it
///
/// **Playing to a room.** No server route plays an episode in a room; see
/// ``PodcastsView``'s doc comment.
struct PodcastEpisodesSheet: View {

    let show: PodcastShow
    let podcasts: PodcastsModel
    let art: PodcastArtStore

    /// The household, for the "listening as" chip under the show's name.
    /// Nil before the screen's `.task` has run.
    let listeners: PlayerListenerModel?

    /// The one save in flight. **The screen's**, not a row's: a lazy row is
    /// recycled as it scrolls and a transfer held on one is cancelled
    /// mid-file.
    let saves: SaveToDeviceModel?

    /// A failed save, said in the shell's toast — a sentence about a save has
    /// to outlive this sheet, and it does.
    let onTrouble: (String) -> Void

    /// 56 points, as Android's 56 dp header art (`PodcastsEpisodes.kt:143`),
    /// and scaled for the same reason the card's is.
    @ScaledMetric private var side: CGFloat = 56

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    heading
                    listeningAsChip
                    saving
                    unsubscribeTrouble
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
        // The saved place, as Android asks it (`PodcastsScreen.kt:316-343`).
        // An `alert` over this sheet rather than another sheet: two sheets
        // cannot both be up, and the unsubscribe confirm above is already an
        // alert for the same reason.
        .alert(
            podcasts.resuming?.title ?? PodcastStrings.resume,
            isPresented: Binding(
                get: { podcasts.resuming != nil },
                set: { if !$0 { podcasts.cancelResume() } }
            ),
            presenting: podcasts.resuming
        ) { asked in
            Button(PodcastStrings.resume) { podcasts.resume() }
            Button(PodcastStrings.startOver) { podcasts.startOver() }
            Button(PodcastStrings.cancel, role: .cancel) { podcasts.cancelResume() }
            _ = asked
        } message: { asked in
            Text(asked.said)
        }
        // The file is here: hand it over. ``SaveToDeviceModel/taken()``
        // deletes the copy, because an app that leaves them in a directory
        // iOS clears "at some point" is an app holding somebody's gigabyte.
        .sheet(isPresented: Binding(
            get: { if case .ready = saves?.state { return true } else { return false } },
            set: { if !$0 { saves?.taken() } }
        )) {
            if case .ready(let url, _) = saves?.state {
                FileShareSheet(url: url)
            }
        }
        // A failed save goes to the shell's toast, which outlives this sheet
        // — ``FilesView`` and ``VideoFeedView`` both do the same.
        .onChange(of: saves?.state) { _, state in
            if case .failed(let why) = state {
                onTrouble(why)
                saves?.taken()
            }
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

    /// A failed unsubscribe, said **in this sheet** rather than toasted.
    ///
    /// The unsubscribe is the one write on this screen that fails with a
    /// sheet in front of it — a stale card's 404 (`podcasts.py:265-269`) and
    /// the device refusal both land here — and the shell draws a toast
    /// behind a presented sheet (`AppShellView.swift:31-46`), so a toast
    /// would be said where nobody can read it. The same shape
    /// ``PodcastSubscribeSheet`` uses for its own two failures, and for the
    /// same reason. Above the search field, so it is not pushed off by a
    /// long list.
    @ViewBuilder
    private var unsubscribeTrouble: some View {
        if let said = podcasts.unsubscribeError {
            Text(said)
                .font(.footnote)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
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
            // The short prompt and the full spoken label, for the reason
            // ``PodcastStrings/searchEpisodesPrompt`` carries.
            PodcastStrings.searchEpisodesPrompt,
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
                PodcastEpisodeRow(
                    episode: episode,
                    // Both answered by a model, never by the row: whether a
                    // play control is drawn is ``PodcastsModel/canPlay(_:)``
                    // (the house has the file **and** this phone has a
                    // player), and the save is offered for the same rows.
                    canPlay: podcasts.canPlay(episode),
                    onPlay: { podcasts.play(episode) },
                    onSave: saves == nil || !episode.canPlay ? nil : { save(episode) }
                )
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
    }

    /// Android's hint chip, under the show's name
    /// (`PodcastsScreen.kt:300-311`), and **nothing at all** when nobody has
    /// been picked. It is here as well as on the grid behind this sheet
    /// because this is where a resume position is actually set, and whose
    /// position it is matters here.
    ///
    /// Near the top and not at the foot, which is where this file's sentence
    /// about listening used to be until train S004: a `LazyVStack`'s foot is
    /// not built until it is scrolled to, so on a list of fifty-seven
    /// episodes it is nowhere.
    @ViewBuilder
    private var listeningAsChip: some View {
        if let listeners, listeners.showsChip {
            Text(listeners.chip)
                .font(.caption)
                .foregroundStyle(Color.brandInk)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 10)
                .frame(minHeight: 28)
                .background(.quaternary.opacity(0.3), in: Capsule())
        }
    }

    /// A save in flight, said on the sheet rather than in a toast: it is
    /// about to put a share sheet up, and a toast that had already faded
    /// would leave that unexplained. ``FilesView`` draws its own the same way
    /// and for the same reason.
    @ViewBuilder
    private var saving: some View {
        if let saves, case .fetching = saves.state {
            SaveToDeviceButton(
                state: saves.state,
                onTap: {},
                onCancel: { saves.cancel() }
            )
        }
    }

    /// Fetch this episode's audio as an attachment.
    ///
    /// `download=1`, which is what makes the server send a
    /// `Content-Disposition` — the same thing Android asks `DownloadManager`
    /// for (`VideosScreen.kt:222`). The name is built here rather than taken
    /// from the server: an episode row carries a title and a file extension
    /// and no filename at all.
    private func save(_ episode: PodcastEpisode) {
        guard let saves else { return }
        saves.save(
            path: PodcastPath.audio(episodeID: episode.id, download: true),
            named: FileSaveModel.safeName(for: PodcastEpisodes.fileName(episode))
        )
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

/// One episode: its name, a mono line saying how long it is, how many
/// chapters it has and — when the house has no file — what the download is
/// doing, and the two controls Android draws.
///
/// `PodcastsEpisodes.kt:216-265`'s `EpisodeRow`, with both of its controls:
/// `play` (`:183`, `:223`) and `save to this device` (`:258`).
///
/// The whole line comes from ``PodcastEpisode/metaLine``, which is where
/// `has_file` and `download_status` are read **together** — the one pair on
/// this screen that must agree.
///
/// ### The row holds nothing
///
/// It is a lazy row, so neither control owns a task: `onPlay` reaches
/// ``PodcastsModel``, which runs the position read on itself, and `onSave`
/// reaches the screen's one ``SaveToDeviceModel``. Both outlive the row, the
/// sheet and the screen — the standing rule that cost a real bug on this
/// track.
///
/// ### Dynamic Type
///
/// The controls sit beside the words at ordinary sizes and under them at
/// accessibility sizes, because at AX5 a glyph column and two lines of text
/// do not share a row. The play glyph's box is `@ScaledMetric`, never a flat
/// `frame(width:)`.
struct PodcastEpisodeRow: View {

    let episode: PodcastEpisode

    /// Whether this phone can play it — ``PodcastsModel/canPlay(_:)``, which
    /// is the house having the file **and** this phone having a player.
    let canPlay: Bool

    let onPlay: () -> Void

    /// Nil when there is nothing to save: no file, or no saver yet.
    let onSave: (() -> Void)?

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    @ScaledMetric(relativeTo: .title2) private var glyph: CGFloat = 30

    var body: some View {
        layout {
            words
            if !dynamicTypeSize.isAccessibilitySize { Spacer(minLength: 4) }
            controls
        }
        .padding(.vertical, 4)
    }

    private var layout: AnyLayout {
        dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 10))
    }

    private var words: some View {
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
        // One stop for the words, reading the name and then the line: two
        // elements would make a back catalogue of fifty-seven a hundred and
        // fourteen stops. The controls stay separate, because a combined
        // element swallows a button.
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var controls: some View {
        HStack(spacing: 4) {
            if canPlay {
                Button(action: onPlay) {
                    Image(systemName: "play.circle")
                        .font(.title2)
                        .frame(width: glyph)
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.brandInk)
                // The **name**, not the bare word: a list read as "play,
                // play, play" has no information in it.
                .accessibilityLabel(PlayerStrings.playNamed(episode.displayTitle))
            }
            if let onSave {
                Button(action: onSave) {
                    Image(systemName: "square.and.arrow.down")
                        .font(.body)
                        .frame(width: glyph)
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.fgMuted)
                .accessibilityLabel(TransferStrings.saveToDevice)
            }
        }
    }
}

/// What a saved episode is called on the phone.
///
/// Its own type rather than a method on ``PodcastEpisode``, because the name
/// is a *presentation* decision — the share sheet shows it — and the row has
/// no filename from the server to use: `podcast_episodes` stores a path the
/// phone never sees and the API answers a title and a `file_ext`
/// (`podcasts.py:311-317`).
enum PodcastEpisodes {

    /// `<title>.<ext>`, with `.mp3` when the server did not say.
    ///
    /// The extension matters more than it looks: iOS picks the app that opens
    /// a shared file by its type, and a podcast saved with no extension is
    /// handed to nothing. `mp3` is the fallback because it is what the
    /// poller writes when a feed's enclosure has no usable type
    /// (`podcast_feed_poller.py`).
    static func fileName(_ episode: PodcastEpisode) -> String {
        let ext = (episode.fileExt ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let suffix = ext.isEmpty ? "mp3" : ext.hasPrefix(".") ? String(ext.dropFirst()) : ext
        return "\(episode.displayTitle).\(suffix)"
    }
}
