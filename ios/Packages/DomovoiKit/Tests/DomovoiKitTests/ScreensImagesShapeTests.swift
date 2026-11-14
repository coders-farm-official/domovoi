import Foundation
import XCTest
@testable import DomovoiKit

/// The Images shapes this batch added or verified: The owner's `4 images,
/// showing 3` (answer 27), and the plugin's own fields as the server really
/// sends them (answers 6 and 25).
///
/// **Its own file, and named after this unit.** `ImagesTests` is not in the
/// `screens` unit's owned list (BATCH-RULES §1: "edit those and nothing
/// else"), and two branches adding cases to one `final class` is a merge
/// conflict a train pays for. The one change made inside `ImagesTests` is the
/// two words in the seed that copies `fixtures/images.py`, which this unit
/// did move, and that case says so where it stands.
///
/// ### What was verified, and against what
///
/// The plugin is not in this repository — it mounts its own router under
/// `/api/plugins/imagegen` (`web/backend/plugin_host.py:531`) — so every
/// shape in `ImagesModels` was transcribed from Android's client,
/// `ImagesScreen.kt:76-125`. On 2026-11-03 the owner said to check it out beside
/// the app and verify (answers 6 and 25), and the repository is
/// `domovoi-plugin-imagegen`, checked out beside the app. One serialiser, `_job_row`
/// (`domovoi_plugin_imagegen/web.py:114-127`), over the columns of
/// `migrations/V001__imagegen_schema.sql`.
///
/// Two values Android's transcription had invented are corrected in the seeds
/// below: `kind` was `txt2img` where the column is `CHECK`-ed to `generate`
/// or `pull`, and a finished `status` was `completed` where the five allowed
/// words are `pending`, `running`, `done`, `failed`, `cancelled`. Both fell
/// through ``ImagesJobStatus/other(_:)`` and drew their own word, so no
/// behaviour changes; what changes is that these tests describe a server that
/// exists.
@MainActor
final class ScreensImagesShapeTests: XCTestCase {

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    /// One row with four images and full params, one with a width and no
    /// height — and the server's own `requested_at`, `params.negative` and
    /// `more` beside them.
    private let historyJSON = """
    {"history":[
    {"id":9,"kind":"generate","model":"sdxl-turbo","prompt":"an orchard at dusk",
     "params":{"width":1024,"height":1024,"steps":28,"seed":773451,"negative":""},
     "status":"done","completed_at":"2026-11-02T01:02:03Z",
     "requested_at":"2026-11-02T01:01:40.220+00:00",
     "result":{"images":["orchard-1.png","orchard-2.png","orchard-3.png","orchard-4.png"]}},
    {"id":8,"kind":"generate","model":"sd15","prompt":"a tram in the rain",
     "params":{"width":512},"status":"done","result":{"images":[]}}],
    "more":false}
    """

    // MARK: - answer 27: the card counts the pictures it is not showing

    /// **The owner's answer 27**: a card with more pictures than tiles says so.
    ///
    /// The row with four images says `4 images, showing 3`; the row with one
    /// says nothing at all. Both halves, because a sentence drawn on every
    /// card would read `1 images, showing 1` on most of them — and that is
    /// the sabotage this case is here for (SAB4: `>` to `>=`).
    func testACardWithMorePicturesThanTilesCountsThem() throws {
        let rows = try decode(ImagesHistoryList.self, historyJSON).history
        let four = rows[0]
        XCTAssertTrue(four.hasMoreImagesThanTiles)
        XCTAssertEqual(
            "4 images, showing 3",
            ImagesStrings.moreImages(total: four.imageNames.count, shown: four.tileNames.count),
            "his words, verbatim (answer 27)"
        )

        // The row with an empty `images` array, and one with exactly three:
        // neither has anything to say.
        XCTAssertFalse(rows[1].hasMoreImagesThanTiles)
        let exactlyThree = try decode(
            ImagesJob.self, #"{"result":{"images":["a.png","b.png","c.png"]}}"#
        )
        XCTAssertEqual(3, exactlyThree.tileNames.count)
        XCTAssertFalse(
            exactlyThree.hasMoreImagesThanTiles,
            "three of three is not `3 images, showing 3`"
        )
        XCTAssertFalse(ImagesJob().hasMoreImagesThanTiles, "and a row with no images is silent")
    }

