import Foundation

/// One `multipart/form-data` body, built by hand.
///
/// Android hands okhttp a `MultipartBody.Builder` and the library writes the
/// bytes (`PhoneUpload.kt:98-113`). Foundation has no such builder, so the
/// body is assembled here — and it is assembled in a **pure** function for
/// one reason: every detail of a multipart body is a thing that is either
/// exactly right or silently wrong. A missing CRLF, a boundary that is a
/// prefix of another line, an unquoted filename, a closing delimiter without
/// its trailing `--`: each one answers 422 from FastAPI with no clue which of
/// them it was. Here they are nine assertions instead.
///
/// Both doors take the file part under the name **`files`** and read it as a
/// list — `/api/files/upload` (`files.py:866`) and
/// `/api/music/library/upload` (`music.py:607`) — so one part named `files`
/// is a one-file batch and not a special case.
public enum PhoneMultipart {

    /// A fresh boundary. Long, random, and with a `-----` run no file's bytes
    /// are going to contain by accident; the body is never scanned for a
    /// collision, exactly as okhttp's is not.
    public static func boundary(_ token: String = UUID().uuidString) -> String {
        "-----domovoi-\(token)"
    }

    /// The `Content-Type` header for a body built with that boundary.
    public static func contentType(boundary: String) -> String {
        "multipart/form-data; boundary=\(boundary)"
    }

    /// `fields` first, in the order given, then one file part.
    ///
    /// The order is the Kotlin's (`PhoneUpload.kt:100`: the form fields, then
    /// `addFormDataPart("files", …)`) and it is not cosmetic — FastAPI reads a
    /// form as a stream, and a `Form(...)` field that arrived after an 8 GiB
    /// file part would have been read after the file was spooled.
    ///
    /// - Parameters:
    ///   - fields: `device_id`, `library_id`, `path` — whatever the door
    ///     takes. A field with an empty value is still sent, because `path=""`
    ///     is what means "the library's root" (`files.py:866`).
    ///   - fileName: what the server will call the file. Sanitised by
    ///     ``safePartName(_:)``.
    ///   - mime: the part's own `Content-Type`.
    public static func body(
        fields: [(name: String, value: String)],
        fileField: String = "files",
        fileName: String,
        mime: String,
        bytes: Data,
        boundary: String
    ) -> Data {
        var out = Data()
        let crlf = "\r\n"
        for field in fields {
            out.append(Data("--\(boundary)\(crlf)".utf8))
            out.append(Data("Content-Disposition: form-data; name=\"\(safePartName(field.name))\"\(crlf)\(crlf)".utf8))
            out.append(Data(field.value.utf8))
            out.append(Data(crlf.utf8))
        }
        out.append(Data("--\(boundary)\(crlf)".utf8))
        out.append(Data(
            "Content-Disposition: form-data; name=\"\(safePartName(fileField))\"; filename=\"\(safePartName(fileName))\"\(crlf)".utf8
        ))
        out.append(Data("Content-Type: \(safeHeaderValue(mime))\(crlf)\(crlf)".utf8))
        out.append(bytes)
        out.append(Data(crlf.utf8))
        // The closing delimiter is the boundary with `--` on BOTH ends.
        out.append(Data("--\(boundary)--\(crlf)".utf8))
        return out
    }

    /// A name or filename that cannot break out of its own header line.
    ///
    /// The name comes from the **phone**: a picked folder can hold a file
    /// called `a"b`, and on a shared volume it can hold one with a newline in
    /// it. A CR or LF inside a quoted string would end the header and let the
    /// rest of the name be read as headers of its own — header injection into
    /// the app's own request. So the separators go, the quote is escaped, and
    /// only the last path component is kept, which is
    /// ``FileSaveModel/safeName(for:)``'s reasoning in the other direction.
    public static func safePartName(_ raw: String) -> String {
        let tail = raw.split(whereSeparator: { $0 == "/" || $0 == "\\" }).last.map(String.init) ?? ""
        // Over the **unicode scalars**, not the characters: Swift reads a
        // CRLF as ONE `Character`, equal to neither "\r" nor "\n", so a
        // character-wise filter let every CRLF straight through — exactly the
        // injection this function exists to stop, caught by
        // `testANameWithANewlineCannotAddAHeader`.
        var cleaned = ""
        for scalar in tail.unicodeScalars {
            switch scalar {
            case "\r", "\n", "\0": continue
            case "\"": cleaned += "\\\""
            default: cleaned.unicodeScalars.append(scalar)
            }
        }
        let trimmed = cleaned.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || trimmed == "." || trimmed == ".." { return FileStrings.unnamedFile }
        return trimmed
    }

    /// The same, for a value that is not quoted at all — a media type, which
    /// a resolver hands over and which must not be able to add a header
    /// either.
    public static func safeHeaderValue(_ raw: String) -> String {
        // Scalars, for the reason ``safePartName(_:)`` gives.
        var kept = String.UnicodeScalarView()
        for scalar in raw.unicodeScalars where scalar != "\r" && scalar != "\n" && scalar != "\0" {
            kept.append(scalar)
        }
        let cleaned = String(kept).trimmingCharacters(in: .whitespaces)
        return cleaned.isEmpty ? "application/octet-stream" : cleaned
    }
}
