import Foundation
import XCTest
@testable import DomovoiKit

/// The upload side of D43: the file a row hands over, the envelope the Music
/// door still needs, and the arithmetic the ring draws.
///
/// None of these cases talks to a server. What is measured here is the part
/// that is **not** a round trip and is therefore the part a UI case cannot
/// pin: whether a copy is made, whether it is deleted, whether a streamed
/// envelope is byte-for-byte the one the in-memory builder produced, and what
/// a percentage does at its three edges.
final class TransferUploadTests: XCTestCase {

    // MARK: - the file a source hands over

    /// A source that answers bytes and nothing else gets the **default**
    /// ``PhoneMediaReading/fileURL(of:)``: a spooled copy, in the upload's own
    /// directory, deleted by its `release`.
    ///
    /// This is the path every source that has not overridden it takes — and
    /// the one `PhoneCompositeSource` and `PhoneSwappableSource` must *not*
    /// take, which is what `testAForwardingSourceDoesNotSpoolWhatItsChildWouldStream`
    /// below is about.
    func testASourceThatOnlyAnswersBytesGetsASpooledCopyThatIsDeleted() async throws {
        let source = BytesOnlySource(bytes: Data("a tune".utf8))
        let item = BytesOnlySource.item("quenlo lullaby.mp3")

        let handed = try await source.fileURL(of: item)
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: handed.url.path),
            "the upload is handed a file that exists: \(handed.url.path)"
        )
        XCTAssertEqual("a tune", try String(contentsOf: handed.url, encoding: .utf8))
        XCTAssertEqual(
            PhoneUploadSpool.name, handed.url.deletingLastPathComponent().lastPathComponent,
            "a copy goes in the upload's own directory, never the save's — the save empties its one"
        )
        XCTAssertEqual(
            "mp3", handed.url.pathExtension,
            "the extension is kept, because URLSession tells the server nothing about the body's name"
        )
        XCTAssertNotEqual(
            "quenlo lullaby.mp3", handed.url.lastPathComponent,
            "the stem is this app's, so nothing a phone or a server said can point anywhere"
        )

        handed.release()
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: handed.url.path),
            "release deletes a copy this app made"
        )
    }

    /// Two uploads in flight do not collide.
    ///
    /// The spool is **not** emptied on the way in (unlike the save's), because
    /// three rows may be on their way at once; what keeps them apart is the
    /// random stem. A fixed name would have been correct in every test with
    /// one upload in it.
    func testTwoFilesSpooledAtOnceAreTwoFiles() async throws {
        let source = BytesOnlySource(bytes: Data("one".utf8))
        let first = try await source.fileURL(of: BytesOnlySource.item("a.mp3"))
        let second = try await source.fileURL(of: BytesOnlySource.item("a.mp3"))
        XCTAssertNotEqual(first.url, second.url)
        XCTAssertTrue(FileManager.default.fileExists(atPath: first.url.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: second.url.path))
        first.release()
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: second.url.path),
            "one upload finishing must not delete another's file"
        )
        second.release()
    }

    /// A file this phone cannot read is refused **before** anything is sent,
    /// and with the name in it.
    func testAFileThatCannotBeReadIsNamedAndNothingIsSpooled() async {
        let source = BytesOnlySource(bytes: nil)
        do {
            _ = try await source.fileURL(of: BytesOnlySource.item("holiday.mov"))
            XCTFail("an unreadable file must not answer a URL")
        } catch let error as PhoneMediaError {
            XCTAssertEqual(
                "can't read \"holiday.mov\" on this phone",
                phoneUploadFailureText("ignored", error)
            )
        } catch {
            XCTFail("the wrong error: \(error)")
        }
    }

    /// **The forwarding trap.** A source that wraps another must forward
    /// `fileURL(of:)` explicitly.
    ///
    /// `PhoneMediaReading` has a protocol-extension default, and a default
    /// does not dispatch dynamically: a wrapper that leaves the requirement
    /// out gets the *default* rather than its child's override, silently, and
    /// every video on the phone goes through memory again — the ceiling back
    /// without a line of code saying so. There is no compiler error to catch
    /// it and nothing at runtime looks wrong, so this is the case that does.
    func testAForwardingSourceDoesNotSpoolWhatItsChildWouldStream() async throws {
        let child = StreamingSource()
        let composite = PhoneCompositeSource([child])
        let handed = try await composite.fileURL(of: StreamingSource.item)
        XCTAssertEqual(
            child.answer, handed.url,
            "the composite answered its own spooled copy instead of forwarding to the child"
        )
        XCTAssertTrue(child.asked, "the child was never asked")

        let swappable = PhoneSwappableSource(child)
        let again = try await swappable.fileURL(of: StreamingSource.item)
        XCTAssertEqual(child.answer, again.url, "the swappable source spooled instead of forwarding")
    }

    // MARK: - the envelope the Music door still needs

    /// The streamed envelope is **byte for byte** the one the in-memory
    /// builder makes.
    ///
    /// `PhoneMultipart` has two producers now and a multipart body is a thing
    /// that is either exactly right or answers 422 with no clue which of nine
    /// exact bytes it was. The in-memory one is covered by its own nine
    /// assertions in `PhoneMultipartTests`; this case says the one that writes
    /// to a file agrees with it, which is the cheapest possible guard against
    /// the two drifting.
    func testTheStreamedEnvelopeIsTheSameBytesAsTheBuiltOne() throws {
        let bytes = Data((0..<(3 * 1024 * 1024 + 7)).map { UInt8($0 % 251) })
        let fields = [
            (name: "device_id", value: "this-phone"),
            (name: "library_id", value: "core:music"),
            (name: "path", value: ""),
        ]
        let boundary = PhoneMultipart.boundary("fixed-for-this-case")

        let built = PhoneMultipart.body(
            fields: fields,
            fileName: "quenlo lullaby.mp3",
            mime: "audio/mpeg",
            bytes: bytes,
            boundary: boundary
        )

        let scratch = try PhoneUploadSpool.directory()
        let source = scratch.appendingPathComponent("source-\(UUID().uuidString).mp3")
        let out = scratch.appendingPathComponent("envelope-\(UUID().uuidString).body")
        try bytes.write(to: source, options: .atomic)
        defer {
            try? FileManager.default.removeItem(at: source)
            try? FileManager.default.removeItem(at: out)
        }

        let length = try PhoneMultipart.spool(
            fields: fields,
            fileName: "quenlo lullaby.mp3",
            mime: "audio/mpeg",
            file: source,
            boundary: boundary,
            to: out
        )

        let written = try Data(contentsOf: out)
        XCTAssertEqual(built, written, "the streamed envelope differs from the built one")
        XCTAssertEqual(
            Int64(built.count), length,
            "the length is what a caller reports progress against, so it must be the body's own"
        )
    }

    /// The envelope is written over whatever was there.
    ///
    /// Two music uploads in a row reuse nothing, but a crash between them can
    /// leave a part-written body on disk, and appending to it would send a
    /// file with two envelopes in it.
    func testASpooledEnvelopeReplacesAnOlderOne() throws {
        let scratch = try PhoneUploadSpool.directory()
        let source = scratch.appendingPathComponent("source-\(UUID().uuidString).mp3")
        let out = scratch.appendingPathComponent("envelope-\(UUID().uuidString).body")
        try Data("x".utf8).write(to: source, options: .atomic)
        try Data(count: 9_000).write(to: out, options: .atomic)
        defer {
            try? FileManager.default.removeItem(at: source)
            try? FileManager.default.removeItem(at: out)
        }

        let length = try PhoneMultipart.spool(
            fields: [(name: "library_id", value: "core:music")],
            fileName: "a.mp3",
            mime: "audio/mpeg",
            file: source,
            boundary: PhoneMultipart.boundary("replace"),
            to: out
        )
        XCTAssertEqual(Int64(try Data(contentsOf: out).count), length)
        XCTAssertLessThan(length, 9_000, "the old body was appended to rather than replaced")
    }

    /// A file bigger than the chunk the copy reads with comes through whole.
    ///
    /// The loop ends on nil **or** an empty `Data`, because `read(upToCount:)`
    /// answers one or the other at the end of a file depending on the
    /// platform; a loop that only checked for nil would have dropped the last
    /// chunk of some files and no others.
    func testAFileLongerThanOneChunkIsCopiedWhole() throws {
        let scratch = try PhoneUploadSpool.directory()
        let source = scratch.appendingPathComponent("source-\(UUID().uuidString).bin")
        let out = scratch.appendingPathComponent("envelope-\(UUID().uuidString).body")
        // Two chunks and a bit: 1 MiB is what the copy reads with, and what
        // `/upload-stream` writes with at the other end.
        let bytes = Data((0..<(2 * 1024 * 1024 + 13)).map { UInt8($0 % 97) })
        try bytes.write(to: source, options: .atomic)
        defer {
            try? FileManager.default.removeItem(at: source)
            try? FileManager.default.removeItem(at: out)
        }

        let boundary = PhoneMultipart.boundary("chunks")
        try PhoneMultipart.spool(
            fields: [],
            fileName: "big.bin",
            mime: "application/octet-stream",
            file: source,
            boundary: boundary,
            to: out
        )
        let written = try Data(contentsOf: out)
        XCTAssertTrue(written.contains(bytes), "the copy lost or reordered the file's bytes")
        XCTAssertTrue(
            String(decoding: written.suffix(80), as: UTF8.self).hasSuffix("--\(boundary)--\r\n"),
            "the closing delimiter is the boundary with `--` on both ends"
        )
    }

    // MARK: - the number the ring draws

    /// The percentage, at its edges.
    ///
    /// Every one of these comes from a real transport behaviour rather than
    /// from a wish to cover a function: a server may declare no length, a
    /// `Content-Length` may be a lie, and a bar that reads 100% while bytes
    /// are still moving is the one wrong answer a person actually notices.
    func testThePercentIsOptionalClampedAndRoundedDown() {
        XCTAssertEqual(0, transferPercent(sent: 0, of: 100))
        XCTAssertEqual(50, transferPercent(sent: 50, of: 100))
        XCTAssertEqual(100, transferPercent(sent: 100, of: 100))
        XCTAssertNil(transferPercent(sent: 10, of: 0), "nothing declared is not nought percent")
        XCTAssertNil(
            transferPercent(sent: 10, of: -1),
            "URLSession says -1 for an unknown total, which is not a total"
        )
        XCTAssertNil(transferPercent(sent: -1, of: 100))
        XCTAssertEqual(
            100, transferPercent(sent: 300, of: 100),
            "a total smaller than what was sent cannot draw 300%"
        )
        XCTAssertEqual(
            99, transferPercent(sent: 999, of: 1000),
            "rounded DOWN: 100% must mean finished"
        )
        XCTAssertEqual(
            0, transferPercent(sent: 1, of: 8 * 1024 * 1024 * 1024),
            "the first chunk of an eight-gigabyte file is 0%, not a rounding error"
        )
    }
}

