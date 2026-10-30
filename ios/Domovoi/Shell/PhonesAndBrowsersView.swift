import DomovoiKit
import SwiftUI

/// Settings → Phones & browsers: **Server**, **Household token** and
/// **Trusted servers**.
///
/// A port of the first three cards of
/// `android/.../ui/screens/settings/ConnectionPanel.kt` (map §6.1-§6.4).
/// Which rows the list has is ``trustedServersRows(trusted:known:active:)``,
/// what the identity section says is ``IdentityDisplay``, what each forget
/// does is ``ForgetAffordance``, and the cascade is ``TrustBook/forget(_:)``.
///
/// **§6.5 is not here and is not stubbed**: Appearance, This device, This
/// phone belongs to and Listening as each need a store key or a network call
/// no batch has built, and a theme chip that does not change the theme is the
/// misleading control the owner has ruled out twice. `I1C3-REPORT.md` says so
/// and sizes it as its own batch.
struct PhonesAndBrowsersPanel: View {

    let model: ShellModel

    @State private var typedURL = ""
    @State private var saving = false
    @State private var askingToForget = false
    @State private var seededFor = ""

    var body: some View {
        ZStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    serverCard
                    householdTokenCard
                    trustedServersCard
                }
                .padding(16)
            }
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
                            // screen that opened it: this panel is in the
                            // workspace, and the workspace is gone the
                            // instant the server is.
                            model.forgetTheActiveServer()
                        }
                    )
                }
            }
        }
        .onAppear { seedTheField() }
    }

    /// The field is seeded from the saved server, and **re-seeded when that
    /// changes** — Android's `remember(serverUrl)`. `seededFor` is what it
    /// was seeded from, so a person's half-typed address is not overwritten
    /// by a redraw.
    private func seedTheField() {
        let url = model.inputs.serverURL
        guard seededFor != url else { return }
        seededFor = url
        typedURL = url
    }

    // MARK: - §6.1 Server

    private var serverCard: some View {
        PanelCard(title: ShellStrings.serverCard, sub: ShellStrings.serverCardSub) {
            HStack(spacing: 6) {
                Circle()
                    .fill(model.connected ? Color.green : Color.red)
                    .frame(width: 8, height: 8)
                Text(model.connected ? ShellStrings.connected : ShellStrings.notConnected)
                    .font(.caption)
                    .foregroundStyle(model.connected ? AnyShapeStyle(Color.okInk) : AnyShapeStyle(Color.errInk))
            }
            TextField(ShellStrings.serverURLPlaceholder, text: $typedURL)
                .font(.footnote.monospaced())
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .accessibilityLabel(ShellStrings.serverCard)
            Button(saving ? ShellStrings.checking : ShellStrings.saveAndReconnect) {
                Task { await save() }
            }
            .buttonStyle(.borderedProminent)
            .foregroundStyle(Color.brandFg)
            .disabled(ServerCredentials.isBlank(typedURL) || saving)

            if !ServerCredentials.isBlank(model.inputs.serverURL) {
                identitySection
            }
        }
    }

    /// `Save & reconnect`: the same three steps as the picker's "add
    /// manually", with one difference the map states plainly — **typing an
    /// address here IS the trust decision, so there is no dialog**. It is
    /// still probed first, and the identity it advertises at that moment is
    /// what the trust decision pins (A6-03 review); an address nothing
    /// answers at is not saved.
    ///
    /// `@MainActor` is written out rather than inferred from `View`: this is
    /// the only `async` method in the app target, it touches a `@MainActor`
    /// model across an `await`, and `swift test` cannot see the mistake if
    /// the inference ever stops holding (part 2 shipped two main-actor
    /// warnings in a view for exactly that reason).
    @MainActor
    private func save() async {
        switch ServerAddress.fromTyped(typedURL) {
        case nil:
            return
        case .refused(let message):
            model.showToast(message)
        case .ok(let url):
            saving = true
            let hit = await Discovery.probe(
                model.container.unauthenticatedProbe,
                base: url,
                timeout: Discovery.manualTimeout
            )
            saving = false
            guard let hit else {
                model.showToast(ShellStrings.couldNotReachDashboard(url: url))
                return
            }
            let store = model.container.store
            store.trustServer(hit.url, identity: hit.identity)
            store.upsertKnownServer(url: hit.url, name: hit.name)
            if store.setServerURL(hit.url) {
                model.showToast(ShellStrings.serverSaved)
            } else {
                model.showToast(ShellStrings.couldNotSwitch)
            }
            seededFor = ""
            model.refresh()
            seedTheField()
        }
    }

    /// §4.4's four-way display: what the phone holds this server to, or why
    /// it holds it to nothing. A server trusted while it offered no identity
    /// and which has grown one since is **never pinned behind the person's
    /// back** — the key it proves is shown with a button, after they compare
    /// it with the dashboard.
    private var identitySection: some View {
        let display = model.identityDisplay
        return VStack(alignment: .leading, spacing: 6) {
            Text(ShellStrings.identityLabel)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Color.fgMuted)
            if let fingerprint = display.fingerprint {
                Text(fingerprint)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
            }
            Text(display.detail)
                .font(.footnote)
                .foregroundStyle(display == .noIdentityOffered ? AnyShapeStyle(Color.warnInk) : AnyShapeStyle(Color.fgMuted))
                .fixedSize(horizontal: false, vertical: true)
            if display.offersPinning {
                Button(ShellStrings.pinThisIdentity) { model.pinTheOfferedIdentity() }
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
            }
        }
    }

    // MARK: - §6.2 Household token

    private var householdTokenCard: some View {
        let paired = model.isPaired
        return PanelCard(title: ShellStrings.householdTokenCard, sub: ShellStrings.householdTokenCardSub) {
            HStack(spacing: 6) {
                Circle()
                    .fill(paired ? Color.green : Color.gray)
                    .frame(width: 8, height: 8)
                Text(paired ? ShellStrings.paired : ShellStrings.notPaired)
                    .font(.caption)
                    .foregroundStyle(paired ? AnyShapeStyle(Color.okInk) : AnyShapeStyle(Color.fgMuted))
            }
            if paired {
                Text(ShellStrings.pairedExplanation)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // Re-pairing is the same checked paste, with a different button
            // word. The field is keyed on whether this phone is paired so
            // that the label follows the state.
            CheckedPairingFieldView(field: PairingFieldModel(
                container: model.container,
                label: paired ? ShellStrings.replace : ShellStrings.pair
            ) { _ in
                model.showToast(ShellStrings.pairedToast)
                model.pairingDone()
            })
            .id(paired)
            if paired {
                Button(ShellStrings.forgetTheToken) {
                    // **Only the token.** Not the trust, not the pin, not the
                    // server — which is the difference between this button
                    // and the forget X two cards down.
                    model.container.store.setToken(nil, for: model.inputs.serverURL)
                    model.showToast(ShellStrings.noLongerPaired)
                    model.refresh()
                }
                .buttonStyle(.bordered)
                .tint(Color.brandInk)
            }
        }
    }

    // MARK: - §6.3 Trusted servers

    private var trustedServersCard: some View {
        let store = model.container.store
        let rows = trustedServersRows(
            trusted: store.trustedServers,
            known: store.knownServers,
            active: model.inputs.serverURL
        )
        return PanelCard(title: ShellStrings.trustedServersCard, sub: ShellStrings.trustedServersCardSub) {
            if rows.isEmpty {
                Text(ShellStrings.noTrustedServersYet)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
            }
            ForEach(rows) { row in
                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.displayName)
                            .font(.subheadline)
                            .lineLimit(1)
                        Text(row.address)
                            .font(.footnote)
                            .foregroundStyle(Color.fgMuted)
                            .lineLimit(1)
                        Text(store.pin(forServer: row.url)?.fingerprint ?? ShellStrings.identityNotPinnedYet)
                            .font(.caption2.monospaced())
                            .foregroundStyle(Color.fgMuted)
                            .lineLimit(1)
                    }
                    Spacer()
                    if row.active {
                        Text(ShellStrings.connected)
                            .font(.caption)
                            .foregroundStyle(Color.brandInk)
                    } else if !ServerCredentials.isBlank(store.token(for: row.url) ?? "") {
                        Text(ShellStrings.paired)
                            .font(.caption)
                            .foregroundStyle(Color.okInk)
                    }
                    Button {
                        switch row.forgetAffordance {
                        case .confirmFirst:
                            askingToForget = true
                        case .immediate:
                            store.removeKnownServer(row.url)
                            model.showToast(ShellStrings.forgot(address: row.address))
                            model.refresh()
                        case .none:
                            break
                        }
                    } label: {
                        Image(systemName: "xmark")
                            .font(.caption2)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityLabel(ShellStrings.forget)
                }
            }
        }
    }
}

/// A Settings card: a title, the line under it, and its contents.
///
/// Android's `PanelCard`. The `sub` is drawn verbatim because it is a
/// user-facing string (map §7.9).
struct PanelCard<Content: View>: View {

    let title: String
    let sub: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.headline)
            Text(sub)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 12))
    }
}
