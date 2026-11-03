import DomovoiKit
import SwiftUI

/// One destination of the graph.
///
/// The counterpart of `android/.../ui/screens/ScreenRouter.kt`: a `switch`
/// over the route, with the capability check in front of it. **The screens
/// themselves are later batches'** — most of Settings — so each of those is a
/// marked placeholder that names what belongs there, and nothing here pretends
/// to be one. Home, Videos, Files, Chat and the library half of Music are
/// built.
///
/// The one piece of real behaviour is the check at the top, and it is
/// ported rather than invented: a route a link reached that the server does
/// not offer gets a **quiet note saying why**, never a dead screen
/// (`ScreenRouter.kt:49`, ``Route/unavailableNote(_:)``). Chat hidden because
/// the server's AI features are off says so; anything else is a plugin that
/// is not installed.
struct RouteScreen: View {

    let model: ShellModel
    let route: Route

    /// Which side of the server-or-device switch each page is showing.
    ///
    /// **Not `@State`.** This view is created fresh for every pushed
    /// destination (`WorkspaceView.swift:159`), so a `@State` here would last
    /// as long as one visit to one page — and Android's `LocalSourceChoices`
    /// is provided by the **workspace** shell and lasts as long as the server
    /// does (`YourDevice.kt:57`). ``ShellModel`` is do-not-edit, so the
    /// lifetime is kept by ``PhoneSourceChoicesStore``, which is keyed to the
    /// address for exactly that reason.
    private var sources: PhoneSourceChoices {
        PhoneSourceChoicesStore.of(server: model.inputs.serverURL)
    }

    var body: some View {
        if !route.visibleWith(model.visibility) {
            let note = route.unavailableNote(model.capabilities)
            VStack(spacing: 8) {
                Text(note.title).font(.headline)
                Text(note.detail)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(24)
        } else {
            screen
        }
    }

    /// One slot per route.
    ///
    /// **Each unbuilt route is three lines — `case`, a `// slot:` comment and
    /// its placeholder — and a branch that builds a screen replaces only the
    /// placeholder line.** That keeps an unchanged line between any two
    /// branches' edits, so the overnight branches of 2026-11-02, each building
    /// a different screen in parallel, merge here without a conflict. Keep
    /// the shape when filling a slot: add lines under the `// slot:` comment,
    /// never move or delete the comment.
    @ViewBuilder private var screen: some View {
        switch route {
        case .home:
            // I1-D part 1 replaces exactly one branch, which is Android's
            // shape too: `ScreenRouter.kt:57` → `Route.Home -> HomeScreen(…)`.
            HomeView(model: model)
        case .music:
            // Only the library half is built; ``MusicView`` says what is left
            // out and why there is no control for any of it. **Wrapped, not
            // changed**: the `server:` closure holds exactly the line that
            // was here (`ScreenRouter.kt:60`).
            PhoneSourceSwitchView(
                page: .music,
                choices: sources,
                serverTitle: model.serverChoice.title,
                server: { MusicView(model: model) },
                device: { PhoneDeviceMusicView(model: model) }
            )
        case .videos:
            // ``VideosView`` says what is left out and why there is no
            // control for any of it (`ScreenRouter.kt:64`).
            PhoneSourceSwitchView(
                page: .videos,
                choices: sources,
                serverTitle: model.serverChoice.title,
                server: { VideosView(model: model) },
                device: { PhoneDeviceVideosView(model: model) }
            )
        case .files:
            // ``FilesView`` says what is left out and why there is no control
            // for any of it — including the delete Android draws and cannot
            // do from a phone (`ScreenRouter.kt:73`).
            PhoneSourceSwitchView(
                page: .files,
                choices: sources,
                serverTitle: model.serverChoice.title,
                server: { FilesView(model: model) },
                device: { PhoneDeviceFilesView(model: model) }
            )
        case .chat:
            // ``ChatView`` says what is left out and why. **The capability
            // check above is Chat's AI check**: a house with
            // `features.ai == false` never reaches this branch and gets
            // ``Route/unavailableNote(_:)``'s "chat is turned off". What
            // ``ChatModel/aiEnabled`` covers is the switch thrown while this
            // screen is already open.
            ChatView(model: model)
        case .settings:
            SettingsPlaceholder(model: model)
        case .podcasts:
            // slot: podcasts
            // ``PodcastsView`` says what is left out and why there is no
            // control for any of it — including playing an episode, which
            // has an engine on this branch and nowhere for one to live.
            PodcastsView(model: model)
        case .audiobooks:
            // slot: audiobooks
            // The **server** half. ``AudiobooksView`` says what is left out
            // and why there is no control for any of it; the phone's own
            // shelf is the PhoneMedia item's, which replaces this one line
            // again with its source switch wrapping the same view.
            AudiobooksView(model: model)
        case .images:
            // slot: images
            ScreenPlaceholder(route: route, onThisPhone: false)
        case .news:
            // slot: news
            ScreenPlaceholder(route: route, onThisPhone: false)
        case .people:
            // slot: people
            ScreenPlaceholder(route: route, onThisPhone: false)
        case .satellites:
            // slot: satellites
            ScreenPlaceholder(route: route, onThisPhone: false)
        case .calendar:
            // slot: calendar
            ScreenPlaceholder(route: route, onThisPhone: false)
        case .stations:
            // slot: stations
            // ``StationsNoPlayback`` until the `player` item's witness lands:
            // the screen then draws **no play affordance at all**, and
            // ``StationsView`` says so on the page. This one argument is the
            // whole of what the merge that brings phone audio changes here.
            StationsView(model: model, playback: StationsNoPlayback())
        case .manual:
            // slot: manual
            ScreenPlaceholder(route: route, onThisPhone: false)
        case .containers:
            // slot: containers
            ScreenPlaceholder(route: route, onThisPhone: false)
        }
    }
}

/// A screen a later batch builds.
///
/// It says which screen it is and that it is not built, because an empty page
/// in a shell that is otherwise working is the kind of thing that gets
/// mistaken for a loading failure.
struct ScreenPlaceholder: View {

