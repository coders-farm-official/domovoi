import Foundation

/// Several phone sources behind one, so a screen sees one list of libraries.
///
/// Android has no equivalent because it needs none: one `PhoneFileSource`
/// holds the picked folders **and** the photo library in one object
/// (`PhoneFileSource.kt:98`). Here they are split — ``PhoneFolderSource`` is
/// `FileManager` and tests on a Mac, the photo library is PhotoKit and lives
/// in the app target (DECISIONS) — so something has to put them back
/// together, and which of the two a library id belongs to is a decision worth
/// testing.
///
/// The order of `parts` is the order of the libraries, and it is the
/// **screen's** choice, not this type's: the device side of Music wants the
/// picked folders (a photo library holds no audio), the device side of Videos
/// wants the photos first (that is where a phone's own videos are), and the
/// device side of Files wants the folders first with photos at the end, as
/// Android lists them.
///
/// ### Why routing asks every part
///
/// A library id is matched by asking each part what it has, in order, and
/// taking the first that claims it. Nothing is cached: ``libraries()`` on a
/// folder source is a bookmark resolution per folder and on a photo source is
/// one authorisation read, both cheap, and a phone has a handful of libraries
/// rather than a thousand. A cache here would have to be invalidated when a
/// folder is added or removed — and a stale route sends an upload's bytes to
/// the wrong library, which is the one failure that cannot be undone from the
/// phone.
public struct PhoneCompositeSource: PhoneMediaReading {

    private let parts: [any PhoneMediaReading]

    public init(_ parts: [any PhoneMediaReading]) {
        self.parts = parts
    }

    public func libraries() async -> [FileLibrary] {
        var out: [FileLibrary] = []
        var seen = Set<String>()
        for part in parts {
            for library in await part.libraries() where seen.insert(library.id).inserted {
                out.append(library)
            }
        }
        return out
    }

    public func page(_ request: PhoneMediaRequest) async throws -> PhoneMediaPage {
        guard let part = await owner(of: request.libraryID) else {
            // No part claims it: the folder was removed, or its bookmark
            // stopped resolving between the picker and this read. The screen
            // says so (``PhoneMediaStrings/folderNoLongerReadable``) rather
            // than drawing an empty folder.
            throw PhoneMediaError.unreadable(name: request.libraryID)
        }
        return try await part.page(request)
    }

    public func bytes(of item: PhoneMediaItem) async throws -> Data {
        guard let part = await owner(of: item.libraryID) else {
            throw PhoneMediaError.unreadable(name: item.name)
        }
        return try await part.bytes(of: item)
    }

    /// Routed, like everything else. **It has to be forwarded explicitly:**
    /// `fileURL(of:)` has a default implementation in a protocol extension,
    /// so without this line the composite would quietly take the default and
    /// spool every phone video through memory — the exact cost D43 removed,
    /// hidden behind a method that compiles and works.
    public func fileURL(of item: PhoneMediaItem) async throws -> PhoneUploadFile {
        guard let part = await owner(of: item.libraryID) else {
            throw PhoneMediaError.unreadable(name: item.name)
        }
        return try await part.fileURL(of: item)
    }

    private func owner(of libraryID: String) async -> (any PhoneMediaReading)? {
        for part in parts {
            if await part.libraries().contains(where: { $0.id == libraryID }) { return part }
        }
        return nil
    }
}

/// A phone source whose inner source can be replaced after a screen is
/// already reading it.
///
/// It exists because of one real bug caught while wiring the folder picker:
/// ``PhoneFolderSource`` is a value built from a **snapshot** of the folder
/// list, so a folder added through the picker was saved, listed by the
/// picker, and then did not appear on the page — the page's source still held
/// the list as it was when the page was built. Re-reading the libraries could
/// not find it, because the thing being re-read was the old snapshot.
///
/// A reference type is the fix, and the lock is not decoration: a page's
/// rows are read off the main actor while the picker replaces the inner
/// source on it.
///
/// The inner source is taken out **under the lock and then let go of**,
/// never held across the `await`: a lock held over a file read is a main
/// thread waiting on a disk.
public final class PhoneSwappableSource: PhoneMediaReading, @unchecked Sendable {

    private let lock = NSLock()
    private var inner: any PhoneMediaReading

    public init(_ inner: any PhoneMediaReading) {
        self.inner = inner
    }

    /// The folder list changed. Anything already in flight finishes against
    /// the source it started with, which is right: it was asked for.
    public func replace(with next: any PhoneMediaReading) {
        lock.withLock { inner = next }
    }

    private var current: any PhoneMediaReading { lock.withLock { inner } }

    public func libraries() async -> [FileLibrary] { await current.libraries() }

    public func page(_ request: PhoneMediaRequest) async throws -> PhoneMediaPage {
        try await current.page(request)
    }

    public func bytes(of item: PhoneMediaItem) async throws -> Data {
        try await current.bytes(of: item)
    }

    /// Forwarded for ``PhoneCompositeSource/fileURL(of:)``'s reason: a
    /// protocol extension's default would silently win here.
    public func fileURL(of item: PhoneMediaItem) async throws -> PhoneUploadFile {
        try await current.fileURL(of: item)
    }
}
