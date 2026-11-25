import DomovoiKit
import SwiftUI

/// Settings → Rooms: devices asking to join (``JoinApprovals``), the port of
/// `JoinRequestsCard.kt` (bd5c4b5e).
///
/// Shown only while this phone holds admin (design §1: no control that can't
/// work): signed in, it reads the requests every 15 s; with admin kept today
/// and no session, one tap asks for Face ID and then reads them. Never on a
/// shared screen. With nothing asking, it draws nothing.
struct JoinRequestsCard: View {

    let model: ShellModel

    @State private var joins: JoinRequestsModel?

    private var admin: AdminModel { model.admin }

    private var live: Bool {
        if case .signedIn = admin.state { return true }
        return false
    }

    var body: some View {
        if !model.visibility.sharedScreen, admin.state != .signedOut {
            Group {
                if let joins {
                    card(joins)
                } else {
                    Color.clear.frame(height: 0)
                }
            }
            .onAppear {
                if joins == nil { joins = JoinRequestsModel(admin: model.container.admin) }
            }
            // Signed in: the list, now and every 15 s, while this is on
            // screen. `.task` ends with the view, which ends the poll.
            .task(id: live) {
                guard live else { return }
                while !Task.isCancelled {
                    await joins?.load(viaAct: false)
                    try? await Task.sleep(nanoseconds: JoinApprovals.pollSeconds * 1_000_000_000)
                }
            }
        }
    }

    @ViewBuilder
    private func card(_ joins: JoinRequestsModel) -> some View {
        // Signed in with nothing asking: no card at all.
        if live, let list = joins.requests, list.isEmpty, joins.error == nil {
            EmptyView()
        } else {
            PanelCard(title: JoinApprovals.title, sub: JoinApprovals.sub) {
                if !live {
                    Button(JoinApprovals.checkWithFaceID) {
                        Task { @MainActor in await joins.load(viaAct: true) }
                    }
                    .buttonStyle(InkOutlineButtonStyle())
                } else if let list = joins.requests {
                    ForEach(Array(list.enumerated()), id: \.element.id) { index, request in
                        if index > 0 { Divider() }
                        JoinRequestRow(model: model, joins: joins, request: request, rooms: joins.rooms)
                    }
                } else {
                    Text(JoinApprovals.checking)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                }
                if let error = joins.error {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(Color.warnInk)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

/// One request: what is asking in household words, which room it is in, the
/// six digits, approve or turn away. Both buttons go through
/// ``AdminSession/act(_:)``, so a tap after the hour asks for Face ID first.
private struct JoinRequestRow: View {

    let model: ShellModel
    let joins: JoinRequestsModel
    let request: JoinRequest
    let rooms: [JoinRoom]

    @State private var choice: JoinChoice?
    @State private var newName = ""
    @State private var code = ""
    @State private var busy = false
    @State private var note: String?

    private var picked: JoinChoice { choice ?? JoinApprovals.preselect(request, rooms: rooms) }

    private var isNewRoom: Bool {
        if case .newRoom = picked { return true }
        return false
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(JoinApprovals.askingLine(request))
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
            if let status = JoinApprovals.status(request) {
                muted(status)
            }
            Picker(JoinApprovals.whichRoom, selection: Binding(get: { picked }, set: { choice = $0 })) {
                ForEach(JoinApprovals.choices(request, rooms: rooms), id: \.self) { c in
                    Text(label(c)).tag(c)
                }
            }
            .pickerStyle(.menu)
            .disabled(busy)
            if isNewRoom {
                TextField(JoinApprovals.newRoomField, text: Binding(
                    get: { newName },
                    set: { newName = String($0.prefix(60)) }
                ))
                .textFieldStyle(.roundedBorder)
                .disabled(busy)
                .accessibilityLabel(JoinApprovals.newRoomField)
            }
            TextField(JoinApprovals.codeField, text: Binding(
                get: { code },
                set: { code = JoinApprovals.cleanCode($0) }
            ))
            .textFieldStyle(.roundedBorder)
            .keyboardType(.numberPad)
            .textContentType(.oneTimeCode)
            .disabled(busy)
            .accessibilityLabel(JoinApprovals.codeField)
            HStack(spacing: 12) {
                Button(busy ? "…" : JoinApprovals.approve) { send(approve: true) }
                    .buttonStyle(BrandFillButtonStyle())
                    .disabled(busy || !JoinApprovals.codeOk(code)
                        || (isNewRoom && newName.trimmingCharacters(in: .whitespaces).isEmpty))
                Button(JoinApprovals.turnAway) { send(approve: false) }
                    .buttonStyle(InkOutlineButtonStyle(ink: .primary))
                    .disabled(busy)
            }
            if let note { muted(note) }
        }
        .onAppear {
            if newName.isEmpty { newName = JoinApprovals.newRoomName(request) }
        }
    }

    /// The picker's words: a room's name, or "A new room".
    private func label(_ c: JoinChoice) -> String {
        switch c {
        case .existing(_, let name): return name
        case .newRoom: return JoinApprovals.aNewRoom
        }
    }

    private func send(approve: Bool) {
        busy = true
        note = nil
        let sending: JoinChoice = isNewRoom ? .newRoom(name: newName) : picked
        let typed = code
        Task { @MainActor in
            let outcome = await joins.send(request, approve: approve, code: typed, choice: sending)
            busy = false
            switch outcome {
            case .done(let toast):
                code = ""
                // The row may leave the list on the next read: said as a toast.
                model.showToast(toast)
                await joins.load(viaAct: false)
            case .refused(let why):
                note = why
            }
        }
    }

    private func muted(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}
