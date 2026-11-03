import Foundation
import Observation

/// One topic's feed list: whether it is open, what is in it, the URL being
/// typed into it, and the three writes a feed row offers.
///
/// `TopicRow`'s eight `remember(topic.id)`s and its `LaunchedEffect(open)`
/// (`NewsDetail.kt:326-468`) — **a model, and not state inside the row**, for
/// the standing rule that a lazy row never holds a `Task`. A row in a
/// `LazyVStack` is built and thrown away as it scrolls, and three of the four
/// things that state is for (a feeds read in flight, an add-feed POST, a
/// validate) outlive the row that started them. Android gets away with it
/// because its topic rows are a plain `Column` that composes all of them at
/// once; iOS does not, and would otherwise cancel a write by scrolling.
///
/// One of these per topic, made and kept by ``NewsPersonModel``, so a feed
/// list that was open before the person scrolled past it is still open when
/// it comes back — which is also Android's behaviour, since `remember` is
/// keyed on `topic.id` rather than on the row.
///
/// ### What this does that Android does not
///
/// **A feeds read that fails says so.** Android's `loadFeeds` is
/// `runCatching { … }.getOrDefault(emptyList())` (`NewsDetail.kt:335-339`),
/// so a failed request is drawn as `"No feeds yet."` — a sentence about the
/// house that the app did not measure. Here the failure is kept in ``error``
/// and the view offers a retry; ``NewsStrings/noFeeds`` is for a read that
/// answered with nothing. DECISIONS.md records the call.
@MainActor
@Observable
public final class NewsFeedsModel {

    /// The topic these feeds belong to, **captured once at init** and never
    /// read from anywhere else.
    ///
    /// This is the read-after-await discipline in its smallest form: every
    /// path below sends `topicID` and publishes under `topicID`, so a feeds
    /// answer cannot land under the topic the person has since expanded.
    public let topicID: Int64

    /// The topic's `kind`, for the empty wording only: a free-form topic with
    /// no feeds says discovery found none, a category says nothing of the
    /// sort (`NewsDetail.kt:412-421`).
    public let kind: String?

    public private(set) var isOpen = false

    /// Nil while the feeds have never been read for this topic — which is
    /// what `feeds == null` means in the Kotlin, and what draws
    /// ``NewsStrings/loadingFeeds``.
    public private(set) var feeds: [NewsFeed]?

    /// The last read's failure, cleared by the next success. See this type's
    /// doc comment for why it exists at all.
    public private(set) var readError: String?

    /// What is typed into the add-feed field.
    public var draftURL = ""

    /// A write is in flight — Android's single `busy` (`NewsDetail.kt:333`),
    /// which gates the add-feed button.
    public private(set) var busy = false

    private let container: AppContainer

    /// Say something to the person. ``NewsPersonModel`` installs this; it
    /// drops a line whose topic is no longer on screen.
    private let say: @MainActor (String) -> Void

    /// "the topics read and the saved feed are now out of date" — adding or
    /// removing a feed changes `feed_count`, which is the number the topic
    /// row shows (`NewsDetail.kt:360`, `:441`).
    private let changed: @MainActor () -> Void

    private var loading: Task<Void, Never>?
    private var stopped = false

    public init(
        topicID: Int64,
        kind: String?,
        in container: AppContainer,
        say: @MainActor @escaping (String) -> Void,
        changed: @MainActor @escaping () -> Void
    ) {
        self.topicID = topicID
        self.kind = kind
        self.container = container
        self.say = say
        self.changed = changed
    }

    // MARK: - what the view asks

    /// Which of the four things the open list shows: a read in flight, a
    /// failure, one of the two empty lines, or rows.
    public enum Showing: Equatable, Sendable {
        case loading
        case failed(String)
        case empty(String)
        case rows([NewsFeed])
    }

    /// `NewsDetail.kt:405-421`, with the failed case added.
    ///
    /// The order matters and is the Kotlin's: `feeds == null` first, so a
    /// re-read of a list that has rows keeps showing them rather than
    /// flickering to "loading feeds…".
    public var showing: Showing {
        guard let feeds else {
            if let readError { return .failed(readError) }
            return .loading
        }
        guard !feeds.isEmpty else {
            return .empty(
                kind == NewsTopic.freeformKind
                    ? NewsStrings.noFeedsFreeform
                    : NewsStrings.noFeeds
            )
        }
        return .rows(feeds)
    }

    /// `NewsDetail.kt:379`.
    public var chevronLabel: String {
        isOpen ? NewsStrings.collapse : NewsStrings.expand
    }

