@file:OptIn(ExperimentalMaterial3Api::class)

package com.domovoi.app.ui.screens.videos

import android.content.Intent
import android.net.Uri
import androidx.annotation.OptIn as AndroidxOptIn
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.drag
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.pager.VerticalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.FastForward
import androidx.compose.material.icons.outlined.FastRewind
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.automirrored.outlined.PlaylistPlay
import androidx.compose.material.icons.outlined.RestartAlt
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.DialogWindowProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
import android.os.Build
import android.view.WindowManager
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import coil.compose.SubcomposeAsyncImage
import com.domovoi.app.LocalApp
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private enum class FeedSheet { Options, Playlist, Details }

private const val SEEK_STEP_MS = 15_000L

/**
 * Full-screen vertical swipe feed (ported from the owner's ScrollPlayer):
 * swipe up / down for the next / previous video in the list it was opened
 * from, tap to pause, double-tap the left or right third to skip 15 s,
 * long-press for the queue, details, start over and save/share, scrub along
 * the bottom edge. Each video loops until you swipe.
 *
 * One ExoPlayer serves the whole feed and moves to whichever page has
 * settled, so flinging through the list never stacks up players or
 * rebuffers pages nobody is watching.
 *
 * [resumeSec] applies to the tapped video only; everything else starts from
 * the beginning (see [startPositionMs]). [onPersist] fires every 5 s while
 * playing, when a page is left, when a video loops (ended = true) and on
 * close. [onClose] gets the key of the video on screen, or null when the
 * viewer emptied the queue.
 */
