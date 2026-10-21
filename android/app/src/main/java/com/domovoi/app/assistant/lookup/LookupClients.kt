package com.domovoi.app.assistant.lookup

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.CookieJar
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

/**
 * Where a phone chat's lookups go (assistant plan part 5, W1). The ONLY
 * network the assistant uses besides the model download, and only after
 * the person's allow (AssistantController asks first; NoNetworkTest keeps
 * the rest of the assistant's code free of any HTTP client):
 *
 * - **At home** ([HomeLookup]): the home server's own route
 *   (`POST /api/chat/lookup` → the core's `/v1/phone/lookup`), which asks
 *   its own sources first (its manual, an offline library), then Wikipedia
 *   and Wikidata, then the web. The server's internet answer "never" is
 *   respected: the phone doesn't go around it.
 * - **Away** ([WikipediaLookup]): Wikipedia directly, its REST search then
 *   each page's summary, one request at a time, with the User-Agent
 *   Wikimedia asks for ([appAgent]). Or the person's own search
 *   ([OwnSearchLookup]: their SearXNG address or Brave key, kept in the
 *   Android keystore), which falls back to Wikipedia when it fails.
 * - **A page the person names** ([PageReader]): HTTPS only, no cookies, no
 *   JavaScript, 2 MB at most, its main text ([PageText]).
 *
 * The clients carry no household token and no cookies: [plainClient] is
 * built fresh, not from the app's client.
 */
interface LookupBackend {
    /** Where a search goes, in the card's words. */
    val destination: String

    suspend fun search(query: String, internet: Boolean = true): LookupOutcome

    suspend fun read(url: String): LookupOutcome
}

data class LookupOutcome(
    val results: List<LookupResult> = emptyList(),
    /** Something left the phone (or the house) for this. */
    val internetUsed: Boolean = false,
    /** A plain sentence when something didn't work as set. */
    val notice: String? = null,
    /** The home server's internet answer is "never": nothing went out. */
    val internetOff: Boolean = false,
    /** Why it failed, in plain words; null when it worked. */
    val error: String? = null,
)

const val PROJECT_URL = "https://github.com/coders-farm-official/domovoi"

/** Wikipedia asks every client to name itself and how to reach its maker
 *  (a generic agent gets 403). codersfarm.org has no contact page yet
 *  (checked 2026-10-21), so the project's address stands in. */
fun appAgent(version: String) = "DomovoiApp/${version.ifBlank { "dev" }} ($PROJECT_URL)"

/** A page fetch a person asked for, not a crawler. */
fun userAgent(version: String) = "DomovoiApp-User/${version.ifBlank { "dev" }} ($PROJECT_URL)"

/** A client with no household token, no cookies and no https→http hop. */
fun plainClient(): OkHttpClient = OkHttpClient.Builder()
    .cookieJar(CookieJar.NO_COOKIES)
    .followRedirects(false)
    .followSslRedirects(false)
    .connectTimeout(8, TimeUnit.SECONDS)
    .readTimeout(10, TimeUnit.SECONDS)
    .callTimeout(20, TimeUnit.SECONDS)
    .build()

private val json = Json { ignoreUnknownKeys = true }
private const val API_MAX_BYTES = 512L * 1024
const val PAGE_MAX_BYTES = 2L * 1024 * 1024
const val WIKIPEDIA_LICENCE = "CC BY-SA 4.0"

private fun JsonElement?.str(): String? = (this as? JsonPrimitive)?.contentOrNull
private fun JsonObject.s(key: String): String? = this[key].str()

internal fun siteOf(url: String?): String =
    url?.toHttpUrlOrNull()?.host?.removePrefix("www.").orEmpty()

private val TAG = Regex("<[^>]+>")
internal fun clean(text: String?, limit: Int): String =
    PageText.unescape(TAG.replace(text.orEmpty(), "")).split(Regex("\\s+")).filter { it.isNotEmpty() }
        .joinToString(" ").take(limit)

