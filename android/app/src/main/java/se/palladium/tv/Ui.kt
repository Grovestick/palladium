package se.palladium.tv

import androidx.compose.animation.core.animateFloatAsState
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage

/** The look of the thing, in one place so the TV and the phone stay consistent. */
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

    val Bg: Color get() = bgIs
    val Panel: Color get() = panelIs
    val Panel2: Color get() = panel2Is
    val Line: Color get() = lineIs
    val Fg: Color get() = fgIs
    val Dim: Color get() = dimIs
    val Accent: Color get() = accentIs

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
    }

    /** "#4a90f0" as the colour to draw with; anything else leaves it alone. */
    fun paint(code: String) {
        accentIs = hex(code, accentIs)
    }
    val Empty = Brush.linearGradient(listOf(Color(0xFF191F27), Color(0xFF12161C)))
}

/**
 * One image loader for the whole app.
 *
 * The default builds its own per request and keeps a small cache, so scrolling back up
 * a grid decodes everything again. A quarter of the heap and a disk cache costs
 * nothing on a television that is doing one thing.
 */
private var loader: coil.ImageLoader? = null

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
 * The press that opened a menu by being held, by the moment it went down.
 *
 * The menu appears with the button still down, and everything that press does from
 * then on - its repeats, and the release at the end of it - arrives at whatever has
 * just taken the focus, which pressed the first thing in the menu. Every event of one
 * press carries the same downTime, so the press that opened the menu can be told from
 * the next one exactly. A window of so many milliseconds cannot: the hold is felt
 * half a second in and the button is let go whenever the hand lets go of it.
 */
private var heldFrom = 0L

fun justHeld(downTime: Long) { heldFrom = downTime }

/** True while an event still belongs to the press that opened something by being held. */
fun fromTheHold(e: android.view.KeyEvent): Boolean =
    heldFrom != 0L && e.downTime == heldFrom &&
        (e.keyCode == android.view.KeyEvent.KEYCODE_DPAD_CENTER ||
         e.keyCode == android.view.KeyEvent.KEYCODE_ENTER ||
         e.keyCode == android.view.KeyEvent.KEYCODE_NUMPAD_ENTER)

/** That press is over: the next one is somebody's own. */
fun holdIsOver() { heldFrom = 0L }

