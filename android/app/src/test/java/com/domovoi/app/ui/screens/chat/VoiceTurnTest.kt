package com.domovoi.app.ui.screens.chat

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayKind
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId

/**
 * The chat mic's wire format and what the app makes of it: the core's
 * answer (domovoi/phone_voice.py), its device actions, the speaker marker,
 * and the hold-to-talk view's own decisions.
 */
class VoiceTurnTest {

    // The core's answer to a turn, as test_phone_voice.py pins it.
    private val answer = """
        {"heard": true, "reason": null, "transcript": "play glass harbor",
         "reply": "Playing Glass Harbor.", "audio": {"mime": "audio/wav", "b64": "UklGRg=="},
         "speaker": {"person_id": 3, "name": "Ana", "confidence": 0.86, "confident": true},
         "expect_followup": false,
         "actions": [
           {"type": "play", "items": [
              {"kind": "track", "id": 12, "title": "Glass Harbor", "artist": "Velvet Kites", "album": "Tide", "duration_sec": 201.5},
              {"kind": "episode", "id": 7, "title": "Ep 7", "artist": "Show"},
              {"kind": "hologram", "id": 1, "title": "?"},
              {"kind": "track", "title": "no id"}]},
           {"type": "pause"},
           {"type": "volume", "change": "set", "percent": 140},
           {"type": "volume", "change": "set"},
           {"type": "teleport"}],
         "matched_handler": "music", "matched_path": "fast",
         "session_id": "5f0c", "room_id": "phone-android-1a2b",
         "messages": [
           {"id": 41, "role": "user", "content": "play glass harbor", "source": "voice",
            "device_id": "android-1a2b", "speaker_person_id": 3, "speaker_name": "Ana",
            "speaker_confidence": 0.86, "speaker_confident": true, "created_at": "2026-10-12T13:00:00+00:00"},
           {"id": 42, "role": "assistant", "content": "Playing Glass Harbor.", "source": "voice",
            "stats": {"model_role": "voice", "matched_handler": "music", "matched_path": "fast",
                      "stt_ms": 640, "route_ms": 90, "tts_ms": 310, "wall_ms": 1200, "brand_new_key": 1},
            "created_at": "2026-10-12T13:00:01+00:00"}],
         "error": null, "a_field_from_later": [1, 2]}
    """.trimIndent()

    private fun decode(json: String) = DomovoiJson.decodeFromString(VoiceTurnReply.serializer(), json)

    @Test fun theCoresAnswerDecodesAndToleratesWhatItDoesNotKnow() {
        val turn = decode(answer)
        assertTrue(turn.heard)
        assertEquals("play glass harbor", turn.transcript)
        assertEquals("Ana", turn.speaker?.name)
        assertEquals(2, turn.messages.size)
        val user = turn.messages[0]
        assertEquals("voice", user.source)
        assertEquals(3L, user.speaker_person_id)
        assertTrue(user.speaker_confident)
        assertEquals("music", turn.messages[1].stats?.matched_handler)
        assertEquals(640.0, turn.messages[1].stats?.stt_ms)
        assertEquals("RIFF", String(turn.wavBytes()!!))
    }

    @Test fun aTurnWithNothingHeardDecodesWithNothingToShow() {
        val turn = decode("""{"heard": false, "reason": "blank", "messages": [], "audio": null}""")
        assertFalse(turn.heard)
        assertNull(turn.wavBytes())
        assertTrue(parseDeviceActions(turn.actions).isEmpty())
    }

    @Test fun deviceActionsBecomeTheAppsOwnPlayerCalls() {
        val actions = parseDeviceActions(decode(answer).actions)
        assertEquals(3, actions.size)
        val play = actions[0] as DeviceAction.Play
        // The same items the Music and Podcasts screens make; unknown kinds
        // and items without an id are left out.
        assertEquals(
            listOf(
                PlayItem.fromTrack(12, "Glass Harbor", "Velvet Kites", "Tide", 201.5),
                PlayItem.fromEpisode(7, "Ep 7", "Show", null, null, emptyList()),
            ),
            play.items,
        )
        assertEquals("/api/music/library/12/audio", play.items[0].src)
        assertEquals(PlayKind.Podcast, play.items[1].kind)
        assertTrue(play.afterReply)
        assertEquals(DeviceAction.Pause, actions[1])
        assertFalse(actions[1].afterReply)
        // A percentage is clamped; a "set" without one is not an action.
        assertEquals(DeviceAction.Volume("set", 100), actions[2])
    }

