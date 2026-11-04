import XCTest
@testable import DomovoiKit

/// What happens when the system takes the audio away, or the headphones come
/// out.
///
/// Android gets both for free from two ExoPlayer flags
/// (`PlayerController.kt:89-98`), so there is nothing to port and nothing over
/// there to compare against — which is the reason this table is written down
/// and tested rather than left inside a notification handler.
final class PlayerInterruptionTests: XCTestCase {

    func testAnInterruptionBeginningPauses() {
        XCTAssertEqual(PlayerInterruption.interruption(began: true, shouldResume: false), .pause)
        XCTAssertEqual(
            PlayerInterruption.interruption(began: true, shouldResume: true), .pause,
            "the flag only means anything on the way out"
        )
    }

    func testAnInterruptionEndingResumesOnlyWhenTheSystemSaysItShould() {
        XCTAssertEqual(PlayerInterruption.interruption(began: false, shouldResume: true), .resume)
        XCTAssertEqual(
            PlayerInterruption.interruption(began: false, shouldResume: false), .nothing,
            "an interruption that ended without the flag is a call the person then left the app from; "
            + "resuming there starts music out of a pocket"
        )
    }

    func testOnlyTheOldDeviceGoingAwayPauses() {
        XCTAssertEqual(
            PlayerInterruption.routeChange(oldDeviceUnavailable: true), .pause,
            "the headphones came out: Apple's own audio-becoming-noisy, and setHandleAudioBecomingNoisy's job"
        )
        XCTAssertEqual(
            PlayerInterruption.routeChange(oldDeviceUnavailable: false), .nothing,
            "pausing when headphones go IN would be a jump scare, and .override fires when we moved the route"
        )
    }

    func testAMediaServicesResetPauses() {
        XCTAssertEqual(PlayerInterruption.mediaServicesReset, .pause)
    }
}
