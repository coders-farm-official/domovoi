import Foundation
import XCTest
@testable import DomovoiKit

/// The upload's body, byte for byte.
///
/// okhttp writes Android's (`PhoneUpload.kt:98`); this app writes its own, and
/// every detail of a multipart body is either exactly right or silently a 422
/// with no clue which detail it was. So they are assertions.
final class PhoneMultipartTests: XCTestCase {

    private func text(_ data: Data) -> String {
        String(decoding: data, as: UTF8.self)
    }

    func testTheBodyHasTheFieldsFirstThenTheFileThenTheClosingDelimiter() {
        let body = PhoneMultipart.body(
            fields: [(name: "library_id", value: "core:documents"), (name: "path", value: "")],
            fileName: "notes.txt",
            mime: "text/plain",
            bytes: Data("hello".utf8),
            boundary: "BOUND"
        )
        XCTAssertEqual(text(body), [
            "--BOUND\r\n",
            "Content-Disposition: form-data; name=\"library_id\"\r\n\r\n",
            "core:documents\r\n",
            "--BOUND\r\n",
            "Content-Disposition: form-data; name=\"path\"\r\n\r\n",
            "\r\n",
            "--BOUND\r\n",
            "Content-Disposition: form-data; name=\"files\"; filename=\"notes.txt\"\r\n",
            "Content-Type: text/plain\r\n\r\n",
            "hello\r\n",
            "--BOUND--\r\n",
        ].joined())
    }

    /// `path=""` is what means "the library's root" (`files.py:866`), so an
    /// empty field is still a part. A body that left it out would upload into
    /// whatever FastAPI's default is instead of where the screen said.
    func testAnEmptyFieldIsStillSent() {
        let body = PhoneMultipart.body(
            fields: [(name: "path", value: "")],
            fileName: "a.bin",
            mime: "application/octet-stream",
            bytes: Data(),
            boundary: "B"
        )
        XCTAssertTrue(text(body).contains("name=\"path\"\r\n\r\n\r\n"))
    }

    func testThePartIsNamedFilesBecauseBothDoorsReadAList() {
        let body = PhoneMultipart.body(
            fields: [],
            fileName: "a.mp3",
            mime: "audio/mpeg",
            bytes: Data("x".utf8),
            boundary: "B"
        )
        XCTAssertTrue(text(body).contains("name=\"files\"; filename=\"a.mp3\""))
    }

    func testTheFileBytesSurviveExactlyEvenWhenTheyAreNotText() {
        let bytes = Data([0x00, 0xFF, 0x0D, 0x0A, 0x2D, 0x2D])
        let body = PhoneMultipart.body(
            fields: [],
            fileName: "a.bin",
            mime: "application/octet-stream",
            bytes: bytes,
            boundary: "B"
        )
        let header = Data("--B\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.bin\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8)
        let tail = Data("\r\n--B--\r\n".utf8)
        XCTAssertEqual(body, header + bytes + tail)
    }

    func testTheClosingDelimiterHasDashesOnBothEnds() {
        let body = text(PhoneMultipart.body(
            fields: [],
            fileName: "a",
            mime: "text/plain",
            bytes: Data(),
            boundary: "B"
        ))
        XCTAssertTrue(body.hasSuffix("--B--\r\n"))
    }

    func testTheContentTypeNamesTheSameBoundaryTheBodyUses() {
        let boundary = PhoneMultipart.boundary("abc")
        XCTAssertEqual(boundary, "-----domovoi-abc")
        XCTAssertEqual(
            PhoneMultipart.contentType(boundary: boundary),
            "multipart/form-data; boundary=-----domovoi-abc"
        )
    }

    func testEveryBoundaryIsItsOwn() {
        XCTAssertNotEqual(PhoneMultipart.boundary(), PhoneMultipart.boundary())
    }

    // MARK: - a name from the phone cannot write headers

    /// A picked folder can hold a file with a newline in its name. Inside a
    /// quoted header value that would end the header and let the rest of the
    /// name be read as headers of this app's own request.
    func testANameWithANewlineCannotAddAHeader() {
        let hostile = "a\r\nContent-Disposition: form-data; name=\"device_id\"\r\n\r\nsomebody-elses-phone"
        XCTAssertEqual(
            PhoneMultipart.safePartName(hostile),
            "aContent-Disposition: form-data; name=\\\"device_id\\\"somebody-elses-phone"
        )
        let body = text(PhoneMultipart.body(
            fields: [],
            fileName: hostile,
            mime: "text/plain",
            bytes: Data(),
            boundary: "B"
        ))
        // Six CRLFs and no more — the ones this body is built from: after the
        // opening delimiter, after the disposition line, after the
        // content-type line, the blank line that ends the headers, the one
        // after the (empty) bytes, and the one after the closing delimiter. A
        // seventh would mean the name had added a line of its own.
        XCTAssertEqual(body.components(separatedBy: "\r\n").count - 1, 6)
        XCTAssertFalse(body.contains("name=\"device_id\""))
    }

    func testAQuoteInANameIsEscapedRatherThanClosingTheValue() {
        XCTAssertEqual(PhoneMultipart.safePartName("say \"hi\".txt"), "say \\\"hi\\\".txt")
    }

    func testOnlyTheLastPathComponentIsSentAsTheName() {
        XCTAssertEqual(PhoneMultipart.safePartName("../../etc/passwd"), "passwd")
        XCTAssertEqual(PhoneMultipart.safePartName("C:\\Users\\k\\a.mp3"), "a.mp3")
    }

    func testANameThatIsNothingAtAllFallsBackToAWord() {
        XCTAssertEqual(PhoneMultipart.safePartName(""), FileStrings.unnamedFile)
        XCTAssertEqual(PhoneMultipart.safePartName("."), FileStrings.unnamedFile)
        XCTAssertEqual(PhoneMultipart.safePartName("/"), FileStrings.unnamedFile)
    }

    func testAMediaTypeCannotAddAHeaderEitherAndNeverGoesOutEmpty() {
        XCTAssertEqual(PhoneMultipart.safeHeaderValue("audio/mpeg\r\nX-Admin: 1"), "audio/mpegX-Admin: 1")
        XCTAssertEqual(PhoneMultipart.safeHeaderValue("  "), "application/octet-stream")
    }

    // MARK: - the type a name is sent as

    func testTheMediaTypeComesFromTheNameAndFallsBackToOctetStream() {
        XCTAssertEqual(phoneMimeType(forFileName: "a.mp3"), "audio/mpeg")
        XCTAssertEqual(phoneMimeType(forFileName: "noextension"), "application/octet-stream")
        XCTAssertEqual(phoneMimeType(forFileName: "a.sdhjkfgsd"), "application/octet-stream")
    }
}
