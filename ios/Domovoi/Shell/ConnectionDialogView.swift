import DomovoiKit
import SwiftUI

/// The one connection sheet, opened from the server pill in either shell: the
/// phone/server switch on top, the server picker below it.
///
/// A port of `ConnectionDialog` and `ServerPickerPanel`
/// (`android/.../ui/shell/ServerPicker.kt`, map §3). It is presented by
/// ``AppShellView`` **above both shells** and not from a shell, because
/// picking a side — or the server dropping out of reach — swaps the shell
/// underneath it (map §10.4).
///
/// Everything this file looks like it decides is decided in `DomovoiKit`:
/// which rows there are (``serverPickerRows(known:found:active:)``), which
/// pill each row gets (``ServerPickerRow/affordance(inUse:)``), what its
/// forget X does (``ServerPickerRow/forgetAffordance``), what the section
/// label says (``serverPickerSectionLabel(progress:swept:foundEmpty:knownEmpty:)``)
/// and — the one worth being careful about — which "nothing was found" line
/// is true (``ServerPickerNotice``).
///
/// ### Why the two confirmations are cards and not `.alert`s
///
/// Both of them have to show a fingerprint in **monospace**, and the forget
/// confirmation shows two of them one under the other so a person can see
/// what changed. An `alert`'s message renders as a single `Text` with the
/// system's own styling: no monospace, no two-column comparison, and a long
/// body truncated. The map asks for monospace in both places by name (§4.2,
/// §6.4), so they are drawn as cards over a dim — which is also what
/// Android's `Dialog { DomovoiCard { … } }` is. A nested `.sheet` inside the
/// presented sheet was the other option and was not taken: it is the
/// presentation SwiftUI is least reliable about, and nothing in
/// `pkgtest.sh` could see it misbehave.
struct ConnectionDialogView: View {

    let model: ShellModel

    @State private var picker: ServerPickerModel?

    var body: some View {
        ZStack {
            sheet
            if let picker, let server = picker.gate.pending {
                DialogCard {
                    TrustServerCard(
                        server: server,
                        onCancel: { picker.cancelTrust() },
                        onConfirm: { if picker.confirmTrust() { model.dismissSheet() } }
                    )
                }
            } else if let picker, let ask = forgetAsk(picker) {
                DialogCard {
                    ForgetServerCard(
                        ask: ask,
                        onCancel: { picker.cancelForgetActive() },
                        onConfirm: {
                            picker.cancelForgetActive()
                            model.forgetTheActiveServer()
                            model.dismissSheet()
                        }
                    )
                }
            }
        }
        .task {
            // Built here rather than in `init` so the sweep starts when the
            // sheet is on screen and is forgotten when it closes — the
            // lifetime Android's `remember` inside the dialog gives it.
            if picker == nil {
                // A plain capture, not `[weak model]`: a capture list cannot
                // name a member without `self.`, and there is no cycle to
                // break — the shell owns no reference to this picker, which
                // this view's `@State` owns and `onDisappear` stops.
                picker = ServerPickerModel(container: model.container) { text in
                    model.showToast(text)
                }
            }
            picker?.scanOnOpen()
        }
        .onDisappear { picker?.stop() }
    }

    private func forgetAsk(_ picker: ServerPickerModel) -> ForgetActiveServerAsk? {
        picker.forgetActiveAsk(status: model.container.identity.status)
    }

    private var sheet: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    modeSwitch
                    if let picker {
                        ServerPickerPanel(model: model, picker: picker) { model.dismissSheet() }
                    }
                }
                .padding(20)
            }
            .navigationTitle(ShellStrings.connectionTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(ShellStrings.close) { model.dismissSheet() }
                }
            }
        }
    }

    /// §3.6: two cards, the server one **greyed with its reason visible
    /// rather than hidden**.
    private var modeSwitch: some View {
        let onPhone = connectionShowsThePhone(
            preferLocal: model.inputs.preferLocal,
            serverURL: model.inputs.serverURL,
            choice: model.serverChoice
        )
        return HStack(alignment: .top, spacing: 8) {
            ModeOption(
                title: ShellStrings.thisPhone,
                sub: ShellStrings.thisPhoneSub,
                selected: onPhone,
                enabled: true
            ) {
                model.chooseThisPhone()
                model.dismissSheet()
            }
            ModeOption(
                title: model.serverChoice.title,
                sub: model.serverChoice.reason,
                selected: !onPhone,
                enabled: model.serverChoice.isEnabled
            ) {
                model.chooseServer()
                model.dismissSheet()
            }
        }
    }
}

