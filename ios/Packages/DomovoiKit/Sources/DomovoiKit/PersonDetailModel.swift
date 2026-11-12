import Foundation
import Observation

/// The outcome of one sub-fetch: the rows, or why they could not be loaded
/// (`Fetched`, `PeopleModels.kt:148`).
///
/// The point of the type is that a failure is kept **per source**: the list
/// is empty *and* carries its error, so a tab can show an error card with
/// retry instead of an innocent "nothing here yet". That is F-A008, which was
/// opened for exactly that lie.
public enum PersonFetched<Value: Sendable>: Sendable {

    case ok(Value)
    case failed(String)

    public func orElse(_ fallback: Value) -> Value {
        switch self {
        case .ok(let value): return value
        case .failed: return fallback
        }
    }

    public var error: String? {
        switch self {
        case .ok: return nil
        case .failed(let why): return why
        }
    }
}

extension PersonFetched: Equatable where Value: Equatable {}

/// One person's detail: the four tabs' data, the six writes a phone is
/// allowed to make, and the switch that must not cross them over.
///
/// `rememberPersonDetail` (`PeopleModels.kt:227`) plus the state the three
/// tab composables hold — five sequential reads keyed on the person, each
/// keeping its own failure, and one toast per load naming every list that
/// failed.
///
/// ### The two rules this model exists to get right
///
/// **Read after `await` with the key captured before it.** The detail is one
/// model that is pointed at a person (``show(_:)``), not one model per row,
/// so a person switched while five requests are in flight is the ordinary
/// case rather than the rare one. Every load and every write captures
/// ``generation`` *before* its first `await` and publishes only while it
/// still matches, so person A's memories can never land under person B. That
/// is the top defect class on this track ([[read-after-await-wrong-key]]) and
/// `PersonDetailModelTests` switches the person **inside** a parked round
/// trip, which is the only arrangement that tests it.
///
/// A generation counter rather than comparing the person's id: reopening the
/// *same* person after a change must also reject the older answer, and an id
/// check would accept it.
///
/// **A lazy row never holds a `Task`.** Every write is a method here, on a
/// model that outlives the row that called it — a `Task` started in a
/// `LazyVStack` row is cancelled when the row scrolls out of the built range,
/// which is a `POST` that silently did not happen.
///
/// ### What this does not do, and why there is no control for it
///
/// Renaming, forgetting a person, deleting a voice sample and deleting a
/// denylist entry are all `require_admin_mutation` — see ``PeopleModel``'s
/// heading and ``PeoplePath``. **Changing a preference** would work from a
/// phone (`PATCH …/preferences` is device tier, `people.py:691`) but
/// Android's tab is read-only, so this is read-only: NIGHT-RULES §6's
/// Android-faithful default, since inventing an editor for a JSONB bag with
/// no schema is not a port.
@MainActor
@Observable
public final class PersonDetailModel {

    private let container: AppContainer
    private let clock: @Sendable () -> Int64

    /// Who the detail is pointed at. Nil before ``show(_:)``.
    public private(set) var person: Person?

    /// Which answers belong to the person on screen. Bumped by every
    /// ``show(_:)``, ``close()`` and ``refresh()``; see this type's heading.
    private var generation = 0

    private var load: Task<Void, Never>?

    public private(set) var sessions: PersonFetched<[PersonSession]> = .ok([])
    public private(set) var conversations: PersonFetched<[PersonTurn]> = .ok([])
    public private(set) var memories: PersonFetched<[PersonMemory]> = .ok([])
    public private(set) var favorites: PersonFetched<[PersonFavorite]> = .ok([])
    public private(set) var preferences: PersonFetched<[PersonPreference]> = .ok([])

    /// True from the first read of a person until the fifth one answers, as
    /// Android's single `loading` is — the five are sequential, so there is
    /// one span and not five.
    public private(set) var loading = false

    public private(set) var toast: String?

    // ---- what is typed into the memory tab --------------------------------

    public private(set) var newMemory = ""
    public private(set) var newFavoriteKind = ""
    public private(set) var newFavoriteValue = ""

    /// Whether the rejected memories are disclosed (`PersonTabs.kt:78`).
    /// Model state rather than view state because it is `remember(person.id)`
    /// over there: it must reset when the person changes, and a view's
    /// `@State` on a destination that is reused would not.
    public private(set) var showRejected = false

    /// What is typed in the conversations tab's search (`PersonTabs.kt:607`).
    public private(set) var query = ""

