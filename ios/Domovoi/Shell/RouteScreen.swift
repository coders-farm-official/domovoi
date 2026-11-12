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
            // **No band wrapper here any more.** `ScreenRouter.kt:67` wraps
            // `ImagesScreen()` in `PluginScreenFrame(CAP_IMAGEGEN)`, and this
            // slot wrapped ``ImagesView`` in `ImagesPluginBand` to match. That
            // put the band outside the page's scroll view, where it kept 256
            // of 852 points at AX5 and left the page a 102-point window
            // (train S001). Owner answer 29 asks a screen's chrome to scroll
            // away at those sizes, so the band is ``ImagesView``'s own first
            // row now, as Stations' already was — a plain `PluginBandView`,
            // which pads no sides of its own. A house whose manifest has no imagegen
            // plugin still draws the content bare (`PluginFrame.kt:98-101`):
            // that is a nil ``PluginBand`` inside the same view.
            // ``ImagesView`` says what is left out and why there is no
            // control for it.
            ImagesView(model: model)
        case .news:
            // slot: news
            // ``NewsView`` says what is left out and why there is no control
            // for any of it. **No capability gate**: `/api/news` is core, so
            // this renders on every house (`Route.swift:83-89` gates
            // `stations` and `images` only).
            NewsView(model: model)
        case .people:
            // slot: people
            // ``PeopleView`` says what is left out and why there is no
            // control for any of it — the rename and the forget Android draws
            // and a phone cannot do, and the voice enrolment this screen
            // never had. Since the nav regroup `navigate(.people)` resolves to
            // Settings → Household → People (``settingsTabFor(_:)``), so, as
            // with Rooms below, this branch is only reached by a direct
            // `go(.people)` and stays filled so that is not a dead screen.
            PeopleView(model: model)
        case .satellites:
            // slot: satellites
            // ``RoomsView`` says what is left out and why there is no control
            // for any of it. `navigate(.satellites)` resolves to Settings →
            // Rooms (``settingsTabFor(_:)``), so this branch is only ever
            // reached by a direct `go(.satellites)` — filled with the same
            // view so that it is not a dead screen.
            RoomsView(model: model)
        case .calendar:
            // slot: calendar
            // ``CalendarScreenView`` says what is left out and why there is
            // no control for any of it — the week grid, "sync now", and the
            // all-day flag no API can read.
            CalendarScreenView(model: model)
        case .lists:
            // slot: lists
            // ``ListsScreenView`` says what is left out and why. No capability
            // gate: `/api/lists` is core (`Route.swift` gates `stations` and
            // `images` only), and the local shell never offers it
            // (``localTabs(phoneCanChat:sharedScreen:savedBooks:)``).
            ListsScreenView(model: model)
        case .stations:
            // slot: stations
            // ``StationsPhonePlayback`` over the app's one player (D46/D49) —
            // the one argument this slot's comment said the merge that brings
            // phone audio would change, and it is the only line that changed
            // here. Nil inside it — a build with no audio engine, which is
            // not a state any phone reaches — puts the screen back to drawing
            // **no play affordance at all**, which is what it was built to do.
            //
            // Re-read on every body evaluation here, and
            // ``StationsModel/use(_:)`` is what carries it into a model built
            // on an earlier visit (review 2 of the 2026-11-03 batch).
            //
            // Reading ``ShellModel/player`` from a `body` is safe and
            // deliberate: the player's slot is `@ObservationIgnored`, and
            // building one touches no audio session — only
            // ``PlayerModel/play(_:startAt:resumeSec:speed:)`` does.
            StationsView(model: model, playback: StationsPhonePlayback(player: model.player))
        case .manual:
            // slot: manual
            // ``ManualView`` says what is left out and why — both omissions
            // are ornament, because nothing on this screen writes.
            ManualView(model: model)
        case .containers:
            // slot: containers
            HealthRouteScreen(model: model)
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
/// The three levels, the search and the fifteen panels are a later batch's.
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

    /// **``ShellModel/settingsPlace``, not a `@State` here.** It was a
    /// `@State`, and a scene-phase bounce — the house's own alert banner is
    /// one — rebuilt this destination and threw the reader back to the
    /// Settings index with no action of their own. That property's note has
    /// the I003 measurement and why Android's "a later visit starts at the
    /// index" still holds.
    ///
    /// Computed rather than a `@Bindable`, so that every `place = …` below
    /// reads exactly as it did; `nonmutating` because what it writes to is a
    /// reference this view only holds.
    private var place: SettingsPlace {
        get { model.settingsPlace }
        nonmutating set { model.settingsPlace = newValue }
    }

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
            //
            // **Kept, against the shell unit's DECISIONS §7.** That asked for
            // this line as redundant, on the ground that ``DialogCard``
            // presents a `fullScreenCover` now (answer 6) and UIKit takes the
            // presenting controller's views out of the window — true on the
            // path where the presentation arrives. It does not hold on the
            // other path: ``DialogPresentationGuard`` draws the card **inline**
            // when the cover is dropped, and inline is the pre-answer-6 shape,
            // with this panel behind the card and sampled through the 35%
            // scrim (`ConnectionDialogView.swift:460-471`). That path is the
            // whole reason the guard exists, so the line is what keeps it from
            // handing back the two `Contrast failed` findings it was added
            // for. It costs a boolean.
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
            // The request is TAKEN, so a deep link's section is honoured once
            // and not on every later arrival here. An unknown key lands on
            // the index rather than nowhere.
            //
            // **What makes a later visit start at the index is no longer this
            // line.** It used to be a side effect of `place` being a `@State`
            // that a new push rebuilt; now that the place is held on
            // ``ShellModel`` so it survives a scene bounce,
            // ``ShellModel/navigate(_:)`` resets it on a fresh visit
            // (`SettingsScreen.kt:233-236`). This `.onAppear` also runs after
            // such a bounce, which is exactly why it must not reset anything
            // itself — it would undo the fix.
            //
            // **Kept as asked, filtered when drawn** (``shown``): a paired
            // phone counts as a shared screen until the server's registration
            // answer lands, so a link to People in that first moment draws
            // Household and then People once the answer says this is a
            // phone — where filtering here would have parked it on
            // Household for good. Android filters at the request
            // (`SettingsScreen.kt:265`); the kitchen tablet sees the same
            // either way.
            if let asked = model.takeSettingsTab() {
                place = settingsPlace(of: asked)
            }
        }
        // **A request that arrives while Settings is already open** — a
        // People tab on the bar tapped from inside Settings, a Health row's
        // link — is taken here, because ``NavigationState/go(_:)`` refuses a
        // move to the open route and `.onAppear` does not fire again. Android's
        // `LaunchedEffect(request.tab)` (`SettingsScreen.kt:269-272`), with its
        // rule: a key Settings does not know leaves it where it is.
        .onChange(of: model.navigation.settingsTab) { _, asked in
            guard asked != nil, let taken = model.takeSettingsTab() else { return }
            let landed = settingsPlace(of: taken)
            if landed != .index { place = landed }
        }
    }

    /// This phone is a shared screen: People is left out of Household, and a
    /// link to it opens the group instead (``SettingsSection/shownOn(sharedScreen:)``).
    private var sharedScreen: Bool { model.visibility.sharedScreen }

    /// ``place`` as it is drawn: Android's `placeOf(at, shared)`, re-read on
    /// every composition (`SettingsScreen.kt:274`), so a section a shared
    /// screen hides is its group — and a phone marked shared while People is
    /// open leaves People.
    private var shown: SettingsPlace {
        settingsPlace(of: place.key, sharedScreen: sharedScreen)
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
            // `back to Rooms & devices` button (Household's old name). A `Text`
            // or a button label with no `fixedSize` can be squeezed flat by
            // its parent, and these are section names and group names —
            // "Phones & browsers", "Chat & AI" — that get long at an
            // accessibility size.
            //
            // `minimumScaleFactor` is the other answer and is rejected
            // throughout this app: it makes text smaller for the person who
            // asked for bigger.
            //
            // **And every one of them is 44 pt tall**, which is Kamron's
            // answer 57 ("fix it once in the shell") read the way the
            // measurement left it. A bare `Button("some words")` is drawn
            // 20.3 pt tall here, and train I004 returned one
            // `Hit area is too small` finding that Apple could not attribute;
            // the rooms unit got to this file by elimination (the I002/I004
            // roster tree diff is the same element set 26.6 pt of scroll
            // apart, and the foot button is the only element whose
            // window-occlusion changed between them — it traded I002's
            // `Contrast failed` for I004's hit-area finding; `switch server`
            // at 121 x 22.3 and the 20-pt text fields are eliminated because
            // they are also on the opened room's screen, which audits
            // `rest = 0`; and it is the only `Hit area is too small` in all
            // 432 cases). So the defect is the control's *size*, not a colour
            // and not the case's scroll. `contentShape` is what makes the
            // grown frame the tap target rather than just the words in it.
            switch shown {
            case .index:
                ForEach(SettingsGroup.allCases, id: \.self) { group in
                    Button(group.label) { place = .group(group) }
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(minHeight: 44)
                        .contentShape(.rect)
                }
            case .group(let group):
                Text(group.label)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(sections(of: group, sharedScreen: sharedScreen), id: \.key) { section in
                    Button(section.label) { place = .section(section) }
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(minHeight: 44)
                        .contentShape(.rect)
                }
                Button(ShellStrings.allSettings) { place = shown.up ?? .index }
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
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
                Button(ShellStrings.backTo(section.group.label)) { place = shown.up ?? .index }
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
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
        case "people":
            // slot: people
            // Settings → Household → People since the nav regroup: the People
            // screen embedded, as Android's `PeopleScreen(embedded = true)`
            // (`SettingsScreen.kt:290`) — Settings draws the heading and
            // already scrolls, so the screen draws only its counts and no
            // scroll view of its own. A shared screen never lists it
            // (``SettingsPlaceholder``'s `shown`).
            PeopleView(model: model, embedded: true)
        case "rooms":
            // slot: rooms
            // `embedded:` is Android's own flag (`SatellitesScreen.kt:80`):
            // the section already has the heading, and Settings already
            // scrolls, so the screen draws neither of its own here.
            RoomsView(model: model, embedded: true)
        case "printers":
            // slot: printers
            PrintersPanelView(model: model)
        case "app-layout":
            // slot: app-layout
            SystemSettingsLayoutPanel(model: model)
        case "wake-words":
            // slot: wake-words
            VoiceSettingsDashboardPanel(model: model, section: section)
        case "voices":
            // slot: voices
            VoiceSettingsPanel(model: model)
        case "greetings":
            // slot: greetings
            VoiceSettingsDashboardPanel(model: model, section: section)
        case "models":
            // slot: models
            // Dashboard-only: every Models write is admin-gated and 401s a
            // phone (``AiSettingsDashboardPanel``).
            AiSettingsDashboardPanel(model: model, section: section)
        case "chat":
            // slot: chat
            AiSettingsChatPanel(model: model)
        case "internet":
            // slot: internet
            // Dashboard-only, as Models is: `GET /api/config/internet` is the
            // panel's only data and is `require_admin_read`.
            AiSettingsDashboardPanel(model: model, section: section)
        case "voice-history":
            // slot: voice-history
            VoiceSettingsDashboardPanel(model: model, section: section)
        case "about":
            // slot: about
            SystemSettingsAboutPanel(model: model)
        case "health":
            // slot: health
            HealthPanelView(model: model)
        case "advanced":
            // slot: advanced
            SystemSettingsAdvancedPanel(model: model)
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
