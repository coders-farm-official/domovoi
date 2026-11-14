import DomovoiKit
import SwiftUI

/// One event, in full — `EventDetail` (`EventEditor.kt:66-170`), as a sheet.
///
/// Android draws it in a 320dp rail beside the grid on a wide window and in a
/// `ModalBottomSheet` on a compact one (`CalendarScreen.kt:212-241`). **A
/// phone is always compact**, so the sheet is the only shape there is to
/// port, and it is the `NavigationStack` + `ScrollView` + `close` shape
/// ``MusicTrackSheet`` already uses.
///
/// ### It holds the editor, rather than the screen holding both
///
/// `edit` opens ``CalendarEventEditorView`` from **this** view's own single
/// `.sheet`, which is where Android puts it: a `Dialog` over the bottom
/// sheet (`EventEditor.kt:191`), with the row still open underneath when it
/// closes. The screen's `.sheet(item:)` therefore never has to change its
/// item while it is presented.
///
/// ### What is not here, and why there is no control for it
///
/// * **No "sync now"** on a google row, and nothing that connects or
///   disconnects a calendar: there is no such route in `web/backend/api/`
///   (map §1.7), so the button would 404 every time. What a google row says
///   is what is true — its pill and ``CalendarStrings/synced(_:)``.
/// * **A google row keeps `edit` and `delete`.** The server accepts both on
///   one (`calendar.py:133,:169`) and Android draws both for every row
///   (`EventEditor.kt:165-168`). A later sync would overwrite such an edit;
///   refusing it here would be inventing a rule the house does not have
///   (DECISIONS §6).
/// * **An event from a linked or a phone calendar has neither** (1.3.0,
///   `EventEditor.kt:168-174`, ``CalendarEntry/readOnly``): the server
///   answers 409 to both, so the controls are not drawn — the sheet says
///   where it is changed instead (``CalendarEntry/readOnlyNote``) — and
///   ``CalendarModel/openEditor(for:)`` and ``CalendarModel/askToDelete(_:)``
///   refuse it too.
struct CalendarEventSheetView: View {

    let entry: CalendarEntry
    let calendar: CalendarModel

    @Environment(\.dismiss) private var dismiss

    @ScaledMetric(relativeTo: .caption) private var rowGlyph: CGFloat = 14

    /// The clock the `synced` line is measured against, read **once** when
    /// the sheet opens — ``MusicTrackSheet``'s reason: a body runs whenever
    /// anything it reads changes, so `synced 15m ago` would quietly become
    /// `16m ago` on an unrelated redraw.
    @State private var openedAtMs: Int64?

