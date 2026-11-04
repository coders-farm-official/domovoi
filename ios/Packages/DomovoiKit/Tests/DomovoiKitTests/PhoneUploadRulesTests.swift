import Foundation
import XCTest
@testable import DomovoiKit

/// A library in the shape `/api/files/libraries` answers
/// (`files_security.py:674`).
private func library(_ id: String, _ label: String, editable: Bool = true) -> FileLibrary {
    FileLibrary(id: id, label: label, kind: "core", icon: "folder", editable: editable)
}

/// Every branch of the upload rules — `DeviceUpload.kt:61-214`.
///
/// They are pure, so each refusal the button can show is a line here rather
/// than a thing discovered against a real house. The destinations matter as
/// much as the refusals: a video that lands in the wrong library is a file a
/// person cannot find again.
final class PhoneUploadRulesTests: XCTestCase {

    // MARK: - the default home library, which decides where a row lands

    func testAVideoGoesToTheVideosLibraryWhenThereIsOne() {
        let libs = [library("core:videos", "Videos"), library("core:documents", "Documents")]
        XCTAssertEqual(phoneVideoLibraryID(libs), "core:videos")
    }

    /// The stub house's own configuration, and a real one: a household whose
    /// Videos folder is not a Files library. The fallback is Documents, and
    /// the toast has to say so.
    func testAVideoFallsToDocumentsOnAHouseWithNoVideosLibrary() {
        let libs = [library("core:music", "Music"), library("core:documents", "Documents")]
        XCTAssertEqual(phoneVideoLibraryID(libs), "core:documents")
    }

    func testAnMp4CountsAsAVideoEvenThoughItsExtensionReadsAsAudioFirst() {
        // `FileKinds.kind` classifies .mp4 as audio, as the server's listing
        // does; `defaultHomeLibrary` overrides that by extension
        // (`PhoneFiles.kt:271`).
        XCTAssertEqual(FileKinds.kind(of: "clip.mp4", isDir: false), "audio")
        let libs = [library("core:videos", "Videos"), library("core:music", "Music")]
        let entry = FileEntry(name: "clip.mp4", kind: FileKinds.kind(of: "clip.mp4", isDir: false))
        XCTAssertEqual(phoneDefaultHomeLibrary(entry, libs), "core:videos")
    }

    func testAPictureGoesToPicturesAndASongToMusic() {
        let libs = [library("core:pictures", "Pictures"), library("core:music", "Music")]
        XCTAssertEqual(phoneDefaultHomeLibrary(FileEntry(name: "a.jpg", kind: "image"), libs), "core:pictures")
        XCTAssertEqual(phoneDefaultHomeLibrary(FileEntry(name: "a.mp3", kind: "audio"), libs), "core:music")
    }

    func testAReadOnlyLibraryIsNeverADestination() {
        let libs = [library("core:pictures", "Pictures", editable: false), library("core:other", "Other")]
        XCTAssertEqual(phoneDefaultHomeLibrary(FileEntry(name: "a.jpg", kind: "image"), libs), "core:other")
    }

    /// The one asymmetry in Android's chain (`PhoneFiles.kt:275`): a document
    /// skips the Other step, because Other is where a Documents folder's
    /// *non*-documents live.
    func testADocumentSkipsOtherAndTakesDocumentsOrTheFirstEditable() {
        let withDocuments = [library("core:other", "Other"), library("core:documents", "Documents")]
        XCTAssertEqual(
            phoneDefaultHomeLibrary(FileEntry(name: "notes.txt", kind: "doc-text"), withDocuments),
            "core:documents"
        )
        let withoutDocuments = [library("plugin:scans", "Scans"), library("core:other", "Other")]
        XCTAssertEqual(
            phoneDefaultHomeLibrary(FileEntry(name: "notes.txt", kind: "doc-text"), withoutDocuments),
            "plugin:scans",
            "a document must not fall into Other, which is the non-documents view"
        )
    }

    func testNothingLandsAnywhereWhenEveryLibraryIsReadOnly() {
        let libs = [library("core:other", "Other", editable: false)]
        XCTAssertNil(phoneDefaultHomeLibrary(FileEntry(name: "a.bin", kind: "other"), libs))
    }

    // MARK: - the probe: which library this device's write access is asked of

    func testTheProbeIsNilUntilTheLibrariesAreRead() {
        for kind in PhoneUploadKind.allCases {
            XCTAssertNil(phoneProbeLibrary(kind, nil), "\(kind) must not probe without a library list")
        }
    }

    func testTheMusicProbeIsTheMusicLibraryEvenWhenItIsReadOnly() {
        // The block is per device, so a read-only Music library still answers
        // the question "may this phone write at all".
        let libs = [library("core:music", "Music", editable: false)]
        XCTAssertEqual(phoneProbeLibrary(.music, libs), "core:music")
    }