// MARK: - the doubles

/// A source with `bytes(of:)` and **no** `fileURL(of:)`, so the default
/// applies.
private final class BytesOnlySource: PhoneMediaReading, @unchecked Sendable {

    private let bytes: Data?

    init(bytes: Data?) {
        self.bytes = bytes
    }

    static func item(_ name: String) -> PhoneMediaItem {
        PhoneMediaItem(
            id: name,
            libraryID: "phone:folder:/a",
            name: name,
            kind: PhoneMediaKind(fileName: name, isDir: false),
            sizeBytes: 1024,
            origin: .file(URL(fileURLWithPath: "/a/\(name)"))
        )
    }

    func libraries() async -> [FileLibrary] { [] }

    func page(_ request: PhoneMediaRequest) async throws -> PhoneMediaPage {
        phoneMediaSlice([], request.page)
    }

    func bytes(of item: PhoneMediaItem) async throws -> Data {
        guard let bytes else { throw PhoneMediaError.unreadable(name: item.name) }
        return bytes
    }
}

/// A source that overrides `fileURL(of:)`, so a wrapper that forgets to
/// forward is visible.
private final class StreamingSource: PhoneMediaReading, @unchecked Sendable {

    /// The URL only this source would answer. Not a real file: nothing in the
    /// forwarding case reads it, and a source that streams in place answers
    /// somebody else's file anyway.
    let answer = URL(fileURLWithPath: "/a/streamed-in-place.mov")
    private(set) var asked = false

    static let item = PhoneMediaItem(
        id: "holiday.mov",
        libraryID: "phone:folder:/a",
        name: "holiday.mov",
        kind: .video,
        sizeBytes: 4 * 1024 * 1024 * 1024,
        origin: .file(URL(fileURLWithPath: "/a/holiday.mov"))
    )

    func libraries() async -> [FileLibrary] {
        [FileLibrary(id: "phone:folder:/a", label: "A", kind: "folder", icon: "folder")]
    }

    func page(_ request: PhoneMediaRequest) async throws -> PhoneMediaPage {
        phoneMediaSlice([Self.item], request.page)
    }

    func bytes(of item: PhoneMediaItem) async throws -> Data {
        XCTFail("a streaming source's bytes must never be read for an upload")
        return Data()
    }

    func fileURL(of item: PhoneMediaItem) async throws -> PhoneUploadFile {
        asked = true
        return .asItIs(answer)
    }
}
