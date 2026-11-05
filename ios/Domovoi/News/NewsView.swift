import DomovoiKit
import SwiftUI

/// News: whose news, their briefing, their topics and feeds, and their saved
/// stories.
///
/// `android/.../ui/screens/news/NewsScreen.kt` (203 lines) and
/// `NewsDetail.kt` (600), with the whole screen ported — every Android
/// control is drawn here, because every write on this API is `require_device`
/// (`web/backend/api/news.py:52`) and that is the token this app already
/// holds. Unlike Files, nothing is left out for want of an admin session.
///
/// ### What is not here, and why there is no control for it
///
/// **The expanded people rail** (`NewsScreen.kt:152-203`). Android draws a
/// 240 dp list of people at expanded width and a horizontal chip row at
/// compact; every iPhone is compact, so the rail is a layout branch nothing
/// can enter — and a branch nobody can reach is a branch nobody tests. The
/// one fact the rail carried that the chips did not, `heard <relTime>`, is
/// kept: it is drawn under the name where the chips are already a column (an
/// accessibility type size). DECISIONS.md records the call.
///
/// **Marking a story read.** `read_at` is drawn (`NewsDetail.kt:586`) and
/// there is no route that writes it — `news.py` has `favorite` for an item
/// and nothing else. A button that cannot do what it says is not drawn
/// ([[no-misleading-controls]]).
///
/// **Turning auto-fetch on**, which ``NewsStrings/noBriefing`` mentions. It
/// is a core setting rather than an `/api/news` route, so this screen says
/// where it lives instead of offering a switch it cannot throw. Android says
/// the same sentence and offers the same nothing.
///
/// **The validity dot does not pulse.** Android's `StatusDot(live = true)`
/// (`NewsDetail.kt:477`) animates for a valid feed; nothing in this app
/// animates yet and ``ToneDot/live`` already records that decision.
///
/// ### A plain `VStack`, deliberately
///
/// Not a `LazyVStack`. The saved feed is capped at the server's `limit`
/// default of 50 (`news.py:372`), the topics list is as long as somebody's
/// interests, and there is no pager on this screen on either phone — so the
/// standing rule that "every files-like page loads as you scroll" does not
/// reach News (there is no `Pager` in `ui/screens/news/`). What a lazy column
/// *would* buy is the add-feed write cancelled by scrolling, which is why
/// ``NewsFeedsModel`` exists at all; the plain column means a row that
/// scrolls away keeps its text field alive as well.
///
/// ### The accessibility identifiers
///
/// Every repeated control here — the chevron, the two ✕s, the star, the
/// re-check — carries Android's own `contentDescription` as its
/// `accessibilityLabel`, so five topic rows hold five buttons labelled
/// `remove topic`. That is what Android reads out, and the words are pinned
/// by ``NewsStrings``, so the label cannot carry the row. The identifier
/// does: `news-remove-topic-tech`, `news-expand-tech`,
/// `news-favorite-301`. Identifiers are invisible to a person, unique per
/// row, and are how `StubHouseUITests+News.swift` taps *one* of five
/// identical labels — the same problem `more for "Albums"` solved in Files,
/// without inventing a sentence Android does not say.
struct NewsView: View {

    let model: ShellModel

    @State private var news: NewsModel?

    /// `onAppear`/`onDisappear`, for ``HomeView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy.
    @State private var visible = false

    /// The clock every `relTime` on this screen is measured against, stamped
    /// when the screen appears.
    ///
    /// Not `Date()` inside `body`, which is ``MusicTrackSheet``'s reason: a
    /// body runs whenever anything it reads changes, so `generated 20m ago`
    /// would quietly become `21m ago` on an unrelated redraw and a test of
    /// ``NewsItem/metaLine(nowMs:)`` would be measuring a different clock
    /// from the screen's. Android recomputes per composition and has the same
    /// property for free; this is the explicit version. Re-stamped when a
    /// person is picked, so switching people does not show the first person's
    /// clock.
    @State private var nowMs = Int64(Date().timeIntervalSince1970 * 1000)

