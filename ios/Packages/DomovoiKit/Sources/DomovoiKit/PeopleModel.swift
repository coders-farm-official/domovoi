import Foundation
import Observation

/// The People screen: the household roster, the filter over it, who is in the
/// room, and the denylist card.
///
/// `PeopleScreen.kt:66`, whose state is two `rememberApi`s and three
/// `remember`s inside a composable. It is a model here for the reason every
/// one of these has been: which of the roster's **four** states is on screen,
/// whether a row is live, and whether an empty list means "nobody enrolled"
/// or "your filter matched nothing" are decisions, and a view cannot be asked
/// whether it got them right. The two empty notes in particular are not
/// interchangeable — `nobody enrolled yet` over a house with four people in
/// it is a lie about the house.
///
/// ### What this does not do, and why there is no control for it
///
/// **Renaming** and **forgetting** a person. `PATCH /api/people/{id}` and
/// `DELETE /api/people/{id}` are `require_admin_mutation`
/// (`people.py:145`, `:355`), which is Bearer-only — a dashboard cookie is a
/// 403 and anything else a 401 (`admin_auth.py:1288-1305`). This app holds a
/// household device token and ``DomovoiClient`` refuses the cookie, so both
/// would 401 every single time from a phone. Android draws both and reports
/// the failure in a toast afterwards; under the owner's no-misleading-controls
/// rule neither is drawn here, there is no path constant for either
/// (``PeoplePath`` says why), and the profile says where they are done
/// (``PeopleStrings/renamingIsOnTheDashboard``).
///
/// **Voice enrolment, and the microphone.** Nothing in Android's People
/// records anything: it shows `voice_profile_count` as a number and never
/// calls `GET /api/people/{id}/profiles` or the per-sample delete. So this
/// port reads neither, asks for no microphone, and `project.yml` is
/// untouched. A phone-sourced sample reaches the server through the chat mic,
/// which is Chat's business. Recorded in `overnight/people/DECISIONS.md` with
/// the question of whether a per-sample screen is worth building later.
///
/// **Deleting a denylist entry** (admin tier, `denylist.py:40`) and
/// **changing a preference** (device tier, but Android's tab is read-only).
///
/// **The expanded two-pane layout** (`PeopleScreen.kt:121-156`). Every iPhone
/// is compact, so the detail is a pushed destination and the rail's
/// *"pick someone from the list"* empty state has nowhere to be.
@MainActor
@Observable
public final class PeopleModel {

    /// `people.last_seen.changed`: somebody spoke, so a row's `last_seen_at`
    /// — and with it the green dot and the header's live count — moved
    /// (`PeopleScreen.kt:67`; the socket pushes it, see
    /// `domovoi/tests/test_web_state_socket_auth.py:273`).
    public static let rosterEvents: Set<String> = ["people.last_seen.changed"]

    private let container: AppContainer
    private let clock: @Sendable () -> Int64
    private var eventSink: Int?

    /// The whole household. **Not a ``Pager``**: `/api/people` takes no
    /// `limit` and returns every row (`people.py:87`), and a pager over a
    /// route with no cursor would ask the same question twice.
    public let roster: ApiRead<[Person]>

    /// The opted-out voices — read for their **count** and nothing else.
    ///
    /// `GET /api/denylist` has no auth dependency at all (`denylist.py:22`),
    /// which is why this read is here and not gated on pairing: it answers
    /// for a phone whose household token the house has forgotten, where the
    /// roster beside it would 401.
    public let denylist: ApiRead<[PeopleDenylistEntry]>

    /// What is typed in the filter field, which is also what the list is
    /// filtered by.
    ///
    /// **One property, no debounce and no second "needle"**: the filter is
    /// client-side over rows already in hand (`PeopleScreen.kt:177`), so
    /// there is no request to debounce, and a `needle` that always equalled
    /// `filter` would be a seam with no caller. Files has two because its
    /// filter goes to the server.
    public private(set) var filter = ""

    /// Who the detail is open on, or nil on the roster.
    public private(set) var selectedID: Int?

    /// Whether the denylist view is open. The third branch of Android's
    /// compact `when` (`PeopleScreen.kt:89-120`), kept as state rather than
    /// as a navigation destination so that the model can say which of the
    /// three screens is up.
    public private(set) var denylistOpen = false

    public init(
        in container: AppContainer,
        clock: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.container = container
        self.clock = clock
        let client = container.client
        self.roster = ApiRead(in: container, eventTypes: Self.rosterEvents) {
            try await client.get(PeoplePath.roster, as: [Person].self)
        }
        self.denylist = ApiRead(in: container) {
            try await client.get(PeoplePath.denylist, as: [PeopleDenylistEntry].self)
        }
    }

    // MARK: - the household

    public var people: [Person] { roster.data ?? [] }

