import DomovoiKit
import SwiftUI

/// The manual: the house's own user guide, read off the server it describes.
///
/// `android/.../ui/screens/manual/ManualScreen.kt` (1024 lines, read at
/// `898b3af2`), reached at `ScreenRouter.kt:75`.
///
/// ### Where the words come from
///
/// **The server's, not the bundle's.** ``ManualStore`` fetches
/// `GET /manual-content.json` (`ManualRepository.kt:31`) from the connected
/// server and keeps one copy per server address under the caches directory.
/// The file is `web/static/manual-content.json`, built by
/// `scripts/manual/build_manual.py`, and the dashboard's `#manual` renders
/// the same document — the app and the dashboard are two readers of one
/// file. So nothing on this screen is written here: every heading, pill,
/// placeholder and hint is `doc.label(…)` or a block's own text, and the only
/// sentences this app owns are the ones a document that did not load cannot
/// supply (``ManualStrings``).
///
/// Four load states, each a different screen (`ManualRepository.Result`):
/// the manual; the manual **plus** a line saying it is the copy from last
/// time; "no manual on this server" with a way to the dashboard; and "the
/// manual didn't load" with `try again`.
///
/// ### What is not, and why there is no control for it
///
/// **Nothing on this screen writes.** Every route it touches is a GET, so
/// there is no control here that could 401 from a phone — the usual casualty
/// of the owner's rule that a control which cannot do what it says is not
/// drawn ([[no-misleading-controls]]). The two things left out are
/// decoration, and both are in
/// `design-notes/ios/overnight/manual/DECISIONS.md`:
///
/// * **The dashed wires do not follow the float.** The nodes bob (answer 31,
///   `ManualScreen.kt:559`) on one `repeatForever` animation each, which the
///   render server runs without a redraw; Android reads its float inside the
///   `Canvas` and redraws every wire with it, 60 times a second for as long
///   as the map is open. A wire runs centre to centre between two ~104-point
///   cards, so the part of it that would have moved is the part the cards
///   cover. ``ManualMapStage/wires(_:size:)`` has the measurement.
/// * **There is no 280 ms zoom between levels.** Android animates its camera
///   inside one composition (`MOVE_MS`, `:549`); here a level change swaps
///   the page under the bar. ``ManualMapStage/body`` says so, and the
///   question of whether to rebuild the zoom is in DECISIONS.
/// * **There is no zoom glide between levels**, and now that a level is a
///   pushed screen the move is the system's own push transition. Kamron's
///   entry 73 is the question of whether the glide is built for real.
///
/// A swipe back **goes up one level** below the accessibility sizes — answer
/// 32, and Android's own system back (`BackHandler(enabled = at != "start")
/// { up() }`, `ManualScreen.kt:315`). It is built without a second
/// `NavigationStack`: each level is a screen presented on the **shell's**
/// stack (``ManualLevelHost``, ``ManualLevelStack``), so the gesture that
/// pops it is the one every other screen of this app already has. At an
/// accessibility size the swipe leaves the manual instead, because the bar
/// and the page share one scroll view there — ``ready(_:cached:_:)`` has the
/// measurement, and it is Kamron's open entry 74.
///
/// The divergence is **said out loud on every level it is reachable from**
/// (``ManualStrings/swipeHint(isAccessibilitySize:)``): `zoom out` carries it
/// on a cluster and on a piece, and the `start here` pill carries it on the
/// map, which draws no `zoom out` (``ManualCrumbs``' `topic != nil`). That
/// last one is review round 2's F2 — the comment that stood here claimed
/// three levels and the code covered two.
///
/// **A `route:` link the app cannot open is plain text, never a link.** That
/// is ``ManualModel/linkIsLive(kind:target:)`` — ported from
/// `manualRouteOpens` (`ManualContent.kt:389`) — and it is the same rule: a
/// link that lands back on Home is a control that lies.
///
/// ### One Android defect is read correctly here rather than copied
///
/// Android's rooms slot can never say what a room is playing:
/// `ManualNowPlaying` (`ManualContent.kt:239`) decodes `now_playing.title`
/// while the server carries the track under `now_playing.song`
/// (`web/backend/schemas.py:236`). ``ManualRoom/song`` reads the real shape.
/// Its room rows also tap through to `settings/satellites`, which the
/// document's route table does not contain — so every row in the shipping app
/// is a button that does nothing. Here they open `settings/rooms`, which the
/// table does have. Both are reported, not fixed there.
struct ManualView: View {

    let model: ShellModel

    @State private var manual: ManualModel?

    /// This screen's hold on ``ManualModel``: it starts the reads and its
    /// `deinit` stops them.
    ///
    /// **`@State`, so it is this view's identity and not this view's
    /// visibility** — ``ManualScreenHold``'s whole doc comment, and review
    /// round 1 of answer 32's must. A level is a pushed screen now, so the
    /// `onDisappear { manual?.stop() }` that used to sit below fired when the
    /// map went up and stopped the very reads the pages above it draw. A
    /// covered screen keeps its `@State` and a popped one does not, so the
    /// object in that storage is the one thing in SwiftUI that can tell a
    /// cover from a destruction. `hold == nil` is also the guard that makes a
    /// repeated `onAppear` do nothing.
    @State private var hold: ManualScreenHold?

    /// `at` in a `rememberSaveable` (`ManualScreen.kt:290`) — the same
    /// promise `@SceneStorage` makes: it survives a process death and dies
    /// with the app's removal. The string is ``ManualPlace/saved``.
    @SceneStorage("manual.place") private var savedPlace = "start"

    /// Read here as well as in ``ManualTopBar``: who owns the scroll view
    /// changes with it (see ``ready(_:cached:_:)``).
    @Environment(\.dynamicTypeSize) private var typeSize

