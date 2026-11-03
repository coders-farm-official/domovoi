import XCTest
@testable import DomovoiKit

/// ``PhoneFolderSource`` against a real temporary directory, which is the
/// point of keeping it pure Foundation (``PhoneMediaSource``'s own doc).
///
/// It is also the only test that reaches ``PhoneMediaKind/init(fileName:isDir:)``
/// on the path the app actually uses: ``PhoneMemorySource`` and
/// `PhoneMediaFake.seeded` all pass `kind:` explicitly, so the classifier had
/// no cover at all and an `mp4` listed as a track on the device side of Music
/// went unnoticed through a whole review.
final class PhoneFolderSourceTests: XCTestCase {

    private var root: URL!
    private var folder: PhoneFolder!
    private var source: PhoneFolderSource!

    override func setUpWithError() throws {
        root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("phonefolder-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        folder = PhoneFolder(path: root.path, name: "Camera")
        let here = root!
        source = PhoneFolderSource(folders: [folder], resolve: { _ in here })
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    private func write(_ name: String, bytes: Int = 3) throws {
        try Data(repeating: 0x7a, count: bytes)
            .write(to: root.appendingPathComponent(name))
    }

    private func page(kinds: Set<PhoneMediaKind> = [], limit: Int = 50) async throws -> PhoneMediaPage {
        try await source.page(PhoneMediaRequest(
            libraryID: PhoneFolderList.libraryID(of: folder),
            kinds: kinds,
            page: PageRequest(cursor: nil, offset: 0, limit: limit)
        ))
    }

    // MARK: - what a name on this phone is

    /// **The `mp4` case.** `mp4` is in both of ``FileKinds``' extension sets
    /// and the server's listing calls it audio (audio first,
    /// `FileKinds.swift:104`). On a page narrowed by kind that order sends
    /// every phone-camera clip to Music and hides it from Videos, so the
    /// phone's classifier answers `video` — the same exception
    /// ``phoneDefaultHomeLibrary(_:_:)`` already makes.
    func testAnMp4IsAVideoOnThePhoneNotATrack() async throws {
        try write("clip.mp4")
        try write("song.mp3")
        try write("notes.txt")

        let videos = try await page(kinds: PhoneMediaKind.videoOnly)
        XCTAssertEqual(videos.items.map(\.name), ["clip.mp4"])

        let music = try await page(kinds: PhoneMediaKind.audioOnly)
        XCTAssertEqual(music.items.map(\.name), ["song.mp3"])
    }

    /// The rest of the precedence is the server's, unchanged: `.m4a` audio,
    /// `.jpg` image, `.txt` doc-text, an unknown extension `other`.
    func testTheOtherKindsAreStillTheServersAnswer() async throws {
        try write("book.m4a")
        try write("snap.jpg")
        try write("notes.txt")
        try write("thing.zzz")

        let all = try await page()
        let byName = Dictionary(uniqueKeysWithValues: all.items.map { ($0.name, $0.kind) })
        XCTAssertEqual(byName["book.m4a"], .audio)
        XCTAssertEqual(byName["snap.jpg"], .image)
        XCTAssertEqual(byName["notes.txt"], .docText)
        XCTAssertEqual(byName["thing.zzz"], .other)
    }

    // MARK: - listing

    /// A folder stays on a kind-narrowed page: narrowing must not hide the
    /// way down to the files of that kind (the `page` loop's own comment).
    func testAFolderStaysOnAKindNarrowedPage() async throws {
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("Trip", isDirectory: true),
            withIntermediateDirectories: false
        )
        try write("notes.txt")

        let videos = try await page(kinds: PhoneMediaKind.videoOnly)
        XCTAssertEqual(videos.items.map(\.name), ["Trip"])
        XCTAssertTrue(videos.items[0].isDir)
    }

    /// Folders first, then by name case-insensitively.
    func testFoldersSortFirstThenNames() async throws {
        try write("b.txt")
        try write("A.txt")
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("zed", isDirectory: true),
            withIntermediateDirectories: false
        )

        let all = try await page()
        XCTAssertEqual(all.items.map(\.name), ["zed", "A.txt", "b.txt"])
    }

    /// The whole directory is listed and then sliced, so `total` is the
    /// folder's own count and `serverHasMore` says there is a second page.
    func testThePageIsAWindowOverTheWholeFolder() async throws {
        for index in 0..<5 { try write("f\(index).txt") }

        let first = try await page(limit: 2)
        XCTAssertEqual(first.items.count, 2)
        XCTAssertEqual(first.total, 5)
        XCTAssertTrue(first.serverHasMore)
    }

    /// A path with `..` in it is refused rather than resolved — the escape the
    /// component-at-a-time loop exists to stop.
    func testAPathCannotClimbOutOfThePickedFolder() async throws {
        do {
            _ = try await source.page(PhoneMediaRequest(
                libraryID: PhoneFolderList.libraryID(of: folder),
                path: "../..",
                page: PageRequest(cursor: nil, offset: 0, limit: 50)
            ))
            XCTFail("a `..` path was listed")
        } catch {
            XCTAssertTrue(error is PhoneMediaError)
        }
    }

    /// A library whose bookmark no longer resolves throws rather than
    /// answering an empty folder, which is what draws
    /// ``PhoneMediaStrings/folderNoLongerReadable``.
    func testAnUnresolvableFolderThrows() async throws {
        let gone = PhoneFolderSource(folders: [folder], resolve: { _ in nil })
        do {
            _ = try await gone.page(PhoneMediaRequest(
                libraryID: PhoneFolderList.libraryID(of: folder),
                page: PageRequest(cursor: nil, offset: 0, limit: 50)
            ))
            XCTFail("an unresolvable folder listed")
        } catch {
            XCTAssertTrue(error is PhoneMediaError)
        }
    }
}
