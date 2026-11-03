import DomovoiKit
import SwiftUI

/// Calendar: what the household has on, three ways.
///
/// `android/.../ui/screens/calendar/CalendarScreen.kt` (428 lines) with its
/// two grid files, as a phone screen. One read —
/// `GET /api/calendar/events` with no parameters, re-read on
/// `calendar.events.changed` — and every view's window cut out of that list
/// client-side, so a chevron costs no round trip (``CalendarModel``).
///
/// ### What is here
///
/// An agenda grouped by day, a single day against the hours, and a month of
/// dots; a `new event` button, and a tap on any row opens that event in full,
/// where it can be edited or deleted after a confirmation. **All three
/// writes work from a paired phone**: both tiers of this route are
/// `require_device` / `require_device_read` (`calendar.py:26,:34`), which is
/// the difference from Files, where the delete is an admin route and is
/// therefore not drawn at all.
///
/// ### What is not, and why there is no control for it
///
/// * **Week view.** Android offers it (`CalendarModels.kt:50-52`) and it is a
///   seven-column time grid of 9-11sp text in fixed-height blocks
///   (`CalendarGrids.kt:212-286`). Seven columns on a 390pt phone is ~49pt
///   each; the standing rules require `@ScaledMetric` glyph widths and
///   `fixedSize` on wrapping text and reject `minimumScaleFactor` outright,
///   and there is no layout that keeps that grid legible at
///   `UICTContentSizeCategoryAccessibilityXXXL`. The week's arithmetic is
///   still ported and tested (``calendarWeekRangeLabel(_:)``), so the view is
///   one file away; map §3.2, DECISIONS §3 asks whether Kamron wants it as a
///   grouped list instead.
/// * **No "sync now"**, no connect, no disconnect. Nothing in
///   `web/backend/api/` syncs a Google calendar, so the button would 404
///   every time. A `source == "google"` row says only what is true: its pill
///   and ``CalendarStrings/synced(_:)`` from `last_synced_at`.
/// * **No all-day switch.** `calendar_events.all_day` is in the table and in
///   no schema (`V001__baseline.sql:231`), so no API can read or write it.
///   Android has no control for it either.
/// * **No swipe-to-delete** on an agenda row. Deleting is confirmed and
///   destructive, and Android's one path is detail → delete → confirm; a
///   swipe would be a second, unconfirmed door to the same irreversible
///   write.
/// * **No detail rail.** Android puts the open event in a 320dp panel beside
///   the grid on a wide window (`CalendarScreen.kt:212-221`) and in a
///   `ModalBottomSheet` on a compact one (`:227`). A phone is always
///   compact, so the sheet is the only shape there is to port.
struct CalendarScreenView: View {

    let model: ShellModel

    @State private var calendar: CalendarModel?

    /// `onAppear`/`onDisappear`, for ``FilesView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy.
    @State private var visible = false

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                header
                if let calendar {
                    CalendarRangeBarView(calendar: calendar)
                    content(calendar)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task {
            // Built here and not in `init`, for ``FilesView``'s reason: the
            // model needs the container and a `@State` initialiser runs
            // before the view has one.
            if calendar == nil {
                calendar = CalendarModel(container: model.container)
            }
            calendar?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            calendar?.stop()
        }
        // **The toast is the shell's**, drawn over the whole app, because
        // `deleted "dentist"` must outlive the sheet that asked for it and
        // does outlive this screen.
        .onChange(of: calendar?.toast) { _, said in
            guard said != nil, let text = calendar?.takeToast() else { return }
            model.showToast(text)
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            calendar?.serverChanged()
        }
        // **One `.sheet` over both the detail and the editor.** Not tidiness:
        // several `.sheet` modifiers on one view do not reliably coexist in
        // SwiftUI — the last one attached wins and the others silently never
        // present (``FilesView``, which shipped that way once).
        //
        // **The detail takes precedence and the editor stacks on top of it**,
        // inside ``CalendarEventSheetView``'s own single `.sheet` — which is
        // what Android does: a `Dialog` over the detail's bottom sheet
        // (`EventEditor.kt:191`), with the row still open underneath when it
        // closes (`editorDraft = null` does not clear `selectedId`). Driving
        // both from *this* modifier would change the presented item while it
        // was on screen every time somebody tapped `edit`, which is the one
        // thing `sheet(item:)` does badly.
        .sheet(item: openSheet) { which in
            if let calendar {
                CalendarSheetHostView(which: which, calendar: calendar)
            }
        }
    }

