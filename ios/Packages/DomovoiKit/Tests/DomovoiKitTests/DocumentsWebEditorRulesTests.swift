import Foundation
import XCTest
@testable import DomovoiKit

/// The three rules the formatted-document editor's `WKWebView` host obeys:
/// the origin lock, the one-shot token, and when a session that was admitted
/// is ended under the person.
///
/// ### Why this file exists
///
/// All three lived in `ios/Domovoi/Documents/DocumentsWebEditorHost.swift`,
/// which `pkgtest.sh` does not compile. Round 2's review could not sabotage
/// either of the two headline behaviours of owner answer 21 for that reason,
/// and said so (note 1): *"with M1 in place there is at this moment nothing
/// anywhere that would catch a regression in either"*. The decisions moved to
/// ``DocumentsWebEditorRules`` in DomovoiKit, the `WebKit` glue stayed in the
/// view, and these are the cases that hold them.
///
/// The Kotlin they are held against is
/// `android/app/src/main/java/com/domovoi/app/ui/screens/documents/WebEditorHost.kt`:
/// `EditorWebClient.shouldOverrideUrlLoading` (`:283-288`),
/// `shouldInterceptRequest` (`:290-299`) and the `AtomicReference` token
/// (`:323, 328`).
final class DocumentsWebEditorRulesTests: XCTestCase {

    private let house = ServerURL.parse("http://192.168.0.197:6370")!

    /// `XCTFail` on an address that does not parse, rather than handing the
    /// rule a `nil`: `navigation(to: nil, …)` answers `.refuseQuietly`, so a
    /// typo in one of these strings would look like a refusal the lock made.
    private func decide(_ address: String, mainFrame: Bool = true) -> DocumentsWebEditorNavigation {
        guard let url = URL(string: address) else {
            XCTFail("this case's own address did not parse: \(address)")
            return .refuseQuietly
        }
        return DocumentsWebEditorRules.navigation(to: url, base: house, mainFrame: mainFrame)
    }

    // MARK: - the origin lock

    /// The house's own pages load, in the main frame and in a sub-frame.
    func testThePagesOfThisServerLoad() {
        XCTAssertEqual(.allow, decide("http://192.168.0.197:6370/doc.html?path=letter.html"))
        XCTAssertEqual(.allow, decide("http://192.168.0.197:6370/api/documents/raw/letter.html"))
        XCTAssertEqual(
            .allow,
            decide("http://192.168.0.197:6370/static/doc_editor.js", mainFrame: false)
        )
    }

    /// Another host, another port and another scheme are each **off** this
    /// origin: `TokenScope.sameServer` is scheme, host and port together, and
    /// a port on the same machine is a different server for this purpose.
    func testAnythingOffThisOriginIsRefusedAndSaid() {
        XCTAssertEqual(.refuseAndSay, decide("http://example.com/anything"))
        XCTAssertEqual(.refuseAndSay, decide("http://192.168.0.197:6369/doc.html"))
        XCTAssertEqual(.refuseAndSay, decide("https://192.168.0.197:6370/doc.html"))
        // The same address with a userinfo part in it is not this server
        // either, and a reader could easily think it was.
        XCTAssertEqual(.refuseAndSay, decide("http://192.168.0.197:6370@evil.example/x"))
    }

    /// `javascript:`, `file:` and a custom scheme are refused in the **main
    /// frame**, which is Android's `toHttpUrlOrNull() ?: return true`.
    func testTheMainFrameTakesHttpOnThisServerAndNothingElse() {
        for address in [
            "javascript:alert(1)",
            "file:///etc/passwd",
            "domovoi://open",
            // Base64 and no angle brackets: a `data:` URL with raw `<` in it
            // is a string `URL(string:)` may refuse, and this case is about
            // the lock, not about Foundation's parser.
            "data:text/html;base64,PHNjcmlwdD5mZXRjaCgnaHR0cDovL2V2aWwnKTwvc2NyaXB0Pg==",
            "blob:http://192.168.0.197:6370/4f2a",
        ] {
            XCTAssertEqual(.refuseAndSay, decide(address),
                           "\(address) was not refused in the main frame")
        }
    }

