package com.domovoi.app.ui.screens.manual

import android.provider.Settings
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.BorderStroke
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material.icons.outlined.Build
import androidx.compose.material.icons.outlined.CellTower
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material.icons.outlined.Circle
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.HelpOutline
import androidx.compose.material.icons.outlined.Map
import androidx.compose.material.icons.outlined.Memory
import androidx.compose.material.icons.outlined.MusicNote
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Smartphone
import androidx.compose.material.icons.outlined.ZoomOut
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.domovoi.app.LocalApp
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.openWebLink
import com.domovoi.app.ui.shell.LocalOpenSettingsTab
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.delay
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlin.math.PI
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin
import kotlin.random.Random

/*
 * The user manual, as the dashboard's #manual draws it (web/static/manual.jsx),
 * from the same file: the connected server's manual-content.json
 * (ManualRepository). Start here first, with its checklist; then the map:
 * "Domovoi" in the middle, the topics around it, a topic's pieces around the
 * topic; a piece opens its page. The camera zooms with animateFloatAsState
 * (280 ms, the house rule's 300 at most), the nodes float and their wires
 * follow, a level's names scramble as it opens. With animations off on the
 * phone (Developer options' animator scale 0) every move is a cut and
 * nothing floats. Links open the app's own screens, else the dashboard in
 * the browser; "More detail" opens the docs of the server's version.
 */

private const val CLUSTER = 0.34f                  // a cluster's scale against its parent's
private const val PHONE_MAX_DP = 600f               // narrower: two nodes to a row
private const val PHONE_NODE_DP = 104f              // a phone's node: one size for all
private const val PHONE_ROW_GAP = 18f

/** A phone's map: seven nodes, two rows above the middle and two below. */
private fun phoneStageHeight(): Float = 4 * (PHONE_NODE_DP + PHONE_ROW_GAP) + PHONE_NODE_DP + 32f
private const val MOVE_MS = 280
private const val FADE_MS = 180
private val EASE = CubicBezierEasing(0.2f, 0f, 0f, 1f)
private const val GLYPHS = "ABCDEFGHKMNPRSTVXZ0123456789#%&*/<>"

private val TOPIC_ICONS: Map<String, ImageVector> = mapOf(
    "how-it-works" to Icons.Outlined.Memory,
    "things-to-say" to Icons.Outlined.ChatBubbleOutline,
    "rooms-and-devices" to Icons.Outlined.CellTower,
    "your-phone" to Icons.Outlined.Smartphone,
    "music-and-media" to Icons.Outlined.MusicNote,
    "chat-files-calendar" to Icons.Outlined.Folder,
    "people-and-privacy" to Icons.Outlined.Groups,
    "keeping-it-running" to Icons.Outlined.Build,
)

/** Where the manual is: "start", "map", "t:<topic>" or "p:<piece>". */
private fun topicOf(at: String) = at.removePrefix("t:").takeIf { at.startsWith("t:") }
private fun pieceOf(at: String) = at.removePrefix("p:").takeIf { at.startsWith("p:") }

@Composable
fun ManualScreen(navigate: (Route) -> Unit) {
    val app = LocalApp.current
    val server by app.prefs.serverUrl.collectAsState()
    var result by remember(server) { mutableStateOf<ManualRepository.Result?>(null) }
    var tick by remember { mutableIntStateOf(0) }
    LaunchedEffect(server, tick) { result = app.manual.load() }

    when (val r = result) {
        null -> LoadingState()
        is ManualRepository.Result.Ready -> ManualBody(r.doc, r.cached, navigate)
        ManualRepository.Result.NoManual -> NoManual(server)
        is ManualRepository.Result.Failed -> Column(Modifier.fillMaxSize().padding(16.dp)) {
            EmptyState("the manual didn't load", "this server can't be reached right now")
            TextButton(onClick = { tick++ }) { Text("try again") }
        }
    }
}

/** A server older than the manual's file: the dashboard has its own. */
@Composable
private fun NoManual(server: String?) {
    val context = LocalContext.current
    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        EmptyState("no manual on this server", "this server is older than the manual; its dashboard has one")
        dashboardUrl(server, "#manual")?.let { url ->
            Button(onClick = { openWebLink(context, url) }) { Text("open the dashboard") }
        }
    }
}

// ─── what the server knows ────────────────────────────────────────────────

@Serializable
private data class HandlerInfo(
    val name: String = "",
    val display: HandlerLabel? = null,
    @SerialName("example_phrases") val examplePhrases: List<String> = emptyList(),
    @SerialName("requires_network") val requiresNetwork: String? = null,
    val origin: String? = null,
)

@Serializable
private data class HandlerLabel(val label: String? = null)

