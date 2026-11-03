import Foundation
import Observation

/// One person's news: their briefing, their topics and feeds, their saved
/// feed, and the six writes the screen offers.
///
/// `PersonNewsDetail` + `TopicsCard` + `SavedFeedCard`
/// (`NewsDetail.kt:78-148`, `:173-322`, `:512-599`) — three composables whose
/// state is twelve `remember`s, gathered into one model because every one of
/// the decisions in them is a decision: whether a category chip's tap is a
/// POST or a DELETE and of which id, whether the poll button may be pressed,
/// what a failed write says, and whether an answer that arrived late belongs
/// to the person on screen.
///
/// ### One model per person, and why that is the whole point
///
/// Android wraps the detail in `key(selectedPerson.id)`
/// (`NewsScreen.kt:90-92`), which **throws away every piece of state** when
/// the selection changes: three reads, the free-form draft, the open topics,
/// their feed lists and their drafts. ``NewsModel`` does the same by building
/// a new one of these, and this type makes that safe rather than incidental:
///
/// ``personID`` is a `let`, captured at init. Every request below is built
/// from it and every answer is published into *this* object's reads. There is
/// no "the currently selected person" to look up after an `await`, which is
/// the defect class that cost this track the most — a reply for person A
/// landing under person B ([[read-after-await-wrong-key]]). The test for it
/// switches person **inside** a parked `items` round trip.
///
/// ### What it does not do, and why there is no control for it
///
/// **Marking a story read.** `read_at` is drawn (`NewsDetail.kt:586`) and
/// there is no route that sets it: `news.py` has `favorite` and nothing else
/// for an item. A button that cannot do what it says is not drawn.
///
/// **Turning auto-fetch on**, which ``NewsStrings/noBriefing`` mentions. It
/// is a core setting (`domovoi/` config, not `/api/news`), so this screen
/// says where it is rather than offering a switch it cannot throw. Android
/// says the same sentence and offers the same nothing.
@MainActor
@Observable
public final class NewsPersonModel {

    /// `news.changed`: a topic, a feed, a favourite or a poll moved
    /// (`news.py` notifies `news_changed` on every mutation, mapped in
    /// `realtime.py:182` and emitted as `news.changed`). All three reads
    /// subscribe, which is what `NewsDetail.kt:84-92` does.
    public static let detailEvents: Set<String> = ["news.changed"]

    /// **Captured at init and never re-read.** See this type's doc comment.
    public let personID: Int64

    /// The person as the roster had them when this model was built — for the
    /// heading only. A later roster read that renames them is the page's
    /// business, not this model's, and Android's `key(person.id)` has exactly
    /// this property.
    public let person: NewsPerson

    public let topics: ApiRead<[NewsTopic]>
    public let items: ApiRead<[NewsItem]>
    public let briefing: ApiRead<NewsBriefing>

    /// A poll is in flight — `NewsDetail.kt:93`, which disables the button.
    public private(set) var polling = false

    /// What is typed into the free-form topic field.
    public var freeformDraft = ""

    /// A topic write is in flight — Android's `busy` (`NewsDetail.kt:185`),
    /// which gates `add topic`.
    public private(set) var addingTopic = false

    /// The topic whose removal is being confirmed, or nil
    /// (`NewsDetail.kt:186`).
    public private(set) var confirmingRemoval: NewsTopic?

    private var feedModels: [Int64: NewsFeedsModel] = [:]

    private let container: AppContainer
    private let say: @MainActor (String) -> Void
    private var stopped = false

    /// - Parameters:
    ///   - person: whose news this is. Its `id` becomes ``personID``.
    ///   - say: where a failed write's sentence goes. ``NewsModel`` installs a
    ///     sink that drops a line from a person who is no longer on screen,
    ///     so a toast cannot name somebody else's topic.
    public init(
        person: NewsPerson,
        in container: AppContainer,
        say: @MainActor @escaping (String) -> Void
    ) {
        self.person = person
        self.personID = person.id
        self.container = container
        self.say = say

        let client = container.client
        let pid = person.id
        self.topics = ApiRead(in: container, eventTypes: Self.detailEvents) {
            try await client.get(NewsPath.topics(person: pid), as: [NewsTopic].self)
        }
        self.items = ApiRead(in: container, eventTypes: Self.detailEvents) {
            try await client.get(NewsPath.items(person: pid), as: [NewsItem].self)
        }
        self.briefing = ApiRead(in: container, eventTypes: Self.detailEvents) {
            try await client.get(NewsPath.briefing(person: pid), as: NewsBriefing.self)
        }
    }

