package com.domovoi.app.away

import java.io.IOException

/**
 * How the phone reaches its house's away onion: one byte stream to
 * `onion:port`, presenting the enrolment's Tor client-auth key. The seam
 * between away chat and the Tor it runs on: [ArtiTransport] (Arti, built
 * from native/domovoi-tor); a test's loopback stands in for it. Nothing
 * above this knows which Tor it is.
 */
interface AwayTransport {
    /** Open one stream to [e]'s onion. Starts whatever it needs (Tor) on
     *  first use. Throws an IOException when the house can't be reached;
     *  [OnionRefused] when Tor says the onion wasn't found, or refused this
     *  phone's client-auth key. */
    suspend fun open(e: AwayEnrolment): AwayStream

    /** Stop everything this transport started (Tor), now. */
    fun stop()
}

/** Tor couldn't use the onion: [code] 0xF0 its descriptor wasn't found,
 *  0xF4 the onion wants a client-auth key this phone doesn't hold (or
 *  holds a wrong one); Tor's extended SOCKS numbers, whichever Tor said
 *  it. To the person, "Can't reach your house." */
class OnionRefused(val socksCode: Int) : IOException("onion refused (0x%02x)".format(socksCode))