@AndroidxOptIn(UnstableApi::class)
@Composable
fun VideoFeedPlayer(
    videos: List<FeedVideo>,
    startIndex: Int,
    resumeSec: Long = 0,
    onSave: ((FeedVideo) -> Unit)? = null,
    onPersist: (suspend (video: FeedVideo, posSec: Long, durSec: Long?, ended: Boolean) -> Unit)? = null,
    onClose: (currentKey: String?) -> Unit,
) {
    val app = LocalApp.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val queue = remember { mutableStateListOf<FeedVideo>().apply { addAll(videos) } }
    var current by remember { mutableIntStateOf(startIndex.coerceIn(0, (videos.size - 1).coerceAtLeast(0))) }
    var resumeUsed by remember { mutableStateOf(false) }
    var isPlaying by remember { mutableStateOf(false) }
    var positionMs by remember { mutableLongStateOf(0L) }
    var durationMs by remember { mutableLongStateOf(0L) }
    var failedKey by remember { mutableStateOf<String?>(null) }
    var sheet by remember { mutableStateOf<FeedSheet?>(null) }
    var seekFlash by remember { mutableStateOf<Pair<Boolean, Int>?>(null) }
    /** The video the player currently holds, so leaving a page can save its position. */
    var loaded by remember { mutableStateOf<FeedVideo?>(null) }

    val player = remember {
        // One audio stream at a time in the house — stop the music first.
        runCatching { if (app.player.exoPlayer.isPlaying) app.player.exoPlayer.pause() }
        ExoPlayer.Builder(context)
            .setMediaSourceFactory(
                DefaultMediaSourceFactory(
                    DefaultDataSource.Factory(context, OkHttpDataSource.Factory(app.api.http)),
                ),
            )
            .build()
            .apply {
                repeatMode = Player.REPEAT_MODE_ONE
                playWhenReady = true
                // Snap to keyframes so dragging the scrubber stays responsive.
                setSeekParameters(SeekParameters.CLOSEST_SYNC)
            }
    }

    fun persist(video: FeedVideo?, ended: Boolean) {
        val cb = onPersist ?: return
        val v = video ?: return
        val pos = player.currentPosition.coerceAtLeast(0) / 1000
        val dur = player.duration.takeIf { it != C.TIME_UNSET && it > 0 }?.div(1000)
        app.scope.launch { cb(v, pos, dur, ended) }
    }

    fun activate(page: Int) {
        val video = queue.getOrNull(page) ?: return
        current = page
        if (loaded?.key == video.key && player.mediaItemCount > 0) {
            player.play()
            return
        }
        if (loaded != null) persist(loaded, ended = false)
        failedKey = null
        player.setMediaItem(MediaItem.fromUri(video.uri), startPositionMs(page, startIndex, resumeSec, resumeUsed))
        // The resume is spent on the first page shown: swiping back to the
        // tapped video later starts it over like any other.
        resumeUsed = true
        player.prepare()
        player.play()
        loaded = video
    }

    DisposableEffect(player) {
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(playing: Boolean) {
                isPlaying = playing
            }

            override fun onPositionDiscontinuity(
                oldPosition: Player.PositionInfo,
                newPosition: Player.PositionInfo,
                reason: Int,
            ) {
                // REPEAT_MODE_ONE wraps with an auto transition: the video was watched to the end.
                if (reason == Player.DISCONTINUITY_REASON_AUTO_TRANSITION) {
                    val cb = onPersist ?: return
                    val v = loaded ?: return
                    val dur = player.duration.takeIf { it != C.TIME_UNSET && it > 0 }?.div(1000)
                    app.scope.launch { cb(v, 0, dur, true) }
                }
            }

            override fun onPlayerError(error: PlaybackException) {
                failedKey = loaded?.key
            }
        }
        player.addListener(listener)
        onDispose {
            persist(loaded, ended = false)
            player.removeListener(listener)
            player.release()
        }
    }

    // Scrubber + periodic resume saves.
    LaunchedEffect(player) {
        var sinceSave = 0L
        while (true) {
            positionMs = player.currentPosition.coerceAtLeast(0L)
            durationMs = player.duration.takeIf { it != C.TIME_UNSET }?.coerceAtLeast(0L) ?: 0L
            val step = if (isPlaying) 250L else 500L
            delay(step)
            sinceSave += step
            if (sinceSave >= 5_000L) {
                sinceSave = 0
                if (player.isPlaying) persist(loaded, ended = false)
            }
        }
    }
    LaunchedEffect(seekFlash) {
        if (seekFlash != null) {
            delay(650)
            seekFlash = null
        }
    }

    fun seekTo(ms: Long) {
        val dur = player.duration.takeIf { it != C.TIME_UNSET }
        val target = if (dur != null) ms.coerceIn(0L, dur) else ms.coerceAtLeast(0L)
        player.seekTo(target)
        positionMs = target
    }

    fun close() = onClose(queue.getOrNull(current)?.key)

    Dialog(
        onDismissRequest = ::close,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        EdgeToEdgeDialogWindow()
        Box(Modifier.fillMaxSize().background(Color.Black)) {
            if (queue.isNotEmpty()) {
                val pagerState = rememberPagerState(initialPage = current) { queue.size }

                LaunchedEffect(pagerState) {
                    snapshotFlow { pagerState.settledPage }.collect { activate(it) }
                }
                // A playlist pick or a removal from the queue moves the current page; follow it.
                LaunchedEffect(pagerState, current, queue.size) {
                    if (pagerState.currentPage != current && current in queue.indices) {
                        pagerState.scrollToPage(current)
                    }
                }

                // ONE video surface for the whole feed, behind the pager, never
                // rebuilt. A surface per page was torn down and re-attached on
                // every swipe, and a swipe that changed the aspect ratio
                // (landscape into portrait) could leave the new one black.
                FeedSurface(player)

                VerticalPager(state = pagerState, modifier = Modifier.fillMaxSize(), key = { queue[it].key }) { page ->
                    val video = queue[page]
                    // The settled page is see-through so the surface shows; while a
                    // swipe is moving, every page is its poster on black instead.
                    val showsVideo = page == pagerState.settledPage &&
                        !pagerState.isScrollInProgress && loaded?.key == video.key
                    Box(
                        Modifier
                            .fillMaxSize()
                            .then(if (showsVideo) Modifier else Modifier.background(Color.Black))
                            .pointerInput(page) {
                                detectTapGestures(
                                    onTap = { if (player.isPlaying) player.pause() else player.play() },
                                    onLongPress = { sheet = FeedSheet.Options },
                                    onDoubleTap = { offset ->
                                        // Edge thirds skip 15 s; the middle third pauses like a tap.
                                        when {
                                            offset.x < size.width / 3f -> {
                                                seekTo(player.currentPosition - SEEK_STEP_MS)
                                                seekFlash = false to (seekFlash?.second ?: 0) + 1
                                            }
                                            offset.x > size.width * 2f / 3f -> {
                                                seekTo(player.currentPosition + SEEK_STEP_MS)
                                                seekFlash = true to (seekFlash?.second ?: 0) + 1
                                            }
                                            else -> if (player.isPlaying) player.pause() else player.play()
                                        }
                                    },
                                )
                            },
                        contentAlignment = Alignment.Center,
                    ) {
                        if (showsVideo) {
                            if (failedKey == video.key) {
                                FeedMessage("can't play this video", "swipe for the next one")
                            } else if (!isPlaying) {
                                Icon(
                                    Icons.Outlined.PlayArrow, contentDescription = "paused",
                                    tint = Color.White.copy(alpha = 0.85f),
                                    modifier = Modifier.size(88.dp)
                                        .background(Color.Black.copy(alpha = 0.35f), CircleShape)
                                        .padding(12.dp),
                                )
                            }
                        } else {
                            FeedPoster(video)
                        }
                    }
                }

                seekFlash?.let { (forward, _) ->
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier
                            .align(if (forward) Alignment.CenterEnd else Alignment.CenterStart)
                            .padding(horizontal = 24.dp)
                            .background(Color.Black.copy(alpha = 0.4f), CircleShape)
                            .padding(horizontal = 14.dp, vertical = 10.dp),
                    ) {
                        Icon(
                            if (forward) Icons.Outlined.FastForward else Icons.Outlined.FastRewind,
                            contentDescription = if (forward) "forward 15 seconds" else "back 15 seconds",
                            tint = Color.White, modifier = Modifier.size(28.dp),
                        )
                        Text("15 s", style = MaterialTheme.typography.labelLarge, color = Color.White,
                            modifier = Modifier.padding(start = 4.dp))
                    }
                }

                FeedScrubber(
                    positionMs = positionMs,
                    durationMs = durationMs,
                    onScrub = ::seekTo,
                    modifier = Modifier.align(Alignment.BottomCenter).navigationBarsPadding(),
                )

                queue.getOrNull(current)?.let { video ->
                    Column(
                        Modifier
                            .align(Alignment.BottomStart)
                            .navigationBarsPadding()
                            .padding(start = 16.dp, end = 72.dp, bottom = 36.dp),
                    ) {
                        Text(
                            video.title, style = MaterialTheme.typography.titleSmall, color = Color.White,
                            maxLines = 2, overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            "${current + 1} / ${queue.size}",
                            style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                            color = Color.White.copy(alpha = 0.7f),
                        )
                    }
                }
            }

            IconButton(
                onClick = ::close,
                modifier = Modifier.align(Alignment.TopEnd).statusBarsPadding().padding(8.dp),
            ) {
                Icon(Icons.Outlined.Close, contentDescription = "close", tint = Color.White)
            }
        }

        val video = queue.getOrNull(current)
        when (sheet) {
            FeedSheet.Options -> if (video != null) {
                FeedSheetFrame(video.title, onDismiss = { sheet = null }) {
                    FeedAction(Icons.AutoMirrored.Outlined.PlaylistPlay, "playlist") { sheet = FeedSheet.Playlist }
                    FeedAction(Icons.Outlined.Info, "details") { sheet = FeedSheet.Details }
                    FeedAction(Icons.Outlined.RestartAlt, "play from beginning") {
                        player.seekTo(0); player.play(); sheet = null
                    }
                    if (onSave != null) {
                        FeedAction(Icons.Outlined.Download, "save to device") { onSave(video); sheet = null }
                    }
                    if (video.shareable) {
                        FeedAction(Icons.Outlined.Share, "share") {
                            shareVideo(context, video); sheet = null
                        }
                    }
                }
            } else sheet = null

            FeedSheet.Playlist -> FeedPlaylistSheet(
                videos = queue,
                currentIndex = current,
                onSelect = { index ->
                    sheet = null
                    current = index
                },
                onRemove = { removed ->
                    val idx = queue.indexOfFirst { it.key == removed.key }
                    if (idx >= 0) {
                        val wasCurrent = idx == current
                        queue.removeAt(idx)
                        if (queue.isEmpty()) {
                            sheet = null
                            onClose(null)
                        } else {
                            current = indexAfterRemoval(current, idx, queue.size)
                            if (wasCurrent) scope.launch { activate(current) }
                        }
                    }
                },
                onDismiss = { sheet = null },
            )

            FeedSheet.Details -> if (video != null) {
                FeedDetailsSheet(video, player, onDismiss = { sheet = null })
            } else sheet = null

            null -> Unit
        }
    }
}