/** GET [url]; (status, body) with the body capped at [max] bytes. */
internal suspend fun fetch(
    http: OkHttpClient, url: String, agent: String, max: Long, headers: Map<String, String> = emptyMap(),
): Triple<Int, String, String?> = withContext(Dispatchers.IO) {
    var current = url
    // Redirects are followed here, at most 3, and only to https (or, for a
    // plain-http start such as a LAN SearXNG, to the same scheme and host).
    repeat(MAX_HOPS + 1) {
        val req = Request.Builder().url(current).header("User-Agent", agent)
            .apply { headers.forEach { (k, v) -> header(k, v) } }.build()
        val next: String? = http.newCall(req).execute().use { resp ->
            if (resp.code in 300..399) {
                val loc = resp.header("Location")?.let { req.url.resolve(it) }
                val ok = loc != null && (loc.scheme == "https" || (loc.scheme == req.url.scheme && loc.host == req.url.host))
                if (!ok) return@withContext Triple(resp.code, "", null)
                return@use loc.toString()
            }
            val body = resp.body ?: return@withContext Triple(resp.code, "", null)
            if (body.contentLength() > max) throw TooLarge()
            val source = body.source()
            if (source.request(max + 1)) throw TooLarge()
            val bytes = source.buffer.readByteArray()
            val type = body.contentType()
            return@withContext Triple(resp.code, String(bytes, type?.charset(Charsets.UTF_8) ?: Charsets.UTF_8),
                type?.let { "${it.type}/${it.subtype}" })
        }
        current = next ?: return@withContext Triple(599, "", null)
    }
    Triple(310, "", null)
}

private const val MAX_HOPS = 3

internal class TooLarge : IOException("too large")

class WikipediaLookup(
    private val http: OkHttpClient,
    private val version: String,
    private val base: String = "https://en.wikipedia.org",
) : LookupBackend {
    override val destination = "Wikipedia (en.wikipedia.org)"

    override suspend fun search(query: String, internet: Boolean): LookupOutcome {
        if (!internet) return LookupOutcome()
        val agent = appAgent(version)
        val q = URLEncoder.encode(query, "UTF-8")
        val (status, body, _) = try {
            fetch(http, "$base/w/rest.php/v1/search/page?q=$q&limit=3", agent, API_MAX_BYTES)
        } catch (e: IOException) {
            return LookupOutcome(internetUsed = true, error = "Wikipedia couldn't be reached")
        }
        if (status >= 400) return LookupOutcome(internetUsed = true, error = "Wikipedia answered with an error ($status)")
        val pages = runCatching { json.parseToJsonElement(body).jsonObject["pages"]?.jsonArray }.getOrNull() ?: JsonArray(emptyList())
        val out = ArrayList<LookupResult>()
        // One at a time: Wikimedia asks clients not to run requests side by side.
        for (p in pages.take(3)) {
            val o = p as? JsonObject ?: continue
            val key = o.s("key") ?: continue
            val title = o.s("title") ?: continue
            val url = "$base/wiki/${URLEncoder.encode(key, "UTF-8").replace("+", "_")}"
            val summary = runCatching { summary(key) }.getOrNull()
            out += LookupResult(
                title = title, url = url, site = siteOf(url), snippet = clean(o.s("excerpt") ?: o.s("description"), 600),
                extract = summary.orEmpty(), sourceName = "Wikipedia", sourceClass = "api", licence = WIKIPEDIA_LICENCE,
            )
        }
        return LookupOutcome(out, internetUsed = true)
    }

    private suspend fun summary(key: String): String? {
        val (status, body, _) = fetch(http, "$base/api/rest_v1/page/summary/${URLEncoder.encode(key, "UTF-8").replace("+", "_")}",
            appAgent(version), API_MAX_BYTES)
        if (status >= 400) return null
        return runCatching { json.parseToJsonElement(body).jsonObject.s("extract") }.getOrNull()?.let { clean(it, 6000) }
    }

    override suspend fun read(url: String): LookupOutcome = PageReader(http, version).read(url)
}

