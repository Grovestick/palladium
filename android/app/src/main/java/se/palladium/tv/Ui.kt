package se.palladium.tv

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.IndicationNodeFactory
import androidx.compose.foundation.interaction.InteractionSource
import androidx.compose.foundation.interaction.PressInteraction
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.drawscope.ContentDrawScope
import androidx.compose.ui.node.DelegatableNode
import androidx.compose.ui.node.DrawModifierNode
import androidx.compose.ui.node.invalidateDraw
import kotlinx.coroutines.launch
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage

/** The look of the thing, in one place so the TV and the phone stay consistent. */
/** How every list slides when the remote moves: unbouncy springs, quick off the mark,
 *  a page up or down settled in Route.START_SLIDE_DOWN_MS and a row along in
 *  START_SLIDE_ALONG_MS. Springs, since the scroll restarts its spec every frame. */
@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
object Slide {
    val down: androidx.compose.animation.core.AnimationSpec<Float> =
        androidx.compose.animation.core.spring(stiffness = Route.slideStiffness(Route.START_SLIDE_DOWN_MS))
    val along: androidx.compose.animation.core.AnimationSpec<Float> =
        androidx.compose.animation.core.spring(stiffness = Route.slideStiffness(Route.START_SLIDE_ALONG_MS))

    /** A direction key held down: its repeats come faster than a slide settles, and each
     *  one restarted it - a long list crawled and lurched (owner, 2026-11-11). Set from
     *  the activity's keys; while it is on every step is a quick straight move. */
    @Volatile var held = false
    private val quick: androidx.compose.animation.core.AnimationSpec<Float> =
        androidx.compose.animation.core.tween(Route.HELD_SLIDE_MS,
                                              easing = androidx.compose.animation.core.LinearEasing)

    /** [spring] for a press, the quick move while a key is held. */
    fun now(spring: androidx.compose.animation.core.AnimationSpec<Float>) = if (held) quick else spring

    /** [base]'s places - on a television Compose's own, the focus at a fixed point of
     *  the screen - reached on [spring]. Scrolling as little as possible instead moved
     *  Home's rows to other places and left posters cut at the edges. */
    fun keeping(base: androidx.compose.foundation.gestures.BringIntoViewSpec,
                spring: androidx.compose.animation.core.AnimationSpec<Float>) =
        object : androidx.compose.foundation.gestures.BringIntoViewSpec {
            override val scrollAnimationSpec get() = now(spring)
            override fun calculateScrollDistance(offset: Float, size: Float, containerSize: Float) =
                base.calculateScrollDistance(offset, size, containerSize)
        }
}

/** The downloads somebody asked for coming in, said once on whichever screen is up -
 *  the shelves or the picture - and not again on the other. */
object Arrivals {
    private var since = 0L
    /** the app, for the notifications posted from what is read here */
    @Volatile var app: android.content.Context? = null
    private val said = HashSet<String>()

    /** What has come in since the last look, each said once. The first look only reads
     *  the server's clock: what came in before the app started is not news. */
    suspend fun fresh(): List<String> {
        if (BuildConfig.PLAY) return emptyList()
        runCatching { WatchBlink.look() }
        // a watchlist's film that has got a source - said on opening the app as well,
        // where it happened while nobody was looking: the server keeps it until then
        val news = Api.watchNewsAll().filter { it.title.isNotEmpty() }
        // and the blinks queued for this app, as notifications where they are on
        app?.let { ctx -> runCatching { Notes.check(ctx) } }
        val watched = news.map { Route.watchNewsSaid(it.title, it.how) }
        val got = Api.downloadsReady(since) ?: return watched
        val first = since == 0L
        since = maxOf(since, got.first - 120)
        if (first) return watched
        val came = got.second.filter { said.add(it.first) }
        // Home and the watchlist were drawn before it came in: drawn again
        if (came.isNotEmpty()) landed.intValue += 1
        return watched + came.map { it.second + " is ready to watch" }
    }

    /** Counts the downloads come in while the app is open; the shelves are keyed on it. */
    val landed = androidx.compose.runtime.mutableIntStateOf(0)

    /** How often a screen looks: a film in is said within seconds of it being whole. */
    const val EVERY_MS = 8_000L
}

/** Something on this viewer's watchlist has come in since they last opened it - a film or
 *  a game they downloaded: the Watchlist tab blinks until it is opened (owner, 2026-11-06). */
object WatchBlink {
    var on by mutableStateOf(false)
    /** what made it blink, by key */
    private var keys: Set<String> = emptySet()
    /** And on the Watchlist itself, the posters of what came in blink in turn, so it is
     *  plain which ones they are (owner, 2026-11-14) - until the Watchlist is left. */
    var fresh by mutableStateOf(emptySet<String>())
    private var askedAt = 0L

    /** Asked at most once a minute, from the arrivals look every screen already makes. */
    suspend fun look() {
        if (System.currentTimeMillis() - askedAt < 60_000L) return
        askedAt = System.currentTimeMillis()
        keys = Api.watchBlink()
        on = keys.isNotEmpty()
    }

    /** The Watchlist opened: its blinking posters are the ones that came in. Asked first,
     *  so a blink that arrived since the last look is among them. */
    suspend fun seen() {
        val now = Api.watchBlink().ifEmpty { keys }
        fresh = now
        keys = emptySet()
        on = false
        Api.watchSeen()
    }

    /** The Watchlist left: nothing blinks there any more. */
    fun left() { fresh = emptySet() }
}

object Skin {
    /**
     * The look this screen is wearing, as the server hands it over.
     *
     * The colours are not written down here: the server holds every palette, so a new
     * look is an entry in a file on it and this app wears it the next time it asks.
     * Two of them are nobody's choice - the machine that keeps copies after dark, and
     * a machine whose card has been given to a game - and those overrule what was
     * picked for as long as they last.
     */
    var Mood by androidx.compose.runtime.mutableStateOf("house")
    /** Where a poster stands: "on" behind the shelves as well, "poster" on a title's
     *  own page and nowhere else, "off". This viewer's answer, from the server, so the
     *  browser, the phone and the television agree. */
    var Backdrop by androidx.compose.runtime.mutableStateOf("poster")

    /** Behind the shelves, which is the only place the two answers differ. */
    val BackdropBehind: Boolean get() = Backdrop == "on"

    /** On a title's own page: everything but off. */
    val BackdropOnPage: Boolean get() = Backdrop != "off"

    private fun hex(code: String, fallback: Color): Color {
        val bare = code.trim().removePrefix("#")
        if (bare.length != 6) return fallback
        return runCatching { Color(bare.toLong(16) or 0xFF000000L) }
            .getOrDefault(fallback)
    }

    private var bgIs by androidx.compose.runtime.mutableStateOf(Color(0xFF0B0D10))
    private var panelIs by androidx.compose.runtime.mutableStateOf(Color(0xFF161A20))
    private var panel2Is by androidx.compose.runtime.mutableStateOf(Color(0xFF1E242C))
    private var lineIs by androidx.compose.runtime.mutableStateOf(Color(0xFF2A323C))
    private var fgIs by androidx.compose.runtime.mutableStateOf(Color(0xFFE8ECF1))
    private var dimIs by androidx.compose.runtime.mutableStateOf(Color(0xFF93A0B0))
    private var accentIs by androidx.compose.runtime.mutableStateOf(Color(0xFF4A90F0))
    private var onAccentIs by androidx.compose.runtime.mutableStateOf(Color(0xFF111111))
    private var cornerIs by androidx.compose.runtime.mutableStateOf(1f)
    private var serifIs by androidx.compose.runtime.mutableStateOf(false)
    //: washes of colour over the ground: x%, y%, radius px, colour
    private var glowIs by androidx.compose.runtime.mutableStateOf(
        emptyList<Pair<FloatArray, Color>>())

    val Bg: Color get() = bgIs
    val Panel: Color get() = panelIs
    val Panel2: Color get() = panel2Is
    val Line: Color get() = lineIs
    val Fg: Color get() = fgIs
    val Dim: Color get() = dimIs
    val Accent: Color get() = accentIs
    /** Text drawn on the accent: dark or white, as the skin says reads. */
    val OnAccent: Color get() = onAccentIs
    /** The face shelf titles are set in. */
    val Titles: androidx.compose.ui.text.font.FontFamily
        get() = if (serifIs) androidx.compose.ui.text.font.FontFamily.Serif
                else androidx.compose.ui.text.font.FontFamily.Default