    func testAFileProbesTheLibraryANonDocumentWouldLandIn() {
        let libs = [library("core:documents", "Documents"), library("core:other", "Other")]
        XCTAssertEqual(phoneProbeLibrary(.file, libs), "core:other")
    }

    // MARK: - the target

    func testNoTargetUntilTheServerHasAnswered() {
        XCTAssertNil(phoneUploadTarget(.music, nil))
        XCTAssertNil(phoneUploadTarget(.video, nil))
        XCTAssertNil(phoneUploadTarget(.file, nil))
    }

    func testABlockedDeviceIsRefusedOnEveryPageWithTheServersOwnReason() {
        let status = PhoneServerWriteStatus(
            libraries: [library("core:music", "Music")],
            writable: false,
            blockedReason: "\"a simulator\" may not upload here"
        )
        for kind in PhoneUploadKind.allCases {
            XCTAssertEqual(
                phoneUploadTarget(kind, status),
                .refused(reason: "\"a simulator\" may not upload here", blocked: true)
            )
        }
    }

    func testABlockedDeviceWithNoStatedReasonGetsTheAndroidSentence() {
        let status = PhoneServerWriteStatus(libraries: [], writable: false)
        XCTAssertEqual(
            phoneUploadTarget(.music, status),
            .refused(reason: "this device isn't allowed to change files", blocked: true)
        )
    }

    func testMusicGoesThroughItsOwnDoorEvenWhenTheLibrariesCouldNotBeRead() {
        // The Music door applies the server's rules itself
        // (`DeviceUpload.kt:135`), so an unreadable library list must not
        // turn into a refusal on this page.
        let status = PhoneServerWriteStatus(libraries: nil, error: "offline?")
        XCTAssertEqual(phoneUploadTarget(.music, status), .direct(.musicLibrary))
    }

    func testMusicIsRefusedWhenTheHouseHasNoMusicLibraryOrAReadOnlyOne() {
        XCTAssertEqual(
            phoneUploadTarget(.music, PhoneServerWriteStatus(libraries: [library("core:documents", "Documents")])),
            .refused(reason: "this server has no Music library to upload into", blocked: false)
        )
        XCTAssertEqual(
            phoneUploadTarget(.music, PhoneServerWriteStatus(libraries: [library("core:music", "Music", editable: false)])),
            .refused(reason: "the server's Music library is read-only", blocked: false)
        )
    }

    func testAVideoNamesTheLibraryItWouldLandIn() {
        let status = PhoneServerWriteStatus(libraries: [library("core:documents", "Documents")])
        XCTAssertEqual(
            phoneUploadTarget(.video, status),
            .direct(.library(id: "core:documents", label: "Documents", path: ""))
        )
        XCTAssertNil(phoneUploadTarget(.video, status)?.refusal, "a destination is not a refusal")
    }

    func testAVideoSaysTheLibrariesCouldNotBeReadAndWhy() {
        XCTAssertEqual(
            phoneUploadTarget(.video, PhoneServerWriteStatus(libraries: nil, error: "500: boom")),
            .refused(reason: "couldn't read the server's libraries (500: boom)", blocked: false)
        )
        // No message: no parenthesis, not an empty one.
        XCTAssertEqual(
            phoneUploadTarget(.video, PhoneServerWriteStatus(libraries: nil)),
            .refused(reason: "couldn't read the server's libraries", blocked: false)
        )
    }

    func testAVideoIsRefusedWhenEveryLibraryIsReadOnly() {
        let status = PhoneServerWriteStatus(libraries: [library("core:documents", "Documents", editable: false)])
        XCTAssertEqual(
            phoneUploadTarget(.video, status),
            .refused(reason: "every library on the server is read-only", blocked: false)
        )
    }

    func testAFileAsksWhereUnlessEveryLibraryIsReadOnly() {
        // The list could not be read: `.pick` would mean "the default home
        // library for this kind" over an EMPTY list, which is nil — a fully
        // enabled Upload button whose tap did nothing. Refused, named, as the
        // `.video` branch answers.
        XCTAssertEqual(
            phoneUploadTarget(.file, PhoneServerWriteStatus(libraries: nil, error: "500: boom")),
            .refused(reason: "couldn't read the server's libraries (500: boom)", blocked: false)
        )
        XCTAssertEqual(
            phoneUploadTarget(.file, PhoneServerWriteStatus(libraries: nil)),
            .refused(reason: "couldn't read the server's libraries", blocked: false)
        )
        XCTAssertEqual(
            phoneUploadTarget(.file, PhoneServerWriteStatus(libraries: [library("core:other", "Other")])),
            .pick
        )
        XCTAssertEqual(
            phoneUploadTarget(.file, PhoneServerWriteStatus(libraries: [library("core:other", "Other", editable: false)])),
            .refused(reason: "every library on the server is read-only", blocked: false)
        )
    }

