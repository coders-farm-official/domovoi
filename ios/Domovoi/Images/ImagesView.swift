import DomovoiKit
import SwiftUI

/// Images: ask the house's own GPU for a picture, watch it being made, and
/// look at what it has made before.
///
/// `android/.../ui/screens/images/ImagesScreen.kt`, the screen that the
/// **Image Generation plugin** puts in the app rather than one of Domovoi's
/// own. `ScreenRouter.kt:67` reaches it through
/// `PluginScreenFrame(CAP_IMAGEGEN)`, which is ``ImagesPluginBand`` here, and
/// the route itself is gated on the `imagegen` capability
/// (``Route/requiredCapability``) — a house without the plugin never gets
/// this far and sees ``Route/unavailableNote(_:)`` instead.
///
/// ### What is here
///
/// Whether the engine is installed, running, or neither; a prompt and a
/// picker over the models this house actually has, and one `generate`; a lane
/// of what is being made right now, with its progress and a cancel; and the
/// history, each generation with its first three pictures, the prompt and the
/// settings it was made with. Tapping a picture opens it, and the share sheet
/// is the way out to Files, Photos or AirDrop. A generation can be deleted,
/// after being asked.
///
/// ### What is not, and why there is no control for it
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
/// open nothing, which is the control this app is built not to draw; the
/// delete confirmation still counts every image, because that is what is
/// being deleted. DECISIONS.
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
/// Every write — generate, cancel, delete — is ``ImagesModel``'s, never a
/// row's. A row in a `LazyVStack` is torn down as it scrolls out and would
/// take its `Task` with it, cancelling the write mid-flight. Android obeys
/// the same rule for the same control: its cancel runs on `app.scope`
/// (`ImagesScreen.kt:349`).
struct ImagesView: View {

    let model: ShellModel

    @State private var images: ImagesModel?

    /// `onAppear`/`onDisappear`, for ``HomeView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy.
    @State private var visible = false

    /// Which generated picture is open, by its file name.
    @State private var looking: ImagesOpenPicture?

    var body: some View {
        ZStack {
            screen
                // **Hidden, not merely behind** — ``SettingsPlaceholder``'s
                // note: `.isModal` stops VoiceOver navigating out of the
                // card, but everything under the 35% scrim is still rendered
                // and still reported, which is why Apple's audit keeps
                // finding `Contrast failed` against text behind a dialog.
                // Nothing behind a confirmation is content.
                .accessibilityHidden(images?.confirmingDelete != nil)
            if let images, let sentence = images.deleteBody {
                DialogCard {
                    ImagesDeleteCard(
                        sentence: sentence,
                        onCancel: { images.confirmingDelete = nil },
                        onConfirm: { images.confirmDelete() }
                    )
                }
            }
        }
    }

    private var screen: some View {
        ScrollView {
            // 10-point gaps and 16-point padding, which is the Kotlin's
            // `LazyColumn` (`ImagesScreen.kt:184`).
            LazyVStack(alignment: .leading, spacing: 10) {
                header
                if let images {
                    engine(images)
                    // `:215` — the prompt, the picker and the button exist
                    // only once the engine is installed. Not merely disabled:
                    // there is nothing to pick and nowhere to send it.
                    if images.state.data?.installed == true {
                        ImagesComposer(images: images)
                    }
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
        // tallest scroll view in the tree, which is the page at an ordinary
        // type size and is **not** the page at AX5: the band above it leaves
        // the page a 102-point window (DECISIONS, "At an accessibility size
        // the band leaves the page a 102-point window") while the shell's
        // phone bar is a 146-point scroll view of its own. Train S004 flicked
        // the phone bar instead and the flick landed on `music`, so
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
    @ViewBuilder private func lane(_ images: ImagesModel) -> some View {
        ForEach(images.laneJobs) { job in
            ImagesJobCard(job: job) { images.cancel(job) }
        }
    }

    /// `history`, its monospace label and its cards — `:247-268`.
    @ViewBuilder private func history(_ images: ImagesModel) -> some View {
        if !images.historyJobs.isEmpty {
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
                    onOpen: { looking = ImagesOpenPicture(id: $0) },
                    onDelete: { images.confirmingDelete = job }
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

/// The delete confirmation's contents — `ConfirmDialog` at
/// `ImagesScreen.kt:272-284`, destructive.
///
/// A ``DialogCard`` rather than a `confirmationDialog`, which is the shape
/// this app's other destructive ask already uses
/// (``SettingsPlaceholder``'s note): the body is a sentence that names a
/// count, it has to wrap at an accessibility size, and `DialogCard` is itself
/// a `ScrollView` over the dim so it cannot centre off the top of the screen.
struct ImagesDeleteCard: View {

    /// ``ImagesModel/deleteBody``: the sentence with the image count and the
    /// plural already in it. Not named `body`, which is the view's own.
    let sentence: String
    let onCancel: () -> Void
    let onConfirm: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(ImagesStrings.deleteTitle)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
            Text(sentence)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 12) {
                Button(ImagesStrings.dialogCancel, action: onCancel)
                    .buttonStyle(.bordered)
                    .frame(minHeight: 44)
                Button(ImagesStrings.delete, action: onConfirm)
                    .buttonStyle(.borderedProminent)
                    // Destructive (`:282`), and `.tint` rather than a red
                    // label: a word in `.red` as text is 3.55:1 and this app
                    // draws text in the ink tokens only. The prominent
                    // button's own fill-and-ink pair is what carries it.
                    .tint(Color.errInk)
                    .frame(minHeight: 44)
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        }
    }
}