    // MARK: - what is open

    /// The detail first: when a row is open **and** the editor is up, this
    /// stays `.detail`, and the editor is the detail sheet's own nested one.
    /// The only item change that happens while a sheet is presented is the
    /// one after a successful create, which is Android's behaviour too — the
    /// dialog closes and the new row is selected (`CalendarScreen.kt:114-121`).
    private var current: CalendarSheet? {
        guard let calendar else { return nil }
        if let id = calendar.selectedID { return .detail(id) }
        if let draft = calendar.draft { return .editor(draft.id) }
        return nil
    }

    private var openSheet: Binding<CalendarSheet?> {
        Binding(
            get: { current },
            // Closing is **per case**, read from what is actually open rather
            // than clearing both: a swipe-down on the editor that also
            // cleared the selection would shut the detail sheet the person
            // opened it from.
            set: { next in
                guard next == nil, let open = current else { return }
                switch open {
                case .editor:
                    calendar?.closeEditor()
                case .detail:
                    calendar?.select(nil)
                    // A nested editor is dismissed with its parent by UIKit
                    // and nothing tells the model, so a draft left behind
                    // would re-present itself from here the moment the
                    // detail closed.
                    calendar?.closeEditor()
                }
            }
        )
    }

    // MARK: - the header

    private var header: some View {
        // A row on a phone, a column at an accessibility size: `new event`
        // beside a two-line title leaves the button about four characters of
        // width at AX5, and this header is the one place on the screen where
        // a control and a heading compete for the same line.
        CalendarHeaderStack(stacked: typeSize.isAccessibilitySize) {
            VStack(alignment: .leading, spacing: 4) {
                Text(CalendarStrings.title)
                    .font(.title2)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
                Text(calendar?.subtitle ?? CalendarStrings.subtitle(count: 0))
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } trailing: {
            Button {
                calendar?.openNewEvent()
            } label: {
                Text(CalendarStrings.newEvent)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.bordered)
            .disabled(calendar == nil)
        }
    }

    // MARK: - the four states, in the Kotlin's order

    /// `CalendarScreen.kt:160-200`: loading, then error, then the empty
    /// wording, then the view itself. The order matters — an error *after* a
    /// success keeps the stale rows with no mark, which is ``ApiRead``'s
    /// ported behaviour and Android's (``CalendarModel/errorText``).
    @ViewBuilder private func content(_ calendar: CalendarModel) -> some View {
        if calendar.isLoading {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
                .accessibilityLabel(CalendarStrings.title)
        } else if let trouble = calendar.errorText {
            VStack(alignment: .leading, spacing: 8) {
                CalendarNoteView(title: CalendarStrings.requestFailed, detail: trouble)
                HomeLink(CalendarStrings.retry) { calendar.events.refresh() }
            }
        } else if let empty = calendar.emptyText {
            CalendarNoteView(title: empty.title, detail: empty.body)
        } else {
            switch calendar.mode {
            case .agenda: CalendarAgendaListView(calendar: calendar)
            case .day: CalendarDayGridView(calendar: calendar)
            case .month: CalendarMonthGridView(calendar: calendar)
            }
        }
    }
}

// MARK: - which sheet

/// The one sheet this screen puts up, as a value, so a single `.sheet(item:)`
/// can carry both shapes.
///
/// The editor's case carries the draft's **id** — nil for a new event — so
/// that `Identifiable` tells "the editor on event 4" from "the editor on a
/// new event": a sheet whose `id` did not change would not be re-presented
/// when the draft underneath it was replaced.
enum CalendarSheet: Identifiable, Equatable {

    case detail(Int64)
    case editor(Int64?)

    var id: String {
        switch self {
        case .detail(let eventID): return "detail-\(eventID)"
        case .editor(let eventID): return "editor-\(eventID.map(String.init) ?? "new")"
        }
    }
}

/// Whichever of the two is open, built where the model is in scope.
struct CalendarSheetHostView: View {

    let which: CalendarSheet
    let calendar: CalendarModel

