package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/**
 * N2's gate: the N0 bench's 150-phrase action eval (wf/assistant-n0,
 * assistant/n0/N0ActionEval.kt, the same phrases and the same checks), run
 * against the product's fast paths: [ActionPatterns] for the actions,
 * [PhoneQuestions] for the five reads. Gate: at least 95% of actions right,
 * and no action at all for a near-miss or chit-chat.
 *
 * Alarm hours said with no am or pm accept both (as the bench does); a
 * missing minute counts as :00.
 */
class ActionEvalTest {
    private val la = ZoneId.of("America/Los_Angeles")
    /** The bench ran mid-afternoon; the phone's clock here says the same. */
    private val now = ZonedDateTime.of(2026, 10, 21, 15, 42, 0, 0, la)

    private enum class Kind { ACTION, NEAR, CHAT }

    private sealed class Check {
        abstract fun ok(a: Map<String, Any?>): Boolean
        data class Num(val f: String, val values: Set<Int>, val missingAs: Int? = null) : Check() {
            override fun ok(a: Map<String, Any?>) = ((a[f] as Int?) ?: missingAs) in values
        }
        data class Range(val f: String, val lo: Int, val hi: Int) : Check() {
            override fun ok(a: Map<String, Any?>) = (a[f] as Int?)?.let { it in lo..hi } == true
        }
        data class Words(val f: String, val words: List<String>) : Check() {
            override fun ok(a: Map<String, Any?>): Boolean {
                val v = plain(a[f]?.toString() ?: return false)
                return words.all { plain(it) in v }
            }
        }
        data class Is(val f: String, val value: String) : Check() {
            override fun ok(a: Map<String, Any?>) = a[f]?.toString() == value
        }
    }

    private data class Case(val kind: Kind, val text: String, val tool: String? = null, val checks: List<Check> = emptyList())

    private fun a(text: String, tool: String, vararg c: Check) = Case(Kind.ACTION, text, tool, c.toList())
    private fun near(text: String) = Case(Kind.NEAR, text)
    private fun chat(text: String) = Case(Kind.CHAT, text)
    private fun timer(text: String, s: Int, label: String? = null) =
        a(text, "set_timer", *listOfNotNull(Check.Num("seconds", setOf(s)), label?.let { Check.Words("label", listOf(it)) }).toTypedArray())
    private fun alarm(text: String, hours: Set<Int>, minute: Int) =
        a(text, "set_alarm", Check.Num("hour", hours), Check.Num("minute", setOf(minute), missingAs = 0))
    private fun control(text: String, action: String, minutes: Int? = null) =
        a(text, "control_playback", *listOfNotNull(Check.Is("action", action), minutes?.let { Check.Num("minutes", setOf(it)) }).toTypedArray())
    private fun music(text: String, vararg words: String) =
        a(text, "play_music", *(if (words.isEmpty()) emptyArray() else arrayOf<Check>(Check.Words("query", words.toList()))))
    private fun book(text: String, vararg words: String) =
        a(text, "play_book", *(if (words.isEmpty()) emptyArray() else arrayOf<Check>(Check.Words("title", words.toList()))))
    private fun info(text: String, topic: String) = a(text, "phone_info", Check.Is("topic", topic))

