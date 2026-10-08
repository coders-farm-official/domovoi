package com.domovoi.app.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiInfo
import android.os.Build
import android.util.Log

/**
 * Which networks the phone could reach the saved server over, kept as ONE
 * string — the network fingerprint — that the [IdentityGate] keys every
 * verdict on (security round 3, A6-03, and the review's blocker on it).
 *
 * The first cut watched the default [Network] object alone: a different
 * one, or none, meant "prove again". That misses every change a VPN hides.
 * With any VpnService up (Tailscale, WireGuard split tunnel, an ad
 * blocker's local VPN, a work VPN with LAN bypass) the default network IS
 * the VPN, and its object stays the same while the Wi-Fi underneath it
 * changes — the LAN traffic to the saved private address goes out on the
 * new Wi-Fi all the same. It also missed a roam onto a twin access point
 * with the home SSID and key, which keeps the Network object and changes
 * only the addresses the phone was given.
 *
 * So the watch now fingerprints everything it can see: every physical
 * Wi-Fi or Ethernet network (registered for on their own, so they are
 * reported even while a VPN is the default), the default network, each
 * one's transports, interface, addresses, gateways, DNS and DHCP server,
 * and the Wi-Fi SSID/BSSID where the app can read them. Every
 * ConnectivityManager callback —
 * onAvailable, onCapabilitiesChanged, onLinkPropertiesChanged, onLost —
 * updates the picture, and a verdict holds only on the fingerprint it was
 * taken on, so a change the callbacks describe WITHOUT a new Network
 * object still forces a new proof.
 *
 * The state machine ([seen], [lost], [fingerprint]) is pure and is what
 * the unit tests drive; [start] is the Android adapter. Registered once
 * per process by DomovoiApplication; never unregistered.
 */
