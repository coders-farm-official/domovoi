import DomovoiKit
import SwiftUI

/// Which group form is open: a new group, or a group being renamed or
/// re-roomed.
enum GroupFormTarget: Equatable {
    case new
    case group(Int64)

    var groupID: Int64? {
        if case .group(let id) = self { return id }
        return nil
    }
}

/// Settings → Rooms' **Groups** card (V060) — the web's `MusicGroupsCard`
/// (`web/static/satellites.jsx` ~:2483), in its words: named sets of rooms
/// the household makes ("upstairs"), so "play jazz upstairs" plays in all of
/// them at once. Made, renamed, re-roomed and deleted here; there is no
/// screen of its own. "Everywhere" needs no group.
///
/// **Household only.** The whole card is absent for a guest, for a phone the
/// server refused (401/403), and on a server older than groups (404) —
/// silently, never as an error (``MusicGroupsModel/access(paired:)``).
///
/// A save the server refuses with a 400 says the server's own sentence, as
/// it is ("A group's name can't use words like station, radio or playlist,
/// …"), on the shell's toast, and the form stays open with what was typed.
struct RoomsGroupsCard: View {

    let shell: ShellModel
    let groups: MusicGroupsModel

    /// The house's room ids, unique and sorted.
    let roomIDs: [String]

    @State private var editing: GroupFormTarget?
    @State private var name = ""
    @State private var picked: Set<String> = []
    @State private var deleting: MusicGroup?
    @FocusState private var nameFocused: Bool

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if groups.access(paired: shell.isPaired).showsControls {
            HomeCard(
                title: GroupStrings.title,
                actionLabel: offersNew ? GroupStrings.newGroup : nil,
                onAction: { open(nil) }
            ) {
                Text(GroupStrings.help)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if editing == .new {
                    form(for: nil)
                }
                if groups.groups.isEmpty, editing != .new {
                    Text(roomIDs.count >= 2 ? GroupStrings.noGroupsYet : GroupStrings.needTwoRooms)
                        .font(.subheadline)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                ForEach(groups.groups) { group in
                    if editing == .group(group.id) {
                        form(for: group)
                    } else {
                        row(group)
                    }
                }
            }
            .alert(
                GroupStrings.deleteTitle(deleting?.name ?? ""),
                isPresented: Binding(
                    get: { deleting != nil },
                    set: { if !$0 { deleting = nil } }
                ),
                presenting: deleting
            ) { group in
                Button(GroupStrings.delete, role: .destructive) { remove(group) }
                Button(GroupStrings.cancel, role: .cancel) { deleting = nil }
            } message: { _ in
                Text(GroupStrings.deleteBody)
            }
        }
    }

    /// "new group": no form open, two rooms to put in one, nothing in flight.
    private var offersNew: Bool {
        editing == nil && roomIDs.count >= 2 && !groups.busy
    }

    // MARK: - a group

    private func row(_ group: MusicGroup) -> some View {
        HStack(alignment: .center, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(group.name)
                    .font(.subheadline.weight(.medium))
                    .fixedSize(horizontal: false, vertical: true)
                Text(group.rooms.joined(separator: ", "))
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
            Spacer(minLength: 8)
            iconButton("pencil", label: GroupStrings.editLabel(group.name)) { open(group) }
            iconButton("trash", label: GroupStrings.deleteLabel(group.name)) { deleting = group }
        }
    }

