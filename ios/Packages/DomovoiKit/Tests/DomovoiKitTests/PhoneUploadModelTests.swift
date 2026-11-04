import Foundation
import XCTest
@testable import DomovoiKit

/// What the model did, recorded from outside it.
private final class Posts: @unchecked Sendable {
    private let lock = NSLock()
    private var paths: [String] = []
    private var bodies: [String] = []

    func record(_ path: String, _ body: Data) {
        lock.withLock {
            paths.append(path)
            bodies.append(String(decoding: body, as: UTF8.self))
        }
    }

    var count: Int { lock.withLock { paths.count } }
    var lastPath: String? { lock.withLock { paths.last } }
    var lastBody: String { lock.withLock { bodies.last ?? "" } }
}

private func library(_ id: String, _ label: String, editable: Bool = true) -> FileLibrary {
    FileLibrary(id: id, label: label, kind: "core", icon: "folder", editable: editable)
}

/// The upload the person taps: the gate, the second tap, the ceiling, the
/// refusal that arrives late, and the reply that lands after the house
/// changed.
///
/// `DeviceUpload.kt:221-319`. Everything is driven through the model's own
/// seams — a canned write status, a canned byte read, a recording POST — so
/// each case is about a decision and not about a server.
@MainActor
final class PhoneUploadModelTests: XCTestCase {

    private let answer = Data("""
    {"saved": ["a.mp3"], "skipped": [], "reindex_triggered": false}
    """.utf8)

    private func item(
        _ name: String = "a.mp3",
        kind: PhoneMediaKind = .audio,
        size: Int64? = 1024
    ) -> PhoneMediaItem {
        PhoneMediaItem(
            id: name,
            libraryID: "phone:folder:/a",
            name: name,
            kind: kind,
            sizeBytes: size,
            origin: .file(URL(fileURLWithPath: "/a/\(name)"))
        )
    }

    /// A status read whose answer the test can change between refreshes.
    private func status(_ said: Shared<PhoneServerWriteStatus?>) -> ApiRead<PhoneServerWriteStatus> {
        ApiRead(base: { "http://house.local:6369" }) {
            guard let now = said.current else { throw DomovoiError.unreachable("not answering") }
            return now
        }
    }

    private func settle(
        _ done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never happened", file: file, line: line)
    }

    private func model(
        kind: PhoneUploadKind = .music,
        said: Shared<PhoneServerWriteStatus?>,
        deviceID: String? = "this-phone",
        posts: Posts = Posts(),
        bytes: @Sendable @escaping (PhoneMediaItem) async throws -> Data = { _ in Data("x".utf8) },
        reply: Data? = nil
    ) -> PhoneUploadModel {
        let answered = reply ?? answer
        return PhoneUploadModel(
            kind: kind,
            status: status(said),
            deviceID: deviceID,
            // The model takes a **file** since D43, so the bytes a case hands
            // over are spooled to one here, and `Posts` reads that file back.
            // The cases below therefore still assert on the body that was
            // sent -- which for the Files door is now the file itself, and for
            // the Music door is the multipart envelope around it.
            file: { item in
                let data = try await bytes(item)
                let url = PhoneUploadSpool.fileURL(
                    in: try PhoneUploadSpool.directory(),
                    for: item.name
                )
                try data.write(to: url, options: .atomic)
                return .spooled(url)
            },
            send: { path, body, _, onProgress in
                let data = (try? Data(contentsOf: body)) ?? Data()
                // One tick, at the end: enough to prove a percent reaches the
                // button. The arithmetic itself is measured against
                // `transferPercent` rather than through this seam.
                onProgress(Int64(data.count), Int64(data.count))
                posts.record(path, data)
                return answered
            },
            boundary: { "BOUND" }
        )
    }

    // MARK: - the gate

