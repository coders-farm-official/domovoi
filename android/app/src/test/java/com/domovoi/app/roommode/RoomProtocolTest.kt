package com.domovoi.app.roommode

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The room phone against docs/uml/satellite-fixtures.json, the file the
 * core's tests (domovoi/tests/test_satellite_supports.py) and the Pi's
 * (satellite/tests/test_protocol_fixtures.py) read: its hello is the
 * fixture's `android_room` hello, it declares only abilities the core knows,
 * and it understands every frame the core may send a device that declared
 * what it declares.
 */
class RoomProtocolTest {
    private val fixtures: JsonObject by lazy {
        val path = System.getProperty("domovoi.satellite.fixtures")
            ?: File(System.getProperty("domovoi.repo.dir") ?: "../..", "docs/uml/satellite-fixtures.json").path
        Json.parseToJsonElement(File(path).readText()) as JsonObject
    }
    private val device get() = fixtures["devices"]!!.jsonObject["android_room"]!!.jsonObject

    @Test
    fun theHelloIsTheFixturesAndroidRoomHello() {
        val want = device["hello"]!!.jsonObject
        val ours = RoomProtocol.hello(
            roomId = want["room_id"]!!.jsonPrimitive.content,
            wakeWord = want["wake_word"]!!.jsonPrimitive.content,
            pairingToken = want["pairing_token"]!!.jsonPrimitive.content,
            approvalCode = want["approval_code"]!!.jsonPrimitive.content,
            echoCanceller = true,
        )
        assertEquals(want, ours)
        assertEquals(RoomProtocol.BOARD, device["expect"]!!.jsonObject["board"]!!.jsonPrimitive.content)
    }

    @Test
    fun withoutAnEchoCancellerThereIsNoFullDuplexAndNeverChatMode() {
        val supports = RoomProtocol.supports(echoCanceller = false)
        assertFalse("full_duplex" in supports)
        assertFalse("chat_mode" in RoomProtocol.supports(echoCanceller = true))
        val known = fixtures["abilities"]!!.jsonObject.keys
        for (name in RoomProtocol.supports(true)) assertTrue("$name is not an ability the core knows", name in known)
    }

    @Test
    fun everyFrameTheCoreMaySendIsHandled() {
        val expect = device["expect"]!!.jsonObject
        val frames = fixtures["always_sent"]!!.jsonArray.map { it.jsonPrimitive.content } +
            expect["frames_sent"]!!.jsonArray.map { it.jsonPrimitive.content }
        for (frame in frames) {
            // "announcement" is a response_start out of turn, not a frame type.
            val type = if (frame == "announcement") "response_start" else frame
            val parsed = RoomProtocol.parse(buildJsonObject { put("type", type) }.toString())
            assertFalse("$type is sent to this device and not handled", parsed is RoomProtocol.ServerFrame.Other)
        }
        // And nothing is declared that brings a frame this client ignores.
        for (frame in expect["frames_never"]!!.jsonArray.map { it.jsonPrimitive.content }) {
            assertTrue(frame, RoomProtocol.parse(buildJsonObject { put("type", frame) }.toString()) is RoomProtocol.ServerFrame.Other)
        }
    }

    @Test
    fun theReadyFeaturesGateTheNewMessageTypes() {
        val ready = RoomProtocol.parse(device["ready"]!!.toString()) as RoomProtocol.ServerFrame.Ready
        assertEquals(setOf("speech_pause", "end_capture", "music_failed", "health", "log_push"), ready.features)
        val pause = buildJsonObject { put("type", "speech_pause") }
        assertTrue(RoomProtocol.maySend(pause, ready.features))
        assertFalse(RoomProtocol.maySend(pause, emptySet()))
        assertFalse(RoomProtocol.maySend(buildJsonObject { put("type", "health") }, setOf("speech_pause")))
        assertTrue(RoomProtocol.maySend(buildJsonObject { put("type", "utterance_end") }, emptySet()))
    }

