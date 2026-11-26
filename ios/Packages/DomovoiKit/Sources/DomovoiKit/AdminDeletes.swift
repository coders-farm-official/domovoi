import Foundation
import Observation

// ---------------------------------------------------------------------------
// Deletes and blocks as admin on this phone (1.5.0; design §1): delete a
// music track, delete a file on the server, remove an audiobook from the
// server, and lift a phone's Files block or queue block. The port of
// Android's `ui/screens/settings/AdminDeletes.kt`, rule for rule and word
// for word.
//
// Each is admin on the server (DELETE /api/music/library/{id}, POST
// /api/files/delete, DELETE /api/files/device-blocks/{id} and
// /api/music/queue-blocks/{id}; the two lists are admin reads), so each is
// offered only while this phone holds admin and never on a shared screen,
// through ``AdminSession/act(_:)`` (the lists through `call`). Every delete
// asks first in the dashboard's words. Adding a block stays on the
// dashboard (Settings → Phones & browsers).
// ---------------------------------------------------------------------------

/// Whether the admin deletes and the blocked-phones list are offered.
public func deletesAdminHeld(_ state: AdminSession.State, sharedScreen: Bool) -> Bool {
    if sharedScreen { return false }
    if case .signedOut = state { return false }
    return true
}

/// `AdminDeleteWords`, word for word.
public enum AdminDeleteWords {
    // MARK: a music track (music.jsx Drawer, deleteTrack)
    public static let upkeepHeld = "rescanning the library and fixing tags are done on the dashboard"
    public static let upkeepOrPhone =
        "rescanning the library, fixing tags and deleting tracks are done on the dashboard, "
        + "or sign in as admin on this phone to delete tracks"
    public static let alsoFile = "also delete file on disk"
    public static let deleteTrack = "delete track"
    public static let delete = "delete"
    public static let cancel = "cancel"

    /// The dashboard asks only before deleting the file too; a library-only
    /// delete asks here as well, in the iPhone's long-standing words.
    public static func trackConfirm(_ title: String?, filePath: String?, alsoFile: Bool) -> String {
        let t = (title?.trimmingCharacters(in: .whitespaces).isEmpty == false ? title : nil) ?? "track"
        if alsoFile {
            let shown = "Permanently delete the file for \"\(t)\" from disk? This can't be undone.\n\n\(filePath ?? "")"
            return shown.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return "Remove \"\(t)\" from the library? The file stays on disk; a rescan will re-add it."
    }

    public static func trackDeleted(_ title: String?, alsoFile: Bool) -> String {
        let t = (title?.trimmingCharacters(in: .whitespaces).isEmpty == false ? title : nil) ?? "track"
        return alsoFile
            ? "deleted \"\(t)\" · file removed from disk"
            : "removed \"\(t)\" from library (file kept; rescan will re-add)"
    }

    // MARK: a file on the server (files.jsx FilesDeleteConfirm)
    public static let filesOrPhone = "deleting is on the dashboard, or sign in as admin on this phone"

    public static func filesTitle(_ n: Int) -> String { "Delete \(n) item\(n == 1 ? "" : "s")?" }
    public static func filesConfirmLabel(folder: Bool) -> String { folder ? "Delete everything" : "Delete" }
    public static func filesBody(folders: Int, paths: [String]) -> String {
        let head = folders > 0
            ? "\(folders) folder\(folders == 1 ? "" : "s") and everything inside \(folders == 1 ? "it" : "them") "
                + "will be permanently deleted. This can’t be undone."
            : "This can’t be undone."
        return paths.isEmpty ? head : head + "\n\n" + paths.joined(separator: "\n")
    }

    public static func filesDeleted(ok: Int, failed: Int) -> String {
        "deleted \(ok) item\(ok == 1 ? "" : "s")\(failed > 0 ? " · \(failed) failed" : "")"
    }

    // MARK: an audiobook (Android's RemoveFromServerDialog)
    public static let removeFromServer = "remove from server (admin)"
    public static let remove = "remove"
    public static func removeTitle(_ title: String) -> String { "Remove \"\(title)\" from the server?" }
    public static let removeBody =
        "This deletes the book's files from the Domovoi server for everyone in the house. "
        + "Copies saved on phones stay on those phones."
    public static func removed(_ title: String) -> String { "removed \"\(title)\" from the server" }

    // MARK: blocked phones (settings.jsx QueueAccessCard, FilesAccessCard)
    public static let blocksTitle = "Blocked phones"
    public static let blocksSub =
        "Devices stopped from editing a room's play queue or from changing files. Adding a block is on the dashboard."
    public static let queueTitle = "Queue access"
    public static let filesTitle = "Files access"
    public static let queueNone = "No blocks — every device may edit every queue."
    public static let filesNone = "No blocks — every device may upload, move and import."
    public static let unblock = "unblock"
    public static let blockRemoved = "block removed"
    public static let everyRoom = "every room"
    public static let unlock = "show with Face ID"
    public static func removeFailed(_ why: String) -> String { "remove failed: \(why)" }

    /// The blocked device's own line: where the block is lifted.
    public static let liftedWhere =
        "An admin lifts the block in the dashboard under Settings → Phones & browsers, "
        + "or on an admin's phone under Settings → Admin on this phone."
}

/// The track sheet's upkeep line: what is left for the dashboard.
public func musicUpkeepLine(_ state: AdminSession.State, sharedScreen: Bool) -> String {
    if deletesAdminHeld(state, sharedScreen: sharedScreen) { return AdminDeleteWords.upkeepHeld }
    return sharedScreen ? MusicStrings.upkeepOnDashboard : AdminDeleteWords.upkeepOrPhone
}

/// The Files list's foot for a server library: none with admin held.
public func filesDeleteLine(_ state: AdminSession.State, sharedScreen: Bool) -> String? {
    if deletesAdminHeld(state, sharedScreen: sharedScreen) { return nil }
    return sharedScreen ? FileStrings.deleteIsAnAdminJob : AdminDeleteWords.filesOrPhone
}

/// One block (either list). `roomID` nil: every room (queue), or a Files
/// block, which has no room.
public struct DeviceBlock: Equatable, Sendable, Identifiable {
    public let id: Int64
    public let deviceID: String?
    public let deviceName: String?
    public let roomID: String?
    public let note: String?
    public let createdAt: String?