    var body: some View {
        ZStack {
            // **Hidden, not merely behind** — ``SettingsPlaceholder``'s
            // finding: `.isModal` stops VoiceOver navigating out of the
            // dialog, but the page is still rendered under a 35% scrim and
            // Apple's audit reports contrast against text nobody can read.
            // Nothing behind a confirmation is content.
            page
                .accessibilityHidden(removalAsked != nil)
            if let topic = removalAsked, let detail = news?.detail {
                DialogCard {
                    NewsRemoveTopicCard(
                        topic: topic,
                        onCancel: { detail.cancelRemoval() },
                        // The topic is passed rather than read back inside
                        // the action: ``NewsPersonModel/confirmRemoval(_:)``
                        // clears the dialog's state first, so a value read
                        // after that would be nil.
                        onConfirm: { detail.confirmRemoval(topic) }
                    )
                }
            }
        }
    }

    /// The topic whose removal is being confirmed, which is the person
    /// model's and not this view's: tapping another chip while the dialog is
    /// up throws the detail away, and with it the question.
    private var removalAsked: NewsTopic? { news?.detail?.confirmingRemoval }

    private var page: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header
                if let news {
                    NewsPeopleChipRow(news: news, nowMs: nowMs) { id in
                        news.select(id)
                        // A new person, a new clock: the previous person's
                        // `4h ago` must not be read against theirs.
                        nowMs = Int64(Date().timeIntervalSince1970 * 1000)
                    }
                    pane(news)
                }
                Spacer(minLength: 0)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // **A drag puts the keyboard away before it scrolls.**
        //
        // Without this the page is *stuck* while the free-form field has
        // focus: SwiftUI keeps a focused field on screen, so every flick is
        // undone by the scroll back to it. Train S002 measured that — ten
        // flicks and the content offset was identical to the pixel — and the
        // person it costs is the one who has just added a topic and wants to
        // see the row it made, which on this page is below the keyboard.
        .scrollDismissesKeyboard(.immediately)
        .task {
            // Built here and not in `init`, for ``HomeView``'s reason: the
            // model needs the container and a `@State` initialiser runs
            // before the view has one.
            if news == nil {
                news = NewsModel(in: model.container)
            }
            news?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            news?.stop()
        }
        // **The toast is the shell's**, drawn over the whole app, because a
        // sentence about a write that failed must outlive this screen — and
        // ``NewsModel`` has already dropped a line belonging to a person who
        // is no longer picked.
        .onChange(of: news?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            news?.setToast(nil)
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            news?.serverChanged()
        }
    }

    // MARK: - the header

    /// `PageHeader("News", …)` — `NewsScreen.kt:97`.
    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(NewsStrings.title)
                .font(.title2)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Text(NewsStrings.subtitle)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - the five states of the pane

    /// `NewsScreen.kt:78-93`, in the Kotlin's own order — which is the whole
    /// specification, since a failed roster read with no data must not fall
    /// through to "nobody is enrolled". The branch is
    /// ``NewsModel/Pane``'s; this draws it.
    @ViewBuilder
    private func pane(_ news: NewsModel) -> some View {
        switch news.pane {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(NewsStrings.title)
        case .failed(let why):
            // The roster's own failure, **with the server's words and a
            // retry** — not "nobody is enrolled". `/api/people` is
            // `require_device_read` (`people.py:60-80`), so this is what a
            // phone whose token has gone stale sees, and telling it the house
            // is empty would be a claim about the house this app did not
            // measure.
            VStack(alignment: .leading, spacing: 8) {
                Text(NewsStrings.requestFailed)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(why)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(NewsStrings.retry) { news.people.refresh() }
                    .accessibilityIdentifier("news-roster-retry")
            }
        case .noPeople:
            note(NewsStrings.noPeople, NewsStrings.noPeopleSub)
        case .pickAPerson:
            note(NewsStrings.pickAPerson, NewsStrings.pickAPersonSub)
        case .person:
            if let detail = news.detail {
                // `key(selectedPerson.id)` (`NewsScreen.kt:90-92`): a new
                // person is a new subtree, so nothing of the previous one's
                // view state — a scroll position, a focused field — survives
                // either. The model is already rebuilt; this is the view's
                // half of the same fact.
                NewsPersonView(detail: detail, categories: news.categoryWords, nowMs: nowMs)
                    .id(detail.personID)
            }
        }
    }

    private func note(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(detail)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 8)
    }
}