    /** A corner as this skin rounds it: the app's own size, scaled. */
    fun round(base: androidx.compose.ui.unit.Dp): androidx.compose.ui.unit.Dp =
        base * Route.cornerScale(cornerIs)

    /** The ground every screen stands on: the background and the skin's washes of
     *  colour - what keeps a dark skin from reading as a black rectangle. */
    fun ground(m: Modifier): Modifier = m.drawBehind {
        drawRect(bgIs)
        glowIs.forEach { (at, colour) ->
            val c = androidx.compose.ui.geometry.Offset(size.width * at[0] / 100f,
                                                        size.height * at[1] / 100f)
            val r = at[2] * density * 0.55f
            drawCircle(Brush.radialGradient(listOf(colour, colour.copy(alpha = 0f)),
                                            center = c, radius = r),
                       radius = r, center = c)
        }
    }

    private fun rgba(text: String): Color? {
        val m = Regex("""rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)""")
            .find(text) ?: return null
        val (r, g, b) = m.destructured.toList().take(3).map { it.toInt() }
        val a = m.groupValues[4].toFloatOrNull() ?: 1f
        return Color(r, g, b, (a * 255).toInt().coerceIn(0, 255))
    }

    /** One answer from the server, worn. Anything it leaves out keeps what it had. */
    fun wear(said: org.json.JSONObject) {
        Mood = said.optString("look", "house")
        // an older server answers true or false, which arrive here as those words
        Backdrop = when (val where = said.optString("backdrop", "poster")) {
            "on", "poster", "off" -> where
            "false" -> "off"
            else -> "on"
        }
        bgIs = hex(said.optString("bg"), bgIs)
        panelIs = hex(said.optString("panel"), panelIs)
        panel2Is = hex(said.optString("panel2"), panel2Is)
        lineIs = hex(said.optString("line"), lineIs)
        fgIs = hex(said.optString("fg"), fgIs)
        dimIs = hex(said.optString("dim"), dimIs)
        accentIs = hex(said.optString("accent"), accentIs)
        onAccentIs = hex(said.optString("onAccent"), Color(0xFF111111))
        cornerIs = said.optDouble("corner", 1.0).toFloat()
        serifIs = said.optString("titles") == "serif"
        glowIs = said.optJSONArray("glow")?.let { arr ->
            (0 until arr.length()).mapNotNull { i ->
                val g = arr.optJSONArray(i) ?: return@mapNotNull null
                val colour = rgba(g.optString(3)) ?: return@mapNotNull null
                floatArrayOf(g.optDouble(0).toFloat(), g.optDouble(1).toFloat(),
                             g.optDouble(2).toFloat()) to colour
            }
        } ?: emptyList()
    }

    /** "#4a90f0" as the colour to draw with; anything else leaves it alone. */
    fun paint(code: String) {
        accentIs = hex(code, accentIs)
    }
    val Empty: Brush get() = Brush.linearGradient(listOf(panel2Is, panelIs))
}

/**
 * One image loader for the whole app.
 *
 * The default builds its own per request and keeps a small cache, so scrolling back up
 * a grid decodes everything again. A quarter of the heap and a disk cache costs
 * nothing on a television that is doing one thing.
 */
private var loader: coil.ImageLoader? = null

/** Built again on the next poster: offline viewing changes how pictures are kept. */
fun forgetImageLoader() { loader = null }

/** A picture from the library's server kept under its path, not its address: the
 *  address changes between home and away, and offline the kept one is what there is. */
fun artKey(url: String): String? {
    if (!Offline.on.value) return null
    val base = Api.base.trimEnd('/')
    if (base.isEmpty() || !url.startsWith(base)) return null
    return "main" + url.removePrefix(base).replace(Regex("[?&](t|again)=[^&]*"), "")
}

@Composable
private fun imageLoader(): coil.ImageLoader {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    return loader ?: coil.ImageLoader.Builder(ctx)
        .memoryCache {
            coil.memory.MemoryCache.Builder(ctx).maxSizePercent(0.25).build()
        }
        .diskCache {
            coil.disk.DiskCache.Builder()
                .directory(ctx.cacheDir.resolve("art"))
                .maxSizeBytes(256L * 1024 * 1024)
                .build()
        }
        .crossfade(false)                 // two dropped frames per poster, on this box
        // with offline viewing on, a kept picture is shown without asking the server again
        .respectCacheHeaders(!Offline.on.value)
        // a name the network will not resolve falls back on its last known address
        .okHttpClient {
            okhttp3.OkHttpClient.Builder().dns(KnownHosts)
                // a picture from a house address, off the house network: refused at
                // once rather than waited on for ten seconds a poster
                .addInterceptor { chain ->
                    val host = chain.request().url.host
                    if (!Api.homeHere("http://$host"))
                        throw java.io.IOException("a house address, not this network")
                    chain.proceed(chain.request())
                }
                .build()
        }
        .build()
        .also { loader = it }
}

/**
 * The download mark: an arrow into a tray, drawn rather than typed.
 *
 * It was the character U+2938, which on a television reads as a tick, a full stop or
 * nothing at all depending on the font the box happens to have. This is the shape
 * everything else uses for the same thing, and it is the same at any size.
 */
@Composable
fun DownloadMark(size: Dp, colour: Color = Skin.Fg) {
    Canvas(Modifier.size(size)) {
        val w = this.size.width
        val h = this.size.height
        val line = Stroke(width = h * 0.11f, cap = StrokeCap.Round)
        // the stem, and the head under it
        drawLine(colour, Offset(w / 2, h * 0.08f), Offset(w / 2, h * 0.52f),
                 strokeWidth = line.width, cap = StrokeCap.Round)
        drawLine(colour, Offset(w * 0.26f, h * 0.36f), Offset(w / 2, h * 0.56f),
                 strokeWidth = line.width, cap = StrokeCap.Round)
        drawLine(colour, Offset(w * 0.74f, h * 0.36f), Offset(w / 2, h * 0.56f),
                 strokeWidth = line.width, cap = StrokeCap.Round)
        // the tray it lands in: two uprights and the floor between them
        drawLine(colour, Offset(w * 0.16f, h * 0.72f), Offset(w * 0.16f, h * 0.90f),
                 strokeWidth = line.width, cap = StrokeCap.Round)
        drawLine(colour, Offset(w * 0.84f, h * 0.72f), Offset(w * 0.84f, h * 0.90f),
                 strokeWidth = line.width, cap = StrokeCap.Round)
        drawLine(colour, Offset(w * 0.16f, h * 0.90f), Offset(w * 0.84f, h * 0.90f),
                 strokeWidth = line.width, cap = StrokeCap.Round)
    }
}

/**
 * The select press that opened a menu by being held, while it is still going on.
 *
 * Holding select opens the menu with the button still down. The release at the end of
 * that same press then arrives at whatever has taken the focus meanwhile - the first
 * item in the menu - and presses it. That release is the end of the press that asked
 * for the menu, not a press of anything in it.
 *
 * Two earlier tries at this both failed, and for the same reason: they guessed when
 * the release would come. Matching on downTime is exact but needs the event to reach a
 * handler that is looking, and the menu is a window of its own that has not always
 * taken the focus by the time it arrives. A window of milliseconds cannot work at all:
 * the hold is felt half a second in and the hand lets go whenever it lets go, a second
 * later or three, so the window was either too short to catch the release or long
 * enough to swallow a real press.
 *
 * So nothing is timed and nothing is routed. The gate opens when a held key opens
 * something, and the next thing that would act on that press closes it by refusing
 * it - there is only ever one release. A key release seen anywhere closes it too. The
 * long stop is only so a hold interrupted by leaving the app cannot leave it open.
 */
private var holdOpen = false
private var holdOpenedAt = 0L
private var holdDownTime = 0L

//: past this a gate was never closed by anything, which means the release never
//: arrived - the app was left, or the menu was dismissed another way
private const val HOLD_STUCK_MS = 10_000L