    /// How many were heard in the last five minutes
    /// (`PeopleScreen.kt:76`), measured against this model's clock so that
    /// the boundary can be tested without waiting for it.
    public var liveCount: Int {
        let now = clock()
        return people.filter { peopleIsLive($0.lastSeenAt, nowMs: now) }.count
    }

    /// `"4 enrolled · 1 heard in the last 5 min"` (`PeopleScreen.kt:85`).
    ///
    /// The one line on the screen only a **completed** read can produce,
    /// which is why the UI case that proves the roster landed waits for it
    /// rather than for a row.
    public var subtitle: String {
        PeopleStrings.subtitle(enrolled: people.count, live: liveCount)
    }

    public var denylistCount: Int { denylist.data?.count ?? 0 }

    public func isLive(_ person: Person) -> Bool {
        peopleIsLive(person.lastSeenAt, nowMs: clock())
    }

    /// `"#3 · same name · 3d ago · high"` (`PeopleScreen.kt:289`).
    public func subtitle(for person: Person) -> String {
        rosterSubtitle(person, nowMs: clock())
    }

    /// `"3p"` — how many voice profiles they have (`PeopleScreen.kt:295`).
    public func profileCount(for person: Person) -> String {
        "\(person.voiceProfileCount)p"
    }

    // MARK: - the filter, and the four states

    /// The rows the filter leaves: `name.contains(search, ignoreCase = true)`
    /// (`PeopleScreen.kt:177`).
    ///
    /// Name only, and case-insensitively — not the notes, not the id.
    ///
    /// Two details are Android's rather than tidier: a **blank** filter
    /// matches everything (Kotlin's `isBlank()`, so a field holding two
    /// spaces shows the whole roster), but a filter that is not blank is
    /// matched **untrimmed** — `"chev "` with the trailing space matches
    /// nobody, because that is what `name.contains(" chev ")` does over
    /// there. Trimming it here would make the phone and the dashboard
    /// disagree about what was searched for, so the oddity is ported and
    /// written down.
    ///
    /// `localizedCaseInsensitiveContains` rather than lower-casing both
    /// sides: `"İ"` lower-cases to two scalars in Turkish and a household
    /// roster is full of names that are not ASCII.
    public var filtered: [Person] {
        guard !filter.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return people }
        return people.filter { $0.name.localizedCaseInsensitiveContains(filter) }
    }

    /// Which of the roster's four states is on screen
    /// (`PeopleScreen.kt:200-224`).
    ///
    /// Loading and failed are reported **only while there is nothing to
    /// show**, which is Android's `state.data == null` on both branches: a
    /// push-driven refresh that fails leaves the rows and the last answer
    /// where they are rather than blanking the roster.
    public var rosterState: PeopleRosterState {
        if roster.loading && roster.data == nil { return .loading }
        if let error = roster.error, roster.data == nil { return .failed(error) }
        let rows = filtered
        if rows.isEmpty { return people.isEmpty ? .nobodyEnrolled : .noMatch }
        return .rows(rows)
    }

    public func typed(_ text: String) { filter = text }

    public func clearFilter() { filter = "" }

    // MARK: - where we are

    /// The selected person, **as the latest roster read has them**.
    ///
    /// Looked up by id on every read rather than held as a value, which is
    /// Android's `people.firstOrNull { it.id == selectedId }`
    /// (`PeopleScreen.kt:81`). It is the half of this screen that keeps the
    /// detail honest: when `people.last_seen.changed` arrives the open
    /// profile's `live` pill and `last heard` tile move with it, and a person
    /// forgotten on the dashboard disappears from under the detail rather
    /// than staying on screen for ever.
    public var selected: Person? {
        guard let selectedID else { return nil }
        return people.first { $0.id == selectedID }
    }

    public func select(_ person: Person) {
        denylistOpen = false
        selectedID = person.id
    }

    public func closeDetail() { selectedID = nil }

    public func openDenylist() {
        selectedID = nil
        denylistOpen = true
    }

    public func closeDenylist() { denylistOpen = false }

    // MARK: - coming and going

    /// The screen appeared: read both lists and listen for the push.
    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        roster.refresh()
        denylist.refresh()
    }

    /// A push arrived. Each read filters by type itself, exactly as the web
    /// hooks do — the denylist has no event and therefore ignores every one
    /// (`PeopleScreen.kt:70`).
    public func received(_ event: StateEvent) {
        roster.received(event)
        denylist.received(event)
    }

    /// The app moved house. Everything this screen holds is the old house's,
    /// including the selected id — two houses can both have a person 3.
    public func serverChanged() {
        selectedID = nil
        denylistOpen = false
        filter = ""
        roster.serverChanged()
        denylist.serverChanged()
    }

    public func stop() {
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        roster.stop()
        denylist.stop()
    }
}
