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
///   cover. ``ManualMapStage/wires(_:size:)`` has the measurement, and the
///   reason only the level on **top** of the stack floats at all.
/// * **There is no 280 ms zoom between levels.** Android animates its camera
///   inside one composition (`MOVE_MS`, `:549`); here each level is a screen,
///   so the move is the stack's push transition. ``ManualMapStage/body`` says
///   so, and the question of whether to rebuild the zoom on top of the stack
///   is in DECISIONS.
/// * **A swipe back goes up a level below the accessibility sizes only**
///   (answer 32). The levels are a `NavigationStack` of the manual's own
///   (``levelStack(_:_:)``), and at an accessibility size this screen puts
///   its bar and its page in one scroll view — where a navigation stack has
///   no height to lay out in. There `zoom out` and the crumbs are the way up,
///   which is what they were at every size before.
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

    /// `at` in a `rememberSaveable` (`ManualScreen.kt:290`) — the same
    /// promise `@SceneStorage` makes: it survives a process death and dies
    /// with the app's removal. The string is ``ManualPlace/saved``.
    @SceneStorage("manual.place") private var savedPlace = "start"

    /// Android reads `Settings.Global.ANIMATOR_DURATION_SCALE == 0`
    /// (`:292`); this is the platform's own answer to the same question, and
    /// the right one.
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

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
        .task {
            if manual == nil {
                manual = ManualModel(in: model.container, visibility: { [model] in model.visibility })
                manual?.restore(saved: savedPlace)
            }
            manual?.start()
        }
        .onDisappear { manual?.stop() }
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
                    if cached { cachedLine }
                    // The level draws **without** a scroll view of its own:
                    // two nested vertical scrolls would fight for the same
                    // drag, and the inner one would be the 90-point slot
                    // this branch exists to get rid of.
                    //
                    // And **without the level stack**, which is the one place
                    // answer 32 does not reach: a `NavigationStack` inside a
                    // `ScrollView` has no height of its own to lay out in.
                    // Here `zoom out` and the crumbs are the way up and the
                    // swipe-back leaves the manual, which is what every level
                    // did before. Written up in DECISIONS.
                    level(doc, manual, at: manual.place, scrolls: false)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            ManualTopBar(manual: manual)
            if cached { cachedLine }
            levelStack(doc, manual)
        }
    }

    /// The manual's own levels, **as a navigation stack**, so a swipe back
    /// goes up a level and only Start here leaves the manual — Kamron's
    /// answer 32.
    ///
    /// ### Why the manual brings a stack of its own
    ///
    /// The workspace's stack is `NavigationStack(path: $model.path)` and
    /// ``ShellModel/path`` is `[Route]` (`ShellModel.swift:497`). A
    /// *typed* path can only carry its own element type: a
    /// `navigationDestination(for: ManualPlace.self)` declared inside it
    /// resolves, but nothing can push a `ManualPlace` onto an array of
    /// `Route`, and a `NavigationLink(value:)` of another type is inert.
    /// ``AudiobookDetailView`` hit the same wall for an `Int64` and drew its
    /// own back row instead; People's `MAP.md` planned a
    /// `navigationDestination` and gave it up for the same reason. Making the
    /// shell's path a `NavigationPath` would fix it for every screen at once
    /// and is in DECISIONS under "Needs a shared change"; this item may not
    /// touch `ShellModel.swift`, so the manual carries its levels on a stack
    /// of its own, inside the route's screen.
    ///
    /// The path is ``ManualModel/levels``, the chain `start → map → topic →
    /// piece`, which is ``ManualModel/up()``'s own walk — so the gesture and
    /// `zoom out` are the same move and cannot disagree. Two ways up is what
    /// Android has (`ManualScreen.kt:456` draws `zoom out` beside the system
    /// back).
    ///
    /// ### The bar stays outside it
    ///
    /// One ``ManualTopBar`` above the stack, not one per level. A bar per
    /// level would mean two or three `map` pills and three search fields in
    /// the tree at once — the covered ones unhittable — and every query in
    /// this item's cases takes a `firstMatch`. It is also the shell's own
    /// shape: `VStack { ShellTopBar; NavigationStack { … } }`
    /// (`WorkspaceView.swift:117`).
    ///
    /// The inner stack's **own** navigation bar is hidden: the route already
    /// has one above it, with the back button that leaves the manual, and two
    /// bars for one screen is chrome nobody asked for.
    private func levelStack(_ doc: ManualDoc, _ manual: ManualModel) -> some View {
        NavigationStack(path: levelPath(manual)) {
            level(doc, manual, at: .start, scrolls: true)
                .toolbar(.hidden, for: .navigationBar)
                .navigationDestination(for: ManualPlace.self) { pushed in
                    level(doc, manual, at: pushed, scrolls: true)
                        .toolbar(.hidden, for: .navigationBar)
                }
        }
    }

    /// ``ManualModel/levels`` as a binding: read it, and a pop writes the
    /// shorter chain back through ``ManualModel/open(levels:)``, which puts
    /// ``ManualModel/place`` on the level that is now on top.
    private func levelPath(_ manual: ManualModel) -> Binding<[ManualPlace]> {
        Binding(
            get: { manual.levels },
            set: { manual.open(levels: $0) }
        )
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

    /// `ManualScreen.kt:337` — hard-coded in the Kotlin too, and `warnInk`
    /// rather than the amber fill, because this is text.
    private var cachedLine: some View {
        Text(ManualStrings.cachedCopy)
            .font(.caption)
            .foregroundStyle(Color.warnInk)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.bottom, 4)
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

    // MARK: - the manual

    /// One level, drawn.
    ///
    /// `at` is passed in rather than read off the model: each screen of
    /// ``levelStack(_:_:)`` draws the level it was pushed **as**, so a
    /// covered level keeps drawing its own page while the one above it is on
    /// screen. `scrolls` is whether the level brings its own scroll view,
    /// which is ``ready(_:cached:_:)``'s decision and nothing else's.
    @ViewBuilder private func level(
        _ doc: ManualDoc,
        _ manual: ManualModel,
        at place: ManualPlace,
        scrolls: Bool
    ) -> some View {
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

    // MARK: - what a link does

    /// `ManualLinks` (`ManualScreen.kt:317`), with the decisions in the model
    /// and only the acting here.
    private func follow(_ link: ManualLinkTarget, in manual: ManualModel) {
        switch link.kind {
        case "node":
            manual.go(link.target)
        case "doc":
            act(manual.openDoc(link.target))
        default:
            act(manual.openRoute(link.target))
        }
    }

    private func act(_ open: ManualOpen) {
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
            active: manual.place == .start
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
