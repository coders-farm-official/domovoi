import DomovoiKit
import SwiftUI

/// Images: ask the house's own GPU for a picture, watch it being made, and
/// look at what it has made before.
///
/// `android/.../ui/screens/images/ImagesScreen.kt`, the screen that the
/// **Image Generation plugin** puts in the app rather than one of Domovoi's
/// own. `ScreenRouter.kt:67` reaches it through
/// `PluginScreenFrame(CAP_IMAGEGEN)`, which is the ``PluginBandView`` row at
/// the top of this page (owner answer 29 moved it inside the scroll view, so
/// it is a row and not a wrapper), and the route itself is gated on the
/// `imagegen` capability
/// (``Route/requiredCapability``) — a house without the plugin never gets
/// this far and sees ``Route/unavailableNote(_:)`` instead.
///
/// ### What is here
///
/// Whether the engine is installed, running, or neither; a lane of what is
/// being made right now, with its progress; and the history, each generation
/// with its first three pictures, the prompt and the settings it was made
/// with. Tapping a picture opens it, and the share sheet is the way out to
/// Files, Photos or AirDrop.
///
/// **It is a read-only screen from a phone**, and the next section is why.
///
/// ### What is not, and why there is no control for it
///
/// **Generate, the lane's cancel, and the history delete** — the plugin's
/// three mutating routes. They were drawn for one round and are gone
/// (2026-11-03).
///
/// All three are `dependencies=ADMIN_ONLY` on the server
/// (`domovoi-plugin-imagegen/domovoi_plugin_imagegen/web.py:149`, applied at
/// `:360` generate, `:339` cancel, `:651` delete history), which is
/// `Depends(admin_required)` (`domovoi/webkit.py:363`) and classifies through
/// `check_admin_request` (`domovoi/admin_auth.py:1245-1274`) — a Bearer
/// session and the dashboard's cookie, and **never** `X-Device-Token`. This
/// app holds no admin credential at all (`DeviceToken.swift:15`;
/// `AudiobookModels.swift:444` records the same thing for the audiobook
/// delete), so on any house whose admin password exists the three answered
/// **401 `admin session required`**, every time, from every phone. The
/// plugin's own module docstring states that posture (`web.py:15-21`) and
/// every shipped version from 1.0.1 on carries it.
///
/// So: a control that cannot do what it says is not drawn (NIGHT-RULES §3),
/// and Kamron settled it twice in the same answer set — answer 5 (hide
/// Android's room-to-room `start`, which 401s from a phone the same way) and
/// answer 41, "admin-only things stay off both phones". The four reads
/// (`/models`, `/jobs`, `/history`, `/file/{name}`) are open and are the
/// whole screen.
///
/// The plumbing behind the three is **kept and still tested** —
/// ``ImagesModel/generate()``, ``ImagesModel/cancel(_:)``,
/// ``ImagesModel/confirmDelete()`` and their three paths. Giving the plugin's
/// three routes the household tier (`enforce_route_tier`,
/// `domovoi/webkit.py:333-360`) is a change in a repository this batch must
/// not edit, and it is the one line that would make them work from both
/// phones; it is logged under "Needs a shared change" in
/// `design-notes/ios/batch-2026-11-03/screens/DECISIONS.md`. The day it
/// lands, this screen draws them again and the model is already there.
///
/// **The engine itself** — installing it, starting it, stopping it,
/// downloading a model. Android has no control for any of that either: the
/// web dashboard's Images page owns it (`ImagesScreen.kt:70-74`), the
/// plugin's client code in `android/` calls no such route, and inventing one
/// would be inventing server behaviour (NIGHT-RULES §6). The three engine
/// states keep Android's own sentences, which say where to go.
///
/// **The plugin's accent colour.** `PluginFrame.kt:95-115` re-tints the whole
/// subtree with the colours the server derived for the plugin. iOS's colours
/// are static `Color` extensions (`DomovoiApp.swift:264-345`) — there is no
/// theme environment to swap — so the band names the plugin and the page
/// keeps Domovoi's amber. Nothing is faked and no second colour system is
/// introduced. DECISIONS, "The plugin accent is not ported".
///
/// **A fourth picture in a generation.** Android draws three
/// (`ImagesScreen.kt:398`) and so does this. A `+1 more` affordance would
/// open nothing, which is the control this app is built not to draw; the card
/// says `4 images, showing 3` instead (Kamron's answer 27). DECISIONS.
///
/// **Re-running a past generation.** No Android control, no route.
///
/// **`kind` and `completed_at`.** Decoded, because the shape is the
/// server's, and drawn nowhere: no Android view draws either, and inventing a
/// date format for a field whose timezone convention nothing in this
/// repository describes is how `[[verify-against-reality]]` entries get
/// written.
///
/// ### The lazy column holds nothing
///
/// Every read is ``ImagesModel``'s and so is every write the model still
/// carries, never a row's. A row in a `LazyVStack` is torn down as it scrolls
/// out and would take its `Task` with it, cancelling the work mid-flight.
/// Android obeys the same rule for the same control: its cancel runs on
/// `app.scope` (`ImagesScreen.kt:349`).
struct ImagesView: View {

