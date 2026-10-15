package com.domovoi.app.phonemizer

import java.io.File

/** The converter's data straight from the repo's domovoi/phonemizer/data
 *  (the folder the app's assets are copied from), loaded once per test run. */
object RepoPhonemizer {
    val dir: File by lazy { PhonemizerGoldenTest.phonemizerDir() }

    val files: PhonemizerFiles by lazy { PhonemizerFiles.folder(File(dir, "data")) }

    val data: PhonemizerData by lazy { PhonemizerData.load(files) }

    /** The android/ folder beside it. */
    val androidDir: File by lazy { File(dir, "../../android").canonicalFile }

    /** A converter without the out-of-dictionary model (unknown words are
     *  spelled): enough for tests that are not about pronunciation. */
    fun withoutModel(pronunciations: List<Pair<String, String>>? = null) = Phonemizer(data, null, pronunciations)
}
