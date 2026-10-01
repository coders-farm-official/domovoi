package com.domovoi.app.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TracesTest {

    // Shaped like the text ApplicationExitInfo.getTraceInputStream() returns
    // for an ANR: a header, then every thread, the main one first.
    private val anrDump = """
        Subject: Input dispatching timed out (com.domovoi.app/com.domovoi.app.MainActivity is not responding. Waited 5001ms for MotionEvent)

        ----- pid 12222 at 2026-09-30 20:39:10.077 -----
        Cmd line: com.domovoi.app
        Build fingerprint: 'google/sdk_gphone64_x86_64/emu64xa:15/AE3A.240806.005/12228598:userdebug/dev-keys'

        DALVIK THREADS (31):
        "main" prio=5 tid=1 Runnable
          | group="main" sCount=0 ucsCount=0 flags=0 obj=0x72a8c4b8 self=0x7a3e2b4c4000
          | sysTid=12222 nice=-10 cgrp=top-app sched=0/0 handle=0x7a3f9a1d5f80
          | state=R schedstat=( 9348823172 141250467 2399 ) utm=870 stm=64 core=1 HZ=100
          native: #00 pc 0000000000598a4c  /apex/com.android.art/lib64/libart.so (art::StackDumpVisitor::StartMethod+92)
          native: #01 pc 00000000005a1b22  /apex/com.android.art/lib64/libart.so (art::Thread::DumpStack+562)
          at androidx.compose.foundation.FocusableNode.<init>(Focusable.kt:197)
          at androidx.compose.foundation.ClickableElement.create(Clickable.kt:475)
          at com.domovoi.app.ui.screens.music.PlayerTabKt.PlayerPanel(PlayerTab.kt:424)
          at com.domovoi.app.ui.screens.music.MusicScreenKt${'$'}MusicScreen${'$'}6${'$'}1${'$'}1${'$'}6.invoke(MusicScreen.kt:426)
          - locked <0x0b4a1c2e> (a java.lang.Object)
          at android.os.Looper.loop(Looper.java:317)

        "Signal Catcher" daemon prio=10 tid=7 Runnable
          | group="system" sCount=0 ucsCount=0 flags=0 obj=0x12c80218 self=0x7a3e2b4d1c00
          at nothing.we.Want(Here.java:1)
    """.trimIndent()

    @Test fun anrTraceKeepsTheMainThreadOnly() {
        val t = AnrTrace.mainThread(anrDump)!!
        assertTrue(t.startsWith("Subject: Input dispatching timed out"))
        assertTrue(t.contains("\"main\" prio=5 tid=1 Runnable"))
        assertTrue(t.contains("at com.domovoi.app.ui.screens.music.PlayerTabKt.PlayerPanel(PlayerTab.kt:424)"))
        assertTrue(t.contains("- locked <0x0b4a1c2e>"))
        assertTrue(t.contains("native: #00"))
        // Thread bookkeeping and the other threads are dropped.
        assertFalse(t.contains("| group="))
        assertFalse(t.contains("Signal Catcher"))
        assertFalse(t.contains("nothing.we.Want"))
    }

    @Test fun anrTraceIsCapped() {
        val frames = (1..200).joinToString("\n") { "  at a.b.C.m$it(C.java:$it)" }
        val natives = (0..20).joinToString("\n") { "  native: #$it pc 0 /x.so" }
        val t = AnrTrace.mainThread("\"main\" prio=5 tid=1 Native\n$natives\n$frames\n", maxFrames = 40, maxNative = 6)!!
        assertEquals(40, t.lines().count { it.trimStart().startsWith("at ") })
        assertEquals(6, t.lines().count { it.trimStart().startsWith("native:") })
        assertTrue(t.lines().last().contains("175 more"))
    }

    @Test fun anrTraceWithoutAMainThreadKeepsTheSubject() {
        assertEquals("Subject: x", AnrTrace.mainThread("Subject: x\n\"other\" tid=2\n  at a.B.c(B.java:1)"))
        assertNull(AnrTrace.mainThread("nothing useful"))
    }

    @Test fun stackKeepsEverySectionTrimmed() {
        val inner = IllegalStateException("inner cause")
        val outer = RuntimeException("outer", inner)
        val text = trimStack(outer.stackTraceToString(), framesPerSection = 3)
        assertTrue(text.startsWith("java.lang.RuntimeException: outer"))
        assertTrue(text.contains("Caused by: java.lang.IllegalStateException: inner cause"))
        val firstSection = text.substringBefore("Caused by:")
        assertTrue(firstSection.lines().count { it.trimStart().startsWith("at ") } <= 3)
    }

    @Test fun stackFromAnOutOfMemoryCrashSurvives() {
        val raw = buildString {
            append("java.lang.OutOfMemoryError: Failed to allocate a 56 byte allocation with 130560 free bytes\n")
            (1..120).forEach { append("\tat androidx.compose.Frame$it(F.kt:$it)\n") }
        }
        val p = crashProblem("main", raw, raw.lineSequence().first(), atMs = 1_000, pid = 12_222)
        assertEquals(ProblemKind.CRASH, p.kind)
        assertTrue(p.summary.startsWith("Crashed: java.lang.OutOfMemoryError"))
        assertEquals(30, p.trace!!.lines().count { it.trimStart().startsWith("at ") })
        assertTrue(p.trace!!.contains("90 more"))
        assertEquals("thread \"main\", pid 12222", p.description)
    }

    @Test fun stackIsCutAtTheCharacterCap() {
        val raw = "x: " + "y".repeat(50_000)
        assertTrue(trimStack(raw, maxChars = 1_000).length < 1_100)
    }

    @Test fun watchdogFramesAreFormattedAndCapped() {
        val frames = (1..50).map { "a.B.m$it(B.java:$it)" }
        val t = formatFrames(frames, max = 40)
        assertEquals(41, t.lines().size)
        assertTrue(t.startsWith("  at a.B.m1(B.java:1)"))
        assertTrue(t.endsWith("... 10 more"))
    }
}