/**
 * A Compose Dialog's window is laid out below the status bar yet keeps the
 * full screen height, so its bottom (title, counter, scrubber) hangs off
 * the screen. Stretch it over the whole display, draw behind both bars with
 * them transparent, and let the content pad itself with the bar insets.
 */
@Composable
private fun EdgeToEdgeDialogWindow() {
    val window = (LocalView.current.parent as? DialogWindowProvider)?.window ?: return
    SideEffect {
        window.setLayout(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.setDimAmount(0f)
        // A dialog window is fitted inside the system bars by default, which is
        // what shifted it down; opt out so MATCH_PARENT means the whole display.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.attributes = window.attributes.apply { fitInsetsTypes = 0 }
        } else {
            window.addFlags(WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            window.attributes = window.attributes.apply {
                layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        }
        @Suppress("DEPRECATION")
        window.statusBarColor = android.graphics.Color.TRANSPARENT
        @Suppress("DEPRECATION")
        window.navigationBarColor = android.graphics.Color.TRANSPARENT
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = false
        }
    }
}

@AndroidxOptIn(UnstableApi::class)
@Composable
private fun FeedSurface(player: ExoPlayer) {
    AndroidView(
        factory = { ctx ->
            PlayerView(ctx).apply {
                useController = false
                resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                setBackgroundColor(android.graphics.Color.BLACK)
            }
        },
        update = { it.player = player },
        onRelease = { it.player = null },
        modifier = Modifier.fillMaxSize(),
    )
}

@Composable
private fun FeedPoster(video: FeedVideo) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        if (video.posterModel != null) {
            SubcomposeAsyncImage(
                model = video.posterModel,
                contentDescription = null,
                contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize(),
                loading = { FeedGlyph(video.title) },
                error = { FeedGlyph(video.title) },
            )
        } else {
            FeedGlyph(video.title)
        }
    }
}

