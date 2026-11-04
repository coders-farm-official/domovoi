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

    override func setUp() {
        super.setUp()
        // The socket cases below share one recorder with every other file
        // that uses it. Reset, as `DomovoiClientTests` does.
        StubURLProtocol.reset()
    }

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

    // MARK: - the two tails, against a socket that records what left

    /// **Why this section exists.** REVIEW-1 finding 2: nothing in the
    /// repository exercised ``DomovoiClient/uploadFile(method:path:fileURL:contentType:trackPairing:onProgress:)``
    /// or ``DomovoiClient/downloadFile(path:into:trackPairing:onProgress:)``
    /// at all, so deleting the `X-Requested-With` header from either left the
    /// whole suite green while every phone upload would have 403'd against
    /// the real door (`/api/files/upload-stream` is `require_device` **and**
    /// `require_requested_with`, `web/backend/api/files.py:1189-1193`). The
    /// same gap left both delegates' redirect forwarding, the streamed
    /// `noteTokenHealth` and the bounded refusal-body read unexercised.
    ///
    /// ``StubURLProtocol`` reaches no network, as everywhere else on this
    /// track, and it records what *left* the phone rather than only what came
    /// back — which is the only position from which a missing header is
    /// visible.
    private var server: String { "http://10.0.0.9:6369" }

    private func makeClient(health: TokenHealth? = nil) -> DomovoiClient {
        DomovoiClient(
            book: .fixed(server: server, token: "household-abc"),
            gate: AdmitsAnyServer(),
            health: health,
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session()
        )
    }

    /// A file on disk with `bytes` in it, in a directory deleted when the
    /// case ends.
    private func temporaryFile(_ bytes: Data, named name: String = "clip.mov") throws -> URL {
        let file = try temporaryDirectory().appendingPathComponent(name, isDirectory: false)
        try bytes.write(to: file, options: .atomic)
        return file
    }

    private func temporaryDirectory() throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("transfer-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        return directory
    }

    /// `XCTAssertThrowsError` has no async form; `DomovoiClientTests` uses the
    /// same shape.
    private func failure<T>(_ body: () async throws -> T) async -> DomovoiError? {
        do {
            _ = try await body()
            return nil
        } catch let error as DomovoiError {
            return error
        } catch {
            XCTFail("not a DomovoiError: \(error)")
            return nil
        }
    }

    /// **The header the real door requires, and the body that really streamed.**
    ///
    /// Four claims about one request: the identity header the route checks,
    /// the household token, the content type the caller chose, and the file's
    /// own bytes arriving as the body (`URLSession` hands a `URLProtocol` the
    /// upload as a stream, which ``StubURLProtocol`` reads back — see the note
    /// on its `body(of:)`).
    func testTheStreamedUploadCarriesTheHeadersTheRealDoorRequires() async throws {
        StubURLProtocol.enqueue(.json(#"{"saved":["clip.mov"]}"#))
        let sent = Data("a clip, longer than nothing".utf8)
        let file = try temporaryFile(sent)
        let answer = try await makeClient().uploadFile(
            method: "POST",
            path: "/api/files/upload-stream?library_id=core%3Adocuments",
            fileURL: file,
            contentType: "video/quicktime"
        )

        XCTAssertEqual(1, StubURLProtocol.requestCount)
        XCTAssertEqual(
            "DomovoiApp", StubURLProtocol.header("X-Requested-With", at: 0),
            "without this header the real upload-stream route refuses every phone upload"
        )
        XCTAssertEqual(
            "household-abc", StubURLProtocol.header(deviceTokenHeader, at: 0),
            "the streamed tail goes through prepare(), so it is paired like every other request"
        )
        XCTAssertEqual("video/quicktime", StubURLProtocol.header("Content-Type", at: 0))
        XCTAssertEqual("POST", StubURLProtocol.recorded[0].httpMethod)
        XCTAssertEqual(
            sent, StubURLProtocol.recorded[0].httpBody,
            "the body is the FILE: a request that sent nothing would pass every other assertion here"
        )
        XCTAssertEqual(#"{"saved":["clip.mov"]}"#, String(decoding: answer, as: UTF8.self))
    }

    /// The same two headers on the save, which the same route rules cover,
    /// and the file where the caller asked for it.
    func testTheStreamedSaveCarriesTheHeadersAndWritesWhereItWasAsked() async throws {
        StubURLProtocol.enqueue(.json("the film's bytes"))
        let into = try temporaryDirectory().appendingPathComponent("film.mkv", isDirectory: false)
        let landed = try await makeClient()
            .downloadFile(path: "/api/files/download?path=film.mkv", into: into)

        XCTAssertEqual("DomovoiApp", StubURLProtocol.header("X-Requested-With", at: 0))
        XCTAssertEqual("household-abc", StubURLProtocol.header(deviceTokenHeader, at: 0))
        XCTAssertEqual(into, landed)
        XCTAssertEqual("the film's bytes", try String(contentsOf: landed, encoding: .utf8))
    }

    /// **The upload's redirect forwarding.** A hop to another host goes out
    /// bare.
    ///
    /// This is the assertion that fails if ``TransferSendDelegate``'s
    /// `willPerformHTTPRedirection` stops forwarding to `RedirectDelegate`'s
    /// `followUp(response:)`: with no delegate method at all the platform
    /// follows the hop by itself and **copies the headers across**, so the
    /// household token would reach whatever host the answer named. Security
    /// round 3 found that on both apps.
    func testAnUploadRedirectedToAnotherHostDoesNotCarryTheToken() async throws {
        StubURLProtocol.enqueue(
            .moved(302, to: "http://10.0.0.10:6369/grab.mov"),
            .json("{}")
        )
        let file = try temporaryFile(Data("a clip".utf8))
        _ = try await makeClient().uploadFile(
            method: "POST",
            path: "/api/files/upload-stream",
            fileURL: file,
            contentType: "video/quicktime"
        )
        XCTAssertEqual(2, StubURLProtocol.requestCount)
        XCTAssertEqual("household-abc", StubURLProtocol.header(deviceTokenHeader, at: 0))
        XCTAssertNil(
            StubURLProtocol.header(deviceTokenHeader, at: 1),
            "the host it was sent on to sees no token"
        )
    }

    /// **The save's redirect forwarding**, and the cleartext rule with it: a
    /// hop to a public plain-HTTP host is refused before anything connects,
    /// and no half-file is left under the name the person would be handed.
    func testASaveRedirectedToAPublicPlainHostIsRefusedAndLeavesNoFile() async throws {
        StubURLProtocol.enqueue(.moved(302, to: "http://203.0.113.5/grab.mkv"))
        let into = try temporaryDirectory().appendingPathComponent("film.mkv", isDirectory: false)
        let error = await failure {
            try await makeClient().downloadFile(path: "/api/files/download?path=film.mkv", into: into)
        }
        XCTAssertEqual(error, .notPermitted(CleartextPolicy.refusalMessage("203.0.113.5")))
        XCTAssertEqual(1, StubURLProtocol.requestCount, "nothing reached the host it named")
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: into.path),
            "a refused save left a file where the share sheet would have read one"
        )
    }

    /// A refused save reads its reason out of the body, tells the health
    /// observer, and leaves nothing behind.
    ///
    /// The body of a download is written to disk like any other, so a refusal
    /// is a file and not a `Data`: a prefix of it is read for the message and
    /// the file is dropped.
    func testARefusedSaveSaysWhyWithoutKeepingTheFile() async throws {
        StubURLProtocol.enqueue(
            .json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401)
        )
        let health = TokenHealth()
        let client = makeClient(health: health)
        let into = try temporaryDirectory().appendingPathComponent("film.mkv", isDirectory: false)
        let error = await failure {
            try await client.downloadFile(path: "/api/files/download?path=film.mkv", into: into)
        }
        guard case .refused(let refusal) = error else {
            return XCTFail("not refused: \(String(describing: error))")
        }
        XCTAssertEqual(401, refusal.status)
        XCTAssertTrue(refusal.deviceTokenRequired, "the reason was read out of the body")
        XCTAssertTrue(client.pairingRequired)
        XCTAssertTrue(
            health.isRejected(server),
            "the streamed tail observes token health like every other request"
        )
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: into.path),
            "the refusal's own body was left on disk as if it were the person's file"
        )
    }

    /// A refusal **longer** than the bound is read only as far as the bound.
    ///
    /// A hostile or broken server answering with a megabyte of body must not
    /// put that megabyte into a sentence — the same rule, and the same
    /// number, as the events tail.
    func testAVastRefusalBodyIsReadOnlyAsFarAsTheBound() async throws {
        let limit = DomovoiClient.refusalBodyLimit
        StubURLProtocol.enqueue(
            Answered.vast(status: 403, bytes: limit * 4)
        )
        let into = try temporaryDirectory().appendingPathComponent("film.mkv", isDirectory: false)
        let error = await failure {
            try await makeClient().downloadFile(path: "/api/files/download?path=film.mkv", into: into)
        }
        guard case .refused(let refusal) = error else {
            return XCTFail("not refused: \(String(describing: error))")
        }
        XCTAssertEqual(403, refusal.status)
        XCTAssertLessThan(
            refusal.message.count, limit * 2,
            "the whole body became the sentence"
        )
    }

    /// **Both delegates forward the progress they are given**, which is the
    /// other half of what nothing exercised.
    ///
    /// Not arithmetic — ``transferPercent(sent:of:)`` is tested above — but
    /// that the callback is wired to the delegate at all: a
    /// `didSendBodyData` or `didWriteData` that stopped forwarding would
    /// leave every ring indeterminate and nothing else in the project would
    /// notice.
    ///
    /// **Called directly rather than through a request**, measured here
    /// 2026-11-03: a `URLProtocol` stub answers out of `startLoading`, and
    /// the loading system reports no `didSendBodyData` for it at all — a case
    /// that uploaded half a mebibyte through ``StubURLProtocol`` and waited
    /// for a tick got none, so it would have failed whether or not the
    /// forwarding was there. The numbers from a real socket are the UI
    /// cases' to assert (`testTransfers_ABigPhoneFileGoesThroughTheStreamedDoor`
    /// watches the drawn percentage against the stub house); what is left for
    /// a package test is the two lines of forwarding, and this is them. The
    /// tasks below are never resumed and reach nothing.
    func testBothTransferDelegatesForwardTheProgressTheyAreGiven() throws {
        let configuration = StubURLProtocol.session()
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let url = URL(string: "\(server)/api/files/upload-stream")!

        let sent = Shared<[Int64]>([])
        let send = TransferSendDelegate(
            hops: redirects(from: url),
            onProgress: { bytes, _ in sent.mutate { $0.append(bytes) } }
        )
        send.urlSession(
            session,
            task: session.dataTask(with: url),
            didSendBodyData: 1024,
            totalBytesSent: 4096,
            totalBytesExpectedToSend: 8192
        )
        XCTAssertEqual(
            [4096], sent.current,
            "the ring is drawn from totalBytesSent, never from the size of one chunk"
        )

        let written = Shared<[Int64]>([])
        let receive = TransferReceiveDelegate(
            hops: redirects(from: url),
            destination: try temporaryDirectory().appendingPathComponent("film.mkv"),
            onProgress: { bytes, _ in written.mutate { $0.append(bytes) } }
        )
        receive.urlSession(
            session,
            downloadTask: session.downloadTask(with: url),
            didWriteData: 2048,
            totalBytesWritten: 6144,
            totalBytesExpectedToWrite: 8192
        )
        XCTAssertEqual([6144], written.current)
    }

    /// A ``RedirectDelegate`` for a request to the active server, built the
    /// way `RedirectDelegateTests` builds one.
    private func redirects(from url: URL) -> RedirectDelegate {
        var request = URLRequest(url: url)
        request.setValue("household-abc", forHTTPHeaderField: deviceTokenHeader)
        return RedirectDelegate(start: request, wsUpgrade: false) { _ in
            .send(token: "household-abc", server: self.server)
        }
    }
}

/// A long refusal body, built without writing the string twice.
private enum Answered {
    static func vast(status: Int, bytes: Int) -> StubURLProtocol.Answer {
        StubURLProtocol.Answer(
            status: status,
            body: Data(("{\"detail\":\"" + String(repeating: "x", count: bytes) + "\"}").utf8)
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
