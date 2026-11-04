import XCTest
@testable import DomovoiKit

/// Rooms' wire models against the shapes `web/backend/` actually sends.
///
/// The one property every test here is about: **a missing field is a default,
/// never a thrown read**. `JSONDecoder` ignores unknown keys but does not
/// tolerate an absent one, and the roster comes from a server that may be
/// older than any of these fields — the house's own stub seed carries nine of
/// `Satellite`'s forty-odd, which is exactly the case that would have thrown
/// the whole screen away.
final class RoomsModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - the roster

    /// A full row, as `GET /api/satellites` sends one (`schemas.py:513`).
    func testAFullRosterRowDecodesEveryFieldTheScreenReads() throws {
        let row = try decode(RoomsSatellite.self, """
        {"room_id":"kitchen","status":"online",
         "last_connected_at":"2026-11-02T18:00:00Z","last_seen_at":"2026-11-02T19:30:00Z",
         "now_playing":{"room_id":"kitchen","state":"play","elapsed_sec":42.5,
           "song":{"file":"a/b.mp3","title":"Hey Jude","artist":"The Beatles","album":"1967-1970",
                   "duration_sec":431.2},
           "stream_url":"http://10.0.0.9:8000/kitchen","favorited":true},
         "wifi":{"rx_mbits":54.4,"tx_mbits":48.2,"ssid":"house","signal_dbm":-52.0,"mode":"station"},
         "mpd_ports":{"control":6600,"http":8000},
         "version":"aaaa111","full_duplex":true,"in_call_with":"office","voice":"glados",
         "voice_on_server":true,"volume":72,"sat_type":"voice","mic_enabled":true,
         "room_label":"Kitchen","hardware":"pi4","board":"xvf3800","supports":["dropin"],
         "capture_commands":false,"timers_own_only":true,
         "upgrade":{"state":"running","expected_sha":"bbbb222","from_sha":"aaaa111",
                    "back_sha":"aaaa111","started_at":"2026-11-02T19:00:00Z",
                    "deadline_at":"2026-11-02T19:06:00Z","finished_at":null},
         "settings_ignored":[],"wake_word":"domovoi","wake_phrase":"hello domo",
         "pairing":{"paired":true,"paired_at":"2026-10-01T00:00:00Z","last_seen_at":"2026-11-02T19:30:00Z"}}
        """)
        XCTAssertEqual("kitchen", row.roomID)
        XCTAssertTrue(row.online)
        XCTAssertEqual("2026-11-02T19:30:00Z", row.lastSeenAt)
        XCTAssertEqual("2026-11-02T18:00:00Z", row.lastConnectedAt)
        XCTAssertEqual("Hey Jude", row.nowPlaying?.song?.title)
        XCTAssertEqual(431.2, row.nowPlaying?.song?.durationSec)
        XCTAssertEqual("http://10.0.0.9:8000/kitchen", row.nowPlaying?.streamURL)
        XCTAssertEqual("house", row.wifi?.ssid)
        XCTAssertEqual(-52, row.wifi?.signalDbm)
        XCTAssertEqual(6600, row.mpdPorts?.control)
        XCTAssertEqual(72, roomsVolumeLevel(row))
        XCTAssertTrue(row.fullDuplex)
        XCTAssertEqual("office", row.inCallWith)
        XCTAssertTrue(row.timersOwnOnly)
        XCTAssertEqual("running", row.upgrade?.state)
        XCTAssertTrue(row.upgrade?.running == true)
        XCTAssertEqual("bbbb222", row.upgrade?.expectedSha)
        // The twenty-odd keys this screen does not read decoded alongside them
        // without a word of complaint, which is the other half of the rule.
    }

    /// The **stub house's own seed** shape: nine keys, which is also every
    /// older server. Every absent field has to be a default.
    func testTheNineFieldRowDecodesAndEveryAbsentFieldIsADefault() throws {
        let rows = try decode([RoomsSatellite].self, """
        [{"room_id":"kitchen","status":"online","last_connected_at":"2026-11-02T18:00:00Z",
          "now_playing":{"state":"play","song":{"title":"Hey Jude","duration_sec":431},"elapsed_sec":42},
          "wifi":{"rx_mbits":54.4,"tx_mbits":48.2},"in_call_with":null,"sat_type":"voice",
          "room_label":"Kitchen","display":{"kiosk_alive":true}},
         {"room_id":"office","status":"offline"}]
        """)
        XCTAssertEqual(["kitchen", "office"], rows.map(\.roomID))
        let kitchen = rows[0]
        XCTAssertNil(kitchen.lastSeenAt, "and the rules fall back to last_connected_at")
        XCTAssertNil(kitchen.version, "unknown, which is not behind")
        XCTAssertNil(kitchen.volume)
        XCTAssertNil(kitchen.voice)
        XCTAssertNil(kitchen.mpdPorts)
        XCTAssertNil(kitchen.upgrade)
        XCTAssertFalse(kitchen.fullDuplex, "absent means no")
        XCTAssertFalse(kitchen.timersOwnOnly, "absent is off, every room's default")
        XCTAssertNil(kitchen.wifi?.ssid)
        XCTAssertNil(kitchen.wifi?.signalDbm)

        let office = rows[1]
        XCTAssertFalse(office.online)
        XCTAssertNil(office.nowPlaying)
        XCTAssertNil(office.wifi)
    }

    /// `volume` is an `int` in the schema and a `Double?` here on purpose: a
    /// server that ever sent `72.0` must not throw the whole roster away.
    func testAFractionalVolumeDoesNotThrowTheRosterAway() throws {
        let row = try decode(RoomsSatellite.self, #"{"room_id":"kitchen","volume":72.0}"#)
        XCTAssertEqual(72, roomsVolumeLevel(row))
    }

    /// A row with **nothing in it** still decodes, which is what keeps one bad
    /// room from blanking a house.
    func testAnEmptyObjectIsStillARow() throws {
        let row = try decode(RoomsSatellite.self, "{}")
        XCTAssertEqual("", row.roomID)
        XCTAssertNil(row.status)
        XCTAssertFalse(row.online)
    }

    // MARK: - the history tabs

    func testTheSessionsAndConversationsAreBareArrays() throws {
        // `response_model=list[Session]` — a bare array, never a wrapper
        // (`satellites.py:876`).
        let sessions = try decode([RoomsSession].self, """
        [{"id":"3fa85f6457174562b3fc","room_id":"kitchen","started_at":"2026-11-02T19:00:00Z",
          "last_activity":"2026-11-02T19:01:12Z","person_id":4,"intent_count":6},
         {"id":"b1","room_id":"kitchen","started_at":"2026-11-02T17:00:00Z",
          "last_activity":"2026-11-02T17:00:30Z","intent_count":1}]
        """)
        XCTAssertEqual(["3fa85f6457174562b3fc", "b1"], sessions.map { $0.id ?? "" })
        XCTAssertEqual(4, sessions[0].personID)
        XCTAssertNil(sessions[1].personID, "a session nobody was recognised in")
        XCTAssertEqual(1, sessions[1].intentCount)

        let turns = try decode([RoomsTurn].self, """
        [{"id":11,"session_id":"3fa85f6457174562b3fc","at":"2026-11-02T19:00:05Z","room_id":"kitchen",
          "user_text":"put the marmalade on the list","assistant_text":"added it",
          "matched_handler":"lists","matched_path":"lists.add","utterance_trigger":"wake"},
         {"id":12,"session_id":"3fa85f6457174562b3fc","at":"2026-11-02T19:00:40Z",
          "user_text":"what's the weather","assistant_text":"rain","matched_handler":null}]
        """)
        XCTAssertEqual([11, 12], turns.map(\.id))
        XCTAssertEqual("lists", turns[0].matchedHandler)
        XCTAssertNil(turns[1].matchedHandler, "which the pill reads as qa")
        XCTAssertEqual("qa", roomsHandlerPill(turns[1]).text)
    }

    func testTheNotesTimersAndPlayedRowsDecode() throws {
        let notes = try decode([RoomsNote].self, """
        [{"id":3,"room_id":"kitchen","body":"the bins go out on Tuesday","captured_at":"2026-11-01T09:00:00Z"}]
        """)
        XCTAssertEqual("the bins go out on Tuesday", notes.first?.body)

        // One plain timer, one long one, and one the server masked (rule M1).
        let timers = try decode([RoomsTimer].self, """
        [{"id":1,"room_id":"kitchen","label":"pasta","message":null,"is_reminder":false,
          "expires_at":"2026-11-02T19:35:00Z","created_at":"2026-11-02T19:25:00Z"},
         {"id":2,"room_id":"kitchen","label":null,"message":"ring mum","is_reminder":true,
          "expires_at":"2026-11-03T09:00:00Z"},
         {"id":3,"room_id":"kitchen","label":null,"message":null,"is_reminder":true,
          "expires_at":"2026-11-02T20:00:00Z","masked":true}]
        """)
        XCTAssertEqual("pasta", roomsTimerLabel(timers[0]))
        XCTAssertEqual("ring mum", roomsTimerLabel(timers[1]))
        XCTAssertTrue(timers[2].masked)
        XCTAssertEqual("reminder (words hidden)", roomsTimerLabel(timers[2]))

        let played = try decode([RoomsPlayed].self, """
        [{"id":9,"room_id":"kitchen","started_at":"2026-11-02T18:00:00Z","source":"library",
          "title":"Hey Jude","artist":"The Beatles","in_library":true,"can_add":false},
         {"id":10,"room_id":"kitchen","started_at":"2026-11-02T18:05:00Z","source":"podcasts",
          "title":"Episode 4","channel":"Some Show","url":"https://example.test/4",
          "external_id":"ep-4","in_library":false,"can_add":true}]
        """)
        XCTAssertFalse(played[0].canAdd, "a library row has nothing to add")
        XCTAssertTrue(played[1].canAdd)
        XCTAssertEqual("ep-4", played[1].externalID)
        XCTAssertEqual("Some Show", played[1].channel)
    }

    // MARK: - config, quiet hours, timer scope

    func testTheConfigAnswerDecodesEveryFieldTypeAndItsValue() throws {
        let config = try decode(RoomsConfigResponse.self, """
        {"reported":true,"fields":[
          {"name":"mic_enabled","label":"Microphone","group":"audio","type":"bool","tier":"restart","value":true},
          {"name":"greeting_mode","label":"Greeting","group":"voice","type":"choice",
           "choices":["greeting","silent"],"choice_labels":{"greeting":"Spoken greeting, then listen"},
           "value":"greeting"},
          {"name":"gain","label":"Gain","group":"audio","type":"int","unit":"dB","min":0,"max":30,"value":12},
          {"name":"hostname","label":"Host name","type":"text","value":"pi-kitchen","help":"as the router sees it"},
          {"name":"debug","label":"Debug log","section":"advanced","type":"bool","value":false}
        ]}
        """)
        XCTAssertTrue(config.reported)
        XCTAssertEqual(5, config.fields.count)
        XCTAssertEqual("on", roomsConfigValueText(config.fields[0]))
        XCTAssertEqual("Spoken greeting, then listen", roomsConfigValueText(config.fields[1]))
        XCTAssertEqual("12 dB", roomsConfigValueText(config.fields[2]))
        XCTAssertEqual("pi-kitchen", roomsConfigValueText(config.fields[3]))
        XCTAssertEqual("as the router sees it", config.fields[3].help)
        XCTAssertEqual(["debug"], roomsConfigSplit(config.fields).advanced.map(\.name))
        XCTAssertEqual("restart", config.fields[0].tier)

        // The offline room's answer.
        let none = try decode(RoomsConfigResponse.self, #"{"reported":false,"fields":[]}"#)
        XCTAssertFalse(none.reported)
        XCTAssertTrue(none.fields.isEmpty)
        XCTAssertTrue(try decode(RoomsConfigResponse.self, "{}").fields.isEmpty, "an older server")
    }

    func testQuietHoursAndTimerScopeDecode() throws {
        let quiet = try decode(RoomsQuietHours.self, """
        {"room_id":"kitchen","mode":"custom","start":"22:00","end":"07:00"}
        """)
        XCTAssertEqual("custom", quiet.mode)
        XCTAssertEqual("22:00", quiet.start)
        // `mode` defaults to the house's, which is what a room that has never
        // been set follows.
        XCTAssertEqual("house", try decode(RoomsQuietHours.self, #"{"room_id":"office"}"#).mode)

        let scope = try decode(RoomsTimerAnnouncements.self, """
        {"room_id":"kitchen","own_only":true,"since":"2026-11-02T19:00:00Z"}
        """)
        XCTAssertTrue(scope.ownOnly)
        XCTAssertFalse(try decode(RoomsTimerAnnouncements.self, "{}").ownOnly)
    }

    // MARK: - health

    func testTheHealthDocumentDecodesThroughBothItsWrappers() throws {
        let doc = try decode(RoomsHealthDoc.self, """
        {"latest":{"at":"2026-11-02T19:30:00Z","sample":{
           "reboot_needed":"network settings pending",
           "net_step":{"state":"failed","detail":"wpa_supplicant refused the country code",
                       "when":"boot","at":1762100000.0,"version":4,
                       "early":"the host-name unit is not installed"},
           "hostname":"domovoi-kitchen","cpu_pct":4.2}}}
        """)
        XCTAssertEqual("network settings pending", roomsRebootNeeded(doc))
        let line = roomsNetStepLine(doc)
        XCTAssertEqual("failed", line?.word)
        XCTAssertEqual(.err, line?.tone)
        XCTAssertEqual("wpa_supplicant refused the country code", line?.detail)
        XCTAssertEqual("domovoi-kitchen", line?.hostname)
        XCTAssertEqual("the host-name unit is not installed", line?.early)

        // `{"latest": null}` — a room that has never reported one. Both rules
        // read it as nothing to say, and nothing is drawn.
        let empty = try decode(RoomsHealthDoc.self, #"{"latest":null}"#)
        XCTAssertNil(roomsRebootNeeded(empty))
        XCTAssertNil(roomsNetStepLine(empty))
    }

    /// `net_step.version` is `Int64` for the Kotlin's stated reason: **one
    /// out-of-range number must never fail the decoding of the whole health
    /// document**.
    func testAnEnormousNetStepVersionStillDecodes() throws {
        let doc = try decode(RoomsHealthDoc.self, """
        {"latest":{"sample":{"net_step":{"state":"applied","version":9007199254740993},"hostname":"pi"}}}
        """)
        XCTAssertEqual("applied", roomsNetStepLine(doc)?.word)
    }

    // MARK: - the two small answers

    func testTheVersionAndAddByUrlAnswersDecode() throws {
        XCTAssertEqual("aaaa111", try decode(RoomsVersionInfo.self, #"{"sha":"aaaa111"}"#).sha)
        XCTAssertNil(try decode(RoomsVersionInfo.self, "{}").sha, "which is unknown, not behind")

        let refused = try decode(RoomsAddByUrlResult.self, """
        {"queued":false,"already_in_library":false,"already_downloading":false,
         "message":"no fulfiller plugin is installed"}
        """)
        XCTAssertEqual("no fulfiller plugin is installed", roomsAddToast(refused, title: "Episode 4"))
        let queued = try decode(RoomsAddByUrlResult.self, #"{"queued":true,"message":null}"#)
        XCTAssertEqual("queued for download: Episode 4", roomsAddToast(queued, title: "Episode 4"))
    }

    /// The announce answer is **Home's model, reused** — one shape, one
    /// decoder, so the two broadcast buttons cannot drift apart.
    func testTheAnnounceAnswerIsHomesModel() throws {
        let both = try decode(HomeAnnounceResult.self, #"{"announced_to":["kitchen","office"]}"#)
        XCTAssertEqual(["kitchen", "office"], both.announcedTo)
        XCTAssertTrue(try decode(HomeAnnounceResult.self, #"{"announced_to":[]}"#).announcedTo.isEmpty)
        XCTAssertTrue(try decode(HomeAnnounceResult.self, "{}").announcedTo.isEmpty)
    }

    // MARK: - the paths

    /// A room id is a person's words and goes in a **path segment**: a space
    /// must be `%20` and never `+`, which in a path segment is a literal plus
    /// and addresses a room that does not exist.
    func testARoomsNameIsEscapedIntoThePath() {
        XCTAssertEqual("/api/satellites/front%20room/announce", RoomsApi.announce("front room"))
        XCTAssertEqual("/api/satellites/Mum%27s%20office/volume", RoomsApi.volume("Mum's office"))
        XCTAssertEqual("/api/satellites/kitchen/timers/7", RoomsApi.timer("kitchen", 7))
        XCTAssertEqual("/api/satellites/kitchen/sessions?limit=100", RoomsApi.sessions("kitchen"))
        XCTAssertEqual("/api/satellites/kitchen/conversations?limit=300", RoomsApi.conversations("kitchen"))
        XCTAssertEqual("/api/satellites/kitchen/recently-played?limit=100", RoomsApi.recentlyPlayed("kitchen"))
        XCTAssertEqual("/api/satellites/kitchen/timer-announcements", RoomsApi.timerScope("kitchen"))
        XCTAssertEqual("/api/satellites/kitchen/quiet-hours", RoomsApi.quietHours("kitchen"))
        XCTAssertEqual("/api/satellites/kitchen/dropin/end", RoomsApi.dropInEnd("kitchen"))
        XCTAssertEqual("/api/satellites/kitchen/health", RoomsApi.health("kitchen"))
        XCTAssertEqual("/api/satellites/kitchen/config", RoomsApi.config("kitchen"))
    }
}