class PageReader(private val http: OkHttpClient, private val version: String) {
    suspend fun read(url: String, query: String = ""): LookupOutcome {
        val u = url.trim().toHttpUrlOrNull()
        if (u == null || u.scheme != "https") return LookupOutcome(error = "I only read pages whose address starts with https://.")
        if (u.username.isNotEmpty() || u.password.isNotEmpty()) return LookupOutcome(error = "That address carries a name or password, so I won't send it.")
        val (status, body, type) = try {
            fetch(http, u.toString(), userAgent(version), PAGE_MAX_BYTES,
                mapOf("Accept" to "text/html,application/xhtml+xml,text/plain;q=0.8"))
        } catch (e: TooLarge) {
            return LookupOutcome(internetUsed = true, error = "That page is larger than 2 MB, so I didn't read it.")
        } catch (e: IOException) {
            return LookupOutcome(internetUsed = true, error = "That page couldn't be reached.")
        }
        if (status in 300..399) return LookupOutcome(internetUsed = true, error = "That page sent me to an address I won't follow.")
        if (status >= 400) return LookupOutcome(internetUsed = true, error = "That site answered with an error ($status).")
        val (title, text) = when (type) {
            "text/html", "application/xhtml+xml", null -> PageText.mainText(body)
            "text/plain" -> "" to PageText.tidy(body)
            else -> return LookupOutcome(internetUsed = true, error = "That address isn't a web page I can read (only HTML and text).")
        }
        if (text.isBlank()) return LookupOutcome(internetUsed = true, error = "That page has no text I could read (it may need JavaScript).")
        val site = siteOf(u.toString())
        return LookupOutcome(
            listOf(LookupResult(title.ifBlank { site }, u.toString(), site, text.take(300),
                LookupRules.bestPassage(text, query.ifBlank { title }, LookupRules.PASSAGE_CHARS * 2), "the page you named", "web",
                if (site.endsWith("wikipedia.org")) WIKIPEDIA_LICENCE else null)),
            internetUsed = true,
        )
    }
}

/** The home server's own lookup route. [post] is the app's client's
 *  POST (the household token rides along there, to the home server only):
 *  it returns (status, body, refusal header). */
