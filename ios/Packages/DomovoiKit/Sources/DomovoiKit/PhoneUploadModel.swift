import Foundation
import Observation

/// The upload doors (`net/PhoneUpload.kt:41`, `:44`) — and the streamed one
/// D43 moved onto.
public enum PhoneUploadPath {

    /// Lands in `MUSIC_DIR/uploads` and is **indexed at once**
    /// (`music.py:607`). `/api/files/upload` into `core:music` would ask the
    /// core's admin-only sweep and index nothing, which is why the Music page
    /// has a door of its own (`PhoneUpload.kt:36`) — and the reason the Music
    /// page is still the one upload here that carries a multipart body (see
    /// ``PhoneMultipart/spool(fields:fileField:fileName:mime:file:boundary:to:)``).
    public static let music = "/api/music/library/upload"

    /// A library id and a folder (`files.py:866`). The multipart door, kept
    /// for nothing now — ``streamed(libraryID:folder:name:deviceID:)`` is what
    /// a file or a video goes through — and left here because the path is the
    /// Kotlin's and a reader comparing the two files should find it.
    public static let files = "/api/files/upload"

    /// **The streamed door** (`files.py:1189`): the body IS the file, there
    /// is no multipart, and the server writes it to disk a chunk at a time as
    /// it arrives (`files.py:1036-1048`). Its ceiling is
    /// `MAX_UPLOAD_FILE_BYTES` — 8 GiB (`files.py:1049`) — against the
    /// 64 MiB this app used to allow.
    ///
    /// - Parameters:
    ///   - folder: the folder on screen, which must already exist. `""` is
    ///     the library's root.
    ///   - name: the file's place **under** that folder, which for a phone
    ///     upload is just its name: this app never sends a tree, so there is
    ///     one segment and no folder to make.
    ///   - deviceID: required and typed 3–64 on this route, so a phone with
    ///     no registered id cannot use it — refused by name in
    ///     ``PhoneUploadModel/upload(_:to:)`` rather than as a 422.
    ///
    /// `on_conflict` is left at its default, `keep_both`, which is what
    /// `/api/files/upload` has always done (`files.py:1216`'s docstring says
    /// so) — so this change does not quietly start replacing people's files.
    /// `reindex` is left at its default too: one file per request and one
    /// reindex after it, because there is no batch here to end with
    /// `/upload-done`.
    public static func streamed(
        libraryID: String,
        folder: String,
        name: String,
        deviceID: String
    ) -> String {
        "/api/files/upload-stream?library_id=\(FilePath.esc(libraryID))"
            + "&rel_path=\(FilePath.esc(name))"
            + "&path=\(FilePath.esc(folder))"
            + "&device_id=\(FilePath.esc(deviceID))"
    }
}