@Serializable
private data class PluginInfo(
    val slug: String = "",
    val name: String? = null,
    val version: String? = null,
    val enabled: Boolean = false,
    val status: String? = null,
)

private class Live(
    val rooms: List<ManualRoom>?,
    val handlers: List<HandlerInfo>?,
    val plugins: List<PluginInfo>?,
    val version: String?,
    val signals: ManualSignals,
)

@Composable
private fun rememberLive(): Live {
    val rooms = rememberApi { it.api.get("/api/satellites").decode<List<ManualRoom>>() }
    val handlers = rememberApi { it.api.get("/api/capabilities/manual").jsonObject["handlers"]!!.decode<List<HandlerInfo>>() }
    val plugins = rememberApi { it.api.get("/api/plugins").jsonObject["plugins"]!!.decode<List<PluginInfo>>() }
    val version = rememberApi { it.api.get("/api/config/version").jsonObject["version"]?.jsonPrimitive?.contentOrNull }
    val people = rememberApi { it.api.get("/api/people").jsonArray.size }
    val tracks = rememberApi { it.api.get("/api/music/library/stats").jsonObject["total_tracks"]?.jsonPrimitive?.longOrNull }
    val heard = rememberApi(rooms.data) { a ->
        val list = rooms.data ?: return@rememberApi null
        if (list.isEmpty()) return@rememberApi false
        list.take(3).any { r ->
            runCatching { a.api.get("/api/satellites/${r.roomId}/sessions?limit=1").jsonArray.isNotEmpty() }.getOrDefault(false)
        }
    }
    return Live(
        rooms.data, handlers.data, plugins.data, version.data,
        ManualSignals(rooms.data, heard.data, people.data, tracks.data),
    )
}

/** What links in the manual do. */
private class ManualLinks(
    val doc: ManualDoc,
    val wake: WakeInfo,
    val onNode: (String) -> Unit,
    val onRoute: (String) -> Unit,
    val onDoc: (String) -> Unit,
)

// ─── the manual ───────────────────────────────────────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ManualBody(doc: ManualDoc, cached: Boolean, navigate: (Route) -> Unit) {
    val app = LocalApp.current
    val context = LocalContext.current
    val server by app.prefs.serverUrl.collectAsState()
    val openSettingsTab = LocalOpenSettingsTab.current
    val live = rememberLive()
    var at by rememberSaveable { mutableStateOf("start") }
    val reduce = remember {
        runCatching { Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }
            .getOrDefault(false)
    }

    val visible = remember(doc, live.plugins) {
        val on = live.plugins.orEmpty().filter { it.enabled && (it.status == "ok" || it.status == "degraded") }.map { it.slug }.toSet()
        doc.pieces.filter { it.plugin == null || it.plugin in on }.map { it.id }.toSet()
    }
    val go: (String) -> Unit = { id ->
        at = when {
            id == "map" -> "map"
            doc.topic(id) != null -> "t:$id"
            doc.piece(id) != null -> "p:${doc.piece(id)!!.aliasOf ?: id}"
            else -> "start"
        }
    }
    val up: () -> Unit = {
        at = when {
            pieceOf(at) != null -> "t:${doc.piece(pieceOf(at))?.topic}"
            topicOf(at) != null -> "map"
            else -> "start"
        }
    }
    BackHandler(enabled = at != "start") { up() }

    val links = ManualLinks(
        doc = doc,
        wake = wakeInfo(doc.wakeWord, live.rooms),
        onNode = go,
        onRoute = { id ->
            val r = doc.routes[id]
            when {
                r == null -> Unit
                r.app.dashboard -> r.dashboard?.let { h -> dashboardUrl(server, h)?.let { openWebLink(context, it) } }
                r.app.screen == "Settings" && r.app.tab != null -> openSettingsTab(r.app.tab)
                else -> Route.entries.firstOrNull { it.name == r.app.screen }?.let(navigate)
            }
        },
        onDoc = { target -> openWebLink(context, docUrl(doc, live.version, target)) },
    )

    Column(Modifier.fillMaxSize()) {
        ManualTop(doc, at, visible, go, up)
        if (cached) {
            Text(
                "this server can't be reached right now: the manual from last time",
                style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.warn,
                modifier = Modifier.padding(horizontal = 16.dp),
            )
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            val piece = doc.piece(pieceOf(at))
            when {
                at == "start" -> StartPage(doc, live, links) { go("map") }
                piece != null -> PiecePage(doc, piece, visible, live, links)
                else -> BoxWithConstraints(Modifier.fillMaxSize()) {
                    // A phone's map is taller than the space under the bar
                    // and the search: it scrolls, at the height its rows need.
                    val stage = if (maxWidth.value < PHONE_MAX_DP) phoneStageHeight() else max(maxHeight.value - 40f, 420f)
                    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 12.dp)) {
                    ManualMap(doc, visible, topicOf(at), reduce, go, Modifier.height(stage.dp))
                    val t = doc.topic(topicOf(at))
                    Text(
                        t?.intro ?: if (t == null) doc.label("map_hint") else "",
                        style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint,
                        modifier = Modifier.padding(vertical = 8.dp),
                    )
                    }
                }
            }
        }
    }
}

