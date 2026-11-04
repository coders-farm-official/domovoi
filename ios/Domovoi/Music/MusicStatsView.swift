import DomovoiKit
import SwiftUI

/// The stats tab: four whole-library numbers.
///
/// `StatsTab.kt`. The arithmetic is ``LibraryStats/tiles`` so that this file
/// is layout and nothing else — which is the split Android does not make, and
/// the reason its "`enriched`: 412 / 5232" line is specified by no test on
/// either side until now.
///
/// Two columns on a phone, one at an accessibility text size: a tile is a
/// label, a number and a line of detail, and two of those side by side stop
/// fitting well before AX5.
struct MusicStatsView: View {

    let music: MusicModel

    var body: some View {
        if let stats = music.stats.data {
            ViewThatFits(in: .horizontal) {
                grid(stats.tiles, columns: 2)
                grid(stats.tiles, columns: 1)
            }
        } else if music.stats.loading {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(MusicStrings.tabStats)
        } else {
            // The read answered and gave nothing, which is a different thing
            // from a library of zero tracks — and the sentence says which one
            // this is rather than showing four dashes that read as data.
            VStack(alignment: .leading, spacing: 4) {
                Text(MusicStrings.statsUnavailable)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(music.stats.error ?? MusicStrings.statsUnavailableNote)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(MusicStrings.retry) { music.stats.refresh() }
            }
        }
    }

    /// The tiles in rows of `columns`.
    ///
    /// Written out rather than `LazyVGrid`: this is four items, inside the
    /// screen's own lazy stack, and a nested lazy container for four things
    /// buys nothing and costs a second set of sizing rules.
    private func grid(_ tiles: [MusicStatTile], columns: Int) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(stride(from: 0, to: tiles.count, by: columns)), id: \.self) { start in
                HStack(alignment: .top, spacing: 12) {
                    ForEach(tiles[start..<min(start + columns, tiles.count)]) { tile in
                        MusicStatTileView(tile: tile)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    // The last row of an odd count keeps its tile at one
                    // column's width instead of stretching across both.
                    if tiles.count - start < columns {
                        ForEach(0..<(columns - (tiles.count - start)), id: \.self) { _ in
                            Spacer().frame(maxWidth: .infinity)
                        }
                    }
                }
            }
        }
    }
}

/// One tile: a label, a number, and a line of detail when there is one.
struct MusicStatTileView: View {

    let tile: MusicStatTile

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(tile.label)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Text(tile.value)
                .font(.title3.monospacedDigit())
                .fixedSize(horizontal: false, vertical: true)
            if let sub = tile.sub {
                Text(sub)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
        // One stop per tile: "total tracks, 5232, across all sources" rather
        // than three stops whose middle one is a bare number.
        .accessibilityElement(children: .combine)
    }
}
