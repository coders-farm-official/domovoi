import Foundation

/// One row of the track sheet's metadata table.
///
/// `TrackDrawer.kt:148-171`'s seven `MetaRow`s. Data rather than layout for
/// ``MusicStatTile``'s reason: what each row says about a track whose field
/// is missing is a decision — `—`, nothing at all, or a pill — and over there
/// it is a `?:` in the middle of a `Composable`.
public struct MusicMetaRow: Equatable, Sendable, Identifiable {

    /// How the value is drawn.
    public enum Style: Equatable, Sendable {
        /// Monospaced: a duration, an id, a timestamp, a path.
        case mono
        /// A pill, in a tone.
        case pill(brand: Bool)
    }

    public var id: String { label }

    public let label: String
    public let value: String
    public let style: Style

    public init(label: String, value: String, style: Style = .mono) {
        self.label = label
        self.value = value
        self.style = style
    }
}

public extension LibraryTrack {

    /// The sheet's metadata table.
    ///
    /// - Parameter nowMs: the clock the two relative times are measured
    ///   against, for ``relTime(_:nowMs:)``'s reason.
    ///
    /// The `source` row is a **pill when there is a source and an em dash
    /// when there is not**, never the word "manual": a file the library found
    /// on disk already says `manual` on the `added via` row directly below,
    /// and the pair reading "manual / manual" is the defect
    /// ``LibraryTrack/sourcePill`` was written for.
    func metaRows(nowMs: Int64) -> [MusicMetaRow] {
        [
            MusicMetaRow(
                label: MusicStrings.metaDuration,
                value: fmtDur(durationSec)
            ),
            sourcePill.map {
                MusicMetaRow(label: MusicStrings.metaSource, value: $0, style: .pill(brand: true))
            } ?? MusicMetaRow(label: MusicStrings.metaSource, value: "—"),
            MusicMetaRow(label: MusicStrings.metaSourceID, value: sourceID ?? "—"),
            MusicMetaRow(label: MusicStrings.metaAdded, value: relTime(addedAt, nowMs: nowMs)),
            MusicMetaRow(
                label: MusicStrings.metaAddedVia,
                value: addedVia ?? "—",
                // Android gives the brand tone to `voice` alone
                // (`TrackDrawer.kt:158`) — the one route that is a person
                // talking to the house rather than a file appearing.
                style: .pill(brand: addedVia == "voice")
            ),
            MusicMetaRow(
                label: MusicStrings.metaEnriched,
                value: enrichedAt != nil ? relTime(enrichedAt, nowMs: nowMs) : "—"
            ),
            MusicMetaRow(label: MusicStrings.metaPath, value: filePath ?? "—"),
        ]
    }
}

/// Deleting one library row, and the two different things that means.
public extension MusicModel {

    /// What the confirmation asks, which is **not one sentence with a word
    /// swapped**.
    ///
    /// `TrackDrawer.kt:252`. Removing a row from the library and deleting the
    /// file off the disk are different acts with different consequences, and
    /// the second one is irreversible, so they get different sentences: the
    /// one that keeps the file says the rescan will bring it back, and the
    /// one that does not says it cannot be undone and shows the path it is
    /// about.
    nonisolated static func deleteQuestion(_ track: LibraryTrack, alsoFile: Bool) -> String {
        guard alsoFile else { return MusicStrings.deleteFromLibraryBody(track.displayTitle) }
        return MusicStrings.deleteFileBody(track.displayTitle, path: track.filePath)
    }

    /// `DELETE /api/music/library/{id}`, with the file or without it.
    ///
    /// The row leaves the list **in place** rather than by a reload, for
    /// ``toggleFavorite(_:)``'s reason: a reload re-fetches every page the
    /// person has scrolled through to learn one thing this app already knows.
    /// The stats read is refreshed, because the total on the header line and
    /// every tile on the stats tab just changed.
    ///
    /// Android keeps its drawer open until the server answers and then closes
    /// it only if it is still the same track's (`TrackDrawer.kt:280`). This
    /// closes the sheet when the person confirms, which is what the rest of
    /// this screen does — `play in room` has closed on the tap since the
    /// first Music batch — and the toast reports either outcome. Both
    /// behaviours are honest; this one does not need a
    /// "is this still the sheet I opened" check to be so, and that check is
    /// the kind of thing that silently stops holding.
    func deleteTrack(_ track: LibraryTrack, alsoFile: Bool) {
        let id = track.id
        let label = track.displayTitle
        let path = alsoFile
            ? "/api/music/library/\(id)?also_file=true"
            : "/api/music/library/\(id)"
        let client = container.client
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                try await client.send(method: "DELETE", path: path)
                guard let self else { return }
                self.library.remove(id)
                self.stats.refresh()
                self.setToast(
                    alsoFile
                        ? MusicStrings.deletedWithFile(label)
                        : MusicStrings.removedFromLibrary(label)
                )
            } catch let trouble {
                self?.setToast(MusicStrings.deleteFailed(trouble.message))
            }
        }
    }
}
