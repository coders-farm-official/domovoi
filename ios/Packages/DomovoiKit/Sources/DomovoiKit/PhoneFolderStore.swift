import Foundation

/// A folder on this phone that the person handed the app through the document
/// picker.
///
/// `data/PhoneFolders.kt:14`'s `PhoneFolder`. Android keeps the Storage
/// Access Framework **tree uri** and holds a persisted read+write grant on
/// it; iOS keeps a **security-scoped bookmark**, which is the same idea in
/// the same words — a token that outlives the pick and can be resolved back
/// to a URL the sandbox will open.
///
/// ### Why there is a `path` beside the bookmark
///
/// Android dedupes, removes and names libraries by the uri, which is a stable
/// string. A bookmark's **bytes are not** a stable identity: resolving one
/// can hand back a new, relocated bookmark, so two `Data` values can name one
/// folder. So the folder's path at the moment it was picked is kept beside
/// the bookmark and is what this list keys on — `uri`'s role, under the one
/// name iOS has for it.
public struct PhoneFolder: Codable, Equatable, Sendable, Identifiable {

    /// The folder's path when it was picked. The list's key, the library id's
    /// tail, and what a "you already added this" check compares.
    public let path: String

    /// What the picker called it, shown as the library's label
    /// (`PhoneFolders.kt:14`).
    public let name: String

    /// The security-scoped bookmark the sandbox resolves. Opaque here: this
    /// type never resolves one, which is what keeps it testable on a Mac with
    /// no picker.
    public let bookmark: Data

    public var id: String { path }

    public init(path: String, name: String, bookmark: Data = Data()) {
        self.path = path
        self.name = name
        self.bookmark = bookmark
    }
}

/// The list logic behind the saved `phone_folders` value, kept free of the
/// file system so it unit-tests — `PhoneFolders.kt:26`'s object, function for
/// function.
///
/// The list is what the person chose; what the sandbox will still open is a
/// separate question, asked by whoever can resolve a bookmark and answered
/// into ``stillResolvable(_:resolvable:)``. That split is Android's exactly:
/// there, `stillGranted` takes the set of uris the platform still holds a
/// grant for rather than asking the platform itself (`PhoneFolders.kt:46`).
///
/// **Taking a folder off this list never touches the files in it**
/// (`PhoneFolders.kt:44`), and ``PhoneMediaStrings/removingKeepsTheFiles``
/// says so on screen.
public enum PhoneFolderList {

    /// The Files library id for a folder, and for the photo library
    /// (`PhoneFolders.kt:49`, `:50`). The same two strings as Android, so a
    /// person's two phones name the same library the same way.
    public static let libraryPrefix = "phone:folder:"
    public static let photosID = "phone:photos"

    /// `PhoneFolders.kt:52`.
    public static func libraryID(of folder: PhoneFolder) -> String { libraryPrefix + folder.path }

    /// `PhoneFolders.kt:54`. Nil for any id that is not a folder's.
    public static func pathOf(libraryID: String) -> String? {
        guard libraryID.hasPrefix(libraryPrefix) else { return nil }
        return String(libraryID.dropFirst(libraryPrefix.count))
    }

    /// `PhoneFolders.kt:30`: blank paths dropped, deduped by path, **and a
    /// corrupt blob decodes to an empty list** rather than throwing. A stored
    /// value this app cannot read must not be able to keep the Files tab from
    /// opening.
    public static func decode(_ raw: String?) -> [PhoneFolder] {
        guard let raw, !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let data = raw.data(using: .utf8),
              let decoded = try? JSONDecoder().decode([PhoneFolder].self, from: data)
        else { return [] }
        var seen = Set<String>()
        return decoded.filter { folder in
            guard !folder.path.isEmpty, !seen.contains(folder.path) else { return false }
            seen.insert(folder.path)
            return true
        }
    }

    /// `PhoneFolders.kt:36`. An empty list encodes as `[]`, not as nothing:
    /// the person removing their last folder has to be storable, or the next
    /// read would bring it back.
    public static func encode(_ list: [PhoneFolder]) -> String {
        guard let data = try? JSONEncoder().encode(list),
              let text = String(data: data, encoding: .utf8) else { return "[]" }
        return text
    }

    /// Append `folder`, or **replace it in place** when its path is already
    /// listed (`PhoneFolders.kt:39`). Picking the same folder again is how a
    /// person renews a bookmark that stopped resolving, so the new bytes have
    /// to win without moving the row.
    public static func with(_ list: [PhoneFolder], _ folder: PhoneFolder) -> [PhoneFolder] {
        guard list.contains(where: { $0.path == folder.path }) else { return list + [folder] }
        return list.map { $0.path == folder.path ? folder : $0 }
    }

    /// `PhoneFolders.kt:42`. Never touches the files.
    public static func without(_ list: [PhoneFolder], path: String) -> [PhoneFolder] {
        list.filter { $0.path != path }
    }

    /// Only the folders the sandbox will still open, by path
    /// (`PhoneFolders.kt:46`'s `stillGranted`).
    ///
    /// The caller resolves the bookmarks and passes the paths that came back.
    /// A folder that drops out here is one whose volume has gone, that was
    /// deleted, or whose bookmark the system discarded.
    public static func stillResolvable(_ list: [PhoneFolder], resolvable: Set<String>) -> [PhoneFolder] {
        list.filter { resolvable.contains($0.path) }
    }
}
