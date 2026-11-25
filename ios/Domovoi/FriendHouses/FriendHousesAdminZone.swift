import DomovoiKit
import SwiftUI

/// Friends and connections on the phone (1.5.0; design §1's Friend Houses
/// jobs): accept or decline a friend request, remove or block a friend,
/// unblock a house. The port of `FriendHousesAdminZone.kt` (3ac999df).
///
/// Only while this phone holds admin: the tab is not there otherwise
/// (``fhTabsFor(_:sharedScreen:)``). Signed in, it reads at once; kept only,
/// "show with Face ID" reads through ``AdminSession/act(_:)``, which every
/// change goes through as well. Making or adding friend files, this house's
/// name, renaming, verifying and apps stay on the dashboard
/// (``FhAdminWords/onDashboard``).
///
/// Every name and `last_error` came from another house: shown only through
/// ``FhPlainText``, as the rest of the folder (`FriendHousesPlainTextTests`).
struct FhConnectionsTab: View {

    let model: ShellModel
    let onChanged: () -> Void

    @State private var zone: FriendHousesAdminModel?
    @State private var ask: FhAdminAsk?
    @State private var blocking: FhFriend?

    private var live: Bool {
        if case .signedIn = model.admin.state { return true }
        return false
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            FhMutedLine(text: FhAdminWords.sub)
            if let zone {
                content(zone)
            }
            FhMutedLine(text: FhAdminWords.onDashboard)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear {
            if zone == nil { zone = FriendHousesAdminModel(admin: model.container.admin) }
        }
        .task(id: live) {
            if zone == nil { zone = FriendHousesAdminModel(admin: model.container.admin) }
            if live { await zone?.load(viaAct: false) }
        }
        .alert(
            Text(verbatim: ask?.title ?? ""),
            isPresented: Binding(get: { ask != nil }, set: { shown in if !shown { ask = nil } }),
            presenting: ask
        ) { asked in
            Button(FhAdminWords.cancel, role: .cancel) { ask = nil }
            Button(asked.confirm, role: asked.destructive ? .destructive : nil) {
                ask = nil
                run(asked.work)
            }
        } message: { asked in
            Text(verbatim: asked.body)
        }
    }

    @ViewBuilder
    private func content(_ zone: FriendHousesAdminModel) -> some View {
        if !live {
            Button(FhAdminWords.unlock) {
                Task { @MainActor in await zone.load(viaAct: true) }
            }
            .buttonStyle(InkOutlineButtonStyle())
            .disabled(zone.busy)
        }
        if let problem = zone.problem {
            FhProblemLine(text: problem)
        }
        if let requests = zone.requests {
            card(FhAdminWords.requestsTitle) {
                if requests.isEmpty { FhMutedLine(text: FhAdminWords.noRequests) }
                ForEach(Array(requests.enumerated()), id: \.element.id) { index, r in
                    if index > 0 { Divider() }
                    FhRequestRow(request: r, busy: zone.busy) { name in
                        run { await zone.accept(r, name: name) }
                    } onDecline: {
                        ask = FhAdminAsk(
                            title: FhAdminWords.declineTitle, body: FhAdminWords.declineBody(r),
                            confirm: FhAdminWords.decline, destructive: true
                        ) { await zone.decline(r) }
                    }
                }
            }
            card(FhAdminWords.friendsTitle) {
                if zone.friends.isEmpty { FhMutedLine(text: FhAdminWords.noFriends) }
                ForEach(Array(zone.friends.enumerated()), id: \.element.id) { index, f in
                    if index > 0 { Divider() }
                    friendRow(f, zone: zone)
                }
            }
            if !zone.blocked.isEmpty {
                card(FhAdminWords.blockedTitle) {
                    ForEach(Array(zone.blocked.enumerated()), id: \.element.house) { index, b in
                        if index > 0 { Divider() }
                        blockedRow(b, zone: zone)
                    }
                }
            }
        }
    }