    /// **The system's, not the override installed below.** `.environment`
    /// applies to a view's descendants, so the action this screen hands its
    /// pages is not the one this screen itself holds — which is what keeps
    /// `act(.web(…))` from re-entering the handler that called it.
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(spacing: 0) {
            if let manual {
                if let load = manual.load, case .ready(let doc, let cached) = load {
                    ready(doc, cached: cached, manual)
                } else {
                    ManualTopBar(manual: manual)
                    page(manual)
                }
            } else {
                loading
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        // Built here and not in `init`, for ``FilesView``'s reason: the model
        // needs the container and a `@State` initialiser runs before the view
        // has one. The visibility closure is read **each time a link is
        // judged**, not captured once, so a capability that arrives after
        // this screen opened is taken into account.
        //
        // **The start and the stop are ``ManualScreenHold``'s, not this
        // callback's**, which is review round 1's must on answer 32. The
        // pair that stood here — `.task { … start() }` and
        // `onDisappear { stop() }` — is not symmetric and its second half is
        // not even about the right event: `.task` runs once per view
        // identity, which a pushed level does not end, while `onDisappear`
        // arrives for a **cover** as well as for a pop. So opening the map
        // stopped the document load and all six live reads, and nothing
        // restarted them — on a screen whose piece pages, two levels up,
        // are what draw them. ``ManualScreenHold`` has the measurement and
        // the dozen sibling files that made it.
        //
        // `onAppear` and not `.task`, for ``RoomsView``'s reason (its D56):
        // the hold is built where a repeated appearance can be guarded
        // against by what it finds, and `hold == nil` is that guard.
        .onAppear {
            if manual == nil {
                manual = ManualModel(in: model.container, visibility: { [model] in model.visibility })
                manual?.restore(saved: savedPlace)
            }
            guard hold == nil, let manual else { return }
            hold = ManualScreenHold(manual)
        }
        .onChange(of: manual?.place) { _, place in
            guard let place else { return }
            savedPlace = place.saved
        }
        // **The levels come off the stack when the manual stops being the
        // route on top of it.** The gate itself is declarative and lives in
        // ``ManualLevelHost/deeperIsShown`` (``isTopRoute``); this is the
        // other half, the model's own place, so that nothing is left asking
        // to be pushed again the moment the manual is uncovered.
        //
        // Review round 1's finding 3: a level is presented on the **shell's**
        // stack, and four other things append to that same stack from
        // outside this screen — the phone bar, which is drawn outside the
        // stack and so is tappable from a pushed level
        // (`ShellChromeViews.swift:277`), a notification tap
        // (``NavigationState/takeRouteFromOutside(_:)``), a capability that
        // went away (``NavigationState/capabilitiesChanged(_:)``, which
        // empties the path), and the manual's own `route:` links. The first
        // build of this answer guarded only the last of those, inside
        // ``act(_:in:)``; a route held underneath presentations that hang off
        // the screen below it is a shape nobody here has measured, and the
        // phone bar is the most ordinary way out of this screen there is. So
        // the guard is at the seam instead, where it covers all four.
        //
        // The cost, unchanged and still Kamron's call (DECISIONS, D32B):
        // leaving the manual while deep lands on Start here on the way back,
        // where Android comes back to the piece. It is the gate alone that
        // would be Android-faithful — the levels would re-push on the way
        // back — and re-pushing three screens onto a stack that is popping
        // another route off it is a shape nothing here can measure either.
        // Reading `.manual` and not `canGoBack`: the manual is never the
        // stack's root (`startRoute` is `.home`), so it is the route on top
        // exactly while nothing is above it.
        .onChange(of: model.navigation.route) { _, route in
            guard route != .manual, let manual else { return }
            zoomOutOfTheManual(manual)
        }
        // Every `[text](route:…)`, `(node:…)` and `(doc:…)` in the document
        // arrives here: a span's link is a URL this file minted
        // (``manualLinkURL(kind:target:)``), and `Text` routes a tapped link
        // through `openURL`. Anything that is not one of ours — the piece
        // page's `More detail` button, which opens GitHub — falls through to
        // the system, which is what `.systemAction` means.
        .environment(\.openURL, OpenURLAction { url in
            guard let link = manualLink(of: url), let manual else { return .systemAction }
            follow(link, in: manual)
            return .handled
        })
    }

    // MARK: - the four load states

    /// `result == null -> LoadingState()` (`ManualScreen.kt:197`).
    private var loading: some View {
        ProgressView()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .accessibilityLabel(ManualStrings.Fallback.eyebrow)
    }

    /// The manual itself: the top bar, the `cached` line, and the level.
    ///
    /// **Who scrolls changes with the type size.** Below the accessibility
    /// sizes the top bar is pinned and each level scrolls under it, which is
    /// Android's shape (`ManualScreen.kt:349`: `ManualTop` above
    /// `ManualBody`). At an accessibility size the bar is two stacked pills,
    /// the crumbs and a growing search field — 310 of the 400 points this
    /// screen is given on a 6.1" phone, measured in train S006, which left
    /// the manual itself a 90-point slot to be read through. So the bar and
    /// the page share **one** scroll view there and the bar scrolls away.
    /// Nothing is lost: the bar's controls are the first thing in the scroll,
    /// a flick from anywhere.
    @ViewBuilder private func ready(
        _ doc: ManualDoc,
        cached: Bool,
        _ manual: ManualModel
    ) -> some View {
        if typeSize.isAccessibilitySize {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ManualTopBar(manual: manual)
                    if cached { ManualCachedLine() }
                    // The level draws **without** a scroll view of its own:
                    // two nested vertical scrolls would fight for the same
                    // drag, and the inner one would be the 90-point slot
                    // this branch exists to get rid of.
                    //
                    // And no pushed screens here, which is why the swipe
                    // means something different at these sizes (answer 32 is
                    // built in the branch below; Kamron's entry 74 is the
                    // question of whether that is alright). A level pushed
                    // on the shell's stack brings its own bar, and a bar
                    // that is 310 of 400 points cannot be the price of
                    // every level change: the whole reason this branch
                    // exists is that the bar has to be able to scroll away,
                    // and a pushed screen's bar scrolls with *its* page,
                    // not with the one underneath it. `zoom out` and the
                    // crumbs are the way up here, and they say so out loud
                    // (``ManualCrumbs``).
                    ManualLevelView(
                        doc: doc,
                        manual: manual,
                        place: manual.place,
                        scrolls: false
                    )
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            // **A level is a screen on the shell's own stack — and there is
            // no second `NavigationStack` here.** Kamron's answer 32 ("swipe
            // back goes up a level"), built the way train I003 proved it
            // cannot be built and then built again.
            //
            // What I003 measured, off the trees, the screenshots and the
            // screen recordings in `overnight-trains/I003/ALL/att/testManual_*`:
            // `90385cce` mounted a `NavigationStack` of the manual's own in
            // this branch, with ``ManualModel/levels`` as its path, and every
            // one of the twenty cases that reach a loaded document failed.
            // The route pushed, this screen drew its top bar while the
            // document was loading (`"start here"` is in the tree at
            // t≈15.4 s, one second after the seeded push), and the moment the
            // document landed and the inner stack mounted, the **outer** push
            // unwound: the shell's breadcrumb still read `domovoi / manual`
            // (``NavigationState/route`` is `path.last`, so `ShellModel.path`
            // was still `[.manual]`) over a tree that held nothing but Home.
            // The twentieth case got the other outcome of the same shape —
            // the app stopped reporting idle and every query after that timed
            // out. `6d071463` took the stack out, and the screen lost the
            // answer with it.
            //
            // **This is the shape that does not bring a stack.** Each level
            // is a screen presented on the stack that is already there, the
            // shell's `NavigationStack(path: $model.path)`
            // (`WorkspaceView.swift:153`), through
            // `navigationDestination(isPresented:)` — the modifier form of
            // the one push a screen inside that stack *can* do. It needs no
            // path element, which is what `ShellModel.path` being a
            // homogeneous `[Route]` forbids: I003 also measured that side of
            // it on the Rooms unit, where a `navigationDestination(for:
            // RoomsOpenRoom.self)` drew, highlighted, took the tap and did
            // nothing, because no value of that type can go on an array of
            // `Route` (`RoomsView.swift:70-105`). So no second stack to
            // unwind, no path element to append, and the gesture that pops a
            // level is the route's own `interactivePopGestureRecognizer` —
            // the one every other screen of this app already relies on.
            //
            // ``ManualLevelHost`` is that screen, ``ManualLevelStack`` the
            // arithmetic it reads, and ``ManualModel/pop(to:)`` what a
            // dismissal writes back. The host at depth 0 is this one: the
            // route's own screen, drawing Start here, with the map above it,
            // a cluster above that and a piece's page above that.
            //
            // **If the push never lands, this screen is the one `6d071463`
            // shipped.** A host whose deeper screen has not drawn 700 ms
            // after the chain asked for it draws the deepest level itself
            // (``ManualLevelHost/shown``), which is the level swapped in
            // place — so the worst case of being wrong about SwiftUI here is
            // the behaviour that passed every case before this commit, with
            // `zoom out` and the crumbs as the way up, and not twenty reds.
            // That guard has a measurement behind it and not an argument:
            // nothing in this repository can run a `NavigationStack` outside
            // a Simulator, so the first real check of the push is the next
            // train.
            ManualLevelHost(
                doc: doc,
                manual: manual,
                cached: cached,
                depth: 0,
                isTopRoute: isTopRoute
            )
        }
    }

    /// Whether the manual is the route on **top** of the shell's stack.
    ///
    /// Read in `body` so that a change to it rebuilds this screen, which is
    /// what makes ``ManualLevelHost/deeperIsShown`` a gate rather than a
    /// reaction: the push of another route and the dismissal of the manual's
    /// levels then resolve in **one** update. The `onChange` beside it is the
    /// model's half of the same seam; both halves are review round 1's
    /// finding 3 and the comment there has the four callers it covers.
    ///
    /// `startRoute` is `.home` (`Route.swift:95`), so the manual is always a
    /// pushed route and `path.last == .manual` is exactly "nothing is above
    /// me".
    private var isTopRoute: Bool { model.navigation.route == .manual }

    @ViewBuilder private func page(_ manual: ManualModel) -> some View {
        if let load = manual.load {
            switch load {
            case .ready(let doc, let cached):
                // Not reached: ``body`` sends `.ready` to ``ready(_:cached:_:)``.
                // Kept so this function still answers for every load state.
                ready(doc, cached: cached, manual)
            case .noManual:
                noManual(manual)
            case .failed:
                failed(manual)
            }
        } else {
            loading
        }
    }

    /// A server older than the manual's file (`NoManual`, `:209`).
    ///
    /// The dashboard button is drawn only when there **is** a server to open:
    /// ``ManualModel/dashboardManualURL`` is nil otherwise, and a button that
    /// cannot do what it says is not drawn.
    private func noManual(_ manual: ManualModel) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ManualEmptyState(
                title: ManualStrings.noManualTitle,
                detail: ManualStrings.noManualDetail
            )
            if let url = manual.dashboardManualURL, let target = URL(string: url) {
                Button(ManualStrings.openTheDashboard) { openURL(target) }
                    .buttonStyle(.borderedProminent)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Unreachable, and no copy kept for this server (`:198`).
    private func failed(_ manual: ManualModel) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ManualEmptyState(
                title: ManualStrings.didNotLoadTitle,
                detail: ManualStrings.didNotLoadDetail
            )
            Button(ManualStrings.tryAgain) {
                manual.reload()
                manual.refreshLive()
            }
            .fixedSize(horizontal: false, vertical: true)
            .frame(minHeight: 44)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - what a link does

    /// `ManualLinks` (`ManualScreen.kt:317`), with the decisions in the model
    /// and only the acting here.
    private func follow(_ link: ManualLinkTarget, in manual: ManualModel) {
        switch link.kind {
        case "node":
            manual.go(link.target)
        case "doc":
            act(manual.openDoc(link.target), in: manual)
        default:
            act(manual.openRoute(link.target), in: manual)
        }
    }

    private func act(_ open: ManualOpen, in manual: ManualModel) {
        switch open {
        case .screen(let route):
            // **The manual's levels come off the stack first, in the same
            // update.** A level is a pushed screen now
            // (``ManualLevelHost``), and this appends a `Route` to the
            // *same* `NavigationStack` those screens are presented on
            // (``ShellModel/navigate(_:)`` → `path.append`). Leaving them
            // there would ask SwiftUI to hold a route pushed underneath
            // presentations that hang off the screen below it — a shape
            // nobody here has measured, whose worst case is a link that
            // visibly does nothing, which is the one thing Kamron's
            // no-misleading-controls rule will not have.
            //
            // **This is not the guard, it is the early one.** The guard is at
            // the seam: the levels are presented only while the manual is the
            // route on top (``ManualLevelHost/deeperIsShown``), and `body`'s
            // `onChange` on the open route collapses the model to match.
            // That covers the phone bar, a notification tap and a capability
            // reset, which this line never could — review round 1's finding
            // 3. Kept all the same, because for the two links this screen
            // owns both writes land in **one** event, so SwiftUI resolves one
            // new stack (`[.manual, <route>]`, no presentations) rather than
            // a push whose presentations come down a step later.
            //
            // The cost is that coming back lands on Start here where
            // Android comes back to the piece: `@SceneStorage` mirrors
            // ``ManualModel/place``, and this moves it. It is in this
            // item's DECISIONS as Kamron's call (D32B), and it is now the
            // cost of leaving the manual while deep by **any** route, not
            // only by a link.
            zoomOutOfTheManual(manual)
            model.navigate(route)
        case .settingsTab(let tab):
            zoomOutOfTheManual(manual)
            model.openSettingsTab(tab)
        case .web(let url):
            guard let target = URL(string: url) else { return }
            openURL(target)
        case .nothing:
            // Kotlin's `r == null -> Unit` (`:324`): a route the document
            // names and does not define goes nowhere, on both platforms.
            break
        }
    }

    /// Put the manual back on Start here, so that nothing of it is left
    /// pushed on the shell's stack when another route goes on top.
    ///
    /// Two callers: the two links that push a route — `.web(…)` opens Safari
    /// and `.nothing` does nothing, so neither touches the stack — and
    /// `body`'s `onChange` on the open route, which is the same thing noticed
    /// at the seam for every other way out of this screen.
    ///
    /// The `levels.isEmpty` guard is what keeps it from reaching the ordinary
    /// exit: a swipe or a back tap from **Start here** leaves the manual with
    /// nothing pushed, and the place a previous visit saved must survive
    /// that. It only ever moves a manual that was left while deep.
    private func zoomOutOfTheManual(_ manual: ManualModel) {
        guard !manual.levels.isEmpty else { return }
        manual.go("start")
    }

}

/// `EmptyState` (`ui/components`), as this screen uses it: a title and a
/// sentence under it, both wrapping.
struct ManualEmptyState: View {

    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Text(detail)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - one level, and the stack of them

/// The line above a level when the document is the copy from last time
/// (`ManualScreen.kt:337`).
///
/// A view of its own rather than a property of ``ManualView``, because two
/// shapes draw it now: the one scroll view at an accessibility size and every
/// pushed level below those sizes (``ManualLevelHost``). `warnInk` rather
/// than the amber fill, because this is text.
struct ManualCachedLine: View {
    var body: some View {
        Text(ManualStrings.cachedCopy)
            .font(.caption)
            .foregroundStyle(Color.warnInk)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.bottom, 4)
    }
}

/// One level of the manual, drawn.
///
/// `place` is handed in rather than read off the model, which is the whole
/// reason this is a view and not a `switch` in one place: a page that is told
/// which level it draws can be keyed on it (``ManualMapStage/levelKey``,
/// ``ManualMapStage/place``) and can be asked whether it is the level on top
/// (``ManualMapStage/isOnTop``), where a page that reads `manual.place` for
/// itself cannot tell "I am the map" from "the map is what is on screen".
/// Both of those matter again now that a covered level stays mounted under a
/// pushed one.
///
/// `scrolls` is whether the level brings its own scroll view, which is
/// ``ManualView/ready(_:cached:_:)``'s decision and nothing else's.
struct ManualLevelView: View {

    let doc: ManualDoc
    let manual: ManualModel
    let place: ManualPlace
    var scrolls: Bool = true

    /// Android reads `Settings.Global.ANIMATOR_DURATION_SCALE == 0`
    /// (`ManualScreen.kt:292`); this is the platform's own answer to the same
    /// question, and the right one.
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        switch place {
        case .start:
            ManualStartPage(doc: doc, manual: manual, scrolls: scrolls)
        case .piece(let id):
            if let piece = doc.piece(id) {
                ManualPiecePage(doc: doc, piece: piece, manual: manual, scrolls: scrolls)
            } else {
                // The level survived a server switch and the new document has
                // no such piece. Kotlin falls to the map here
                // (`doc.piece(pieceOf(at)) == null`), and so does this.
                ManualMapPage(
                    doc: doc,
                    manual: manual,
                    focus: nil,
                    reduceMotion: reduceMotion,
                    // The place is the one this page was asked for and not
                    // the map it fell back to drawing: this page IS the
                    // level, and ``ManualMapPage/place`` says which.
                    place: place,
                    scrolls: scrolls
                )
            }
        case .map:
            ManualMapPage(
                doc: doc,
                manual: manual,
                focus: nil,
                reduceMotion: reduceMotion,
                place: place,
                scrolls: scrolls
            )
        case .topic(let id):
            ManualMapPage(
                doc: doc,
                manual: manual,
                focus: doc.topic(id),
                reduceMotion: reduceMotion,
                place: place,
                scrolls: scrolls
            )
        }
    }
}

/// One level as a **screen**, so that the system's swipe-back goes up a level
/// — Kamron's answer 32, and Android's
/// `BackHandler(enabled = at != "start") { up() }` (`ManualScreen.kt:315`).
///
/// ### The shape, and the shape it is not
///
/// Depth `0` is the route's own screen (Start here); each host presents the
/// next one through `navigationDestination(isPresented:)` on the **shell's**
/// `NavigationStack` (`WorkspaceView.swift:153`). There is no second
/// `NavigationStack` and no path element: ``ManualView/ready(_:cached:_:)``
/// has what train I003 measured about both of those, and ``ManualLevelStack``
/// is the arithmetic every host here reads.
///
/// Each host draws the whole screen — the top bar, the `cached` line, the
/// level — because a pushed screen covers the one below it: a bar drawn only
/// at depth 0 would leave a map with no pills, no crumbs, no search field and
/// no `zoom out`. The bar reads the model, so the top host's bar is the open
/// level's; a covered one's is out of sight except during the few hundred
/// milliseconds of an interactive swipe, where the crumbs underneath still
/// read the level being left. That is the one cosmetic cost of the gesture
/// and it is in this item's DECISIONS.
///
/// ### What happens if the push does not land
///
/// Nothing in this repository can run a `NavigationStack`: `pkgtest` builds
/// the package, `appcheck` type-checks, and only a train on the Mac drives
/// the real thing. So this host does not assume it worked. ``shown`` falls
/// back to the deepest level — the level swapped in place, which is what the
/// screen did before this commit — when a host has asked for a deeper screen
/// and none has drawn 700 ms later. The fallback is deliberately *late*: in
/// the ordinary case the deeper screen reports itself within a frame and
/// nothing swaps, so the page does not flicker between the two shapes on
/// every tap.
///
/// It covers both ways this can be wrong, and they are different:
///
///  * `navigationDestination(isPresented:)` does nothing at all inside a
///    path-driven stack — then depth 0 draws every level itself and the
///    screen is `6d071463`'s, with `zoom out` and the crumbs as the way up;
///  * a **chain** of them does not all mount in one update, which is what a
///    search result asks for (Start here to a piece is three screens at
///    once) — then the deepest host that did mount draws the piece, so the
///    page is right and only the depth of the stack is short.
///
/// The question is asked **once per push**, when ``presentsDeeper`` turns
/// true, and is never re-asked while that push stands (``deeperDrew``,
/// ``waitForTheScreenAbove()``). The first build asked it again on every
/// change to the chain, and a buried host cannot have it answered — its child
/// is already mounted and does not fire `onAppear` a second time — so at two
/// levels deep every host below the top fell into this branch 700 ms after
/// every move: a state write on a covered screen, which re-declares that
/// screen's own `navigationDestination`, which is the family of thing I003
/// measured unwinding a push. A guard that reproduces what it guards against
/// is worse than none, and this is review round 1's finding 2.
///
/// So a *working* push never reaches the late state at any depth. If a train
/// shows a map in the tree where a cluster should be, the fallback really is
/// what drew it, and the chain really was short.
struct ManualLevelHost: View {

