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
///
/// ### It has to say it is modal, and it did not
///
/// Android's `Dialog` is a window: the screen behind it stops being
/// reachable. This is a `ZStack` over the screen, so the screen behind it
/// stayed in the accessibility tree — focusable, swipeable, and read out.
///
/// The audit found it (2026-10-30, the `forget-card` sweep) without being
/// asked about it. With the forget dialog open over Settings → Phones &
/// browsers it reported two `Contrast failed`, both naming elements *behind*
/// the card: the Trusted-servers paragraph and a `connected` badge. They were
/// true readings. Those elements really were sitting under a 35% black scrim
/// and really were unreadable — which is what a scrim is for, and is only a
/// finding because they were still being offered to the person as content.
///
/// The worse half is what the same tree showed: the panel's own `forget`
/// control at y=658 and the dialog's destructive `forget` at y=1409, both
/// live, both labelled `forget`. A person moving by VoiceOver with this
/// dialog open heard "forget", and then "forget", and one of the two deletes
/// a server's token and identity without asking again.
///
/// `.isModal` was the first answer: VoiceOver ignores everything outside the
/// element that carries it, so the duplicate control went away with it.
///
/// ### `.isModal` was not enough, and the measurement says so
///
/// It stops VoiceOver **navigating** out of the card. It leaves everything
/// behind the card in the accessibility tree and rendered under the scrim,
/// which is what Apple's audit reports. With the Alerts explain card up over
/// Home — the same `ZStack`, with `.isModal` and with
/// `screen.accessibilityHidden(…)` beside it — the audit still named
/// `domovoi / home`, `127.0.0.1:6601`, `unverified`, `Kitchen` and the four
/// phone-bar labels, `Contrast failed` and `Contrast nearly passed`, every one
/// a view behind the 35% scrim; and the tree attachment listed the whole of
/// Home as the card's siblings
/// (train S009, `overnight-trains/S009/Alerts/att/testAlerts_TurningOnAlertsExplainsItselfFirst/`).
///
/// That item fixed its own card by making it a `fullScreenCover`
/// (`AlertsHintRowView.swift:254-273`), which worked: `fullScreenCover` is
/// `UIModalPresentationStyle.fullScreen`, and UIKit takes the presenting
/// controller's views **out of the window** once the transition ends. There is
/// then nothing behind the card to audit, to read at 35%, or to reach with
/// VoiceOver. `.accessibilityHidden` could not do that from inside a
/// `NavigationStack` destination, because it is a SwiftUI attribute and does
/// not cross into the UIKit controller hosting the stack's content.
///
/// So the shape moves here, where all four dialogs share it (owner answer 6:
/// "`DialogCard` becomes a presentation, shared fix for all four dialogs").
/// **The call-site API is unchanged** — `DialogCard { … }` still — because its
/// seven uses are in six files this unit does not own.
///
/// Three things are deliberate:
///
///  * **Outside the cover the host draws nothing** (``hostOfNothing``). It has
///    to stay in the hierarchy for the presentation to live, and it must draw
///    and measure nothing: a call site puts this in a `ZStack` beside the
///    screen, not in place of it.
///  * **No `.presentationBackground(.clear)`.** A transparent full-screen
///    cover is the one case UIKit does *not* take the presenter's views out of
///    the window for, so it would hand back the hole this change closes, and
///    show black rather than the screen if it did not. The 35% dim stays
///    inside the cover instead — it is what the Alerts card already ships and
///    what the S009 train measured clean.
///  * **The card is still a `ScrollView`, and still `.isModal`.** The scroll
///    view is why the card is reachable at AX5 (`RouteScreen.swift:247-249`),
///    and the trait is belt and braces for a future SwiftUI that presents over
///    the window rather than in place of it.
///
/// `.constant(true)`: the host exists only while its call site's `if` holds, so
/// the call site's own condition is the binding, and nothing inside the cover
/// can dismiss it behind the call site's back — a swipe cannot dismiss a
/// `fullScreenCover` either.
///
/// ### A presentation that does not arrive draws the card anyway
///
/// Review 1's must finding, and it is a real one: `AlertsHintLifecycle`
/// presents ``AlertsPermissionCardView`` in a `fullScreenCover` of its own
/// (`AlertsHintRowView.swift:273`) and that view's whole body is a
/// `DialogCard`, so the Alerts card is a cover **inside** a cover. If SwiftUI
/// drops an inner presentation requested during the outer transition, the
/// outer cover is left showing this host — and a host that drew nothing, over
/// an opaque presentation background, is a blank screen with no control on it
/// and no dismiss gesture.
///
/// So the host is not unconditionally nothing. ``coverAppeared`` is set by the
/// cover's own content, and if it has not been set by the time ``lostAfter``
/// has passed, the card is drawn **inline** — the exact shape every one of
/// these seven call sites shipped before answer 6. The failure mode that
/// cannot happen any more is the dead end: whatever becomes of the
/// presentation, the card and its buttons are on the screen.
///
/// It is a deadline and not a cleverer test because "did the presentation
/// arrive" is a question about time: nothing in SwiftUI reports a dropped
/// presentation, and `@Environment(\.isPresented)` answers true for a pushed
/// `NavigationStack` destination as well as a sheet, which is where
/// `RouteScreen.swift:273`'s card lives. Normal presentations take about a
/// third of a second, so a second and a half is quiet in every case that
/// works.
///
/// **What the fallback costs, which "a card late" did not say.** Inline is the
/// pre-answer-6 shape exactly: the 35% scrim over the page, with the whole
/// screen still behind it in the accessibility tree and still being sampled
/// through the scrim. So when it fires it hands back the contrast and tree
/// findings answer 6 removed — it trades a hard, visible failure (a blank
/// screen, a picture attached to a train) for an intermittent accessibility
/// regression that only an audit on that screen would notice (review 2). That
/// is the better trade for a person holding the phone and it is not free, so
/// it is written here, in ``DialogPresentationGuard`` and in DECISIONS §1
/// rather than left as a card arriving late. The way not to pay it at all is
/// the one-line Alerts fix the same §1 calls a merge blocker: with no cover
/// inside a cover there is no presentation to drop.
///
/// **This does not make the Alerts fix optional.** One line in
/// `AlertsHintRowView.body` ends the nesting itself, that file is not this
/// unit's, and
/// `design-notes/ios/batch-2026-11-03/shell/DECISIONS.md` §1 says it blocks
/// the merge. This is the guard under it, not a replacement for it.
struct DialogCard<Content: View>: View {

