import DomovoiKit
import SwiftUI

/// Stations: the radio plugin's screen — search a catalogue, keep the ones
/// worth keeping, and see what the sampler has heard on them.
///
/// `android/.../ui/screens/stations/StationsScreen.kt` (685 lines) plus
/// `StationSearch.kt` (598), wrapped on Android in
/// `PluginScreenFrame(CAP_STATIONS)` (`ScreenRouter.kt:72`) — the band here
/// is ``StationsPluginBand``.
///
/// ### What is here
///
/// Two scopes of search: radio-browser's catalogue, and the FCC FM import
/// already in the house. The favourites, twenty at a time, loading as you
/// scroll. The ten most recent, which the server trims so there is nothing
/// more to ask for. A star that saves a directory hit and keeps the id the
/// save answers. A detail sheet per favourite with its five fields, its
/// sampling interval, its stream URL and the detections the fingerprinter
/// matched. And `forget`, behind a confirmation.
///
/// ### What is not, and why there is no control for it
///
/// **`import fcc fm`.** Android's header draws it (`StationsScreen.kt:191`).
/// `POST /fcc-import` carries no `@device_endpoint`, so it sits on the radio
/// module's **admin** default and the module's own docstring says so in as
/// many words — the import "stays on the admin default" (`web.py:38-40`).
/// This app has no admin session and cannot get one, so that button would
/// 401 from every phone, every time; Files' delete hit the same wall. The
/// screen says where the import is done instead
/// (``StationsStrings/importIsAnAdminJob``), once, where Android puts the
/// button ([[no-misleading-controls]]). Android's own button is worse than
/// useless: it decodes the answer as `{state, inserted, updated}` where the
/// route answers `{started, state, result, error}`, so its toast always reads
/// "fcc running: 0 new, 0 updated" about a job that has not begun (MAP §8.1,
/// reported, not fixed — `android/` is out of bounds).
///
/// **Nothing, on the playback front, any more.** ``StationsPlayback`` was
/// the seam and the branch passed ``StationsNoPlayback``, so no play
/// affordance was drawn at all. D46 (Kamron's answer 46: "the real play
/// buttons are added in this same work") filled it:
/// ``StationsPhonePlayback`` is the witness the route slot passes now, over
/// ``ShellModel/player``, and every affordance the screen already knew how to
/// draw appears — the rows that play, the play glyph, `play here`.
/// ``StationsModel/playbackNote`` goes quiet by itself, because it is
/// `playback.canPlay ? nil : …`, and the subtitle's
/// `· tap a station to play it` is true again.
///
/// The witness still answers **false on a phone with no device id**, which is
/// what ``ShellModel/player`` is nil for: a station needs no position slot,
/// but the player that plays it writes one for whatever else is in its queue,
/// and a second player built just for radio is a second thing heard at once.
/// So the whole screen goes back to drawing no play affordance in that state,
/// which is exactly what it was built to do.
///
/// **Pages.** Android's `prev` / `next` footers, its `page n / m` line, its
/// "nothing on this page" empty and the effect that steps back a page when
/// unfavouriting empties the last one are all gone, under the owner's
/// standing decision of 2026-10-11: both lists load as you scroll. The
/// favourites **count** stays, from `/badge`.
///
/// **A room.** Not an omission: nothing under Android's `screens/stations/`
/// or `player/` posts a station to a room — grepped, MAP §3.6 — so there is
/// no room control on this screen to port.
///
/// ### Why the rows are not inside their cards
///
/// A `LazyVStack`'s laziness reaches its **own direct children** only. The
/// favourites and the search results are therefore siblings of the cards
/// above them rather than content inside them: twenty rows nested in a card
/// would all be built the moment the card appeared, every `onAppear` would
/// fire at once, and the next page would be fetched before anybody scrolled.
/// Recent is inside its card because the server caps it at ten.
struct StationsView: View {

    let model: ShellModel

    /// The audio engine, or the witness that says there is none. Handed in by
    /// the route slot so that the merge which brings the player changes **one
    /// line** and nothing in this file.
    let playback: any StationsPlayback

    @State private var stations: StationsModel?

