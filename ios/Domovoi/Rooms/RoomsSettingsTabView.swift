import DomovoiKit
import SwiftUI

/// The settings tab: quiet hours, which this phone may change, and the room's
/// configuration, which an admin on this phone may change too.
///
/// `SatQuietHours.kt:99` and `SatSettingsTab.kt:63`.
///
/// ### Who may change the configuration
///
/// `PATCH …/{room}/config` is **admin security** (`satellites.py:1712-1716`)
/// while the GET beside it is device read (`:1639-1647`). So the editor —
/// a switch per `bool`, a menu per `choice`, a field per `int`/`float`/`str`,
/// and `Save and restart` after the dashboard's confirm — is drawn only while
/// this phone holds admin and the screen is not a shared one
/// (``roomAdminHeld(_:sharedScreen:)``), and saves through
/// ``AdminSession/act(_:)``. Otherwise every field is **words**
/// (``roomsConfigValueText(_:)``) under one sentence saying where they are
/// changed: ``RoomAdminWords/configOrPhone``, or on a shared screen
/// ``RoomsStrings/configIsReadOnly``.
///
/// The Advanced fields stay words even with admin
/// (``RoomAdminWords/advancedOnDashboard``), as does a field the room's
/// software doesn't have yet (``RoomsConfigField/editableHere``).
///
/// Quiet hours are the exception and are fully drawn: `PUT …/quiet-hours` is
/// device tier (`:1425-1432`).
struct RoomsSettingsTabView: View {

    /// For admin: its state, whether this is a shared screen, the session.
    let shell: ShellModel
    let model: RoomsModel
    let reads: RoomsRoomModel
    let room: RoomsSatellite

    @State private var roomAdmin: RoomAdminModel?
    /// What has been changed and not saved, by field name.
    @State private var edits: [String: RoomsConfigValue] = [:]
    /// What the last save refused, and why, by field name.
    @State private var rejected: [String: String] = [:]
    @State private var confirmSave = false

    private var adminHeld: Bool {
        roomAdminHeld(shell.admin.state, sharedScreen: shell.visibility.sharedScreen)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            quietHours
            config
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task(id: adminHeld) {
            if roomAdmin == nil { roomAdmin = RoomAdminModel(admin: shell.container.admin) }
        }
        .onChange(of: room.roomID) { _, _ in
            edits = [:]
            rejected = [:]
        }
        .alert(RoomAdminWords.save, isPresented: $confirmSave) {
            Button(RoomAdminWords.saveConfirmButton) { save() }
            Button(RoomAdminWords.cancel, role: .cancel) {}
        } message: {
            Text(RoomAdminWords.saveConfirm(room.roomID))
        }
    }

    // MARK: - quiet hours (`SatQuietHoursSection`, `SatQuietHours.kt:99`)

