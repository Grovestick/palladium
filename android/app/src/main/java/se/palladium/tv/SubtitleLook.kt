package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * The Aa menu: how subtitles are drawn, for the default or for one title.
 *
 * The preview stays at the top while the choices scroll under it, and shows where the
 * text lands on the two kinds of film there are: one wider than the screen, with black
 * above and below, and one that fills it. Which height applies depends on that, and a
 * preview of one case only hid the other.
 */

val SUB_SIZES = listOf(0.8f to "80%", 1f to "100%", 1.25f to "125%",
                       1.5f to "150%", 1.8f to "200%")
val SUB_COLOURS = listOf("white" to "White", "yellow" to "Yellow", "cyan" to "Cyan",
                         "green" to "Green", "grey" to "Grey")
val SUB_BACKS = listOf("none" to "None", "shadow" to "Shadow", "dark" to "Dark box",
                       "black" to "Black")
//: heights on the picture, counted in rows up from its bottom edge
val SUB_POSITIONS = listOf(0f to "Bottom", 0.08f to "Just up", 0.16f to "Raised",
                           0.28f to "High", 0.99f to "Very bottom")
//: heights below the picture, counted in rows down from its bottom edge
val SUB_BELOW = listOf(0f to "Nearest", 0.08f to "One down", 0.16f to "Two down",
                       0.99f to "Very bottom")
val SUB_BASES = listOf("picture" to "On screen", "screen" to "Off screen")

/** Which of the offered numbers a stored one belongs to: the nearest, never none. */
fun nearestOption(values: List<Float>, value: Float): Int {
    var best = 0
    for (i in values.indices) {
        if (Math.abs(values[i] - value) < Math.abs(values[best] - value)) best = i
    }
    return best
}

fun subtitleColour(name: String): Color = when (name) {
    "yellow" -> Color(0xFFFFE94D)
    "cyan" -> Color(0xFF6FD3FF)
    "green" -> Color(0xFF8DEA6A)
    "grey" -> Color(0xFFC9D3DC)
    else -> Color.White
}

/** Rows up (on the picture) or down (below it) a stored height stands for. */
private fun rowsOf(position: Float): Int =
    if (position >= 0.9f) 0 else Math.round(position / 0.08f).let {
        // 0.28 is the fourth peg, not three and a half rows
        if (Math.abs(position - 0.28f) < 0.01f) 3 else it
    }

/** The on-screen height in force: its own when Off screen is on, the position when not. */
private fun onScreenHeight(l: Api.SubLook): Float =
    if (l.base == "screen") (if (l.onPicture >= 0f) l.onPicture else 0.08f) else l.position