    let doc: ManualDoc
    let manual: ManualModel
    let cached: Bool

    /// `0` is the route's screen; `1` the map, `2` a cluster, `3` a piece.
    let depth: Int

    /// Whether the manual is still the route on top of the shell's stack.
    ///
    /// **A level is presented only while it is.** The shell's stack is
    /// appended to from outside this screen — the phone bar, a notification
    /// tap, a capability that went away — and a route pushed *underneath*
    /// presentations that hang off the screen below it is a shape nobody here
    /// has measured. ``ManualView/isTopRoute`` has the four callers and the
    /// seam's other half; this is the gate, and it is declarative so the push
    /// and the dismissal resolve in one update.
    let isTopRoute: Bool

    /// What this host tells the host below it: that it has drawn. Depth 0 is
    /// told by nobody, so it defaults to a closure that does nothing.
    ///
    /// **One direction only.** It used to carry `false` from `onDisappear`
    /// as well, and that is the callback a pushed screen fires on the screen
    /// it *covers*: at two levels deep every buried host was told its child
    /// had gone, fell into the late branch below 700 ms later, and wrote
    /// state — which rebuilds a covered screen's body and re-declares its
    /// own `navigationDestination`, the family of thing train I003 measured
    /// unwinding a push. The guard against repeating I003 was the likeliest
    /// cause of one, on a timer. Review round 1's finding 2.
    var onDrawn: () -> Void = {}

