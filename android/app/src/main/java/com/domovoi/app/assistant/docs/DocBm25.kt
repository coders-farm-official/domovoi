package com.domovoi.app.assistant.docs

import kotlin.math.ln
import kotlin.math.roundToLong

/**
 * BM25 over a document's passages (our own; the server's twin is
 * `domovoi/doc_qa/bm25.py`): k1 = 1.2, b = 0.75, the "plus one" idf. Scores
 * are rounded to 6 places, as the server's are, so ties break the same way.
 */
object DocBm25 {
    private const val K1 = 1.2
    private const val B = 0.75

    fun scores(passages: List<Passage>, queryTerms: List<String>): List<Double> {
        val n = passages.size
        if (n == 0) return emptyList()
        val q = queryTerms.filter { it.isNotEmpty() }.distinct()
        if (q.isEmpty()) return List(n) { 0.0 }
        val lengths = passages.map { it.terms.size }
        val avg = (lengths.sum().toDouble() / n).takeIf { it != 0.0 } ?: 1.0
        val df = HashMap<String, Int>()
        val tfs = passages.map { p ->
            val tf = HashMap<String, Int>()
            p.terms.forEach { tf[it] = (tf[it] ?: 0) + 1 }
            q.forEach { t -> if (t in tf) df[t] = (df[t] ?: 0) + 1 }
            tf
        }
        return passages.indices.map { i ->
            val tf = tfs[i]
            var s = 0.0
            for (t in q) {
                val f = tf[t] ?: 0
                if (f == 0) continue
                val d = df[t] ?: 0
                val idf = ln(1.0 + (n - d + 0.5) / (d + 0.5))
                s += idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * lengths[i] / avg))
            }
            round(s, 6)
        }
    }

    internal fun round(x: Double, places: Int): Double {
        var m = 1.0
        repeat(places) { m *= 10 }
        return (x * m).roundToLong() / m
    }
}
