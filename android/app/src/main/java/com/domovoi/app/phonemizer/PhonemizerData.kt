package com.domovoi.app.phonemizer

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import java.io.InputStream
import java.nio.LongBuffer

/**
 * Where the converter's data files are, by their path under
 * `domovoi/phonemizer/data/` ("cmudict/cmudict.dict", "g2p/g2p.int8.onnx").
 * The app reads them from its assets (`phonemizer/`, copied from that
 * folder at build time and checked against its SHA256SUMS); the JVM tests
 * read the folder itself.
 */
fun interface PhonemizerFiles {
    fun open(path: String): InputStream

    companion object {
        fun folder(dir: File) = PhonemizerFiles { File(dir, it).inputStream() }
    }
}

/** The out-of-dictionary model's vocabulary (`g2p/g2p_vocab.json`). */
class G2pVocab(val letters: Map<Char, Long>, val phones: List<String>, val maxChars: Int)

/**
 * Everything the converter loads once: CMUdict with Domovoi's corrections
 * (only a word's first variant is ever used, so only that is kept), the
 * weak forms, the voices' phoneme id map, the shipped "say it like" list
 * and the model's vocabulary.
 */
class PhonemizerData(
    /** word -> its first pronunciation, ARPAbet separated by single spaces. */
    val lex: Map<String, String>,
    /** word -> (default, before a vowel, clause-final) weak forms. */
    val fw: Map<String, Triple<String, String, String>>,
    val idMap: Map<String, IntArray>,
    val defaultPronunciations: List<Pair<String, String>>,
    val vocab: G2pVocab,
) {
    companion object {
        const val CMUDICT = "cmudict/cmudict.dict"
        const val CORRECTIONS = "lexicon_corrections.txt"
        const val FUNCTION_WORDS = "function_words_en.tsv"
        const val PRONUNCIATIONS = "pronunciations_default.txt"
        const val ID_MAP = "phoneme_ids_en.json"
        const val G2P_MODEL = "g2p/g2p.int8.onnx"
        const val G2P_VOCAB = "g2p/g2p_vocab.json"

        private val VARIANT = Regex("""\(\p{Nd}+\)$""")

        /** The phones after the word, separated by single spaces (as
         *  str.split() would leave them). */
        private fun phonesOf(rest: String): String {
            var plain = rest.isNotEmpty() && !Py.isSpace(rest[0])
            if (plain) {
                for (i in 1 until rest.length) {
                    val c = rest[i]
                    if (Py.isSpace(c) && (c != ' ' || Py.isSpace(rest[i - 1]))) {
                        plain = false
                        break
                    }
                }
            }
            return if (plain) rest else Py.split(rest).joinToString(" ")
        }

        fun load(files: PhonemizerFiles): PhonemizerData {
            val lex = HashMap<String, String>(140_000)
            // 135,000 lines: parsed by hand (a word, then its phones), and a
            // later variant of a word already seen is skipped unread.
            files.open(CMUDICT).bufferedReader(Charsets.UTF_8).useLines { lines ->
                for (raw in lines) {
                    val hash = raw.indexOf('#')
                    val line = Py.strip(if (hash >= 0) raw.substring(0, hash) else raw)
                    if (line.isEmpty()) continue
                    val gap = line.indexOfFirst { Py.isSpace(it) }
                    val first = if (gap < 0) line else line.substring(0, gap)
                    val w = if (first.endsWith(')')) VARIANT.replace(first, "") else first
                    if (w in lex) continue
                    lex[w] = if (gap < 0) "" else phonesOf(line.substring(gap + 1))
                }
            }
            // The first line for a word in the corrections replaces every
            // CMUdict variant of it, so it is the word's first variant now.
            val corrected = HashSet<String>()
            files.open(CORRECTIONS).bufferedReader(Charsets.UTF_8).useLines { lines ->
                for (raw in lines) {
                    val line = Py.strip(raw)
                    if (line.isEmpty() || line.startsWith("#")) continue
                    val parts = Py.split(line)
                    if (corrected.add(parts[0])) lex[parts[0]] = parts.drop(1).joinToString(" ")
                }
            }
            val fw = HashMap<String, Triple<String, String, String>>()
            files.open(FUNCTION_WORDS).bufferedReader(Charsets.UTF_8).useLines { lines ->
                for (line in lines) {
                    if (line.startsWith("#") || Py.strip(line).isEmpty()) continue
                    val (w, d, v, fin) = line.split("\t")
                    fw[w] = Triple(d, if (v == "-") d else v, if (fin == "-") d else fin)
                }
            }
            val idJson = Json.parseToJsonElement(files.open(ID_MAP).use { it.readBytes().toString(Charsets.UTF_8) })
            val idMap = idJson.jsonObject.getValue("phoneme_id_map").jsonObject.mapValues { (_, v) ->
                v.jsonArray.map { it.jsonPrimitive.int }.toIntArray()
            }
            val defaults = Pronunciations.parseText(files.open(PRONUNCIATIONS).use { it.readBytes().toString(Charsets.UTF_8) })
            val vocabJson = Json.parseToJsonElement(files.open(G2P_VOCAB).use { it.readBytes().toString(Charsets.UTF_8) }).jsonObject
            val letters = vocabJson.getValue("letters").jsonPrimitive.content
            val vocab = G2pVocab(
                letters = letters.withIndex().associate { (i, c) -> c to (i + 1).toLong() },
                phones = vocabJson.getValue("phones").jsonArray.map { it.jsonPrimitive.content },
                maxChars = vocabJson.getValue("max_chars").jsonPrimitive.int,
            )
            return PhonemizerData(lex, fw, idMap, defaults, vocab)
        }
    }
}

/** The out-of-dictionary model: letter ids in, CTC logits out. */
interface G2pModel : AutoCloseable {
    /** [ids]: one id per letter. Returns logits, one row per output frame. */
    fun logits(ids: LongArray): Array<FloatArray>
}

/**
 * [G2pModel] on ONNX Runtime (the int8 `g2p.int8.onnx`), one thread, as the
 * Python converter runs it. Input `chars` int64 [1, T]; output `logits`
 * float [1, 3T, phones].
 */
class OrtG2pModel(modelBytes: ByteArray) : G2pModel {
    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession = OrtSession.SessionOptions().use { options ->
        options.setIntraOpNumThreads(1)
        options.setInterOpNumThreads(1)
        env.createSession(modelBytes, options)
    }
    private val input = session.inputNames.first()

    override fun logits(ids: LongArray): Array<FloatArray> {
        OnnxTensor.createTensor(env, LongBuffer.wrap(ids), longArrayOf(1, ids.size.toLong())).use { tensor ->
            session.run(mapOf(input to tensor)).use { result ->
                @Suppress("UNCHECKED_CAST")
                val out = result[0].value as Array<Array<FloatArray>>
                return out[0]
            }
        }
    }

    override fun close() {
        session.close()
    }

    companion object {
        fun load(files: PhonemizerFiles): OrtG2pModel =
            OrtG2pModel(files.open(PhonemizerData.G2P_MODEL).use { it.readBytes() })
    }
}