@Composable
private fun FeedGlyph(title: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(32.dp)) {
        Icon(Icons.Outlined.Movie, contentDescription = null, tint = Color.White.copy(alpha = 0.4f),
            modifier = Modifier.size(64.dp))
        Text(
            title, style = MaterialTheme.typography.bodyMedium, color = Color.White.copy(alpha = 0.6f),
            textAlign = TextAlign.Center, maxLines = 2, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 12.dp),
        )
    }
}

@Composable
private fun FeedMessage(title: String, sub: String) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier.background(Color.Black.copy(alpha = 0.6f), RoundedCornerShape(12.dp)).padding(20.dp),
    ) {
        Text(title, style = MaterialTheme.typography.titleSmall, color = Color.White)
        Text(sub, style = MaterialTheme.typography.bodySmall, color = Color.White.copy(alpha = 0.7f),
            modifier = Modifier.padding(top = 4.dp))
    }
}

/**
 * Seek bar pinned to the bottom edge: a hairline that thickens under the
 * finger. A tap jumps there; a drag scrubs continuously with a time bubble.
 * The strip is a taller invisible touch target than the line it draws.
 */
@Composable
private fun FeedScrubber(
    positionMs: Long,
    durationMs: Long,
    onScrub: (Long) -> Unit,
    modifier: Modifier = Modifier,
) {
    var scrubbing by remember { mutableStateOf(false) }
    var fraction by remember { mutableFloatStateOf(0f) }
    val played = when {
        scrubbing -> fraction
        durationMs > 0 -> (positionMs.toFloat() / durationMs).coerceIn(0f, 1f)
        else -> 0f
    }
    val trackHeight by animateDpAsState(if (scrubbing) 10.dp else 3.dp, label = "trackHeight")
    val trackAlpha by animateFloatAsState(if (scrubbing) 1f else 0.75f, label = "trackAlpha")

    BoxWithConstraints(
        modifier = modifier
            .fillMaxWidth()
            .height(28.dp)
            .pointerInput(durationMs) {
                if (durationMs <= 0) return@pointerInput
                awaitEachGesture {
                    val down = awaitFirstDown()
                    down.consume()
                    fraction = (down.position.x / size.width).coerceIn(0f, 1f)
                    scrubbing = true
                    onScrub((fraction * durationMs).toLong())
                    drag(down.id) { change ->
                        change.consume()
                        fraction = (change.position.x / size.width).coerceIn(0f, 1f)
                        onScrub((fraction * durationMs).toLong())
                    }
                    scrubbing = false
                }
            },
        contentAlignment = Alignment.BottomStart,
    ) {
        Box(
            Modifier.fillMaxWidth().padding(bottom = 4.dp).height(trackHeight)
                .background(Color.White.copy(alpha = 0.25f * trackAlpha), RoundedCornerShape(5.dp)),
        ) {
            Box(
                Modifier.fillMaxWidth(played).fillMaxHeight()
                    .background(Domovoi.colors.brand.copy(alpha = trackAlpha), RoundedCornerShape(5.dp)),
            )
        }
        if (scrubbing) {
            val thumb = 16.dp
            Box(
                Modifier.align(Alignment.BottomStart)
                    .offset(x = (maxWidth - thumb) * played, y = (-1).dp)
                    .size(thumb)
                    .background(Domovoi.colors.brand, CircleShape),
            )
            Text(
                "${fmtDur(fraction * durationMs / 1000.0)}  /  ${fmtDur(durationMs / 1000.0)}",
                color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.SemiBold,
                fontFamily = MonoFamily,
                modifier = Modifier.align(Alignment.BottomCenter).offset(y = (-30).dp)
                    .background(Color.Black.copy(alpha = 0.55f), RoundedCornerShape(8.dp))
                    .padding(horizontal = 10.dp, vertical = 4.dp),
            )
        }
    }
}