/// Sending one file from this phone to the house, **of any size**.
///
/// `ui/screens/local/DeviceUpload.kt:221-319`'s `DeviceUploader`, with
/// `net/PhoneUpload.kt:175`'s `PhoneUploads` folded in — on Android those are
/// two objects because the uploader is rebuilt on every composition and the
/// in-flight map has to outlive it. Here one `@Observable` model does both,
/// held by the screen, which is the same lifetime: **the upload never runs on
/// the row**, because a lazy row is recycled while it scrolls and a `Task` on
/// one is cancelled mid-file (the standing rule; it cost a real bug on this
/// track).
///
/// ### What D43 changed
///
/// This file used to read the whole file with `source.bytes(of:)`, build a
/// multipart body around it in memory, and POST it through
/// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
/// — so a 64 MiB ceiling, a row over it with no upload control, and "backing
/// up phone videos to the house" not working for most phone videos. Now:
///
/// * A file or a video is sent to ``PhoneUploadPath/streamed(libraryID:folder:name:deviceID:)``
///   with **the file as the body**, off disk, through
///   ``DomovoiClient/uploadFile(method:path:fileURL:contentType:trackPairing:onProgress:)``.
///   Nothing proportional to the file is in this app's memory.
/// * A **track** still goes through the Music door, because that is the only
///   one that indexes it where a phone can see it — with its multipart
///   envelope written to a file rather than built in memory
///   (``PhoneMultipart/spool(fields:fileField:fileName:mime:file:boundary:to:)``,
///   which explains the trade).
/// * The bytes come from ``PhoneMediaReading/fileURL(of:)``, which hands back
///   a file **and what to do when the upload is finished with it** — the
///   security scope a picked folder's file needs held open, or the copy to
///   delete.
/// * ``PhoneUploadButtonState/busy(percent:)`` finally carries a number.
///
/// ### What is not, and why there is no control for it
///
/// * **No background upload.** A backup that runs while the phone is in
///   somebody's pocket wants a background `URLSession`, which is its own
///   session, its own out-of-client delegate and a relaunch path. It is
///   written down in `design-notes/ios/batch-2026-11-03/transfers/DECISIONS.md`
///   rather than half-built: an upload runs while the app is open, which is
///   what the row says.
/// * **No pause and resume.** `/upload-stream` has no range or offset
///   (`files.py:1189`), so there is nothing to resume to. A cancelled upload
///   leaves nothing on the server — it writes to a hidden part-file and
///   renames it only when complete (`files.py:1254`).
/// * **Nothing over 8 GiB**, which is the server's ceiling and not this
///   app's: it answers 413 with its own sentence, naming the size, and
///   ``phoneUploadFailureText(_:_:)`` shows it.
@MainActor
@Observable
public final class PhoneUploadModel {

    public let kind: PhoneUploadKind

    /// The server's say on uploads from this device, re-read when the house
    /// changes because ``ApiRead`` is keyed to the address. Two requests in
    /// one read — the libraries, then one row of the probe library — exactly
    /// as `fetchWriteStatus` makes them (`DeviceUpload.kt:290`).
    public let status: ApiRead<PhoneServerWriteStatus>

    /// What the person is told. One at a time, as the shell draws them.
    public private(set) var toast: String?

    /// Bytes already in hand, for a file that came from a picker rather than
    /// from a list — **spooled to disk the moment they arrive**.
    ///
    /// PHPicker and the document picker both run **out of process** and hand
    /// over the bytes themselves; there is no asset and no durable file URL
    /// to read later, and the document picker's security scope is closed by
    /// the time any `await` returns. So those bytes are written to a file of
    /// this app's own here and the `Data` is let go of at once — which is the
    /// difference between holding a picked 50 MiB video for the life of the
    /// screen and holding nothing.
    private var picked: [String: URL] = [:]

    /// The rows whose file is on its way, by ``PhoneUploadKey``, and how far
    /// each has got.
    ///
    /// A map and not the set it used to be: nothing could report a percent
    /// when the body was built in memory, and now everything can.
    private var running: [String: Int?] = [:]

    /// Which house an in-flight upload belongs to.
    ///
    /// A reply that lands after the app has moved house must not be reported
    /// as the new house's: a library id and a device id are both reusable, so
    /// "uploaded … to Documents" would look right under a server that never
    /// saw the file. [[read-after-await-wrong-key]] — captured **before** the
    /// `await`, and `PhoneUploadModelTests` switches inside the round trip.
    private var epoch = 0

    private let deviceID: String?
    private let send: PhoneUploadSend
    private let file: @Sendable (PhoneMediaItem) async throws -> PhoneUploadFile
    private let newBoundary: @Sendable () -> String

    /// - Parameters:
    ///   - status: the write-status read. Built by the convenience
    ///     initialiser from a container, or by a test from its own base.
    ///   - file: the phone's file. ``PhoneMediaReading/fileURL(of:)``, or a
    ///     test's.
    ///   - send: the POST. Separated from ``DomovoiClient`` so a test can
    ///     read the file that was sent and the progress that was reported.
    public init(
        kind: PhoneUploadKind,
        status: ApiRead<PhoneServerWriteStatus>,
        deviceID: String?,
        file: @Sendable @escaping (PhoneMediaItem) async throws -> PhoneUploadFile,
        send: @escaping PhoneUploadSend,
        boundary: @Sendable @escaping () -> String = { PhoneMultipart.boundary() }
    ) {
        self.kind = kind
        self.status = status
        self.deviceID = deviceID
        self.file = file
        self.send = send
        self.newBoundary = boundary
    }