    /// A **sub-frame** takes `data:` and `blob:` — the subresource seam
    /// `shouldInterceptRequest` is about, which the editor uses for a
    /// `blob:` print copy and a `data:` picture. The bridge is not injected
    /// into a sub-frame, so nothing in one holds the token.
    ///
    /// `javascript:` and `file:` are refused there too: the seam is those two
    /// schemes, not "anything that is not http".
    func testASubFrameTakesDataAndBlobAndStillNotEverything() {
        XCTAssertEqual(.allow, decide("data:image/png;base64,AAAA", mainFrame: false))
        XCTAssertEqual(.allow, decide("blob:http://192.168.0.197:6370/4f2a", mainFrame: false))
        XCTAssertEqual(.refuseAndSay, decide("javascript:alert(1)", mainFrame: false))
        XCTAssertEqual(.refuseAndSay, decide("file:///etc/passwd", mainFrame: false))
        XCTAssertEqual(.refuseAndSay, decide("http://evil.example/x", mainFrame: false))
    }

    /// `about:blank` in the main frame, and **only** `about:blank`: it is how
    /// `recheck(_:)`'s `loadHTMLString("", baseURL: nil)` arrives, and
    /// cancelling it would leave the page it is taking down on screen and
    /// typeable. `about:srcdoc` is a document whose content is the page's own
    /// text and the bridge is re-injected into whatever main-frame document
    /// loads, so it is not in (round 2's review, note 6).
    func testOnlyAboutBlankIsAllowedInTheMainFrame() {
        XCTAssertEqual(.allow, decide("about:blank"))
        XCTAssertEqual(.allow, decide("ABOUT:BLANK"))
        XCTAssertEqual(.refuseAndSay, decide("about:srcdoc"))
        XCTAssertEqual(.refuseAndSay, decide("about:blank#x"))
        XCTAssertEqual(.refuseAndSay, decide("about:blankish"))
    }

    /// No target frame is a `window.open` / `target="_blank"`, which this
    /// host draws no view for. It gets the **strict** rule: the `data:` a
    /// sub-frame would be allowed is refused, and the house's own page is
    /// still allowed.
    func testANavigationWithNoTargetFrameGetsTheStrictRule() {
        XCTAssertEqual(.refuseAndSay, decide("data:text/plain;base64,eA==", mainFrame: true))
        XCTAssertEqual(.allow, decide("http://192.168.0.197:6370/doc.html", mainFrame: true))
    }

    /// A navigation with no address at all is cancelled with **nothing said**:
    /// no tap of the person's produced it, so there is nothing to tell them.
    func testANavigationWithNoAddressIsRefusedQuietly() {
        XCTAssertEqual(
            .refuseQuietly,
            DocumentsWebEditorRules.navigation(to: nil, base: house, mainFrame: true)
        )
    }

    /// The cleartext policy is asked **here** as well as in `prepare(in:)`,
    /// because a redirect can change the scheme — and `http` on a public
    /// address is what that policy refuses. Both of these are the active
    /// server by `sameServer`; only the private one may load.
    func testCleartextIsAskedAgainOnTheNavigation() {
        let publicHouse = ServerURL.parse("http://203.0.113.9:6370")!
        XCTAssertEqual(
            .refuseAndSay,
            DocumentsWebEditorRules.navigation(
                to: URL(string: "http://203.0.113.9:6370/doc.html"),
                base: publicHouse,
                mainFrame: true
            )
        )
        XCTAssertEqual(.allow, decide("http://192.168.0.197:6370/doc.html"))
    }

    // MARK: - the one-shot token

    /// The first document gets the token; every later one gets nothing, which
    /// is Android's `AtomicReference.getAndSet(null)` surviving a navigation
    /// where the injected script's own `var t = ''` cannot.
    func testTheTokenIsHandedOverOnceAndNeverAgain() {
        var oneShot = DocumentsWebEditorRules.OneShotToken()
        XCTAssertFalse(oneShot.hasBeenTaken)
        XCTAssertEqual("hhh-token", oneShot.toInject("hhh-token"))
        // Asked twice before the page reads it: still the token. The hand-over
        // is recorded when the page **takes** it, not when the script is
        // built, so a cover that is rebuilt before the first read still works.
        XCTAssertEqual("hhh-token", oneShot.toInject("hhh-token"))

        oneShot.wasTaken()
        XCTAssertTrue(oneShot.hasBeenTaken)
        XCTAssertEqual("", oneShot.toInject("hhh-token"),
                       "a reload would be handed the household token again")
        XCTAssertEqual("", oneShot.toInject("another-token"))
    }

