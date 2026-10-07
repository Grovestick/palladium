package se.palladium.tv

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

/**
 * Everything coming in, in the order it will arrive: the one downloading, then the
 * waiting ones in the order they start. A name opens its poster; the arrows move one of
 * this viewer's own waiting downloads up or down among the others they may move.
 */
@Composable
fun DownloadQueue(onClose: () -> Unit, onOpen: (Media) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var order by remember { mutableStateOf(Api.downloading.value) }
    var busy by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf("") }

    fun line(d: Media): String {
        val state = if (d.offerState == "queued") "waiting"
                    else "${(d.offerProgress * 100).toInt()}%"
        return state + "   " + d.title + (if (d.offerWho.isNotEmpty()) "  ·  " + d.offerWho else "")
    }

    fun move(at: Int, step: Int) {
        // within the ones this viewer may move: the arrow skips over the others
        val movable = order.indices.filter { order[it].offerMine }
        val here = movable.indexOf(at)
        val there = movable.getOrNull(here + step) ?: return
        val now = order.toMutableList()
        val one = now[at]
        now[at] = now[there]
        now[there] = one
        order = now
        busy = true
        scope.launch {
            val ok = Api.reorderDownloads(order)
            failed = if (ok) "" else "Could not move that"
            Api.refreshDownloading(ctx)
            busy = false
        }
    }

    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        title = { Text("Downloads", color = Skin.Fg) },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()),
                   verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (order.isEmpty()) Text("Nothing is coming in", color = Skin.Dim, fontSize = 14.sp)
                order.forEachIndexed { i, d ->
                    Row(verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Pill(line(d), primary = d.offerState != "queued",
                             modifier = Modifier.weight(1f)) { onOpen(d) }
                        if (d.offerMine) {
                            Pill("↑") { if (!busy) move(i, -1) }
                            Pill("↓") { if (!busy) move(i, 1) }
                        }
                    }
                }
                if (failed.isNotEmpty()) Text(failed, color = Skin.Dim, fontSize = 13.sp)
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
    )
}
