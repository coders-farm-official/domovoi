import DomovoiKit
import SwiftUI

/// Podcasts: the house's subscribed shows, and one show's episodes.
///
/// `android/.../ui/screens/podcasts/PodcastsScreen.kt`, with what this app
/// does not build **left out rather than stubbed**.
///
/// ### What is here
///
/// A grid of subscribed shows a page at a time, with the server's own search
/// across a show's title, its author *and* its feed URL; `poll now`, which
/// makes the house fetch every feed; `subscribe`, by RSS URL or by searching
/// the directory; and a show's episodes in a sheet, with their own server
/// search, their durations and chapter counts, `unsubscribe` behind a
/// destructive confirm, **`play` on every episode the house has the file
/// for**, `save to this device`, and the "listening as" chip. A pull re-reads
/// the list.
///
/// ### Playing, saving and the listener arrived in the 2026-11-03 batch
///
/// All three were absent, and each for a reason that has been answered:
///
/// * **`play`** needed an owner for the engine. There is one now —
///   ``ShellModel/player``, D49 / Kamron's answer 2 — and this screen hands
///   it to ``PodcastsModel/player``. Nil on a build with no audio engine —
///   not on a phone that has not joined a house (``ShellModel/player``,
///   corrected in review 2) — and nil still draws no control.
/// * **`save to this device`** needed a transfer that does not hold the file
///   in memory. ``SaveToDeviceModel`` is that (D47), and the route serves
///   `download=1` as an attachment.
/// * **The listener** needed somewhere to be stored. ``PlayerListenerStore``
///   is that (D11), the selector is in Settings → Phones & browsers, and this
///   screen draws Android's hint chip.
///
/// ### What is not, and why there is no control for it
///
/// **Playing to a room**, which neither app can: a room plays library track
/// ids through `POST /api/music/play-tracks` and an episode has none
/// (`CastPlan.kt:10-20, 70-77`), and no server route plays an episode in a
/// room at all — that exists only through voice, in the core's own MPD path.
/// Android's own subtitle says "cast to a room" and its screen has no such
/// control; ``PodcastStrings/subtitle`` drops the claim.
///
/// ### The grid is lazy and a card holds nothing
///
/// `LazyVGrid`, because this is a files-like page and the owner's standing
/// decision of 2026-10-11 is that those load as you scroll. A lazy card is
/// recycled, so **no card owns a task or a write**: the cover fetch belongs
/// to ``PodcastArtStore`` and every write to ``PodcastsModel``, both of
/// which outlive any card.
struct PodcastsView: View {

    let model: ShellModel

    @State private var podcasts: PodcastsModel?

    @State private var art: PodcastArtStore?

    /// The household, for the "listening as" chip. Android reads the same
    /// list on this screen (`PodcastsScreen.kt:308`).
    @State private var listeners: PlayerListenerModel?

    /// The one save in flight, held by the screen and not by a row: a lazy
    /// card is recycled while it scrolls and a `Task` on one is cancelled
    /// mid-file (the standing rule).
    @State private var saves: SaveToDeviceModel?

    /// `onAppear`/`onDisappear`, for ``HomeView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy.
    @State private var visible = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                header
                listeningAsChip
                searchField
                if let podcasts, let art {
                    grid(podcasts, art)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // A pull **re-reads the list and does not poll the feeds**, which is
        // Android's own split and its own reasoning
        // (`PodcastsScreen.kt:104-105`): a pull must not send the house to
        // the internet behind the person's back. `poll now` is the only thing
        // that does.
        //
        // The model is captured rather than reached for through `@State`:
        // this action is a `@Sendable` closure, and a `@MainActor` class is
        // `Sendable` where a `View` struct is not.
        .refreshable { [podcasts] in
            await podcasts?.pulled()
        }
        .task {
            // Built here and not in `init`, for ``HomeView``'s reason: the
            // model needs the container and a `@State` initialiser runs
            // before the view has one.
            if podcasts == nil {
                podcasts = PodcastsModel(in: model.container)
            }
            if art == nil {
                art = PodcastArtStore(in: model.container)
            }
            if listeners == nil {
                listeners = PlayerListenerModel(in: model.container, listeners: model.listeners)
            }
            if saves == nil {
                saves = SaveToDeviceModel(in: model.container)
            }
            // **The player, asked for on every appearance.** Reading
            // `model.player` is what builds it — the app's one player is
            // built on first use and never at launch, because a `.playback`
            // session activated at launch stops whatever the person was
            // already listening to. So it is read here, in a `.task`, and
            // never in a `body`.
            podcasts?.player = model.player
            podcasts?.start()
            listeners?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            podcasts?.stop()
            listeners?.stop()
        }
        // **The toast is the shell's**, drawn over the whole app, because a
        // toast about a poll that failed must outlive a scroll and does
        // outlive this screen. The two failures that happen *inside* the
        // subscribe sheet are not toasts at all — a toast is drawn behind a
        // sheet, where nobody can read it (``PodcastsModel/subscribeError``).
        .onChange(of: podcasts?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            podcasts?.setToast(nil)
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            podcasts?.serverChanged()
            art?.serverChanged()
            saves?.serverChanged()
            // The player survives the move — ``ShellModel/refresh()`` has
            // told it, which is what drops the old house's episode out of its
            // queue — but it is re-read here so that no model keeps a slot
            // read on an earlier visit. (Review 2: the case this comment used
            // to name, a phone introducing itself, does not exist — the
            // device id is minted locally on first launch.)
            podcasts?.player = model.player
        }
        // **One `.sheet` over an enum**, which is ``FilesView``'s shape and
        // not a tidiness preference: several `.sheet` modifiers on one view
        // do not reliably coexist in SwiftUI — the last one attached wins and
        // the others silently never present.
        .sheet(item: openSheet) { which in
            if let podcasts, let art {
                switch which {
                case .episodes(let show):
                    PodcastEpisodesSheet(
                        show: show,
                        podcasts: podcasts,
                        art: art,
                        listeners: listeners,
                        saves: saves,
                        onTrouble: { model.showToast($0) }
                    )
                case .subscribe:
                    PodcastSubscribeSheet(podcasts: podcasts, art: art)
                }
            }
        }
    }