    /// The model a screen builds: the status read keyed to `container`'s
    /// address, and the client's own streamed POST.
    public convenience init(
        kind: PhoneUploadKind,
        in container: AppContainer,
        deviceID: String?,
        source: any PhoneMediaReading
    ) {
        let client = container.client
        let device = deviceID
        let read = ApiRead<PhoneServerWriteStatus>(in: container, eventTypes: FilesModel.listEvents) {
            await Self.fetchWriteStatus(kind: kind, deviceID: device) { path in
                try await client.get(path, as: FileLibraries.self)
            } browse: { path in
                try await client.get(path, as: FileBrowse.self)
            }
        }
        self.init(
            kind: kind,
            status: read,
            deviceID: deviceID,
            file: { try await source.fileURL(of: $0) },
            send: { path, body, contentType, onProgress in
                // **No timeout.** The old multipart POST passed 90 s because
                // it was waiting on one answer for a body it had already
                // built; a streamed upload of a multi-gigabyte video is a
                // journey, and `uploadFile` deliberately sets no ceiling on
                // the whole of one (see its note). A stalled upload is still
                // caught, by the session's 60 s between arrivals.
                try await client.uploadFile(
                    method: "POST",
                    path: path,
                    fileURL: body,
                    contentType: contentType,
                    onProgress: onProgress
                )
            }
        )
    }

    // MARK: - what the server said

    /// `DeviceUpload.kt:228`. Nil while the server has not answered.
    public var target: PhoneUploadTarget? { phoneUploadTarget(kind, status.data) }

    /// The card above the list, when there is one (`DeviceUpload.kt:230`).
    public var refused: (reason: String, blocked: Bool)? { target?.refusal }

    /// `DeviceUpload.kt:232`, with the percent the streamed upload reports.
    public func state(for key: String) -> PhoneUploadButtonState {
        phoneUploadButtonState(
            target: target,
            inFlight: running.keys.contains(key),
            percent: running[key] ?? nil
        )
    }

    /// Whether this row gets an upload control at all.
    ///
    /// **Every file does, since D43.** It used to also ask
    /// `!isTooBig(item)`, and a row over 64 MiB got a line saying how big it
    /// was instead of a control. There is no ceiling in this app any more:
    /// the server's is 8 GiB and it says so itself, with the size in the
    /// sentence, which is a refusal nobody needs a client-side guess for.
    public func offersUpload(_ item: PhoneMediaItem) -> Bool { !item.isDir }

    /// The screen appeared.
    public func start() { status.refresh() }

    /// The app moved house: everything in flight belonged to the last one.
    public func serverChanged() {
        epoch += 1
        running.removeAll()
        // The file a pick spooled belonged to the house that has gone.
        for url in picked.values { try? FileManager.default.removeItem(at: url) }
        picked.removeAll()
        toast = nil
    }

    public func setToast(_ said: String?) { toast = said }

    // MARK: - the tap

    /// True when an upload may go ahead now; otherwise says why, **and asks
    /// the server again** (`DeviceUpload.kt:236`) — a refusal the page is
    /// showing may have been lifted since it opened.
    @discardableResult
    public func gate() -> Bool {
        guard let target else {
            toast = PhoneMediaStrings.stillAskingTheServer
            return false
        }
        if case .refused(let reason, _) = target {
            toast = reason
            status.refresh()
            return false
        }
        return true
    }

    /// Where `item` would land: the page's own destination, or — for a target
    /// of ``PhoneUploadTarget/pick``, which has no sheet on this phone — the
    /// **default** library for the file's kind, named in the toast
    /// (DECISIONS).
    public func destination(for item: PhoneMediaItem) -> PhoneUploadDest? {
        switch target {
        case .direct(let dest):
            return dest
        case .pick:
            let libraries = status.data?.libraries ?? []
            let entry = FileEntry(name: item.name, kind: item.kind.rawValue)
            guard let id = phoneDefaultHomeLibrary(entry, libraries),
                  let library = libraries.first(where: { $0.id == id })
            else { return nil }
            return .library(id: id, label: library.label, path: "")
        case .refused, nil:
            return nil
        }
    }