    @Test
    fun serverFramesParse() {
        assertEquals(
            RoomProtocol.ServerFrame.Error("awaiting_approval", "waiting for approval on the dashboard", "305117"),
            RoomProtocol.parse("""{"type":"error","reason":"awaiting_approval","message":"waiting for approval on the dashboard","code":"305117"}"""),
        )
        assertEquals(RoomProtocol.ServerFrame.ResponseStart("Hi", 22050), RoomProtocol.parse("""{"type":"response_start","text":"Hi","audio_sample_rate":22050}"""))
        assertEquals(RoomProtocol.ServerFrame.ResponseEnd(false, true), RoomProtocol.parse("""{"type":"response_end","interrupted":false,"expect_followup":true}"""))
        assertEquals(RoomProtocol.ServerFrame.EndCapture(7), RoomProtocol.parse("""{"type":"end_capture","utt":7}"""))
        assertEquals(RoomProtocol.ServerFrame.SetVolume(100), RoomProtocol.parse("""{"type":"set_volume","level":140}"""))
        assertEquals(RoomProtocol.ServerFrame.SetWakeWord("hey_house", 0.6), RoomProtocol.parse("""{"type":"set_wake_word","slug":"hey_house","threshold":0.6}"""))
        assertEquals(RoomProtocol.ServerFrame.DropinStart("office", "Office", 16000, true), RoomProtocol.parse("""{"type":"dropin_start","peer_room":"office","peer_label":"Office","audio_sample_rate":16000,"full_duplex":true}"""))
        assertTrue(RoomProtocol.parse("not json") is RoomProtocol.ServerFrame.Other)
        assertTrue(RoomProtocol.parse("""{"type":"chat_start"}""") is RoomProtocol.ServerFrame.Other)
    }

    @Test
    fun onlyAnHttpUrlIsAStream() {
        assertTrue(RoomProtocol.isStreamUrl("http://192.168.0.240:8051/stream.mp3"))
        assertFalse(RoomProtocol.isStreamUrl("-w /sdcard/x"))
        assertFalse(RoomProtocol.isStreamUrl("file:///etc/passwd"))
        assertFalse(RoomProtocol.isStreamUrl("http://h/a b"))
        assertFalse(RoomProtocol.isStreamUrl(null))
    }

    @Test
    fun theStreamUrlFollowsTheCoreUrl() {
        assertEquals("ws://192.168.0.240:6370/v1/stream/kitchen", RoomProtocol.streamUrl("http://192.168.0.240:6370", "kitchen"))
        assertEquals("wss://h:6370/v1/stream/a", RoomProtocol.streamUrl("https://h:6370/", "a"))
    }

    @Test
    fun theHealthSampleCarriesOnlyWhatTheCoreKeepsPlusTheBatteryPair() {
        // Every key the core keeps from some device's sample.
        val stored = fixtures["devices"]!!.jsonObject.values.flatMap {
            it.jsonObject["health"]!!.jsonObject["stored"]!!.jsonObject.keys
        }.toSet()
        val sample = HealthSample.build(
            HealthSample.Facts(
                seq = 3, ts = 1760280120.0, rssKb = 98000, cpuPct = 6.5, tempC = 34.0, uptimeS = 432000,
                procUptimeS = 5400, coreOk = true, micExpected = true, micFps = 0.0, idle = true, wsUp = true,
                wsDownForS = 0, reconnects = 2, wifiRxMbits = 144.0, wifiSsid = null, wifiSignalDbm = -58,
                batteryPercent = 64, batteryCharging = false, onPower = true, micBlocked = true,
                wakeLoopAgeS = 0.1, threads = 31,
            ),
        )
        assertEquals("health", sample["type"]!!.jsonPrimitive.content)
        // Only keys the core keeps; the battery as its one object.
        val extra = sample.keys - stored - "type"
        assertEquals(emptySet<String>(), extra)
        assertEquals(null, sample["battery_percent"])
        assertEquals(null, sample["battery_charging"])
        assertEquals(
            device["health"]!!.jsonObject["frame"]!!.jsonObject["battery"],
            sample["battery"],
        )
        assertEquals(64, sample["battery"]!!.jsonObject["percent"]!!.jsonPrimitive.content.toInt())
        assertEquals(JsonPrimitive(true), sample["mic_blocked"])
        assertTrue(sample.toString().length < 4096)
    }
}
