import Foundation
import XCTest
@testable import DomovoiKit

/// Photo and video results on the room display (1.5.0, `room_display.media`,
/// domovoi/room_media.py), against docs/uml/satellite-fixtures.json's
/// `room_display_frames`, the file the core's tests read: what is parsed,
/// what is shown and for how long, and which thumbnail paths the room
/// fetches. Android's `RoomDisplayMediaTest`, case for case.
final class RoomDisplayMediaTests: XCTestCase {

    private func frames() throws -> RoomJSON {
        try XCTUnwrap(try RoomTestKit.json("docs/uml/satellite-fixtures.json")["room_display_frames"])
    }

    private func feed(_ text: String, file: StaticString = #filePath, line: UInt = #line) -> RoomFeed {
        guard case .display(let f) = RoomProtocol.parse(text) else {
            XCTFail("not a room_display: \(text)", file: file, line: line)
            return RoomFeed(nowPlaying: nil, timers: [], ringing: nil)
        }
        return f
    }

    private func fixtureFeed(_ frame: String) throws -> RoomFeed {
        feed(try XCTUnwrap(try frames()[frame]).serialized)
    }

    private func state(_ f: RoomFeed?, elapsed: Int64, link: RoomLink = .ready) -> RoomDisplayModel.State {
        RoomDisplayModel.build(RoomDisplayModel.Input(
            status: RoomStatus(link: link, wakeReady: true), running: true, feed: f, volume: 40,
            now: Date(timeIntervalSince1970: 0), elapsedMS: elapsed, timeZone: TimeZone(identifier: "UTC")!,
            locale: Locale(identifier: "en_US"), use24h: false, width: 411, height: 891
        ))
    }

    func testTheFixturesMediaFrameReads() throws {
        let m = try XCTUnwrap(try fixtureFeed("with_media").media)
        XCTAssertEqual(7, m.id)
        XCTAssertEqual(12, m.count)
        XCTAssertFalse(m.more)
        XCTAssertEqual(90_000, m.expiresInMS)
        XCTAssertTrue(m.said.hasPrefix("I found 12 photos"))
        XCTAssertEqual(2, m.items.count)
        let photo = m.items[0], video = m.items[1]
        XCTAssertEqual(.image, photo.kind)
        XCTAssertEqual("/v1/room-media/PLACEHOLDERtokenAAAAAAAAAAAAAAAA", photo.thumb)
        XCTAssertEqual("A beach with a red umbrella.", photo.caption)
        XCTAssertEqual("2025-07-14", photo.date)
        XCTAssertNil(photo.atS)
        XCTAssertEqual(.video, video.kind)
        XCTAssertNil(video.caption)
        XCTAssertEqual(42.0, video.atS)
        // Every key the fixture lists is one this app reads.
        func names(_ key: String) throws -> Set<String> {
            guard case .array(let items)? = try frames()[key] else { return [] }
            return Set(items.compactMap(\.stringValue))
        }
        XCTAssertEqual(["id", "said", "count", "more", "expires_in_ms", "items"], try names("media_keys"))
        XCTAssertEqual(["kind", "thumb", "caption", "date", "at_s"], try names("media_item_keys"))
    }

    func testBeforeMediaAndClearedHaveNone() throws {
        let before = try fixtureFeed("before_media")
        let cleared = try fixtureFeed("media_cleared")
        XCTAssertNil(before.media)
        XCTAssertNil(cleared.media)
        // The rest of the frame is the same with or without media.
        XCTAssertEqual(before.timers, try fixtureFeed("with_media").timers)
        XCTAssertEqual(before.timers, cleared.timers)
    }