    /// Send something the person picked, whose bytes are already in hand.
    ///
    /// The one upload in this item that is **not** a row: PHPicker and the
    /// document picker are the two doors that need no permission at all
    /// (``PhonePickToSendView``), and what they hand over is `Data`. It is
    /// written straight to a file here, so the rest of this model has one
    /// shape to deal with and nothing holds a picked video in memory.
    ///
    /// A pick this app cannot write to its own temporary directory is said
    /// out loud rather than dropped: the picker *is* the control, so a pick
    /// that produced silence would be worse than one that explains itself.
    public func uploadPicked(name: String, bytes: Data, kind itemKind: PhoneMediaKind) {
        let item = PhoneMediaItem(
            // The size is in the key, so picking a different file of the same
            // name is a different upload rather than "already uploading".
            id: "picked:\(name):\(bytes.count)",
            libraryID: "phone:picked",
            name: name,
            kind: itemKind,
            sizeBytes: Int64(bytes.count),
            origin: .file(URL(fileURLWithPath: "/picked/\(name)"))
        )
        do {
            let url = PhoneUploadSpool.fileURL(in: try PhoneUploadSpool.directory(), for: name)
            try bytes.write(to: url, options: .atomic)
            picked[item.id] = url
        } catch {
            toast = phoneUploadFailureText(name, PhoneMediaError.unreadable(name: name))
            return
        }
        upload(item)
    }