    /// The same rule **against the fixture's own rows**, which is what keeps
    /// the UI case from going vacuous.
    ///
    /// Row 21 is the four-image row and row 22 the one-image row
    /// (`fixtures/images.py:199-247`), so the seed itself has to keep driving
    /// both halves of the sentence: a seed trimmed to three images would make
    /// `testImages_AHistoryCardShowsThreeTilesOfFour`'s `4 images, showing 3`
    /// assertion true of nothing, and nothing else would fail.
    ///
    /// Split from `ImagesTests.testTheFixturesOwnRowsDriveTheLaneTheMetaLineAndTheTiles`,
    /// which holds the lane filter and the meta line against the same
    /// fixture, because that file is not this unit's to add cases to.
    func testTheFixturesFourImageRowStillDrivesTheSentence() throws {
        let fixtureHistory = """
        {"history":[
        {"id":21,"kind":"generate","model":"sdxl-turbo",
         "prompt":"a kingfisher on a frozen reed",
         "params":{"width":1024,"height":1024,"steps":28,"seed":773451,"negative":""},
         "status":"done","pct":100,
         "result":{"images":["kingfisher 01.png","kingfisher-02.png","kingfisher-03.png","kingfisher-04.png"]}},
        {"id":22,"kind":"generate","model":"flux-schnell",
         "prompt":"a tin robot watering a window box",
         "params":{"width":512,"height":null,"steps":4,"seed":null},
         "status":"done","pct":100,"result":{"images":["robot-01.png"]}}],
        "more":false}
        """
        let history = try decode(ImagesHistoryList.self, fixtureHistory).history
        XCTAssertEqual([21, 22], history.map(\.id))
        XCTAssertEqual(4, history[0].imageNames.count, "row 21 is the four-image row")
        XCTAssertTrue(history[0].hasMoreImagesThanTiles)
        XCTAssertEqual(
            "4 images, showing 3",
            ImagesStrings.moreImages(
                total: history[0].imageNames.count, shown: history[0].tileNames.count
            )
        )
        // Row 22, which is the half the UI case could not see for a round:
        // its assertion matched on a suffix the one-image wording does not
        // share (review 1, finding 5), so the gate being removed was invisible
        // there. Here it is the count itself.
        XCTAssertEqual(1, history[1].imageNames.count, "row 22 is the one-image row")
        XCTAssertFalse(
            history[1].hasMoreImagesThanTiles,
            "the one-image card would draw `1 images, showing 1`"
        )
    }

    // MARK: - the shapes, against the plugin's own code

    /// The two values Android's client invented, and the three fields the
    /// server sends that nothing here reads.
    ///
    /// `_job_row` (`web.py:114-127`) also serves `requested_at`, `params`
    /// carries `negative` (`:383-389`) and `…/history` carries `more`
    /// (`:649`). This case is what fails if a later pass "tidies" one of
    /// those three out of the fixtures on the belief that the server does not
    /// send it, or adds a decoder for one without a screen to draw it.
    func testTheServersOwnExtraFieldsAreCarriedAndIgnored() throws {
        // A row carrying them decodes exactly as before: an unknown key is
        // not an error for a hand-written `decodeIfPresent` decoder, which is
        // the promise ``ImagesModels``' header makes about a plugin shipping
        // a new field.
        let row = try decode(ImagesHistoryList.self, historyJSON).history[0]
        XCTAssertEqual(773_451, row.params?.seed)
        XCTAssertEqual(28, row.params?.steps)
        XCTAssertEqual("generate", row.kind, "the column allows `generate` and `pull`")
        XCTAssertEqual("done", row.statusWord, "and `done`, never `completed`")
        XCTAssertFalse(row.status.inLane, "`done` is history, not the lane")

        // And `more` beside the `history` key changes nothing about the
        // wrapper.
        XCTAssertEqual(2, try decode(ImagesHistoryList.self, historyJSON).history.count)

        // A whole-engine-state answer to `…/models`, which is what the route
        // actually sends (`web.py:270-288` over `engine.py:177-205`) and what
        // the stub house now serves: sixteen keys beside `models`, three of
        // them read.
        let fat = try decode(ImagesGenState.self, """
        {"installed":true,"running":true,"desired_running":true,"autostart":true,
         "url":"http://127.0.0.1:8188","url_error":null,
         "pinned_revision":"v0.3.45 (1b5f2c9d0e7a)","gpu_index":0,
         "engine_dir":"/e","models_dir":"/m","output_dir":"/o",
         "output_in_pictures":true,"git_available":true,
         "install":{"running":false,"stage":"done","status_text":"installed",
                    "error":null,"pct":100},
         "core_online":true,"engine_online":true,
         "models":[{"id":"z-image-turbo","label":"Z-Image-Turbo","installed":true,
                    "recommended":true,"partial":false,"description":"6B distilled",
                    "license":"Apache-2.0","speed":"fast","est_vram_gb":13,
                    "download_gb":20.5,"default_size":[1024,1024]}]}
        """)
        XCTAssertTrue(fat.ready, "the two fields the engine line reads are top-level")
        XCTAssertEqual(["z-image-turbo"], fat.installedModels.map(\.id))
        XCTAssertEqual("Z-Image-Turbo  ·  recommended", fat.installedModels[0].menuLabel)
    }

    /// The five words the `status` column allows, and one from outside it.
    ///
    /// `ImagesTests.testTheLanePredicatesMatchAndroidsForEveryWord` holds the
    /// same table with `completed` in it, which is a word the plugin has
    /// never sent; this adds the two that are real and the one that is not,
    /// so that a migration widening the constraint is caught by a case that
    /// expects it to.
    func testTheStatusWordsAreTheColumnsOwn() {
        let table: [(String, Bool, Bool)] = [
            ("pending", true, true),
            ("running", true, true),
            ("failed", false, true),
            ("done", false, false),
            ("cancelled", false, false),
            // A word from outside the constraint still draws itself, because
            // a migration may widen it — ``ImagesJobStatus/other(_:)``.
            ("queued-elsewhere", false, false),
        ]
        for (word, active, lane) in table {
            let status = ImagesJobStatus(word)
            XCTAssertEqual(active, status.isActive, word)
            XCTAssertEqual(lane, status.inLane, word)
            XCTAssertEqual(word, status.word, "the raw word is what a pill draws")
        }
    }
}