    // MARK: - the button

    func testTheButtonChecksUntilThereIsATargetAndThenSaysWhatItCanDo() {
        XCTAssertEqual(phoneUploadButtonState(target: nil, inFlight: false, percent: nil), .checking)
        XCTAssertEqual(phoneUploadButtonState(target: .pick, inFlight: false, percent: nil), .ready)
        XCTAssertEqual(
            phoneUploadButtonState(target: .refused(reason: "no", blocked: true), inFlight: false, percent: nil),
            .refused(reason: "no")
        )
    }

    func testAnUploadInFlightWinsOverEveryOtherState() {
        // Including over a refusal that arrived while it was running: the row
        // must keep its ring until its own file is done.
        XCTAssertEqual(
            phoneUploadButtonState(target: .refused(reason: "no", blocked: false), inFlight: true, percent: nil),
            .busy(percent: nil)
        )
        XCTAssertEqual(phoneUploadButtonState(target: nil, inFlight: true, percent: 42), .busy(percent: 42))
    }

    func testTheBusyLabelNamesThePercentOnlyWhenThereIsOne() {
        XCTAssertEqual(PhoneMediaStrings.uploadingLabel(nil), "uploading")
        XCTAssertEqual(PhoneMediaStrings.uploadingLabel(7), "uploading 7%")
    }

    // MARK: - the destination's own name

    func testADestinationNamesItselfTheWayAToastWouldReadIt() {
        XCTAssertEqual(PhoneUploadDest.musicLibrary.whereItLands, "Music")
        XCTAssertEqual(PhoneUploadDest.library(id: "a", label: "Documents", path: "").whereItLands, "Documents")
        XCTAssertEqual(
            PhoneUploadDest.library(id: "a", label: "Documents", path: "notes/2026").whereItLands,
            "Documents / notes/2026"
        )
    }

    // MARK: - the two doors' replies

    func testTheFilesDoorsSavedListAndTheMusicDoorsSavedCountBothDecode() throws {
        let files = Data("""
        {"saved": ["a.mp3", "b.mp3"], "skipped": [], "reindex_triggered": true, "created_dir": false}
        """.utf8)
        let music = Data("""
        {"saved": 1, "files": ["a.mp3"], "skipped": ["b.mp3"], "reindex_triggered": true}
        """.utf8)
        let decoder = JSONDecoder()
        XCTAssertEqual(
            try decoder.decode(PhoneUploadReply.self, from: files),
            PhoneUploadReply(saved: 2, skipped: 0, reindexTriggered: true)
        )
        XCTAssertEqual(
            try decoder.decode(PhoneUploadReply.self, from: music),
            PhoneUploadReply(saved: 1, skipped: 1, reindexTriggered: true),
            "`saved` is an int on the music door and a list on the files door — a decoder that took "
                + "only one shape would report every music upload as nothing uploaded"
        )
    }

    func testAnAnswerWithNothingInItReadsAsNothingUploaded() throws {
        let reply = try JSONDecoder().decode(PhoneUploadReply.self, from: Data("{}".utf8))
        XCTAssertEqual(reply, PhoneUploadReply(saved: 0))
        XCTAssertEqual(
            phoneUploadDoneText("a.mp3", .musicLibrary, reply),
            "nothing was uploaded"
        )
    }

    func testTheDoneToastNamesTheFileThePlaceAndWhatElseHappened() {
        XCTAssertEqual(
            phoneUploadDoneText("a.mp3", .musicLibrary, PhoneUploadReply(saved: 1)),
            "uploaded \"a.mp3\" to Music"
        )
        XCTAssertEqual(
            phoneUploadDoneText(
                "a.mp3",
                .library(id: "core:documents", label: "Documents", path: ""),
                PhoneUploadReply(saved: 1, skipped: 2, reindexTriggered: true)
            ),
            "uploaded \"a.mp3\" to Documents · 2 skipped · indexing…"
        )
    }

    // MARK: - the failure toast

    func testARefusalIsSaidInTheServersOwnWordsAndIsNotCalledAFailure() {
        let error = DomovoiError.refused(ApiFailure(
            status: 403,
            message: "403: {\"detail\":\"\\\"a simulator\\\" may not upload here\"}",
            body: "{\"detail\":\"\\\"a simulator\\\" may not upload here\"}"
        ))
        XCTAssertEqual(
            phoneUploadFailureText("a.mp3", error),
            "upload refused: \"a simulator\" may not upload here"
        )
    }

