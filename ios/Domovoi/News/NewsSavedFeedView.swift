import DomovoiKit
import SwiftUI

/// The saved feed: the stories this house has fetched for one person, newest
/// first, favourites pinned.
///
/// `SavedFeedCard` (`NewsDetail.kt:512-599`). **The order is the server's** —
/// `favorited DESC, published_at DESC NULLS LAST, id DESC` (`news.py:386`) —
/// and nothing here re-sorts it, which is why favouriting a story visibly
/// moves it: the write is followed by a re-read of that one list.
struct NewsSavedFeedView: View {

    let detail: NewsPersonModel
    let nowMs: Int64

    var body: some View {
        HomeCard(title: NewsStrings.savedFeedLabel) {
            VStack(alignment: .leading, spacing: 12) {
                Text(NewsStrings.savedFeedSub)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if detail.hasNoStories {
                    note(NewsStrings.noStories, NewsStrings.noStoriesSub)
                }
                if detail.items.data == nil, let why = detail.items.error {
                    // A saved feed that has **never** landed and failed. The
                    // empty wording is for a read that answered with nothing
                    // (``NewsPersonModel/hasNoStories``), and the two must not
                    // be shown as one thing.
                    Text(why)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("news-stories-error")
                }
                ForEach(detail.stories) { story in
                    NewsStoryRow(story: story, detail: detail, nowMs: nowMs)
                    // `HorizontalDivider()` after every story (`:596`),
                    // including the last — the Kotlin's own shape.
                    Divider()
                }
            }
        }
    }

    private func note(_ title: String, _ sub: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(sub)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// One story: the star, the title, the summary and the meta line
/// (`NewsDetail.kt:536-596`).
///
/// ### The title is a link only when it is one
///
/// `webLinkOrNull` (`ui/components/LinkScheme.kt:16-23`) is ported as
/// ``newsWebLink(_:)``, and a story whose url is not http(s) with something
/// after the scheme — `javascript:alert(1)`, `file:///etc/passwd`, a bare
/// `example.com`, `"https://"` on its own — is **plain text**, not a dead
/// link. A title drawn as tappable that opens nothing is a control that
/// cannot do what it says.
struct NewsStoryRow: View {

    let story: NewsItem
    let detail: NewsPersonModel
    let nowMs: Int64

    @ScaledMetric(relativeTo: .body) private var glyph: CGFloat = 22

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            star
            VStack(alignment: .leading, spacing: 4) {
                title
                if let summary = story.plainSummary, !summary.isEmpty {
                    // The tags taken out with Android's own regex
                    // (``newsPlainSummary(_:)``). Two lines there (`:578`);
                    // here it wraps, because `lineLimit` plus an
                    // accessibility type size is the clipped-text finding
                    // Apple's audit reports.
                    Text(summary)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("news-story-summary-\(story.id)")
                }
                Text(story.metaLine(nowMs: nowMs))
                    .font(.caption2.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("news-story-meta-\(story.id)")
            }
        }
    }

    /// `favorite` / `unfavorite` (`:539-560`). The write sends the
    /// **negation of this row's own flag**, captured before the request
    /// (``NewsPersonModel/toggleFavorite(_:)``), so the star is not a one-way
    /// control.
    private var star: some View {
        Button {
            detail.toggleFavorite(story)
        } label: {
            Image(systemName: story.favorited ? "star.fill" : "star")
                .font(.footnote)
                .frame(width: glyph, height: glyph)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // `brandInk`, never ``Color/brand``: a star that is the only thing
        // saying a story is pinned is a graphic that has to be seen, and the
        // fill amber is 2.05:1.
        .foregroundStyle(story.favorited ? Color.brandInk : Color.fgMuted)
        .accessibilityLabel(detail.favoriteLabel(story))
        .accessibilityValue(story.displayTitle)
        .accessibilityIdentifier("news-favorite-\(story.id)")
    }

    @ViewBuilder
    private var title: some View {
        if let link = story.webLink {
            Link(destination: link) {
                Text(story.displayTitle)
                    .font(.subheadline)
                    .foregroundStyle(Color.brandInk)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                    .frame(minHeight: 44, alignment: .leading)
            }
            .accessibilityIdentifier("news-story-title-\(story.id)")
        } else {
            Text(story.displayTitle)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
                .accessibilityIdentifier("news-story-title-\(story.id)")
        }
    }
}