    private val cases: List<Case> = listOf(
        // Timers (20)
        timer("Set a timer for 10 minutes", 600),
        timer("set a timer for ten minutes", 600),
        timer("Timer for 5 minutes.", 300),
        timer("Set a 15 minute timer", 900),
        timer("Start a timer for half an hour", 1800),
        timer("Set a timer for an hour and a half", 5400),
        timer("set a timer for 1 hour and 30 minutes", 5400),
        timer("Set a timer for 90 seconds", 90),
        timer("Set a timer for forty-five seconds", 45),
        timer("Set a timer for twenty five minutes called laundry", 1500, "laundry"),
        timer("Set a timer for 12 minutes for the pasta", 720, "pasta"),
        timer("timer for three minutes for the eggs", 180, "eggs"),
        timer("Can you set a timer for 8 minutes?", 480),
        timer("Please set a timer for two hours", 7200),
        timer("I need a timer for 20 minutes", 1200),
        timer("Count down five minutes for me", 300),
        timer("Set a pasta timer for 11 minutes", 660, "pasta"),
        timer("Start a quarter of an hour timer", 900),
        timer("Set a timer for a minute", 60),
        timer("Give me a 30 second timer", 30),
        // Alarms (15)
        alarm("Set an alarm for 7 am", setOf(7), 0),
        alarm("Set an alarm for 6:30", setOf(6, 18), 30),
        alarm("Wake me at 6:30", setOf(6, 18), 30),
        alarm("Wake me up at 7", setOf(7, 19), 0),
        alarm("Set an alarm for 5:45 am", setOf(5), 45),
        alarm("Set an alarm for 10 pm", setOf(22), 0),
        alarm("Alarm for 8:15 tomorrow morning", setOf(8), 15),
        alarm("Wake me up at half past six", setOf(6, 18), 30),
        alarm("Set an alarm for quarter to seven", setOf(6, 18), 45),
        alarm("Can you wake me at seven thirty?", setOf(7, 19), 30),
        alarm("I need to get up at 5 tomorrow", setOf(5, 17), 0),
        a("Wake me a bit before seven tomorrow", "set_alarm", Check.Num("hour", setOf(6)), Check.Range("minute", 30, 59)),
        alarm("Set my alarm for noon", setOf(12), 0),
        alarm("Set an alarm for 9 in the morning", setOf(9), 0),
        alarm("Alarm at 6 pm", setOf(18), 0),
        // Playback (21)
        control("Pause", "pause"),
        control("pause the music", "pause"),
        control("Resume", "resume"),
        control("Continue the music", "resume"),
        control("Next", "next"),
        control("Skip this song", "next"),
        control("Next track", "next"),
        control("Previous", "previous"),
        control("Go back", "previous"),
        control("Previous song", "previous"),
        control("Stop", "stop"),
        control("Stop the music", "stop"),
        control("Hold on, pause it for a sec", "pause"),
        control("Can you skip to the next song?", "next"),
        control("Go back one song", "previous"),
        control("Unpause", "resume"),
        control("Keep playing", "resume"),
        control("Stop playing music in 30 minutes", "sleep", 30),
        control("Set a sleep timer for 20 minutes", "sleep", 20),
        control("Stop the music in an hour", "sleep", 60),
        control("Turn the music off after 45 minutes", "sleep", 45),
        // Music (15)
        music("Play some jazz", "jazz"),
        music("Play The Beatles", "beatles"),
        music("Play something by Miles Davis", "miles davis"),
        music("Play my running playlist", "running"),
        music("Put on some classical music", "classical"),
        music("I want to hear Glass Harbor by Velvet Kites", "glass harbor"),
        music("Play Velvet Kites", "velvet kites"),
        music("Play the album Blue Train", "blue train"),
        music("Can you play some relaxing music?", "relaxing"),
        music("Shuffle my music"),
        music("Play lo-fi beats", "lo", "fi"),
        music("Listen to some rock", "rock"),
        music("Play the song Clair de Lune", "clair de lune"),
        music("Play music"),
        music("Start my workout mix", "workout"),
        // Books (8)
        book("Continue my book"),
        book("Resume my audiobook"),
        book("Play my audiobook"),
        book("Keep reading my book"),
        book("Play the Hobbit audiobook", "hobbit"),
        book("Go back to my book"),
        book("Continue listening to Dune", "dune"),
        book("Pick up my audiobook where I left off"),
        // Reads (10)
        info("What's playing?", "now_playing"),
        info("What song is this?", "now_playing"),
        info("How much battery do I have?", "battery"),
        info("What's my battery level?", "battery"),
        info("Is my phone charging?", "battery"),
        info("What timers do I have?", "timers"),
        info("How much time is left on my timer?", "timers"),
        info("Do I have any alarms set?", "alarms"),
        info("How much storage is left?", "storage"),
        info("When is my next alarm?", "alarms"),
        // Near-misses (35)
        near("How do I set a timer on this phone?"),
        near("My alarm didn't go off this morning."),
        near("The timer on my oven is broken."),
        near("What's a good song for a road trip?"),
        near("I can't stop thinking about that movie."),
        near("Next week I'm going to Spain."),
        near("Play is important for kids' development."),
        near("Who played Gandalf in the movies?"),
        near("Can you stop talking like a robot?"),
        near("Skip the small talk, what's the capital of Peru?"),
        near("Next time, tell me a shorter story."),
        near("Resume writing is hard, any tips?"),
        near("How long should I boil an egg?"),
        near("What's ten minutes in seconds?"),
        near("Is 7 am too early to wake up?"),
        near("Tell me about the book 1984."),
        near("What's the battery life of a typical laptop?"),
        near("My car battery died yesterday."),
        near("The music in that restaurant was too loud."),
        near("What is a sleep timer?"),
        near("Should I set an alarm or just trust myself to wake up?"),
        near("I paused my gym membership."),
        near("Who wrote the music for Star Wars?"),
        near("What's the best way to wake up early?"),
        near("I stopped drinking coffee last month."),
        near("Do you know any good audiobooks?"),
        near("How do alarm clocks work?"),
        near("Let's play a game."),
        near("Why does my phone battery drain so fast?"),
        near("Remind me what a timer is for in cooking."),
        near("I love falling asleep to music."),
        near("What does 'previous' mean?"),
        near("My kids keep changing the song in the car."),
        near("Is it bad to charge my phone overnight?"),
        near("Tell me a story about an alarm clock."),
        // Chit-chat (26)
        chat("Hello!"),
        chat("How are you today?"),
        chat("Tell me a joke."),
        chat("What's the capital of France?"),
        chat("Thanks, that's all."),
        chat("Good morning."),
        chat("Who are you?"),
        chat("What can you do?"),
        chat("Why is the sky blue?"),
        chat("Write a haiku about rain."),
        chat("What's 12 times 8?"),
        chat("Recommend a good book for a long flight."),
        chat("I'm bored."),
        chat("Give me a simple pancake recipe."),
        chat("How far away is the moon?"),
        chat("What's the weather like?"),
        chat("How do you say hello in Spanish?"),
        chat("What day is it?"),
        chat("Good night."),
        chat("Do you like music?"),
        chat("What's your favourite song?"),
        chat("Explain photosynthesis simply."),
        chat("I had a long day."),
        chat("What should I cook for dinner?"),
        chat("Give me a fun fact."),
        chat("How many legs does a spider have?"),
    )

