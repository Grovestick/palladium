package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.focus.focusRequester
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * How subtitles are drawn - the same four choices everywhere they can be changed.
 *
 * Opened from Settings for the default, and from a film (its page, or the player) for
 * that film alone. The server decides what an override means: it stores only the
 * values that differ, so a later change to the default still reaches a film that never
 * disagreed with it.
 *
 * A burned-in track is drawn into the picture by ffmpeg rather than by the player, so
 * whoever opens this passes an [onChanged] that knows whether the stream has to be
 * rebuilt to show the difference.
 */

val SUB_SIZES = listOf(0.8f to "80%", 1f to "100%", 1.25f to "125%",
                       1.5f to "150%", 1.8f to "200%")
val SUB_COLOURS = listOf("white" to "White", "yellow" to "Yellow", "cyan" to "Cyan",
                         "green" to "Green", "grey" to "Grey")
val SUB_BACKS = listOf("none" to "None", "shadow" to "Shadow", "dark" to "Dark box",
                       "black" to "Black")
val SUB_POSITIONS = listOf(0f to "Bottom", 0.08f to "Just up", 0.16f to "Raised",
                           0.28f to "High", 0.99f to "Very bottom")
//: What "Bottom" is the bottom of. A film wider than the set is drawn with black above
//: and below it, and the last row of the panel is not the last row of the picture.
val SUB_BASES = listOf("picture" to "On screen", "screen" to "Off screen")

