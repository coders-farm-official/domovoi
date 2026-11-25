import DomovoiKit
import SwiftUI
import UIKit

/// Settings → Household → Admin on this phone (1.5.0; ``AdminSession``):
/// the port of `AdminPanel.kt` (bd5c4b5e).
///
/// The admin password signs this phone in for admin jobs; with the box
/// ticked, the phone keeps admin for the rest of the day behind Face ID (the
/// day's grant, ``KeychainGrantLocker``). Never on a shared screen (the
/// section is not even listed there, ``SettingsSection/sharedScreenHiddenKeys``)
/// and never in room mode. What the jobs are grows one area at a time; the
/// panel says which ones work from the phone today (``AdminStrings/jobs``).
///
/// Every word is ``AdminStrings``'; what each button does, and what it says
/// afterwards, is ``AdminModel``'s, so both are tested in the package.
struct AdminPanel: View {

    let model: ShellModel

    @State private var password = ""
    @State private var keep = true

    private var admin: AdminModel { model.admin }
    private var shared: Bool { model.visibility.sharedScreen }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            signInCard
            // The phones blocked from queues or files, with unblock.
            if !HouseholdLock.shared.roomModeActive, !ServerCredentials.isBlank(model.inputs.serverURL),
               deletesAdminHeld(admin.state, sharedScreen: shared) {
                BlockedPhonesCard(model: model)
            }
        }
    }

    private var signInCard: some View {
        PanelCard(title: AdminStrings.title, sub: AdminStrings.sub) {
            if shared {
                muted(AdminStrings.shared)
            } else if HouseholdLock.shared.roomModeActive {
                muted(AdminSession.roomMode)
            } else if ServerCredentials.isBlank(model.inputs.serverURL) {
                muted(AdminStrings.connectFirst)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    Text(AdminStrings.stateLine(admin.state))
                        .font(.body)
                        .fixedSize(horizontal: false, vertical: true)
                    controls
                    if let message = admin.message {
                        Text(message)
                            .font(.footnote)
                            .foregroundStyle(Color.warnInk)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    muted(AdminStrings.jobs)
                }
            }
        }
        // The server may have changed since the state was last worked out.
        .task(id: ServerCredentials.normalize(model.inputs.serverURL)) { admin.refresh() }
    }

    @ViewBuilder
    private var controls: some View {
        switch admin.state {
        case .signedOut:
            SecureField(AdminStrings.passwordLabel, text: $password)
                .textFieldStyle(.roundedBorder)
                .textContentType(.password)
                .submitLabel(.go)
                .onSubmit(signIn)
                .disabled(admin.busy)
                .accessibilityLabel(AdminStrings.passwordLabel)
            if admin.canKeepToday {
                Toggle(AdminStrings.keepToggle, isOn: $keep)
                    .font(.body)
                    .disabled(admin.busy)
                    .frame(minHeight: 44)
            } else {
                muted(AdminStrings.noBiometrics)
            }
            Button(admin.busy ? AdminStrings.signingIn : AdminStrings.signIn, action: signIn)
                .buttonStyle(BrandFillButtonStyle())
                .disabled(admin.busy || password.isEmpty)
        case .signedIn(_, _, let keptUntil):
            HStack(spacing: 12) {
                if keptUntil == nil, admin.canKeepToday {
                    Button(AdminStrings.keepToday) { run { await admin.keepToday(label: label) } }
                        .buttonStyle(InkOutlineButtonStyle())
                }
                Button(AdminStrings.signOut) { run { await admin.signOut() } }
                    .buttonStyle(InkOutlineButtonStyle(ink: .primary))
            }
            .disabled(admin.busy)
        case .kept:
            HStack(spacing: 12) {
                Button(AdminStrings.goOn) { run { await admin.resume() } }
                    .buttonStyle(BrandFillButtonStyle())
                Button(AdminStrings.signOut) { run { await admin.signOut() } }
                    .buttonStyle(InkOutlineButtonStyle(ink: .primary))
            }
            .disabled(admin.busy)
        }
    }

    /// What the dashboard's session list shows for this phone.
    private var label: String { AdminStrings.label(model: UIDevice.current.model) }

    private func signIn() {
        guard !password.isEmpty, !admin.busy else { return }
        // The password leaves the field at once: it is never kept.
        let typed = password
        password = ""
        let keepIt = keep
        run { await admin.signIn(password: typed, keep: keepIt, label: label) }
    }

    private func run(_ work: @escaping @MainActor () async -> Void) {
        Task { @MainActor in await work() }
    }

    private func muted(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// The top bar's "admin" badge (design §1): filled while signed in, muted
/// while admin is kept today, absent when signed out or on a shared screen
/// (``AdminBadge``). A tap opens Settings → Household → Admin on this phone.
struct AdminBadgeView: View {

    let model: ShellModel

    var body: some View {
        if let badge = model.admin.badge(sharedScreen: model.visibility.sharedScreen) {
            AdminBadgeButton(badge: badge) { model.openSettingsTab("admin-on-phone") }
        }
    }
}

/// The badge itself, for the bar and for the toolbar at the accessibility
/// sizes (``ShellScrollAwayChrome``).
struct AdminBadgeButton: View {

    let badge: AdminBadge
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            Text(AdminStrings.badge)
                .font(.caption2)
                .foregroundStyle(badge == .filled ? Color.brandFg : Color.fgMuted)
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(
                    badge == .filled ? AnyShapeStyle(Color.brand) : AnyShapeStyle(.quaternary),
                    in: Capsule()
                )
                // The frame is the space and `contentShape` the target, as
                // the pill's toolbar twin and the phone bar's tabs have it.
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(AdminStrings.badgeAccessibility)
    }
}

