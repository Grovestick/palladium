package se.palladium.tv

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.heightIn
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
fun EpisodeDownloads(ep: Media, onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var got by remember { mutableStateOf<Api.Carrying?>(null) }
    var said by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    LaunchedEffect(ep.ratingKey) { got = Api.trackerVersions(ep) }
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
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
                            val words = listOf(
                                if (v.pick) "Recommended" else "Tracker",
                                "${v.seeds} seeding",
                                if (v.size > 0) "%.1f GB".format(v.size / 1073741824.0) else "",
                            ).filter { it.isNotEmpty() }.joinToString("  ·  ") +
                                "  —  " + v.name.replace(Regex("""\.(mkv|mp4|avi|m4v|ts)$""",
                                                               RegexOption.IGNORE_CASE), "") +
                                (if (v.why.isNotEmpty()) "  ·  " + v.why else "")
                            Pill(words, primary = v.pick, dim = v.why.isNotEmpty(),
                                 small = true) {
                                if (busy || v.why.isNotEmpty()) return@Pill
                                busy = true
                                scope.launch {
                                    val (ok, name) = Api.takeFromTracker(ep, v.id)
                                    said = if (ok) "Downloading $name" else name
                                    busy = false
                                }
                            }
                        }
                    }
                }
                if (said.isNotEmpty()) Text(said, color = Skin.Dim, fontSize = 13.sp)
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
    )
}