    private var quietHours: some View {
        VStack(alignment: .leading, spacing: 8) {
            RoomsSectionLabel(text: RoomsStrings.quietHoursLabel)
            // Three choices as a column of rows rather than a segmented
            // control: "Same as the house" and "Off for this room" do not fit
            // a phone's width between them, and `minimumScaleFactor` is
            // rejected throughout this app.
            ForEach(RoomsQuietChoice.all) { choice in
                quietChoice(choice)
            }
            if reads.quietMode == "custom" {
                customHours
            }
            Text(RoomsStrings.quietHelp)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// One mode.
    ///
    /// Picking `house` or `off` saves at once; picking `custom` saves
    /// **nothing** on its own, because the two times are what it means and
    /// they may not be clock times yet (`SatQuietHours.kt:150-152`).
    private func quietChoice(_ choice: RoomsQuietChoice) -> some View {
        let picked = reads.quietMode == choice.id
        return Button {
            reads.quietModeDraft = choice.id
            if choice.id != "custom" {
                model.saveQuietHours(room, mode: choice.id)
            }
        } label: {
            HStack(spacing: 8) {
                // The glyph is not the only carrier: the trait below says
                // `selected` as well, because a tick is invisible to
                // VoiceOver.
                Image(systemName: picked ? "largecircle.fill.circle" : "circle")
                    .font(.body)
                    .accessibilityHidden(true)
                Text(choice.label)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            }
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(picked ? Color.brandInk : Color.primary)
        .accessibilityAddTraits(picked ? [.isButton, .isSelected] : .isButton)
        .disabled(reads.busy != nil)
    }

    /// `from` and `to`, and the one `Save`.
    ///
    /// **Nothing is sent unless both are 24-hour clock times**:
    /// ``roomsQuietBody(mode:start:end:)`` returns nil and
    /// ``RoomsStrings/timesLookLike`` goes up as a toast instead. A screen
    /// that posted `7:5` would leave the server to decide what it meant.
    private var customHours: some View {
        VStack(alignment: .leading, spacing: 8) {
            clockField(
                RoomsStrings.quietFrom,
                text: Binding(get: { reads.quietStartDraft }, set: { reads.quietStartDraft = $0 })
            )
            clockField(
                RoomsStrings.quietTo,
                text: Binding(get: { reads.quietEndDraft }, set: { reads.quietEndDraft = $0 })
            )
            Button(RoomsStrings.quietSave) {
                model.saveQuietHours(room, mode: "custom")
            }
            .buttonStyle(BrandFillButtonStyle())
            .font(.subheadline)
            .fixedSize(horizontal: false, vertical: true)
            .frame(minHeight: 44)
            .contentShape(.rect)
            .disabled(reads.busy != nil)
        }
    }

    private func clockField(_ label: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // A single-line field on purpose: `22:00` is five characters and
            // cannot clip, and a vertical axis would take the Return key's
            // meaning with it for nothing.
            TextField(RoomsStrings.timesLookLike, text: text)
                .textFieldStyle(.roundedBorder)
                .font(.subheadline.monospacedDigit())
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.numbersAndPunctuation)
                .frame(minHeight: 44)
                .accessibilityLabel(label)
        }
    }

    // MARK: - the room's config, read-only (`SatSettingsTab.kt:63`)

    @ViewBuilder
    private var config: some View {
        VStack(alignment: .leading, spacing: 8) {
            RoomsSectionLabel(text: RoomsStrings.tabSettings)
            if let answer = reads.config.data {
                if !answer.reported || answer.fields.isEmpty {
                    // Android's wording, with its hint replaced: "the room
                    // must be online to **edit** its config" would promise a
                    // control this screen does not have.
                    Text(RoomsStrings.settingsUnavailable)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(RoomsStrings.settingsNeedsOnlineRoom)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    let held = adminHeld
                    Text(
                        held ? RoomAdminWords.configEdit
                            // A shared screen never holds admin: its words stay.
                            : shell.visibility.sharedScreen ? RoomsStrings.configIsReadOnly
                            : RoomAdminWords.configOrPhone
                    )
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    let split = roomsConfigSplit(answer.fields)
                    ForEach(RoomsConfigGroup.of(split.common)) { group in
                        groupRows(group.id, group.fields, editing: held)
                    }
                    if held {
                        saveRow(answer.fields)
                    }
                    if !split.advanced.isEmpty {
                        // Android puts the advanced fields behind a disclosure.
                        // They are words here even with admin, and then the
                        // disclosure says why.
                        DisclosureGroup(RoomsStrings.advancedGroup) {
                            if held {
                                Text(RoomAdminWords.advancedOnDashboard)
                                    .font(.footnote)
                                    .foregroundStyle(Color.fgMuted)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            ForEach(RoomsConfigGroup.of(split.advanced)) { group in
                                groupRows(group.id, group.fields, editing: false)
                            }
                        }
                        .font(.subheadline)
                        .tint(Color.brandInk)
                    }
                }
            } else if let why = reads.config.error {
                Text(why)
                    .font(.subheadline)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .accessibilityLabel(RoomsStrings.tabSettings)
            }
        }
    }

    /// One group of fields, in **the order the server listed them**
    /// (``roomsConfigGroups(_:)``): a settings tab whose sections move between
    /// reads is one nobody can scan.
    private func groupRows(_ group: String, _ fields: [RoomsConfigField], editing: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            RoomsSectionLabel(text: group)
            ForEach(fields) { field in
                if editing && field.editableHere {
                    editorRow(field)
                } else {
                    readRow(field)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 12, style: .continuous)
        )
    }

    private func readRow(_ field: RoomsConfigField) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                RoomsReadRow(
                    label: field.label ?? field.name,
                    value: roomsConfigValueText(field)
                )
                // `restart` on a field that only takes effect after
                // one (`SatSettingsTab.kt:307`). A fact, and on this
                // screen it is the only thing the tier can be.
                if field.tier == "restart" {
                    HomeTonePill(label: RoomsStrings.restartTier, tone: .warn)
                }
            }
            if let help = field.help, !help.isEmpty {
                Text(help)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - the editor (admin held; `EditableFieldRow`, `SatSettingsTab.kt`)

    private func editorRow(_ field: RoomsConfigField) -> some View {
        let label = field.label ?? field.name
        let why = roomsConfigEditProblem(field, edits[field.name]) ?? rejected[field.name]
        return VStack(alignment: .leading, spacing: 4) {
            switch field.type {
            case "bool":
                Toggle(isOn: Binding(
                    get: {
                        if case .bool(let b) = edits[field.name] { return b }
                        return field.boolValue
                    },
                    set: { edits[field.name] = .bool($0) }
                )) {
                    fieldLabel(label, field)
                }
                .tint(Color.brand)
                .disabled(roomAdmin?.saving == true)
            case "choice":
                fieldLabel(label, field)
                choicePicker(field, label: label)
            default:
                fieldLabel(label, field)
                HStack(spacing: 6) {
                    TextField(label, text: Binding(
                        get: { edits[field.name]?.asText ?? field.editText },
                        set: { edits[field.name] = .text($0) }
                    ))
                    .textFieldStyle(.roundedBorder)
                    .font(.subheadline)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(field.type == "str" ? .default : .numbersAndPunctuation)
                    .frame(minHeight: 44)
                    .accessibilityLabel(label)
                    .disabled(roomAdmin?.saving == true)
                    if let unit = field.unit, !unit.isEmpty {
                        Text(unit)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                    }
                }
            }
            if let why {
                Text(why)
                    .font(.caption)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let help = field.help, !help.isEmpty {
                Text(help)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func fieldLabel(_ label: String, _ field: RoomsConfigField) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            if field.tier == "restart" {
                HomeTonePill(label: RoomsStrings.restartTier, tone: .warn)
            }
        }
    }

    /// A menu of the field's choices, with the server's labels; a value not
    /// set reads `(not set)`, as on Android.
    private func choicePicker(_ field: RoomsConfigField, label: String) -> some View {
        let current = edits[field.name]?.asText ?? field.editText
        var options = field.choices ?? []
        if !options.contains(current) { options.insert(current, at: 0) }
        return Picker(label, selection: Binding(
            get: { current },
            set: { edits[field.name] = .text($0) }
        )) {
            ForEach(options, id: \.self) { c in
                Text(c.isEmpty ? RoomAdminWords.notSet : field.choiceLabels?[c] ?? c).tag(c)
            }
        }
        .pickerStyle(.menu)
        .tint(Color.brandInk)
        .frame(minHeight: 44)
        .disabled(roomAdmin?.saving == true)
    }

    /// `Save and restart`, enabled when something changed and every number
    /// reads as one, and `undo changes`.
    @ViewBuilder
    private func saveRow(_ fields: [RoomsConfigField]) -> some View {
        let saving = roomAdmin?.saving == true
        let changes = roomsConfigChanges(fields, edits)
        let problems = roomsConfigEditProblems(fields, edits)
        HStack(spacing: 8) {
            Button(saving ? RoomAdminWords.saving : RoomAdminWords.save) {
                confirmSave = true
            }
            .buttonStyle(BrandFillButtonStyle())
            .font(.subheadline)
            .frame(minHeight: 44)
            .disabled(saving || changes.isEmpty || !problems.isEmpty)
            if !edits.isEmpty {
                Button(RoomAdminWords.undo) {
                    edits = [:]
                    rejected = [:]
                }
                .buttonStyle(.plain)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                .frame(minHeight: 44)
                .disabled(saving)
            }
        }
    }

    /// The confirmed save: what was refused stays edited, with why under it,
    /// and the config is read again.
    private func save() {
        guard let zone = roomAdmin, let fields = reads.config.data?.fields else { return }
        let changes = roomsConfigChanges(fields, edits)
        let id = room.roomID
        Task { @MainActor in
            let out = await zone.save(id, changes)
            if let r = out.result {
                rejected = Dictionary(r.rejected.map { ($0.name, $0.reason) }, uniquingKeysWith: { a, _ in a })
                edits = edits.filter { rejected[$0.key] != nil }
                reads.config.refresh()
            }
            model.setToast(out.said)
        }
    }
}

// ---------------------------------------------------------------------------
// Two shapes a `ForEach` can take
//
// ``RoomsStrings/quietModes`` and ``roomsConfigGroups(_:)`` are both arrays of
// **labelled tuples**, which is the right shape for a rule that a test reads
// and the wrong one for a view: Swift has no key path to a tuple element, so
// `ForEach(..., id: \.value)` does not compile at all. Rather than change two
// package signatures a package test already pins, each is wrapped here, in the
// one file that iterates it.
// ---------------------------------------------------------------------------

/// One quiet-hours mode, as a row (`QUIET_CHOICES`, `SatQuietHours.kt:58`).
struct RoomsQuietChoice: Identifiable {

    /// The value the server takes — `house`, `custom`, `off`.
    let id: String
    let label: String

    static let all: [RoomsQuietChoice] = RoomsStrings.quietModes.map {
        RoomsQuietChoice(id: $0.value, label: $0.label)
    }
}

/// One group of config fields, **in the order the server listed them**
/// (``roomsConfigGroups(_:)`` keeps first-appearance order on purpose).
struct RoomsConfigGroup: Identifiable {

    let id: String
    let fields: [RoomsConfigField]

    static func of(_ fields: [RoomsConfigField]) -> [RoomsConfigGroup] {
        roomsConfigGroups(fields).map { RoomsConfigGroup(id: $0.group, fields: $0.fields) }
    }
}