/** A held key has just opened something. */
fun justHeld(downTime: Long) {
    holdOpen = true
    holdOpenedAt = android.os.SystemClock.uptimeMillis()
    holdDownTime = downTime
}

/** That press is over: the next one is somebody's own. */
fun holdIsOver() {
    holdOpen = false
    holdDownTime = 0L
}

private fun holdStuck(): Boolean =
    android.os.SystemClock.uptimeMillis() - holdOpenedAt > HOLD_STUCK_MS

/**
 * Whether a press this instant is the tail of the hold that put this on screen.
 *
 * Asking closes the gate. The press being asked about is the release, and a press is
 * released once - so the control that asks is the one that swallows it, and every
 * press after it is somebody's own.
 */
fun stillTheHold(): Boolean {
    if (!holdOpen) return false
    val stuck = holdStuck()
    holdIsOver()
    return !stuck
}

/** True while an event still belongs to the press that opened something by being held. */
fun fromTheHold(e: android.view.KeyEvent): Boolean =
    holdOpen && !holdStuck() &&
        (e.keyCode == android.view.KeyEvent.KEYCODE_DPAD_CENTER ||
         e.keyCode == android.view.KeyEvent.KEYCODE_ENTER ||
         e.keyCode == android.view.KeyEvent.KEYCODE_NUMPAD_ENTER) &&
        (holdDownTime == 0L || e.downTime == holdDownTime)


/** Artwork, or the Palladium P when a title has none. Never a broken image. */
@Composable
fun Art(url: String?, title: String, modifier: Modifier = Modifier, mark: Int = 56,
        scale: ContentScale = ContentScale.Crop,
        /* drawn in one colour: a studio's mark is dark on nothing as often as not */
        tint: Color? = null,
        /* what shows where the picture does not reach. A poster fills its slot, so
           this is the grey a missing one leaves behind; a backdrop is fitted rather
           than cropped, and the grey either side of it reads as a band down the edge
           of the screen - which is what it was doing. */
        ground: androidx.compose.ui.graphics.Brush? = Skin.Empty,
        /* how much of the picture shows: painted faint, not faded as a layer - a faded
           layer is the whole screen drawn twice, once aside and once blended back */
        alpha: Float = 1f,
        /* without colour: offline, a title not kept on this device */
        grey: Boolean = false) {
    // the grey under the picture only until the picture is there: under a poster that
    // has loaded it is a layer more for the television's graphics to fill each frame
    var shown by remember(url) { mutableStateOf(false) }
    Box(if (shown || ground == null) modifier else modifier.background(ground),
        contentAlignment = Alignment.Center) {
        if (mark > 0 && !shown) {
            Text("P", color = Skin.Line, fontSize = mark.sp,
                 fontFamily = FontFamily.Serif, fontWeight = FontWeight.SemiBold)
        }
        if (url != null) {
            // A poster that failed is asked for again, three times with a pause between:
            // switching servers asks a whole screen of them at once, and one that timed
            // out then stayed blank until the app was started again.
            var tries by remember(url) { mutableStateOf(0) }
            var failed by remember(url) { mutableStateOf(false) }
            if (failed && tries < 3) {
                androidx.compose.runtime.LaunchedEffect(url, tries) {
                    kotlinx.coroutines.delay(1500L * (tries + 1))
                    failed = false
                    tries += 1
                }
            }
            val asked = if (tries == 0) url
                        else url + (if (url.contains("?")) "&" else "?") + "again=" + tries
            // Crop fills a poster-shaped slot; Fit is for the backdrop, where cropping
            // a tall poster into a squarish box pushes its head and feet off the screen
            val ctx = androidx.compose.ui.platform.LocalContext.current
            val kept = artKey(url)
            val model: Any = if (kept == null) asked else
                coil.request.ImageRequest.Builder(ctx).data(asked)
                    .diskCacheKey(kept).memoryCacheKey(kept).build()
            AsyncImage(model = model, contentDescription = title, imageLoader = imageLoader(),
                       contentScale = scale, modifier = Modifier.fillMaxSize(), alpha = alpha,
                       colorFilter = tint?.let { androidx.compose.ui.graphics.ColorFilter.tint(it) }
                           ?: if (grey) androidx.compose.ui.graphics.ColorFilter.colorMatrix(
                               androidx.compose.ui.graphics.ColorMatrix().apply { setToSaturation(0f) })
                           else null,
                       // a picture Fit leaves room beside keeps its ground: that is
                       // where the grey shows
                       onSuccess = { if (scale != ContentScale.Fit) shown = true },
                       onError = { failed = true })
        }
    }
}

/** The band across a poster's top corner: one shape in one place, and only the word
 *  in it changes - what can be asked for, what can be fetched, what is on shuffle.
 *  The poster is clipped, so the band ends at its edges rather than inside them. */
@Composable
fun androidx.compose.foundation.layout.BoxScope.CornerBand(
    word: String, width: Int, colour: Color = Skin.Accent, ink: Color = Color.White) {
    // `width` is the poster's real width, measured, not the number the caller passed:
    // in a grid the column decides it, and a band placed by a nominal width sits too
    // near the corner, where the readable run is short and a word loses its last
    // letters.
    Box(Modifier.align(Alignment.TopEnd)
            .offset(x = (width * 0.30f).dp, y = (width * 0.11f).dp)
            .rotate(45f)
            .width((width * 0.95f).dp)
            .background(colour),
        contentAlignment = Alignment.Center) {
        Text(word, color = ink,
             fontSize = (width / 13).coerceIn(8, 13).sp,
             fontWeight = FontWeight.Bold,
             maxLines = 1, softWrap = false,
             modifier = Modifier.padding(vertical = 2.dp))
    }
}