    /// Whether the screen this host presents has **ever** drawn while it has
    /// been presented.
    ///
    /// Not "is it on screen": nothing in SwiftUI can answer that from here,
    /// for the reason on ``onDrawn``. It is reset where the presentation
    /// itself ends — ``waitForTheScreenAbove()`` — so the question "did my
    /// push land" is asked exactly once per push and never again while that
    /// push stands.
    @State private var deeperDrew = false

    /// Whether the screen this host presents was asked for and never came —
    /// see the type's "What happens if the push does not land".
    @State private var deeperIsLate = false

    /// The level this host last drew as **its own**.
    ///
    /// Read only while the chain is shorter than this host's depth, which is
    /// a host on its way out: a screen being popped has to keep drawing the
    /// level it is popped *off*, or the pop animation would show the level
    /// underneath sliding away from itself.
    @State private var lastOwn: ManualPlace = .start

    private var chain: [ManualPlace] { manual.levels }

    /// Whether this host presents a deeper screen **now**: the chain's
    /// arithmetic, and the manual still being the route on top.
    private var presentsDeeper: Bool {
        isTopRoute && ManualLevelStack.presentsDeeper(at: depth, in: chain)
    }

    /// The level this host is the screen for, while the chain still reaches it.
    private var own: ManualPlace? { ManualLevelStack.level(at: depth, in: chain) }