@Composable
fun SubtitleLookDialog(
    titleKey: String,               // empty for the default, or "l123" for one film
    device: String,                 // "tv", "web" or "phone"
    scope: CoroutineScope,
    onChanged: (Api.SubLook) -> Unit,
    onClose: () -> Unit,
    // offered while a film is playing: which subtitle, as against how it looks
    onTracks: (() -> Unit)? = null,
) {
    var look by remember { mutableStateOf<Api.SubLook?>(null) }
    var burnIn by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    val window = androidx.compose.ui.platform.LocalConfiguration.current
    // a phone held sideways has a few hundred pixels of height
    val cramped = window.screenHeightDp < 500
    val onTv = androidx.compose.ui.platform.LocalContext.current.packageManager
        .hasSystemFeature(android.content.pm.PackageManager.FEATURE_LEANBACK)

    LaunchedEffect(titleKey, device) { look = Api.subtitleLook(titleKey, device) }

    val save: (Api.SubLook) -> Unit = { next ->
        if (!busy) {
            busy = true
            look = next                       // the preview moves at once
            scope.launch {
                look = Api.saveSubtitleLook(titleKey, next, device)
                busy = false
                look?.let(onChanged)
            }
        }
    }

    val heading = (if (titleKey.isEmpty()) "Subtitles" else "Subtitles for this title") +
        "  •  " + when (device) {
            "tv" -> "television"; "phone" -> "phone"; else -> "computer"
        }

    val content: @Composable () -> Unit = {
        val l = look
        if (l == null) {
            Text("…", color = Skin.Dim)
        } else {
            val reset: () -> Unit = {
                busy = true
                scope.launch {
                    look = Api.resetSubtitleLook(titleKey, device)
                    busy = false
                    look?.let(onChanged)
                }
            }
            if (onTv || cramped) {
                // Side by side: the previews down the left and every setting beside
                // them. On a television a remote only reaches what is on the screen - a
                // row scrolled out of sight was skipped and down went straight to Done -
                // so everything fits. A phone held sideways has no height for a preview
                // above the choices: it filled the screen and nothing could be pressed.
                androidx.compose.foundation.layout.Row(
                    horizontalArrangement = Arrangement.spacedBy(if (cramped) 12.dp else 20.dp)) {
                    Column(Modifier.width(if (cramped) 150.dp else 230.dp),
                           verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Screen(l, wide = true, label = "Wide film")
                        Screen(l, wide = false, label = "Fills the screen")
                    }
                    Column(Modifier.weight(1f).then(
                        if (cramped) Modifier.verticalScroll(rememberScrollState())
                            .heightIn(max = (window.screenHeightDp - 70).coerceAtLeast(160).dp)
                        else Modifier)) {
                        Choices(titleKey, l, cramped = false, onTv = onTv, inline = true,
                                save = save, onInfo = { burnIn = true }, onReset = reset)
                    }
                }
            } else {
                Column {
                    // the preview stays put; everything under it scrolls
                    androidx.compose.foundation.layout.Row(
                        horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Screen(l, wide = true, label = "Wide film",
                               modifier = Modifier.weight(1f))
                        Screen(l, wide = false, label = "Fills the screen",
                               modifier = Modifier.weight(1f))
                    }
                    Column(Modifier
                        .padding(top = 8.dp)
                        .verticalScroll(rememberScrollState())
                        .heightIn(max = (window.screenHeightDp - (if (cramped) 190 else 330))
                                         .coerceAtLeast(120).dp)) {
                        Choices(titleKey, l, cramped, onTv, inline = false, save = save,
                                onInfo = { burnIn = true }, onReset = reset)
                    }
                }
            }
        }
    }

    if (burnIn) BurnInInfo { burnIn = false }

    if (cramped) {
        // held sideways: Done on the title line, the rest of the screen for the menu
        androidx.compose.ui.window.Dialog(
            onDismissRequest = onClose,
            properties = androidx.compose.ui.window.DialogProperties(
                usePlatformDefaultWidth = false),
        ) {
            androidx.compose.material3.Surface(
                color = Skin.Panel, shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth(0.94f),
            ) {
                Column(Modifier.padding(horizontal = 14.dp, vertical = 8.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(heading, color = Skin.Fg, fontSize = 15.sp,
                             fontWeight = FontWeight.SemiBold,
                             modifier = Modifier.weight(1f))
                        onTracks?.let { Pill("Subtitles…") { it() } }
                        Pill("Done", primary = true) { onClose() }
                    }
                    content()
                }
            }
        }
        return
    }

    AlertDialog(
        onDismissRequest = onClose,
        properties = androidx.compose.ui.window.DialogProperties(
            usePlatformDefaultWidth = false),
        modifier = Modifier.fillMaxWidth(if (onTv) 0.94f else 0.92f),
        containerColor = Skin.Panel,
        title = {
            Text(heading, color = Skin.Fg, fontSize = 17.sp,
                 fontWeight = FontWeight.SemiBold)
        },
        text = { content() },
        confirmButton = { Pill("Done", primary = true) { onClose() } },
        dismissButton = onTracks?.let { { Pill("Subtitles…") { it() } } },
    )
}

/**
 * One small screen at 16:9: a film wider than the set, with black above and below it,
 * or one that fills it. The line sits where these settings put it - below the picture
 * when that is on and there is room, and on it at the on-screen height otherwise.
 */
