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
        var out = head(fields: fields, fileField: fileField, fileName: fileName, mime: mime, boundary: boundary)
        out.append(bytes)
        out.append(tail(boundary: boundary))
        return out
    }

    /// `\r\n`, named once. A missing one of these is the 422 with no clue in
    /// it that this file's doc comment is about.
    static let crlf = "\r\n"

    /// Everything before the file's bytes: the form fields, then the file
    /// part's own headers.
    ///
    /// Extracted so ``body(fields:fileField:fileName:mime:bytes:boundary:)``
    /// and ``spool(fields:fileField:fileName:mime:file:boundary:to:)`` cannot
    /// drift. Two copies of nine exact bytes is how one of them ends up
    /// wrong, and the one that would have been wrong is the streaming one —
    /// the path with no test that reads the body back.
    static func head(
        fields: [(name: String, value: String)],
        fileField: String,
        fileName: String,
        mime: String,
        boundary: String
    ) -> Data {
        var out = Data()
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
        return out
    }

    /// Everything after them. The closing delimiter is the boundary with
    /// `--` on **both** ends.
    static func tail(boundary: String) -> Data {
        Data("\(crlf)--\(boundary)--\(crlf)".utf8)
    }

    /// The same body, written to a **file**, with the file part streamed in
    /// off disk.
    ///
    /// ### Why the Music door still needs a multipart body at all
    ///
    /// D43 moved uploads onto `/api/files/upload-stream`, whose body IS the
    /// file (`web/backend/api/files.py:1036`) — no envelope, nothing in
    /// memory. The Music page cannot use it. `/upload-stream` into
    /// `core:music` would land the track in the library and then ask the
    /// core's **admin-only** sweep to index it (`files.py:1250`'s
    /// `_trigger_reindex`, which proxies `/v1/admin/library/reindex` with the
    /// caller's own credentials); from a phone, which holds a device token
    /// and no admin session, that call fails and the track is invisible until
    /// the indexer's next startup sweep. `/api/music/library/upload` indexes
    /// it on the spot (`music.py:607`), which is the reason the Music page
    /// has a door of its own (`PhoneUpload.kt:36`) — so the door stays, and
    /// what changes is where its body lives.
    ///
    /// So: the envelope is written to `out`, the file's bytes are copied in a
    /// chunk at a time, and the closing delimiter is appended. The app holds
    /// one chunk, never the file. It costs one copy on **disk** for a music
    /// track, which is a few megabytes and the honest price of immediate
    /// indexing.
    ///
    /// - Parameters:
    ///   - file: the file to send. Read with a `FileHandle`, so a four-
    ///     gigabyte one would work here too — nothing in this function is
    ///     about size.
    ///   - out: where the body is written. Replaced if it is there.
    /// - Returns: the body's length in bytes, which is what a caller needs to
    ///   report progress against.
    @discardableResult
    public static func spool(
        fields: [(name: String, value: String)],
        fileField: String = "files",
        fileName: String,
        mime: String,
        file: URL,
        boundary: String,
        to out: URL
    ) throws -> Int64 {
        let prologue = head(
            fields: fields, fileField: fileField,
            fileName: fileName, mime: mime, boundary: boundary
        )
        let epilogue = tail(boundary: boundary)

        try? FileManager.default.removeItem(at: out)
        guard FileManager.default.createFile(atPath: out.path, contents: nil),
              let sink = try? FileHandle(forWritingTo: out)
        else { throw PhoneMediaError.unreadable(name: fileName) }
        defer { try? sink.close() }
        let source = try FileHandle(forReadingFrom: file)
        defer { try? source.close() }

        try sink.write(contentsOf: prologue)
        var copied = 0
        // A megabyte at a time, the number `/upload-stream` writes with
        // (`UPLOAD_CHUNK_BYTES`, `files.py:1012`). `read(upToCount:)` answers
        // nil **or** an empty `Data` at the end of a file depending on the
        // platform's mood, so both end the loop.
        while let chunk = try source.read(upToCount: 1024 * 1024), !chunk.isEmpty {
            try sink.write(contentsOf: chunk)
            copied += chunk.count
        }
        try sink.write(contentsOf: epilogue)
        return Int64(prologue.count + copied + epilogue.count)
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
