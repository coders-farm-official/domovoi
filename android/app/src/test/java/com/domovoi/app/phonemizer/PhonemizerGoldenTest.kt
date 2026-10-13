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
 * what the server's says: fix the port, never the fixture. Every text is
 * there once per phoneme set (a case's `dialect`: "en-us"; none: "en").
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

    /** The case's phoneme set: its `dialect`, or "en". */
    private val JsonObject.dialect: String get() = this["dialect"]?.jsonPrimitive?.content ?: Phonemizer.EN

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
     * The fixture's allowance, for a case the fixture marks `near_tie` only,
     * that did not match exactly: the only differences are in the model's
     * output for the case's near-tie words, each at most one phone from the
     * fixture's. A near tie in a "say it like" respelling (`respelling_model`,
     * say_it_like-010's "kamren") is held to the rule the Python test uses:
     * the same entries, each at most one phone per tie from the fixture's,
     * and the sentences within two characters per tie.
     */
    private fun withinNearTie(case: JsonObject, p: Phonemizer): Boolean {
        val nearTie = case["near_tie"]?.strings()?.toSet() ?: return false
        if (nearTie.isEmpty()) return false
        val respelled = case["respelling_model"]?.jsonArray?.map { it.jsonArray[0].jsonPrimitive.content }.orEmpty()
        if (nearTie.any { it in respelled }) {
            val want = case.getValue("resolved").jsonObject.mapValues { it.value.jsonPrimitive.content.split(' ') }
            val got = p.pronunciations().mapValues { it.value.resolved.split(' ') }
            if (want.keys != got.keys || !want.all { (k, v) -> distance(v, got.getValue(k)) <= nearTie.size }) return false
            val wantText = case.getValue("phonemes").strings()
            val gotText = p.phonemize(case.getValue("text").jsonPrimitive.content, case.dialect)
            return wantText.size == gotText.size &&
                distance(wantText.joinToString(" | ").map { it.toString() }, gotText.joinToString(" | ").map { it.toString() }) <= 2 * nearTie.size
        }
        val want = case["model"]?.jsonArray?.associate { e ->
            e.jsonArray[0].jsonPrimitive.content to e.jsonArray[1].jsonPrimitive.content.split(' ')
        } ?: return false
        val got = p.phonemizeDetailed(case.getValue("text").jsonPrimitive.content, case.dialect)
            .flatMap { it.second }.filter { it.src == "model" }.associate { it.w!! to it.ph }
        if (got.keys != want.keys) return false
        return want.all { (w, phones) ->
            val mine = got.getValue(w)
            if (w in nearTie) distance(mine, phones) <= 1 else mine == phones
        }
    }

    @Test fun theFixtureWasMadeFromTheseDataFiles() {
        val want = golden.getValue("data_sha256").jsonObject
        assertTrue(want.size >= 8)
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
            PhonemizerData.CMUDICT, PhonemizerData.CORRECTIONS, PhonemizerData.FUNCTION_WORDS, PhonemizerData.FUNCTION_WORDS_US,
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
        val counts = golden.getValue("counts").jsonObject
        assertEquals(Phonemizer.DIALECTS.toSet(), counts.keys)
        assertEquals(counts.values.sumOf { set -> set.jsonObject.values.sumOf { it.jsonPrimitive.int } }, all.size)
        for (d in Phonemizer.DIALECTS) {
            assertEquals(d, counts.getValue(d).jsonObject.values.sumOf { it.jsonPrimitive.int }, all.count { it.dialect == d })
        }
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
            val got = p.phonemize(text, case.dialect)
            val gotResolved = p.pronunciations().mapValues { it.value.resolved }
            val wantResolved = case["resolved"]?.jsonObject?.mapValues { it.value.jsonPrimitive.content }
            val resolvedOk = wantResolved == null || wantResolved == gotResolved
            if (got == want && resolvedOk) {
                exact++
                val ids = got.map { PhonemeIds.ids(it, data.idMap).toList() }
                if (ids != case.getValue("ids").idLists()) failures += "$id ids differ for $got"
            } else if (withinNearTie(case, p)) {
                allowed += "$id: want $want, got $got" + if (resolvedOk) "" else " (resolved $gotResolved)"
            } else {
                failures += "$id $text\n    want $want\n    got  $got" +
                    if (resolvedOk) "" else "\n    resolved $gotResolved, want $wantResolved"
            }
        }
        println("golden.json: ${all.size} cases (${Phonemizer.DIALECTS.joinToString { d -> "$d ${all.count { it.dialect == d }}" }}), " +
            "$exact exact, ${allowed.size} within the near-tie allowance, ${failures.size} failed")
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

    /** The sets the fixture names are the ones the port writes, by the
     *  name a voice's .onnx.json gives them. */
    @Test fun thePhonemeSetsAreTheConvertersOwn() {
        val sets = golden.getValue("dialects").jsonObject
        assertEquals(Phonemizer.DIALECTS.toSet(), sets.keys)
        for ((name, set) in sets) {
            val espeak = set.jsonObject.getValue("espeak_voice").jsonPrimitive.content
            assertEquals(name, Phonemizer.dialectForEspeakVoice(espeak))
        }
        assertEquals("en-us", Phonemizer.dialectForEspeakVoice("EN_us"))
        assertEquals(null, Phonemizer.dialectForEspeakVoice("de"))
        assertEquals(PhonemizerData.FUNCTION_WORDS_US, sets.getValue("en-us").jsonObject.getValue("function_words").jsonPrimitive.content)
    }

    @Test fun theIdsAreTheVoicesOwn() {
        val meta = Json.parseToJsonElement(File(dir, "data/phoneme_ids_en.json").readText()).jsonObject
        val voices = meta.getValue("voice_configs_sha256").jsonObject.keys.map { it.removeSuffix(".onnx.json") }.toSet()
        val ids = golden.getValue("ids").jsonObject
        assertEquals(voices, setOf(ids.getValue("voice").jsonPrimitive.content) + ids.getValue("same_for").strings())
    }

    /** A syllable of a "say it like" entry the model reads is listed and
     *  marked like any word of the text (say_it_like-010: "kamren"), so the
     *  allowance above needs no list of its own. Since the respelling key
     *  (2026-10-12) "shiv" (say_it_like-004) is read by the key, the same
     *  on every runtime: no near tie. */
    @Test fun nearTiesInRespellingsAreMarkedInTheFixture() {
        val case = cases.first { it.getValue("id").jsonPrimitive.content == "say_it_like-010" }
        assertEquals("kamren", case.getValue("respelling_model").jsonArray[0].jsonArray[0].jsonPrimitive.content)
        val siobhan = cases.first { it.getValue("id").jsonPrimitive.content == "say_it_like-004" }
        assertTrue("near_tie" !in siobhan && "respelling_model" !in siobhan)
        assertTrue("shiv" !in golden.getValue("near_tie_words").strings())
    }

    /** The respelling key on the same syllables as test_phonemizer.py's
     *  test_the_respelling_key, and Kamron's name (test_kamron). */
    @Test fun theRespellingKey() {
        val key = mapOf(
            "shuh" to "SH AH1", "zhahn" to "ZH AA1 N", "dhuh" to "DH AH1", "tsuh" to "T S AH1",
            "shiv" to "SH IH1 V", "voy" to "V OY1", "awn" to "AO1 N", "eer" to "IH1 R", "zu" to "Z UW1",
            "kowt" to "K AW1 T", "bair" to "B EH1 R", "chess" to "CH EH1 S", "yoo" to "Y UW1", "nyew" to "N Y UW1",
        )
        for ((syllable, arpa) in key) assertEquals(syllable, arpa, Pronunciations.respellArpa(syllable)?.joinToString(" "))
        for (syllable in listOf("", "my", "shh", "boat'", "two-", "naive", "aeiou", "é")) {
            assertEquals(syllable, null, Pronunciations.respellArpa(syllable))
        }
        assertEquals(listOf("R", "EH1", "N") to "lex", ph.respellingArpa("ren"))
        assertEquals("lex", ph.respellingArpa("ts").second)
        val kamron = mapOf(
            "KAM-ren" to ("K AE1 M R AH0 N" to "kˈamɹən."), "Kam-ren" to ("K AE1 M R AH0 N" to "kˈamɹən."),
            "KAM-run" to ("K AE1 M R AH0 N" to "kˈamɹən."), "kamren" to ("K AE1 M ER0 AH0 N" to "kˈaməɹən."),
            "kam-REN" to ("K AE2 M R EH1 N" to "kamɹˈɛn."),
        )
        for ((say, want) in kamron) {
            val p = Phonemizer(data, model, listOf("Kamron" to say))
            assertEquals(say, want.first, p.pronunciations().getValue("kamron").resolved)
            assertEquals(say, listOf(want.second), p.phonemize("Kamron."))
        }
        fun resolved(word: String, say: String) =
            Phonemizer(data, model, listOf(word to say)).pronunciations().values.single().resolved
        assertEquals("K AE1 M ER0 AH0 N", resolved("Cameron", "CAM-er-on"))
        assertEquals("SH AE1 R AH0 N", resolved("Sharon", "SHA-ron"))
        assertEquals("K AE2 M ER1 AA2 N", resolved("Cameron", "cam-ER-on"))
        assertEquals("JH EH2 N IH1 F ER0", resolved("Jennifer", "jen-NIF-er"))
    }

    /** normalize.py's long-number pass (review 2026-10-12): more than 30
     *  digits are read digit by digit before anything else; 30 and fewer as
     *  before (test_phonemizer.py, the same cases). */
    @Test fun aLongNumberIsReadDigitByDigit() {
        val ws = Regex("""\s+""")
        assertEquals(List(31) { "seven" }, Normalize.prenormalize("7".repeat(31)).trim().split(ws))
        assertEquals("dollars", Normalize.prenormalize("\$" + "1".repeat(18)).trim().split(ws).last())
        // read before the money pattern sees it, as normalize.py does
        assertEquals(listOf("\$") + List(31) { "one" }, Normalize.prenormalize("\$" + "1".repeat(31)).trim().split(ws))
        assertEquals(4400, Regex("wˈɒn").findAll(ph.phonemize("1".repeat(4400))[0]).count())
    }

    @Test fun aVeryLongNumberIsQuickAndNeverThrows() {
        val texts = listOf(
            "1".repeat(16000), "1".repeat(16000) + "%", "1".repeat(16000) + " km", "1".repeat(16000) + "°C",
            "x " + "1".repeat(4400), "\$" + "1".repeat(5000), "3." + "1".repeat(16000), "1".repeat(16000) + ".5",
            "1".repeat(16000) + "th", "1,".repeat(8000),
        )
        for (text in texts) {
            val t0 = System.nanoTime()
            val words = Normalize.prenormalize(text)
            val ms = (System.nanoTime() - t0) / 1_000_000
            assertTrue("${text.take(12)}... took $ms ms", ms < 2000)
            assertTrue(words.isNotBlank())
        }
    }

    /** The benchmark's voice samples are the fixture's 13 comparison sentences. */
    @Test fun theVoiceSamplesAreTheFixturesSentences() {
        val sentences = cases.filter { it.getValue("group").jsonPrimitive.content == "sentences" && it.dialect == Phonemizer.EN }
            .map { it.getValue("text").jsonPrimitive.content }
        assertEquals(sentences, VoiceSamples.LINES)
    }
}