/**
 * A poster. Focus lifts and rings it, because on a TV the remote gives no other clue
 * about where you are.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun Poster(m: Media, width: Int = 150, fill: Boolean = false,
           /** what to say under the title instead of the usual line */
           instead: String? = null,
           /** the caller's own, for the one poster a screen wants to stand on */
           modifier: Modifier = Modifier,
           /** held rather than pressed, where a screen has something to offer for it */
           onHold: (() -> Unit)? = null,
           /** an episode's number in the corner - Home only, where an episode card
            *  stands among films and series and its number is what tells it apart */
           episodeMark: Boolean = false,
           /** on a series: the number of its newest episode, as an episode's own */
           latestMark: Boolean = false,
           /** written across the foot of the picture: an episode's number and name,
            *  where every episode wears the same season poster and a line under it
            *  fell below the edge of a television's screen */
           onPicture: String? = null,
           /** left out of the collection's shuffle: 1 part of it, 2 all of it - red */
           shuffleOff: Int = 0,
           /** the lines held under the picture: two for a row of episodes, whose names
            *  wrap; one for a row of seasons */
           captionLines: Int = 2,
           /** the row is measured for one line, and a second spills into the room under it
            *  rather than making the row taller: a series page sized to its screen */
           spill: Boolean = false,
           onClick: () -> Unit) {
    // Tied to the film, because a lazy grid reuses these.
    //
    // Left unkeyed, the flag belongs to the slot rather than to what is in it:
    // scroll the focused poster off the top and the slot comes back round with
    // somebody else in it and the ring still on. Two posters then looked focused
    // at once, and the one that had kept the ring was usually first in its row -
    // which reads as the remote having jumped there.
    var focused by remember(m.ratingKey) { mutableStateOf(false) }
    // drawn only where a ring means something; see focusShows()
    val ring = focused && focusShows()
    // whether the press going on now has become a hold, which is what tells the
    // release apart from an ordinary one
    var heldByKey by remember { mutableStateOf(false) }
    // what the poster actually came out as, in pixels: with fill set the column
    // decides its width and the caller's number is only a default
    var realPx by remember { mutableStateOf(0) }
    val press = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }
    // Four per cent, not six. The lift grows the picture inside its own cell now,
    // and what it grows into is the padding around it - eight points, which six
    // per cent of a poster overruns, so the white ring was cut off top and bottom.
    // Four fits, and reads the same across a room.
    val scale by animateFloatAsState(if (ring) 1.04f else 1f, label = "posterScale")
    // came in since the Watchlist was last opened: an accent ring coming and going, the
    // same as the heading that said so
    val fresh = m.ratingKey in WatchBlink.fresh
    val pulse = if (fresh) androidx.compose.animation.core.rememberInfiniteTransition(
        label = "fresh").animateFloat(1f, 0.15f, androidx.compose.animation.core.infiniteRepeatable(
            androidx.compose.animation.core.tween(600),
            androidx.compose.animation.core.RepeatMode.Reverse), label = "fresh").value else 0f
    // the pixels this will actually occupy, so the server can send that and no more
    val density = androidx.compose.ui.platform.LocalDensity.current.density
    val pixels = remember(width, density) { ((width * density).toInt() / 20) * 20 }
    Column(
        // in a grid the cell decides the width, and the poster takes all of it; in a
        // row there is no cell, so it keeps the width it was given
        modifier.padding(
            horizontal = 5.dp,
            // less above and below a poster where the shelves are stacked close
            vertical = if (androidx.compose.ui.platform.LocalConfiguration
                               .current.screenHeightDp < 560) 4.dp else 8.dp)
            .then(if (fill) Modifier.fillMaxWidth() else Modifier.width(width.dp))
            // hasFocus as well as isFocused: focus asked for by name can land on the
            // group around a control rather than the control itself, and the
            // ring then never appeared although the remote was there
            .onFocusChanged { focused = it.isFocused || it.hasFocus }
            .focusable()
            // No highlight of its own. The press indication is drawn over the whole
            // column - poster, title and line under it - so it came up as a grey
            // square bigger than the poster. The white ring already says where the
            // remote is, which is the only thing that needs saying.
            // Select on the remote, watched rather than taken: every event is passed
            // on, because removing this stopped a hold being a hold at all - the
            // clickable below needs to see the whole press. What it is here for is to
            // know when the hold has begun, which is the moment the key starts
            // repeating, and to shut the gate again when the press is released.
            .then(if (onHold == null) Modifier else Modifier.onPreviewKeyEvent { e ->
                val key = e.nativeKeyEvent
                val select = key.keyCode == android.view.KeyEvent.KEYCODE_DPAD_CENTER ||
                    key.keyCode == android.view.KeyEvent.KEYCODE_ENTER ||
                    key.keyCode == android.view.KeyEvent.KEYCODE_NUMPAD_ENTER
                if (!select) return@onPreviewKeyEvent false
                when {
                    // The press begins. Nothing is a hold yet.
                    key.action == android.view.KeyEvent.ACTION_DOWN &&
                        key.repeatCount == 0 -> {
                        heldByKey = false
                        false                  // a plain press is the clickable's
                    }
                    // It repeats, so it is a hold. The menu is opened here because
                    // nothing else will: the clickable below reports a long click from
                    // a finger and never from this remote, which is why a four-second
                    // hold on it came out as an ordinary press.
                    key.action == android.view.KeyEvent.ACTION_DOWN -> {
                        if (!heldByKey) {
                            heldByKey = true
                            justHeld(key.downTime)
                            onHold()
                        }
                        true                   // and its repeats are nobody else's
                    }
                    // The release of a hold. Taken here and gone: the menu it asked
                    // for is already up, and passed on it would press the first thing
                    // in it - which is how holding a poster went to the title.
                    key.action == android.view.KeyEvent.ACTION_UP && heldByKey -> {
                        // A key still down when a window opens is released twice: the
                        // window losing the focus is sent a cancelled one, and the
                        // real release goes to the window that took it. Closing on the
                        // first left the gate shut when the second arrived, and that
                        // second is the one that pressed the menu.
                        if (!key.isCanceled) {
                            heldByKey = false
                            holdIsOver()
                        }
                        true
                    }
                    else -> false              // an ordinary press, left to the clickable
                }
            })
            .then(if (onHold == null)
                      Modifier.clickable(interactionSource = press, indication = null,
                                         onClick = onClick)
                  else Modifier.combinedClickable(
                      interactionSource = press, indication = null,
                      onClick = onClick,
                      // The gate is opened here because this is where a hold actually
                      // comes from. A hand-written key handler sat below .focusable()
                      // in this chain for three versions, which is past the node that
                      // holds the focus - it never received an event, so nothing ever
                      // armed the guard and the release went on pressing the first
                      // item in the menu it had just opened.
                      // a finger, which does report a long press. The gate is not
                      // armed for it: a finger lifts over the poster and lands on
                      // nothing, so there is no release to swallow.
                      onLongClick = onHold))
    ) {
        // anything placed against the poster's own edges is worked out from the width
        // it was drawn at, not the one that was asked for
        val bandW = if (realPx > 0) (realPx / density).toInt() else width
        // a live channel is a round card, its logo in the middle
        val round = m.live || m.liveGroup.isNotEmpty()
        val shape = if (round) androidx.compose.foundation.shape.CircleShape
                    else RoundedCornerShape(Skin.round(10.dp))
        Box(
            Modifier.fillMaxWidth()
                .then(if (fill) Modifier.aspectRatio(if (round) 1f else 2f / 3f)
                      else Modifier.height((width * (if (round) 1.0 else 1.5)).dp))
                // The lift, on the picture and nowhere else: it is a drawing
                // instruction, so the picture grows and the slot does not. Anywhere
                // in the column's own chain it changes the bounds a lazy grid
                // measures, and the page drifts as the remote moves sideways.
                //
                // and faded where this machine has no file for it: the place is real
                // and worth showing, the film simply is not here tonight.
                .graphicsLayer {
                    scaleX = scale; scaleY = scale
                    if (m.unplayable) alpha = 0.42f
                }
                .onSizeChanged { realPx = it.width }
                // no shadow: it is a blur pass under a moving poster, and the white
                // ring already says which one has the focus
                .clip(shape)
                // white, not accent: half the things on screen are already accent, and
                // on a television the only question is which one the remote is pointing at
                .border(if (ring || shuffleOff > 0 || fresh) 3.dp else 0.dp,
                        if (ring) Color.White
                        else if (shuffleOff > 0) Color(0xFFD64A40)
                        else if (fresh) Skin.Accent.copy(alpha = pulse)
                        else Color.Transparent,
                        shape)
        ) {
            // A film on offer is shown in full colour like any other: the artwork is
            // what makes it recognisable, and drained of it a shelf of them read as
            // broken rather than available. The mark below says it is not here yet.
            // greyed for an episode that has not come yet: it is a date, not a film
            // a series on the released shelf wears the season its newest episode is in
            Art(Api.artUrl(m, pixels, if (latestMark) m.latestThumb.ifEmpty { null } else null),
                m.title,
                Modifier.fillMaxSize().then(if (m.upcoming || m.away) Modifier.alpha(
                    if (m.fetchable) 0.75f else 0.38f) else Modifier),
                mark = width / 3,
                // offline viewing: colour for what is kept on this device, grey for the rest
                grey = Offline.view() && !Offline.has(m))
            if (shuffleOff == 2)
                Box(Modifier.fillMaxSize().background(Color(0x5CD64A40)))
            // A band across the corner for anything that is not here and cannot be
            // played: it reads at a glance on a shelf where everything else can be.
            // The poster is clipped, so the band ends at its edges.
            // Only something that can be asked for and nothing else. A film on a
            // pack wears the download mark it always wore: the band is for titles
            // with no file anywhere, and put on both it covered the whole library.
            //
            // a film on offer from a torrent pack: how far its download has got, from the live
            // download list (refreshed every 10 s); the shelf row is a snapshot from when the
            // grid loaded and kept showing Queued or 0% while the film came in
            // and a new release being fetched from the tracker, which is a title to ask
            // for wearing a download
            val live = if (m.offered || m.askable)
                Api.downloading.value.firstOrNull { it.ratingKey == m.ratingKey } else null
            val coming = m.offered || live != null ||
                (m.askable && m.offerState in setOf("queued", "downloading"))
            // gone from the live list after getting near the end: it is in, whatever
            // the shelf's snapshot still says
            val state = live?.offerState
                ?: if (m.offered && Api.cameIn(m.ratingKey)) "done" else m.offerState
            val progress = live?.offerProgress ?: m.offerProgress
            val eta = live?.offerEta ?: m.offerEta
            val mbit = live?.offerMbit ?: m.offerMbit
            // which version: a film already here showed a percentage and nothing to
            // say what was coming in
            val version = (live?.offerVersion ?: m.offerVersion).let { if (it.isEmpty()) "" else "$it · " }
            // the mark always, and words only when there are some: a film nobody
            // has asked for yet said nothing but a character, and on a television
            // that was a smudge in the corner of the picture
            // come in since this shelf was read: a film now, whatever the shelf still
            // says. The ribbon said Download over a film that was already here.
            val arrived = (m.offered || m.askable) && live == null && Api.cameIn(m.ratingKey)
            val said = if (!coming || arrived) "" else
                Route.posterDownload(state, progress.toDouble(), mbit.toDouble())
            // The same band across the corner for the three things a poster says
            // about itself; only the word in it changes. One band, chosen: three
            // conditions each drawing their own put two words over each other in
            // the one corner, which is the corner all three want.
            // A shelf being shuffled, standing on Continue watching as one row. Said
            // across the poster because the row reads as the episode it happens to be
            // showing otherwise, and pressing it plays whatever the hat has next
            // rather than that programme.
            // nothing happening yet: the band, which reads across a room. Once it is
            // coming in the badge stays - the band holds one word, and the percentage
            // and the rate are the point while it arrives.
            // something on the tracker for it: the band says what pressing it
            // does, and asking is not what it does any more
            if (m.askable && said.isNotEmpty()) { /* coming in: the badge says how far */ }
            else if (arrived) CornerBand("NEW", bandW, Color(0xFFD9A21B))
            // on SVT Play: it streams from there, which beats asking or downloading
            else if (Route.streamBand(m.askable, m.addable, m.svtId))
                CornerBand("STREAM", bandW, Color(0xFF1E9E6A))
            // grey where all there is was filmed in a cinema: a cam or a telesync
            else if (m.askable && m.onTracker) CornerBand("DOWNLOAD", bandW,
                                                          Route.downloadBand(m.cam))
            // not asked of the tracker yet: neither word is known
            else if (m.askable && m.checking && !m.asked) CornerBand("CHECKING", bandW, Skin.Dim)
            else if (m.askable) CornerBand(if (m.asked) "ASKED" else "REQUEST", bandW,
                                      if (m.asked) Color(0xFF2F6B35) else Skin.Accent)
            // on the tracker already, before its date or after: it can be fetched
            else if (m.upcoming && m.fetchable) CornerBand("DOWNLOAD", bandW)
            // out already, and nothing here or on the tracker to fetch it from
            else if (m.upcoming && m.missing) CornerBand("MISSING", bandW, Skin.Dim)
            // out today, and not on the tracker yet
            else if (m.upcoming && m.airsToday) CornerBand("TODAY", bandW, Color(0xFFE6E8EB), Color(0xFF111111))
            else if (m.upcoming) CornerBand(m.band.uppercase().ifEmpty { Route.upcomingBand(m.airDate.ifEmpty { m.released },
                java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).format(java.util.Date())) },
                bandW, Skin.Dim)
            else if (m.shuffle.isNotEmpty()) CornerBand(Route.shuffleBand(m.shuffleId), bandW)
            // here, and aired this past week
            else if (m.fresh) CornerBand("NEW", bandW, Color(0xFFD9A21B))
            // off the popular list and not held yet: pressing it adds it
            else if (m.addable) when (Route.seriesBand(true, m.onTracker, m.checking)) {
                "CHECKING" -> CornerBand("CHECKING", bandW, Skin.Dim)
                "REQUEST" -> CornerBand("REQUEST", bandW, Skin.Accent)
                else -> CornerBand("ADD", bandW)
            }
            else if (m.away) CornerBand("DOWNLOAD", bandW)
            else if (m.offered && said.isEmpty() && !arrived) CornerBand("DOWNLOAD", bandW)
            if (coming && said.isNotEmpty()) {
                Row(Modifier.align(Alignment.TopStart).padding(5.dp)
                        .background(Color(0xD9070A0E), RoundedCornerShape(Skin.round(5.dp)))
                        .padding(horizontal = 4.dp, vertical = 3.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    DownloadMark(13.dp)
                    Spacer(Modifier.width(3.dp))
                    Text(said, color = Skin.Fg, fontSize = 10.sp,
                         fontWeight = FontWeight.SemiBold)
                }
            }
            // held in 2160 lines somewhere. Bottom right: the watched tick has the
            // top right, and the progress bar runs along the bottom, so it is
            // lifted clear of that.
            // which episode, bottom right: S02E03 - lifted above the 4K mark when both
            // on a television only on the poster the remote is on; a phone has no such
            // poster, so there it stays
            val markSE = when {
                episodeMark && m.type == "episode" && m.parentIndex != null && m.index != null ->
                    Pair(m.parentIndex!!, m.index!!)
                latestMark && m.type == "show" && m.latestNumber > 0 ->
                    Pair(m.latestSeason, m.latestNumber)
                else -> null
            }
            if (markSE != null && (ring || !focusShows())) {
                Box(Modifier.align(Alignment.BottomEnd)
                        .padding(start = 5.dp, top = 5.dp, end = 5.dp,
                                 bottom = if (m.maxHeight >= 1700) 28.dp else 8.dp)
                        .background(Color(0xD9070A0E), RoundedCornerShape(Skin.round(5.dp)))
                        .padding(horizontal = 5.dp, vertical = 2.dp)) {
                    Text(String.format(java.util.Locale.US, "S%02dE%02d", markSE.first, markSE.second),
                         color = Skin.Fg, fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
                }
            }
            // on a television the marks below show on the focused poster only; the
            // ribbons stay. A phone has no focus, so there they always show.
            val marks = ring || !focusShows()
            // held in more than one cut: bottom left, opposite the 4K mark
            if (marks && m.cuts > 1) {
                Box(Modifier.align(Alignment.BottomStart)
                        .padding(start = 5.dp, top = 5.dp, end = 5.dp, bottom = 8.dp)
                        .background(Color(0xD9070A0E), RoundedCornerShape(Skin.round(5.dp)))
                        .padding(horizontal = 5.dp, vertical = 2.dp)) {
                    Text("${m.cuts} CUTS", color = Color(0xFFE0B341), fontSize = 10.sp,
                         fontWeight = FontWeight.SemiBold)
                }
            }
            if (marks && m.maxHeight >= 1700) {
                Box(Modifier.align(Alignment.BottomEnd)
                        .padding(start = 5.dp, top = 5.dp, end = 5.dp, bottom = 8.dp)
                        .background(Color(0xD9070A0E), RoundedCornerShape(Skin.round(5.dp)))
                        .padding(horizontal = 5.dp, vertical = 2.dp)) {
                    Text("4K", color = Skin.Accent, fontSize = 10.sp,
                         fontWeight = FontWeight.SemiBold)
                }
            }
            // finished, or part way through a series. Not under a band: both want the
            // same corner, and a shuffled row is the shelf rather than the episode it
            // happens to be showing.
            // a season or a series with every episode seen gets the tick a film gets;
            // part way through is not counted on the poster
            val allSeen = m.watched ||
                (m.episodeCount > 0 && m.watchedEpisodes >= m.episodeCount)
            if (allSeen && m.shuffle.isEmpty()) {
                Box(Modifier.align(Alignment.TopEnd).padding(5.dp)
                        .background(Color(0xD9070A0E), RoundedCornerShape(Skin.round(5.dp)))
                        .padding(horizontal = 5.dp, vertical = 2.dp)) {
                    Text("✓", color = Color(0xFF5FD08A), fontSize = 12.sp,
                         fontWeight = FontWeight.SemiBold)
                }
            }
            val p = m.progressFraction()
            // the machine that keeps copies is holding this one: it plays when the
            // server with the library on it is off. Bottom left, lifted clear of the
            // progress bar where there is one.
            val dot = marks && Api.copiedHere(m)
            if (dot) {
                Box(Modifier.align(Alignment.BottomStart)
                        .padding(start = 6.dp, bottom = if (p > 0f) 12.dp else 7.dp)
                        .size(9.dp)
                        .background(Color(0xFF42C96A), RoundedCornerShape(Skin.round(5.dp))))
            }
            // a favorite: a red heart in the corner, over the dot when there is one
            if (m.ratingKey in Api.favKeys.value) {
                Text("\u2665", color = Color(0xFFFF4D5E), fontSize = 15.sp,
                     style = androidx.compose.ui.text.TextStyle(
                         shadow = androidx.compose.ui.graphics.Shadow(Color.Black,
                                                                     blurRadius = 4f)),
                     modifier = Modifier.align(Alignment.BottomStart)
                         .padding(start = 5.dp,
                                  bottom = ((if (p > 0f) 10 else 4) +
                                            (if (dot) 11 else 0)).dp))
            }
            if (!onPicture.isNullOrEmpty()) {
                Box(Modifier.align(Alignment.BottomStart).fillMaxWidth()
                        .background(androidx.compose.ui.graphics.Brush.verticalGradient(
                            listOf(Color(0x00000000), Color(0xE6000000))))
                        .padding(start = 8.dp, end = 8.dp, top = 22.dp,
                                 bottom = if (p > 0f) 10.dp else 7.dp)) {
                    Text(onPicture, color = Color.White,
                         fontSize = if (width < 110) 11.sp else 13.sp,
                         fontWeight = FontWeight.SemiBold, maxLines = 3,
                         overflow = TextOverflow.Ellipsis, lineHeight = 15.sp)
                }
            }
            if (p > 0f) {
                Box(Modifier.align(Alignment.BottomStart).fillMaxWidth().height(4.dp)
                    .background(Color(0xAA000000))) {
                    Box(Modifier.fillMaxWidth(p).fillMaxHeight().background(Skin.Accent))
                }
            }
        }
        // No name under the picture: a poster is the title written large by somebody
        // paid to make it recognisable, and a shelf of names cut off halfway told
        // nobody anything the artwork had not. Except where the caller asks for a
        // line - a season, an episode - because "Season 3" is nowhere in the art: one
        // programme's seasons are the same picture four times over.
        // a season with no line of its own still says which: its picture is often the
        // programme's, and a favourite or a Home card said only the programme
        val line = Route.posterLine(m.type, m.index, instead, m.grandparentTitle)
        if (line != null && line.isNotEmpty()) {
            Text(line, color = if (ring) Skin.Fg else Color(0xFFD3DAE2),
                 fontSize = if (width < 110) 11.sp else 12.5.sp,
                 // the theme's 24sp line left a blank line's gap inside a wrapped name
                 lineHeight = if (width < 110) 13.sp else 15.sp,
                 // two lines held whether used or not: a name that wrapped made its
                 // row taller as it scrolled in, and every poster in it moved
                 fontWeight = FontWeight.Medium,
                 minLines = if (spill) 1 else captionLines, maxLines = captionLines,
                 overflow = TextOverflow.Ellipsis,
                 modifier = if (spill)
                     // one line of height in the layout; a second is drawn below it,
                     // over the page's bottom margin, and moves nothing
                     Modifier.padding(top = 5.dp)
                         .height(with(androidx.compose.ui.platform.LocalDensity.current) {
                             (if (width < 110) 13.sp else 15.sp).toDp() })
                         .wrapContentHeight(align = Alignment.Top, unbounded = true)
                 else Modifier.padding(top = 5.dp))
        }
    }
}

