package se.palladium.tv

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
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

/** The collections a title can go on: a tick for all of it, a half for some, and a new one. */
@Composable
fun ShelfPicker(m: Media, start: List<Api.ShelfMark>, onClose: () -> Unit,
                onChanged: (List<Api.ShelfMark>) -> Unit) {
    val scope = rememberCoroutineScope()
    // From an episode or a season, which of it goes on or comes off. Ticking and unticking
    // start on what the page is - a season taken off a collection is that season - and a new
    // collection starts on the whole series: opened on season 1 and named after the
    // programme, only season 1 went on.
    val show = (if (m.type == "episode") m.grandparentKey else if (m.type == "season") m.parentKey else null)
        ?.takeIf { it.isNotEmpty() }
    val season = if (m.type == "episode") m.parentKey?.takeIf { it.isNotEmpty() } else null
    val scopes = buildList {
        if (show != null) add("Whole series" to m.copy(ratingKey = show, type = "show").also { it.srv = m.srv })
        if (season != null) add("This season" to m.copy(ratingKey = season, type = "season").also { it.srv = m.srv })
        if (m.type == "season") add("This season" to m)
        if (m.type == "episode") add("This episode" to m)
    }
    var target by remember { mutableStateOf(m) }
    var shelves by remember { mutableStateOf(start) }
    var naming by remember { mutableStateOf(false) }
    // a name to start from, so a television without a keyboard to hand can press Make it:
    // the programme for an episode or a season, the title itself otherwise
    val suggested = (m.grandparentTitle?.takeIf { it.isNotBlank() } ?: m.title).trim()
    var name by remember { mutableStateOf("") }
    var offer by remember { mutableStateOf(suggested) }
    var busy by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf("") }
    LaunchedEffect(target.ratingKey) { shelves = Api.shelvesHolding(target) }

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
                if (scopes.size > 1) {
                    androidx.compose.foundation.layout.Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        scopes.forEach { (said, one) ->
                            Pill(said, active = target.ratingKey == one.ratingKey) { target = one }
                        }
                    }
                }
                if (shelves.isEmpty() && !naming)
                    Text("No collections yet", color = Skin.Dim, fontSize = 14.sp)
                shelves.forEach { s ->
                    val tick = when (s.state) { "all" -> "✓  "; "some" -> "◐  "; else -> "" }
                    Pill(tick + s.name, primary = s.state == "all",
                         modifier = Modifier.fillMaxWidth()) {
                        if (!busy) {
                            busy = true
                            scope.launch { took(Api.markShelf(target, s.id, s.state != "all")) }
                        }
                    }
                }
                if (naming) {
                    // the app's own keyboard on a television, walked with the remote;
                    // the programme's name offered on it as one press
                    TextBox(name, offer, Modifier.fillMaxWidth(),
                            suggestion = offer) { name = it }
                }
                if (failed.isNotEmpty()) Text(failed, color = Skin.Dim, fontSize = 13.sp)
            }
        },
        confirmButton = {
            if (naming) {
                Pill("Make it", primary = true) {
                    // nothing typed: the name offered
                    val asked = name.trim().ifEmpty { offer }
                    if (asked.isNotEmpty() && !busy) {
                        busy = true
                        scope.launch {
                            if (took(Api.newShelf(target, asked))) {
                                naming = false
                                name = ""
                            }
                        }
                    }
                }
            } else {
                Pill("+ New collection") {
                    // taken, not suggested twice: "Westerns" when a collection "Westerns" exists
                    // gets a number after it
                    val taken = shelves.map { it.name.lowercase() }.toSet()
                    var pick = suggested
                    var n = 2
                    while (pick.lowercase() in taken) pick = "$suggested $n".also { n++ }
                    offer = pick
                    name = ""
                    naming = true
                    scopes.firstOrNull()?.let { target = it.second }
                }
            }
        },
        dismissButton = { Pill("Done") { onClose() } })
}
