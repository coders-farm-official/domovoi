package com.domovoi.app.assistant.drafts

/**
 * What changed between two versions of a draft, the twin of the server's
 * `domovoi/drafts/diff.py`: a word-level Myers diff (our own, no library)
 * over the draft as it reads ([displayText]), and the one line a revision's
 * reply says ("cut 120 words (380 → 260)").
 */
object DraftDiff {
    /** Past this many tokens on both sides the diff doesn't look for words
     *  in common inside the changed stretch (diff.py's MAX_TOKENS). */
    const val MAX_TOKENS = 40000
    /** The most work the word diff does before it settles for the plain
     *  view: the tokens both versions start and end with kept, the stretch
     *  between removed then added. The same budget as diff.py's
     *  WORK_BUDGET, so phone and server show the same changes; it keeps
     *  two unrelated long drafts from taking seconds and gigabytes. */
    const val WORK_BUDGET = 300_000
    // Whitespace as Python's \s reads it (DraftSpace).
    private val TOKEN = DraftSpace.TOKENS

    /** The draft as plain text, as the changes view shows it. */
    fun displayText(markdown: String): String {
        val out = StringBuilder()
        var prev: String? = null
        for (b in DraftBlocks.parse(markdown)) {
            val text = b.lines.joinToString("\n") { if (b.style == "bullet") "• $it" else it }
            if (out.isNotEmpty()) out.append(if (b.style == "bullet" && prev == "bullet") "\n" else "\n\n")
            out.append(text)
            prev = b.style
        }
        return out.toString()
    }

    private fun isSpace(t: String) = t.isNotEmpty() && t.all { it.isWhitespace() }

    fun tokens(text: String): List<String> = TOKEN.findAll(text).map { m ->
        val t = m.value
        if (isSpace(t)) {
            when (t.count { it == '\n' }) {
                0 -> " "
                1 -> "\n"
                else -> "\n\n"
            }
        } else {
            t
        }
    }.toList()

    /** The diff past its budget: the tokens both sides start and end with
     *  kept, everything between shown as removed then added. */
    private fun replaced(a: List<String>, b: List<String>): List<Pair<String, String>> {
        val n = a.size
        val m = b.size
        var p = 0
        while (p < n && p < m && a[p] == b[p]) p++
        var s = 0
        while (s < n - p && s < m - p && a[n - 1 - s] == b[m - 1 - s]) s++
        return listOf(
            "=" to a.subList(0, p), "-" to a.subList(p, n - s),
            "+" to b.subList(p, m - s), "=" to a.subList(n - s, n),
        ).filter { it.second.isNotEmpty() }.map { it.first to it.second.joinToString("") }
    }

    /** Myers' greedy diff, as the server's: each step keeps only the
     *  diagonals it can have reached, and the work (a diagonal tried or a
     *  token compared, counted exactly as diff.py does) stops at
     *  [WORK_BUDGET], past which it is [replaced]. */
    private fun myers(a: List<String>, b: List<String>): List<Pair<String, String>> {
        val n = a.size
        val m = b.size
        if (n + m == 0) return emptyList()
        if (n + m > MAX_TOKENS) return replaced(a, b)
        // Step d costs at least d + 1 work, so the budget caps d as well.
        val maxd = minOf(n + m, Math.sqrt(2.0 * WORK_BUDGET).toInt() + 1)
        val off = maxd + 1
        val v = IntArray(2 * maxd + 3)
        val trace = mutableListOf<IntArray>()
        var work = 0L
        var done = false
        for (d in 0..maxd) {
            trace += v.copyOfRange(off - d - 1, off + d + 2)
            var k = -d
            while (k <= d) {
                var x = if (k == -d || (k != d && v[off + k - 1] < v[off + k + 1])) v[off + k + 1] else v[off + k - 1] + 1
                var y = x - k
                val x0 = x
                while (x < n && y < m && a[x] == b[y]) {
                    x++
                    y++
                }
                v[off + k] = x
                work += 1 + x - x0
                if (x >= n && y >= m) {
                    done = true
                    break
                }
                if (work > WORK_BUDGET) return replaced(a, b)
                k += 2
            }
            if (done) break
        }
        if (!done) return replaced(a, b)
        // Walk back through the trace (step d's slice starts at diagonal -d-1).
        val ops = mutableListOf<Pair<String, String>>()
        var x = n
        var y = m
        for (d in trace.indices.reversed()) {
            val vv = trace[d]
            val o = d + 1
            val k = x - y
            val pk = if (k == -d || (k != d && vv[o + k - 1] < vv[o + k + 1])) k + 1 else k - 1
            val px = vv[o + pk]
            val py = px - pk
            while (x > px && y > py) {
                ops += "=" to a[x - 1]
                x--
                y--
            }
            if (d > 0) {
                if (x == px) {
                    ops += "+" to b[y - 1]
                    y--
                } else {
                    ops += "-" to a[x - 1]
                    x--
                }
            }
        }
        ops.reverse()
        return ops
    }

    /** `[[op, text], …]` turning [old] into [new] (both [displayText]):
     *  "=" kept, "+" added, "-" removed; runs of one op joined (each run
     *  built once: adding token by token would be quadratic). */
    fun diff(old: String, new: String): List<Pair<String, String>> {
        val ops = mutableListOf<String>()
        val texts = mutableListOf<StringBuilder>()
        for ((op, t) in myers(tokens(old), tokens(new))) {
            if (ops.isNotEmpty() && ops.last() == op) texts.last().append(t)
            else {
                ops += op
                texts += StringBuilder(t)
            }
        }
        return ops.indices.map { ops[it] to texts[it].toString() }
    }

    private fun words(text: String): Int = tokens(text).count { !isSpace(it) }

    /** The one line a revision's reply says about what changed. */
    fun changeLine(oldMarkdown: String, newMarkdown: String): String {
        val old = displayText(oldMarkdown)
        val new = displayText(newMarkdown)
        val ow = words(old)
        val nw = words(new)
        val ops = diff(old, new)
        val added = ops.filter { it.first == "+" }.sumOf { words(it.second) }
        val removed = ops.filter { it.first == "-" }.sumOf { words(it.second) }
        if (added == 0 && removed == 0) return "no changes"
        val net = nw - ow
        val small = maxOf(5, Math.floorDiv(ow, 10))
        fun w(n: Int) = "word${if (n == 1) "" else "s"}"
        return when {
            net < 0 && -net >= small -> "cut ${-net} ${w(-net)} ($ow → $nw)"
            net > 0 && net >= small -> "added $net ${w(net)} ($ow → $nw)"
            else -> {
                val changed = maxOf(added, removed)
                "changed $changed ${w(changed)} ($ow → $nw)"
            }
        }
    }
}