    // MARK: - what the view asks

    /// `NewsDetail.kt:121`.
    public var heading: String { NewsStrings.personsNews(person.displayName) }

    /// `NewsDetail.kt:132`.
    public var pollLabel: String { polling ? NewsStrings.polling : NewsStrings.pollNow }

    public var topicRows: [NewsTopic] { topics.data ?? [] }

    public var stories: [NewsItem] { items.data ?? [] }

    /// The topics read has answered with nothing — not while it is in flight
    /// and not while it has failed, because `"No topics yet"` over a failed
    /// read is a claim about the house this app did not measure.
    public var hasNoTopics: Bool {
        topics.data?.isEmpty == true && topics.error == nil
    }

    /// The saved feed has answered with nothing, on the same terms.
    public var hasNoStories: Bool {
        items.data?.isEmpty == true && items.error == nil
    }

    /// The category words that are on — `NewsDetail.kt:187`.
    public var activeCategories: Set<String> {
        Set(topicRows.filter(\.isCategory).compactMap(\.topic))
    }

    public func isCategoryOn(_ category: String) -> Bool {
        activeCategories.contains(category)
    }

    /// `NewsDetail.kt:268`: enabled when no topic write is in flight and
    /// there is something non-blank to add.
    public var canAddTopic: Bool {
        !addingTopic && !freeformDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The feeds model for a topic row, made on first ask and kept.
    ///
    /// Kept, so that a topic the person expanded is still expanded after a
    /// `news.changed` re-read of the topics list — and so that the feed list
    /// a lazy row scrolled past is not re-requested when it comes back.
    public func feeds(for topic: NewsTopic) -> NewsFeedsModel {
        if let existing = feedModels[topic.id] { return existing }
        let model = NewsFeedsModel(
            topicID: topic.id,
            kind: topic.kind,
            in: container,
            say: { [weak self] line in self?.say(line) },
            changed: { [weak self] in self?.topicsAndStoriesChanged() }
        )
        feedModels[topic.id] = model
        return model
    }

    // MARK: - poll now

    /// `POST /api/news/poll?person_id={pid}` — `NewsDetail.kt:95-112`.
    ///
    /// The toast counts `house + topics` from the reply and nothing else
    /// (`:101-103`); `briefed` is in the reply and Android does not say it
    /// (``NewsPollReply``). All three reads are refreshed on success, because
    /// a poll can add stories, attach feeds and write a new briefing in one
    /// pass.
    ///
    /// **409 under `INTERNET_ACCESS=never`** (`news.py:452-453`) carries the
    /// server's own sentence through ``failureText(_:_:)`` — the person who
    /// turned the internet off is the person able to act on being told so.
    public func pollNow() {
        guard !polling, !stopped else { return }
        polling = true
        let client = container.client
        let path = NewsPath.poll(person: personID)
        Task { @MainActor [weak self] in
            var line: String
            var ok = false
            do {
                let reply = try await client.post(path, as: NewsPollReply.self)
                line = reply.toastLine
                ok = true
            } catch let trouble as DomovoiError {
                line = failureText(NewsStrings.verbPoll, trouble)
            } catch {
                line = failureText(NewsStrings.verbPoll, .unreachable("\(error)"))
            }
            guard let self, !self.stopped else { return }
            self.polling = false
            if ok { self.everythingChanged() }
            self.say(line)
        }
    }

    // MARK: - topics

    /// A category chip's tap: **a toggle** — `NewsDetail.kt:189-208`.
    ///
    /// An existing `kind == "category"` topic with this word is DELETEd by
    /// **its own id**; otherwise one is POSTed. The id comes from the topics
    /// read on screen, which is the only place that knows it, and it is
    /// resolved **before** the request rather than after, so a `news.changed`
    /// arriving mid-flight cannot redirect the delete at another row.
    public func toggleCategory(_ category: String) {
        guard !stopped else { return }
        if let existing = topicRows.first(where: { $0.isCategory && $0.topic == category }) {
            write(
                method: "DELETE",
                path: NewsPath.topic(existing.id),
                body: nil,
                verb: NewsStrings.verbUpdateCategory,
                said: nil
            )
        } else {
            write(
                method: "POST",
                path: NewsPath.topics(person: personID),
                body: NewsPath.topicBody(kind: NewsTopic.categoryKind, topic: category),
                verb: NewsStrings.verbUpdateCategory,
                said: nil
            )
        }
    }

    /// `POST …/topics {kind: "freeform", topic}` — `NewsDetail.kt:210-232`.
    ///
    /// The field is cleared on success only (`:225`). A duplicate is **not**
    /// an error on this route — the server returns the existing row
    /// (`news.py:180-191`) — so adding a topic twice says it was added twice,
    /// which is what Android says too.
    public func addFreeform() {
        let topic = freeformDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !topic.isEmpty, !addingTopic, !stopped else { return }
        addingTopic = true
        write(
            method: "POST",
            path: NewsPath.topics(person: personID),
            body: NewsPath.topicBody(kind: NewsTopic.freeformKind, topic: topic),
            verb: NewsStrings.verbAddTopic,
            said: NewsStrings.topicAdded,
            clearsAdding: true
        ) { model in
            model.freeformDraft = ""
        }
    }

    /// The ✕ on a topic row: ask first — `NewsDetail.kt:282`, `:288-303`.
    ///
    /// A destructive write with a confirm whose body **names the topic**
    /// (``NewsStrings/removeTopicBody(_:)``), so a dialog that confirmed the
    /// wrong row would be visible in the dialog itself.
    public func removeRequested(_ topic: NewsTopic) {
        confirmingRemoval = topic
    }

    public func cancelRemoval() {
        confirmingRemoval = nil
    }

    /// `DELETE /api/news/topics/{tid}` — `NewsDetail.kt:294-300`.
    ///
    /// Takes the topic as an argument rather than reading
    /// ``confirmingRemoval`` inside the task: the dialog is dismissed first,
    /// and a value read after that would be nil.
    /// The topic's feeds model is dropped **only on success**, in the write's
    /// own hook. Dropping it up front broke the row a refused DELETE left
    /// behind: `stop()` is permanent, the row stays on screen (a failure
    /// re-reads nothing, so `topicRows` is unchanged),
    /// ``NewsTopicsCardView``'s `@State` dictionary still holds the stopped
    /// model — its `.task(id:)` does not re-run, the id list did not change —
    /// and the chevron's `load()` returns at `guard !stopped`. The row said
    /// `loading feeds…` for ever and add-feed, re-check and remove-feed on it
    /// were silent no-ops. Reachable with a 404 (removed from another device)
    /// or an unreachable house.
    public func confirmRemoval(_ topic: NewsTopic) {
        guard !stopped else { return }
        confirmingRemoval = nil
        let gone = topic.id
        write(
            method: "DELETE",
            path: NewsPath.topic(gone),
            body: nil,
            verb: NewsStrings.verbRemoveTopic,
            said: nil
        ) { model in
            model.feedModels.removeValue(forKey: gone)?.stop()
        }
    }

    // MARK: - the saved feed

    /// `POST /api/news/items/{id}/favorite {favorited: !favorited}` —
    /// `NewsDetail.kt:539-560`.
    ///
    /// **The body is the negation of the row's own flag**, captured before
    /// the request: a write that sent `favorited: true` unconditionally would
    /// make the star a one-way control. Only the saved feed is re-read
    /// afterwards, as Android re-reads only `items` (`:548`), because
    /// favouriting changes the order of that list and nothing else.
    public func toggleFavorite(_ item: NewsItem) {
        guard !stopped else { return }
        write(
            method: "POST",
            path: NewsPath.favorite(item: item.id),
            body: NewsPath.favoriteBody(!item.favorited),
            verb: NewsStrings.verbUpdateFavorite,
            said: nil,
            refresh: .storiesOnly
        )
    }

    /// `NewsDetail.kt:556`.
    public func favoriteLabel(_ item: NewsItem) -> String {
        item.favorited ? NewsStrings.unfavorite : NewsStrings.favorite
    }

    // MARK: - the writes, in one place

    /// Which reads a successful write invalidates.
    private enum Refresh {
        /// The topics list and the saved feed — `onChanged` in the Kotlin
        /// (`NewsDetail.kt:141-144`).
        case topicsAndStories
        /// The saved feed alone (`:548`).
        case storiesOnly
    }

    private func write(
        method: String,
        path: String,
        body: Data?,
        verb: String,
        said: String?,
        refresh: Refresh = .topicsAndStories,
        clearsAdding: Bool = false,
        onSuccess: @MainActor @escaping (NewsPersonModel) -> Void = { _ in }
    ) {
        let client = container.client
        Task { @MainActor [weak self] in
            var line: String? = said
            var ok = true
            do {
                try await client.send(method: method, path: path, body: body)
            } catch let trouble as DomovoiError {
                ok = false
                line = failureText(verb, trouble)
            } catch {
                ok = false
                line = failureText(verb, .unreachable("\(error)"))
            }
            guard let self, !self.stopped else { return }
            if clearsAdding { self.addingTopic = false }
            if ok {
                onSuccess(self)
                switch refresh {
                case .topicsAndStories: self.topicsAndStoriesChanged()
                case .storiesOnly: self.items.refresh()
                }
            }
            if let line { self.say(line) }
        }
    }

    /// `onChanged` (`NewsDetail.kt:141-144`): a topic or a feed moved, so the
    /// topics list and the saved feed are both out of date. The briefing is
    /// not — only a poll writes one.
    private func topicsAndStoriesChanged() {
        topics.refresh()
        items.refresh()
    }

    /// A poll touches all three (`NewsDetail.kt:105-107`).
    private func everythingChanged() {
        topics.refresh()
        items.refresh()
        briefing.refresh()
    }

    // MARK: - coming and going

    public func start() {
        guard !stopped else { return }
        topics.refresh()
        items.refresh()
        briefing.refresh()
        // An expanded topic whose feeds read was in flight when the screen
        // went away lost that read to ``NewsFeedsModel/pause()`` — a
        // cancelled read leaves `feeds` nil and `readError` nil, which is
        // exactly `showing == .loading`. `isOpen` survived the pause, so
        // without this the row showed `loading feeds…` until the person
        // happened to collapse and expand it again. ``resume()`` is the one
        // that keeps a row which genuinely *failed*, and its retry.
        for model in feedModels.values { model.resume() }
    }

    /// A push arrived. Handed to all three reads, each of which filters on
    /// ``detailEvents`` itself.
    public func received(_ event: StateEvent) {
        topics.received(event)
        items.received(event)
        briefing.received(event)
    }

    /// The screen went away, and this person may still be picked when it
    /// comes back.
    ///
    /// The three reads are stopped — they leave the server-key registry and
    /// drop what is in flight — and nothing else is touched, so the free-form
    /// draft and which topics were expanded survive a tab switch.
    /// ``start()`` re-reads and re-joins. **Not** ``stop()``, which is
    /// permanent: a paused model that had been stopped would never read
    /// again, and the screen would come back blank.
    public func pause() {
        topics.stop()
        items.stop()
        briefing.stop()
        for model in feedModels.values { model.pause() }
    }

    /// This person is no longer on screen — another was picked, or the model
    /// is being thrown away.
    ///
    /// Everything in flight is cancelled and every pending write becomes a
    /// no-op, which is what makes "the previous person's late answer is
    /// dropped" true rather than hoped for.
    public func stop() {
        stopped = true
        polling = false
        addingTopic = false
        confirmingRemoval = nil
        topics.stop()
        items.stop()
        briefing.stop()
        for model in feedModels.values { model.stop() }
        feedModels.removeAll()
    }
}
