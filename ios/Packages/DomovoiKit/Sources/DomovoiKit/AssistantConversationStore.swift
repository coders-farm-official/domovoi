import Foundation

/// The conversations kept on this phone: one JSON file each, in the app's own
/// Application Support directory, outside every backup
/// (`ConversationStore`, `ConversationStore.kt:228`).
///
/// ### Where, and why there
///
/// Android's is `filesDir/assistant/` — app-private, excluded from backup by
/// the app's backup rules, gone on uninstall. The iOS twin of "app-private and
/// gone on uninstall" is the container itself, so the only decision left is
/// *which* container directory, and it is **Application Support**, not Caches
/// and not Documents: Caches is the one iOS may empty under pressure, and
/// Documents is what a file provider would show the person. The directory is
/// then marked `isExcludedFromBackup`, which is the twin of the backup rules
/// — a person's chats with a model on their phone have no business in iCloud,
/// and this milestone is the first thing in the app to keep anything a
/// household server has never seen.
///
/// ### The write
///
/// Temporary file, then an atomic replace, as the Kotlin does
/// (`AtomicFiles.replace`): a process killed mid-write leaves the previous
/// version, never half a file. `FileManager.replaceItemAt` is the API for
/// it — **and it fails when there is nothing to replace**, which is every
/// first save of every conversation, so a destination that does not exist is
/// a plain move. That is the one line of this type that is not a port of
/// anything and it is the line a test would miss by only ever saving twice.
///
/// ### The read
///
/// A file that cannot be decoded is **left out of the list** rather than
/// breaking it, which is the Kotlin's `runCatching{}.getOrNull()`. With every
/// field tolerated on the way in (``AssistantConversation/init(from:)``) the
/// only files that can fail are truncated or not JSON at all — and those are
/// exactly the ones a half-finished write used to leave, before the atomic
/// one.
///
/// `@unchecked Sendable` with the reason: it stores a `URL` and a coder, all
/// `let`, and holds nothing across a suspension because no method here is
/// `async`. The file system is the shared state, and it is the file system's
/// own lock that serializes it.
public final class AssistantConversationStore: @unchecked Sendable {

    /// `^[a-z0-9-]{1,64}$` (`ConversationStore.kt:290`). A file name comes
    /// from this and nothing else, so a conversation id can never walk out of
    /// the directory.
    public static func isValidId(_ id: String) -> Bool {
        guard !id.isEmpty, id.count <= 64 else { return false }
        return id.allSatisfy { $0.isASCII && ($0.isLowercase && $0.isLetter || $0.isNumber || $0 == "-") }
    }

    /// Sortable by time, unique enough for one phone: `c-<ms>-<base36>`
    /// (`newId`, `ConversationStore.kt:293`).
    public static func newId(_ now: Int64, random: Int = Int.random(in: 0..<(1 << 30))) -> String {
        "c-\(now)-\(String(random, radix: 36))"
    }

    /// The directory the conversations live in, made on first save.
    public let directory: URL

    private let encoder: JSONEncoder
    private let decoder = JSONDecoder()
    private let files = FileManager.default

    /// - Parameter directory: where the files go. Defaults to
    ///   `Application Support/assistant/`; a test passes a temporary
    ///   directory, which is the whole reason this is a parameter.
    public init(directory: URL? = nil) {
        self.directory = directory ?? Self.defaultDirectory()
        let encoder = JSONEncoder()
        // Readable by a person with a text editor, and stable between two
        // writes of the same conversation — a diff of two saves should be the
        // message that was added.
        encoder.outputFormatting = [.sortedKeys]
        self.encoder = encoder
    }