/**
 * Which of the offered numbers a stored one belongs to.
 *
 * Not equality: a size or a height can arrive from an older file, from another client,
 * or from the day 100% changed meaning and every stored size was rescaled to keep the
 * screen looking the same. 0.81 is not 0.8 and 0.972 is not 1.0, and a menu where
 * nothing at all is marked tells a viewer less than one marked at the nearest peg.
 * Pressing any peg saves that peg's own number, so the first press tidies it up.
 */
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
    // the longer answer about OLED panels, when somebody asks for it
    var burnIn by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    // a phone held sideways has a few hundred pixels of height once the system bars
    // have taken theirs: the dialog has to be told, because it will not ask
    val window = androidx.compose.ui.platform.LocalConfiguration.current
    val cramped = window.screenHeightDp < 500
    // a television is driven by a remote and has to be told where the cursor starts;
    // a phone is touched, and a ring drawn round something nobody pressed is noise
    val onTv = androidx.compose.ui.platform.LocalContext.current.packageManager
        .hasSystemFeature(android.content.pm.PackageManager.FEATURE_LEANBACK)

    LaunchedEffect(titleKey, device) { look = Api.subtitleLook(titleKey, device) }

    val save: (Api.SubLook) -> Unit = { next ->
        if (!busy) {
            busy = true
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
    val body: @Composable () -> Unit = {
        val l = look
        if (l == null) {
            Text("…", color = Skin.Dim)
        } else {
            // a phone has less room than the dialog wants, and a choice nobody can
            // see is worse than no choice at all
            // What is left of the screen once the title line has had its share -
            // and on a short screen that is all there is, since Done sits on the
            // title line rather than in a bar of its own along the bottom.
            Column(Modifier.verticalScroll(rememberScrollState())
                       .heightIn(max = (window.screenHeightDp - if (cramped) 58 else 84)
                                     .coerceAtLeast(140).dp)) {
                // Which settings these are. A title with its own is not showing the
                // general ones, and the two disagreeing looks like a fault when it is
                // simply an override nobody could see.
                if (titleKey.isNotEmpty() && l.override) {
                    Text("This title has its own settings, shown here. The general " +
                         "ones are in Settings.",
                         color = Skin.Accent, fontSize = 12.sp,
                         modifier = Modifier.padding(bottom = 6.dp))
                } else if (titleKey.isNotEmpty()) {
                    Text("The general settings, shown here. Change one and it becomes " +
                         "this title's own.",
                         color = Skin.Dim, fontSize = 12.sp,
                         modifier = Modifier.padding(bottom = 6.dp))
                }
                // what the choices actually look like, rather than their names -
                // the first thing to give up when the screen is short
                if (!cramped) Box(Modifier.fillMaxWidth().height(46.dp)
                        .background(Color(0xFF05070A), RoundedCornerShape(8.dp)),
                    contentAlignment = androidx.compose.ui.Alignment.BottomCenter) {
                    Text("The quick brown fox",
                         color = subtitleColour(l.colour),
                         fontSize = (15 * l.size).sp,
                         modifier = Modifier
                             .padding(bottom = (6 + l.position * 60).dp)
                             .background(
                                 when (l.background) {
                                     "dark" -> Color(0x8C000000)
                                     "black" -> Color.Black
                                     else -> Color.Transparent
                                 }, RoundedCornerShape(3.dp))
                             .padding(horizontal = 6.dp))
                }
                val size: @Composable () -> Unit = {
                    Choices(label = "Size", options = SUB_SIZES.map { it.second },
                            selected = nearestOption(SUB_SIZES.map { it.first }, l.size),
                            // the remote lands on what is in force rather than on the
                            // first peg in the row, so left and right mean "a bit more"
                            // and "a bit less" from where the viewer actually is
                            focusHere = onTv) { i ->
                        save(l.copy(size = SUB_SIZES[i].first))
                    }
                }
                val colour: @Composable () -> Unit = {
                    Choices(label = "Colour", options = SUB_COLOURS.map { it.second },
                            selected = SUB_COLOURS.indexOfFirst { it.first == l.colour }) { i ->
                        save(l.copy(colour = SUB_COLOURS[i].first))
                    }
                    // Subtitles sit in one place for hours, which is the pattern an
                    // OLED panel learns. Said in one line here, with the arithmetic a
                    // press away for anybody who wants it.
                    Row(verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(top = 4.dp)) {
                        Text("Grey is kinder to an OLED.",
                             color = Skin.Dim, fontSize = 11.5.sp, maxLines = 1,
                             modifier = Modifier.weight(1f, fill = false))
                        var asking by remember { mutableStateOf(false) }
                        Text("Info",
                             color = Skin.Accent, fontSize = 11.5.sp,
                             modifier = Modifier
                                 .padding(start = 8.dp)
                                 .clip(RoundedCornerShape(999.dp))
                                 .border(if (asking) 2.dp else 1.dp,
                                         if (asking) Color.White else Skin.Line,
                                         RoundedCornerShape(999.dp))
                                 .onFocusChanged { asking = it.isFocused }
                                 .focusable()
                                 .clickable(
                                     interactionSource = remember { MutableInteractionSource() },
                                     indication = null) { burnIn = true }
                                 .padding(horizontal = 8.dp, vertical = 2.dp))
                    }
                }
                val behind: @Composable () -> Unit = {
                    Choices(label = "Behind", options = SUB_BACKS.map { it.second },
                            selected = SUB_BACKS.indexOfFirst { it.first == l.background }) { i ->
                        save(l.copy(background = SUB_BACKS[i].first))
                    }
                }
                val height: @Composable () -> Unit = {
                    // off the picture the same four steps go the other way: the
                    // first sits nearest the film, the rest further down into the black
                    val steps = if (l.base == "screen")
                        listOf(0f to "Nearest", 0.08f to "One down",
                               0.16f to "Two down", 0.99f to "Very bottom")
                    else SUB_POSITIONS
                    Choices(label = "Height", options = steps.map { it.second },
                            selected = nearestOption(steps.map { it.first },
                                                     l.position)) { i ->
                        save(l.copy(position = steps[i].first))
                    }
                    // and what that height is measured from: with a wide film on a
                    // television, the bottom of the picture is some way above the
                    // bottom of the panel, and every step counts up from wherever
                    // this says nought is
                    Choices(label = "Placed", options = SUB_BASES.map { it.second },
                            selected = SUB_BASES.indexOfFirst { it.first == l.base }
                                .coerceAtLeast(0)) { i ->
                        // off the picture there are three steps, not four: a height
                        // deeper than the last of them comes back to it rather than
                        // being marked as one step and drawn as another
                        val to = SUB_BASES[i].first
                        val fit = if (to == "screen" && l.position < 0.9f &&
                                      l.position > 0.16f) 0.16f else l.position
                        save(l.copy(base = to, position = fit))
                    }
                    Text("A film wider than the set is drawn with black above and " +
                         "below it. Heights count from the bottom edge of whichever " +
                         "is chosen.",
                         color = Skin.Dim, fontSize = 11.sp,
                         modifier = Modifier.padding(top = 2.dp))
                }
                if (cramped) {
                    // sideways there is width to spare and no height at all: two
                    // columns, which is half the height of four rows
                    Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                        Column(Modifier.weight(1f)) { size(); behind() }
                        Column(Modifier.weight(1f)) { colour(); height() }
                    }
                } else {
                    size(); colour(); behind(); height()
                }
                if (titleKey.isNotEmpty()) {
                    Row(Modifier.padding(top = 8.dp)) {
                        Pill(if (l.override) "Reset to default" else "Same as default",
                             outline = l.override) {
                            if (l.override) {
                                busy = true
                                scope.launch {
                                    look = Api.resetSubtitleLook(titleKey, device)
                                    busy = false
                                    look?.let(onChanged)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    if (cramped) {
        // Held sideways, Material's own dialog spends the screen on itself: a title
        // block, padding round the content, and a button bar along the bottom deep
        // enough to scroll the choices out of sight. This one puts Done on the title
        // line and gives the rest to the choices.
        androidx.compose.ui.window.Dialog(
            onDismissRequest = onClose,
            properties = androidx.compose.ui.window.DialogProperties(
                usePlatformDefaultWidth = false),
        ) {
            androidx.compose.material3.Surface(
                color = Skin.Panel,
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth(0.94f),
            ) {
                Column(Modifier.padding(horizontal = 14.dp, vertical = 8.dp)) {
                    Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        Text(heading, color = Skin.Fg, fontSize = 15.sp,
                             fontWeight = FontWeight.SemiBold,
                             modifier = Modifier.weight(1f))
                        onTracks?.let { Pill("Subtitles…") { it() } }
                        Pill("Done", primary = true) { onClose() }
                    }
                    body()
                }
            }
        }
        return
    }

    if (burnIn) {
        AlertDialog(
            onDismissRequest = { burnIn = false },
            containerColor = Skin.Panel,
            title = { Text("Subtitle colour on an OLED", color = Skin.Fg,
                           fontSize = 16.sp, fontWeight = FontWeight.SemiBold) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("An OLED pixel dims with the light it has given out. "
                         + "Subtitles are the hardest thing on it: same place, "
                         + "hours at a time.",
                         color = Skin.Dim, fontSize = 13.sp)
                    Text("How bright each colour is, against white:",
                         color = Skin.Fg, fontSize = 13.sp)
                    Text("white   100%\n" +
                         "yellow   80%   (and only a third as much blue)\n" +
                         "green    66%\n" +
                         "grey     64%\n" +
                         "cyan     57%",
                         color = Skin.Dim, fontSize = 12.5.sp,
                         fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace)
                    Text("Grey is a third dimmer than white everywhere. Yellow "
                         + "gives up little brightness but rests the blue "
                         + "subpixel, which is the one that ages first.",
                         color = Skin.Dim, fontSize = 13.sp)
                    Text("Changing the height now and then spreads the wear, "
                         + "and a paused film lets the screen sleep after five "
                         + "minutes.",
                         color = Skin.Dim, fontSize = 13.sp)
                }
            },
            confirmButton = { Pill("Close") { burnIn = false } },
        )
    }

    AlertDialog(
        onDismissRequest = onClose,
        properties = androidx.compose.ui.window.DialogProperties(
            usePlatformDefaultWidth = false),
        modifier = Modifier.fillMaxWidth(0.8f),
        containerColor = Skin.Panel,
        title = {
            Text(heading, color = Skin.Fg, fontSize = 17.sp,
                 fontWeight = FontWeight.SemiBold)
        },
        text = { body() },
        confirmButton = { Pill("Done", primary = true) { onClose() } },
        dismissButton = onTracks?.let {
            { Pill("Subtitles…") { it() } }
        },
    )
}

/**
 * One labelled set of choices, wrapping onto as many lines as it needs.
 *
 * Wrapping rather than scrolling sideways: on a phone the selected pill was regularly
 * past the right-hand edge, which defeats the point of showing it at all.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Choices(label: String, options: List<String>, selected: Int,
                    focusHere: Boolean = false, onPick: (Int) -> Unit) {
    Text(label, color = Skin.Dim, fontSize = 12.sp,
         modifier = Modifier.padding(top = 5.dp, bottom = 1.dp))
    val here = remember { androidx.compose.ui.focus.FocusRequester() }
    var asked by remember { mutableStateOf(false) }
    FlowRow(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        options.forEachIndexed { i, text ->
            val mine = focusHere && i == selected
            Pill(text, active = i == selected, small = true,
                 modifier = if (mine) Modifier.focusRequester(here) else Modifier) {
                onPick(i)
            }
        }
    }
    if (focusHere) {
        LaunchedEffect(selected) {
            // once, when the dialog appears - not after every press, which would drag
            // the remote back to the row it started in
            if (!asked && selected >= 0) {
                asked = true
                try { here.requestFocus() } catch (notReady: Exception) { }
            }
        }
    }
}
