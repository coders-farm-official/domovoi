import Foundation
import XCTest
@testable import DomovoiKit

/// "Why this song?" (Up next 1.3.0): the port of Android's `UpNextWhyTest`
/// (8230ccd2, 795d0250). The server's sentence on a pick — the phone's own
/// session (`service.py` pick_json `why`) and a room's queue
/// (`rooms.py` queue_view `items[].why`) — reaches the row exactly as sent,
/// and only a pick with one gets a tappable chip.
@MainActor
final class UpNextWhyTests: XCTestCase {

    // As domovoi/up_next/why.py words them (capitals and "·" kept: the phone
    // must not touch them). played_together from an album: why.py keeps the
    // album's own capitals.
    private let sentence = "on Harvest Moon with the song you started with · you play this often"
    private let listSentence = "from Road Trip"

    /// pick_json, field for field, with `why` (1.3.0).
    func testASessionPickCarriesTheServersWhyIntoTheQueue() throws {
        let json = """
        {"id":"s1","target":{"kind":"device","device_id":"ios-1a2b"},"mode":"library","autoplay":true,
         "person":null,"seed":{"track_id":812,"title":"Harvest Moon","artist":"Neil Young","label":"Harvest Moon","list":null},
         "picks":[{"seq":1,"track_id":901,"title":"Old Man","artist":"Neil Young","album":"Harvest",
                   "duration_sec":203.4,"reason_code":"same_era","reason_label":"90s","status":"queued",
                   "mpd_song_id":null,"batch_no":1,"why":"\(sentence)"},
                  {"seq":2,"track_id":902,"title":"T","artist":"A","album":"","duration_sec":null,
                   "reason_code":"wildcard","reason_label":"wildcard","status":"queued",
                   "mpd_song_id":null,"batch_no":1,"why":null}],
         "batch_no":1,"have_through_seq":2,"ended":false}
        """
        let s = try JSONDecoder().decode(UpNextSession.self, from: Data(json.utf8))
        let items = s.picks.map { UpNextRules.pickItem($0, sessionID: s.id) }
        XCTAssertEqual(2, items.count)
        XCTAssertEqual(sentence, items.first?.why)
        XCTAssertNil(items.last?.why, "a pick from before 1.3.0 has none")
        // The player's queue view carries it to the row.
        let entries = items.map { PlayerModelMusicEngine.entry($0) }
        XCTAssertEqual(sentence, entries.first?.why)
        XCTAssertEqual(sentence, WhyChip.line(WhyChip.phone(try XCTUnwrap(entries.first)), open: true))
        // Asked for again, it is nobody's pick: no reason, no sentence.
        XCTAssertNil(items.first?.askedFor.why)
    }

    /// The room queue's item (music_queue.py + rooms.queue_view).
    func testARoomQueuePickCarriesTheServersWhy() throws {
        let json = """
        {"room_id":"office","items":[
          {"song_id":11,"pos":0,"file":"a.mp3","title":"A","artist":"X","album":null,"duration_sec":200,
           "added_by":null,"added_by_device_id":null,"added_at":null,"guest_device_id":null,"guest_source":null,
           "playing":true,"origin":"explicit","reason_label":null,"why":null,"up_next_seq":null,"track_id":5,
           "artists":[],"cover_v":null,"cover_url":null},
          {"song_id":12,"pos":1,"file":"b.mp3","title":"B","artist":"Y","album":null,"duration_sec":200,
           "added_by":null,"added_by_device_id":null,"added_at":null,"guest_device_id":null,"guest_source":null,
           "playing":false,"origin":"auto","reason_label":"from road trip","why":"\(listSentence)","up_next_seq":3,
           "track_id":6,"artists":[],"cover_v":null,"cover_url":null}],
         "current_song_id":11,"editable":true,"blocked_reason":null,"guest":false,"admin":false,
         "session_id":"r1","autoplay":true,"up_next":{"session_id":"r1","mode":"list","autoplay":true,
         "seed":{"title":"A","list":{"kind":"playlist","name":"Road Trip"}},"ended":false}}
        """
        let q = try JSONDecoder().decode(RoomQueue.self, from: Data(json.utf8))
        XCTAssertEqual(2, q.items.count)
        let first = try XCTUnwrap(q.items.first)
        let last = try XCTUnwrap(q.items.last)
        XCTAssertNil(WhyChip.room(first), "not a pick: no chip")
        let chip = try XCTUnwrap(WhyChip.room(last))
        XCTAssertTrue(chip.tappable)
        XCTAssertEqual("from road trip", chip.text)
        XCTAssertEqual(listSentence, WhyChip.line(chip, open: true))
    }

    func testTheLineIsTheServersTextExactlyAndOnlyWhenOpen() throws {
        let chip = try XCTUnwrap(WhyChip.of(label: "90s", why: sentence))
        XCTAssertTrue(chip.tappable)
        XCTAssertEqual("90s", chip.text)
        XCTAssertEqual("why this song? 90s", chip.description)
        XCTAssertNil(WhyChip.line(chip, open: false), "closed: nothing under the row")
        XCTAssertEqual(sentence, WhyChip.line(chip, open: true), "as sent, nothing added or changed")
    }

    func testNoWhyIsAPlainChipThatShowsNothing() throws {
        let plain = try XCTUnwrap(WhyChip.of(label: "same artist", why: nil))
        XCTAssertFalse(plain.tappable)
        XCTAssertEqual("same artist", plain.description)
        XCTAssertNil(WhyChip.line(plain, open: true))
        XCTAssertEqual(false, WhyChip.of(label: "same artist", why: "  ")?.tappable, "a blank sentence is none")
        XCTAssertNil(WhyChip.of(label: nil, why: nil), "neither: no chip")
        XCTAssertNil(WhyChip.of(label: " ", why: ""), "blank both: no chip")
        XCTAssertEqual("why?", WhyChip.of(label: nil, why: sentence)?.text, "no label: the web's 'why?'")
    }

    func testARoomEntryThatIsNoPickGetsNoChipEvenWithAReason() {
        let explicit = QueueItem(songID: 1, origin: "explicit", reasonLabel: "90s", why: sentence)
        XCTAssertNil(WhyChip.room(explicit))
    }
}