    /** Review round 2's near-misses (functional-testing/review-phone-actions,
     *  probe.txt): chat words and search verbs that must not act. Scored in
     *  a chat with no playback (the turn before was the model's, nothing is
     *  playing): bare "continue" and "next one" are the model's there. */
    private val reviewNear: List<String> = listOf(
        "I set a timer for 10 minutes", "I paused the music", "I skipped that song", "I played some jazz yesterday",
        "We listened to some jazz", "I set an alarm for 7", "I stopped the music", "I woke up at 7", "Played some jazz",
        "Set a timer for 10 minutes yesterday", "Should I set a timer for 10 minutes?", "Did you set a timer for 10 minutes?",
        "Can I set an alarm for 7?", "Can I hear that again?", "Can I hear it?", "Can I watch a video?", "Why did you pause?",
        "What happens if I skip?", "Can you play the guitar?", "Can you play chess?", "Could you continue?",
        "Is it ok to play music?", "Can I listen to music while I sleep?", "Do I need to wake up at 7?",
        "What if I set a timer for 10 minutes?", "Don't set a timer", "Don't set a timer for 10 minutes",
        "Please don't play music", "Do not pause", "No, don't stop the music", "Never mind the timer",
        "Not now, play jazz later", "Don't wake me at 7", "I don't want to set an alarm for 7", "No timer for 10 minutes",
        "No alarm for 7 please", "stop the timer", "cancel the timer", "cancel my alarm", "don't stop", "no music",
        "She said set a timer for 5 minutes", "He told me to set an alarm for 7", "\"set a timer for 5 minutes\"",
        "'set a timer for 5 minutes'", "What does \"play\" mean?", "The song is called play that funky music",
        "The book says play", "My boss said stop", "Type next",
        "continue", "Continue.", "keep going", "go on", "carry on", "next one", "next question", "another one",
        "again", "more", "repeat that", "play it",
        "play a joke on him", "play a prank on my brother", "put on a sweater", "put on a movie", "put on a smile",
        "listen to your heart", "listen to this joke", "let's hear it", "let's hear a joke", "play hooky", "play the fool",
        "start a new playlist", "play twenty questions with me", "play something to make me laugh", "play catch",
        "play with the dog", "put on sunscreen", "start the timer", "shuffle the deck", "play the long game",
        "play the harmonica", "put the kids to bed", "watch the kids", "watch out", "watch the game video",
        "I want to watch the video", "open the video", "play that video again", "never mind",
    )

