package com.domovoi.app.assistant

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import com.domovoi.app.DomovoiApplication
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.time.Instant
import java.time.ZoneId

/**
 * DEBUG BUILDS ONLY: N2's gate on a phone (design-notes plan, milestone N2),
 * through a phone chat's real path with the app's real Clock app intents
 * and the app's real player:
 *
 *     adb shell am start -n com.domovoi.app/.MainActivity   # on screen: Android freezes a cached app
 *     adb shell am broadcast -a com.domovoi.app.debug.N2_GATE -n com.domovoi.app/.assistant.N2GateReceiver --es set all
 *     adb shell run-as com.domovoi.app cat files/n2-gate.txt
 *
 * `set`: clock (a timer and alarms asked first, yes / no / unclear, the
 * card's button, "show my alarms"), player (play, pause, next, previous,
 * stop, sleep, shuffle, each undone by voice or the button), video, or all.
 * It needs songs on the phone with the music permission (Velvet Kites'
 * "Glass Harbor" and "Paper Moon", John Coltrane's "Blue Train") and
 * Movies/glacier-trip.mp4 with the videos permission. Never a model: every
 * phrase here is the phone's own pattern. Only a sender holding DUMP (adb's
 * shell) may start it; a release APK does not contain it.
 */
@OptIn(UnstableApi::class)
class N2GateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val which = intent.getStringExtra("set") ?: "all"
        if (running) return
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

    private var pass = 0
    private var fail = 0

    private suspend fun run(ctx: Context, which: String) {
        val app = (ctx as DomovoiApplication).container
        val out = File(ctx.filesDir, OUT).apply { writeText("") }
        fun line(s: String) {
            out.appendText(s + "\n")
            Log.i(TAG, s)
        }
        fun check(name: String, ok: Boolean, detail: String) {
            if (ok) pass++ else fail++
            line("${if (ok) "PASS" else "FAIL"} $name :: $detail")
        }
        val host = AppActionHost(ctx, app.player, app.books, app.bookPositions)
        val c = AssistantController.forApp(
            ctx,
            beforeLoad = { app.assistant.unload() },
            phoneVoice = { null },
            phone = { settings -> app.phoneChatContext(ctx, settings) },
            actions = { host },
        )
        withTimeoutOrNull(60_000) { while (!c.state.value.installed) delay(200) }
        line("N2 GATE on ${android.os.Build.MODEL}, Android ${android.os.Build.VERSION.RELEASE}; installed=${c.state.value.installed}")
        line("clock app takes: " + ClockIntent.entries.joinToString { "${it.name}=${host.clockTakes(it)}" })
        val before = c.state.value.conversation.id
        c.newConversation()
        withTimeoutOrNull(10_000) { while (c.state.value.conversation.id == before) delay(50) }
        val id = c.state.value.conversation.id

        suspend fun ask(q: String): StoredMessage {
            withTimeoutOrNull(60_000) { while (c.state.value.replying) delay(100) }
            val n = c.state.value.conversation.messages.size
            c.send(q)
            withTimeoutOrNull(60_000) { while (c.state.value.conversation.messages.size < n + 2 || c.state.value.replying) delay(100) }
            val m = c.state.value.conversation.messages.last()
            line("  > $q\n    < ${m.text}  [${m.fastPath ?: "model"}${m.card?.let { " card:${it.kind}/${it.state}" } ?: ""}]")
            return m
        }
        fun lastIndex() = c.state.value.conversation.messages.lastIndex
        fun cardAt(i: Int) = c.state.value.conversation.messages.getOrNull(i)?.card
        val p = app.player
        fun playerLine() = "playing=${p.isPlaying.value} index=${p.index.value} queue=${p.queue.value.map { it.title }} " +
            "pos=${"%.1f".format(p.positionSec.value)} sleep=${p.sleepRemainingSec.value}"

        if (which == "all" || which == "clock") {
            line("\nCLOCK")
            val t = ask("Set a timer for 2 minutes called pasta")
            check("timer asks first", t.card?.kind == CardKind.ACTION && t.card?.state == CardState.OPEN, t.text)
            val y = ask("yes")
            check("a clear yes sets it", y.text.startsWith("Done:"), y.text)
            val a = ask("Set an alarm for 6:45 am")
            val ai = lastIndex()
            c.actionConfirmed(id, ai)
            withTimeoutOrNull(10_000) { while (cardAt(ai)?.state == CardState.OPEN) delay(100) }
            check("the card's button sets the alarm", cardAt(ai)?.state == CardState.DONE, "${a.text} -> ${cardAt(ai)?.state}")
            delay(2_000)
            val next = AndroidPhoneReader(ctx).nextAlarm()?.let { Instant.ofEpochMilli(it.triggerAtMs).atZone(ZoneId.systemDefault()) }
            check("the Clock app's next alarm is 6:45", next?.hour == 6 && next.minute == 45, "next alarm: $next")
            ask("wake me at 7 every weekday")
            val wi = lastIndex()
            val no = ask("no, wait")
            check("a no-word cancels", no.text == ActionTexts.DECLINED && cardAt(wi)?.state == CardState.DISMISSED, no.text)
            val v = ask("wake me a bit before seven tomorrow")
            check("a vague time is asked about", v.text.startsWith("How about 6:45"), v.text)
            val u = ask("hmm")
            check("unclear asks again", u.text.startsWith(ActionTexts.DIDNT_CATCH) && u.card?.state == CardState.OPEN, u.text)
            val yp = ask("yes play some jazz")
            check("yes with more is not a clear yes", yp.text.startsWith(ActionTexts.DIDNT_CATCH), yp.text)
            ask("cancel")
            val s = ask("show my alarms")
            check("show my alarms opens the Clock app", s.text.startsWith("Opening your alarms"), s.text)
            delay(3_000)
        }
        if (which == "all" || which == "player") {
            line("\nPLAYER")
            val pl = ask("play velvet kites")
            delay(3_000)
            check("play music from the phone", pl.card?.kind == CardKind.UNDO && p.queue.value.size == 2 && p.isPlaying.value, playerLine())
            delay(3_000)
            ask("pause")
            delay(1_000)
            check("pause runs at once", !p.isPlaying.value, playerLine())
            ask("undo")
            delay(1_500)
            check("undo resumes", p.isPlaying.value, playerLine())
            ask("next")
            delay(1_500)
            check("next", p.index.value == 1, playerLine())
            ask("undo")
            delay(1_500)
            check("undo goes back to the first song", p.index.value == 0 && p.isPlaying.value, playerLine())
            delay(4_000)
            val posBefore = p.positionSec.value
            ask("go back")
            delay(1_500)
            check("go back restarts the song", p.positionSec.value < posBefore, playerLine())
            ask("undo")
            delay(1_500)
            check("undo puts the place back", kotlin.math.abs(p.positionSec.value - posBefore) < 4, "was ${"%.1f".format(posBefore)}; ${playerLine()}")
            ask("set a sleep timer for 20 minutes")
            delay(1_000)
            check("playback sleep, not a Clock timer", (p.sleepRemainingSec.value ?: 0) in 1190..1200, playerLine())
            ask("undo")
            delay(1_000)
            check("undo clears the sleep timer", p.sleepRemainingSec.value == null, playerLine())
            ask("stop")
            delay(1_000)
            check("stop", p.queue.value.isEmpty(), playerLine())
            ask("undo")
            delay(2_000)
            check("undo brings the queue back", p.queue.value.size == 2 && p.isPlaying.value, playerLine())
            val q0 = p.queue.value.map { it.uid }
            ask("shuffle my music")
            delay(2_000)
            check("shuffle my music", p.queue.value.size == 3 && p.isPlaying.value, playerLine())
            ask("undo")
            delay(2_000)
            check("undo puts the earlier queue back", p.queue.value.map { it.uid } == q0, playerLine())
            ask("pause")
            val pi = lastIndex()
            c.undoTapped(id, pi)
            withTimeoutOrNull(5_000) { while (cardAt(pi)?.state == CardState.OPEN) delay(100) }
            delay(1_000)
            check("the undo button", cardAt(pi)?.state == CardState.DONE && p.isPlaying.value, playerLine())
            val h = ask("play jazz in the kitchen")
            check("house actions are not this round", h.fastPath == AssistantController.FAST_ACTION_HOUSE, h.text)
            ask("stop")
        }
        if (which == "all" || which == "video") {
            line("\nVIDEO")
            val v = ask("play the glacier trip video")
            check("a video asks first", v.card?.kind == CardKind.ACTION && c.state.value.playingVideo == null, v.text)
            ask("yes")
            check("yes plays it", c.state.value.playingVideo?.title?.contains("glacier") == true, "${c.state.value.playingVideo}")
            c.videoClosed()
        }
        c.delete(id)
        line("\nN2 gate: $pass passed, $fail failed\nDONE")
    }

    companion object {
        private const val TAG = "N2Gate"
        private const val OUT = "n2-gate.txt"
        @Volatile private var running = false
        private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    }
}
