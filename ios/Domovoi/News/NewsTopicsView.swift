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
    /// ### Rows, not a `LazyVGrid` — and the grid was the finding
    ///
    /// This was `LazyVGrid(columns: [.adaptive(minimum:)])`, and train S002
    /// reported **nine** `Dynamic Type font sizes are partially unsupported`,
    /// one per chip, each naming the chip and its 104 × 44 cell.
    /// ``HomeEverythingView`` has already paid for that lesson and measured
    /// it to the end (its note, and the six findings it cost Home): the same
    /// `Text` with the same `.caption` audits clean once it is out of a lazy
    /// container's cell, and none of height, label, column width or traits
    /// changes it. Nine chips with no fetch behind any of them never earned
    /// the laziness.
    ///
    /// Three to a row, and **one at the accessibility sizes**: 104 points of
    /// chip at 48-point text is one syllable, and `minimumScaleFactor` is
    /// rejected throughout this app. A short last row is padded with clear
    /// chips so that two chips and three chips are the same width.
    private var chips: some View {
        VStack(spacing: 8) {
            ForEach(chipRows, id: \.self) { row in
                HStack(spacing: 8) {
                    ForEach(row, id: \.self) { word in
                        NewsCategoryChip(word: word, on: detail.isCategoryOn(word)) {
                            detail.toggleCategory(word)
                        }
                    }
                    ForEach(Array(0 ..< max(0, chipsPerRow - row.count)), id: \.self) { _ in
                        Color.clear.frame(maxWidth: .infinity, minHeight: 44)
                    }
                }
            }
        }
    }

    private var chipsPerRow: Int { typeSize.isAccessibilitySize ? 1 : 3 }

    /// ``categories`` in rows of ``chipsPerRow``.
    private var chipRows: [[String]] {
        stride(from: 0, to: categories.count, by: chipsPerRow).map { start in
            Array(categories[start ..< min(start + chipsPerRow, categories.count)])
        }
    }

    @Environment(\.dynamicTypeSize) private var typeSize

    // MARK: - the free-form field

    /// `NewsDetail.kt:255-271`: a single-line field and an `add topic`
    /// button, both gated on something non-blank being typed.
    ///
    /// **`submitLabel(.done)` is allowed here**, where the standing rule
    /// forbids it: the rule is about a `TextField(axis: .vertical)`, whose
    /// Return key makes a newline and whose `onSubmit` never fires. This
    /// field is single-line, which is Android's `ImeAction.Done`
    /// (`:264`), and the key does what it says.
    ///
    /// ### `add topic` is drawn when there is something to add
    ///
    /// Android keeps the button in place and disables it (`:268`). Drawn
    /// disabled it is a dimmed label on a dimmed fill, which is what train
    /// S002's `Contrast failed: news-add-topic` is — and a control that
    /// cannot do what it says is not drawn here (Kamron's rule). Appearing
    /// with the first character is also what gives the field its full width
    /// back: the same run reported the placeholder clipped at 225 points
    /// beside the button. **329 points was not enough either** — train S005
    /// reported `Text clipped` against the full-width field, and that is what
    /// shortened the placeholder itself (the field below). The button still
    /// appears only once there is something to add, for the contrast reason
    /// above. DECISIONS.md.
    private var freeform: some View {
        HStack(spacing: 8) {
            TextField(
                // **Short on screen, whole to a reader.** Android's sentence
                // is 39 characters and does not fit this field at the
                // ordinary type size: train S005's audit reported
                // `Text clipped` against it at its full 329-point width, and
                // the run's screenshot shows the placeholder cut off inside
                // `Formula 1` — the example is the part that was lost.
                // ``NewsStrings/freeformPlaceholderShort`` keeps the example
                // and drops the words the `add topic` button beside it
                // already says; the label below is still Android's whole
                // sentence. DECISIONS.md, "The hint did not fit the field".
                NewsStrings.freeformPlaceholderShort,
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

            if detail.canAddTopic {
                // The poll button's style: ``Color/brandFg`` on
                // ``Color/brand``, 8.50:1, both drawn by the style.
                Button(NewsStrings.addTopic) { detail.addFreeform() }
                    .buttonStyle(BrandFillButtonStyle())
                    .frame(minHeight: 44)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("news-add-topic")
            }
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
                // The measured pair on the fill, and `brandInk` — not
                // `fgMuted` — on the unselected chip: `fgMuted` was measured
                // against the **white canvas**, and this chip's fill is
                // `secondarySystemBackground`, where it is what Apple's audit
                // fails. ``HomeEverythingView``'s tile draws `brandInk` on
                // that same fill and audits clean. Never ``Color/brand`` as
                // text, which is 2.05:1.
                .foregroundStyle(on ? Color.brandFg : Color.brandInk)
                .background(
                    on ? Color.brand : Color(.secondarySystemBackground),
                    in: Capsule()
                )
                // **The outline is the chip's only shape, and it was under
                // the bar.** Measured off train R001's own foot screenshot
                // (`overnight-trains/R001/News/att/testNews_PassesApplesAccessibilityAudit/news-12-audited-foot…png`,
                // 3x), an unpicked chip's fill is `secondarySystemBackground`
                // at 242,242,247 and the card under it is 241,241,242 —
                // **1.01:1**. The fill draws nothing. The ring is the only
                // thing saying where the tap target is and which chips are
                // on, which makes it a graphic carrying meaning, and Apple's
                // bar for one of those is 3:1:
                //
                //     unpicked ring  fgMuted@0.35 (188,187,188)  1.71:1 light, 1.98:1 dark
                //     unpicked ring  fgMuted@0.70                3.34:1 light, 4.05:1 dark
                //     picked ring    brandInk on brand           2.76:1
                //     picked ring    brandFg  on brand           8.49:1
                //
                // So the picked ring takes `brandFg`, which is already the
                // measured pair for this fill and already the colour of the
                // word inside it, and the unpicked one goes to 0.7. The two
                // **fills** stay as they are: there is no token that puts
                // 3:1 between a quiet chip and a near-white card without
                // making the chip a block of colour, and Android's unpicked
                // chip is a quiet surface with a border too
                // (`NewsDetail.kt:306-322`).
                //
                // This is **not** the ten `Contrast failed` train R001
                // reported against these nine chips and the `saved feed`
                // heading: those were measured at 5.07:1 and 18.60:1 off that
                // same screenshot and are the integration build's app-wide
                // audit sweep (`integration/news.md`, round 2). This is a
                // defect that diagnosis turned up on its way past.
                .overlay(
                    Capsule().stroke(
                        on ? Color.brandFg : Color.fgMuted.opacity(0.7),
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
                        kindPill
                        Spacer(minLength: 4)
                        remove
                    }
                    word
                    count
                }
            } else {
                HStack(spacing: 8) {
                    chevron
                    kindPill
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

    /// Identified **per row** by its topic's word, so a case can read back
    /// what kind the app posted for one named topic. Without the identifier
    /// the only way to ask was "is there a `freeform` label anywhere on the
    /// screen", which the seeded row already answered — the assertion that
    /// named the behaviour was testing nothing.
    private var kindPill: some View {
        HomeTonePill(label: topic.kindLabel, tone: topic.isCategory ? .brand : .idle)
            .accessibilityIdentifier("news-topic-kind-\(topic.topicLabel)")
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

            // Drawn when it can act, and in the page's one action pair —
            // ``freeform``'s note says why for both.
            if feeds.canAddFeed {
                Button(NewsStrings.addFeed) { feeds.addFeed() }
                    .buttonStyle(BrandFillButtonStyle())
                    .frame(minHeight: 44)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("news-add-feed-\(feeds.topicID)")
            }
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
            NewsFeedStateDot(feed: feed)
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

/// Whether a feed is valid, as a dot **with a word in it**.
///
/// `StatusDot(ok, live = true)` / `StatusDot(idle)` — `NewsDetail.kt:477`,
/// with two differences, both of them decisions:
///
/// 1. **It is not pulsing.** Nothing in this app animates yet, and
///    ``ToneDot/live`` already records that call.
/// 2. **It is not hidden from VoiceOver**, which every other dot in this app
///    is — because every other dot has a word beside it saying what it grades
///    (`HomeCardView.swift:150-155`) and this one does not. A feed whose only
///    sign of being broken is the hue of an 8-point circle is a tone carrying
///    meaning on its own, which is the one thing the contrast rule here
///    forbids. So the circle carries ``NewsStrings/feedValid`` /
///    ``NewsStrings/feedInvalid`` as its label. Android says nothing at all.
///
/// The fill is ``HealthTone/inkColour`` and **not** ``HealthTone/dotColour``:
/// the dot colours are `.green` and `.orange` at 2.22:1 and 2.20:1, which is
/// under the 3:1 a meaningful graphic needs, and the only reason the rest of
/// the app can keep them is that those dots are hidden and decorative. This
/// one is an element a person is meant to read, so it takes the measured ink.
struct NewsFeedStateDot: View {

    let feed: NewsFeed

    @ScaledMetric(relativeTo: .body) private var size: CGFloat = 8

    var body: some View {
        Circle()
            .fill((feed.valid ? HealthTone.ok : HealthTone.idle).inkColour)
            .frame(width: size, height: size)
            // A shape has no baseline, so `firstTextBaseline` cannot lift it
            // to the cap height of the title beside it — ``ToneDot``'s own
            // offset, for the same reason.
            .offset(y: -1)
            .accessibilityLabel(feed.valid ? NewsStrings.feedValid : NewsStrings.feedInvalid)
            .accessibilityIdentifier("news-feed-state-\(feed.id)")
    }
}
