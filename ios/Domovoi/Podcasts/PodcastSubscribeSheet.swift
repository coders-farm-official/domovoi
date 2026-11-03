import DomovoiKit
import SwiftUI

/// Subscribe to a podcast: search the directory, or paste a feed URL.
///
/// `PodcastsSubscribe.kt:54-209`'s `SubscribeDialog` — the same two tabs
/// (`search` · `by rss url`), the same placeholders, and the same two
/// requests: `GET /api/podcasts/discover?q=` for the hits, then
/// `POST /api/podcasts/subscriptions` with the **hit's own `feed_url`**, so
/// the person sees what they are adding before it is added. Subscribing by
/// name — which makes the server search the directory itself — is not sent,
/// exactly as Android does not send it.
///
/// ### A failure is said in here, not behind here
///
/// Android toasts both failures and leaves the dialog up
/// (`PodcastsSubscribe.kt:82, 101`). A toast on iOS is the shell's and is
/// drawn **behind a sheet**, where nobody can read it — and a failed
/// subscribe is exactly the case where the sheet stays up, because the person
/// has to be able to correct the URL. So ``PodcastsModel/discoveryError`` and
/// ``PodcastsModel/subscribeError`` are drawn here, in the same words
/// (`failureText` with the same verbs). DECISIONS.md.
///
/// ### The write outlives the sheet
///
/// `subscribe` is the household's write, not this sheet's, so dismissing
/// mid-flight does not cancel it (Android puts it in `app.scope` for that
/// reason) and the sheet closes **on success only** —
/// ``PodcastsModel/subscribe(feedURL:)`` owns both halves. Nothing in this
/// file holds a `Task`.
struct PodcastSubscribeSheet: View {

    let podcasts: PodcastsModel
    let art: PodcastArtStore

    /// Which tab, which is the **sheet's** state and not the model's: it
    /// decides nothing about a request, and the model has no opinion about it.
    @State private var tab: Tab = .search

    /// What is typed. Neither field is debounced — Android's directory search
    /// runs on a button or the Search key, never per keystroke, because each
    /// one sends the server to iTunes.
    @State private var query = ""
    @State private var feedURL = ""

    /// 40 points, as Android's 40 dp hit thumbnail
    /// (`PodcastsSubscribe.kt:152`).
    @ScaledMetric private var side: CGFloat = 40

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    enum Tab: String, CaseIterable {
        case search
        case url

        var label: String {
            switch self {
            case .search: return PodcastStrings.tabSearch
            case .url: return PodcastStrings.tabByFeedURL
            }
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    picker
                    switch tab {
                    case .search: searchTab
                    case .url: urlTab
                    }
                    if let said = podcasts.subscribeError {
                        trouble(said)
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(PodcastStrings.subscribeTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button {
                        podcasts.closeSubscribe()
                    } label: {
                        Image(systemName: "xmark")
                            .frame(minWidth: 44, minHeight: 44)
                            .contentShape(.rect)
                    }
                    .accessibilityLabel(PodcastStrings.close)
                }
            }
        }
    }

    /// The two pills. A `Picker` in the segmented style rather than two
    /// buttons: it is one control with one value, which is what the pills
    /// are, and VoiceOver reads the choice rather than two unrelated taps.
    private var picker: some View {
        Picker(PodcastStrings.subscribeTitle, selection: $tab) {
            ForEach(Tab.allCases, id: \.self) { which in
                Text(which.label).tag(which)
            }
        }
        .pickerStyle(.segmented)
        .accessibilityLabel(PodcastStrings.subscribeTitle)
    }

    // MARK: - search the directory

    @ViewBuilder
    private var searchTab: some View {
        fieldRow(
            placeholder: PodcastStrings.showNamePlaceholder,
            text: $query,
            button: PodcastStrings.search,
            submit: .search,
            // Blank does nothing, as Android's `if (q.isBlank()) return`
            // does: the route types `q` with `min_length=1`
            // (`podcasts.py:390`), so a blank would be a 422 for a field the
            // person can see is empty.
            enabled: !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && !podcasts.discovering
        ) {
            podcasts.discover(query)
        }
        if podcasts.discovering {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(PodcastStrings.search)
        }
        if let said = podcasts.discoveryError {
            trouble(said)
        }
        ForEach(podcasts.discoveries) { hit in
            PodcastDiscoveryRow(
                hit: hit,
                art: art,
                side: side,
                stacked: dynamicTypeSize.isAccessibilitySize,
                busy: podcasts.subscribing
            ) {
                guard let feed = hit.feedURL else { return }
                podcasts.subscribe(feedURL: feed)
            }
            Divider()
        }
    }

