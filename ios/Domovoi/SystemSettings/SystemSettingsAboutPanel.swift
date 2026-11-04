import DomovoiKit
import SwiftUI

/// Settings → **About & updates**: what Domovoi is, this app's version, the
/// manual, a help chat, this phone's problem reports, privacy and the licences.
///
/// A port of `AboutPanel` (`android/.../ui/screens/settings/SettingsScreen.kt:467-540`),
/// read on 2026-11-02, with its cards in Android's order: About Domovoi, Ask
/// Domovoi for help, problem reports, privacy, licences.
///
/// ### What is not, and why there is no control for it
///
///  - **The app-update card** (`AboutAppUpdateCard`, `update/AppUpdates.kt:19-22`
///    → `/api/devices/android-app`, verified at `web/backend/api/devices.py:404`
///    and `domovoi/main.py:1962`). An App Store app does not fetch its own
///    build, and the route it would ask serves an APK. No card, no "check for
///    updates", and **no App Store link** either: the app is not published, so
///    the link would open a page that does not exist. There is no iOS
///    counterpart route and there will not be one.
///  - **Developer options and the seven-tap gesture**
///    (`DeveloperOptionsSection.kt:60-130`, `data/DeveloperOptions.kt:96-116`).
///    Android's own rule is that they do not exist where there is nothing
///    under them, and the one tool it puts there is the on-device assistant
///    benchmark, which iOS has no assistant for (`Route.swift:169-173`). So
///    the version line is plain text, no tap counts, and there is no section
///    and no switch. ``systemSettingsAppVersionText(version:debugBuild:)`` is
///    still ported, because the line is still drawn.
///  - **The 64-bit assistant note** (`AssistantIn64BitAppNote`,
///    `SettingsScreen.kt:536`). It is a statement about which Android APK you
///    installed.
///  - **The phone-chat branch for help** (`AskForHelp.kt:82-85`). No on-device
///    engine, so ``systemSettingsHelpEntry(connected:aiEnabled:)`` has two
///    answers rather than three.
struct SystemSettingsAboutPanel: View {

    let model: ShellModel

