import Foundation
import XCTest
@testable import DomovoiKit

/// Deletes and blocks as admin on this phone (1.5.0): Android's
/// `AdminDeletesTest`, case for case, then the model against a stub house.
final class AdminDeletesTests: XCTestCase {

    private let server = "http://192.168.1.20:6369"
    private static let bearer = "Bearer " + String(repeating: "a", count: 64)

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    func testOfferedOnlyWithAdminAndNeverOnASharedScreen() {
        let signedIn = AdminSession.State.signedIn(server: server, idleUntilMs: nil, keptUntilMs: nil)
        let kept = AdminSession.State.kept(server: server, untilMs: 1)
        XCTAssertTrue(deletesAdminHeld(signedIn, sharedScreen: false))
        XCTAssertTrue(deletesAdminHeld(kept, sharedScreen: false))
        XCTAssertFalse(deletesAdminHeld(signedIn, sharedScreen: true))
        XCTAssertFalse(deletesAdminHeld(.signedOut, sharedScreen: false))
        XCTAssertEqual(AdminDeleteWords.upkeepHeld, musicUpkeepLine(signedIn, sharedScreen: false))
        XCTAssertEqual(MusicStrings.upkeepOnDashboard, musicUpkeepLine(signedIn, sharedScreen: true))
        XCTAssertTrue(musicUpkeepLine(.signedOut, sharedScreen: false).hasPrefix(MusicStrings.upkeepOnDashboard + ", or sign in as admin on this phone"))
        XCTAssertNil(filesDeleteLine(signedIn, sharedScreen: false))
        XCTAssertEqual(FileStrings.deleteIsAnAdminJob, filesDeleteLine(.signedOut, sharedScreen: true))
        XCTAssertEqual("deleting is on the dashboard, or sign in as admin on this phone", filesDeleteLine(.signedOut, sharedScreen: false))
        XCTAssertTrue(AdminStrings.jobs.contains("delete a music track (its drawer), a file on the server (Files) or an audiobook (remove from server)"))
        XCTAssertTrue(AdminStrings.jobs.contains("lift a phone's queue or Files block"))
    }

    func testTheWordsAreTheDashboards() {
        XCTAssertEqual(
            "Permanently delete the file for \"Blue\" from disk? This can't be undone.\n\n/music/blue.flac",
            AdminDeleteWords.trackConfirm("Blue", filePath: "/music/blue.flac", alsoFile: true)
        )
        XCTAssertEqual(
            "Remove \"Blue\" from the library? The file stays on disk; a rescan will re-add it.",
            AdminDeleteWords.trackConfirm("Blue", filePath: nil, alsoFile: false)
        )
        XCTAssertEqual("deleted \"Blue\" · file removed from disk", AdminDeleteWords.trackDeleted("Blue", alsoFile: true))
        XCTAssertEqual("removed \"Blue\" from library (file kept; rescan will re-add)", AdminDeleteWords.trackDeleted("Blue", alsoFile: false))
        XCTAssertEqual("/api/music/library/9?also_file=true", AdminDeletesApi.trackPath(9, alsoFile: true))
        XCTAssertEqual("Delete 1 item?", AdminDeleteWords.filesTitle(1))
        XCTAssertEqual("This can’t be undone.\n\nnotes/a.txt", AdminDeleteWords.filesBody(folders: 0, paths: ["notes/a.txt"]))
        XCTAssertEqual(
            "1 folder and everything inside it will be permanently deleted. This can’t be undone.\n\nold",
            AdminDeleteWords.filesBody(folders: 1, paths: ["old"])
        )
        XCTAssertEqual("Delete everything", AdminDeleteWords.filesConfirmLabel(folder: true))
        XCTAssertEqual("deleted 2 items · 1 failed", AdminDeleteWords.filesDeleted(ok: 2, failed: 1))
        XCTAssertTrue(FileStrings.blocked(nil).hasSuffix(AdminDeleteWords.liftedWhere))
        XCTAssertTrue(PhoneMediaStrings.blockedTail.hasSuffix(AdminDeleteWords.liftedWhere))
    }

    /// The words are the Kotlin's: read from `AdminDeletes.kt` itself.
    func testTheWordsAreTheKotlins() throws {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { root = root.deletingLastPathComponent() }
        let kotlin = root.appendingPathComponent("android/app/src/main/java/com/domovoi/app/ui/screens/settings/AdminDeletes.kt")
        guard let source = try? String(contentsOf: kotlin, encoding: .utf8) else {
            throw XCTSkip("the Android source isn't next to this package")
        }
        let flat = source.replacingOccurrences(of: "\" +\n            \"", with: "")
        for words in [
            AdminDeleteWords.upkeepHeld, AdminDeleteWords.upkeepOrPhone, AdminDeleteWords.filesOrPhone,
            AdminDeleteWords.blocksTitle, AdminDeleteWords.blocksSub, AdminDeleteWords.queueNone, AdminDeleteWords.filesNone,
            AdminDeleteWords.liftedWhere,
        ] {
            XCTAssertTrue(flat.contains("\"\(words)\""), words)
        }
    }

