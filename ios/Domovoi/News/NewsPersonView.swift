import DomovoiKit
import SwiftUI

/// One person's news: the heading and `poll now`, the briefing card, the
/// topic manager and the saved feed.
///
/// `PersonNewsDetail` (`NewsDetail.kt:78-148`), which composes the same four
/// things in the same order. The three reads are
/// ``NewsPersonModel``'s and every one of them belongs to the person this
/// view was built for: the model captured its `personID` at init, so a late
/// answer cannot land here from somebody else's request
/// ([[read-after-await-wrong-key]]).
struct NewsPersonView: View {

    let detail: NewsPersonModel

    /// The nine category words, from the page's own read — passed in rather
    /// than read again, because they are a static set for the house and two
    /// reads of one list is how two cards come to disagree about it.
    let categories: [String]

    let nowMs: Int64

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            heading
            briefing
            NewsTopicsCardView(detail: detail, categories: categories)
            NewsSavedFeedView(detail: detail, nowMs: nowMs)
        }
    }

    // MARK: - the heading and the poll

    /// `NewsDetail.kt:117-135`: the person's name, the sub-line, and the one
    /// button that reaches the internet.
    ///
    /// A column at an accessibility size rather than a row, so `polling…`
    /// does not squeeze `Kamron's news` to one word a line. Branched on
    /// `dynamicTypeSize.isAccessibilitySize`, which is what the rest of this
    /// app does (`HomeAnnounceView.swift:58`) — and not `ViewThatFits`, whose
    /// choice a test cannot predict from the type size alone.
    @ViewBuilder
    private var heading: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 10) {
                headingText
                pollButton
            }
        } else {
            HStack(alignment: .top, spacing: 12) {
                headingText
                Spacer(minLength: 8)
                pollButton
            }
        }
    }

    private var headingText: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(detail.heading)
                .font(.title3)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("news-person-heading")
            Text(NewsStrings.personSub)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// `poll now` / `polling…` — `NewsDetail.kt:131-133`, disabled while a
    /// poll is in flight.
    ///
    /// **It is drawn on every house**, including one with
    /// `INTERNET_ACCESS=never`, where the route answers 409
    /// (`news.py:452-453`). That is not a control that cannot do what it
    /// says: nothing on this phone knows the egress setting — there is no
    /// read for it on this API — and the server's own refusal is what the
    /// toast then says, word for word, to the person who is able to change
    /// it. Guessing the answer instead would hide the button on a house whose
    /// internet is on. DECISIONS.md, "A 409 says the server's own sentence".
    private var pollButton: some View {
        Button(detail.pollLabel) { detail.pollNow() }
            .buttonStyle(BrandFillButtonStyle())
            .disabled(detail.polling)
            .frame(minHeight: 44)
            .accessibilityIdentifier("news-poll")
    }

    // MARK: - the briefing

    /// `BriefingCard` (`NewsDetail.kt:150-167`): the last spoken digest, or
    /// how to get one.
    ///
    /// The sub-line is keyed on `generated_at` and not on the text, as the
    /// Kotlin is (`:155-156`) — and the body in muted ink is this app's own
    /// sentence rather than the server's (``NewsBriefing/hasBriefing``), so
    /// the two are told apart by eye. A `{null, null}` briefing is a
    /// **successful** read (`news.py:436-437`), which is why there is no
    /// error branch here.
    private var briefing: some View {
        HomeCard(title: NewsStrings.briefingLabel) {
            VStack(alignment: .leading, spacing: 8) {
                if let now = detail.briefing.data {
                    Text(now.subLine(nowMs: nowMs))
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(now.bodyText)
                        .font(.subheadline)
                        .foregroundStyle(now.hasBriefing ? Color.primary : Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("news-briefing-body")
                } else if let why = detail.briefing.error {
                    // A read that has **never** landed and failed. Android
                    // draws the standing "no briefing yet" sentence whatever
                    // happened (`NewsDetail.kt:162` keys on the value alone),
                    // which tells a person the house has no digest when the
                    // truth is that this phone could not ask. The message is
                    // the server's, already worded by the read.
                    Text(why)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("news-briefing-body")
                } else {
                    ProgressView()
                        .accessibilityLabel(NewsStrings.briefingLabel)
                }
            }
        }
    }
}