    let route: Route
    /// The local shell's copy of a screen (the phone's own media) rather than
    /// the server's.
    let onThisPhone: Bool

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: route.systemImage)
                .font(.largeTitle)
                .foregroundStyle(Color.fgMuted)
            Text(route.label.lowercased())
                .font(.title3)
            Text(onThisPhone ? "on this phone — not built yet" : "not built yet")
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
    }
}

/// Settings, far enough to show that the **link side** of it works: a link
/// naming a section — `"rooms"`, or the old `"satellites"` and `"connection"`
/// — lands on that section, which is ``settingsPlace(of:)`` resolving it
/// through the alias table.
///
/// The three levels, the search and the fourteen panels are a later batch's.
/// What this draws is where the request landed, and takes it, so coming back
/// to Settings later starts at the index.
///
/// ### The screen scrolls, and the panel inside it does not
///
/// Found during the audit work and fixed in 3b. This was a `VStack` ending in
/// a `Spacer()` with **no `ScrollView`**, while ``PhonesAndBrowsersPanel`` was
/// a `ZStack { ScrollView { … } }` — the screen that should scroll did not and
/// the panel that should not did, which sliced the `Household token` card
/// across the panel's viewport edge with the back link pinned underneath it.
/// It is visible in a screenshot.
///
/// ### Where the forget dialog went, and why
///
/// **Hoisted to this screen, as a ``DialogCard`` in a `ZStack` at the root,
/// rather than replaced with a system `confirmationDialog`.**
///
/// It had to move: the panel's `ZStack` was screen-sized only *because* its
/// `ScrollView` filled the screen, so removing that would have let a centred
/// dialog land off-screen — exactly the bug being fixed, one level down. Both
/// homes were defensible, and `confirmationDialog` would have scaled for
/// Dynamic Type for nothing and always been on screen.
///
/// The card won on what it has to show. ``ForgetActiveServerAsk`` is a
/// forty-word paragraph **and** the `pinned:` / `it now proves:` fingerprint
/// lines, and those lines exist to be compared character by character with
/// what the dashboard shows: they are monospaced and selectable, and a review
/// already found this card drawing the prose in monospace and called it the
/// hardest-to-read form of the most consequential dialog in the app. A
/// `confirmationDialog`'s `message:` is one small centred block with no
/// monospace, no selection and no second paragraph, so moving there would
/// have traded a layout bug for a legibility one on the fingerprints.
///
/// It is reachable at every type size because ``DialogCard`` is itself a
/// `ScrollView` over a full-screen dim: the card grows, the backdrop does not
/// move, and at AX5 the content scrolls rather than centring off the top.
struct SettingsPlaceholder: View {

    let model: ShellModel
    @State private var place: SettingsPlace = .index

    /// **The panel's, held here.** The dialog is drawn at this level, so the
    /// state that opens it is too — a binding rather than a second copy,
    /// because two booleans for one dialog is how a dialog comes to be shown
    /// with nothing to confirm.
    @State private var askingToForget = false