    /// Send `item` to `dest`, or to wherever ``destination(for:)`` says.
    ///
    /// `DeviceUpload.kt:253`, with its order kept: the gate first, then the
    /// second-tap refusal, then the "uploading…" toast, and only then the
    /// request. A second tap **does not** start a second upload — it says so
    /// (`DeviceUpload.kt:257`), which is the difference between one file
    /// posted and two.
    ///
    /// **Every path out of here either starts an upload or says something**,
    /// and every one of them lets go of a picked file: a silent return left a
    /// tap that did nothing (the owner's no-misleading-controls rule) and a
    /// spooled copy nobody would ever delete.
    public func upload(_ item: PhoneMediaItem, to dest: PhoneUploadDest? = nil) {
        guard gate() else { return dropPick(item.id) }
        guard let to = dest ?? destination(for: item) else {
            // Reachable: `.pick` with no readable library list. The rules now
            // refuse that case before the button is drawn
            // (``phoneUploadTarget(_:_:)``), so this is the floor under it
            // rather than the path — and it names the reason either way.
            dropPick(item.id)
            toast = PhoneMediaStrings.couldNotReadLibraries(status.data?.error)
            return
        }
        let key = PhoneUploadKey.of(item, kind)
        guard running[key] == nil else {
            // The running upload took its own file before its `await`, so
            // this pick is nobody's and must not be left on disk: the key is
            // the row's ORIGIN, which for a pick is `/picked/<name>` with no
            // size in it, so a second pick of a same-named file of a
            // different size lands here under an id of its own.
            dropPick(item.id)
            toast = PhoneMediaStrings.alreadyUploading(item.name)
            return
        }
        // The Files door's `device_id` is required and typed 3–64
        // (`files.py:866`, and `:1199` on the streamed one); without one the
        // request is a 422 the person can do nothing about, so it is refused
        // here, named.
        let plan: PhoneUploadRequest
        switch to {
        case .musicLibrary:
            // Optional on this door (`music.py:607`), so an unregistered
            // phone may still upload a track — and the Files write block is
            // applied by the server before anything is written
            // (`music.py:644`).
            let fields: [(name: String, value: String)] = (deviceID?.isEmpty == false)
                ? [(name: "device_id", value: deviceID!)]
                : []
            plan = .multipart(path: PhoneUploadPath.music, fields: fields)
        case .library(let id, _, let folder):
            guard let deviceID, deviceID.count >= 3 else {
                toast = FileStrings.noDeviceID
                return dropPick(item.id)
            }
            plan = .stream(path: PhoneUploadPath.streamed(
                libraryID: id,
                folder: folder,
                name: PhoneMultipart.safePartName(item.name),
                deviceID: deviceID
            ))
        }
        running[key] = nil
        toast = PhoneMediaStrings.uploadingTo(item.name, to.whereItLands)

        // Captured BEFORE the await, every one of them: which house this is
        // for, where it is going, and what it is called. A reply read against
        // whatever the model holds when it lands is the defect this item was
        // warned about.
        let mine = epoch
        let name = item.name
        let inHand = picked[item.id]
        let rowID = item.id
        let boundary = newBoundary()
        let fileOf = self.file
        let post = send
        let row = item
        Task { @MainActor [weak self] in
            // The file, and what to do with it afterwards. **One `release`
            // per `fileURL(of:)`, on every path out**, which is why it is a
            // local and not something a `defer` deep in a branch owns: a
            // security scope left open is a grant this app keeps for ever,
            // and a spooled copy never released is somebody's video sitting
            // in the container until iOS feels like clearing it.
            var handed: PhoneUploadFile?
            var envelope: URL?
            func letGo() {
                handed?.release()
                handed = nil
                if let envelope { try? FileManager.default.removeItem(at: envelope) }
                envelope = nil
            }
            do {
                let sending: URL
                if let inHand {
                    // A pick is already a file of this app's own, so there is
                    // nothing to scope and nothing to copy; it is deleted by
                    // `finish`, under its row id.
                    sending = inHand
                } else {
                    let got = try await fileOf(row)
                    handed = got
                    sending = got.url
                }
                guard self?.epoch == mine else { return letGo() }
                let answer: Data
                switch plan {
                case .stream(let path):
                    answer = try await Self.run(
                        post, path, sending, row.mimeType, key, mine, self
                    )
                case .multipart(let path, let fields):
                    let spool = PhoneUploadSpool.fileURL(
                        in: try PhoneUploadSpool.directory(),
                        for: "\(name).multipart"
                    )
                    try PhoneMultipart.spool(
                        fields: fields,
                        fileName: name,
                        mime: row.mimeType,
                        file: sending,
                        boundary: boundary,
                        to: spool
                    )
                    envelope = spool
                    guard self?.epoch == mine else { return letGo() }
                    answer = try await Self.run(
                        post, path, spool, PhoneMultipart.contentType(boundary: boundary),
                        key, mine, self
                    )
                }
                letGo()
                guard let self, mine == self.epoch else { return }
                let reply = (try? JSONDecoder().decode(PhoneUploadReply.self, from: answer))
                    ?? PhoneUploadReply(saved: 0)
                self.finish(key, mine, phoneUploadDoneText(name, to, reply), picked: rowID)
            } catch is CancellationError {
                letGo()
                guard let self, mine == self.epoch else { return }
                self.finish(key, mine, nil, picked: rowID)
            } catch {
                letGo()
                guard let self, mine == self.epoch else { return }
                self.finish(key, mine, phoneUploadFailureText(name, error), picked: rowID)
                // A refusal the button did not know about — blocked since the
                // page opened — so ask again, and it shows
                // (`DeviceUpload.kt:283`).
                if (error as? DomovoiError)?.failure?.status == 403 { self.status.refresh() }
            }
        }
    }

    /// The row is done: stop its ring and say what happened, unless the house
    /// changed under it.
    private func finish(_ key: String, _ mine: Int, _ said: String?, picked id: String?) {
        guard mine == epoch else { return }
        running.removeValue(forKey: key)
        // A picked file's spooled copy is not kept after its upload has
        // answered. The row's own id, passed down — never parsed back out of
        // the upload key, which is a different string with its own prefix.
        if let id { dropPick(id) }
        if let said { toast = said }
    }

    /// Forget a pick and delete the file it spooled. Safe for an id that is
    /// not a pick, which is every row.
    private func dropPick(_ id: String) {
        guard let url = picked.removeValue(forKey: id) else { return }
        try? FileManager.default.removeItem(at: url)
    }