    public init(id: Int64, deviceID: String? = nil, deviceName: String? = nil, roomID: String? = nil, note: String? = nil, createdAt: String? = nil) {
        self.id = id
        self.deviceID = deviceID
        self.deviceName = deviceName
        self.roomID = roomID
        self.note = note
        self.createdAt = createdAt
    }

    /// Who: the name, else the id, else "a device".
    public var who: String {
        if let n = deviceName, !n.trimmingCharacters(in: .whitespaces).isEmpty { return n }
        if let d = deviceID, !d.trimmingCharacters(in: .whitespaces).isEmpty { return d }
        return "a device"
    }
}

public func parseDeviceBlocks(_ data: Data) -> [DeviceBlock] {
    guard let rows = (try? JSONSerialization.jsonObject(with: data)) as? [Any] else { return [] }
    return rows.compactMap { row in
        guard let o = row as? [String: Any],
              let n = o["id"] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() else { return nil }
        return DeviceBlock(
            id: n.int64Value, deviceID: o["device_id"] as? String, deviceName: o["device_name"] as? String,
            roomID: o["room_id"] as? String, note: o["note"] as? String, createdAt: o["created_at"] as? String
        )
    }
}

/// A block's second line: its scope (queue), why, and when.
public func blockDetail(_ b: DeviceBlock, queue: Bool, when: (String) -> String) -> String {
    var parts: [String] = []
    if queue { parts.append(b.roomID ?? AdminDeleteWords.everyRoom) }
    if let note = b.note, !note.trimmingCharacters(in: .whitespaces).isEmpty { parts.append(note) }
    if let at = b.createdAt { parts.append("added \(when(at))") }
    return parts.joined(separator: " · ")
}

/// The routes, each with `headers` and `onlyTo` from ``AdminSession``.
public struct AdminDeletesApi: Sendable {

    public static let queueBlocks = "/api/music/queue-blocks"
    public static let filesBlocks = "/api/files/device-blocks"

    let client: DomovoiClient

    public init(client: DomovoiClient) {
        self.client = client
    }

    public static func trackPath(_ id: Int64, alsoFile: Bool) -> String {
        "/api/music/library/\(id)" + (alsoFile ? "?also_file=true" : "")
    }

    public func deleteTrack(_ id: Int64, alsoFile: Bool, headers: [String: String], onlyTo: String) async throws {
        _ = try await client.raw(method: "DELETE", path: Self.trackPath(id, alsoFile: alsoFile), headers: headers, onlyTo: onlyTo)
    }

    /// `POST /api/files/delete`: how many went, and how many didn't.
    public func deleteFiles(
        libraryID: String, paths: [String], recursive: Bool, headers: [String: String], onlyTo: String
    ) async throws -> (deleted: Int, failed: Int) {
        let body = try JSONSerialization.data(withJSONObject: ["library_id": libraryID, "paths": paths, "recursive": recursive])
        let data = try await client.raw(method: "POST", path: "/api/files/delete", body: body, headers: headers, onlyTo: onlyTo)
        let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        return ((o?["deleted"] as? [Any])?.count ?? 0, (o?["failed"] as? [Any])?.count ?? 0)
    }

    public func blocks(queue: Bool, headers: [String: String], onlyTo: String) async throws -> [DeviceBlock] {
        parseDeviceBlocks(try await client.raw(method: "GET", path: queue ? Self.queueBlocks : Self.filesBlocks, headers: headers, onlyTo: onlyTo))
    }

