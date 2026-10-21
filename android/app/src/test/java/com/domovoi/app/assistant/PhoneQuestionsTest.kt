package com.domovoi.app.assistant

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/** The phone's own fast paths (N1): what each question form matches, what
 *  it answers from the phone, the permission flow, and the never-blend
 *  rules. */
class PhoneQuestionsTest {
    private val la = ZoneId.of("America/Los_Angeles")
    private val now = ZonedDateTime.of(2026, 10, 21, 15, 42, 0, 0, la)
    private val nowMs = now.toInstant().toEpochMilli()
    private val style = ClockStyle(la, Locale.US, is24Hour = false)

    private fun inputs(phone: PhoneContext) = PhoneAnswerInputs(now, style, phone, "Gemma 4 E2B", LlmBackend.CPU)

    private fun say(q: PhoneQuestion, phone: PhoneContext): String = runBlocking {
        (PhoneQuestions.answer(q, inputs(phone)) as PhoneAnswer.Said).text
    }

    private fun granted(): FakePhone = FakePhone().also { p -> PhonePermission.entries.forEach { p.permissions[it] = PermissionState.GRANTED } }

    // ── Matching ────────────────────────────────────────────────────────

    private val asked: Map<PhoneQuestion, List<String>> = mapOf(
        PhoneQuestion.NOW_PLAYING to listOf("What's playing?", "what song is this", "Who sings this?", "what am I listening to right now"),
        PhoneQuestion.BATTERY to listOf("How much battery do I have?", "what's my battery at", "battery level", "Hey Domovoi, how much battery is left?"),
        PhoneQuestion.CHARGING to listOf("Is it charging?", "is my phone charging", "is the phone plugged in"),
        PhoneQuestion.STORAGE to listOf("How much space do I have?", "how much storage is left", "Is my phone full?", "how much free space do I have left"),
        PhoneQuestion.NETWORK to listOf("Am I on Wi-Fi or mobile data?", "am i on wifi", "Am I on mobile data", "am I online", "do I have internet"),
        PhoneQuestion.NEXT_ALARM to listOf("When's my next alarm?", "what time is my alarm set for", "do I have an alarm set", "is an alarm set"),
        PhoneQuestion.DO_NOT_DISTURB to listOf("Is Do Not Disturb on?", "is DND on", "am I on do not disturb", "is my phone on silent"),
        PhoneQuestion.TIMERS to listOf("What timers do I have?", "do I have any timers running", "how much time is left on my timer", "what reminders do I have"),
        PhoneQuestion.PHONE_MODEL to listOf("What phone is this?", "which phone is this", "what model is this phone"),
        PhoneQuestion.ANDROID_VERSION to listOf("Which Android is this?", "what version of Android am I running", "what android version is this"),
        PhoneQuestion.WHICH_MODEL to listOf("Which model are you?", "what model are you", "what AI are you", "are you on the GPU"),
        PhoneQuestion.CALENDAR_TODAY to listOf("What's on my calendar?", "what do I have today", "what's on my calendar today", "what is my schedule"),
        PhoneQuestion.CALENDAR_TOMORROW to listOf("What's on my calendar tomorrow?", "do I have anything tomorrow"),
        PhoneQuestion.CALENDAR_NEXT to listOf("What's my next meeting?", "when is my next appointment"),
        PhoneQuestion.WHERE_AM_I to listOf("Where am I?", "what's my location"),
        PhoneQuestion.WIFI_NAME to listOf("What Wi-Fi am I on?", "what's the wifi network called", "what network am I connected to"),
        PhoneQuestion.HEADPHONES to listOf("Which headphones are connected?", "are my earbuds connected", "what bluetooth devices are connected"),
        PhoneQuestion.WEATHER to listOf("What's the weather like?", "is it going to rain today", "what's the weather here"),
    )

    @Test fun eachFormMatchesItsQuestion() {
        asked.forEach { (q, phrases) -> phrases.forEach { assertEquals(it, q, PhoneQuestions.match(it)) } }
    }