    /** What the product's fast paths do with [text], as the bench's tool
     *  call (name and arguments); null for no action. A read the bench
     *  doesn't score (the weather's "I can't", the clock) is no action. */
    private fun run(text: String, playback: Boolean = true): Pair<String, Map<String, Any?>>? {
        when (val ask = ActionPatterns.match(text, now, playback)) {
            is ActionAsk.Do -> return toTool(ask.action)
            ActionAsk.House, ActionAsk.Undo -> return "house_or_undo" to emptyMap()
            null -> Unit
        }
        val topic = when (PhoneQuestions.match(text)) {
            PhoneQuestion.NOW_PLAYING -> "now_playing"
            PhoneQuestion.BATTERY, PhoneQuestion.CHARGING -> "battery"
            PhoneQuestion.TIMERS -> "timers"
            PhoneQuestion.NEXT_ALARM -> "alarms"
            PhoneQuestion.STORAGE -> "storage"
            else -> null
        } ?: return null
        return "phone_info" to mapOf("topic" to topic)
    }

    private fun toTool(a: PhoneAction): Pair<String, Map<String, Any?>> = when (a.kind) {
        ActionKind.SET_TIMER -> "set_timer" to mapOf("seconds" to a.seconds, "label" to a.label)
        ActionKind.SET_ALARM -> "set_alarm" to mapOf("hour" to a.hour, "minute" to a.minute)
        ActionKind.PLAY_MUSIC -> "play_music" to mapOf("query" to a.query)
        ActionKind.PLAY_BOOK -> "play_book" to mapOf("title" to a.query)
        ActionKind.PAUSE, ActionKind.RESUME, ActionKind.NEXT, ActionKind.PREVIOUS, ActionKind.STOP ->
            "control_playback" to mapOf("action" to a.kind.name.lowercase())
        ActionKind.SLEEP -> "control_playback" to mapOf("action" to "sleep", "minutes" to a.minutes)
        else -> a.kind.name.lowercase() to emptyMap()
    }

    private enum class Outcome { RIGHT, WRONG_ARGS, WRONG_ACTION, MISSED, FALSE_ACTION, NONE_OK }

    private fun score(c: Case): Outcome {
        val got = run(c.text)
        return when {
            c.tool == null -> if (got == null) Outcome.NONE_OK else Outcome.FALSE_ACTION
            got == null -> Outcome.MISSED
            got.first != c.tool -> Outcome.WRONG_ACTION
            c.checks.all { it.ok(got.second) } -> Outcome.RIGHT
            else -> Outcome.WRONG_ARGS
        }
    }

    @Test fun theBenchsPhrasesPassTheGate() {
        assertEquals("the bench's 150 phrases", 150, cases.size)
        val scored = cases.map { it to score(it) }
        val actions = scored.filter { it.first.kind == Kind.ACTION }
        val right = actions.count { it.second == Outcome.RIGHT }
        val falseNear = scored.filter { it.first.kind == Kind.NEAR && it.second == Outcome.FALSE_ACTION }
        val falseChat = scored.filter { it.first.kind == Kind.CHAT && it.second == Outcome.FALSE_ACTION }
        val pct = right * 100.0 / actions.size
        val report = buildString {
            appendLine(String.format(Locale.US, "actions right: %d/%d (%.1f%%)", right, actions.size, pct))
            appendLine("false actions: near-misses ${falseNear.size}/35, chit-chat ${falseChat.size}/26")
            scored.filter { it.second != Outcome.RIGHT && it.second != Outcome.NONE_OK }
                .forEach { (c, o) -> appendLine("  $o: \"${c.text}\" -> ${run(c.text)}") }
        }
        println(report)
        assertEquals(89, actions.size)
        assertTrue("actions at 95% or better\n$report", pct >= 95.0)
        assertTrue("no false action on a near-miss\n$report", falseNear.isEmpty())
        assertTrue("no false action on chit-chat\n$report", falseChat.isEmpty())
    }

    @Test fun theReviewsNearMissesNeverAct() {
        val acted = reviewNear.mapNotNull { t -> run(t, playback = false)?.let { "\"$t\" -> $it" } }
        println("review near-misses: ${reviewNear.size - acted.size}/${reviewNear.size} left alone")
        assertTrue("false actions on the review's near-misses: " + acted.joinToString("; "), acted.isEmpty())
    }

    private companion object {
        fun plain(s: String) = s.lowercase(Locale.US).replace(Regex("[^a-z0-9]+"), "")
    }
}
