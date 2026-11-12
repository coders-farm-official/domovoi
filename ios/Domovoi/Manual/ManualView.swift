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
/// drawn ([[no-misleading-controls]]). The one thing left out is decoration,
/// and it is in `design-notes/ios/overnight/manual/DECISIONS.md`:
///
/// * **The dashed wires do not follow the float.** The nodes bob (answer 31,
///   `ManualScreen.kt:559`) on one `repeatForever` animation each, which the
///   render server runs without a redraw; Android reads its float inside the
///   `Canvas` and redraws every wire with it, 60 times a second for as long
///   as the map is open. A wire runs centre to centre between two ~104-point
///   cards, so the part of it that would have moved is the part the cards
///   cover. ``ManualMapStage``'s wires have the note. (They **do** follow the
///   camera: during the zoom glide they are redrawn every frame, as
///   Android's are.)
///
/// ### One screen holds every level (the owner's answer 73)
///
/// Android draws the manual in one composition: the top bar, then a `when`
/// over `at` (`ManualScreen.kt:344-347`) whose map arm is **one** `ManualMap`
/// for the map and for every cluster, with a camera that glides between them
/// over 280 ms (`MOVE_MS`, `:166`; the camera at `:548-552`). This screen is
/// the same shape: ``ready(_:cached:_:)`` draws the bar and one
/// ``ManualLevelView``, whose map arm is one ``ManualMapStage`` for the map
/// and every cluster, so a move between those two is a value change inside
/// one stage and the camera glides (``ManualGlide``, ``ManualCamera``). Start
/// here and a piece's page are other arms, so a move to or from them is a
/// cut, as on Android. Under Reduce Motion every move is a cut — Android's
/// `snap()` (`:549`), which has no cross-fade fallback either.
///
/// It replaces answer 32's stack of pushed level screens
/// (`navigationDestination(isPresented:)` on the shell's stack), which could
/// not glide — a pushed level is a different screen, with no camera shared
/// between it and the one under it — and which could not be laid out at the
/// accessibility sizes at all.
///
/// ### The swipe back goes up a level, at every size (answer 74)
///
/// ``ManualBackSwipe`` puts a recogniser in front of the navigation stack's
/// pop: from the map, a cluster or a piece a swipe from the leading edge goes
/// up one level, and from Start here it fails and the system's pop leaves the
/// manual — Android's `BackHandler(enabled = at != "start") { up() }`
/// (`:315`). It sits on the navigation controller's view, outside both of this
/// screen's layouts, so the accessibility-size layout (bar and page in one
/// scroll view) answers the same. VoiceOver's escape — the two-finger Z — is
/// the same decision (``escape()``). `zoom out` and the crumbs still go up,
/// and `zoom out`'s hint says the swipe does too
/// (``ManualStrings/swipeGoesUpHint``).
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
    /// visibility** — ``ManualScreenHold``'s whole doc comment. A route pushed
    /// over the manual (the phone bar, a notification, a `route:` link)
    /// covers this screen and delivers `onDisappear` while it stays mounted,
    /// and the reads it would have stopped are the ones the manual draws when
    /// it is uncovered. A covered screen keeps its `@State` and a popped one
    /// does not, so the object in that storage is the one thing in SwiftUI
    /// that can tell a cover from a destruction. `hold == nil` is also the
    /// guard that makes a repeated `onAppear` do nothing.
    @State private var hold: ManualScreenHold?

    /// `at` in a `rememberSaveable` (`ManualScreen.kt:290`) — the same
    /// promise `@SceneStorage` makes: it survives a process death and dies
    /// with the app's removal. The string is ``ManualPlace/saved``.
    ///
    /// **The promise, not Android's shell.** Android loses this value every
    /// time another route is opened, because the composable holding it is
    /// disposed (``act(_:in:)`` has the reading); this screen is covered
    /// instead and keeps it.
    @SceneStorage("manual.place") private var savedPlace = "start"

    /// Read here as well as in ``ManualTopBar``: who owns the scroll view
    /// changes with it (see ``ready(_:cached:_:)``).
    @Environment(\.dynamicTypeSize) private var typeSize

    /// **The system's, not the override installed below.** `.environment`
    /// applies to a view's descendants, so the action this screen hands its
    /// pages is not the one this screen itself holds — which is what keeps
    /// `act(.web(…))` from re-entering the handler that called it.
    @Environment(\.openURL) private var openURL

    /// The route's own pop, for VoiceOver's escape on Start here.
    @Environment(\.dismiss) private var dismiss

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
        // **The swipe back, at every size** — ``ManualBackSwipe``. In the
        // background so it is under everything and takes no room; it draws
        // nothing and is hidden from accessibility.
        .background {
            ManualBackSwipe(decide: { [weak manual] in back(manual) }, goUp: { [weak manual] in manual?.up() })
        }
        // VoiceOver's escape (the two-finger Z) is the same decision as the
        // swipe: up a level, or out of the manual from Start here. The
        // container is `.contain` so the action has an element to hang on
        // whose children are still every element they were.
        .accessibilityElement(children: .contain)
        .accessibilityAction(.escape) { escape() }
        // Built here and not in `init`, for ``FilesView``'s reason: the model
        // needs the container and a `@State` initialiser runs before the view
        // has one. The visibility closure is read **each time a link is
        // judged**, not captured once, so a capability that arrives after
        // this screen opened is taken into account.
        //
        // **The start and the stop are ``ManualScreenHold``'s, not this
        // callback's** (review round 1's must on answer 32): `.task` runs once
        // per view identity and `onDisappear` arrives for a cover as well as
        // for a pop, so a `.task { start() }` / `onDisappear { stop() }` pair
        // stops the reads when a route is pushed over the manual and nothing
        // restarts them.
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
        // Every `[text](route:…)`, `(node:…)` and `(doc:…)` in the document
        // arrives here: a span's link is a URL this file minted
        // (``manualLinkURL(kind:target:)``), and `Text` routes a tapped link
        // through `openURL`. Anything that is not one of ours — the piece
        // page's `More detail` button, which opens GitHub — falls through to
        // the system, which is what `.systemAction` means.
        //
        // **This one install covers every level, at every size**, because
        // every level is drawn inside this view's body — ``ready(_:cached:_:)``
        // builds the level in both of its branches, the pinned bar and the
        // accessibility-size scroll view alike, and nothing of the manual is
        // presented through `navigationDestination`. That is what train I005
        // measured going wrong: a level presented as a pushed screen is not
        // an environment descendant of the view that installed this, and a
        // `route:` link tapped on a pushed piece did nothing at all.
        // `ManualLinkHostTests` reads both halves of that off this file.
        .environment(\.openURL, links)
    }

    /// What a tapped link in the document does.
    private var links: OpenURLAction {
        OpenURLAction { url in
            guard let link = manualLink(of: url), let manual else { return .systemAction }
            follow(link, in: manual)
            return .handled
        }
    }

    /// The one decision behind the swipe and the escape.
    private func back(_ manual: ManualModel?) -> ManualBack {
        guard let manual else { return .notTheManuals }
        return manualBack(at: manual.place, manualIsOnTop: model.navigation.route == .manual)
    }

    /// VoiceOver's escape: up a level, or leave the manual from Start here.
    private func escape() {
        switch back(manual) {
        case .upALevel: manual?.up()
        case .leaveTheManual: dismiss()
        case .notTheManuals: break
        }
    }

    // MARK: - the four load states

    /// `result == null -> LoadingState()` (`ManualScreen.kt:197`).
    private var loading: some View {
        ProgressView()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .accessibilityLabel(ManualStrings.Fallback.eyebrow)
    }

    /// The manual itself: the top bar, the `cached` line, and the level —
    /// **every** level, on this one screen (answer 73).
    ///
    /// **Who scrolls changes with the type size.** Below the accessibility
    /// sizes the top bar is pinned and the level scrolls under it, which is
    /// Android's shape (`ManualScreen.kt:349`: `ManualTop` above
    /// `ManualBody`). At an accessibility size the bar is two stacked pills,
    /// the crumbs and a growing search field — 310 of the 400 points this
    /// screen is given on a 6.1" phone, measured in train S006, which left
    /// the manual itself a 90-point slot to be read through. So the bar and
    /// the page share **one** scroll view there and the bar scrolls away.
    /// Nothing is lost: the bar's controls are the first thing in the scroll,
    /// a flick from anywhere.
    ///
    /// Both branches draw the same ``ManualLevelView`` for `manual.place`, so
    /// the glide, the retype, the links and the swipe are the same at every
    /// size; only who owns the scroll differs.
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
                    ManualLevelView(doc: doc, manual: manual, scrolls: false)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            VStack(spacing: 0) {
                ManualTopBar(manual: manual)
                if cached { ManualCachedLine() }
                ManualLevelView(doc: doc, manual: manual, scrolls: true)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
    }

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
                    // Android's filled `Button` (its theme's `primary`/`onPrimary`, `brand` and
                    // `brandFg`). Until unit ix-fill this was a `.borderedProminent` with no
                    // tint, so it filled with the app's ``Color/brandInk`` and drew a white
                    // label: 5.65:1 light and **1.91:1** dark, and grey on grey disabled.
                    .buttonStyle(BrandFillButtonStyle())
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

    /// `onRoute` (`ManualScreen.kt:321-327`).
    ///
    /// **The manual keeps its level when a link opens another screen, and
    /// Android's does not** — an Android defect read, not copied. Android's
    /// `at` is a `rememberSaveable` (`ManualScreen.kt:290`), which is a
    /// promise to keep the place; but `ScreenRouter` (`ScreenRouter.kt:56`)
    /// composes only the current route, and the shell's one
    /// `SaveableStateProvider` is keyed on the server's address
    /// (`AppShell.kt:231`), not on the route. So `navigate(...)` disposes
    /// `ManualScreen`, its saveable goes with it, and coming back starts again
    /// at `"start"`. Here the manual is covered, not destroyed (``hold``'s
    /// doc), so its model keeps `place` and coming back lands on the piece
    /// the link was in. A candidate for the Android defects ledger
    /// (`design-notes/ios/ANDROID-DEFECTS-FOUND-BY-IOS.md`), not yet entered.
    ///
    /// While a level was a pushed screen this collapsed the manual to Start
    /// here first (DECISIONS D32B), because a route pushed underneath live
    /// presentations was a shape nobody had measured; with one screen there
    /// is nothing presented to collapse.
    private func act(_ open: ManualOpen, in manual: ManualModel) {
        switch open {
        case .screen(let route):
            model.navigate(route)
        case .settingsTab(let tab):
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

// MARK: - one level

/// The line above a level when the document is the copy from last time
/// (`ManualScreen.kt:337`).
///
/// A view of its own rather than a property of ``ManualView``, because both
/// of that screen's layouts draw it (``ManualView/ready(_:cached:_:)``).
/// `warnInk` rather than the amber fill, because this is text.
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

/// The level the manual is on, drawn — `ManualBody`'s `when`
/// (`ManualScreen.kt:344-347`).
///
/// **An `if` chain and not a `switch` over ``ManualPlace``, and that is the
/// glide.** The map and every cluster must be the **same** view, so that a
/// move between them is a change of `focus` inside one ``ManualMapStage`` and
/// its camera can glide (answer 73). A `switch` gives `.map` and `.topic` two
/// arms and two identities, and a move between two identities is a removal
/// and an insertion — the stage is rebuilt, and there is no camera to move.
/// Here they share the last `else`, exactly as Android's map arm is one
/// `ManualMap` for `"map"` and every `"t:<id>"` (`:347`), and so does a
/// piece the document no longer has (Kotlin's `doc.piece(pieceOf(at)) ==
/// null` falls to the map too).
///
/// `scrolls` is whether the level brings its own scroll view, which is
/// ``ManualView/ready(_:cached:_:)``'s decision and nothing else's.
struct ManualLevelView: View {

    let doc: ManualDoc
    let manual: ManualModel
    var scrolls: Bool = true

    /// Android reads `Settings.Global.ANIMATOR_DURATION_SCALE == 0`
    /// (`ManualScreen.kt:292`); this is the platform's own answer to the same
    /// question, and the right one.
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let place = manual.place
        if place == .start {
            ManualStartPage(doc: doc, manual: manual, scrolls: scrolls)
        } else if let id = place.pieceID, let piece = doc.piece(id) {
            ManualPiecePage(doc: doc, piece: piece, manual: manual, scrolls: scrolls)
        } else {
            ManualMapPage(
                doc: doc,
                manual: manual,
                focus: doc.topic(place.topicID),
                reduceMotion: reduceMotion,
                scrolls: scrolls
            )
        }
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
            // the swipe leaves the manual and there is nothing above to go up
            // to, so the sentence would be false; from a cluster or a piece
            // `zoom out` is on screen and carries it.
            hint: manual.place == .map ? ManualStrings.swipeGoesUpHint : nil
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
            TextField(
                placeholder,
                text: query,
                // Owner answer 65's vertical leg (`ShellHintPrompt`).
                prompt: ShellHintPrompt.text(placeholder),
                axis: .vertical
            )
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
    /// ``ManualStrings/swipeGoesUpHint`` on `zoom out`, and draws `zoom out`
    /// only while `topic != nil` — so on the **map** there was no control
    /// carrying the sentence at all and a VoiceOver user was told nothing.
    /// The map's way up is this pill.
    ///
    /// Nothing can see it: `XCUIElement` exposes no hint, so
    /// `ManualAnswersTests.testZoomOutSaysWhatTheSwipeDoes` pins the sentence
    /// and this is its second reader, by review only.
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
            // **The gesture is said out loud.** Answer 32 put a swipe back on
            // this screen and answer 74 made it go up a level at every text
            // size (``ManualBackSwipe``). iOS announces a hardware back for
            // nobody — there isn't one — so a leading-edge swipe is
            // undiscoverable with VoiceOver unless the control that does the
            // same job mentions it.
            .accessibilityHint(ManualStrings.swipeGoesUpHint)
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