class NetworkWatch(
    private val onChange: () -> Unit = {},
    private val log: (String) -> Unit = {},
) {
    /** Which callback reported a network: the default-network callback or
     *  the physical (Wi-Fi / Ethernet) one. Each keeps its own entries, so
     *  one's onLost never removes what the other still sees. */
    enum class Source { DEFAULT, PHYSICAL }

    /** What is known about one network. Every part is optional: the
     *  callbacks arrive in any order and each fills in its own. */
    data class Facts(
        val transports: Set<String> = emptySet(),
        /** `ssid/bssid` when readable (location permission; masked otherwise). */
        val wifi: String? = null,
        val iface: String? = null,
        val addresses: Set<String> = emptySet(),
        val gateways: Set<String> = emptySet(),
        val dns: Set<String> = emptySet(),
        val dhcp: String? = null,
    )

    private data class Key(val source: Source, val id: String)

    private val networks = HashMap<Key, Facts>()

    /** The current fingerprint; [NONE] until a network has been reported. */
    @Volatile
    var fingerprint: String = NONE
        private set

    private var changes = 0
    private var reported = false
    private var batching = false

    /** How many times the picture changed after the first one. */
    val changeCount: Int get() = synchronized(this) { changes }

    /** [source] reported [id]; [update] adds what the callback said. */
    @Synchronized
    fun seen(source: Source, id: String, update: (Facts) -> Facts = { it }) {
        val key = Key(source, id)
        if (source == Source.DEFAULT) {
            // One default at a time: a new default replaces the old entry
            // even when the framework sends no onLost for it.
            networks.keys.filter { it.source == Source.DEFAULT && it.id != id }.forEach { networks.remove(it) }
        }
        networks[key] = update(networks[key] ?: Facts())
        recompute()
    }

    /** [source] lost [id]. */
    @Synchronized
    fun lost(source: Source, id: String) {
        if (networks.remove(Key(source, id)) != null) recompute()
    }

    /** Several reports as ONE picture (the seed at start): the fingerprint
     *  is recomputed once, at the end, so the networks that are up when
     *  the process starts are one first picture, not a change each. */
    @Synchronized
    fun batch(block: NetworkWatch.() -> Unit) {
        batching = true
        try {
            block()
        } finally {
            batching = false
        }
        recompute()
    }

    private fun recompute() {
        if (batching) return
        val next = encode(networks)
        if (next == fingerprint) return
        fingerprint = next
        // The first picture after registering describes the network the
        // process started on: nothing has changed yet. (Losing every
        // network and finding one again later IS a change.)
        if (!reported) {
            reported = true
            return
        }
        changes++
        log("network changed: $next")
        onChange()
    }

    private fun encode(all: Map<Key, Facts>): String =
        all.entries
            .sortedWith(compareBy({ it.key.source.name }, { it.key.id }))
            .joinToString(";") { (key, f) ->
                listOf(
                    key.source.name.take(1), key.id,
                    f.transports.sorted().joinToString(","),
                    f.wifi.orEmpty(),
                    f.iface.orEmpty(),
                    f.addresses.sorted().joinToString(","),
                    f.gateways.sorted().joinToString(","),
                    f.dns.sorted().joinToString(","),
                    f.dhcp.orEmpty(),
                ).joinToString("|")
            }

    companion object {
        private const val TAG = "NetworkWatch"

        /** No network reported yet. */
        const val NONE = ""

        private val TRANSPORT_NAMES = mapOf(
            NetworkCapabilities.TRANSPORT_CELLULAR to "cellular",
            NetworkCapabilities.TRANSPORT_WIFI to "wifi",
            NetworkCapabilities.TRANSPORT_BLUETOOTH to "bluetooth",
            NetworkCapabilities.TRANSPORT_ETHERNET to "ethernet",
            NetworkCapabilities.TRANSPORT_VPN to "vpn",
            NetworkCapabilities.TRANSPORT_WIFI_AWARE to "wifi-aware",
            NetworkCapabilities.TRANSPORT_LOWPAN to "lowpan",
        )

        /**
         * Register with ConnectivityManager: the default network, and every
         * Wi-Fi or Ethernet network whether or not it is the default. The
         * picture is seeded from what is up right now, so the first verdict
         * is already keyed to the real network rather than to [NONE].
         */
        fun start(context: Context, watch: NetworkWatch) {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return
            try {
                seed(cm, watch)
                cm.registerDefaultNetworkCallback(Callback(cm, watch, Source.DEFAULT))
                val physical = NetworkRequest.Builder()
                    .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                    .addTransportType(NetworkCapabilities.TRANSPORT_ETHERNET)
                    .build()
                cm.registerNetworkCallback(physical, Callback(cm, watch, Source.PHYSICAL))
            } catch (e: RuntimeException) {
                // Too many callbacks registered, or no permission: the gate
                // still asks for a proof at every process start, after every
                // ten minutes and on every return to the foreground, just
                // not per network.
                Log.w(TAG, "network callback not registered: ${e.message}")
            }
        }

        private fun seed(cm: ConnectivityManager, watch: NetworkWatch) = watch.batch {
            val active = cm.activeNetwork
            @Suppress("DEPRECATION")
            val all = runCatching { cm.allNetworks.toList() }.getOrDefault(emptyList())
            for (network in (all + listOfNotNull(active)).distinct()) {
                val caps = cm.getNetworkCapabilities(network) ?: continue
                val link = cm.getLinkProperties(network)
                val physical = caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                    caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
                if (network == active) seen(Source.DEFAULT, id(network)) { it.with(caps).with(link) }
                if (physical) seen(Source.PHYSICAL, id(network)) { it.with(caps).with(link) }
            }
        }

        private fun id(network: Network): String = network.toString()

        private fun Facts.with(caps: NetworkCapabilities?): Facts {
            caps ?: return this
            val transports = TRANSPORT_NAMES.filterKeys { caps.hasTransport(it) }.values.toSet()
            // The networks a VPN runs over are a system API; the physical
            // callback sees them change on its own.
            val wifi = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                (caps.transportInfo as? WifiInfo)?.let { "${it.ssid}/${it.bssid}" }
            } else {
                null
            }
            return copy(transports = transports, wifi = wifi ?: this.wifi)
        }

        private fun Facts.with(link: LinkProperties?): Facts {
            link ?: return this
            val dhcp = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) link.dhcpServerAddress?.hostAddress else null
            return copy(
                iface = link.interfaceName,
                addresses = link.linkAddresses.map { it.toString() }.toSet(),
                gateways = link.routes.mapNotNull { it.gateway?.hostAddress }.toSet(),
                dns = link.dnsServers.mapNotNull { it.hostAddress }.toSet(),
                dhcp = dhcp,
            )
        }

        private class Callback(
            private val cm: ConnectivityManager,
            private val watch: NetworkWatch,
            private val source: Source,
        ) : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                // The capabilities and link come in their own callbacks right
                // after; reading them here too makes the first picture whole.
                val caps = cm.getNetworkCapabilities(network)
                val link = cm.getLinkProperties(network)
                watch.seen(source, id(network)) { it.with(caps).with(link) }
            }

            override fun onCapabilitiesChanged(network: Network, networkCapabilities: NetworkCapabilities) {
                watch.seen(source, id(network)) { it.with(networkCapabilities) }
            }

            override fun onLinkPropertiesChanged(network: Network, linkProperties: LinkProperties) {
                watch.seen(source, id(network)) { it.with(linkProperties) }
            }

            override fun onLost(network: Network) = watch.lost(source, id(network))
        }
    }
}
