package com.domovoi.app.phonemizer

import com.domovoi.app.assistant.VoiceSamples
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.AfterClass
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.BeforeClass
import org.junit.Test
import java.io.File
import java.security.MessageDigest

/**
 * The Kotlin converter against the fixture the Python one is held to:
 * `domovoi/phonemizer/golden.json`, read from the repo (the Gradle test
 * task passes its folder as `domovoi.phonemizer.dir`), with the data files
 * from `domovoi/phonemizer/data/` itself. Every case must match exactly;
 * the fixture's `near_tie` cases alone may differ by one phoneme of the
 * model's output (another ONNX Runtime may pick the other side of a near
 * tie). A failure here is a difference in what the phone's voice says from
 * what the server's says: fix the port, never the fixture.
 */
class PhonemizerGoldenTest {

    companion object {
        lateinit var dir: File
        lateinit var golden: JsonObject
        lateinit var data: PhonemizerData
        lateinit var model: OrtG2pModel
        lateinit var ph: Phonemizer

        /** The repo's domovoi/phonemizer/, from the Gradle property or by
         *  walking up from the working directory. */
        fun phonemizerDir(): File {
            System.getProperty("domovoi.phonemizer.dir")?.let { return File(it) }
            var d: File? = File("").absoluteFile
            while (d != null) {
                val f = File(d, "domovoi/phonemizer/golden.json")
                if (f.isFile) return f.parentFile
                d = d.parentFile
            }
            error("domovoi/phonemizer/golden.json not found")
        }

        @BeforeClass @JvmStatic fun load() {
            dir = phonemizerDir()
            golden = Json.parseToJsonElement(File(dir, "golden.json").readText(Charsets.UTF_8)).jsonObject
            val files = PhonemizerFiles.folder(File(dir, "data"))
            data = PhonemizerData.load(files)
            model = OrtG2pModel.load(files)
            ph = Phonemizer(data, model)
        }

        @AfterClass @JvmStatic fun close() {
            model.close()
        }

        fun sha256(f: File): String =
            MessageDigest.getInstance("SHA-256").digest(f.readBytes()).joinToString("") { "%02x".format(it) }
    }

    private val cases: List<JsonObject> get() = golden.getValue("cases").jsonArray.map { it.jsonObject }

    private fun JsonElement.strings(): List<String> = jsonArray.map { it.jsonPrimitive.content }

    private fun JsonElement.idLists(): List<List<Int>> = jsonArray.map { l -> l.jsonArray.map { it.jsonPrimitive.int } }

    /**
     * Cases whose one difference is a near tie of the model that the fixture
     * does not mark: scripts/g2p/make_golden.py records `near_tie` for the
     * model words of a case's text, not for the syllables of a "say it like"
     * respelling, which go through the same model. "shiv" (Siobhan =
     * shiv-AWN) is IY1 17.983 against IH1 17.937 on ONNX Runtime 1.24.4,
     * 0.046 apart; ONNX Runtime 1.28.0 here picks IH1. Reported as a fixture
     * gap, not patched here. Each entry is checked to really be within the
     * fixture's margin and one phone of the fixture's answer.
     */
    private val unmarkedNearTies = mapOf("say_it_like-004" to "shiv")

    /** The smallest gap, over the model's frames, between the phone it
     *  picks and the runner-up. */
    private fun modelMargin(word: String): Float {
        val ids = word.mapNotNull { data.vocab.letters[it] }.take(data.vocab.maxChars).toLongArray()
        return model.logits(ids).minOf { f -> f.sortedArrayDescending().let { it[0] - it[1] } }
    }

    private fun withinUnmarkedNearTie(case: JsonObject, id: String, p: Phonemizer): Boolean {
        val word = unmarkedNearTies[id] ?: return false
        val margin = golden.getValue("near_tie_margin").jsonPrimitive.content.toFloat()
        if (modelMargin(word) >= margin) return false
        val want = case.getValue("resolved").jsonObject.mapValues { it.value.jsonPrimitive.content.split(' ') }
        val got = p.pronunciations().mapValues { it.value.resolved.split(' ') }
        return want.keys == got.keys && want.all { (k, v) -> distance(v, got.getValue(k)) <= 1 }
    }

    /** Levenshtein distance over phone tokens. */
    private fun distance(a: List<String>, b: List<String>): Int {
        val d = Array(a.size + 1) { IntArray(b.size + 1) }
        for (i in 0..a.size) d[i][0] = i
        for (j in 0..b.size) d[0][j] = j
        for (i in 1..a.size) for (j in 1..b.size) {
            d[i][j] = minOf(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + if (a[i - 1] == b[j - 1]) 0 else 1)
        }
        return d[a.size][b.size]
    }

    /**
     * The fixture's allowance for a near-tie case that did not match
     * exactly: the only differences are in the model's output for the
     * case's near-tie words, each at most one phone from the fixture's.
     */
    private fun withinNearTie(case: JsonObject, p: Phonemizer): Boolean {
        val nearTie = case["near_tie"]?.strings()?.toSet() ?: return false
        val want = case["model"]?.jsonArray?.associate { e ->
            e.jsonArray[0].jsonPrimitive.content to e.jsonArray[1].jsonPrimitive.content.split(' ')
        } ?: return false
        val got = p.phonemizeDetailed(case.getValue("text").jsonPrimitive.content)
            .flatMap { it.second }.filter { it.src == "model" }.associate { it.w!! to it.ph }
        if (got.keys != want.keys) return false
        return want.all { (w, phones) ->
            val mine = got.getValue(w)
            if (w in nearTie) distance(mine, phones) <= 1 else mine == phones
        }
    }

