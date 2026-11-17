import DomovoiKit
import SwiftUI

/// **The one way Friend houses shows text from another house**: message
/// bodies, friend and conversation names, list titles, `last_error`, and the
/// server's own `detail` sentences.
///
/// `Text(verbatim:)` and nothing else. A `Text` given a `LocalizedStringKey`
/// (any string literal, or an interpolated one) parses Markdown, so
/// `**z**` would turn bold and `[link](http://y)` into a tappable link;
/// `verbatim:` draws the characters exactly as they came. SwiftUI's `Text`
/// has no data detectors, so a URL in a message is words, not a link, and
/// nothing here fetches, previews or opens anything. `.lineLimit(nil)` and a
/// vertical `fixedSize` keep every line break and never cut a message short.
///
/// Every `Text` in this folder is `Text(verbatim:)`, the app's own words
/// included, so the rule is one a source scan can hold the folder to
/// (`FriendHousesPlainTextTests` in DomovoiKit), with no exceptions to argue
/// about. Android's twin is `FhPlain` (`FriendHousesScreen.kt`).
struct FhPlainText: View {

    let text: String
    var font: Font = .body
    var weight: Font.Weight?
    var colour: Color = .primary

    var body: some View {
        Text(verbatim: text)
            .font(font)
            .fontWeight(weight)
            .foregroundStyle(colour)
            .lineLimit(nil)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A muted line of the app's own words (the admin-only sentences, notes).
struct FhMutedLine: View {

    let text: String

    var body: some View {
        FhPlainText(text: text, font: .footnote, colour: Color.fgMuted)
    }
}

/// A problem a read or a send ran into, as the server or ``fhProblem(_:_:)``
/// said it: a warning dot and the sentence.
struct FhProblemLine: View {

    let text: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            ToneDot(tone: .warn)
            FhPlainText(text: text, font: .footnote)
        }
        .accessibilityElement(children: .combine)
    }
}

/// An empty list: one lowercase line, and a second when there is something
/// to say about it.
struct FhEmptyNote: View {

    let title: String
    var detail: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            FhPlainText(text: title, font: .subheadline)
            if let detail {
                FhPlainText(text: detail, font: .footnote, colour: Color.fgMuted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
    }
}

/// A launcher's count: the Friend houses entry's unread messages (the bar,
/// the sidebar and Home's grid). The brand fill with its own text colour,
/// 8.49:1 (``HomeTonePill``'s note); hidden from VoiceOver, which reads the
/// count as the entry's value instead.
struct RouteCountBadge: View {

    let count: Int

    var body: some View {
        Text(verbatim: count > 99 ? "99+" : "\(count)")
            .font(.caption2.monospacedDigit())
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .foregroundStyle(Color.brandFg)
            .background(Color.brand, in: Capsule())
            .fixedSize()
            .accessibilityHidden(true)
    }
}

extension View {

    /// The count a launcher entry carries, said to VoiceOver as its value
    /// ("2 unread"), and nothing when there is none.
    func routeCountValue(_ count: Int?) -> some View {
        accessibilityValue(Text(verbatim: count.map(FhWords.unread) ?? ""))
    }
}
