package com.domovoi.app.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.util.Log

/**
 * Tells the [IdentityGate] when the phone's default network changes, so the
 * saved server has to prove its identity again before the token goes to it
 * (security round 3, A6-03). Registered once per process by
 * DomovoiApplication; never unregistered (the process ends with it).
 *
 * What counts as a change: a different default [Network] becoming
 * available (a new Wi-Fi association, Wi-Fi to mobile data), or the
 * default network going away. Roaming between access points of one
 * network keeps the same Network object and is not a change — an evil twin
 * with the home SSID is a new association, and so is one.
 */
object NetworkWatch {
    private const val TAG = "NetworkWatch"

    @Volatile
    private var current: Network? = null

    fun start(context: Context, onChange: () -> Unit) {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return
        try {
            cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    val before = current
                    current = network
                    // The first callback after registering describes the
                    // network the process started on: nothing has changed yet.
                    if (before != null && before != network) onChange()
                }

                override fun onLost(network: Network) {
                    if (current == network) {
                        current = null
                        onChange()
                    }
                }
            })
        } catch (e: RuntimeException) {
            // Too many callbacks registered, or no permission: the gate still
            // asks for a proof at every process start, just not per network.
            Log.w(TAG, "network callback not registered: ${e.message}")
        }
    }
}