/** Artwork, or the Palladium P when a title has none. Never a broken image. */
@Composable
fun Art(url: String?, title: String, modifier: Modifier = Modifier, mark: Int = 56,
        scale: ContentScale = ContentScale.Crop,
        /* what shows where the picture does not reach. A poster fills its slot, so
           this is the grey a missing one leaves behind; a backdrop is fitted rather
           than cropped, and the grey either side of it reads as a band down the edge
           of the screen - which is what it was doing. */
        ground: androidx.compose.ui.graphics.Brush = Skin.Empty) {
    Box(modifier.background(ground), contentAlignment = Alignment.Center) {
        if (mark > 0) {
            Text("P", color = Color(0xFF2B333D), fontSize = mark.sp,
                 fontFamily = FontFamily.Serif, fontWeight = FontWeight.SemiBold)
        }
        if (url != null) {
            // Crop fills a poster-shaped slot; Fit is for the backdrop, where cropping
            // a tall poster into a squarish box pushes its head and feet off the screen
            AsyncImage(model = url, contentDescription = title, imageLoader = imageLoader(),
                       contentScale = scale, modifier = Modifier.fillMaxSize())
        }
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
           onClick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    // held on the remote: the select button kept down repeats its press
    var heldByKey by remember { mutableStateOf(false) }
    // and pressed here: a release whose press landed on another screen is not a click
    var downByKey by remember { mutableStateOf(false) }
    val press = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }
    val scale by animateFloatAsState(if (focused) 1.06f else 1f, label = "posterScale")
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
            .scale(scale)
            // hasFocus as well as isFocused: focus asked for by name can land on the
            // group around a control rather than the control itself, and the
            // ring then never appeared although the remote was there
            .onFocusChanged { focused = it.isFocused || it.hasFocus }
            .focusable()
            // No highlight of its own. The press indication is drawn over the whole
            // column - poster, title and line under it - so it came up as a grey
            // square bigger than the poster. The white ring already says where the
            // remote is, which is the only thing that needs saying.
            .then(if (onHold == null) Modifier else Modifier.onPreviewKeyEvent { e ->
                val key = e.nativeKeyEvent
                val select = key.keyCode == android.view.KeyEvent.KEYCODE_DPAD_CENTER ||
                    key.keyCode == android.view.KeyEvent.KEYCODE_ENTER ||
                    key.keyCode == android.view.KeyEvent.KEYCODE_NUMPAD_ENTER
                // Acted on at the release. Opened on the repeat, a menu took focus with
                // the button still down and the release pressed its first item, Go to
                // title. Every select event is taken here, so the click handler below
                // cannot fire a long press of its own as well.
                when {
                    !select -> false
                    key.action == android.view.KeyEvent.ACTION_DOWN -> {
                        // Acted on the moment the key repeats, which is when a hold
                        // becomes a hold: waiting for the release meant holding the
                        // button down and nothing happening until you gave up.
                        if (key.repeatCount == 0) downByKey = true
                        else if (!heldByKey) {
                            heldByKey = true
                            downByKey = false     // the release is not a press any more
                            justHeld(key.downTime)
                            onHold()
                        }
                        true
                    }
                    key.action == android.view.KeyEvent.ACTION_UP -> {
                        val pressed = downByKey
                        heldByKey = false
                        downByKey = false
                        if (pressed) onClick()
                        true
                    }
                    else -> true
                }
            })
            .then(if (onHold == null)
                      Modifier.clickable(interactionSource = press, indication = null,
                                         onClick = onClick)
                  else Modifier.combinedClickable(
                      interactionSource = press, indication = null,
                      onClick = onClick, onLongClick = onHold))
    ) {
        Box(
            Modifier.fillMaxWidth()
                .then(if (fill) Modifier.aspectRatio(2f / 3f)
                      else Modifier.height((width * 1.5).dp))
                // no shadow: it is a blur pass under a moving poster, and the white
                // ring already says which one has the focus
                .clip(RoundedCornerShape(10.dp))
                // white, not accent: half the things on screen are already accent, and
                // on a television the only question is which one the remote is pointing at
                .border(if (focused) 3.dp else 0.dp,
                        if (focused) Color.White else Color.Transparent,
                        RoundedCornerShape(10.dp))
        ) {
            // A film on offer is shown in full colour like any other: the artwork is
            // what makes it recognisable, and drained of it a shelf of them read as
            // broken rather than available. The mark below says it is not here yet.
            Art(Api.artUrl(m, pixels), m.title, Modifier.fillMaxSize(), mark = width / 3)
            // a film on offer from a torrent pack: how far its download has got, from the live
            // download list (refreshed every 10 s); the shelf row is a snapshot from when the
            // grid loaded and kept showing Queued or 0% while the film came in
            if (m.offered) {
                val live = Api.downloading.value.firstOrNull { it.ratingKey == m.ratingKey }
                // gone from the live list after getting near the end: it is in, whatever
                // the shelf's snapshot still says
                val state = live?.offerState
                    ?: if (Api.cameIn(m.ratingKey)) "done" else m.offerState
                val progress = live?.offerProgress ?: m.offerProgress
                val eta = live?.offerEta ?: m.offerEta
                val mbit = live?.offerMbit ?: m.offerMbit
                // the mark always, and words only when there are some: a film nobody
                // has asked for yet said nothing but a character, and on a television
                // that was a smudge in the corner of the picture
                val said = when (state) {
                    "downloading" -> "${(progress * 100).toInt()}%" +
                        (if (eta >= 0) " · " + (
                            if (eta < 60) "$eta s"
                            else if (eta < 3600) "${eta / 60} min"
                            else "${eta / 3600} h ${eta % 3600 / 60} min") else "") +
                        (if (mbit > 0) String.format(java.util.Locale.US, " · %.0f Mbit/s", mbit) else "")
                    "queued" -> "Queued"
                    "done" -> "Arriving"
                    else -> ""
                }
                Row(Modifier.align(Alignment.TopStart).padding(5.dp)
                        .background(Color(0xD9070A0E), RoundedCornerShape(5.dp))
                        .padding(horizontal = 4.dp, vertical = 3.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    DownloadMark(13.dp)
                    if (said.isNotEmpty()) {
                        Spacer(Modifier.width(3.dp))
                        Text(said, color = Skin.Fg, fontSize = 10.sp,
                             fontWeight = FontWeight.SemiBold)
                    }
                }
            }
            // held in 2160 lines somewhere. Bottom right: the watched tick has the
            // top right, and the progress bar runs along the bottom, so it is
            // lifted clear of that.
            if (m.maxHeight >= 1700) {
                Box(Modifier.align(Alignment.BottomEnd)
                        .padding(start = 5.dp, top = 5.dp, end = 5.dp, bottom = 8.dp)
                        .background(Color(0xD9070A0E), RoundedCornerShape(5.dp))
                        .padding(horizontal = 5.dp, vertical = 2.dp)) {
                    Text("4K", color = Skin.Accent, fontSize = 10.sp,
                         fontWeight = FontWeight.SemiBold)
                }
            }
            // A shelf being shuffled, standing on Continue watching as one row. Said
            // across the poster because the row reads as the episode it happens to be
            // showing otherwise, and pressing it plays whatever the hat has next
            // rather than that programme.
            if (m.shuffle.isNotEmpty()) {
                // Big, because the whole job of it is to be told apart at a glance
                // from a film started the ordinary way - including one out of the
                // same shelf. Small print across a poster is something to notice
                // afterwards.
                // The word alone. It sat on a filled box, which over a bright
                // poster reads as a grey square somebody has left there: the artwork
                // is what is being covered, and the badge only has to be legible over
                // it. The shadow is what holds it on a pale poster.
                Box(Modifier.align(Alignment.Center).rotate(-45f)) {
                    Text("SHUFFLE", color = Color.White,
                         fontSize = if (width < 130) 13.sp else 17.sp,
                         letterSpacing = 3.5.sp, maxLines = 1,
                         fontWeight = FontWeight.ExtraBold,
                         style = androidx.compose.ui.text.TextStyle(
                             shadow = androidx.compose.ui.graphics.Shadow(
                                 color = Color(0xE6000000),
                                 offset = androidx.compose.ui.geometry.Offset(0f, 2f),
                                 blurRadius = 14f)))
                }
            }
            // finished, or part way through a series
            if (m.watched || m.watchedEpisodes > 0) {
                Box(Modifier.align(Alignment.TopEnd).padding(5.dp)
                        .background(Color(0xD9070A0E), RoundedCornerShape(5.dp))
                        .padding(horizontal = 5.dp, vertical = 2.dp)) {
                    Text(if (m.watched) "\u2713"
                         else m.watchedEpisodes.toString() + "/" + m.episodeCount,
                         color = if (m.watched) Color(0xFF5FD08A) else Skin.Accent,
                         fontSize = if (m.watched) 12.sp else 10.sp,
                         fontWeight = FontWeight.SemiBold)
                }
            }
            val p = m.progressFraction()
            // the machine that keeps copies is holding this one: it plays when the
            // server with the library on it is off. Bottom left, lifted clear of the
            // progress bar where there is one.
            if (Api.copiedHere(m)) {
                Box(Modifier.align(Alignment.BottomStart)
                        .padding(start = 6.dp, bottom = if (p > 0f) 12.dp else 7.dp)
                        .size(9.dp)
                        .background(Color(0xFF42C96A), RoundedCornerShape(5.dp)))
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
                                            (if (Api.copiedHere(m)) 11 else 0)).dp))
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
        if (instead != null && instead.isNotEmpty()) {
            Text(instead, color = if (focused) Skin.Fg else Color(0xFFD3DAE2),
                 fontSize = if (width < 110) 11.sp else 12.5.sp,
                 fontWeight = FontWeight.Medium, maxLines = 2,
                 overflow = TextOverflow.Ellipsis,
                 modifier = Modifier.padding(top = 5.dp))
        }
    }
}