    /// Which level this host draws.
    private var shown: ManualPlace {
        if presentsDeeper, deeperIsLate, !deeperDrew { return manual.place }
        return own ?? lastOwn
    }

    var body: some View {
        VStack(spacing: 0) {
            ManualTopBar(manual: manual)
            if cached { ManualCachedLine() }
            ManualLevelView(doc: doc, manual: manual, place: shown, scrolls: true)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .onAppear {
            onDrawn()
            if let own { lastOwn = own }
        }
        .onChange(of: own) { _, now in
            if let now { lastOwn = now }
        }
        // **Keyed on the presentation and not on the chain.** One push, one
        // question: it restarts when this host starts presenting a deeper
        // screen and when it stops, and a move that only makes the chain
        // *longer* above this host does not re-ask a question this host has
        // already had answered. Keying it on the chain is what let a buried
        // host re-ask, never be answered (its child was already mounted and
        // does not fire `onAppear` twice) and fall into the late branch —
        // review round 1's finding 2.
        .task(id: presentsDeeper) { await waitForTheScreenAbove() }
        .navigationDestination(isPresented: deeperIsShown) { deeper }
    }

    /// Presented while the chain is longer than this depth; a dismissal cuts
    /// the chain back to this host's own level, which is one step of
    /// ``ManualModel/up()`` taken from the stack rather than from `place`.
    ///
    /// The setter runs for a tap on the navigation bar's back button and for
    /// the leading-edge swipe alike — one gesture, one control, one way up.
    ///
    /// It is read as "the person dismissed this screen", so it is answered
    /// only while the manual is the route on top: a dismissal that is
    /// ``isTopRoute`` going false is the gate closing under a route pushed
    /// over the whole manual, and ``ManualView``'s `onChange` is already
    /// collapsing the model for that. Without the guard the two would both
    /// write, in an order nothing here fixes, and a stack left half-cut
    /// re-pushes a level on the way back.
    private var deeperIsShown: Binding<Bool> {
        Binding(
            get: { presentsDeeper },
            set: { presented in
                guard !presented, isTopRoute else { return }
                manual.pop(to: depth)
            }
        )
    }

    /// The screen above this one, until the manual runs out of levels.
    ///
    /// `AnyView` is what stops a view whose destination is its own type from
    /// being an infinitely recursive Swift type. It costs one erased view per
    /// level and there are at most ``ManualLevelStack/maxDepth`` of them.
    @ViewBuilder private var deeper: some View {
        if depth < ManualLevelStack.maxDepth {
            AnyView(
                ManualLevelHost(
                    doc: doc,
                    manual: manual,
                    cached: cached,
                    depth: depth + 1,
                    isTopRoute: isTopRoute,
                    onDrawn: { deeperDrew = true }
                )
            )
        }
    }

    /// Give the screen above 700 ms to draw, then say it never came.
    ///
    /// 700 ms is longer than a push transition (about 350 ms) and well longer
    /// than the frame a presented view needs to report `onAppear`, so a
    /// working push never reaches the write; it is short enough that a build
    /// where the push does nothing still answers a tap inside the time a
    /// person waits for a screen.
    ///
    /// Run from `.task(id: presentsDeeper)`, so this is also the one place
    /// the two flags are cleared: a presentation that has ended is a question
    /// the next push gets to ask again, and `onDisappear` — the only other
    /// candidate — cannot tell a presentation ending from this screen being
    /// covered by the next one.
    private func waitForTheScreenAbove() async {
        // Each write is guarded on the value changing: a `@State` write is a
        // body rebuild, and this host may be a covered screen when the
        // presentation ends under it (the manual losing the top of the stack
        // is one way), where a rebuild is exactly what finding 2 was about.
        //
        // **The guard removes the redundant writes and not that one**, which
        // is review round 2's F3 and is deliberate: when ``isTopRoute`` goes
        // false — the phone-bar exit — every host's presentation ends at
        // once, and a host that had `deeperDrew == true` writes it false
        // while covered, in the same update as the route being pushed. The
        // alternative is worse: clearing in the *presenting* branch races the
        // child's `onAppear`, and losing that race arms the 700 ms fallback
        // on a push that landed, which is two stages both believing they are
        // on top (``ManualMapStage/wires(_:size:)`` says that cannot happen).
        // So this host writes on tear-down, on purpose, in the same update as
        // a rebuild that was happening anyway.
        guard presentsDeeper else {
            if deeperDrew { deeperDrew = false }
            if deeperIsLate { deeperIsLate = false }
            return
        }
        guard !deeperDrew else { return }
        if deeperIsLate { deeperIsLate = false }
        try? await Task.sleep(for: .milliseconds(700))
        guard !Task.isCancelled, presentsDeeper, !deeperDrew else { return }
        deeperIsLate = true
    }
}

// MARK: - the top bar

/// `ManualTop` (`ManualScreen.kt:367`): the two pills, the crumbs, the search
/// field and its results.
struct ManualTopBar: View {