    @Test fun aBookIsPlayedFromWhereItsListenerLeftOff() {
        val raw = DomovoiJson.parseToJsonElement(
            """[{"type": "play", "resume": true, "items": [{"kind": "book", "id": 5, "title": "Cellar Tales", "duration_sec": 3600}]}]""",
        )
        val (play) = parseDeviceActions((raw as kotlinx.serialization.json.JsonArray).map { it.jsonObject })
        play as DeviceAction.Play
        assertTrue(play.resume)
        assertEquals(PlayItem.fromBook(5, "Cellar Tales", null, 3600.0, null, emptyList()), play.items.single())
        assertTrue(play.items.single().isSpoken)
        // A song never resumes.
        assertFalse((parseDeviceActions(decode(answer).actions)[0] as DeviceAction.Play).resume)
    }

    @Test fun aPlayWithNothingPlayableIsNoAction() {
        val raw = DomovoiJson.parseToJsonElement("""[{"type": "play", "items": [{"kind": "x", "id": 1, "title": "t"}]}]""")
        val list = (raw as kotlinx.serialization.json.JsonArray).map { it.jsonObject }
        assertTrue(parseDeviceActions(list).isEmpty())
    }

    @Test fun aRefusalReadsAsTheServersOwnSentence() {
        val busy = voiceRefusal(409, """{"detail": "this phone's last turn is still being answered", "code": "busy"}""")
        assertEquals("this phone's last turn is still being answered", busy.message)
        assertEquals("busy", busy.code)
        // No body worth reading: a plain fallback, never a stack of HTML.
        assertEquals("Domovoi is still answering the last one.", voiceRefusal(409, "<html>").message)
        assertEquals(502, voiceRefusal(502, "").status)
        assertTrue(voiceRefusal(502, "").message!!.contains("502"))
    }

    @Test fun thePlayerStateSaysWhatThePhoneIsPlaying() {
        assertNull(playerState(null, false))
        val st: JsonObject = playerState(PlayItem.fromStation(4, "Jazz FM"), true)!!
        assertEquals("\"Jazz FM\"", st["title"].toString())
        assertEquals("\"radio\"", st["kind"].toString())
        assertEquals("true", st["playing"].toString())
    }

    // ── The speaker marker ──────────────────────────────────────────────

    @Test fun onlyASpokenMessageCarriesAMarker() {
        assertNull(speakerMark("typed", "user", 3, "Ana", true, null))
        assertNull(speakerMark(null, "user", 3, "Ana", true, null))
        assertNull(speakerMark("voice", "assistant", null, null, false, null))
    }

    @Test fun theMarkerIsSofterWhenUnsureOrNotTheOwner() {
        val sure = speakerMark("voice", "user", 3, "Ana", confident = true, ownerId = "3")!!
        assertEquals("Ana", sure.label)
        assertFalse(sure.soft)

        val noOwnerSet = speakerMark("voice", "user", 3, "Ana", confident = true, ownerId = null)!!
        assertFalse(noOwnerSet.soft)

        val unsure = speakerMark("voice", "user", 3, "Ana", confident = false, ownerId = "3")!!
        assertEquals("maybe Ana", unsure.label)
        assertTrue(unsure.soft)

        val someoneElse = speakerMark("voice", "user", 5, "Ben", confident = true, ownerId = "3")!!
        assertEquals("Ben", someoneElse.label)
        assertTrue(someoneElse.soft)
        assertTrue(someoneElse.description.contains("not this phone's owner"))

        val nobody = speakerMark("voice", "user", null, null, confident = false, ownerId = "3")!!
        assertEquals("voice not recognized", nobody.label)
        assertNull(nobody.name)
        assertTrue(nobody.soft)

        // A person forgotten since: the marker stays, without a name to show.
        assertEquals("someone it knows", speakerMark("voice", "user", 9, null, true, null)!!.label)
    }

    // ── The hold-to-talk view's decisions ───────────────────────────────

