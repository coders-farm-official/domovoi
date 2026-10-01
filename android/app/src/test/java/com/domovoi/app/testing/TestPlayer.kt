package com.domovoi.app.testing

import android.content.ContextWrapper
import androidx.media3.exoplayer.ExoPlayer
import com.domovoi.app.AppContainer
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.player.PlayerController
import java.lang.reflect.Proxy

/**
 * A real [PlayerController] on the JVM, with no device under it.
 *
 * Its ExoPlayer is a [RecordingExoPlayer] put in place of the lazily built
 * one (nothing in a unit test can build a real ExoPlayer), so its own code —
 * the queue it keeps, the media items it hands the engine — runs as it does
 * in the app. Its Prefs is never read by what these tests call, and is
 * allocated without running its constructor (which reads DataStore).
 */
internal fun testPlayer(exo: RecordingExoPlayer = RecordingExoPlayer()): PlayerController {
    val player = PlayerController(
        ContextWrapper(null),
        ApiClient({ "http://192.0.2.1:6369" }),
        allocateWithoutConstructor(Prefs::class.java),
    )
    setField(player, "exoPlayer\$delegate", lazyOf(exo.player))
    return player
}

/** The app's process-wide graph with only [player] in it — enough for code
 *  that reads `LocalApp.current.player` or `app.player`. */
internal fun appWith(player: PlayerController): AppContainer =
    allocateWithoutConstructor(AppContainer::class.java).also { setField(it, "player", player) }

/** An ExoPlayer that records every call made on it and does nothing. */
internal class RecordingExoPlayer {
    val calls = mutableListOf<Pair<String, List<Any?>>>()

    val player: ExoPlayer = Proxy.newProxyInstance(
        ExoPlayer::class.java.classLoader,
        arrayOf(ExoPlayer::class.java),
    ) { proxy, method, args ->
        when (method.name) {
            "equals" -> proxy === args?.get(0)
            "hashCode" -> System.identityHashCode(proxy)
            "toString" -> "RecordingExoPlayer"
            else -> {
                calls += method.name to (args?.toList() ?: emptyList())
                defaultOf(method.returnType)
            }
        }
    } as ExoPlayer

    fun named(name: String): List<List<Any?>> = calls.filter { it.first == name }.map { it.second }

    private fun defaultOf(type: Class<*>): Any? = when (type) {
        java.lang.Boolean.TYPE -> false
        java.lang.Integer.TYPE -> 0
        java.lang.Long.TYPE -> 0L
        java.lang.Float.TYPE -> 0f
        java.lang.Double.TYPE -> 0.0
        java.lang.Short.TYPE -> 0.toShort()
        java.lang.Byte.TYPE -> 0.toByte()
        java.lang.Character.TYPE -> 0.toChar()
        else -> null
    }
}

/** An instance of [cls] whose constructor never ran: every field is null or
 *  zero until a test sets the ones the code under test reads. */
@Suppress("UNCHECKED_CAST")
internal fun <T> allocateWithoutConstructor(cls: Class<T>): T {
    val unsafeClass = Class.forName("sun.misc.Unsafe")
    val unsafe = unsafeClass.getDeclaredField("theUnsafe").apply { isAccessible = true }.get(null)
    return unsafeClass.getMethod("allocateInstance", Class::class.java).invoke(unsafe, cls) as T
}

internal fun setField(target: Any, name: String, value: Any?) {
    var cls: Class<*>? = target.javaClass
    while (cls != null) {
        val field = cls.declaredFields.firstOrNull { it.name == name }
        if (field != null) {
            field.isAccessible = true
            field.set(target, value)
            return
        }
        cls = cls.superclass
    }
    error("${target.javaClass.name} has no field $name")
}
