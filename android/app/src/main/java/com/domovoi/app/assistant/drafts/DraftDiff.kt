package com.domovoi.app.assistant.drafts

/**
 * What changed between two versions of a draft, the twin of the server's
 * `domovoi/drafts/diff.py`: a word-level Myers diff (our own, no library)
 * over the draft as it reads ([displayText]), and the one line a revision's
 * reply says ("cut 120 words (380 → 260)").
 */
object DraftDiff {
    const val MAX_TOKENS = 40000
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

    private fun myers(a: List<String>, b: List<String>): List<Pair<String, String>> {
        val n = a.size
        val m = b.size
        if (n + m == 0) return emptyList()
        if (n + m > MAX_TOKENS) return a.map { "-" to it } + b.map { "+" to it }
        val max = n + m
        val off = max
        var v = IntArray(2 * max + 2)
        val trace = mutableListOf<IntArray>()
        var done = false
        for (d in 0..max) {
            trace += v.copyOf()
            var k = -d
            while (k <= d) {
                var x = if (k == -d || (k != d && v[off + k - 1] < v[off + k + 1])) v[off + k + 1] else v[off + k - 1] + 1
                var y = x - k
                while (x < n && y < m && a[x] == b[y]) {
                    x++
                    y++
                }
                v[off + k] = x
                if (x >= n && y >= m) {
                    done = true
                    break
                }
                k += 2
            }
            if (done) break
        }
        val ops = mutableListOf<Pair<String, String>>()
        var x = n
        var y = m
        for (d in trace.indices.reversed()) {
            v = trace[d]
            val k = x - y
            val pk = if (k == -d || (k != d && v[off + k - 1] < v[off + k + 1])) k + 1 else k - 1
            val px = v[off + pk]
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
     *  "=" kept, "+" added, "-" removed; runs of one op joined. */
    fun diff(old: String, new: String): List<Pair<String, String>> {
        val out = mutableListOf<Pair<String, String>>()
        for ((op, t) in myers(tokens(old), tokens(new))) {
            if (out.isNotEmpty() && out.last().first == op) out[out.lastIndex] = op to (out.last().second + t)
            else out += op to t
        }
        return out
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
