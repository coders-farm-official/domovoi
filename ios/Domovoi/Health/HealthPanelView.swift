import DomovoiKit
import SwiftUI

/// Settings → Health, and the `Containers` route — `HealthPanel`
/// (`ContainersScreen.kt:83`).
///
/// Android's **Containers route *is* this panel**: `ScreenRouter.kt:76` is
/// `Route.Containers -> HealthPanel(navigate)`, and the panel's own doc
/// comment calls itself "Settings → System → Health". So one screen fills both
/// slots here — ``HealthPanelView`` for the Settings section and
/// ``HealthRouteScreen`` for the route, which is this view inside a
/// `ScrollView` of its own because `SettingsPlaceholder` already supplies one
/// (`RouteScreen.swift:150` records what a nested scroller cost last time).
///
/// Which one a person actually reaches: `settingsTabFor(.containers)` is
/// `"health"` (`Route.swift:140`) and `ShellNavigation.navigate(_:)` turns
/// such a route into `openSettingsTab(tab)`, so **every tap and every link
/// that names `.containers` lands in Settings**. The route slot is filled
/// anyway, because a route a deep link can still append must not render
/// `not built yet` while the screen exists one level away.
///
/// **Every decision is ``HealthPanelState``'s** — which of the three branches
/// the screen is, the summary line and its dot, the four parts and their
/// checks, whether the freeze card is drawn, what the Containers foot says.
/// This file is layout.
///
/// ### What is not, and why there is no control for it
///
///  * **No acknowledge and no clear.** Both routes are admin-tier at both
///    hops (`containers.py:133`, `:146`) and this app holds a household
///    token, so either button would 401 every time — the
///    no-misleading-controls rule's own example. The panel says
///    ``HealthPanelStrings/adminClearsThese`` instead, which is Android's
///    sentence. Android's panel offers neither either.
///  * **No "run a check now", no mpd image rebuild.** Same tier
///    (`containers.py:108`, `:118`), and not on Android's panel.
///  * **No updates, disk-space, pending-approval or internet rows.** Those
///    reads are admin-only, so a grey `not checked yet` row would be a lie
///    about *who* did not check. The foot says where they live, which is the
///    paragraph at the bottom of all three branches.
///  * **No `expected` / `found` detail on an incident.** The server adds
///    those keys for an admin caller only (`container_guard/views.py:107`),
///    so there is nothing to disclose and ``HealthIncident`` does not declare
///    them.
///  * **No notification when an incident opens.** Android posts one
///    (`IncidentNotifier`); that is `alerts/`, not the panel, and it needs a
///    local-notification authorisation — which means `project.yml`, which
///    this item must not edit. Nothing on this screen implies one will
///    arrive. It is the open question in DECISIONS.
///  * **No container banner in the shell.** `ShellModel.chrome` passes
///    `containerBanner: nil` and `ShellModel.swift` is a do-not-edit file, so
///    this panel is the only place the guard shows. In DECISIONS under
///    "Needs a shared change".
struct HealthPanelView: View {

    let model: ShellModel

    /// Built in `.task` rather than in an initialiser: a `@State` initialiser
    /// runs before the view can rely on having a container, and `.task` is
    /// cancelled when the route leaves — which is "a route holds nothing",
    /// exactly as `rememberApi`'s state dying with the composable is.
    @State private var panel: HealthPanelModel?

    @Environment(\.openURL) private var openURL

    /// `LocalSharedScreen.current` (`:92`) — the kitchen tablet. A shared
    /// screen is a guest here whatever its token could read.
    private var shared: Bool { model.visibility.sharedScreen }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let panel {
                // One call per redraw, and the clock is read from the model so
                // every relative time on the screen counts against the same
                // instant.
                content(panel.panel(shared: shared), nowMs: panel.nowMs)
            } else {
                // The frame before `.task` has run. Not `.loading`'s branch —
                // that one is about a config read that has not answered — but
                // it looks the same, which is why both go through
                // ``loadingLine``.
                loadingLine
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task {
            if panel == nil { panel = HealthPanelModel(in: model.container) }
            panel?.start()
        }
        .onDisappear { panel?.stop() }
    }

    // MARK: - the three branches (`:132-158`)