    let manual: ManualModel

    @Environment(\.dynamicTypeSize) private var typeSize

    /// Whether the search field holds the keyboard.
    ///
    /// Android clears focus on both ways out of the search box
    /// (`ManualScreen.kt:386` for the Search key, `:409` for a tapped
    /// result) and the port had kept only the `query = ""` half of those two
    /// lines. The keyboard is 233 of this screen's 852 points, so opening a
    /// piece from a search left it standing over the piece that had just
    /// been opened — which is also how train S009's audit came to report
    /// **nine** anonymous `Contrast failed` against this screen. Every one of
    /// the nine is an element whose `minY` was at or below the keyboard's top
    /// (561) in that run: text drawn under a keyboard has no pixels an audit
    /// can read, the same cause ``StubHouseUITests`` records for four of the
    /// pairing screen's five. The colours were never in question — sampled
    /// off the audit's own screenshot, the weakest text on that page is `ok`
    /// at 5.05:1 and every chip and pill is 6.66:1 or better.
    @FocusState private var searchFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let doc = manual.doc {
                pills(doc)
                if manual.place != .start {
                    ManualCrumbs(doc: doc, manual: manual)
                }
                searchField(doc)
                if !manual.query.trimmingCharacters(in: .whitespaces).isEmpty {
                    results(doc)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
        .padding(.bottom, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// `SegPill` ×2 (`:398`). Which one is lit is the Kotlin's test exactly:
    /// `start here` while the place is Start here, `map` for every other
    /// level.
    @ViewBuilder private func pills(_ doc: ManualDoc) -> some View {
        // A row of two short words normally; a column once the words are big
        // enough that two of them cannot share a line (standing rule).
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 6) { pillPair(doc) }
        } else {
            HStack(spacing: 6) { pillPair(doc) }
        }
    }

    @ViewBuilder private func pillPair(_ doc: ManualDoc) -> some View {
        ManualSegPill(
            label: doc.label("start_here", fallback: ManualStrings.Fallback.startHere),
            active: manual.place == .start,
            // **Only on the map**, where this pill is the way up and no
            // `zoom out` is drawn (``ManualSegPill/hint``). From Start here
            // the swipe leaves the manual at every size and there is nothing
            // above to go up to, so the sentence would be false; from a
            // cluster or a piece `zoom out` is on screen and carries it.
            hint: manual.place == .map
                ? ManualStrings.swipeHint(isAccessibilitySize: typeSize.isAccessibilitySize)
                : nil
        ) {
            manual.go("start")
        }
        ManualSegPill(
            label: doc.label("map", fallback: ManualStrings.Fallback.map),
            active: manual.place != .start
        ) {
            manual.go("map")
        }
    }

    /// The search box (`:409`).
    ///
    /// ``MusicView``'s shape, and for its reason: a single-line field with a
    /// `search` key at the ordinary sizes, and a growing field with **no**
    /// `submitLabel` and no `onSubmit` at an accessibility size — a vertical
    /// field's Return key makes a newline, so a key labelled `search` would
    /// lie. Nothing is lost: the list searches as it is typed on both
    /// platforms (`remember(query, visible)`, `:370`), and the key only ever
    /// took the first result.
    @ViewBuilder private func searchField(_ doc: ManualDoc) -> some View {
        let placeholder = doc.label(
            "search_placeholder",
            fallback: ManualStrings.Fallback.searchPlaceholder
        )
        if typeSize.isAccessibilitySize {
            TextField(placeholder, text: query, axis: .vertical)
                .lineLimit(2...)
                .padding(.horizontal, 8)
                .padding(.vertical, 10)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
                .overlay { RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary) }
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .focused($searchFocused)
                .accessibilityLabel(placeholder)
                .onChange(of: manual.query) { _, typed in
                    if typed.contains(where: \.isNewline) {
                        manual.query = typed.filter { !$0.isNewline }
                    }
                }
        } else {
            TextField(placeholder, text: query)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .focused($searchFocused)
                // `:386`: the Search key opens the first result, clears the
                // query **and** clears focus. All three, not the first two.
                .onSubmit {
                    manual.openFirstResult()
                    searchFocused = false
                }
                .accessibilityLabel(placeholder)
        }
    }