    @ViewBuilder let content: Content

    /// Set by the cover's own content the first time it appears. State, not a
    /// binding: the question is about this host's presentation and nothing
    /// outside it has an answer.
    @State private var coverAppeared = false

    /// Set when the deadline passed with no cover. Kept separately from
    /// ``coverAppeared`` so a cover that arrives late still takes the screen —
    /// it is opaque and on top — rather than racing one flag.
    @State private var presentationLost = false

    /// How long the presentation gets before the card is drawn inline —
    /// ``DialogPresentationGuard/lostAfterNanoseconds``, which is where the
    /// number and the reason for it are written and where `ShellFoldTests`
    /// reads them.
    private static var lostAfter: UInt64 { DialogPresentationGuard.lostAfterNanoseconds }

    var body: some View {
        ZStack {
            hostOfNothing
            // **The rule is ``DialogPresentationGuard``, not an `&&` here.**
            // Review 2: this guard had no test on any path, and what it
            // reproduces when it fires — the page still behind the card, which
            // is the pre-answer-6 shape — is the thing
            // `testShell_ADialogLeavesNothingBehindIt` forbids. The decision is
            // a value in the package now, where both paths have a test today,
            // and that case asserts the fallback did **not** fire by looking
            // for ``Self/inlineFallbackIdentifier``.
            if DialogPresentationGuard.cardIsInline(
                coverAppeared: coverAppeared,
                deadlinePassed: presentationLost
            ) {
                // `.contain` and not a bare identifier: an identifier on a
                // `ZStack` that is not itself an accessibility element can be
                // dropped from the tree, and a marker that is sometimes in the
                // tree is worse than none. A container keeps the card's own
                // leaves reachable — the card is what a person is reading —
                // and gives the marker an element of its own.
                card
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier(Self.inlineFallbackIdentifier)
            }
        }
        .fullScreenCover(isPresented: .constant(true)) {
            card.onAppear { coverAppeared = true }
        }
        .task {
            try? await Task.sleep(nanoseconds: Self.lostAfter)
            // A cancelled sleep means the dialog closed while we waited, and
            // writing state into a view that is going away is how a SwiftUI
            // warning becomes a habit.
            guard !Task.isCancelled else { return }
            if !coverAppeared { presentationLost = true }
        }
    }