/// One side of the phone/server switch.
private struct ModeOption: View {

    let title: String
    let sub: String
    let selected: Bool
    let enabled: Bool
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                Text(sub)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if selected {
                    Text(ShellStrings.inUse)
                        .font(.caption2)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(.tint.opacity(0.15), in: Capsule())
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(selected ? AnyShapeStyle(.tint) : AnyShapeStyle(.separator))
            )
            // Greyed, with the reason still legible: Android's `alpha 0.45f`.
            .opacity(enabled ? 1 : 0.45)
        }
        .buttonStyle(.plain)
        .disabled(!enabled || selected)
        .accessibilityLabel(ShellStrings.useLabel(title))
    }
}

/// The picker: the scan, the rows and manual entry.
struct ServerPickerPanel: View {

    let model: ShellModel
    let picker: ServerPickerModel
    let onSelected: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            scanHeader
            if let line = picker.notice.text {
                NoticeLine(text: line, warning: picker.notice.isWarning)
            }
            ForEach(picker.rows) { row in
                ServerRow(
                    row: row,
                    inUse: !model.inputs.preferLocal && model.serverChoice.isEnabled,
                    onSelect: { if picker.select(row) { onSelected() } },
                    onForget: {
                        switch row.forgetAffordance {
                        case .immediate: picker.forget(row)
                        case .confirmFirst: picker.askToForgetActive()
                        case .none: break
                        }
                    }
                )
            }
            manualEntry
        }
    }

    private var scanHeader: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(picker.sectionLabel)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.fgMuted)
                Spacer()
                Button(ShellStrings.rescan) { picker.rescan() }
                    .font(.footnote)
                    .disabled(picker.isScanning)
            }
            if picker.isScanning {
                ProgressView(
                    value: Double(picker.progress.done),
                    total: Double(max(picker.progress.total, 1))
                )
                .progressViewStyle(.linear)
            }
        }
    }

    private var manualEntry: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(ShellStrings.addManually)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Color.fgMuted)
                .padding(.top, 4)
            HStack(spacing: 8) {
                TextField(ShellStrings.manualPlaceholder, text: Binding(
                    get: { picker.manual },
                    set: { picker.manual = $0 }
                ))
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .accessibilityLabel(ShellStrings.addManually)
                Button(picker.manualButtonLabel) {
                    Task {
                        let outcome = await picker.addManual()
                        // A trusted address connected straight away and the
                        // sheet's work is done; an untrusted one raised the
                        // trust card, which is drawn over this sheet, so it
                        // stays up.
                        if case .found = outcome, picker.gate.pending == nil { onSelected() }
                    }
                }
                .disabled(!picker.manualButtonEnabled)
                .buttonStyle(.borderedProminent)
                // The app's tint is `brandInk` (`DomovoiApp.swift`), so
                // a prominent button would fill with the ink and draw
                // near-black `brandFg` on it at 3.08:1. The fill is the
                // other token, and 8.49:1 with this label colour.
                .tint(Color.brand)
                .foregroundStyle(Color.brandFg)
            }
            if let error = picker.manualError {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// §3.4's line. Loud for a fact the person can act on, quiet for one they
/// cannot (``ServerPickerNotice/isWarning``).
private struct NoticeLine: View {

    let text: String
    let warning: Bool

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            if warning {
                Image(systemName: "wifi.slash")
                    .font(.caption)
                    .foregroundStyle(Color.warnInk)
            }
            Text(text)
                .font(.footnote)
                .foregroundStyle(warning ? AnyShapeStyle(Color.warnInk) : AnyShapeStyle(Color.fgMuted))
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// One row of the list: a dot, the name, the address, the pill, the forget.
private struct ServerRow: View {

    let row: ServerPickerRow
    let inUse: Bool
    let onSelect: () -> Void
    let onForget: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(dot)
                .frame(width: 8, height: 8)
            // The same two faults as the trusted-servers row in
            // ``PhonesAndBrowsersView``, which Apple's audit caught there and
            // cannot catch here: nothing in a Simulator may open this sheet,
            // because doing so starts the LAN discovery sweep across whatever
            // network the Mac is on. So this one is fixed by reading, not by
            // measuring, and the numbers quoted are that row's.
            VStack(alignment: .leading, spacing: 2) {
                Text(row.displayName)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(row.address)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Text(row.affordance(inUse: inUse).text)
                .font(.caption)
                .foregroundStyle(Color.brandInk)
            if row.forgetAffordance != .none {
                Button(action: onForget) {
                    Image(systemName: "xmark")
                        .font(.caption2)
                        // 8.7 points there, and worse here than there: this
                        // row carries its own `onTapGesture` to select the
                        // server, so a near-miss on an 8.7-point forget does
                        // not do nothing — it connects to the server instead.
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.fgMuted)
                .accessibilityLabel(ShellStrings.forget)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
        .contentShape(Rectangle())
        .onTapGesture { if row.isSelectable { onSelect() } }
    }

    private var dot: Color {
        if row.active { return Color.brandInk }
        return row.online ? .green : .gray
    }
}

/// A confirmation drawn as a card over a dim, which is what Android's
/// `Dialog { DomovoiCard { … } }` is.
struct DialogCard<Content: View>: View {

    @ViewBuilder let content: Content

    var body: some View {
        ZStack {
            Color.black.opacity(0.35)
                .ignoresSafeArea()
            ScrollView {
                content
                    .padding(20)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.background, in: RoundedRectangle(cornerRadius: 14))
                    .padding(20)
            }
        }
    }
}

/// "Is this your Domovoi?" — the address first, because that is what a person
/// can check against the box, then the identity it advertises.
///
/// **Nothing has been saved at this point**, and the paragraph says so.
/// Cancelling leaves the app connected to whatever it was connected to. The
/// fingerprint is **unproven**: it is what `/api/health` claimed, vetted only
/// for shape — and it is what a yes pins, so the first proof has to match
/// what the person compared with the dashboard rather than whatever answers
/// first on some later network (A6-03).
struct TrustServerCard: View {

    let server: PendingServer
    let onCancel: () -> Void
    let onConfirm: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(ShellStrings.trustTitle, systemImage: "externaldrive.connected.to.line.below")
                .font(.headline)
                .foregroundStyle(Color.warnInk)
            Text(server.address)
                .font(.title3)
            if let fingerprint = server.fingerprint {
                Text(fingerprint)
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .textSelection(.enabled)
            }
            Text(ShellStrings.trustBody(name: server.name, offersIdentity: server.fingerprint != nil))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button(ShellStrings.trustCancel, action: onCancel)
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
                Button(ShellStrings.trustConfirm, action: onConfirm)
                    .buttonStyle(.borderedProminent)
                    // The app's tint is `brandInk` (`DomovoiApp.swift`), so
                    // a prominent button would fill with the ink and draw
                    // near-black `brandFg` on it at 3.08:1. The fill is the
                    // other token, and 8.49:1 with this label colour.
                    .tint(Color.brand)
                    .foregroundStyle(Color.brandFg)
            }
        }
    }
}