    /** Near misses go to the model: about the topic, not asking for the
     *  reading (the N0 eval's false-action list). */
    @Test fun nearMissesGoToTheModel() {
        listOf(
            "Why does my phone battery drain so fast?",
            "Is it bad to charge my phone overnight?",
            "How do I set a timer on this phone?",
            "My alarm didn't go off this morning.",
            "What's a good song for a road trip?",
            "how do I free up space on android",
            "what is the best phone to buy",
            "what's the weather like in Paris in May",
            "where am I supposed to put the key",
            "what is wifi",
            "tell me about android",
            "what timers are good for pasta",
        ).forEach { assertNull(it, PhoneQuestions.match(it)) }
    }

    @Test fun theClockQuestionsStayTheClocks() {
        listOf("what time is it", "what's the date").forEach { assertNull(it, PhoneQuestions.match(it)) }
    }

    // ── Answers ─────────────────────────────────────────────────────────

    @Test fun battery() {
        val phone = FakePhone()
        assertEquals("The battery is at 82% and charging.", say(PhoneQuestion.BATTERY, phone))
        phone.reader.battery = BatteryReading(40, charging = false)
        assertEquals("The battery is at 40%, not charging.", say(PhoneQuestion.BATTERY, phone))
        assertEquals("No, it isn't charging. The battery is at 40%.", say(PhoneQuestion.CHARGING, phone))
        phone.reader.battery = null
        assertEquals("I can't read the battery on this phone.", say(PhoneQuestion.BATTERY, phone))
    }

    @Test fun storageAndNetwork() {
        val phone = FakePhone()
        assertEquals("You have 23.4 GB free of 128 GB.", say(PhoneQuestion.STORAGE, phone))
        assertEquals("You're on Wi-Fi.", say(PhoneQuestion.NETWORK, phone))
        phone.reader.network = NetworkReading(NetworkKind.MOBILE)
        assertEquals("You're on mobile data.", say(PhoneQuestion.NETWORK, phone))
        phone.reader.network = NetworkReading(NetworkKind.NONE, airplaneMode = true)
        assertEquals("You're offline: airplane mode is on.", say(PhoneQuestion.NETWORK, phone))
    }

    @Test fun theNextAlarmAndDoNotDisturb() {
        val phone = FakePhone()
        assertEquals("You have no alarm set in the Clock app.", say(PhoneQuestion.NEXT_ALARM, phone))
        phone.reader.alarm = AlarmReading(now.plusDays(1).withHour(6).withMinute(30).toInstant().toEpochMilli())
        assertEquals("Your next alarm is tomorrow at 6:30 AM.", say(PhoneQuestion.NEXT_ALARM, phone))
        assertEquals("Do Not Disturb is off.", say(PhoneQuestion.DO_NOT_DISTURB, phone))
        phone.reader.dnd = DndMode.PRIORITY
        assertEquals("Do Not Disturb is on: only priority notifications come through.", say(PhoneQuestion.DO_NOT_DISTURB, phone))
    }

    @Test fun thePhoneAndTheModel() {
        val phone = FakePhone()
        assertEquals("This is a Google Pixel 7 on Android 15, with 8 GB of memory.", say(PhoneQuestion.PHONE_MODEL, phone))
        assertEquals("This phone runs Android 15.", say(PhoneQuestion.ANDROID_VERSION, phone))
        assertEquals("I'm Domovoi, running Gemma 4 E2B on this phone's CPU, with no internet.", say(PhoneQuestion.WHICH_MODEL, phone))
        phone.reader.device = DeviceReading("samsung", "SM-F946B", "16", 36, 11_500_000_000)
        assertEquals("This is a Samsung SM-F946B on Android 16, with 12 GB of memory.", say(PhoneQuestion.PHONE_MODEL, phone))
    }

