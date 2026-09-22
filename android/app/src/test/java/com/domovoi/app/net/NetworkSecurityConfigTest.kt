package com.domovoi.app.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Document
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * The platform half of AND-1, read straight from the module's sources: the
 * manifest points at res/xml/network_security_config.xml, no longer asks for
 * cleartext itself, and opts out of backups; the config trusts the system
 * store only; the unused logging interceptor is gone from the build.
 * (The IP-range half is CleartextPolicyTest.)
 */
class NetworkSecurityConfigTest {

    private val android = "http://schemas.android.com/apk/res/android"

    /** Gradle runs unit tests from the module directory; fall back to the
     *  android/ root in case the runner starts one level up. */
    private fun moduleFile(rel: String): File =
        listOf(File(rel), File("app/$rel")).firstOrNull { it.isFile }
            ?: error("cannot find $rel from ${File(".").absolutePath}")

    private fun xml(rel: String): Document =
        DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
            .newDocumentBuilder().parse(moduleFile(rel))

    private fun Element.androidAttr(name: String): String? =
        getAttributeNodeNS(android, name)?.value

    private fun Document.elements(tag: String): List<Element> {
        val nodes = getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }

    // ---- manifest ---------------------------------------------------------

    private val application: Element by lazy {
        xml("src/main/AndroidManifest.xml").elements("application").single()
    }

    @Test fun manifestReferencesTheNetworkSecurityConfig() {
        assertEquals("@xml/network_security_config", application.androidAttr("networkSecurityConfig"))
        assertTrue(moduleFile("src/main/res/xml/network_security_config.xml").isFile)
    }

    @Test fun manifestNoLongerAsksForCleartextItself() {
        // The config file owns the cleartext decision; the attribute would be
        // ignored with a config present and would mislead a reader.
        assertNull(application.androidAttr("usesCleartextTraffic"))
    }

    @Test fun backupsAreOff() {
        assertEquals("false", application.androidAttr("allowBackup"))
    }

    // ---- network_security_config.xml --------------------------------------

    private val config: Document by lazy { xml("src/main/res/xml/network_security_config.xml") }

    @Test fun configHasOneBaseConfigThatTrustsTheSystemStoreOnly() {
        val base = config.elements("base-config").single()
        val certs = config.elements("certificates").filter { it.parentNode.parentNode === base }
        assertEquals(listOf("system"), certs.map { it.getAttribute("src") })
    }

    @Test fun configKeepsTheHomeNetworkReachableInTheClear() {
        // Android cannot express an IP range here, and the usual server
        // address (e.g. http://192.168.0.117:6369, or 10.0.2.2:6390 from the
        // emulator) is an RFC 1918 IP: the platform half stays open and
        // CleartextPolicy narrows it to the home network on every request.
        val base = config.elements("base-config").single()
        assertEquals("true", base.getAttribute("cleartextTrafficPermitted"))
        assertFalse(config.elements("certificates").any { it.getAttribute("src") == "user" })
    }

    // ---- build.gradle.kts ---------------------------------------------------

    @Test fun okhttpLoggingInterceptorIsNotADependency() {
        val gradle = moduleFile("build.gradle.kts").readText()
        assertFalse(gradle, gradle.contains("okhttp.logging"))
        assertFalse(gradle, gradle.contains("logging-interceptor"))
        assertTrue(gradle, gradle.contains("implementation(libs.okhttp)"))
    }
}