/// "forget this server?" — with the pinned identity and the one the server
/// proves now side by side, so a person can see what changed (§6.4).
struct ForgetServerCard: View {

    let ask: ForgetActiveServerAsk
    let onCancel: () -> Void
    let onConfirm: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(forgetServerTitle, systemImage: "externaldrive.badge.xmark")
                .font(.headline)
                .foregroundStyle(Color.errInk)
            Text(ask.address)
                .font(.title3)
            // The prose in prose and the keys in monospace: §6.4 wants the
            // fingerprints comparable with the dashboard, and the paragraph
            // readable. Both halves come from
            // `forgetActiveServerText(address:pinned:proven:)`.
            Text(ask.paragraph)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if let identity = ask.identityLines {
                Text(identity)
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            HStack {
                Spacer()
                Button(forgetServerCancel, action: onCancel)
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
                // **No brand colour here.** A destructive role draws itself
                // red whatever the tint is, and forgetting a server is the
                // one button on this sheet that should not look like the
                // friendly one. `--danger` is its own token in
                // `colors_and_type.css` for the same reason.
                Button(forgetServerConfirm, role: .destructive, action: onConfirm)
                    .buttonStyle(.borderedProminent)
                    // The app's tint is `brandInk` (`DomovoiApp.swift`), so
                    // a prominent button would fill with the ink and draw
                    // near-black `brandFg` on it at 3.08:1. The fill is the
                    // other token, and 8.49:1 with this label colour.
                    .tint(Color.brand)
            }
        }
    }
}
