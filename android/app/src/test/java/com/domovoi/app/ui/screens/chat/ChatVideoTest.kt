package com.domovoi.app.ui.screens.chat

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * A video in a home-server chat (ChatVideo.kt; the server's
 * web/backend/api/chat_videos.py and domovoi/chat_video/limits.py, the
 * dashboard's web/static/chat_videos.jsx): when the paperclip offers one,
 * the checks before anything is sent and their sentences, the upload
 * request, every refusal in words, the send's body, following a job, stop,
 * and what was kept.
 */
class ChatVideoTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient(baseUrlProvider = { server.url("/").toString().trimEnd('/') })
    }

    @After fun down() = server.shutdown()

    private fun models(json: String) = DomovoiJson.decodeFromString(ChatModelsInfo.serializer(), json)

    private val id = "a".repeat(32)

    private fun job(state: String, line: String = "", progress: VideoJobProgress = VideoJobProgress(), jobId: String = id) =
        ServerVideoJob(id = jobId, thread_id = 12, message_id = 345, name = "party.mp4", duration_s = 151.3, state = state,
            line = line, progress = progress)

    // --- The gate -----------------------------------------------------------

    @Test fun theVideoIsOfferedOnlyWhenTheServerCanWatchOne() {
        val on = models("""{"installed":["llama3.2:3b"],"video":{"available":true,"reason":null,"max_seconds":180,"max_mb":2048,""" +
            """"accept":"video/mp4,video/quicktime,video/webm,.mp4,.m4v,.mov,.webm","extensions":[".mp4",".m4v",".mov",".webm"]}}""")
        assertTrue(videoOffered(on, ChatPlaceKind.Server, aiOn = true))
        assertEquals(180.0, on.video!!.max_seconds, 0.0)
        assertEquals(2048L, on.video!!.max_mb)

        val off = models("""{"video":{"available":false,"reason":"no-ffmpeg"}}""")
        assertFalse("no ffmpeg on the server", videoOffered(off, ChatPlaceKind.Server, aiOn = true))
        val aiOffByServer = models("""{"video":{"available":false,"reason":"ai-off"}}""")
        assertFalse("the server says AI is off", videoOffered(aiOffByServer, ChatPlaceKind.Server, aiOn = true))

        val older = models("""{"installed":["llama3.2:3b"],"vision_installed":true}""")
        assertNull(older.video)
        assertFalse("an older server has no video key: no", videoOffered(older, ChatPlaceKind.Server, aiOn = true))
        assertFalse("the models not read yet", videoOffered(null, ChatPlaceKind.Server, aiOn = true))
    }

    @Test fun aiOffOrThePhonesOwnChatNeverOfferAVideo() {
        val on = models("""{"video":{"available":true}}""")
        assertFalse("the household's AI features off: hidden", videoOffered(on, ChatPlaceKind.Server, aiOn = false))
        assertFalse("Domovoi on this phone: the server's feature only", videoOffered(on, ChatPlaceKind.Phone, aiOn = true))
    }

    @Test fun thePaperclipItemSaysTheLimit() {
        assertEquals("a video (up to 3 minutes)", VIDEO_MENU_ITEM)
        assertEquals("one video a message", VIDEO_ONE_A_MESSAGE)
    }

    // --- Length, size and format before anything is sent --------------------

    @Test fun aClipOverThreeMinutesIsRefusedInTheServersWords() {
        assertEquals(
            "Videos up to 3 minutes can be attached. This one is 3:42 long.",
            videoRefusalBeforeUpload("long.mp4", 222.0, 10_000_000),
        )
        assertEquals(
            "Videos up to 3 minutes can be attached. This one is 1:02:05 long.",
            videoRefusalBeforeUpload("film.mp4", 3725.0, 10_000_000),
        )
    }

    @Test fun theLengthHasHalfASecondOfSlack() {
        assertNull(videoRefusalBeforeUpload("a.mp4", 180.0, 1))
        assertNull("a 3:00 clip that probes a little over", videoRefusalBeforeUpload("a.mp4", 180.5, 1))
        assertEquals(
            "Videos up to 3 minutes can be attached. This one is 3:01 long.",
            videoRefusalBeforeUpload("a.mp4", 180.51, 1),
        )
        assertNull("the phone couldn't tell: the server decides", videoRefusalBeforeUpload("a.mp4", null, 1))
    }

    @Test fun aFileOverTwoGibIsRefusedInTheServersWords() {
        val max = 2048L * 1024 * 1024
        assertNull(videoRefusalBeforeUpload("big.mov", 170.0, max))
        assertEquals(
            "Videos up to 2 GB can be attached. Send a shorter clip, or record it at a lower quality.",
            videoRefusalBeforeUpload("big.mov", 170.0, max + 1),
        )
        assertNull("size unknown: the server decides", videoRefusalBeforeUpload("big.mov", 170.0, -1))
        // The server's own limits win over the defaults.
        assertEquals(VIDEO_TOO_LARGE, videoRefusalBeforeUpload("a.mp4", 10.0, 2L * 1024 * 1024,
            ServerVideoInfo(available = true, max_mb = 1)))
    }

    @Test fun aFormatChatDoesntTakeIsRefusedBeforeSending() {
        assertEquals(VIDEO_UNSUPPORTED, videoRefusalBeforeUpload(null, 10.0, 1))
        assertEquals(
            "Chat takes MP4, MOV and WebM videos (H.264 or HEVC, the way phones record them). " +
                "Share the video from your phone as MP4 and try again.",
            VIDEO_UNSUPPORTED,
        )
    }

    @Test fun theNameSentEndsTheWayChatTakes() {
        assertEquals("party.mp4", videoUploadName("party.mp4", "video/mp4"))
        assertEquals("Clip.MOV", videoUploadName("Clip.MOV", "video/quicktime"))
        assertEquals("a gallery name without an ending gets its type's", "VID_2026.mp4", videoUploadName("VID_2026", "video/mp4"))
        assertEquals("rec.webm", videoUploadName("rec", "video/webm"))
        assertEquals("video.mov", videoUploadName(null, "video/quicktime"))
        assertNull("not a format chat takes", videoUploadName("clip.mkv", "video/x-matroska"))
        assertNull(videoUploadName("clip.3gp", "video/3gpp"))
        assertNull(videoUploadName("clip", null))
    }

    @Test fun clocksAndSizesReadAsTheDashboardsDo() {
        assertEquals("2:31", videoClock(151.3))
        assertEquals("0:00", videoClock(null))
        assertEquals("3:00", videoClock(180.02))
        assertEquals("393 MB", videoSizeText(412_345_678))
        assertEquals("1 MB", videoSizeText(10))
        assertEquals("1.9 GB", videoSizeText(2_000_000_000))
    }

    // --- The upload ---------------------------------------------------------

    @Test fun theVideoStreamsAsTheRawBodyWithItsTypeAndLength() = runBlocking {
        server.enqueue(MockResponse().setBody(
            """{"token":"${"b".repeat(32)}","name":"party one.mp4","duration_s":151.3,"size":300000,"width":1920,""" +
                """"height":1080,"has_audio":true,"estimate_s":372.4,"estimate_text":"about 6 minutes"}""",
        ))
        val bytes = ByteArray(300_000) { (it % 251).toByte() }
        var opened = 0
        val seen = mutableListOf<Int>()
        val staged = uploadChatVideo(
            api, "party one.mp4", "video/mp4", bytes.size.toLong(),
            open = { opened++; ByteArrayInputStream(bytes) },
            onPercent = { seen += it },
        )
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/chat/videos?name=party%20one.mp4", req.path)
        assertEquals("party one.mp4", req.requestUrl!!.queryParameter("name"))
        assertEquals("video/mp4", req.getHeader("Content-Type"))
        assertEquals("300000", req.getHeader("Content-Length"))
        assertEquals("DomovoiApp", req.getHeader("X-Requested-With"))
        assertTrue("the raw file, not a multipart form", req.body.readByteArray().contentEquals(bytes))
        assertEquals("read from the file once", 1, opened)
        assertEquals(100, seen.last())
        assertEquals(seen.sorted().distinct(), seen)
        assertEquals("b".repeat(32), staged.token)
        assertEquals(151.3, staged.duration_s, 1e-9)
        assertEquals("about 6 minutes", staged.estimate_text)
        assertEquals("2:31 · 1 MB · watching takes about 6 minutes", stagedVideoLine(staged, 100))
        assertEquals("sending… 42%", stagedVideoLine(null, 42))
    }

    @Test fun aNameWithOddCharactersIsEncodedInTheQuery() {
        val path = videoUploadPath("Mum & Dad's 50th #1.mp4")
        assertEquals("/api/chat/videos?name=Mum%20%26%20Dad%27s%2050th%20%231.mp4", path)
        val url = okhttp3.HttpUrl.Builder().scheme("http").host("h").build().resolve(path)!!
        assertEquals("Mum & Dad's 50th #1.mp4", url.queryParameter("name"))
    }

    @Test fun cancellingTheUploadStopsTheRequest() = runBlocking {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        val bytes = ByteArray(1_000)
        val upload = async(kotlinx.coroutines.Dispatchers.IO) {
            uploadChatVideo(api, "a.mp4", "video/mp4", bytes.size.toLong(), { ByteArrayInputStream(bytes) }, {})
        }
        assertTrue(server.takeRequest(5, TimeUnit.SECONDS) != null)
        delay(100)
        upload.cancel()
        try {
            withTimeout(5_000) { upload.await() }
            fail("a cancelled upload doesn't finish")
        } catch (e: TimeoutCancellationException) {
            fail("the cancel didn't stop the request")
        } catch (e: kotlinx.coroutines.CancellationException) {
            assertTrue(upload.isCancelled)
        }
    }

    // --- Refusals in words ----------------------------------------------------

    private fun refusal(status: Int, code: String, detail: String, extra: String = "") =
        ApiException(status, "$status", body = """{"detail":"$detail","code":"$code"$extra}""")

    @Test fun everyRefusalShowsTheServersSentence() {
        val cases = listOf(
            refusal(413, "video_too_large", "Videos up to 2 GB can be attached. Send a shorter clip, or record it at a lower quality.", ""","max_mb":2048"""),
            refusal(415, "video_unsupported", VIDEO_UNSUPPORTED),
            refusal(422, "video_too_long", "Videos up to 3 minutes can be attached. This one is 3:42 long.", ""","duration_s":222.0,"max_seconds":180"""),
            refusal(422, "video_unreadable", "Domovoi couldn't read that video. It may be damaged, or still being saved on the phone."),
            refusal(503, "video_no_tools", "This home server can't read videos: it needs ffmpeg, which isn't installed."),
            refusal(507, "video_no_room", "The home server is short of disk space for that video. Free some space and try again."),
        )
        cases.forEach { e ->
            val detail = DomovoiJson.parseToJsonElement(e.body).jsonObject["detail"]!!.jsonPrimitive.content
            assertEquals("status ${e.status}", detail, videoFailureText(e))
        }
    }

    @Test fun theTransportGuardsPlainTextTooLargeReadsAsTheServersSentence() {
        assertEquals(VIDEO_TOO_LARGE, videoFailureText(ApiException(413, "413 Request Entity Too Large", body = "Request body too large")))
    }

    @Test fun aiOffIsSaidInPlainWords() {
        assertEquals(AI_CHAT_OFF_TEXT, videoFailureText(ApiException(409, "409", body = "{\"detail\":\"x\"}", refusal = "ai-off")))
        assertEquals(
            "a proxy that dropped the header",
            AI_CHAT_OFF_TEXT,
            videoFailureText(ApiException(409, "409", body = "{\"detail\":\"AI features are turned off for this home (Settings → Models)\"}")),
        )
    }

    @Test fun anythingElseSaysAttachFailed() {
        assertEquals("attach failed: 500 Internal Server Error", videoFailureText(ApiException(500, "500 Internal Server Error", body = "boom")))
        assertEquals("attach failed (offline?)", videoFailureText(IOException("reset")))
        assertNull(videoRefusalText(404, """{"detail":"Not Found"}"""))
    }

    @Test fun aRefusalFromTheServerArrivesWithItsHeaderAndBody() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(409).setHeader("X-Domovoi-Refusal", "ai-off")
            .setBody("""{"detail":"AI features are turned off for this home (Settings → Models)"}"""))
        server.enqueue(MockResponse().setResponseCode(422).setBody(
            """{"detail":"Videos up to 3 minutes can be attached. This one is 3:42 long.","code":"video_too_long","duration_s":222.0,"max_seconds":180}""",
        ))
        val bytes = ByteArray(10)
        val first = runCatching { uploadChatVideo(api, "a.mp4", "video/mp4", 10, { ByteArrayInputStream(bytes) }, {}) }
        assertEquals(AI_CHAT_OFF_TEXT, videoFailureText(first.exceptionOrNull()!!))
        val second = runCatching { uploadChatVideo(api, "a.mp4", "video/mp4", 10, { ByteArrayInputStream(bytes) }, {}) }
        assertEquals("Videos up to 3 minutes can be attached. This one is 3:42 long.", videoFailureText(second.exceptionOrNull()!!))
    }

    // --- The send -------------------------------------------------------------

    private val headsUp = buildJsonObject { put("off", false) }

    @Test fun theSendCarriesTheVideosToken() {
        val body = messageSendBody("what song is this?", "dev-1", emptyList(), emptyList(), listOf("c".repeat(32)), null, headsUp)
        val videos = body["videos"]!!.jsonArray
        assertEquals(1, videos.size)
        assertEquals("c".repeat(32), videos[0].jsonObject["token"]!!.jsonPrimitive.content)
        assertEquals(setOf("token"), videos[0].jsonObject.keys)
        assertEquals("words sent with it are its question", "what song is this?", body["content"]!!.jsonPrimitive.content)
        assertEquals("dev-1", body["device_id"]!!.jsonPrimitive.content)
    }

    @Test fun aVideoGoesAloneWithEmptyWordsAndAtMostOne() {
        val body = messageSendBody("", "dev-1", emptyList(), emptyList(), listOf("c".repeat(32), "d".repeat(32)), null, headsUp)
        assertEquals("", body["content"]!!.jsonPrimitive.content)
        assertEquals(1, (body["videos"] as JsonArray).size)
    }

    @Test fun noVideoNoKey() {
        val body = messageSendBody("hi", "dev-1", listOf(ImageRef("t", "p.jpg")), emptyList(), emptyList(), 7L, headsUp)
        assertFalse(body.containsKey("videos"))
        assertFalse(body.containsKey("documents"))
        assertEquals(7L, body["revise_draft"]!!.jsonPrimitive.content.toLong())
        assertEquals(headsUp, body["heads_up"] as JsonObject)
        assertEquals("t", body["images"]!!.jsonArray[0].jsonObject["token"]!!.jsonPrimitive.content)
    }

    // --- Following a job --------------------------------------------------------

    @Test fun aThreadsVideosReadWithTheMessageTheyWereSentWith() {
        val list = DomovoiJson.decodeFromString(MessageList.serializer(), """{"messages":[{"id":345,"role":"user","content":""}],"jobs":[],
            "videos":[{"id":"$id","thread_id":12,"message_id":345,"name":"party.mp4","question":"","duration_s":151.3,"size":412345678,
            "state":"running","stage":"frames","line":"Watching your video… about 4 minutes left","waiting_for_voice":false,
            "estimate_s":372.4,"eta_s":241.0,"progress":{"pieces_done":18,"pieces":18,"frames_done":3,"frames":9},
            "summary":null,"notes":[],"error":null,"error_code":null,"cancel_requested":false,"created_at":"x","finished_at":null}]}""")
        val v = list.videos.single()
        assertEquals(345L, v.message_id)
        assertTrue(v.live)
        assertEquals(9, v.progress.frames)
        val none = DomovoiJson.decodeFromString(MessageList.serializer(), """{"messages":[],"jobs":[]}""")
        assertTrue("an older server: no videos", none.videos.isEmpty())
    }

    @Test fun pollingRunsWhileAJobIsQueuedOrRunningAndStopsAfter() {
        assertEquals(id, liveVideoKey(listOf(job("queued"))))
        assertEquals("$id,${"e".repeat(32)}", liveVideoKey(listOf(job("running"), job("running", jobId = "e".repeat(32)))))
        listOf("done", "failed", "cancelled").forEach { assertEquals(it, "", liveVideoKey(listOf(job(it)))) }
        assertEquals("", liveVideoKey(emptyList()))
        assertEquals(3_000L, VIDEO_POLL_MS)
    }

    @Test fun aRoundReadsOnlyLiveJobsAndSaysWhenOneEnded() = runBlocking {
        val asked = mutableListOf<String>()
        val other = "f".repeat(32)
        val stillRunning = pollVideos(listOf(job("running"), job("done", jobId = other))) { asked += it; job("running", "Watching your video… about 3 minutes left") }
        assertEquals(listOf(id), asked)
        assertFalse(stillRunning.ended)
        assertEquals("Watching your video… about 3 minutes left", stillRunning.views.single().line)

        val ended = pollVideos(listOf(job("running"))) { job("done", "Watched") }
        assertTrue("one left queued/running: the thread again", ended.ended)

        val dropped = pollVideos(listOf(job("running"))) { throw IOException("offline") }
        assertFalse("a failed read never stops the following", dropped.ended)
        assertTrue(dropped.views.isEmpty())

        val merged = mergeVideoViews(listOf(job("running"), job("done", jobId = other)), listOf(job("cancelled", "Stopped")))
        assertEquals(listOf("cancelled", "done"), merged.map { it.state })
    }

    @Test fun theJobIsReadFromItsRoute() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"id":"$id","state":"queued","line":"Waiting for 1 other video first…"}"""))
        val v = fetchVideoJob(api, id)
        assertEquals("/api/chat/video-jobs/$id", server.takeRequest().path)
        assertEquals("Waiting for 1 other video first…", v.line)
    }

    @Test fun stopPostsTheCancelAndAnswersTheView() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"id":"$id","state":"running","line":"Stopping…","cancel_requested":true}"""))
        val v = cancelVideoJob(api, id)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/chat/video-jobs/$id/cancel", req.path)
        assertEquals("DomovoiApp", req.getHeader("X-Requested-With"))
        assertTrue(v.cancel_requested)
        val chip = videoChipView(v)
        assertFalse("no second stop while it stops", chip.canStop)
        assertEquals("Stopping…", chip.line)
    }

    @Test fun theChipShowsTheLineTheBarAndWhatCanBeDone() {
        val running = videoChipView(job("running", "Watching your video… about 4 minutes left",
            VideoJobProgress(pieces_done = 18, pieces = 18, frames_done = 3, frames = 9)))
        assertEquals("party.mp4", running.name)
        assertEquals("2:31", running.length)
        assertEquals("Watching your video… about 4 minutes left", running.line)
        assertEquals(21f / 28f, running.progress!!, 1e-6f)
        assertTrue(running.canStop)
        assertFalse(running.canOpenKept)

        val nearlyDone = videoChipView(job("running", "x", VideoJobProgress(pieces_done = 30, pieces = 30, frames_done = 12, frames = 12)))
        assertEquals("never full while it still runs", 0.95f, nearlyDone.progress!!, 1e-6f)

        val queued = videoChipView(job("queued", "Waiting to start…"))
        assertNull("no bar while it waits", queued.progress)
        assertTrue(queued.canStop)

        val done = videoChipView(job("done", "Watched"))
        assertNull(done.progress)
        assertFalse(done.canStop)
        assertTrue(done.canOpenKept)

        val failed = videoChipView(job("failed", "Couldn't watch it").copy(error = "The picture model isn't installed."))
        assertEquals("The picture model isn't installed.", failed.error)
        assertFalse(failed.canStop || failed.canOpenKept)

        assertEquals("a missing line still says how it stands", "Stopped", videoChipView(job("cancelled")).line)
        assertEquals("stop", VIDEO_STOP)
        assertEquals("what was kept", VIDEO_KEPT)
    }

    // --- What was kept ----------------------------------------------------------

    @Test fun theResultsReadAsTheDashboardShowsThem() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"job":{"id":"$id","state":"done"},"summary":"Someone sings happy birthday.",
            "transcript":[{"start_s":0.85,"end_s":9.1,"text":"happy birthday to you"}],
            "frames":[{"at_s":61.0,"might_show":"a cake with candles on a table"}]}"""))
        val r = fetchVideoResults(api, id)
        assertEquals("/api/chat/video-jobs/$id/results", server.takeRequest().path)
        val kept = videoKeptView(r)
        assertEquals(listOf(KeptLine("0:01", "happy birthday to you")), kept.said)
        assertFalse(kept.noSpeech)
        assertEquals("frames only as what they might show", listOf(KeptLine("1:01", "might show: a cake with candles on a table")), kept.frames)
        assertEquals("these are a small model's guesses, and can be wrong", kept.framesNote)
        assertEquals("the video itself was deleted after it was watched", kept.deleted)
        assertEquals("what was said", KEPT_SAID)
        assertEquals("what the pictures might show", KEPT_FRAMES)
    }

    @Test fun aSilentVideoWithNoFramesSaysSo() {
        val kept = videoKeptView(VideoResults(summary = "x"))
        assertTrue(kept.noSpeech)
        assertEquals("no speech was heard", KEPT_NO_SPEECH)
        assertTrue(kept.frames.isEmpty())
        assertNull("no guesses, no note about them", kept.framesNote)
        assertEquals(KEPT_DELETED, kept.deleted)
    }
}