    @ViewBuilder
    private func content(_ state: HealthPanelState, nowMs: Int64) -> some View {
        switch state.body {
        case .rows(let rows):
            rowsBranch(rows, nowMs: nowMs)
        case .summary(let count):
            // `AttentionView.Summary` (`:151`) — a count and no detail, for a
            // house whose `home_problems_visibility` is `summary`, or for any
            // house on a shared screen.
            noDetails(
                HealthPanelState.summaryAdminLine(count: count),
                note: state.noDetailsNote
            )
        case .noDetails(let line):
            noDetails(line, note: state.noDetailsNote)
        case .loading:
            loadingLine
        }
        // **All three branches** (`:160`), which is the point: a phone that may
        // see nothing still learns where the rest of Health is.
        dashboardFoot(state)
    }

    /// `AttentionView.Rows` (`:133-149`): the open incidents, the summary
    /// line, the four parts, in that order.
    @ViewBuilder
    private func rowsBranch(
        _ rows: HealthPanelRows,
        nowMs: Int64
    ) -> some View {
        // `openIncidents(state, tick)` (`:288`) — the freeze card first, then
        // the list. Both only ever drawn for a household tier with something
        // open; ``HealthPanelRows`` has already applied that.
        if rows.freeze {
            freezeCard
        }
        if rows.showsOpenIncidents {
            HealthSectionCard(
                title: HealthPanelStrings.needAttentionTitle,
                count: "\(rows.openCount)\(HealthPanelStrings.openSuffix)"
            ) {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(rows.openIncidents.indices, id: \.self) { index in
                        HealthRowDivider(isFirst: index == 0)
                        HealthIncidentRowView(
                            incident: rows.openIncidents[index],
                            nowMs: nowMs
                        )
                    }
                }
            }
        }
        summaryLine(rows)
        ForEach(rows.parts) { part in
            HealthSectionCard(title: part.label) {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(part.checks.indices, id: \.self) { index in
                        HealthRowDivider(isFirst: index == 0)
                        HealthCheckRowView(
                            check: part.checks[index],
                            nowMs: nowMs,
                            onFix: open
                        )
                    }
                    // `if (part == HealthPart.Containers) ContainersPart(...)`
                    // (`:145`) — inside the Containers card, under its check.
                    if part.part == .containers {
                        containersFoot(rows.containersFoot, nowMs: nowMs)
                    }
                }
            }
        }
    }

    /// `SummaryLine(problems)` (`:184`) — **not in a card**, which is
    /// Android's: it is the one line that is about the whole screen.
    private func summaryLine(_ rows: HealthPanelRows) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            ToneDot(tone: rows.summaryTone)
            Text(rows.summaryLine)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Spacer(minLength: 0)
        }
        // **No `accessibilityElement(children: .combine)` here**, deliberately.
        // The dot is already hidden and the words are the only child, so the
        // line reads alone either way — and combining would turn the leaf into
        // an `.other` element, which a measurement of the *text*'s height
        // cannot find (`app.staticTexts`, the Chat pass's lesson: `.any`
        // measures the padded container instead).
    }

    /// `FreezeCard()` (`:326`): what a tamper incident holds back, four lines,
    /// each behind a `·`.
    private var freezeCard: some View {
        HealthSectionCard(
            title: HealthPanelStrings.pausedTitle,
            count: HealthPanelStrings.pausedSub
        ) {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(HealthFreezeList.lines, id: \.self) { line in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        // The bullet is a **separate `Text`**, as Android's
                        // is, so the line's own text wraps under itself
                        // instead of under the bullet.
                        Text(HealthPanelStrings.freezeBullet)
                            .font(.subheadline)
                            .foregroundStyle(Color.fgMuted)
                            .accessibilityHidden(true)
                        Text(line)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                            .multilineTextAlignment(.leading)
                        Spacer(minLength: 0)
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// `ContainersPart` (`:249`) — the guest line, or the last day's resolved
    /// and cleared incidents and the sentence saying who deals with them.
    @ViewBuilder
    private func containersFoot(
        _ foot: HealthPanelContainersFoot,
        nowMs: Int64
    ) -> some View {
        switch foot {
        case .guest(let line):
            Divider()
            quietLine(line)
        case .household(let resolved, let showsAdminNote):
            if !resolved.isEmpty {
                Divider()
                Text(HealthPanelStrings.resolvedLabel)
                    .font(.footnote.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                    .padding(.horizontal, 16)
                    .padding(.top, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                ForEach(resolved) { incident in
                    HealthIncidentRowView(incident: incident, nowMs: nowMs)
                }
            }
            if showsAdminNote {
                // The sentence that stands in for the acknowledge and clear
                // buttons neither app draws.
                quietLine(HealthPanelStrings.adminClearsThese)
            }
        case HealthPanelContainersFoot.none:
            // Nothing has answered yet. The Containers check above already
            // reads `not checked yet`, so a second line saying so would say
            // it twice.
            EmptyView()
        }
    }

    /// `NoDetails(line, shared)` (`:206`): a card with the line and, under it,
    /// who does see the details.
    private func noDetails(_ line: String, note: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(line)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Text(note)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
    }

    /// `LoadingState()` (`Kit.kt:275`) — the visibility setting has not been
    /// read, so which of the three branches this screen is, is not known yet.
    ///
    /// **A spinner and no words**, which is Android's: `LoadingState` is a
    /// `CircularProgressIndicator` in a box and nothing else. A sentence here
    /// would be a string no Kotlin file has. Its VoiceOver label is the
    /// section's own name, already pinned in ``SettingsSection/all``, rather
    /// than an invented one — ``ChatView``'s pattern.
    private var loadingLine: some View {
        ProgressView()
            .padding(.vertical, 40)
            .frame(maxWidth: .infinity, alignment: .center)
            .accessibilityLabel(SettingsSection.named("health")?.label ?? "")
    }

    /// The paragraph and the button (`:160-172`).
    ///
    /// **The button is not drawn when there is no saved server**
    /// (``HealthPanelState/dashboardURL`` is nil), which is Android's
    /// `url == null` branch minus the toast nobody could have asked for.
    @ViewBuilder
    private func dashboardFoot(_ state: HealthPanelState) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(state.dashboardFoot)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            if let address = state.dashboardURL, let url = URL(string: address) {
                // ``HomeLink`` and not a bordered button: Android's is an
                // `OutlinedButton`, and this app has exactly one link shape,
                // already measured at 44 points with its `contentShape` — a
                // second button look would be a second thing to measure. In
                // DECISIONS.
                HomeLink(HealthPanelStrings.openTheDashboard) {
                    openDashboard(url)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func quietLine(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - the two things a tap does

    /// `open: (HomeTarget) -> Unit` (`:116-125`).
    private func open(_ target: HomeTarget) {
        switch target {
        case .screen(let route):
            // `navigate(Route.Satellites)` from the room checks, which on both
            // platforms opens Settings → Rooms (``settingsTabFor(_:)``).
            model.navigate(route)
        case .settings(let tab):
            model.openSettingsTab(tab)
        case .dashboard(let hash):
            // The `stt` and `plugins` fixes: a page only the dashboard has.
            guard
                let address = healthDashboardURL(
                    serverURL: model.container.store.serverURL,
                    hash: hash
                ),
                let url = URL(string: address)
            else {
                model.showToast(HealthPanelStrings.noBrowser)
                return
            }
            openDashboard(url)
        }
    }

    /// `openInBrowser(context, url)` and its toast (`:120-123`).
    ///
    /// The app's **first** `openURL`, and it lives in the view file so
    /// `DomovoiKit` still imports no UIKit. The completion handler is the
    /// honest half: Safari can be restricted by Screen Time, and a tap that
    /// did nothing would otherwise say nothing. The URL is
    /// `http://<private LAN address>/#settings/health`, which Safari opens —
    /// App Transport Security binds this app's own networking, not Safari's.
    ///
    /// `OpenURLAction` is `@MainActor`, so the non-`@Sendable` completion
    /// formed here inherits this view's `@MainActor` isolation and may both
    /// capture `model` and call it directly. An earlier draft hopped through
    /// `Task { @MainActor in … }` on the belief that the completion was
    /// nonisolated; that belief was never compiled (`pkgtest.sh` builds
    /// `DomovoiKit` only), and under Swift 6's `complete` concurrency
    /// checking it was the worse of two readings — if it had been true, the
    /// capture of `model`, not the call of `showToast`, is what crosses, and
    /// the hop would not have saved it.
    private func openDashboard(_ url: URL) {
        openURL(url) { accepted in
            guard !accepted else { return }
            model.showToast(HealthPanelStrings.noBrowser)
        }
    }
}

/// The `containers` route's slot: ``HealthPanelView`` with a scroller of its
/// own.
///
/// The Settings section is drawn inside `SettingsPlaceholder`'s `ScrollView`
/// (`RouteScreen.swift:227`), so the panel itself must not be one — "the
/// screen scrolls, and the panel inside it does not", which that file records
/// as a defect it already paid for. The route has no scroller above it, so
/// this is where the route gets one.
struct HealthRouteScreen: View {

    let model: ShellModel

    var body: some View {
        ScrollView {
            HealthPanelView(model: model)
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
