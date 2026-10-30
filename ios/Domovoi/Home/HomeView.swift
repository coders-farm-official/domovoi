import DomovoiKit
import SwiftUI

/// Home: all eight of its sections, in Android's slot order.
///
/// Android's `HomeScreen` is a `LazyColumn` of nine fixed `item` slots —
/// header, first run, app update, needs attention, timers, rooms, announce,
/// today, everything — and "a section with nothing to say gets no slot at
/// all, so it leaves no gap either" (`HomeScreen.kt:490`). Eight of the nine
/// are here. The ninth, the app-update card, is **out of scope by the owner's
/// decision**: an iOS app updates through the App Store and the whole
/// mechanism (a non-Play install check, a server-hosted APK) has no
/// counterpart.
///
/// ### Everything a section could lose is held here
///
/// Four pieces of state and three in-flight writes live in this view rather
/// than in the sections that draw them, and they are here for **one** reason
/// said once:
///
/// Android hoists the announce draft and its send out of the section into the
/// screen, with the reason at `HomeScreen.kt:347` — "the section is a
/// `LazyColumn` item, and scrolling it out of view while a send is in flight
/// would cancel the request (and its toast) with it". **A `LazyVStack` row
/// holding a `Task` is the identical bug on iOS**: a task started inside a
/// lazy row dies when the row is recycled, so a person who tapped send and
/// scrolled down to the rooms would get no announcement and no toast saying
/// so. The column below is a plain `VStack`, which does not have the problem —
/// but it is one `LazyVStack` away from having it, §7.6 forbids pagination
/// here so it will never need to be lazy, and the hoist costs two bindings.
///
/// The same argument applies to the timer cancel and the room transports,
/// which Android keeps in the screen too, and to ``StopAllArm`` — see below,
/// where it is a decision rather than an accident.
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
/// to `domovoi` on a hiccup, and why the rooms section's failure branch is
/// only ever a read that has *never* loaded.
///
/// **The reads stay seven.** `HomeModel`'s own comment says why: collapsing
/// them into one loading/loaded/failed value loses the property the whole
/// screen is built on, that the rooms read failing must not blank the timers.
struct HomeView: View {

    let model: ShellModel

    @State private var home: HomeModel?

    /// The vanish-watcher, **one per server** — `remember(serverUrl) {
    /// TimerBook() }` (`HomeScreen.kt:231`).
    ///
    /// Here rather than in ``HomeModel`` because that is where Android keeps
    /// it and because ``TimerBook/timerView(of:serverNowMs:)`` is built to be
    /// called from a body: it observes the read and derives the view inside
    /// **one** call, which is what makes the frame that first renders a read a
    /// row has vanished from the frame that shows its `done ·` line. A model
    /// property would be the same call in a different place.
    ///
    /// It survives a server switch, which a composition does not, so
    /// ``TimerBook/forget()`` is called explicitly below — "a different server
    /// is a different house: nothing it never had can have fired".
    @State private var book = TimerBook()

    /// `rememberSaveable` on Android: the two `+N more` taps, which expand
    /// their lists in place rather than navigating. Home's state dies when the
    /// route leaves, on both phones, and neither ever collapses again.
    @State private var attentionExpanded = false
    @State private var timersExpanded = false

    /// The announce draft and its send — **hoisted, see the type's note.**
    @State private var announceDraft = ""
    @State private var announcing = false
    @State private var announceTask: Task<Void, Never>?