/** A small fact, the sort that belongs beside a title rather than in a sentence. */
@Composable
fun Chip(text: String, accent: Boolean = false) {
    Text(
        text,
        color = if (accent) Skin.OnAccent else Skin.Dim,
        fontSize = 11.5.sp,
        fontWeight = FontWeight.Medium,
        modifier = Modifier.padding(end = 8.dp)
            .background(if (accent) Skin.Accent else Skin.Panel2, RoundedCornerShape(Skin.round(6.dp)))
            .padding(horizontal = 9.dp, vertical = 4.dp),
    )
}

/** A search spoken rather than typed: the platform's speech dialog, on a phone or a
 *  television, where the device has one; nothing where it does not. */
@Composable
fun SpeakPill(onText: (String) -> Unit, small: Boolean = false) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val ask = remember {
        val said = android.content.Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                      android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(android.speech.RecognizerIntent.EXTRA_PROMPT, "Search")
        // Speech Recognition by Google where the device has it, whatever was saved as
        // Always: Google voice search chosen once on the streamer stuck for good
        said.setPackage(SPEECH_PACKAGE)
        if (said.resolveActivity(ctx.packageManager) == null) said.setPackage(null)
        said
    }
    val hear = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.StartActivityForResult()) { r ->
        r.data?.getStringArrayListExtra(android.speech.RecognizerIntent.EXTRA_RESULTS)
            ?.firstOrNull()?.trim()?.takeIf { it.isNotEmpty() }?.let(onText)
    }
    val can = remember { ask.resolveActivity(ctx.packageManager) != null }
    if (!can) return
    // the microphone, as every search box draws it
    Pill("Search by voice", filled = true, small = small, icon = MicIcon) {
        try { hear.launch(ask) } catch (_: android.content.ActivityNotFoundException) {}
    }
}