    /// Android's hint chip, under the header
    /// (`PodcastsScreen.kt:300-311`): `listening as <name>`, and **nothing at
    /// all** when no listener has been picked. The selector it hints at is in
    /// Settings → Phones & browsers, which is also where Android keeps it.
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

    // MARK: - what is open

    /// The one sheet that is up, from the two pieces of model state that can
    /// ask for one.
    ///
    /// The episodes sheet comes first because it is the one that can be open
    /// while the other is asked for: a subscribe is reached from the header,
    /// which is behind the episodes sheet, but ``PodcastsModel/serverChanged()``
    /// clears both and the order only ever settles that race.
    private var current: PodcastSheet? {
        guard let podcasts else { return nil }
        if let show = podcasts.selected { return .episodes(show) }
        if podcasts.subscribeSheetOpen { return .subscribe }
        return nil
    }

    private var openSheet: Binding<PodcastSheet?> {
        Binding(
            get: { current },
            // Closing is **per case**, read from what is actually open rather
            // than clearing both: a swipe-down that reset everything would
            // throw away a subscribe still in flight behind the sheet, which
            // is the household's write and not this sheet's
            // (`PodcastsSubscribe.kt:90`).
            set: { next in
                guard next == nil, let open = current else { return }
                switch open {
                case .episodes: podcasts?.closeEpisodes()
                case .subscribe: podcasts?.closeSubscribe()
                }
            }
        )
    }

