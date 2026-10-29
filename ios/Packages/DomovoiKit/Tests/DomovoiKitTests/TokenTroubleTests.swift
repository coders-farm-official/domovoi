import XCTest
@testable import DomovoiKit

/// The words the "doesn't recognise this phone" card says, held against
/// Android's.
///
/// These strings are copied from
/// `android/.../ui/shell/TokenTroubleScreen.kt`, and the point of the
/// assertions below is that they are copied *exactly*: the two apps must say
/// the same thing to the same person about the same situation. If the Android
/// wording changes and only one side moves, this is what fails and says so.
/// A port of the pure half of `TokenTroubleTest.kt`.
final class TokenTroubleTests: XCTestCase {

    func testTheTitlesAreAndroidsWordForWord() {
        XCTAssertEqual(
            tokenTroubleTitle(.notRecognised(waitMs: 0)),
            "Domovoi doesn't recognise this phone any more"
        )
        XCTAssertEqual(tokenTroubleTitle(.waiting(waitMs: 60_000)), "Domovoi asked this phone to wait")
    }

    func testTheExplanationNamesTheAddressAndWhatHappened() {
        let text = tokenTroubleText(.notRecognised(waitMs: 0), address: "10.0.0.9:6369")
        XCTAssertEqual(text, """
            10.0.0.9:6369 answered, but it no longer accepts the household token this phone was \
            paired with. That happens when Domovoi is reinstalled or moved to a new machine, or \
            when someone changes the household token. This phone has stopped sending the old one.
            """)
    }

    func testTheWaitingExplanationCountsDownAndNamesTheUsualCause() {
        let text = tokenTroubleText(.waiting(waitMs: 245_000), address: "10.0.0.9:6369")
        XCTAssertEqual(text, """
            10.0.0.9:6369 turned away too many wrong household tokens from this phone's address, \
            often from an app or a browser tab on it that still holds an old one, so it won't check \
            another one for 4:05. Nothing is sent until then, and this phone tries again by itself \
            when the wait is over.
            """)
    }

    /// A refused token is NOT tried again, so its card never says "trying
    /// again" — the two cases read differently on purpose.
    func testTheWaitLineSaysWhatWillActuallyHappen() {
        XCTAssertEqual(tokenTroubleWaitLine(.notRecognised(waitMs: 245_000)), "a new token can be tried in 4:05")
        XCTAssertEqual(tokenTroubleWaitLine(.waiting(waitMs: 245_000)), "trying again in 4:05")
        XCTAssertNil(tokenTroubleWaitLine(.notRecognised(waitMs: 0)), "no wait, no line")
        XCTAssertNil(tokenTroubleWaitLine(.waiting(waitMs: 0)))
    }

    func testTheHintTellsThePersonWhereTheTokenIs() {
        let dashboard = "An admin finds it on the dashboard under Settings → Phones & browsers → Household token."
        XCTAssertTrue(tokenTroubleHint(.notRecognised(waitMs: 0)).contains(dashboard))
        XCTAssertTrue(tokenTroubleHint(.waiting(waitMs: 1_000)).contains(dashboard))
        // The 2026-10-17 case: a dashboard tab in the phone's own browser,
        // still holding the old token, kept the address waiting for the app
        // too. Every one of the three hints says so.
        for trouble in [TokenTrouble.notRecognised(waitMs: 0), .notRecognised(waitMs: 1_000), .waiting(waitMs: 1_000)] {
            XCTAssertTrue(
                tokenTroubleHint(trouble).contains("close that tab or pair it again too"),
                "\(trouble)"
            )
        }
        XCTAssertTrue(
            tokenTroubleHint(.notRecognised(waitMs: 245_000)).contains("wait 4:05 after the wrong token"),
            "a refused token inside a wait says when the new one will be tried"
        )
    }

    func testTheClockRoundsUpSoItNeverReadsZeroWhileTheServerStillWaits() {
        XCTAssertEqual(waitClock(0), "0:00")
        XCTAssertEqual(waitClock(1), "0:01")
        XCTAssertEqual(waitClock(999), "0:01")
        XCTAssertEqual(waitClock(1_000), "0:01")
        XCTAssertEqual(waitClock(1_001), "0:02")
        XCTAssertEqual(waitClock(59_000), "0:59")
        XCTAssertEqual(waitClock(60_000), "1:00")
        XCTAssertEqual(waitClock(245_000), "4:05")
        XCTAssertEqual(waitClock(-5_000), "0:00", "a wait in the past is no wait")
    }

    /// Which card, if any. A changed identity comes first and is not this
    /// card's business: the token is not sent to a server that proved a
    /// different key, and the person is asked about the key before anything
    /// about pairing. A wait only matters while there is a token to wait
    /// with.
    func testWhichCardTheShellShows() {
        let server = "http://10.0.0.9:6369"
        XCTAssertNil(tokenTrouble(serverURL: "", paired: true, rejected: true, waitMs: 60_000))
        XCTAssertNil(tokenTrouble(serverURL: "  ", paired: true, rejected: true, waitMs: 60_000))
        XCTAssertNil(
            tokenTrouble(serverURL: server, paired: false, rejected: true, waitMs: 60_000),
            "an unpaired phone is the pairing screen's business"
        )
        XCTAssertNil(
            tokenTrouble(serverURL: server, paired: true, rejected: true, waitMs: 0, identityChanged: true),
            "the key comes first"
        )
        XCTAssertEqual(
            tokenTrouble(serverURL: server, paired: true, rejected: true, waitMs: 60_000),
            .notRecognised(waitMs: 60_000),
            "refused wins over waiting, and carries the wait"
        )
        XCTAssertEqual(
            tokenTrouble(serverURL: server, paired: true, rejected: true, waitMs: -1),
            .notRecognised(waitMs: 0)
        )
        XCTAssertEqual(
            tokenTrouble(serverURL: server, paired: true, rejected: false, waitMs: 60_000),
            .waiting(waitMs: 60_000)
        )
        XCTAssertNil(tokenTrouble(serverURL: server, paired: true, rejected: false, waitMs: 0), "nothing is wrong")
    }

    /// The throttle error's own message, which is the one Android's
    /// `TokenThrottledException` carries.
    func testTheThrottleErrorSaysHowLongInWholeSeconds() {
        XCTAssertEqual(
            DomovoiError.tokenThrottled(waitMs: 30_000).message,
            "Domovoi asked this phone to wait 30 s before it tries the household token again"
        )
        XCTAssertEqual(
            DomovoiError.tokenThrottled(waitMs: 29_001).message,
            "Domovoi asked this phone to wait 30 s before it tries the household token again",
            "rounded up, as Android rounds it"
        )
    }

    /// The address a card shows is the saved one with the scheme off — what a
    /// person can check against the box.
    func testTheAddressOnTheCardIsTheOneOnTheBox() {
        XCTAssertEqual(ServerCredentials.address("http://10.0.0.9:6369/"), "10.0.0.9:6369")
        XCTAssertEqual(ServerCredentials.address("https://domovoi.lan:6369"), "domovoi.lan:6369")
        XCTAssertEqual(ServerCredentials.address("  http://10.0.0.9:6369///  "), "10.0.0.9:6369")
        XCTAssertEqual(ServerCredentials.normalize("  http://10.0.0.9:6369///  "), "http://10.0.0.9:6369")
        XCTAssertEqual(ServerCredentials.normalize(""), "")
    }
}
