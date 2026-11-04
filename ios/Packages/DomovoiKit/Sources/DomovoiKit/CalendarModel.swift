import Foundation
import Observation
import Synchronization

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
/// ### One bounded read per range
///
/// It used to be `GET /api/calendar/events` with **no parameters** (`:84`,
/// DECISIONS §7) — the first 500 events by `starts_at` (`calendar.py:41`) with
/// every view's window cut out of that list client-side, so a chevron cost no
/// round trip. The cap was the cost, and it was worse than it sounded: the
/// route is `ORDER BY starts_at ASC LIMIT 500` **from the dawn of time**, so a
/// house whose oldest 500 events are all in the past drew an empty agenda.
///
/// **Kamron's answer 16 (2026-11-03): "yes: ask the server for a window per
/// view (`?start=&end=`)."** So:
///
/// * ``wantedWindow`` is the visible range as the route's two bounds
///   (``calendarWindow(mode:anchor:in:)``), and it moves with the chevrons,
///   `today`, a month cell's tap and the view toggle.
/// * The read is ``events``, whose value is a ``CalendarPage`` — **the rows
///   together with the window they were asked for.** That pairing is the
///   read-after-`await` rule made structural: the window is read off
///   ``window`` *before* the request and travels back inside the reply, so
///   there is no moment at which rows for one range can be filed under
///   another. A reply that lands after the range moved is dropped by
///   ``ApiRead``'s own cancel-on-refresh, which is what moving the range
///   calls when it asks for anything — and a page that lands when it did
///   not, or that came back cut, is answered by ``pageLanded()``: a page
///   that cannot draw the range on screen is re-pointed at that range and
///   read again, once.
/// * Stepping stays responsive because a page is **reused**, and widened
///   rather than replaced: a range already inside the page costs nothing
///   (``CalendarPage/serves(_:)``), and a range next to it is read as the
///   union of the two (``CalendarWindow/widens(to:)``), so flicking back and
///   forth over a fortnight settles into one window instead of a request a
///   tap. A jump that meets nothing read — and any step at all once a page
///   has come back cut by `LIMIT` — is a fresh read of just that range.
///
/// The cost that remains is named at ``calendarWindow(mode:anchor:in:)``: the
/// route filters on `starts_at`, so an event longer than a day that began
/// more than a day before the agenda's anchor is not read.
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

    /// One window's rows, re-read on `calendar.events.changed`.
    public let events: ApiRead<CalendarPage>

    /// The window the next request will ask for, shared with ``events``'
    /// `@Sendable` fetch closure — ``HomeModel``'s `DayWindow`, which holds
    /// Home's own calendar window for the same reason: the closure runs
    /// outside this actor and the window changes under it.
    ///
    /// It is the **asked** window, not the wanted one: after a widening it is
    /// the union, which is bigger than any one view needs.
    private let window: CalendarWindowBox

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
    ///
    /// Settable through ``show(_:)`` and not directly, because since answer 16
    /// each view has its **own read**: a bare `var` would let the toggle move
    /// the range without anything asking the server for it, and the screen
    /// would draw the old window's rows under the new view for as long as
    /// nobody else happened to refresh.
    public private(set) var mode: CalendarViewMode = .agenda

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
        let opening = CalendarDay(ms: nowMs(), in: timeZone())
        self.anchor = opening
        // The agenda's window, because ``mode`` opens on the agenda. Built
        // before ``events`` so the very first request is already bounded.
        let window = CalendarWindowBox(
            calendarWindow(mode: .agenda, anchor: opening, in: timeZone())
        )
        self.window = window
        let client = container.client
        self.events = ApiRead(in: container, eventTypes: Self.eventTypes) {
            // **The window is taken here, before the `await`**, and handed
            // back inside the page. The alternative — reading the box again
            // when the reply lands — is the top defect class on this track
            // with the house swapped for the range: the rows of one range
            // filed under another, which no view could tell from a server
            // that answered wrongly.
            let asked = window.current
            let rows = try await client.get(
                calendarEventsPath(asked),
                as: [CalendarEntry].self
            )
            return CalendarPage(
                window: asked,
                rows: rows,
                // A read that came back exactly full may have been cut by
                // `LIMIT`, so it is not evidence about any range but its own.
                truncated: rows.count >= calendarWindowLimit
            )
        }
        // Last, because it captures `self` — ``FilesModel``'s own
        // `libraries.onValue` line, for the same reason it exists there: the
        // read's success path is the one place that knows a page has landed,
        // and a failure must not be mistaken for one.
        self.events.onValue = { [weak self] in self?.pageLanded() }
    }

    // MARK: - lifecycle

    /// The screen appeared: read, and join the push registry.
    ///
    /// The window is set from the anchor first. A screen that came back after
    /// ``stop()`` may have been left on a range the box no longer names —
    /// nothing moves the anchor while the screen is gone, but ``serverChanged()``
    /// can have cleared everything around it — and a `refresh()` that reused
    /// a stale box would read the wrong fortnight once.
    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        window.set(wantedWindow)
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

    /// A page landed. If it cannot answer the range on screen, ask for that
    /// range and nothing else — **once**.
    ///
    /// Two pages arrive that cannot, and both used to end in a screen that
    /// said the wrong thing for ever:
    ///
    /// * **a page for a range the person has left.** Nothing cancels a read
    ///   whose range was abandoned while the page in hand still served the
    ///   new one (`rangeMoved`'s first branch), so the abandoned reply lands
    ///   into ``ApiRead``'s one value slot and takes the drawn page with it.
    /// * **a page cut by `LIMIT`.** It is the oldest `limit` rows of its own
    ///   window and no answer about any range inside it
    ///   (``CalendarPage/serves(_:)``), so a widened read that came back cut
    ///   leaves the view it was widened for unanswerable.
    ///
    /// It terminates, which is what makes it safe to run off a landing: the
    /// re-read is pointed at ``wantedWindow`` exactly, and a page whose
    /// window **is** the range serves it whether or not `limit` cut it — so
    /// the second landing settles, cut or not. ``ApiRead/refresh()`` is called
    /// from inside the read's own success path on purpose: it cancels the
    /// task that is running, which has published its value and is returning.
    ///
    /// That argument is load-bearing rather than decorative, so it is
    /// measured and not asserted: without the `window.set(wanted)` below the
    /// repair re-reads the window the box still holds — the union, which
    /// comes back cut, which fires the repair again — an unbounded loop
    /// against the house with ``isLoading`` true for ever. Both halves of the
    /// guard are pinned: `testAWidenedReadThatComesBackCutIsReReadForTheRangeOnScreenAndStops`
    /// (the cut page, and the ask-exactly-once) and
    /// `testComingStraightBackFromAViewWhoseReadWasAbandonedStillSettles`
    /// (the abandoned reply).
    private func pageLanded() {
        let wanted = wantedWindow
        guard events.data?.serves(wanted) != true else { return }
        window.set(wanted)
        events.refresh()
    }

    /// The saved server changed under us. Everything the person had chosen in
    /// **this** house goes with it: a selected id, a half-written draft and a
    /// pending delete all name a row id that belongs to the house they have
    /// left, and `7` exists in both.
    public func serverChanged() {
        // The window goes with everything else: a union widened over the old
        // house's ranges is not a range anybody asked this one for, and the
        // next read would be bigger than any view needs.
        window.set(wantedWindow)
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

    /// The window the next request will ask for — ``HomeModel``'s
    /// `calendarWindowStart`, for a test that has to say "and the widening
    /// asked for the union, not for the new range alone".
    public var askedWindow: CalendarWindow { window.current }

    // MARK: - what is drawn

    /// Every event the read has — the current page's rows, which are one
    /// window's and no longer "the house's first 500".
    public var all: [CalendarEntry] { events.data?.rows ?? [] }

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

    /// `"<n> events in view · the household's calendar"`
    /// (`CalendarScreen.kt:145`).
    ///
    /// Android counts the **whole** list here, because the whole list is what
    /// it read. Since answer 16 this phone reads a window, so the only honest
    /// count left is of what is drawn — ``visible`` — and the sentence says
    /// `in view` to mean it (``CalendarStrings/subtitle(count:)``).
    public var subtitle: String { CalendarStrings.subtitle(count: visible.count) }

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

    /// The week view's **seven** day groups, empty days included (answer 14).
    public var weekSections: [CalendarAgendaSection] {
        calendarWeekSections(visible, anchor: anchor, todayMs: nowMs(), in: timeZone())
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

    /// The window this view needs read — answer 16's range, as the route's
    /// two bounds.
    ///
    /// Derived, never stored: the anchor, the mode and the zone are the facts,
    /// and a cached copy of a function of three facts is a fourth fact that
    /// can be wrong.
    public var wantedWindow: CalendarWindow {
        calendarWindow(mode: mode, anchor: anchor, in: timeZone())
    }

    /// The read for the visible range has not come back yet.
    ///
    /// This replaces `data == null` in all three states below, and it is the
    /// difference a windowed read makes: the page can be a perfectly good page
    /// **of another range**, and a screen that treated it as an answer would
    /// flash `nothing in this range` on every chevron into unread territory.
    ///
    /// ``CalendarPage/serves(_:)`` and **not** a bare "the page's window
    /// covers this range".
    ///
    /// It was the second of those until REVIEW-2's must 1, and the difference
    /// is a page cut short by `LIMIT`: it covers its own window and carries
    /// only the oldest `limit` rows of it, so for a range *inside* that
    /// window it is no answer at all. Taking it for one cost both wordings at
    /// once — a failed read said `nothing on the calendar` instead of saying
    /// why (``errorText`` is gated on this being true, ``emptyText`` on its
    /// being false, and the retry link lives in the error branch), and the
    /// successful path flashed the same sentence for one round trip. Measured
    /// on the Mac: 26 agenda chevrons back into a crowded house, the read
    /// made to fail, `today` — `errorText` nil with `events.error` set, and
    /// `nothing on the calendar` permanently, with no way to retry.
    ///
    /// The spinner that `serves(_:)` could leave is closed where it arises
    /// rather than here: ``pageLanded()`` re-points the read at the range on
    /// screen when a page lands that cannot answer it, so this is pending
    /// only while something is on its way.
    public var rangePending: Bool {
        !(events.data?.serves(wantedWindow) ?? false)
    }

    /// The loading state's own condition — `loading && data == null` (`:161`),
    /// with ``rangePending`` standing in for "there is nothing for this range".
    public var isLoading: Bool { rangePending && events.error == nil }

    /// The error state's — `error != null && data == null` (`:162`).
    ///
    /// A failure **after** a success keeps the stale rows with no mark, which
    /// is ``ApiRead``'s ported behaviour and Android's — but only for the
    /// range those rows are. A read that failed while the person was stepping
    /// into a range nothing has been read for says why: the alternative is a
    /// spinner that never stops, or a screen that is silently empty for want
    /// of a request.
    public var errorText: String? {
        guard rangePending, let error = events.error else { return nil }
        return error.isEmpty ? CalendarStrings.requestFailed : error
    }

    /// The two empty wordings (`:171-180`): the agenda has its own, and every
    /// other view says what range is empty — the day label in day view, else
    /// the view's word.
    public var emptyText: (title: String, body: String)? {
        guard !rangePending, errorText == nil, visible.isEmpty else { return nil }
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
        rangeMoved()
    }

    /// `today` (`:300`) — back to the anchor the screen opened on.
    public func goToToday() {
        anchor = CalendarDay(ms: nowMs(), in: timeZone())
        rangeMoved()
    }

    /// `pickDay` (`:140`) — a month cell's tap: anchor there and switch to
    /// day, which is the only way this port's month cell reveals what is on a
    /// date (DECISIONS §4).
    public func pickDay(_ day: CalendarDay) {
        anchor = day
        mode = .day
        rangeMoved()
    }

    /// The view toggle — `view = v` (`CalendarScreen.kt:287`).
    ///
    /// A method rather than a settable ``mode`` so that every way of moving
    /// the range goes through ``rangeMoved()``. The anchor is deliberately
    /// **not** moved: Android keeps it across a view change too, so switching
    /// from the week of the 8th to month lands on the 8th's month.
    public func show(_ next: CalendarViewMode) {
        guard next != mode else { return }
        mode = next
        rangeMoved()
    }

    /// The range changed: read it, unless what is already here covers it.
    ///
    /// Three outcomes, and the middle one is what keeps the chevrons
    /// responsive:
    ///
    /// * the page already **serves** the new range — nothing is asked, and
    ///   ``ApiRead/fetches`` does not move. Agenda → day on the anchor is
    ///   this: the day is inside the agenda's own open-ended window.
    /// * the asked window **widens** to the new range
    ///   (``CalendarWindow/widens(to:)``) — their union is read, so a
    ///   fortnight of stepping back and forth settles into one window rather
    ///   than costing a request a tap.
    /// * it does not — the new range alone is read. `today` from six months
    ///   out must not read half a year to get home, and a review measured
    ///   that it did: two open-ended windows meet however far apart they are,
    ///   so in the agenda — the view this screen opens on — the floor sank
    ///   with every step back and nothing ever brought it home (REVIEW-1,
    ///   must 1).
    ///
    /// **A cut page bars a widening outright.** `truncated` is proof that
    /// `limit` and not the window is what bounds this house's read, and the
    /// route is `ORDER BY starts_at ASC`: asking from a floor below the range
    /// on screen then spends those 500 rows on instants nobody is looking at
    /// and comes back with nothing to draw. That is the empty agenda answer
    /// 16 was asked for, one range-change later.
    ///
    /// The union is taken against ``window`` (what was **asked**) and not
    /// against the page's own window, because a read may still be in flight:
    /// unioning against a landed page would ask for less than the read
    /// already on its way is bringing.
    ///
    /// That is about the **union**, and it is not an invariant that the box
    /// is never narrowed below a read in flight: the branch that asks for
    /// nothing narrows it to the range on screen unconditionally (below,
    /// REVIEW-2's must 3), and a wider read can be in flight while it does.
    /// Narrowing is safe *there* in a way that narrowing a request is not —
    /// nothing is being asked for, so no range is dropped from any request; a
    /// page carries the window it asked for (``CalendarPage``), so the wider
    /// reply still lands under its own range and nothing is misfiled; and the
    /// cost is at most one later request for rows that landing was about to
    /// bring (REVIEW-FIX-1, note 1).
    ///
    /// ``ApiRead/refresh()`` cancels whatever is in flight, which is what
    /// drops a reply for the range the person has just left.
    private func rangeMoved() {
        let wanted = wantedWindow
        let asked = window.current
        // A page cut by `LIMIT` is not evidence about any range but its own,
        // and it is evidence about the house: widening is off while it is
        // what is in hand.
        let widen = events.data?.truncated != true && asked.widens(to: wanted)
        // Served, so there is nothing to ask for: the rows are already here.
        //
        // It used to take a second condition — that the window a read may
        // still be in flight for covered the new range — because a reply for
        // a range the person has left replaces the page they are looking at,
        // and agenda → month → straight back to the agenda then left the
        // month's page in ``ApiRead``'s one slot with nothing on its way to
        // replace it: `rangePending` for ever, a spinner that never stops.
        // That guess about what might land is now ``pageLanded()``, which
        // acts on what actually did, and the guess cost a round trip it did
        // not have to: the box is narrowed to the range on screen below, so
        // the very next backwards chevron failed it and re-read rows that
        // were already drawn (REVIEW-2, should 5).
        if events.data?.serves(wanted) == true {
            // The box is pointed at the range on screen, always. It must not
            // be left holding a floor nobody is looking at: `today` after a
            // walk into the past lands exactly here, and a box still pointed
            // at may means the **next** read — a push, a write, the screen
            // coming back — spends its limit on the summer and comes home
            // with nothing to draw (REVIEW-2, must 3; REVIEW-1, must 1).
            // Pointing it costs no request, and misfiles nothing: a page
            // carries the window it asked for (``CalendarPage``), so anything
            // in flight still lands under its own range.
            window.set(wanted)
            return
        }
        window.set(widen ? asked.union(wanted) : wanted)
        events.refresh()
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

// MARK: - what one read came back with

/// One windowed read's answer — **the rows together with the window they were
/// asked for.**
///
/// The pairing is the point. `ApiRead<[CalendarEntry]>` would hand the model a
/// bare array and leave it to work out which range those rows are, by reading
/// the range back after the `await` — the read-after-`await`-wrong-key defect
/// with the house swapped for the range, and the one shape of it a view could
/// not possibly detect, since rows for the wrong fortnight look exactly like a
/// server that answered wrongly. Here the key travels inside the value, so
/// there is nothing to read back.
public struct CalendarPage: Equatable, Sendable {

    /// What was asked for, read off ``CalendarModel``'s window box **before**
    /// the request went.
    public let window: CalendarWindow

    /// What came back, in the server's order (`ORDER BY starts_at ASC`).
    public let rows: [CalendarEntry]

    /// The read came back with `limit` rows, so `LIMIT` may have cut it and
    /// ``rows`` may not be everything in ``window``.
    public let truncated: Bool

    public init(window: CalendarWindow, rows: [CalendarEntry], truncated: Bool = false) {
        self.window = window
        self.rows = rows
        self.truncated = truncated
    }

    /// `wanted` can be drawn from these rows with no further request.
    ///
    /// Both of the two questions a screen has about a page — "is this the
    /// answer for the range on screen?" (``CalendarModel/rangePending``) and
    /// "do I need to ask?" (``CalendarModel`` `rangeMoved`) — are this one.
    /// There used to be a second, weaker predicate (`landed(for:)`, the bare
    /// `window.contains(wanted)`) for the first of them, and a cut page
    /// passing it is what made a failed read say the calendar was empty
    /// (REVIEW-2, must 1). It is gone rather than left with no caller.
    ///
    /// A **cut** page serves only itself: it is the first `limit` rows of its
    /// window, so for any smaller range inside it there may be rows it never
    /// carried. `window == wanted` is the one case where that does not matter,
    /// because the read for `wanted` is exactly this one and reading it again
    /// would return the same cut.
    public func serves(_ wanted: CalendarWindow) -> Bool {
        if window == wanted { return true }
        return !truncated && window.contains(wanted)
    }
}

/// The window the calendar read will ask for, readable from its `@Sendable`
/// fetch closure — ``HomeModel``'s `DayWindow` and ``FilesModel``'s
/// `FileFolderBox`, for the same reason: `Mutex` is `~Copyable`, so it can be
/// a stored property but not a capture.
final class CalendarWindowBox: Sendable {

    private let value: Mutex<CalendarWindow>

    init(_ initial: CalendarWindow) {
        value = Mutex(initial)
    }

    var current: CalendarWindow { value.withLock { $0 } }

    func set(_ next: CalendarWindow) {
        value.withLock { $0 = next }
    }
}
