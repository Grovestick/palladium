package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

/**
 * The tracker's releases of an episode not here yet, early ones included: each with what
 * its colour means and its size, pressed to download. Green is the recommended one.
 */
@Composable
fun EpisodeDownloads(ep: Media, onPage: (() -> Unit)? = null, onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var got by remember { mutableStateOf<Api.Carrying?>(null) }
    var said by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    LaunchedEffect(ep.ratingKey) { got = Api.trackerVersions(ep) }
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        // most of the screen, as the film's list: release names are long
        properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false),
        modifier = Modifier.fillMaxWidth(Route.PICKER_WIDTH).widthIn(max = Route.PICKER_MAX.dp),
        title = { Text("E" + (ep.index ?: "") + "  " + ep.title, color = Skin.Fg) },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()),
                   verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val rows = got?.rows
                when {
                    rows == null -> Text("Asking the tracker…", color = Skin.Dim, fontSize = 14.sp)
                    rows.isEmpty() -> Text("Nothing on the tracker for this one yet",
                                           color = Skin.Dim, fontSize = 14.sp)
                    else -> {
                        got?.free?.takeIf { it >= 0 }?.let {
                            Text("${it.toInt()} GB free", color = Skin.Dim, fontSize = 13.sp)
                        }
                        rows.forEach { v ->
                            val facts = listOf(
                                // both: "Recommended" alone hid that it was a pack's
                                if (v.pick) "Recommended" else "",
                                if (v.pack) "In a pack" else if (v.box)
                                    (if (v.seasonPack) "Season pack" else "Box set") else "Tracker",
                                if (v.seeds >= 0) "${v.seeds} seeding" else "",
                                if (v.size > 0) "%.1f GB".format(v.size / 1073741824.0) else "",
                                v.why,
                            ).filter { it.isNotEmpty() }.joinToString("  ·  ")
                            // the name whole, over as many lines as it takes: a one-line
                            // button cut it off where releases differ, at the end
                            var lit by remember(v.id) { mutableStateOf(false) }
                            val ring = lit && focusShows()
                            Column(Modifier.fillMaxWidth()
                                       .clip(RoundedCornerShape(Skin.round(8.dp)))
                                       .background(if (ring) Skin.Accent else Skin.Panel2)
                                       .onFocusChanged { lit = it.isFocused || it.hasFocus }
                                       .focusable()
                                       .clickable(enabled = v.why.isEmpty() && !busy) {
                                           busy = true
                                           scope.launch {
                                               val (ok, name) = Api.takeFromTracker(ep, v.id)
                                               said = if (ok) "Downloading $name" else name
                                               busy = false
                                           }
                                       }
                                       .padding(horizontal = 12.dp, vertical = 9.dp)) {
                                Text(v.name.replace(Regex("""\.(mkv|mp4|avi|m4v|ts)$""",
                                                          RegexOption.IGNORE_CASE), ""),
                                     color = if (ring) Color.Black
                                             else if (v.why.isNotEmpty()) Color(0xFFFF6B6B)
                                             else if (v.pick) Color(0xFF5FD08A)
                                             else if (v.pack) PackBlue else Skin.Fg,
                                     fontSize = 13.sp, lineHeight = 17.sp)
                                Text(facts, color = if (ring) Color.Black else Skin.Dim,
                                     fontSize = 11.5.sp, modifier = Modifier.padding(top = 2.dp))
                            }
                        }
                    }
                }
                if (said.isNotEmpty()) Text(said, color = Skin.Dim, fontSize = 13.sp)
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
        // an episode a pack offers has a page too: asking for it is done there
        dismissButton = onPage?.let { open -> { Pill("Episode page") { onClose(); open() } } },
    )
}
