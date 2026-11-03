import Foundation
import Observation

/// The News page: the roster, the nine category words, who is picked, and the
/// one person's detail that is on screen.
///
/// `NewsScreen.kt:57-114` — two reads, a default selection and a five-way
/// branch, as a model because each of those is a decision a view cannot be
/// asked about: which person is picked when the roster first lands, which of
/// five things the pane shows, and whether the detail on screen belongs to
/// the person whose name is highlighted.
///
/// ### Only the compact layout
///
/// Android draws a 240 dp people rail at expanded width and a horizontal chip
/// row at compact (`NewsScreen.kt:99-113`). **Every iPhone is compact**, so
/// only the chip row is ported; the rail's one extra fact, `heard <relTime>`,
/// is kept and shown under the name where the chips are already a column.
/// DECISIONS.md records the call. There is no `compact` flag here, because a
/// branch nothing can enter is a branch nobody tests.
///
/// ### What this screen does not need that Files did
///
/// Nothing is dropped for authentication. The `/api/news` reads are open and
/// every write is `require_device` (`news.py:52`) — the household token this
/// app already holds — so the whole Android screen ports, controls and all.
/// The roster is the one read that can be refused: `/api/people` is
/// `require_device_read` (`people.py:60-80`), and an unpaired phone gets a
/// 401 there, which this screen shows as the roster's own failure with a
/// retry rather than as "nobody is enrolled".
@MainActor
@Observable
public final class NewsModel {

    /// `people.last_seen.changed` — somebody was heard, which changes the
    /// roster's **order** and so which person is "first"
    /// (`NewsScreen.kt:63`).
    public static let rosterEvents: Set<String> = ["people.last_seen.changed"]

    public let people: ApiRead<[NewsPerson]>

    /// The nine category words, in the server's `CATEGORY_FEEDS` insertion
    /// order (`news.py:112-115`, `domovoi/clients/news_feeds.py:69-88`) — a
    /// static set, with **no event types**, as Android reads it
    /// (`NewsScreen.kt:66-68`).
    public let categories: ApiRead<[String]>

    public private(set) var selectedID: Int64?

    /// The person on screen, or nil for one of the four other pane states.
    ///
    /// Rebuilt from scratch whenever ``selectedID`` changes, which is
    /// Android's `key(selectedPerson.id)` (`NewsScreen.kt:90-92`): nothing of
    /// the previous person's state survives — not their reads, not the
    /// free-form draft, not which topics were expanded.
    public private(set) var detail: NewsPersonModel?

    /// The last thing a write had to say. The shell draws it; the view clears
    /// it with ``setToast(_:)``.
    ///
    /// **It lives here and not on ``NewsPersonModel``**, which is where the
    /// map put it: a failed write's sentence has to outlive the person it was
    /// made for, because tapping another chip while a slow DELETE is in
    /// flight would otherwise throw the refusal away with the model. Files
    /// found the same thing about leaving a screen (`FilesView.swift:86-93`).
    /// A line from a person who is no longer picked is still **dropped** —
    /// see ``sink(for:)``.
    public private(set) var toast: String?

    private let container: AppContainer

    public init(in container: AppContainer) {
        self.container = container
        let client = container.client
        self.people = ApiRead(in: container, eventTypes: Self.rosterEvents) {
            try await client.get(NewsPath.people, as: [NewsPerson].self)
        }
        self.categories = ApiRead(in: container) {
            try await client.get(NewsPath.categories, as: [String].self)
        }
        // On the read that **landed**, not on every change: a failed roster
        // read must not move the selection.
        self.people.onValue = { [weak self] in self?.rosterLanded() }
    }

    // MARK: - the roster

    public var roster: [NewsPerson] { people.data ?? [] }

    public var categoryWords: [String] { categories.data ?? [] }

    public var selectedPerson: NewsPerson? {
        roster.first { $0.id == selectedID }
    }

