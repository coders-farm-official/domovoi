import Foundation
import XCTest
@testable import DomovoiKit

/// Rename and forget as admin on this phone (1.5.0): Android's
/// `PeopleAdminTest`, case for case, then the model against a stub house.
final class PeopleAdminTests: XCTestCase {

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
        XCTAssertTrue(peopleAdminHeld(signedIn, sharedScreen: false))
        XCTAssertTrue(peopleAdminHeld(kept, sharedScreen: false))
        XCTAssertFalse(peopleAdminHeld(signedIn, sharedScreen: true))
        XCTAssertFalse(peopleAdminHeld(.signedOut, sharedScreen: false))
        XCTAssertNil(peopleEditLine(signedIn, sharedScreen: false))
        XCTAssertEqual(PeopleStrings.renamingIsOnTheDashboard, peopleEditLine(signedIn, sharedScreen: true))
        XCTAssertEqual(
            "renaming and forgetting someone are done on the dashboard, or sign in as admin on this phone",
            peopleEditLine(.signedOut, sharedScreen: false)
        )
        XCTAssertTrue(AdminStrings.jobs.contains("rename or forget someone (People)"))
    }

    func testARenameSendsTheNameTidiedOrNothing() {
        XCTAssertEqual("Mary Ann", peopleRenameTo(current: "Mary", draft: "  Mary   Ann "))
        XCTAssertNil(peopleRenameTo(current: "Mary", draft: "Mary"))
        XCTAssertNil(peopleRenameTo(current: "Mary", draft: "  Mary "))
        XCTAssertNil(peopleRenameTo(current: "Mary", draft: "   "))
        XCTAssertEqual("mary", peopleRenameTo(current: "Mary", draft: "mary"))
    }

    func testForgetAsksInTheDashboardsWords() {
        let alex = Person(id: 7, name: "Alex", voiceProfileCount: 3)
        XCTAssertEqual("Forget Alex?", PeopleAdminWords.forgetTitle(alex))
        XCTAssertEqual("Forget Alex (#7)?", PeopleAdminWords.forgetTitle(Person(id: 7, name: "Alex", namesakes: 1)))
        XCTAssertEqual(
            "This is a cascade delete. Domovoi will lose:\n"
                + "• 3 voice profiles\n"
                + "• what they said: 12 conversation turns in 2 sessions, with Domovoi's answers\n"
                + "• their memories, favorites and preferences\n\n"
                + "Turns where someone else said their name stay: Domovoi goes by who was speaking.",
            PeopleAdminWords.forgetBody(alex, sessionCount: 2, turnCount: 12)
        )
        let one = PeopleAdminWords.forgetBody(Person(id: 7, name: "Alex", voiceProfileCount: 1), sessionCount: 0, turnCount: 1)
        XCTAssertTrue(one.contains("• 1 voice profile\n"))
        XCTAssertTrue(one.contains("what they said: 1 conversation turn, with"))
        XCTAssertTrue(PeopleAdminWords.forgetBody(alex, sessionCount: 1, turnCount: 200).contains("200+ conversation turns in 1 session,"))
        XCTAssertTrue(peopleForgetConfirmed(typed: " Alex ", name: "Alex"))
        XCTAssertFalse(peopleForgetConfirmed(typed: "alex", name: "Alex"))
        XCTAssertFalse(peopleForgetConfirmed(typed: "", name: "Alex"))
        XCTAssertEqual("forgot Alex · cascade complete", PeopleAdminWords.forgot("Alex"))
    }

    /// The words are the Kotlin's: read from `PeopleAdmin.kt` itself.
    func testTheWordsAreTheKotlins() throws {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { root = root.deletingLastPathComponent() }
        let kotlin = root.appendingPathComponent("android/app/src/main/java/com/domovoi/app/ui/screens/people/PeopleAdmin.kt")
        guard let source = try? String(contentsOf: kotlin, encoding: .utf8) else {
            throw XCTSkip("the Android source isn't next to this package")
        }
        for words in [PeopleAdminWords.editOrPhone, PeopleAdminWords.rename, PeopleAdminWords.forget, PeopleAdminWords.newName] {
            XCTAssertTrue(source.contains("\"\(words)\""), words)
        }
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
    func testRenameAndForgetCarryTheBearer() async throws {
        let fh = FhHouse()
        fh.answer("PATCH", "/api/people/7", #"{"id":7,"name":"Sam"}"#)
        fh.answer("DELETE", "/api/people/7", #"{"ok":true}"#)
        let zone = PeopleAdminModel(admin: await signedIn(fh))
        let alex = Person(id: 7, name: "Alex")
        let renamed = await zone.rename(alex, to: "Sam")
        XCTAssertEqual(true, renamed.ok)
        XCTAssertEqual("renamed to Sam", renamed.said)
        let patch = try XCTUnwrap(sent("PATCH", "/api/people/7"))
        XCTAssertEqual(Self.bearer, patch.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual("Sam", (try JSONSerialization.jsonObject(with: try XCTUnwrap(patch.httpBody)) as? [String: Any])?["name"] as? String)
        let forgot = await zone.forget(alex)
        XCTAssertEqual(true, forgot.ok)
        XCTAssertEqual(Self.bearer, sent("DELETE", "/api/people/7")?.value(forHTTPHeaderField: "Authorization"))
    }

    @MainActor
    func testATakenNameIsSaidInTheServersWords() async {
        let fh = FhHouse()
        fh.answer("PATCH", "/api/people/7", #"{"detail":"Someone is already called Sam."}"#, status: 409)
        let zone = PeopleAdminModel(admin: await signedIn(fh))
        let out = await zone.rename(Person(id: 7, name: "Alex"), to: "Sam")
        XCTAssertFalse(out.ok)
        XCTAssertEqual("Someone is already called Sam.", out.said)
    }

    @MainActor
    func testWithoutAdminNothingIsSent() async {
        let fh = FhHouse()
        let session = await signedIn(fh)
        session.forget()
        let zone = PeopleAdminModel(admin: session)
        let out = await zone.forget(Person(id: 7, name: "Alex"))
        XCTAssertFalse(out.ok)
        XCTAssertEqual(adminOnly, out.said)
        XCTAssertNil(sent("DELETE", "/api/people/7"))
    }
}