    private var day: CalendarDay {
        CalendarDay(ms: calendarStartMs(entry, in: calendar.zone), in: calendar.zone)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    identity
                    if place != nil || note != nil {
                        Divider()
                        placeAndNote
                    }
                    Divider()
                    provenance
                    Divider()
                    actions
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(CalendarStrings.close) { dismiss() }
                }
            }
            .task {
                if openedAtMs == nil { openedAtMs = calendar.clockMs }
            }
            .alert(CalendarStrings.deleteTitle, isPresented: confirming) {
                Button(CalendarStrings.deleteConfirm, role: .destructive) {
                    // Closed on the confirmation, as ``MusicTrackSheet``'s
                    // delete is: the request belongs to the model and
                    // outlives this sheet, and the toast reports either
                    // outcome over whatever is on screen then.
                    //
                    // The row is **passed**, as `MusicTrackSheet` passes its
                    // track: this tap also drives `confirming`'s setter,
                    // which calls `cancelDelete()`, and SwiftUI does not
                    // contract which runs first. A `confirmDelete()` that
                    // read the target back was a no-op on the order where the
                    // dismissal won — a destructive control that silently did
                    // nothing.
                    calendar.confirmDelete(entry)
                    dismiss()
                }
                Button(CalendarStrings.cancel, role: .cancel) {
                    calendar.cancelDelete()
                }
            } message: {
                Text(CalendarStrings.deleteBody(entry.title))
            }
            // **The editor, stacked on this sheet** — one `.sheet` modifier,
            // one piece of state (``CalendarModel/draft``).
            .sheet(item: editorSheet) { _ in
                if let draft = calendar.draft {
                    CalendarEventEditorView(initial: draft, calendar: calendar)
                }
            }
        }
    }

    // MARK: - what it is

    private var identity: some View {
        HStack(alignment: .top, spacing: 10) {
            // Android's 4dp colour bar (`EventEditor.kt:76`). Hidden from
            // VoiceOver: the source pill below says the same thing in a
            // word, and a colour is never the only carrier of meaning.
            RoundedRectangle(cornerRadius: 2)
                .fill(calendarDotColor(entry))
                .frame(width: 4)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(entry.title)
                    .font(.title3)
                    .fixedSize(horizontal: false, vertical: true)
                Text(calendarDateLong(day))
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                // The range, only when there is an end — `:93`; `all day`
                // for an all-day event.
                if entry.endsAt != nil || entry.allDay {
                    Text(calendarRange(entry, in: calendar.zone))
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text(calendarClock(ms: calendarStartMs(entry, in: calendar.zone), timeZone: calendar.zone))
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    // MARK: - where, and what about

    private var place: String? {
        guard let location = entry.location,
              !location.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return nil }
        return location
    }

    private var note: String? {
        guard let description = entry.description,
              !description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return nil }
        return description
    }

    @ViewBuilder private var placeAndNote: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let place {
                detailRow("mappin.and.ellipse", CalendarStrings.locationLabel, place)
            }
            if let note {
                detailRow("text.alignleft", CalendarStrings.descriptionLabel, note)
            }
        }
    }

    /// A label above its value, not beside it — ``MusicTrackSheet``'s reason:
    /// Android's 14dp icon plus a fixed label column leaves a description
    /// four characters of room at an accessibility size.
    private func detailRow(_ glyph: String, _ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 4) {
                Image(systemName: glyph)
                    .font(.system(size: rowGlyph))
                    .accessibilityHidden(true)
                Text(label)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(value)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: - where it came from

    private var provenance: some View {
        VStack(alignment: .leading, spacing: 6) {
            CalendarSourceBadge(entry: entry)
            // `synced <relTime>` for a row from another calendar that has
            // one, and only then (`EventEditor.kt:149-156`). There is no
            // sync control to go with it on this screen.
            if entry.isImported, entry.lastSyncedAt != nil {
                Text(CalendarStrings.synced(
                    relTime(entry.lastSyncedAt, nowMs: openedAtMs ?? calendar.clockMs)
                ))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            }
            // `EventEditor.kt:159`, in the body where Android has it.
            //
            // **Not the sheet's navigation title**, which is what
            // ``MusicTrackSheet`` does with a row's identity: that would put
            // the same words in two elements, which is a VoiceOver stop that
            // says the same thing twice and an ambiguous query for a test.
            // This sheet's bar carries `close` and nothing else.
            // Not for a read-only event (`EventEditor.kt:159-165`): its
            // number is the house's copy's, and means nothing to anyone.
            if !entry.readOnly {
                Text(CalendarStrings.eventNumber(entry.id))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - what can be done to it

    /// An event from a linked or phone calendar is the house's copy: no
    /// edit or delete (they could only fail), and the note says where it is
    /// changed instead (`EventEditor.kt:168-174`).
    @ViewBuilder
    private var actions: some View {
        if let note = entry.readOnlyNote {
            Text(note)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            editAndDelete
        }
    }

    private var editAndDelete: some View {
        VStack(alignment: .leading, spacing: 4) {
            HomeLink(CalendarStrings.edit) { calendar.openEditor(for: entry) }
            Button {
                calendar.askToDelete(entry)
            } label: {
                Text(CalendarStrings.deleteConfirm)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(minHeight: 44, alignment: .center)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            // ``Color/errInk``, never `.red` as text: 5.65:1 against 3.55:1.
            .foregroundStyle(Color.errInk)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - the two things this sheet puts up

    private var confirming: Binding<Bool> {
        Binding(
            get: { calendar.deleteTarget?.id == entry.id },
            set: { up in if !up { calendar.cancelDelete() } }
        )
    }

    private var editorSheet: Binding<CalendarSheet?> {
        Binding(
            get: { calendar.draft.map { CalendarSheet.editor($0.id) } },
            set: { next in if next == nil { calendar.closeEditor() } }
        )
    }
}