    /// `stop all`'s arm.
    ///
    /// ### Where this lives is a decision, and this is it
    ///
    /// **It lives here, above the rooms section, and it does not disarm
    /// because the section scrolled away.**
    ///
    /// Android's sits inside the button (`remember { StopAllArm() }`,
    /// `HomeSections.kt:646`), so scrolling the rooms section off a phone
    /// disarms it. Map §4 calls that harmless and notes it is the exact
    /// opposite of the announce field's deliberate hoisting — which is the
    /// giveaway: it is an accident of where a `remember` happened to be
    /// written, not an answer to a question anybody asked.
    ///
    /// Two taps that stop other people's music is a confirmation, and a
    /// confirmation that silently forgets the first tap is a control that
    /// misleads about its own state — the owner's standing rule. Worse here
    /// than in general, because the button can also vanish for a *second*
    /// reason: it is drawn only while two or more rooms are playing or a batch
    /// is in flight, so a room stopping on its own takes it away mid-decision.
    /// With the arm inside it, the next tap after any of that silently becomes
    /// a first tap again — the person asks twice and is answered once.
    ///
    /// So: hoisted, and **disarmed on a timer of its own** (``disarm``) rather
    /// than only by the four-second window inside
    /// ``StopAllArm/isArmed(nowMs:)``. The window alone would be correct about
    /// the *tap* and wrong about the *label*: the screen's clock ticks every
    /// thirty seconds when nothing is counting, so `stop 2 rooms?` could sit
    /// on screen for half a minute after it had stopped meaning anything.
    /// Android gets the redraw from `LaunchedEffect(arm)`; this is that.
    @State private var arm = StopAllArm()
    @State private var disarm: Task<Void, Never>?
    @State private var stoppingAll = false

    /// Rooms with a write in flight — Android's `busy` map
    /// (`HomeScreen.kt:415`). Every room of a `stop all` batch is marked for
    /// the whole batch.
    @State private var busyRooms: Set<String> = []

    /// Whether this view is on screen, as `onAppear`/`onDisappear` report it.
    ///
    /// Not a nicety: Home is a `NavigationStack` root, so it is still in the
    /// hierarchy — and still receiving `onChange(of: scenePhase)` — under a
    /// pushed Settings. See the `onChange` below.
    @State private var visible = false

    @Environment(\.horizontalSizeClass) private var sizeClass

    @Environment(\.scenePhase) private var scenePhase

    private var compact: Bool { ShellWidth(sizeClass) == .compact }

    private var shared: Bool { model.visibility.sharedScreen }