    var body: some View {
        switch which {
        case .detail(let eventID):
            // Read out of the **list** rather than from a copy held in the
            // sheet: an edit's PATCH needs no local patching, because the
            // refresh replaces the row and this redraws from it. A row that
            // has gone (a delete that raced the sheet) draws nothing rather
            // than a stale copy of itself.
            if let entry = calendar.all.first(where: { $0.id == eventID }) {
                CalendarEventSheetView(entry: entry, calendar: calendar)
            } else {
                Color.clear
            }
        case .editor:
            if let draft = calendar.draft {
                CalendarEventEditorView(initial: draft, calendar: calendar)
            } else {
                Color.clear
            }
        }
    }
}

// MARK: - the bar over the views

/// The view toggle, `today`, and the two chevrons either side of the range
/// label — `CalendarScreen.kt:269-325`.
///
/// Android lays it out as a `FlowRow` so the pieces wrap on a narrow window.
/// Here the toggle **scrolls sideways** and the rest wraps to a second line
/// at an accessibility size: a `FlowRow` has no SwiftUI equivalent before
/// iOS 16's `Layout`, and three scaling words that must not be clipped are
/// exactly what a horizontal scroll view is for — no fixed widths, no
/// `minimumScaleFactor`, nothing cut at AX5.
struct CalendarRangeBarView: View {

    let calendar: CalendarModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            modeToggle
            if typeSize.isAccessibilitySize {
                // The label on its own line: at AX5 a month name between two
                // chevrons and a `today` leaves it no width at all.
                Text(calendar.rangeLabel)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 4) {
                    chevron(-1, "chevron.left", CalendarStrings.previous)
                    chevron(1, "chevron.right", CalendarStrings.next)
                    today
                }
            } else {
                HStack(spacing: 4) {
                    chevron(-1, "chevron.left", CalendarStrings.previous)
                    Text(calendar.rangeLabel)
                        .font(.headline)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    chevron(1, "chevron.right", CalendarStrings.next)
                    today
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var modeToggle: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 6) {
                ForEach(CalendarViewMode.allCases, id: \.self) { mode in
                    Button {
                        calendar.mode = mode
                    } label: {
                        Text(mode.label)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 14)
                            .frame(minHeight: 44)
                            // ``Color/brandFg`` on ``Color/brand`` is 8.49:1;
                            // `brand` as text would be 2.05:1, which is why
                            // the chosen word is drawn on the fill and never
                            // in it.
                            .foregroundStyle(
                                calendar.mode == mode ? Color.brandFg : Color.primary
                            )
                            .background(
                                calendar.mode == mode
                                    ? Color.brand
                                    : Color(.secondarySystemBackground),
                                in: Capsule()
                            )
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(calendar.mode == mode ? [.isSelected] : [])
                }
            }
            .padding(.vertical, 1)
        }
        .scrollIndicators(.hidden)
    }

    private var today: some View {
        Button {
            calendar.goToToday()
        } label: {
            Text(CalendarStrings.today)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 10)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
    }

    private func chevron(_ direction: Int, _ glyph: String, _ label: String) -> some View {
        Button {
            calendar.step(direction)
        } label: {
            Image(systemName: glyph)
                .font(.body)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .accessibilityLabel(label)
    }
}

// MARK: - the small shared pieces

/// `EmptyState(title, message)` (`ui/components/Kit.kt:284`) — and the error
/// state, which is the same two lines plus a button.
struct CalendarNoteView: View {

    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(detail)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 8)
    }
}

/// A heading and a control: side by side, or stacked at an accessibility
/// size.
///
/// Named for this screen, not for the shape, even though nothing about it is
/// calendar-specific: twenty branches are adding files to this one module
/// tonight and "a heading beside a control" is the likeliest shape for two of
/// them to need. A bare `AdaptiveHeaderStack` in two of them is an
/// `invalid redeclaration` found only when a train merges them.
///
/// Its own type rather than an `if` in the body, because the two branches
/// must hold the **same** two subviews — a header written twice is a header
/// whose two spellings drift, and this app has one of those already.
struct CalendarHeaderStack<Leading: View, Trailing: View>: View {

    let stacked: Bool
    let leading: Leading
    let trailing: Trailing

    /// Spelt out rather than left to the memberwise initialiser, so the two
    /// closures are `@ViewBuilder` at the call site beyond doubt.
    init(
        stacked: Bool,
        @ViewBuilder leading: () -> Leading,
        @ViewBuilder trailing: () -> Trailing
    ) {
        self.stacked = stacked
        self.leading = leading()
        self.trailing = trailing()
    }

    var body: some View {
        if stacked {
            VStack(alignment: .leading, spacing: 8) {
                leading
                trailing
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(alignment: .top, spacing: 12) {
                leading
                Spacer(minLength: 8)
                trailing
            }
        }
    }
}