    @Test fun theFixtureWasMadeFromTheseDataFiles() {
        val want = golden.getValue("data_sha256").jsonObject
        assertTrue(want.size >= 7)
        for ((rel, hash) in want) {
            assertEquals("$rel changed since golden.json was made", hash.jsonPrimitive.content, sha256(File(dir, "data/$rel")))
        }
    }

    /** The build copies what SHA256SUMS lists into the app's assets after
     *  checking each hash; the list covers every file the converter reads. */
    @Test fun sha256sumsCoversEveryDataFileTheConverterReads() {
        val sums = File(dir, "data/SHA256SUMS").readLines().map { it.trim() }
            .filter { it.isNotEmpty() && !it.startsWith("#") }
            .associate { line -> line.split(Regex("""\s+"""), limit = 2).let { it[1].removePrefix("*") to it[0] } }
        listOf(
            PhonemizerData.CMUDICT, PhonemizerData.CORRECTIONS, PhonemizerData.FUNCTION_WORDS,
            PhonemizerData.PRONUNCIATIONS, PhonemizerData.ID_MAP, PhonemizerData.G2P_MODEL, PhonemizerData.G2P_VOCAB,
            "cmudict/LICENSE",
        ).forEach { rel ->
            assertEquals("SHA256SUMS: $rel", sums[rel], sha256(File(dir, "data/$rel")))
        }
        assertEquals(
            "the model the brief names",
            "b13e610b90dda492fa85581d9d2ceaf3be33f0ce43d2519cdd7ae0e34a74e543", sums[PhonemizerData.G2P_MODEL],
        )
    }

    @Test fun theDefaultListIsTheShippedOne() {
        val want = golden.getValue("default_pronunciations").jsonArray.map { it.strings().let { (w, s) -> w to s } }
        assertEquals(want, data.defaultPronunciations)
        assertTrue(ph.hasModel)
    }

    @Test fun everyCase() {
        val all = cases
        assertEquals(golden.getValue("counts").jsonObject.values.sumOf { it.jsonPrimitive.int }, all.size)
        val failures = mutableListOf<String>()
        var exact = 0
        val allowed = mutableListOf<String>()
        for (case in all) {
            val id = case.getValue("id").jsonPrimitive.content
            val text = case.getValue("text").jsonPrimitive.content
            val want = case.getValue("phonemes").strings()
            val p = case["pronunciations"]?.let { list ->
                Phonemizer(data, model, list.jsonArray.map { e -> e.strings().let { (w, s) -> w to s } })
            } ?: ph
            val got = p.phonemize(text)
            val gotResolved = p.pronunciations().mapValues { it.value.resolved }
            val wantResolved = case["resolved"]?.jsonObject?.mapValues { it.value.jsonPrimitive.content }
            val resolvedOk = wantResolved == null || wantResolved == gotResolved
            if (got == want && resolvedOk) {
                exact++
                val ids = got.map { PhonemeIds.ids(it, data.idMap).toList() }
                if (ids != case.getValue("ids").idLists()) failures += "$id ids differ for $got"
            } else if (withinNearTie(case, p) || withinUnmarkedNearTie(case, id, p)) {
                allowed += "$id: want $want, got $got" + if (resolvedOk) "" else " (resolved $gotResolved)"
            } else {
                failures += "$id $text\n    want $want\n    got  $got" +
                    if (resolvedOk) "" else "\n    resolved $gotResolved, want $wantResolved"
            }
        }
        println("golden.json: ${all.size} cases, $exact exact, ${allowed.size} within the near-tie allowance, ${failures.size} failed")
        allowed.forEach { println("  near tie: $it") }
        if (failures.isNotEmpty()) fail("${failures.size} of ${all.size} cases differ:\n" + failures.joinToString("\n"))
    }

    @Test fun invalidEntriesAreRefused() {
        val invalid = golden.getValue("invalid_say_it_like").jsonArray
        assertTrue(invalid.size >= 5)
        for (e in invalid) {
            val (word, say) = (e as JsonArray).strings()
            try {
                ph.resolvePronunciation(word, say)
                fail("accepted [$word = $say]")
            } catch (_: PronunciationException) {
            }
        }
    }

    @Test fun aBadListLeavesTheOldOneInForce() {
        val p = Phonemizer(data, model, listOf("Nguyen" to "win"))
        try {
            p.setPronunciations(listOf("Domovoi" to "DOH-moe-voy", "Bob" to "b@b"))
            fail("a bad entry was taken")
        } catch (e: PronunciationException) {
            assertTrue(e.message!!.startsWith("Bob:"))
        }
        assertEquals(setOf("nguyen"), p.pronunciations().keys)
    }

    @Test fun theIdsAreTheVoicesOwn() {
        val meta = Json.parseToJsonElement(File(dir, "data/phoneme_ids_en.json").readText()).jsonObject
        val voices = meta.getValue("voice_configs_sha256").jsonObject.keys.map { it.removeSuffix(".onnx.json") }.toSet()
        val ids = golden.getValue("ids").jsonObject
        assertEquals(voices, setOf(ids.getValue("voice").jsonPrimitive.content) + ids.getValue("same_for").strings())
    }

    /** The benchmark's voice samples are the fixture's 13 comparison sentences. */
    @Test fun theVoiceSamplesAreTheFixturesSentences() {
        val sentences = cases.filter { it.getValue("group").jsonPrimitive.content == "sentences" }
            .map { it.getValue("text").jsonPrimitive.content }
        assertEquals(sentences, VoiceSamples.LINES)
    }
}