/** The recognizer Speak asks for by name, before whatever the device has as default. */
const val SPEECH_PACKAGE = "com.google.android.tts"

/** The microphone: Material's mic, the mark every search box uses for speaking. */
val MicIcon: androidx.compose.ui.graphics.vector.ImageVector by lazy {
    androidx.compose.ui.graphics.vector.ImageVector.Builder(
        name = "Mic", defaultWidth = 24.dp, defaultHeight = 24.dp,
        viewportWidth = 24f, viewportHeight = 24f)
        .addPath(androidx.compose.ui.graphics.vector.PathParser()
                     .parsePathString("M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-2.1A7 7 0 0 0 19 12z")
                     .toNodes(),
                 fill = SolidColor(Color.White))
        .build()
}

/** Pill button used for the tabs and for anything the remote should be able to reach. */
@Composable
fun Pill(label: String, active: Boolean = false, primary: Boolean = false,
         outline: Boolean = false,
         /* a ground of its own while resting, for a control that has to look like a
            control on a phone - where nothing is ever focused and a bare word on a
            dark page is indistinguishable from a label */
         filled: Boolean = false,
         /* a white ring while resting: something has finished and is waiting to be
            pressed, which is worth catching the eye even before the remote arrives */
         ready: Boolean = false,
         /* tighter: five rows of full-sized pills did not fit a TV screen */
         small: Boolean = false,
         /* the same height, less width either side: a row of tabs and the two controls
            that belong with them do not fit across a phone at the full width, and a
            shorter pill is easier to press than one that is off the screen */
         narrow: Boolean = false,
         /* shown but not usable: a control that does not apply to what is playing.
            Hiding it would leave somebody hunting for a setting that is not missing,
            only inapplicable - so it stays, faded, and the reason is said beside it */
         dim: Boolean = false,
         /* drawn instead of the words, which then only name it for a screen reader */
         icon: androidx.compose.ui.graphics.vector.ImageVector? = null,
         /* an accent ring coming and going: something behind it has come in and not been
            looked at yet */
         blink: Boolean = false,
         modifier: Modifier = Modifier, onClick: () -> Unit) {
    // The release of the hold that opened a menu is not a press of what is in it.
    // Dropped here as well as at the window, because a menu that has just appeared
    // has not always taken the focus by the time that release arrives - and the item
    // it landed on was the first one, Go to title.
    val press = onClick
    val guarded: () -> Unit = { if (!stillTheHold()) press() }
    var focused by remember { mutableStateOf(false) }
    // drawn only where a ring means something; see focusShows()
    val ring = focused && focusShows()
    val pulse = if (blink) rememberInfiniteTransition(label = "blink").animateFloat(
        1f, 0f, androidx.compose.animation.core.infiniteRepeatable(
            androidx.compose.animation.core.tween(600),
            androidx.compose.animation.core.RepeatMode.Reverse), label = "blink").value else 0f
    val bg = when {
        active || primary -> Skin.Accent
        ring || filled -> Skin.Panel2
        else -> Color.Transparent
    }
    Box(
        // the gap to the next one, tighter on a small pill: three of these and a
        // narrow phone is 360 points across, not the 411 a Pixel gives
        modifier.padding(end = if (small) 4.dp else 8.dp)
            .clip(RoundedCornerShape(Skin.round(999.dp)))
            .background(bg)
            // A yellow pill on a dark ground reads as "chosen", which is not the same as
            // "the remote is here". The white ring says the second, over either state,
            // and an accent outline marks a second action that is not the main one.
            .border(if (ring || outline || ready || blink) 2.dp else 0.dp,
                    when {
                        ring -> Color.White
                        ready -> Color.White
                        blink -> Skin.Accent.copy(alpha = pulse)
                        outline -> Skin.Accent
                        else -> Color.Transparent
                    },
                    RoundedCornerShape(Skin.round(999.dp)))
            // hasFocus as well as isFocused: focus asked for by name can land on the
            // group around a control rather than the control itself, and the
            // ring then never appeared although the remote was there
            .onFocusChanged { focused = it.isFocused || it.hasFocus }
            .focusable()
            .then(if (dim) Modifier.alpha(0.45f) else Modifier)
            .clickable(enabled = !dim, onClick = guarded)
            // and on a short screen the tall ones come down to the small size:
            // a row of pills nine points deep either side is a shelf's worth of
            // height above the shelves, on the one screen that has none to spare
            .padding(horizontal = if (small) 10.dp else if (narrow) 12.dp else 18.dp,
                     vertical = if (small ||
                                    androidx.compose.ui.platform.LocalConfiguration
                                        .current.screenHeightDp < 560) 5.dp else 9.dp),
    ) {
        if (icon != null) {
            androidx.compose.material3.Icon(icon, contentDescription = label,
                tint = if (active || primary) Skin.OnAccent else Skin.Fg,
                modifier = Modifier.size(if (small) 16.dp else 19.dp))
            return@Box
        }
        Text(label,
             color = if (active || primary) Skin.OnAccent else Skin.Fg,
             fontSize = if (small ||
                           androidx.compose.ui.platform.LocalConfiguration
                               .current.screenHeightDp < 560) 12.5.sp else 14.sp,
             // never let a squeezed row wrap a button caption into a column of letters
             maxLines = 1,
             softWrap = false,
             overflow = TextOverflow.Clip,
             fontWeight = if (active || primary) FontWeight.SemiBold else FontWeight.Normal)
    }
}

