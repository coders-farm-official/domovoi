import DomovoiKit
import SwiftUI

/// Home, as far as I1-D part 1 goes: **the header and nothing else**.
///
/// Android's `HomeScreen` is a `LazyColumn` of nine fixed `item` slots —
/// header, first run, app update, needs attention, timers, rooms, announce,
/// today, everything — and "a section with nothing to say gets no slot at
/// all" (`HomeScreen.kt:490`). This is the first slot. The other eight, the
/// health checks, the timer book and every string they need are **not
/// written**, and `design-notes/ios/I1D1-REPORT.md` lists them as what part 2
/// has to build rather than a comment here claiming somebody will.
///
/// ### No pull-to-refresh, no page fetch, no pager
///
/// The standing owner decision of 2026-10-11 is that every files-like page
/// loads as you scroll. **Home is not one of those pages** (map §7.6): a grep
/// of `ui/screens/home/` finds no `Pager`, no `rememberPager`, no
/// `LoadMoreNearEnd` and no `items(`, the reads are capped server-side at
/// whole answers rather than first pages, and the `+N more` links expand an
/// already-fetched list. A `.refreshable` or an `onAppear`-driven page fetch
/// here would reproduce Android's `nearEnd` bug for a list with no second
/// page. `Pagination.swift` stays unused by this batch.
///
/// ### What it reads
///
/// One read: `GET /api/config`, for the house's name. The other six of
/// Android's seven are part 2's. The read is an ``ApiRead``, so it carries
/// the five behaviours of `rememberApi` — including that a failure keeps the
/// last value, which is why a header that has shown a name once never falls
/// back to `domovoi` on a hiccup.
struct HomeView: View {

    let model: ShellModel

    @State private var config: ApiRead<HomeConfig>?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HomeHeaderView(state: headerState, onPair: openPairing)
                Spacer(minLength: 0)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task {
            // Built here rather than in `init`, because the read needs the
            // container and a `@State` initialiser runs before the view has
            // one it can rely on. `.task` is also cancelled when the route
            // leaves, which is "a route holds nothing": leaving Home and
            // coming back re-reads, exactly as `rememberApi`'s state dying
            // with the composable does.
            if config == nil {
                let container = model.container
                config = ApiRead(base: { container.store.serverURL }) {
                    try await HomeApi.config(container.client)
                }
            }
            config?.refresh()
        }
        .onDisappear { config?.stop() }
    }

    private var headerState: HomeHeaderState {
        HomeHeaderState(
            name: HomeConfig.name(of: config?.data),
            date: homeDate(ms: Int64(Date().timeIntervalSince1970 * 1000), timeZone: .current),
            // The counts come from the rooms and timer reads, which part 2
            // writes; `countsReady: false` is what Android draws while the
            // rooms read has not answered — the date with a skeleton beside
            // it — and it is the honest state of a screen with no rooms read
            // at all.
            counts: [],
            countsReady: false,
            live: model.connected,
            paired: model.isPaired
        )
    }

    /// Android: `onPair = { navigate(Route.Settings) }` (`HomeScreen.kt:400`),
    /// which lands on the Settings **index** — five groups and a search box —
    /// while the household token field is three levels in, at Rooms &
    /// devices → Phones & browsers → Household token. Map §11.4 calls that a
    /// control that does not do what it says, which is the owner's standing
    /// rule, and `openSettingsTab("phones-and-browsers")` is right there.
    ///
    /// This asks for that section by key. It is a deliberate divergence from
    /// Android, it is in `I1D1-REPORT.md` as one, and `settingsPlace(of:)`
    /// already resolves the key — the same mechanism the attention rows and
    /// `edit the bar` use on both phones.
    private func openPairing() {
        model.openSettingsTab("phones-and-browsers")
    }
}
