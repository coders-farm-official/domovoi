package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The on-device assistant shows only where its engine (LiteRT-LM, 64-bit
 * only) is in the install: a 32-bit phone or APK hides it
 * ([AssistantSupport]).
 */
class AssistantSupportTest {

    private val inApk = "/data/app/com.domovoi.app/base.apk!/lib/arm64-v8a/liblitertlm_jni.so"

    @Test fun a64BitProcessWithTheEngineHasTheAssistant() {
        var asked: String? = null
        assertTrue(AssistantSupport.decide(is64Bit = true) { asked = it; inApk })
        assertEquals("litertlm_jni", asked)
    }

    @Test fun a32BitProcessNeverHasIt() {
        // A 64-bit phone running the 32-bit APK is a 32-bit process; even a
        // library the lookup claims to find could not be loaded into it.
        var asked = false
        assertFalse(AssistantSupport.decide(is64Bit = false) { asked = true; inApk })
        assertFalse(asked)
    }

    @Test fun noLibraryOrAFailingLookupMeansNoAssistant() {
        assertFalse(AssistantSupport.decide(is64Bit = true) { null })
        assertFalse(AssistantSupport.decide(is64Bit = true) { error("no class loader") })
    }

    /** On the JVM the class loader is no dex loader and Process.is64Bit()
     *  is a stub: the assistant counts as absent, never a crash. */
    @Test fun offAndroidItIsAbsent() {
        assertFalse(AssistantSupport.available)
    }
}