    /// `Application Support/assistant/`, and `.../assistant/` under the
    /// temporary directory if the container has no Application Support at all
    /// (which no iPhone does; a command-line test host can).
    public static func defaultDirectory() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return base.appendingPathComponent("assistant", isDirectory: true)
    }

    /// Every readable conversation, newest first (`list`,
    /// `ConversationStore.kt:239`).
    public func list() -> [AssistantConversationSummary] {
        all().map(\.summary).sorted {
            if $0.updated != $1.updated { return $0.updated > $1.updated }
            return $0.id > $1.id
        }
    }

    /// Every readable conversation, whole — search and sharing
    /// (`all`, `ConversationStore.kt:245`).
    public func all() -> [AssistantConversation] {
        jsonFiles().compactMap { read($0) }
    }

    /// The conversations whose title or text contains `query`
    /// (`assistantSearchConversations(_:query:)`).
    public func search(_ query: String) -> [AssistantConversationHit] {
        assistantSearchConversations(all(), query: query)
    }

    public func load(_ id: String) -> AssistantConversation? {
        guard Self.isValidId(id) else { return nil }
        return read(file(id))
    }

    /// Writes `conversation` out, temporary file first.
    ///
    /// Returns whether it was written. The Kotlin's is `Unit` and throws on an
    /// invalid id; here an unwritable disk is a sentence on screen
    /// (``AssistantPhoneModel``), so the answer comes back instead of being
    /// thrown past the caller that has to say it.
    @discardableResult
    public func save(_ conversation: AssistantConversation) -> Bool {
        guard Self.isValidId(conversation.id) else { return false }
        guard makeDirectory() else { return false }
        let target = file(conversation.id)
        let tmp = directory.appendingPathComponent("\(conversation.id).json.tmp")
        guard let blob = try? encoder.encode(conversation) else { return false }
        do {
            try blob.write(to: tmp, options: .atomic)
            if files.fileExists(atPath: target.path) {
                _ = try files.replaceItemAt(target, withItemAt: tmp)
            } else {
                // `replaceItemAt` needs something to replace: every first save
                // of every conversation lands here.
                try files.moveItem(at: tmp, to: target)
            }
            return true
        } catch {
            try? files.removeItem(at: tmp)
            return false
        }
    }

    /// Deletes one conversation. An id that was never saved is a success, as
    /// the Kotlin's `!it.exists() || it.delete()` is: "it is not there" is
    /// what the caller asked for.
    @discardableResult
    public func delete(_ id: String) -> Bool {
        guard Self.isValidId(id) else { return true }
        let target = file(id)
        guard files.fileExists(atPath: target.path) else { return true }
        return (try? files.removeItem(at: target)) != nil
    }

    /// Deletes every conversation, and any leftover temporary file
    /// (`clearAll`, `ConversationStore.kt:271`).
    @discardableResult
    public func clearAll() -> Bool {
        let names = (try? files.contentsOfDirectory(atPath: directory.path)) ?? []
        var allGone = true
        for name in names where name.hasSuffix(".json") || name.hasSuffix(".json.tmp") {
            let url = directory.appendingPathComponent(name)
            if (try? files.removeItem(at: url)) == nil { allGone = false }
        }
        return allGone
    }

    /// A new, empty conversation — **not saved** until it has a message, so a
    /// new chat left alone leaves nothing behind (`create`,
    /// `ConversationStore.kt:277`).
    public func create(now: Int64) -> AssistantConversation {
        AssistantConversation(id: Self.newId(now), created: now, updated: now)
    }

    // MARK: - the disk

    private func file(_ id: String) -> URL {
        directory.appendingPathComponent("\(id).json")
    }

    /// Makes the directory and marks it out of backups. The mark is set every
    /// time rather than once: it is a resource value on the directory, and a
    /// directory restored from a backup of an older build would arrive without
    /// it.
    private func makeDirectory() -> Bool {
        do {
            if !files.fileExists(atPath: directory.path) {
                try files.createDirectory(at: directory, withIntermediateDirectories: true)
            }
            var url = directory
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try? url.setResourceValues(values)
            return true
        } catch {
            return false
        }
    }

    private func jsonFiles() -> [URL] {
        let names = (try? files.contentsOfDirectory(atPath: directory.path)) ?? []
        return names
            .filter { $0.hasSuffix(".json") && Self.isValidId(String($0.dropLast(5))) }
            .map { directory.appendingPathComponent($0) }
    }

    private func read(_ url: URL) -> AssistantConversation? {
        guard let blob = try? Data(contentsOf: url) else { return nil }
        guard var conversation = try? decoder.decode(AssistantConversation.self, from: blob) else { return nil }
        // The file's name is the id; a file with no `id` field still opens
        // (`ConversationStore.kt:285`).
        let name = String(url.lastPathComponent.dropLast(5))
        if conversation.id != name { conversation.id = name }
        return conversation
    }
}