    /// A token the page never read is still there for the next document, as
    /// an un-taken `AtomicReference` is: `wasTaken()` is the only thing that
    /// closes it, and nothing else does.
    func testAnUntakenTokenSurvivesForTheNextDocument() {
        let oneShot = DocumentsWebEditorRules.OneShotToken()
        XCTAssertEqual("hhh-token", oneShot.toInject("hhh-token"))
        XCTAssertFalse(oneShot.hasBeenTaken)
        XCTAssertEqual(DocumentsWebEditorRules.OneShotToken(), oneShot,
                       "asking what to inject changed the one-shot's state")
    }

    // MARK: - ending a session that was admitted

    private let page = "http://192.168.0.197:6370"

    func testNothingChangedEndsNothing() {
        XCTAssertNil(DocumentsWebEditorRules.sessionEnd(
            activeServerURL: page, pageServerURL: page,
            identityChanged: false, trouble: nil
        ))
    }

    func testTheActiveServerMovingEndsIt() {
        XCTAssertEqual(.serverMoved, DocumentsWebEditorRules.sessionEnd(
            activeServerURL: "http://192.168.0.44:6370", pageServerURL: page,
            identityChanged: false, trouble: nil
        ))
    }

    func testADifferentHouseOnTheAddressEndsIt() {
        XCTAssertEqual(.identityChanged, DocumentsWebEditorRules.sessionEnd(
            activeServerURL: page, pageServerURL: page,
            identityChanged: true, trouble: nil
        ))
    }

    /// Round 2's review, S2: a refused token ends the session. An admin
    /// blocking this device while the editor is open used to leave the page
    /// up, holding the token it was handed, with every `fetch` it made coming
    /// back 401 from the house as the page's own error.
    func testARefusedTokenEndsIt() {
        XCTAssertEqual(.tokenRefused, DocumentsWebEditorRules.sessionEnd(
            activeServerURL: page, pageServerURL: page,
            identityChanged: false, trouble: .notRecognised(waitMs: 0)
        ))
        // With a wait running beside the refusal, it is still the refusal.
        XCTAssertEqual(.tokenRefused, DocumentsWebEditorRules.sessionEnd(
            activeServerURL: page, pageServerURL: page,
            identityChanged: false, trouble: .notRecognised(waitMs: 240_000)
        ))
    }

    /// A wait the server asked for does **not** end it, and neither does an
    /// `.unproven` probe (which is `notOurs` without `identityChanged`, so it
    /// reaches this rule as all-false). Both end by themselves, and ending
    /// the session in either costs unsaved text for something still in
    /// flight — Android refuses one request and leaves the page where it is.
    func testAWaitAndAnUnprovenProbeLeaveThePageAlone() {
        XCTAssertNil(DocumentsWebEditorRules.sessionEnd(
            activeServerURL: page, pageServerURL: page,
            identityChanged: false, trouble: .waiting(waitMs: 90_000)
        ))
        XCTAssertNil(DocumentsWebEditorRules.sessionEnd(
            activeServerURL: page, pageServerURL: page,
            identityChanged: false, trouble: nil
        ))
    }

    /// The server moving is answered before the identity and before the
    /// token, because it is the one that decides which sentence is drawn:
    /// ``DocumentsStrings/editorNoServer`` for a move and
    /// ``DocumentsStrings/editorNotPaired`` for the other two.
    func testAMoveWinsOverTheOtherTwo() {
        XCTAssertEqual(.serverMoved, DocumentsWebEditorRules.sessionEnd(
            activeServerURL: "http://192.168.0.44:6370", pageServerURL: page,
            identityChanged: true, trouble: .notRecognised(waitMs: 0)
        ))
    }
}
