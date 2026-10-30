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

    /// Whether this view is on screen, as `onAppear`/`onDisappear` report it.
    ///
    /// Not a nicety: Home is a `NavigationStack` root, so it is still in the
    /// hierarchy — and still receiving `onChange(of: scenePhase)` — under a
    /// pushed Settings. See the `onChange` below.
    @State private var visible = false

    @Environment(\.horizontalSizeClass) private var sizeClass

    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HomeHeaderView(state: headerState, onPair: openPairing)
                // **The `shown` test is here and not only inside the card.**
                // It was only inside, and a Simulator tree measured what that
                // costs: with nothing to say the card renders empty but is
                // still a child of this `VStack`, so it is still given one of
                // the 16-point gaps. Home's content box was 111 points tall
                // for 63 points of header — 16 top, 63, **16 for a child that
                // drew nothing**, 16 bottom.
                //
                // SwiftUI omits a nil child and its spacing; it does not omit
                // a child whose `body` evaluates to nothing. Android's rule is
                // explicit about the same thing — "a section with nothing to
                // say gets no slot at all, so it leaves no gap either"
                // (`HomeScreen.kt:490`) — so the test moves up to where the
                // slot is decided. The card keeps its own `if state.shown`:
                // it is the one that makes the card correct wherever it is
                // used, and this one is the one that makes the layout right.
                if let home {
                    let attention = home.attention(
                        shared: model.visibility.sharedScreen,
                        compact: sizeClass != .regular,
                        expanded: attentionExpanded
                    )
                    if attention.shown {
                        HomeAttentionView(
                            state: attention,
                            nowMs: home.nowMs,
                            onRow: open,
                            onMore: { attentionExpanded = true }
                        )
                    }
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
        .onAppear { visible = true }
        // `repeatOnLifecycle(STARTED)` on Android: the loops stop with the
        // screen rather than polling a screen nobody is looking at.
        .onDisappear {
            visible = false
            home?.stop()
        }
        // `LifecycleEventEffect(ON_RESUME)` (`HomeScreen.kt:211`), which the
        // fifteen-second focus gap then throws away if the screen was only
        // away for a moment.
        //
        // **Guarded on being visible**, because Home is the root of a
        // `NavigationStack` (`WorkspaceView.swift:34`): a pushed destination
        // fires Home's `onDisappear` while this view stays in the hierarchy
        // with its `onChange` live. Android cannot reach that at all — a
        // `LifecycleEventEffect` is scoped to the composable — so the port is
        // a view that does not call a model it has left. ``HomeModel`` guards
        // the same thing on its own side; two independent mechanisms, because
        // the one-sided version cost Home its socket for whole sessions.
        .onChange(of: scenePhase) { _, phase in
            if phase == .active, visible { home?.cameBackOnScreen() }
        }
        // Same guard, same reason: `LaunchedEffect(live, lifecycle)` does not
        // run for a composable that is gone. A socket change missed while Home
        // was away is picked up by ``HomeModel/start()``, which reads the
        // current value when the screen comes back.
        .onChange(of: model.connected) { _, live in
            if visible { home?.socketChanged(live: live) }
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
            // **No Home row can carry one, and the type is what says so:**
            // ``HealthCheck/row`` (`HealthChecks.swift`) hardcodes
            // ``healthTarget`` and takes no parameter, so an
            // ``AttentionRow`` has no way to carry anything else. That is a
            // stronger guarantee than a test, and this comment used to credit
            // the test instead — `HealthChecksTests`'
            // `rows.allSatisfy { $0.target == .settings("health") }`, which
            // runs over one fixture producing three rows rather than "every
            // check at once".
            //
            // So this branch is unreachable rather than unimplemented. The
            // `.dashboard` targets in `HealthChecks.swift` are the per-check
            // `fix` links, which are *Health's* rows and not Home's, and a
            // part-3 row that wanted one would have to change `row` — which is
            // where the owner's no-misleading-controls rule gets its say,
            // because there is no in-app dashboard opener to call: opening one
            // means a web view or Safari, and that is a decision nobody has
            // made.
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