    /// `onAppear`/`onDisappear`, for ``HomeView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy.
    @State private var visible = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                StationsPluginBand(model: model)
                header
                if let stations {
                    StationsSearchCard(stations: stations)
                    results(stations)
                    recent(stations)
                    favouritesHeader(stations)
                    favourites(stations)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task {
            // Built here and not in `init`, for ``HomeView``'s reason: the
            // model needs the container and a `@State` initialiser runs
            // before the view has one.
            if stations == nil {
                stations = StationsModel(in: model.container, playback: playback)
            }
            stations?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            stations?.stop()
        }
        // **The toast is the shell's**, drawn over the whole app: a toast
        // about a star that failed must outlive a scroll, and does outlive
        // this screen.
        .onChange(of: stations?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            stations?.setToast(nil)
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            stations?.serverChanged()
        }
        // One sheet, keyed on the station — `.sheet(item:)` rebuilds on a
        // changed id and not otherwise, so opening a second favourite
        // replaces what the sheet shows instead of keeping the first one's.
        .sheet(item: openSheet) { key in
            if let stations, let detail = stations.detail, let row = stations.selectedStation {
                StationsDetailSheet(stations: stations, detail: detail, row: row)
                    // The id is in the identity of the sheet, not of its
                    // content; naming it here keeps the compiler from
                    // complaining about an unused binding and documents what
                    // the sheet is about.
                    .id(key.id)
            }
        }
    }

    // MARK: - what is open

    /// A sheet's identity: the station's id.
    struct StationsSheetKey: Identifiable {
        let id: Int
    }

    private var openSheet: Binding<StationsSheetKey?> {
        Binding(
            get: { stations.flatMap(\.selected).map(StationsSheetKey.init) },
            // A swipe-down closes the detail the same way the chevron does,
            // so the model stops the detections read rather than leaving it
            // registered for pushes about a station nobody is looking at.
            set: { next in
                guard next == nil else { return }
                stations?.closeDetail()
            }
        )
    }

