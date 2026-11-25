import DomovoiKit
import SwiftUI

/// Rename, beside the name on the profile (admin held): the box, save,
/// cancel. A taken name keeps the box open with what was typed; the toast
/// says why. The port of `PersonRename` (`PeopleAdmin.kt`).
struct PersonRenameRow: View {

    let person: Person
    let zone: PeopleAdminModel
    let say: @MainActor (String) -> Void
    let onRenamed: @MainActor () -> Void

    @State private var editing = false
    @State private var draft = ""

    var body: some View {
        if editing {
            VStack(alignment: .leading, spacing: 6) {
                TextField(PeopleAdminWords.newName, text: Binding(
                    get: { draft },
                    set: { draft = String($0.prefix(60)) }
                ))
                .textFieldStyle(.roundedBorder)
                .autocorrectionDisabled()
                .submitLabel(.done)
                .onSubmit(save)
                .disabled(zone.busy)
                .accessibilityLabel(PeopleAdminWords.newName)
                HStack(spacing: 8) {
                    Button(PeopleAdminWords.save, action: save)
                        .buttonStyle(BrandFillButtonStyle())
                    Button(PeopleAdminWords.cancel) { editing = false }
                        .buttonStyle(InkOutlineButtonStyle(ink: .primary))
                }
                .font(.subheadline)
                .disabled(zone.busy)
            }
        } else {
            Button(PeopleAdminWords.rename) {
                draft = person.name
                editing = true
            }
            .buttonStyle(InkOutlineButtonStyle())
            .font(.subheadline)
        }
    }

    private func save() {
        guard let name = peopleRenameTo(current: person.name, draft: draft) else {
            editing = false
            return
        }
        Task { @MainActor in
            let out = await zone.rename(person, to: name)
            say(out.said)
            if out.ok {
                editing = false
                onRenamed()
            }
        }
    }
}

/// "forget person" and the dashboard's confirm, typing the name included
/// (admin held). The port of `PersonForget` (`PeopleAdmin.kt`).
struct PersonForgetButton: View {

    let person: Person
    let sessionCount: Int
    let turnCount: Int
    let zone: PeopleAdminModel
    let say: @MainActor (String) -> Void
    let onForgotten: @MainActor () -> Void

    @State private var asking = false
    @State private var typed = ""

    var body: some View {
        Button(PeopleAdminWords.forget) {
            typed = ""
            asking = true
        }
        .buttonStyle(InkOutlineButtonStyle(ink: .errInk))
        .font(.subheadline)
        .disabled(zone.busy)
        .sheet(isPresented: $asking) {
            confirm
                .presentationDetents([.medium, .large])
        }
    }

    private var confirm: some View {
        let ok = peopleForgetConfirmed(typed: typed, name: person.name)
        return ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text(verbatim: PeopleAdminWords.forgetTitle(person))
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(verbatim: PeopleAdminWords.forgetBody(person, sessionCount: sessionCount, turnCount: turnCount))
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Text(verbatim: PeopleAdminWords.typeToConfirm(person.name))
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                TextField("", text: $typed)
                    .textFieldStyle(.roundedBorder)
                    .font(.body.monospaced())
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityLabel(PeopleAdminWords.typeToConfirm(person.name))
                HStack(spacing: 8) {
                    Button(PeopleAdminWords.cancel) { asking = false }
                        .buttonStyle(InkOutlineButtonStyle(ink: .primary))
                    Button(PeopleAdminWords.forgetButton(person.name)) {
                        asking = false
                        Task { @MainActor in
                            let out = await zone.forget(person)
                            say(out.said)
                            if out.ok { onForgotten() }
                        }
                    }
                    .buttonStyle(DangerFillButtonStyle())
                    .disabled(!ok || zone.busy)
                }
                .font(.subheadline)
            }
            .padding(20)
        }
    }
}
