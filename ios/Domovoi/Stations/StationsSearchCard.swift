import DomovoiKit
import SwiftUI

/// The search surface — `StationSearch.kt:84`, everything above the results.
///
/// Two scopes (the radio-browser catalogue, and the FCC FM import already in
/// the house), a query box, a country box in the online scope only, a submit
/// button, the favourites that match what is being typed, and — before a
/// search has been run — either the tag chips or the FM tip.
///
/// **The result rows are not in here.** They are siblings of this card in
/// ``StationsView``'s `LazyVStack`, because a `LazyVStack`'s laziness only
/// reaches its own direct children: thirty rows nested inside a card would
/// all be built the moment the card appeared, every `onAppear` would fire at
/// once, and page two would be fetched before anybody had scrolled. That
/// would also make a case about loading as you scroll pass for the wrong
/// reason.
struct StationsSearchCard: View {

    let stations: StationsModel

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            scopes
            fields
            favouriteMatches
            if stations.showsTagChips { tags }
            if stations.showsFMTip { tip }
            if let note = stations.searchEmptyNote { empty(note) }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
    }

    // MARK: - the two scopes (`:316`)

    private var scopes: some View {
        // Side by side on a phone, stacked at an accessibility size: each
        // button is two lines of text, and two of them across a 390-point
        // screen at AX5 is four words a line.
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(spacing: 8) { onlineScope; fmScope }
            } else {
                HStack(spacing: 8) { onlineScope; fmScope }
            }
        }
    }

    private var onlineScope: some View {
        scopeButton(
            .online,
            StationsStrings.scopeOnline,
            StationsStrings.scopeOnlineSub
        )
    }

    private var fmScope: some View {
        scopeButton(.fm, StationsStrings.scopeFM, StationsStrings.scopeFMSub)
    }

    private func scopeButton(_ scope: StationsScope, _ label: String, _ sub: String) -> some View {
        let chosen = stations.scope == scope
        return Button {
            stations.switchScope(scope)
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                    .font(.subheadline.weight(chosen ? .semibold : .regular))
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Text(sub)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            .padding(.horizontal, 10)
            .frame(minHeight: 44)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(chosen ? Color.brandInk : Color.fgMuted.opacity(0.35), lineWidth: 1)
            )
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // The two sub-lines are the choice's explanation, not its name, and
        // `selected` is what a screen reader needs to hear.
        .accessibilityLabel(label)
        .accessibilityValue(sub)
        .accessibilityAddTraits(chosen ? [.isSelected] : [])
    }

    // MARK: - the boxes and the button

    private var fields: some View {
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 8) { query; country; submit }
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    query
                    HStack(spacing: 8) { country; submit }
                }
            }
        }
    }

    /// The query box.
    ///
    /// A **single-line** `TextField`, so `submitLabel(.search)` is honest:
    /// the multi-line rule — a vertical-axis field's Return key makes a
    /// newline, so a key labelled `search` would lie — does not apply here,
    /// and the IME action running the search is Android's own
    /// (`StationSearch.kt:200`).
    private var query: some View {
        TextField(
            StationsStrings.placeholder(stations.scope),
            text: Binding(get: { stations.query }, set: { stations.typed($0) })
        )
        .textFieldStyle(.roundedBorder)
        .autocorrectionDisabled()
        .textInputAutocapitalization(.never)
        .submitLabel(.search)
        .onSubmit { stations.submit() }
        .accessibilityLabel(StationsStrings.placeholder(stations.scope))
    }

    /// The country box, **online scope only** (`:210`): the FCC import is one
    /// country's, so the filter means nothing there.
    @ViewBuilder private var country: some View {
        if stations.scope == .online {
            TextField(
                StationsStrings.countryPlaceholder,
                text: Binding(get: { stations.country }, set: { stations.typedCountry($0) })
            )
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.characters)
            .frame(maxWidth: 110)
            .submitLabel(.search)
            .onSubmit { stations.submit() }
            .accessibilityLabel(StationsStrings.countryPlaceholder)
        }
    }

    private var submit: some View {
        Button(stations.submitLabel) { stations.submit() }
            .buttonStyle(.borderedProminent)
            .tint(Color.brand)
            .disabled(stations.results.state.loading)
    }

    // MARK: - the favourites that match what is typed (`:112`)

    /// Answered before the directory has been asked, and absent when nothing
    /// matches — ``StationsModel/typed(_:)`` degrades quietly, as Android's
    /// `getOrDefault(emptyList())` does, so there is no failure to report
    /// here.
    @ViewBuilder private var favouriteMatches: some View {
        let found = stations.favoriteMatches
        if !found.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Image(systemName: "star.fill")
                        .font(.caption)
                        .foregroundStyle(Color.brandInk)
                        .accessibilityHidden(true)
                    Text(StationsStrings.yourFavorites)
                        .font(.caption.weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                    Text(StationsStrings.matches(found.count))
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                ForEach(found, id: \.rowKey) { row in
                    StationsFavoriteMatchRow(stations: stations, row: row)
                }
            }
        }
    }

    // MARK: - the chips and the tip

    /// `tags:` and `ONLINE_TAG_PRESETS` (`:36`, `:264`). A tap puts the word
    /// in the box and searches with it in the same breath
    /// (``StationsModel/tapTag(_:)``).
    private var tags: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(StationsStrings.tagsLabel)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // Wraps rather than scrolls sideways: six short words fit on two
            // lines of a phone, and a horizontal scroller hides the chips
            // past the edge at an accessibility size.
            HStack(spacing: 6) {
                ForEach(StationsStrings.tagPresets, id: \.self) { tag in
                    Button(tag) { stations.tapTag(tag) }
                        .font(.caption)
                        .buttonStyle(.bordered)
                        .frame(minHeight: 44)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var tip: some View {
        Text(StationsStrings.fmTip)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func empty(_ note: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(StationsStrings.noMatch)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(note)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// One favourite that matches what is being typed — `FavoriteMatchRow`
/// (`StationSearch.kt:349`).
///
/// **No star on this row**, as on Android: it is already saved, which is why
/// it is in this list at all.
struct StationsFavoriteMatchRow: View {

    let stations: StationsModel
    let row: StationsRow

    var body: some View {
        StationsPlayTap(stations: stations, row: row) {
            HStack(spacing: 8) {
                Text(row.name)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 4)
                Text(StationsStrings.head(row))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(minHeight: 44)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
    }
}

/// One directory hit, or one FM row — `SearchResultRow`
/// (`StationSearch.kt:394`).
///
/// The star first, because it is the one thing every row of this list is for.
/// Then what the row is, which is two different shapes: an online hit shows
/// its stream URL, its country and up to three of its tags; an FM row shows
/// `WJIM · 97.5 FM`, its name and its market. A row already in the table says
/// `saved`.
struct StationsSearchResultRow: View {

    let stations: StationsModel
    let row: StationsRow

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            StationsStarButton(stations: stations, row: row, isSearchHit: true)
            StationsPlayTap(stations: stations, row: row) { face }
        }
    }

    private var face: some View {
        VStack(alignment: .leading, spacing: 2) {
            if stations.askedScope == .fm {
                Text(StationsStrings.fmHead(row))
                    .font(.subheadline.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Text(row.name)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if let market = StationsStrings.market(row) {
                    Text(market)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text(row.name)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if let url = row.streamURL {
                    // Wraps rather than truncating: a URL is the one thing on
                    // this row somebody may want to read character by
                    // character, and `minimumScaleFactor` is rejected
                    // throughout this app.
                    Text(url)
                        .font(.caption2)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                Text(StationsStrings.head(row))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            chips
        }
        .frame(minHeight: 56)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(.rect)
    }

    /// Up to **three** tags (`:478`) and the `saved` pill. Three because that
    /// is what Android shows, and because radio-browser hands back rows with
    /// a dozen.
    @ViewBuilder private var chips: some View {
        let tags = Array(row.tags.prefix(3))
        if !tags.isEmpty || row.favorited {
            HStack(spacing: 6) {
                if row.favorited {
                    StationsChip(label: StationsStrings.saved)
                }
                if !dynamicTypeSize.isAccessibilitySize {
                    // Dropped at an accessibility size, where three chips and
                    // the pill are four lines of their own: they are the
                    // least of what this row says, and the station's name,
                    // URL and country are all above them.
                    ForEach(tags, id: \.self) { tag in
                        StationsChip(label: tag)
                    }
                }
            }
        }
    }
}
