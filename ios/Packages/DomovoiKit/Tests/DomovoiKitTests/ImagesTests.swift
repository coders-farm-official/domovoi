import XCTest
@testable import DomovoiKit

/// The Images screen's data layer: what decodes, what the rules say at their
/// boundaries, what a tap sends, what it refuses to send, and where an answer
/// lands when the house changed while it was in flight.
///
/// The Image Generation plugin is **not in this repository** — it mounts its
/// own router under `/api/plugins/imagegen` (`plugin_host.py:531`) — so every
/// shape below was Android's `ImagesScreen.kt:76-125`, transcribed, and these
/// tests are the only specification either platform has for them:
/// `ImagesScreen.kt` is a composable and Android's JVM suite is unit-only, so
/// nothing over there tests the lane filter, the two wrapper keys or the meta
/// line's "both or neither".
///
/// **The shapes are now verified against the plugin's own code**
/// (`domovoi-project/domovoi-plugin-imagegen`, Kamron's answers 6 and 25,
/// 2026-11-03), and the JSON below is the server's: one serialiser `_job_row`
/// (`domovoi_plugin_imagegen/web.py:114-127`) over the columns of
/// `migrations/V001__imagegen_schema.sql`. Two values in it were invented and
/// are corrected — `kind` was `txt2img` where the column is `CHECK`-ed to
/// `generate` or `pull`, and a finished `status` was `completed` where the
/// five allowed words are `pending`, `running`, `done`, `failed`,
/// `cancelled`. Both fell through ``ImagesJobStatus/other(_:)`` and drew
/// their own word, so no behaviour changes; what changes is that these tests
/// now describe a server that exists.
@MainActor
final class ImagesTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the server's JSON, as the plugin sends it

    /// Three models, one of them not installed — the answer `…/models` gives.
    private let modelsJSON = """
    {"installed":true,"engine_online":true,"models":[
    {"id":"sdxl-turbo","label":"SDXL Turbo","installed":true,"recommended":true},
    {"id":"sd15","label":"Stable Diffusion 1.5","installed":true,"recommended":false},
    {"id":"flux-dev","label":"FLUX.1 dev","installed":false,"recommended":false}]}
    """

    /// A running job, a failed one and a completed one. The completed one is
    /// the row that discriminates: it must not reach the lane.
    private let jobsJSON = """
    {"jobs":[
    {"id":11,"kind":"generate","model":"sdxl-turbo","prompt":"a lighthouse in fog",
     "status":"running","pct":42,"status_text":"sampling step 7 of 20",
     "requested_at":"2026-11-02T01:00:11.004+00:00","completed_at":null},
    {"id":12,"kind":"generate","model":"sdxl-turbo","prompt":"a brass orrery",
     "status":"failed","error":"the engine ran out of memory",
     "requested_at":"2026-11-02T00:58:02.100+00:00",
     "completed_at":"2026-11-02T00:58:31.700+00:00"},
    {"id":13,"kind":"generate","model":"sd15","prompt":"a quiet harbour",
     "status":"done","result":{"images":["harbour-1.png"]}}]}
    """

    /// One row with four images and full params, one with a width and no
    /// height — the branch at `ImagesScreen.kt:387`.
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

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    func testTheModelsAnswerDecodesEveryField() throws {
        let state = try decode(ImagesGenState.self, modelsJSON)
        XCTAssertTrue(state.installed)
        XCTAssertTrue(state.engineOnline)
        XCTAssertTrue(state.ready)
        XCTAssertEqual(3, state.models.count)
        XCTAssertEqual(["sdxl-turbo", "sd15"], state.installedModels.map(\.id))
        XCTAssertEqual("SDXL Turbo  ·  recommended", state.installedModels[0].menuLabel)
        XCTAssertEqual("Stable Diffusion 1.5", state.installedModels[1].menuLabel)
    }

    func testAJobDecodesEveryFieldIncludingTheOnesNothingDraws() throws {
        let jobs = try decode(ImagesJobList.self, jobsJSON).jobs
        let running = jobs[0]
        XCTAssertEqual(11, running.id)
        // Decoded and deliberately never drawn — and `generate` is one of
        // the two words the column allows (the other is `pull`, which is a
        // model download and never reaches this screen's lane).
        XCTAssertEqual("generate", running.kind)
        XCTAssertEqual("sdxl-turbo", running.model)
        XCTAssertEqual("a lighthouse in fog", running.prompt)
        XCTAssertEqual(.running, running.status)
        XCTAssertEqual(42, running.pct)
        XCTAssertEqual("sampling step 7 of 20", running.statusText)
        XCTAssertEqual("sampling step 7 of 20 · 42%", running.progressLine)
        XCTAssertNil(running.error)
        XCTAssertEqual("the engine ran out of memory", jobs[1].error)
        XCTAssertEqual("2026-11-02T01:02:03Z", try decode(ImagesHistoryList.self, historyJSON).history[0].completedAt)
    }

    /// `JobList` and `HistoryList` are two wrappers with two **different**
    /// keys over one row type (`ImagesScreen.kt:120`, `:123`). Reading one
    /// with the other's key decodes to an empty list — which on screen looks
    /// exactly like "nothing has happened yet", the trap
    /// `fixtures/README.md` records from Home's four reads.
    func testHistoryAndTheJobListAreReadFromTheirOwnKeys() throws {
        XCTAssertEqual(3, try decode(ImagesJobList.self, jobsJSON).jobs.count)
        XCTAssertEqual(2, try decode(ImagesHistoryList.self, historyJSON).history.count)
        // The cross-reads: each wrapper over the other's body sees nothing,
        // and neither throws — which is why the bug is silent.
        XCTAssertTrue(try decode(ImagesHistoryList.self, jobsJSON).history.isEmpty)
        XCTAssertTrue(try decode(ImagesJobList.self, historyJSON).jobs.isEmpty)
        // Nor is either one a bare array.
        XCTAssertNil(try? decode([ImagesJob].self, jobsJSON))
    }

    /// Kotlin's `coerceInputValues` plus `data class` defaults, which is what
    /// the hand-written `decodeIfPresent ?? default` is for: an explicit
    /// `null` in a field Kotlin declares non-null must not throw.
    func testAnExplicitNullInEveryNonNullFieldDecodesToItsDefault() throws {
        let job = try decode(ImagesJob.self, """
        {"id":null,"kind":null,"model":null,"prompt":null,"params":null,
         "status":null,"pct":null,"status_text":null,"error":null,
         "result":null,"completed_at":null}
        """)
        XCTAssertEqual(0, job.id)
        XCTAssertEqual("", job.kind)
        XCTAssertEqual("", job.model)
        XCTAssertEqual("", job.statusWord)
        XCTAssertEqual(.other(""), job.status)
        XCTAssertNil(job.prompt)
        XCTAssertNil(job.params)
        XCTAssertTrue(job.imageNames.isEmpty)

        let state = try decode(ImagesGenState.self, #"{"installed":null,"engine_online":null,"models":null}"#)
        XCTAssertFalse(state.ready)
        XCTAssertTrue(state.models.isEmpty)

        let params = try decode(ImagesJobParams.self, #"{"width":null,"height":null,"steps":null,"seed":null}"#)
        XCTAssertNil(params.width)
        XCTAssertNil(params.seed)

        XCTAssertTrue(try decode(ImagesJobResult.self, #"{"images":null}"#).images.isEmpty)
        XCTAssertTrue(try decode(ImagesJobList.self, #"{"jobs":null}"#).jobs.isEmpty)
        XCTAssertTrue(try decode(ImagesHistoryList.self, #"{"history":null}"#).history.isEmpty)
    }

    func testAnEmptyObjectDecodesToEveryDefault() throws {
        let job = try decode(ImagesJob.self, "{}")
        XCTAssertEqual(ImagesJob(), job)
        XCTAssertEqual("", job.laneTitle)
        XCTAssertEqual(ImagesStrings.working, job.progressLine)
        XCTAssertEqual(ImagesGenState(), try decode(ImagesGenState.self, "{}"))
        XCTAssertEqual(ImagesJobParams(), try decode(ImagesJobParams.self, "{}"))
        XCTAssertEqual(ImagesJobList(), try decode(ImagesJobList.self, "{}"))
        XCTAssertEqual(ImagesHistoryList(), try decode(ImagesHistoryList.self, "{}"))
    }

    /// A seed is a Kotlin `Long` (`ImagesScreen.kt:116`), so a value past
    /// 2^31 must survive the decode rather than overflow.
    func testASeedBeyondThirtyTwoBitsSurvives() throws {
        let params = try decode(ImagesJobParams.self, #"{"seed":9007199254740991}"#)
        XCTAssertEqual(9_007_199_254_740_991, params.seed)
        let job = try decode(ImagesJob.self, #"{"id":4611686018427387903}"#)
        XCTAssertEqual(4_611_686_018_427_387_903, job.id)
    }

    // MARK: - the status words, and the two predicates

    /// `status` is an open string: Android compares it to four literals and
    /// lets everything else fall through (`:180-183`, `:329`, `:335-338`).
    /// The pill draws the raw word (`:334`), so an unheard-of status must
    /// render **its own word**.
    func testAnUnknownStatusKeepsItsWordAndIsNeitherActiveNorInTheLane() {
        let queued = ImagesJobStatus("queued-behind-another-house")
        XCTAssertEqual("queued-behind-another-house", queued.word)
        XCTAssertFalse(queued.isActive)
        XCTAssertFalse(queued.inLane)
        XCTAssertFalse(queued.isFailure)
    }

    func testTheLanePredicatesMatchAndroidsForEveryWord() {
        let table: [(String, active: Bool, lane: Bool)] = [
            ("pending", true, true),
            ("running", true, true),
            ("failed", false, true),
            // The server's fifth and fourth words. `done` replaces
            // `completed`, which no version of the plugin has ever sent:
            // the `CHECK` allows `pending`, `running`, `done`, `failed`,
            // `cancelled` and nothing else.
            ("done", false, false),
            ("cancelled", false, false),
            // And a word from outside the constraint still draws itself,
            // because a migration may widen it.
            ("queued-elsewhere", false, false),
        ]
        for (word, active, lane) in table {
            let status = ImagesJobStatus(word)
            XCTAssertEqual(word, status.word)
            XCTAssertEqual(active, status.isActive, "isActive for \(word)")
            XCTAssertEqual(lane, status.inLane, "inLane for \(word)")
        }
    }

    // MARK: - the history card's two rules

    /// `:385-390`. The size is drawn **only when both** width and height are
    /// present (`:387`); `model` is a non-null Kotlin string and stays in the
    /// join even when it is empty.
    func testTheMetaLineNeedsBothWidthAndHeight() throws {
        let rows = try decode(ImagesHistoryList.self, historyJSON).history
        XCTAssertEqual("sdxl-turbo · 1024 × 1024 · 28 steps · seed 773451", rows[0].metaLine)
        // A width and no height: no `×` anywhere, and no half-size either.
        XCTAssertEqual("sd15", rows[1].metaLine)
        XCTAssertFalse(rows[1].metaLine.contains("×"))
        XCTAssertFalse(rows[1].metaLine.contains("512"))

        // Height alone is just as refused, and steps and seed still show.
        let heightOnly = ImagesJob(
            model: "sd15",
            params: ImagesJobParams(height: 768, steps: 12, seed: 7)
        )
        XCTAssertEqual("sd15 · 12 steps · seed 7", heightOnly.metaLine)

        // No params at all.
        XCTAssertEqual("sd15", ImagesJob(model: "sd15").metaLine)
    }

    /// `names.take(3)` (`:398`). The fourth image is not drawn and not
    /// counted on the card — but the **confirmation counts every one of
    /// them** (`:275`), which is the pair this test exists to keep together.
    func testAHistoryCardDrawsThreeTilesOfFourAndTheDialogCountsFour() throws {
        let row = try decode(ImagesHistoryList.self, historyJSON).history[0]
        XCTAssertEqual(4, row.imageNames.count)
        XCTAssertEqual(["orchard-1.png", "orchard-2.png", "orchard-3.png"], row.tileNames)
        XCTAssertFalse(row.tileNames.contains("orchard-4.png"))
        XCTAssertEqual(
            "Delete this generation and its 4 images? This can't be undone.",
            ImagesStrings.deleteBody(images: row.imageNames.count)
        )
        // **A tile says its position and never "of n".** The card draws three
        // of this row's four pictures, so a label reading `picture 1 of 3`
        // would be counting the tiles while sounding like a count of the
        // pictures — and the only count on this card that means anything is
        // the dialog's four, asserted above.
        XCTAssertEqual("picture 1", ImagesStrings.tile(position: 1))
        XCTAssertEqual("picture 3", ImagesStrings.tile(position: row.tileNames.count))
        XCTAssertFalse(
            ImagesStrings.tile(position: 1).contains("of"),
            "a tile's label counts the tiles, which is not the number of pictures"
        )
    }

    /// **Kamron's answer 27**: a card with more pictures than tiles says so.
    ///
    /// The row with four images says `4 images, showing 3`; the row with one
    /// says nothing at all. Both halves, because a sentence drawn on every
    /// card would read `1 images, showing 1` on most of them.
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
        let exactlyThree = try decode(ImagesJob.self, #"{"result":{"images":["a.png","b.png","c.png"]}}"#)
        XCTAssertEqual(3, exactlyThree.tileNames.count)
        XCTAssertFalse(
            exactlyThree.hasMoreImagesThanTiles,
            "three of three is not `3 images, showing 3`"
        )
        XCTAssertFalse(ImagesJob().hasMoreImagesThanTiles, "and a row with no images is silent")
    }

    // MARK: - the shapes, against the plugin's own code

    /// The two values this file used to invent, and the three fields the
    /// server sends that nothing here reads.
    ///
    /// Verified against `domovoi-plugin-imagegen` (answers 6 and 25): `kind`
    /// is `CHECK`-ed to `generate`/`pull` and `status` to five words
    /// (`migrations/V001__imagegen_schema.sql`), and `_job_row`
    /// (`web.py:114-127`) also serves `requested_at`, while `params` carries
    /// `negative` (`:383-389`) and `…/history` carries `more` (`:649`).
    ///
    /// This case is what fails if a later pass "tidies" one of those three
    /// out of the fixtures on the belief that the server does not send it, or
    /// adds a decoder for one without a screen to draw it.
    func testTheServersOwnExtraFieldsAreCarriedAndIgnored() throws {
        // `requested_at` and `params.negative` are in the seed above, and a
        // row carrying them decodes exactly as before: an unknown key is not
        // an error for a hand-written `decodeIfPresent` decoder, which is the
        // promise ``ImagesModels``' header makes about a plugin shipping a
        // new field.
        let row = try decode(ImagesHistoryList.self, historyJSON).history[0]
        XCTAssertEqual(773_451, row.params?.seed)
        XCTAssertEqual(28, row.params?.steps)
        XCTAssertEqual("generate", row.kind)
        XCTAssertEqual("done", row.statusWord)
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

    /// `:273`'s plural, at the three counts that matter: none, Kotlin's one
    /// special case, and several.
    func testTheConfirmationPluralises() {
        XCTAssertEqual(
            "Delete this generation and its 0 images? This can't be undone.",
            ImagesStrings.deleteBody(images: 0)
        )
        XCTAssertEqual(
            "Delete this generation and its 1 image? This can't be undone.",
            ImagesStrings.deleteBody(images: 1)
        )
        XCTAssertEqual(
            "Delete this generation and its 2 images? This can't be undone.",
            ImagesStrings.deleteBody(images: 2)
        )
    }

    // MARK: - the paths

    /// `Uri.encode` with no allowed set escapes `/` too (`:125`), so the name
    /// is one opaque path segment. ``FilePath/escPath(_:)`` deliberately lets
    /// `/` through and must **not** be what this uses: that way round is a
    /// quiet traversal in one direction and a quiet 404 in the other.
    func testTheFilePathEscapesEverySeparatorInAName() {
        XCTAssertEqual("/api/plugins/imagegen/file/orchard-1.png", ImagesPaths.file("orchard-1.png"))
        XCTAssertEqual("/api/plugins/imagegen/file/a%20cat.png", ImagesPaths.file("a cat.png"))
        XCTAssertEqual("/api/plugins/imagegen/file/sub%2Fdir%2Fx.png", ImagesPaths.file("sub/dir/x.png"))
        XCTAssertEqual("/api/plugins/imagegen/file/..%2F..%2Fetc%2Fpasswd", ImagesPaths.file("../../etc/passwd"))
        XCTAssertEqual("/api/plugins/imagegen/file/x%23y%3Fz%26w.png", ImagesPaths.file("x#y?z&w.png"))
        // The trap in one line: `escPath` would leave the separators alone.
        XCTAssertTrue(FilePath.escPath("sub/dir/x.png").contains("/"))
        XCTAssertFalse(ImagesPaths.file("sub/dir/x.png").dropFirst("/api/plugins/imagegen/file/".count).contains("/"))
    }

    func testTheSevenRoutesAreTheSevenAndroidCalls() {
        XCTAssertEqual("/api/plugins/imagegen/models", ImagesPaths.models)
        XCTAssertEqual("/api/plugins/imagegen/jobs", ImagesPaths.jobs)
        XCTAssertEqual("/api/plugins/imagegen/history", ImagesPaths.history)
        XCTAssertEqual("/api/plugins/imagegen/generate", ImagesPaths.generate)
        XCTAssertEqual("/api/plugins/imagegen/jobs/11/cancel", ImagesPaths.cancel(job: 11))
        XCTAssertEqual("/api/plugins/imagegen/history/9", ImagesPaths.deleteHistory(job: 9))
        XCTAssertEqual(["imagegen.jobs.changed"], ImagesPaths.events)
    }

    /// Exactly two keys, the prompt trimmed (`:164`), and a prompt with a
    /// quotation mark and a newline in it still makes valid JSON — which is
    /// why the body goes through an encoder and not through interpolation.
    func testTheGenerateBodyIsTwoKeysAndTheTrimmedPrompt() throws {
        let body = ImagesPaths.generateBody(model: "sdxl-turbo", prompt: "  a \"brass\" orrery\n  ")
        let fields = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(["model", "prompt"], fields.keys.sorted())
        XCTAssertEqual("sdxl-turbo", fields["model"] as? String)
        XCTAssertEqual("a \"brass\" orrery", fields["prompt"] as? String)
    }

    // MARK: - the rules the button and the picker read

    /// `:231`'s four conjuncts, one at a time — which is the only way to
    /// catch one of them having been dropped.
    func testGenerateIsOffForEachMissingThing() {
        let ready = ImagesGenState(
            installed: true,
            engineOnline: true,
            models: [ImagesGenModel(id: "sdxl-turbo", label: "SDXL Turbo", installed: true)]
        )
        let active = ready.activeModel(chosen: nil)
        XCTAssertNotNil(active)

        XCTAssertTrue(ImagesModel.canGenerate(state: ready, active: active, prompt: "a cat", busy: false))

        // No read at all.
        XCTAssertFalse(ImagesModel.canGenerate(state: nil, active: active, prompt: "a cat", busy: false))
        // Not installed.
        XCTAssertFalse(ImagesModel.canGenerate(
            state: ImagesGenState(installed: false, engineOnline: true, models: ready.models),
            active: active, prompt: "a cat", busy: false
        ))
        // Engine offline.
        XCTAssertFalse(ImagesModel.canGenerate(
            state: ImagesGenState(installed: true, engineOnline: false, models: ready.models),
            active: active, prompt: "a cat", busy: false
        ))
        // No model.
        XCTAssertFalse(ImagesModel.canGenerate(state: ready, active: nil, prompt: "a cat", busy: false))
        // Empty prompt, and a blank one: Kotlin's `isNotBlank`, so three
        // spaces and a newline enable nothing.
        XCTAssertFalse(ImagesModel.canGenerate(state: ready, active: active, prompt: "", busy: false))
        XCTAssertFalse(ImagesModel.canGenerate(state: ready, active: active, prompt: "   \n ", busy: false))
        // Already generating.
        XCTAssertFalse(ImagesModel.canGenerate(state: ready, active: active, prompt: "a cat", busy: true))
    }

    /// `:148`, `:153` and `:307-308`: only installed models are offered, the
    /// active one falls back to the first installed, and a chosen model the
    /// engine has since dropped does not leave the picker pointing at
    /// nothing.
    func testThePickerOffersOnlyInstalledModelsAndFallsBack() throws {
        let state = try decode(ImagesGenState.self, modelsJSON)
        XCTAssertEqual(["SDXL Turbo", "Stable Diffusion 1.5"], state.installedModels.map(\.label))
        XCTAssertFalse(state.installedModels.contains { $0.label == "FLUX.1 dev" })

        XCTAssertEqual("sdxl-turbo", state.activeModel(chosen: nil)?.id)
        XCTAssertEqual("SDXL Turbo", state.pickerLabel(chosen: nil))
        XCTAssertEqual("sd15", state.activeModel(chosen: "sd15")?.id)
        // An uninstalled id is not choosable: it falls back to the first
        // installed one rather than to a model that cannot generate.
        XCTAssertEqual("sdxl-turbo", state.activeModel(chosen: "flux-dev")?.id)

        let none = ImagesGenState(installed: true, engineOnline: true, models: [
            ImagesGenModel(id: "flux-dev", label: "FLUX.1 dev", installed: false),
        ])
        XCTAssertNil(none.activeModel(chosen: nil))
        XCTAssertEqual(ImagesStrings.noModelsInstalled, none.pickerLabel(chosen: nil))
    }

    /// `:190-212`, in Android's own `when` order — and the order **is** the
    /// behaviour: "no data and loading" before "no data", or a screen that is
    /// still reading announces that the plugin did not answer.
    func testTheEngineLineFollowsAndroidsOrder() {
        XCTAssertEqual(.loading, ImagesEngineLine.of(nil, loading: true, error: nil))

        XCTAssertEqual(
            .unavailable(detail: "connection refused"),
            ImagesEngineLine.of(nil, loading: false, error: "connection refused")
        )
        XCTAssertEqual(
            .unavailable(detail: ImagesStrings.unavailableSub),
            ImagesEngineLine.of(nil, loading: false, error: nil)
        )
        // A read that failed *and* has a message is still "unavailable", not
        // "loading": `loading` is only true while there has never been a
        // value (`ApiRead.swift:57`).
        XCTAssertEqual(
            .notInstalled,
            ImagesEngineLine.of(ImagesGenState(installed: false, engineOnline: true), loading: false, error: "stale")
        )
        XCTAssertEqual(
            .offline,
            ImagesEngineLine.of(ImagesGenState(installed: true, engineOnline: false), loading: false, error: nil)
        )
        XCTAssertEqual(
            .running,
            ImagesEngineLine.of(ImagesGenState(installed: true, engineOnline: true), loading: false, error: nil)
        )

        XCTAssertNil(ImagesEngineLine.running.title)
        XCTAssertNil(ImagesEngineLine.loading.sub)
        XCTAssertEqual(ImagesStrings.notInstalledTitle, ImagesEngineLine.notInstalled.title)
        XCTAssertEqual(ImagesStrings.notInstalledSub, ImagesEngineLine.notInstalled.sub)
        XCTAssertEqual(ImagesStrings.offlineSub, ImagesEngineLine.offline.sub)
        XCTAssertEqual("boom", ImagesEngineLine.unavailable(detail: "boom").sub)
    }

    /// `PluginFrame.kt:124`, `:144`, `:170-181`: a blank name falls back to
    /// the slug, a blank publisher draws no "by", and no plugin at all means
    /// no band (`:98-101` renders the content bare).
    func testTheBandNamesThePluginOrFallsBackToItsSlug() {
        let full = CapabilityPlugin(
            slug: "imagegen",
            version: "1.4.0",
            name: "Image Generation",
            publisher: "Coders Farm",
            appCapabilities: ["imagegen"]
        )
        let band = ImagesBand.of(full)
        XCTAssertEqual("Image Generation", band?.name)
        XCTAssertEqual("by Coders Farm", band?.publisherLine)
        XCTAssertEqual("/#plugins/imagegen", band?.managePath)
        XCTAssertEqual(
            "manage Image Generation on the dashboard's Plugins page",
            band?.manageLabel
        )

        let bare = ImagesBand.of(CapabilityPlugin(slug: "imagegen", name: "  ", publisher: " "))
        XCTAssertEqual("imagegen", bare?.name)
        XCTAssertNil(bare?.publisherLine)

        XCTAssertNil(ImagesBand.of(nil))
    }

    // MARK: - the model against a server that is not a server

    /// Every route this screen asks for, answered from the three bodies
    /// above, with the writes recorded.
    private final class House: Sendable {
        let models: Shared<String>
        let jobs: Shared<String>
        let history: Shared<String>

        /// 200 unless a test wants a write to fail.
        let writeStatus = Shared<Int>(200)

        init(models: String, jobs: String, history: String) {
            self.models = Shared(models)
            self.jobs = Shared(jobs)
            self.history = Shared(history)
        }

        func install() {
            // Bound before the dispatcher rather than captured through
            // `self`, which is what `MusicModelTests.House` does and for the
            // same reason: the closure is `@Sendable` and these four are the
            // only things in it that change.
            let (models, jobs, history) = (self.models, self.jobs, self.history)
            let writeStatus = self.writeStatus
            StubURLProtocol.dispatch { request in
                guard let path = request.url?.path else { return .json("{}", status: 404) }
                switch path {
                case "/api/health":
                    // The identity gate's probe: a trusted server proves
                    // itself before any read goes out, and a house that did
                    // not answer this would refuse every read below and every
                    // assertion over it would be about nothing.
                    let challenge = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                        .queryItems?.first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                case ImagesPaths.models: return .json(models.current)
                case ImagesPaths.jobs: return .json(jobs.current)
                case ImagesPaths.history: return .json(history.current)
                default:
                    guard writeStatus.current == 200 else {
                        return .json(#"{"detail":"the engine is busy"}"#, status: writeStatus.current)
                    }
                    return .json("{}")
                }
            }
        }
    }

    private func rig(
        models: String? = nil,
        jobs: String? = nil,
        history: String? = nil
    ) -> (ImagesModel, AppContainer, InMemoryShellStore, House) {
        let house = House(
            models: models ?? modelsJSON,
            jobs: jobs ?? jobsJSON,
            history: history ?? historyJSON
        )
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        // Trusted **with its pin**, which is what a paired phone holds: an
        // address trusted with no pin makes the gate probe and refuse until
        // it is satisfied, and `setServerURL` refuses an address that is not
        // trusted at all — so both halves are needed to move house in a test.
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return (ImagesModel(in: container), container, store, house)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func requests(_ method: String) -> [String] {
        StubURLProtocol.recorded
            .filter { $0.httpMethod == method }
            .compactMap { $0.url?.path }
    }

    func testStartReadsAllThreeAndSortsTheLaneFromHistory() async {
        let (model, _, _, _) = rig()
        model.start()
        await settleUntil { model.state.data != nil && model.jobs.data != nil && model.history.data != nil }

        XCTAssertEqual(.running, model.engineLine)
        XCTAssertTrue(model.ready)

        // The lane holds the running and the failed job; the completed one
        // belongs to history and must not be in it (`:180-183`).
        XCTAssertEqual([11, 12], model.laneJobs.map(\.id))
        XCTAssertFalse(model.laneJobs.contains { $0.prompt == "a quiet harbour" })
        XCTAssertEqual([9, 8], model.historyJobs.map(\.id))

        // As a set: the three reads are started in order and answered
        // concurrently, so the order they are *recorded* in is the session's
        // business, and asserting it would be asserting about `URLSession`.
        XCTAssertEqual(
            Set([ImagesPaths.models, ImagesPaths.jobs, ImagesPaths.history]),
            Set(requests("GET").filter { $0.hasPrefix(ImagesPaths.root) })
        )
        model.stop()
    }

    /// One push, three reads — all three listen for the same single type
    /// (`:138`, `:141`, `:144`), and nothing else re-reads anything.
    func testOnePushRereadsAllThreeAndAnotherTypeRereadsNothing() async {
        let (model, _, _, _) = rig()
        model.start()
        await settleUntil { model.history.data != nil }
        let before = (model.state.fetches, model.jobs.fetches, model.history.fetches)

        model.received(StateEvent(type: "music.now_playing.changed", frame: "{}"))
        XCTAssertEqual(before.0, model.state.fetches)
        XCTAssertEqual(before.1, model.jobs.fetches)
        XCTAssertEqual(before.2, model.history.fetches)

        model.received(StateEvent(type: "imagegen.jobs.changed", frame: "{}"))
        XCTAssertEqual(before.0 + 1, model.state.fetches)
        XCTAssertEqual(before.1 + 1, model.jobs.fetches)
        XCTAssertEqual(before.2 + 1, model.history.fetches)
        model.stop()
    }

    func testGeneratePostsTheModelAndThePromptAndRereadsTheLane() async throws {
        let (model, _, _, _) = rig()
        model.start()
        await settleUntil { model.state.data != nil && model.jobs.data != nil }

        // Nothing typed: refused, and nothing leaves the phone.
        XCTAssertFalse(model.canGenerate)
        XCTAssertFalse(model.generate())
        XCTAssertTrue(requests("POST").isEmpty)

        model.prompt = "  a lighthouse in fog  "
        XCTAssertTrue(model.canGenerate)
        let jobsReadsBefore = model.jobs.fetches
        XCTAssertTrue(model.generate())
        XCTAssertTrue(model.busy, "the button is off while the POST is in flight")

        await settleUntil { !model.busy }
        XCTAssertEqual([ImagesPaths.generate], requests("POST"))
        // The recorded copy carries the bytes: a `URLProtocol` is handed the
        // body as a stream and `StubURLProtocol` reads it back into
        // `httpBody` for exactly this assertion (`StubURLProtocol.swift:109-128`).
        let sent = StubURLProtocol.recorded.first { $0.url?.path == ImagesPaths.generate }
        let bytes = try XCTUnwrap(sent?.httpBody)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
        XCTAssertEqual(["model", "prompt"], body.keys.sorted(), "exactly two keys (`:162-165`)")
        XCTAssertEqual("sdxl-turbo", body["model"] as? String)
        XCTAssertEqual("a lighthouse in fog", body["prompt"] as? String, "trimmed, as `:164` trims it")

        await settleUntil { model.jobs.fetches > jobsReadsBefore }
        // The prompt is **not** cleared (`:156-170` clears nothing), so a
        // second go at one word different is one word of typing.
        XCTAssertEqual("  a lighthouse in fog  ", model.prompt)
        XCTAssertNil(model.toast)
        model.stop()
    }

    func testAGenerateThatFailsSaysTheServersOwnWordsAndFreesTheButton() async throws {
        let (model, _, _, house) = rig()
        model.start()
        await settleUntil { model.state.data != nil }
        house.writeStatus.set(503)

        model.prompt = "a brass orrery"
        XCTAssertTrue(model.generate())
        await settleUntil { model.toast != nil }
        XCTAssertFalse(model.busy, "a failed generate must not leave the button off for good")
        // The server's own sentence, through ``DomovoiError/message`` — by
        // containment, as every other write test in this suite asserts it,
        // because the client may wrap what the house said.
        let said = try XCTUnwrap(model.takeToast())
        XCTAssertTrue(said.contains("the engine is busy"), said)
        XCTAssertNil(model.toast, "and the shell takes it once")
        model.stop()
    }

    func testCancelIsRefusedForAJobThatIsNotStillHappening() async throws {
        let (model, _, _, _) = rig()
        model.start()
        await settleUntil { model.jobs.data != nil }
        // **And the history, which is a second read.** Waiting on `jobs`
        // alone and then taking `historyJobs[0]` is a race this suite lost:
        // `start()` fires three reads, the lane's can land first, and the
        // subscript then traps on an empty array — which takes the whole
        // xctest process with it and reports as `0 tests in 0 suites`, not as
        // a failing case ([[ios-app-track]]: never index in a test). It was
        // intermittent for exactly that reason.
        await settleUntil { model.history.data != nil }

        let running = try XCTUnwrap(model.laneJobs.first { $0.id == 11 })
        let failed = try XCTUnwrap(model.laneJobs.first { $0.id == 12 })
        let completed = try XCTUnwrap(model.historyJobs.first)

        XCTAssertFalse(model.cancel(failed), "a failed job has no cancel button (`:347`)")
        XCTAssertFalse(model.cancel(completed))
        XCTAssertTrue(requests("POST").isEmpty)

        let before = model.jobs.fetches
        XCTAssertTrue(model.cancel(running))
        await settleUntil { model.jobs.fetches > before }
        XCTAssertEqual([ImagesPaths.cancel(job: 11)], requests("POST"))
        // **The bytes, not only the path.** A bodyless POST sets no
        // `httpBody` and no `Content-Type` (`DomovoiClient.swift:618-622`),
        // while Android's `api.post` substitutes `JsonObject(emptyMap())` and
        // always sends `application/json` (`ApiClient.kt:339-353`) — so a
        // Pydantic body on the plugin's cancel route would 422 every cancel,
        // and the path alone could not see it (2026-11-02 review).
        let sent = StubURLProtocol.recorded.last(where: { $0.httpMethod == "POST" })
        XCTAssertEqual(Data("{}".utf8), sent?.httpBody, "cancel sent no body")
        XCTAssertEqual(
            "application/json",
            sent?.value(forHTTPHeaderField: "Content-Type"),
            "cancel sent a body with no content type"
        )
        XCTAssertNil(model.toast)
        model.stop()
    }

    func testDeletingAHistoryRowAsksFirstThenToastsAndRereads() async throws {
        let (model, _, _, _) = rig()
        model.start()
        await settleUntil { model.history.data != nil }

        XCTAssertNil(model.deleteBody)
        // `first`, not `[0]`: a subscript on an empty array traps, and a trap
        // in a test ends the process rather than the case.
        model.confirmingDelete = try XCTUnwrap(model.historyJobs.first)
        XCTAssertEqual(
            "Delete this generation and its 4 images? This can't be undone.",
            model.deleteBody
        )
        // The ask alone sends nothing: a destructive write happens on the
        // confirmation and nowhere else.
        XCTAssertTrue(requests("DELETE").isEmpty)

        let before = model.history.fetches
        model.confirmDelete()
        XCTAssertNil(model.confirmingDelete)
        await settleUntil { model.toast != nil }
        XCTAssertEqual(ImagesStrings.deleted, model.toast)
        XCTAssertEqual([ImagesPaths.deleteHistory(job: 9)], requests("DELETE"))
        await settleUntil { model.history.fetches > before }
        model.stop()
    }

    func testADeleteThatFailsSaysSoAndKeepsTheRow() async throws {
        let (model, _, _, house) = rig()
        model.start()
        await settleUntil { model.history.data != nil }
        house.writeStatus.set(500)

        model.confirmingDelete = try XCTUnwrap(model.historyJobs.first)
        model.confirmDelete()
        await settleUntil { model.toast != nil }
        let said = try XCTUnwrap(model.toast)
        XCTAssertTrue(said.hasPrefix("delete failed: "), said)
        XCTAssertTrue(said.contains("the engine is busy"), said)
        XCTAssertEqual([9, 8], model.historyJobs.map(\.id), "the row is still there")
        model.stop()
    }

    /// **The switch happens inside the round trip, which is the only place it
    /// means anything.** `[[read-after-await-wrong-key]]`: the top defect
    /// class on this track, four instances already. House A's generate must
    /// not toast under house B's name, must not clear B's `busy`, and must
    /// not re-read B's lane as if something there had changed — a job id is
    /// the plugin's, and two houses can both have a job 7.
    func testAServerSwitchMidGenerateLandsNowhere() async {
        let house = House(models: modelsJSON, jobs: jobsJSON, history: historyJSON)
        let park = parkRequest(
            to: ImagesPaths.generate,
            answering: { .json(#"{"detail":"house A refused it"}"#, status: 500) },
            otherwise: { request in
                guard let path = request.url?.path else { return .json("{}", status: 404) }
                switch path {
                case "/api/health":
                    let challenge = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                        .queryItems?.first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                case ImagesPaths.models: return .json(house.models.current)
                case ImagesPaths.jobs: return .json(house.jobs.current)
                case ImagesPaths.history: return .json(house.history.current)
                default: return .json("{}")
                }
            }
        )
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        store.upsertKnownServer(url: server, name: nil)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = ImagesModel(in: container)
        model.start()
        await settleUntil { model.state.data != nil }

        model.prompt = "a lighthouse in fog"
        XCTAssertTrue(model.generate())
        await settleUntil { park.arrived.current }
        XCTAssertTrue(model.busy)

        // The person switches houses while the generate is in flight.
        let next = "http://10.0.0.77:6369"
        store.trustServer(next, identity: TestIdentity.real.pin)
        store.upsertKnownServer(url: next, name: nil)
        XCTAssertTrue(store.setServerURL(next))
        store.setToken("household-xyz", for: next)
        container.serverAddressChanged()
        model.serverChanged()
        XCTAssertFalse(model.busy, "the move cleared it; the old house's answer must not touch it")
        let readsUnderB = model.jobs.fetches

        park.release.set(true)
        await settleUntil { StubURLProtocol.recorded.contains { $0.url?.path == ImagesPaths.generate } }
        // Give the continuation every chance to do the wrong thing.
        for _ in 0..<200 { await Task.yield() }

        XCTAssertFalse(park.ranOut.current, "the answer landed; it did not time out")
        XCTAssertNil(model.toast, "house A's failure must not be said under house B's name")
        XCTAssertEqual(readsUnderB, model.jobs.fetches, "house A's answer must not re-read house B's lane")
        model.stop()
    }

    func testMovingHouseDropsTheModelChoiceAndKeepsTheTyping() async {
        let (model, container, store, _) = rig()
        model.start()
        await settleUntil { model.state.data != nil }
        model.prompt = "a long paragraph of a prompt"
        model.chosenModelID = "sd15"
        model.confirmingDelete = model.historyJobs.first

        let next = "http://10.0.0.77:6369"
        store.trustServer(next, identity: TestIdentity.real.pin)
        store.upsertKnownServer(url: next, name: nil)
        XCTAssertTrue(store.setServerURL(next))
        container.serverAddressChanged()
        model.serverChanged()

        XCTAssertEqual("a long paragraph of a prompt", model.prompt, "the person's own typing survives")
        XCTAssertNil(model.chosenModelID, "a model id is the other engine's")
        XCTAssertNil(model.confirmingDelete, "and so is the row a dialog was asking about")
        model.stop()
    }

    // MARK: - the five breakages of train X004, over the stub house's own rows

    /// The negative control's five edits, each asserted against **the data
    /// the UI cases actually see** — `fixtures/images.py`'s own `jobs` and
    /// `history` rows, transcribed, ids and all.
    ///
    /// Why this exists next to tests that already cover the same four rules.
    /// Train X004 broke the app on purpose and reported that none of the five
    /// owning UI cases caught it. The train's own `summary.json` says why:
    /// `Executed 3 tests`, and all seventeen `testImages_…` verdicts are
    /// `not-run` with no per-case log and an empty `att/`. **Not one Images
    /// case ran.** A case that does not run catches nothing, and nothing in
    /// the evidence shows any of them to be vacuous — four of the five
    /// breakages are caught here in the package, where this branch can run
    /// them tonight without a Simulator or a build slot for a whole app.
    ///
    /// The existing rule tests (`testTheLanePredicatesMatchAndroidsForEveryWord`,
    /// `testTheMetaLineNeedsBothWidthAndHeight`,
    /// `testAHistoryCardDrawsThreeTilesOfFourAndTheDialogCountsFour`,
    /// `testTheGenerateBodyIsTwoKeysAndTheTrimmedPrompt`) hold the rules at
    /// their boundaries with data of their own. This one holds them against
    /// the fixture, so that a seed edited to stop discriminating — the word
    /// `quicksilver` moved into a history row, row 22 given a height — fails
    /// here rather than quietly turning a UI case into a tautology.
    func testTheFixturesOwnRowsDriveTheLaneTheMetaLineAndTheTiles() throws {
        // `fixtures/images.py:151-196`.
        let fixtureJobs = """
        {"jobs":[
        {"id":7,"kind":"generate","model":"sdxl-turbo","prompt":"a brass orrery on a velvet desk",
         "params":{"width":1024,"height":1024,"steps":28,"seed":41,"negative":""},
         "status":"running","pct":42,"status_text":"sampling step 7 of 20",
         "requested_at":"2026-11-03T09:14:02.511+00:00","completed_at":null},
        {"id":8,"kind":"generate","model":"flux-schnell","prompt":"a cathedral of folded paper",
         "params":{"width":512,"height":512,"steps":4,"seed":9001,"negative":""},
         "status":"failed","error":"the engine ran out of memory",
         "requested_at":"2026-11-03T09:11:40.002+00:00",
         "completed_at":"2026-11-03T09:12:08.774+00:00"},
        {"id":9,"kind":"generate","model":"sdxl-turbo","prompt":"a quicksilver tide at dusk",
         "params":{"width":768,"height":768,"steps":20,"seed":5,"negative":""},
         "status":"done","pct":100,"result":{"images":["tide-01.png"]}}]}
        """
        let jobs = try decode(ImagesJobList.self, fixtureJobs).jobs
        XCTAssertEqual(3, jobs.count, "the fixture's three rows, or this test is about something else")

        // **Breakage 1** — `inLane` returned true unconditionally, so job 9
        // was drawn in the live lane.
        let lane = jobs.filter(\.status.inLane)
        XCTAssertEqual([7, 8], lane.map(\.id), "the lane is the two that are still happening")
        XCTAssertFalse(
            lane.contains { ($0.prompt ?? "").contains("quicksilver") },
            "a finished job reached the lane"
        )
        // And the word is still the discriminator the UI case leans on: it
        // must be in exactly one row of the fixture, the one that must not be
        // in the lane.
        XCTAssertEqual(
            1,
            jobs.filter { ($0.prompt ?? "").contains("quicksilver") }.count,
            "`quicksilver` is no longer the one word that tells the filter apart"
        )

        // `fixtures/images.py:199-247`.
        let fixtureHistory = """
        {"history":[
        {"id":21,"kind":"generate","model":"sdxl-turbo",
         "prompt":"a kingfisher on a frozen reed",
         "params":{"width":1024,"height":1024,"steps":28,"seed":773451,"negative":""},
         "status":"done","pct":100,
         "result":{"images":["kingfisher 01.png","kingfisher-02.png","kingfisher-03.png","kingfisher-04.png"]}},
        {"id":22,"kind":"generate","model":"flux-schnell",
         "prompt":"a tin robot watering a window box",
         "params":{"width":512,"height":null,"steps":4,"seed":null,"negative":""},
         "status":"done","pct":100,"result":{"images":["robot-01.png"]}}],
        "more":false}
        """
        let history = try decode(ImagesHistoryList.self, fixtureHistory).history
        XCTAssertEqual([21, 22], history.map(\.id))

        // **Answer 27, against the fixture.** Row 21 is the four-image row
        // and row 22 the one-image row, so the seed itself has to keep
        // driving both halves of the sentence — a seed trimmed to three
        // images would make the UI case that asserts `4 images, showing 3`
        // vacuous without failing anywhere else.
        XCTAssertTrue(history[0].hasMoreImagesThanTiles)
        XCTAssertEqual(
            "4 images, showing 3",
            ImagesStrings.moreImages(
                total: history[0].imageNames.count, shown: history[0].tileNames.count
            )
        )
        XCTAssertFalse(history[1].hasMoreImagesThanTiles)

        // **Breakage 2** — a size drawn from a width alone, so row 22 read
        // `flux-schnell · 512 × 0 · 4 steps`. The exact sabotaged string is
        // named here because that is the shape the owning UI case now denies
        // by name too.
        XCTAssertEqual("flux-schnell · 4 steps", history[1].metaLine)
        XCTAssertNotEqual("flux-schnell · 512 × 0 · 4 steps", history[1].metaLine)
        XCTAssertFalse(history[1].metaLine.contains("×"), "half a size is not a size")
        XCTAssertEqual("sdxl-turbo · 1024 × 1024 · 28 steps · seed 773451", history[0].metaLine)

        // **Breakage 3** — `prefix(4)`, so a fourth tile Android does not
        // draw. Four images on the row, three names on the card.
        XCTAssertEqual(4, history[0].imageNames.count)
        XCTAssertEqual(
            ["kingfisher 01.png", "kingfisher-02.png", "kingfisher-03.png"],
            history[0].tileNames
        )
        XCTAssertEqual(3, history[0].tileNames.count)
        XCTAssertFalse(history[0].tileNames.contains("kingfisher-04.png"))

        // **Breakage 4** — `CodingKeys` renamed `prompt` to `text`, so the
        // POST carried the prompt under a key the plugin does not read and
        // the queued job came back with none. The key's *name* is the whole
        // assertion: a body with two keys and the right values under the
        // wrong names passes a count.
        let body = ImagesPaths.generateBody(model: "sdxl-turbo", prompt: "a paper submarine in a bathtub")
        let fields = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual("a paper submarine in a bathtub", fields["prompt"] as? String)
        XCTAssertNil(fields["text"], "the prompt went out under `text`, which the plugin does not read")
        XCTAssertEqual(["model", "prompt"], fields.keys.sorted())
    }

    /// **Breakage 5** — the view's `if` widened to `count >= 0`, so a house
    /// that has generated nothing grew a `history` heading over nothing.
    ///
    /// The guard now lives in ``ImagesModel/showsHistory`` and the view reads
    /// it, which is the point: in a view it was the one rule on this screen
    /// that only a Simulator could check, and on the night it mattered the
    /// Simulator ran none of the cases.
    func testTheHistorySectionIsDrawnOnlyWhenThereIsHistory() async {
        let (empty, _, _, _) = rig(jobs: #"{"jobs":[]}"#, history: #"{"history":[]}"#)
        empty.start()
        await settleUntil { empty.history.data != nil }
        XCTAssertTrue(empty.historyJobs.isEmpty)
        XCTAssertFalse(empty.showsHistory, "a `history` heading over no history")
        empty.stop()

        let seeded = rig().0
        seeded.start()
        await settleUntil { seeded.history.data != nil }
        XCTAssertEqual(2, seeded.historyJobs.count)
        XCTAssertTrue(seeded.showsHistory, "history rows with no section to put them in")
        seeded.stop()
    }
}