    /// A write in flight: `"memory"` or `"favorite"`, nil otherwise.
    ///
    /// Android has no such flag — it launches each write into the composable
    /// scope and lets two overlap. This has one so the view can disable a
    /// `save` that is already saving; a second tap on a slow house would
    /// otherwise `POST` the same memory twice, and the memories route does
    /// not upsert (`people.py:461`), where favourites do (`:633`).
    public private(set) var busy: String?

    public init(
        in container: AppContainer,
        clock: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.container = container
        self.clock = clock
    }

    // MARK: - pointing it at somebody

    /// Show `person`, dropping everything the previous one's reads were
    /// doing.
    ///
    /// Re-showing the person already on screen is **not** a reload: Android
    /// keys its state on `person.id`, so re-selecting the same row keeps what
    /// is there. What does change is the ``person`` value itself, because the
    /// roster's push may have moved their `last_seen_at`.
    public func show(_ next: Person) {
        guard person?.id != next.id else {
            person = next
            return
        }
        generation += 1
        load?.cancel()
        person = next
        sessions = .ok([])
        conversations = .ok([])
        memories = .ok([])
        favorites = .ok([])
        preferences = .ok([])
        newMemory = ""
        newFavoriteKind = ""
        newFavoriteValue = ""
        showRejected = false
        query = ""
        busy = nil
        toast = nil
        reload()
    }

    /// The detail closed. The in-flight reads are dropped, and so is their
    /// right to publish.
    public func close() {
        generation += 1
        load?.cancel()
        load = nil
        person = nil
        busy = nil
    }

    /// `refresh()` — `tick++` (`PeopleModels.kt:277`), which re-runs all
    /// five.
    public func refresh() {
        guard person != nil else { return }
        generation += 1
        load?.cancel()
        // Every other generation bump (`show(_:)`, `close()`) releases the
        // write flag; this one must too, so the flag's lifetime never
        // outlives the generation that owns it.
        busy = nil
        reload()
    }

    private func reload() {
        guard let id = person?.id else { return }
        // Captured BEFORE the first await, and compared after every one.
        let mine = generation
        let client = container.client
        loading = true
        load = Task { @MainActor [weak self] in
            let gotSessions = await Self.fetch {
                try await client.get(PeoplePath.sessions(id), as: [PersonSession].self)
            }
            guard let self, self.generation == mine else { return }
            self.sessions = gotSessions

            let gotConversations = await Self.fetch {
                try await client.get(PeoplePath.conversations(id), as: [PersonTurn].self)
            }
            guard self.generation == mine else { return }
            self.conversations = gotConversations

            let gotMemories = await Self.fetch {
                try await client.get(PeoplePath.memories(id), as: [PersonMemory].self)
            }
            guard self.generation == mine else { return }
            self.memories = gotMemories

            let gotFavorites = await Self.fetch {
                try await client.get(PeoplePath.favorites(id), as: [PersonFavorite].self)
            }
            guard self.generation == mine else { return }
            self.favorites = gotFavorites

            let gotPreferences = await Self.fetch { () -> [PersonPreference] in
                let body = try await client.raw(method: "GET", path: PeoplePath.preferences(id))
                // The route answers the JSONB column as a **bare object**
                // (`people.py:688`), so a body that is not an object is a
                // shape this app does not understand — a failed read, not an
                // empty section. "A wrapper where the server sends a bare
                // value" is the trap that cost Home four sections.
                guard let rows = PeoplePreferences.read(body) else {
                    throw DomovoiError.notDomovoi(PeopleStrings.requestFailed)
                }
                return rows
            }
            guard self.generation == mine else { return }
            self.preferences = gotPreferences

            self.loading = false
            // One toast per load, naming every failed list in tab order and
            // quoting the first failure (`PeopleModels.kt:204`).
            if let said = personDetailFailureToast(self.errors) { self.toast = said }
        }
    }

    /// One sub-fetch, keeping its failure instead of throwing it
    /// (`fetched`, `PeopleModels.kt:168`).
    ///
    /// Cancellation is **not** a failure: Android rethrows
    /// `CancellationException` so that a re-keyed effect does not paint an
    /// error card for the read it replaced. Here the generation check does
    /// the same job, and the `.failed("cancelled")` that a bare `catch` would
    /// produce is kept out of the value anyway.
    private static func fetch<Value: Sendable>(
        _ body: () async throws -> Value
    ) async -> PersonFetched<Value> {
        do {
            return .ok(try await body())
        } catch is CancellationError {
            return .failed(PeopleStrings.requestFailed)
        } catch let trouble {
            return .failed(Self.why(trouble))
        }
    }

