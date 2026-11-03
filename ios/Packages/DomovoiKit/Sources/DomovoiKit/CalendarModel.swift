import Foundation
import Observation

/// The Calendar screen's state and its three writes — `CalendarScreen.kt:76-261`
/// as a type, in the shape ``FilesModel`` and ``ChatModel`` have.
///
/// ### Why this is a model and not lines in a view
///
/// Two of the standing rules are structural and this type is where they are
/// obeyed:
///
/// * **A lazy row never holds a `Task`.** Android's `save` and `delete` run in
///   a `rememberCoroutineScope()` owned by the screen (`:98`, `:129`), not by
///   a row; here they are methods on an object that outlives every row the
///   agenda builds and drops.
/// * **The server address is captured before the `await`.**
///   ``save(_:)`` and ``delete(_:)`` read `container.store.serverURL` on the
///   way in and compare it again before they touch ``toast``,
///   ``selectedID``, ``draft`` or the read — so a reply for house A cannot
///   land under house B, which is the top defect class on this track.
///
/// ### One read, and what it costs
///
/// `GET /api/calendar/events` with **no parameters** (`:84`, DECISIONS §7):
/// the first 500 events by `starts_at` (`calendar.py:41`), every view's window
/// cut out of that list client-side, so a chevron costs no round trip. The
/// cost is the cap — in a house with more than 500 events the far future is
/// not there.
///
/// Both tiers of this route are `require_device` / `require_device_read`
/// (`calendar.py:26,:34`), so **create, edit and delete all work from a paired
/// phone**. This screen is the opposite of Files, whose delete is an admin
/// route and is therefore not drawn there: nothing here is drawn and dead.
///
/// ### What is not, and why there is no control for it
///
/// * **No sync.** No route in `web/backend/api/` connects, disconnects or
///   refreshes a Google calendar, so there is no "sync now": it would 404
///   every time. A `source == "google"` row says only what is true — its pill
///   and ``CalendarStrings/synced(_:)`` from `last_synced_at`.
/// * **No all-day switch.** `calendar_events.all_day` is in the table and in
///   no schema (`V001__baseline.sql:231`), so no API can read or write it.
///   Android has no control for it either.
/// * **A google row keeps edit and delete**, because the server accepts both
///   on one (`calendar.py:133,:169`) and Android draws both for every row
///   (`EventEditor.kt:165-168`). A later sync would overwrite such an edit;
///   refusing it here would be inventing a rule the house does not have
///   (DECISIONS §6).
/// * **No swipe-to-delete** on an agenda row. Deleting is confirmed and
///   destructive and Android's one path is detail → delete → confirm; a swipe
///   would be a second, unconfirmed door to the same irreversible write.
@MainActor
@Observable
public final class CalendarModel {

    // MARK: - the read

    /// The whole list, re-read on `calendar.events.changed`.
    public let events: ApiRead<[CalendarEntry]>

    /// The push every write fires — `pg_notify('calendar_changed')` inside the
    /// write's own transaction (`calendar.py:127`), mapped to the topic
    /// `calendar.events` (`realtime.py:130`) and broadcast as
    /// `"\(channel).changed"` (`:375`). Its payload is a 30-day window of four
    /// fields (`realtime.py:482-512`) and **nothing decodes it**: it means
    /// "re-read", which is all ``ApiRead`` does with it.
    public static let eventTypes: Set<String> = ["calendar.events.changed"]

    // MARK: - what the person has chosen

    /// Agenda, as a phone opens (`CalendarScreen.kt:89` — compact leads with
    /// agenda). There is no rotation reset to port: Android's `view` is
    /// `remember(compact)`-keyed and a phone is always compact.
    public var mode: CalendarViewMode = .agenda

    /// The date every view is drawn around — `anchor` (`:91`), which starts at
    /// today and is moved by the chevrons, by `today` and by a month-cell tap.
    public private(set) var anchor: CalendarDay

    /// The row the detail sheet is open on, or nil — `selectedId` (`:92`).
    public private(set) var selectedID: Int64?

    /// The editor's draft, or nil when it is closed — `editorDraft` (`:93`).
    public var draft: CalendarDraft?

    /// The row the confirm alert is about — `deleteTarget` (`:94`).
    public private(set) var deleteTarget: CalendarEntry?

    /// Which house the last confirm alert was raised in, captured when it was
    /// asked for. ``confirmDelete(_:)`` takes its row as an argument rather
    /// than reading ``deleteTarget`` back, so this — and not the target — is
    /// what keeps a pending delete belonging to one house.
    private var deleteAskedServer: String?

    /// What the last write said. Read and cleared by the screen, like
    /// ``MusicModel/takeToast()``.
    public private(set) var toast: String?

    // MARK: - what it was built from

    private let container: AppContainer
    private let nowMs: @Sendable () -> Int64
    private let timeZone: @Sendable () -> TimeZone
    private var eventSink: Int?

