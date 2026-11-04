import DomovoiKit
import SwiftUI

// ---------------------------------------------------------------------------
// The rows the two lists on the Stations screen are made of, and the two
// small pieces both of them draw.
//
// Every name in this file carries the item (NIGHT-RULES §2): twenty branches
// each add files to one module, and a bare `StatusDot` or `Chip` is an
// `invalid redeclaration` that drops a whole branch from a train.
//
// **The dot is ``ToneDot`` and the tone pill is ``HomeTonePill``**, not
// copies of them. Those two were measured — the dot's colours against the
// 3:1 a meaningful graphic needs, the pill's surface against Apple's contrast
// audit, which `.bar` cost this track five rounds of misdiagnosis — and a
// second mapping of ``HealthTone`` in this file is how two screens come to
// disagree about what amber means. ``StationsRules/samplerTone(_:)`` and
// ``StationsRules/detectionSource(_:)`` answer in that same vocabulary for
// exactly this reason.
// ---------------------------------------------------------------------------

/// One row of the Recent strip — `RecentRow` (`StationsScreen.kt:313`).
///
/// The glyph says which kind of station it is, then the name, then when it
/// was last played, then a star. **The star here is always a PATCH**: a row
/// in this strip is in the server's table by definition — it got there
/// because `POST /play` persisted it — so there is nothing to create
/// (``StationsModel/toggleFavorite(_:)``).
struct StationsRecentRow: View {

    let stations: StationsModel
    let row: StationsRow

    /// The glyph column grows with the type size. A fixed `frame(width:)`
    /// does not clip an `Image` that scales — it draws straight across the
    /// row's own words, which is the Files bug a screenshot found and no
    /// automated check did (``FileRowView``).
    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 26

    var body: some View {
        HStack(spacing: 8) {
            StationsPlayTap(stations: stations, row: row) {
                face
            }
            StationsStarButton(stations: stations, row: row, isSearchHit: false)
        }
    }

    private var face: some View {
        HStack(spacing: 12) {
            Image(systemName: row.source == "fm" ? "antenna.radiowaves.left.and.right" : "radio")
                .font(.body)
                .foregroundStyle(Color.fgMuted)
                .frame(width: glyphWidth, alignment: .center)
                .accessibilityHidden(true)
            // One element, so VoiceOver reads "name, 4m ago" as a row rather
            // than as two stops — and so a case can assert what this row
            // says and what it does **not** say in one label.
            VStack(alignment: .leading, spacing: 2) {
                Text(row.name)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Text(stations.relative(row.lastPlayedAt))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
            Spacer(minLength: 0)
        }
        .frame(minHeight: 44)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(.rect)
    }
}

/// One favourite — `FavoriteRow` (`StationsScreen.kt:457`).
///
/// Two taps, and they are Android's two: the **row** plays it and the
/// **chevron** opens its detail. Kept, because they are different intents and
/// a single tap would have to pick one.
///
/// What the row says, under the name: the three-state now-playing line, and
/// `97.5 FM · sampling every 180s`. What it shows: a dot that is live once
/// something has been sampled (``StationsRules/samplerTone(_:)``).
///
/// The interval editor and `forget`, which Android draws inside this row when
/// it is expanded, are in the sheet — MAP §5: a 600-point block under a row
/// pushes the rest of a phone's page off screen, and the chevron already
/// reads as "open this".
struct StationsFavoriteRow: View {

    let stations: StationsModel
    let row: StationsRow

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        // Four things on one line is one too many at an accessibility size,
        // where the name alone can take three lines (rule §3).
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 8) {
                StationsPlayTap(stations: stations, row: row) { face }
                HStack(spacing: 12) {
                    ToneDot(tone: StationsRules.samplerTone(row))
                    chevron
                }
            }
        } else {
            HStack(spacing: 8) {
                StationsPlayTap(stations: stations, row: row) { face }
                ToneDot(tone: StationsRules.samplerTone(row))
                chevron
            }
        }
    }

    private var face: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(row.name)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            StationsNowPlayingLine(stations: stations, row: row)
            Text(stations.metaLine(row))
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        // The three lines are one stop, and one label a case can ask what it
        // holds: "the track is on 04's row and `no live metadata` is not".
        .accessibilityElement(children: .combine)
        .frame(minHeight: 56)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(.rect)
    }

    /// Opens the detail sheet. An icon-only control, so it carries Android's
    /// own `contentDescription` as its label, and the station's name as its
    /// **value** — `details and detections` is the same word on twenty rows,
    /// and `firstMatch` on it would be whichever row the tree happens to hold
    /// first (the trap ``openChatRowMenu`` was written for).
    private var chevron: some View {
        Button {
            stations.toggleDetail(row)
        } label: {
            Image(systemName: stations.selected == row.id ? "chevron.down" : "chevron.right")
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .frame(width: 44, height: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            stations.selected == row.id ? StationsStrings.hideDetails : StationsStrings.openDetails
        )
        .accessibilityValue(row.name)
    }
}