    private func card<Content: View>(_ title: String, @ViewBuilder _ rows: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            FhPlainText(text: title, font: .headline)
                .accessibilityAddTraits(.isHeader)
            rows()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private func friendRow(_ f: FhFriend, zone: FriendHousesAdminModel) -> some View {
        let name = FhAdminWords.houseName(f.name)
        VStack(alignment: .leading, spacing: 6) {
            FhRowOrColumn {
                FhPlainText(text: name)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } trailing: {
                HStack(spacing: 8) {
                    Button(FhAdminWords.remove) {
                        ask = FhAdminAsk(
                            title: FhAdminWords.removeTitle(name), body: FhAdminWords.removeBody,
                            confirm: FhAdminWords.remove, destructive: true
                        ) { await zone.remove(f) }
                    }
                    .buttonStyle(InkOutlineButtonStyle(ink: .primary))
                    Button(FhAdminWords.block) { blocking = (blocking == f) ? nil : f }
                        .buttonStyle(InkOutlineButtonStyle(ink: .errInk))
                }
                .disabled(zone.busy)
            }
            // Block asks its two questions here, under the row: an alert
            // can't hold a switch.
            if blocking == f {
                FhBlockPanel(name: name) { keep, move in
                    blocking = nil
                    run { await zone.block(f, keepMessages: keep, newAddress: move) }
                } onCancel: {
                    blocking = nil
                }
            }
        }
    }

    private func blockedRow(_ b: FhBlockedHouse, zone: FriendHousesAdminModel) -> some View {
        let name = FhAdminWords.houseName(b.name)
        return FhRowOrColumn {
            FhPlainText(text: name)
                .frame(maxWidth: .infinity, alignment: .leading)
        } trailing: {
            Button(FhAdminWords.unblock) {
                ask = FhAdminAsk(
                    title: FhAdminWords.unblockTitle(name), body: FhAdminWords.unblockBody(name),
                    confirm: FhAdminWords.unblock, destructive: false
                ) { await zone.unblock(b) }
            }
            .buttonStyle(InkOutlineButtonStyle(ink: .primary))
            .disabled(zone.busy)
        }
    }

    /// One change; its toast is the shell's, and the household tabs read
    /// again after it.
    private func run(_ work: @escaping @MainActor () async -> String?) {
        Task { @MainActor in
            if let said = await work() {
                model.showToast(said)
                onChanged()
            }
        }
    }
}

/// A question asked before a change: decline, remove, unblock.
struct FhAdminAsk {
    let title: String
    let body: String
    let confirm: String
    let destructive: Bool
    let work: @MainActor () async -> String?
}

/// One request: waiting here (the name this house calls them, accept or
/// decline), accepted (telling them now), or ours (its state).
struct FhRequestRow: View {

    let request: FhRequest
    let busy: Bool
    let onAccept: (String) -> Void
    let onDecline: () -> Void

    @State private var name: String?

    private var typed: String { name ?? String((request.theirName ?? "").prefix(60)) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if request.waitingHere {
                FhPlainText(text: FhAdminWords.incoming(request))
                if let code = request.code {
                    FhMutedLine(text: FhAdminWords.code(code))
                }
                TextField(FhAdminWords.nameField, text: Binding(
                    get: { typed },
                    set: { name = String($0.prefix(60)) }
                ))
                .textFieldStyle(.roundedBorder)
                .disabled(busy)
                .accessibilityLabel(Text(verbatim: FhAdminWords.nameField))
                HStack(spacing: 8) {
                    Button(FhAdminWords.accept) { onAccept(typed) }
                        .buttonStyle(BrandFillButtonStyle())
                        .disabled(busy || typed.trimmingCharacters(in: .whitespaces).isEmpty)
                    Button(FhAdminWords.decline, action: onDecline)
                        .buttonStyle(InkOutlineButtonStyle(ink: .primary))
                        .disabled(busy)
                }
            } else if request.direction == "in" {
                FhMutedLine(text: FhAdminWords.accepted(request))
            } else {
                FhMutedLine(text: FhAdminWords.outgoing(request))
                if request.state == "unsent", let error = request.lastError {
                    FhPlainText(text: error, font: .footnote, colour: Color.fgMuted)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

/// Block's two questions, both on by default as the dashboard's: keep the
/// messages, and move this house to a new private address.
struct FhBlockPanel: View {

    let name: String
    let onBlock: (_ keepMessages: Bool, _ newAddress: Bool) -> Void
    let onCancel: () -> Void

    @State private var keep = true
    @State private var move = true

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            FhPlainText(text: FhAdminWords.blockTitle(name), font: .subheadline, weight: .semibold)
            FhMutedLine(text: FhAdminWords.blockBody(name))
            Toggle(isOn: $keep) { FhPlainText(text: FhAdminWords.keepMessages, font: .footnote) }
                .frame(minHeight: 44)
            Toggle(isOn: $move) { FhPlainText(text: FhAdminWords.newAddress, font: .footnote) }
                .frame(minHeight: 44)
            HStack(spacing: 8) {
                Button(FhAdminWords.block) { onBlock(keep, move) }
                    .buttonStyle(DangerFillButtonStyle())
                Button(FhAdminWords.cancel, action: onCancel)
                    .buttonStyle(InkOutlineButtonStyle(ink: .primary))
            }
        }
        .padding(12)
        .background(Color.brand.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }
}