    public init(
        container: AppContainer,
        nowMs: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        timeZone: @Sendable @escaping () -> TimeZone = { .current }
    ) {
        self.container = container
        self.nowMs = nowMs
        self.timeZone = timeZone
        self.anchor = CalendarDay(ms: nowMs(), in: timeZone())
        let client = container.client
        self.events = ApiRead(in: container, eventTypes: Self.eventTypes) {
            try await client.get("/api/calendar/events", as: [CalendarEntry].self)
        }
    }

    // MARK: - lifecycle

    /// The screen appeared: read, and join the push registry.
    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        events.refresh()
    }

    /// The screen is gone. "A route holds nothing" — the read stops and coming
    /// back re-reads.
    public func stop() {
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        events.stop()
    }

    public func received(_ event: StateEvent) {
        events.received(event)
    }

    /// The saved server changed under us. Everything the person had chosen in
    /// **this** house goes with it: a selected id, a half-written draft and a
    /// pending delete all name a row id that belongs to the house they have
    /// left, and `7` exists in both.
    public func serverChanged() {
        events.serverChanged()
        selectedID = nil
        draft = nil
        deleteTarget = nil
        deleteAskedServer = nil
        toast = nil
    }

    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
    }

    // MARK: - what is drawn

    /// Every event the read has, or none while it has not answered.
    public var all: [CalendarEntry] { events.data ?? [] }

    /// The zone every view of this screen formats in.
    ///
    /// Exposed rather than left to a view reading `TimeZone.current` again:
    /// the window, the grouping and the grid placement are all cut in
    /// ``timeZone``, and a row whose clock was written in a *second* zone
    /// would sit under the wrong day header on a phone that had just crossed
    /// one. One zone, read once, from the thing that owns it.
    public var zone: TimeZone { timeZone() }

    /// The clock the day grid's `now` rule and the detail sheet's
    /// ``CalendarStrings/synced(_:)`` are measured against — ``nowMs``, for
    /// the same reason as ``zone``.
    public var clockMs: Int64 { nowMs() }

    /// `"<n> events · the household's calendar"` (`CalendarScreen.kt:145`) —
    /// counted over the **whole** list, not the visible window, as Android
    /// counts it.
    public var subtitle: String { CalendarStrings.subtitle(count: all.count) }

    /// What sits between the chevrons.
    public var rangeLabel: String {
        calendarRangeLabel(mode: mode, anchor: anchor)
    }

    /// The current view's window.
    public var visible: [CalendarEntry] {
        calendarVisibleEvents(mode: mode, anchor: anchor, events: all, in: timeZone())
    }

    /// The agenda's day groups, with today's header told from tomorrow's.
    public var agendaSections: [CalendarAgendaSection] {
        calendarAgendaSections(visible, todayMs: nowMs(), in: timeZone())
    }

    /// The month grid's 42 cells.
    public var monthCells: [CalendarMonthCell] {
        calendarMonthCells(anchor: anchor, events: all, in: timeZone())
    }

    /// The row the detail sheet is open on — `selected` (`:95`).
    ///
    /// Read out of the **list** rather than held as a copy, which is Android's
    /// shape and the reason an edit's PATCH needs no local patching: the next
    /// read replaces the row and the sheet redraws from it.
    public var selected: CalendarEntry? {
        all.first { $0.id == selectedID }
    }

    /// The loading state's own condition — `loading && data == null` (`:161`).
    public var isLoading: Bool { events.loading && events.data == nil }

    /// The error state's — `error != null && data == null` (`:162`). A failure
    /// **after** a success keeps the stale rows with no mark, which is
    /// ``ApiRead``'s ported behaviour and Android's.
    public var errorText: String? {
        guard events.data == nil, let error = events.error else { return nil }
        return error.isEmpty ? CalendarStrings.requestFailed : error
    }

    /// The two empty wordings (`:171-180`): the agenda has its own, and every
    /// other view says what range is empty — the day label in day view, else
    /// the view's word.
    public var emptyText: (title: String, body: String)? {
        guard events.answered, errorText == nil, visible.isEmpty else { return nil }
        if mode == .agenda {
            return (CalendarStrings.emptyAgendaTitle, CalendarStrings.emptyAgendaBody)
        }
        let what = mode == .day ? calendarDayLabel(anchor) : mode.label
        return (CalendarStrings.emptyRangeTitle, CalendarStrings.emptyRangeBody(what))
    }

    // MARK: - moving about

    /// A chevron — `stepAnchor` (`:306`, `:313`).
    public func step(_ direction: Int) {
        anchor = calendarStep(anchor, mode: mode, by: direction)
    }

    /// `today` (`:300`) — back to the anchor the screen opened on.
    public func goToToday() {
        anchor = CalendarDay(ms: nowMs(), in: timeZone())
    }

    /// `pickDay` (`:140`) — a month cell's tap: anchor there and switch to
    /// day, which is the only way this port's month cell reveals what is on a
    /// date (DECISIONS §4).
    public func pickDay(_ day: CalendarDay) {
        anchor = day
        mode = .day
    }

    public func select(_ id: Int64?) {
        selectedID = id
    }

    // MARK: - the editor and the alert

    /// The header's `new event` button — `blankDraft(anchor)` (`:147`).
    public func openNewEvent() {
        draft = CalendarDraft.blank(anchor: anchor, nowMs: nowMs(), timeZone: timeZone())
    }

    /// The detail sheet's `edit` — `draftOf(selected)` (`:217`).
    ///
    /// Does nothing for an entry whose `starts_at` will not parse
    /// (``CalendarDraft/init(_:)`` returns nil): an editor opened on a
    /// nonsense date would let a save overwrite the row's real time. Android
    /// opens it on `LocalDateTime.now()`, which is that overwrite.
    public func openEditor(for entry: CalendarEntry) {
        guard let opened = CalendarDraft(entry) else { return }
        draft = opened
    }

    public func closeEditor() {
        draft = nil
    }

    public func askToDelete(_ entry: CalendarEntry) {
        deleteTarget = entry
        deleteAskedServer = container.store.serverURL
    }

    /// **Leaves ``deleteAskedServer`` alone on purpose.** The alert's
    /// dismissal binding calls this from the very same tap that confirms, in
    /// an order SwiftUI does not contract, so clearing the house here would
    /// put back the silent no-op ``confirmDelete(_:)`` was changed to avoid.
    /// Only a new ask or a house change writes it.
    public func cancelDelete() {
        deleteTarget = nil
    }

    // MARK: - the writes

    /// `save(d)` (`CalendarScreen.kt:97-126`) — POST a new event, PATCH an
    /// existing one.
    ///
    /// Returns whether it went, so a refusal is not invisible: a draft that
    /// fails its own validation (``CalendarDraft/refusal``) sends nothing, and
    /// the editor stays open showing why. That is the Kotlin's order too — it
    /// validates in the button and calls `onSave` only when both messages are
    /// nil (`EventEditor.kt:264-267`).
    ///
    /// ``selectedID`` after a create comes from the **201 body's `id`**
    /// (`calendar.py:110`, read at `CalendarScreen.kt:114-117`), not from a
    /// re-read: the refresh is asynchronous and the row would not be there
    /// yet.
    @discardableResult
    public func save(_ draft: CalendarDraft) -> Bool {
        guard draft.refusal == nil else { return false }
        let client = container.client
        // **Before the await.** The reply is a reply from *this* house; a
        // toast, a selection and a refresh applied after a switch would all
        // belong to the other one.
        let asked = container.store.serverURL
        let path = draft.id.map { "/api/calendar/events/\($0)" } ?? "/api/calendar/events"
        let creating = draft.id == nil
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                let saved = creating
                    ? try await client.post(path, body: draft.body, as: CalendarEntry.self)
                    : try await client.patch(path, body: draft.body, as: CalendarEntry.self)
                guard let self, asked == self.container.store.serverURL else { return }
                self.toast = creating ? CalendarStrings.eventCreated : CalendarStrings.eventUpdated
                self.selectedID = creating ? saved.id : draft.id
                self.draft = nil
                self.events.refresh()
            } catch let trouble {
                guard let self, asked == self.container.store.serverURL else { return }
                self.toast = CalendarStrings.saveFailed(trouble.message)
            }
        }
        return true
    }

    /// `delete(e)` (`CalendarScreen.kt:128-138`), after the confirm.
    ///
    /// ``DomovoiClient/send(method:path:body:)`` and **not** `delete(_:as:)`:
    /// the route answers **204 with no body** (`calendar.py:172`) and a
    /// decoding delete would throw on the empty reply — a delete that worked
    /// and said it failed.
    ///
    /// **The row is an argument, not a read of ``deleteTarget``.** The alert's
    /// destructive button and the binding that calls ``cancelDelete()`` on
    /// dismissal both fire from one tap, and SwiftUI does not contract which
    /// goes first: reading the target back here made a confirm that ran second
    /// a silent no-op — the alert closed, the sheet closed, no DELETE was sent
    /// and nothing said so. What keeps the delete bound to one house is
    /// ``deleteAskedServer``, captured when the alert was asked for.
    public func confirmDelete(_ entry: CalendarEntry) {
        deleteTarget = nil
        let asked = container.store.serverURL
        // The house the alert was raised in, not the row: a confirm after the
        // saved server moved names an id that belongs to the house the person
        // has left.
        guard deleteAskedServer == asked else { return }
        let client = container.client
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                try await client.send(method: "DELETE", path: "/api/calendar/events/\(entry.id)")
                guard let self, asked == self.container.store.serverURL else { return }
                self.toast = CalendarStrings.deleted(entry.title)
                if self.selectedID == entry.id { self.selectedID = nil }
                self.events.refresh()
            } catch let trouble {
                guard let self, asked == self.container.store.serverURL else { return }
                self.toast = CalendarStrings.deleteFailed(trouble.message)
            }
        }
    }
}