    nonisolated static func why(_ error: any Error) -> String {
        let text = (error as? DomovoiError)?.message ?? error.localizedDescription
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? PeopleStrings.requestFailed : trimmed
    }

    // MARK: - what the tabs read

    public var errors: PersonDetailErrors {
        PersonDetailErrors(
            sessions: sessions.error,
            conversations: conversations.error,
            memories: memories.error,
            favorites: favorites.error,
            preferences: preferences.error
        )
    }

    public var sessionRows: [PersonSession] { sessions.orElse([]) }
    public var turnRows: [PersonTurn] { conversations.orElse([]) }
    public var memoryRows: [PersonMemory] { memories.orElse([]) }
    public var favoriteRows: [PersonFavorite] { favorites.orElse([]) }
    public var preferenceRows: [PersonPreference] { preferences.orElse([]) }

    /// The four tab titles, with `"?"` for a list that failed
    /// (`PersonDetail.kt:111-119`).
    public var tabTitles: [String] {
        [
            PeopleStrings.tabProfile,
            PeopleStrings.tabMemory(
                personCountLabel(memoryRows.count + favoriteRows.count, error: errors.memoryTab)
            ),
            PeopleStrings.tabSessions(personCountLabel(sessionRows.count, error: errors.sessions)),
            PeopleStrings.tabConversations(
                personCountLabel(turnRows.count, error: errors.conversations)
            ),
        ]
    }

    // MARK: - the memory tab

    /// The rows of one status, and nothing else — a memory whose status the
    /// server made up appears in none of the three lists, which is Android's
    /// behaviour too (its three filters would all miss it).
    ///
    /// Named away from the ``memories`` property on purpose: a method and a
    /// property with one base name is a redeclaration this package cannot
    /// check from Windows.
    public func memoriesWithStatus(_ status: PersonMemoryStatus) -> [PersonMemory] {
        memoryRows.filter { $0.status == status }
    }

    public var pendingMemories: [PersonMemory] { memoriesWithStatus(.pending) }
    public var activeMemories: [PersonMemory] { memoriesWithStatus(.active) }
    public var rejectedMemories: [PersonMemory] { memoriesWithStatus(.rejected) }

    /// `"2 active · 1 pending"` (`PersonTabs.kt:165`).
    public var memoryCounts: String {
        PeopleStrings.memoryCounts(active: activeMemories.count, pending: pendingMemories.count)
    }

    /// Whether the empty note is what the memories section shows.
    ///
    /// **Never when the read failed** — that is the error card's place
    /// (F-A008) — and never while there are pending rows, which is Android's
    /// `active.isEmpty() && pending.isEmpty()` (`PersonTabs.kt:219`): a
    /// person with one pending memory and no active ones has memories, and
    /// saying otherwise above the band listing them would read as a bug.
    public var showsNoMemoriesNote: Bool {
        errors.memories == nil && activeMemories.isEmpty && pendingMemories.isEmpty
    }

    /// The favourites grouped by kind, first-appearance order kept — see
    /// ``PersonFavoriteGroup``.
    public var favoriteGroups: [PersonFavoriteGroup] {
        var order: [String] = []
        var byKind: [String: [PersonFavorite]] = [:]
        for favorite in favoriteRows {
            if byKind[favorite.kind] == nil { order.append(favorite.kind) }
            byKind[favorite.kind, default: []].append(favorite)
        }
        return order.map { PersonFavoriteGroup(kind: $0, favorites: byKind[$0] ?? []) }
    }

    /// `"3 saved · 2 kinds"` (`PersonTabs.kt:264`).
    public var favoriteCounts: String {
        PeopleStrings.favoriteCounts(saved: favoriteRows.count, kinds: favoriteGroups.count)
    }

    /// `"3 keys"` (`PersonTabs.kt:316`).
    public var preferenceCounts: String {
        PeopleStrings.preferenceCounts(preferenceRows.count)
    }

    /// The spinner, not an empty tab: `loading` **and** nothing in any of the
    /// three sections (`PersonTabs.kt:153`).
    public var memoryTabIsLoading: Bool {
        loading && memoryRows.isEmpty && favoriteRows.isEmpty && preferenceRows.isEmpty
    }

    public func typedMemory(_ text: String) { newMemory = text }
    public func typedFavoriteKind(_ text: String) { newFavoriteKind = text }
    public func typedFavoriteValue(_ text: String) { newFavoriteValue = text }
    public func toggleRejected() { showRejected.toggle() }