    // MARK: - the header (`StationsScreen.kt:181`)

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(StationsStrings.title)
                .font(.title2)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            if let stations {
                Text(stations.subtitle)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if let note = stations.playbackNote {
                    Text(note)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            // Where Android's `import fcc fm` button is, saying where the
            // import happens instead of being a button that 401s.
            Text(StationsStrings.importIsAnAdminJob)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - the search results (`StationSearch.kt:394`)

    @ViewBuilder private func results(_ stations: StationsModel) -> some View {
        let list = stations.results.state
        ForEach(Array(list.items.enumerated()), id: \.element.rowKey) { index, row in
            StationsSearchResultRow(stations: stations, row: row)
                .onAppear {
                    // The guarded question, never a bare "the last row
                    // appeared" (``Paging/nearEnd(totalItems:lastVisible:threshold:)``).
                    if stations.shouldLoadMoreResults(lastVisible: index) {
                        stations.results.loadMore()
                    }
                }
            Divider()
        }
        footer(
            loading: list.loading && !list.items.isEmpty,
            error: list.error,
            canLoadMore: list.canLoadMore,
            retry: { stations.results.retry() },
            more: { stations.results.loadMore() }
        )
    }

    // MARK: - Recent (`StationsScreen.kt:259`)

    /// Ten rows at most, by the server's own trim (`web.py:72`), so there is
    /// no paging here by design and the whole strip can live inside its card.
    @ViewBuilder private func recent(_ stations: StationsModel) -> some View {
        HomeCard(title: StationsStrings.recent) {
            VStack(alignment: .leading, spacing: 6) {
                Text(StationsStrings.lastN(stations.recentRows.count))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if stations.recentRows.isEmpty, stations.recent.answered {
                    note(StationsStrings.nothingPlayed, StationsStrings.nothingPlayedSub)
                }
                ForEach(stations.recentRows, id: \.rowKey) { row in
                    StationsRecentRow(stations: stations, row: row)
                    Divider()
                }
            }
        }
    }

    // MARK: - Favorites (`StationsScreen.kt:372`)

    /// The card's heading and its count, as a header **row** rather than a
    /// card: the rows below it are the lazy list, and they cannot be inside
    /// the card that names them (see the type's doc comment).
    private func favouritesHeader(_ stations: StationsModel) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: "star.fill")
                .font(.footnote)
                .foregroundStyle(Color.brandInk)
                .accessibilityHidden(true)
            Text(StationsStrings.favorites)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            if let total = stations.favoritesTotal {
                Text("\(total)")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder private func favourites(_ stations: StationsModel) -> some View {
        let list = stations.favorites.state
        if !list.loaded, list.error == nil {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(StationsStrings.favorites)
        }
        ForEach(Array(list.items.enumerated()), id: \.element.rowKey) { index, row in
            StationsFavoriteRow(stations: stations, row: row)
                .onAppear {
                    if stations.shouldLoadMoreFavorites(lastVisible: index) {
                        stations.favorites.loadMore()
                    }
                }
            Divider()
        }
        if stations.favoritesAreEmpty {
            note(StationsStrings.noFavorites, StationsStrings.noFavoritesSub)
        }
        footer(
            loading: list.loading && !list.items.isEmpty,
            error: list.error,
            canLoadMore: list.canLoadMore,
            retry: { stations.favorites.retry() },
            more: { stations.favorites.loadMore() }
        )
    }

    // MARK: - the two things a list's foot can be

    /// `ErrorState` (`Kit.kt:282`) or `load more`, and never both.
    ///
    /// The link is drawn **as well as** the scroll trigger, not instead of
    /// it: the trigger cannot fire for a list somebody has stopped scrolling,
    /// and VoiceOver's reader does not scroll at all.
    @ViewBuilder private func footer(
        loading: Bool,
        error: String?,
        canLoadMore: Bool,
        retry: @escaping () -> Void,
        more: @escaping () -> Void
    ) -> some View {
        if loading {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        } else if let error {
            VStack(alignment: .leading, spacing: 8) {
                Text(StationsStrings.unreachable)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(StationsStrings.retry, action: retry)
            }
        } else if canLoadMore {
            HomeLink(StationsStrings.loadMore, action: more)
        }
    }

    private func note(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(detail)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 8)
    }
}

/// The app's one player, as this screen's seam.
///
/// `app.player.playItems(listOf(PlayItem.fromStation(...)))`
/// (`StationPlay.kt:71`) — one line on Android, because its player is on its
/// container. Here the owner is ``ShellModel/player`` (D49) and this is the
/// adapter, which is what the route slot's comment said the merge would be:
/// *"This one argument is the whole of what the merge that brings phone audio
/// changes here."*
///
/// A `struct` and not a class: ``StationsPlayback`` is `Sendable`, and a
/// main-actor-isolated class is `Sendable` — so holding the player in a value
/// is the shape that satisfies both without an `@unchecked`.
///
/// ``canPlay`` is false for a nil player, and the screen then draws no play
/// affordance anywhere. ``StationsModel/play(_:)`` checks it before it POSTs
/// `/play`, which matters: that route stamps the station into Recent, so a
/// silent button's one visible effect would be a history entry for something
/// nobody heard.
struct StationsPhonePlayback: StationsPlayback {

    let player: PlayerModel?

    var canPlay: Bool { player != nil }

    /// Hand the row to the player as a live stream.
    ///
    /// ``PlayerItem/fromStation(id:name:)`` builds it: the artist line is the
    /// literal `"live radio"` and `seekable` is **false**, so the model
    /// refuses a seek and the lock screen's scrubber is disabled — a live
    /// stream has no place to seek to. Its `src` is
    /// `/api/plugins/radio/stations/{id}/stream`, the same relay
    /// ``StationsRules/stream(_:)`` names, which resolves a **row id** rather
    /// than a URL so the server can refuse a house-local address and relay
    /// bytes the phone cannot reach itself.
    ///
    /// The row is always one the server has stamped: the model POSTs `/play`
    /// first and hands on the row that answered, which is how a directory hit
    /// that was never saved gets an id.
    @MainActor
    func play(station: StationsRow) {
        player?.play([PlayerItem.fromStation(id: Int64(station.id), name: station.name)])
    }
}