/**
 * Whether a focus ring is worth drawing: something is steering by remote or keyboard.
 *
 * A ring says where the remote is, and on a phone there is no remote. Focus is still
 * handed out there - the app asks for it by name, a tab asks for it when pressed - so
 * the ring sat on whatever was touched last and stayed after the screen changed.
 */
@Composable
fun focusShows(): Boolean =
    onTv() ||
    androidx.compose.ui.platform.LocalInputModeManager.current.inputMode ==
        androidx.compose.ui.input.InputMode.Keyboard

/** Which keyboard a television types with: the app's own on screen, or - when this
 *  device is set so - the system's, which Google TV hands to a phone. Kept per device. */
object Typing {
    val phone = androidx.compose.runtime.mutableStateOf(false)
    private var read = false
    fun load(ctx: android.content.Context) {
        if (read) return
        read = true
        phone.value = ctx.getSharedPreferences("palladium", android.content.Context.MODE_PRIVATE)
            .getBoolean("phoneKeyboard", false)
    }
    fun set(ctx: android.content.Context, on: Boolean) {
        phone.value = on
        ctx.getSharedPreferences("palladium", android.content.Context.MODE_PRIVATE)
            .edit().putBoolean("phoneKeyboard", on).apply()
    }
}

/** True on a television, where the remote is the only input device. */
@Composable
fun onTv(): Boolean {
    val ui = androidx.compose.ui.platform.LocalContext.current.resources.configuration.uiMode
    return ui and android.content.res.Configuration.UI_MODE_TYPE_MASK ==
        android.content.res.Configuration.UI_MODE_TYPE_TELEVISION
}

/**
 * A text box that can actually be typed into on a television.
 *
 * On a phone this is an ordinary Compose field. On a television, pressing it opens a
 * dialog holding a platform EditText and asks for the keyboard - the one thing that
 * reliably brings up Gboard for TV. Without it Google TV shrugs and suggests typing on
 * a phone instead, which is a poor way to search a film library.
 */
