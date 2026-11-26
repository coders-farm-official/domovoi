import Foundation
import XCTest
@testable import DomovoiKit

/// Rename and remove a printer as admin on this phone (1.5.0): Android's
/// `PrinterAdminTest`, case for case, then the model against a stub house.
final class PrinterAdminTests: XCTestCase {

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
        XCTAssertTrue(printersAdminHeld(signedIn, sharedScreen: false))
        XCTAssertTrue(printersAdminHeld(kept, sharedScreen: false))
        XCTAssertFalse(printersAdminHeld(signedIn, sharedScreen: true))
        XCTAssertFalse(printersAdminHeld(.signedOut, sharedScreen: false))
        XCTAssertNil(printersAdminLine(signedIn, sharedScreen: false))
        XCTAssertEqual(PrintingStrings.adminOnlyLine, printersAdminLine(.signedOut, sharedScreen: true))
        XCTAssertEqual(
            "An admin can rename or remove printers on the web dashboard, or sign in as admin on this phone.",
            printersAdminLine(.signedOut, sharedScreen: false)
        )
        XCTAssertTrue(AdminStrings.jobs.contains("rename or remove a printer (Settings → Printers)"))
    }

    func testRemoveAsksInTheDashboardsWords() {
        XCTAssertEqual("Remove Office laser?", PrinterAdminWords.removeTitle("Office laser"))
        XCTAssertEqual("Domovoi stops printing with it. You can add it again later.", PrinterAdminWords.removeBody(canScan: false))
        XCTAssertEqual("Domovoi stops printing and scanning with it. You can add it again later.", PrinterAdminWords.removeBody(canScan: true))
        XCTAssertEqual("removed Office laser", PrinterAdminWords.removed("Office laser"))
        XCTAssertEqual("Hall printer", printerRenameTo(current: "Office", draft: "  Hall printer "))
        XCTAssertNil(printerRenameTo(current: "Office", draft: "Office"))
        XCTAssertNil(printerRenameTo(current: "Office", draft: "   "))
        XCTAssertEqual(60, printerRenameTo(current: "Office", draft: String(repeating: "x", count: 80))?.count)
    }

    /// The words are the Kotlin's: read from `PrinterAdmin.kt` itself.
    func testTheWordsAreTheKotlins() throws {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { root = root.deletingLastPathComponent() }
        let kotlin = root.appendingPathComponent("android/app/src/main/java/com/domovoi/app/ui/screens/printing/PrinterAdmin.kt")
        guard let source = try? String(contentsOf: kotlin, encoding: .utf8) else {
            throw XCTSkip("the Android source isn't next to this package")
        }
        for words in [PrinterAdminWords.adminLineOrPhone, PrinterAdminWords.rename, PrinterAdminWords.remove, PrinterAdminWords.name] {
            XCTAssertTrue(source.contains("\"\(words)\""), words)
        }
    }

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
    func testRenameAndRemoveCarryTheBearer() async throws {
        let fh = FhHouse()
        fh.answer("POST", "/api/printers/4/rename", #"{"id":4,"name":"Hall"}"#)
        fh.answer("DELETE", "/api/printers/4", #"{"removed":true}"#)
        let zone = PrinterAdminModel(admin: await signedIn(fh))
        let office = Printer(id: 4, name: "Office")
        let renamed = await zone.rename(office, to: "Hall")
        XCTAssertTrue(renamed.ok)
        XCTAssertNil(renamed.said)
        let post = try XCTUnwrap(sent("POST", "/api/printers/4/rename"))
        XCTAssertEqual(Self.bearer, post.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual("Hall", (try JSONSerialization.jsonObject(with: try XCTUnwrap(post.httpBody)) as? [String: Any])?["name"] as? String)
        let removed = await zone.remove(office)
        XCTAssertEqual("removed Office", removed.said)
        XCTAssertEqual(Self.bearer, sent("DELETE", "/api/printers/4")?.value(forHTTPHeaderField: "Authorization"))
    }

    @MainActor
    func testWithoutAdminNothingIsSent() async {
        let fh = FhHouse()
        let session = await signedIn(fh)
        session.forget()
        let zone = PrinterAdminModel(admin: session)
        let out = await zone.remove(Printer(id: 4, name: "Office"))
        XCTAssertFalse(out.ok)
        XCTAssertEqual(adminOnly, out.said)
        XCTAssertNil(sent("DELETE", "/api/printers/4"))
    }
}
