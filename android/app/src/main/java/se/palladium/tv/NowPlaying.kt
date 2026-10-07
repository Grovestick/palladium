package se.palladium.tv

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalView
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private val Good = Color(0xFF5FD08A)
private val Low = Color(0xFFE0B341)
private val Dry = Color(0xFFFF6B6B)

/**
 * What this server is sending, as the web's Now playing shows it: a card per stream with
 * where it has got to, how much picture the player holds ahead, and its rate over the
 * last two minutes. Read every three seconds while the page is open. The owner's.
 */
/** What is playing now and each stream's rate over the last two minutes, read every 3 s. */
@Composable
private fun rememberLive(): Pair<List<Api.Live>, Map<String, List<Double>>> {
    var live by remember { mutableStateOf<List<Api.Live>>(emptyList()) }
    // each stream's rate, a sample every three seconds, keyed by who and what
    var history by remember { mutableStateOf<Map<String, List<Double>>>(emptyMap()) }
    LaunchedEffect(Unit) {
        while (true) {
            // a missed or refused answer keeps what is drawn: emptied every time the
            // connection stumbled, the page flicked between the streams and nothing
            val now = Api.watching()
            if (now == null) { kotlinx.coroutines.delay(3000); continue }
            live = now
            history = now.associate { one ->
                val id = liveId(one)
                id to ((history[id] ?: emptyList()) + one.mbit).takeLast(40)
            }
            kotlinx.coroutines.delay(3000)
        }
    }
    return live to history
}

private fun liveId(one: Api.Live) = one.who + "|" + one.title + "|" + one.episode

private fun totals(live: List<Api.Live>): String =
    if (live.isEmpty()) "Nothing playing"
    else "${live.size} playing  ·  " +
         String.format(java.util.Locale.US, "%.1f Mbit/s out", live.sumOf { it.mbit })

@Composable
fun NowPlaying(onFull: (() -> Unit)? = null) {
    val (live, history) = rememberLive()
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(bottom = 8.dp)) {
        Text(totals(live), color = Skin.Dim, fontSize = 13.sp, modifier = Modifier.weight(1f))
        if (onFull != null) Pill("Full screen") { onFull() }
    }
    live.forEach { one -> LiveCard(one, history[liveId(one)] ?: emptyList()) }
}

/**
 * The same, filling the screen: for a tablet on a stand. Cards side by side where there
 * is room, the graphs larger, and the screen kept on for as long as it is open.
 */
@Composable
fun NowPlayingScreen(onBack: () -> Unit) {
    val (live, history) = rememberLive()
    val view = LocalView.current
    DisposableEffect(Unit) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }
    Column(Modifier.fillMaxSize().background(Skin.Bg)
               .padding(horizontal = 20.dp, vertical = 16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(bottom = 14.dp)) {
            Pill("Back") { onBack() }
            Text("Now playing", color = Skin.Fg, fontSize = 22.sp,
                 fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = 12.dp))
            Text(totals(live), color = Skin.Dim, fontSize = 15.sp,
                 modifier = Modifier.padding(start = 18.dp))
        }
        LazyVerticalGrid(columns = GridCells.Adaptive(380.dp),
                         horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            items(live.size) { i ->
                val one = live[i]
                LiveCard(one, history[liveId(one)] ?: emptyList(), big = true)
            }
        }
    }
}