    private func iconButton(_ glyph: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: glyph)
                .font(.body)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .disabled(groups.busy || editing != nil)
        .accessibilityLabel(label)
    }

    // MARK: - the form (`MusicGroupForm`)

    private func form(for group: MusicGroup?) -> some View {
        let rooms = groupFormRooms(house: roomIDs, group: group)
        let ready = groupFormReady(name: name, rooms: picked, busy: groups.busy)
        return VStack(alignment: .leading, spacing: 10) {
            Text(GroupStrings.nameLabel)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .accessibilityHidden(true)
            nameField
            Text(GroupStrings.roomsLabel)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(rooms, id: \.self) { room in
                roomPick(room)
            }
            buttons(ready: ready)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
    }

    /// The group's name — **a different control at an accessibility size**,
    /// as ``AudiobooksView``'s search box: one line with a `done` key at the
    /// ordinary sizes, a field that grows with the type at the accessibility
    /// ones (where a vertical field's Return would put in a newline, so the
    /// newline is taken as "done" instead). Either way Return puts the
    /// keyboard away, so the room ticks and save under it can be reached.
    /// A name is one line of at most 40 characters (the server's
    /// `max_length`).
    @ViewBuilder
    private var nameField: some View {
        let typed = Binding(
            get: { name },
            set: { new in
                if new.contains(where: \.isNewline) { nameFocused = false }
                let flat = String(new.filter { !$0.isNewline })
                name = String(flat.prefix(40))
            }
        )
        Group {
            if typeSize.isAccessibilitySize {
                TextField(
                    GroupStrings.nameFieldLabel,
                    text: typed,
                    prompt: ShellHintPrompt.text(GroupStrings.namePlaceholder),
                    axis: .vertical
                )
                .lineLimit(1...)
            } else {
                TextField(
                    GroupStrings.nameFieldLabel,
                    text: typed,
                    prompt: ShellHintPrompt.text(GroupStrings.namePlaceholder)
                )
                .submitLabel(.done)
                .onSubmit { nameFocused = false }
            }
        }
        .focused($nameFocused)
        .font(.subheadline)
        .textInputAutocapitalization(.never)
        .padding(.horizontal, 8)
        .padding(.vertical, 10)
        .frame(minHeight: 44)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
        .overlay {
            RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary)
        }
        .accessibilityLabel(GroupStrings.nameFieldLabel)
    }

    /// One room, picked or not: a button with a tick, so the picked state is
    /// a word to VoiceOver (selected) and not a colour.
    private func roomPick(_ room: String) -> some View {
        let on = picked.contains(room)
        return Button {
            if on { picked.remove(room) } else { picked.insert(room) }
        } label: {
            HStack(spacing: 8) {
                Image(systemName: on ? "checkmark.square.fill" : "square")
                    .accessibilityHidden(true)
                Text(room)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .accessibilityLabel(room)
        .accessibilityAddTraits(on ? .isSelected : [])
        // A fixed handle for UI tests: the roster's own cards carry the
        // room's id in their labels too.
        .accessibilityIdentifier("rooms.groups.pick.\(room)")
    }

    @ViewBuilder
    private func buttons(ready: Bool) -> some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 8) {
                saveButton(ready: ready)
                cancelButton
            }
        } else {
            HStack(spacing: 8) {
                Spacer(minLength: 0)
                cancelButton
                saveButton(ready: ready)
            }
        }
    }

    private var cancelButton: some View {
        Button(GroupStrings.cancel) { editing = nil }
            .buttonStyle(InkOutlineButtonStyle())
            .font(.subheadline)
            .disabled(groups.busy)
    }

    private func saveButton(ready: Bool) -> some View {
        Button(GroupStrings.save) { save() }
            .buttonStyle(BrandFillButtonStyle())
            .font(.subheadline)
            .disabled(!ready)
    }

    // MARK: - the writes

    private func open(_ group: MusicGroup?) {
        name = group?.name ?? ""
        picked = Set(group?.rooms ?? [])
        editing = group.map { GroupFormTarget.group($0.id) } ?? .new
    }

    private func save() {
        guard let target = editing else { return }
        let sentName = name
        let rooms = Array(picked)
        let model = groups
        Task { @MainActor in
            guard let said = await model.save(editing: target.groupID, name: sentName, rooms: rooms) else { return }
            shell.showToast(said.text)
            // The form closes on a save that worked — the one it was for.
            if said.ok, editing == target { editing = nil }
        }
    }

    private func remove(_ group: MusicGroup) {
        deleting = nil
        let model = groups
        Task { @MainActor in
            guard let said = await model.delete(group) else { return }
            shell.showToast(said.text)
        }
    }
}
