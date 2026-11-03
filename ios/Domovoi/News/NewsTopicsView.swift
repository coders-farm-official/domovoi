import DomovoiKit
import SwiftUI

/// The topic manager: the nine category chips, the free-form field, and one
/// row per topic with its feeds under it.
///
/// `TopicsCard` and `TopicRow` (`NewsDetail.kt:173-322`, `:326-468`).
///
/// ### Where the feed models come from, and why not from `body`
///
/// ``NewsPersonModel/feeds(for:)`` makes a model on first ask **and keeps
/// it** — which means it writes to the person model. Calling it from `body`
/// would read and then write a property of an `@Observable` object while the
/// view was being computed: the read registers a dependency, the write
/// invalidates it, and the row redraws itself for ever. So the models are
/// collected in a `.task(id:)`, which runs *after* the update, keyed on the
/// list of topic ids — so a topic that arrives from a `news.changed` re-read
/// gets one, and a topic that was removed has its model dropped rather than
/// kept for the life of the screen.
struct NewsTopicsCardView: View {

    let detail: NewsPersonModel
    let categories: [String]

    /// One ``NewsFeedsModel`` per topic row, taken from the person model so
    /// that a feed list which was open before a re-read is still open after
    /// it. The view holds the references; the person model owns them.
    @State private var feedModels: [Int64: NewsFeedsModel] = [:]

    var body: some View {
        HomeCard(title: NewsStrings.topicsLabel) {
            VStack(alignment: .leading, spacing: 12) {
                Text(NewsStrings.topicsSub)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                chips
                freeform
                rows
            }
        }
        .task(id: detail.topicRows.map(\.id)) {
            for topic in detail.topicRows where feedModels[topic.id] == nil {
                feedModels[topic.id] = detail.feeds(for: topic)
            }
            // A topic that has gone takes its model with it. Without this the
            // dictionary is a list of every topic the screen ever saw, and a
            // model for a removed topic would answer a feeds read for a row
            // that is not on screen.
            let live = Set(detail.topicRows.map(\.id))
            if feedModels.count != live.count {
                feedModels = feedModels.filter { live.contains($0.key) }
            }
        }
    }

    // MARK: - the nine categories

    /// `FlowRow` of the nine keys (`NewsDetail.kt:243-250`).
    ///
    /// SwiftUI has no flow layout, and the substitute is a `LazyVGrid` with
    /// **adaptive** columns whose minimum is a ``ScaledMetric``: at the
    /// ordinary size that is three or four chips to a row, and at an
    /// accessibility size it becomes one, which is what a flow row would do
    /// with chips whose text is 48 points tall. A fixed column count would
    /// clip the long words (`entertainment`) at the larger sizes.
    private var chips: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: chipMinimum), spacing: 8)], spacing: 8) {
            ForEach(categories, id: \.self) { word in
                NewsCategoryChip(word: word, on: detail.isCategoryOn(word)) {
                    detail.toggleCategory(word)
                }
            }
        }
    }

    @ScaledMetric(relativeTo: .body) private var chipMinimum: CGFloat = 104

    // MARK: - the free-form field

    /// `NewsDetail.kt:255-271`: a single-line field and an `add topic`
    /// button, both gated on something non-blank being typed.
    ///
    /// **`submitLabel(.done)` is allowed here**, where the standing rule
    /// forbids it: the rule is about a `TextField(axis: .vertical)`, whose
    /// Return key makes a newline and whose `onSubmit` never fires. This
    /// field is single-line, which is Android's `ImeAction.Done`
    /// (`:264`), and the key does what it says.
    private var freeform: some View {
        HStack(spacing: 8) {
            TextField(
                NewsStrings.freeformPlaceholder,
                text: Binding(
                    get: { detail.freeformDraft },
                    set: { detail.freeformDraft = $0 }
                )
            )
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .submitLabel(.done)
            .onSubmit { detail.addFreeform() }
            .accessibilityLabel(NewsStrings.freeformPlaceholder)
            .accessibilityIdentifier("news-freeform")

            Button(NewsStrings.addTopic) { detail.addFreeform() }
                .buttonStyle(.bordered)
                .tint(Color.brandInk)
                .disabled(!detail.canAddTopic)
                .frame(minHeight: 44)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("news-add-topic")
        }
    }

    // MARK: - the rows

    @ViewBuilder
    private var rows: some View {
        if detail.hasNoTopics {
            Text(NewsStrings.noTopics)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        if detail.topics.data == nil, let why = detail.topics.error {
            // A topics read that has **never** landed and failed says so.
            // ``NewsPersonModel/hasNoTopics`` is false in that case for the
            // same reason: "No topics yet" over a failed read is a claim
            // about the house this app did not measure.
            Text(why)
                .font(.footnote)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
        }
        ForEach(detail.topicRows) { topic in
            // Drawn only once its feeds model exists — one frame later, from
            // the `.task` above. A chevron with nothing behind it is a
            // control that cannot do what it says, even for a frame.
            if let feeds = feedModels[topic.id] {
                NewsTopicRow(topic: topic, feeds: feeds, detail: detail)
                Divider()
            }
        }
    }
}

