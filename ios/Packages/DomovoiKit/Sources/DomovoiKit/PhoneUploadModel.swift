import Foundation
import Observation

/// The two upload doors (`net/PhoneUpload.kt:41`, `:44`).
public enum PhoneUploadPath {

    /// Lands in `MUSIC_DIR/uploads` and is **indexed at once**
    /// (`music.py:607`). `/api/files/upload` into `core:music` would ask the
    /// core's admin-only sweep and index nothing, which is why the Music page
    /// has a door of its own (`PhoneUpload.kt:36`).
    public static let music = "/api/music/library/upload"

    /// A library id and a folder (`files.py:866`). Its `device_id` is
    /// **required** and typed 3–64, so a phone with no registered id cannot
    /// use this door at all — see ``PhoneUploadModel/upload(_:to:)``.
    public static let files = "/api/files/upload"
}

/// Sending one file from this phone to the house.
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
/// ### What is not, and why there is no control for it
///
/// * **No byte-level progress.** Android streams the bytes and reports whole
///   percent as it goes (`PhoneUpload.kt:52`); here the body is built in
///   memory and handed to ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``,
///   which reports nothing until it answers. So the ring is indeterminate,
///   ``PhoneUploadButtonState/busy(percent:)`` is always built with `nil`
///   here, and there is no "47%" that would be a guess. The percent is kept
///   in the ported rule because the rule is shared with Android's and because
///   `/api/files/upload-stream` is the item that will fill it in.
/// * **Nothing over ``cap``.** 64 MiB, the same number and the same reasoning
///   as ``FileSaveModel/cap``: `raw` takes `body: Data`, so the file plus its
///   multipart copy are both in memory. A row over it is drawn with **no
///   upload control** and a line saying how big it is
///   (``PhoneMediaStrings/tooBigToUpload(_:)``); the refusal here is the
///   floor under that, not the thing a person is meant to meet.
@MainActor
@Observable
public final class PhoneUploadModel {

    /// The largest file this app will post. See "What is not" above.
    ///
    /// The literal, not ``FileSaveModel/cap`` itself: that one is a static of
    /// a `@MainActor` type, so reading it to initialise this one would be a
    /// main-actor read from a non-isolated initialiser. The **number** and
    /// the reasoning are the same deliberately — both are `Data` through
    /// `raw` — and a test asserts the two are equal, so they cannot drift.
    ///
    /// `nonisolated`, because a row's own "is this too big to offer a control
    /// for" question is asked from wherever a row is built — including inside
    /// the `@Sendable` closures that read the bytes — and a `@MainActor`
    /// type's static would be main-actor isolated like everything else on it.
    public nonisolated static let cap: Int64 = 64 * 1024 * 1024

    public let kind: PhoneUploadKind

    /// The server's say on uploads from this device, re-read when the house
    /// changes because ``ApiRead`` is keyed to the address. Two requests in
    /// one read — the libraries, then one row of the probe library — exactly
    /// as `fetchWriteStatus` makes them (`DeviceUpload.kt:290`).
    public let status: ApiRead<PhoneServerWriteStatus>

    /// What the person is told. One at a time, as the shell draws them.
    public private(set) var toast: String?

    /// Bytes already in hand, for a file that came from a picker rather than
    /// from a list.
    ///
    /// PHPicker and the document picker both run **out of process** and hand
    /// over the bytes themselves — there is no asset and no durable file URL
    /// to read later, and the document picker's security scope is closed by
    /// the time any `await` returns. So those bytes are parked here and
    /// ``upload(_:to:)`` takes them in place of calling ``read``.
    ///
    /// Dropped in ``finish(_:_:_:)``, so a picked file is not held in memory
    /// after its upload has answered.
    private var picked: [String: Data] = [:]

    /// The rows whose file is on its way, by ``PhoneUploadKey``.
    ///
    /// A set and not a map of percents: nothing can report one (above).
    private var running: Set<String> = []

    /// Which house an in-flight upload belongs to.
    ///
    /// A reply that lands after the app has moved house must not be reported
    /// as the new house's: a library id and a device id are both reusable, so
    /// "uploaded … to Documents" would look right under a server that never
    /// saw the file. [[read-after-await-wrong-key]] — captured **before** the
    /// `await`, and `PhoneUploadModelTests` switches inside the round trip.
    private var epoch = 0