    /// The identifier the **inline fallback** carries, and nothing else does
    /// — ``DialogPresentationGuard/inlineFallbackIdentifier``.
    ///
    /// It lives in the package because a **generic** type cannot hold a static
    /// stored property, and because the reason for it belongs beside the rule:
    /// a dropped presentation cannot be provoked from a UI case, so this is
    /// the other half of the reading. Every case that asserts there is nothing
    /// behind a dialog also asserts this is absent, which is what says the
    /// case measured the presentation and not the fallback. Without it, a
    /// branch that fired every time would show up only as a contrast finding
    /// somebody else's screen is blamed for.
    private static var inlineFallbackIdentifier: String {
        DialogPresentationGuard.inlineFallbackIdentifier
    }

    /// The host: it takes no hits and says nothing, so the `ZStack` a call
    /// site puts this in is the screen plus a presentation.
    private var hostOfNothing: some View {
        Color.clear
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    /// The card itself — the cover's content, and the inline fallback. One
    /// property rather than two copies, so the thing a person sees cannot
    /// differ between the path that works and the path that catches it.
    private var card: some View {
        ZStack {
            Color.black.opacity(0.35)
                .ignoresSafeArea()
                .accessibilityHidden(true)
            ScrollView {
                content
                    .padding(20)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.background, in: RoundedRectangle(cornerRadius: 14))
                    .padding(20)
            }
            // **The reason this type exists rather than a `ZStack` at each
            // call site.** Every dialog in the app gets the trait — and now
            // the presentation — from here, so a new one cannot be added
            // without them.
            .accessibilityAddTraits(.isModal)
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
                // Android's `OutlinedButton` (`ServerPicker.kt:395`): no
                // fill, so `cancel` is the ink on the card and not on a wash
                // of itself (that `.bordered` chip is 4.38:1 on white — unit
                // mx-ink, InkButtonStyles.swift; not opened by any case here:
                // this sheet starts a LAN sweep).
                Button(ShellStrings.trustCancel, action: onCancel)
                    .buttonStyle(InkOutlineButtonStyle())
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
                // **Apple's audit: "the accessibilityLabel of this element is
                // not human-readable"** (2026-10-30). It is the one place in
                // the app where an address is a bare line of text with
                // nothing beside it saying what it is — the pill and the
                // server rows are inside controls that name themselves — so
                // VoiceOver announced `127.0.0.1:6601` and no more. The text
                // on screen is unchanged: it is still the thing a person
                // compares with the dashboard character by character.
                // **`Text clipped` at AX5** (2026-10-30), which is what the
                // audit reported for this element once it had a readable
                // label: 286x131 points of an address that wraps, with no
                // `fixedSize`, while every sibling on the card has one.
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityLabel(forgetServerAddressLabel(ask.address))
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
                // Android's `OutlinedButton` (`ForgetServer.kt:100`), for the
                // trust card's reason above (unit mx-ink).
                Button(forgetServerCancel, action: onCancel)
                    .buttonStyle(InkOutlineButtonStyle())
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
