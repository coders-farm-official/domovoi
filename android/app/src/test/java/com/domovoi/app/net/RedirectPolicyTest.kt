package com.domovoi.app.net

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The follow-up the app builds for a redirect (security round 3 review,
 * A6-01): the same method and body rules as OkHttp's own follower, plus
 * three stricter ones — a WebSocket upgrade is never redirected, an
 * https→http downgrade is never followed, and the household token never
 * rides along (the hop's own interceptor decides it).
 */
class RedirectPolicyTest {

    private fun request(url: String, build: Request.Builder.() -> Unit = {}): Request =
        Request.Builder().url(url).apply(build).build()

    private fun redirect(request: Request, code: Int, location: String?): Response =
        Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(code).message("redirect")
            .apply { if (location != null) header("Location", location) }
            .build()

    @Test fun aPostBecomesAGetWithoutItsBodyOrContentHeaders() {
        val post = request("http://10.0.0.9:6369/api/x") {
            post("""{"a":1}""".toRequestBody("application/json".toMediaType()))
            header("X-Requested-With", "DomovoiApp")
        }
        val next = RedirectPolicy.followUp(post, redirect(post, 302, "/api/y"))!!
        assertEquals("GET", next.method)
        assertNull(next.body)
        assertNull(next.header("Content-Type"))
        assertEquals("the other headers stay", "DomovoiApp", next.header("X-Requested-With"))
        assertEquals("http://10.0.0.9:6369/api/y", next.url.toString())
    }

    @Test fun a307KeepsAGetAndRefusesAPost() {
        val get = request("http://10.0.0.9:6369/api/x")
        assertEquals("GET", RedirectPolicy.followUp(get, redirect(get, 307, "/api/y"))!!.method)
        val post = request("http://10.0.0.9:6369/api/x") { post("".toRequestBody()) }
        assertNull("OkHttp's rule: a 307/308 of a POST is handed back", RedirectPolicy.followUp(post, redirect(post, 308, "/api/y")))
    }

    @Test fun authorizationIsDroppedOffHostAndKeptOnHost() {
        val req = request("http://10.0.0.9:6369/api/x") { header("Authorization", "Bearer admin") }
        assertEquals("Bearer admin", RedirectPolicy.followUp(req, redirect(req, 302, "/api/y"))!!.header("Authorization"))
        assertNull(RedirectPolicy.followUp(req, redirect(req, 302, "http://10.0.0.9:6370/api/y"))!!.header("Authorization"))
        assertNull(RedirectPolicy.followUp(req, redirect(req, 302, "http://10.0.0.10:6369/api/y"))!!.header("Authorization"))
    }

    @Test fun theTokenHeaderNeverRidesAlongEvenOnHost() {
        val req = request("http://10.0.0.9:6369/api/x") { header(DEVICE_TOKEN_HEADER, "household-abc") }
        val next = RedirectPolicy.followUp(req, redirect(req, 302, "/api/y"))!!
        assertNull("the hop's own interceptor puts it back where it belongs", next.header(DEVICE_TOKEN_HEADER))
    }

    @Test fun anHttpsToHttpDowngradeIsNotFollowed() {
        val req = request("https://domovoi.example/api/x")
        assertNull(RedirectPolicy.followUp(req, redirect(req, 302, "http://domovoi.example/api/x")))
        assertNotNull("an upgrade is", RedirectPolicy.followUp(request("http://10.0.0.9:6369/x"), redirect(req, 302, "https://10.0.0.9:6369/x")))
    }

    @Test fun aWebSocketUpgradeIsNotFollowed() {
        val ws = request("http://10.0.0.9:6369/ws/state") { tag(TokenScope.WsUpgrade::class.java, TokenScope.WsUpgrade.MARK) }
        assertNull(RedirectPolicy.followUp(ws, redirect(ws, 302, "/ws/elsewhere")))
    }

    @Test fun notARedirectOrNoLocationIsHandedBack() {
        val req = request("http://10.0.0.9:6369/api/x")
        assertNull(RedirectPolicy.followUp(req, redirect(req, 200, "/api/y")))
        assertNull(RedirectPolicy.followUp(req, redirect(req, 404, "/api/y")))
        assertNull(RedirectPolicy.followUp(req, redirect(req, 302, null)))
        assertNull("a Location that does not parse", RedirectPolicy.followUp(req, redirect(req, 302, "ftp://x/y")))
    }

    @Test fun aRelativeLocationResolvesAgainstTheRequest() {
        val req = request("http://10.0.0.9:6369/api/music/library/7/audio")
        assertEquals(
            "http://10.0.0.9:6369/api/music/library/7/audio?download=1",
            RedirectPolicy.followUp(req, redirect(req, 301, "audio?download=1"))!!.url.toString(),
        )
    }
}
