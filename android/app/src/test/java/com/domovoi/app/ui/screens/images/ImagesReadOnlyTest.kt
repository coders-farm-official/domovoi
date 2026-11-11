package com.domovoi.app.ui.screens.images

import com.domovoi.app.net.ApiClient
import com.domovoi.app.testing.Bytecode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Images is look-only from a phone (the owner's decision 67, 2026-11-04:
 * "image generation is dashboard only").
 *
 * The imagegen plugin marks its three mutating routes `ADMIN_ONLY`
 * (`domovoi-plugin-imagegen/domovoi_plugin_imagegen/web.py:149`, applied at
 * `:360` generate, `:339` cancel, `:651` delete history) and this app sends
 * only `X-Device-Token`, so a phone is never admin and all three answered
 * 401 "admin session required". The screen drew them anyway, which is the
 * control the owner's no-misleading-controls rule forbids; the iPhone removed
 * the same three on 2026-11-03 (`ios/Domovoi/Images/ImagesView.swift`).
 *
 * Two kinds of check, because either alone can pass while the screen is
 * wrong:
 *
 *  * the **compiled** screen writes nothing — no `post`, `delete`, `put`,
 *    `patch`, `upload` or `raw` on `ApiClient` from any method of
 *    `ImagesScreenKt` or its lambdas, counting the synthetic `…$default`
 *    bridges the compiler emits for a call that omits the optional body
 *    (see [writeNames], and [the write names catch a body-less write] which
 *    proves it on code compiled in this very file). That is the thing that
 *    matters, and it cannot be satisfied by renaming a string or by
 *    spelling the path from pieces.
 *  * the **code** has no control for them, so a button is not re-added
 *    pointing at a route that happens to be reached some other way.
 *
 * The source checks run against [code], which is the file with its comments
 * taken out: the file's header names all three routes on purpose, so a
 * search of the raw text would find `/generate` in the very sentence that
 * explains why there is no generate.
 *
 * A positive control runs first in each half. Without it a wrong facade
 * name, or a screen that had been emptied altogether, would pass every
 * assertion here by having nothing in it.
 */
class ImagesReadOnlyTest {

    private val facade = "com.domovoi.app.ui.screens.images.ImagesScreenKt"
    private val apiClient = "com/domovoi/app/net/ApiClient"

    /**
     * Every name a write on [ApiClient] can be invoked under.
     *
     * The plain name is not enough. Each of these takes an optional body
     * (`ApiClient.kt:312-362`: `suspend fun post(path: String, body:
     * JsonElement? = null)`), so a call that omits it — which is how two of
     * the three controls removed here were written (a one-argument
     * `api.post(".../jobs/<id>/cancel")`) — does not invoke
     * `post` at all; it invokes the synthetic static bridge
     * `ApiClient.post` + `$default`. A set of plain names is blind to exactly
     * the calls this test exists to forbid. `upload` has no optional
     * parameter and so no bridge, but it costs nothing to list, and `raw`
     * is here because it is public: it is the one way to reach POST or
     * DELETE without naming either.
     */
    private val writeNames: Set<String> =
        listOf("post", "delete", "put", "patch", "upload", "raw")
            .flatMap { listOf(it, it + '$' + "default") }
            .toSet()

    /**
     * Not called. It exists so that [the write names catch a body-less write]
     * has real compiler output to check [writeNames] against: these four
     * calls omit the body, so this file's own bytecode holds the bridge
     * spelling, whatever the Kotlin version does with it.
     */
    @Suppress("unused")
    private suspend fun bodylessWrites(api: ApiClient) {
        api.post("/x")
        api.delete("/x")
        api.put("/x")
        api.patch("/x")
    }

    private fun moduleFile(rel: String): File =
        listOf(File(rel), File("app/$rel")).firstOrNull { it.isFile }
            ?: error("cannot find $rel from ${File(".").absolutePath}")