    // MARK: - the header

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(PodcastStrings.title)
                .font(.title2)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Text(PodcastStrings.subtitle)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if let podcasts {
                actions(podcasts)
            }
        }
    }

    /// `poll now` and `subscribe`, which are Android's two header actions
    /// (`PodcastsScreen.kt:133-136`) — under the title rather than beside it,
    /// because two buttons and a wrapping title do not share a phone's width
    /// at an accessibility type size.
    @ViewBuilder
    private func actions(_ podcasts: PodcastsModel) -> some View {
        let stacked = dynamicTypeSize.isAccessibilitySize
        let layout = stacked
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(spacing: 10))
        layout {
            Button {
                podcasts.poll()
            } label: {
                // The label **is** the state: `polling…` while the house is
                // fetching every feed (`PodcastsScreen.kt:134`), so a button
                // that will not act again says why on its own face.
                Text(podcasts.polling ? PodcastStrings.polling : PodcastStrings.pollNow)
                    .frame(minHeight: 44)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .buttonStyle(.bordered)
            // **The tint, because a bordered button's label is the accent
            // colour** and this app's accent is ``Color/brand`` — amber, which
            // its own doc comment calls "a fill, never text" at 2.05:1 on
            // white. Apple's audit reported "Contrast is not high enough for
            // element unless font size is larger" against this button on train
            // S004; Stations got the same finding on six untinted chips in the
            // same run. ``Color/brandInk`` is 5.65:1, and it is what every
            // other `.bordered` button in this app already names
            // (`HomeTimersView.swift:290`, `ConnectionDialogView.swift:445`).
            .tint(Color.brandInk)
            // **And the label colour, which the tint alone does not settle.**
            // A `.bordered` button draws a wash of its own tint behind the
            // label as well as in it, and the wash lifts the background: the
            // ink is 5.4:1 on white but about 4.4:1 on a 12-15% amber wash
            // over white, which is the band Apple reports as "not high enough
            // unless font size is larger" — and did report, against this
            // button, on train S007 after the tint above had already been
            // put right. ``Color/brandFg`` is near-black, so it is over 15:1
            // on the wash whatever iOS picks for its opacity, and it is what
            // this app already draws on amber.
            .foregroundStyle(Color.brandFg)
            .disabled(podcasts.polling)

            Button {
                podcasts.openSubscribe()
            } label: {
                Text(PodcastStrings.subscribe)
                    .frame(minHeight: 44)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .buttonStyle(.borderedProminent)
            // The other half of the same pattern: a prominent button draws the
            // tint as its **fill**, so this one takes ``Color/brand`` where
            // its neighbour takes the ink (`HomeAnnounceView.swift:158-163`).
            //
            // **The `foregroundStyle` is half of it, and it was missing.**
            // This comment used to say the fill had ``Color/brandFg`` over it
            // and nothing in the code made that true: SwiftUI draws a
            // prominent button's label white unless it is told otherwise, and
            // white on this amber is 2.05:1. Apple's audit failed it outright
            // on train S007 — a finding this file had been classifying as a
            // measurement artifact, against a comment that was arguing for
            // its own correctness.
            .tint(Color.brand)
            .foregroundStyle(Color.brandFg)
        }
    }

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    @ViewBuilder
    private var searchField: some View {
        if let podcasts {
            TextField(
                // **The short prompt, the full spoken label**, which
                // ``PodcastStrings/searchShowsPrompt`` carries the measurement
                // for: Apple's audit named this field for clipping at larger
                // type sizes with `search shows…` in it, and the control is
                // one fixed row that cannot grow to hold it.
                PodcastStrings.searchShowsPrompt,
                text: Binding(get: { podcasts.filter }, set: { podcasts.typed($0) })
            )
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            // Single-line, so a Return key labelled `search` tells the truth.
            // The rule against `submitLabel` is about a `TextField(axis:
            // .vertical)`, whose Return makes a newline (NIGHT-RULES §3);
            // this field has no axis.
            .submitLabel(.search)
            .accessibilityLabel(PodcastStrings.searchShows)
        }
    }

    // MARK: - the grid

    @ViewBuilder
    private func grid(_ podcasts: PodcastsModel, _ art: PodcastArtStore) -> some View {
        if let count = podcasts.countText {
            Text(count)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        // Three states, not two: a list that has not answered is not an
        // empty list (``PagedState/loaded``).
        if !podcasts.shows.state.loaded {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(PodcastStrings.title)
        }
        LazyVGrid(
            // Adaptive 220pt columns, as Android's `GridCells.Adaptive(220.dp)`:
            // one across a phone, more on a wide one, and no number anywhere
            // saying how many.
            columns: [GridItem(.adaptive(minimum: 220), spacing: 12)],
            alignment: .leading,
            spacing: 12
        ) {
            ForEach(Array(podcasts.shows.state.items.enumerated()), id: \.element.id) { index, show in
                PodcastShowCard(show: show, art: art) {
                    podcasts.select(show)
                }
                .onAppear {
                    // The guarded question, never a bare "the last card
                    // appeared" (``Paging/nearEnd(totalItems:lastVisible:threshold:)``).
                    if podcasts.shouldLoadMore(lastVisible: index) {
                        podcasts.shows.loadMore()
                    }
                }
            }
        }
        // Two lines, and which two says which emptiness it is: a search that
        // matched nothing names the needle back, a house with nothing
        // subscribed says what to do about it (``PodcastsModel/emptyNote``).
        if let note = podcasts.emptyNote {
            VStack(alignment: .leading, spacing: 4) {
                Text(note.title)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(note.detail)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                // Android puts a `subscribe` button on this state as well as
                // in the header (`PodcastsScreen.kt:155`), because a house
                // with no shows has nothing else on the page to reach for.
                if podcasts.needle.isEmpty {
                    HomeLink(PodcastStrings.subscribe) { podcasts.openSubscribe() }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 8)
        }
        footer(podcasts)
    }

    @ViewBuilder
    private func footer(_ podcasts: PodcastsModel) -> some View {
        let list = podcasts.shows.state
        if list.loading, !list.items.isEmpty {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        } else if let trouble = list.error {
            // The **server's own message**, where Android's `PagedFooter`
            // draws a fixed "couldn't load shows" (`Paged.kt:148`). Every
            // other list in this app shows the message, and it is the half
            // that says whether the house refused, timed out or is not there.
            VStack(alignment: .leading, spacing: 8) {
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(PodcastStrings.retry) { podcasts.shows.retry() }
            }
        } else if list.canLoadMore {
            // Drawn as well as the scroll trigger, not instead of it: the
            // trigger cannot fire for a grid the person has stopped
            // scrolling, and VoiceOver's reader does not scroll at all.
            HomeLink(PodcastStrings.loadMore) { podcasts.shows.loadMore() }
        }
    }
}

/// The one sheet this screen puts up, over the two things that can ask for
/// one.
///
/// One enum rather than two `.sheet` modifiers, which ``FilesView`` says why.
enum PodcastSheet: Identifiable {
    case episodes(PodcastShow)
    case subscribe

    /// The case **and** the show, so that opening a second show replaces the
    /// sheet's content rather than keeping the first one's: `.sheet(item:)`
    /// rebuilds on a changed id and not otherwise.
    var id: String {
        switch self {
        case .episodes(let show): return "episodes:\(show.id)"
        case .subscribe: return "subscribe"
        }
    }
}

/// One subscribed show: its cover, its name, its author, and how much of it
/// the house has.
///
/// `PodcastsScreen.kt:232-264`'s `SubscriptionCard`, field for field.
///
/// ### No filled background behind the muted text
///
/// Android's `DomovoiCard` fills the card with its `raised` colour. This one
/// is a **stroked** rectangle over the page's own canvas, because
/// ``Color/fgMuted`` was measured against that canvas and fails Apple's
/// contrast audit on `secondarySystemBackground` (NIGHT-RULES §3) — and both
/// of this card's lower lines are `fgMuted`. The cover square keeps the fill;
/// nothing muted is drawn on it.
struct PodcastShowCard: View {

    let show: PodcastShow
    let art: PodcastArtStore
    let onOpen: () -> Void

    /// 48 points, as Android's 48 dp — and `@ScaledMetric`, so the square
    /// grows with the type size rather than staying a fixed box beside text
    /// three times its height (NIGHT-RULES §3).
    @ScaledMetric private var side: CGFloat = 48

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        Button(action: onOpen) {
            layout {
                PodcastArtView(path: PodcastArt.path(for: show), store: art, side: side)
                VStack(alignment: .leading, spacing: 2) {
                    Text(show.displayTitle)
                        .font(.subheadline)
                        // **No line limit at all**, where this read
                        // `.lineLimit(2)`. The reasoning was already the right
                        // one — a show whose name is its feed URL
                        // (``PodcastShow/displayTitle``'s second fallback) is
                        // unrecognisable truncated, and `minimumScaleFactor`
                        // is rejected throughout this app — but two lines is
                        // still a truncation, and Apple's audit on train S004
                        // reported "Text of this element may be clipped at
                        // larger Dynamic Type sizes" against this very leaf
                        // holding a 43-character URL in 186 points. Videos hit
                        // the same finding from `lineLimit(1)` and answered it
                        // by adding a line (`VideosView.swift:326`); on a card
                        // whose whole name can be a URL the honest answer is
                        // to let it wrap as far as it needs. `fixedSize` is
                        // what then gives it the room.
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    if let author = show.author, !author.isEmpty {
                        Text(author)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            // The limit goes for the same reason, and this one
                            // was never worth having: an author is one short
                            // line everywhere the audit looked, so the cap
                            // only ever bit at the type sizes where it must
                            // not.
                            .fixedSize(horizontal: false, vertical: true)
                            .multilineTextAlignment(.leading)
                    }
                    Text(show.downloadedLine)
                        .font(.caption)
                        .monospaced()
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
            .overlay {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(Color(.separator), lineWidth: 1)
            }
        }
        .buttonStyle(.plain)
        // The whole card is the button, so what it reads out is the show and
        // not the word "open": VoiceOver goes through fifty of these.
        //
        // ``PodcastShow/spokenTitle`` and not `displayTitle`: the drawn name
        // of an untitled show is its feed URL, which Apple's audit failed on
        // train S004 as a label that is not human-readable — and spelling a
        // URL out is not a name. The URL stays in the card's text.
        .accessibilityLabel(show.spokenTitle)
        // The author **and** the counts: a labelled button is one element and
        // its children stop being read, so an author said only in a `Text`
        // would be said to nobody (``PodcastShow/spokenDetail``).
        .accessibilityValue(show.spokenDetail)
    }

    /// The cover beside the words, or above them once the words are big
    /// enough that a 48-point square and three wrapping lines cannot share a
    /// phone's width (NIGHT-RULES §3).
    private var layout: AnyLayout {
        dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 10))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 10))
    }
}