@Composable
private fun LiveCard(one: Api.Live, rates: List<Double>, big: Boolean = false) {
    Column(Modifier.fillMaxWidth().padding(bottom = 10.dp)
               .clip(RoundedCornerShape(8.dp)).background(Skin.Panel2)
               .padding(horizontal = 12.dp, vertical = 10.dp),
           verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Text(one.title + (if (one.episode.isNotEmpty()) "  ·  " + one.episode else ""),
             color = Skin.Fg, fontSize = if (big) 17.sp else 14.sp,
             fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(listOf(one.who, one.device, one.how, one.quality,
                    if (one.state == "paused") "paused" else "",
                    if (one.casual) "shuffle" else "")
                 .filter { it.isNotEmpty() }.joinToString("  ·  "),
             color = Skin.Dim, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        // where it has got to
        val done = if (one.duration > 0) (one.position / one.duration).coerceIn(0.0, 1.0) else 0.0
        Bar("${clock(one.position)} of ${clock(one.duration)}", done, Skin.Accent)
        // how much the player holds ahead: a minute is full, dry is red
        if (one.ahead >= 0 || one.stalled) {
            val held = (one.ahead / 60.0).coerceIn(0.0, 1.0)
            Bar(if (one.stalled) "stalled" else "${one.ahead.toInt()} s ahead", held,
                if (one.stalled || one.ahead < 10) Dry else if (one.ahead < 30) Low else Good)
        }
        // the rate over the last two minutes
        val rate = String.format(java.util.Locale.US, "%.1f Mbit/s", one.mbit) +
            (if (one.average > 0) String.format(java.util.Locale.US,
                 "  ·  avg %.1f  ·  peak %.1f", one.average, one.peak) else "")
        if (big) {
            Text(rate, color = Skin.Dim, fontSize = 13.sp)
            Spark(rates, Modifier.fillMaxWidth().height(70.dp))
        } else Row {
            Text(rate, color = Skin.Dim, fontSize = 11.5.sp, modifier = Modifier.width(260.dp))
            Spark(rates, Modifier.weight(1f).height(26.dp))
        }
    }
}

@Composable
private fun Bar(label: String, share: Double, colour: Color) {
    Row {
        Box(Modifier.weight(1f).height(8.dp).padding(top = 4.dp)
                .clip(RoundedCornerShape(3.dp)).background(Color(0x33FFFFFF))) {
            Box(Modifier.fillMaxWidth(share.toFloat()).height(8.dp).background(colour))
        }
        Text(label, color = Skin.Dim, fontSize = 11.5.sp,
             modifier = Modifier.width(120.dp).padding(start = 8.dp))
    }
}

@Composable
private fun Spark(rates: List<Double>, modifier: Modifier) {
    Canvas(modifier) {
        if (rates.size < 2) return@Canvas
        val top = (rates.maxOrNull() ?: 1.0).coerceAtLeast(0.5)
        val step = size.width / (rates.size - 1)
        val line = Path()
        rates.forEachIndexed { i, r ->
            val x = i * step
            val y = size.height - (r / top * size.height).toFloat()
            if (i == 0) line.moveTo(x, y) else line.lineTo(x, y)
        }
        val fill = Path().apply {
            addPath(line)
            lineTo(size.width, size.height)
            lineTo(0f, size.height)
            close()
        }
        drawPath(fill, Good.copy(alpha = 0.18f))
        drawPath(line, Good, style = Stroke(width = 2f))
        drawCircle(Good, 3f, Offset(size.width, size.height -
            (rates.last() / top * size.height).toFloat()))
    }
}

private fun clock(seconds: Double): String {
    val s = seconds.toInt().coerceAtLeast(0)
    return if (s >= 3600) String.format(java.util.Locale.US, "%d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60)
           else String.format(java.util.Locale.US, "%d:%02d", s / 60, s % 60)
}

/**
 * The web's own Now playing, full screen: the drawing of what is talking to what and the
 * live rows, exactly as the browser draws them, rather than a second copy of them here.
 * Signed in with this app's key, and the screen kept on while it is open.
 */
@Composable
fun MonitorScreen(onBack: () -> Unit) {
    val view = LocalView.current
    DisposableEffect(Unit) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }
    Box(Modifier.fillMaxSize().background(Skin.Bg)) {
        androidx.compose.ui.viewinterop.AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { ctx ->
                android.webkit.WebView(ctx).apply {
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    setBackgroundColor(android.graphics.Color.BLACK)
                    // the remote goes to the page, so up and down move through its rows
                    isFocusable = true
                    isFocusableInTouchMode = true
                    val base = Api.base.trimEnd('/')
                    // the page signed in at whichever address it is opened on
                    fun open(at: String) {
                        if (Api.token.isNotEmpty()) {
                            android.webkit.CookieManager.getInstance()
                                .setCookie(at, "pal=" + Api.token)
                        }
                        loadUrl("$at/#monitor")
                    }
                    var triedOther = false
                    webViewClient = object : android.webkit.WebViewClient() {
                        override fun onPageFinished(view: android.webkit.WebView, url: String?) {
                            view.requestFocus()
                        }
                        // A network that could not resolve the server's name showed the
                        // web view's own error page - an upside-down robot - and nothing
                        // else: opened again by the address the name last resolved to.
                        override fun onReceivedError(
                            view: android.webkit.WebView,
                            request: android.webkit.WebResourceRequest,
                            error: android.webkit.WebResourceError) {
                            if (!request.isForMainFrame || triedOther) return
                            val other = KnownHosts.byAddress(base) ?: return
                            triedOther = true
                            view.post { open(other.trimEnd('/')) }
                        }
                    }
                    open(base)
                }
            })
        Box(Modifier.align(Alignment.TopEnd).padding(10.dp)) {
            Pill("Close") { onBack() }
        }
    }
}