/// "remove topic" — the confirmation, whose body **names the topic**
/// (`NewsDetail.kt:288-303`).
///
/// The name is in the question for one reason: a dialog that confirmed the
/// wrong row would be visible in the dialog itself rather than only in what
/// disappeared afterwards. The UI case reads that sentence.
struct NewsRemoveTopicCard: View {

    let topic: NewsTopic
    let onCancel: () -> Void
    let onConfirm: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(NewsStrings.removeTopic, systemImage: "trash")
                .font(.headline)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
            Text(NewsStrings.removeTopicBody(topic.topicLabel))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("news-remove-ask")
            HStack(spacing: 8) {
                Spacer()
                // **A plain text button, which is also what Android draws.**
                // `ConfirmDialog`'s dismiss is a `TextButton` with `fgMuted`
                // on the dialog's own container (`ui/components/Dialogs.kt:41`),
                // and this was the app's `.bordered` + `.tint(brandInk)`
                // instead — which Apple's audit failed on train S008 with
                // "Contrast is not high enough for element unless font size
                // is larger", naming `news-remove-cancel`.
                //
                // It was right, and the arithmetic is the modifier's: a
                // `.bordered` button fills with its tint at about 20% and
                // draws the label in the tint itself, so `brandInk`'s
                // measured **5.65:1 on white** becomes `brandInk` on
                // `brandInk`-washed white — **4.31:1**, which is under the
                // 4.5:1 body text needs and exactly the "nearly passed" band.
                // On the card's own `.background` (``DialogCard``), `fgMuted`
                // is the token measured for this canvas: **7.44:1** light and
                // **7.88:1** dark. A fixed dark label on the tinted fill
                // would have passed in light mode and failed in dark.
                //
                // The 44-point floor is explicit because `.borderless` has
                // none: this button measured 34.3 points tall with the
                // bordered style.
                Button(NewsStrings.cancel, action: onCancel)
                    .buttonStyle(.borderless)
                    // Both, deliberately: `.borderless` draws its label in
                    // the **tint**, and `.foregroundStyle` is what every
                    // other label on this card is set with. Either alone
                    // leaves the colour depending on which one that style
                    // happens to read.
                    .tint(Color.fgMuted)
                    .foregroundStyle(Color.fgMuted)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
                    .accessibilityIdentifier("news-remove-cancel")
                // **No fill, as Android has none.** `ConfirmDialog`'s confirm
                // is a `TextButton` with its label in `colors.err` when
                // `destructive` (`Dialogs.kt:36-38`). This was a
                // `.borderedProminent` with no tint, so it filled with the
                // app's ``Color/brandInk`` (or with red, for the destructive
                // role — either way) and drew a white label on it: 5.65:1
                // light at best, **1.91:1** on the dark ink. The words are now
                // ``Color/errInk`` on the card itself, drawn by the style so
                // the role cannot repaint them: 6.08:1 on white and 5.70:1 on
                // the dark sheet (``InkOutlineButtonStyle``'s table; unit
                // ix-fill). The stroke is that style's, beside a `cancel`
                // that has none — the one detail that is not Android's.
                Button(NewsStrings.remove, role: .destructive, action: onConfirm)
                    .buttonStyle(InkOutlineButtonStyle(ink: .errInk))
                    .accessibilityIdentifier("news-remove-confirm")
            }
        }
    }
}