    /// Pick the first row once the roster lands, and **only while nothing is
    /// picked** — `NewsScreen.kt:70-74`'s `if (selectedId == null)`.
    ///
    /// "The first row" is "most recently heard" because the server sorts
    /// `last_seen_at DESC NULLS LAST, id DESC` (`people.py:108`). That is
    /// deliberately not re-sorted here: a client-side sort would disagree
    /// with the dashboard the moment the server's rule changed, and the order
    /// the chips appear in is the same order.
    ///
    /// A person who is picked and then **leaves the roster** — removed on the
    /// People page — is not re-picked, which is Android's behaviour too: the
    /// pane falls to `"pick a person"` rather than silently showing somebody
    /// else's news. That is the one place this differs from Files, whose
    /// ejected library *is* replaced, and the reason is that a library is a
    /// place and a person is a person.
    private func rosterLanded() {
        guard selectedID == nil, let first = roster.first else { return }
        select(first.id)
    }

    /// Pick a person. Tearing down the previous detail is what makes the
    /// previous person's late answers unable to land (``NewsPersonModel``).
    public func select(_ id: Int64) {
        guard id != selectedID || detail == nil else { return }
        selectedID = id
        rebuildDetail()
    }

    private func rebuildDetail() {
        detail?.stop()
        guard let person = selectedPerson else {
            detail = nil
            return
        }
        let model = NewsPersonModel(person: person, in: container, say: sink(for: person.id))
        detail = model
        model.start()
    }

    /// Where a write's sentence goes: into ``toast``, **unless the person it
    /// was made for is no longer the one picked**.
    ///
    /// The captured `person` is the key, taken before any `await` the write
    /// does. Without it, a slow DELETE for Kamron's topic would toast
    /// `remove topic failed: …` over Chevy's news, naming a row that is not
    /// on screen — the same defect shape as a reply landing under the wrong
    /// server ([[read-after-await-wrong-key]]), one level up.
    private func sink(for person: Int64) -> @MainActor (String) -> Void {
        { [weak self] line in
            guard let self, self.selectedID == person else { return }
            self.toast = line
        }
    }

    // MARK: - which of five things the pane shows

    /// `NewsScreen.kt:78-93`, in the Kotlin's own order — the order is the
    /// whole specification, since a failed roster read with no data must not
    /// fall through to "nobody is enrolled".
    public enum Pane: Equatable, Sendable {
        /// The roster has never answered and is in flight (`:79`).
        case loading
        /// It has never answered and failed; the message is the server's, or
        /// `"request failed"` (`:80-81`).
        case failed(String)
        /// It answered, with nobody in it (`:82-85`).
        case noPeople
        /// Somebody is enrolled and nobody is picked (`:86-89`) — which the
        /// default selection normally prevents, and which is reachable when
        /// the picked person leaves the roster.
        case pickAPerson
        /// One person's news (`:90-92`).
        case person
    }

    public var pane: Pane {
        if people.data == nil {
            if let error = people.error { return .failed(error) }
            if people.loading { return .loading }
            // Never asked: the screen has not started yet. Treated as
            // loading, which is what a view that has just appeared should
            // draw.
            return .loading
        }
        if roster.isEmpty { return .noPeople }
        guard detail != nil else { return .pickAPerson }
        return .person
    }

    // MARK: - coming and going

    public func setToast(_ said: String?) { toast = said }

    /// The screen appeared: be keyed to the server again, and read what has
    /// not been read.
    public func start() {
        people.refresh()
        categories.refresh()
        // A detail built before the screen went away is restarted rather than
        // rebuilt, so coming back to this screen does not throw away which
        // topics were open.
        detail?.start()
    }

    /// A push arrived — handed to the roster and to the detail, each of which
    /// filters on its own event types.
    public func received(_ event: StateEvent) {
        people.received(event)
        categories.received(event)
        detail?.received(event)
    }

    /// The app moved house. Everything this screen holds is the old house's,
    /// **including the person id**, which two houses can both have — the
    /// reason the selection is cleared rather than kept.
    public func serverChanged() {
        detail?.stop()
        detail = nil
        selectedID = nil
        toast = nil
        people.serverChanged()
        categories.serverChanged()
    }

    /// The screen went away. The detail is **paused, not stopped**: it may be
    /// the same person when the screen comes back, and a stopped model never
    /// reads again.
    public func stop() {
        people.stop()
        categories.stop()
        detail?.pause()
    }
}