    private var query: Binding<String> {
        Binding(get: { manual.query }, set: { manual.query = $0 })
    }

    /// The result list (`:424`): the matches, or `search_empty`.
    ///
    /// **The matches are read once into `hits`, and the list is built off
    /// that array.** ``ManualModel/results`` is a computed property that
    /// re-runs `searchManual` on every access (deliberately — see its doc
    /// comment), so it is a *different value* each time it is read. The first
    /// version of this view wrote `ForEach(manual.results.indices)` and then
    /// `manual.results[at]` inside the closure: `ForEach` holds the range
    /// from one read and evaluates its content against a later one, and
    /// picking a result sets `query = ""` — which makes the next read empty
    /// while that range still says `0..<1`. `hits[at]` then trapped on an
    /// index out of range and took the app down, with no crash report and no
    /// ObjC exception: train S003 lost all seven cases that open a piece from
    /// the search field to it, and two of them could only say the app no
    /// longer existed. A captured array cannot shrink under the range taken
    /// from it.
    private func results(_ doc: ManualDoc) -> some View {
        let hits = manual.results
        return VStack(alignment: .leading, spacing: 0) {
            if hits.isEmpty {
                Text(doc.label("search_empty", fallback: ManualStrings.Fallback.searchEmpty))
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(12)
            }
            // Indices, not `enumerated()`: Swift has no key path to a tuple
            // element, so `id: \.element.id` does not compile.
            ForEach(hits.indices, id: \.self) { at in
                let piece = hits[at]
                if at > 0 { Divider() }
                Button {
                    manual.open(result: piece)
                    // `:409`: `go(p.id); query = ""; focus.clearFocus()`. The
                    // keyboard covers the bottom 233 points, which is most of
                    // the piece this tap just opened.
                    searchFocused = false
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(piece.title)
                            .font(.subheadline)
                            .foregroundStyle(Color.primary)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(
                            ManualStrings.searchSubtitle(
                                topic: doc.topic(piece.topic)?.title ?? "",
                                subtitle: piece.subtitle
                            )
                        )
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(piece.title)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(.quaternary)
        }
    }
}

/// One of the two pills at the top (`SegPill`, `ManualScreen.kt:435`).
///
/// Android's minimum is 40 dp; Apple's is 44, and the audit measures it.
struct ManualSegPill: View {

    let label: String
    let active: Bool
    /// What the leading-edge swipe does from the level this pill is the way
    /// up from, or nil for a pill that is nobody's way up.
    ///
    /// **Review round 2's F2.** ``ManualCrumbs`` carries
    /// ``ManualStrings/swipeHint(isAccessibilitySize:)`` on `zoom out`, and
    /// draws `zoom out` only while `topic != nil` — so on the **map**, one of
    /// the three levels where the gesture means two different things at two
    /// text sizes, there was no control carrying the sentence at all and a
    /// VoiceOver user was told nothing. The map's way up is this pill.
    ///
    /// Nothing can see it: `XCUIElement` exposes no hint, so
    /// `ManualAnswersTests.testZoomOutSaysWhatTheSwipeDoesAtThisTextSize`
    /// pins the sentence and this is its second reader, by review only.
    var hint: String? = nil
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .foregroundStyle(active ? Color.brandInk : Color.primary)
                .padding(.horizontal, 14)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .background(
            active ? Color.brand.opacity(0.14) : Color(.secondarySystemBackground),
            in: RoundedRectangle(cornerRadius: 8, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                // See ManualMapView: a ternary of two unlike shape styles
                // needs `AnyShapeStyle` on both arms.
                .strokeBorder(
                    active ? AnyShapeStyle(Color.brandInk) : AnyShapeStyle(.quaternary)
                )
        }
        // The pills are a two-way choice and the lit one is not merely
        // coloured: VoiceOver is told which is picked.
        .accessibilityAddTraits(active ? [.isButton, .isSelected] : .isButton)
        // An empty hint is no hint, which is what every pill but one gets.
        // Said unconditionally rather than behind a `ViewBuilder` `if`: two
        // arms are a `_ConditionalContent` and this app has already paid for
        // a modifier that flipped a subtree's identity
        // (`ShellChromeViews.swift:505-530`).
        .accessibilityHint(Text(verbatim: hint ?? ""))
    }
}

/// `Crumbs` (`ManualScreen.kt:443`): Domovoi › topic › piece, each but the
/// last a way back, plus `zoom out` whenever a topic is in play.
struct ManualCrumbs: View {

    let doc: ManualDoc
    let manual: ManualModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // Android's `FlowRow` wraps; SwiftUI has no flow layout, so the row
        // becomes a column at an accessibility size — where a three-level
        // crumb trail cannot share a line on any iPhone.
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 2) { trail }
        } else {
            HStack(alignment: .firstTextBaseline, spacing: 4) { trail }
        }
    }

