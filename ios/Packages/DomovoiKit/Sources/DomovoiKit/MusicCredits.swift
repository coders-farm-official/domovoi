import Foundation

/// The artists a track credits beyond the one its artist line already names
/// (a track's `artists`, in the file's order, V059): what the library shows,
/// quietly, as "with B, C".
///
/// The web's `creditedWith` (`web/static/components.jsx`) and Android's
/// `Credits.kt`, word for word: a name is left out when it is blank or
/// already appears, in any case, anywhere in the artist line ("Main feat.
/// Guest" leaves Guest out); the rest are joined with ", " however many
/// there are (no "and"). "" when there are none.
///
/// Shown after the artist on a library row and a playlist's song rows
/// ("A · with B, C"), and on a line of its own under the artist in the
/// track sheet — where the web and Android show it.
public enum MusicCredits {

    /// The word before the names.
    public static let lead = "with "

    /// Between two names.
    public static let joiner = ", "

    /// Between the artist and the credits on a row.
    public static let separator = " · "

    public static func with(artist: String?, artists: [String]) -> String {
        let shown = (artist ?? "").lowercased()
        let extra = artists.filter { (name: String) -> Bool in
            let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
            return !trimmed.isEmpty && !shown.contains(trimmed.lowercased())
        }
        return extra.isEmpty ? "" : lead + extra.joined(separator: joiner)
    }

    /// ``with(artist:artists:)`` for a library row.
    public static func with(_ track: LibraryTrack) -> String {
        with(artist: track.artist, artists: track.artists)
    }

    /// A row's artist line: `artist`, then " · " and the credits when there
    /// are any.
    public static func artistLine(_ artist: String, credits: String) -> String {
        credits.isEmpty ? artist : artist + separator + credits
    }
}