    // MARK: - paste a feed

    private var urlTab: some View {
        fieldRow(
            placeholder: PodcastStrings.feedURLPlaceholder,
            text: $feedURL,
            button: PodcastStrings.subscribe,
            // **Not** `.search`: this field subscribes. Android sets
            // `ImeAction.Search` on the discovery field only
            // (`PodcastsSubscribe.kt:146-147`) and leaves this one's default
            // (`:193-203`), and a Return key labelled `Search` over a button
            // that says `subscribe` is the same lie NIGHT-RULES §3 forbids.
            submit: .done,
            enabled: !feedURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && !podcasts.subscribing
        ) {
            podcasts.subscribe(feedURL: feedURL)
        }
    }

    /// A field and the button that acts on it, side by side — or one above
    /// the other once the type size makes that impossible (NIGHT-RULES §3).
    @ViewBuilder
    private func fieldRow(
        placeholder: String,
        text: Binding<String>,
        button: String,
        submit: SubmitLabel,
        enabled: Bool,
        act: @escaping () -> Void
    ) -> some View {
        let layout = dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(alignment: .center, spacing: 8))
        layout {
            TextField(placeholder, text: text)
                .textFieldStyle(.roundedBorder)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                // A single-line field, so the Return key can honestly say
                // what it does — and each tab passes its own word, because
                // the two do different things (`search` here, `subscribe` on
                // the url tab: Android's `ImeAction.Search` is on the
                // discovery field alone, `PodcastsSubscribe.kt:146-147`).
                // It was hard-coded `.search` for both until review. The
                // rule against `submitLabel` is about a vertical-axis field,
                // where Return makes a newline.
                .submitLabel(submit)
                .onSubmit { if enabled { act() } }
                .accessibilityLabel(placeholder)
                .frame(maxWidth: .infinity)
            Button(action: act) {
                Text(button)
                    .frame(minHeight: 44)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .buttonStyle(.bordered)
            .disabled(!enabled)
        }
    }

    private func trouble(_ said: String) -> some View {
        Text(said)
            .font(.footnote)
            .foregroundStyle(Color.errInk)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// One directory hit: a thumbnail, the show's name, its author, and `add`.
///
/// `PodcastsSubscribe.kt:152-187`. **A row, never a `Task`**: `add` calls
/// into ``PodcastsModel/subscribe(feedURL:)``, which owns the write, because
/// this row is a lazy cell and the write has to outlive both the row and the
/// sheet.
struct PodcastDiscoveryRow: View {

    let hit: PodcastDiscovery
    let art: PodcastArtStore
    let side: CGFloat
    let stacked: Bool
    let busy: Bool
    let onAdd: () -> Void

    var body: some View {
        layout {
            PodcastArtView(path: PodcastArt.path(for: hit), store: art, side: side)
            VStack(alignment: .leading, spacing: 2) {
                Text(hit.displayTitle)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if let author = hit.author, !author.isEmpty {
                    Text(author)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            // **No `add` for a hit with no feed.** The server hides a hit
            // whose feed it would refuse to fetch (`podcasts.py:414-417`), so
            // this is unreachable through a real answer — and a button that
            // would post an empty `feed_url` for the server to 400 is a
            // control that cannot do what it says
            // (``PodcastDiscovery/canSubscribe``).
            if hit.canSubscribe {
                Button(action: onAdd) {
                    Text(PodcastStrings.add)
                        .frame(minHeight: 44)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .buttonStyle(.bordered)
                .disabled(busy)
                // Fifteen hits means fifteen buttons all saying `add`, so
                // each one says what it adds.
                .accessibilityLabel(PodcastStrings.add)
                .accessibilityValue(hit.displayTitle)
            }
        }
        .padding(.vertical, 8)
    }

    private var layout: AnyLayout {
        stacked
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(alignment: .center, spacing: 10))
    }
}