    private var topic: ManualTopic? {
        doc.topic(manual.place.topicID ?? doc.piece(manual.place.pieceID)?.topic)
    }

    private var piece: ManualPiece? { doc.piece(manual.place.pieceID) }

    @ViewBuilder private var trail: some View {
        ManualCrumb(text: doc.centre.title, link: topic != nil) { manual.go("map") }
        if let topic {
            ManualCrumbChevron()
            ManualCrumb(text: topic.title, link: piece != nil) { manual.go(topic.id) }
        }
        if let piece {
            ManualCrumbChevron()
            ManualCrumb(text: piece.title, link: false) {}
        }
        if topic != nil {
            Button {
                manual.up()
            } label: {
                Label(
                    doc.label("zoom_out", fallback: ManualStrings.Fallback.zoomOut),
                    systemImage: "minus.magnifyingglass"
                )
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .frame(minHeight: 44)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            // **The gesture is said out loud, and it says the truth of the
            // size it is read at.** Answer 32 put a swipe back on this
            // screen; below the accessibility sizes it does what this button
            // does (``ManualLevelHost``) and at them it leaves the manual
            // (``ManualView/ready(_:cached:_:)``, Kamron's entry 74). iOS
            // announces a hardware back for nobody — there isn't one — so a
            // leading-edge swipe is undiscoverable with VoiceOver unless the
            // control that does the same job mentions it. A hint that said
            // "a swipe does this too" at every size would be untrue at two
            // of them, which is the no-misleading-controls rule pointed at a
            // sentence rather than at a button.
            .accessibilityHint(
                ManualStrings.swipeHint(isAccessibilitySize: typeSize.isAccessibilitySize)
            )
        }
    }
}

/// The `›` between two crumbs. Hidden from VoiceOver: the crumbs read as
/// their own words and "chevron right" three times is noise.
struct ManualCrumbChevron: View {
    var body: some View {
        Image(systemName: "chevron.right")
            .font(.caption2)
            .foregroundStyle(Color.fgMuted)
            .accessibilityHidden(true)
    }
}

/// One crumb (`Crumb`, `ManualScreen.kt:467`). The last one is not a control,
/// so it is not drawn as one.
struct ManualCrumb: View {

    let text: String
    let link: Bool
    let action: () -> Void

    var body: some View {
        if link {
            Button(action: action) {
                Text(text)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
        } else {
            Text(text)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - a span's link, as a URL

/// What a tapped link in the manual's text was.
struct ManualLinkTarget: Equatable {
    /// `route` | `node` | `doc`.
    let kind: String
    let target: String
}

/// The scheme this screen mints its own links under.
///
/// `Text` hands a tapped link to `openURL` as a `URL`, and `ManualSpan.link`
/// carries a kind and a target that are not a URL at all (`node:how-it-works`,
/// `doc:docs/rooms.md#wake`). So the span becomes a URL of this app's own,
/// and ``ManualView`` recognises it again on the way back. **Not registered
/// in `Info.plist`**, deliberately: nothing outside the app may send one, and
/// the item must not edit `project.yml`.
let manualLinkScheme = "domovoi-manual"

/// A span's link as a URL. Nil when the target cannot be encoded, and then
/// the span is drawn as plain text — a link that cannot be followed is not
/// drawn as one.
func manualLinkURL(kind: String, target: String) -> URL? {
    // `.alphanumerics` and not a URL character set: a `doc:` target carries
    // `/` and `#`, and both are structural in a URL.
    guard let encoded = target.addingPercentEncoding(withAllowedCharacters: .alphanumerics),
          ["route", "node", "doc"].contains(kind)
    else { return nil }
    return URL(string: "\(manualLinkScheme)://\(kind)?t=\(encoded)")
}

/// The other direction. Nil for any URL that is not one of ours, which is how
/// a real link (`More detail`, the dashboard) reaches the system unchanged.
func manualLink(of url: URL) -> ManualLinkTarget? {
    guard url.scheme == manualLinkScheme, let kind = url.host else { return nil }
    guard let target = URLComponents(url: url, resolvingAgainstBaseURL: false)?
        .queryItems?
        .first(where: { $0.name == "t" })?
        .value
    else { return nil }
    return ManualLinkTarget(kind: kind, target: target)
}