@Composable
fun TextBox(
    value: String,
    placeholder: String,
    modifier: Modifier = Modifier,
    multiline: Boolean = false,
    /** dots rather than letters, for the one box where somebody is being watched */
    password: Boolean = false,
    /** a name to offer on the keyboard as one press: the programme, for a collection */
    suggestion: String = "",
    onValue: (String) -> Unit,
) {
    var editing by remember { mutableStateOf(false) }
    // the app's own keyboard on a television, unless this device is set to type on a phone
    Typing.load(androidx.compose.ui.platform.LocalContext.current)
    val tv = onTv() && !Typing.phone.value
    var focused by remember { mutableStateOf(false) }

    Box(
        modifier
            .background(Skin.Panel, RoundedCornerShape(Skin.round(8.dp)))
            .border(if (focused) 2.dp else 0.dp,
                    if (focused) Color.White else Color.Transparent, RoundedCornerShape(Skin.round(8.dp)))
            .then(if (tv) Modifier
                // hasFocus as well as isFocused: focus asked for by name can land on the
            // group around a control rather than the control itself, and the
            // ring then never appeared although the remote was there
            .onFocusChanged { focused = it.isFocused || it.hasFocus }
                .focusable()
                .clickable { editing = true } else Modifier)
            // On a short screen the box stands on the same line as the name and the
            // picker, and a box twice their height is what pushed the second shelf
            // off the bottom. The letters stay readable; it is the air that goes.
            .padding(horizontal = 12.dp,
                     vertical = if (androidx.compose.ui.platform.LocalConfiguration
                                        .current.screenHeightDp < 560) 3.dp else 12.dp),
    ) {
        if (value.isEmpty()) Text(placeholder, color = Skin.Dim, fontSize = 14.sp)
        // a password is dots wherever it is drawn: a television is watched by
        // whoever is in the room
        val shown = if (password) "•".repeat(value.length) else value
        if (tv) {
            // a plain label on TV: the dialog does the typing
            if (value.isNotEmpty()) Text(shown, color = Skin.Fg, fontSize = 14.sp)
        } else {
            BasicTextField(
                value = value,
                onValueChange = onValue,
                singleLine = !multiline,
                textStyle = TextStyle(color = Skin.Fg, fontSize = 14.sp),
                cursorBrush = SolidColor(Skin.Accent),
                visualTransformation = if (password)
                    androidx.compose.ui.text.input.PasswordVisualTransformation()
                else androidx.compose.ui.text.input.VisualTransformation.None,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }

    if (editing) {
        KeyboardDialog(value, placeholder, multiline, suggestion,
                       onDone = { onValue(it); editing = false },
                       onDismiss = { editing = false })
    }
}

/**
 * The app's own keyboard, for a television.
 *
 * Not a system IME and deliberately so: Google TV keeps reinstating the bridge that
 * defers typing to a phone, and an app cannot out-argue it. A grid of keys the remote
 * can walk is entirely ours, works on every box, and needs no permission.
 */
@Composable
private fun KeyboardDialog(
    initial: String,
    label: String,
    multiline: Boolean,
    suggestion: String,
    onDone: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var buffer by remember { mutableStateOf(initial) }
    // A token is mixed case and carries underscores; an address carries a colon and
    // slashes. Without these there are things this app asks for that cannot be typed
    // on the one keyboard a television has.
    // Shift, as a telephone does it: off, on for one letter, or held down. A title
    // wants a capital at the front and nothing else, which is one press of the arrow;
    // a token wants several, which is two.
    // 0 off, 1 for the next letter only, 2 until pressed again
    var shift by remember { mutableStateOf(1) }
    val first = remember { FocusRequester() }
    // Ten to a line, which puts the digits on one row in the order everybody reads
    // them. The dialog below is wide enough to hold it - the earlier one was not, and
    // 8 and 9 hung off the edge where the remote could not reach them.
    //
    // The Swedish three sit at the end of the alphabet, where they belong: what gets
    // typed here is mostly film titles, and without them somebody writes "Grasanka"
    // and means "Gräsänka". The punctuation has a row of its own now.
    val rows = remember {
        listOf("abcdefghij", "klmnopqrst", "uvwxyzåäö", "0123456789",
               "-._:/,!?' ")
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        // Material caps a dialog at 560dp and ignores any width asked for; a
        // television has three times that, and a keyboard needs it
        properties = androidx.compose.ui.window.DialogProperties(
            usePlatformDefaultWidth = false),
        modifier = Modifier.fillMaxWidth(0.92f),
        containerColor = Skin.Panel,
        title = { Text(label, color = Skin.Dim, fontSize = 14.sp) },
        text = {
            Column {
                // what has been typed so far, large enough to read from a sofa
                Text(buffer.ifEmpty { " " }, color = Skin.Fg, fontSize = 20.sp,
                     modifier = Modifier.fillMaxWidth()
                         .background(Skin.Panel2, RoundedCornerShape(Skin.round(8.dp)))
                         .padding(horizontal = 14.dp, vertical = 12.dp))
                // offered as one press, and kept offered while it is not what is typed
                if (suggestion.isNotBlank() && buffer != suggestion) {
                    Spacer(Modifier.height(8.dp))
                    Pill(suggestion) { buffer = suggestion }
                }
                Spacer(Modifier.height(12.dp))
                // every row fills the width and its keys share it equally, so a
                // row of ten fits by construction rather than by my arithmetic
                rows.forEachIndexed { r, row ->
                    Row(Modifier.fillMaxWidth()) {
                        row.forEachIndexed { c, ch ->
                            // the key shows the character it will produce: on a screen
                            // across a room that is the only way to know the case
                            val shown = if (shift > 0) ch.uppercaseChar() else ch
                            Key(shown.toString(),
                                modifier = Modifier.weight(1f).then(
                                    if (r == 0 && c == 0)
                                        Modifier.focusRequester(first) else Modifier)) {
                                buffer += shown
                                // one letter's worth of shift is spent on it
                                if (shift == 1) shift = 0
                            }
                        }
                    }
                }
                Row(Modifier.fillMaxWidth()) {
                    // one press: a capital for the next letter. Two: capitals until
                    // it is pressed again. Three: back to lower case.
                    Key(when (shift) {
                            1 -> "↑"          // for the next letter
                            2 -> "⇧ ABC"      // held down
                            else -> "⇡"       // off
                        },
                        modifier = Modifier.weight(1f)) {
                        shift = (shift + 1) % 3
                    }
                    Key("space", modifier = Modifier.weight(1f)) { buffer += " " }
                    Key("\u232b", modifier = Modifier.weight(1f)) {
                        buffer = buffer.dropLast(1)
                    }
                    Key("clear", modifier = Modifier.weight(1f)) { buffer = "" }
                }
            }
        },
        confirmButton = { Pill("Done", primary = true) { onDone(buffer) } },
        dismissButton = { Pill("Cancel") { onDismiss() } },
    )

    LaunchedEffect(Unit) { runCatching { first.requestFocus() } }
}

/** One key. Focus rings it white, the way everything else on a television does. */
@Composable
private fun Key(label: String, modifier: Modifier = Modifier,
                onPress: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    Box(
        modifier
            .padding(2.dp)
            .height(48.dp)
            .clip(RoundedCornerShape(Skin.round(7.dp)))
            .background(if (focused) Skin.Accent else Skin.Panel2)
            .border(if (focused) 2.dp else 0.dp,
                    if (focused) Color.White else Color.Transparent, RoundedCornerShape(Skin.round(7.dp)))
            // hasFocus as well as isFocused: focus asked for by name can land on the
            // group around a control rather than the control itself, and the
            // ring then never appeared although the remote was there
            .onFocusChanged { focused = it.isFocused || it.hasFocus }
            .focusable()
            .clickable(onClick = onPress),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = if (focused) Skin.OnAccent else Skin.Fg,
             fontSize = if (label.length > 1) 13.sp else 19.sp, maxLines = 1)
    }
}

@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier) {
    // quieter on a short screen, where every point of height is a shelf that does or
    // does not fit under the tabs
    val small = androidx.compose.ui.platform.LocalConfiguration.current.screenHeightDp < 560
    Text(text.uppercase(), color = Skin.Dim,
         fontSize = if (small) 10.sp else 12.sp,
         letterSpacing = if (small) 1.sp else 1.2.sp,
         fontWeight = FontWeight.SemiBold, fontFamily = Skin.Titles, modifier = modifier)
}

/** A film on offer being fetched: how far and how long, across its poster on its own page. */
@Composable
fun androidx.compose.foundation.layout.BoxScope.OfferProgress(loaded: Media) {
    val m = Api.liveOffer(loaded)          // live progress, not the page's snapshot
    // a film on offer, or any title with a download in the live list: a new release
    // being fetched from the tracker showed nothing on its own page
    if (!(loaded.offered || m !== loaded || loaded.askable) ||
        m.offerState !in setOf("queued", "downloading")) return
    Box(Modifier.align(Alignment.TopEnd).padding(6.dp)
            .background(Color(0xD9070A0E), RoundedCornerShape(Skin.round(6.dp)))
            .padding(horizontal = 7.dp, vertical = 3.dp)) {
        Text(if (m.offerState == "queued") "Queued"
             else "${(m.offerProgress * 100).toInt()}%" +
                  (if (m.offerEta >= 0) " · " + etaShort(m.offerEta) else "") +
                  (if (m.offerMbit > 0) String.format(java.util.Locale.US, " · %.0f Mbit/s", m.offerMbit) else ""),
             color = Skin.Fg, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
    }
}

/** A download's time left, short enough for a poster's corner. */
fun etaShort(s: Long): String = when {
    s < 60 -> "$s s"
    s < 3600 -> "${s / 60} min"
    else -> "${s / 3600} h ${s % 3600 / 60} min"
}

/**
 * Swallow whatever is left of the press that opened this, wherever it lands.
 *
 * A menu opened by holding the button is a window of its own: the repeats and the
 * release of that press go to it, not to the screen behind it, so the guard on the
 * activity never sees them - and the release pressed the first thing in the menu
 * before a finger had left the remote.
 */
fun Modifier.stillHeld(): Modifier = this.then(
    Modifier.onPreviewKeyEvent { e ->
        val key = e.nativeKeyEvent
        // the release of the hold that opened this, and only that: see the note on
        // the same rule in the activity
        if (fromTheHold(key) && key.action == android.view.KeyEvent.ACTION_UP) {
            if (!key.isCanceled) holdIsOver()   // a cancelled one is not the release
            true
        } else false
    })


/**
 * The indication every control is drawn with: a press, and nothing else.
 *
 * The default draws a state layer for focus and hover as well, and on a phone
 * something always holds focus - so a white patch sat on whichever icon had it with
 * nobody having touched it. Where the remote is is shown by this app's white ring,
 * which leaves the press as the only thing an indication has to draw.
 */
object PressOnly : IndicationNodeFactory {
    override fun create(interactionSource: InteractionSource): DelegatableNode =
        PressNode(interactionSource)

    override fun equals(other: Any?): Boolean = other === PressOnly

    override fun hashCode(): Int = 1
}

private class PressNode(private val source: InteractionSource) :
    Modifier.Node(), DrawModifierNode {

    // counted, not a flag: a second finger down and one up left it drawn or not drawn
    // depending on which arrived last
    private var down = 0

    override fun onAttach() {
        coroutineScope.launch {
            source.interactions.collect { i ->
                when (i) {
                    is PressInteraction.Press -> down++
                    is PressInteraction.Release -> down--
                    is PressInteraction.Cancel -> down--
                    else -> return@collect
                }
                invalidateDraw()
            }
        }
    }

    override fun ContentDrawScope.draw() {
        drawContent()
        if (down > 0) {
            // rounded to half the short side: round on an icon, a stadium on a pill,
            // and never a square laid over something with corners
            drawRoundRect(
                Color.White.copy(alpha = 0.13f),
                cornerRadius = CornerRadius(minOf(size.width, size.height) / 2f))
        }
    }
}