    var body: some View {
        ScrollView {
            // A plain `VStack` and not a `LazyVStack`. Home is nine slots at
            // most, every read is a whole answer, and the lazy version is the
            // announce bug waiting to happen (see the type's note).
            VStack(alignment: .leading, spacing: 16) {
                HomeHeaderView(state: headerState, onPair: openPairing)
                if let home {
                    sections(home)
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
                home = HomeModel(in: model.container)
            }
            home?.start()
        }
        .onAppear { visible = true }
        // `repeatOnLifecycle(STARTED)` on Android: the loops stop with the
        // screen rather than polling a screen nobody is looking at.
        .onDisappear {
            visible = false
            home?.stop()
            // **The announce send is deliberately NOT cancelled here**, and
            // that is a named divergence: Android's runs in the composable's
            // scope and dies with the screen. The toast it produces is the
            // shell's and outlives Home, the request is one a person asked
            // for in so many words, and cancelling it would mean a tap that
            // announced nothing and said nothing. The disarm timer *is*
            // cancelled — it exists only to redraw a button that has gone.
            disarm?.cancel()
            disarm = nil
            arm = StopAllArm()
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
        // **A different server is a different house.** Android re-creates the
        // book on `remember(serverUrl)`; a `@State` instance outlives that, so
        // the forget is explicit. Without it every timer of the previous house
        // is missing from the new house's first read, and every one of them
        // would be announced as having just gone off — on a screen whose rooms
        // and name belong to somebody else.
        .onChange(of: model.inputs.serverURL) { _, _ in
            book.forget()
            announceDraft = ""
            arm = StopAllArm()
            busyRooms = []
        }
        // ### The clock, set from what is drawn — and NOT from inside `body`
        //
        // ``HomeClock/tickPeriodMs(counting:)`` is 1 s while something is
        // counting and 30 s otherwise, and part 2 shipped with nothing setting
        // `counting`: it said so, and this is the batch that drew the first
        // countdown.
        //
        // It is an `.onChange` and not a line in the section builder because
        // ``HomeModel/counting`` is an `@Observable` property: writing it
        // while `body` is being computed is modifying state during a view
        // update, which is at best a runtime warning and at worst a redraw
        // that feeds itself. `.onChange` runs **after** the update, and
        // ``HomeModel/setCounting(_:)`` restarts the loop only when the value
        // actually changed, so the steady state is one comparison per redraw.
        //
        // `initial: true` is what makes a Home that opens with a timer
        // already running tick at one second from its first frame instead of
        // waiting for the value to change.
        .onChange(of: countingNow, initial: true) { _, counting in
            home?.setCounting(counting)
        }
    }

    /// Whether anything on the screen is counting — ``homeCounting(timers:rooms:)``.
    ///
    /// Derived from the same two values the sections draw, so it cannot
    /// disagree with them. It does observe the book, which is deliberate: the
    /// book is a plain class and not `@Observable`, so reading it here costs
    /// no invalidation and the observe-then-derive call is idempotent for a
    /// read it has already seen.
    private var countingNow: Bool {
        guard let home else { return false }
        return homeCounting(
            timers: book.timerView(of: home.timers.data, serverNowMs: home.serverNowMs),
            rooms: home.roomsForDisplay
        )
    }

    // MARK: - the slots, in Android's order

    @ViewBuilder
    private func sections(_ home: HomeModel) -> some View {
        // ``TimerBook/timerView(of:serverNowMs:)`` is the **only** public
        // entry, and it observes the read and derives the view inside one
        // call. That is what makes the frame which first renders a read a row
        // has vanished from the frame that shows its `done ·` line: an
        // `.onChange` that observed and a body that derived would be wrong by
        // exactly one tick, every time, and a test that called the two in the
        // right order by hand would not see it.
        let timers = book.timerView(of: home.timers.data, serverNowMs: home.serverNowMs)
        let rooms = home.roomsForDisplay
        let announceOnline = announceOnlineCount(
            onlineRooms: home.onlineRoomCount,
            coreDown: home.coreDown
        )

        if home.isFirstRun(active: timers.active) {
            // The manual's phrase is not read on iOS, so this is always the
            // fallback — ``HomeFirstRunView`` carries the reasoning.
            HomeFirstRunView(hint: HomeHint(phrase: nil)) {
                model.navigate(.manual)
            }
        }

        HomeAttentionView(
            state: home.attention(
                shared: shared,
                compact: compact,
                expanded: attentionExpanded
            ),
            nowMs: home.nowMs,
            onRow: open,
            onMore: { attentionExpanded = true }
        )

        HomeTimersView(
            view: timers,
            serverNowMs: home.serverNowMs,
            shared: shared,
            onlineRooms: Set(rooms.filter(\.online).map(\.roomID)),
            expanded: timersExpanded,
            onMore: { timersExpanded = true },
            onCancel: { cancel($0, in: home) },
            cancelling: { book.isCancelling($0) }
        )

        HomeRoomsView(
            rooms: rooms,
            answered: home.rooms.answered,
            hasData: home.rooms.data != nil,
            dbDown: home.dbDown,
            stale: home.coreDown,
            sinceReadSec: home.sinceRoomsReadSec,
            nowMs: home.nowMs,
            // On the **server's** clock, because a room's chip shows a timer.
            timerLeft: timerLeftByRoom(timers.active, nowMs: home.serverNowMs),
            arm: arm,
            stoppingAll: stoppingAll,
            onStopAll: { stopAll(in: home) },
            onTransport: { transport($0, verb: $1, in: home) },
            busy: busyRooms.contains,
            onOpenRooms: { model.navigate(.satellites) }
        )

        HomeAnnounceView(
            onlineCount: announceOnline,
            message: $announceDraft,
            sending: announcing,
            onSend: { announce(onlineCount: announceOnline) }
        )

        HomeTodayView(
            answered: home.calendar.answered,
            hasData: home.calendar.data != nil,
            days: todayDays(
                events: home.calendar.data ?? [],
                nowMs: home.nowMs,
                timeZone: .current,
                shared: shared,
                // `HOME_PHONE_ROWS = 3` on a phone, `HOME_TODAY_ROWS = 6`
                // otherwise, and the cap is applied **before** the grouping.
                limit: compact ? homePhoneRows : homeTodayRows
            ),
            emptyText: todayEmptyText(
                events: home.calendar.data ?? [],
                nowMs: home.nowMs,
                timeZone: .current,
                shared: shared
            ),
            onOpenCalendar: { model.navigate(.calendar) }
        )

        // **Phones only** (`HomeScreen.kt:497`): the sidebar already lists
        // every screen, so a wide pane would draw the same grid twice.
        if compact {
            HomeEverythingView(
                tiles: model.everything,
                onOpen: { model.navigate($0) },
                onEditTheBar: { model.openSettingsTab("app-layout") }
            )
        }
    }

    // MARK: - the header

    private var headerState: HomeHeaderState {
        var timers = TimerView()
        if let home {
            timers = book.timerView(of: home.timers.data, serverNowMs: home.serverNowMs)
        }
        return HomeHeaderState(
            name: HomeConfig.name(of: home?.config.data),
            // ``HomeModel/nowMs``, not `Date()` — one clock for the screen,
            // and the one the attention rows' times are counted against.
            date: homeDate(
                ms: home?.nowMs ?? Int64(Date().timeIntervalSince1970 * 1000),
                timeZone: .current
            ),
            // ``HomeModel/counts(active:)``, which keeps the rooms read's
            // optional: a failed read gives no room parts while the timer
            // parts still count, where an empty house gives the single part
            // `no rooms yet`. Flattening the two is map §11.2.
            counts: home?.counts(active: timers.active) ?? [],
            // `lineReady = sats.answered()`. **A skeleton is a promise**: it
            // is kept here by a read that is in flight, which is why part 1's
            // `false` on a screen with no rooms read drew one for ever. With
            // no model yet there is nothing pending either, so `true`.
            countsReady: home?.rooms.answered ?? true,
            live: model.connected,
            paired: model.isPaired
        )
    }

    // MARK: - the writes
    //
    // All four are device tier, and a refusal raises the pairing screen the
    // ordinary way: ``DomovoiClient/raw(method:path:…)`` sets `pairing` on a
    // device-token refusal and the shell watches it, so nothing here inspects
    // or re-raises pairing itself.

    /// `onAnnounce` (`HomeScreen.kt:354`).
    ///
    /// ``announceAttempt(message:onlineCount:sending:)`` decides: an empty
    /// message and a house with nothing online each get a toast of their own
    /// rather than a silent bail, and a send already in flight does nothing at
    /// all. The branch order is the package's — a person who taps send with an
    /// empty field is told to type something rather than told the house is
    /// empty.
    private func announce(onlineCount: Int) {
        switch announceAttempt(
            message: announceDraft,
            onlineCount: onlineCount,
            sending: announcing
        ) {
        case .toast(let text):
            model.showToast(text)
        case .nothing:
            break
        case .send(let message):
            announcing = true
            announceTask = Task { @MainActor in
                do {
                    let delivered = try await HomeApi.announceAll(
                        model.container.client,
                        message: message
                    )
                    // **The rooms that took it, not the 200**: "a 200 can
                    // still mean dead connections under the server's
                    // active-sessions map".
                    model.showToast(
                        announceToast(delivered: delivered, onlineCount: onlineCount)
                    )
                    // Cleared on success only, so a failed send does not
                    // throw away what somebody typed.
                    announceDraft = ""
                } catch {
                    // ``HomeApi/announceAll`` is `throws(DomovoiError)`, but
                    // Swift does not narrow a caught typed throw inside this
                    // closure, so `error` arrives as `any Error` and
                    // ``failureText`` will not take it. ``HomeApi/stopRooms``
                    // carries the same note for the same reason, from inside
                    // a task group.
                    //
                    // Mapped rather than ignored: a send that failed for a
                    // reason nobody anticipated still has to say something,
                    // because the alternative is a button that silently does
                    // nothing — which is what the announce field's own
                    // no-op toasts exist to avoid.
                    let failure = (error as? DomovoiError)
                        ?? .unreachable(error.localizedDescription)
                    model.showToast(failureText("broadcast", failure))
                }
                announcing = false
            }
        }
    }

    /// A timer's cross.
    ///
    /// ``TimerBook/beginCancel(_:)`` is **both** the double-tap guard and the
    /// optimistic mark, in one call, because two lines a caller can forget to
    /// write is how `that one already finished` comes to land on top of
    /// `cancelled pasta timer` and tell a person the opposite of what
    /// happened. If it answers false, nothing at all is sent.
    private func cancel(_ timer: HomeTimer, in home: HomeModel) {
        guard book.beginCancel(timer.id) else { return }
        Task { @MainActor in
            let outcome = await HomeApi.cancelTimer(
                model.container.client,
                timer: timer,
                shared: shared
            )
            // The optimistic mark survives **only** a confirmation, so a timer
            // that in fact fired can still show its `done ·` line.
            book.settleCancel(timer.id, outcome: outcome)
            model.showToast(outcome.toast)
            home.timers.refresh()
        }
    }

    /// `stop all`: the first tap arms, the second stops.
    ///
    /// ``StopAllArm/tap(nowMs:)`` is pure and says which tap this is. The
    /// disarm task is what makes the *label* honest — see ``arm``.
    private func stopAll(in home: HomeModel) {
        let rooms = stopAllCandidates(home.roomsForDisplay, stale: home.coreDown)
        let (next, fire) = arm.tap(nowMs: home.nowMs)
        arm = next
        disarm?.cancel()
        guard fire else {
            disarm = Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(homeStopAllArmMs))
                guard !Task.isCancelled else { return }
                arm = StopAllArm()
            }
            return
        }
        disarm = nil
        guard !rooms.isEmpty else { return }
        stoppingAll = true
        // Every room of the batch is busy for the whole batch, so no single
        // room's transport can be tapped underneath it.
        busyRooms.formUnion(rooms)
        Task { @MainActor in
            let outcome = await HomeApi.stopRooms(model.container.client, rooms: rooms)
            model.showToast(stopAllToast(total: rooms.count, outcome: outcome))
            busyRooms.subtract(rooms)
            stoppingAll = false
            home.rooms.refresh()
        }
    }

