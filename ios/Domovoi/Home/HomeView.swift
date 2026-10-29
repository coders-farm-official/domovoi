import DomovoiKit
import SwiftUI

/// Home, as far as I1-D part 2 goes: **the header and the needs-attention
/// card**.
///
/// Android's `HomeScreen` is a `LazyColumn` of nine fixed `item` slots —
/// header, first run, app update, needs attention, timers, rooms, announce,
/// today, everything — and "a section with nothing to say gets no slot at
/// all" (`HomeScreen.kt:490`). This is the first slot and the fourth. The
/// other seven — first run, app update, timers, rooms, announce, today, the
/// everything grid — are **not written**, and `design-notes/ios/I1D2-REPORT.md`
/// lists them as part 3's rather than a comment here claiming somebody will.
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
/// All seven of Android's reads, through ``HomeModel``: the config, the
/// health, the rooms, the timers, the plugins, the pending acquisitions and
/// the calendar's week. Each is an ``ApiRead``, so each carries the five
/// behaviours of `rememberApi` — including that a failure keeps the last
/// value, which is why a header that has shown a name once never falls back
/// to `domovoi` on a hiccup.
///
/// **The reads stay seven.** `HomeModel`'s own comment says why: collapsing
/// them into one loading/loaded/failed value loses the property the whole
/// screen is built on, that the rooms read failing must not blank the
/// timers.
struct HomeView: View {

    let model: ShellModel

    @State private var home: HomeModel?

    /// `rememberSaveable` on Android: the `+N more` tap, which expands the
    /// attention list in place. Home's state dies when the route leaves, on
    /// both phones.
    @State private var attentionExpanded = false

    @Environment(\.horizontalSizeClass) private var sizeClass

    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HomeHeaderView(state: headerState, onPair: openPairing)
                if let home {
                    HomeAttentionView(
                        state: home.attention(
                            shared: model.visibility.sharedScreen,
                            compact: sizeClass != .regular,
                            expanded: attentionExpanded
                        ),
                        nowMs: home.nowMs,
                        onRow: open,
                        onMore: { attentionExpanded = true }
                    )
                }
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
            if home == nil {
                // `init(in:)`, which builds all seven reads against the
                // container's server-address registry, so a switch re-keys
                // every one of them without this view having to remember a
                // line.
                home = HomeModel(in: model.container)
            }
            // `start()` is the `.mount` trigger: the seven reads, the clock,
            // the poll fallback and the health tick. Which reads a trigger
            // fires is ``homeRefreshPlan(for:in:)``'s decision and not this
            // view's — the one place a trigger turns into reads.
            home?.start()
        }
        // `repeatOnLifecycle(STARTED)` on Android: the loops stop with the
        // screen rather than polling a screen nobody is looking at.
        .onDisappear { home?.stop() }
        // `LifecycleEventEffect(ON_RESUME)` (`HomeScreen.kt:211`), which the
        // fifteen-second focus gap then throws away if the screen was only
        // away for a moment.
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { home?.cameBackOnScreen() }
        }
        .onChange(of: model.connected) { _, live in
            home?.socketChanged(live: live)
        }
    }

    /// Where an attention row goes — Settings → Health for every row Home
    /// draws today (``healthTarget``), and **Home fixes nothing itself**: the
    /// per-check `fix` targets are Health's rows, not Home's.
    private func open(_ row: AttentionRow) {
        switch row.target {
        case .settings(let tab):
            model.openSettingsTab(tab)
        case .screen(let route):
            model.navigate(route)
        case .dashboard:
            // **No Home row carries one**, and this is not an assumption:
            // every ``AttentionRow`` targets ``healthTarget``, which
            // `HealthChecksTests` asserts for every check at once
            // (`rows.allSatisfy { $0.target == .settings("health") }`). The
            // `.dashboard` targets in `HealthChecks.swift` are the per-check
            // `fix` links, which are *Health's* rows and not Home's.
            //
            // So this branch is unreachable rather than unimplemented, and
            // the test is what keeps it that way: a part-3 row with a
            // dashboard target fails that assertion instead of shipping a tap
            // that silently does nothing, which is the owner's
            // no-misleading-controls rule. There is also no in-app dashboard
            // opener to call — opening one means a web view or Safari, and
            // that is a decision nobody has made.
            break
        }
    }

    private var headerState: HomeHeaderState {
        HomeHeaderState(
            name: HomeConfig.name(of: home?.config.data),
            // ``HomeModel/nowMs``, not `Date()` — one clock for the screen,
            // and the one the attention rows' times are counted against.
            date: homeDate(
                ms: home?.nowMs ?? Int64(Date().timeIntervalSince1970 * 1000),
                timeZone: .current
            ),
            // **Still empty, and `countsReady` still `true`.** The counts
            // themselves — "3 rooms · 2 timers" — are §3.1 and part 3's: the
            // status line's words are not ported and nothing here can
            // compose them. `countsReady` is Android's `sats.answered()`, so
            // `false` means "a read is pending" and draws the loading
            // skeleton; passing `false` with nothing coming (which part 1
            // shipped) drew that skeleton permanently. Part 3 passes
            // `home.rooms.answered` with real counts behind it.
            counts: [],
            countsReady: true,
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