    @Test fun whatsPlaying() {
        val phone = FakePhone()
        assertEquals("Nothing is playing.", say(PhoneQuestion.NOW_PLAYING, phone))
        phone.facts = PromptFacts(NowPlayingFact("Glass Harbor", "Velvet Kites", positionSec = 72.0, durationSec = 238.0), sleepRemainingSec = 600)
        assertEquals(
            "Playing \"Glass Harbor\" by Velvet Kites on this phone, 1:12 of 3:58. The sleep timer stops it in 10 minutes.",
            say(PhoneQuestion.NOW_PLAYING, phone),
        )
        phone.facts = PromptFacts(NowPlayingFact("Song", null, paused = true, room = "kitchen"))
        assertEquals("Paused: \"Song\" in the kitchen.", say(PhoneQuestion.NOW_PLAYING, phone))
    }

    @Test fun timersFromThePhonesOwnCopy() {
        val phone = FakePhone()
        assertTrue(say(PhoneQuestion.TIMERS, phone).startsWith("This phone has no timers or reminders from your home server."))
        phone.timers = listOf(
            PhoneTimer("pasta", reminder = false, triggerAtMs = nowMs + 9 * 60_000, room = "kitchen"),
            PhoneTimer("call mom", reminder = true, triggerAtMs = now.withHour(18).withMinute(30).toInstant().toEpochMilli()),
            PhoneTimer("secret", reminder = true, triggerAtMs = now.withHour(19).withMinute(0).toInstant().toEpochMilli(), masked = true),
            PhoneTimer("old", reminder = false, triggerAtMs = nowMs - 1_000),
        )
        val said = say(PhoneQuestion.TIMERS, phone)
        assertEquals(
            "This phone has 3 timers and reminders from your home server: pasta, 9 minutes left (kitchen); " +
                "a reminder, call mom, at 6:30 PM today; a private reminder at 7:00 PM today.",
            said,
        )
        assertFalse("a private reminder's words never show", said.contains("secret"))
    }

    @Test fun headphonesNeedNoPermission() {
        val phone = FakePhone()
        assertEquals(null, PhoneQuestion.HEADPHONES.permission)
        assertEquals("No Bluetooth headphones or speakers are connected.", say(PhoneQuestion.HEADPHONES, phone))
        phone.reader.headphones = listOf("WH-1000XM5")
        assertEquals("WH-1000XM5 is connected.", say(PhoneQuestion.HEADPHONES, phone))
    }

    // ── Permissions: asked once, refused means "can't", never a nag ─────

    @Test fun aPermissionNeverAskedComesBackAsTheAsk() = runBlocking {
        val phone = FakePhone()
        val a = PhoneQuestions.answer(PhoneQuestion.CALENDAR_TODAY, inputs(phone))
        assertEquals(PhoneAnswer.Ask(PhonePermission.CALENDAR, PhoneQuestion.CALENDAR_TODAY), a)
        assertFalse("nothing is read before it's allowed", phone.reader.reads.contains("calendar"))
    }

    @Test fun aRefusedPermissionSaysItCantAndNeverAsks() = runBlocking {
        val phone = FakePhone().also { it.permissions[PhonePermission.CALENDAR] = PermissionState.DECLINED }
        val a = PhoneQuestions.answer(PhoneQuestion.CALENDAR_TODAY, inputs(phone)) as PhoneAnswer.Said
        assertTrue(a.text.startsWith("I can't read this phone's calendar without the calendar permission"))
        assertFalse(a.text.contains("home server"))
        assertFalse(phone.reader.reads.contains("calendar"))
        phone.permissions[PhonePermission.LOCATION] = PermissionState.DECLINED
        val where = PhoneQuestions.answer(PhoneQuestion.WHERE_AM_I, inputs(phone)) as PhoneAnswer.Said
        assertTrue(where.text.startsWith("I can't see where the phone is"))
    }