    /// `enabled = newBody.isNotBlank()` (`PersonTabs.kt:185`), and not while
    /// a save is already in flight (see ``busy``).
    public var canSaveMemory: Bool {
        !newMemory.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && busy == nil
    }

    /// Both fields (`PersonTabs.kt:292`) — a `save` that could only ever 422
    /// is a control that cannot do what it says.
    public var canSaveFavorite: Bool {
        !newFavoriteKind.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !newFavoriteValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && busy == nil
    }

    // MARK: - the sessions tab

    /// This session's turns, filtered client-side by `session_id`
    /// (`PersonTabs.kt:475`).
    ///
    /// Client-side because there is no per-session turns route: the
    /// conversations read is capped at `limit=200`, so a session older than
    /// the two hundredth turn legitimately has none in hand — which is why
    /// the expanded row says ``PeopleStrings/noRowsInSession`` rather than
    /// drawing nothing.
    public func turns(in session: PersonSession) -> [PersonTurn] {
        turnRows.filter { $0.sessionID == session.id }
    }

    public var sessionsTabIsLoading: Bool { loading && sessionRows.isEmpty }

    /// `"Jordan hasn't had a session yet"`, or nil when there are rows or the
    /// read failed (`PersonTabs.kt:466`).
    public var sessionsEmptyNote: (title: String, detail: String)? {
        guard errors.sessions == nil, !sessionsTabIsLoading, sessionRows.isEmpty else { return nil }
        return (
            PeopleStrings.noSessionsYet(person?.name ?? ""),
            PeopleStrings.noSessionsYetSub
        )
    }

    /// `"2 turns"` and `"last 4m ago"` — the row's right-hand pair
    /// (`PersonTabs.kt:519-524`).
    public func sessionMeta(_ session: PersonSession) -> (turns: String, last: String) {
        (
            PeopleStrings.turnCount(session.intentCount),
            PeopleStrings.lastHeard(relTime(session.lastActivity, nowMs: clock()))
        )
    }

    /// `"aaaa1111 · 2:00"` (`PersonTabs.kt:511`).
    public func sessionSubtitle(_ session: PersonSession) -> String {
        "\(session.shortID) · \(fmtDur(session.durationSec))"
    }

    public func sessionTime(_ session: PersonSession) -> String {
        relTime(session.startedAt, nowMs: clock())
    }

    public func turnTime(_ turn: PersonTurn) -> String {
        relTime(turn.at, nowMs: clock())
    }

    // MARK: - the conversations tab

    public func typedQuery(_ text: String) { query = text }