@Composable
private fun ManualTop(doc: ManualDoc, at: String, visible: Set<String>, go: (String) -> Unit, up: () -> Unit) {
    val focus = LocalFocusManager.current
    var query by rememberSaveable { mutableStateOf("") }
    val results = remember(query, visible) { searchManual(doc, visible, query) }
    Column(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            SegPill(doc.label("start_here", fallback = "start here"), at == "start") { go("start") }
            SegPill(doc.label("map", fallback = "map"), at != "start") { go("map") }
        }
        if (at != "start") Crumbs(doc, at, go, up)
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            singleLine = true,
            placeholder = { Text(doc.label("search_placeholder", fallback = "search the manual")) },
            leadingIcon = { Icon(Icons.Outlined.Search, contentDescription = null) },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = {
                results.firstOrNull()?.let { go(it.id); query = ""; focus.clearFocus() }
            }),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Domovoi.colors.brand, unfocusedBorderColor = Domovoi.colors.border,
            ),
        )
        if (query.isNotBlank()) {
            Surface(
                shape = RoundedCornerShape(10.dp), color = Domovoi.colors.card,
                border = BorderStroke(1.dp, Domovoi.colors.border), modifier = Modifier.fillMaxWidth(),
            ) {
                Column {
                    if (results.isEmpty()) {
                        Text(
                            doc.label("search_empty", fallback = "nothing matches"),
                            style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                            modifier = Modifier.padding(12.dp),
                        )
                    }
                    results.forEachIndexed { i, p ->
                        if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                        Column(
                            Modifier.fillMaxWidth()
                                .clickable(role = Role.Button) { go(p.id); query = ""; focus.clearFocus() }
                                .padding(horizontal = 12.dp, vertical = 9.dp),
                        ) {
                            Text(p.title, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                            Text(
                                "${doc.topic(p.topic)?.title ?: ""} · ${p.subtitle}",
                                style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted,
                            )
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SegPill(label: String, active: Boolean, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(6.dp),
        color = if (active) Domovoi.colors.brandSoft else Domovoi.colors.card,
        border = BorderStroke(1.dp, if (active) Domovoi.colors.brand else Domovoi.colors.border),
        modifier = Modifier.heightIn(min = 40.dp),
    ) {
        Box(Modifier.padding(horizontal = 14.dp, vertical = 9.dp), contentAlignment = Alignment.Center) {
            Text(label, style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fg)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Crumbs(doc: ManualDoc, at: String, go: (String) -> Unit, up: () -> Unit) {
    val piece = doc.piece(pieceOf(at))
    val topic = doc.topic(topicOf(at) ?: piece?.topic)
    FlowRow(verticalArrangement = Arrangement.Center, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Crumb(doc.centre.title, topic != null) { go("map") }
        if (topic != null) {
            Icon(Icons.Outlined.ChevronRight, null, Modifier.size(16.dp).align(Alignment.CenterVertically), tint = Domovoi.colors.fgFaint)
            Crumb(topic.title, piece != null) { go(topic.id) }
        }
        if (piece != null) {
            Icon(Icons.Outlined.ChevronRight, null, Modifier.size(16.dp).align(Alignment.CenterVertically), tint = Domovoi.colors.fgFaint)
            Crumb(piece.title, false) {}
        }
        if (topic != null) {
            TextButton(onClick = up) {
                Icon(Icons.Outlined.ZoomOut, null, Modifier.size(16.dp))
                Spacer(Modifier.width(4.dp))
                Text(doc.label("zoom_out", fallback = "zoom out"), style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}

@Composable
private fun Crumb(text: String, link: Boolean, onClick: () -> Unit) {
    Text(
        text,
        style = MaterialTheme.typography.labelLarge,
        color = if (link) Domovoi.colors.fgMuted else Domovoi.colors.fg,
        modifier = (if (link) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            .padding(vertical = 10.dp),
    )
}

// ─── the map ──────────────────────────────────────────────────────────────

private data class Pos(val x: Float, val y: Float, val s: Float)

private fun ring(n: Int, w: Float, h: Float, phone: Boolean, nw: Float, nh: Float): List<Pair<Float, Float>> {
    if (!phone) {
        val rx = max(w / 2 - nw / 2 - 14, 150f)
        val ry = max(h / 2 - nh / 2 - 14, 110f)
        return List(n) { i ->
            val a = (-90.0 + i * 360.0 / n) * PI / 180
            (rx * cos(a)).toFloat() to (ry * sin(a)).toFloat()
        }
    }
    val rows = ceil(n / 2.0).toInt()
    val upRows = ceil(rows / 2.0).toInt()
    val step = nh + PHONE_ROW_GAP
    val dx = nw / 2 + 5
    return List(n) { i ->
        val r = i / 2
        val y = (if (r < upRows) -(upRows - r) else r - upRows + 1) * step
        val alone = i == n - 1 && n % 2 == 1
        (if (alone) 0f else if (i % 2 == 1) dx else -dx) to y
    }
}

@Composable
private fun ManualMap(
    doc: ManualDoc, visible: Set<String>, focus: String?, reduce: Boolean,
    go: (String) -> Unit, modifier: Modifier,
) {
    BoxWithConstraints(
        modifier.fillMaxWidth().clipToBounds()
            .background(Domovoi.colors.sunken, RoundedCornerShape(14.dp))
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(14.dp)),
    ) {
        val w = maxWidth.value
        val h = maxHeight.value
        val phone = w < PHONE_MAX_DP
        val nw = if (phone) (w - 14) / 2 else 196f
        val nh = if (phone) PHONE_NODE_DP else 66f
        val ringTopics = doc.ringTopics
        val centreTopic = doc.centre.opens

        // World positions (dp): the centre, the topics, every cluster's hub and pieces.
        val geo = remember(doc, w, h, visible) {
            val g = LinkedHashMap<String, Pos>()
            g["domovoi"] = Pos(0f, 0f, 1f)
            ring(ringTopics.size, w, h, phone, nw, nh).forEachIndexed { i, (x, y) -> g["t:${ringTopics[i].id}"] = Pos(x, y, 1f) }
            doc.topics.forEach { t ->
                val c = if (t.id == centreTopic) Pos(0f, 0f, 1f) else g["t:${t.id}"]!!
                g["h:${t.id}"] = Pos(c.x, c.y, CLUSTER)
                val kids = t.pieces.filter { it in visible }
                ring(kids.size, w, h, phone, nw, nh).forEachIndexed { j, (x, y) ->
                    g["p:${kids[j]}"] = Pos(c.x + x * CLUSTER, c.y + y * CLUSTER, CLUSTER)
                }
            }
            g
        }
        val focusTopic = doc.topic(focus)
        val shown: Set<String> = if (focusTopic == null) {
            setOf("domovoi") + ringTopics.map { "t:${it.id}" }
        } else {
            setOf("h:${focusTopic.id}") + focusTopic.pieces.filter { it in visible }.map { "p:$it" }
        }
        val edges: List<Pair<String, String>> = if (focusTopic == null) {
            ringTopics.map { "domovoi" to "t:${it.id}" }
        } else {
            focusTopic.pieces.filter { it in visible }.map { "h:${focusTopic.id}" to "p:$it" }
        }

        // The camera: level 1 looks at the centre at 1:1, level 2 at a cluster at 1/CLUSTER.
        val cam = focusTopic?.let { geo["h:${it.id}"] } ?: Pos(0f, 0f, 1f)
        val spec = if (reduce) snap<Float>() else tween<Float>(MOVE_MS, easing = EASE)
        val k by animateFloatAsState(if (focusTopic != null) 1 / CLUSTER else 1f, spec, label = "zoom")
        val cx by animateFloatAsState(cam.x, spec, label = "cx")
        val cy by animateFloatAsState(cam.y, spec, label = "cy")
        val density = LocalDensity.current.density

        // One clock for the float; nothing floats with animations off.
        val clock = rememberInfiniteTransition(label = "float")
        val t by clock.animateFloat(0f, 600f, infiniteRepeatable(tween(600_000, easing = LinearEasing)), label = "t")
        val ids = remember(geo) { geo.keys.toList() }
        fun bob(id: String, time: Float): Float {
            if (reduce || id !in shown) return 0f
            val i = ids.indexOf(id)
            val dur = 5.5f + (i % 4) * 0.9f
            return 5f * sin(2 * PI.toFloat() * (time + i * 0.7f) / dur) * (geo[id]?.s ?: 1f)
        }

        val wire = Domovoi.colors.borderStrong
        Canvas(Modifier.fillMaxSize()) {
            val time = t
            val dash = PathEffect.dashPathEffect(floatArrayOf(4 * density, 4 * density))
            edges.forEach { (a, b) ->
                val pa = geo[a] ?: return@forEach
                val pb = geo[b] ?: return@forEach
                val ax = ((pa.x - cx) * k + w / 2) * density
                val bx = ((pb.x - cx) * k + w / 2) * density
                val ay = ((pa.y + bob(a, time) - cy) * k + h / 2) * density
                val by = ((pb.y + bob(b, time) - cy) * k + h / 2) * density
                val my = (ay + by) / 2
                val path = Path().apply { moveTo(ax, ay); cubicTo(ax, my, bx, my, bx, by) }
                drawPath(path, wire, style = Stroke(width = 1.4f * density, pathEffect = dash), alpha = 0.8f)
            }
        }

        // The layer the camera moves: world (0,0) at the stage's middle.
        Box(
            Modifier.fillMaxSize().graphicsLayer {
                transformOrigin = TransformOrigin(0f, 0f)
                scaleX = k
                scaleY = k
                translationX = (w / 2 - cx * k) * density
                translationY = (h / 2 - cy * k) * density
            },
        ) {
            val levelKey = focus ?: "map"
            geo.forEach { (id, p) ->
                val title: String
                val sub: String?
                val icon: ImageVector?
                var preview = false
                var plugin = false
                var hub = false
                val onClick: () -> Unit
                when {
                    id == "domovoi" -> { title = doc.centre.title; sub = doc.centre.subtitle; icon = null; hub = true; onClick = { go(centreTopic) } }
                    id.startsWith("t:") -> {
                        val tp = doc.topic(id.removePrefix("t:"))!!
                        title = tp.title; sub = tp.subtitle; icon = TOPIC_ICONS[tp.id] ?: Icons.Outlined.HelpOutline
                        onClick = { go(tp.id) }
                    }
                    id.startsWith("h:") -> {
                        val tp = doc.topic(id.removePrefix("h:"))!!
                        title = tp.title; sub = tp.subtitle; icon = TOPIC_ICONS[tp.id] ?: Icons.Outlined.HelpOutline
                        hub = true; onClick = { go("map") }
                    }
                    else -> {
                        val pc = doc.piece(id.removePrefix("p:"))!!
                        title = pc.title; sub = pc.subtitle; icon = null; preview = pc.preview; plugin = pc.plugin != null
                        onClick = { go(pc.id) }
                    }
                }
                AnimatedVisibility(
                    visible = id in shown,
                    enter = fadeIn(if (reduce) snap() else tween(FADE_MS)),
                    exit = fadeOut(if (reduce) snap() else tween(FADE_MS)),
                    modifier = Modifier
                        .offset(x = (p.x - nw / 2).dp, y = (p.y - nh / 2).dp)
                        .width(nw.dp)
                        .graphicsLayer {
                            scaleX = p.s
                            scaleY = p.s
                            translationY = bob(id, t) * density
                        },
                ) {
                    MapNode(title, sub, icon, preview, plugin, hub, nh.dp, phone, levelKey, reduce, onClick)
                }
            }
        }
    }
}

@Composable
private fun MapNode(
    title: String, sub: String?, icon: ImageVector?, preview: Boolean, plugin: Boolean, hub: Boolean,
    minHeight: Dp, fixed: Boolean, levelKey: String, reduce: Boolean, onClick: () -> Unit,
) {
    // On a phone every node is one size, so the rows never run into each
    // other: two lines of name, two of subtitle, a pill folded into it.
    val tags = listOfNotNull("preview".takeIf { preview }, "plugin".takeIf { plugin })
    Surface(
        shape = RoundedCornerShape(10.dp),
        color = Domovoi.colors.card,
        border = BorderStroke(1.dp, if (hub) Domovoi.colors.borderStrong else Domovoi.colors.border),
        shadowElevation = 1.dp,
        modifier = Modifier.fillMaxWidth().then(if (fixed) Modifier.height(minHeight) else Modifier.heightIn(min = minHeight))
            .clickable(role = Role.Button, onClick = onClick)
            .semantics { contentDescription = listOfNotNull(title, sub).joinToString(": ") },
    ) {
        Column(Modifier.padding(horizontal = 11.dp, vertical = 9.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                if (icon != null) Icon(icon, null, Modifier.size(15.dp), tint = Domovoi.colors.brand)
                ScrambleText(title, levelKey, reduce)
            }
            if (sub != null || tags.isNotEmpty()) {
                Text(
                    buildAnnotatedString {
                        append(sub.orEmpty())
                        tags.forEach { t ->
                            append(if (length > 0) "  " else "")
                            withStyle(SpanStyle(color = Domovoi.colors.fgMuted, background = Domovoi.colors.idleSoft, fontSize = 11.sp)) {
                                append(" $t ")
                            }
                        }
                    },
                    style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                    maxLines = 2, overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

/** A node's name, which scrambles into place as its level opens. */
@Composable
private fun ScrambleText(text: String, key: String, reduce: Boolean) {
    // (two lines at most: a phone's node is one fixed size)
    var shown by remember(text) { mutableStateOf(text) }
    LaunchedEffect(key, text) {
        if (reduce) { shown = text; return@LaunchedEffect }
        val steps = 8
        for (frame in 0..steps) {
            shown = buildString {
                text.forEachIndexed { i, c ->
                    append(if (c == ' ' || i < frame.toFloat() / steps * text.length) c else GLYPHS[Random.nextInt(GLYPHS.length)])
                }
            }
            delay(24)
        }
        shown = text
    }
    Text(
        shown,
        style = MaterialTheme.typography.titleSmall.copy(fontFamily = MonoFamily, fontWeight = FontWeight.SemiBold, fontSize = 13.sp),
        color = Domovoi.colors.fg,
        maxLines = 2, overflow = TextOverflow.Ellipsis,
    )
}

// ─── pages ────────────────────────────────────────────────────────────────

@Composable
private fun StartPage(doc: ManualDoc, live: Live, links: ManualLinks, onMap: () -> Unit) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            Text(doc.label("eyebrow", fallback = "user manual"), style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
            Text(
                doc.start.title, style = MaterialTheme.typography.headlineSmall, color = Domovoi.colors.fg,
                modifier = Modifier.semantics { heading() },
            )
        }
        items(doc.start.blocks.filter { shownInApp(it.only) }) { b ->
            if (b.type == "checklist") Checklist(doc, live, links) else Block(b, doc, live, links)
        }
        item {
            Button(onClick = onMap, modifier = Modifier.padding(top = 8.dp)) {
                Icon(Icons.Outlined.Map, null, Modifier.size(16.dp))
                Spacer(Modifier.width(6.dp))
                Text(doc.label("show_map", fallback = "show me the map"))
            }
        }
    }
}

@Composable
private fun PiecePage(doc: ManualDoc, piece: ManualPiece, visible: Set<String>, live: Live, links: ManualLinks) {
    val siblings = doc.topic(piece.topic)?.pieces?.filter { it in visible }.orEmpty()
    val next = siblings.getOrNull(siblings.indexOf(piece.id) + 1)?.let { doc.piece(it) }
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            Text(
                (doc.topic(piece.topic)?.title ?: "").lowercase(),
                style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted,
                modifier = Modifier.clickable(role = Role.Button) { links.onNode(piece.topic) }.padding(vertical = 4.dp),
            )
            Text(
                piece.title, style = MaterialTheme.typography.headlineSmall, color = Domovoi.colors.fg,
                modifier = Modifier.semantics { heading() },
            )
            if (piece.preview || piece.plugin != null) {
                Row(Modifier.padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (piece.preview) Pill("preview")
                    if (piece.plugin != null) Pill("plugin")
                }
            }
        }
        items(piece.blocks.filter { shownInApp(it.only) }) { b -> Block(b, doc, live, links) }
        item {
            Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                piece.more?.let { m ->
                    TextButton(onClick = { links.onDoc(m.target) }) {
                        Text(m.text)
                        Spacer(Modifier.width(4.dp))
                        Icon(Icons.AutoMirrored.Outlined.OpenInNew, null, Modifier.size(14.dp))
                    }
                }
                Spacer(Modifier.weight(1f))
                next?.let { n ->
                    TextButton(onClick = { links.onNode(n.id) }) { Text("${doc.label("next", fallback = "next")}: ${n.title}") }
                }
            }
        }
    }
}

@Composable
private fun Block(b: ManualBlock, doc: ManualDoc, live: Live, links: ManualLinks) {
    val c = Domovoi.colors
    when (b.type) {
        "p" -> ManualText(b.text, links)
        "list" -> Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            b.items.filter { shownInApp(it.only) }.forEachIndexed { i, it ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(if (b.ordered) "${i + 1}." else "•", style = MaterialTheme.typography.bodyMedium, color = c.fgFaint)
                    ManualText(it.text, links)
                }
            }
        }
        "label" -> Row(
            Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                b.text, style = MaterialTheme.typography.titleSmall,
                color = if (b.role == "wrong") c.warn else c.fg, fontWeight = FontWeight.SemiBold,
            )
            if (b.admin) Pill(doc.label("admin", "text", "admin"), Tone.Warn)
            b.note?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = c.fgMuted) }
        }
        "say" -> {
            // What the language models answer is left out while the server's
            // AI features are off (features.ai false).
            val phrases = shownPhrases(b.phrases, LocalCapabilities.current.features)
            if (phrases.isNotEmpty()) Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                phrases.forEachIndexed { i, p ->
                    if (p.then) return@forEachIndexed
                    val next = phrases.getOrNull(i + 1)?.takeIf { it.then }
                    SayRow(p, next, doc, links)
                }
            }
        }
        "table" -> Column(Modifier.border(1.dp, c.borderSoft, RoundedCornerShape(8.dp))) {
            b.rows.forEachIndexed { i, row ->
                if (i > 0) HorizontalDivider(color = c.borderSoft)
                Column(Modifier.fillMaxWidth().padding(10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    row.getOrNull(0)?.let { ManualText(it, links, color = c.fg) }
                    row.getOrNull(1)?.let { ManualText(it, links, color = c.fgMuted) }
                }
            }
        }
        "live" -> LiveSlot(b.slot, doc, live, links)
        else -> Unit
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SayRow(p: ManualPhrase, then: ManualPhrase?, doc: ManualDoc, links: ManualLinks) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Phrase(p.text)
        if (then != null) {
            Text("then", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, modifier = Modifier.align(Alignment.CenterVertically))
            Phrase(then.text)
        }
        p.note?.let { Box(Modifier.align(Alignment.CenterVertically)) { ManualText(it, links, color = Domovoi.colors.fgMuted, small = true) } }
        if (p.pending != null) Pill(doc.label("pending", "text", "coming"))
    }
}

@Composable
private fun Phrase(text: String) {
    Text(
        "“$text”",
        style = MaterialTheme.typography.bodySmall.copy(fontFamily = MonoFamily),
        color = Domovoi.colors.fg,
        modifier = Modifier
            .background(Domovoi.colors.sunken, RoundedCornerShape(6.dp))
            .border(1.dp, Domovoi.colors.borderSoft, RoundedCornerShape(6.dp))
            .padding(horizontal = 7.dp, vertical = 3.dp),
    )
}

/** The file's inline markup, with links that open the app's screens, the
 *  dashboard, the docs, or another page of the manual. */
@Composable
private fun ManualText(text: String, links: ManualLinks, color: Color = Domovoi.colors.fg, small: Boolean = false) {
    val c = Domovoi.colors
    val doc = links.doc
    val caps = LocalCapabilities.current
    val annotated: AnnotatedString =
        buildAnnotatedString {
            parseInline(text).forEach { s ->
                when (s) {
                    is Span.Plain -> append(s.text)
                    is Span.Bold -> withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(s.text) }
                    is Span.Italic -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { append(s.text) }
                    is Span.Code -> withStyle(SpanStyle(fontFamily = MonoFamily, background = c.sunken)) { append(s.text) }
                    is Span.Wake -> withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) {
                        append(fill(doc.wakeWord.quoted, mapOf("phrase" to links.wake.lead)))
                    }
                    is Span.Tag -> {
                        val (label, fg, bg) = when (s.name) {
                            "admin" -> Triple(doc.label("admin", "text", "admin"), c.warn, c.warnSoft)
                            "preview" -> Triple(doc.label("preview", "text", "preview"), c.fgMuted, c.idleSoft)
                            else -> Triple(doc.label("plugin", "text", "plugin"), c.fgMuted, c.idleSoft)
                        }
                        withStyle(SpanStyle(color = fg, background = bg, fontSize = 11.sp)) { append(" $label ") }
                    }
                    is Span.Link -> {
                        val route = if (s.kind == "route") doc.routes[s.target] else null
                        // A route the app can't open here (no app screen, or one this
                        // server doesn't offer: Chat with AI off) reads as plain text.
                        if (route != null && !manualRouteOpens(route, caps)) {
                            append(s.text)
                        } else {
                            withLink(
                                LinkAnnotation.Clickable(
                                    tag = "${s.kind}:${s.target}",
                                    styles = TextLinkStyles(SpanStyle(color = c.brandPress, textDecoration = TextDecoration.Underline)),
                                ) {
                                    when (s.kind) {
                                        "node" -> links.onNode(s.target)
                                        "doc" -> links.onDoc(s.target)
                                        else -> links.onRoute(s.target)
                                    }
                                },
                            ) { append(s.text) }
                        }
                    }
                }
            }
        }
    Text(
        annotated,
        style = if (small) MaterialTheme.typography.bodySmall else MaterialTheme.typography.bodyMedium,
        color = color,
    )
}

// ─── live slots ───────────────────────────────────────────────────────────

@Composable
private fun LiveSlot(slot: String?, doc: ManualDoc, live: Live, links: ManualLinks) {
    val c = Domovoi.colors
    when (slot) {
        "wake-word" -> wakeNote(doc.wakeWord, links.wake)?.let {
            Text(
                it, style = MaterialTheme.typography.bodySmall, color = c.fg,
                modifier = Modifier.fillMaxWidth().background(c.brandSoft, RoundedCornerShape(10.dp)).padding(10.dp),
            )
        }
        "rooms" -> live.rooms?.let { rooms ->
            LiveBox {
                if (rooms.isEmpty()) LiveEmpty(doc.label("rooms_none", fallback = "no rooms yet"))
                rooms.forEachIndexed { i, r ->
                    if (i > 0) HorizontalDivider(color = c.borderSoft)
                    Row(
                        Modifier.fillMaxWidth().clickable(role = Role.Button) { links.onRoute("settings/satellites") }.padding(10.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        StatusDot(if (r.status == "online") Tone.Ok else if (r.status == "waiting") Tone.Warn else Tone.Idle, live = r.status == "online")
                        Column(Modifier.weight(1f)) {
                            Text(r.name, style = MaterialTheme.typography.bodyMedium, color = c.fg, fontWeight = FontWeight.SemiBold)
                            val word = wakePhraseOf(doc.wakeWord, r)?.let { fill(doc.wakeWord.quoted, mapOf("phrase" to it)) }
                            val np = r.nowPlaying?.title ?: r.nowPlaying?.file
                            Text(
                                listOfNotNull(r.status, word, np).joinToString(" · "),
                                style = MaterialTheme.typography.bodySmall, color = c.fgMuted,
                            )
                        }
                    }
                }
            }
        }
        "plugins" -> live.plugins?.let { plugins ->
            LiveBox {
                if (plugins.isEmpty()) LiveEmpty(doc.label("plugins_none", fallback = "no plugins installed"))
                plugins.forEachIndexed { i, p ->
                    if (i > 0) HorizontalDivider(color = c.borderSoft)
                    Row(Modifier.fillMaxWidth().padding(10.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(p.name ?: p.slug, style = MaterialTheme.typography.bodyMedium, color = c.fg, modifier = Modifier.weight(1f))
                        p.version?.let { Text(it, style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily), color = c.fgMuted) }
                        Pill(if (p.enabled) p.status ?: "" else "disabled", if (p.enabled && p.status == "ok") Tone.Ok else Tone.Idle)
                    }
                }
            }
        }
        "things-to-say" -> live.handlers?.let { all ->
            val ours = remember(doc) { phrasesByHandler(doc) }
            val features = LocalCapabilities.current.features
            val listed = listedHandlers(doc, all.map { it.name }, features).toSet()
            val handlers = all.filter { it.name in listed }
            LiveBox {
                handlers.forEachIndexed { i, h ->
                    if (i > 0) HorizontalDivider(color = c.borderSoft)
                    val said = h.examplePhrases.filter { it.isNotBlank() }.ifEmpty { ours[h.name].orEmpty() }
                    ThingsRow(h, said, doc)
                }
            }
        }
        else -> Unit
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ThingsRow(h: HandlerInfo, said: List<String>, doc: ManualDoc) {
    val c = Domovoi.colors
    Column(Modifier.fillMaxWidth().padding(10.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Text(h.display?.label ?: h.name, style = MaterialTheme.typography.bodyMedium, color = c.fg, fontWeight = FontWeight.SemiBold)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(5.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            if (said.isEmpty()) Text(doc.label("say_none", fallback = "no phrases listed"), style = MaterialTheme.typography.bodySmall, color = c.fgMuted)
            said.take(3).forEach { Phrase(it) }
            if (h.origin != null && h.origin != "core") Pill("${doc.label("plugin", "text", "plugin")}: ${h.origin}")
            h.requiresNetwork?.let { net ->
                val words = runCatching { (doc.labels["net"] as? JsonObject)?.str(net) }.getOrNull()
                if (words != null) Pill(words)
            }
        }
    }
}

@Composable
private fun LiveBox(content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().border(1.dp, Domovoi.colors.borderSoft, RoundedCornerShape(10.dp))) { content() }
}

@Composable
private fun LiveEmpty(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, modifier = Modifier.padding(10.dp))
}

@Composable
private fun Checklist(doc: ManualDoc, live: Live, links: ManualLinks) {
    val c = Domovoi.colors
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        appChecklist(doc, live.signals).forEach { (text, done) ->
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Icon(
                    if (done == true) Icons.Outlined.CheckCircle else Icons.Outlined.Circle,
                    contentDescription = when (done) { true -> "done"; false -> "not yet"; null -> doc.label("checklist_unknown") },
                    tint = if (done == true) c.ok else c.fgFaint,
                    modifier = Modifier.size(18.dp).padding(top = 2.dp),
                )
                ManualText(text, links, color = if (done == true) c.fgMuted else c.fg)
            }
        }
    }
}