/// One category chip — on is the brand fill, off is the card fill with a
/// border (`NewsDetail.kt:306-322`).
///
/// The tap is a **toggle** and the model decides which way
/// (``NewsPersonModel/toggleCategory(_:)``): an existing category topic with
/// this word is deleted by its own id, otherwise one is posted.
struct NewsCategoryChip: View {

    let word: String
    let on: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(word)
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .frame(maxWidth: .infinity, minHeight: 44)
                // The measured pair on the fill, `fgMuted` on the card — and
                // never ``Color/brand`` as text, which is 2.05:1.
                .foregroundStyle(on ? Color.brandFg : Color.fgMuted)
                .background(
                    on ? Color.brand : Color(.secondarySystemBackground),
                    in: Capsule()
                )
                .overlay(
                    Capsule().stroke(
                        on ? Color.brandInk : Color.fgMuted.opacity(0.35),
                        lineWidth: 1
                    )
                )
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("news-category-\(word)")
        .accessibilityAddTraits(on ? [.isButton, .isSelected] : [.isButton])
    }
}

/// One topic: the chevron, the kind pill, the word, its feed count, and the
/// ✕ that asks before removing it (`NewsDetail.kt:371-400`).
///
/// **Five things on one line**, which is why the row becomes a column at an
/// accessibility size: `entertainment` beside `12 feeds` beside two icon
/// buttons has nowhere to go at 48-point text but into one word a line.
struct NewsTopicRow: View {

    let topic: NewsTopic
    let feeds: NewsFeedsModel
    let detail: NewsPersonModel

    @Environment(\.dynamicTypeSize) private var typeSize

    /// The chevron's box and the ✕'s, scaled — never a fixed
    /// `frame(width:)`, which is the clipped-glyph bug the Dynamic Type rule
    /// is about.
    @ScaledMetric(relativeTo: .body) private var glyph: CGFloat = 22

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        chevron
                        HomeTonePill(label: topic.kindLabel, tone: topic.isCategory ? .brand : .idle)
                        Spacer(minLength: 4)
                        remove
                    }
                    word
                    count
                }
            } else {
                HStack(spacing: 8) {
                    chevron
                    HomeTonePill(label: topic.kindLabel, tone: topic.isCategory ? .brand : .idle)
                    word
                    Spacer(minLength: 4)
                    count
                    remove
                }
            }
            if feeds.isOpen {
                NewsFeedsListView(feeds: feeds)
                    .padding(.leading, 8)
            }
        }
    }

    private var chevron: some View {
        Button {
            feeds.toggle()
        } label: {
            Image(systemName: feeds.isOpen ? "chevron.down" : "chevron.right")
                .font(.footnote)
                .frame(width: glyph, height: glyph)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        // Android's own `contentDescription` (`NewsDetail.kt:379`), which is
        // the same word on every row — so the **value** is what says which
        // row, and the identifier is what a test taps.
        .accessibilityLabel(feeds.chevronLabel)
        .accessibilityValue(topic.topicLabel)
        .accessibilityIdentifier("news-expand-\(topic.topicLabel)")
    }

    private var word: some View {
        Text(topic.topicLabel)
            .font(.subheadline)
            // Android ellipsises at one line (`:386-390`). Here it wraps:
            // `minimumScaleFactor` is rejected throughout this app and a
            // free-form topic is a phrase a person typed, which is worth
            // reading in full more than it is worth one line.
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
            .accessibilityIdentifier("news-topic-word-\(topic.topicLabel)")
    }

    /// `"2 feeds"` — **the count from the topics read**, never the length of
    /// the lazily-loaded feeds list (`NewsDetail.kt:393`). The two disagree
    /// while a list is open and a feed has just been added, and the server is
    /// the one that counted.
    private var count: some View {
        Text(topic.feedCountLine)
            .font(.caption.monospaced())
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier("news-topic-count-\(topic.topicLabel)")
    }

    private var remove: some View {
        Button {
            detail.removeRequested(topic)
        } label: {
            Image(systemName: "xmark")
                .font(.footnote)
                .frame(width: glyph, height: glyph)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.errInk)
        .accessibilityLabel(NewsStrings.removeTopic)
        .accessibilityValue(topic.topicLabel)
        .accessibilityIdentifier("news-remove-topic-\(topic.topicLabel)")
    }
}

