package com.domovoi.app.assistant

import android.os.Build
import android.os.Process
import dalvik.system.BaseDexClassLoader

/**
 * Whether this install can run the on-device assistant at all.
 *
 * Its engine, LiteRT-LM, ships native code for arm64-v8a and x86_64 only.
 * The 32-bit APKs (armeabi-v7a, x86; app/build.gradle.kts) carry none, and
 * neither does what Play installs on a 32-bit phone from the App Bundle; a
 * 64-bit phone running a 32-bit APK is a 32-bit process with none either.
 * There the assistant's card and anything that leads to it are hidden
 * instead of failing on first use; everything else in the app (the server's
 * features, the phone's own voice on ONNX Runtime, local media) is the same.
 *
 * The check asks the app's own class loader where the library is, which
 * also finds it inside the APK (the libraries are stored uncompressed and
 * never extracted), without loading it.
 */
object AssistantSupport {
    /** LiteRT-LM's JNI library, `liblitertlm_jni.so`. */
    const val ENGINE_LIBRARY = "litertlm_jni"

    /** True when the assistant's engine is in this install and this process
     *  can load it. Decided once per process. */
    val available: Boolean by lazy {
        decide(Process.is64Bit()) { name ->
            (AssistantSupport::class.java.classLoader as? BaseDexClassLoader)?.findLibrary(name)
        }
    }

    /** This phone's memory, read once when the app starts
     *  (DomovoiApplication); null until then. */
    @Volatile var memory: PhoneMemory? = null

    /** This install can start a chat on the phone: it has the engine
     *  ([available]) and the phone has the memory for the model
     *  ([DeviceTiers.MIN_GB]). Chat's "on this phone" place, and Chat itself
     *  where no server place works ([com.domovoi.app.ui.shell.chatAvailable]). */
    val phoneCanChat: Boolean
        get() = available && canChat(memory)

    /** The memory half of [phoneCanChat]; unknown memory counts as enough. */
    internal fun canChat(memory: PhoneMemory?): Boolean =
        memory == null || DeviceTiers.of(memory) != DeviceTier.Unsupported

    /** A 64-bit phone running the 32-bit app: the 64-bit APK would add the
     *  assistant, so Settings says so instead of saying nothing. */
    val thirtyTwoBitAppOn64BitPhone: Boolean by lazy {
        !Process.is64Bit() && Build.SUPPORTED_64_BIT_ABIS.orEmpty().isNotEmpty()
    }

    /** The rule, apart from Android: a 64-bit process that has the engine's
     *  library. A failing lookup counts as not there. */
    internal fun decide(is64Bit: Boolean, findLibrary: (String) -> String?): Boolean =
        is64Bit && runCatching { findLibrary(ENGINE_LIBRARY) }.getOrNull() != null
}
