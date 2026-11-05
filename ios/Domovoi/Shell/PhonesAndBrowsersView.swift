import DomovoiKit
import SwiftUI

/// Settings → Phones & browsers: **Server**, **Household token** and
/// **Trusted servers**.
///
/// A port of the first three cards of
/// `android/.../ui/screens/settings/ConnectionPanel.kt` (map §6.1-§6.4), plus
/// **Listening as** from §6.5. Which rows the list has is
/// ``trustedServersRows(trusted:known:active:)``, what the identity section
/// says is ``IdentityDisplay``, what each forget does is
/// ``ForgetAffordance``, and the cascade is ``TrustBook/forget(_:)``.
///
/// **Listening as** arrived with the 2026-11-03 batch (Kamron's answer 11:
/// "yes, match Android: build the 'listening as' picker"). It is here and not
/// on Podcasts or Audiobooks because that is where Android puts it — those two
/// screens draw a *hint chip* and the real selector lives in this panel
/// (`AudiobooksScreen.kt:333`, `ConnectionPanel.kt:350-378`). What it decides
/// is which server row a resume position belongs to, and the rows it offers
/// are ``PlayerListenerModel``'s.
///
/// **The rest of §6.5 is still not here and is still not stubbed**:
/// Appearance, This device and This phone belongs to each need a store key or
/// a writer no batch has built, and a theme chip that does not change the
/// theme is the misleading control the owner has ruled out twice.
/// ### This panel does not scroll, and does not own the forget dialog
///
/// Both changed in 3b. It was `ZStack { ScrollView { … } }` **inside a screen
/// with no `ScrollView` at all**, so the scroll was in the wrong place: the
/// panel's viewport sliced the `Household token` card in half and pinned the
/// back link under it, which is visible in a screenshot. ``SettingsPlaceholder``
/// scrolls now and this is a plain stack of three cards.
///
/// The dialog moved with the scroll view, because it was centred in a `ZStack`
/// that was screen-sized only *because* that `ScrollView` filled the screen.
/// ``SettingsPlaceholder`` carries the decision and the reasoning; what is
/// left here is the binding, so that the control which asks and the dialog
/// which answers share one piece of state rather than two.
struct PhonesAndBrowsersPanel: View {

    let model: ShellModel

    /// Set by the forget X, read and cleared by the dialog, which lives at the
    /// screen's root — see ``SettingsPlaceholder``.
    @Binding var askingToForget: Bool

    @State private var typedURL = ""
    @State private var saving = false
    @State private var seededFor = ""

