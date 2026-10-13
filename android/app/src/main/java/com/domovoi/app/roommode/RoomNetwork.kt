package com.domovoi.app.roommode

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import com.domovoi.app.net.CleartextPolicy
import com.domovoi.app.net.RedirectPolicy
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import okhttp3.Dns
import okhttp3.OkHttpClient
import java.net.InetAddress
import java.util.concurrent.TimeUnit

/**
 * The room's way onto the network: Wi-Fi (or a wired dock), never the
 * phone's mobile data. Every request and the room's socket go out on a
 * client bound to that network ([client]), so a phone whose Wi-Fi drops
 * goes offline instead of carrying the room's microphone over cellular.
 *
 * The room's client is NOT the app's: it has no household-token
 * interceptor (a room phone sends no household credential anywhere), and
 * keeps the app's cleartext rule (plain http only on the home network) and
 * redirect rule (followed by hand, never across hosts with anything).
 */
class RoomNetwork(context: Context) {
    private val cm = context.getSystemService(ConnectivityManager::class.java)
    private val nets = LinkedHashMap<Network, Boolean>()
    private val _network = MutableStateFlow<Network?>(null)

    /** The Wi-Fi or Ethernet network the room uses; null: none. */
    val network: StateFlow<Network?> = _network

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            synchronized(nets) { nets[network] = true; publish() }
        }

        override fun onLost(network: Network) {
            synchronized(nets) { nets.remove(network); publish() }
        }
    }

    private fun publish() {
        _network.value = nets.keys.lastOrNull()
    }

    fun start() {
        val wifi = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .addTransportType(NetworkCapabilities.TRANSPORT_ETHERNET)
            // A home Wi-Fi with no internet is still the home network.
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        runCatching { cm.registerNetworkCallback(wifi, callback) }
    }

    fun stop() {
        runCatching { cm.unregisterNetworkCallback(callback) }
        synchronized(nets) { nets.clear(); publish() }
    }

    /** Suspends until there is a Wi-Fi network. */
    suspend fun awaitNetwork(): Network = network.filterNotNull().first()

    /** A client bound to [net]: its sockets and its DNS go out there only. */
    fun client(net: Network): OkHttpClient = BASE.newBuilder()
        .socketFactory(net.socketFactory)
        .dns(object : Dns {
            override fun lookup(hostname: String): List<InetAddress> = net.getAllByName(hostname).toList()
        })
        .build()

    companion object {
        /** No token interceptor, ever. Ping every 15 s so a half-open
         *  socket fails within ~30 s (the core pings too). */
        val BASE: OkHttpClient = OkHttpClient.Builder()
            .followRedirects(false)
            .followSslRedirects(false)
            .addInterceptor(RedirectPolicy.interceptor)
            .addInterceptor(CleartextPolicy.interceptor)
            .connectTimeout(6, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .pingInterval(15, TimeUnit.SECONDS)
            .build()
    }
}