    /// Built in `task`, like every model in this app: it needs the container,
    /// and a `@State` initialiser runs before the view has one.
    @State private var help: SystemSettingsHelpModel?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            aboutCard
            helpCard
            SystemSettingsProblemsCard(model: model)
            SystemSettingsPrivacyCard()
            SystemSettingsLicencesCard()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task {
            guard help == nil else { return }
            help = SystemSettingsHelpModel(
                serverKey: { [container = model.container] in
                    AppContainer.serverKey(container.store.serverURL)
                },
                // **The thrown type is spelled out.** A closure literal
                // infers `throws` as `any Error`, not as the parameter's
                // `throws(DomovoiError)`, so without this signature it does
                // not convert — `error: invalid conversion of thrown error
                // type 'any Error' to 'DomovoiError'`, which is the same
                // typed-throws trap this item's package tests hit at
                // `27652c11`.
                createThread: { [client = model.container.client] ()
                    async throws(DomovoiError) -> ChatThread in
                    try await client.post(
                        ChatPath.newThread,
                        body: systemSettingsHelpThreadBody(),
                        as: ChatThread.self
                    )
                },
                accept: { [container = model.container] key, thread in
                    container.requestChatThread(
                        serverKey: key,
                        thread: thread.id,
                        title: systemSettingsHelpTitle
                    )
                }
            )
        }
    }

    // MARK: - About Domovoi (SettingsScreen.kt:478-531)

    private var aboutCard: some View {
        PanelCard(
            title: SystemSettingsStrings.aboutTitle,
            sub: SystemSettingsStrings.aboutSub
        ) {
            VStack(alignment: .leading, spacing: 10) {
                paragraph(SystemSettingsStrings.aboutFirst, strong: SystemSettingsStrings.aboutFirstStrong)
                paragraph(SystemSettingsStrings.aboutSecond, strong: nil)
                paragraph(SystemSettingsStrings.aboutThird, strong: SystemSettingsStrings.aboutThirdStrong)
                // The version line, and nothing tappable about it — see this
                // file's heading. `systemSettingsVersionLine()` reads
                // `CFBundleShortVersionString`, so a line reading
                // `app version unknown` means the bundle key is wrong, which
                // is what the UI case checks for.
                Text(systemSettingsVersionLine())
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Text(SystemSettingsStrings.serverBuildLine)
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                manualButton(SystemSettingsStrings.openTheUserManual, symbol: "book")
            }
        }
    }

    /// One About paragraph, with the word Android draws semibold inside it.
    ///
    /// `Text + Text` rather than an `AttributedString`: the three pieces stay
    /// one accessibility element whose label is the whole sentence, which is
    /// what a screen reader should read and what a UI case matches a prefix
    /// of. Android's own emphasis is `FontWeight.SemiBold` at full-strength
    /// `fg` against `fgMuted` prose (`SettingsScreen.kt:470`), and that is
    /// what the two colours below are.
    private func paragraph(_ whole: String, strong: String?) -> some View {
        var text = Text(whole).foregroundStyle(Color.fgMuted)
        if let strong, let range = whole.range(of: strong) {
            text = Text(String(whole[whole.startIndex..<range.lowerBound]))
                .foregroundStyle(Color.fgMuted)
                + Text(strong)
                .foregroundStyle(Color.primary)
                .fontWeight(.semibold)
                + Text(String(whole[range.upperBound...]))
                .foregroundStyle(Color.fgMuted)
        }
        return text
            .font(.callout)
            .fixedSize(horizontal: false, vertical: true)
    }

    // MARK: - Ask Domovoi for help (AskForHelp.kt:45-96)

    /// Android's `AskForHelpCard`, with ``SystemSettingsHelpEntry`` deciding
    /// which of the two things the one button does.
    ///
    /// The entry is recomputed on every draw, from ``ShellModel/connected``
    /// and the capability manifest — Android's `collectAsState` on both
    /// (`AskForHelp.kt:50-51`) — so a house whose AI switch goes off while
    /// this card is open changes the button rather than failing on the tap.
    @ViewBuilder private var helpCard: some View {
        let entry = systemSettingsHelpEntry(
            connected: model.connected,
            aiEnabled: model.capabilities.aiEnabled
        )
        PanelCard(title: SystemSettingsStrings.helpTitle, sub: SystemSettingsStrings.helpSub) {
            VStack(alignment: .leading, spacing: 8) {
                switch entry {
                case .manual:
                    manualButton(SystemSettingsStrings.openTheUserManual, symbol: "questionmark.circle")
                case .serverChat:
                    Button {
                        guard let help else { return }
                        Task {
                            await help.askForHelp()
                            // Taken, so a redraw cannot push Chat twice —
                            // Android removes its intent extra for the same
                            // reason (`MainActivity.kt:140`).
                            if help.takeShouldOpenChat() { model.navigate(.chat) }
                        }
                    } label: {
                        buttonFace(SystemSettingsStrings.helpAsk, symbol: "bubble.left")
                    }
                    // Nil only for the instant before `task` runs, and the
                    // model refuses a second overlapping start itself —
                    // a disabled button is not a guarantee under VoiceOver's
                    // own activation.
                    .disabled(help == nil || help?.starting == true)
                }
                if let said = help?.error {
                    Text(said)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isStaticText)
                }
            }
        }
    }

    /// `navigate(Route.Manual)` (`AskForHelp.kt:86`, `SettingsScreen.kt:525`).
    ///
    /// **The Manual screen itself is another item's slot** (`RouteScreen.swift`,
    /// `// slot: manual`), so today this lands on a page that says it is not
    /// built. The button is drawn anyway, and that is not the
    /// no-misleading-controls rule being bent: this control does exactly what
    /// it says — it goes to the Manual screen — and Home's "everything" grid
    /// already offers the same destination (`everythingRoutes`,
    /// `Route.swift:124-127`). Leaving it out of About while Home keeps it
    /// would be a divergence with nothing behind it. Noted in this item's
    /// `DECISIONS.md`.
    private func manualButton(_ title: String, symbol: String) -> some View {
        Button {
            model.navigate(.manual)
        } label: {
            buttonFace(title, symbol: symbol)
        }
    }

    /// A glyph and a wrapping label, 44 points tall.
    ///
    /// The glyph carries no label of its own — Android's
    /// `contentDescription = null` on each of these (`SettingsScreen.kt:521`,
    /// `AskForHelp.kt:91`) — because the words beside it already say what the
    /// button does.
    private func buttonFace(_ title: String, symbol: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: symbol)
                .imageScale(.small)
            Text(title)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(minHeight: 44)
    }
}
