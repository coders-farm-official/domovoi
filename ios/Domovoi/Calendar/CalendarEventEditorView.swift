import DomovoiKit
import SwiftUI

/// Which of the editor's four date/time controls is open.
///
/// Named after the item, like every other new type on this branch: twenty
/// branches adding a `PickerField` to one module only find out when a train
/// merges them.
enum CalendarPickerField: Hashable {
    case startDate
    case startTime
    case endDate
    case endTime
}

/// The create/edit form — `EventEditorDialog` (`EventEditor.kt:178-365`).
///
/// A title, two date-and-time rows, a location and a note, and the two
/// validations Android does in its save button: `title is required` and
/// `end must be after start`, computed **on the tap** and cleared on the next
/// keystroke, which is the Kotlin's own order (`:264-267`).
///
/// ### The pickers expand in place; they are not more sheets
///
/// Android's date and time buttons open a `DatePickerDialog` and a
/// `TimePickerDialog` (`:290-364`). This editor is already a sheet — and,
/// reached through `edit`, a sheet on a sheet — so a third layer for a wheel
/// is a stack of modals a swipe-down can unwind in the wrong order. Tapping
/// a button expands Apple's own picker underneath it instead, one at a time,
/// which is the same two taps and nothing to dismiss.
///
/// ### The title and the location wrap
///
/// Apple's audit failed the **title** field with "Text of this element may be
/// clipped at larger Dynamic Type sizes" in trains S002 and S005. S002's
/// answer was to take `.textFieldStyle(.roundedBorder)` off it — the control
/// that style builds measured 361 x 34 whatever the type size was — and the
/// finding came back against the same element at 361 x 42. So the height was
/// never what the audit was measuring.
///
/// What it measures is the **text against the element's width**, and the
/// discriminator is in the same sheet: `location` is the same field, the same
/// box, the same 361 x 42, and it is *not* named. Its placeholder is the one
/// word `optional`; the title's is `e.g. dinner with mom`, which at an
/// accessibility size does not fit 361 points of one unwrapping line. A
/// single-line `TextField` scrolls its text sideways instead of wrapping it,
/// so at AX5 a long title shows its first few words and the rest is off the
/// side of a box with no scrollbar — and `minimumScaleFactor` is rejected
/// everywhere on this track for making text smaller for the person who asked
/// for bigger.
///
/// This is the same finding, on the same shape, that the Settings server
/// address field answered on 2026-10-30 (`PhonesAndBrowsersView.swift:76-120`),
/// and this takes its recipe exactly: `axis: .vertical`, `.lineLimit(2...)`
/// so the field's own accessibility element is two lines tall rather than the
/// 18 points one line of a vertical field reports, the border drawn by
/// ``SwiftUI/View/calendarFieldBox()`` rather than by a field style, and
/// newlines stripped in the setter.
///
/// **Android keeps `singleLine = true` here** (`:214`, `:247`). It is kept
/// faithful in what it stores — a title can still hold no newline — and not
/// in what it clips, because Compose's single line is a platform default and
/// not a decision about this form.
///
/// ### The note has no `submitLabel` and no `onSubmit`
///
/// It is a `TextField(axis: .vertical)` so the box grows with the type size
/// instead of clipping one line of it, and the Return key of a vertical
/// field **makes a newline**. A key labelled `send` or `done` there would be
/// a key that does not do what it says (NIGHT-RULES §3); three screens
/// shipped with one.
///
/// ### Moving the start keeps the duration
///
/// `:224-231`, through ``CalendarDraft/movingStart(to:)``, falling back to
/// one hour when the duration is zero or negative — so a draft whose end was
/// dragged before its start does not stay inverted when the start moves.
/// Both of the start's controls go through it, the date as well as the time,
/// which is what the Kotlin's single `DateTimeRow(start)` callback does.
struct CalendarEventEditorView: View {

    /// The draft as it was when this opened. `@State` is seeded from it
    /// once; the model's copy is not written back on every keystroke,
    /// because a half-typed title is not a thing another view should see.
    let initial: CalendarDraft
    let calendar: CalendarModel

    @State private var draft: CalendarDraft
    @State private var titleError: String?
    @State private var endError: String?
    @State private var expanded: CalendarPickerField?

    @Environment(\.dynamicTypeSize) private var typeSize

    init(initial: CalendarDraft, calendar: CalendarModel) {
        self.initial = initial
        self.calendar = calendar
        _draft = State(initialValue: initial)
    }

