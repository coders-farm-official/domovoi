package com.domovoi.app.ui.shell

import com.domovoi.app.data.ServerCredentials
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** A server the picker found and the user has not answered for yet. */
data class PendingServer(val url: String, val name: String?) {
    /** `10.0.0.42:6369` — shown prominently, so the address can be checked
     *  against the box before anything is trusted. */
    val address: String get() = ServerCredentials.address(url)
}

/**
 * The step between "tapped a server in the picker" and "the app is talking
 * to it". Connecting is consequential — the app loads that server's
 * capability manifest, renders the plugin-backed screens it advertises and
 * sends it this phone's requests — and a LAN sweep finds whatever answers
 * `/api/health`, so a server that has not been trusted is shown, with its
 * address, and confirmed first.
 *
 * Nothing happens on [select] for an untrusted server but [pending] being
 * set: no preference is written, no capability or plugin route is loaded,
 * no request is sent. [confirm] is the only path that trusts and connects;
 * [cancel] leaves the app exactly as it was.
 *
 * Deliberately free of Compose and Android so the rule is unit-testable —
 * the picker holds one of these in `remember` and collects [pending].
 */
class ServerConnectGate(
    private val isTrusted: (String) -> Boolean,
    private val onTrust: (String) -> Unit,
    private val onConnect: (String, String?) -> Unit,
) {
    private val _pending = MutableStateFlow<PendingServer?>(null)
    val pending: StateFlow<PendingServer?> = _pending

    /** True when the server was already trusted and is now connected;
     *  false when the user has to answer first (see [pending]). */
    fun select(url: String, name: String? = null): Boolean {
        val clean = ServerCredentials.normalize(url)
        if (clean.isBlank()) return false
        if (!isTrusted(clean)) {
            _pending.value = PendingServer(clean, name)
            return false
        }
        _pending.value = null
        onConnect(clean, name)
        return true
    }

    /** The user said yes to what [pending] shows. */
    fun confirm(): Boolean {
        val p = _pending.value ?: return false
        _pending.value = null
        onTrust(p.url)
        onConnect(p.url, p.name)
        return true
    }

    fun cancel() { _pending.value = null }
}