    /// A bad `media` is no media; the frame still applies.
    func testABadMediaIsNoMediaNeverAFailedFrame() throws {
        let timers = #""timers":[{"id":7,"kind":"timer","label":"pasta","remaining_ms":1000}]"#
        for bad in [
            #""oops""#, "[]", "{}", #"{"id":"x","items":[]}"#, #"{"id":1}"#, #"{"id":1,"items":"no"}"#,
            #"{"id":1,"items":[{"kind":"gif","thumb":"/v1/room-media/AAAAAAAAAAAAAAAAAAAAAAAA"}]}"#,
        ] {
            let f = feed(#"{"type":"room_display",\#(timers),"media":\#(bad)}"#)
            XCTAssertNil(f.media, bad)
            XCTAssertEqual(1, f.timers.count, bad)
        }
        // At most six items, captions trimmed, a photo's `at_s` dropped, a
        // date that is not one dropped, an unknown thumb a blank tile.
        let items = (1...9).map { _ in
            #"{"kind":"image","thumb":"https://example.com/x.jpg","caption":"  sea  ","date":"last summer","at_s":3}"#
        }.joined(separator: ",")
        let m = try XCTUnwrap(feed(#"{"type":"room_display","media":{"id":3,"said":"s","count":2,"more":true,"items":[\#(items)]}}"#).media)
        XCTAssertEqual(6, m.items.count)
        XCTAssertEqual(6, m.count)
        XCTAssertTrue(m.more)
        XCTAssertEqual(90_000, m.expiresInMS)
        for item in m.items {
            XCTAssertNil(item.thumb)
            XCTAssertEqual("sea", item.caption)
            XCTAssertNil(item.date)
            XCTAssertNil(item.atS)
        }
    }

    /// Exactly the core's route plus one of its tokens (domovoi/room_media.py
    /// TOKEN_RE `^[A-Za-z0-9_-]{20,64}$`), and the cover route as before.
    func testTheRoomFetchesOnlyTheCoresThumbnailRoute() {
        for good in [
            "/v1/room-media/PLACEHOLDERtokenAAAAAAAAAAAAAAAA",
            "/v1/room-media/" + String(repeating: "a", count: 20), "/v1/room-media/" + String(repeating: "Z9_-", count: 16),
        ] {
            XCTAssertTrue(RoomDisplayFeed.isMediaThumbPath(good) && RoomDisplayFeed.isImagePath(good), good)
        }
        for bad in [
            "/v1/room-media/", "/v1/room-media/" + String(repeating: "a", count: 19), "/v1/room-media/" + String(repeating: "a", count: 65),
            "/v1/room-media/abc%2Fdefghijklmnopqrstuv", "/v1/room-media/abcdefghijklmnopqrstuv/x",
            "/v1/room-media/abcdefghijklmnopqrstuv?x=1", "/v1/room-media/abcdefghijklmnopqrstuv#x",
            "/v1/room-media/../music/cover/1", "/v1/room-media/abcdefghij.lmnopqrstuv", "//evil.example/v1/room-media/abcdefghijklmnopqrstuv",
            "http://192.168.1.20:6370/v1/room-media/abcdefghijklmnopqrstuv", "/v1/room-media2/abcdefghijklmnopqrstuv",
            "/api/images/thumb?path=x", " /v1/room-media/abcdefghijklmnopqrstuv",
            "/v1/room-media/abcdefghijklmnopqrstuvé", "/v1/room-media/abcdefghijklmnopqrstuv\n",
        ] {
            XCTAssertFalse(RoomDisplayFeed.isMediaThumbPath(bad) || RoomDisplayFeed.isImagePath(bad), bad)
        }
        XCTAssertTrue(RoomDisplayFeed.isImagePath("/v1/music/cover/12"))
        XCTAssertFalse(RoomDisplayFeed.isArtPath("/v1/room-media/PLACEHOLDERtokenAAAAAAAAAAAAAAAA"), "the cover route takes no token")
    }

    func testResultsShowUntilTheyExpireAndANewIdReplacesThem() throws {
        var f = try fixtureFeed("with_media")
        f.receivedAt = 10_000
        XCTAssertEqual(7, state(f, elapsed: 10_000).media?.id)
        XCTAssertTrue(state(f, elapsed: 10_000).hasCards)
        XCTAssertTrue(state(f, elapsed: 10_000).ticks, "redrawn every second, so they go on time")
        XCTAssertNotNil(state(f, elapsed: 99_999).media, "still there a moment before")
        XCTAssertNil(state(f, elapsed: 100_000).media, "gone after expires_in_ms, with no new frame")
        // Newer results replace them; a cleared frame hides them.
        var next = feed(#"{"type":"room_display","media":{"id":8,"said":"two","count":1,"more":false,"expires_in_ms":90000,"items":[{"kind":"image","thumb":null,"caption":null,"date":null,"at_s":null}]}}"#)
        next.receivedAt = 20_000
        XCTAssertEqual(8, state(next, elapsed: 20_000).media?.id)
        XCTAssertNil(state(try fixtureFeed("media_cleared"), elapsed: 0).media)
        // Not connected: nothing the server said is shown.
        XCTAssertNil(state(f, elapsed: 10_000, link: .connecting).media)
    }

    func testEachTileSaysWhatTheKioskSays() throws {
        let m = try XCTUnwrap(state(try fixtureFeed("with_media"), elapsed: 0).media)
        let photo = m.tiles[0], video = m.tiles[1]
        XCTAssertEqual("Might show: A beach with a red umbrella.", photo.caption)
        XCTAssertEqual("Jul 14, 2025", photo.date)
        XCTAssertFalse(photo.video)
        XCTAssertNil(photo.at)
        XCTAssertEqual("photo, Might show: A beach with a red umbrella., Jul 14, 2025", photo.description)
        XCTAssertTrue(video.video)
        XCTAssertEqual("0:42", video.at)
        XCTAssertNil(video.caption)
        XCTAssertEqual("video at 0:42, Aug 2, 2025", video.description)
        // 12 found, 2 sent: the rest are on the Photos page.
        XCTAssertEqual(RoomDisplayModel.mediaMore, m.more)
        XCTAssertEqual("Might show: a dog", RoomDisplayModel.mightShow("a dog"))
        XCTAssertNil(RoomDisplayModel.mediaDate("2025-13-40", locale: Locale(identifier: "en_US")))
        // All sent and no more: no line.
        let all = feed(#"{"type":"room_display","media":{"id":9,"said":"s","count":1,"more":false,"expires_in_ms":90000,"items":[{"kind":"image","thumb":null}]}}"#)
        XCTAssertNil(state(all, elapsed: 0).media?.more)
        // A caption is drawn as it is: a link or markup is just words.
        let hostile = feed(#"{"type":"room_display","media":{"id":10,"said":"s","count":1,"more":false,"expires_in_ms":90000,"items":[{"kind":"image","caption":"**z** [x](http://y)"}]}}"#)
        XCTAssertEqual("Might show: **z** [x](http://y)", state(hostile, elapsed: 0).media?.tiles.first?.caption)
    }

    /// The card draws the answer and every caption as plain text, never
    /// Markdown or a link.
    func testTheViewsDrawTheCaptionAsPlainText() throws {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { root = root.deletingLastPathComponent() }
        let views = try String(
            contentsOf: root.appendingPathComponent("ios/Domovoi/RoomMode/RoomDisplayViews.swift"), encoding: .utf8
        )
        let card = try XCTUnwrap(views.components(separatedBy: "struct RoomMediaCard").dropFirst().first?
            .components(separatedBy: "struct RoomTimersCard").first)
        XCTAssertTrue(card.contains("Text(verbatim: caption)"))
        XCTAssertTrue(card.contains("Text(verbatim: media.said)"))
        XCTAssertFalse(card.contains("AttributedString") || card.contains("markdown") || card.contains("Link("))
    }
}
