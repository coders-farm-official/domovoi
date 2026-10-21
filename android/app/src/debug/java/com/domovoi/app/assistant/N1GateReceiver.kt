package com.domovoi.app.assistant

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.domovoi.app.DomovoiApplication
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.util.Locale

/**
 * DEBUG BUILDS ONLY: N1's gate (design-notes plan, milestone N1), run on the
 * phone through a phone chat's real path (the fast paths, then the model
 * with the facts, and the read tools only for a model allowed them), with
 * the prompt's facts pinned so
 * every question has something it could mention:
 *
 *     adb shell am start -n com.domovoi.app/.MainActivity   # on screen: Android freezes a cached app
 *     adb shell am broadcast -a com.domovoi.app.debug.N1_GATE -n com.domovoi.app/.assistant.N1GateReceiver --es set all
 *     adb shell run-as com.domovoi.app cat files/n1-gate.txt
 *
 * `set`: scripted (the 20 questions, meant for airplane mode), general (the
 * 30 questions whose answers must never bring up a fact unasked) or all.
 * Each question gets a fresh conversation, deleted afterwards. Only a
 * sender holding DUMP (adb's shell) may start it; a release APK does not
 * contain it.
 */
class N1GateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val which = intent.getStringExtra("set") ?: "all"
        if (running) {
            Log.w(TAG, "a gate run is already going")
            return
        }
        running = true
        scope.launch {
            try {
                run(context.applicationContext, which)
            } catch (e: Throwable) {
                Log.e(TAG, "gate failed", e)
                File(context.filesDir, OUT).appendText("\nFAILED: $e\n")
            } finally {
                running = false
            }
        }
    }

    private suspend fun run(ctx: Context, which: String) {
        val app = (ctx as DomovoiApplication).container
        val out = File(ctx.filesDir, OUT).apply { writeText("") }
        fun line(s: String) {
            out.appendText(s + "\n")
            Log.i(TAG, s)
        }
        app.assistant.unload()
        // Android freezes a backgrounded app's process: run with the app on
        // screen (adb shell am start -n com.domovoi.app/.MainActivity first).
        val controller = AssistantController.forApp(
            ctx,
            beforeLoad = { app.assistant.unload() },
            phoneVoice = { null },
            phone = { settings ->
                val real = app.phoneChatContext(ctx, settings)
                object : PhoneContext by real {
                    override fun facts(): PromptFacts = PINNED
                }
            },
        )
        controller.screenShown()
        withTimeoutOrNull(60_000) { while (!controller.state.value.installed) delay(200) }
        // Load the model before the first question (its first load writes
        // the CPU weight cache: minutes on the emulator).
        controller.prepare()
        withTimeoutOrNull(900_000) { while (controller.state.value.engine !is EngineState.Ready) delay(500) }
        line("engine: ${controller.state.value.engine}")
        val network = AndroidPhoneReader(ctx).network()
        line("N1 GATE on ${android.os.Build.MODEL}, Android ${android.os.Build.VERSION.RELEASE}; network at start: $network")
        line("pinned facts: ${PhoneFacts.line(PINNED)}")
        if (which == "all" || which == "scripted") {
            var ok = 0
            line("\nSCRIPTED (20, airplane mode)")
            SCRIPTED.forEachIndexed { i, (q, expect) ->
                val a = ask(controller, q)
                val pass = a.error == null && a.text.isNotBlank() && Regex(expect, RegexOption.IGNORE_CASE).containsMatchIn(a.text)
                if (pass) ok++
                line(String.format(Locale.US, "%2d. %s [%s] %s", i + 1, if (pass) "PASS" else "FAIL", a.how, q))
                line("    -> ${a.text.replace('\n', ' ')}${a.error?.let { " ERROR $it" } ?: ""}")
            }
            line("scripted: $ok/${SCRIPTED.size} answered")
        }
        if (which == "all" || which == "general") {
            var clean = 0
            line("\nGENERAL (30): no fact mentioned unasked")
            GENERAL.forEachIndexed { i, q ->
                val a = ask(controller, q)
                val said = MENTIONS.filter { a.text.contains(it, ignoreCase = true) }
                val pass = said.isEmpty() && a.error == null
                if (pass) clean++
                line(String.format(Locale.US, "%2d. %s [%s] %s", i + 1, if (pass) "CLEAN" else "MENTIONED $said", a.how, q))
                line("    -> ${a.text.replace('\n', ' ')}${a.error?.let { " ERROR $it" } ?: ""}")
            }
            line("general: $clean/${GENERAL.size} never mentioned a fact")
        }
        controller.screenHidden()
        controller.unload()
        line("\nDONE")
    }

    private data class Answer(val text: String, val error: String?, val how: String)

    private suspend fun ask(c: AssistantController, q: String): Answer {
        // The last reply must be done first: a send during a reply is ignored.
        withTimeoutOrNull(300_000) { while (c.state.value.replying) delay(200) }
        val before = c.state.value.conversation.id
        c.newConversation()
        withTimeoutOrNull(10_000) { while (c.state.value.conversation.id == before) delay(50) }
        val id = c.state.value.conversation.id
        c.send(q)
        val done = withTimeoutOrNull(300_000) {
            while (c.state.value.conversation.messages.size < 2 || c.state.value.replying) delay(200)
            true
        } == true
        val m = c.state.value.conversation.messages.getOrNull(1)
        val how = when {
            m == null -> "none"
            m.fromClock -> "clock"
            m.fastPath != null -> "fast:${m.fastPath}" + (m.card?.let { " card:${it.kind.name.lowercase()}" } ?: "")
            m.tools.isNotEmpty() -> "model+${m.tools.joinToString("+")} ${m.firstTextMs ?: "?"} ms"
            else -> "model ${m.firstTextMs ?: "?"} ms"
        }
        c.delete(id)
        delay(300)
        if (!done) c.stop()
        return Answer(m?.text.orEmpty(), m?.error ?: if (!done) "no reply in 300 s" else null, how)
    }

    companion object {
        private const val TAG = "N1Gate"
        private const val OUT = "n1-gate.txt"
        @Volatile private var running = false
        private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

        /** Distinctive names, so a mention is never a coincidence. */
        val PINNED = PromptFacts(
            playing = NowPlayingFact("Glass Harbor", "Velvet Kites", positionSec = 72.0, durationSec = 238.0),
            sleepRemainingSec = 20 * 60,
            server = ServerLink.CONNECTED,
            listener = "Kamron",
        )

        private val MENTIONS = listOf("glass harbor", "velvet kites", "sleep timer", "kamron", "home server")

        /** The 20 scripted questions and what a right answer contains. */
        private val SCRIPTED: List<Pair<String, String>> = listOf(
            "What time is it?" to "\\d{1,2}:\\d{2}",
            "How much battery do I have?" to "\\d+%",
            "Is it charging?" to "charging",
            "How much space do I have?" to "GB|MB",
            "Am I on Wi-Fi or mobile data?" to "offline|airplane|wi-fi|mobile",
            "When's my next alarm?" to "alarm",
            "Is Do Not Disturb on?" to "do not disturb",
            "What timers do I have?" to "home server chats",
            "What phone is this?" to "android",
            "Which Android is this?" to "android \\d+",
            "Which model are you?" to "gemma",
            "What's playing?" to "glass harbor",
            "What's on my calendar today?" to "calendar",
            "Which headphones are connected?" to "headphones|bluetooth|connected",
            "How long is left on the sleep timer?" to "20",
            "What's my name?" to "kamron",
            "What rooms are in my house?" to "home server chats",
            "Am I connected to the home server?" to "yes|connected",
            "Do I have a video called glacier trip on this phone?" to "glacier",
            "How much memory does this phone have?" to "\\d+ ?GB",
        )

        private val GENERAL: List<String> = listOf(
            "Tell me a joke.",
            "What's the capital of France?",
            "How do I boil an egg?",
            "Give me a fun fact about octopuses.",
            "What should I cook for dinner tonight?",
            "How far away is the moon?",
            "Write a haiku about autumn.",
            "What's a good name for a cat?",
            "How do I tie a tie?",
            "Why is the sky blue?",
            "Recommend a good book.",
            "What's 15 percent of 80?",
            "How do I get rid of hiccups?",
            "Explain photosynthesis simply.",
            "What's the tallest mountain in the world?",
            "How many legs does a spider have?",
            "Give me a quick workout tip.",
            "What rhymes with orange?",
            "What's the difference between weather and climate?",
            "Tell me something interesting.",
            "How do I make good coffee?",
            "Who wrote Romeo and Juliet?",
            "What's a good stretch for my lower back?",
            "How do airplanes stay in the air?",
            "Suggest something fun to do this weekend.",
            "What is the speed of light?",
            "How do I say thank you in Spanish?",
            "What's a healthy breakfast?",
            "Help me name my new houseplant.",
            "How are you today?",
        )
    }
}
