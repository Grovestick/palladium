package se.palladium.tv

import androidx.appcompat.app.AppCompatActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

//: a copy in one of the house's packs, told apart from the tracker's own releases
val PackBlue = Color(0xFF7FB4FF)

/**
 * Every version of one film to download, whatever there is of them - one included, so a
 * download is always a choice somebody confirmed. The tracker's releases and the packs'
 * copies side by side, each saying which it is; on disk orange, the settings' pick green,
 * a pack copy blue, one that cannot be had red.
 */
@Composable
fun DownloadPicker(full: Media, rows: List<Api.Carried>, onClose: () -> Unit,
                   onSaid: (String) -> Unit,
                   /** GB free where it would land; negative when not known */
                   free: Double = -1.0,
                   /** what pressing one does instead of downloading it: a series' pack */
                   onTake: ((Api.Carried) -> Unit)? = null) {
    val ctx = LocalContext.current
    // the search behind this list: said while it runs, and the list drawn again with it
    var live by remember { mutableStateOf(rows) }
    var checking by remember { mutableStateOf(onTake == null) }
    // the release pressed, waiting on Download or Cancel
    var asking by remember { mutableStateOf<Api.Carried?>(null) }
    var askingPlain by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        if (onTake == null) {
            val newer = Api.trackerVersions(full, true).rows
            if (newer.isNotEmpty()) live = newer
        }
        checking = false
    }
    // Held here, not in the dialog's text: that is drawn again on its own, and a request
    // kept there started over each time while the window's own focus went to the top
    // row - the lit row jumped between the two and Down did nothing (2026-11-07).
    val opening = remember { FocusRequester() }
    // a dialog is its own window, and a request made before that window has focus is
    // dropped without a word: asked again until a row holds it, and never after
    var landed by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        repeat(14) {
            kotlinx.coroutines.delay(150)
            if (landed) return@LaunchedEffect
            runCatching { opening.requestFocus() }
        }
    }
    asking?.let { one ->
        AlertDialog(
            onDismissRequest = { asking = null },
            containerColor = Skin.Panel,
            title = { Text("Download this one?", color = Skin.Fg, fontSize = 17.sp) },
            text = {
                Text(one.name + (if (one.size > 0) "  ·  " + String.format(java.util.Locale.US,
                         "%.1f GB", one.size / 1073741824.0) else ""),
                     color = Skin.Fg, fontSize = 13.sp, lineHeight = 18.sp)
            },
            confirmButton = {
                Pill("Download", primary = true) {
                    asking = null
                    onClose()
                    val plain = askingPlain
                    onSaid(if (plain) "Checking the tracker…" else "Starting the download…")
                    (ctx as AppCompatActivity).lifecycleScope.launch {
                        val id = if (plain) Api.bestAtPress(full, one.id) else one.id
                        val (got, word) = Api.takeFromTracker(full, id)
                        onSaid(if (got) "" else word)
                    }
                }
            },
            dismissButton = { Pill("Cancel") { asking = null } })
    }
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        // most of the screen: the platform's width cut release names off on a phone
        properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false),
        modifier = Modifier.fillMaxWidth(Route.PICKER_WIDTH).widthIn(max = Route.PICKER_MAX.dp),
        title = {
            Text("Which one" + (if (free >= 0) "  ·  " + Math.round(free) + " GB free" else "")
                 + (if (checking) "  ·  Checking the tracker…" else ""),
                 color = Skin.Fg, fontSize = 18.sp)
        },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                // it opens on the one the settings put forward, lit
                val first = live.firstOrNull { it.pick && it.word.isEmpty() }
                    ?: live.firstOrNull { it.why.isEmpty() && it.word.isEmpty() } ?: live.firstOrNull()
                // not keyed: a keyed row was rebuilt under the focus on every redraw, which
                // let go of the focus and took it back many times a second (2026-11-07)
                live.forEach { one -> run {
                    var lit by remember(one.id) { mutableStateOf(false) }
                    val ring = lit && focusShows()
                    Column(
                        Modifier.fillMaxWidth()
                            .then(if (one === first) Modifier.focusRequester(opening) else Modifier)
                            .padding(bottom = 8.dp)
                            .clip(RoundedCornerShape(Skin.round(8.dp)))
                            .background(if (ring) Skin.Accent else Skin.Panel2)
                            .onFocusChanged {
                                lit = it.isFocused || it.hasFocus
                                // arrived once the one it opens on has it: the window's own
                                // first focus lands on the top row before that
                                if (lit && one === first) landed = true
                            }
                            .focusable()
                            .clickable {
                                // a download is asked about first (owner, 2026-11-15)
                                if (onTake == null && !one.have && one.why.isEmpty()) {
                                    asking = one
                                    // the one it opened on: nobody chose, so the best as of now
                                    askingPlain = one === first
                                    return@clickable
                                }
                                onClose()
                                when {
                                    onTake != null -> onTake(one)
                                    one.have -> onSaid("That one is on disk already")
                                    else -> onSaid(one.why)
                                }
                            }
                            .padding(horizontal = 12.dp, vertical = 9.dp)) {
                        Text(one.name,
                             color = if (ring) Color.Black
                                     else if (one.why.isNotEmpty()) Color(0xFFFF6B6B)
                                     // the pick green and the best 4K violet, on disk or not;
                                     // on disk says so in yellow on its tag (owner, 2026-11-15)
                                     else if (one.pick) Color(0xFF5FD08A)
                                     else if (one.uhd) Color(0xFFB794F6)
                                     else if (one.have) Color(0xFFE0B341)
                                     else if (one.pack) PackBlue
                                     else Skin.Fg,
                             fontSize = 13.sp, lineHeight = 17.sp)
                        // the 1080p / 4K tag in the row's colour, On disk in yellow, the rest dim
                        val resColor = if (one.pick) Color(0xFF5FD08A)
                                       else if (one.uhd) Color(0xFFB794F6) else null
                        val tag = Route.versionTag(one.disk, one.have, one.pack, one.box,
                                                   one.seasonPack, one.packShort)
                        // where it comes from first - On disk, Tracker - then its resolution
                        // (owner, 2026-11-15)
                        val here = one.disk || one.have
                        val tagPart = tag.takeIf { it.isNotEmpty() }?.let {
                            it to (if (here) Color(0xFFE0B341) else null) }
                        val parts = listOfNotNull(
                            one.word.takeIf { it.isNotEmpty() }?.let { it to null },
                            tagPart,
                            one.res.takeIf { it.isNotEmpty() }?.let { it to resColor },
                            "Freeleech".takeIf { one.freeleech }?.let { it to null },
                            "${one.seeds} seeding".takeIf { one.seeds >= 0 }?.let { it to null },
                            one.mbit.takeIf { it > 0 }?.let { "$it Mbit/s" to null },
                            one.size.takeIf { it > 0 }?.let {
                                String.format(java.util.Locale.US, "%.1f GB", it / 1073741824.0) to null },
                            one.why.takeIf { it.isNotEmpty() }?.let { it to null },
                        )
                        val line = androidx.compose.ui.text.buildAnnotatedString {
                            parts.forEachIndexed { at, (words, color) ->
                                if (at > 0) append("   ·   ")
                                if (color != null && !ring) {
                                    pushStyle(androidx.compose.ui.text.SpanStyle(color = color))
                                    append(words)
                                    pop()
                                } else append(words)
                            }
                        }
                        Text(line, color = if (ring) Color.Black else Skin.Dim,
                             fontSize = 11.5.sp, modifier = Modifier.padding(top = 2.dp))
                    }
                } }
            }
        },
        confirmButton = { Pill("Close") { onClose() } })
}