    /// pause / resume / stop, one room.
    ///
    /// **No toast on either outcome, which is Android's** (map §4: the result
    /// column is `finally → refetchRooms()` and nothing else). The re-read is
    /// the feedback: a pause that did not happen comes back still playing. It
    /// is the one write on this screen that says nothing when it fails, and it
    /// is in `I1D3B-REPORT.md` as a thing to ask about rather than quietly
    /// evened out on one platform.
    private func transport(_ room: HomeRoom, verb: String, in home: HomeModel) {
        guard !busyRooms.contains(room.roomID) else { return }
        busyRooms.insert(room.roomID)
        Task { @MainActor in
            // The verb on the wire is the API's: `resume` is a route of its
            // own and not `play` (`POST /api/music/resume/<room>`).
            try? await HomeApi.roomAction(
                model.container.client,
                room: room.roomID,
                verb: verb
            )
            busyRooms.remove(room.roomID)
            home.rooms.refresh()
        }
    }

    // MARK: - navigation

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
            // ``HealthCheck/row`` hardcodes ``healthTarget`` and takes no
            // parameter, so an ``AttentionRow`` has no way to carry anything
            // else. That is a stronger guarantee than a test. The
            // `.dashboard` targets in `HealthChecks.swift` are the per-check
            // `fix` links, which are *Health's* rows and not Home's, and a
            // row that wanted one would have to change `row` — which is where
            // the owner's no-misleading-controls rule gets its say, because
            // there is no in-app dashboard opener to call.
            break
        }
    }

    /// Android: `onPair = { navigate(Route.Settings) }` (`HomeScreen.kt:400`),
    /// which lands on the Settings **index** while the household token field
    /// is three levels in. Map §11.4 calls that a control that does not do
    /// what it says, which is the owner's standing rule, and
    /// `openSettingsTab("phones-and-browsers")` is right there. A deliberate
    /// divergence, recorded in `I1D1-REPORT.md`.
    private func openPairing() {
        model.openSettingsTab("phones-and-browsers")
    }
}
