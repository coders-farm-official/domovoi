import XCTest
@testable import DomovoiKit

/// What a Home room row **says** — one VoiceOver stop for the left side, the
/// way TalkBack reads Android's one clickable `Column`
/// (`HomeSections.kt:712-782`).
///
/// The case to read first is
/// ``testARoomsTimerChipIsReadWithTheRoomNotAsABareStop()``: it is the one
/// that fails if the chips go back to being drawn (and spoken) outside the
/// row, which is what train I005's tree shows — a `StaticText '24m 58s'`
/// standing alone after `stop kitchen`.
final class HomeRoomSpokenTests: XCTestCase {

    private func room(
        _ id: String,
        status: String? = "online",
        state: String? = nil,
        song: HomeSong? = nil,
        elapsed: Double? = nil,
        wifi: HomeWifi? = nil,
        satType: String = "voice",
        kioskAlive: Bool? = nil,
        lastConnectedAt: String? = nil,
        inCallWith: String? = nil
    ) -> HomeRoom {
        HomeRoom(
            roomID: id,
            status: status,
            lastConnectedAt: lastConnectedAt,
            nowPlaying: (state == nil && song == nil && elapsed == nil)
                ? nil
                : HomeNowPlaying(state: state, song: song, elapsedSec: elapsed),
            wifi: wifi,
            inCallWith: inCallWith,
            satType: satType,
            display: kioskAlive == nil ? nil : HomeDisplay(kioskAlive: kioskAlive)
        )
    }

    private let harvest = HomeSong(title: "Harvest Moon", artist: "Neil Young", durationSec: 302)

    /// The stub house's kitchen, as train I005 drew it, plus its timer chip.
    func testARoomsTimerChipIsReadWithTheRoomNotAsABareStop() {
        let kitchen = room("kitchen", state: "play", song: harvest, elapsed: 99)
        XCTAssertEqual(
            spokenRoom(kitchen, stale: false, elapsedSec: 99, nowMs: 0, timerLeftSec: 1498),
            "kitchen, playing, Harvest Moon · Neil Young, 1:39 / 5:02, 24m 58s"
        )
    }

    /// No timer, no chip: the label ends at the clock, exactly the words
    /// `.accessibilityElement(children: .combine)` used to produce.
    func testAPlayingRoomWithNoChipsReadsAsItAlwaysDid() {
        let kitchen = room("kitchen", state: "play", song: harvest, elapsed: 99)
        XCTAssertEqual(
            spokenRoom(kitchen, stale: false, elapsedSec: 99, nowMs: 0, timerLeftSec: nil),
            "kitchen, playing, Harvest Moon · Neil Young, 1:39 / 5:02"
        )
    }

    /// The stub house's study: offline, last seen three hours ago.
    func testAnOfflineRoomSaysWhenItWasLastSeen() throws {
        let now = try XCTUnwrap(isoMs("2026-09-28T12:00:00Z"))
        let study = room("study", status: "offline", lastConnectedAt: "2026-09-28T09:00:00Z")
        XCTAssertEqual(
            spokenRoom(study, stale: false, elapsedSec: 0, nowMs: now, timerLeftSec: nil),
            "study, offline, last seen 3h ago"
        )
    }

    /// An online quiet room has no pill (``roomPill(_:stale:)`` is nil) and
    /// says `quiet`.
    func testAQuietRoomHasNoPillAndSaysQuiet() {
        XCTAssertEqual(
            spokenRoom(room("hall"), stale: false, elapsedSec: 0, nowMs: 0, timerLeftSec: nil),
            "hall, quiet"
        )
    }

    /// A radio stream has no duration, so the clock is the elapsed alone —
    /// Android's `if (dur > 0) " / …" else ""` (`HomeSections.kt:755`). No
    /// `/ —` for VoiceOver to read as "em dash".
    func testAStreamWithNoDurationReadsTheElapsedAlone() {
        let radio = HomeSong(title: "FIP", durationSec: nil)
        let den = room("den", state: "play", song: radio, elapsed: 72)
        XCTAssertEqual(roomClock(elapsedSec: 72, song: radio), "1:12")
        XCTAssertEqual(
            spokenRoom(den, stale: false, elapsedSec: 72, nowMs: 0, timerLeftSec: nil),
            "den, playing, FIP, 1:12"
        )
    }

    /// A zero duration is no duration on Android (`dur > 0`), not `/ 0:00`.
    func testAZeroDurationReadsTheElapsedAlone() {
        let zero = HomeSong(title: "FIP", durationSec: 0)
        XCTAssertEqual(roomClock(elapsedSec: 72, song: zero), "1:12")
        let den = room("den", state: "play", song: zero, elapsed: 72)
        XCTAssertEqual(
            spokenRoom(den, stale: false, elapsedSec: 72, nowMs: 0, timerLeftSec: nil),
            "den, playing, FIP, 1:12"
        )
    }

    /// All four chips, in Android's order (`HomeSections.kt:776-781`): timer,
    /// call, dead screen, weak link — drawn and spoken from the same list.
    func testTheChipsAreAndroidsFourInAndroidsOrder() {
        let tv = room(
            "lounge",
            wifi: HomeWifi(signalDbm: -80),
            satType: "video",
            kioskAlive: false,
            inCallWith: "kitchen"
        )
        XCTAssertEqual(
            roomChips(tv, timerLeftSec: 45),
            [.timer(45), .inCall("in call with kitchen"), .screenStopped, .weakWifi]
        )
        XCTAssertEqual(
            roomChips(tv, timerLeftSec: 45).map(\.tone),
            [.idle, .idle, .warn, .idle]
        )
        XCTAssertEqual(
            spokenRoom(tv, stale: false, elapsedSec: 0, nowMs: 0, timerLeftSec: 45),
            "lounge, quiet, 45s, in call with kitchen, screen stopped, weak wi-fi"
        )
    }

    /// While the core is down every room is `last known`, and an offline
    /// room's call and link are not news — the chips follow their own rules.
    func testALastKnownRoomSaysSo() {
        let kitchen = room("kitchen", state: "play", song: harvest, elapsed: 99)
        XCTAssertEqual(
            spokenRoom(kitchen, stale: true, elapsedSec: 99, nowMs: 0, timerLeftSec: nil),
            "kitchen, last known, Harvest Moon · Neil Young, 1:39 / 5:02"
        )
        let gone = room("study", status: "offline", wifi: HomeWifi(signalDbm: -90), inCallWith: "x")
        XCTAssertEqual(roomChips(gone, timerLeftSec: nil), [])
    }
}