@Composable
private fun Screen(l: Api.SubLook, wide: Boolean, label: String,
                   modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(label, color = Skin.Dim, fontSize = 11.sp)
        BoxWithConstraints(Modifier.fillMaxWidth().aspectRatio(16f / 9f)
                .clip(RoundedCornerShape(6.dp)).background(Color.Black)
                .border(1.dp, Skin.Line, RoundedCornerShape(6.dp))) {
            val h = maxHeight
            // a 2.39:1 picture on a 16:9 screen leaves an eighth of the height black
            // above it and an eighth below
            val band = if (wide) h * 0.128f else 0.dp
            Box(Modifier.fillMaxWidth().padding(vertical = band).fillMaxHeight()
                    .background(Color(0xFF3A4A5C)))
            // the text a little larger than true, or it could not be read at this size
            val text = (h.value * 0.065f * l.size).coerceAtLeast(9f)
            val row = h * (0.065f * l.size * 1.17f)
            val air = h * (0.065f * l.size * 0.17f)
            val below = l.base == "screen" && l.position < 0.9f && band >= row + air * 2
            val words = Modifier
                .background(when (l.background) {
                    "dark" -> Color(0x8C000000); "black" -> Color.Black
                    else -> Color.Transparent }, RoundedCornerShape(2.dp))
                .padding(horizontal = 3.dp)
            val line: @Composable () -> Unit = {
                Text("The quick brown fox", color = subtitleColour(l.colour),
                     fontSize = text.sp, lineHeight = text.sp, maxLines = 1,
                     textAlign = TextAlign.Center, modifier = words)
            }
            if (below) {
                // under the picture, a row further down each step
                val top = h - band + air + row * rowsOf(l.position)
                Box(Modifier.fillMaxWidth().padding(top = top.coerceAtMost(h - row - air * 2)),
                    contentAlignment = Alignment.TopCenter) { line() }
            } else {
                // on the picture, up from its bottom edge by the on-screen height; Very
                // bottom is the screen's own edge, whatever the film
                val high = if (l.base == "screen") onScreenHeight(l) else l.position
                // Very bottom is the screen's edge only where it was chosen for: on the
                // picture, or below it when the band holds the line
                val floor = if (l.base == "screen")
                                l.position >= 0.9f && band >= row + air * 2
                            else l.position >= 0.9f
                val up = if (floor) air else band + air + row * rowsOf(high)
                Box(Modifier.fillMaxSize().padding(bottom = up),
                    contentAlignment = Alignment.BottomCenter) { line() }
            }
        }
    }
}

/** Every choice, in the order they are thought about: size, colour, backing, place. */
@Composable
private fun Choices(titleKey: String, l: Api.SubLook, cramped: Boolean, onTv: Boolean,
                    inline: Boolean, save: (Api.SubLook) -> Unit, onInfo: () -> Unit,
                    onReset: () -> Unit) {
    if (titleKey.isNotEmpty()) {
        Text(if (l.override) "This title has its own settings, shown here. The general " +
                             "ones are in Settings."
             else "The general settings, shown here. Change one and it becomes this " +
                  "title's own.",
             color = if (l.override) Skin.Accent else Skin.Dim, fontSize = 12.sp,
             modifier = Modifier.padding(bottom = 4.dp))
    }
    val look: @Composable () -> Unit = {
        Row("Size", inline, SUB_SIZES.map { it.second },
            nearestOption(SUB_SIZES.map { it.first }, l.size), focusHere = onTv) { i ->
            save(l.copy(size = SUB_SIZES[i].first))
        }
        Row("Colour", inline, SUB_COLOURS.map { it.second },
            SUB_COLOURS.indexOfFirst { it.first == l.colour }) { i ->
            save(l.copy(colour = SUB_COLOURS[i].first))
        }
        OledLine(onInfo, inline)
        Row("Behind", inline, SUB_BACKS.map { it.second },
            SUB_BACKS.indexOfFirst { it.first == l.background }) { i ->
            save(l.copy(background = SUB_BACKS[i].first))
        }
    }
    val place: @Composable () -> Unit = {
        // the on-screen height is always its own setting
        val onSteps = SUB_POSITIONS.filter { it.first < 0.9f || l.base != "screen" }
        Row(if (inline) "On the picture" else if (l.base == "screen")
                "On screen height - films that fill the screen" else "On screen height",
            inline,
            onSteps.map { it.second },
            nearestOption(onSteps.map { it.first }, onScreenHeight(l))) { i ->
            save(if (l.base == "screen") l.copy(onPicture = onSteps[i].first)
                 else l.copy(position = onSteps[i].first))
        }
        Row(if (inline) "Below the picture" else "Below the picture when there is room",
            inline, listOf("Off", "On"),
            if (l.base == "screen") 1 else 0) { i ->
            if (i == 1 && l.base != "screen") {
                val kept = if (l.position < 0.9f) l.position else 0.08f
                save(l.copy(base = "screen", position = 0f, onPicture = kept))
            } else if (i == 0 && l.base == "screen") {
                save(l.copy(base = "picture", position = onScreenHeight(l)))
            }
        }
        if (l.base == "screen") {
            Row(if (inline) "Below, how far" else "Below the picture height - wide films",
                inline, SUB_BELOW.map { it.second },
                nearestOption(SUB_BELOW.map { it.first }, l.position)) { i ->
                save(l.copy(position = SUB_BELOW[i].first))
            }
        }
        Text(if (l.base == "screen")
                 "Wide films: in the black below the picture. Films that fill the " +
                 "screen: on the picture, at its height."
             else "The text sits on the picture, lifted a row at a time.",
             color = Skin.Dim, fontSize = 11.sp, modifier = Modifier.padding(top = 3.dp))
    }
    if (cramped) {
        // sideways: two columns, half the height of one
        androidx.compose.foundation.layout.Row(
            horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Column(Modifier.weight(1f)) { look() }
            Column(Modifier.weight(1f)) { place() }
        }
    } else {
        look(); place()
    }
    if (titleKey.isNotEmpty() && l.override) {
        androidx.compose.foundation.layout.Row(Modifier.padding(top = 8.dp)) {
            Pill("Reset to default", outline = true) { onReset() }
        }
    }
}

