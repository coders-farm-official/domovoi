package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BenchReportTest {

    private val device = DeviceFacts(
        manufacturer = "Acme", model = "Phone 9", soc = "Acme Chip 3", android = "16", api = 36,
        totalRamBytes = 12_000_000_000, lowRamDevice = false, abi = "arm64-v8a", app = "1.0.0 (debug build)",
    )

    @Test fun procStatusGivesPeakAndCurrentResidentMemory() {
        val status = "Name:\tcom.domovoi.app\nVmPeak:\t 9000000 kB\nVmHWM:\t 2345678 kB\nVmRSS:\t  1234567 kB\nThreads:\t80\n"
        assertEquals(2_345_678L to 1_234_567L, parseProcStatus(status))
        assertEquals(null to null, parseProcStatus("Name:\tx\n"))
    }

    @Test fun medianOfOddEvenAndEmpty() {
        assertEquals(2.0, median(listOf(3.0, 1.0, 2.0))!!, 0.0)
        assertEquals(2.5, median(listOf(4.0, 1.0, 2.0, 3.0))!!, 0.0)
        assertNull(median(emptyList()))
    }

    @Test fun heatSummaryComparesTheFirstAndLastMinute() {
        val samples = listOf(
            HeatSample(20.0, 14.0, 30.0, 0, true),
            HeatSample(50.0, 12.0, 31.0, 1, true),
            HeatSample(300.0, 9.0, 38.0, 2, false),
            HeatSample(570.0, 7.0, 41.0, 3, false),
            HeatSample(600.0, 6.0, 42.0, 2, false),
        )
        val s = summarizeHeat(samples)
        assertEquals(5, s.turns)
        assertEquals(10.0, s.minutes, 1e-9)
        assertEquals(13.0, s.firstMinuteTps!!, 1e-9)
        assertEquals(6.5, s.lastMinuteTps!!, 1e-9)
        assertEquals(6.0, s.minTps!!, 0.0)
        assertEquals(30.0, s.startTempC!!, 0.0)
        assertEquals(42.0, s.endTempC!!, 0.0)
        assertEquals(3, s.worstThermalStatus)
        assertEquals(3, s.offlineTurns)
        assertEquals("severe", thermalName(3))
    }

    @Test fun theReportCarriesEveryGateNumber() {
        val stats = LlmStats(0.9, 120, 48, 210.0, 11.2)
        val results = BenchResults(
            loads = listOf(LoadResult(LlmBackend.CPU, 6_400, firstInProcess = true)),
            textTurns = listOf(
                TextTurnResult("Why is the sky blue?", 900, 4_200, stats, "Sunlight scatters."),
                TextTurnResult("Bread?", 1_100, 3_900, stats.copy(decodeTokensPerS = 10.0), "Wrap it."),
            ),
            voiceTurns = listOf(
                VoiceTurnResult("microphone", "Moonshine tiny (English)", 1.8, 500, "how tall is everest",
                    sttMs = 180, llmFirstTextMs = 700, firstSentenceMs = 1_100, ttsFirstAudioMs = 300,
                    toFirstAudioMs = 1_580, reply = "About 8,849 metres."),
                VoiceTurnResult("microphone", "Moonshine tiny (English)", 0.4, 500, "", 90, null, null, null, null, "",
                    error = "nothing recognizable was heard"),
            ),
            audioIn = listOf(AudioInResult("spoken by the phone", "Answer the question in this audio.", 1_400, 3_000, "Everest.", stats)),
            heat = HeatResult(10, listOf(HeatSample(30.0, 11.0, 31.0, 0, true), HeatSample(600.0, 8.0, 40.0, 2, false)), false),
            memory = listOf(MemoryReading("model loaded (CPU)", 1_900_000, 1_700_000, 5_000_000_000)),
            traffic = listOf(TrafficReading("before", 1_000, 500, true), TrafficReading("text test", 1_000, 500, false)),
            notes = listOf("voice test stopped"),
        )
        val text = renderBenchReport(device, results, "2026-10-12 09:30")

        listOf(
            "Acme Phone 9, Acme Chip 3",
            "Android 16 (API 36), arm64-v8a, RAM 12.0 GB",
            "CPU: 6400 ms (first in this app run)",
            "median first text: 1000 ms",
            "median decode: 10.6 tokens/s",
            "end of speech to first word 1580 ms (2080 ms counting the 500 ms silence wait)",
            "speech to text 180 ms for 1.8 s of audio",
            "failed: nothing recognizable was heard",
            "first text 1400 ms",
            "decode first minute 11.0 t/s, last minute 8.0 t/s",
            "worst thermal status moderate",
            "1 of 2 replies ran with no network",
            "model loaded (CPU): peak 1855 MB",
            "text test: 0 B in, 0 B out (no network)",
            "voice test stopped",
        ).forEach { assertTrue("missing: $it\n---\n$text", text.contains(it)) }
    }

    @Test fun anEmptyRunRendersJustTheHeader() {
        val text = renderBenchReport(device, BenchResults(), "now")
        assertTrue(BenchResults().isEmpty)
        assertFalse(text.contains("TEXT TURNS"))
        assertTrue(text.startsWith("Domovoi on-device assistant benchmark"))
    }
}
