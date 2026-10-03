package com.domovoi.app.player

import com.domovoi.app.net.ApiClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * B11: podcast artwork is loaded from the server only. The list rows, the
 * episode overlay, the search results and the media session all draw a
 * server path, and never an absolute URL that would send the phone to a
 * publisher.
 */
class PodcastArtworkTest {

    private val api = ApiClient(baseUrlProvider = { "http://192.168.0.117:6369" })

    @Test fun aServerPathLoadsFromTheActiveServer() {
        assertEquals(
            "http://192.168.0.117:6369/api/podcasts/subscriptions/3/artwork?v=1a2b3c4d",
            PodcastArtwork.model("/api/podcasts/subscriptions/3/artwork?v=1a2b3c4d", api::absolute),
        )
        assertEquals(
            "http://192.168.0.117:6369/api/podcasts/discover/artwork/0123456789abcdef0123456789abcdef",
            PodcastArtwork.model("/api/podcasts/discover/artwork/0123456789abcdef0123456789abcdef", api::absolute),
        )
    }

    @Test fun anAbsoluteUrlIsRefused() {
        // What an older server answered: the publisher's own image.
        assertNull(PodcastArtwork.model("https://image.simplecastcdn.com/show/art.jpg", api::absolute))
        assertNull(PodcastArtwork.model("http://is1-ssl.mzstatic.com/image/thumb/600x600bb.jpg", api::absolute))
        // Protocol-relative and backslash spellings resolve to another host.
        assertNull(PodcastArtwork.model("//cdn.example/art.jpg", api::absolute))
        assertNull(PodcastArtwork.model("/\\cdn.example/art.jpg", api::absolute))
        assertNull(PodcastArtwork.path("content://media/external/images/1"))
    }

    @Test fun noArtworkIsThePlaceholder() {
        assertNull(PodcastArtwork.model(null, api::absolute))
        assertNull(PodcastArtwork.model("", api::absolute))
        assertNull(PodcastArtwork.model("   ", api::absolute))
    }

    @Test fun anEpisodeItemCarriesOnlyAServerCover() {
        val served = PlayItem.fromEpisode(7, "Ep 7", "Show", 60.0, "/api/podcasts/subscriptions/3/artwork?v=1", emptyList())
        assertEquals("/api/podcasts/subscriptions/3/artwork?v=1", served.coverPath)
        val remote = PlayItem.fromEpisode(7, "Ep 7", "Show", 60.0, "https://feeds.example/show/art.jpg", emptyList())
        // The media session's artwork loader never sees the publisher's URL.
        assertNull(remote.coverPath)
        assertEquals("/api/podcasts/episodes/7/audio", remote.src)
    }
}