    func testA403WithNoDetailStillReadsAsARefusal() {
        let error = DomovoiError.refused(ApiFailure(status: 403, message: "403: ", body: ""))
        XCTAssertEqual(
            phoneUploadFailureText("a.mp3", error),
            "upload refused: this device may not upload here"
        )
    }

    func testAServerDetailThatIsNotAStringIsNotShownToAnybody() {
        // FastAPI's 422 answers `detail` as a LIST of field errors; showing
        // that to a person is worse than showing the status.
        let body = "{\"detail\":[{\"loc\":[\"body\",\"device_id\"],\"msg\":\"too short\"}]}"
        XCTAssertNil(phoneServerDetail(body))
        let error = DomovoiError.httpError(ApiFailure(status: 422, message: "422: \(body)", body: body))
        XCTAssertEqual(phoneUploadFailureText("a.mp3", error), "upload failed: 422: \(body)")
    }

    func testAnErrorWithADetailIsAFailureAndNotARefusal() {
        let body = "{\"detail\":\"no room left\"}"
        let error = DomovoiError.serverTrouble(ApiFailure(status: 507, message: "507: \(body)", body: body))
        XCTAssertEqual(phoneUploadFailureText("a.mp3", error), "upload failed: no room left")
    }

    func testOnlyATransportFailureIsBlamedOnTheConnection() {
        XCTAssertEqual(
            phoneUploadFailureText("a.mp3", DomovoiError.unreachable("no route to host")),
            "upload failed (offline?)"
        )
    }

    func testAFileThisPhoneCannotReadSaysSoAndNamesIt() {
        XCTAssertEqual(
            phoneUploadFailureText("ignored", PhoneMediaError.unreadable(name: "holiday.mov")),
            "can't read \"holiday.mov\" on this phone"
        )
    }

    /// The ceiling's **sentence** outlives the ceiling (D43).
    ///
    /// `PhoneMediaError.tooBig` is no longer thrown by anything: the upload
    /// streams off disk and never measures the file. The case and its wording
    /// stay because `PhoneUploadRowButton`'s `PhoneUploadTooBigNote` and
    /// `PhoneMediaListModel` still render them, and those are the phone-media
    /// unit's to remove — a dangling `case` in a public enum is a compile
    /// error in somebody else's file. So this case guards the text and says,
    /// in one place, that nothing reaches it.
    ///
    /// The size is a literal rather than `PhoneUploadModel.cap + 1`, because
    /// that static is gone. 65 MiB: what the old ceiling would have refused.
    func testTheOldCeilingsSentenceStillReadsRightThoughNothingThrowsIt() {
        let size: Int64 = 68_157_440
        XCTAssertEqual(
            phoneUploadFailureText("big.mov", PhoneMediaError.tooBig(name: "big.mov", size: size)),
            PhoneMediaStrings.tooBigToUpload(size)
        )
        XCTAssertTrue(PhoneMediaStrings.tooBigToUpload(size).contains("65"))
    }

    // There used to be a `testTheUploadCeilingIsTheSameNumberAsTheDownloadCeiling`
    // here, asserting `PhoneUploadModel.cap == FileSaveModel.cap` — one
    // product limit said twice, kept honest by a test. Both statics are gone
    // (D43 streams the upload off disk, D47 streams the save onto it), so
    // there is no number to agree about. What replaces it is not an
    // assertion: `grep -rn "64 \* 1024 \* 1024" ios/` finds nothing, which is
    // the batch's own check that neither ceiling grew back somewhere else.

    // MARK: - the row keys

    func testEachPageKeysItsUploadsItsOwnWay() {
        let item = PhoneMediaItem(
            id: "songs/a.mp3",
            libraryID: "phone:folder:/a",
            name: "a.mp3",
            kind: .audio,
            origin: .file(URL(fileURLWithPath: "/a/songs/a.mp3"))
        )
        XCTAssertEqual(PhoneUploadKey.of(item, .music), "track:songs/a.mp3")
        XCTAssertEqual(PhoneUploadKey.of(item, .video), "video:songs/a.mp3")
        XCTAssertEqual(PhoneUploadKey.of(item, .file), "file:phone:folder:/a/songs/a.mp3")
    }

    // MARK: - the card above the list

    func testABlockedCardTellsThePersonWhereTheBlockIsLifted() {
        let said = PhoneMediaStrings.refusedCard(reason: "this device isn't allowed to change files", blocked: true)
        XCTAssertTrue(said.hasPrefix("this device isn't allowed to change files. You can browse"))
        XCTAssertTrue(said.contains("Settings → Phones & browsers"))
    }

    func testAReadOnlyCardSaysTheRestOfThePageStillWorks() {
        XCTAssertEqual(
            PhoneMediaStrings.refusedCard(reason: "every library on the server is read-only", blocked: false),
            "every library on the server is read-only, so uploads are off. Everything else here works."
        )
    }
}
