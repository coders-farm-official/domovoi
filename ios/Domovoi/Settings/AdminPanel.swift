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
            Button {
                model.openSettingsTab("admin-on-phone")
            } label: {
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
}