    private var editing: Bool { initial.id != nil }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    titleField
                    row(CalendarStrings.startsAt, .startDate, .startTime, error: nil)
                    row(CalendarStrings.endsAt, .endDate, .endTime, error: endError)
                    locationField
                    noteField
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(
                editing ? CalendarStrings.editEventTitle : CalendarStrings.newEventTitle
            )
            .navigationBarTitleDisplayMode(.inline)
            // **The toolbar, where Android has a row at the foot of the
            // dialog** (`:258-277`). The words are the Kotlin's; the
            // position is the one an iPhone sheet puts a confirm and a
            // cancel in, and a bottom row would have put `create event`
            // below a three-line note at an accessibility size — off the
            // bottom of a form that scrolls.
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button(CalendarStrings.cancel) { calendar.closeEditor() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button(editing ? CalendarStrings.save : CalendarStrings.createEvent) {
                        attemptSave()
                    }
                }
            }
        }
    }

    // MARK: - the fields

    private var titleField: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(CalendarStrings.titleLabel)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // **`axis: .vertical` with two lines of room** — see this file's
            // doc comment, under "The title and the location wrap".
            TextField(
                CalendarStrings.titlePlaceholder,
                text: Binding(
                    get: { draft.title },
                    // `onValueChange = { title = it; titleErr = null }`
                    // (`:208`): the message goes the moment it is being
                    // answered, not when the button is pressed again.
                    //
                    // Newlines are stripped as they arrive, not at save
                    // time: Android's field is `singleLine = true`
                    // (`:214`) so a title there can never hold one, and a
                    // person whose typing is silently altered later would
                    // see the house rename their event. Stripping here
                    // means the Return key of a wrapping field simply does
                    // nothing, which is why there is no `submitLabel` on it
                    // (NIGHT-RULES §3 — a key labelled `done` that makes a
                    // newline is a key that lies).
                    set: { typed in
                        draft.title = typed.filter { !$0.isNewline }
                        titleError = nil
                    }
                ),
                axis: .vertical
            )
            .lineLimit(2...)
            .calendarFieldBox()
            .accessibilityLabel(CalendarStrings.titleLabel)
            if let titleError {
                Text(titleError)
                    .font(.caption)
                    // ``Color/errInk``, never `.red` as text.
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var locationField: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(CalendarStrings.locationLabel)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // The same shape as the title for the same reason
            // (this file's doc comment): Apple named only the title, because
            // this field's placeholder is the one word `optional` and short
            // text is not clipped text — but a typed location
            // (`Bartholomew's pottery studio, 42 Mill Road`) is longer than
            // either, and the audit would have nothing to measure until
            // somebody typed one.
            TextField(
                CalendarStrings.optional,
                text: Binding(
                    get: { draft.location },
                    set: { typed in draft.location = typed.filter { !$0.isNewline } }
                ),
                axis: .vertical
            )
            .lineLimit(2...)
            .calendarFieldBox()
            .accessibilityLabel(CalendarStrings.locationLabel)
        }
    }

    private var noteField: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(CalendarStrings.descriptionLabel)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // `minLines = 3` (`:251`). **No `submitLabel`, no `onSubmit`** —
            // see this file's doc comment.
            TextField(
                CalendarStrings.optionalNotes,
                text: Binding(get: { draft.description }, set: { draft.description = $0 }),
                axis: .vertical
            )
            .lineLimit(3...)
            .calendarFieldBox()
            .accessibilityLabel(CalendarStrings.descriptionLabel)
        }
    }

    // MARK: - a date and a time

    /// Android's `DateTimeRow`: a date button and a time button
    /// (`:290-310`), with the section label above and the error below.
    ///
    /// The two buttons share the section's word as their accessibility label
    /// and carry the date or the clock as their **value**, so VoiceOver reads
    /// "starts at, 11:00am" and the two are told apart by what they hold
    /// rather than by their position in a tree.
    @ViewBuilder private func row(
        _ label: String,
        _ dateField: CalendarPickerField,
        _ timeField: CalendarPickerField,
        error: String?
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // A row on a phone, a column at an accessibility size: `nov 2,
            // 2026` and `11:00am` side by side at AX5 is two clipped boxes.
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 6) {
                    stepButton(label, dateField)
                    stepButton(label, timeField)
                }
            } else {
                HStack(spacing: 8) {
                    stepButton(label, dateField)
                    stepButton(label, timeField)
                }
            }
            if let error {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
            picker(dateField)
            picker(timeField)
        }
    }

    private func stepButton(_ label: String, _ field: CalendarPickerField) -> some View {
        let shown = buttonText(for: field)
        return Button {
            expanded = expanded == field ? nil : field
        } label: {
            Text(shown)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                .frame(maxWidth: .infinity, alignment: .leading)
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.fgMuted.opacity(0.35), lineWidth: 1)
                )
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
        .accessibilityValue(shown)
    }

    @ViewBuilder private func picker(_ field: CalendarPickerField) -> some View {
        if expanded == field {
            switch field {
            case .startDate, .endDate:
                DatePicker(
                    labelFor(field),
                    selection: binding(for: field),
                    displayedComponents: [.date]
                )
                .datePickerStyle(.graphical)
                .labelsHidden()
                .accessibilityLabel(labelFor(field))
            case .startTime, .endTime:
                DatePicker(
                    labelFor(field),
                    selection: binding(for: field),
                    displayedComponents: [.hourAndMinute]
                )
                .datePickerStyle(.wheel)
                .labelsHidden()
                .accessibilityLabel(labelFor(field))
            }
        }
    }

    private func labelFor(_ field: CalendarPickerField) -> String {
        switch field {
        case .startDate, .startTime: return CalendarStrings.startsAt
        case .endDate, .endTime: return CalendarStrings.endsAt
        }
    }

    private func buttonText(for field: CalendarPickerField) -> String {
        let zone = calendar.zone
        switch field {
        case .startDate:
            return calendarDateButton(
                ms: Int64(draft.start.timeIntervalSince1970 * 1000),
                in: zone
            )
        case .startTime:
            return calendarClock(
                ms: Int64(draft.start.timeIntervalSince1970 * 1000),
                timeZone: zone
            )
        case .endDate:
            return calendarDateButton(
                ms: Int64(draft.end.timeIntervalSince1970 * 1000),
                in: zone
            )
        case .endTime:
            return calendarClock(
                ms: Int64(draft.end.timeIntervalSince1970 * 1000),
                timeZone: zone
            )
        }
    }

    private func binding(for field: CalendarPickerField) -> Binding<Date> {
        switch field {
        case .startDate, .startTime:
            return Binding(
                get: { draft.start },
                // **The duration moves with the start** — both controls,
                // because the Kotlin's one callback covers both (`:224-231`).
                set: { draft = draft.movingStart(to: $0); endError = nil }
            )
        case .endDate, .endTime:
            return Binding(
                get: { draft.end },
                set: { draft.end = $0; endError = nil }
            )
        }
    }

    // MARK: - saving

    /// `:264-267` — both messages, computed here, in the Kotlin's order, and
    /// the request sent only when neither is there.
    ///
    /// Not ``CalendarDraft/refusal``, which returns the **first** of the two:
    /// Android shows a title that is missing *and* an end that is wrong at
    /// the same time, each under its own field, and a person who had both
    /// wrong would otherwise fix one and be told about the other.
    /// ``CalendarModel/save(_:)`` still checks `refusal` itself — it is the
    /// belt behind this brace.
    private func attemptSave() {
        let trimmed = draft.title.trimmingCharacters(in: .whitespacesAndNewlines)
        titleError = trimmed.isEmpty ? CalendarStrings.titleRequired : nil
        endError = draft.end <= draft.start ? CalendarStrings.endAfterStart : nil
        guard titleError == nil, endError == nil else { return }
        calendar.save(draft)
    }
}