    /// How far `key`'s upload has got, from a `URLSession` thread's numbers.
    ///
    /// `mine` and not the model's own `epoch`: a tick that lands after the
    /// app has moved house must not move a new house's ring.
    private func note(_ key: String, _ mine: Int, sent: Int64, of total: Int64) {
        guard mine == epoch, running.keys.contains(key) else { return }
        running[key] = transferPercent(sent: sent, of: total)
    }

    // MARK: - the request

    /// Make the request and answer with its body.
    ///
    /// Static, so the `Task` above holds nothing but what it captured, and
    /// **it returns the body** rather than putting it anywhere: three rows
    /// can be uploading at once, so a shared place to leave the last answer
    /// in would hand one row's reply to another ("uploaded … to Documents"
    /// under a file that failed).
    private static func run(
        _ post: PhoneUploadSend,
        _ path: String,
        _ body: URL,
        _ contentType: String,
        _ key: String,
        _ mine: Int,
        _ model: PhoneUploadModel?
    ) async throws -> Data {
        try await post(path, body, contentType) { sent, total in
            // The callback is on a `URLSession` thread; the ring is on the
            // main actor. `mine` goes with it, so a tick from the house the
            // app has left moves nothing.
            Task { @MainActor in model?.note(key, mine, sent: sent, of: total) }
        }
    }

    // MARK: - the status read

    /// `fetchWriteStatus` (`DeviceUpload.kt:290`): the libraries, then one row
    /// of the probe library for this device's `writable` / `blocked_reason`.
    ///
    /// **A failed browse is not a refusal** (`DeviceUpload.kt:306`): the
    /// upload itself still meets the server's rules and would answer with its
    /// own 403, so the status keeps `writable == true` and lets the door
    /// decide. A failed *libraries* read is different — it is recorded, and
    /// the per-kind rules in ``phoneUploadTarget(_:_:)`` say what each page
    /// does without them.
    ///
    /// `nonisolated` and taking its two requests as closures so that the
    /// convenience initialiser can hand it a client and a test can hand it
    /// two canned answers.
    nonisolated static func fetchWriteStatus(
        kind: PhoneUploadKind,
        deviceID: String?,
        libraries: @Sendable (String) async throws -> FileLibraries,
        browse: @Sendable (String) async throws -> FileBrowse
    ) async -> PhoneServerWriteStatus {
        var error: String?
        var libs: [FileLibrary]?
        do {
            libs = try await libraries(FilePath.libraries).libraries
        } catch is CancellationError {
            return PhoneServerWriteStatus(libraries: nil, error: "cancelled")
        } catch let trouble {
            error = (trouble as? DomovoiError)?.message ?? "\(trouble)"
        }
        guard let probe = phoneProbeLibrary(kind, libs) else {
            return PhoneServerWriteStatus(libraries: libs, error: error)
        }
        // One row is plenty: only `writable` / `blocked_reason` are read.
        let path = FilePath.browse(libraryID: probe, deviceID: deviceID) + "&limit=1"
        guard let folder = try? await browse(path) else {
            return PhoneServerWriteStatus(libraries: libs, error: error)
        }
        return PhoneServerWriteStatus(
            libraries: libs,
            writable: folder.writable,
            blockedReason: folder.blockedReason,
            error: error
        )
    }
}

/// The POST, as this model needs it: a path, a **file**, its type, and
/// somewhere to report progress.
///
/// A named type because the signature has four parameters and one of them is
/// a closure, and an inline one of those in three places is three chances to
/// get the order wrong.
public typealias PhoneUploadSend = @Sendable (
    _ path: String,
    _ fileURL: URL,
    _ contentType: String,
    _ onProgress: @Sendable @escaping (Int64, Int64) -> Void
) async throws -> Data

/// Which door an upload is going through, decided before the `Task` starts.
private enum PhoneUploadRequest: Sendable {
    /// `/api/files/upload-stream`: the body is the file.
    case stream(path: String)
    /// `/api/music/library/upload`: the body is an envelope around the file,
    /// written to disk (``PhoneMultipart/spool(fields:fileField:fileName:mime:file:boundary:to:)``).
    case multipart(path: String, fields: [(name: String, value: String)])
}