    /// `user_text + " " + assistant_text`, case-insensitively
    /// (`PersonTabs.kt:608-611`). Both halves, which is why a search for a
    /// word only Domovoi said still finds the turn.
    public var filteredTurns: [PersonTurn] {
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return turnRows }
        return turnRows.filter { turn in
            let haystack = "\(turn.userText ?? "") \(turn.assistantText ?? "")"
            return haystack.localizedCaseInsensitiveContains(query)
        }
    }

    /// `"1 of 4"` (`PersonTabs.kt:653`) — the filtered count over the
    /// **unfiltered** total, which is what makes a search that found one
    /// thing out of four readable.
    public var conversationCount: String {
        PeopleStrings.shownOf(filteredTurns.count, turnRows.count)
    }

    public var conversationsTabIsLoading: Bool { loading && turnRows.isEmpty }

    /// `"nothing matches"` with either the query or "hasn't said anything
    /// yet" under it (`PersonTabs.kt:659-663`).
    public var conversationsEmptyNote: (title: String, detail: String)? {
        guard errors.conversations == nil, !conversationsTabIsLoading, filteredTurns.isEmpty else {
            return nil
        }
        let typed = !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return (
            PeopleStrings.nothingMatches,
            typed
                ? PeopleStrings.queryIs(query)
                : PeopleStrings.nothingSaidYet(person?.name ?? "")
        )
    }

    // MARK: - the six writes

    /// `POST …/memories {"body": …}` (`PersonTabs.kt:87`).
    ///
    /// `require_device` (`people.py:463`), which **a paired phone passes** —
    /// this is one of the six things this screen can really do.
    public func saveMemory() {
        guard let id = person?.id else { return }
        let body = newMemory.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty, busy == nil else { return }
        write("memory", said: PeopleStrings.savedMemory, failed: PeopleStrings.saveFailed) {
            client in
            try await client.send(
                method: "POST",
                path: PeoplePath.memories(id),
                body: PeoplePath.memoryBody(body)
            )
        } onSuccess: { model in
            model.newMemory = ""
        }
    }

    /// `PATCH …/memories/{id} {"status": …}` (`PersonTabs.kt:104`) —
    /// `approve` and `reject` are one call with two values, as Android has
    /// it, so the two cannot send different shapes.
    public func setStatus(_ memory: PersonMemory, to status: PersonMemoryStatus) {
        guard let id = person?.id, busy == nil else { return }
        let said = status == .active ? PeopleStrings.approved : PeopleStrings.rejected
        write("memory", said: said, failed: PeopleStrings.patchFailed) { client in
            try await client.send(
                method: "PATCH",
                path: PeoplePath.memory(id, memory.id),
                body: PeoplePath.statusBody(status)
            )
        }
    }

    /// `DELETE …/memories/{id}` — `204`, no body (`PersonTabs.kt:118`).
    public func deleteMemory(_ memory: PersonMemory) {
        guard let id = person?.id, busy == nil else { return }
        write("memory", said: PeopleStrings.deleted, failed: PeopleStrings.deleteFailed) {
            client in
            try await client.send(method: "DELETE", path: PeoplePath.memory(id, memory.id))
        }
    }

    /// `POST …/favorites {"kind": lower, "value": …, "rank": 0}`
    /// (`PersonTabs.kt:126`). The kind is lower-cased, as Android lower-cases
    /// it (`:127`), because the server upserts on `(person, kind, value)`
    /// (`people.py:633`) and `Team` and `team` would otherwise be two kinds.
    public func saveFavorite() {
        guard let id = person?.id else { return }
        let kind = newFavoriteKind.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let value = newFavoriteValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !kind.isEmpty, !value.isEmpty, busy == nil else { return }
        write(
            "favorite",
            said: PeopleStrings.savedFavorite,
            failed: PeopleStrings.saveFailed
        ) { client in
            try await client.send(
                method: "POST",
                path: PeoplePath.favorites(id),
                body: PeoplePath.favoriteBody(kind: kind, value: value)
            )
        } onSuccess: { model in
            model.newFavoriteKind = ""
            model.newFavoriteValue = ""
        }
    }

    /// `DELETE …/favorites/{id}` — `204`, no body (`PersonTabs.kt:145`).
    public func deleteFavorite(_ favorite: PersonFavorite) {
        guard let id = person?.id, busy == nil else { return }
        write("favorite", said: PeopleStrings.deleted, failed: PeopleStrings.deleteFailed) {
            client in
            try await client.send(method: "DELETE", path: PeoplePath.favorite(id, favorite.id))
        }
    }

    /// The one write path: send it, then say so and re-read.
    ///
    /// The generation is captured **before** the request and checked after
    /// it, exactly as ``reload()`` does. Without that, a save whose answer
    /// lands after the reader went back and opened somebody else would toast
    /// `saved memory` over the new person's screen and call ``refresh()`` for
    /// a person whose reads had already been replaced — person A's answer
    /// under person B, in its write form.
    private func write(
        _ what: String,
        said: String,
        failed: @escaping @Sendable (String) -> String,
        _ body: @escaping @Sendable (DomovoiClient) async throws -> Void,
        onSuccess: @escaping @Sendable @MainActor (PersonDetailModel) -> Void = { _ in }
    ) {
        let mine = generation
        let client = container.client
        let failedText = failed
        busy = what
        Task { @MainActor [weak self] in
            do {
                try await body(client)
                guard let self else { return }
                // The flag is released whatever the generation did while the
                // request was out: `refresh()` bumps the generation too (a
                // retry on one of the memory tab's three error cards does
                // exactly that), and a write that returned early here used to
                // leave `busy` set for ever, which killed all six writes this
                // screen allows until the detail was closed.
                if self.busy == what { self.busy = nil }
                guard self.generation == mine else { return }
                onSuccess(self)
                self.toast = said
                // `detail.refresh()` after every write (`PersonTabs.kt:99`):
                // the server decides the new row's id, status and source, so
                // the list is re-read rather than patched up locally.
                self.refresh()
            } catch {
                guard let self else { return }
                if self.busy == what { self.busy = nil }
                guard self.generation == mine else { return }
                self.toast = failedText(Self.why(error))
            }
        }
    }

    public func setToast(_ said: String?) { toast = said }

    /// The app moved house: this person is the old house's.
    public func serverChanged() {
        close()
        toast = nil
    }
}
