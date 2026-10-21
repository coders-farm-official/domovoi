package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.DataInputStream
import java.io.File
import java.net.JarURLConnection
import java.util.jar.JarFile

/**
 * The no-network rule, checked on the compiled classes: in the assistant's
 * code only the model downloader may touch an HTTP client or socket, and the
 * libraries it runs on (LiteRT-LM; sherpa-onnx's Kotlin API, built in from
 * android/native/sherpa-onnx; ONNX Runtime's Java API, packaged though
 * unused) reference no networking class at all in their Java/Kotlin layer.
 * Every class name, method owner and type descriptor a class uses sits in its
 * constant pool, so a class that never names `okhttp3/…` or `java/net/…`
 * there cannot call into them.
 *
 * (Their native libraries were checked by hand, for M0 and again for the
 * rebuilt sherpa-onnx and ONNX Runtime 1.28.0: none imports socket, connect,
 * getaddrinfo or any other networking symbol from libc.)
 */
class NoNetworkTest {

    private val networking = listOf(
        "okhttp3/", "okio/", "java/net/", "javax/net/", "android/net/http/", "android/webkit/",
        "java/nio/channels/SocketChannel", "java/nio/channels/DatagramChannel", "java/nio/channels/ServerSocketChannel",
    )

    /** Reading whether the phone is online and the app's byte counters for
     *  the report: no connection is opened. */
    private val probeMayRead = setOf(
        "android/net/ConnectivityManager", "android/net/Network", "android/net/TrafficStats",
    )

    @Test fun onlyTheModelDownloaderTouchesTheNetwork() {
        val classes = classesIn("com/domovoi/app/assistant", AssistantBench::class.java) +
            classesIn("com/domovoi/app/ui/screens/assistant", Class.forName("com.domovoi.app.ui.screens.assistant.AssistantBenchmarkCardKt", false, javaClass.classLoader)) +
            // Documents in phone chats (D1): read on the phone, never sent.
            classesIn("com/domovoi/app/assistant/docs", com.domovoi.app.assistant.docs.DocQa::class.java)
        assertTrue("no classes found", classes.size > 10)
        val offenders = classes.filterNot { (name, _) -> name.startsWith("ModelDownloader") }
            .mapNotNull { (name, bytes) ->
                val refs = utf8Constants(bytes).filter { s -> networking.any { s.contains(it) } }
                refs.takeIf { it.isNotEmpty() }?.let { "$name: $it" }
            }
        assertEquals(emptyList<String>(), offenders)

        val androidNet = classes.flatMap { (name, bytes) ->
            utf8Constants(bytes).flatMap { Regex("android/net/[A-Za-z/$]+").findAll(it).map { m -> name to m.value } }
        }.filterNot { (name, ref) -> name.startsWith("DeviceProbe") && ref in probeMayRead }
        assertEquals(emptyList<Pair<String, String>>(), androidNet)
    }

    /** The one allowed path the plan adds (part 5, W1): the lookup clients,
     *  used only after the person's allow (PhoneLookupsTest counts the
     *  calls). The rules, the page reader's text extraction and the
     *  keystore's box touch no network. */
    @Test fun inTheLookupPackageOnlyTheClientsTouchTheNetwork() {
        val classes = classesIn("com/domovoi/app/assistant/lookup", com.domovoi.app.assistant.lookup.LookupRules::class.java)
        assertTrue("no classes found", classes.size > 5)
        val clients = setOf("LookupClientsKt", "WikipediaLookup", "PageReader", "HomeLookup", "OwnSearchLookup", "TooLarge", "PublicDns", "OwnSearchError")
        val offenders = classes.filterNot { (name, _) -> clients.any { name == it || name.startsWith(it + "$") } }
            .mapNotNull { (name, bytes) ->
                val refs = utf8Constants(bytes).filter { s -> networking.any { s.contains(it) } }
                refs.takeIf { it.isNotEmpty() }?.let { "$name: $it" }
            }
        assertEquals(emptyList<String>(), offenders)
    }

    @Test fun theDownloaderIsWhereTheHttpClientIs() {
        val downloader = classesIn("com/domovoi/app/assistant", AssistantBench::class.java)
            .filter { it.first.startsWith("ModelDownloader") }
        assertTrue(downloader.any { (_, bytes) -> utf8Constants(bytes).any { it.contains("okhttp3/OkHttpClient") } })
    }

    @Test fun theLibrariesJavaLayersReferenceNoNetworking() {
        val litert = classesIn("com/google/ai/edge/litertlm", com.google.ai.edge.litertlm.Engine::class.java)
        val sherpa = classesIn("com/k2fsa/sherpa/onnx", com.k2fsa.sherpa.onnx.OfflineRecognizer::class.java)
        val ort = classesIn("ai/onnxruntime", ai.onnxruntime.OrtEnvironment::class.java)
        assertTrue(litert.size > 20 && sherpa.size > 20 && ort.size > 20)
        val offenders = (litert + sherpa + ort).mapNotNull { (name, bytes) ->
            val refs = utf8Constants(bytes).filter { s -> networking.any { s.contains(it) } || s.contains("android/net/") }
            refs.takeIf { it.isNotEmpty() }?.let { "$name: $it" }
        }
        assertEquals(emptyList<String>(), offenders)
    }

    // ── Class-file reading ──────────────────────────────────────────────

    /** Every class file in [packagePath] (not sub-packages), from the
     *  directory or jar that holds [anchor]. */
    private fun classesIn(packagePath: String, anchor: Class<*>): List<Pair<String, ByteArray>> {
        val url = anchor.classLoader!!.getResource(anchor.name.replace('.', '/') + ".class")!!
        return when (url.protocol) {
            "file" -> File(url.toURI()).parentFile!!.listFiles()!!
                .filter { it.name.endsWith(".class") }
                .map { it.name.removeSuffix(".class") to it.readBytes() }
            "jar" -> {
                val jar: JarFile = (url.openConnection() as JarURLConnection).jarFile
                jar.entries().toList()
                    .filter { it.name.startsWith("$packagePath/") && it.name.endsWith(".class") &&
                        !it.name.removePrefix("$packagePath/").contains('/') }
                    .map { e -> e.name.substringAfterLast('/').removeSuffix(".class") to jar.getInputStream(e).use { it.readBytes() } }
            }
            else -> error("cannot list classes at $url")
        }
    }

    /** The CONSTANT_Utf8 entries of a class file's constant pool. */
    private fun utf8Constants(bytes: ByteArray): List<String> {
        val input = DataInputStream(bytes.inputStream())
        check(input.readInt() == 0xCAFEBABE.toInt())
        input.readUnsignedShort(); input.readUnsignedShort()
        val count = input.readUnsignedShort()
        val out = mutableListOf<String>()
        var i = 1
        while (i < count) {
            when (val tag = input.readUnsignedByte()) {
                1 -> out += input.readUTF()
                3, 4 -> input.readInt()
                5, 6 -> { input.readLong(); i++ }
                7, 8, 16, 19, 20 -> input.readUnsignedShort()
                9, 10, 11, 12, 17, 18 -> { input.readUnsignedShort(); input.readUnsignedShort() }
                15 -> { input.readUnsignedByte(); input.readUnsignedShort() }
                else -> error("unknown constant pool tag $tag")
            }
            i++
        }
        return out
    }
}