class HomeLookup(
    private val post: suspend (path: String, body: String) -> Triple<Int, String, String?>,
    override val destination: String = "your home server's search: Wikipedia and Wikidata first, then the web",
) : LookupBackend {
    override suspend fun search(query: String, internet: Boolean): LookupOutcome =
        call("""{"query":${JsonPrimitive(query)},"internet":$internet}""", internet)

    override suspend fun read(url: String): LookupOutcome = call("""{"url":${JsonPrimitive(url)}}""", true)

    private suspend fun call(body: String, internet: Boolean): LookupOutcome {
        val (status, text, refusal) = try {
            post("/api/chat/lookup", body)
        } catch (e: IOException) {
            return LookupOutcome(error = "your home server didn't answer")
        }
        if (status == 409 && refusal == "internet-off") return LookupOutcome(internetOff = true)
        val o = runCatching { json.parseToJsonElement(text).jsonObject }.getOrNull()
        if (status >= 400 || o == null) {
            return LookupOutcome(internetUsed = internet, error = o?.s("detail") ?: "the lookup didn't work ($status)")
        }
        val results = (o["results"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }.take(3).map { r ->
            LookupResult(
                title = r.s("title").orEmpty().take(200), url = r.s("url"), site = r.s("site").orEmpty(),
                snippet = r.s("snippet").orEmpty().take(600), extract = r.s("extract").orEmpty().take(4000),
                sourceName = r.s("source_name").orEmpty(), sourceClass = r.s("source_class") ?: "web", licence = r.s("licence"),
            )
        }
        return LookupOutcome(results, internetUsed = (o["internet_used"] as? JsonPrimitive)?.booleanOrNull == true,
            notice = o.s("notice"))
    }
}

/** The person's own search (their SearXNG address or Brave key, from the
 *  keystore), Wikipedia first and as the fallback when it fails. */
class OwnSearchLookup(
    private val http: OkHttpClient,
    private val version: String,
    private val own: OwnSearch,
    private val wikipedia: LookupBackend = WikipediaLookup(http, version),
    private val braveBase: String = "https://api.search.brave.com",
) : LookupBackend {
    override val destination: String = when (own.kind) {
        OwnSearch.BRAVE -> "Wikipedia, then Brave Search with your key"
        else -> "Wikipedia, then your own SearXNG (${siteOf(own.url)})"
    }

    override suspend fun search(query: String, internet: Boolean): LookupOutcome {
        if (!internet) return LookupOutcome()
        val wiki = wikipedia.search(query, true)
        val web = runCatching { ownResults(query) }
        val extra = web.getOrNull().orEmpty().filterNot { r -> wiki.results.any { it.url == r.url } }
        val notice = web.exceptionOrNull()?.let { "${it.message ?: "Your own search didn't work"}, so this came from Wikipedia." }
        return LookupOutcome((wiki.results + extra).take(3), internetUsed = true, notice = notice)
    }

    private suspend fun ownResults(query: String): List<LookupResult> {
        val q = URLEncoder.encode(query, "UTF-8")
        return if (own.kind == OwnSearch.BRAVE) {
            val (status, body, _) = try {
                fetch(http, "$braveBase/res/v1/web/search?q=$q&count=3", appAgent(version), API_MAX_BYTES,
                    mapOf("X-Subscription-Token" to own.key, "Accept" to "application/json"))
            } catch (e: IOException) { throw IOException("Brave Search didn't answer") }
            when (status) {
                401, 403 -> throw IOException("Brave Search refused your key")
                429 -> throw IOException("Brave Search says your key is over its limit")
            }
            if (status >= 400) throw IOException("Brave Search answered with an error ($status)")
            val web = runCatching { json.parseToJsonElement(body).jsonObject["web"]?.jsonObject?.get("results")?.jsonArray }
                .getOrNull().orEmpty()
            web.mapNotNull { it as? JsonObject }.mapNotNull { r ->
                val url = r.s("url") ?: return@mapNotNull null
                LookupResult(clean(r.s("title"), 200), url, siteOf(url), clean(r.s("description"), 600), "", "Brave Search", "web")
            }
        } else {
            val (status, body, _) = try {
                fetch(http, "${own.url.trimEnd('/')}/search?q=$q&format=json", appAgent(version), API_MAX_BYTES)
            } catch (e: IOException) { throw IOException("Your SearXNG didn't answer") }
            if (status == 403) throw IOException("Your SearXNG refused the search (is its JSON output on?)")
            if (status >= 400) throw IOException("Your SearXNG answered with an error ($status)")
            val items = runCatching { json.parseToJsonElement(body).jsonObject["results"]?.jsonArray }.getOrNull().orEmpty()
            items.mapNotNull { it as? JsonObject }.mapNotNull { r ->
                val url = r.s("url") ?: return@mapNotNull null
                LookupResult(clean(r.s("title"), 200), url, siteOf(url), clean(r.s("content"), 600), "", "your SearXNG", "web")
            }.take(3)
        }
    }

    /** The settings' Test button: one search of a fixed word. */
    suspend fun test(): String = runCatching { ownResults("Wikipedia") }.fold(
        { "It works: ${it.size} result(s) for a test search." },
        { "${it.message ?: "It didn't work"}." },
    )

    override suspend fun read(url: String): LookupOutcome = PageReader(http, version).read(url)
}

/** The person's own search, as the keystore keeps it. */
data class OwnSearch(val kind: String, val url: String = "", val key: String = "") {
    companion object {
        const val SEARXNG = "searxng"
        const val BRAVE = "brave"
    }
}

private fun JsonArray?.orEmpty(): List<JsonElement> = this ?: emptyList()