/// Settings → Admin on this phone: the blocked phones, each with unblock
/// (no confirm, as the dashboard). The port of Android's
/// `BlockedPhonesCard` (`AdminDeletes.kt`): read with the session; kept
/// only, one tap reads them through Face ID.
struct BlockedPhonesCard: View {

    let model: ShellModel

    @State private var zone: AdminDeletesModel?

    private var live: Bool {
        if case .signedIn = model.admin.state { return true }
        return false
    }

    var body: some View {
        PanelCard(title: AdminDeleteWords.blocksTitle, sub: AdminDeleteWords.blocksSub) {
            VStack(alignment: .leading, spacing: 12) {
                if let zone {
                    if zone.queueBlocks == nil, !live {
                        Button(AdminDeleteWords.unlock) {
                            Task { @MainActor in await zone.loadBlocks(viaAct: true) }
                        }
                        .buttonStyle(InkOutlineButtonStyle())
                        .disabled(zone.busy)
                    }
                    if let problem = zone.problem {
                        Text(problem).font(.footnote).foregroundStyle(Color.warnInk)
                    }
                    if let q = zone.queueBlocks { section(zone, queue: true, q) }
                    if let f = zone.filesBlocks { section(zone, queue: false, f) }
                }
            }
        }
        .task(id: live) {
            if zone == nil { zone = AdminDeletesModel(admin: model.container.admin) }
            if live { await zone?.loadBlocks(viaAct: false) }
        }
    }

    private func section(_ zone: AdminDeletesModel, queue: Bool, _ list: [DeviceBlock]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(queue ? AdminDeleteWords.queueTitle : AdminDeleteWords.filesTitle)
                .font(.subheadline.weight(.semibold))
            if list.isEmpty {
                Text(queue ? AdminDeleteWords.queueNone : AdminDeleteWords.filesNone)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(list) { b in
                HStack(alignment: .center, spacing: 8) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(verbatim: b.who).font(.body)
                        let detail = blockDetail(b, queue: queue) { relTime($0, nowMs: Int64(Date().timeIntervalSince1970 * 1000)) }
                        if !detail.isEmpty {
                            Text(verbatim: detail)
                                .font(.footnote)
                                .foregroundStyle(Color.fgMuted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer(minLength: 8)
                    Button(AdminDeleteWords.unblock) {
                        Task { @MainActor in model.showToast(await zone.lift(queue: queue, b)) }
                    }
                    .buttonStyle(InkOutlineButtonStyle())
                    .disabled(zone.busy)
                }
                .frame(minHeight: 44)
            }
        }
    }
}