    public func lift(queue: Bool, id: Int64, headers: [String: String], onlyTo: String) async throws {
        _ = try await client.raw(method: "DELETE", path: "\(queue ? Self.queueBlocks : Self.filesBlocks)/\(id)", headers: headers, onlyTo: onlyTo)
    }
}

/// The deletes, one at a time, each answering its toast.
@MainActor
@Observable
public final class AdminDeletesModel {

    public private(set) var busy = false
    /// The two block lists; nil until read with a session.
    public private(set) var queueBlocks: [DeviceBlock]?
    public private(set) var filesBlocks: [DeviceBlock]?
    public private(set) var problem: String?

    private let admin: AdminSession
    private let api: AdminDeletesApi

    public init(admin: AdminSession) {
        self.admin = admin
        api = AdminDeletesApi(client: admin.client)
    }

    private func run<T: Sendable>(
        _ action: String, _ work: @escaping @Sendable (AdminDeletesApi, [String: String], String) async throws -> T
    ) async -> Result<T, AdminDeleteFailure> {
        busy = true
        defer { busy = false }
        let api = api
        do {
            return .success(try await admin.act { h, srv in try await work(api, h, srv) })
        } catch {
            return .failure(AdminDeleteFailure(said: Self.problem(action, error)))
        }
    }

    public func deleteTrack(_ track: LibraryTrack, alsoFile: Bool) async -> (ok: Bool, said: String) {
        let id = track.id
        switch await run("delete", { api, h, srv in try await api.deleteTrack(id, alsoFile: alsoFile, headers: h, onlyTo: srv) }) {
        case .success: return (true, AdminDeleteWords.trackDeleted(track.title, alsoFile: alsoFile))
        case .failure(let f): return (false, f.said)
        }
    }

    public func deleteFile(libraryID: String, _ entry: FileEntry) async -> (ok: Bool, said: String) {
        let rel = entry.rel
        let dir = entry.isDir
        switch await run("delete", { api, h, srv in
            let r = try await api.deleteFiles(libraryID: libraryID, paths: [rel], recursive: dir, headers: h, onlyTo: srv)
            return [r.deleted, r.failed]
        }) {
        case .success(let n): return (true, AdminDeleteWords.filesDeleted(ok: n[0], failed: n[1]))
        case .failure(let f): return (false, f.said)
        }
    }

    /// An audiobook off the server: the same delete, in `core:audiobooks`.
    public func removeBook(path: String, isFolder: Bool, title: String) async -> (ok: Bool, said: String) {
        switch await run("remove", { api, h, srv in
            _ = try await api.deleteFiles(libraryID: "core:audiobooks", paths: [path], recursive: isFolder, headers: h, onlyTo: srv)
        }) {
        case .success: return (true, AdminDeleteWords.removed(title))
        case .failure(let f): return (false, f.said)
        }
    }

    /// The block lists: signed in through `call`; kept only, the tap,
    /// through `act`.
    public func loadBlocks(viaAct: Bool) async {
        let api = api
        let read: @Sendable ([String: String], String) async throws -> [[DeviceBlock]] = { h, srv in
            [try await api.blocks(queue: true, headers: h, onlyTo: srv), try await api.blocks(queue: false, headers: h, onlyTo: srv)]
        }
        do {
            let both = viaAct ? try await admin.act(read) : try await admin.call(read)
            queueBlocks = both[0]
            filesBlocks = both[1]
            problem = nil
        } catch is AdminSession.NotSignedIn {
            queueBlocks = nil
            filesBlocks = nil
        } catch {
            problem = Self.problem("load", error)
        }
    }

    public func lift(queue: Bool, _ b: DeviceBlock) async -> String {
        let id = b.id
        switch await run("remove", { api, h, srv in try await api.lift(queue: queue, id: id, headers: h, onlyTo: srv) }) {
        case .success:
            await loadBlocks(viaAct: false)
            return AdminDeleteWords.blockRemoved
        case .failure(let f): return AdminDeleteWords.removeFailed(f.said)
        }
    }

    static func problem(_ action: String, _ error: any Error) -> String {
        if let why = (error as? AdminSession.AdminUnavailable)?.reason { return why }
        if error is AdminSession.NotSignedIn { return adminOnly }
        if let e = error as? DomovoiError { return failureText(action, e) }
        return "\(action) failed"
    }
}

public struct AdminDeleteFailure: Error, Sendable {
    public let said: String
}

extension MusicModel {
    /// A track deleted as admin on this phone: off the list without a
    /// reload, and the counts read again.
    public func trackDeleted(_ id: Int64) {
        library.remove(id)
        stats.refresh()
    }
}

extension AudiobooksModel {
    /// A book removed from the server as admin on this phone: the list is
    /// read again.
    public func bookRemoved() { list.reload() }
}