// MARK: - the field's own box

extension View {

    /// The editor's text fields' border, drawn here rather than by
    /// `.textFieldStyle(.roundedBorder)`.
    ///
    /// `.roundedBorder` builds a UIKit control of a **fixed** height around
    /// the text, and Apple's audit failed the `title` field with "Text
    /// clipped: text of this element may be clipped at larger Dynamic Type
    /// sizes" in train S002 — the element measured 361 x 34 whatever the type
    /// size was. An unstyled field inside padding and a rounded background
    /// has no fixed box in it, so the control grows with the text instead of
    /// cropping it, which is the same shape
    /// ``PhonesAndBrowsersView`` settled on for the same reason.
    ///
    /// **Taking the style off was necessary and not sufficient.** S005 named
    /// the same element again at 361 x 42, which is what sent the two
    /// single-line fields to `axis: .vertical` — see
    /// ``CalendarEventEditorView``, "The title and the location wrap". This
    /// box is what draws their border now that no field style can: with
    /// `.roundedBorder` on a vertical field the control is 18 points tall and
    /// Apple fails it for hit area instead, which the server address field
    /// measured from both sides of the modifier
    /// (`PhonesAndBrowsersView.swift:99-115`).
    ///
    /// Named for the screen, not for the shape (NIGHT-RULES §2): twenty
    /// branches are adding to this module tonight and `fieldBox()` is a name
    /// two of them would take.
    func calendarFieldBox() -> some View {
        padding(.horizontal, 8)
            .padding(.vertical, 10)
            .background(
                Color(.secondarySystemBackground),
                in: RoundedRectangle(cornerRadius: 8)
            )
    }
}