    @Test fun aCancelledTouchIsNeverASend() {
        // Android cancels a touch (the shade, a call, a dialog, screen off)
        // with an "up" that is already consumed: that throws the take away.
        assertEquals(HoldStep.CANCEL, holdStep(present = true, pressed = false, consumed = true))
        // The pointer gone from the event altogether: the same.
        assertEquals(HoldStep.CANCEL, holdStep(present = false, pressed = false, consumed = false))
        // Someone else claimed the gesture while it was held.
        assertEquals(HoldStep.CANCEL, holdStep(present = true, pressed = true, consumed = true))
        // Only a finger lifted off the button, unclaimed, sends.
        assertEquals(HoldStep.RELEASE, holdStep(present = true, pressed = false, consumed = false))
        assertEquals(HoldStep.HOLD, holdStep(present = true, pressed = true, consumed = false))
    }

    @Test fun aFingerHasSlidOffOnlyPastTheMargin() {
        val m = 40f
        assertFalse(slidOff(10f, 10f, 400, 400, m))
        assertFalse(slidOff(-39f, 200f, 400, 400, m))
        assertTrue(slidOff(-41f, 200f, 400, 400, m))
        assertTrue(slidOff(200f, 441f, 400, 400, m))
        assertTrue(slidOff(200f, -60f, 400, 400, m))
    }

    @Test fun theDockOpensWhenPushedPastItsEndAndClosesWhenPulledPastTheTop() {
        assertFalse(dockExpanded(expanded = false, pulledSoFar = -30f, thresholdPx = 100f))
        assertTrue(dockExpanded(expanded = false, pulledSoFar = -100f, thresholdPx = 100f))
        // Pulling down a closed dock does nothing.
        assertFalse(dockExpanded(expanded = false, pulledSoFar = 500f, thresholdPx = 100f))
        assertTrue(dockExpanded(expanded = true, pulledSoFar = 99f, thresholdPx = 100f))
        assertFalse(dockExpanded(expanded = true, pulledSoFar = 120f, thresholdPx = 100f))
        // Pushing on past the end of the open thread keeps it open.
        assertTrue(dockExpanded(expanded = true, pulledSoFar = -500f, thresholdPx = 100f))
    }

    @Test fun theDockShowsOnlyTheLatestExchange() {
        val isUser: (String) -> Boolean = { it.startsWith("u") }
        assertEquals(emptyList<String>(), latestExchange(emptyList(), isUser))
        assertEquals(listOf("u2", "a2"), latestExchange(listOf("u1", "a1", "u2", "a2"), isUser))
        assertEquals(listOf("u3"), latestExchange(listOf("u1", "a1", "u3"), isUser))
        assertEquals(listOf("a2"), latestExchange(listOf("a1", "a2"), isUser))
    }

    @Test fun theHintSaysWhatTheButtonDoesNow() {
        assertEquals("hold to talk", talkHint(TalkPhase.Idle, false, null))
        assertEquals("Domovoi asked you something — hold to answer", talkHint(TalkPhase.Idle, true, null))
        assertEquals("didn't catch that", talkHint(TalkPhase.Idle, true, "didn't catch that"))
        assertEquals("release to cancel", talkHint(TalkPhase.CancelArmed, false, null))
        assertEquals("press to stop", talkHint(TalkPhase.Speaking, false, null))
        assertTrue(talkHint(TalkPhase.Recording, false, "x").startsWith("listening"))
    }

    // ── Message details ─────────────────────────────────────────────────

    @Test fun aSpokenTurnsDetailsSayWhoWasHeardAndWhatAnswered() {
        val turn = decode(answer)
        val user = MessageFacts(
            id = 41, threadId = 2, role = "user", content = "play glass harbor", createdAt = null,
            model = null, error = null, imageNames = emptyList(), device = "this phone",
            source = "voice", speaker = "Ana",
        )
        val rows = detailRows(user, ZoneId.of("UTC")).toMap()
        assertEquals("said into the chat mic", rows["spoken"])
        assertEquals("Ana", rows["heard as"])

        val reply = MessageFacts(
            id = 42, threadId = 2, role = "assistant", content = "Playing Glass Harbor.", createdAt = null,
            model = null, error = null, imageNames = emptyList(), stats = turn.messages[1].stats, source = "voice",
        )
        val r = detailRows(reply, ZoneId.of("UTC")).toMap()
        assertEquals("answered out loud", r["spoken"])
        assertEquals("music (fast)", r["answered by"])
        assertEquals("hearing 0.6s · answering 0.1s · speaking 0.3s", r["time spent"])
        assertEquals("finished in 1.2s", r["reply time"])
    }
}