    /// The household, for the Listening as dropdown. Built in `.task` and not
    /// in an initialiser, for ``HomeView``'s reason: the model needs the
    /// container and a `@State` initialiser runs before the view has one.
    @State private var listeners: PlayerListenerModel?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            serverCard
            householdTokenCard
            trustedServersCard
            listeningAsCard
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear { seedTheField() }
        .task {
            if listeners == nil {
                listeners = PlayerListenerModel(in: model.container, listeners: model.listeners)
            }
            listeners?.start()
        }
        .onDisappear { listeners?.stop() }
    }

    // MARK: - §6.5 Listening as

    /// Whose resume positions this phone saves — the real selector, of which
    /// Podcasts and Audiobooks draw only a hint chip.
    ///
    /// `ConnectionPanel.kt:350-378`, word for word: the card's title and its
    /// subtitle, a dropdown whose null row reads "me (this device)", the
    /// write, and the toast naming whoever was picked. The failure sentence is
    /// drawn only when the read has **no data at all**
    /// (``PlayerListenerModel/trouble``), because a list a few seconds old
    /// with a failed refresh behind it is still a list worth picking from.
    ///
    /// A `Menu`, as every other picker in this app is
    /// (``FilesView/libraryPicker(_:)``) — not a `Picker`, whose `.menu` style
    /// draws the selected value in a shape this app's cards do not use, and
    /// whose automatic style on a phone is a wheel.
    @ViewBuilder
    private var listeningAsCard: some View {
        PanelCard(title: PlayerStrings.listeningAsCard, sub: PlayerStrings.listeningAsCardSub) {
            if let listeners {
                if let trouble = listeners.trouble {
                    Text(trouble)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    listenerPicker(listeners)
                }
            } else {
                ProgressView()
                    .accessibilityLabel(PlayerStrings.listeningAsCard)
            }
        }
    }

    private func listenerPicker(_ listeners: PlayerListenerModel) -> some View {
        let chosen = listeners.selected
        return Menu {
            ForEach(listeners.options) { option in
                Button {
                    // The store and the player first, then this card's own
                    // observed mirror of it: ``PlayerListenerStore`` is a
                    // `Mutex` and not `@Observable`, so without `adopt()` the
                    // tick below would follow the pick only because the toast
                    // on the next line re-renders the whole shell — which is
                    // not something this card may depend on.
                    model.listenerChanged(to: option.personID)
                    listeners.adopt()
                    model.showToast(listeners.toast(for: option))
                } label: {
                    // A tick rather than a highlight: a menu row's selected
                    // state is not read out, and this one decides whose
                    // listening history a place belongs to.
                    if option.personID == chosen?.personID {
                        Label(option.name, systemImage: "checkmark")
                    } else {
                        Text(option.name)
                    }
                }
            }
        } label: {
            HStack(spacing: 10) {
                Text(chosen?.name ?? PlayerStrings.listenerThisDevice)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 4)
                Image(systemName: "chevron.down")
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
            }
            .padding(.horizontal, 12)
            // 44 points, stated: a `Menu`'s label is hit-tested as the label
            // draws, and one line of `subheadline` is 20.
            .frame(minHeight: 44)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.fgMuted.opacity(0.35), lineWidth: 1)
            )
            .contentShape(.rect)
        }
        .accessibilityLabel(PlayerStrings.listeningAsCard)
        .accessibilityValue(chosen?.name ?? PlayerStrings.listenerThisDevice)
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
            // **`axis: .vertical`, so the whole address is on screen.**
            // Apple's audit named this field for "Text of this element may be
            // clipped at larger Dynamic Type sizes", and the tree shows why:
            // 301 points of single-line field holding
            // `http://127.0.0.1:6601`, which at an accessibility size shows
            // about half of it. An address you can only see half of is the
            // same fault as a truncated fingerprint — it looks like an
            // answer — and a person checking why their phone will not connect
            // is exactly who needs to read all of it.
            //
            // A vertical field accepts a newline from the Return key, which
            // an address must never carry, so they are stripped as they
            // arrive rather than at save time: `normalise(_:)` would see a
            // broken address, and the person would see their own typing
            // silently altered later instead of never accepting the newline.
            TextField(
                ShellStrings.serverURLPlaceholder,
                text: $typedURL,
                // Owner answer 65's vertical leg. The one vertical-axis field
                // train I005 measured drew its hint at 1.70:1 and
                // `ShellHintInkAppearance`'s swap did not reach it, so the
                // hint is passed as a prompt instead; the same string goes to
                // the title, which keeps the accessibility label and
                // `placeholderValue` exactly what they were.
                prompt: ShellHintPrompt.text(ShellStrings.serverURLPlaceholder),
                axis: .vertical
            )
                .onChange(of: typedURL) { _, new in
                    if new.contains(where: \.isNewline) {
                        typedURL = new.filter { !$0.isNewline }
                    }
                }
                .font(.footnote.monospaced())
                // **The border is drawn here rather than by
                // `.textFieldStyle(.roundedBorder)`.** That style with
                // `axis: .vertical` produces an 18-point control, which
                // Apple's audit fails as "Hit area is too small" — a finding
                // the single-line field never had. `.frame(minHeight: 34)`
                // does not reach it from either side of the style modifier:
                // measured at 18 points with the frame before it and 18
                // points with the frame after it, because a field style
                // builds the control around what it is handed and the frame
                // lands on the text inside the border either way.
                //
                // Nor does padding reach it. The padding below moved the box
                // and left the audit's finding exactly where it was, because
                // what the audit measures is the **field's own accessibility
                // element**, and for a vertical field that is the height of
                // the text in it: 18 points, one line of `.footnote`. The
                // single-line field reports 34 because `.roundedBorder` is
                // part of the control.
                //
                // `lineLimit(2...)` is what changes that element — two lines
                // of room, 36 points, and the right shape for this field
                // besides, since `http://192.168.1.10:6369` does not fit one
                // line of a Settings card at any type size worth having.
                .lineLimit(2...)
                // The border is drawn here, `.quaternary` on an 8-point
                // radius like the app's other boxes
                // (`ConnectionDialogView.swift:329`).
                .padding(.horizontal, 8)
                .padding(.vertical, 10)
                .background(
                    Color(.secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 8)
                )
                .overlay {
                    RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary)
                }
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .accessibilityLabel(ShellStrings.serverCard)
            Button(saving ? ShellStrings.checking : ShellStrings.saveAndReconnect) {
                Task { await save() }
            }
            .buttonStyle(BrandFillButtonStyle())
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
                // Android's filled `Button` (`ConnectionPanel.kt:441`):
                // ``Color/brandFg`` on ``Color/brand``, 8.50:1. The `.bordered`
                // chip it replaces read the ink on a wash of itself over this
                // card, about 4.06:1 (measured on Printing's card in I001 —
                // unit mx-ink, InkButtonStyles.swift).
                Button(ShellStrings.pinThisIdentity) { model.pinTheOfferedIdentity() }
                    .buttonStyle(BrandFillButtonStyle())
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
                // Android's filled `Button` (`ConnectionPanel.kt:229`), for
                // the pin button's reason above (unit mx-ink).
                .buttonStyle(BrandFillButtonStyle())
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
                // **No `.lineLimit(1)` on these three.** They were the
                // pairing screen's three "Text of this element may be clipped
                // at larger Dynamic Type sizes": a name, an address and a
                // fingerprint, in a row that also holds a status word and a
                // button, so there is not much width to go round and at an
                // accessibility size a single line of any of them is cut.
                //
                // A fingerprint is the one that matters. It exists to be
                // compared against what the dashboard shows, and a truncated
                // fingerprint is worse than no fingerprint: it looks like an
                // answer. So it wraps, like every other long string in this
                // app — `minimumScaleFactor` was rejected at the I0 gate for
                // making text smaller for the person who asked for bigger.
                HStack(alignment: .top, spacing: 10) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.displayName)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(row.address)
                            .font(.footnote)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(store.pin(forServer: row.url)?.fingerprint ?? ShellStrings.identityNotPinnedYet)
                            .font(.caption2.monospaced())
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
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
                            // **The third time this app has got this wrong**,
                            // after the header's live dot and the phone bar's
                            // tabs. A Simulator measured this one at
                            // `{{337, 1018}, {8.7, 8.7}}` — an 8.7-point
                            // target on the control that forgets a server,
                            // its household token and its pinned identity
                            // together. A `caption2` glyph is 8.7 points, and
                            // a glyph is all `.buttonStyle(.plain)` gives
                            // SwiftUI to hit-test, so the frame is stated and
                            // `contentShape` makes the frame the target.
                            .frame(minWidth: 44, minHeight: 44)
                            .contentShape(Rectangle())
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