// ---------------------------------------------------------------------------
// Long-press sheets
// ---------------------------------------------------------------------------

@Composable
private fun FeedSheetFrame(title: String, onDismiss: () -> Unit, content: @Composable () -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = Domovoi.colors.raised) {
        Column(Modifier.navigationBarsPadding()) {
            Text(
                title, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(horizontal = 24.dp, vertical = 8.dp),
            )
            HorizontalDivider(color = Domovoi.colors.border, modifier = Modifier.padding(bottom = 8.dp))
            content()
        }
    }
}

@Composable
private fun FeedAction(icon: ImageVector, label: String, onClick: () -> Unit) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 24.dp, vertical = 14.dp),
    ) {
        Icon(icon, contentDescription = null, tint = Domovoi.colors.brand)
        Text(label, style = MaterialTheme.typography.bodyLarge, color = Domovoi.colors.fg,
            modifier = Modifier.padding(start = 16.dp))
    }
}

/** The session queue: tap a row to jump to it, close to drop it (never deletes the file). */
@Composable
private fun FeedPlaylistSheet(
    videos: List<FeedVideo>,
    currentIndex: Int,
    onSelect: (Int) -> Unit,
    onRemove: (FeedVideo) -> Unit,
    onDismiss: () -> Unit,
) {
    FeedSheetFrame("playlist · ${videos.size} video${if (videos.size == 1) "" else "s"}", onDismiss) {
        val listState = rememberLazyListState()
        LaunchedEffect(Unit) {
            if (currentIndex in videos.indices) listState.scrollToItem(currentIndex)
        }
        LazyColumn(state = listState, modifier = Modifier.fillMaxHeight(0.7f)) {
            itemsIndexed(videos, key = { _, v -> v.key }) { index, video ->
                val isCurrent = index == currentIndex
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().clickable { onSelect(index) }
                        .padding(start = 24.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
                ) {
                    Text(
                        "${index + 1}",
                        style = MaterialTheme.typography.labelLarge.copy(fontFamily = MonoFamily),
                        color = if (isCurrent) Domovoi.colors.brand else Domovoi.colors.fgMuted,
                        modifier = Modifier.width(36.dp),
                    )
                    Column(Modifier.weight(1f)) {
                        Text(
                            video.title, style = MaterialTheme.typography.bodyLarge,
                            fontWeight = if (isCurrent) FontWeight.SemiBold else FontWeight.Normal,
                            color = if (isCurrent) Domovoi.colors.brand else Domovoi.colors.fg,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                        val meta = listOfNotNull(video.sizeBytes?.let { fmtBytes(it) }, video.modifiedEpochSec?.let(::fmtDate))
                        if (meta.isNotEmpty()) {
                            Text(meta.joinToString("  ·  "), style = MaterialTheme.typography.labelSmall,
                                color = Domovoi.colors.fgMuted)
                        }
                    }
                    IconButton(onClick = { onRemove(video) }) {
                        Icon(Icons.Outlined.Close, contentDescription = "remove from playlist",
                            tint = Domovoi.colors.fgMuted)
                    }
                }
            }
        }
    }
}

/** File details, plus what the player knows about the stream once it is loaded. */
@AndroidxOptIn(UnstableApi::class)
@Composable
private fun FeedDetailsSheet(video: FeedVideo, player: ExoPlayer, onDismiss: () -> Unit) {
    val format = player.videoFormat
    val durMs = player.duration.takeIf { it != C.TIME_UNSET && it > 0 }
    FeedSheetFrame(video.title, onDismiss) {
        Column(Modifier.padding(horizontal = 24.dp).padding(bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp)) {
            DetailRow("size", video.sizeBytes?.let { fmtBytes(it) } ?: "unknown")
            video.modifiedEpochSec?.let { DetailRow("modified", fmtDate(it)) }
            DetailRow("duration", durMs?.let { fmtDur(it / 1000.0) } ?: "unknown")
            DetailRow(
                "resolution",
                format?.takeIf { it.width > 0 && it.height > 0 }?.let { "${it.width} × ${it.height}" } ?: "unknown",
            )
            format?.bitrate?.takeIf { it > 0 }?.let { DetailRow("bitrate", "${it / 1000} kbps") }
            format?.frameRate?.takeIf { it > 0 }?.let { DetailRow("frame rate", "%.0f fps".format(it)) }
            format?.sampleMimeType?.let { DetailRow("codec", it.substringAfter('/')) }
            video.location?.let { DetailRow("location", it) }
        }
    }
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(Modifier.padding(vertical = 6.dp)) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted,
            modifier = Modifier.width(110.dp))
        Text(value, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg,
            modifier = Modifier.weight(1f))
    }
}

private val DATE_FMT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")

private fun fmtDate(epochSec: Double): String =
    DATE_FMT.format(Instant.ofEpochMilli((epochSec * 1000).toLong()).atZone(ZoneId.systemDefault()))

private fun shareVideo(context: android.content.Context, video: FeedVideo) {
    runCatching {
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "video/*"
            putExtra(Intent.EXTRA_STREAM, Uri.parse(video.uri))
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        context.startActivity(Intent.createChooser(intent, "share video"))
    }
}