/** Why grey is offered, in one line, with the numbers a press away. */
@Composable
private fun OledLine(onInfo: () -> Unit, inline: Boolean = false) {
    androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.padding(top = 2.dp, start = if (inline) 118.dp else 0.dp)) {
        Text("Grey is kinder to an OLED.", color = Skin.Dim, fontSize = 11.5.sp,
             maxLines = 1, modifier = Modifier.weight(1f, fill = false))
        var asking by remember { mutableStateOf(false) }
        Text("Info", color = Skin.Accent, fontSize = 11.5.sp,
             modifier = Modifier
                 .padding(start = 8.dp)
                 .clip(RoundedCornerShape(999.dp))
                 .border(if (asking) 2.dp else 1.dp,
                         if (asking) Color.White else Skin.Line, RoundedCornerShape(999.dp))
                 .onFocusChanged { asking = it.isFocused }
                 .focusable()
                 .clickable(interactionSource = remember { MutableInteractionSource() },
                            indication = null) { onInfo() }
                 .padding(horizontal = 8.dp, vertical = 2.dp))
    }
}

/**
 * One labelled row of choices. A plain row, not one that wraps: on a television a
 * wrapped pill was a line below its neighbours and the remote went to the next setting
 * instead. It scrolls sideways where it has to, and the chosen pill is always reachable.
 */
@Composable
private fun Row(label: String, inline: Boolean, options: List<String>, selected: Int,
                focusHere: Boolean = false, onPick: (Int) -> Unit) {
    val here = remember { FocusRequester() }
    var asked by remember { mutableStateOf(false) }
    val pills: @Composable () -> Unit = {
        androidx.compose.foundation.layout.Row(
            Modifier.horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            options.forEachIndexed { i, text ->
                val mine = focusHere && i == selected
                Pill(text, active = i == selected, small = true,
                     modifier = if (mine) Modifier.focusRequester(here) else Modifier) {
                    onPick(i)
                }
            }
        }
    }
    if (inline) {
        // the label beside its pills: a row to a setting, so the menu fits the screen
        androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(top = 2.dp)) {
            Text(label, color = Skin.Dim, fontSize = 12.sp, maxLines = 1,
                 modifier = Modifier.width(118.dp))
            pills()
        }
    } else {
        Text(label, color = Skin.Dim, fontSize = 12.sp,
             modifier = Modifier.padding(top = 6.dp, bottom = 1.dp))
        pills()
    }
    if (focusHere) {
        LaunchedEffect(selected) {
            // once, when the menu opens - not after every press
            if (!asked && selected >= 0) {
                asked = true
                try { here.requestFocus() } catch (notReady: Exception) { }
            }
        }
    }
}

@Composable
private fun BurnInInfo(onClose: () -> Unit) {
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        title = { Text("Subtitle colour on an OLED", color = Skin.Fg,
                       fontSize = 16.sp, fontWeight = FontWeight.SemiBold) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("An OLED pixel dims with the light it has given out. Subtitles are " +
                     "the hardest thing on it: same place, hours at a time.",
                     color = Skin.Dim, fontSize = 13.sp)
                Text("How bright each colour is, against white:",
                     color = Skin.Fg, fontSize = 13.sp)
                Text("white   100%\n" +
                     "yellow   80%   (and only a third as much blue)\n" +
                     "green    66%\n" +
                     "grey     64%\n" +
                     "cyan     57%",
                     color = Skin.Dim, fontSize = 12.5.sp, fontFamily = FontFamily.Monospace)
                Text("Grey is a third dimmer than white everywhere. Yellow gives up little " +
                     "brightness but rests the blue subpixel, which is the one that ages " +
                     "first.", color = Skin.Dim, fontSize = 13.sp)
                Text("Changing the height now and then spreads the wear, and a paused film " +
                     "lets the screen sleep after five minutes.",
                     color = Skin.Dim, fontSize = 13.sp)
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
    )
}