    private let deviceID: String?
    private let send: @Sendable (_ path: String, _ body: Data, _ contentType: String) async throws -> Data
    private let read: @Sendable (PhoneMediaItem) async throws -> Data
    private let newBoundary: @Sendable () -> String

    /// - Parameters:
    ///   - status: the write-status read. Built by the convenience
    ///     initialiser from a container, or by a test from its own base.
    ///   - read: the phone's bytes. ``PhoneMediaReading/bytes(of:)``, or a
    ///     test's.
    ///   - send: the POST. Separated from ``DomovoiClient`` so the body this
    ///     model builds can be asserted byte for byte.
    public init(
        kind: PhoneUploadKind,
        status: ApiRead<PhoneServerWriteStatus>,
        deviceID: String?,
        read: @Sendable @escaping (PhoneMediaItem) async throws -> Data,
        send: @Sendable @escaping (_ path: String, _ body: Data, _ contentType: String) async throws -> Data,
        boundary: @Sendable @escaping () -> String = { PhoneMultipart.boundary() }
    ) {
        self.kind = kind
        self.status = status
        self.deviceID = deviceID
        self.read = read
        self.send = send
        self.newBoundary = boundary
    }

    /// The model a screen builds: the status read keyed to `container`'s
    /// address, and the client's own POST.
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
            read: { try await source.bytes(of: $0) },
            send: { path, body, contentType in
                // Ninety seconds: a 64 MiB file over a slow link is worth
                // waiting for, and the client's default is tuned for a
                // screen's reads. `raw` sends `X-Requested-With` on every
                // request (`DomovoiClient.swift:619`), which is what
                // `/api/files/upload`'s CSRF guard requires
                // (`csrf_guard.py:34`).
                try await client.raw(
                    method: "POST",
                    path: path,
                    body: body,
                    contentType: contentType,
                    timeout: 90
                )
            }
        )
    }

    // MARK: - what the server said

    /// `DeviceUpload.kt:228`. Nil while the server has not answered.
    public var target: PhoneUploadTarget? { phoneUploadTarget(kind, status.data) }

    /// The card above the list, when there is one (`DeviceUpload.kt:230`).
    public var refused: (reason: String, blocked: Bool)? { target?.refusal }

    /// `DeviceUpload.kt:232`.
    public func state(for key: String) -> PhoneUploadButtonState {
        phoneUploadButtonState(target: target, inFlight: running.contains(key), percent: nil)
    }

    /// Whether this row gets an upload control at all. A row over the cap
    /// gets none — the owner's rule, and the reason the cap is checked here
    /// rather than only inside ``upload(_:to:)``.
    public func offersUpload(_ item: PhoneMediaItem) -> Bool {
        !item.isDir && !isTooBig(item)
    }

    public func isTooBig(_ item: PhoneMediaItem) -> Bool {
        guard let size = item.sizeBytes else { return false }
        return size > Self.cap
    }

    /// The screen appeared.
    public func start() { status.refresh() }

    /// The app moved house: everything in flight belonged to the last one.
    public func serverChanged() {
        epoch += 1
        running.removeAll()
        // The bytes of a pick belonged to the house that has gone.
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
    /// (``PhonePickToSendView``), and what they hand over is `Data`.
    ///
    /// A picked file over ``cap`` gets the refusal as a **toast** rather than
    /// a missing control, which is the one place this item departs from "a
    /// control that cannot do what it says is not drawn": the picker *is* the
    /// control, nothing is known about the file until after the pick, and a
    /// pick that produced silence would be worse than one that says the file
    /// is too big.
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
        picked[item.id] = bytes
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
    /// and every one of them lets go of a picked file's bytes: a silent
    /// return left a tap that did nothing (the owner's no-misleading-controls
    /// rule) and held a 50 MiB video for the life of the screen.
    public func upload(_ item: PhoneMediaItem, to dest: PhoneUploadDest? = nil) {
        guard gate() else {
            picked.removeValue(forKey: item.id)
            return
        }
        guard let to = dest ?? destination(for: item) else {
            // Reachable: `.pick` with no readable library list. The rules now
            // refuse that case before the button is drawn
            // (``phoneUploadTarget(_:_:)``), so this is the floor under it
            // rather than the path — and it names the reason either way.
            picked.removeValue(forKey: item.id)
            toast = PhoneMediaStrings.couldNotReadLibraries(status.data?.error)
            return
        }
        let key = PhoneUploadKey.of(item, kind)
        if let size = item.sizeBytes, size > Self.cap {
            // No control is drawn on such a ROW, so this is a floor — but it
            // is the real path for a PICKED file, whose size is not known
            // until after the pick (``uploadPicked(name:bytes:kind:)``), so
            // the bytes are let go of here too rather than held for an
            // upload that will never run.
            picked.removeValue(forKey: item.id)
            toast = PhoneMediaStrings.tooBigToUpload(size)
            return
        }
        guard !running.contains(key) else {
            // The running upload captured its own bytes before its `await`,
            // so this copy is nobody's and must not be held: the key is the
            // row's ORIGIN, which for a pick is `/picked/<name>` with no
            // size in it, so a second pick of a same-named file of a
            // different size lands here under an id of its own.
            picked.removeValue(forKey: item.id)
            toast = PhoneMediaStrings.alreadyUploading(item.name)
            return
        }
        // The Files door's `device_id` is required and typed 3–64
        // (`files.py:866`); without one the request is a 422 the person can
        // do nothing about, so it is refused here, named.
        var path = PhoneUploadPath.files
        var fields: [(name: String, value: String)] = []
        switch to {
        case .musicLibrary:
            path = PhoneUploadPath.music
            // Optional on this door (`music.py:607`), so an unregistered
            // phone may still upload a track — and the Files block is applied
            // by the server before anything is written (`music.py:644`).
            if let deviceID, !deviceID.isEmpty { fields = [(name: "device_id", value: deviceID)] }
        case .library(let id, _, let folder):
            guard let deviceID, deviceID.count >= 3 else {
                toast = FileStrings.noDeviceID
                return
            }
            fields = [
                (name: "library_id", value: id),
                (name: "path", value: folder),
                (name: "device_id", value: deviceID),
            ]
        }
        running.insert(key)
        toast = PhoneMediaStrings.uploadingTo(item.name, to.whereItLands)

        // Captured BEFORE the await, every one of them: which house this is
        // for, where it is going, and what it is called. A reply read against
        // whatever the model holds when it lands is the defect this item was
        // warned about.
        let mine = epoch
        let name = item.name
        // Captured before the `await` with everything else: a picked file's
        // bytes belong to the pick that started this upload.
        let inHand = picked[item.id]
        let rowID = item.id
        let boundary = newBoundary()
        let bytesOf = read
        let post = send
        let row = item
        Task { @MainActor [weak self] in
            do {
                // Not `inHand ?? bytesOf(row)`: `??`'s right side is an
                // AUTOCLOSURE, and an `async` call cannot go in one.
                let bytes: Data
                if let inHand {
                    bytes = inHand
                } else {
                    bytes = try await bytesOf(row)
                }
                // Read through the optional rather than unwrapping `self`:
                // `guard let self` once per scope is all Swift allows, and
                // the one after the POST is the one that must not be dropped.
                // Nothing is posted to a house the person has left.
                guard self?.epoch == mine else { return }
                guard Int64(bytes.count) <= Self.cap else {
                    throw PhoneMediaError.tooBig(name: name, size: Int64(bytes.count))
                }
                let body = PhoneMultipart.body(
                    fields: fields,
                    fileName: name,
                    mime: row.mimeType,
                    bytes: bytes,
                    boundary: boundary
                )
                let answer = try await post(path, body, PhoneMultipart.contentType(boundary: boundary))
                guard let self, mine == self.epoch else { return }
                let reply = (try? JSONDecoder().decode(PhoneUploadReply.self, from: answer))
                    ?? PhoneUploadReply(saved: 0)
                self.finish(key, mine, phoneUploadDoneText(name, to, reply), picked: rowID)
            } catch is CancellationError {
                guard let self, mine == self.epoch else { return }
                self.finish(key, mine, nil, picked: rowID)
            } catch {
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
        running.remove(key)
        // A picked file's bytes are not kept after its upload has answered.
        // The row's own id, passed down — never parsed back out of the
        // upload key, which is a different string with its own prefix.
        if let id { picked.removeValue(forKey: id) }
        if let said { toast = said }
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