    let model: ShellModel

    @State private var images: ImagesModel?

    /// `onAppear`/`onDisappear`, for ``HomeView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy.
    @State private var visible = false

    /// Which generated picture is open, by its file name.
    @State private var looking: ImagesOpenPicture?

    /// **No `ZStack` and no dialog.** The one dialog this screen had was the
    /// delete confirmation, and the delete it confirmed is admin-only (this
    /// type's own note). A confirmation with nothing to confirm is a scrim
    /// over a screen, so the body is the page.
    var body: some View {
        screen
    }

    private var screen: some View {
        ScrollView {
            // 10-point gaps and 16-point padding, which is the Kotlin's
            // `LazyColumn` (`ImagesScreen.kt:184`).
            LazyVStack(alignment: .leading, spacing: 10) {
                // **The plugin band is the page's first row, not a wrapper
                // around the page** — the band half of owner answer 29, which
                // asks a screen's chrome to scroll away at accessibility text
                // sizes.
                //
                // It was `ImagesPluginBand { ImagesView(…) }` in
                // `RouteScreen.swift`, i.e. a `VStack` *outside* this
                // `ScrollView`, so the band held its 256 points of the 852 on
                // a 393-point phone at AX5 whatever the page did (train S001,
                // `testImages_AtTheLargestTypeSize`): with the header's 300,
                // the navigation bar's 44 and the phone bar's 146 the page
                // was left a 102-point window. Inside the scroll view it
                // scrolls off like any other row. ``PluginBandPlace`` has the
                // padding argument — `.insideThePage` pads by 0 because the
                // `.padding(16)` below is the page's.
                //
                // The move needed `ImagesView.swift` and
                // `RouteScreen.swift` at once, which is why neither the shell
                // nor the player unit could make it (shell's DECISIONS §4a).
                PluginBandView(
                    model: model,
                    capability: capImagegen,
                    icon: Route.images.systemImage,
                    place: .insideThePage
                ) {
                    EmptyView()
                }
                header
                if let images {
                    engine(images)
                    // `:215`'s prompt, picker and `generate` are **not
                    // here**: the route behind them is admin-only and this
                    // phone has no admin session, so the whole composer is
                    // out rather than drawn and dead (this type's own note,
                    // Kamron's answer 41).
                    lane(images)
                    history(images)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // **The page says which scroll view it is.**
        //
        // `StubHouseUITests+Images.swift`'s `imagesPage(_:)` used to take the
        // tallest scroll view in the tree, and the band outside the page used
        // to make the page the SHORTER one at AX5: 102 points against the
        // shell's 146-point phone bar (DECISIONS, "At an accessibility size
        // the band leaves the page a 102-point window"). The band is a row of
        // this scroll view now, so that particular inversion is gone — but
        // the identifier stays, because "the tallest scroll view" was never
        // what the case meant and the phone bar can outgrow a short page
        // again. Train S004 flicked the phone bar instead and the flick
        // landed on `music`, so
        // `testImages_AtTheLargestTypeSize` measured Music and compared
        // nothing. An identifier is the only thing in the tree that tells two
        // scroll views apart by *what they are* rather than by their size;
        // `images-page` is spelt the same in that file.
        //
        // `.accessibilityIdentifier` alone names this element — it does not
        // make the scroll view an accessibility element or merge its children,
        // so nothing VoiceOver reads changes.
        .accessibilityIdentifier("images-page")
        .task {
            // Built here rather than in `init`, for ``HomeView``'s reason:
            // the model needs the container and a `@State` initialiser runs
            // before the view has one.
            if images == nil {
                images = ImagesModel(in: model.container)
            }
            images?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            images?.stop()
        }
        // **The toast is the shell's**, drawn over the whole app: a generate
        // that failed to start must be readable after a scroll, and it
        // outlives this screen.
        .onChange(of: images?.toast) { _, said in
            guard said != nil else { return }
            if let taken = images?.takeToast() { model.showToast(taken) }
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            images?.serverChanged()
            // The open picture is the old house's file, under a name the new
            // house can have too — [[read-after-await-wrong-key]] in the one
            // place a sheet can hold it. Close it rather than let it refetch
            // against a different engine.
            looking = nil
        }
        // One `.sheet`, as ``FilesView`` has one: several `.sheet` modifiers
        // on a single view do not reliably coexist in SwiftUI — the last
        // attached wins and the others never present.
        // No ``ImagesModel`` is handed to the sheet: it holds none, by
        // ``ImagesPictureSheet``'s own note, so this screen's `looking = nil`
        // on a house move is the whole mechanism that closes it.
        .sheet(item: $looking) { open in
            ImagesPictureSheet(name: open.id, model: model) {
                looking = nil
            }
        }
    }

    // MARK: - the page

    /// `PageHeader("Images", …)` — `ImagesScreen.kt:186`. Spelt out here the
    /// way ``FilesView``'s header is, because this app has no shared
    /// `PageHeader` and one invented for a single screen would be a shape
    /// guessed from one example.
    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(ImagesStrings.title)
                .font(.title2)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Text(ImagesStrings.subtitle)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The engine line's five states — `ImagesScreen.kt:190-212`, in
    /// Android's own `when` order, which ``ImagesEngineLine`` holds so that
    /// the order is testable without a view.
    @ViewBuilder private func engine(_ images: ImagesModel) -> some View {
        switch images.engineLine {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        case .running:
            HStack(spacing: 8) {
                // `live: true` is Android's pulse, which this app does not
                // animate anywhere yet (``ToneDot``'s note). The word beside
                // it is what carries the meaning either way.
                ToneDot(tone: .ok, live: true)
                Text(ImagesStrings.engineRunning)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        case .unavailable, .notInstalled, .offline:
            // All three are `EngineNote` (`:288-296`): a card with a title
            // and a muted line. ``ImagesEngineLine`` already has both
            // sentences, so the view does not choose any words.
            ImagesEngineNote(
                title: images.engineLine.title ?? "",
                sub: images.engineLine.sub ?? ""
            )
        }
    }

    /// The live lane — `:245`. No label above it: Android has none, and a
    /// heading over an empty lane would be a section that is usually empty.
    ///
    /// Read-only: the per-job cancel is admin-only and is not drawn (this
    /// type's own note). What the lane is for from a phone is seeing what the
    /// house is making and what failed, which is the half that works.
    @ViewBuilder private func lane(_ images: ImagesModel) -> some View {
        ForEach(images.laneJobs) { job in
            ImagesJobCard(job: job)
        }
    }

    /// `history`, its monospace label and its cards — `:247-268`.
    @ViewBuilder private func history(_ images: ImagesModel) -> some View {
        // ``ImagesModel/showsHistory``, not `!historyJobs.isEmpty` written out
        // again here: the guard is the one rule on this screen that lives in
        // a view, so it is the one rule a package test cannot reach, and
        // train X004's control widened it in place to `count >= 0`. Reading
        // the model's rule puts it where ImagesTests can hold it.
        if images.showsHistory {
            // `:248-255` — the label exists only when there is history, which
            // is why it is inside the guard rather than above it.
            Text(ImagesStrings.history)
                .font(.caption.monospaced())
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
                .padding(.top, 6)
            ForEach(images.historyJobs) { job in
                ImagesHistoryCard(
                    job: job,
                    tiles: images.tiles,
                    onOpen: { looking = ImagesOpenPicture(id: $0) }
                )
            }
        }
    }
}

/// Which picture the sheet is showing, as something `.sheet(item:)` can key
/// on.
///
/// The file name **is** the identity: two generations cannot produce the same
/// name on one house, and a different name is a different sheet.
struct ImagesOpenPicture: Identifiable, Equatable {
    let id: String
}
