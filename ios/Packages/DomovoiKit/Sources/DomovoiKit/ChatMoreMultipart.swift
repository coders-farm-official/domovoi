import Foundation

/// A `multipart/form-data` body, built by hand.
///
/// OkHttp gives Android `MultipartBody.Builder` (`VoiceTurn.kt:164`);
/// Foundation gives Swift nothing at all, and
/// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
/// takes `Data` and a content type — which is exactly enough, and is why a
/// voice turn is portable without touching the client
/// (`DomovoiClient.swift:599`).
///
/// Pure and unit-tested, because the mistakes in a multipart body are all
/// invisible until a server rejects it: a missing `\r\n`, a boundary that
/// appears inside a part, a header folded the wrong way. The one route that
/// uses it is the voice turn (``ChatMoreVoiceTurn``).
public struct ChatMoreMultipart: Sendable {

    /// The boundary, without the leading dashes a line carries.
    public let boundary: String

    private var parts = Data()

    /// - Parameter boundary: a caller's own, for a test that wants a body it
    ///   can read. The default is a fresh UUID with no `-`, which cannot
    ///   appear in a UUID-free payload and is what OkHttp does.
    public init(boundary: String = ChatMoreMultipart.freshBoundary()) {
        self.boundary = boundary
    }

    public static func freshBoundary() -> String {
        "domovoi" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
    }

    /// `Content-Type` for the request, with this body's boundary.
    public var contentType: String { "multipart/form-data; boundary=\(boundary)" }

    /// A plain form field (`addFormDataPart(name, value)`).
    public mutating func add(name: String, value: String) {
        parts.append(Data("--\(boundary)\r\n".utf8))
        parts.append(Data("Content-Disposition: form-data; name=\"\(Self.escape(name))\"\r\n\r\n".utf8))
        parts.append(Data(value.utf8))
        parts.append(Data("\r\n".utf8))
    }

    /// A file field (`addFormDataPart(name, filename, body)`).
    public mutating func add(
        name: String,
        filename: String,
        mime: String = "application/octet-stream",
        bytes: Data
    ) {
        parts.append(Data("--\(boundary)\r\n".utf8))
        // One `String` first: `.utf8` binds to the literal it follows,
        // so `"a" + "b".utf8` is a String plus a UTF8View and does not
        // compile.
        let disposition =
            "Content-Disposition: form-data; name=\"\(Self.escape(name))\"; "
            + "filename=\"\(Self.escape(filename))\"\r\n"
        parts.append(Data(disposition.utf8))
        parts.append(Data("Content-Type: \(mime)\r\n\r\n".utf8))
        parts.append(bytes)
        parts.append(Data("\r\n".utf8))
    }

    /// The whole body, closing boundary and all. Reading it does not end the
    /// builder: another part may still be added and the body read again.
    public var body: Data {
        var out = parts
        out.append(Data("--\(boundary)--\r\n".utf8))
        return out
    }

    /// A quotation mark or a line break inside a field name or a file name
    /// would end the header early, which is how a crafted file name becomes
    /// an extra part. RFC 6266's answer is a backslash escape for the quote;
    /// a CR or an LF has no escape at all and is dropped.
    private static func escape(_ text: String) -> String {
        text
            .replacingOccurrences(of: "\r", with: "")
            .replacingOccurrences(of: "\n", with: "")
            .replacingOccurrences(of: "\"", with: "\\\"")
    }
}
