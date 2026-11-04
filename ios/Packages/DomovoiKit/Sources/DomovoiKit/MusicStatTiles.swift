import Foundation

/// The stats tab's four tiles, as **data**.
///
/// `StatsTab.kt:33-62`'s `StatsGrid`, whose tiles are built inside a
/// `@Composable` and are therefore specified by nothing: the arithmetic that
/// decides "`enriched`: 412 / 5232, 4820 pending" is four lines in the middle
/// of a layout. Here it is a function of ``LibraryStats``, so the awkward
/// cases — a library of zero, a library whose durations were never indexed, a
/// single `added_via` bucket — are things a test can state.
public struct MusicStatTile: Equatable, Sendable, Identifiable {

    /// The label, which is also the identity: four fixed tiles, and the one
    /// whose label moves (`added via spotify`) moves with its content.
    public var id: String { label }

    public let label: String
    public let value: String
    /// The line under the number, or nil when there is nothing true to put
    /// there. Android passes `null` in two of its four cases and draws
    /// nothing; so does this.
    public let sub: String?

    public init(label: String, value: String, sub: String?) {
        self.label = label
        self.value = value
        self.sub = sub
    }
}

public extension LibraryStats {

    /// The four tiles, in Android's order.
    ///
    /// The `added_via` breakdown is **sorted by count, descending**, and its
    /// vocabulary is open: plugins register their own values, so the tile
    /// reads whatever buckets the server reported and nothing here switches
    /// on a known set.
    ///
    /// Ties are broken by the bucket's name so the tile does not reshuffle
    /// between reads — Android's `sortedByDescending` is stable over the map's
    /// iteration order, and a Swift `Dictionary`'s order is **not** stable
    /// between runs, so "the biggest bucket" would otherwise be a coin toss
    /// between two equal ones.
    var tiles: [MusicStatTile] {
        let ranked = byAddedVia.sorted {
            $0.value != $1.value ? $0.value > $1.value : $0.key < $1.key
        }
        let top = ranked.first
        let rest = ranked.dropFirst()

        let hasDuration = (totalDurationSec ?? 0) > 0
        let minutes = hasDuration ? rounded((totalDurationSec ?? 0) / 60) : 0

        return [
            MusicStatTile(
                label: MusicStrings.statTotalTracks,
                value: "\(totalTracks)",
                sub: MusicStrings.statAcrossSources
            ),
            MusicStatTile(
                label: MusicStrings.statTotalDuration,
                value: hasDuration ? fmtBigDur(totalDurationSec) : "—",
                sub: hasDuration
                    ? MusicStrings.statMinutes(minutes)
                    : MusicStrings.statNoDurations
            ),
            MusicStatTile(
                label: MusicStrings.statAddedVia(top?.key),
                value: "\(top?.value ?? 0)",
                sub: rest.isEmpty
                    ? nil
                    : rest.map { "\($0.value) \($0.key)" }.joined(separator: " · ")
            ),
            MusicStatTile(
                label: MusicStrings.statEnriched,
                // `—` for an empty library rather than `0 / 0`, which reads
                // as a failure rather than as an absence.
                value: totalTracks > 0 ? "\(enrichedCount) / \(totalTracks)" : "—",
                sub: totalTracks > 0
                    // `max(0, …)`, which Android does not do: the two numbers
                    // come from different queries, so a sweep that finished
                    // between them can report more enriched rows than total
                    // rows, and "-3 pending" is a number no person can read.
                    ? MusicStrings.statPending(max(0, totalTracks - enrichedCount))
                    : nil
            ),
        ]
    }
}
