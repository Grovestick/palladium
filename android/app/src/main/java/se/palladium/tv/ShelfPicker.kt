package se.palladium.tv

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
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

/** The collections a title can go on: a tick for all of it, a half for some, and a new one. */
@Composable
fun ShelfPicker(m: Media, start: List<Api.ShelfMark>, onClose: () -> Unit,
                onChanged: (List<Api.ShelfMark>) -> Unit) {
    val scope = rememberCoroutineScope()
    var shelves by remember { mutableStateOf(start) }
    var naming by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf("") }
    LaunchedEffect(m.ratingKey) { shelves = Api.shelvesHolding(m) }

    fun took(now: List<Api.ShelfMark>?): Boolean {
        busy = false
        if (now == null) {
            failed = "Could not change that"
            return false
        }
        failed = ""
        shelves = now
        onChanged(now)
        return true
    }

    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        title = { Text("Collections", color = Skin.Fg) },
        text = {
            Column(Modifier.heightIn(max = 380.dp).verticalScroll(rememberScrollState()),
                   verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (shelves.isEmpty() && !naming)
                    Text("No collections yet", color = Skin.Dim, fontSize = 14.sp)
                shelves.forEach { s ->
                    val tick = when (s.state) { "all" -> "✓  "; "some" -> "◐  "; else -> "" }
                    Pill(tick + s.name, primary = s.state == "all",
                         modifier = Modifier.fillMaxWidth()) {
                        if (!busy) {
                            busy = true
                            scope.launch { took(Api.markShelf(m, s.id, s.state != "all")) }
                        }
                    }
                }
                if (naming) {
                    OutlinedTextField(value = name, onValueChange = { name = it },
                                      singleLine = true, label = { Text("Name") })
                }
                if (failed.isNotEmpty()) Text(failed, color = Skin.Dim, fontSize = 13.sp)
            }
        },
        confirmButton = {
            if (naming) {
                Pill("Make it", primary = true) {
                    val asked = name.trim()
                    if (asked.isNotEmpty() && !busy) {
                        busy = true
                        scope.launch {
                            if (took(Api.newShelf(m, asked))) {
                                naming = false
                                name = ""
                            }
                        }
                    }
                }
            } else {
                Pill("+ New collection") { naming = true }
            }
        },
        dismissButton = { Pill("Done") { onClose() } })
}
