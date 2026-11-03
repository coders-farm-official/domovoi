import DomovoiKit
import SwiftUI

/// The settings tab: quiet hours, which this phone may change, and the room's
/// configuration, which it may only read.
///
/// `SatQuietHours.kt:99` and `SatSettingsTab.kt:63`.
///
/// ### What is not here, and why there is no control for it
///
/// **`save & restart`, and with it every editable field.** `PATCH
/// …/{room}/config` is **admin** (`web/backend/api/satellites.py:1712-1716`)
/// while the GET beside it is device read (`:1639-1647`), so a phone can see
/// what a room is set to and can never change it. Android draws a switch per
/// `bool`, a dropdown per `choice` and a numeric field per `int`/`float`
/// (`SatSettingsTab.kt:268-330`); each of those here would be a control that
/// looks like it saves and 401s every time, which is exactly what Kamron's
/// no-misleading-controls rule forbids. So every field is **words**
/// (``roomsConfigValueText(_:)``) and one sentence says where they are
/// changed.
///
/// Quiet hours are the exception and are fully drawn: `PUT …/quiet-hours` is
/// device tier (`:1425-1432`).
struct RoomsSettingsTabView: View {

    let model: RoomsModel
    let reads: RoomsRoomModel
    let room: RoomsSatellite

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            quietHours
            config
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
            .buttonStyle(.borderedProminent)
            .tint(Color.brand)
            .foregroundStyle(Color.brandFg)
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
                    Text(RoomsStrings.configIsReadOnly)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    let split = roomsConfigSplit(answer.fields)
                    ForEach(RoomsConfigGroup.of(split.common)) { group in
                        groupRows(group.id, group.fields)
                    }
                    if !split.advanced.isEmpty {
                        // Android puts the advanced fields behind a disclosure
                        // with a warning about changing them. Nothing here
                        // changes anything, so the disclosure stays (it is a
                        // long list nobody scans) and the warning does not.
                        DisclosureGroup(RoomsStrings.advancedGroup) {
                            ForEach(RoomsConfigGroup.of(split.advanced)) { group in
                                groupRows(group.id, group.fields)
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
    private func groupRows(_ group: String, _ fields: [RoomsConfigField]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            RoomsSectionLabel(text: group)
            ForEach(fields) { field in
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
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 12, style: .continuous)
        )
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