/// One topic's feeds, once it has been expanded (`NewsDetail.kt:404-466`).
///
/// Four states and not Android's three: a read in flight, **a read that
/// failed**, one of the two empty lines, or the rows. Android's `loadFeeds`
/// is `runCatching { … }.getOrDefault(emptyList())` (`:335-339`), so over
/// there a feeds request that failed is drawn as `"No feeds yet."` — a
/// sentence about the house that the app did not measure. DECISIONS.md, "A
/// feeds read that fails will say so".
struct NewsFeedsListView: View {

    let feeds: NewsFeedsModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            switch feeds.showing {
            case .loading:
                HStack(spacing: 8) {
                    ProgressView()
                    Text(NewsStrings.loadingFeeds)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            case .failed(let why):
                VStack(alignment: .leading, spacing: 4) {
                    Text(NewsStrings.feedsUnread)
                        .font(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(why)
                        .font(.caption)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                    HomeLink(NewsStrings.feedsUnreadRetry) { feeds.retry() }
                        .accessibilityIdentifier("news-feeds-retry-\(feeds.topicID)")
                }
            case .empty(let line):
                Text(line)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            case .rows(let rows):
                ForEach(rows) { row in
                    NewsFeedRowView(feed: row, feeds: feeds)
                }
            }
            adder
        }
    }

    /// `NewsDetail.kt:452-464`: a URL field and `add`.
    ///
    /// **`add` is gated on a non-blank URL**, where Android gates it on
    /// `!busy` alone and then returns early from `addFeed` on a blank one
    /// (`:347`) — a button that is offered and does nothing. DECISIONS.md.
    private var adder: some View {
        HStack(spacing: 8) {
            TextField(
                NewsStrings.feedURLPlaceholder,
                text: Binding(
                    get: { feeds.draftURL },
                    set: { feeds.draftURL = $0 }
                )
            )
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .keyboardType(.URL)
            .submitLabel(.done)
            .onSubmit { feeds.addFeed() }
            .accessibilityLabel(NewsStrings.feedURLPlaceholder)
            .accessibilityIdentifier("news-feed-url-\(feeds.topicID)")

            Button(NewsStrings.addFeed) { feeds.addFeed() }
                .buttonStyle(.bordered)
                .tint(Color.brandInk)
                .disabled(!feeds.canAddFeed)
                .frame(minHeight: 44)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("news-add-feed-\(feeds.topicID)")
        }
    }
}

/// One feed: whether it is valid, what it is called, where it came from, and
/// the two things that can be done to it (`NewsDetail.kt:471-506`).
struct NewsFeedRowView: View {

    let feed: NewsFeed
    let feeds: NewsFeedsModel

    @Environment(\.dynamicTypeSize) private var typeSize

    @ScaledMetric(relativeTo: .body) private var glyph: CGFloat = 20

    var body: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 6) {
                titleLine
                meta
                HStack(spacing: 8) {
                    recheck
                    remove
                    Spacer(minLength: 4)
                }
            }
        } else {
            HStack(alignment: .top, spacing: 8) {
                VStack(alignment: .leading, spacing: 4) {
                    titleLine
                    meta
                }
                Spacer(minLength: 4)
                recheck
                remove
            }
        }
    }

    private var titleLine: some View {
        HStack(spacing: 6) {
            // `StatusDot(ok, live = true)` for a valid feed, idle otherwise
            // (`:477`) — **still**, not pulsing: nothing in this app animates
            // yet, which ``ToneDot/live`` already records. The dot is never
            // the only carrier: `valid` is also what the re-check changes and
            // the meta line names the source.
            ToneDot(tone: feed.valid ? .ok : .idle)
            Text(feed.displayTitle)
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
                .accessibilityIdentifier("news-feed-title-\(feed.id)")
        }
    }

    private var meta: some View {
        Text(feed.metaLine)
            .font(.caption2.monospaced())
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
            .accessibilityIdentifier("news-feed-meta-\(feed.id)")
    }

    private var recheck: some View {
        Button {
            feeds.validate(feed)
        } label: {
            Image(systemName: "arrow.clockwise")
                .font(.caption)
                .frame(width: glyph, height: glyph)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .accessibilityLabel(NewsStrings.recheckValidity)
        .accessibilityValue(feed.displayTitle)
        .accessibilityIdentifier("news-validate-\(feed.id)")
    }

    private var remove: some View {
        Button {
            feeds.remove(feed)
        } label: {
            Image(systemName: "xmark")
                .font(.caption)
                .frame(width: glyph, height: glyph)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.errInk)
        .accessibilityLabel(NewsStrings.removeFeed)
        .accessibilityValue(feed.displayTitle)
        .accessibilityIdentifier("news-remove-feed-\(feed.id)")
    }
}