    /// Android enables `add` on `!busy` alone (`NewsDetail.kt:463`) and then
    /// returns early from `addFeed` on a blank URL (`:347`) — a button that
    /// is offered and does nothing.
    ///
    /// Here it is gated on a non-blank URL as well, which is what the
    /// free-form topic field next to it already does (`:268`). A control that
    /// cannot do what it says is not drawn, and a blank URL is the one case
    /// where Android's own code says the tap is a no-op.
    public var canAddFeed: Bool {
        !busy && !draftURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: - opening

    /// The chevron. Opening reads the feeds **only the first time**
    /// (`NewsDetail.kt:341-343`: `LaunchedEffect(open) { if (open && feeds ==
    /// null) loadFeeds() }`), so collapsing and expanding again costs no
    /// request.
    public func toggle() {
        isOpen.toggle()
        guard isOpen else { return }
        if feeds == nil { load() }
    }

    /// Read this topic's feeds. Cancels a read already in flight, so two taps
    /// in quick succession mean one request.
    public func load() {
        guard !stopped else { return }
        loading?.cancel()
        let client = container.client
        let topic = topicID
        loading = Task { @MainActor [weak self] in
            do {
                let rows = try await client.get(
                    NewsPath.feeds(topic: topic),
                    as: [NewsFeed].self
                )
                guard !Task.isCancelled, let self, !self.stopped else { return }
                self.feeds = rows
                self.readError = nil
            } catch is CancellationError {
                return
            } catch let trouble as DomovoiError {
                guard !Task.isCancelled, let self, !self.stopped else { return }
                // The message is kept rather than toasted: it belongs inside
                // the topic row that failed, where the retry is. A toast would
                // name no topic.
                self.readError = failureText(NewsStrings.verbLoadFeeds, trouble)
            } catch {
                guard !Task.isCancelled, let self, !self.stopped else { return }
                self.readError = failureText(NewsStrings.verbLoadFeeds, .unreachable("\(error)"))
            }
        }
    }

    /// A retry after ``readError``, which clears the failure first so the
    /// view goes back to "loading feeds…" rather than showing the old message
    /// under a spinner.
    public func retry() {
        readError = nil
        load()
    }

    // MARK: - the three writes

    /// `POST /api/news/topics/{tid}/feeds {url}` — `NewsDetail.kt:345-365`.
    ///
    /// The field is cleared **only on success**, as the Kotlin clears it
    /// (`:357`): a URL the server refused is a URL the person wants to
    /// correct, not retype.
    public func addFeed() {
        let url = draftURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !url.isEmpty, !busy, !stopped else { return }
        busy = true
        write(
            method: "POST",
            path: NewsPath.feeds(topic: topicID),
            body: NewsPath.feedBody(url: url),
            verb: NewsStrings.verbAddFeed,
            said: NewsStrings.feedAdded,
            clearsBusy: true
        ) { model in
            model.draftURL = ""
        }
    }

    /// `POST /api/news/feeds/{fid}/validate` — `NewsDetail.kt:425-433`.
    ///
    /// **409 under `INTERNET_ACCESS=never`** (`news.py:333-334`): the server's
    /// own sentence carries through ``failureText(_:_:)``, which is the whole
    /// reason this control's failure is not a generic "failed".
    ///
    /// The topics read is **not** refreshed afterwards, because Android does
    /// not refresh it (`:430` reloads the feeds alone): validating changes a
    /// feed's `valid`, not the count.
    public func validate(_ feed: NewsFeed) {
        guard !stopped else { return }
        write(
            method: "POST",
            path: NewsPath.validate(feed: feed.id),
            body: nil,
            verb: NewsStrings.verbValidate,
            said: nil,
            alsoChanged: false
        )
    }

    /// `DELETE /api/news/topics/{tid}/feeds/{fid}` — `NewsDetail.kt:434-445`.
    ///
    /// The topic path is **this model's** `topicID`, not a value read from
    /// anywhere else, so a removal cannot be sent against a different topic.
    public func remove(_ feed: NewsFeed) {
        guard !stopped else { return }
        write(
            method: "DELETE",
            path: NewsPath.feed(topic: topicID, feed: feed.id),
            body: nil,
            verb: NewsStrings.verbRemoveFeed,
            said: nil
        )
    }

    /// One write, its refusal worded, the feeds read again, and the topics
    /// read told that the count moved.
    ///
    /// `busy` is cleared **by the write that set it** — Android's `busy =
    /// false` after the `runCatching` (`NewsDetail.kt:363`) — and by no
    /// other, so a validate landing mid-add does not re-enable the add
    /// button under the person's finger. A failed add clears it too, or the
    /// button would stay disabled for good.
    private func write(
        method: String,
        path: String,
        body: Data?,
        verb: String,
        said: String?,
        alsoChanged: Bool = true,
        clearsBusy: Bool = false,
        onSuccess: @MainActor @escaping (NewsFeedsModel) -> Void = { _ in }
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
            // Published under this model, whose `topicID` was fixed at init:
            // there is no current-topic lookup for a late answer to land
            // against.
            guard let self, !self.stopped else { return }
            if clearsBusy { self.busy = false }
            if ok {
                onSuccess(self)
                self.load()
                if alsoChanged { self.changed() }
            }
            if let line { self.say(line) }
        }
    }

    /// The screen went away, and may come back. Drops the read in flight and
    /// nothing else: ``isOpen``, ``feeds`` and ``draftURL`` are what the
    /// person left on screen.
    public func pause() {
        loading?.cancel()
        loading = nil
    }

    /// The screen came back (``NewsPersonModel/start()``). Reads again only
    /// if this list is open and has **nothing to show and nothing to say** —
    /// a cancelled read leaves both nil, which the view draws as
    /// `loading feeds…` for ever. A list that answered keeps its rows and
    /// costs no request; a list that failed keeps its message and its
    /// ``retry()``, which is the person's own decision to make.
    public func resume() {
        guard !stopped, isOpen, feeds == nil, readError == nil else { return }
        load()
    }

    /// The person went away, or the topic did. Stops the read in flight and
    /// makes every pending write a no-op on the way back — **permanent**, so
    /// a model whose topic was removed can never publish again.
    public func stop() {
        stopped = true
        pause()
        busy = false
    }
}