    var body: some View {
        ZStack {
            // **Hidden, not merely behind.** `.isModal` on ``DialogCard``
            // stops VoiceOver *navigating* out of the card, but the panel is
            // still in the tree and still rendered under a 35% scrim, which
            // is why Apple's audit kept reporting two `Contrast failed`
            // against text behind the dialog after the trait was added. They
            // were true: that text is unreadable while the dialog is up.
            // Nothing behind a confirmation is content, so it says so.
            screen
                .accessibilityHidden(askingToForget)
            if askingToForget, let ask = model.forgetActiveAsk() {
                DialogCard {
                    ForgetServerCard(
                        ask: ask,
                        onCancel: { askingToForget = false },
                        onConfirm: {
                            askingToForget = false
                            // The forget asks for the picker as it goes
                            // (``ShellModel/forgetTheActiveServer()``), which
                            // is the only way the dialog can outlive the
                            // screen that opened it: Settings is in the
                            // workspace, and the workspace is gone the
                            // instant the server is.
                            model.forgetTheActiveServer()
                        }
                    )
                }
            }
        }
    }

    private var screen: some View {
        ScrollView {
            content
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear {
            // The request is TAKEN, so a later visit starts at the index
            // (`SettingsScreen.kt:233-236`). An unknown key lands on the
            // index rather than nowhere.
            if let asked = model.takeSettingsTab() {
                place = settingsPlace(of: asked)
            }
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(ShellStrings.settingsTitle).font(.title2)
            Text(ShellStrings.settingsSub)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // **Every label here wraps.** Apple's audit failed this screen on
            // "Text of this element may be clipped at larger Dynamic Type
            // sizes" and, once the element was legible in the log, named two
            // of these: the `Phones & browsers` heading and the
            // `back to Rooms & devices` button. A `Text` or a button label
            // with no `fixedSize` can be squeezed flat by its parent, and
            // these are section names and group names — "Rooms & devices",
            // "Chat & AI" — that get long at an accessibility size.
            //
            // `minimumScaleFactor` is the other answer and is rejected
            // throughout this app: it makes text smaller for the person who
            // asked for bigger.
            switch place {
            case .index:
                ForEach(SettingsGroup.allCases, id: \.self) { group in
                    Button(group.label) { place = .group(group) }
                        .fixedSize(horizontal: false, vertical: true)
                }
            case .group(let group):
                Text(group.label)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(sections(of: group), id: \.key) { section in
                    Button(section.label) { place = .section(section) }
                        .fixedSize(horizontal: false, vertical: true)
                }
                Button(ShellStrings.allSettings) { place = place.up ?? .index }
                    .fixedSize(horizontal: false, vertical: true)
            case .section(let section):
                Text(section.label)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(section.sub)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                SettingsSectionScreen(
                    model: model,
                    section: section,
                    askingToForget: $askingToForget
                )
                Button(ShellStrings.backTo(section.group.label)) { place = place.up ?? .index }
                    .fixedSize(horizontal: false, vertical: true)
            }
            // **No `Spacer()`.** It was here to push the content up in a
            // `VStack` that filled the screen; inside a `ScrollView` a
            // `Spacer` has no space to take and the `frame(maxHeight:)` on the
            // scroll view is what fills the screen now.
        }
    }
}

/// One slot per Settings section — the same three-line shape as
/// ``RouteScreen``'s, for the same reason: parallel branches each fill a
/// different section and must merge here without a conflict. Fill a slot by
/// replacing its ``SettingsSectionNotBuilt()`` line; never move a
/// `// slot:` comment.
struct SettingsSectionScreen: View {

    let model: ShellModel
    let section: SettingsSection
    @Binding var askingToForget: Bool

    var body: some View {
        switch section.key {
        case "phones-and-browsers":
            // The one section built before 2026-11-02. Its first three cards
            // are part 3's; §6.5's four are a batch of their own, as
            // ``PhonesAndBrowsersPanel`` says and the report sizes.
            PhonesAndBrowsersPanel(model: model, askingToForget: $askingToForget)
        case "rooms":
            // slot: rooms
            SettingsSectionNotBuilt()
        case "printers":
            // slot: printers
            PrintersPanelView(model: model)
        case "app-layout":
            // slot: app-layout
            SettingsSectionNotBuilt()
        case "wake-words":
            // slot: wake-words
            SettingsSectionNotBuilt()
        case "voices":
            // slot: voices
            SettingsSectionNotBuilt()
        case "greetings":
            // slot: greetings
            SettingsSectionNotBuilt()
        case "models":
            // slot: models
            SettingsSectionNotBuilt()
        case "chat":
            // slot: chat
            SettingsSectionNotBuilt()
        case "internet":
            // slot: internet
            SettingsSectionNotBuilt()
        case "voice-history":
            // slot: voice-history
            SettingsSectionNotBuilt()
        case "about":
            // slot: about
            SettingsSectionNotBuilt()
        case "health":
            // slot: health
            SettingsSectionNotBuilt()
        case "advanced":
            // slot: advanced
            SettingsSectionNotBuilt()
        default:
            SettingsSectionNotBuilt()
        }
    }
}

/// What an unbuilt section says — the words this file drew inline before the
/// sections had slots.
struct SettingsSectionNotBuilt: View {
    var body: some View {
        Text("not built yet")
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
    }
}
