package se.palladium.tv

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
@Composable
fun Poster(m: Media, width: Int = 150, fill: Boolean = false,
           /** what to say under the title instead of the usual line */
           instead: String? = null,
           onClick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    val scale by animateFloatAsState(if (focused) 1.06f else 1f, label = "posterScale")
    // the pixels this will actually occupy, so the server can send that and no more
    val density = androidx.compose.ui.platform.LocalDensity.current.density
    val pixels = remember(width, density) { ((width * density).toInt() / 20) * 20 }
    Column(
        // in a grid the cell decides the width, and the poster takes all of it; in a
        // row there is no cell, so it keeps the width it was given
        Modifier.padding(horizontal = 5.dp, vertical = 8.dp)
            .then(if (fill) Modifier.fillMaxWidth() else Modifier.width(width.dp))
            .scale(scale)
            // hasFocus as well as isFocused: focus asked for by name can land on the
            // group around a control rather than the control itself, and the
            // ring then never appeared although the remote was there
            .onFocusChanged { focused = it.isFocused || it.hasFocus }
            .focusable()
            .clickable(onClick = onClick)
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
            Art(Api.artUrl(m, pixels), m.title, Modifier.fillMaxSize(), mark = width / 3)
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
            if (p > 0f) {
                Box(Modifier.align(Alignment.BottomStart).fillMaxWidth().height(4.dp)
                    .background(Color(0xAA000000))) {
                    Box(Modifier.fillMaxWidth(p).fillMaxHeight().background(Skin.Accent))
                }
            }
        }
        Text(m.title, color = if (focused) Skin.Fg else Color(0xFFD3DAE2), fontSize = 12.5.sp,
             fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis,
             modifier = Modifier.padding(top = 8.dp))
        val under = instead ?: m.subtitle
        if (under.isNotEmpty()) {
            Text(under, color = Skin.Dim, fontSize = 11.sp, maxLines = 1,
                 overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 2.dp))
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
            .padding(horizontal = if (small) 10.dp else if (narrow) 12.dp else 18.dp,
                     vertical = if (small) 5.dp else 9.dp),
    ) {
        Text(label,
             color = if (active || primary) Color(0xFF111111) else Skin.Fg,
             fontSize = if (small) 12.5.sp else 14.sp,
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
            .padding(horizontal = 12.dp, vertical = 12.dp),
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
    Text(text.uppercase(), color = Skin.Dim, fontSize = 12.sp, letterSpacing = 1.2.sp,
         fontWeight = FontWeight.SemiBold, modifier = modifier)
}