    private val src: String by lazy {
        moduleFile("src/main/java/com/domovoi/app/ui/screens/images/ImagesScreen.kt").readText()
    }

    /** [src] with `/* … */` blocks and `//` tails removed. This file has no
     *  string literal containing either marker, so nothing real is lost. */
    private val code: String by lazy {
        src.replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), "")
            .lines().joinToString("\n") { it.substringBefore("//") }
    }

    @Test
    fun `the screen still reads the plugin`() {
        // The positive control for the bytecode half: these are the four
        // open routes, and they are the whole screen.
        val reads = Bytecode.callers(facade, apiClient, setOf("get"))
        assertTrue("the screen reads nothing — wrong facade?", reads.isNotEmpty())
        for (path in listOf("/models", "/jobs", "/history", "/file/")) {
            assertTrue(path, code.contains("\$IG_API$path"))
        }
    }

    @Test
    fun `the write names catch a body-less write`() {
        // The positive control for [writeNames] itself. [bodylessWrites]
        // makes four writes with no body; if the set only held the plain
        // names this would find none of them, and the assertion below would
        // be satisfied by a screen full of cancel buttons.
        val mine = Bytecode.callers(
            "com.domovoi.app.ui.screens.images.ImagesReadOnlyTest", apiClient, writeNames,
        )
        assertEquals(
            "writeNames misses the compiler's spelling of a body-less write: $mine",
            listOf(Bytecode.Call("ImagesReadOnlyTest", "bodylessWrites")),
            mine.distinct(),
        )
        // One per call, so a set that caught only some of the four would
        // still be reported here.
        assertEquals("all four body-less writes are seen: $mine", 4, mine.size)
    }

    @Test
    fun `the compiled screen makes no write of any kind`() {
        val writes = Bytecode.callers(facade, apiClient, writeNames)
        assertEquals(
            "Images is read-only from a phone (decision 67): $writes",
            emptyList<Bytecode.Call>(), writes,
        )
    }

    @Test
    fun `generate is not drawn`() {
        assertFalse(code.contains("/generate"))
        // The prompt field, the model picker and the button that sent it.
        assertFalse(code.contains("describe the image"))
        assertFalse(code.contains("\"generate\""))
        assertFalse(code.contains("AutoAwesome"))
        assertFalse("the picker only existed to pick what to generate with", code.contains("ModelPicker"))
        assertFalse(code.contains("ExposedDropdownMenu"))
    }

    @Test
    fun `a running job offers no cancel`() {
        assertFalse(code.contains("/cancel"))
        assertFalse(code.contains("Icons.Outlined.Cancel"))
        assertFalse("no contentDescription for a control that is not there", code.contains("\"cancel\""))
        // The lane is still drawn: a report on what the house is doing.
        assertTrue(code.contains("private fun JobCard(job: ImageJob)"))
        assertTrue(code.contains("CircularProgressIndicator"))
    }

    @Test
    fun `a past generation offers no delete and no confirmation`() {
        assertFalse(code.contains("\$IG_API/history/\${"))
        assertFalse(code.contains("DeleteOutline"))
        assertFalse(code.contains("\"delete\""))
        // A destructive dialog that could only ever end in a 401 was worse
        // than the dead button that opened it.
        assertFalse(code.contains("ConfirmDialog"))
        assertFalse(code.contains("confirmDelete"))
        // The pictures still open, which is the whole point of the card.
        assertTrue(code.contains("onOpen = { name ->"))
        assertTrue(code.contains("ServerFileTransfer.open("))
    }

    @Test
    fun `the file says why the three are missing`() {
        // The rule only holds if the next person reading this file knows
        // what to undo the day the plugin gives those routes the household
        // tier. NIGHT-RULES section 3's "What is not, and why there is no
        // control for it".
        assertTrue(src.contains("ADMIN_ONLY"))
        assertTrue(src.contains("enforce_route_tier"))
        assertTrue(src.contains("web.py:149"))
    }
}