/** A small fact, the sort that belongs beside a title rather than in a sentence. */
@Composable
fun Chip(text: String, accent: Boolean = false) {
    Text(
        text,
        color = if (accent) Color(0xFF111111) else Skin.Dim,
        fontSize = 11.5.sp,
        fontWeight = FontWeight.Medium,
        modifier = Modifier.padding(end = 8.dp)
            .background(if (accent) Skin.Accent else Skin.Panel2, RoundedCornerShape(6.dp))
            .padding(horizontal = 9.dp, vertical = 4.dp),
    )
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
         modifier: Modifier = Modifier, onClick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    val bg = when {
        active || primary -> Skin.Accent
        focused || filled -> Skin.Panel2
        else -> Color.Transparent
    }
    Box(
        // the gap to the next one, tighter on a small pill: three of these and a
        // narrow phone is 360 points across, not the 411 a Pixel gives
        modifier.padding(end = if (small) 4.dp else 8.dp)
            .clip(RoundedCornerShape(999.dp))
            .background(bg)
            // A yellow pill on a dark ground reads as "chosen", which is not the same as
            // "the remote is here". The white ring says the second, over either state,
            // and an accent outline marks a second action that is not the main one.
            .border(if (focused || outline || ready) 2.dp else 0.dp,
                    when {
                        focused -> Color.White
                        ready -> Color.White
                        outline -> Skin.Accent
                        else -> Color.Transparent
                    },
                    RoundedCornerShape(999.dp))
            // hasFocus as well as isFocused: focus asked for by name can land on the
            // group around a control rather than the control itself, and the
            // ring then never appeared although the remote was there
            .onFocusChanged { focused = it.isFocused || it.hasFocus }
            .focusable()
            .then(if (dim) Modifier.alpha(0.45f) else Modifier)
            .clickable(enabled = !dim, onClick = onClick)
            // and on a short screen the tall ones come down to the small size:
            // a row of pills nine points deep either side is a shelf's worth of
            // height above the shelves, on the one screen that has none to spare
            .padding(horizontal = if (small) 10.dp else if (narrow) 12.dp else 18.dp,
                     vertical = if (small ||
                                    androidx.compose.ui.platform.LocalConfiguration
                                        .current.screenHeightDp < 560) 5.dp else 9.dp),
    ) {
        Text(label,
             color = if (active || primary) Color(0xFF111111) else Skin.Fg,
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
    onValue: (String) -> Unit,
) {
    var editing by remember { mutableStateOf(false) }
    val tv = onTv()
    var focused by remember { mutableStateOf(false) }

    Box(
        modifier
            .background(Skin.Panel, RoundedCornerShape(8.dp))
            .border(if (focused) 2.dp else 0.dp,
                    if (focused) Color.White else Color.Transparent, RoundedCornerShape(8.dp))
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
        KeyboardDialog(value, placeholder, multiline,
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
                         .background(Skin.Panel2, RoundedCornerShape(8.dp))
                         .padding(horizontal = 14.dp, vertical = 12.dp))
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
            .clip(RoundedCornerShape(7.dp))
            .background(if (focused) Skin.Accent else Skin.Panel2)
            .border(if (focused) 2.dp else 0.dp,
                    if (focused) Color.White else Color.Transparent, RoundedCornerShape(7.dp))
            // hasFocus as well as isFocused: focus asked for by name can land on the
            // group around a control rather than the control itself, and the
            // ring then never appeared although the remote was there
            .onFocusChanged { focused = it.isFocused || it.hasFocus }
            .focusable()
            .clickable(onClick = onPress),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = if (focused) Color(0xFF111111) else Skin.Fg,
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
         fontWeight = FontWeight.SemiBold, modifier = modifier)
}

/** A film on offer being fetched: how far and how long, across its poster on its own page. */
@Composable
fun androidx.compose.foundation.layout.BoxScope.OfferProgress(loaded: Media) {
    val m = Api.liveOffer(loaded)          // live progress, not the page's snapshot
    if (!loaded.offered || m.offerState !in setOf("queued", "downloading")) return
    Box(Modifier.align(Alignment.TopEnd).padding(6.dp)
            .background(Color(0xD9070A0E), RoundedCornerShape(6.dp))
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
        if (!fromTheHold(key)) false
        else {
            if (key.action == android.view.KeyEvent.ACTION_UP) holdIsOver()
            true
        }
    })