    @Test fun offWifiTheNetworksNameNeedsNoAsking() = runBlocking {
        val phone = FakePhone().also { it.reader.network = NetworkReading(NetworkKind.MOBILE) }
        val a = PhoneQuestions.answer(PhoneQuestion.WIFI_NAME, inputs(phone))
        assertEquals(PhoneAnswer.Said("You're not on Wi-Fi: you're on mobile data."), a)
        assertFalse(phone.reader.reads.contains("wifi"))
    }

    @Test fun withLocationTheNetworkAndThePlace() {
        val phone = granted()
        assertEquals("You're on the Wi-Fi network \"HomeNet\".", say(PhoneQuestion.WIFI_NAME, phone))
        phone.reader.wifi = null
        assertTrue(say(PhoneQuestion.WIFI_NAME, phone).contains("precise location"))
        assertEquals(
            "The phone's last location is 47.6062° N, 122.3321° W (to about 20 m). I can't turn that into an address without the internet.",
            say(PhoneQuestion.WHERE_AM_I, phone),
        )
    }

    // ── Never blended: the phone's calendar, never the household's ──────

    @Test fun theCalendarIsThePhonesOwn() {
        val phone = granted()
        val at = { h: Int, m: Int -> now.withHour(h).withMinute(m).toInstant().toEpochMilli() }
        phone.reader.events = listOf(
            CalendarEvent("Dentist", at(16, 30), at(17, 0), allDay = false),
            CalendarEvent("Lunch", at(12, 0), at(13, 0), allDay = false),
            CalendarEvent("Flight", now.plusDays(1).withHour(8).withMinute(0).toInstant().toEpochMilli(), null, allDay = false),
        )
        assertEquals("On this phone's calendar today from now, 1 event: Dentist at 4:30 PM.", say(PhoneQuestion.CALENDAR_TODAY, phone))
        assertEquals("On this phone's calendar tomorrow, 1 event: Flight at 8:00 AM.", say(PhoneQuestion.CALENDAR_TOMORROW, phone))
        assertEquals("Your next event is Dentist, at 4:30 PM today.", say(PhoneQuestion.CALENDAR_NEXT, phone))
    }

    /** One honest line, and only that: the household's calendar is never
     *  read from a phone chat. */
    @Test fun anEmptyPhoneCalendarPointsToHomeServerChatsInOneLine() {
        val phone = granted()
        assertEquals("Nothing else is on this phone's calendar today.", say(PhoneQuestion.CALENDAR_TODAY, phone))
        phone.serverSet = true
        assertEquals(
            "Nothing else is on this phone's calendar today. The household calendar is in your home server chats.",
            say(PhoneQuestion.CALENDAR_TODAY, phone),
        )
        phone.permissions[PhonePermission.CALENDAR] = PermissionState.DECLINED
        assertTrue(say(PhoneQuestion.CALENDAR_TODAY, phone).endsWith(PhoneQuestions.HOUSEHOLD_CALENDAR))
    }

    @Test fun theWeatherIsNotLookedUp() {
        val phone = FakePhone()
        assertEquals("I can't check the weather from this phone: phone chats don't use the internet.", say(PhoneQuestion.WEATHER, phone))
        phone.serverSet = true
        assertTrue(say(PhoneQuestion.WEATHER, phone).endsWith("Your home server chats can tell you."))
        assertEquals(null, PhoneQuestion.WEATHER.permission)
    }

    @Test fun timesAreSaidRelativeToToday() {
        val i = inputs(FakePhone())
        assertEquals("at 6:00 PM today", PhoneQuestions.whenSaid(now.withHour(18).withMinute(0).toInstant().toEpochMilli(), i))
        assertEquals("Friday at 9:00 AM", PhoneQuestions.whenSaid(now.plusDays(2).withHour(9).withMinute(0).toInstant().toEpochMilli(), i))
        assertEquals("4 minutes", PhoneQuestions.left(4 * 60_000))
        assertEquals("1 hour 5 minutes", PhoneQuestions.left(65 * 60_000))
    }
}
