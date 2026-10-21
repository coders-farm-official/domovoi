package com.domovoi.app.assistant.drafts

/**
 * Whitespace as Python's `\s` and `str.split()` read it (the server's rules),
 * written out as one character class: Android's ICU regex has no `(?U)` flag
 * and the JVM's `\s` (the unit tests) is ASCII only, so neither can be used
 * where the phone and the server must split text the same way.
 */
internal object DraftSpace {
    const val CLASS = "[\\t\\n\u000B\\f\\r\u001C-\u001F \u0085   -     　]"
    val RUNS = Regex("$CLASS+")
    /** Runs of non-space or runs of space (DraftDiff's tokens). */
    val TOKENS = Regex("[^${CLASS.substring(1, CLASS.length - 1)}]+|$CLASS+")
    /** Runs of non-space (words). */
    val WORDS = Regex("[^${CLASS.substring(1, CLASS.length - 1)}]+")
}