    func testATapBeforeTheServerHasAnsweredSaysSoAndSendsNothing() async {
        let posts = Posts()
        let model = model(said: Shared(nil), posts: posts)
        XCTAssertNil(model.target)
        model.upload(item())
        XCTAssertEqual(model.toast, "still asking the server whether this phone may upload…")
        XCTAssertEqual(posts.count, 0)
    }

    func testATapOnARefusedPageSaysWhyAndAsksTheServerAgain() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(
            libraries: [library("core:music", "Music")],
            writable: false,
            blockedReason: "\"a simulator\" may not upload here"
        ))
        let posts = Posts()
        let model = model(said: said, posts: posts)
        model.start()
        await settle({ model.target != nil }, "the write status")
        let asked = model.status.fetches

        model.upload(item())
        XCTAssertEqual(model.toast, "\"a simulator\" may not upload here")
        XCTAssertEqual(posts.count, 0)
        XCTAssertEqual(
            model.status.fetches, asked + 1,
            "a refusal the page is showing may have been lifted since it opened, so the gate re-reads"
        )
        XCTAssertEqual(model.state(for: PhoneUploadKey.of(item(), .music)), .refused(reason: "\"a simulator\" may not upload here"))
    }

    // MARK: - the two doors

    func testATrackGoesThroughTheMusicDoorAndTheToastNamesMusic() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(libraries: [library("core:music", "Music")]))
        let posts = Posts()
        let model = model(said: said, posts: posts)
        model.start()
        await settle({ model.target != nil }, "the write status")

        model.upload(item())
        await settle({ model.toast?.hasPrefix("uploaded") == true }, "the done toast")
        XCTAssertEqual(
            posts.lastPath, "/api/music/library/upload",
            "/api/files/upload into core:music asks the admin-only sweep and indexes nothing"
        )
        XCTAssertEqual(model.toast, "uploaded \"a.mp3\" to Music")
        XCTAssertTrue(posts.lastBody.contains("name=\"device_id\"\r\n\r\nthis-phone"))
        XCTAssertFalse(posts.lastBody.contains("library_id"), "the music door takes no library")
    }

    func testAMusicDoorReplyThatCountsInsteadOfListingStillReadsAsSaved() async {
        // `/api/music/library/upload` answers `saved` as an int
        // (`music.py:539`); a decoder that took only the files door's list
        // would report every track as nothing uploaded.
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(libraries: [library("core:music", "Music")]))
        let model = model(
            said: said,
            reply: Data("{\"saved\": 1, \"files\": [\"a.mp3\"], \"skipped\": [], \"reindex_triggered\": true}".utf8)
        )
        model.start()
        await settle({ model.target != nil }, "the write status")
        model.upload(item())
        await settle({ model.toast?.hasPrefix("uploaded") == true }, "the done toast")
        XCTAssertEqual(model.toast, "uploaded \"a.mp3\" to Music · indexing…")
    }

    func testAVideoNamesTheLibraryItLandedInAndSendsTheFilesDoorEveryFieldItRequires() async {
        // The house has no Videos library, which is a real configuration (the
        // stub house is one): the fallback is Documents, and the toast has to
        // say Documents and not "the server".
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(
            libraries: [library("core:music", "Music"), library("core:documents", "Documents")]
        ))
        let posts = Posts()
        let model = model(kind: .video, said: said, posts: posts)
        model.start()
        await settle({ model.target != nil }, "the write status")

        model.upload(item("clip.mov", kind: .video))
        await settle({ model.toast?.hasPrefix("uploaded") == true }, "the done toast")
        // **The streamed door, and everything in the query** (D43). This used
        // to be `/api/files/upload` with a multipart body whose parts carried
        // the library, the path and the device; the body IS the file now
        // (`files.py:1189`), so what used to be asserted about the parts is
        // asserted about the URL -- and the body is the bytes, with no
        // envelope anywhere in it.
        XCTAssertEqual(
            posts.lastPath,
            "/api/files/upload-stream?library_id=core%3Adocuments&rel_path=clip.mov"
                + "&path=&device_id=this-phone"
        )
        XCTAssertEqual(model.toast, "uploaded \"clip.mov\" to Documents")
        XCTAssertEqual(posts.lastBody, "x", "the body is the file, not a multipart envelope")
        XCTAssertFalse(posts.lastBody.contains("Content-Disposition"))
    }

    /// `/api/files/upload`'s `device_id` is required and typed 3–64
    /// (`files.py:866`), so an unregistered phone cannot use that door at
    /// all. It is said, not 422'd.
    func testAPhoneWithNoRegisteredIdIsToldRatherThanSending() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(
            libraries: [library("core:documents", "Documents")]
        ))
        let posts = Posts()
        let model = model(kind: .video, said: said, deviceID: nil, posts: posts)
        model.start()
        await settle({ model.target != nil }, "the write status")
        model.upload(item("clip.mov", kind: .video))
        XCTAssertEqual(model.toast, FileStrings.noDeviceID)
        XCTAssertEqual(posts.count, 0)
    }

    /// The music door's `device_id` is optional (`music.py:607`), and the
    /// Files block is applied by the server before anything is written
    /// (`music.py:644`) — so this one still goes.
    func testAPhoneWithNoRegisteredIdMayStillUploadATrack() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(libraries: [library("core:music", "Music")]))
        let posts = Posts()
        let model = model(said: said, deviceID: nil, posts: posts)
        model.start()
        await settle({ model.target != nil }, "the write status")
        model.upload(item())
        await settle({ posts.count == 1 }, "the post")
        XCTAssertFalse(posts.lastBody.contains("device_id"))
    }

    // MARK: - the ceiling that is gone (D43)

    /// **Every file gets a control, whatever its size.**
    ///
    /// Two cases used to live here, and both asserted the opposite:
    /// `testARowOverTheCeilingGetsNoControlAtAllAndSaysHowBigItIs` (no
    /// control for a row over 64 MiB, a toast if something asked anyway) and
    /// `testAFileThatTurnsOutBiggerThanItsRowSaidIsStillRefused` (the bytes
    /// re-measured after the read, in case a stale listing undersold them).
    /// The body is streamed off disk now, so neither the row's stated size nor
    /// the file's real size is consulted anywhere in this model, and there is
    /// no number left for either case to be about. A folder is the one row
    /// with no control, because the streamed door takes one file per request
    /// (`files.py:1189`).
    func testARowOfAnySizeGetsAnUploadControlAndIsPosted() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(libraries: [library("core:music", "Music")]))
        let posts = Posts()
        let model = model(said: said, posts: posts)
        model.start()
        await settle({ model.target != nil }, "the write status")

        // Four gigabytes: over the old cap by a factor of sixty-four, and over
        // what a `Data` on a phone can hold at all, which is the point.
        let huge = item("film.mov", kind: .video, size: 4 * 1024 * 1024 * 1024)
        XCTAssertTrue(model.offersUpload(huge), "a four-gigabyte row must still offer an upload")
        XCTAssertTrue(model.offersUpload(item()))
        XCTAssertFalse(
            model.offersUpload(PhoneMediaItem(
                id: "Trips",
                libraryID: "phone:folder:/a",
                name: "Trips",
                kind: .folder,
                isDir: true,
                origin: .file(URL(fileURLWithPath: "/a/Trips"))
            )),
            "a folder is the one row with nothing to send"
        )

        model.upload(huge)
        await settle({ posts.count == 1 }, "the post")
        XCTAssertEqual(
            model.toast?.hasPrefix("too big"), false,
            "nothing refuses a file for its size any more: \(model.toast ?? "nil")"
        )
    }

    /// The ring carries a number now (D43).
    ///
    /// ``transferPercent(sent:of:)`` is what turns the transport's two counts
    /// into the one the button draws, and all three of its edges are decided
    /// here rather than left to a UI case: an honest fraction, a total the
    /// server never declared, and a total that is a lie.
    func testThePercentOfATransferIsClampedAndOptional() {
        XCTAssertEqual(transferPercent(sent: 512, of: 1024), 50)
        XCTAssertEqual(transferPercent(sent: 0, of: 1024), 0)
        XCTAssertNil(transferPercent(sent: 512, of: -1), "an undeclared total is not a percent")
        XCTAssertNil(transferPercent(sent: 512, of: 0))
        XCTAssertEqual(transferPercent(sent: 2048, of: 1024), 100, "a lying total cannot draw 200%")
        XCTAssertEqual(
            transferPercent(sent: 1023, of: 1024), 99,
            "rounded DOWN: a bar that says 100% while bytes are still going is the one number that must not appear"
        )
    }

    // MARK: - two taps

    func testASecondTapDoesNotStartASecondUpload() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(libraries: [library("core:music", "Music")]))
        let posts = Posts()
        let release = Shared(false)
        let model = model(
            said: said,
            posts: posts,
            bytes: { _ in
                while !release.current { await Task.yield() }
                return Data("x".utf8)
            }
        )
        model.start()
        await settle({ model.target != nil }, "the write status")

        model.upload(item())
        let key = PhoneUploadKey.of(item(), .music)
        // Nil **at this moment**, not for ever (D43 gave the upload a real
        // percent): this upload is still inside the file read -- the `release`
        // box below has not been set -- so nothing has been sent and there is
        // no fraction to report. A ring pinned at 0 would read as a stall,
        // which is why nil stays the indeterminate case.
        XCTAssertEqual(model.state(for: key), .busy(percent: nil))
        XCTAssertEqual(PhoneMediaStrings.uploadingLabel(nil), "uploading")

        model.upload(item())
        XCTAssertEqual(model.toast, "\"a.mp3\" is already uploading")

        release.set(true)
        await settle({ model.toast?.hasPrefix("uploaded") == true }, "the done toast")
        XCTAssertEqual(posts.count, 1, "one tap, one file")
        XCTAssertEqual(model.state(for: key), .ready)
    }

    // MARK: - a refusal that arrives after the page opened

    func testA403AfterThePageOpenedIsSaidAndThenShownOnTheButton() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(libraries: [library("core:music", "Music")]))
        let body = "{\"detail\":\"\\\"a simulator\\\" may not upload here\"}"
        let model = PhoneUploadModel(
            kind: .music,
            status: status(said),
            deviceID: "this-phone",
            file: { _ in
                let url = PhoneUploadSpool.fileURL(
                    in: try PhoneUploadSpool.directory(),
                    for: "a.mp3"
                )
                try Data("x".utf8).write(to: url, options: .atomic)
                return .spooled(url)
            },
            send: { _, _, _, _ in
                throw DomovoiError.refused(ApiFailure(status: 403, message: "403: \(body)", body: body))
            },
            boundary: { "BOUND" }
        )
        model.start()
        await settle({ model.target != nil }, "the write status")
        // The block lands on the server while the page is open.
        said.set(PhoneServerWriteStatus(
            libraries: [library("core:music", "Music")],
            writable: false,
            blockedReason: "\"a simulator\" may not upload here"
        ))

        model.upload(item())
        await settle({ model.toast?.hasPrefix("upload refused") == true }, "the refusal toast")
        XCTAssertEqual(model.toast, "upload refused: \"a simulator\" may not upload here")
        // The 403 re-read the status, so the button now says so instead of
        // lying (`DeviceUpload.kt:283`).
        await settle(
            { model.refused?.blocked == true },
            "the button catching up with the block"
        )
        XCTAssertEqual(model.state(for: PhoneUploadKey.of(item(), .music)),
                       .refused(reason: "\"a simulator\" may not upload here"))
    }

    // MARK: - [[read-after-await-wrong-key]]

    /// The top defect class on this track: a reply for server A reported under
    /// server B. The switch is **inside** the round trip, which is the only
    /// place it catches anything.
    func testAReplyForTheOldHouseIsNotReportedUnderTheNewOne() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(libraries: [library("core:music", "Music")]))
        let posts = Posts()
        let release = Shared(false)
        let model = model(
            said: said,
            posts: posts,
            bytes: { _ in
                while !release.current { await Task.yield() }
                return Data("x".utf8)
            }
        )
        model.start()
        await settle({ model.target != nil }, "the write status")

        model.upload(item())
        XCTAssertEqual(model.toast, "uploading \"a.mp3\" to Music…")

        // Mid-trip: the person switched house.
        model.serverChanged()
        XCTAssertNil(model.toast)
        release.set(true)

        // Let the whole round trip finish under the new house.
        for _ in 0..<2_000 { await Task.yield() }
        XCTAssertNil(
            model.toast,
            "the old house's \"uploaded …\" must not appear under the new one — a library id and a device id are both reusable"
        )
        // And no row is left claiming to be uploading. The write status is
        // the other half of a house switch and re-reads on its own, keyed to
        // the address (``ApiRead``); this model's job is that nothing in
        // flight survives.
        XCTAssertEqual(model.state(for: PhoneUploadKey.of(item(), .music)), .ready)
    }

    // MARK: - a target of `pick`, which has no sheet on this phone

    func testAFileWithNoSheetGoesToTheDefaultLibraryAndTheToastSaysWhere() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(
            libraries: [library("core:documents", "Documents"), library("core:other", "Other")]
        ))
        let posts = Posts()
        let model = model(kind: .file, said: said, posts: posts)
        model.start()
        await settle({ model.target != nil }, "the write status")
        XCTAssertEqual(model.target, .pick)

        model.upload(item("notes.txt", kind: .docText))
        await settle({ model.toast?.hasPrefix("uploaded") == true }, "the done toast")
        XCTAssertEqual(model.toast, "uploaded \"notes.txt\" to Documents")
        XCTAssertTrue(
            posts.lastPath?.contains("library_id=core%3Adocuments") == true,
            "the streamed door carries the library in the query: \(posts.lastPath ?? "nil")"
        )
    }

    // MARK: - the status read itself

    func testTheStatusReadAsksTheLibrariesThenOneRowOfTheProbeLibrary() async {
        let asked = Shared<[String]>([])
        let got = await PhoneUploadModel.fetchWriteStatus(
            kind: .music,
            deviceID: "this-phone",
            libraries: { path in
                asked.mutate { $0.append(path) }
                return FileLibraries(libraries: [library("core:music", "Music")], documentExtensions: [])
            },
            browse: { path in
                asked.mutate { $0.append(path) }
                return FileBrowse(libraryID: "core:music", writable: false, blockedReason: "blocked")
            }
        )
        XCTAssertEqual(asked.current.first, "/api/files/libraries")
        let browse = try? XCTUnwrap(asked.current.last)
        XCTAssertTrue(browse?.hasPrefix("/api/files/browse?library_id=core%3Amusic") ?? false)
        XCTAssertTrue(browse?.contains("device_id=this-phone") ?? false)
        XCTAssertTrue(browse?.hasSuffix("&limit=1") ?? false, "one row is plenty: only writable and blocked_reason are read")
        XCTAssertEqual(got.writable, false)
        XCTAssertEqual(got.blockedReason, "blocked")
    }

    /// `DeviceUpload.kt:306`: a failed browse is **not** a refusal — the
    /// upload still meets the server's rules and would answer with its own
    /// 403.
    func testAFailedBrowseLeavesUploadsOnRatherThanRefusingThem() async {
        let got = await PhoneUploadModel.fetchWriteStatus(
            kind: .music,
            deviceID: "this-phone",
            libraries: { _ in FileLibraries(libraries: [library("core:music", "Music")], documentExtensions: []) },
            browse: { _ in throw DomovoiError.unreachable("no route") }
        )
        XCTAssertTrue(got.writable)
        XCTAssertNil(got.error)
        XCTAssertEqual(phoneUploadTarget(.music, got), .direct(.musicLibrary))
    }

    func testAFailedLibrariesReadIsRecordedAndEachPageDecidesWhatToDo() async {
        let got = await PhoneUploadModel.fetchWriteStatus(
            kind: .video,
            deviceID: "this-phone",
            libraries: { _ in throw DomovoiError.unreachable("no route") },
            browse: { _ in XCTFail("nothing to probe without a library list"); return FileBrowse() }
        )
        XCTAssertNil(got.libraries)
        XCTAssertEqual(got.error, "no route")
        XCTAssertEqual(
            phoneUploadTarget(.video, got),
            .refused(reason: "couldn't read the server's libraries (no route)", blocked: false)
        )
        XCTAssertEqual(phoneUploadTarget(.music, got), .direct(.musicLibrary))
    }

    // MARK: - saving a picture or a video back to this phone

    func testPhotosTakesPicturesAndVideosAndNothingElse() {
        XCTAssertTrue(PhoneSaveToPhotosModel.canSave(kind: .image))
        XCTAssertTrue(PhoneSaveToPhotosModel.canSave(kind: .video))
        for kind in [PhoneMediaKind.audio, .pdf, .docText, .docOffice, .other, .folder] {
            XCTAssertFalse(PhoneSaveToPhotosModel.canSave(kind: kind), "Photos does not hold \(kind)")
        }
        XCTAssertTrue(PhoneSaveToPhotosModel.canSave(FileEntry(name: "a.jpg", kind: "image")))
        XCTAssertFalse(PhoneSaveToPhotosModel.canSave(FileEntry(name: "a.pdf", kind: "pdf")))
    }

    func testAPictureSavedToPhotosSaysSo() async {
        let added = Shared(0)
        let model = PhoneSaveToPhotosModel(
            ask: { .allowed },
            add: { _, _ in added.mutate { $0 += 1 } }
        )
        model.save(name: "beach.jpg", kind: .image, url: URL(fileURLWithPath: "/tmp/beach.jpg"))
        XCTAssertEqual(model.state, .saving("beach.jpg"))
        await settle({ model.state == .saved("saved \"beach.jpg\" to Photos") }, "the saved state")
        XCTAssertEqual(added.current, 1)
        model.taken()
        XCTAssertEqual(model.state, .idle)
    }

    func testAPersonWhoSaysNoToThePromptIsToldWhereToChangeIt() async {
        let added = Shared(0)
        let model = PhoneSaveToPhotosModel(
            ask: { .refused },
            add: { _, _ in added.mutate { $0 += 1 } }
        )
        model.save(name: "beach.jpg", kind: .image, url: URL(fileURLWithPath: "/tmp/beach.jpg"))
        await settle({ model.state == .failed(PhoneMediaStrings.photosAccessRefused) }, "the refusal")
        XCTAssertEqual(added.current, 0, "nothing is written without the add-only permission")
    }

    func testAFailedCopyReportsTheSystemsOwnReason() async {
        let model = PhoneSaveToPhotosModel(
            ask: { .allowed },
            add: { _, _ in throw DomovoiError.notPermitted("the library is read-only") }
        )
        model.save(name: "beach.jpg", kind: .image, url: URL(fileURLWithPath: "/tmp/beach.jpg"))
        await settle(
            { model.state == .failed("couldn't save to Photos: the library is read-only") },
            "the failure"
        )
    }

    func testADocumentIsRefusedByTheModelAndHasNoControlAnyway() {
        let model = PhoneSaveToPhotosModel(ask: { .allowed }, add: { _, _ in XCTFail("not reachable") })
        model.save(name: "notes.txt", kind: .docText, url: URL(fileURLWithPath: "/tmp/notes.txt"))
        XCTAssertEqual(model.state, .failed(PhoneMediaStrings.onlyPicturesAndVideosGoToPhotos))
    }

    func testASecondTapWhileTheSystemAlertIsUpIsTheSameTap() async {
        let added = Shared(0)
        let release = Shared(false)
        let model = PhoneSaveToPhotosModel(
            ask: {
                while !release.current { await Task.yield() }
                return .allowed
            },
            add: { _, _ in added.mutate { $0 += 1 } }
        )
        model.save(name: "beach.jpg", kind: .image, url: URL(fileURLWithPath: "/tmp/beach.jpg"))
        model.save(name: "hill.jpg", kind: .image, url: URL(fileURLWithPath: "/tmp/hill.jpg"))
        XCTAssertEqual(model.state, .saving("beach.jpg"))
        release.set(true)
        await settle({ model.state == .saved("saved \"beach.jpg\" to Photos") }, "the saved state")
        XCTAssertEqual(added.current, 1)
    }
    // MARK: - a file the person picked, whose bytes are already in hand

    /// PHPicker and the document picker hand over `Data` and no durable URL,
    /// so ``PhoneUploadModel/uploadPicked(name:bytes:kind:)`` must post
    /// **those** bytes and never call the row-reading seam.
    func testAPickedFilePostsTheBytesItWasGivenAndNeverReadsARow() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(libraries: [library("core:music", "Music")]))
        let posts = Posts()
        let model = model(
            said: said,
            posts: posts,
            // Discriminating: if anything called the row seam, the body would
            // say `from the row` and this case fails rather than passing on
            // bytes that happen to be the right length.
            bytes: { _ in Data("from the row".utf8) }
        )
        model.start()
        await settle({ model.target != nil }, "the write status")

        model.uploadPicked(name: "quenlo lullaby.mp3", bytes: Data("picked bytes".utf8), kind: .audio)
        await settle({ posts.count == 1 }, "the picked upload")
        XCTAssertEqual(posts.lastPath, PhoneUploadPath.music)
        XCTAssertTrue(
            posts.lastBody.contains("picked bytes"),
            "the picker's own bytes are what goes up: \(posts.lastBody)"
        )
        XCTAssertFalse(posts.lastBody.contains("from the row"))
        // The toast is written when the POST *returns*, after `posts` has
        // recorded it, so reading it straight away raced the answer (one run
        // in three on the Mac, 2026-11-03). Waiting for the finished wording
        // still fails if the upload never says it finished.
        await settle(
            { model.toast == "uploaded \"quenlo lullaby.mp3\" to Music" },
            "the finished toast (last seen: \(model.toast ?? "nil"))"
        )
    }

    /// A picked file well over the old ceiling **is posted** (D43).
    ///
    /// This was `testAPickedFileOverTheCapIsRefusedWithoutPosting` and
    /// asserted a toast and `posts.count == 0`. Eighty mebibytes here rather
    /// than the four gigabytes the row case uses, because a pick really does
    /// arrive as `Data` -- that is what the picker hands over -- and this case
    /// is about what the model then does with it: spool it to a file and
    /// stream that file, instead of measuring it and refusing.
    func testAPickedFileOverTheOldCapIsSpooledAndPosted() async {
        let said = Shared<PhoneServerWriteStatus?>(PhoneServerWriteStatus(libraries: [library("core:music", "Music")]))
        let posts = Posts()
        let model = model(said: said, posts: posts)
        model.start()
        await settle({ model.target != nil }, "the write status")

        let big = Data(count: 80 * 1024 * 1024)
        model.uploadPicked(name: "grendalf huge raw.wav", bytes: big, kind: .audio)
        await settle({ posts.count == 1 }, "the picked upload")
        XCTAssertEqual(posts.lastPath, PhoneUploadPath.music)
        await settle(
            { model.toast == "uploaded \"grendalf huge raw.wav\" to Music" },
            "the finished toast (last seen: \(model.toast ?? "nil"))"
        )
    }
}