    func testTheBlocksReadAsTheDashboardListsThem() {
        let list = parseDeviceBlocks(Data("""
            [{"id":3,"device_id":"dev-a","device_name":"Kid's tablet","room_id":"kitchen","note":"bedtime","created_at":"2026-11-20T10:00:00Z"},
             {"id":4,"device_id":"dev-b","device_name":null,"room_id":null,"note":null,"created_at":null},
             {"device_id":"no-id"}]
            """.utf8))
        XCTAssertEqual([3, 4], list.map(\.id))
        XCTAssertEqual("Kid's tablet", list[0].who)
        XCTAssertEqual("dev-b", list[1].who)
        XCTAssertEqual("kitchen · bedtime · added X", blockDetail(list[0], queue: true) { _ in "X" })
        XCTAssertEqual("every room", blockDetail(list[1], queue: true) { _ in "X" })
        XCTAssertEqual("bedtime · added X", blockDetail(list[0], queue: false) { _ in "X" })
        XCTAssertEqual([], parseDeviceBlocks(Data("{}".utf8)))
    }

    // MARK: - the model, against a stub house

    @MainActor
    private func signedIn(_ fh: FhHouse) async -> AdminSession {
        fh.answer("POST", "/api/auth/login", #"{"ok":true,"token":"\#(String(repeating: "a", count: 64))","idle_timeout_min":60}"#)
        let (container, _) = fhContainer(fh, server: server)
        let session = container.admin
        let out = await session.signIn(password: "right-password", label: "phone")
        XCTAssertEqual(.ok, out)
        return session
    }

    private func sent(_ method: String, _ path: String) -> URLRequest? {
        StubURLProtocol.recorded.last { $0.httpMethod == method && $0.url?.path == path }
    }

    @MainActor
    func testEachDeleteAndLiftCarriesTheBearer() async throws {
        let fh = FhHouse()
        fh.answer("DELETE", "/api/music/library/9?also_file=true", "")
        fh.answer("POST", "/api/files/delete", #"{"deleted":["a.txt"],"failed":[],"reindex_triggered":true}"#)
        fh.answer("GET", "/api/music/queue-blocks", #"[{"id":3,"device_name":"Tablet","room_id":null}]"#)
        fh.answer("GET", "/api/files/device-blocks", "[]")
        fh.answer("DELETE", "/api/music/queue-blocks/3", "")
        let zone = AdminDeletesModel(admin: await signedIn(fh))

        let track = await zone.deleteTrack(LibraryTrack(id: 9, title: "Blue"), alsoFile: true)
        XCTAssertEqual(true, track.ok)
        XCTAssertEqual("deleted \"Blue\" · file removed from disk", track.said)

        let file = await zone.deleteFile(libraryID: "documents", FileEntry(name: "a.txt", rel: "a.txt", isDir: false))
        XCTAssertEqual("deleted 1 item", file.said)
        let body = try XCTUnwrap(sent("POST", "/api/files/delete")?.httpBody)
        let sentBody = try XCTUnwrap(try JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual("documents", sentBody["library_id"] as? String)
        XCTAssertEqual(["a.txt"], sentBody["paths"] as? [String])

        let book = await zone.removeBook(path: "Some Book", isFolder: true, title: "Some Book")
        XCTAssertEqual("removed \"Some Book\" from the server", book.said)
        let bookBody = try XCTUnwrap(try JSONSerialization.jsonObject(with: try XCTUnwrap(sent("POST", "/api/files/delete")?.httpBody)) as? [String: Any])
        XCTAssertEqual("core:audiobooks", bookBody["library_id"] as? String)
        XCTAssertEqual(true, bookBody["recursive"] as? Bool)

        await zone.loadBlocks(viaAct: false)
        XCTAssertEqual([3], zone.queueBlocks?.map(\.id))
        XCTAssertEqual([], zone.filesBlocks)
        let lifted = await zone.lift(queue: true, try XCTUnwrap(zone.queueBlocks?.first))
        XCTAssertEqual("block removed", lifted)

        for (method, path) in [
            ("DELETE", "/api/music/library/9"), ("POST", "/api/files/delete"),
            ("GET", "/api/music/queue-blocks"), ("GET", "/api/files/device-blocks"), ("DELETE", "/api/music/queue-blocks/3"),
        ] {
            XCTAssertEqual(Self.bearer, sent(method, path)?.value(forHTTPHeaderField: "Authorization"), path)
        }
    }

    @MainActor
    func testWithoutAdminNothingIsSent() async {
        let fh = FhHouse()
        let session = await signedIn(fh)
        session.forget()
        let zone = AdminDeletesModel(admin: session)
        let out = await zone.deleteTrack(LibraryTrack(id: 9, title: "Blue"), alsoFile: false)
        XCTAssertFalse(out.ok)
        XCTAssertEqual(adminOnly, out.said)
        await zone.loadBlocks(viaAct: false)
        XCTAssertNil(zone.queueBlocks)
        XCTAssertNil(sent("DELETE", "/api/music/library/9"))
    }
}