/// The three sentences a now-playing line is, and never one with blanks in it
/// (`StationsScreen.kt:620`, ``StationsRules/nowPlaying(_:)``).
///
/// A track beats everything; `icy_supported == false` means the ICY poller
/// has given up and is said quietly, in italic, as Android says it; **nil is
/// not false** — never probed is `listening…`.
struct StationsNowPlayingLine: View {

    let stations: StationsModel
    let row: StationsRow

    var body: some View {
        switch stations.nowPlaying(row) {
        case .track(let track, let since):
            Text(line(track, since))
                .font(.caption)
                .foregroundStyle(Color.brandInk)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        case .noLiveMetadata:
            Text(StationsStrings.noLiveMetadata)
                .font(.caption)
                .italic()
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        case .listening:
            Text(StationsStrings.listening)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// `"Sonora Pine — Red Fire Serpent · 2m ago"`, and the track alone when
    /// the server sent no stamp for it (`:624`).
    private func line(_ track: String, _ since: String?) -> String {
        guard since != nil else { return track }
        return "\(track) · \(stations.relative(since))"
    }
}

/// A row's star — `StarTap` (`StationSearch.kt:590`), the one control on this
/// screen whose meaning depends on where it is.
///
/// On a **search** row it is the state machine: a saved row is a PATCH, a
/// directory hit (`id == 0`) is a POST that saves and favourites, and the id
/// that POST answers is kept on the row or the next tap does nothing (plugin
/// board F-P013). On a **Recent** row it is always a PATCH.
///
/// Both are ``StationsModel`` methods and neither is a `Task` in this view:
/// these rows are in lazy lists, and a lazy row never holds a `Task` — the
/// write has to outlive the row that scrolled off while it was in flight.
struct StationsStarButton: View {

    let stations: StationsModel
    let row: StationsRow

    /// A search row's star saves an unsaved hit; a list row's only flips a
    /// row that is already in the table.
    let isSearchHit: Bool

    var body: some View {
        Button {
            if isSearchHit {
                stations.star(row)
            } else {
                stations.toggleFavorite(row)
            }
        } label: {
            Image(systemName: row.favorited ? "star.fill" : "star")
                .font(.footnote)
                .foregroundStyle(row.favorited ? Color.brandInk : Color.fgMuted)
                .frame(width: 44, height: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .disabled(stations.isBusy(row))
        .accessibilityLabel(
            row.favorited ? StationsStrings.unfavorite : StationsStrings.favorite
        )
        .accessibilityValue(row.name)
    }
}

/// A row's body, tappable only when something can play it.
///
/// **This is where [[no-misleading-controls]] lands on this screen.** Android
/// makes every row a `clickable` that plays, and plays through a disabled
/// play button when the URL is one nothing can open. Here there are three
/// cases and they are not the same no:
///
/// * This build has no audio engine (``StationsModel/canPlayAnything``): the
///   row is **not a control at all**, because every row would be dead.
///   ``StationsView`` says so once, on the page.
/// * The engine is there and the URL is house-local, missing or not http:
///   the row is a disabled control with ``StationsModel/whyNotPlayable(_:)``
///   under it — the station is the thing that cannot be played, and the row
///   says which.
/// * Otherwise it plays.
struct StationsPlayTap<Content: View>: View {

    let stations: StationsModel
    let row: StationsRow
    @ViewBuilder let content: Content

    var body: some View {
        if !stations.canPlayAnything {
            // Not a disabled button: a greyed-out row on every row of the
            // screen reads as an app that has stopped working.
            content
        } else if stations.canPlay(row) {
            Button {
                stations.play(row)
            } label: {
                content
            }
            .buttonStyle(.plain)
            .accessibilityHint(StationsStrings.playNamed(row.name))
        } else {
            VStack(alignment: .leading, spacing: 2) {
                content
                if let why = stations.whyNotPlayable(row) {
                    Text(why)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
            }
        }
    }
}

/// One small bordered word — a tag, or the `saved` pill on a search row
/// (`StationSearch.kt:470`, `:520`).
///
/// Not ``HomeTonePill``: that carries a ``HealthTone``, a graded word whose
/// colour means something. A tag is a tag.
struct StationsChip: View {

    let label: String

    var body: some View {
        Text(label)
            .font(.caption2)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .foregroundStyle(Color.fgMuted)
            .overlay {
                Capsule().strokeBorder(Color(.separator), lineWidth: 1)
            }
    }
}
